import concurrent.futures
from concurrent.futures.process import BrokenProcessPool
import copy
import errno
import itertools
import json
import multiprocessing
import multiprocessing.context
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.platforms.base import CpuActivity, PlatformCapabilities
from token_meter.services import indexing


def _codex_rows(session_id, turns):
    rows = [
        {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta",
         "payload": {"id": session_id, "cwd": "/work/project", "model_provider": "openai"}},
        {"timestamp": "2026-08-11T00:00:01Z", "type": "turn_context",
         "payload": {"model": "gpt-5.5", "cwd": "/work/project"}},
    ]
    for turn in range(turns):
        second = 2 + turn * 4
        rows.extend([
            {"timestamp": f"2026-08-11T00:{second // 60:02d}:{second % 60:02d}Z",
             "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": f"2026-08-11T00:{(second + 1) // 60:02d}:{(second + 1) % 60:02d}Z",
             "type": "response_item",
             "payload": {"type": "function_call", "name": "read", "call_id": f"c{turn}"}},
            {"timestamp": f"2026-08-11T00:{(second + 2) // 60:02d}:{(second + 2) % 60:02d}Z",
             "type": "event_msg", "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 1000 + 37 * turn,
                                      "output_tokens": 50 + turn,
                                      "cached_input_tokens": 400}}}},
            {"timestamp": f"2026-08-11T00:{(second + 3) // 60:02d}:{(second + 3) % 60:02d}Z",
             "type": "event_msg", "payload": {"type": "task_complete", "duration_ms": 3000}},
        ])
    return rows


def _fork_rows():
    def meta(physical, logical, **extra):
        return {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta",
                "payload": {"id": physical, "session_id": logical, "cwd": "/work/project", **extra}}

    def turn(hour, tool, last, total):
        stamp = f"2026-08-11T{hour:02d}:00:0"
        return [
            {"timestamp": stamp + "1Z", "type": "turn_context",
             "payload": {"model": "gpt-5.5", "cwd": "/work/project"}},
            {"timestamp": stamp + "1Z", "type": "event_msg", "payload": {"type": "task_started"}},
            {"timestamp": stamp + "1.500Z", "type": "response_item",
             "payload": {"type": "function_call", "name": tool}},
            {"timestamp": stamp + "2Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": last[0], "output_tokens": last[1]},
                "total_token_usage": {"input_tokens": total[0], "output_tokens": total[1]}}}},
            {"timestamp": stamp + "3Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 2000}},
        ]

    root = [meta("fork-root", "fork-task")] + turn(0, "inherited", (100, 10), (100, 10))
    child = ([meta("fork-child", "fork-task", forked_from_id="fork-root"),
              meta("fork-root", "fork-task")]
             + turn(1, "inherited", (100, 10), (100, 10))
             + turn(2, "child_tool", (50, 5), (150, 15)))
    return root, child


