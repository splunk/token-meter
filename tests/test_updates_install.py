import unittest
import json
import os
import plistlib
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter


class SoftwareUpdateTests(unittest.TestCase):
    def enabled_settings(self, root):
        path = Path(root) / "settings.json"
        path.write_text(json.dumps({"updates": {"enabled": True}, "keep": "value"}))
        return path

    def runner(self, outputs, calls):
        def run(command, **kwargs):
            args = tuple(command[3:])
            calls.append(args)
            value = outputs.get(args)
            if isinstance(value, int):
                return meter.subprocess.CompletedProcess(command, value, "", "failed")
            if value is None:
                raise AssertionError(f"Unexpected git command: {args}")
            return meter.subprocess.CompletedProcess(command, 0, value, "")
        return run

    def test_windows_background_git_checks_do_not_create_a_console_window(self):
        observed = {}

        def runner(command, **kwargs):
            observed.update(kwargs)
            return meter.subprocess.CompletedProcess(command, 0, "main\n", "")

        windows = meter.platform_services(
            "windows", environment={"USERPROFILE": r"C:\Users\example"},
            home=r"C:\Users\example",
        )
        with mock.patch.object(meter, "_PLATFORM_SERVICES", windows):
            self.assertEqual(
                meter._run_update_git(r"C:\Token Meter", ["rev-parse", "HEAD"],
                                      runner=runner),
                "main",
            )

        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_update_setting_defaults_on_and_preserves_an_explicit_off_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"model_pricing": {"claude": {}}}))
            initial = meter.update_settings(str(path))
            invalid = meter.set_update_settings({"enabled": "yes"}, str(path))
            result = meter.set_update_settings(
                {"enabled": False, "auto_install": True}, str(path)
            )
            stored = json.loads(path.read_text())
            explicit = meter.update_settings(str(path))
        self.assertTrue(initial["enabled"])
        self.assertTrue(initial["auto_install"])
        self.assertEqual(initial["interval_seconds"], 600)
        self.assertFalse(invalid["ok"])
        self.assertTrue(result["ok"])
        self.assertFalse(explicit["enabled"])
        self.assertFalse(explicit["auto_install"])
        self.assertEqual(
            stored["updates"], {"enabled": False, "auto_install": False}
        )
        self.assertIn("model_pricing", stored)

    def test_auto_install_can_be_disabled_without_disabling_update_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"keep": "value"}))
            result = meter.set_update_settings(
                {"enabled": True, "auto_install": False}, str(path)
            )
            stored = json.loads(path.read_text())
            explicit = meter.update_settings(str(path))
        self.assertTrue(result["ok"])
        self.assertEqual(
            explicit,
            {"enabled": True, "auto_install": False, "interval_seconds": 600},
        )
        self.assertEqual(
            stored,
            {"keep": "value", "updates": {"enabled": True, "auto_install": False}},
        )

    def test_menubar_update_projection_is_bounded_and_actionable(self):
        status = {
            "enabled": True,
            "auto_install": False,
            "state": "available",
            "available": True,
            "can_update": True,
            "current_revision": "a" * 40,
            "latest_revision": "b" * 40,
            "message": "/private/source checkout has an update",
            "path": "/private/source",
            "actions": {
                "token": "local_action_token-123",
                "check": True,
                "install": True,
            },
        }
        projected = meter.menubar_software_update(status)
        self.assertEqual(projected, {
            "enabled": True,
            "auto_install": False,
            "state": "available",
            "available": True,
            "can_update": True,
            "current_revision": "a" * 40,
            "latest_revision": "b" * 40,
            "actions": {
                "token": "local_action_token-123",
                "install": True,
            },
        })
        self.assertNotIn("message", projected)
        self.assertNotIn("path", projected)

    def test_update_watcher_preserves_terminal_result_for_one_interval_then_checks(self):
        wake = mock.Mock()
        wake.wait.side_effect = [False, RuntimeError("stop watcher")]
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "complete"}),
              mock.patch.object(meter, "check_for_software_update") as check):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        self.assertEqual(
            wake.wait.call_args_list,
            [mock.call(meter.UPDATE_CHECK_INTERVAL_S), mock.call(meter.UPDATE_CHECK_INTERVAL_S)],
        )
        check.assert_called_once_with()

    def test_update_watcher_starts_a_safe_available_update_when_enabled(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": "b" * 40,
        }
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": True},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "current"}),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ) as check,
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        check.assert_called_once_with()
        start.assert_called_once_with()

    def test_update_watcher_keeps_checking_when_auto_install_is_disabled(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": "b" * 40,
        }
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "current"}),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ) as check,
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        check.assert_called_once_with()
        start.assert_not_called()

    def test_update_watcher_does_not_repeat_the_same_failed_revision(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        target = "b" * 40
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": target,
        }
        record = {"phase": "available", "failed_revision": target}
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": True},
              ),
              mock.patch.object(meter, "_update_status_record", return_value=record),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ),
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        start.assert_not_called()

    def test_automatic_check_fetches_and_reports_a_clean_fast_forward_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            calls = []
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/main\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
                ("fetch", "--quiet", "--prune", "--no-tags", "origin"): "",
                ("rev-parse", "HEAD"): "a" * 40,
                ("rev-parse", "@{upstream}"): "b" * 40,
                ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): "0\t2\n",
                ("status", "--porcelain"): "",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, calls),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "available")
        self.assertTrue(status["available"])
        self.assertTrue(status["can_update"])
        self.assertEqual(status["current_revision"], "a" * 40)
        self.assertEqual(status["latest_revision"], "b" * 40)
        self.assertEqual(status["behind"], 2)
        self.assertEqual(status["next_check_at"], 1834)
        self.assertIn(("fetch", "--quiet", "--prune", "--no-tags", "origin"), calls)
        self.assertNotIn(tmp, json.dumps(status))

    def test_available_update_fails_closed_when_checkout_is_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/main\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
                ("fetch", "--quiet", "--prune", "--no-tags", "origin"): "",
                ("rev-parse", "HEAD"): "a" * 40,
                ("rev-parse", "@{upstream}"): "b" * 40,
                ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): "0 1",
                ("status", "--porcelain"): " M page.html\n",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, []),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "attention")
        self.assertTrue(status["available"])
        self.assertFalse(status["can_update"])
        self.assertTrue(status["dirty"])
        self.assertIn("local changes", status["message"])

    def test_automatic_update_rejects_a_checkout_not_on_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/feature\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "feature\n",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, []),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "attention")
        self.assertFalse(status["can_update"])
        self.assertIn("main", status["message"])

    def test_linux_helper_failure_has_an_actionable_bounded_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "failed",
                "error_code": "linux_helper_unavailable",
                "checked_at": 1234,
                "available": False,
                "can_update": False,
            }, str(status_path))
            status = meter.software_update_status(
                settings_path=str(settings_path), status_path=str(status_path),
            )

        self.assertEqual(status["state"], "attention")
        self.assertEqual(
            status["message"],
            "The installed Linux update helper is unavailable. Reinstall Token Meter to repair automatic updates.",
        )
        self.assertNotIn(tmp, json.dumps(status))

    def test_explicit_install_starts_only_the_bounded_detached_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "available",
                "current_revision": "a" * 40,
                "latest_revision": "b" * 40,
                "checked_at": 1234,
                "available": True,
                "can_update": True,
                "ahead": 0,
                "behind": 1,
                "dirty": False,
            }, str(status_path))
            popen = mock.Mock()
            macos = meter.platform_services("darwin", environment={}, home=tmp)
            with (mock.patch.object(meter, "source_checkout_path", return_value=tmp),
                  mock.patch.object(meter, "_PLATFORM_SERVICES", macos),
                  mock.patch.object(meter.os.path, "isfile", return_value=True),
                  mock.patch.object(meter.os, "access", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            command = popen.call_args.args[0]
            kwargs = popen.call_args.kwargs
        self.assertTrue(result["ok"])
        self.assertTrue(command[0].endswith("/scripts/update"))
        self.assertEqual(command[1:], [tmp, str(status_path)])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("cwd", kwargs)
        self.assertNotIn(tmp, json.dumps(result))

    def test_windows_install_starts_the_helper_outside_the_runtime_it_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            checkout = os.path.join(tmp, "source")
            os.mkdir(checkout)
            meter._persist_update_status({
                "phase": "available",
                "current_revision": "a" * 40,
                "latest_revision": "b" * 40,
                "checked_at": 1234,
                "available": True,
                "can_update": True,
                "ahead": 0,
                "behind": 1,
                "dirty": False,
            }, str(status_path))
            popen = mock.Mock()
            windows = meter.platform_services("windows", environment={}, home=tmp)
            with (mock.patch.object(meter, "source_checkout_path", return_value=checkout),
                  mock.patch.object(meter, "_PLATFORM_SERVICES", windows),
                  mock.patch.object(meter.os.path, "isfile", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            command = popen.call_args.args[0]
            kwargs = popen.call_args.kwargs
        self.assertTrue(result["ok"])
        self.assertEqual(command[0], "powershell.exe")
        self.assertEqual(command[-2:], [checkout, str(status_path)])
        self.assertEqual(kwargs["cwd"], checkout)
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP; DETACHED_PROCESS stops
        # Windows PowerShell before the helper runs.
        self.assertEqual(kwargs["creationflags"], 0x08000200)

    def test_windows_install_refuses_a_checkout_that_disappeared(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "available",
                "current_revision": "a" * 40,
                "latest_revision": "b" * 40,
                "checked_at": 1234,
                "available": True,
                "can_update": True,
                "ahead": 0,
                "behind": 1,
                "dirty": False,
            }, str(status_path))
            popen = mock.Mock()
            windows = meter.platform_services("windows", environment={}, home=tmp)
            with (mock.patch.object(meter, "source_checkout_path",
                                    return_value=os.path.join(tmp, "missing")),
                  mock.patch.object(meter, "_PLATFORM_SERVICES", windows),
                  mock.patch.object(meter.os.path, "isfile", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            phase = meter._update_status_record(str(status_path)).get("phase")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "The installed updater is unavailable.")
        popen.assert_not_called()
        self.assertEqual(phase, "available")

    def test_linux_install_launches_the_helper_as_a_transient_user_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            target = "b" * 40
            meter._persist_update_status({
                "phase": "failed",
                "error_code": "update_interrupted",
                "current_revision": "a" * 40,
                "latest_revision": target,
                "failed_revision": target,
                "checked_at": 1234,
                "available": True,
                "can_update": False,
            }, str(status_path))
            popen = mock.Mock()
            linux = meter.platform_services(
                "linux", environment={"INVOCATION_ID": "a" * 32}, home=tmp,
            )
            with (mock.patch.object(meter, "source_checkout_path", return_value=tmp),
                  mock.patch.object(meter, "_PLATFORM_SERVICES", linux),
                  mock.patch("token_meter.platforms.linux.shutil.which",
                             return_value="/usr/bin/systemd-run"),
                  mock.patch.object(meter.os.path, "isfile", return_value=True),
                  mock.patch.object(meter.os, "access", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            command = popen.call_args.args[0]
        self.assertTrue(result["ok"])
        self.assertEqual(command[:2], ["/usr/bin/systemd-run", "--user"])
        self.assertIn("--setenv=TOKEN_METER_UPDATE_RETRY_REVISION=" + target, command)
        separator = command.index("--")
        self.assertTrue(command[separator + 1].endswith("/scripts/update"))
        self.assertEqual(command[separator + 2:], [tmp, str(status_path)])

    def test_stale_in_progress_update_becomes_a_retryable_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            target = "b" * 40
            record = {
                "phase": "installing",
                "current_revision": target,
                "latest_revision": target,
                "previous_revision": "a" * 40,
                "checked_at": 1000,
                "started_at": 1001,
                "available": True,
                "can_update": False,
            }
            meter._persist_update_status(record, str(status_path))
            fresh = meter._recover_stale_update(
                record, now=1001 + meter.UPDATE_STALE_AFTER_S - 1,
                status_path=str(status_path),
            )
            still_installing = json.loads(status_path.read_text())["phase"]
            recovered = meter._recover_stale_update(
                record, now=1001 + meter.UPDATE_STALE_AFTER_S,
                status_path=str(status_path),
            )
            stored = json.loads(status_path.read_text())
            status = meter.software_update_status(
                settings_path=str(settings_path), status_path=str(status_path),
            )
        self.assertFalse(fresh)
        self.assertEqual(still_installing, "installing")
        self.assertTrue(recovered)
        self.assertEqual(stored["phase"], "failed")
        self.assertEqual(stored["error_code"], "update_interrupted")
        self.assertEqual(stored["failed_revision"], target)
        self.assertEqual(stored["previous_revision"], "a" * 40)
        self.assertEqual(status["state"], "attention")
        self.assertIn("stopped before it finished", status["message"])

    def test_stale_check_falls_back_to_checked_at_and_needs_a_timestamp(self):
        """The Windows helper writes in-progress records without started_at."""
        with tempfile.TemporaryDirectory() as tmp:
            status_path = str(Path(tmp) / "update-status.json")
            windows_record = {
                "phase": "installing", "checked_at": 1000, "latest_revision": "b" * 40,
            }
            meter._persist_update_status(windows_record, status_path)
            fresh = meter._recover_stale_update(
                windows_record, now=1005, status_path=status_path,
            )
            undated = meter._recover_stale_update(
                {"phase": "fetching"}, now=10 ** 9, status_path=status_path,
            )
            still_installing = json.loads(Path(status_path).read_text())["phase"]
            stale = meter._recover_stale_update(
                windows_record, now=1000 + meter.UPDATE_STALE_AFTER_S,
                status_path=status_path,
            )
            stored = json.loads(Path(status_path).read_text())
        self.assertFalse(fresh)
        self.assertFalse(undated)
        self.assertEqual(still_installing, "installing")
        self.assertTrue(stale)
        self.assertEqual(stored["error_code"], "update_interrupted")

    def test_update_watcher_waits_on_a_fresh_in_progress_update(self):
        wake = mock.Mock()
        wake.wait.side_effect = [None, RuntimeError("stop watcher")]
        fresh = {"phase": "installing", "started_at": int(time.time())}
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(meter, "_update_status_record", return_value=fresh),
              mock.patch.object(meter, "_persist_update_status") as persist,
              mock.patch.object(meter, "check_for_software_update") as check):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        persist.assert_not_called()
        self.assertEqual(wake.wait.call_args_list, [mock.call(5), mock.call(5)])
        check.assert_not_called()

    def test_update_watcher_recovers_a_stale_in_progress_update(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        stale = {"phase": "installing", "started_at": 1}
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(
                  meter, "_update_status_record",
                  side_effect=[stale, {"phase": "failed"}],
              ),
              mock.patch.object(meter, "_persist_update_status", return_value=True) as persist,
              mock.patch.object(meter, "check_for_software_update") as check):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        self.assertEqual(persist.call_args.args[0]["error_code"], "update_interrupted")
        self.assertEqual(wake.wait.call_args_list, [mock.call(meter.UPDATE_CHECK_INTERVAL_S)])
        check.assert_not_called()

    def test_explicit_install_retries_a_failed_current_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            target = "b" * 40
            meter._persist_update_status({
                "phase": "failed",
                "error_code": "install_failed",
                "current_revision": target,
                "latest_revision": target,
                "previous_revision": "a" * 40,
                "failed_revision": target,
                "checked_at": 1234,
                "available": False,
                "can_update": False,
            }, str(status_path))
            popen = mock.Mock()
            with (mock.patch.object(meter, "source_checkout_path", return_value=tmp),
                  mock.patch.object(meter.os.path, "isfile", return_value=True),
                  mock.patch.object(meter.os, "access", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            retry_environment = popen.call_args.kwargs["env"]
        self.assertTrue(result["ok"])
        self.assertEqual(
            retry_environment["TOKEN_METER_UPDATE_RETRY_REVISION"], target
        )

    def test_manual_check_cannot_overwrite_an_install_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "installing",
                "current_revision": "b" * 40,
                "latest_revision": "b" * 40,
                "available": True,
                "can_update": False,
            }, str(status_path))
            result = meter.trigger_software_update_check(
                settings_path=str(settings_path),
                status_path=str(status_path),
            )
            stored = json.loads(status_path.read_text())
        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["error"])
        self.assertEqual(stored["phase"], "installing")


class InstallationTests(unittest.TestCase):
    def test_menu_bar_launcher_builds_a_stable_background_app_bundle(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run-menubar").read_text()
        info = plistlib.loads((root / "menubar" / "Info.plist").read_bytes())
        self.assertIn('APP="$ROOT/.build/Token Meter Menu Bar.app"', script)
        self.assertIn('ditto "$ROOT/menubar/Info.plist" "$INFO"', script)
        self.assertIn('exec "$BIN"', script)
        self.assertEqual(info["CFBundleIdentifier"], "com.token-meter.menubar")
        self.assertEqual(info["CFBundleExecutable"], "token-meter-menubar")
        self.assertTrue(info["LSUIElement"])

    def test_user_installer_waits_for_both_supervised_runtime_jobs_and_returns_control(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install").read_text()
        manifest = (root / "runtime-manifest.txt").read_text()
        server_start = script.index(
            '"$INSTALL_ROOT/scripts/install-launch-agent" server-only'
        )
        readiness_check = script.index('"state_ready": true')
        menubar_start = script.index(
            '"$INSTALL_ROOT/scripts/install-launch-agent" menubar-only'
        )
        self.assertLess(server_start, readiness_check)
        self.assertLess(readiness_check, menubar_start)
        self.assertIn('$HOME/Library/Application Support/Token Meter/runtime', script)
        self.assertIn('TOKEN_METER_READINESS_TIMEOUT_SECONDS:-600', script)
        self.assertIn('curl -fsS --max-time 5 "$HEALTH_URL"', script)
        self.assertNotIn('curl -fsS --max-time 1 "$HEALTH_URL"', script)
        self.assertIn('launchctl print "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl print "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('"$INSTALL_ROOT/meter.py"', script)
        self.assertIn('"$INSTALL_ROOT/scripts/run-menubar"', script)
        self.assertIn('ditto "$source_path" "$install_path"', script)
        self.assertIn('python3 -m token_meter.packaging parity', script)
        self.assertIn('assets/brand/logo-splunk-by-cisco-rgb-w.png', manifest)
        self.assertIn('menubar/Info.plist', manifest)
        self.assertIn(
            'printf \'%s\\n\' "$UPDATE_SOURCE_ROOT" > "$INSTALL_ROOT/SOURCE_CHECKOUT"',
            script,
        )
        self.assertIn('git clone --quiet --no-local "$SOURCE_ROOT"', script)
        self.assertIn(
            'git -C "$MANAGED_SOURCE_ROOT" fetch --quiet --no-tags',
            script,
        )
        self.assertIn(
            'git -C "$MANAGED_SOURCE_ROOT" merge --quiet --ff-only FETCH_HEAD',
            script,
        )
        self.assertIn('remote set-url origin "$source_remote_url"', script)
        self.assertIn('"branch.$managed_branch.merge" "refs/heads/$source_branch"', script)
        self.assertIn('source_slug=', script)
        self.assertIn('source_slug="${source_slug%.git}"', script)
        self.assertIn('MANAGED_SOURCE_ROOT="$INSTALL_PARENT/source-$source_slug"', script)
        self.assertIn('Preserving the existing managed checkout at:', script)
        self.assertIn("Token Meter installation complete.", script)
        self.assertNotIn('exec "$INSTALL_ROOT/scripts/install-launch-agent"', script)

    def test_installers_show_progress_during_long_waits_and_always_clean_up(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("install", "install-linux"):
            with self.subTest(installer=name):
                script = (root / "scripts" / name).read_text()
                intro = script.index(
                    'echo "Indexing local agent history (first run can take a few minutes)\u2026"'
                )
                start = script.index('progress_start "Indexing local agent history" "Still indexing\u2026"')
                readiness = script.index('readiness_deadline=$((SECONDS + READINESS_TIMEOUT_SECONDS))')
                done = script.index('progress_done "Server ready"')
                self.assertLess(intro, start)
                self.assertLess(start, readiness)
                self.assertLess(readiness, done)
                self.assertIn("if [ -t 1 ]; then", script)
                self.assertIn("frames=(\u280b \u2819 \u2839 \u2838 \u283c \u2834 \u2826 \u2827 \u2807 \u280f)", script)
                self.assertIn('while kill -0 "$parent" 2>/dev/null; do', script)
                # The macOS installer also stops its background menu bar build.
                cleanup = "install_cleanup" if name == "install" else "progress_stop"
                self.assertIn(f"trap {cleanup} EXIT", script)
                self.assertIn(f"trap '{cleanup}; trap - INT; kill -INT $$' INT", script)
                self.assertIn(f"trap '{cleanup}; trap - TERM; kill -TERM $$' TERM", script)
                self.assertIn('fail() {\n  progress_stop\n  echo "Token Meter installation failed: $*" >&2', script)
                self.assertNotIn("\\033", script)
                self.assertIn("the local server did not finish indexing within ${READINESS_TIMEOUT_SECONDS}s at $HEALTH_URL.", script)
        mac = (root / "scripts" / "install").read_text()
        self.assertIn('install_cleanup() {\n  progress_stop\n  stop_menubar_build\n}', mac)
        # The companion compiles in the background while the server indexes,
        # but only when a toolchain is already installed: probing goes through
        # `xcode-select -p`, which never opens Apple's install dialog.
        self.assertIn('developer_dir="$(xcode-select -p 2>/dev/null)" || return 1', mac)
        readiness = mac.index("readiness_deadline=$((SECONDS + READINESS_TIMEOUT_SECONDS))")
        early_start = mac.index("  if swift_toolchain_installed; then\n    start_menubar_build\n  fi")
        self.assertLess(early_start, readiness)
        self.assertNotIn("swiftc", mac[mac.index("install_cleanup() {"):readiness])
        build_check = mac.index('"$INSTALL_ROOT/scripts/run-menubar" --check')
        self.assertIn('MENUBAR_BUILD_PID=$!', mac[build_check:build_check + 200])
        build_intro = mac.index(
            'echo "Building the menu bar companion (first build compiles Swift, ~1 min)\u2026"'
        )
        build_start = mac.index('progress_start "Building the menu bar companion" "Still building\u2026"')
        build_wait = mac.index('wait "$MENUBAR_BUILD_PID" || menubar_check_status=$?')
        build_stop = mac.index("progress_stop", build_wait)
        self.assertLess(build_start, build_wait)
        build_fail = mac.index(
            'fail "the native companion cannot build with the installed Swift toolchain and macOS SDKs."'
        )
        build_done = mac.index('progress_done "Menu bar companion ready"')
        self.assertLess(build_intro, build_start)
        self.assertLess(build_wait, build_stop)
        self.assertLess(build_stop, build_fail)
        self.assertLess(build_fail, build_done)

    def test_update_helper_requires_clean_fast_forward_then_reuses_installer(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "update").read_text()
        self.assertIn("git -C \"$SOURCE_ROOT\" fetch --quiet --prune --no-tags", script)
        self.assertIn("git -C \"$SOURCE_ROOT\" status --porcelain", script)
        self.assertIn("git -C \"$SOURCE_ROOT\" merge --ff-only '@{upstream}'", script)
        self.assertIn('branch="$(git -C "$SOURCE_ROOT" rev-parse --abbrev-ref HEAD', script)
        self.assertIn('[[ "$branch" != "main" || "${upstream##*/}" != "main" ]]', script)
        self.assertIn('TOKEN_METER_UPDATE_RETRY_REVISION', script)
        self.assertIn('record["failed_revision"]', script)
        self.assertIn('INSTALL_SCRIPT="$SOURCE_ROOT/scripts/install"', script)
        self.assertIn('INSTALL_SCRIPT="$SOURCE_ROOT/scripts/install-linux"', script)
        self.assertIn('TOKEN_METER_INSTALL_ROOT="$RUNTIME_ROOT" "$INSTALL_SCRIPT"', script)
        self.assertNotIn("reset --hard", script)
        self.assertNotIn("sudo ", script)

    def test_update_entrypoint_dispatches_linux_to_linux_helper(self):
        root = Path(__file__).resolve().parents[1]
        entrypoint = (root / "scripts" / "update").read_text()
        linux_helper = (root / "scripts" / "update-linux").read_text()
        manifest = (root / "runtime-manifest.txt").read_text()

        self.assertIn('case "$(uname -s)" in', entrypoint)
        self.assertIn('LINUX_HELPER="$ENTRYPOINT_ROOT/scripts/update-linux"', entrypoint)
        self.assertIn('[[ ! -x "$LINUX_HELPER" ]]', entrypoint)
        self.assertIn('"error_code": "linux_helper_unavailable"', entrypoint)
        self.assertIn('exec env TOKEN_METER_UPDATE_LINUX_HELPER=1 "$LINUX_HELPER" "$@"', entrypoint)
        self.assertIn('Darwin)', entrypoint)
        self.assertIn('supported platforms are macOS and Linux.', entrypoint)
        self.assertIn('[[ "$(uname -s)" == "Linux" ]]', linux_helper)
        self.assertIn(
            'exec env TOKEN_METER_UPDATE_LINUX_HELPER=1 "$ENTRYPOINT_ROOT/scripts/update" "$@"',
            linux_helper,
        )
        self.assertNotIn('git -C "$SOURCE_ROOT"', linux_helper)
        self.assertIn('required scripts/update-linux', manifest)

    def test_windows_update_helper_requires_main_and_supports_failed_retry(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "update-windows.ps1").read_text()
        self.assertIn('$Branch = (Get-GitText @("-C", $SourceRoot, "rev-parse", "--abbrev-ref", "HEAD"))', script)
        self.assertIn('$Branch -ne "main"', script)
        self.assertIn('$env:TOKEN_METER_UPDATE_RETRY_REVISION', script)
        self.assertIn('failed_revision =', script)

    def test_launch_agents_supervise_server_and_menu_bar_independently(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install-launch-agent").read_text()
        self.assertIn('SERVER_LABEL="com.token-meter.server"', script)
        self.assertIn('MENUBAR_LABEL="com.token-meter.menubar"', script)
        self.assertIn("<string>$SERVER_PROGRAM</string>", script)
        self.assertIn("<string>$MENUBAR_PROGRAM</string>", script)
        self.assertIn("Verify the replacement survives that", script)
        self.assertGreaterEqual(
            script.count('launchctl print "gui/$UID/$label"'), 3,
        )
        self.assertEqual(script.count("<key>KeepAlive</key>"), 2)
        self.assertEqual(script.count("<key>SuccessfulExit</key>"), 1)
        self.assertEqual(script.count("<key>SuccessfulExit</key>\n    <false/>"), 1)
        self.assertNotIn("start-token-meter", script)
        self.assertIn("all|server-only|menubar-only", script)
        self.assertIn("server-only)", script)
        self.assertIn("menubar-only)", script)
        self.assertNotIn("kickstart -k", script)
        self.assertIn("attempt <= 10", script)
        self.assertIn('launchctl print "gui/$UID/$label"', script)
        self.assertIn("sleep 0.2", script)
        for label in ("SERVER_LABEL", "MENUBAR_LABEL"):
            self.assertIn(f'launchctl bootout "gui/$UID/${label}"', script)

    def test_foreground_launcher_waits_for_indexing_before_starting_menubar(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "start-token-meter").read_text()
        readiness_check = script.index('"state_ready": true')
        menubar_start = script.index('exec "$ROOT/scripts/run-menubar"')
        self.assertLess(readiness_check, menubar_start)
        self.assertIn('TOKEN_METER_READINESS_TIMEOUT_SECONDS:-600', script)
        self.assertIn('curl -fsS --max-time 5 "$HEALTH_URL"', script)
        self.assertNotIn('curl -fsS --max-time 1 "$HEALTH_URL"', script)

    def test_uninstaller_removes_both_supervised_jobs(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "uninstall-launch-agent").read_text()
        self.assertIn('launchctl bootout "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl bootout "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('rm -f "$MENUBAR_PLIST" "$SERVER_PLIST"', script)

    def test_linux_installer_uses_xdg_runtime_systemd_and_appindicator_tray(self):
        root = Path(__file__).resolve().parents[1]
        entrypoint = (root / "scripts" / "install").read_text()
        installer = (root / "scripts" / "install-linux").read_text()
        systemd = (root / "scripts" / "install-systemd-user").read_text()
        runner = (root / "scripts" / "run-menubar").read_text()
        tray = (root / "menubar" / "token_meter_tray.py").read_text()
        self.assertIn('exec "$SOURCE_ROOT/scripts/install-linux" "$@"', entrypoint)
        self.assertNotIn("install-systemd-user", entrypoint)
        self.assertNotIn("launchctl", installer)
        self.assertNotIn("install-launch-agent", installer)
        self.assertIn("XDG_DATA_HOME", installer)
        self.assertIn("install-systemd-user", installer)
        self.assertIn('"$INSTALL_ROOT/scripts/install-systemd-user" server-only', installer)
        self.assertIn('"$INSTALL_ROOT/scripts/install-systemd-user" menubar-only', installer)
        self.assertIn("systemctl --user is-active", installer)
        self.assertIn("token-meter-server.service", systemd)
        self.assertIn("token-meter-tray.service", systemd)
        self.assertIn("all|server-only|menubar-only", systemd)
        self.assertEqual(systemd.count("Restart=on-failure"), 2)
        self.assertIn("Linux)", runner)
        self.assertIn("AyatanaAppIndicator3", tray)
        self.assertIn("AppIndicator3", tray)
        self.assertIn("XDG_CONFIG_HOME", tray)
        self.assertIn("refresh_menu_content", tray)
        self.assertIn("self.indicator.set_menu(self.menu)", tray)
        self.assertNotIn("self.indicator.set_menu(menu)", tray)
        self.assertIn("Recent sessions", tray)
        self.assertIn("Provider limits", tray)
        self.assertIn("Open Budget Settings", tray)
        self.assertIn("Menu bar title", tray)
        self.assertIn("def open_dashboard(self):", tray)

    def test_linux_runtime_uses_xdg_desktop_and_trash_paths(self):
        if not meter.IS_LINUX:
            self.skipTest("Linux-specific default paths")
        self.assertTrue(meter.CURSOR_STATE_DB.startswith(meter.XDG_CONFIG_HOME))
        self.assertTrue(all(path.startswith(meter.XDG_CONFIG_HOME)
                            for path in meter.CLAUDE_DESKTOP_DATA_ROOTS))
        self.assertEqual(meter.session_action_capability()["destination"], "Trash")


class RemovedAgentDefaultsEndpointTests(unittest.TestCase):
    def test_removed_agent_defaults_endpoint_returns_not_found(self):
        for method in ("do_GET", "do_POST"):
            with self.subTest(method=method):
                handler = object.__new__(meter.H)
                handler.path = "/settings/agent-defaults"
                errors, responses = [], []
                handler.send_error = lambda status: errors.append(status)
                handler._send = lambda *args, **kwargs: responses.append((args, kwargs))
                handler.headers = {"Host": "127.0.0.1:8722"}
                getattr(handler, method)()
                self.assertEqual(errors, [404])
                self.assertEqual(responses, [])


@unittest.skip("Agent defaults feature removed")
class AgentDefaultSettingsTests(unittest.TestCase):
    def test_agent_default_status_does_not_project_model_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "existing-codex-model"\nmodel_reasoning_effort = "high"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "existing-claude-model",
                "modelOverrides": {"alias": "existing-provider-model"},
                "effortLevel": "high",
            }))

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        serialized = json.dumps(status)
        for client in status["clients"]:
            self.assertNotIn("model", client["defaults"])
            self.assertNotIn("hidden_model", client)
        for configured_model in (
            "existing-codex-model", "existing-claude-model", "existing-provider-model",
        ):
            self.assertNotIn(configured_model, serialized)

    def test_saving_agent_behavior_defaults_preserves_existing_model_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "existing-codex-model"\nmodel_reasoning_effort = "low"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "existing-claude-model",
                "modelOverrides": {"alias": "existing-provider-model"},
                "effortLevel": "low",
            }))

            codex = meter.set_agent_default_settings(
                "codex", {
                    "model": "legacy-request-model", "preserve_model": False,
                    "reasoning_effort": "high", "context_limit": 128000,
                },
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {
                    "model": "legacy-request-model", "preserve_model": False,
                    "reasoning_effort": "xhigh", "context_limit": 500000,
                },
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertIn('model = "existing-codex-model"', codex_text)
        self.assertEqual(claude_json["model"], "existing-claude-model")
        self.assertEqual(
            claude_json["modelOverrides"], {"alias": "existing-provider-model"},
        )
        self.assertNotIn("model", codex["defaults"])
        self.assertNotIn("model", claude["defaults"])

    def test_agent_default_save_rejects_concurrent_model_changes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "initial-codex-model"\nmodel_reasoning_effort = "low"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "initial-claude-model",
                "modelOverrides": {"alias": "initial-provider-model"},
                "effortLevel": "low",
            }))
            original_write = meter.atomic_write_text

            def concurrent_codex_write(path, text, *args, **kwargs):
                codex_path.write_text(
                    codex_path.read_text().replace(
                        "initial-codex-model", "concurrent-codex-model",
                    )
                )
                return original_write(path, text, *args, **kwargs)

            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=concurrent_codex_write):
                codex = meter.set_agent_default_settings(
                    "codex", {"reasoning_effort": "high", "context_limit": 128000},
                    codex_config=str(codex_path), claude_settings=str(claude_path),
                )

            def concurrent_claude_write(path, text, *args, **kwargs):
                concurrent = json.loads(claude_path.read_text())
                concurrent["model"] = "concurrent-claude-model"
                concurrent["modelOverrides"] = {"alias": "concurrent-provider-model"}
                claude_path.write_text(json.dumps(concurrent))
                return original_write(path, text, *args, **kwargs)

            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=concurrent_claude_write):
                claude = meter.set_agent_default_settings(
                    "claude", {"reasoning_effort": "xhigh", "context_limit": 500000},
                    codex_config=str(codex_path), claude_settings=str(claude_path),
                )

            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(codex["ok"])
        self.assertFalse(claude["ok"])
        self.assertIn('model = "concurrent-codex-model"', codex_text)
        self.assertEqual(claude_json["model"], "concurrent-claude-model")
        self.assertEqual(
            claude_json["modelOverrides"], {"alias": "concurrent-provider-model"},
        )
        for secret in (
            "concurrent-codex-model", "concurrent-claude-model",
            "concurrent-provider-model",
        ):
            self.assertNotIn(secret, json.dumps({"codex": codex, "claude": claude}))

    def test_codex_defaults_report_cli_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "gpt-5.6-terra"\n')
            claude_path.write_text("{}\n")

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            saved = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "high", "context_limit": 128000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        codex = status["clients"][0]
        self.assertEqual(codex["scope"], "cli")
        self.assertEqual(codex["label"], "Codex CLI")
        self.assertEqual(saved["scope"], "cli")
        self.assertIn("Codex CLI defaults saved", saved["message"])
        self.assertNotIn("Desktop", saved["message"])

    def test_agent_default_settings_update_only_the_supported_client_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                '# Keep this comment.\nmodel = "old-model"\n'
                'model_reasoning_effort = "low"\n\n'
                '[mcp_servers.local]\ncommand = "keep-me"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "old-model",
                "effortLevel": "low",
                "env": {"KEEP": "value"},
                "enabledPlugins": {"keep@personal": True},
            }))

            codex = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "high", "context_limit": 128000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "xhigh", "context_limit": 500000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertTrue(codex["ok"])
        self.assertTrue(codex["verified"])
        self.assertTrue(claude["ok"])
        self.assertTrue(claude["verified"])
        self.assertIn('model = "old-model"', codex_text)
        self.assertIn('model_reasoning_effort = "high"', codex_text)
        self.assertIn("model_context_window = 128000", codex_text)
        self.assertIn('[mcp_servers.local]\ncommand = "keep-me"', codex_text)
        self.assertEqual(claude_json["model"], "old-model")
        self.assertEqual(claude_json["effortLevel"], "xhigh")
        self.assertEqual(claude_json["env"], {
            "KEEP": "value", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "500000",
        })
        self.assertEqual(claude_json["enabledPlugins"], {"keep@personal": True})

    def test_clearing_agent_default_settings_removes_only_the_saved_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "gpt-5.6-terra"\nmodel_reasoning_effort = "high"\n'
                'model_context_window = 256000\n[features]\nskills = true\n'
            )
            claude_path.write_text(json.dumps({
                "model": "claude-sonnet-4-6", "effortLevel": "high",
                "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "256000", "KEEP": "value"},
            }))

            codex = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "", "context_limit": None},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "", "context_limit": None},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertTrue(codex["ok"])
        self.assertTrue(claude["ok"])
        self.assertIn('model = "gpt-5.6-terra"', codex_text)
        self.assertNotIn("model_reasoning_effort", codex_text)
        self.assertNotIn("model_context_window", codex_text)
        self.assertIn("[features]\nskills = true", codex_text)
        self.assertEqual(claude_json["model"], "claude-sonnet-4-6")
        self.assertNotIn("effortLevel", claude_json)
        self.assertEqual(claude_json["env"], {"KEEP": "value"})

    def test_agent_default_settings_reject_an_invalid_context_limit_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "keep"\n')
            claude_path.write_text(json.dumps({"model": "keep"}))

            result = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "high", "context_limit": 99999},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(result["ok"])
        self.assertIn("Context limit", result["error"])
        self.assertEqual(codex_text, 'model = "keep"\n')
        self.assertEqual(claude_json, {"model": "keep"})

    def test_agent_default_settings_reject_a_fractional_context_limit_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = Path(tmp) / "settings.json"
            claude_path.write_text(json.dumps({"model": "keep"}))

            result = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "high", "context_limit": 128000.5},
                claude_settings=str(claude_path),
            )

            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(result["ok"])
        self.assertIn("whole number", result["error"])
        self.assertEqual(claude_json, {"model": "keep"})

    def test_agent_defaults_accept_only_documented_persistent_reasoning_efforts(self):
        for effort in ("minimal", "low", "medium", "high", "xhigh"):
            with self.subTest(effort=effort):
                defaults = meter.normalize_agent_default_settings(
                    "codex", {"reasoning_effort": effort, "context_limit": 128000},
                )
                self.assertEqual(defaults["reasoning_effort"], effort)
        for client, efforts in (("codex", ("none", "max", "ultra")),
                                ("claude", ("minimal", "none", "max", "ultra"))):
            for effort in efforts:
                with self.subTest(client=client, effort=effort):
                    with self.assertRaisesRegex(ValueError, "not supported"):
                        meter.normalize_agent_default_settings(
                            client, {"reasoning_effort": effort, "context_limit": 128000},
                        )

    def test_codex_agent_defaults_preserve_triple_quoted_root_content_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            codex_path.write_text(
                'developer_instructions = """\n[keep this literal instruction]\n"""\n'
                'model = "gpt#old"\n[features]\nskills = true\n'
            )

            result = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "minimal", "context_limit": 128000},
                codex_config=str(codex_path),
            )
            text = codex_path.read_text()

        self.assertTrue(result["ok"])
        self.assertIn('developer_instructions = """\n[keep this literal instruction]\n"""', text)
        self.assertIn('model = "gpt#old"', text)
        self.assertLess(text.index("model_context_window = 128000"), text.index("[features]"))
        self.assertIn("[features]\nskills = true", text)

    def test_codex_agent_defaults_preserve_multiline_arrays_before_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            codex_path.write_text('matrix = [\n  [1, 2]\n]\n[features]\nskills = true\n')

            result = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "minimal", "context_limit": 128000},
                codex_config=str(codex_path),
            )
            text = codex_path.read_text()

        self.assertTrue(result["ok"])
        self.assertIn("matrix = [\n  [1, 2]\n]\n", text)
        self.assertLess(text.index("model_context_window = 128000"), text.index("[features]"))
        self.assertIn("[features]\nskills = true", text)

    def test_agent_default_status_redacts_unvalidated_or_sensitive_config_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "/very/secret/path"\nmodel_reasoning_effort = "secret"\n')
            claude_path.write_text(json.dumps({
                "model": "arn:aws:bedrock:private-account:application/profile",
                "effortLevel": "super-secret-token",
                "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "999999999999999999999"},
            }))

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        serialized = json.dumps(status)
        self.assertEqual(status["clients"][0]["defaults"], {
            "reasoning_effort": None, "context_limit": None,
        })
        self.assertEqual(status["clients"][1]["defaults"], {
            "reasoning_effort": None, "context_limit": None,
        })
        for secret in ("/very/secret/path", "private-account", "super-secret-token", "999999999999999999999"):
            self.assertNotIn(secret, serialized)

    def test_legacy_model_fields_are_ignored_when_saving_other_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = Path(tmp) / "settings.json"
            hidden_model = "arn:aws:bedrock:private-account:application/profile"
            claude_path.write_text(json.dumps({"model": hidden_model, "effortLevel": "high"}))

            result = meter.set_agent_default_settings(
                "claude", {"model": "", "preserve_model": True, "reasoning_effort": "xhigh",
                           "context_limit": 500000},
                claude_settings=str(claude_path),
            )
            saved = json.loads(claude_path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(saved["model"], hidden_model)
        self.assertEqual(saved["effortLevel"], "xhigh")
        self.assertNotIn("private-account", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
