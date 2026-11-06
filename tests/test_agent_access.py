import unittest
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter
from tests.dashboard_page import DASHBOARD_PAGE


class AgentAccessTests(unittest.TestCase):
    _real_app_running = staticmethod(meter.claude_app_running)

    def setUp(self):
        patcher = mock.patch.object(meter, "claude_app_running", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_client_environment_prepends_wrapper_runtime_directory(self):
        with mock.patch.dict(meter.os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            env = meter.agent_client_environment("/Users/test/.nvm/versions/node/v24/bin/codex")
        self.assertEqual(env["PATH"].split(":" )[0], "/Users/test/.nvm/versions/node/v24/bin")
        self.assertIn("/usr/bin", env["PATH"].split(":"))

    def test_client_environment_keeps_symlink_directory_for_sibling_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper_dir = Path(tmp) / "node" / "bin"
            target_dir = Path(tmp) / "package" / "bin"
            wrapper_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            target = target_dir / "codex.js"
            target.write_text("#!/usr/bin/env node\n")
            wrapper = wrapper_dir / "codex"
            wrapper.symlink_to(target)
            env = meter.agent_client_environment(str(wrapper))
        self.assertEqual(env["PATH"].split(":")[0], str(wrapper_dir))

    def test_client_discovery_checks_user_bin_when_service_path_is_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / ".local" / "bin" / "claude"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            with mock.patch.object(meter.os.path, "expanduser",
                                   side_effect=lambda value: tmp if value == "~" else value):
                found = meter.agent_client_executable("claude", which=lambda name: None)
        self.assertEqual(found, str(binary))

    def test_connection_commands_use_fixed_vectors_and_user_scope(self):
        launcher = "/Applications/Token Meter/bin/token-meter-mcp"
        codex = meter.agent_access_command("codex", True, launcher=launcher, cli_path="/usr/bin/codex")
        claude = meter.agent_access_command("claude", True, launcher=launcher, cli_path="/usr/bin/claude")
        self.assertEqual(codex, ["/usr/bin/codex", "mcp", "add", "--env",
                                "TOKEN_METER_CALLER=codex", "tokenmeter", "--", launcher])
        self.assertEqual(claude, ["/usr/bin/claude", "mcp", "add", "--transport", "stdio",
                                 "--scope", "user", "tokenmeter", "--env",
                                 "TOKEN_METER_CALLER=claude", "--", launcher])

    def test_codex_status_requires_exact_launcher_and_caller_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)

            class Completed:
                returncode = 0
                stdout = json.dumps({"enabled": True, "transport": {
                    "type": "stdio", "command": str(launcher), "args": [],
                    "env": {"TOKEN_METER_CALLER": "codex"},
                }})
                stderr = ""

            status = meter.agent_access_client_status(
                "codex", launcher=str(launcher), runner=lambda *a, **k: Completed(),
                which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(status["connected"])
        self.assertFalse(status["conflict"])
        self.assertNotIn("/usr/bin/codex", status["connect_command"])

    def test_windows_codex_status_check_does_not_create_a_console_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            observed = {}

            def runner(command, **kwargs):
                observed.update(kwargs)
                return meter.subprocess.CompletedProcess(command, 1, "", "missing")

            windows = meter.platform_services(
                "windows", environment={"USERPROFILE": r"C:\Users\example"},
                home=r"C:\Users\example",
            )
            with mock.patch.object(meter, "_PLATFORM_SERVICES", windows):
                meter.agent_access_client_status(
                    "codex", launcher=str(launcher), runner=runner,
                    which=lambda name: fr"C:\Tools\{name}.exe",
                )

        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_windows_connection_change_does_not_create_a_console_window(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}
        after = {**before, "configured": True, "connected": True}
        states = iter((before, after))
        observed = {}

        def runner(command, **kwargs):
            observed.update(kwargs)
            return meter.subprocess.CompletedProcess(command, 0, "", "")

        windows = meter.platform_services(
            "windows", environment={"USERPROFILE": r"C:\Users\example"},
            home=r"C:\Users\example",
        )
        with mock.patch.object(meter, "_PLATFORM_SERVICES", windows), \
                mock.patch.object(meter, "agent_access_launcher",
                                  return_value=r"C:\Token Meter\token-meter-mcp.cmd"), \
                mock.patch.object(meter, "agent_client_executable",
                                  return_value=r"C:\Tools\codex.exe"):
            result = meter.set_agent_access(
                "codex", True, runner=runner,
                status_getter=lambda client: next(states),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_claude_status_reads_only_the_user_scope_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": str(launcher), "args": [],
                "env": {"TOKEN_METER_CALLER": "claude"},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}", claude_desktop_config_paths=[])
        self.assertTrue(status["connected"])
        self.assertEqual(status["label"], "Claude")
        self.assertEqual(status["status"], "Connected to Claude Code")

    def test_different_existing_entry_is_reported_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": "/tmp/different", "args": [], "env": {},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}", claude_desktop_config_paths=[])
        self.assertFalse(status["connected"])
        self.assertTrue(status["conflict"])
        self.assertEqual(status["status"], "Existing entry differs from this install")

    def test_conflicting_entry_requires_explicit_repair_confirmation(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}
        runner = mock.Mock()
        result = meter.set_agent_access("codex", True, runner=runner,
                                        status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflict"])
        self.assertIn("Confirm repair", result["error"])
        runner.assert_not_called()

    def test_repair_replaces_only_named_entry_and_verifies_connection(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}
        after = {**before, "configured": True, "connected": True, "conflict": False}
        states = iter((before, after))
        commands = []

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv, **kwargs):
            commands.append(argv)
            self.assertNotIn("shell", kwargs)
            return Completed()

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"), \
                mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/codex"):
            result = meter.set_agent_access("codex", True, repair=True, runner=runner,
                                            status_getter=lambda client: next(states))
        self.assertTrue(result["ok"])
        self.assertTrue(result["repaired"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(commands, [
            ["/usr/bin/codex", "mcp", "remove", "tokenmeter"],
            ["/usr/bin/codex", "mcp", "add", "--env", "TOKEN_METER_CALLER=codex",
             "tokenmeter", "--", "/tmp/token-meter-mcp"],
        ])

    def test_repair_stops_when_existing_entry_cannot_be_removed(self):
        before = {"label": "Claude Code", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}

        class Completed:
            returncode = 1
            stdout = ""
            stderr = "entry is locked\n"

        runner = mock.Mock(return_value=Completed())
        absent = {"label": "Claude app", "detected": False}
        with mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/claude"):
            result = meter.set_agent_access("claude", True, repair=True, runner=runner,
                                            status_getter=lambda client: absent if client == "claude_desktop" else before)
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflict"])
        self.assertIn("remove the existing tokenmeter entry", result["error"])
        self.assertIn("entry is locked", result["error"])
        runner.assert_called_once()

    def test_connection_change_is_verified_after_cli_success(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}
        after = {**before, "configured": True, "connected": True}
        states = iter((before, after))
        observed = {}

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv, **kwargs):
            observed["argv"] = argv
            observed["kwargs"] = kwargs
            return Completed()

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"):
            result = meter.set_agent_access("codex", True, runner=runner,
                                            status_getter=lambda client: next(states))
        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(observed["argv"][-3:], ["tokenmeter", "--", "/tmp/token-meter-mcp"])
        self.assertNotIn("shell", observed["kwargs"])

    def test_connection_failure_includes_bounded_cli_reason(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}

        class Completed:
            returncode = 127
            stdout = ""
            stderr = "env: node: No such file or directory\n"

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"), \
                mock.patch.object(meter, "agent_client_executable", return_value="/tmp/codex"):
            result = meter.set_agent_access("codex", True, runner=lambda *a, **k: Completed(),
                                            status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertIn("env: node", result["error"])

    def _desktop_fixture(self, tmp, config=None, root_name="Claude"):
        launcher = Path(tmp) / "token-meter-mcp"
        if not launcher.exists():
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
        root = Path(tmp) / root_name
        root.mkdir()
        path = root / "claude_desktop_config.json"
        if isinstance(config, bytes):
            path.write_bytes(config)
        elif config is not None:
            path.write_text(config if isinstance(config, str) else json.dumps(config, indent=2))
        return str(launcher), path

    def _desktop_toggle(self, launcher, paths, enabled, repair=False):
        paths = [str(path) for path in paths]
        status_getter = lambda client: meter.claude_desktop_access_status(launcher, paths)
        with mock.patch.object(meter, "CLAUDE_DESKTOP_CONFIGS", paths), \
                mock.patch.object(meter, "agent_access_launcher", return_value=launcher):
            return meter._set_agent_access_part("claude_desktop", enabled, repair,
                                                mock.Mock(side_effect=AssertionError("no subprocess")),
                                                status_getter)

    def test_status_lists_the_claude_app_beside_cli_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp)
            status = meter.agent_access_status(
                launcher=launcher, which=lambda name: None,
                claude_config_path=str(Path(tmp) / ".claude.json"),
                claude_desktop_config_paths=[str(path)])
        self.assertEqual([row["id"] for row in status["clients"]], ["codex", "claude"])
        claude = status["clients"][1]
        self.assertEqual(claude["label"], "Claude")
        self.assertTrue(claude["detected"])
        self.assertTrue(claude["available"])
        self.assertFalse(claude["configured"])
        self.assertEqual(claude["status"], "Ready to connect")
        self.assertTrue(status["any_detected"])

    def test_claude_app_missing_is_not_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = meter.claude_desktop_access_status(
                str(Path(tmp) / "launcher"), [str(Path(tmp) / "absent" / "claude_desktop_config.json")])
        self.assertFalse(status["detected"])
        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "App not found")

    def test_claude_app_connect_changes_only_the_tokenmeter_entry(self):
        existing = {"preferences": {"sidebarMode": "chat", "name": "caf\u00e9"},
                    "mcpServers": {"other": {"command": "/bin/other", "args": ["x"], "env": {"K": "v"}}}}
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp, existing)
            path.chmod(0o600)
            result = self._desktop_toggle(launcher, [path], True)
            saved = json.loads(path.read_text(encoding="utf-8"))
            mode = path.stat().st_mode & 0o777
        self.assertTrue(result["ok"])
        self.assertTrue(result["client"]["connected"])
        self.assertIn("Quit and reopen the Claude app", result["message"])
        self.assertEqual(saved["preferences"], existing["preferences"])
        self.assertEqual(saved["mcpServers"]["other"], existing["mcpServers"]["other"])
        self.assertEqual(saved["mcpServers"]["tokenmeter"], {
            "command": launcher, "args": [], "env": {"TOKEN_METER_CALLER": "claude"}})
        self.assertEqual(mode, 0o600)

    def test_claude_app_connect_writes_every_app_config_including_third_party_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, first = self._desktop_fixture(tmp, {"preferences": {}})
            _, third_party = self._desktop_fixture(tmp, {"deploymentMode": "3p", "mcpServers": {
                "other": {"command": "/bin/other"}}}, root_name="Claude-3p")
            partial = meter.claude_desktop_access_status(launcher, [str(first), str(third_party)])
            meter.write_claude_desktop_access(True, launcher, [str(first)])
            half = meter.claude_desktop_access_status(launcher, [str(first), str(third_party)])
            result = self._desktop_toggle(launcher, [first, third_party], True)
            saved = [json.loads(path.read_text()) for path in (first, third_party)]
        self.assertFalse(partial["connected"])
        self.assertFalse(half["connected"])
        self.assertEqual(partial["status"], "Ready to connect")
        self.assertEqual(half["status"], "Connected in only one Claude app config")
        self.assertTrue(result["ok"])
        for config in saved:
            self.assertEqual(config["mcpServers"]["tokenmeter"], meter.claude_desktop_access_entry(launcher))
        self.assertEqual(saved[1]["deploymentMode"], "3p")
        self.assertEqual(saved[1]["mcpServers"]["other"], {"command": "/bin/other"})

    def test_claude_app_only_third_party_root_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, third_party = self._desktop_fixture(tmp, {}, root_name="Claude-3p")
            absent = Path(tmp) / "Claude" / "claude_desktop_config.json"
            result = self._desktop_toggle(launcher, [absent, third_party], True)
            self.assertFalse(absent.parent.exists())
            saved = json.loads(third_party.read_text())
        self.assertTrue(result["ok"])
        self.assertIn("tokenmeter", saved["mcpServers"])

    def test_claude_app_connect_creates_a_missing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp)
            self.assertIsNone(meter.write_claude_desktop_access(True, launcher, [str(path)]))
            status = meter.claude_desktop_access_status(launcher, [str(path)])
            saved = json.loads(path.read_text())
        self.assertTrue(status["connected"])
        self.assertEqual(list(saved), ["mcpServers"])

    def test_claude_app_disconnect_removes_only_the_tokenmeter_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp)
            path.write_text(json.dumps({"keep": 1, "mcpServers": {
                "other": {"command": "/bin/other"},
                "tokenmeter": meter.claude_desktop_access_entry(launcher)}}))
            result = self._desktop_toggle(launcher, [path], False)
            saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(saved, {"keep": 1, "mcpServers": {"other": {"command": "/bin/other"}}})

    def test_claude_app_conflict_needs_repair_then_replaces_only_that_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp, {"mcpServers": {
                "other": {"command": "/bin/other"},
                "tokenmeter": {"command": "/tmp/different", "args": [], "env": {}}}})
            refused = self._desktop_toggle(launcher, [path], True)
            untouched = json.loads(path.read_text())
            repaired = self._desktop_toggle(launcher, [path], True, repair=True)
            saved = json.loads(path.read_text())
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["conflict"])
        self.assertEqual(untouched["mcpServers"]["tokenmeter"]["command"], "/tmp/different")
        self.assertTrue(repaired["ok"])
        self.assertTrue(repaired["repaired"])
        self.assertEqual(saved["mcpServers"]["tokenmeter"], meter.claude_desktop_access_entry(launcher))
        self.assertEqual(saved["mcpServers"]["other"], {"command": "/bin/other"})

    def test_claude_app_config_that_cannot_be_parsed_is_left_unchanged(self):
        for raw in (b"{not json", b'["list"]', b'{"mcpServers": []}', b"", b'{"a": "\xff\xfe"}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                launcher, path = self._desktop_fixture(tmp, raw)
                status = meter.claude_desktop_access_status(launcher, [str(path)])
                error = meter.write_claude_desktop_access(True, launcher, [str(path)])
                toggled = self._desktop_toggle(launcher, [path], True)
                self.assertEqual(status["status"], "Claude app config could not be read")
                self.assertTrue(status["unreadable"])
                self.assertTrue(status["available"])
                self.assertIn("left", error)
                self.assertNotIn(tmp, error)
                self.assertFalse(toggled["ok"])
                self.assertIn("could not read", toggled["error"])
                self.assertEqual(path.read_bytes(), raw)

    def _claude_fixture(self, tmp, code_entry=None, app_config=None):
        launcher, app_path = self._desktop_fixture(tmp, app_config)
        code_path = Path(tmp) / ".claude.json"
        code_path.write_text(json.dumps({"mcpServers": {"tokenmeter": code_entry}} if code_entry else {"keep": 1}))
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            data = json.loads(code_path.read_text())
            servers = data.setdefault("mcpServers", {})
            if argv[1:3] == ["mcp", "add"]:
                servers["tokenmeter"] = {"type": "stdio", "command": argv[-1], "args": [],
                                         "env": {"TOKEN_METER_CALLER": "claude"}}
            elif argv[1:3] == ["mcp", "remove"]:
                servers.pop("tokenmeter", None)
            code_path.write_text(json.dumps(data))
            return meter.subprocess.CompletedProcess(argv, 0, "", "")

        def status_getter(client):
            return meter.agent_access_client_status(
                client, launcher=launcher, which=lambda name: f"/usr/bin/{name}",
                claude_config_path=str(code_path), claude_desktop_config_paths=[str(app_path)])

        return launcher, code_path, app_path, runner, calls, status_getter

    def _claude_toggle(self, launcher, app_path, runner, status_getter, enabled, repair=False):
        with mock.patch.object(meter, "CLAUDE_DESKTOP_CONFIGS", [str(app_path)]), \
                mock.patch.object(meter, "agent_access_launcher", return_value=launcher), \
                mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/claude"):
            return meter.set_agent_access("claude", enabled, repair=repair, runner=runner,
                                          status_getter=status_getter)

    def test_one_claude_connect_sets_up_claude_code_and_the_claude_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={"keep": 2})
            before = getter("claude")
            result = self._claude_toggle(launcher, app_path, runner, getter, True)
            app = json.loads(app_path.read_text())
            code = json.loads(code_path.read_text())
        self.assertEqual(before["status"], "Ready to connect")
        self.assertEqual(before["connect_command"].count("\n"), 1)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["client"]["connected"])
        self.assertEqual(result["client"]["status"], "Connected to Claude Code and the Claude app")
        self.assertEqual(result["message"],
                         "Claude connected. Start a new Claude Code session and reopen the Claude app.")
        self.assertEqual([argv[1:3] for argv in calls], [["mcp", "add"]])
        self.assertEqual(app["keep"], 2)
        self.assertEqual(app["mcpServers"]["tokenmeter"], meter.claude_desktop_access_entry(launcher))
        self.assertEqual(code["mcpServers"]["tokenmeter"]["command"], launcher)

    def test_one_claude_disconnect_removes_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={})
            self._claude_toggle(launcher, app_path, runner, getter, True)
            result = self._claude_toggle(launcher, app_path, runner, getter, False)
            app = json.loads(app_path.read_text())
            code = json.loads(code_path.read_text())
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["client"]["configured"])
        self.assertNotIn("tokenmeter", app["mcpServers"])
        self.assertNotIn("tokenmeter", code["mcpServers"])

    def test_running_claude_app_is_skipped_but_claude_code_still_connects(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={})
            with mock.patch.object(meter, "claude_app_running", return_value=True):
                status = getter("claude")
                result = self._claude_toggle(launcher, app_path, runner, getter, True)
            app = json.loads(app_path.read_text())
            code = json.loads(code_path.read_text())
        self.assertFalse(status["blocked"])
        self.assertEqual(status["status"], "Ready to connect. Quit the Claude app to change its connection")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["partial"])
        self.assertIn("Quit the Claude app first", result["message"])
        self.assertIn("click Connect again", result["message"])
        self.assertEqual(result["client"]["status"],
                         "Connected to Claude Code only. Quit the Claude app to change its connection")
        self.assertTrue(result["client"]["blocked"])
        self.assertEqual(app, {})
        self.assertIn("tokenmeter", code["mcpServers"])

    def test_running_claude_app_blocks_when_only_the_app_is_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={})
            self._claude_toggle(launcher, app_path, runner, getter, True)
            app_before = app_path.read_bytes()
            code_before = code_path.read_bytes()
            calls.clear()
            with mock.patch.object(meter, "claude_app_running", return_value=True):
                status = getter("claude")
                result = self._claude_toggle(launcher, app_path, runner, getter, False)
            self.assertEqual(code_path.read_bytes(), code_before)
            self.assertEqual(app_path.read_bytes(), app_before)
        self.assertTrue(status["connected"])
        self.assertTrue(status["blocked"])
        self.assertFalse(result["ok"])
        self.assertIn("Quit the Claude app first", result["error"])
        self.assertEqual(calls, [])
        with tempfile.TemporaryDirectory() as tmp:
            launcher, _, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={})
            with mock.patch.object(meter, "claude_app_running", return_value=True):
                meter.write_claude_desktop_access(False, launcher, [str(app_path)])
                self._claude_toggle(launcher, app_path, runner, getter, True)
                calls.clear()
                refused = self._claude_toggle(launcher, app_path, runner, getter, True)
                blocked = getter("claude")
        self.assertFalse(refused["ok"])
        self.assertIn("Quit the Claude app first", refused["error"])
        self.assertEqual(calls, [])
        self.assertTrue(blocked["blocked"])

    def test_unreadable_claude_app_config_does_not_stop_claude_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config=b"{bad")
            result = self._claude_toggle(launcher, app_path, runner, getter, True)
            self.assertEqual(app_path.read_bytes(), b"{bad")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["partial"])
        self.assertIn("could not read", result["message"])
        self.assertEqual(result["client"]["status"],
                         "Connected to Claude Code only. The Claude app config could not be read")
        self.assertTrue(result["client"]["blocked"])

    def test_running_claude_app_does_not_block_finishing_claude_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, _, app_path, runner, calls, getter = self._claude_fixture(tmp)
            meter.write_claude_desktop_access(True, launcher, [str(app_path)])
            with mock.patch.object(meter, "claude_app_running", return_value=True):
                partial = getter("claude")
                result = self._claude_toggle(launcher, app_path, runner, getter, True)
        self.assertEqual(partial["status"], "Connected to the Claude app only")
        self.assertFalse(partial["blocked"])
        self.assertTrue(result["ok"], result)
        self.assertEqual([argv[1:3] for argv in calls], [["mcp", "add"]])

    def test_claude_code_only_machine_still_connects_with_one_button(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, _, runner, calls, _ = self._claude_fixture(tmp)
            getter = lambda client: meter.agent_access_client_status(
                client, launcher=launcher, which=lambda name: f"/usr/bin/{name}",
                claude_config_path=str(code_path), claude_desktop_config_paths=[])
            result = self._claude_toggle(launcher, Path(tmp) / "absent" / "c.json", runner, getter, True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message"], "Claude connected. Start a new Claude Code session.")

    def test_claude_app_running_is_checked_only_where_the_host_can_tell(self):
        seen = []

        def runner(argv, **kwargs):
            seen.append(argv)
            self.assertNotIn("shell", kwargs)
            return meter.subprocess.CompletedProcess(argv, self._pgrep_code, "", "")

        for platform_id, code, expected in (("macos", 0, True), ("macos", 1, False),
                                            ("windows", 0, None), ("linux", 0, None)):
            self._pgrep_code = code
            with self.subTest(platform_id=platform_id, code=code), \
                    mock.patch.object(meter._PLATFORM_SERVICES, "platform_id", platform_id):
                self.assertEqual(self._real_app_running(runner), expected)
        self.assertEqual(seen, [["pgrep", "-a", "-x", "Claude"]] * 2)

    def test_disconnect_is_refused_while_a_claude_app_config_is_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, code_path, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config=b"{bad")
            self._claude_toggle(launcher, app_path, runner, getter, True)
            code_before = code_path.read_bytes()
            calls.clear()
            result = self._claude_toggle(launcher, app_path, runner, getter, False)
            self.assertEqual(code_path.read_bytes(), code_before)
            self.assertEqual(app_path.read_bytes(), b"{bad")
        self.assertFalse(result["ok"])
        self.assertIn("could not read", result["error"])
        self.assertEqual(calls, [])

    def test_repair_message_reflects_whether_anything_was_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, _, app_path, runner, calls, getter = self._claude_fixture(tmp, app_config={"mcpServers": {
                "tokenmeter": {"command": "/tmp/different", "args": [], "env": {}}}})
            with mock.patch.object(meter, "claude_app_running", return_value=True):
                result = self._claude_toggle(launcher, app_path, runner, getter, True, repair=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["repaired"])
        self.assertTrue(result["message"].startswith("Claude Code connected."))

    def test_malformed_tokenmeter_entries_are_conflicts_not_crashes(self):
        for entry in ({"command": "c", "env": "x"}, {"command": "c", "args": 5, "env": {}},
                      "oops", None, ["list"], {"env": {"TOKEN_METER_CALLER": "claude"}, "args": "x"}):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                launcher, path = self._desktop_fixture(tmp, {"mcpServers": {"tokenmeter": entry}})
                before = path.read_bytes()
                status = meter.agent_access_status(
                    launcher=launcher, which=lambda name: None,
                    claude_config_path=str(Path(tmp) / ".claude.json"),
                    claude_desktop_config_paths=[str(path)])["clients"][1]
                refused = self._desktop_toggle(launcher, [path], True)
                self.assertTrue(status["conflict"])
                self.assertFalse(status["connected"])
                self.assertFalse(refused["ok"])
                self.assertEqual(path.read_bytes(), before)

    def test_unreadable_config_wins_over_a_conflict_in_another_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, conflicting = self._desktop_fixture(tmp, {"mcpServers": {
                "tokenmeter": {"command": "/tmp/different", "args": [], "env": {}}}})
            _, unreadable = self._desktop_fixture(tmp, b"{bad", root_name="Claude-3p")
            status = meter.claude_desktop_access_status(launcher, [str(conflicting), str(unreadable)])
            toggled = self._desktop_toggle(launcher, [conflicting, unreadable], True, repair=True)
            saved = json.loads(conflicting.read_text())
        self.assertEqual(status["status"], "Claude app config could not be read")
        self.assertFalse(toggled["ok"])
        self.assertIn("could not read", toggled["error"])
        self.assertEqual(saved["mcpServers"]["tokenmeter"]["command"], "/tmp/different")

    def test_settings_row_shows_unreadable_claude_app_config_instead_of_launcher_error(self):
        page = Path(DASHBOARD_PAGE).read_text()
        self.assertIn("const disabled=!row.connected&&!row.available;", page)
        self.assertIn("try{refreshed=await loadAgentAccess();}finally{agentAccessRechecking=false;}", page)
        self.assertIn("if(!button||button.disabled)return;", page)
        self.assertIn("title:'Quit the Claude app first'", page)
        self.assertIn("title:'Claude app settings could not be read'", page)
        self.assertIn("const action=row.connected?'Disconnect':row.conflict?'Repair':'Connect';", page)
        self.assertIn("if(row)openAgentAccessDialog(client,!row.connected);", page)
        self.assertIn("!row.detected?(row.id==='claude'?row.status:'CLI not found'):"
                      "!row.available?'Token Meter launcher unavailable':row.status", page)

    def test_unreadable_claude_app_config_does_not_break_other_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp, b"\xff\xfe\x00")
            status = meter.agent_access_status(
                launcher=launcher, which=lambda name: None,
                claude_config_path=str(Path(tmp) / ".claude.json"),
                claude_desktop_config_paths=[str(path)])
        self.assertTrue(status["ok"])
        self.assertIn("The Claude app config could not be read", status["clients"][1]["status"])
        self.assertEqual(status["clients"][0]["id"], "codex")

    def test_claude_app_write_keeps_a_symlinked_config_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp)
            real = Path(tmp) / "dotfiles.json"
            real.write_text(json.dumps({"keep": True}))
            path.symlink_to(real)
            self.assertIsNone(meter.write_claude_desktop_access(True, launcher, [str(path)]))
            self.assertTrue(path.is_symlink())
            saved = json.loads(real.read_text())
        self.assertTrue(saved["keep"])
        self.assertIn("tokenmeter", saved["mcpServers"])

    def test_claude_app_refuses_to_overwrite_a_config_changed_mid_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher, path = self._desktop_fixture(tmp, {"preferences": {"v": 1}})
            original = meter._read_claude_desktop_config

            def app_writes_during_read(target):
                result = original(target)
                Path(target).write_text(json.dumps({"preferences": {"v": 2}, "extra": "app"}))
                return result

            with mock.patch.object(meter, "_read_claude_desktop_config", side_effect=app_writes_during_read):
                error = meter.write_claude_desktop_access(True, launcher, [str(path)])
            saved = json.loads(path.read_text())
        self.assertIn("changed its config", error)
        self.assertEqual(saved, {"preferences": {"v": 2}, "extra": "app"})

    def test_windows_claude_app_entry_runs_cmd_launcher_through_cmd_exe(self):
        launcher = r"C:\Token Meter\scripts\run-token-meter-mcp.cmd"
        entry = meter.claude_desktop_access_entry(launcher)
        self.assertEqual(entry["command"], "cmd.exe")
        self.assertEqual(entry["args"], ["/c", launcher])
        self.assertTrue(meter._claude_desktop_entry_matches(entry, launcher))
        self.assertFalse(meter._claude_desktop_entry_matches(
            {"command": launcher, "args": [], "env": {"TOKEN_METER_CALLER": "claude"}}, launcher))


