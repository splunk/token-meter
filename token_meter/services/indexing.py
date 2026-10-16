"""Load-aware scheduling for cold session indexing.

Parallel indexing must never make the machine feel slower. Workers run at
reduced priority (owned by the platform layer), the pool never exceeds half the
CPUs the process may run on, battery or low-power mode caps it further, and while the build
runs the number of in-flight tasks follows measured idle CPU with additive
increase and multiplicative decrease: it halves as soon as less than one CPU is
idle and grows by one only while at least two are idle. Saturation also asks
workers to switch to the platform's yielding scheduling class until capacity
returns. Physical memory caps the pool, and kernel memory pressure caps the
in-flight limit (half the pool on warn, one worker on critical) and restarts the
pool smaller, because the CPU signal cannot see swapping and Python workers keep
their peak memory until they exit.
"""

import concurrent.futures
import math
import time


MAX_INDEX_WORKERS = 6
POWER_CONSTRAINED_MAX_WORKERS = 2
MIN_PARALLEL_SOURCES = 16
BUDGET_RECHECK_S = 1.0
PROBE_PRIME_S = 0.5
RESERVED_IDLE_CPUS = 1.0
GROW_IDLE_CPUS = 2.0
# A worker parsing a large trace peaks at a few hundred MB, so allow one worker
# per 4 GiB of physical memory (8 GiB → 2, 16 GiB → 4, 24 GiB → 6).
GIB_PER_WORKER = 4
UNKNOWN_MEMORY_MAX_WORKERS = 2
MEMORY_NORMAL, MEMORY_WARN, MEMORY_CRITICAL = "normal", "warn", "critical"


