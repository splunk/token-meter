"""Immutable contracts shared by host-platform implementations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple


class ProcessPurpose(str, Enum):
    DEFAULT = "default"
    DETACHED = "detached"


@dataclass(frozen=True)
class PlatformPaths:
    config_home: str
    data_home: str
    cache_home: str
    claude_desktop_data_roots: Tuple[str, ...]
    cursor_state_db: str
    cursor_request_logs: str
    opencode_data_root: str
    opencode_cache_root: str
    default_trash_dir: str


@dataclass(frozen=True)
class ProcessOptions:
    supported: bool = True
    close_fds: bool = False
    start_new_session: bool = False
    hidden_window: bool = False
    creation_flags: int = 0
    error_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class UpdatePlan:
    supported: bool
    script_path: str = ""
    command: Tuple[str, ...] = ()
    error_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class TrashPlan:
    supported: bool
    strategy: str = ""
    destination_root: Optional[str] = None
    destination_label: str = "Trash"
    command: Tuple[str, ...] = ()
    error_code: str = ""
    message: str = ""


@dataclass(frozen=True)
class CpuActivity:
    """Cumulative CPU ticks across all cores."""

    busy: int
    idle: int


@dataclass(frozen=True)
class PlatformCapabilities:
    paths: bool
    detached_process: bool
    trash: bool
    # Cold-index worker processes. Enable only where spawned workers, their
    # priority, and their exit with the server use standard host mechanisms.
    parallel_index: bool = False


class PlatformServices(Protocol):
    platform_id: str

    def resolve_paths(self) -> PlatformPaths:
        ...

    def process_options(self, purpose: ProcessPurpose) -> ProcessOptions:
        ...

    def trash_plan(
        self, path: str, override: Optional[str] = None,
        command_available: bool = False,
    ) -> TrashPlan:
        ...

    def update_plan(
        self, source_root: str, checkout: str, status_path: str,
    ) -> UpdatePlan:
        ...

    def agent_launcher(self, source_root: str) -> str:
        ...

    def capabilities(self) -> PlatformCapabilities:
        ...

    def lower_background_priority(self) -> bool:
        """Best-effort: yield CPU and disk to foreground work in this process."""
        ...

    def power_constrained(self) -> Optional[bool]:
        """True on battery or low-power mode, False on mains, None when unknown."""
        ...

    def set_background_mode(self, yielding: bool) -> bool:
        """Best-effort: switch this thread between normal and yielding scheduling."""
        ...

    def memory_pressure(self) -> Optional[str]:
        """``"normal"``, ``"warn"``, or ``"critical"``; None when unknown."""
        ...

    def cpu_activity(self) -> Optional["CpuActivity"]:
        """Cumulative host CPU ticks, or None when the host does not expose them."""
        ...
