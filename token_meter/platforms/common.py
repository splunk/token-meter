"""Shared POSIX platform behavior."""

import os
import posixpath

from .base import PlatformCapabilities, ProcessOptions, ProcessPurpose, UpdatePlan


BACKGROUND_NICE_INCREMENT = 10


def expand_home(path, home):
    value = str(path or "")
    if value == "~":
        return home
    if value.startswith("~/"):
        return posixpath.join(home, value[2:])
    return value


class PosixPlatformServices:
    def __init__(self, environment, home):
        self.environment = dict(environment or {})
        self.home = str(home or self.environment.get("HOME") or os.path.expanduser("~"))

    def _environment_path(self, name, default):
        return expand_home(self.environment.get(name, default), self.home)

    def process_options(self, purpose):
        try:
            purpose = ProcessPurpose(purpose)
        except ValueError:
            return ProcessOptions(
                supported=False,
                error_code="unsupported_process_purpose",
                message="This process operation is unavailable.",
            )
        if purpose is ProcessPurpose.DETACHED:
            return ProcessOptions(close_fds=True, start_new_session=True)
        return ProcessOptions()

    def capabilities(self):
        return PlatformCapabilities(paths=True, detached_process=True, trash=True)

    def update_plan(self, source_root, checkout, status_path, retry_revision=""):
        script = posixpath.join(str(source_root), "scripts", "update")
        return UpdatePlan(
            supported=True,
            script_path=script,
            command=(script, str(checkout), str(status_path)),
        )

    def agent_launcher(self, source_root):
        return posixpath.join(str(source_root), "scripts", "run-token-meter-mcp")

    def lower_background_priority(self):
        try:
            os.nice(BACKGROUND_NICE_INCREMENT)
        except (AttributeError, OSError):
            return False
        return True

    def power_constrained(self):
        return None

    def memory_pressure(self):
        return None

    def cpu_activity(self):
        return None

    def set_background_mode(self, yielding):
        return False

    def power_source(self):
        return None
