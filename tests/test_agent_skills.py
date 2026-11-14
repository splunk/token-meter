import contextlib
import http.client
import io
import json
import os
import threading
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
            "token-meter-debug": {"afe6ed2f78aa988e5ab50a7633bc4aa20723c9a4d30508dce517d8bc249266a3"},
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

    def debug_helper(self):
        # The skill's tm_debug.py block, saved and run exactly as an agent would.
        code = re.search(r"```python\n(# tm_debug\.py.*?)\n```", self.debug_text(), re.S)
        self.assertIsNotNone(code)
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        helper = folder / "tm_debug.py"
        helper.write_text(code.group(1) + "\n", encoding="utf-8")
        return helper, folder

    def run_helper(self, helper, *args, stdin="", runtime=None):
        # Isolated from the developer's own install: every default runtime root is inside the temp folder.
        home = helper.parent / "home"
        env = {key: value for key, value in os.environ.items() if not key.startswith("TOKEN_METER_")}
        env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "AppData" / "Local"),
                   XDG_DATA_HOME=str(home / ".local" / "share"))
        if runtime is not None:
            env["TOKEN_METER_INSTALL_ROOT"] = str(runtime)
        result = subprocess.run([sys.executable, "-I", str(helper), *args], input=stdin, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def helper_namespace(self, helper):
        namespace = {"__name__": "tm_debug"}
        exec(compile(helper.read_text(encoding="utf-8"), str(helper), "exec"), namespace)
        return namespace

    def test_debug_skill_asks_first_stays_read_only_and_posts_only_on_yes(self):
        text = " ".join(self.debug_text().split())
        for rule in (
            "## 1. Ask what they want",
            "**Diagnose**", "**Shareable issue**", "**Fix and pull request**",
            "Diagnosis is read-only",
            "Never open them, print their contents, or list their names",
            "Never read or print `api-token`",
            "Only `/health` is safe to print whole",
            "Read them only through the helper in section 3, which allows a fixed set of fields",
            "Never read `/menubar` itself",
            "make a private temp folder",
            'python3 -I "$d/tm_debug.py"',
            "Never branch or edit inside Token Meter's managed clone",
            "`TOKEN_METER_SOURCE_ROOT`",
            "without showing them",
            "Install only from a branch with no upstream",
            "tell the user (`git push -u` sets it again)",
            "restore from a clean `main` checkout",
            "`--backend-only` (Windows `-BackendOnly`)",
            "equals that checkout's `git rev-parse --short HEAD` exactly, with no `+local`",
            "Never reset, clean, or discard changes in any checkout",
            "reuse the folder's literal path",
            "report only true or false",
            "Get-Content -ErrorAction SilentlyContinue",
            "Get-CimInstance Win32_Process",
            "Run both the branch install and the restore with those settings",
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

    def test_debug_skill_log_command_prints_only_frames_and_exception_classes(self):
        helper, folder = self.debug_helper()
        runtime = folder / "runtime"
        (runtime / "token_meter" / "web").mkdir(parents=True)
        (runtime / "token_meter" / "mcp").mkdir()
        (runtime / "meter.py").write_text("main()\n", encoding="utf-8")
        (runtime / "token_meter" / "app.py").write_text("def do_GET(self):\n    pass\n", encoding="utf-8")
        (runtime / "token_meter" / "web" / "server.py").write_text("def serve():\n    pass\n", encoding="utf-8")
        (runtime / "token_meter" / "mcp" / "contracts.py").write_text("class QueryError(ValueError):\n    pass\n",
                                                                      encoding="utf-8")
        log = folder / "meter.err.log"
        log.write_text(
            "server ready for Acmecorp\n"
            "Traceback (most recent call last):\n"
            '  File "/Users/alice/Library/Application Support/Token Meter/runtime/meter.py", line 3, in <module>\n'
            '  File "/Users/alice/Library/Application Support/Token Meter/runtime/token_meter/app.py", line 42, in do_GET\n'
            '  File "/usr/lib/python3.12/socketserver.py", line 826, in write\n'
            '  File "/Users/alice/src/acmecorp_payroll.py", line 9, in secret_func\n'
            '  File "/Users/alice/acmecorp/token_meter/leak.py", line 5, in acme_func\n'
            '  File "/Users/alice/venv/lib/python3.12/acmecorp_billing.py", line 3, in acme_func\n'
            '  File "/Users/alice/venv/lib/python3.12/site-packages/json/acme.py", line 3, in acme_func\n'
            '  File "/Users/alice/work/lib/python3.12/json/acmecorp_spoof.py", line 1, in acme_func\n'
            '  File "/Users/alice/work/runtime/token_meter/acmecorp_spoof.py", line 1, in acme_func\n'
            '  File "/Users/alice/work/runtime/token_meter/app.py", line 1, in acme_func\n'
            '  File "/usr/lib/python3.12/socketserver.py", line 1, in acme_func\n'
            '  File "C:\\Users\\alice\\AppData\\Local\\Token Meter\\runtime\\token_meter\\web\\server.py", line 7, in serve\n'
            "OSError: [Errno 2] No such file: '/Users/alice/Client (Acmecorp)/notes, Inc.txt'\n"
            "PermissionError: [WinError 5] Access is denied: 'C:\\\\Users\\\\alice'\n"
            "KeyError: 'Refactor billing for Acmecorp'\n"
            "ValueError: secret prompt words alice@example.com\n"
            "urllib.error.URLError: <urlopen error refused>\n"
            "token_meter.mcp.contracts.QueryError: bad cursor for Acmecorp\n"
            "sqlite3.OperationalError: database is locked\n"
            "ssl.SSLCertVerificationError: certificate verify failed for acmecorp.example\n"
            "json.JSONDecodeError: Expecting value\n"
            "sqlite3.AcmecorpError: payroll\n"
            "token_meter.app.AcmecorpError: payroll\n"
            "socketserver.AcmecorpError: payroll\n"
            "acmecorp.billing.AcmecorpLedgerError: payroll\n"
            "AcmecorpDomainError: payroll\n"
            + "A" * 200000 + "Error\n"
            "relative Acmecorp/x.json and C:Acmecorp\\x token=Acmecorp-token\n"
            "Nov 05 alices-laptop python3[12]: X-Action-Token: Acmecorp-token\n",
            encoding="utf-8",
        )
        expected = [
            "Traceback", "  frame meter.py:3 in <module>", "  frame app.py:42 in do_GET",
            "  frame socketserver.py:826 in write", "  frame <other>", "  frame <other>", "  frame <other>",
            "  frame <other>", "  frame <other>", "  frame <other>", "  frame <other>", "  frame <other>",
            "  frame server.py:7 in serve", "  OSError [Errno 2]",
            "  PermissionError [WinError 5]", "  KeyError ", "  ValueError ", "  URLError ", "  QueryError ",
            "  OperationalError ", "  SSLCertVerificationError ", "  JSONDecodeError ", "  <other exception> ",
            "  <other exception> ", "  <other exception> ", "  <other exception> ", "  <other exception> ",
        ]
        self.assertEqual(self.run_helper(helper, "log", str(log), runtime=runtime).splitlines(), expected)
        piped = self.run_helper(helper, "log", "-", stdin=log.read_text(encoding="utf-8"), runtime=runtime)
        self.assertEqual(piped.splitlines(), expected)
        for leak in ("alice", "Acmecorp", "acmecorp", "acme", "secret", "Support", "laptop", "token", "@", "AAAA"):
            self.assertNotIn(leak, piped)
        clean = folder / "clean.log"
        clean.write_text("all good\n", encoding="utf-8")
        self.assertEqual(self.run_helper(helper, "log", str(clean)).strip(), "no traceback found")
        self.assertEqual(self.run_helper(helper, "log", str(folder / "Acmecorp" / "tray.err.log")).strip(), "missing")
        self.assertEqual(self.run_helper(helper, "log", str(folder)).strip(), "unreadable")
        if os.name != "nt":
            self.assertEqual(self.run_helper(helper, "log", "/dev/zero").strip(), "unreadable")

    def test_debug_skill_log_command_reads_a_custom_install_root(self):
        helper, folder = self.debug_helper()
        custom = folder / "TM install"
        (custom / "token_meter").mkdir(parents=True)
        (custom / "token_meter" / "app.py").write_text("def do_GET(self):\n    pass\n", encoding="utf-8")
        log = folder / "meter.err.log"
        log.write_text(f'  File "{custom}/token_meter/app.py", line 4, in do_GET\n'
                       '  File "/usr/lib/python3.13t/socketserver.py", line 826, in write\n', encoding="utf-8")
        self.assertEqual(self.run_helper(helper, "log", str(log), runtime=custom).splitlines(),
                         ["  frame app.py:4 in do_GET", "  frame socketserver.py:826 in write"])
        self.assertEqual(self.run_helper(helper, "log", str(log)).splitlines(),
                         ["  frame <other>", "  frame socketserver.py:826 in write"])

    def test_debug_skill_tree_command_walks_nested_roots_and_names_nothing(self):
        helper, folder = self.debug_helper()
        root = folder / "Acmecorp sessions"
        (root / "2026" / "10" / "01").mkdir(parents=True)
        old = root / "2026" / "10" / "01" / "acmecorp-session.jsonl"
        new = root / "2026" / "10" / "01" / "second.jsonl"
        old.write_text("x" * 10, encoding="utf-8")
        new.write_text("y" * 5, encoding="utf-8")
        os.utime(old, (1790000000, 1790000000))
        os.utime(new, (1793000000, 1793000000))
        os.utime(root / "2026", (1780000000, 1780000000))
        newest = time.strftime("%Y-%m-%d %H:%M", time.localtime(1793000000))
        self.assertEqual(self.run_helper(helper, "tree", str(root)).strip(),
                         f"files 2 bytes 15 newest {newest} unreadable 0")
        self.assertEqual(self.run_helper(helper, "tree", str(new)).strip(),
                         f"files 1 bytes 5 newest {newest} unreadable 0")
        self.assertEqual(self.run_helper(helper, "tree", str(folder / "Acmecorp missing")).strip(), "missing")
        (folder / "empty").mkdir()
        self.assertEqual(self.run_helper(helper, "tree", str(folder / "empty")).strip(),
                         "files 0 bytes 0 newest none unreadable 0")
        if hasattr(os, "mkfifo"):
            os.mkfifo(folder / "fifo")
            self.assertEqual(self.run_helper(helper, "tree", str(folder / "fifo")).strip(), "unreadable")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs POSIX permissions")
    def test_debug_skill_tree_command_reports_unreadable_as_unknown(self):
        helper, folder = self.debug_helper()
        root = folder / "Acmecorp"
        (root / "locked").mkdir(parents=True)
        (root / "locked" / "a.jsonl").write_text("x", encoding="utf-8")
        (root / "open.jsonl").write_text("yy", encoding="utf-8")
        os.chmod(root / "locked", 0)
        self.addCleanup(os.chmod, root / "locked", 0o755)
        out = self.run_helper(helper, "tree", str(root)).strip()
        self.assertTrue(out.startswith("files 1 bytes 2 newest ") and out.endswith(" unreadable 1"), out)
        self.assertEqual(self.run_helper(helper, "tree", str(root / "locked")).strip(), "unreadable")
        self.assertEqual(self.run_helper(helper, "tree", str(root / "locked" / "a.jsonl")).strip(), "unreadable")
        os.chmod(root / "open.jsonl", 0)
        self.addCleanup(os.chmod, root / "open.jsonl", 0o644)
        out = self.run_helper(helper, "tree", str(root)).strip()
        self.assertEqual(out, "files 0 bytes 0 newest none unreadable 2")

    def test_debug_skill_field_command_prints_only_allowlisted_fields(self):
        helper, _ = self.debug_helper()
        body = {"ok": True, "launcher_ready": True, "actions": {"token": "secret-token"},
                "clients": [{"id": "codex", "connect_command": "codex mcp add x", "label": "Acmecorp",
                             "skills": {"state": "missing", "names": ["a"]}},
                            {"id": "C:\\Users\\alice", "connect_command": "claude mcp add x", "label": "Acmecorp",
                             "skills": {"state": "installed", "names": ["b"]}}]}
        namespace = self.helper_namespace(helper)
        urlopen = mock.Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps(body).encode()))
        namespace["OPEN"] = urlopen
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            namespace["field"]("/agent-access/status", "ok", "launcher_ready", "actions.token", "actions",
                               "clients.*.id", "clients.*.skills.state", "clients.*.connect_command",
                               "clients.*.label", "clients.*.skills.names", "clients")
            namespace["field"]("/menubar", "session", "source.label")
            namespace["field"]("@evil.example/x", "ok")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(urlopen.call_args.args[0], "http://127.0.0.1:8722/agent-access/status")
        self.assertEqual(out.getvalue().splitlines(), [
            "ok true", "launcher_ready true", "actions.token <not allowed>", "actions <not allowed>",
            'clients.*.id ["codex", "<withheld>"]', 'clients.*.skills.state ["missing", "installed"]',
            "clients.*.connect_command <not allowed>", "clients.*.label <not allowed>",
            "clients.*.skills.names <not allowed>", "clients <not allowed>",
            "/menubar <endpoint not allowed>", "@evil.example/x <endpoint not allowed>",
        ])
        for endpoint, keys in (("/updates/status", "state message ahead behind dirty"),
                               ("/agent-access/status", "clients.*.id clients.*.detected clients.*.connected "
                                                        "clients.*.skills.state clients.*.skills.installed"),
                               ("/work-insights/status", "status.state status.reason status.pending status.setup.state")):
            for key in keys.split():
                self.assertIn(key, namespace["FIELDS"][endpoint])
                self.assertIn(key, self.debug_text())

    def helper_against_redirect(self, helper):
        import http.server
        hits = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        namespace = self.helper_namespace(helper)
        namespace["BASE"] = f"http://127.0.0.1:{server.server_address[1]}"
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"}), \
                contextlib.redirect_stdout(out):
            namespace["status"]("/health")
        self.assertEqual(hits, ["/health"])
        return out.getvalue().strip()

    def test_debug_skill_status_and_failures_print_no_detail(self):
        helper, folder = self.debug_helper()
        namespace = self.helper_namespace(helper)
        namespace["OPEN"] = mock.Mock(side_effect=ConnectionRefusedError("/Users/alice"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            namespace["status"]("/health")
            namespace["status"]("@evil.example/x")
            namespace["main"](["field", "/health", "ok"])
            namespace["main"](["nope"])
            namespace["main"]([])
        self.assertEqual(out.getvalue().splitlines(), [
            "/health unreachable ConnectionRefusedError", "<endpoint not allowed>",
            "error ConnectionRefusedError", "error KeyError", "error IndexError",
        ])
        opener = namespace["urllib"].request.build_opener(namespace["urllib"].request.ProxyHandler({}),
                                                          namespace["NoRedirect"])
        self.assertTrue(any(isinstance(h, namespace["NoRedirect"]) for h in opener.handlers))
        self.assertIn("build_opener(urllib.request.ProxyHandler({}), NoRedirect).open", helper.read_text(encoding="utf-8"))
        self.assertIsNone(namespace["NoRedirect"]().redirect_request(None, None, 302, "Found", {}, "http://evil.example/"))
        self.assertEqual(self.helper_against_redirect(helper), "/health 302")
        result = subprocess.run([sys.executable, "-I", str(helper), "nope"], capture_output=True, text=True, timeout=30)
        self.assertEqual((result.stdout.strip(), result.stderr), ("error KeyError", ""))
        self.assertNotIn(str(folder), result.stdout)

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
        platforms = "".join(read("token_meter", "platforms", n) for n in ("macos.py", "linux.py", "windows.py"))
        self.assertIn('"Cursor", "User", "globalStorage", "state.vscdb"', platforms)
        self.assertIn('"Cursor", "logs"', platforms)
        self.assertIn('posixpath.join(data_home, "opencode")', platforms)
        self.assertIn('ntpath.join(local, "opencode")', platforms)
        for fragment in ("<appdata>/Cursor/User/globalStorage/state.vscdb", "<appdata>/Cursor/logs",
                         "${XDG_DATA_HOME:-~/.local/share}/opencode", "%LOCALAPPDATA%\\opencode"):
            self.assertIn(fragment, text)
        self.assertTrue((REPO / "scripts" / "install-windows.cmd").is_file())
        self.assertIn("scripts\\install-windows.cmd", text)
        self.assertNotIn("install-windows.ps1`", text)
        self.assertIn("systemctl --user show -p ActiveState,SubState,Result,ExecMainStatus token-meter-server", text)
        self.assertIn("-o cat | python3 -I \"$d/tm_debug.py\" log -", text)
        self.assertNotIn("systemctl --user status", text)
        page = read("page.html")
        self.assertIn("Most read numbers through the <code>tokenmeter</code> MCP", page)
        self.assertIn("Usage review, budget pacing, skill hygiene, and debugging", page)
        self.assertNotIn("The skills need the MCP connection", read("specs", "USER_GUIDE.md"))

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
