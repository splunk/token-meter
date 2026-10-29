"""Server-owned notification rules, claims, settings, and HTTP contract."""

import copy
import inspect
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import meter
from token_meter.domain import notifications as N

NOW = 1_761_700_000.0
PREFS = {
    "quota_alerts": True, "quota_threshold": 80,
    "budget_alerts": True, "budget_thresholds": [80, 90, 100],
}


def quota(used, reset_at=NOW + 7500, *, status="ok", stale=False, label="Codex", window="Weekly"):
    return [{
        "id": "codex", "label": label, "status": status, "stale": stale,
        "windows": [{"id": "primary", "label": window, "used_percent": used, "reset_at": reset_at}],
    }]


def budget(percent, *, month="2026-10", spend=None, total=450.0, lower_bound=False, runtimes=()):
    return {
        "configured": True, "month": month, "budget": total,
        "spend": total * percent / 100 if spend is None else spend,
        "percent": percent / 100, "lower_bound": lower_bound,
        "runtimes": [
            {"provider": provider, "label": label, "allocation": 100.0,
             "spend": pct, "percent": pct / 100}
            for provider, label, pct in runtimes
        ],
    }


def observe(quotas=(), budget_status=None, hints=(), hints_enabled=False):
    return {"quotas": list(quotas), "budget": budget_status,
            "hints": list(hints), "hints_enabled": hints_enabled}


def run(state, observation, prefs=PREFS, now=NOW, established=True):
    state, events, _ = N.evaluate(state, observation, prefs, now, quota_established=established)
    return state, events


