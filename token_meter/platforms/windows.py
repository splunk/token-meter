"""Windows per-user path, process, update, and trash policy."""

import ntpath

from .base import (
    PlatformCapabilities,
    PlatformPaths,
    ProcessOptions,
    ProcessPurpose,
    TrashPlan,
    UpdatePlan,
)


CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


class WindowsPlatformServices:
    platform_id = "windows"

    def __init__(self, environment, home):
        self.environment = dict(environment or {})
        self.home = str(
            home or self.environment.get("USERPROFILE") or
            ntpath.expanduser("~")
        )

    def _environment_path(self, name, default):
        value = str(self.environment.get(name) or default)
        if value == "~":
            return self.home
        if value.startswith("~\\") or value.startswith("~/"):
            return ntpath.join(self.home, value[2:])
        return ntpath.normpath(value)

    def resolve_paths(self):
        roaming = self._environment_path(
            "APPDATA", ntpath.join(self.home, "AppData", "Roaming")
        )
        local = self._environment_path(
            "LOCALAPPDATA", ntpath.join(self.home, "AppData", "Local")
        )
        return PlatformPaths(
            config_home=roaming,
            data_home=local,
            cache_home=local,
            claude_desktop_data_roots=(
                ntpath.join(roaming, "Claude"),
                ntpath.join(roaming, "Claude-3p"),
            ),
            cursor_state_db=ntpath.join(
                roaming, "Cursor", "User", "globalStorage", "state.vscdb"
            ),
            cursor_request_logs=ntpath.join(roaming, "Cursor", "logs"),
            opencode_data_root=ntpath.join(local, "opencode"),
            opencode_cache_root=ntpath.join(local, "opencode"),
            default_trash_dir=ntpath.join(local, "Token Meter", "Trash"),
        )

    def process_options(self, purpose):
        try:
            purpose = ProcessPurpose(purpose)
        except ValueError:
            return ProcessOptions(
                supported=False,
                error_code="unsupported_process_purpose",
                message="This process operation is unavailable.",
            )
        flags = CREATE_NO_WINDOW
        if purpose is ProcessPurpose.DETACHED:
            flags |= CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        return ProcessOptions(
            close_fds=True,
            hidden_window=True,
            creation_flags=flags,
        )

    def trash_plan(self, path, override=None, command_available=False):
        destination = self.resolve_paths().default_trash_dir
        if override:
            destination = str(override)
            if destination == "~":
                destination = self.home
            elif destination.startswith("~\\") or destination.startswith("~/"):
                destination = ntpath.join(self.home, destination[2:])
            destination = ntpath.normpath(destination)
        return TrashPlan(
            supported=True,
            strategy="move",
            destination_root=destination,
            destination_label="Trash",
        )

    def update_plan(self, source_root, checkout, status_path):
        script = ntpath.join(str(source_root), "scripts", "update-windows.ps1")
        return UpdatePlan(
            supported=True,
            script_path=script,
            command=(
                "powershell.exe", "-NoLogo", "-NoProfile",
                "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                "-File", script, str(checkout), str(status_path),
            ),
        )

    def agent_launcher(self, source_root):
        return ntpath.join(str(source_root), "scripts", "run-token-meter-mcp.cmd")

    def capabilities(self):
        return PlatformCapabilities(paths=True, detached_process=True, trash=True)

    def power_source(self):
        return None
