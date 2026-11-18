import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.app_helpers import common
from token_meter.services import power


class PowerSettingsTests(unittest.TestCase):
    def setUp(self):
        # Writes update the live mode and watcher cadence; keep them out of other tests.
        patcher = mock.patch.dict(meter._POWER_STATE, {"mode": None}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(common.set_cadence_scale, 1.0)

    def test_default_is_automatic_and_invalid_stored_values_migrate_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(meter.power_settings(str(path)), {"low_power_mode": "auto"})
            path.write_text(json.dumps({"power": {"low_power_mode": "turbo"}, "other": 1}))
            self.assertEqual(meter.power_settings(str(path)), {"low_power_mode": "auto"})
            path.write_text(json.dumps({"power": "nonsense"}))
            self.assertEqual(meter.power_settings(str(path)), {"low_power_mode": "auto"})
            path.write_text("[1, 2]")
            self.assertEqual(meter.power_settings(str(path)), {"low_power_mode": "auto"})

    def test_writes_validate_preserve_other_settings_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"menubar": {"cache_timer": "off"}}))
            first = meter.set_power_settings({"low_power_mode": "on"}, str(path))
            self.assertTrue(first["ok"])
            self.assertTrue(first["changed"])
            self.assertEqual(first["power"]["mode"], "on")
            self.assertTrue(first["power"]["active"])
            stat = path.stat()
            second = meter.set_power_settings({"low_power_mode": "on"}, str(path))
            self.assertFalse(second["changed"])
            self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
            stored = json.loads(path.read_text())
            self.assertEqual(stored["menubar"], {"cache_timer": "off"})
            self.assertEqual(stored["power"], {"low_power_mode": "on"})
            for bad in ({"low_power_mode": "sometimes"}, {"low_power_mode": 1}, ["on"], "on"):
                with self.subTest(bad=bad):
                    self.assertFalse(meter.set_power_settings(bad, str(path))["ok"])
            self.assertEqual(meter.power_settings(str(path)), {"low_power_mode": "on"})

    def test_write_failures_hide_os_error_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter, "write_settings_json", side_effect=OSError("/secret/path")):
                result = meter.set_power_settings({"low_power_mode": "off"}, path)
            self.assertFalse(result["ok"])
            self.assertNotIn("/secret", result["error"])


class LowPowerStatusTests(unittest.TestCase):
    def test_automatic_follows_battery_and_system_low_power(self):
        self.assertEqual(
            power.low_power_status("auto", True, "battery"),
            {"mode": "auto", "active": True, "reason": "battery", "power_source": "battery"},
        )
        system = power.low_power_status("auto", True, "ac")
        self.assertTrue(system["active"])
        self.assertEqual(system["reason"], "system")
        self.assertFalse(power.low_power_status("auto", False, "ac")["active"])

    def test_unknown_power_never_turns_automatic_mode_on(self):
        status = power.low_power_status("auto", None, None)
        self.assertFalse(status["active"])
        self.assertIsNone(status["reason"])
        self.assertIsNone(status["power_source"])

    def test_explicit_modes_override_the_power_source(self):
        self.assertEqual(power.low_power_status("on", False, "ac")["reason"], "setting")
        self.assertTrue(power.low_power_status("on", None, None)["active"])
        self.assertFalse(power.low_power_status("off", True, "battery")["active"])

    def test_unexpected_probe_values_are_unknown(self):
        status = power.low_power_status("auto", "yes", "mains")
        self.assertFalse(status["active"])
        self.assertIsNone(status["power_source"])

    def test_constrained_without_a_named_source_reads_as_battery(self):
        # Linux laptops on USB-C power report no Mains supply; there is no OS low-power probe there.
        self.assertEqual(power.low_power_status("auto", True, None)["reason"], "battery")

    def test_validation_message_uses_the_settings_labels(self):
        with self.assertRaisesRegex(ValueError, "On battery, Always, or Off"):
            power.normalize_power_settings({"low_power_mode": "turbo"})


class PowerMonitorTests(unittest.TestCase):
    def test_probes_are_cached_for_the_ttl(self):
        clock = [100.0]
        calls = []
        monitor = power.PowerMonitor(
            lambda: calls.append("c") or True, lambda: "battery",
            clock=lambda: clock[0], ttl=30,
        )
        self.assertEqual(monitor.reading(), (None, None))
        monitor.refresh()
        self.assertEqual(monitor.reading(), (True, "battery"))
        clock[0] += 29
        monitor.refresh()
        self.assertEqual(len(calls), 1)
        clock[0] += 2
        monitor.refresh()
        self.assertEqual(len(calls), 2)

    def test_probe_failures_read_as_unknown(self):
        for error in (OSError("pmset missing"), RuntimeError("unexpected")):
            with self.subTest(error=type(error).__name__):
                def broken(error=error):
                    raise error
                monitor = power.PowerMonitor(broken, broken, clock=lambda: 1.0)
                monitor.refresh()
                self.assertEqual(monitor.reading(), (None, None))

    def test_background_refresh_never_blocks_and_runs_one_probe_at_a_time(self):
        import threading
        release, started, calls = threading.Event(), threading.Event(), []

        def slow():
            calls.append(1)
            started.set()
            release.wait(5)
            return True
        monitor = power.PowerMonitor(slow, lambda: "battery", clock=lambda: 1.0, ttl=0)
        self.assertFalse(monitor.refresh(background=True))
        self.assertTrue(started.wait(5))
        self.assertEqual(monitor.reading(), (None, None))  # caller keeps the cached value
        monitor.refresh(background=True)
        monitor.refresh(background=True)
        release.set()
        for _ in range(500):
            if monitor.reading() == (True, "battery"):
                break
            threading.Event().wait(0.01)
        self.assertEqual(monitor.reading(), (True, "battery"))
        self.assertEqual(len(calls), 1)