class QuotaRuleTests(unittest.TestCase):
    def seeded(self, used=70.0, **kwargs):
        state, events = run(N.empty_state(), observe(quota(used, **kwargs)))
        self.assertEqual(events, [])
        return state

    def test_first_sighting_records_without_alerting(self):
        state = self.seeded(96.0)
        self.assertEqual(state["quota"]["codex:primary"]["last_used_percent"], 96.0)
        self.assertEqual(state["quota"]["codex:primary"]["fired"], [])

    def test_highest_crossing_alerts_once_and_records_all(self):
        state = self.seeded(70.0)
        state, events = run(state, observe(quota(96.0)))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["id"], f"quota:codex:primary:{int(NOW + 7500)}:95")
        self.assertEqual(event["kind"], "quota")
        self.assertEqual(event["severity"], "critical")
        self.assertEqual(event["title"], "Codex quota critical")
        self.assertEqual(event["body"], "Weekly reached 96% used; resets in 2h 5m.")
        self.assertEqual(event["link"], "#sessions")
        self.assertFalse(event["claimed"])
        self.assertEqual(state["quota"]["codex:primary"]["fired"], [80, 95])
        _, again = run(state, observe(quota(97.0)))
        self.assertEqual(again, [])

    def test_severity_levels(self):
        for used, severity in ((85.0, "warning"), (100.0, "exhausted")):
            state = self.seeded(10.0)
            _, events = run(state, observe(quota(used)))
            self.assertEqual(events[0]["severity"], severity)

    def test_user_threshold_changes_the_warning_level(self):
        state = self.seeded(10.0)
        prefs = dict(PREFS, quota_threshold=90)
        _, events = run(state, observe(quota(85.0)), prefs=prefs)
        self.assertEqual(events, [])

    def test_reset_clears_and_announces_when_back_below_threshold(self):
        state = self.seeded(70.0)
        state, _ = run(state, observe(quota(90.0)))
        state, events = run(state, observe(quota(5.0, reset_at=NOW + 90_000)))
        self.assertEqual([e["kind"] for e in events], ["quota_reset"])
        self.assertEqual(events[0]["id"], f"quota-reset:codex:primary:{int(NOW + 90_000)}")
        self.assertEqual(events[0]["title"], "Codex quota reset")
        self.assertEqual(events[0]["body"], "Weekly is back to 5% used.")
        self.assertEqual(state["quota"]["codex:primary"]["fired"], [])

    def test_small_reset_drift_is_not_a_reset(self):
        state = self.seeded(70.0)
        state, _ = run(state, observe(quota(90.0)))
        _, events = run(state, observe(quota(5.0, reset_at=NOW + 7500 + 30)))
        self.assertEqual(events, [])

    def test_establishment_gate_suppresses_alerts_but_advances_state(self):
        state = self.seeded(70.0)
        state, events = run(state, observe(quota(96.0)), established=False)
        self.assertEqual(events, [])
        self.assertEqual(state["quota"]["codex:primary"]["last_used_percent"], 96.0)

    def test_alerts_off_advances_state_without_alerting(self):
        state = self.seeded(70.0)
        state, events = run(state, observe(quota(96.0)), prefs=dict(PREFS, quota_alerts=False))
        self.assertEqual(events, [])
        self.assertEqual(state["quota"]["codex:primary"]["fired"], [])

    def test_stale_or_failed_providers_are_ignored(self):
        state = self.seeded(70.0)
        for kwargs in ({"stale": True}, {"status": "error"}):
            _, events = run(state, observe(quota(99.0, **kwargs)))
            self.assertEqual(events, [])

    def test_observed_fresh_flag(self):
        _, _, fresh = N.evaluate(N.empty_state(), observe(quota(1.0)), PREFS, NOW, quota_established=False)
        self.assertTrue(fresh)
        _, _, fresh = N.evaluate(N.empty_state(), observe(quota(1.0, stale=True)), PREFS, NOW,
                                 quota_established=False)
        self.assertFalse(fresh)

    def test_recrossing_without_a_reset_time_is_not_dropped(self):
        state = self.seeded(10.0, reset_at=None)
        steps = [(85.0, None), (85.0, NOW + 5000), (10.0, NOW + 9000), (10.0, None), (85.0, None)]
        alerts = []
        for index, (used, reset_at) in enumerate(steps):
            state, events = run(state, observe(quota(used, reset_at=reset_at)), now=NOW + index * 10)
            alerts.extend(e["kind"] for e in events)
        self.assertEqual(alerts, ["quota", "quota_reset", "quota"])

    def test_reset_labels(self):
        state = self.seeded(10.0, reset_at=None)
        _, events = run(state, observe(quota(85.0, reset_at=None)))
        self.assertEqual(events[0]["body"], "Weekly reached 85% used; reset time unavailable.")
        self.assertEqual(events[0]["id"], f"quota:codex:primary:none-{int(NOW)}:80")
        state = self.seeded(10.0, reset_at=NOW - 5)
        _, events = run(state, observe(quota(85.0, reset_at=NOW - 5)))
        self.assertTrue(events[0]["body"].endswith("reset pending."))


