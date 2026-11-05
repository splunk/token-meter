import contextlib
import http.client
import io
import json
import os
import threading
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter import packaging
from token_meter.services import agent_skills


REPO = Path(__file__).resolve().parents[1]
SKILL = "token-meter-usage-review"


def which_none(name):
    return None


def which_all(name):
    return f"/usr/bin/{name}"


class AgentSkillPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.roots = {"codex": str(self.home / "codex-skills"),
                      meter.CLAUDE_CODE_CLIENT: str(self.home / "claude-skills")}
        self.source = (REPO / "agent-skills" / SKILL / "SKILL.md").read_bytes()

    def target(self, part="codex"):
        return Path(self.roots[part]) / SKILL / "SKILL.md"

    def toggle(self, client, enabled, which=which_all):
        return meter.set_agent_skills(client, enabled, roots=self.roots, source_root=str(REPO), which=which)

    def test_install_copies_the_shipped_skill_and_reports_installed(self):
        result = self.toggle("codex", True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.target().read_bytes(), self.source)
        self.assertEqual(result["skills"]["state"], "installed")
        self.assertFalse(self.target(meter.CLAUDE_CODE_CLIENT).exists())
        self.assertIn("Codex", result["message"])

    def test_claude_installs_into_claude_code_skills(self):
        result = self.toggle("claude", True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.target(meter.CLAUDE_CODE_CLIENT).read_bytes(), self.source)
        self.assertEqual(result["skills"]["target"], "Claude Code")

    def test_install_again_is_idempotent(self):
        self.toggle("codex", True)
        again = self.toggle("codex", True)
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])

    def test_same_named_user_skill_is_never_overwritten_or_deleted(self):
        user = self.target()
        user.parent.mkdir(parents=True)
        user.write_text("---\nname: token-meter-usage-review\n---\nmine\n")
        installed = self.toggle("codex", True)
        self.assertTrue(installed["partial"])
        self.assertIn(SKILL, installed["message"])
        self.assertIn(SKILL, installed["skills"]["unmanaged"])
        removed = self.toggle("codex", False)
        self.assertTrue(removed["ok"])
        self.assertEqual(user.read_text(), "---\nname: token-meter-usage-review\n---\nmine\n")

    def test_remove_deletes_only_the_managed_file(self):
        self.toggle("codex", True)
        extra = self.target().parent / "notes.md"
        extra.write_text("keep")
        result = self.toggle("codex", False)
        self.assertTrue(result["ok"])
        self.assertFalse(self.target().exists())
        self.assertEqual(extra.read_text(), "keep")
        self.assertEqual(result["skills"]["state"], "conflict")

    def test_folder_with_user_files_but_no_skill_file_is_left_alone(self):
        folder = Path(self.roots["codex"]) / SKILL
        folder.mkdir(parents=True)
        (folder / "notes.txt").write_text("mine")
        self.assertIn(SKILL, self.toggle("codex", True)["skills"]["unmanaged"])
        self.assertFalse(self.target().exists())

    def test_folder_with_only_finder_metadata_is_installable(self):
        folder = Path(self.roots["codex"]) / SKILL
        folder.mkdir(parents=True)
        (folder / ".DS_Store").write_bytes(b"x")
        self.assertTrue(self.toggle("codex", True)["ok"])

    def test_remove_drops_the_empty_folder(self):
        self.toggle("codex", True)
        self.toggle("codex", False)
        self.assertFalse(self.target().parent.exists())

    def test_symlinked_skill_folder_is_not_followed(self):
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "SKILL.md").write_bytes(self.source)
        Path(self.roots["codex"]).mkdir(parents=True)
        os.symlink(elsewhere, self.target().parent)
        self.assertIn(SKILL, self.toggle("codex", True)["skills"]["unmanaged"])
        self.toggle("codex", False)
        self.assertEqual((elsewhere / "SKILL.md").read_bytes(), self.source)

    def test_missing_client_blocks_install_without_writing(self):
        with mock.patch.object(meter, "agent_client_executable", return_value=None):
            result = self.toggle("codex", True, which=which_none)
        self.assertFalse(result["ok"])
        self.assertFalse(Path(self.roots["codex"]).exists())

    def test_invalid_requests_are_rejected(self):
        self.assertFalse(meter.set_agent_skills("cursor", True, roots=self.roots, source_root=str(REPO))["ok"])
        self.assertFalse(meter.set_agent_skills("codex", "yes", roots=self.roots, source_root=str(REPO))["ok"])

    def previous_release(self, data):
        released = agent_skills.RELEASED_SKILL_HASHES[SKILL] | {agent_skills._sha256(data)}
        patcher = mock.patch.dict(agent_skills.RELEASED_SKILL_HASHES, {SKILL: released})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refresh_updates_managed_copies_and_adds_nothing(self):
        stale = self.target()
        stale.parent.mkdir(parents=True)
        old = self.source.replace(b"three", b"two")
        stale.write_bytes(old)
        self.previous_release(old)
        meter.refresh_installed_agent_skills(roots=self.roots, source_root=str(REPO))
        self.assertEqual(stale.read_bytes(), self.source)
        self.assertFalse(self.target(meter.CLAUDE_CODE_CLIENT).exists())

    def test_outdated_state_is_reported(self):
        stale = self.target()
        stale.parent.mkdir(parents=True)
        stale.write_bytes(self.source + b"\nold\n")
        self.previous_release(self.source + b"\nold\n")
        status = meter.agent_skills_status("codex", roots=self.roots, source_root=str(REPO), which=which_all)
        self.assertEqual(status["outdated"], [SKILL])

    def test_edited_copy_is_the_users_and_is_never_replaced_or_deleted(self):
        edited = self.target()
        edited.parent.mkdir(parents=True)
        body = self.source + b"\nMY CUSTOM EDITS\n"
        edited.write_bytes(body)
        self.assertIn(SKILL, self.toggle("codex", True)["skills"]["unmanaged"])
        meter.refresh_installed_agent_skills(roots=self.roots, source_root=str(REPO))
        self.toggle("codex", False)
        self.assertEqual(edited.read_bytes(), body)

    def test_newer_unknown_copy_is_not_downgraded_by_an_older_runtime(self):
        newer = self.target()
        newer.parent.mkdir(parents=True)
        newer.write_bytes(self.source.replace(b"three", b"four"))
        meter.refresh_installed_agent_skills(roots=self.roots, source_root=str(REPO))
        self.assertIn(b"four", newer.read_bytes())

    def test_symlinked_skills_root_is_followed_for_dotfile_setups(self):
        real = self.home / "dotfiles-skills"
        real.mkdir()
        os.symlink(real, self.roots["codex"])
        self.assertTrue(self.toggle("codex", True)["ok"])
        self.assertEqual((real / SKILL / "SKILL.md").read_bytes(), self.source)

    def test_fifo_skill_file_is_unmanaged_without_blocking(self):
        folder = self.target().parent
        folder.mkdir(parents=True)
        os.mkfifo(self.target())
        status = meter.agent_skills_status("codex", roots=self.roots, source_root=str(REPO), which=which_all)
        self.assertEqual(status["state"], "conflict")
        self.toggle("codex", False)
        self.assertTrue(self.target().exists())

    def test_mixed_install_succeeds_and_names_the_skipped_skill(self):
        source = self.home / "src"
        for name in ("token-meter-a", "token-meter-b"):
            (source / "agent-skills" / name).mkdir(parents=True)
            (source / "agent-skills" / name / "SKILL.md").write_bytes(
                b"---\nname: x\n---\n<!-- token-meter-managed-skill: x -->\n" + name.encode())
        mine = Path(self.roots["codex"]) / "token-meter-b"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine")
        result = meter.set_agent_skills("codex", True, roots=self.roots, source_root=str(source), which=which_all)
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertIn("token-meter-b", result["message"])
        self.assertEqual((mine / "SKILL.md").read_text(), "mine")
        self.assertTrue((Path(self.roots["codex"]) / "token-meter-a" / "SKILL.md").exists())

    def test_conflict_on_every_skill_fails_without_writing(self):
        for name in agent_skills.package_skills(str(REPO)):
            mine = Path(self.roots["codex"]) / name
            mine.mkdir(parents=True)
            (mine / "SKILL.md").write_text("mine")
        result = self.toggle("codex", True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["skills"]["state"], "conflict")
        for name in agent_skills.package_skills(str(REPO)):
            self.assertEqual((Path(self.roots["codex"]) / name / "SKILL.md").read_text(), "mine")

    def test_install_without_a_package_fails_cleanly(self):
        empty = self.home / "no-package"
        empty.mkdir()
        result = meter.set_agent_skills("codex", True, roots=self.roots, source_root=str(empty), which=which_all)
        self.assertFalse(result["ok"])
        self.assertFalse(Path(self.roots["codex"]).exists())

    def test_only_token_meter_named_marked_skills_are_packaged(self):
        source = self.home / "src"
        for name, body in (("token-meter-a", b"<!-- token-meter-managed-skill: x -->"),
                           ("token-meter-b", b"no marker"),
                           ("other", b"<!-- token-meter-managed-skill: x -->")):
            (source / "agent-skills" / name).mkdir(parents=True)
            (source / "agent-skills" / name / "SKILL.md").write_bytes(body)
        self.assertEqual(list(agent_skills.package_skills(str(source))), ["token-meter-a"])

    def test_status_payload_has_no_local_paths(self):
        status = meter.agent_access_status(skill_roots=self.roots, skill_source_root=str(REPO),
                                           which=which_none, claude_desktop_config_paths=[])
        for row in status["clients"]:
            self.assertIn("skills", row)
        text = json.dumps([row["skills"] for row in status["clients"]])
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn(str(REPO), text)