def worker_ceiling(cpu_count, power_constrained=None, memory_bytes=None):
    """Return the largest pool to start, or 0 when parallel indexing is not worth it."""
    try:
        cpus = int(cpu_count or 0)
    except (TypeError, ValueError):
        cpus = 0
    if cpus < 2:
        return 0
    ceiling = min(MAX_INDEX_WORKERS, cpus // 2)
    if power_constrained:
        ceiling = min(ceiling, POWER_CONSTRAINED_MAX_WORKERS)
    try:
        gib = float(memory_bytes) / float(1 << 30)
    except (TypeError, ValueError):
        gib = None
    if gib is None or not math.isfinite(gib) or gib <= 0:
        ceiling = min(ceiling, UNKNOWN_MEMORY_MAX_WORKERS)
    else:
        ceiling = min(ceiling, int(gib // GIB_PER_WORKER))
    return max(1, ceiling)


def memory_limited(limit, ceiling, pressure):
    """Apply memory pressure after the CPU step.

    ``warn`` caps the limit at half the ceiling and ``critical`` drops it to one
    worker; normal or unknown pressure changes nothing.
    """
    if pressure == MEMORY_CRITICAL:
        return 1
    if pressure == MEMORY_WARN:
        return max(1, min(limit, ceiling // 2))
    return limit


def idle_cpus(previous, current, cpu_count):
    """Return idle CPUs between two samples, or None on a missing, empty, or reset interval."""
    if previous is None or current is None:
        return None
    busy = current.busy - previous.busy
    idle = current.idle - previous.idle
    if busy < 0 or idle < 0 or busy + idle <= 0:
        return None
    return float(cpu_count or 0) * idle / (busy + idle)


def initial_worker_limit(ceiling, idle):
    """Start with the idle capacity minus the reserved CPU; unknown idle trusts the ceiling."""
    if ceiling < 1:
        return 0
    if idle is None or not math.isfinite(idle):
        return ceiling
    return max(1, min(ceiling, int(math.floor(idle - RESERVED_IDLE_CPUS))))


def next_worker_limit(limit, ceiling, idle):
    """Apply one AIMD step: halve when the host is saturated, grow slowly when it is not."""
    if idle is None or not math.isfinite(idle):
        return limit
    if idle < RESERVED_IDLE_CPUS:
        return max(1, limit // 2)
    if idle >= GROW_IDLE_CPUS:
        return min(ceiling, limit + 1)
    return limit


class RecyclingExecutor:
    """An executor that can restart with fewer worker processes.

    A worker that parsed a large trace keeps that memory until it exits, so
    lowering the dispatch limit alone does not relieve memory pressure. Shrinking
    waits for in-flight work (its futures still resolve), then starts a smaller
    pool. If the replacement cannot start, later submissions raise and the
    caller's sequential path takes over.
    """

    def __init__(self, factory, size):
        self._factory = factory
        self.size = size
        self._executor = factory(size)

    def submit(self, fn, *args):
        if self._executor is None:
            raise RuntimeError("index pool is unavailable")
        return self._executor.submit(fn, *args)

    def shrink(self, size):
        size = max(1, int(size))
        if self._executor is None or size >= self.size:
            return False
        self._executor.shutdown(wait=True)
        self._executor = None
        self._executor = self._factory(size)
        self.size = size
        return True

    def shutdown(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True)


class AdaptiveWorkerBudget:
    """Callable ``budget(active_workers) -> allowed in-flight tasks``.

    Memory pressure is applied after the CPU step and reported through
    ``on_memory_limit`` so the pool itself can shrink.

    Prefers host CPU tick deltas. Without them it estimates idle CPUs from the
    1-minute load average minus this build's active workers; with neither it
    holds the current limit, because workers still run at reduced priority.
    """

    def __init__(self, ceiling, cpu_count, cpu_sampler, load_average, on_pressure=None,
                 memory_pressure=None, on_memory_limit=None):
        self.ceiling = ceiling
        self.cpu_count = cpu_count
        self._cpu_sampler = cpu_sampler
        self._load_average = load_average
        self._on_pressure = on_pressure
        self._memory_pressure = memory_pressure
        self._on_memory_limit = on_memory_limit
        self._previous = self._sample()
        self.limit = None
        self.pressured = False

    def _sample(self):
        try:
            return self._cpu_sampler()
        except Exception:
            return None

    def _idle(self, active_workers):
        current = self._sample()
        idle = idle_cpus(self._previous, current, self.cpu_count)
        if current is not None:
            self._previous = current
        if idle is not None:
            return idle
        try:
            load = self._load_average()
        except Exception:
            load = None
        if load is None:
            return None
        # The load average already counts this build's running workers.
        return max(0.0, float(self.cpu_count or 0) - float(load))

    def __call__(self, active_workers=0):
        idle = self._idle(active_workers)
        if self.limit is None:
            self.limit = initial_worker_limit(self.ceiling, idle)
        else:
            self.limit = next_worker_limit(self.limit, self.ceiling, idle)
        memory = self._memory()
        self.limit = memory_limited(self.limit, self.ceiling, memory)
        if memory in (MEMORY_WARN, MEMORY_CRITICAL) and self._on_memory_limit is not None:
            # Size the pool by memory alone: a CPU dip must not shrink it for good.
            try:
                self._on_memory_limit(memory_limited(self.ceiling, self.ceiling, memory))
            except Exception:
                pass
        if idle is not None and math.isfinite(idle):
            # Hysteresis: yield on saturation, resume only once capacity returns.
            if idle < RESERVED_IDLE_CPUS and not self.pressured:
                self._set_pressure(True)
            elif idle >= GROW_IDLE_CPUS and self.pressured:
                self._set_pressure(False)
        return self.limit

    def _memory(self):
        if self._memory_pressure is None:
            return None
        try:
            return self._memory_pressure()
        except Exception:
            return None

    def _set_pressure(self, pressured):
        self.pressured = pressured
        if self._on_pressure is not None:
            try:
                self._on_pressure(pressured)
            except Exception:
                pass


def run_adaptive(items, submit, budget, on_result, clock=time.monotonic,
                 recheck_s=BUDGET_RECHECK_S):
    """Run ``submit(item)`` futures with in-flight work bounded by ``budget(active)``.

    ``on_result(item, value)`` receives each completed value. Any submission or
    executor failure stops new submissions; in-flight work still drains so its
    results are kept. Returns ``True`` when every item was processed.
    """
    pending = iter(items)
    exhausted = False
    stopped = False
    in_flight = {}
    limit = max(1, int(budget(0) or 1))
    next_check = clock() + recheck_s
    while True:
        while not stopped and not exhausted and len(in_flight) < limit:
            try:
                item = next(pending)
            except StopIteration:
                exhausted = True
                break
            try:
                in_flight[submit(item)] = item
            except Exception:
                stopped = True
        if not in_flight:
            break
        done, _ = concurrent.futures.wait(
            tuple(in_flight), timeout=recheck_s,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        for future in done:
            item = in_flight.pop(future)
            try:
                value = future.result()
            except Exception:
                stopped = True
                continue
            on_result(item, value)
        if clock() >= next_check:
            limit = max(1, int(budget(len(in_flight)) or 1))
            next_check = clock() + recheck_s
    return exhausted and not stopped