class BudgetRuleTests(unittest.TestCase):
    def test_first_sighting_marks_met_thresholds_without_alerting(self):
        state, events = run(N.empty_state(), observe(budget_status=budget(92.0)))
        self.assertEqual(events, [])
        self.assertEqual(state["budget"]["overall"], {"month": "2026-10", "last_percent": 92.0, "fired": [80, 90]})

    def test_overall_crossing(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(70.0)))
        state, events = run(state, observe(budget_status=budget(91.58)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], "budget:overall:2026-10:90")
        self.assertEqual(events[0]["title"], "Overall monthly budget reached 90%")
        self.assertEqual(events[0]["body"], "$412.11 of $450.00 recorded for 2026-10.")
        self.assertEqual(events[0]["link"], "#spend-budgets")
        self.assertEqual(state["budget"]["overall"]["fired"], [80, 90])

    def test_exceeded_fires_once_per_month_and_suppresses_overall_threshold(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(95.0)))
        state, events = run(state, observe(budget_status=budget(104.0, lower_bound=True)))
        self.assertEqual([e["id"] for e in events], ["budget-exceeded:2026-10"])
        self.assertEqual(events[0]["kind"], "budget_exceeded")
        self.assertEqual(events[0]["title"], "Overall monthly budget exceeded")
        self.assertEqual(events[0]["body"], "At least $468.00 of $450.00 recorded for 2026-10 (104% used).")
        self.assertIn(100, state["budget"]["overall"]["fired"])
        _, again = run(state, observe(budget_status=budget(110.0)))
        self.assertEqual(again, [])

    def test_runtime_scope_body(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(10.0, runtimes=[("codex", "Codex", 50.0)])))
        _, events = run(state, observe(budget_status=budget(10.0, runtimes=[("codex", "Codex", 85.4)])))
        self.assertEqual(events[0]["id"], "budget:codex:2026-10:80")
        self.assertEqual(events[0]["title"], "Codex monthly budget reached 80%")
        self.assertEqual(events[0]["body"], "Codex reached 85% of its allocation.")

    def test_empty_state_does_not_replay_an_already_exceeded_month(self):
        state, events = run(N.empty_state(), observe(budget_status=budget(120.0)))
        self.assertEqual(events, [])
        self.assertEqual(state["budget_exceeded_months"], ["2026-10"])
        _, again = run(state, observe(budget_status=budget(130.0)))
        self.assertEqual(again, [])

    def test_newly_configured_budget_already_exceeded_still_alerts(self):
        state, _ = run(N.empty_state(), observe(quota(10.0)))
        _, events = run(state, observe(quota(10.0), budget_status=budget(150.0)))
        self.assertEqual([e["id"] for e in events], ["budget-exceeded:2026-10"])

    def test_exceeded_month_seen_while_alerts_are_off_alerts_once_enabled(self):
        off = dict(PREFS, budget_alerts=False)
        state, _ = run(N.empty_state(), observe(budget_status=budget(120.0)), prefs=off)
        _, events = run(state, observe(budget_status=budget(125.0)))
        self.assertEqual([e["id"] for e in events], ["budget-exceeded:2026-10"])

    def test_month_rollover_still_alerts_when_already_exceeded(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(50.0)))
        _, events = run(state, observe(budget_status=budget(110.0, month="2026-11")))
        self.assertEqual([e["id"] for e in events], ["budget-exceeded:2026-11"])

    def test_new_month_reseeds(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(95.0)))
        state, events = run(state, observe(budget_status=budget(85.0, month="2026-11")))
        self.assertEqual(events, [])
        self.assertEqual(state["budget"]["overall"]["month"], "2026-11")

    def test_budget_alerts_off(self):
        prefs = dict(PREFS, budget_alerts=False)
        state, _ = run(N.empty_state(), observe(budget_status=budget(70.0)), prefs=prefs)
        state, events = run(state, observe(budget_status=budget(120.0)), prefs=prefs)
        self.assertEqual(events, [])
        self.assertEqual(state["budget"]["overall"]["fired"], [])

    def test_unconfigured_budget_is_ignored(self):
        status = dict(budget(150.0), configured=False)
        state, events = run(N.empty_state(), observe(budget_status=status))
        self.assertEqual(events, [])
        self.assertEqual(state["budget"], {})

    def test_small_amounts_use_three_decimals(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(10.0, total=0.5)))
        _, events = run(state, observe(budget_status=budget(85.0, total=0.5)))
        self.assertEqual(events[0]["body"], "$0.425 of $0.500 recorded for 2026-10.")


