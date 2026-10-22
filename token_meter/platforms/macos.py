"""macOS path and lifecycle policy."""

import os
import posixpath
import re
import subprocess
import time

from .base import CpuActivity, PlatformCapabilities, PlatformPaths, TrashPlan
from .common import PosixPlatformServices, expand_home


# <sys/resource.h>: disk I/O policy for the whole process. UTILITY yields to
# interactive I/O without the near-stall THROTTLE can cause under contention.
IOPOL_TYPE_DISK = 0
IOPOL_SCOPE_PROCESS = 0
IOPOL_UTILITY = 4
# <sys/qos.h>. Nice alone barely moves the macOS scheduler; QoS decides core
# placement. Workers spawned from Terminal would otherwise inherit
# USER_INTERACTIVE and compete with the user's foreground work.
QOS_CLASS_DEFAULT = 0x15
QOS_CLASS_UTILITY = 0x11
PMSET_TIMEOUT_S = 2.0
# <mach/host_info.h>: HOST_CPU_LOAD_INFO returns user, system, idle, nice ticks.
HOST_CPU_LOAD_INFO = 3
CPU_STATE_COUNT = 4
_mach_host = {}
# <sys/kern_memorystatus.h>: the kernel's own memory-pressure level, the same
# signal behind Activity Monitor's pressure graph.
_MEMORY_PRESSURE_LEVELS = {1: "normal", 2: "warn", 4: "critical"}
_LOW_POWER_LINE = re.compile(r"^\s*(lowpowermode|powermode)\s+(\d+)\s*$", re.MULTILINE)


def _run_pmset(args):
    completed = subprocess.run(
        ("/usr/bin/pmset",) + tuple(args), capture_output=True, text=True,
        timeout=PMSET_TIMEOUT_S, check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""


def _host_cpu_ticks():
    import ctypes
    if "libc" not in _mach_host:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        libc.mach_host_self.restype = ctypes.c_uint
        # One send right per process, reused for every sample.
        _mach_host["port"] = libc.mach_host_self()
        _mach_host["libc"] = libc
    ticks = (ctypes.c_uint * CPU_STATE_COUNT)()
    count = ctypes.c_uint(CPU_STATE_COUNT)
    result = _mach_host["libc"].host_statistics(
        _mach_host["port"], HOST_CPU_LOAD_INFO, ctypes.byref(ticks), ctypes.byref(count),
    )
    if result != 0 or count.value < CPU_STATE_COUNT:
        return None
    return tuple(int(value) for value in ticks)


def _vm_pressure_level():
    import ctypes
    sysctlbyname = _mach_host.get("sysctlbyname")
    if sysctlbyname is None:
        sysctlbyname = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sysctlbyname
        sysctlbyname.argtypes = (
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p, ctypes.c_size_t,
        )
        sysctlbyname.restype = ctypes.c_int
        _mach_host["sysctlbyname"] = sysctlbyname
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    result = sysctlbyname(
        b"kern.memorystatus_vm_pressure_level", ctypes.byref(value), ctypes.byref(size), None, 0,
    )
    return int(value.value) if result == 0 else None


def parse_pmset_power(battery_output, settings_output):
    """Interpret ``pmset -g batt`` and ``pmset -g`` text; None when neither is readable."""
    battery_output = str(battery_output or "")
    settings_output = str(settings_output or "")
    on_battery = None
    if "'Battery Power'" in battery_output:
        on_battery = True
    elif "'AC Power'" in battery_output:
        on_battery = False
    low_power = None
    for name, value in _LOW_POWER_LINE.findall(settings_output):
        # ``powermode`` is 0 automatic, 1 low, 2 high; ``lowpowermode`` is 0/1.
        low_power = bool(low_power) or value == "1"
    if on_battery or low_power:
        return True
    if on_battery is None and low_power is None:
        return None
    return False


class MacOSPlatformServices(PosixPlatformServices):
    platform_id = "macos"

    def resolve_paths(self):
        config_home = self._environment_path("XDG_CONFIG_HOME", "~/.config")
        data_home = self._environment_path("XDG_DATA_HOME", "~/.local/share")
        cache_home = self._environment_path("XDG_CACHE_HOME", "~/.cache")
        application_support = posixpath.join(self.home, "Library", "Application Support")
        return PlatformPaths(
            config_home=config_home,
            data_home=data_home,
            cache_home=cache_home,
            claude_desktop_data_roots=(
                posixpath.join(application_support, "Claude"),
                posixpath.join(application_support, "Claude-3p"),
            ),
            cursor_state_db=posixpath.join(
                application_support, "Cursor", "User", "globalStorage", "state.vscdb"
            ),
            cursor_request_logs=posixpath.join(application_support, "Cursor", "logs"),
            opencode_data_root=posixpath.join(data_home, "opencode"),
            opencode_cache_root=posixpath.join(cache_home, "opencode"),
            default_trash_dir=posixpath.join(self.home, ".Trash"),
        )

    def trash_plan(self, path, override=None, command_available=False):
        destination_root = (
            expand_home(override, self.home)
            if override
            else self.resolve_paths().default_trash_dir
        )
        return TrashPlan(
            supported=True,
            strategy="move",
            destination_root=destination_root,
            destination_label="macOS Trash",
        )

    _power_cache = (0.0, None)

    def power_source(self):
        """Read the current power source from pmset, cached for 30 seconds."""
        checked_at, value = self._power_cache
        if time.monotonic() - checked_at < 30:
            return value
        try:
            output = subprocess.run(
                ["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=2,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            output = ""
        value = "battery" if "'Battery Power'" in output else "ac" if "'AC Power'" in output else None
        type(self)._power_cache = (time.monotonic(), value)
        return value

    def capabilities(self):
        # Cold-index workers are verified on macOS hosts only; Linux keeps the
        # sequential path until they are proven under systemd and containers.
        return PlatformCapabilities(
            paths=True, detached_process=True, trash=True, parallel_index=True,
        )

    def lower_background_priority(self):
        lowered = super().lower_background_priority()
        try:
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            libc.setiopolicy_np(IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS, IOPOL_UTILITY)
        except (AttributeError, OSError, ImportError):
            pass
        self.set_background_mode(False)
        return lowered

    def set_background_mode(self, yielding):
        try:
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            qos = QOS_CLASS_UTILITY if yielding else QOS_CLASS_DEFAULT
            return libc.pthread_set_qos_class_self_np(qos, 0) == 0
        except (AttributeError, OSError, ImportError):
            return False

    def memory_pressure(self, reader=None):
        try:
            level = (reader or _vm_pressure_level)()
        except (AttributeError, OSError, ImportError, TypeError, ValueError):
            return None
        return _MEMORY_PRESSURE_LEVELS.get(level)

    def cpu_activity(self, reader=None):
        try:
            ticks = (reader or _host_cpu_ticks)()
            if not ticks:
                return None
            user, system, idle, nice = (int(value) for value in ticks)
        except (AttributeError, OSError, ImportError, TypeError, ValueError):
            return None
        return CpuActivity(busy=user + system + nice, idle=idle)

    def power_constrained(self, run=None):
        run = run or _run_pmset
        try:
            battery = run(("-g", "batt"))
            settings = run(("-g",))
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return parse_pmset_power(battery, settings)