class CadenceScaleTests(unittest.TestCase):
    def tearDown(self):
        common.set_cadence_scale(1.0)

    def test_low_power_stretches_watcher_cadences(self):
        self.assertEqual(power.cadence_scale({"active": False}), 1.0)
        self.assertEqual(power.cadence_scale({"active": True}), power.LOW_POWER_CADENCE_SCALE)

    def test_due_checks_follow_the_cadence_scale(self):
        rebuild = common._CURRENT_SESSION_REBUILD_S
        self.assertTrue(common.current_session_refresh_due("b", "a", rebuild, 0.0))
        self.assertTrue(common.cross_session_refresh_due(True, False, common._XSESS_LIVE_REFRESH_S, 0.0))
        self.assertTrue(common.source_membership_probe_due(common._SOURCE_MEMBERSHIP_PROBE_S, 0.0))
        common.set_cadence_scale(4.0)
        self.assertEqual(common.cadence_scale(), 4.0)
        self.assertFalse(common.current_session_refresh_due("b", "a", rebuild, 0.0))
        self.assertTrue(common.current_session_refresh_due("b", "a", rebuild * 4, 0.0))
        self.assertFalse(common.cross_session_refresh_due(True, False, common._XSESS_LIVE_REFRESH_S, 0.0))
        self.assertFalse(common.source_membership_probe_due(common._SOURCE_MEMBERSHIP_PROBE_S, 0.0))
        self.assertFalse(common.source_membership_fallback_due(common._SOURCE_MEMBERSHIP_FALLBACK_S, 0.0))
        self.assertFalse(common.source_discovery_refresh_due(True, common._SOURCE_DISCOVERY_REFRESH_S, 0.0))
        # Membership changes and first-time discovery stay immediate.
        self.assertTrue(common.cross_session_refresh_due(True, True, 0.1, 0.0))
        self.assertTrue(common.current_session_refresh_due("b", None, 0.1, 0.0))
        self.assertTrue(common.source_discovery_refresh_due(False, 0.1, 0.0))

    def test_cross_session_snapshot_freshness_follows_the_cadence_scale(self):
        age = common._XSESS_TTL * 2
        with mock.patch.dict(meter._xsess, {"data": {"x": 1}, "at": meter.time.time() - age}), \
                mock.patch.object(meter, "pricing_recalculation_running", return_value=False):
            self.assertFalse(meter._cross_session_snapshot_fresh())
            common.set_cadence_scale(power.LOW_POWER_CADENCE_SCALE)
            self.assertTrue(meter._cross_session_snapshot_fresh())
            self.assertEqual(meter.cross_session(sources=[]), {"x": 1})

    def test_invalid_scales_fall_back_to_normal(self):
        for bad in (0, -1, float("nan"), "fast", None):
            with self.subTest(bad=bad):
                common.set_cadence_scale(bad)
                self.assertEqual(common.cadence_scale(), 1.0)


class PowerPayloadTests(unittest.TestCase):
    def test_refresh_sets_watcher_cadence_and_payloads_carry_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            monitor = power.PowerMonitor(lambda: True, lambda: "battery", clock=lambda: 1.0)
            monitor.refresh()
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", path), \
                    mock.patch.object(meter, "_POWER_MONITOR", monitor), \
                    mock.patch.dict(meter._POWER_STATE, {"mode": None}, clear=True):
                try:
                    status = meter.refresh_power_status()
                    self.assertTrue(status["active"])
                    self.assertEqual(status["reason"], "battery")
                    self.assertEqual(common.cadence_scale(), power.LOW_POWER_CADENCE_SCALE)
                    self.assertEqual(meter.power_status(), status)
                    meter.set_power_settings({"low_power_mode": "off"}, path)
                    self.assertFalse(meter.power_status()["active"])
                    self.assertEqual(common.cadence_scale(), 1.0)
                finally:
                    common.set_cadence_scale(1.0)

    def test_writing_another_settings_file_leaves_live_state_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, other = str(Path(tmp) / "live.json"), str(Path(tmp) / "other.json")
            monitor = power.PowerMonitor(lambda: False, lambda: "ac", clock=lambda: 1.0)
            monitor.refresh()
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", live), \
                    mock.patch.object(meter, "_POWER_MONITOR", monitor), \
                    mock.patch.dict(meter._POWER_STATE, {"mode": None}, clear=True):
                try:
                    result = meter.set_power_settings({"low_power_mode": "on"}, other)
                    self.assertTrue(result["power"]["active"])
                    self.assertFalse(meter.power_status()["active"])
                    self.assertEqual(common.cadence_scale(), 1.0)
                finally:
                    common.set_cadence_scale(1.0)

    def test_post_route_is_action_protected(self):
        app_source = Path(power.__file__).parents[1].joinpath("app.py").read_text()
        self.assertIn('"/settings/power"', app_source)
        self.assertIn('payload["power"] = power_status()', app_source)
        self.assertIn('"power": power_status(),', app_source)


if __name__ == "__main__":
    unittest.main()
