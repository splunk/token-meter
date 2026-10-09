"""Linux/XDG path and lifecycle policy."""

import glob
import os

from .base import PlatformPaths, TrashPlan
from .common import PosixPlatformServices, expand_home


class LinuxPlatformServices(PosixPlatformServices):
    platform_id = "linux"

    def resolve_paths(self):
        config_home = self._environment_path("XDG_CONFIG_HOME", "~/.config")
        data_home = self._environment_path("XDG_DATA_HOME", "~/.local/share")
        cache_home = self._environment_path("XDG_CACHE_HOME", "~/.cache")
        return PlatformPaths(
            config_home=config_home,
            data_home=data_home,
            cache_home=cache_home,
            claude_desktop_data_roots=(
                os.path.join(config_home, "Claude"),
                os.path.join(config_home, "Claude-3p"),
            ),
            cursor_state_db=os.path.join(
                config_home, "Cursor", "User", "globalStorage", "state.vscdb"
            ),
            cursor_request_logs=os.path.join(config_home, "Cursor", "logs"),
            opencode_data_root=os.path.join(data_home, "opencode"),
            opencode_cache_root=os.path.join(cache_home, "opencode"),
            default_trash_dir=os.path.join(data_home, "Trash", "files"),
        )

    def trash_plan(self, path, override=None, command_available=False):
        if not override and command_available:
            return TrashPlan(
                supported=True,
                strategy="command",
                destination_label="Trash",
                command=("gio", "trash", str(path)),
            )
        destination_root = (
            expand_home(override, self.home)
            if override
            else self.resolve_paths().default_trash_dir
        )
        return TrashPlan(
            supported=True,
            strategy="move",
            destination_root=destination_root,
            destination_label="Trash",
        )

    def power_source(self):
        """Report mains power from sysfs; None when no supply is described."""
        seen = False
        for supply in glob.glob("/sys/class/power_supply/*"):
            try:
                with open(os.path.join(supply, "type"), encoding="utf-8") as handle:
                    kind = handle.read().strip()
                if kind != "Mains":
                    continue
                with open(os.path.join(supply, "online"), encoding="utf-8") as handle:
                    online = handle.read().strip()
            except OSError:
                continue
            seen = True
            if online == "1":
                return "ac"
        return "battery" if seen else None
