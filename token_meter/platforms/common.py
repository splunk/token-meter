"""Shared POSIX platform behavior."""

import os

from .base import PlatformCapabilities, ProcessOptions, ProcessPurpose, UpdatePlan


def expand_home(path, home):
    value = str(path or "")
    if value == "~":
        return home
    if value.startswith("~/"):
        return os.path.join(home, value[2:])
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

    def update_plan(self, source_root, checkout, status_path):
        script = os.path.join(str(source_root), "scripts", "update")
        return UpdatePlan(
            supported=True,
            script_path=script,
            command=(script, str(checkout), str(status_path)),
        )

    def agent_launcher(self, source_root):
        return os.path.join(str(source_root), "scripts", "run-token-meter-mcp")

    def power_source(self):
        return None