class ColdIndexFixture(unittest.TestCase):
    SESSIONS = 20

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        # Spawned workers re-import the module and do not see in-process
        # patches; a temporary HOME keeps them inside this fixture.
        self.home = root
        sessions = root / ".codex" / "sessions"
        day = sessions / "2026" / "08" / "11"
        day.mkdir(parents=True)
        index_lines = []
        for number in range(self.SESSIONS):
            session_id = f"session-{number}"
            trace = day / f"rollout-{session_id}.jsonl"
            trace.write_text("".join(
                json.dumps(row) + "\n" for row in _codex_rows(session_id, 2 + number % 5)
            ))
            os.utime(trace, (1_784_548_800 + number, 1_784_548_800 + number))
            index_lines.append(json.dumps({"id": session_id, "thread_name": f"Title {number}"}))
        fork_root, fork_child = _fork_rows()
        for name, rows, mtime in (("fork-root", fork_root, 10), ("fork-child", fork_child, 20)):
            trace = day / f"rollout-{name}.jsonl"
            trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
            os.utime(trace, (1_784_548_000 + mtime, 1_784_548_000 + mtime))
        self.fork_child = str(day / "rollout-fork-child.jsonl")
        index = root / ".codex" / "session_index.jsonl"
        index.write_text("\n".join(index_lines) + "\n")
        patches = [
            mock.patch.object(meter, "CODEX_SESSIONS", str(sessions)),
            mock.patch.object(meter, "CODEX_INDEX", str(index)),
            mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")),
            mock.patch.object(meter, "CURSOR_PROJECTS", str(root / "no-cursor")),
            mock.patch.object(meter, "OPENCODE_DB", str(root / "no-opencode.db")),
            mock.patch.object(meter, "KIRO_SESSIONS", str(root / "no-kiro")),
            mock.patch.object(meter, "KIRO_AGENT_STORAGE", str(root / "no-kiro-agent")),
            mock.patch.object(meter, "PI_AGENT_DIR", str(root / "no-pi-agent")),
            mock.patch.object(meter, "HERMES_STATE_DB", str(root / "no-hermes.db")),
            mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []),
            mock.patch.object(meter, "claude_desktop_index", return_value={}),
            mock.patch.object(meter, "_summary_cache", {}),
            mock.patch.object(meter, "_parallel_index_enabled", True),
            mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}),
            # The parallel path is exercised on every test host; production
            # enablement per platform is covered by the capability tests.
            mock.patch.object(meter._PLATFORM_SERVICES, "capabilities", return_value=PlatformCapabilities(
                paths=True, detached_process=True, trash=True, parallel_index=True,
            )),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.sources = meter.all_session_sources()
        self.assertEqual(len(self.sources), self.SESSIONS + 2)

    def sequential_rows(self):
        meter._summary_cache.clear()
        rows = {source["path"]: copy.deepcopy(meter.session_summary(source))
                for source in self.sources}
        meter._summary_cache.clear()
        return rows

    def cached_rows(self):
        return {path: entry["row"] for path, entry in meter._summary_cache.items()}