class HintRuleTests(unittest.TestCase):
    HINT = {"id": "s1:effort", "title": "Lower effort", "body": "Try medium effort."}

    def test_first_evaluation_seeds_then_new_hints_alert_once(self):
        state, events = run(N.empty_state(), observe(hints=[self.HINT], hints_enabled=True))
        self.assertEqual(events, [])
        new = dict(self.HINT, id="s2:model")
        state, events = run(state, observe(hints=[self.HINT, new], hints_enabled=True))
        self.assertEqual([e["id"] for e in events], ["hint:s2:model"])
        self.assertEqual(events[0]["title"], "Token Meter: Lower effort")
        self.assertEqual(events[0]["kind"], "live_hint")
        _, again = run(state, observe(hints=[self.HINT, new], hints_enabled=True))
        self.assertEqual(again, [])

    def test_reenabling_reseeds(self):
        state, _ = run(N.empty_state(), observe(hints_enabled=True))
        state, _ = run(state, observe(hints_enabled=False))
        _, events = run(state, observe(hints=[self.HINT], hints_enabled=True))
        self.assertEqual(events, [])

    def test_seen_ids_are_bounded(self):
        state, _ = run(N.empty_state(), observe(hints_enabled=True))
        for index in range(N.MAX_HINT_IDS + 20):
            hint = {"id": f"h{index}", "title": "t", "body": "b"}
            state, _ = run(state, observe(hints=[hint], hints_enabled=True), now=NOW + index * 100)
        self.assertEqual(len(state["hints"]["seen"]), N.MAX_HINT_IDS)
        self.assertEqual(state["hints"]["seen"][-1], f"h{N.MAX_HINT_IDS + 19}")

    def test_only_twelve_hints_are_considered(self):
        state, _ = run(N.empty_state(), observe(hints_enabled=True))
        hints = [{"id": f"h{i}", "title": "t", "body": "b"} for i in range(20)]
        _, events = run(state, observe(hints=hints, hints_enabled=True))
        self.assertEqual(len(events), 12)


class EventLogTests(unittest.TestCase):
    def test_events_are_bounded_newest_first_and_expire(self):
        state = N.empty_state()
        state["events"] = [
            {"id": f"e{i}", "created_at": NOW - 10 * i, "claimed": False, "claimed_by": None}
            for i in range(60)
        ] + [{"id": "old", "created_at": NOW - N.EVENT_TTL_S - 1, "claimed": False, "claimed_by": None}]
        state, _ = run(state, observe())
        self.assertEqual(len(state["events"]), N.MAX_EVENTS)
        self.assertEqual(state["events"][0]["id"], "e0")
        self.assertNotIn("old", [e["id"] for e in state["events"]])

    def test_existing_event_ids_are_not_duplicated(self):
        state, _ = run(N.empty_state(), observe(budget_status=budget(70.0)))
        state, first = run(state, observe(budget_status=budget(85.0)))
        state["budget"]["overall"]["fired"] = []
        state, second = run(state, observe(budget_status=budget(70.0)))
        state, third = run(state, observe(budget_status=budget(85.0)))
        self.assertEqual(len(first), 1)
        self.assertEqual(third, [])
        self.assertEqual([e["id"] for e in state["events"]].count(first[0]["id"]), 1)

    def test_normalize_state_recovers_from_garbage(self):
        for raw in (None, [], "x", {"version": 99}, {"quota": "bad", "events": {"a": 1}}):
            state = N.normalize_state(raw)
            self.assertEqual(state, N.empty_state())

    def test_evaluate_does_not_mutate_input(self):
        state, _ = run(N.empty_state(), observe(quota(70.0)))
        snapshot = copy.deepcopy(state)
        run(state, observe(quota(96.0)))
        self.assertEqual(state, snapshot)


class ClaimabilityTests(unittest.TestCase):
    def test_only_fresh_unclaimed_events_are_claimable(self):
        events = [
            {"id": "fresh", "created_at": NOW - 10, "claimed": False},
            {"id": "old", "created_at": NOW - N.CLAIM_WINDOW_S - 1, "claimed": False},
            {"id": "done", "created_at": NOW - 10, "claimed": True},
        ]
        self.assertEqual([N.claimable(e, NOW) for e in events], [True, False, False])