class CapabilityConfigTests(unittest.TestCase):
    def test_skill_catalog_cache_avoids_rescanning_but_refreshes_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "custom" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text("---\nname: custom\n---\n")
            codex_pattern = os.path.join(
                os.path.expanduser("~/.codex/skills"), "**", "SKILL.md",
            )

            def matching_glob(pattern, recursive=False):
                del recursive
                return [str(skill)] if pattern == codex_pattern else []

            meter.invalidate_discovered_skill_cache()
            try:
                with mock.patch.object(meter.glob, "glob", side_effect=matching_glob), \
                        mock.patch.object(
                            meter, "_skill_measurability", return_value="unknown",
                        ) as measurability:
                    first = meter.discovered_skills([{
                        "name": "custom", "providers": ["codex"],
                        "activations": 1, "sessions_used": 1,
                        "last_used": "2026-08-18",
                    }])
                    second = meter.discovered_skills([{
                        "name": "custom", "providers": ["codex"],
                        "activations": 2, "sessions_used": 2,
                        "last_used": "2026-08-19",
                    }])
            finally:
                meter.invalidate_discovered_skill_cache()

        self.assertEqual(measurability.call_count, 1)
        self.assertEqual(first[0]["activations"], 1)
        self.assertEqual(second[0]["activations"], 2)
        self.assertEqual(second[0]["measurement"], "measurable")

    def scan_claude_skill_rows(self, root, sources):
        projects = root / "claude-projects"
        meter.invalidate_discovered_skill_cache()
        try:
            with mock.patch.object(meter, "CLAUDE_USER_SKILLS", str(root / "user-skills")), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(projects)), \
                    mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []), \
                    mock.patch.object(meter, "claude_plugin_installations", return_value={}), \
                    mock.patch.object(meter, "source_inventory_snapshot",
                                      return_value={"sources": tuple(sources)}):
                rows = meter.discovered_skills()
        finally:
            meter.invalidate_discovered_skill_cache()
        return [row for row in rows if row["runtime"] == "Claude"]

    def test_inventory_lists_personal_and_session_project_claude_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for skill_dir in (root / "user-skills" / "personal-helper",
                              root / "repo" / ".claude" / "skills" / "repo-review",
                              root / "codex-repo" / ".claude" / "skills" / "not-claude"):
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n")
            session = str(root / "claude-projects" / "p" / "s.jsonl")
            rows = self.scan_claude_skill_rows(root, [
                {"provider": "claude", "path": session, "project": str(root / "repo")},
                {"provider": "claude", "path": session, "project": str(root / "repo")},
                {"provider": "claude", "path": session, "project": "local"},
                {"provider": "codex", "path": "/tmp/c.jsonl", "project": str(root / "codex-repo")},
            ])

        by_name = {row["name"]: row for row in rows}
        self.assertEqual(set(by_name), {"personal-helper", "repo-review"})
        self.assertEqual(by_name["personal-helper"]["source"], "Personal")
        self.assertEqual(by_name["repo-review"]["source"], "Project")
        self.assertEqual(by_name["repo-review"]["origin_id"], "claude:project")
        self.assertTrue(by_name["repo-review"]["enabled"])
        self.assertFalse(by_name["repo-review"]["mutable"])

    def test_project_skill_roots_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = os.path.join(tmp, "claude-projects", "p", "s.jsonl")
            sources = [
                {"provider": "claude", "path": session, "project": f"/work/repo-{index}"}
                for index in range(meter.MAX_PROJECT_SKILL_ROOTS + 10)
            ]
            with mock.patch.object(meter, "CLAUDE_PROJECTS", os.path.join(tmp, "claude-projects")), \
                    mock.patch.object(meter, "source_inventory_snapshot",
                                      return_value={"sources": tuple(sources)}):
                roots = meter.claude_project_skill_roots()
        self.assertEqual(len(roots), meter.MAX_PROJECT_SKILL_ROOTS)

    def test_personal_skill_wins_name_clash_and_first_project_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for skill_dir in (root / "user-skills" / "review",
                              root / "a" / ".claude" / "skills" / "review",
                              root / "a" / ".claude" / "skills" / "deploy",
                              root / "b" / ".claude" / "skills" / "deploy"):
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n")
            session = str(root / "claude-projects" / "p" / "s.jsonl")
            rows = self.scan_claude_skill_rows(root, [
                {"provider": "claude", "path": session, "project": str(root / "a")},
                {"provider": "claude", "path": session, "project": str(root / "b")},
            ])

        self.assertEqual(sorted((row["name"], row["source"]) for row in rows),
                         [("deploy", "Project"), ("review", "Personal")])
        deploy = next(row for row in rows if row["name"] == "deploy")
        self.assertIn(os.path.join("a", ".claude", "skills", "deploy"), deploy["path"])

    def test_skill_catalog_rescans_once_session_inventory_is_ready(self):
        snapshot = {"ready": False, "sources": ()}
        meter.invalidate_discovered_skill_cache()
        try:
            with mock.patch.object(meter, "source_inventory_snapshot", side_effect=lambda: snapshot), \
                    mock.patch.object(meter, "_scan_discovered_skills", return_value=[]) as scan:
                meter.discovered_skills()
                meter.discovered_skills()
                snapshot = {"ready": True, "sources": ()}
                meter.discovered_skills()
                meter.discovered_skills()
        finally:
            meter.invalidate_discovered_skill_cache()
        self.assertEqual(scan.call_count, 2)

    def test_only_native_skill_tool_calls_count_as_native_activations(self):
        evidence = meter.summarize_tool_evidence([
            {**meter.tool_identity("Skill"), "output_tokens": 0, "ts": 100,
             "args_fingerprint": "a", "error": False, "skills": ["repo-review"]},
            {**meter.tool_identity("Read"), "output_tokens": 0, "ts": 101,
             "args_fingerprint": "b", "error": False,
             "skills": meter.skill_names_from_value(
                 {"file_path": "/r/.agents/skills/adapter-only/SKILL.md"}, "Read")},
        ])
        session = os.path.join(meter.CLAUDE_PROJECTS, "p", "s.jsonl")
        waste = meter.global_tool_waste([{
            "id": "s", "provider": "claude", "project": "/r", "path": session,
            "_tool_evidence": evidence,
        }])
        hosts = {row["name"]: row["hosts"]["Claude"] for row in waste["skills"]}
        self.assertEqual(hosts["repo-review"]["native_activations"], 1)
        self.assertEqual(hosts["adapter-only"]["activations"], 1)
        self.assertEqual(hosts["adapter-only"]["native_activations"], 0)

        meter.invalidate_discovered_skill_cache()
        try:
            with mock.patch.object(meter, "_scan_discovered_skills", return_value=[]):
                rows = meter.discovered_skills(waste["skills"])
        finally:
            meter.invalidate_discovered_skill_cache()
        self.assertEqual([row["name"] for row in rows], ["repo-review"])

    def test_unlisted_claude_skill_activations_become_observed_rows(self):
        inventory = [{"name": "known", "runtime": "Claude", "measurement": "unknown"}]
        usage = [
            {"name": "known", "hosts": {"Claude": {"activations": 2, "native_activations": 2}}},
            {"name": "repo-only", "hosts": {"Claude": {
                "activations": 3, "native_activations": 3,
                "sessions_used": 2, "last_used": "2026-10-27",
            }}},
            {"name": "path-only", "hosts": {"Claude": {"activations": 2, "native_activations": 0}}},
            {"name": "codex-only", "hosts": {"Codex": {"activations": 4, "native_activations": 4}}},
            {"name": "legacy", "providers": ["claude"], "activations": 1},
            {"name": "${name}", "hosts": {"Claude": {"activations": 1, "native_activations": 1}}},
        ]
        meter.invalidate_discovered_skill_cache()
        try:
            with mock.patch.object(meter, "_scan_discovered_skills", return_value=inventory):
                rows = meter.discovered_skills(usage)
        finally:
            meter.invalidate_discovered_skill_cache()

        by_name = {row["name"]: row for row in rows}
        self.assertEqual(set(by_name), {"known", "repo-only"})
        self.assertEqual(by_name["known"]["activations"], 2)
        observed = by_name["repo-only"]
        self.assertEqual(observed["source"], "Observed in traces")
        self.assertEqual(observed["configuration"], "Not in config")
        self.assertIsNone(observed["enabled"])
        self.assertEqual((observed["activations"], observed["sessions_used"]), (3, 2))
        self.assertEqual(observed["last_used"], "2026-10-27")
        self.assertEqual(observed["measurement"], "measurable")
        self.assertEqual(observed["id"], "skill:claude:claude:observed:repo-only")

    def test_skill_identity_separates_runtime_origin_and_plugin(self):
        identities = {
            meter.skill_identity("Codex", "browser", "codex:built-in"),
            meter.skill_identity("Codex", "browser", "codex:user"),
            meter.skill_identity("Codex", "browser", "codex:plugin:openai-bundled", "browser@openai-bundled"),
            meter.skill_identity("Claude", "browser", "claude:plugin:skills-marketplace", "browser@skills-marketplace"),
        }
        self.assertEqual(len(identities), 4)

    def test_optional_summary_counts_only_mutable_skill_packs(self):
        mcp_items = [
            {"name": "context7", "mutable": True, "enabled": True, "used": False,
             "codex_enabled": True, "claude_enabled": False},
            {"name": "core", "mutable": False, "enabled": True, "used": False},
        ]
        skill_items = [
            {"name": "browser", "runtime": "Codex", "plugin_id": "browser@bundled",
             "mutable": True, "enabled": True, "used": True, "activations": 2},
            {"name": "local", "runtime": "Codex", "plugin_id": "",
             "mutable": False, "enabled": True, "used": False},
        ]
        groups = meter.capability_control_groups(mcp_items, skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["skill_packs_enabled"], 1)

    def test_runtime_skill_packs_are_not_review_candidates(self):
        skill_items = [
            {"id": "runtime-skill", "name": "browser", "runtime": "Codex",
             "plugin_id": "browser@openai-bundled", "mutable": True, "enabled": True,
             "used": False, "reviewable": False, "origin": "runtime_pack"},
            {"id": "user-skill", "name": "custom", "runtime": "Codex",
             "plugin_id": "custom@personal", "mutable": True, "enabled": True,
             "used": False, "reviewable": True, "origin": "user_plugin"},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["review_candidates"], ["skill_pack:Codex:custom@personal"])

    def test_observed_tools_do_not_claim_configuration_enabled(self):
        waste = {"inventory_tools": [{
            "id": "codex::exec", "name": "exec", "display": "exec",
            "kind": "tool", "runtime": "Codex", "namespace": "shell",
            "calls": 2, "output_tokens": 120, "last_used": "2026-08-10",
            "advertised_sessions": 0, "eager_sessions": 0, "deferred_sessions": 0,
        }]}
        with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                mock.patch.object(meter, "discovered_skills", return_value=[]), \
                mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                mock.patch.object(meter, "claude_local_agent_sources", return_value=[]):
            capabilities = meter.capability_inventory(waste)
        row = capabilities["items"][0]
        self.assertIsNone(row["enabled"])
        self.assertEqual(row["configuration"], "Unknown")
        self.assertEqual(row["state"], "Observed only")
        self.assertIsNone(capabilities["summary"]["tools"]["enabled"])
        self.assertEqual(capabilities["summary"]["tools"]["observed"], 1)

    def test_capability_revisions_are_stable_across_generation_time(self):
        waste = {"inventory_tools": [{
            "id": "codex::exec", "name": "exec", "display": "exec",
            "kind": "tool", "runtime": "Codex", "namespace": "shell",
            "calls": 2, "output_tokens": 120, "last_used": "2026-08-10",
            "advertised_sessions": 1, "eager_sessions": 1, "deferred_sessions": 0,
        }]}
        with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                mock.patch.object(meter, "discovered_skills", return_value=[]), \
                mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                mock.patch.object(meter, "claude_local_agent_sources", return_value=[]), \
                mock.patch.object(meter.time, "time", side_effect=[100, 200, 300]):
            first = meter.capability_inventory(waste)
            second = meter.capability_inventory(waste)
            changed = meter.capability_inventory({
                **waste,
                "inventory_tools": [{**waste["inventory_tools"][0], "calls": 3}],
            })
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(first["review_revision"], second["review_revision"])
        self.assertEqual(first["inventory_revision"], second["inventory_revision"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertNotEqual(second["inventory_revision"], changed["inventory_revision"])
        self.assertNotEqual(second["revision"], changed["revision"])

    def test_dashboard_state_omits_heavy_capability_rows_and_tool_waste(self):
        capabilities = {
            "items": [{"id": "tool:exec"}],
            "summary": {"tools": {"available": 1}},
            "control_groups": [], "inventory_revision": "inventory-a",
        }
        state = {"xsession": {
            "capabilities": capabilities,
            "tool_waste": {"inventory_tools": [{"private": "trace detail"}]},
            "total_sessions": 2,
        }}
        payload = meter.dashboard_state_payload(state)
        public_capabilities = payload["xsession"]["capabilities"]
        self.assertNotIn("items", public_capabilities)
        self.assertEqual(public_capabilities["inventory_count"], 1)
        self.assertNotIn("tool_waste", payload["xsession"])
        self.assertIn("items", state["xsession"]["capabilities"])
        self.assertIn("tool_waste", state["xsession"])
        self.assertIn(
            'elif req_path == "/capabilities/inventory":',
            Path(meter.IMPLEMENTATION_FILE).read_text(),
        )

    def test_pack_without_runtime_logs_is_not_a_review_candidate(self):
        base = {
            "id": "skill_pack:Claude:custom@personal", "name": "custom@personal",
            "control_type": "skill_pack", "enabled": True, "used": False,
            "mutable": True, "reviewable": True, "unmeasurable": False,
        }
        unscanned = meter.optional_capability_summary([{**base, "scanned_sessions": 0}])
        scanned = meter.optional_capability_summary([{**base, "scanned_sessions": 3}])
        self.assertEqual(unscanned["review_candidates"], [])
        self.assertEqual(unscanned["unscanned_packs"], 1)
        self.assertEqual(scanned["review_candidates"], [base["id"]])
        self.assertEqual(scanned["unscanned_packs"], 0)

    def test_claude_desktop_and_3p_logs_do_not_cover_claude_code_packs(self):
        skill = {
            "id": "skill:claude:custom@personal:custom", "name": "custom",
            "runtime": "Claude", "source": "User-installed plugin",
            "plugin_id": "custom@personal", "mutable": True, "enabled": True,
            "used": False, "reviewable": True, "measurement": "measurable",
            "unmeasurable": False, "activations": 0, "last_used": "Never",
            "setting_path": "~/.claude/settings.json",
        }
        for runtime in ("Claude Desktop", "Claude-3P"):
            with self.subTest(runtime=runtime):
                waste = meter.global_tool_waste([{
                    "id": runtime, "provider": "claude", "runtime": runtime,
                    "project": "/repo", "_tool_evidence": {},
                }])
                with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                        mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                        mock.patch.object(meter, "discovered_skills", return_value=[skill]), \
                        mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                        mock.patch.object(meter, "claude_local_agent_sources", return_value=[]):
                    capabilities = meter.capability_inventory(waste)
                self.assertEqual(capabilities["control_groups"][0]["scanned_sessions"], 0)
                self.assertEqual(
                    capabilities["summary"]["optional"]["review_candidates"], []
                )

    def test_bulk_disable_accepts_only_exact_review_candidates(self):
        capabilities = {
            "summary": {"optional": {"review_candidates": ["skill_pack:Claude:custom@personal"]}},
            "control_groups": [
                {"id": "skill_pack:Claude:custom@personal", "name": "custom@personal",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": True},
                {"id": "skill_pack:Codex:browser@openai-bundled", "name": "browser@openai-bundled",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": False},
            ],
        }
        changed = []

        def fake_setter(control, enabled):
            changed.append((control["id"], enabled))
            return {"ok": True, "verified": True}

        result = meter.disable_capability_controls(
            ["skill_pack:Claude:custom@personal"], capabilities, fake_setter)
        rejected = meter.disable_capability_controls(
            ["skill_pack:Codex:browser@openai-bundled"], capabilities, fake_setter)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(changed, [("skill_pack:Claude:custom@personal", False)])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["invalid_control_ids"], ["skill_pack:Codex:browser@openai-bundled"])

    def test_session_optional_summary_excludes_mcp_and_default_activity(self):
        capabilities = {"control_groups": [
            {"id": "skill_pack:Codex:browser@bundled", "control_type": "skill_pack",
             "name": "browser@bundled", "runtime": "Codex", "enabled": True,
             "mutable": True, "members": ["browser"]},
        ], "summary": {"optional": {"review_candidate_names": []}}}
        state = {"provider": "codex", "tools": {
            "by_name": [
                {"name": "exec", "namespace": "exec", "kind": "tool", "calls": 3},
                {"name": "mcp__context7__query", "namespace": "context7", "kind": "mcp", "calls": 1},
            ],
            "skills": [{"name": "browser", "activations": 1}],
        }}
        summary = meter.session_optional_capabilities(state, capabilities)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["avoidable_eager_definition_tokens"], 0)
        self.assertNotIn("context7", [row["name"] for row in summary["groups"]])

    def test_mcp_parser_ignores_nested_env_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[mcp_servers.node_repl]\nenabled = false\n[mcp_servers.node_repl.env]\nTOKEN = 'x'\n")
            rows = meter.toml_named_sections(str(path), "mcp_servers")
        self.assertEqual(list(rows), ["node_repl"])

    def test_codex_plugin_toggle_changes_only_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[plugins."docs@runtime"]\nenabled = true\nsource = "keep"\n[features]\nskills = true\n')
            with mock.patch.object(meter, "CODEX_CONFIG", str(path)):
                result = meter.set_codex_plugin_enabled("docs@runtime", False)
            text = path.read_text()
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertIn("enabled = false", text)
        self.assertIn('source = "keep"', text)

    def test_claude_plugin_toggle_verifies_written_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"enabledPlugins": {"docs@personal": True}}))
            with (mock.patch.object(meter, "CLAUDE_SETTINGS", str(path)),
                  mock.patch.object(meter, "claude_plugin_installations",
                                    return_value={"docs@personal": {"installPath": tmp}})):
                result = meter.set_claude_plugin_enabled("docs@personal", False)
            written = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertFalse(written["enabledPlugins"]["docs@personal"])


if __name__ == "__main__":
    unittest.main()