class PrefetchSessionSummariesTests(ColdIndexFixture):
    @staticmethod
    def thread_executor(max_workers, pressure=None):
        return concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def prefetch(self, **overrides):
        options = {
            "executor_factory": self.thread_executor,
            "cpu_count": 8,
            "budget": lambda active=0: 4,
            "power_constrained": False,
            "prime_s": 0,
            "memory_bytes": 32 << 30,
        }
        options.update(overrides)
        return meter.prefetch_session_summaries(self.sources, **options)

    def test_parallel_rows_equal_sequential_rows_and_are_reused(self):
        expected = self.sequential_rows()

        stored = self.prefetch()

        self.assertEqual(stored, len(self.sources))
        self.assertEqual(self.cached_rows(), expected)
        with mock.patch.object(meter, "_index_summary_worker") as worker:
            self.assertEqual(self.prefetch(), 0)
        worker.assert_not_called()

    def test_real_spawned_workers_return_identical_rows(self):
        expected = self.sequential_rows()

        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            stored = meter.prefetch_session_summaries(
                self.sources, cpu_count=4, budget=lambda active=0: 2,
                power_constrained=False, prime_s=0, memory_bytes=32 << 30,
            )

        self.assertEqual(stored, len(self.sources))
        self.assertEqual(self.cached_rows(), expected)
        # The forked child's inherited prefix is removed inside the worker too.
        self.assertEqual(self.cached_rows()[self.fork_child]["tokens"], 55)

    def test_process_creation_failure_falls_back_to_identical_rows(self):
        expected = self.sequential_rows()
        original_start = multiprocessing.context.SpawnProcess.start
        starts = itertools.count(1)

        def start(process):
            # Only the first worker may start; any later spawn hits ENOMEM.
            if next(starts) >= 2:
                raise OSError(errno.ENOMEM, "Cannot allocate memory")
            return original_start(process)

        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                mock.patch.object(multiprocessing.context.SpawnProcess, "start", start):
            stored = meter.prefetch_session_summaries(
                self.sources, cpu_count=8, budget=lambda active=0: 4,
                power_constrained=False, prime_s=0, memory_bytes=32 << 30,
            )

        self.assertLess(stored, len(self.sources))
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)

    def test_killed_worker_falls_back_to_identical_rows(self):
        expected = self.sequential_rows()

        class KillingExecutor(concurrent.futures.ProcessPoolExecutor):
            submitted = 0

            def submit(self, *args, **kwargs):
                KillingExecutor.submitted += 1
                if KillingExecutor.submitted == 4:
                    # What macOS memory-pressure termination looks like to the pool.
                    victim = next(iter(self._processes.values()))
                    os.kill(victim.pid, signal.SIGKILL)
                    victim.join(5)
                return super().submit(*args, **kwargs)

        def factory(max_workers, pressure=None):
            return KillingExecutor(
                max_workers=max_workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=meter._index_worker_initializer,
                initargs=(pressure, meter._index_worker_runtime_state()),
            )

        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            stored = meter.prefetch_session_summaries(
                self.sources, executor_factory=factory, cpu_count=4,
                budget=lambda active=0: 1, power_constrained=False, prime_s=0,
                memory_bytes=32 << 30,
            )

        self.assertLess(stored, len(self.sources))
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)

    def test_memory_pressure_restarts_the_default_pool_smaller(self):
        expected = self.sequential_rows()
        sizes = []

        def factory(max_workers, pressure=None):
            sizes.append(max_workers)
            return self.thread_executor(max_workers)

        idle = iter(CpuActivity(busy=step, idle=step * 9) for step in itertools.count(0, 100))
        with mock.patch.object(meter._PLATFORM_SERVICES, "cpu_activity", side_effect=lambda: next(idle)), \
                mock.patch.object(meter._PLATFORM_SERVICES, "memory_pressure", return_value="critical"):
            stored = meter.prefetch_session_summaries(
                self.sources, executor_factory=factory, cpu_count=8,
                power_constrained=False, prime_s=0, memory_bytes=32 << 30,
            )

        self.assertEqual(sizes, [4, 1])
        self.assertEqual(stored, len(self.sources))
        self.assertEqual(self.cached_rows(), expected)

    def test_failed_pool_restart_falls_back_to_identical_rows(self):
        expected = self.sequential_rows()
        calls = itertools.count()

        def factory(max_workers, pressure=None):
            if next(calls):
                raise OSError("cannot spawn a smaller pool")
            return self.thread_executor(max_workers)

        idle = iter(CpuActivity(busy=step, idle=step * 9) for step in itertools.count(0, 100))
        with mock.patch.object(meter._PLATFORM_SERVICES, "cpu_activity", side_effect=lambda: next(idle)), \
                mock.patch.object(meter._PLATFORM_SERVICES, "memory_pressure", return_value="critical"):
            stored = meter.prefetch_session_summaries(
                self.sources, executor_factory=factory, cpu_count=8,
                power_constrained=False, prime_s=0, memory_bytes=32 << 30,
            )

        self.assertEqual(next(calls), 2, "the smaller pool restart was attempted")
        self.assertEqual(stored, 0)

        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)

    def test_disabled_outside_the_server_entrypoint(self):
        factory = mock.Mock()
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            self.assertEqual(self.prefetch(executor_factory=factory), 0)
        factory.assert_not_called()

    def test_adapters_that_need_a_shared_connection_stay_in_process(self):
        adapter = meter.runtime_registry().get("codex")
        with mock.patch.object(type(adapter), "worker_summaries", False, create=True):
            self.assertEqual(self.prefetch(), 0)
        self.assertEqual(meter._summary_cache, {})
        self.assertFalse(getattr(meter.runtime_registry().get("opencode"), "worker_summaries", True))

    def test_platforms_without_parallel_indexing_stay_sequential(self):
        expected = self.sequential_rows()
        factory = mock.Mock()
        for name in ("win32", "linux"):
            with self.subTest(platform=name):
                host = meter.platform_services(name, environment={}, home=str(self.home))
                meter._summary_cache.clear()
                meter._xsess["data"] = None
                with mock.patch.object(meter, "_PLATFORM_SERVICES", host), \
                        mock.patch.object(meter, "_index_executor", factory):
                    self.assertEqual(self.prefetch(executor_factory=factory), 0)
                    meter.cross_session(self.sources)
                self.assertEqual(self.cached_rows(), expected)
        with mock.patch.object(meter, "multiprocessing", mock.Mock(spec=[])):
            self.assertEqual(self.prefetch(executor_factory=factory), 0)
        factory.assert_not_called()

    def test_pool_size_comes_from_physical_memory_when_not_given(self):
        sizes = []

        def factory(max_workers, pressure=None):
            sizes.append(max_workers)
            return self.thread_executor(max_workers)

        with mock.patch.object(meter, "_physical_memory_bytes", return_value=8 << 30) as memory:
            self.prefetch(executor_factory=factory, cpu_count=16, memory_bytes=None)
        memory.assert_called_once_with()
        self.assertEqual(sizes, [2])

    def test_pool_size_comes_from_usable_cpus_when_not_given(self):
        sizes = []

        def factory(max_workers, pressure=None):
            sizes.append(max_workers)
            return self.thread_executor(max_workers)

        with mock.patch.object(meter, "_usable_cpu_count", return_value=4) as usable:
            self.prefetch(executor_factory=factory, cpu_count=None)
        usable.assert_called_once_with()
        self.assertEqual(sizes, [2])

    def test_usable_cpus_respect_affinity_where_available(self):
        with mock.patch.object(os, "sched_getaffinity", create=True, return_value={0, 1, 2}):
            self.assertEqual(meter._usable_cpu_count(), 3)
        with mock.patch.object(os, "sched_getaffinity", create=True, side_effect=OSError), \
                mock.patch.object(os, "cpu_count", return_value=12):
            self.assertEqual(meter._usable_cpu_count(), 12)

    def test_small_cold_sets_and_single_cpu_hosts_stay_sequential(self):
        factory = mock.Mock()
        self.assertEqual(
            meter.prefetch_session_summaries(
                self.sources[:indexing.MIN_PARALLEL_SOURCES - 1],
                executor_factory=factory, cpu_count=8, budget=lambda active=0: 4,
                power_constrained=False, prime_s=0, memory_bytes=32 << 30,
            ),
            0,
        )
        self.assertEqual(self.prefetch(executor_factory=factory, cpu_count=1), 0)
        factory.assert_not_called()

    def test_battery_caps_the_pool_size(self):
        sizes = []

        def factory(max_workers, pressure=None):
            sizes.append(max_workers)
            return self.thread_executor(max_workers)

        self.prefetch(executor_factory=factory, cpu_count=16, power_constrained=True)

        self.assertEqual(sizes, [indexing.POWER_CONSTRAINED_MAX_WORKERS])

    def test_pool_start_failure_falls_back_to_identical_sequential_rows(self):
        expected = self.sequential_rows()

        def factory(max_workers, pressure=None):
            raise OSError("process creation blocked")

        self.assertEqual(self.prefetch(executor_factory=factory), 0)
        self.assertEqual(meter._summary_cache, {})
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            cross = meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)
        self.assertEqual(len(cross["sessions"]), len(self.sources))

    def test_worker_errors_are_left_for_the_sequential_path(self):
        expected = self.sequential_rows()
        failing = {self.sources[0]["path"], self.sources[1]["path"]}
        original = meter._index_summary_worker

        def worker(source):
            if source["path"] in failing:
                return None
            return original(source)

        with mock.patch.object(meter, "_index_summary_worker", side_effect=worker):
            stored = self.prefetch()

        self.assertEqual(stored, len(self.sources) - len(failing))
        self.assertTrue(failing.isdisjoint(meter._summary_cache))
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)

    def test_broken_pool_stops_dispatch_and_keeps_completed_rows(self):
        expected = self.sequential_rows()
        calls = itertools.count()
        original = meter._index_summary_worker

        def worker(source):
            if next(calls) == 3:
                raise BrokenProcessPool("worker killed")
            return original(source)

        with mock.patch.object(meter, "_index_summary_worker", side_effect=worker):
            stored = self.prefetch(budget=lambda active=0: 1)

        self.assertEqual(stored, 3)
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter.cross_session(self.sources)
        self.assertEqual(self.cached_rows(), expected)

    def test_concurrent_callers_share_one_prefetch(self):
        factory_calls = []
        release = threading.Event()

        def factory(max_workers, pressure=None):
            factory_calls.append(max_workers)
            release.wait(5)
            return self.thread_executor(max_workers)

        threads = [
            threading.Thread(target=self.prefetch, kwargs={"executor_factory": factory})
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(10)

        self.assertEqual(factory_calls, [4])
        self.assertEqual(len(meter._summary_cache), len(self.sources))

    def test_cross_session_output_is_unchanged_by_parallel_prefetch(self):
        with mock.patch.object(meter, "_parallel_index_enabled", False):
            meter._xsess["data"] = None
            sequential = copy.deepcopy(meter.cross_session(self.sources))
        meter._summary_cache.clear()
        meter._xsess["data"] = None
        with mock.patch.object(meter, "_index_executor", self.thread_executor), \
                mock.patch.object(meter._PLATFORM_SERVICES, "power_constrained",
                                  return_value=False):
            parallel = meter.cross_session(self.sources)
        meter._xsess["data"] = None
        for payload in (sequential, parallel):
            payload.pop("generated_at", None)
        self.assertEqual(parallel["sessions"], sequential["sessions"])
        self.assertEqual(parallel["daily"], sequential["daily"])
        self.assertEqual(parallel["total_cost"], sequential["total_cost"])

    def test_main_enables_parallel_indexing_for_the_server_only(self):
        self.addCleanup(setattr, meter, "_matched_pace_persist", False)
        with mock.patch.object(meter, "_parallel_index_enabled", False), \
                mock.patch.object(meter, "serve_local"):
            meter.main()
            self.assertTrue(meter._parallel_index_enabled)


_ORPHAN_PARENT = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
import meter
if __name__ == "__main__":
    executor = meter._index_executor(2, None)
    print(executor.submit(os.getpid).result(), executor.submit(os.getcwd).result(), flush=True)
    time.sleep(60)
"""


class WorkerLifecycleTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process probing")
    def test_workers_exit_when_the_server_is_killed_and_do_not_pin_the_runtime_dir(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "parent.py"
            script.write_text(_ORPHAN_PARENT)
            parent = subprocess.Popen(
                [sys.executable, str(script), str(root)], cwd=temp,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            try:
                worker_pid, worker_cwd = parent.stdout.readline().split(maxsplit=1)
                worker_pid = int(worker_pid)
            finally:
                parent.kill()
                parent.wait(10)
                parent.stdout.close()
            self.assertNotEqual(os.path.realpath(worker_cwd.strip()), os.path.realpath(temp))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _alive(worker_pid):
                time.sleep(0.1)
            alive = _alive(worker_pid)
            if alive:
                os.kill(worker_pid, signal.SIGKILL)
            self.assertFalse(alive, "index worker outlived its parent")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WorkerPressureTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(meter._index_worker_pressure.update, {"event": None, "yielding": False})

    def test_workers_follow_the_shared_pressure_signal_once_per_change(self):
        event = threading.Event()
        with mock.patch.object(meter._PLATFORM_SERVICES, "lower_background_priority"), \
                mock.patch.object(meter._PLATFORM_SERVICES, "set_background_mode") as mode:
            meter._index_worker_initializer(event)
            meter._follow_index_pressure()
            event.set()
            meter._follow_index_pressure()
            meter._follow_index_pressure()
            event.clear()
            meter._follow_index_pressure()

        self.assertEqual([call.args for call in mode.call_args_list], [(True,), (False,)])

    def test_worker_returns_none_instead_of_raising(self):
        adapter = mock.Mock()
        adapter.summarize_legacy.side_effect = ValueError("corrupt trace")
        with mock.patch.object(meter, "runtime_registry") as registry:
            registry.return_value.get.return_value = adapter
            self.assertIsNone(meter._index_summary_worker({"provider": "codex", "path": "x"}))


class WorkerPolicyTests(unittest.TestCase):
    def test_ceiling_uses_at_most_half_the_cpus_and_respects_power(self):
        plenty = 64 << 30
        cases = [
            ((None, False, plenty), 0), ((1, False, plenty), 0), (("bad", False, plenty), 0),
            ((2, False, plenty), 1), ((3, False, plenty), 1), ((8, False, plenty), 4),
            ((12, False, plenty), 6), ((64, False, plenty), indexing.MAX_INDEX_WORKERS),
            ((12, True, plenty), 2), ((2, True, plenty), 1), ((12, None, plenty), 6),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(indexing.worker_ceiling(*args), expected)

    def test_ceiling_allows_one_worker_per_four_gib(self):
        cases = [(2, 1), (4, 1), (8, 2), (12, 3), (16, 4), (24, 6), (128, 6)]
        for gib, expected in cases:
            with self.subTest(gib=gib):
                self.assertEqual(indexing.worker_ceiling(64, False, gib << 30), expected)
        for unknown in (None, 0, -1, "bad", float("nan")):
            with self.subTest(memory=unknown):
                self.assertEqual(
                    indexing.worker_ceiling(64, False, unknown),
                    indexing.UNKNOWN_MEMORY_MAX_WORKERS,
                )
        self.assertEqual(indexing.worker_ceiling(64, True, 64 << 30), 2)

    def test_memory_pressure_caps_or_drops_the_limit(self):
        self.assertEqual(indexing.memory_limited(6, 6, "normal"), 6)
        self.assertEqual(indexing.memory_limited(6, 6, None), 6)
        self.assertEqual(indexing.memory_limited(6, 6, "unexpected"), 6)
        self.assertEqual(indexing.memory_limited(6, 6, "warn"), 3)
        self.assertEqual(indexing.memory_limited(2, 6, "warn"), 2)
        self.assertEqual(indexing.memory_limited(1, 1, "warn"), 1)
        self.assertEqual(indexing.memory_limited(6, 6, "critical"), 1)

    def test_idle_cpus_rejects_missing_empty_and_reset_intervals(self):
        before = CpuActivity(busy=100, idle=100)
        self.assertAlmostEqual(
            indexing.idle_cpus(before, CpuActivity(busy=130, idle=170), 10), 7.0,
        )
        self.assertIsNone(indexing.idle_cpus(None, before, 10))
        self.assertIsNone(indexing.idle_cpus(before, None, 10))
        self.assertIsNone(indexing.idle_cpus(before, before, 10))
        self.assertIsNone(indexing.idle_cpus(before, CpuActivity(busy=5, idle=500), 10))

    def test_initial_limit_leaves_one_cpu_idle(self):
        self.assertEqual(indexing.initial_worker_limit(6, None), 6)
        self.assertEqual(indexing.initial_worker_limit(6, 0.0), 1)
        self.assertEqual(indexing.initial_worker_limit(6, 3.2), 2)
        self.assertEqual(indexing.initial_worker_limit(6, 11.0), 6)
        self.assertEqual(indexing.initial_worker_limit(0, 11.0), 0)

    def test_aimd_halves_under_saturation_and_grows_by_one(self):
        limit = 6
        trace = []
        for idle in (0.2, 0.5, 0.0, 1.5, 2.5, 3.0, None, 9.0, 9.0, 9.0, 9.0):
            limit = indexing.next_worker_limit(limit, 6, idle)
            trace.append(limit)
        self.assertEqual(trace, [3, 1, 1, 1, 2, 3, 3, 4, 5, 6, 6])


class AdaptiveWorkerBudgetTests(unittest.TestCase):
    def samples(self, idle_fractions, cpu_count=10, step=1000):
        busy = idle = 0
        values = [CpuActivity(busy=0, idle=0)]
        for fraction in idle_fractions:
            idle += int(step * fraction)
            busy += step - int(step * fraction)
            values.append(CpuActivity(busy=busy, idle=idle))
        return iter(values)

    def test_pressure_uses_hysteresis(self):
        events = []
        stream = self.samples([0.9, 0.05, 0.15, 0.25, 0.9])
        budget = indexing.AdaptiveWorkerBudget(
            6, 10, lambda: next(stream), lambda: None, on_pressure=events.append,
        )

        limits = [budget(active) for active in (0, 6, 3, 3, 3)]

        # idle CPUs: 9 → 0.5 (saturated) → 1.5 (hold) → 2.5 (resume) → 9
        self.assertEqual(limits, [6, 3, 3, 4, 5])
        self.assertEqual(events, [True, False])

    def test_memory_pressure_overrides_idle_cpu(self):
        stream = self.samples([0.9] * 6)
        levels = iter(["warn", "warn", "critical", "normal", "normal", "normal"])
        budget = indexing.AdaptiveWorkerBudget(
            6, 10, lambda: next(stream), lambda: None, memory_pressure=lambda: next(levels),
        )

        limits = [budget(0) for _ in range(6)]

        # Idle CPU alone would allow 6 every time.
        self.assertEqual(limits, [3, 3, 1, 2, 3, 4])

    def test_memory_pressure_reports_the_limit_for_pool_shrinking(self):
        stream = self.samples([0.9] * 4)
        levels = iter(["normal", "warn", "critical", "normal"])
        reported = []
        budget = indexing.AdaptiveWorkerBudget(
            6, 10, lambda: next(stream), lambda: None,
            memory_pressure=lambda: next(levels), on_memory_limit=reported.append,
        )
        for _ in range(4):
            budget(0)
        self.assertEqual(reported, [3, 1])

    def test_warn_shrinks_to_half_the_pool_even_while_cpu_is_saturated(self):
        stream = self.samples([0.9, 0.0, 0.0])
        levels = iter(["normal", "warn", "warn"])
        reported = []
        budget = indexing.AdaptiveWorkerBudget(
            6, 10, lambda: next(stream), lambda: None,
            memory_pressure=lambda: next(levels), on_memory_limit=reported.append,
        )
        limits = [budget(0) for _ in range(3)]
        self.assertEqual(limits, [6, 3, 1])
        self.assertEqual(reported, [3, 3])

    def test_failing_memory_probe_is_ignored(self):
        def broken():
            raise OSError("sysctl unavailable")

        stream = self.samples([0.9])
        budget = indexing.AdaptiveWorkerBudget(
            6, 10, lambda: next(stream), lambda: None, memory_pressure=broken,
        )
        self.assertEqual(budget(0), 6)

    def test_load_average_fallback_when_ticks_are_unavailable(self):
        budget = indexing.AdaptiveWorkerBudget(6, 8, lambda: None, lambda: 7.5)
        self.assertEqual(budget(0), 1)
        budget = indexing.AdaptiveWorkerBudget(6, 8, lambda: None, lambda: 1.0)
        self.assertEqual(budget(0), 6)

    def test_failing_probes_trust_the_ceiling_then_hold(self):
        def broken():
            raise OSError("unavailable")

        budget = indexing.AdaptiveWorkerBudget(4, 8, broken, broken)
        self.assertEqual([budget(0), budget(4)], [4, 4])


class RecyclingExecutorTests(unittest.TestCase):
    def test_shrinks_only_downward_after_draining_in_flight_work(self):
        made = []

        def factory(size):
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=size)
            made.append((size, executor))
            return executor

        pool = indexing.RecyclingExecutor(factory, 4)
        slow = pool.submit(lambda: (threading.Event().wait(0.05), "done")[1])
        self.assertFalse(pool.shrink(4))
        self.assertFalse(pool.shrink(6))
        self.assertTrue(pool.shrink(1))
        self.assertTrue(slow.done())
        self.assertEqual(slow.result(), "done")
        self.assertEqual([size for size, _ in made], [4, 1])
        self.assertEqual(pool.submit(lambda: 7).result(), 7)
        pool.shutdown()

    def test_failed_restart_makes_later_submissions_raise(self):
        calls = itertools.count()

        def factory(size):
            if next(calls):
                raise OSError("cannot spawn")
            return concurrent.futures.ThreadPoolExecutor(max_workers=size)

        pool = indexing.RecyclingExecutor(factory, 2)
        with self.assertRaises(OSError):
            pool.shrink(1)
        with self.assertRaises(RuntimeError):
            pool.submit(lambda: 1)
        pool.shutdown()


class RunAdaptiveTests(unittest.TestCase):
    def test_in_flight_work_never_exceeds_the_current_budget(self):
        limits = iter([3, 1, 1, 2, 2, 2] + [2] * 100)
        state = {"limit": None, "submitted": 0, "delivered": 0, "violations": []}

        def budget(active):
            state["limit"] = next(limits)
            return state["limit"]

        def submit(item):
            in_flight = state["submitted"] - state["delivered"]
            if in_flight >= state["limit"]:
                state["violations"].append((item, in_flight, state["limit"]))
            state["submitted"] += 1
            return executor.submit(lambda: (threading.Event().wait(0.005), item * 2)[1])

        results = {}

        def on_result(item, value):
            state["delivered"] += 1
            results[item] = value

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            ticks = itertools.count()
            complete = indexing.run_adaptive(
                range(20), submit, budget, on_result,
                clock=lambda: next(ticks), recheck_s=1,
            )

        self.assertTrue(complete)
        self.assertEqual(state["violations"], [])
        self.assertEqual(results, {item: item * 2 for item in range(20)})

    def test_submission_failure_stops_but_drains_in_flight_results(self):
        submitted = []
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            def submit(item):
                if item == 2:
                    raise RuntimeError("cannot schedule new futures")
                submitted.append(item)
                return executor.submit(lambda: item)

            complete = indexing.run_adaptive(
                range(10), submit, lambda active: 2, results.__setitem__,
            )

        self.assertFalse(complete)
        self.assertEqual(submitted, [0, 1])
        self.assertEqual(results, {0: 0, 1: 1})


if __name__ == "__main__":
    unittest.main()