class ShippedSkillTests(unittest.TestCase):
    def test_every_shipped_skill_is_valid_for_claude_and_codex(self):
        names = list(agent_skills.package_skills(str(REPO)))
        self.assertIn(SKILL, names)
        for name in names:
            text = (REPO / "agent-skills" / name / "SKILL.md").read_text(encoding="utf-8")
            front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(front, name)
            self.assertIn(f"name: {name}\n", front.group(1) + "\n")
            self.assertRegex(front.group(1), r"(?m)^description: .{20,1024}$")

    def test_every_shipped_skill_version_is_registered(self):
        # Changing a shipped SKILL.md? Add its new sha256 to RELEASED_SKILL_HASHES and keep
        # the old ones, so existing unedited installs still update and can be removed.
        for name, data in agent_skills.package_skills(str(REPO)).items():
            self.assertIn(agent_skills._sha256(data), agent_skills.RELEASED_SKILL_HASHES.get(name, ()), name)

    def test_released_hashes_are_never_dropped(self):
        known = {
            SKILL: {"cfd82e1531311c5271cf879f81325ee3aa71b9b390750bd972e05f5e826c294f",
                    "938f1e38f8e9666c025896a7a87363bcb4840fb768add85b15d110733190e3a0",
                    "064d01799aabbd921a9daa70c9b2c2d2cc47fdf95cf1202aff72d1a201a955ca"},
            "token-meter-budget-pacing": {"051a4b6fc25fecb48c2b7efd3c3dbebf581183134f84335004dc7765b45ffe8f"},
            "token-meter-skill-hygiene": {"67a92b2800a0eb3e052a232e6fec68145bd9935538709ae47ff7442e1a4fdde5"},
            "token-meter-debug": {"9eabaa8baf633c687b36f57ea9e0887b0128a2ba51c6ab754cd1301c6dc8477f"},
        }
        for name, hashes in known.items():
            self.assertLessEqual(hashes, set(agent_skills.RELEASED_SKILL_HASHES.get(name, ())), name)

    def test_crlf_checkout_hashes_like_lf(self):
        data = (REPO / "agent-skills" / SKILL / "SKILL.md").read_bytes()
        self.assertEqual(agent_skills._sha256(data.replace(b"\n", b"\r\n")), agent_skills._sha256(data))

    def test_package_lists_the_four_skills_and_readme_documents_them(self):
        names = set(agent_skills.package_skills(str(REPO)))
        self.assertEqual(names, {
            SKILL, "token-meter-budget-pacing", "token-meter-skill-hygiene", "token-meter-debug",
        })
        readme = (REPO / "agent-skills" / "README.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", readme)

    def test_every_skill_keeps_the_privacy_rule(self):
        for name in agent_skills.package_skills(str(REPO)):
            text = (REPO / "agent-skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("Never include prompts", text, name)

    def test_budget_skill_changes_caps_only_on_request_with_confirm(self):
        text = (REPO / "agent-skills" / "token-meter-budget-pacing" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Only set a cap when the user directly asks", text)
        self.assertIn("wait for the user's\n  yes", text)
        self.assertIn("never set it on your own", text)
        self.assertIn("`confirm: true`", text)
        self.assertIn("Never change the monthly budget", text)

    def test_hygiene_skill_never_changes_configuration(self):
        text = (REPO / "agent-skills" / "token-meter-skill-hygiene" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Never disable, remove, or edit anything", text)

    DEBUG = REPO / "agent-skills" / "token-meter-debug" / "SKILL.md"

    def debug_text(self):
        return self.DEBUG.read_text(encoding="utf-8")

    def debug_snippet(self, first_line):
        # The skill's fenced Python heredoc bodies, run here as they would be by an agent.
        for block in re.findall(r"```bash\n(.*?)\n```", self.debug_text(), re.S):
            body = re.search(r"<<'PY'\n(.*?)\nPY$", block, re.S)
            if body and body.group(1).startswith(first_line):
                return body.group(1)
        self.fail(f"snippet starting {first_line!r} not found")

    def test_debug_skill_asks_first_stays_read_only_and_posts_only_on_yes(self):
        text = " ".join(self.debug_text().split())
        for rule in (
            "## 1. Ask what they want",
            "**Diagnose**", "**Shareable issue**", "**Fix and pull request**",
            "Diagnosis is read-only",
            "Never open them, print their contents, or list their names",
            "Never read or print `api-token`",
            "Only `/health` is safe to print whole",
            "Create it only after they say yes",
            "ask before reinstalling from the branch",
            "ask before each of commit, push, and pull request creation",
            "failing test built only from synthetic data",
            "Never copy local trace, log, or settings content",
            "Never run the installer copy inside the runtime folder",
            "https://github.com/splunk/token-meter/blob/main/specs/SECURITY.md",
            "Never use `sudo`",
            "Never include prompts",
        ):
            self.assertIn(rule, text)

    def test_debug_skill_log_filter_strips_paths_and_keeps_errors(self):
        code = self.debug_snippet("import re, sys")
        log = Path(tempfile.mkdtemp()) / "meter.err.log"
        self.addCleanup(lambda: (log.unlink(missing_ok=True), log.parent.rmdir()))
        log.write_text(
            "server ready\n"
            "Traceback (most recent call last):\n"
            '  File "/Users/alice/Library/Application Support/Token Meter/runtime/token_meter/app.py", line 42, in do_GET\n'
            "OSError: cannot read /Users/alice/Secret Project/notes.txt\n"
            "FileNotFoundError: C:\\Users\\alice\\clientco\\x.json\n",
            encoding="utf-8",
        )
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["-", str(log)]), contextlib.redirect_stdout(out):
            exec(compile(code, "log-filter", "exec"), {})
        printed = out.getvalue()
        self.assertIn("<path>/app.py, line 42", printed)
        self.assertIn("OSError: cannot read <path>", printed)
        self.assertNotIn("server ready", printed)
        for leak in ("alice", "Secret", "Project", "notes", "clientco", "Support", "Meter"):
            self.assertNotIn(leak, printed)

    def test_debug_skill_endpoint_picker_prints_only_named_fields(self):
        code = self.debug_snippet("import json, sys")
        body = {"clients": [{"id": "codex", "connect_command": "/Users/alice/run", "skills": {"state": "missing"}},
                            {"id": "claude", "connect_command": "/Users/alice/run", "skills": {"state": "installed"}}],
                "actions": {"token": "secret-token"}}
        out = io.StringIO()
        response = io.BytesIO(json.dumps(body).encode())
        with mock.patch.object(sys, "argv", ["-", "/agent-access/status", "clients.*.id", "clients.*.skills.state"]), \
                mock.patch("urllib.request.urlopen", return_value=response) as urlopen, \
                contextlib.redirect_stdout(out):
            exec(compile(code, "endpoint-picker", "exec"), {})
        self.assertEqual(urlopen.call_args.args[0], "http://127.0.0.1:8722/agent-access/status")
        self.assertEqual(out.getvalue().splitlines(), [
            'clients.*.id ["codex", "claude"]',
            'clients.*.skills.state ["missing", "installed"]',
        ])
        self.assertNotIn("alice", out.getvalue())
        self.assertNotIn("secret-token", out.getvalue())

    def test_debug_skill_paths_match_the_installers_and_platforms(self):
        text = self.debug_text()
        read = lambda *parts: (REPO.joinpath(*parts)).read_text(encoding="utf-8")
        install, install_linux = read("scripts", "install"), read("scripts", "install-linux")
        install_windows, start_windows = read("scripts", "install-windows.ps1"), read("scripts", "start-token-meter.ps1")
        launch_agent, systemd = read("scripts", "install-launch-agent"), read("scripts", "install-systemd-user")
        app = read("token_meter", "app.py")
        runtimes = "".join(read("token_meter", "runtimes", name) for name in ("agy.py", "kiro.py", "claude.py"))
        self.assertIn('"$HOME/Library/Application Support/Token Meter/runtime"', install)
        self.assertIn("~/Library/Application Support/Token Meter/runtime", text)
        self.assertIn("${XDG_DATA_HOME:-$HOME/.local/share}/token-meter/runtime", install_linux)
        self.assertIn("${XDG_DATA_HOME:-~/.local/share}/token-meter/runtime", text)
        self.assertIn('"Token Meter\\runtime"', install_windows)
        self.assertIn("%LOCALAPPDATA%\\Token Meter\\runtime", text)
        for script in (install, install_linux, install_windows):
            self.assertIn("TOKEN_METER_INSTALL_ROOT", script)
        self.assertIn("`TOKEN_METER_INSTALL_ROOT`", text)
        for name in ("INSTALLED_REVISION", "INSTALL_MODE", "SOURCE_CHECKOUT"):
            self.assertIn(name, install)
            self.assertIn(f"`{name}`", text)
        for log in ("meter.log", "meter.err.log", "menubar.err.log"):
            self.assertIn(f"$ROOT/{log}", launch_agent)
            self.assertIn(f"`{log}`", text)
        for name in ("meter.log", "meter.err.log", "tray.log", "tray.err.log", "meter.pid", "tray.pid"):
            self.assertIn(f'"{name}"', start_windows)
            self.assertIn(f"`{name}`", text)
        self.assertIn('$RunName = "Token Meter"', install_windows)
        for label in ("com.token-meter.server", "com.token-meter.menubar"):
            self.assertIn(f'"{label}"', launch_agent)
            self.assertIn(f"`{label}`", text)
        for unit in ("token-meter-server", "token-meter-tray"):
            self.assertIn(f"{unit}.service", systemd)
            self.assertIn(unit, text)
        for endpoint in ("/health", "/menubar", "/updates/status", "/agent-access/status",
                         "/work-insights/status"):
            self.assertIn(f'req_path == "{endpoint}"', app)
            self.assertIn(f"`{endpoint}`", text)
        self.assertIn("PORT = 8722", app)
        for field in ("source_clients", "runtime_adapter_failures", "pricing_revision"):
            self.assertIn(f'"{field}"', app)
            self.assertIn(f"`{field}`", text)
        for name in ("settings.json", "update-status.json", "notifications.json", "git-delivery.sqlite3",
                     "work-insights.sqlite3", "api-token"):
            self.assertIn(f"~/.token-meter/{name}", app)
            self.assertIn(f"`{name}`", text)
        for root in ("~/.claude/projects", "~/.codex/sessions", "~/.cursor/projects", "~/.kiro/sessions",
                     "~/.pi/agent", "~/.hermes"):
            self.assertIn(root, app)
            self.assertIn(root, text)
        for env in ("OPENCODE_DB", "KIRO_SESSIONS", "PI_CODING_AGENT_DIR", "HERMES_HOME", "HERMES_STATE_DB"):
            self.assertIn(f'"{env}"', app)
            self.assertIn(f"`{env}`", text)
        for env in ("KIRO_AGENT_STORAGE", "AGY_HOME", "AGY_BRAIN_DIR"):
            self.assertIn(f'"{env}"', runtimes)
            self.assertIn(f"`{env}`", text)
        for folder in ("claude-code-sessions", "local-agent-mode-sessions", "kiro.kiroagent"):
            self.assertIn(f'"{folder}"', runtimes)
            self.assertIn(folder, text)
        self.assertIn('".gemini", "antigravity", "brain"', runtimes)
        self.assertIn("~/.gemini/antigravity/brain", text)

    def test_usage_review_keeps_estimates_and_missing_evidence_rules(self):
        text = (REPO / "agent-skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
        for rule in ("estimate", "Missing evidence", "coverage", "Never include prompts"):
            self.assertIn(rule, text)

    def test_package_ships_in_the_staged_runtime(self):
        entries = packaging.load_manifest(REPO / "runtime-manifest.txt")
        files = packaging.manifest_source_files(REPO, entries)
        self.assertIn(f"agent-skills/{SKILL}/SKILL.md", files)

    def test_dashboard_has_a_separate_skills_button(self):
        page = (REPO / "page.html").read_text(encoding="utf-8")
        for marker in ("data-agent-skills", "/agent-skills/toggle", "Token Meter skills",
                       "function openAgentSkillsDialog", "agentClientActions",
                       "https://github.com/splunk/token-meter/blob/main/agent-skills/README.md"):
            self.assertIn(marker, page)


class AgentSkillHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.roots = {"codex": os.path.join(tmp.name, "codex"), meter.CLAUDE_CODE_CLIENT: os.path.join(tmp.name, "claude")}
        for patcher in (mock.patch.object(meter, "AGENT_SKILL_ROOTS", self.roots),
                        mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/codex")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)

    def post(self, body, action=True):
        port = self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
        if action:
            headers["X-Token-Meter-Action"] = meter._ACTION_TOKEN
        conn.request("POST", "/agent-skills/toggle", json.dumps(body), headers)
        response = conn.getresponse()
        payload = json.loads(response.read() or b"null")
        conn.close()
        return response.status, payload

    def test_toggle_requires_the_action_token(self):
        status, _ = self.post({"client": "codex", "enabled": True}, action=False)
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(self.roots["codex"]))

    def test_toggle_installs_and_removes_over_http(self):
        status, result = self.post({"client": "codex", "enabled": True})
        self.assertEqual(status, 200, result)
        self.assertTrue(os.path.isfile(os.path.join(self.roots["codex"], SKILL, "SKILL.md")))
        status, result = self.post({"client": "codex", "enabled": False})
        self.assertEqual(status, 200, result)
        self.assertFalse(os.path.exists(os.path.join(self.roots["codex"], SKILL)))
        status, result = self.post({"client": "codex", "enabled": False})
        self.assertIn("No Token Meter skills", result["message"])


if __name__ == "__main__":
    unittest.main()
