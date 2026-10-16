import unittest

from token_meter.platforms.base import ProcessPurpose
from token_meter.platforms.registry import platform_services


class PlatformServicesTests(unittest.TestCase):
    def test_macos_paths_preserve_library_and_xdg_precedence(self):
        platform = platform_services(
            "darwin",
            environment={
                "XDG_CONFIG_HOME": "/xdg/config",
                "XDG_DATA_HOME": "/xdg/data",
                "XDG_CACHE_HOME": "/xdg/cache",
            },
            home="/Users/example",
        )

        paths = platform.resolve_paths()

        self.assertEqual(paths.claude_desktop_data_roots, (
            "/Users/example/Library/Application Support/Claude",
            "/Users/example/Library/Application Support/Claude-3p",
        ))
        self.assertEqual(
            paths.cursor_state_db,
            "/Users/example/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        )
        self.assertEqual(paths.cursor_request_logs,
                         "/Users/example/Library/Application Support/Cursor/logs")
        self.assertEqual(paths.opencode_data_root, "/xdg/data/opencode")
        self.assertEqual(paths.opencode_cache_root, "/xdg/cache/opencode")
        self.assertEqual(paths.default_trash_dir, "/Users/example/.Trash")

    def test_linux_paths_use_xdg_overrides(self):
        platform = platform_services(
            "linux",
            environment={
                "XDG_CONFIG_HOME": "/xdg/config",
                "XDG_DATA_HOME": "/xdg/data",
                "XDG_CACHE_HOME": "/xdg/cache",
            },
            home="/home/example",
        )

        paths = platform.resolve_paths()

        self.assertEqual(paths.claude_desktop_data_roots,
                         ("/xdg/config/Claude", "/xdg/config/Claude-3p"))
        self.assertEqual(
            paths.cursor_state_db,
            "/xdg/config/Cursor/User/globalStorage/state.vscdb",
        )
        self.assertEqual(paths.cursor_request_logs, "/xdg/config/Cursor/logs")
        self.assertEqual(paths.opencode_data_root, "/xdg/data/opencode")
        self.assertEqual(paths.opencode_cache_root, "/xdg/cache/opencode")
        self.assertEqual(paths.default_trash_dir, "/xdg/data/Trash/files")

    def test_posix_process_options_keep_detached_update_behavior(self):
        for platform_name in ("darwin", "linux"):
            with self.subTest(platform_name=platform_name):
                options = platform_services(
                    platform_name, environment={}, home="/home/example"
                ).process_options(ProcessPurpose.DETACHED)

                self.assertTrue(options.supported)
                self.assertTrue(options.close_fds)
                self.assertTrue(options.start_new_session)
                self.assertFalse(options.hidden_window)
                self.assertEqual(options.creation_flags, 0)

    def test_windows_paths_use_roaming_and_local_application_data(self):
        platform = platform_services(
            "win32",
            environment={
                "APPDATA": r"C:\Users\example\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\example\AppData\Local",
            },
            home=r"C:\Users\example",
        )

        paths = platform.resolve_paths()

        self.assertEqual(paths.config_home, r"C:\Users\example\AppData\Roaming")
        self.assertEqual(paths.data_home, r"C:\Users\example\AppData\Local")
        self.assertEqual(paths.cache_home, r"C:\Users\example\AppData\Local")
        self.assertEqual(paths.claude_desktop_data_roots, (
            r"C:\Users\example\AppData\Roaming\Claude",
            r"C:\Users\example\AppData\Roaming\Claude-3p",
        ))
        self.assertEqual(
            paths.cursor_state_db,
            r"C:\Users\example\AppData\Roaming\Cursor\User\globalStorage\state.vscdb",
        )
        self.assertEqual(
            paths.cursor_request_logs,
            r"C:\Users\example\AppData\Roaming\Cursor\logs",
        )
        self.assertEqual(
            paths.default_trash_dir,
            r"C:\Users\example\AppData\Local\Token Meter\Trash",
        )

    def test_windows_process_trash_and_update_launch_are_owned_platform_decisions(self):
        platform = platform_services(
            "windows",
            environment={
                "APPDATA": r"C:\Users\example\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\example\AppData\Local",
            },
            home=r"C:\Users\example",
        )

        options = platform.process_options(ProcessPurpose.DETACHED)
        trash = platform.trash_plan(r"C:\traces\session.jsonl")
        update = platform.update_plan(
            r"C:\Token Meter", r"C:\Token Meter\source",
            r"C:\Token Meter\update-status.json",
        )

        self.assertTrue(options.supported)
        self.assertTrue(options.close_fds)
        self.assertFalse(options.start_new_session)
        self.assertTrue(options.hidden_window)
        self.assertEqual(options.creation_flags, 0x08000208)
        self.assertEqual(trash.strategy, "move")
        self.assertEqual(
            trash.destination_root,
            r"C:\Users\example\AppData\Local\Token Meter\Trash",
        )
        self.assertEqual(trash.destination_label, "Trash")
        self.assertEqual(update.script_path, r"C:\Token Meter\scripts\update-windows.ps1")
        self.assertEqual(update.command, (
            "powershell.exe", "-NoLogo", "-NoProfile",
            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", r"C:\Token Meter\scripts\update-windows.ps1",
            r"C:\Token Meter\source", r"C:\Token Meter\update-status.json",
        ))
        self.assertEqual(
            platform.agent_launcher(r"C:\Token Meter"),
            r"C:\Token Meter\scripts\run-token-meter-mcp.cmd",
        )

    def test_posix_update_plan_preserves_existing_helper_contract(self):
        platform = platform_services("darwin", environment={}, home="/Users/example")

        update = platform.update_plan(
            "/opt/token-meter", "/Users/example/source", "/Users/example/status.json"
        )

        self.assertEqual(update.script_path, "/opt/token-meter/scripts/update")
        self.assertEqual(update.command, (
            "/opt/token-meter/scripts/update",
            "/Users/example/source",
            "/Users/example/status.json",
        ))
        self.assertEqual(
            platform.agent_launcher("/opt/token-meter"),
            "/opt/token-meter/scripts/run-token-meter-mcp",
        )

    def test_linux_update_plan_runs_the_helper_outside_the_server_cgroup(self):
        platform = platform_services("linux", environment={}, home="/home/example")

        update = platform.update_plan(
            "/opt/token-meter", "/home/example/source", "/home/example/status.json",
            systemd_run="/usr/bin/systemd-run",
        )
        retry = platform.update_plan(
            "/opt/token-meter", "/home/example/source", "/home/example/status.json",
            retry_revision="b" * 40, systemd_run="/usr/bin/systemd-run",
        )

        self.assertTrue(update.supported)
        self.assertEqual(update.script_path, "/opt/token-meter/scripts/update")
        self.assertEqual(update.command, (
            "/usr/bin/systemd-run", "--user", "--quiet", "--collect",
            "--unit=token-meter-update", "--description=Token Meter update", "--",
            "/opt/token-meter/scripts/update",
            "/home/example/source",
            "/home/example/status.json",
        ))
        self.assertIn(
            "--setenv=TOKEN_METER_UPDATE_RETRY_REVISION=" + "b" * 40, retry.command,
        )
        self.assertLess(
            retry.command.index("--setenv=TOKEN_METER_UPDATE_RETRY_REVISION=" + "b" * 40),
            retry.command.index("--"),
        )

    def test_linux_update_plan_falls_back_to_the_helper_without_systemd_run(self):
        platform = platform_services("linux", environment={}, home="/home/example")

        update = platform.update_plan(
            "/opt/token-meter", "/home/example/source", "/home/example/status.json",
            systemd_run="",
        )

        self.assertEqual(update.command, (
            "/opt/token-meter/scripts/update",
            "/home/example/source",
            "/home/example/status.json",
        ))

    def test_linux_prefers_gio_and_macos_uses_owned_trash_directory(self):
        linux = platform_services("linux", environment={}, home="/home/example")
        macos = platform_services("darwin", environment={}, home="/Users/example")

        linux_plan = linux.trash_plan("/logs/session.jsonl", command_available=True)
        macos_plan = macos.trash_plan("/logs/session.jsonl", command_available=True)

        self.assertEqual(linux_plan.strategy, "command")
        self.assertEqual(linux_plan.command, ("gio", "trash", "/logs/session.jsonl"))
        self.assertEqual(linux_plan.destination_label, "Trash")
        self.assertEqual(macos_plan.strategy, "move")
        self.assertEqual(macos_plan.destination_root, "/Users/example/.Trash")
        self.assertEqual(macos_plan.destination_label, "macOS Trash")

    def test_unsupported_platform_returns_bounded_capability_results(self):
        platform = platform_services(
            "plan9", environment={"HOME": "/private/sentinel"}, home="/private/sentinel"
        )

        process = platform.process_options(ProcessPurpose.DETACHED)
        trash = platform.trash_plan("/private/sentinel/session.jsonl")
        update = platform.update_plan(
            "/private/sentinel", "/private/sentinel/source",
            "/private/sentinel/status.json",
        )

        self.assertFalse(process.supported)
        self.assertEqual(process.error_code, "unsupported_platform")
        self.assertFalse(trash.supported)
        self.assertEqual(trash.error_code, "unsupported_platform")
        self.assertFalse(update.supported)
        self.assertEqual(update.error_code, "unsupported_platform")
        self.assertNotIn("/private/sentinel", process.message)
        self.assertNotIn("/private/sentinel", trash.message)
        self.assertNotIn("/private/sentinel", update.message)


if __name__ == "__main__":
    unittest.main()