class ClaimTests(unittest.TestCase):
    def state_with_event(self, created_at=NOW):
        state = N.empty_state()
        state["events"] = [{"id": "budget:overall:2026-10:90", "created_at": created_at,
                            "claimed": False, "claimed_by": None}]
        return state

    def test_first_claim_wins(self):
        state, result = N.claim(self.state_with_event(), "budget:overall:2026-10:90", "token-meter-menubar", NOW)
        self.assertEqual(result, {"ok": True, "claimed": True})
        self.assertTrue(state["events"][0]["claimed"])
        self.assertEqual(state["events"][0]["claimed_by"], "token-meter-menubar")
        state, result = N.claim(state, "budget:overall:2026-10:90", "ghost", NOW)
        self.assertEqual(result, {"ok": True, "claimed": False, "reason": "claimed"})
        self.assertEqual(state["events"][0]["claimed_by"], "token-meter-menubar")

    def test_unknown_and_expired(self):
        _, result = N.claim(self.state_with_event(), "nope", "ghost", NOW)
        self.assertEqual(result["reason"], "unknown")
        state = self.state_with_event(created_at=NOW - N.CLAIM_WINDOW_S - 1)
        state, result = N.claim(state, "budget:overall:2026-10:90", "ghost", NOW)
        self.assertEqual(result["reason"], "expired")
        self.assertFalse(state["events"][0]["claimed"])

    def test_consumer_names(self):
        for good in ("token-meter-menubar", "ghost", "a", "g.tray_2"):
            self.assertTrue(N.valid_consumer(good), good)
        for bad in ("", "Ghost", "-x", "a" * 49, "x y", None, 3):
            self.assertFalse(N.valid_consumer(bad), bad)


class NotificationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "settings.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_report_their_source(self):
        self.assertEqual(meter.notification_settings(self.path),
                         {"source": "default", "quota_alerts": True, "quota_threshold": 80})

    def test_validated_idempotent_write(self):
        first = meter.set_notification_settings({"quota_alerts": False, "quota_threshold": 90}, self.path)
        self.assertEqual(first, {"ok": True, "changed": True,
                                 "notifications": {"quota_alerts": False, "quota_threshold": 90}})
        second = meter.set_notification_settings({"quota_alerts": False, "quota_threshold": 90}, self.path)
        self.assertFalse(second["changed"])
        self.assertEqual(meter.notification_settings(self.path)["source"], "saved")
        for bad in ({"quota_alerts": "yes", "quota_threshold": 80}, {"quota_alerts": True, "quota_threshold": 85},
                    {"quota_alerts": True, "quota_threshold": True}, "x", {"quota_alerts": True}):
            self.assertFalse(meter.set_notification_settings(bad, self.path)["ok"], bad)
        self.assertEqual(meter.notification_settings(self.path)["quota_threshold"], 90)

    def test_migrate_applies_only_when_unset(self):
        result = meter.set_notification_settings({"quota_alerts": True, "quota_threshold": 95}, self.path, migrate=True)
        self.assertTrue(result["changed"])
        result = meter.set_notification_settings({"quota_alerts": False, "quota_threshold": 80}, self.path, migrate=True)
        self.assertEqual(result, {"ok": True, "changed": False,
                                  "notifications": {"quota_alerts": True, "quota_threshold": 95}})

    def test_other_settings_are_preserved(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"menubar": {"cache_timer": "off"}}, fh)
        meter.set_notification_settings({"quota_alerts": True, "quota_threshold": 80}, self.path)
        with open(self.path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["menubar"], {"cache_timer": "off"})

    def test_invalid_stored_value_falls_back_to_default(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"notifications": {"quota_alerts": 3}}, fh)
        self.assertEqual(meter.notification_settings(self.path)["source"], "default")


class NotificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.settings = os.path.join(self.dir.name, "settings.json")
        self.state = os.path.join(self.dir.name, "notifications.json")
        persist = mock.patch.object(meter, "_notification_persist", True)
        persist.start()
        self.addCleanup(persist.stop)
        meter.reset_notification_runtime()

    def tearDown(self):
        meter.reset_notification_runtime()
        self.dir.cleanup()

    def refresh(self, observation, now=NOW, force=True):
        return meter.refresh_notifications(now=now, observation=observation, path=self.state,
                                           settings_path=self.settings, force=force)

    def test_state_persists_and_first_fresh_pass_establishes_quota(self):
        self.refresh(observe(quota(70.0)))
        state = self.refresh(observe(quota(96.0)), now=NOW + 5)
        self.assertEqual([e["severity"] for e in state["events"]], ["critical"])
        with open(self.state, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["events"][0]["id"], state["events"][0]["id"])

    def test_restart_requires_a_new_fresh_pass_before_quota_alerts(self):
        self.refresh(observe(quota(70.0)))
        meter.reset_notification_runtime()
        state = self.refresh(observe(quota(96.0)), now=NOW + 5)
        self.assertEqual(state["events"], [])

    def test_throttle_skips_evaluation(self):
        self.refresh(observe(quota(70.0)))
        self.refresh(observe(quota(10.0)), now=NOW + 1)
        state = self.refresh(observe(quota(96.0)), now=NOW + 1.5, force=False)
        self.assertEqual(state["events"], [])
        state = self.refresh(observe(quota(96.0)), now=NOW + 4, force=False)
        self.assertEqual(len(state["events"]), 1)

    def test_corrupt_state_file_starts_over(self):
        with open(self.state, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        state = self.refresh(observe(budget_status=budget(95.0)))
        self.assertEqual(state["events"], [])
        self.assertEqual(state["budget"]["overall"]["fired"], [80, 90])

    def test_rules_wait_for_cross_session_data(self):
        # A cold server reports a placeholder 0% budget and no hints; seeding from it would turn the
        # first real observation into spurious "crossings".
        with mock.patch.object(meter, "_xsess", {"data": None}), mock.patch.object(meter, "STATE", None):
            self.assertIsNone(meter.notification_observation())
            with mock.patch.object(meter, "notification_observation", wraps=meter.notification_observation):
                state = meter.refresh_notifications(path=self.state, settings_path=self.settings, now=NOW)
        self.assertEqual(state, N.empty_state())
        self.assertFalse(os.path.exists(self.state))
        ready = {"budget": budget(84.0), "current_sessions": []}
        with mock.patch.object(meter, "_xsess", {"data": ready}), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.object(meter, "live_hint_notifications_enabled", return_value=False):
            state = meter.refresh_notifications(path=self.state, settings_path=self.settings, now=NOW + 0.5)
        self.assertEqual(state["budget"]["overall"]["fired"], [80])
        self.assertEqual(state["events"], [])

    def test_payload_marks_expired_backlog_unclaimable_so_new_events_are_reachable(self):
        self.refresh(observe(hints_enabled=True))
        hints = [{"id": f"h{i}", "title": "t", "body": "b"} for i in range(6)]
        self.refresh(observe(hints=hints, hints_enabled=True), now=NOW + 5)
        fresh = hints + [{"id": "fresh", "title": "t", "body": "b"}]
        later = NOW + N.CLAIM_WINDOW_S + 60
        self.refresh(observe(hints=fresh, hints_enabled=True), now=later)
        with mock.patch.object(meter.time, "time", return_value=later + 1):
            payload = meter.notifications_payload(path=self.state, settings_path=self.settings)
        claimable = [e["id"] for e in payload["events"] if e["claimable"]]
        self.assertEqual(claimable, ["hint:fresh"])

    def test_hints_reseed_once_per_server_process(self):
        self.refresh(observe(hints_enabled=True))
        meter.reset_notification_runtime()
        state = self.refresh(observe(hints=[{"id": "while-down", "title": "t", "body": "b"}], hints_enabled=True),
                             now=NOW + 5)
        self.assertEqual(state["events"], [])
        state = self.refresh(observe(hints=[{"id": "after", "title": "t", "body": "b"}], hints_enabled=True),
                             now=NOW + 10)
        self.assertEqual([e["id"] for e in state["events"]], ["hint:after"])

    def test_prefs_combine_notification_and_budget_settings(self):
        meter.set_notification_settings({"quota_alerts": False, "quota_threshold": 95}, self.settings)
        prefs = meter.notification_prefs(self.settings)
        self.assertEqual(prefs["source"], "saved")
        self.assertEqual(prefs["quota_alerts"], False)
        self.assertEqual(prefs["quota_threshold"], 95)
        self.assertEqual(prefs["budget_alerts"], True)
        self.assertEqual(prefs["budget_thresholds"], [80, 90, 100])
        self.assertIn("live_hints", prefs)

    def test_claim_is_saved_and_single_winner_under_concurrency(self):
        self.refresh(observe(budget_status=budget(70.0)))
        state = self.refresh(observe(budget_status=budget(85.0)), now=NOW + 5)
        event_id = state["events"][0]["id"]
        results = []
        threads = [
            threading.Thread(target=lambda name=name: results.append(
                meter.claim_notification(event_id, name, path=self.state, now=NOW + 10)))
            for name in ("token-meter-menubar", "token-meter-tray", "ghost", "other")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(1 for r in results if r["claimed"]), 1)
        with open(self.state, encoding="utf-8") as fh:
            saved = json.load(fh)["events"][0]
        self.assertTrue(saved["claimed"])

    def test_payload_contract(self):
        self.refresh(observe(budget_status=budget(70.0)))
        self.refresh(observe(budget_status=budget(85.0)), now=NOW + 5)
        payload = meter.notifications_payload(path=self.state, settings_path=self.settings)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["actions"], {"token": meter._ACTION_TOKEN, "claim": "/notifications/claim",
                                              "settings": "/settings/notifications"})
        self.assertEqual(set(payload["prefs"]), {"source", "quota_alerts", "quota_threshold", "budget_alerts",
                                                 "budget_thresholds", "live_hints"})
        self.assertEqual(set(payload["events"][0]), {"id", "kind", "severity", "title", "body", "link",
                                                     "created_at", "claimed", "claimed_by", "claimable"})
        text = json.dumps(payload)
        self.assertNotIn(self.dir.name, text)
        self.assertNotIn(os.path.expanduser("~"), text)


