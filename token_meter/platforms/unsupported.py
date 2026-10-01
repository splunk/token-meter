"""Bounded capability results for hosts without an implementation."""

from .base import PlatformCapabilities, ProcessOptions, TrashPlan, UpdatePlan


class UnsupportedPlatformServices:
    def __init__(self, platform_id):
        self.platform_id = str(platform_id or "unknown")[:32]

    def resolve_paths(self):
        raise RuntimeError("Platform paths are unavailable on this host.")

    def process_options(self, purpose):
        return ProcessOptions(
            supported=False,
            error_code="unsupported_platform",
            message="This process operation is unavailable on this platform.",
        )

    def trash_plan(self, path, override=None, command_available=False):
        return TrashPlan(
            supported=False,
            error_code="unsupported_platform",
            message="Moving session logs to Trash is unavailable on this platform.",
        )

    def update_plan(self, source_root, checkout, status_path):
        return UpdatePlan(
            supported=False,
            error_code="unsupported_platform",
            message="Software updates are unavailable on this platform.",
        )

    def agent_launcher(self, source_root):
        return ""

    def capabilities(self):
        return PlatformCapabilities(paths=False, detached_process=False, trash=False)

    def power_source(self):
        return None
