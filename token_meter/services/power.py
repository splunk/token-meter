"""Low power mode: when Token Meter should slow itself down to save battery.

Automatic mode follows the host: on battery or in the operating system's own
low-power mode, the server stretches its watcher cadences, the dashboard stops
ambient motion and polls less often, and the menu bar polls less often. The
host is probed at most once per ``POWER_PROBE_TTL_S``; an unreadable host is
unknown and never turns automatic mode on.
"""

import subprocess
import threading
import time

LOW_POWER_MODES = ("auto", "on", "off")
DEFAULT_LOW_POWER_MODE = "auto"
POWER_PROBE_TTL_S = 30.0
# Watcher sleep, trace rebuild, cross-session refresh, and discovery intervals
# stretch by this factor while low power mode is active.
LOW_POWER_CADENCE_SCALE = 4.0
_POWER_SOURCES = ("battery", "ac")


def normalize_power_settings(values):
    """Validate the machine-wide power preference shared by every surface."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("Power settings must be an object.")
    mode = values.get("low_power_mode", DEFAULT_LOW_POWER_MODE)
    if mode not in LOW_POWER_MODES:
        raise ValueError("Low power mode must be Automatic, On, or Off.")
    return {"low_power_mode": mode}


def low_power_status(mode, constrained, source):
    """Public status: whether low power is active and why."""
    mode = mode if mode in LOW_POWER_MODES else DEFAULT_LOW_POWER_MODE
    source = source if source in _POWER_SOURCES else None
    if mode == "on":
        active, reason = True, "setting"
    elif mode == "off":
        active, reason = False, None
    else:
        active = constrained is True
        # Constrained while not on battery means the OS low-power setting.
        reason = ("battery" if source == "battery" else "system") if active else None
    return {"mode": mode, "active": active, "reason": reason, "power_source": source}


def cadence_scale(status):
    return LOW_POWER_CADENCE_SCALE if (status or {}).get("active") else 1.0


class PowerMonitor:
    """Caches the host's power reading; ``refresh`` probes at most once per TTL."""

    def __init__(self, constrained_probe, source_probe=None, clock=time.monotonic,
                 ttl=POWER_PROBE_TTL_S):
        self._constrained_probe = constrained_probe
        self._source_probe = source_probe
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._checked_at = None
        self._reading = (None, None)

    @staticmethod
    def _probe(probe):
        if probe is None:
            return None
        try:
            return probe()
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def refresh(self, force=False):
        """Probe when the cached reading is older than the TTL; True when it changed."""
        now = self._clock()
        with self._lock:
            if not force and self._checked_at is not None and now - self._checked_at < self._ttl:
                return False
            self._checked_at = now
        constrained = self._probe(self._constrained_probe)
        source = self._probe(self._source_probe)
        reading = (
            constrained if isinstance(constrained, bool) else None,
            source if source in _POWER_SOURCES else None,
        )
        with self._lock:
            changed = reading != self._reading
            self._reading = reading
        return changed

    def reading(self):
        with self._lock:
            return self._reading
