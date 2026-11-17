"""Work insights pacing: the automatic duty cycle, thermal back-off, and idle detection."""

import tempfile
import unittest
from unittest import mock

from token_meter.services import host_signals as H
from token_meter.services import work_insights as W
from tests.test_work_insights import FakeClient, Clock, make_service, model_response, turns, drain

_probe_patches = (mock.patch.object(W, "_default_memory_probe", lambda: None),
                  mock.patch.object(W, "_default_thermal_probe", lambda: 0),
                  mock.patch.object(W, "_default_idle_probe", lambda: None))


def setUpModule():
    # Tests must not depend on the test machine's free memory, temperature, or whether someone is typing.
    for patch in _probe_patches:
        patch.start()


def tearDownModule():
    for patch in _probe_patches:
        patch.stop()


class DutyCycleAndThermalTests(unittest.TestCase):
    """Automatic pace keeps the model to a share of the time; a warm Mac slows or stops labeling."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_pacer_rests_in_proportion_to_the_last_warm_call(self):
        clock = Clock()
        pacer = W.Pacer(clock)
        pacer.consume()
        clock.now += 1.0
        self.assertAlmostEqual(pacer.wait_time(60, 0.2), 0.0)  # no call timed yet: only the rate applies
        pacer.record_busy(2.0)
        self.assertAlmostEqual(pacer.wait_time(60, 0.2), 8.0)  # 2 s working, 8 s resting
        self.assertAlmostEqual(pacer.wait_time(60, None), 0.0)  # without a share, only the rate applies
        clock.now += 5.0
        self.assertAlmostEqual(pacer.wait_time(60, 0.2), 3.0)
        pacer.record_busy(60.0)  # a very slow call rests at most the cap, inside Ollama's keep-alive
        self.assertAlmostEqual(pacer.wait_time(60, 0.15), W.DUTY_WAIT_CAP_S)
        self.assertLess(W.DUTY_WAIT_CAP_S, W.KEEP_ALIVE_S)

    def test_share_is_fuller_only_when_idle_plugged_in_cool_and_quiet(self):
        service, values, _clock = make_service(self.tmp.name, {"rate_per_minute": "auto"})
        idle = {"last_thermal": 0, "on_battery": False, "last_idle": W.USER_IDLE_S, "last_load": 0.1}
        for name, value in dict(idle).items():
            service.__dict__[name] = value
        self.assertEqual(service._duty(values), W.AUTO_DUTY_IDLE)
        for name, busy in (("last_idle", W.USER_IDLE_S - 1), ("last_idle", None), ("last_thermal", None),
                           ("on_battery", True), ("last_load", W.AUTO_QUIET_LIMIT + 0.01), ("last_load", None)):
            for key, value in idle.items():
                service.__dict__[key] = value
            service.__dict__[name] = busy
            self.assertEqual(service._duty(values), W.AUTO_DUTY_ACTIVE, (name, busy))
        for key, value in idle.items():
            service.__dict__[key] = value
        service.last_thermal = W.THERMAL_FAIR
        self.assertEqual(service._duty(values), W.WARM_DUTY)
        # A chosen pace is only a calls-a-minute cap, but a warm Mac still keeps the model to a small share.
        values["rate_per_minute"] = 60
        self.assertEqual(service._duty(values), W.WARM_DUTY)
        service.last_thermal = 0
        self.assertIsNone(service._duty(values))
        self.assertLess(W.WARM_DUTY, W.AUTO_DUTY_ACTIVE)
        self.assertLess(W.AUTO_DUTY_ACTIVE, W.AUTO_DUTY_IDLE)

    def test_busy_checks_read_thermal_state_and_idle_time(self):
        service, values, _clock = make_service(self.tmp.name, {"rate_per_minute": "auto"},
                                               thermal_probe=lambda: 1, idle_probe=lambda: 900.0)
        self.assertEqual(service._throttle_reason(values), "")
        self.assertEqual((service.last_thermal, service.last_idle), (1, 900.0))

    def test_automatic_pace_rests_after_each_warm_call(self):
        service, _values, clock = make_service(self.tmp.name, {"rate_per_minute": "auto"})
        calls = []

        def respond(prompt):
            start = clock.now
            clock.now += 3.0  # every call takes 3 s
            calls.append((start, clock.now))
            return model_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("please fix the chart", "now make the bars wider please"))
        drain(service)
        self.assertGreaterEqual(len(calls), 6)
        # The first call is cold (loading), so only rests after warm calls are bound by the share.
        rest = 3.0 * (1 / W.AUTO_DUTY_ACTIVE - 1)
        for (_s0, end), (start, _e1) in zip(calls[1:], calls[2:]):
            self.assertGreaterEqual(start - end, rest - 0.01)
        working = sum(e - s for s, e in calls[1:])
        self.assertLessEqual(working / (calls[-1][1] - calls[1][0]), W.AUTO_DUTY_ACTIVE + 0.05)

    def test_a_chosen_pace_does_not_rest_on_a_cool_mac(self):
        service, _values, clock = make_service(self.tmp.name, {"rate_per_minute": 60})
        calls = []

        def respond(prompt):
            calls.append(clock.now)
            clock.now += 3.0
            return model_response("A")

        FakeClient.responder = respond
        service.observe("s1", turns("please fix the chart"))
        self.assertEqual(service.step(), 0.0)
        self.assertEqual([round(b - a, 2) for a, b in zip(calls, calls[1:])], [3.0] * (len(calls) - 1))

    def test_a_hot_mac_waits_and_unloads_at_any_pace(self):
        for pace in ("auto", 60):
            thermal = {"value": W.THERMAL_SERIOUS}
            service, _values, _clock = make_service(tempfile.mkdtemp(dir=self.tmp.name), {"rate_per_minute": pace},
                                                    thermal_probe=lambda: thermal["value"])
            service.loaded = True
            service.observe("s1", turns("please fix the chart"))
            self.assertEqual(service.step(), W.THROTTLE_WAIT_S)
            self.assertEqual((service.state, service.reason), (W.STATE_THROTTLED, "too_warm"))
            self.assertEqual(FakeClient.prompts, [])
            self.assertEqual(FakeClient.unloads, 1)
            thermal["value"] = W.THERMAL_FAIR  # cooler: labeling resumes, at the warm share
            service.retry_at = 0
            self.assertEqual(service.step(), 0.0)
            self.assertEqual(service.state, W.STATE_RUNNING)
            self.assertTrue(FakeClient.prompts)

    def test_heat_that_rises_during_an_item_stops_before_the_next_call(self):
        readings = iter([0, 0, W.THERMAL_SERIOUS])  # item check, first call, second call
        service, _values, _clock = make_service(self.tmp.name, {"rate_per_minute": "auto"},
                                                thermal_probe=lambda: next(readings, W.THERMAL_SERIOUS))
        service.observe("s1", turns("please fix the chart"))
        self.assertEqual(service.step(), 0.0)
        self.assertEqual(len(FakeClient.prompts), 1)
        self.assertEqual(service.step(), W.THROTTLE_WAIT_S)
        self.assertEqual(service.reason, "too_warm")
        self.assertEqual(len(FakeClient.prompts), 1)
        self.assertEqual(len(service.queue), 1)  # the item waits; nothing was stored half-asked

    def test_default_probes_answer_or_say_unknown(self):
        idle = H.seconds_since_input()
        self.assertTrue(idle is None or idle >= 0)
        self.assertIn(H.thermal_state(), (None, 0, 1, 2, 3))
        with mock.patch.object(H.sys, "platform", "linux"):
            self.assertIsNone(H.thermal_state())
            self.assertIsNone(H.seconds_since_input())
        with mock.patch.object(H.sys, "platform", "darwin"), \
                mock.patch.object(H.subprocess, "run", side_effect=OSError("no ioreg")):
            self.assertIsNone(H.seconds_since_input())
        for stdout, expected in (('  |   "HIDIdleTime" = 2500000000\n', 2.5), ("", None)):
            with mock.patch.object(H.sys, "platform", "darwin"), \
                    mock.patch.object(H.subprocess, "run", return_value=mock.Mock(stdout=stdout)):
                self.assertEqual(H.seconds_since_input(), expected)
        # A runtime that cannot answer makes the thermal reading unknown rather than raising.
        with mock.patch.object(H, "_thermal", (mock.Mock(side_effect=RuntimeError("objc")), 1, 2)):
            self.assertIsNone(H.thermal_state())

    def test_commit_labels_rest_after_warm_calls_and_stop_when_too_warm(self):
        service, _values, clock = make_service(self.tmp.name, {"rate_per_minute": "auto"})
        service.commit_label_sink = lambda *args: None
        calls = []

        def respond(prompt):
            start = clock.now
            clock.now += 2.0
            calls.append((start, clock.now))
            return model_response("A")

        FakeClient.responder = respond
        service.loaded, service.last_call_at = True, clock.now  # warm, so the first call is timed too
        service.observe_commit("repo", "one", "Fix the flickering menu")
        self.assertEqual(service.step(), 0.0)
        self.assertEqual(len(calls), 2)  # both option orders
        rest = 2.0 * (1 / W.AUTO_DUTY_ACTIVE - 1)
        self.assertGreaterEqual(calls[1][0] - calls[0][1], rest - 0.01)
        # Heat during the commit's calls stops before the next one and keeps the commit queued.
        before = len(calls)
        service.thermal_probe = lambda: W.THERMAL_SERIOUS if len(calls) > before else 0
        service.observe_commit("repo", "two", "Add a chart")
        self.assertEqual(service.step(), 0.0)
        self.assertEqual(len(calls), before + 1)
        self.assertIn(("repo", "two"), service.commit_queue)

    def test_estimate_allows_for_the_rest_until_the_rate_is_measured(self):
        service, values, _clock = make_service(self.tmp.name, {"rate_per_minute": "auto"})
        service.observe("s1", turns("please fix the chart", "now make the bars wider please"))
        service.last_load, service.last_thermal, service.on_battery = 0.1, 0, False
        service.pacer.busy = 0.0
        quick = service.status()["eta_s"]
        self.assertGreater(quick, 0)
        service.pacer.busy = 4.0  # 4 s warm calls at 15%: one call every ~27 s, an item every ~2.2 min
        slow = service.status()["eta_s"]
        cycle = 4.0 / W.AUTO_DUTY_ACTIVE
        self.assertAlmostEqual(slow, int(service.status()["pending"] / (60.0 / cycle / 5) * 60), delta=1)
        self.assertGreater(slow, quick * 10)
        values["rate_per_minute"] = 20  # a chosen pace on a cool Mac has no rest
        self.assertEqual(service.status()["eta_s"], int(service.status()["pending"] / 4 * 60))

    def test_a_mac_that_warms_during_a_rest_rests_longer_from_the_next_call(self):
        service, _values, clock = make_service(self.tmp.name, {"rate_per_minute": "auto"})
        readings = {"value": 0}
        service.thermal_probe = lambda: readings["value"]
        calls = []

        def respond(prompt):
            calls.append(clock.now)
            clock.now += 2.0
            readings["value"] = W.THERMAL_FAIR  # warms while the call runs
            return model_response("A")

        FakeClient.responder = respond
        service.loaded, service.last_call_at = True, clock.now
        service.observe("s1", turns("please fix the chart"))
        self.assertEqual(service.step(), 0.0)
        self.assertGreaterEqual(calls[1] - calls[0] - 2.0, 2.0 * (1 / W.WARM_DUTY - 1) - 0.01)

    def test_a_chosen_pace_does_not_read_idle_time(self):
        reads = []
        service, values, _clock = make_service(self.tmp.name, {"rate_per_minute": 20},
                                               idle_probe=lambda: reads.append(1) or 900.0)
        service._throttle_reason(values)
        self.assertEqual((reads, service.last_idle), ([], None))
        values["rate_per_minute"] = "auto"
        service._throttle_reason(values)
        self.assertEqual((reads, service.last_idle), ([1], 900.0))


if __name__ == "__main__":
    unittest.main()
