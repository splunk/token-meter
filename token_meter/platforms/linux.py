"""Linux/XDG path and lifecycle policy."""

import glob
import os
import posixpath
import shutil

from .base import CpuActivity, PlatformPaths, TrashPlan, UpdatePlan
from .common import PosixPlatformServices, expand_home


POWER_SUPPLY_ROOT = "/sys/class/power_supply"
POWER_SUPPLY_MAX_ENTRIES = 32
PROC_STAT_PATH = "/proc/stat"
UPDATE_UNIT_NAME = "token-meter-update"


def parse_proc_stat(text):
    """Return aggregate CPU ticks from ``/proc/stat`` text, or None."""
    for line in str(text or "").splitlines():
        fields = line.split()
        if not fields or fields[0] != "cpu":
            continue
        try:
            values = [int(value) for value in fields[1:9]]
        except ValueError:
            return None
        if len(values) < 4:
            return None
        values += [0] * (8 - len(values))
        user, nice, system, idle, iowait, irq, softirq, steal = values
        return CpuActivity(
            busy=user + nice + system + irq + softirq + steal,
            idle=idle + iowait,
        )
    return None


def _read_supply_value(root, name, field):
    try:
        with open(posixpath.join(root, name, field), encoding="utf-8") as handle:
            return handle.read(64).strip()
    except (OSError, UnicodeDecodeError):
        return ""


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
                posixpath.join(config_home, "Claude"),
                posixpath.join(config_home, "Claude-3p"),
            ),
            cursor_state_db=posixpath.join(
                config_home, "Cursor", "User", "globalStorage", "state.vscdb"
            ),
            cursor_request_logs=posixpath.join(config_home, "Cursor", "logs"),
            opencode_data_root=posixpath.join(data_home, "opencode"),
            opencode_cache_root=posixpath.join(cache_home, "opencode"),
            default_trash_dir=posixpath.join(data_home, "Trash", "files"),
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

    def update_plan(self, source_root, checkout, status_path, retry_revision="",
                    systemd_run=None):
        plan = super().update_plan(source_root, checkout, status_path, retry_revision)
        # Only a server started by systemd shares a cgroup with its children;
        # elsewhere a user manager may be absent and systemd-run would fail.
        if not self.environment.get("INVOCATION_ID"):
            return plan
        if systemd_run is None:
            systemd_run = shutil.which("systemd-run", path=self.environment.get("PATH"))
        if not systemd_run:
            return plan
        # systemd tracks the server's cgroup, not its session: a setsid child is
        # still stopped with the server when the installer restarts it. A
        # transient user unit survives that restart, but starts from the user
        # manager's environment rather than this process's.
        command = [
            systemd_run, "--user", "--quiet", "--collect",
            f"--unit={UPDATE_UNIT_NAME}",
            "--description=Token Meter update",
        ]
        if retry_revision:
            command.append(f"--setenv=TOKEN_METER_UPDATE_RETRY_REVISION={retry_revision}")
        command.append("--")
        command.extend(plan.command)
        return UpdatePlan(
            supported=True, script_path=plan.script_path, command=tuple(command),
        )

    def cpu_activity(self, path=PROC_STAT_PATH):
        try:
            with open(path, encoding="utf-8") as handle:
                return parse_proc_stat(handle.read(4096))
        except (OSError, UnicodeDecodeError):
            return None

    def power_constrained(self, root=POWER_SUPPLY_ROOT):
        try:
            names = sorted(os.listdir(root))[:POWER_SUPPLY_MAX_ENTRIES]
        except OSError:
            return None
        mains_offline = mains_online = battery_discharging = False
        for name in names:
            # Peripheral batteries (mice, headsets) report scope "Device".
            if _read_supply_value(root, name, "scope") == "Device":
                continue
            kind = _read_supply_value(root, name, "type")
            online = _read_supply_value(root, name, "online")
            if kind in ("Mains", "USB") and online == "1":
                mains_online = True
            elif kind == "Mains" and online == "0":
                mains_offline = True
            elif kind == "Battery":
                if _read_supply_value(root, name, "status") == "Discharging":
                    battery_discharging = True
        if mains_online:
            return False
        # An offline mains adapter means the host is running from its battery;
        # an idle USB-C port alone says nothing about the host's power source.
        if battery_discharging or mains_offline:
            return True
        return None
