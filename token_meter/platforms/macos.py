"""macOS path and lifecycle policy."""

import os
import subprocess
import time

from .base import PlatformPaths, TrashPlan
from .common import PosixPlatformServices, expand_home


class MacOSPlatformServices(PosixPlatformServices):
    platform_id = "macos"

    def resolve_paths(self):
        config_home = self._environment_path("XDG_CONFIG_HOME", "~/.config")
        data_home = self._environment_path("XDG_DATA_HOME", "~/.local/share")
        cache_home = self._environment_path("XDG_CACHE_HOME", "~/.cache")
        application_support = os.path.join(self.home, "Library", "Application Support")
        return PlatformPaths(
            config_home=config_home,
            data_home=data_home,
            cache_home=cache_home,
            claude_desktop_data_roots=(
                os.path.join(application_support, "Claude"),
                os.path.join(application_support, "Claude-3p"),
            ),
            cursor_state_db=os.path.join(
                application_support, "Cursor", "User", "globalStorage", "state.vscdb"
            ),
            cursor_request_logs=os.path.join(application_support, "Cursor", "logs"),
            opencode_data_root=os.path.join(data_home, "opencode"),
            opencode_cache_root=os.path.join(cache_home, "opencode"),
            default_trash_dir=os.path.join(self.home, ".Trash"),
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