class NotificationPersistenceGuardTests(unittest.TestCase):
    def test_nothing_is_read_or_written_unless_the_server_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "n.json")
            with mock.patch.object(meter, "_notification_persist", False):
                state = meter.refresh_notifications(path=path, observation=observe(quota(99.0)), force=True)
            self.assertEqual(state, N.empty_state())
            self.assertFalse(os.path.exists(path))

    def test_claims_and_payload_reads_also_require_the_running_server(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "n.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "events": [{"id": "x", "created_at": NOW, "claimed": False}]}, fh)
            before = os.listdir(directory)
            with mock.patch.object(meter, "_notification_persist", False):
                result = meter.claim_notification("x", "ghost", path=path, now=NOW)
                payload = meter.notifications_payload(path=path)
            self.assertFalse(result["claimed"])
            self.assertEqual(payload["events"], [])
            self.assertEqual(os.listdir(directory), before)

    def test_main_enables_persistence(self):
        source = inspect.getsource(meter.main)
        self.assertIn("_notification_persist = True", source)


class NotificationHttpTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(meter, "TOKEN_METER_NOTIFICATIONS", os.path.join(self.dir.name, "n.json")),
            mock.patch.object(meter, "TOKEN_METER_SETTINGS", os.path.join(self.dir.name, "s.json")),
            mock.patch.object(meter, "_notification_persist", True),
        ]
        for patch in self.patches:
            patch.start()
        meter.reset_notification_runtime()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        meter.reset_notification_runtime()
        self.dir.cleanup()

    def post(self, path, body, *, token=None, origin="http://127.0.0.1:8722"):
        handler = object.__new__(meter.H)
        handler.path = path
        raw = json.dumps(body).encode("utf-8")
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(raw)),
                           "X-Token-Meter-Action": meter._ACTION_TOKEN if token is None else token}
        if origin:
            handler.headers["Origin"] = origin
        handler.rfile = io.BytesIO(raw)
        handler._send = mock.Mock()
        handler.send_error = mock.Mock()
        handler.do_POST()
        self.assertFalse(handler.send_error.called)
        args, kwargs = handler._send.call_args
        return json.loads(args[0]), kwargs.get("status", 200)

    def get(self, path):
        handler = object.__new__(meter.H)
        handler.path = path
        handler.headers = {}
        handler._send = mock.Mock()
        handler.send_error = mock.Mock()
        with mock.patch.object(meter, "notification_observation", return_value=observe()):
            handler.do_GET()
        self.assertFalse(handler.send_error.called)
        return json.loads(handler._send.call_args.args[0])

    def seed_event(self):
        meter.refresh_notifications(now=NOW, observation=observe(budget_status=budget(70.0)), force=True)
        state = meter.refresh_notifications(now=NOW + 5, observation=observe(budget_status=budget(85.0)), force=True)
        return state["events"][0]["id"]

    def test_get_notifications(self):
        payload = self.get("/notifications")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["events"], [])

    def test_claim_route(self):
        event_id = self.seed_event()
        with mock.patch.object(meter.time, "time", return_value=NOW + 10):
            first, status = self.post("/notifications/claim", {"id": event_id, "consumer": "token-meter-menubar"})
            second, _ = self.post("/notifications/claim", {"id": event_id, "consumer": "ghost"})
        self.assertEqual((first, status), ({"ok": True, "claimed": True}, 200))
        self.assertEqual(second["reason"], "claimed")

    def test_claim_route_rejects_bad_input_and_callers(self):
        event_id = self.seed_event()
        payload, status = self.post("/notifications/claim", {"id": event_id, "consumer": "Bad Name"})
        self.assertEqual(status, 400)
        payload, status = self.post("/notifications/claim", {"id": 5, "consumer": "ghost"})
        self.assertEqual(status, 400)
        payload, status = self.post("/notifications/claim", {"id": event_id, "consumer": "ghost"}, token="wrong")
        self.assertEqual(status, 403)
        payload, status = self.post("/notifications/claim", {"id": event_id, "consumer": "ghost"},
                                    origin="https://evil.example")
        self.assertEqual(status, 403)
        state = meter.load_notification_state()
        self.assertFalse(state["events"][0]["claimed"])

    def test_settings_route(self):
        payload, status = self.post("/settings/notifications", {"quota_alerts": False, "quota_threshold": 90})
        self.assertEqual(status, 200)
        self.assertTrue(payload["changed"])
        payload, status = self.post("/settings/notifications", {"quota_alerts": False, "quota_threshold": 7})
        self.assertEqual(status, 400)
        payload, _ = self.post("/settings/notifications",
                               {"quota_alerts": True, "quota_threshold": 80, "migrate": True})
        self.assertFalse(payload["changed"])
        payload, status = self.post("/settings/notifications",
                                    {"quota_alerts": True, "quota_threshold": 80, "migrate": "yes"})
        self.assertEqual(status, 400)

    def test_menubar_embeds_notifications_and_drops_hint_fields(self):
        with (
            mock.patch.object(meter, "notification_observation", return_value=observe()),
            mock.patch.object(meter, "STATE", {}),
            mock.patch.object(meter, "provider_quota_snapshots", return_value=[]),
        ):
            payload = meter.menubar_state()
        self.assertEqual(payload["notifications"]["schema_version"], 1)
        self.assertNotIn("live_hints", payload)
        self.assertNotIn("live_hints_enabled", payload)


if __name__ == "__main__":
    unittest.main()
