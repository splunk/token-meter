import unittest
import json
import tempfile
import threading
from pathlib import Path
from unittest import mock
import meter


class DailySummaryTests(unittest.TestCase):
    def test_compare_sessions_state_projects_selected_sessions_without_content(self):
        from token_meter.domain.compare import trace_key
        pool = [
            {"id": "one", "path": "/private/traces/ws/one/messages.jsonl", "provider": "kiro"},
            {"id": "two", "path": "/private/traces/ws/two/messages.jsonl", "provider": "kiro"},
            {"id": "root", "path": "/private/traces/rollout-9-root.jsonl", "provider": "codex"},
        ]
        key = {source["id"]: trace_key(source) for source in pool}

        def summary(source):
            return {
                "id": source["id"], "path": source["path"], "title": "Same prompt",
                "label": "Kiro", "provider": source["provider"], "models": ["m"],
                "usage_basis": "reported", "availability": {"cost": True},
            }

        def state(source):
            cost = 1.0 if source["id"] == "one" else 2.5
            return {
                "availability": {"cost": True, "tokens": True},
                "total_cost": cost, "turns": 4, "total_tokens": 1000,
                "timing": {"duration_s": 60, "duration_available": True},
                "series": [{"i": 1, "cost": cost, "in": 10, "out": 5,
                            "user_message": "PRIVATE PROMPT TEXT"}],
                "source": {"path": source["path"]},
            }

        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["sessions"] = [
                {"id": "one", "title": "Same prompt", "path": pool[0]["path"]},
                {"id": "root", "title": "same prompt", "mtime": 5, "cost": 0.4, "path": pool[2]["path"]},
                {"id": "four", "title": "Different", "path": "/private/traces/four.jsonl"},
            ]
            with mock.patch.object(meter, "cached_session_sources", return_value=(pool, True)), \
                    mock.patch.object(meter, "session_summary", side_effect=summary), \
                    mock.patch.object(meter, "cached_session_state", side_effect=state):
                payload, status = meter.compare_sessions_state(f"{key['one']},{key['two']},tgone")
                single, single_status = meter.compare_sessions_state(key["one"])
                empty, empty_status = meter.compare_sessions_state("")
                missing, missing_status = meter.compare_sessions_state("tgone,talsogone")
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual([row["letter"] for row in payload["sessions"]], ["A", "B"])
        self.assertEqual([row["id"] for row in payload["sessions"]], ["one", "two"])
        # Repeated file names fall back to the session id; unique stems open the exact trace.
        self.assertEqual([row["open_id"] for row in payload["sessions"]], ["one", "two"])
        self.assertEqual(payload["missing"], ["tgone"])
        self.assertTrue(payload["same_title"])
        self.assertEqual(payload["best"]["cost"], key["one"])
        self.assertEqual([row["key"] for row in payload["matches"]], [key["root"]])
        self.assertEqual(payload["matches"][0]["open_id"], "rollout-9-root")
        encoded = json.dumps(payload)
        self.assertNotIn("PRIVATE PROMPT TEXT", encoded)
        self.assertNotIn("/private/traces", encoded)
        self.assertEqual(single_status, 200)
        self.assertNotIn("insights", single)
        self.assertNotIn("insights", payload)
        self.assertEqual((empty_status, empty["ok"]), (400, False))
        self.assertEqual((missing_status, missing["ok"]), (404, False))
        self.assertIn(
            'elif req_path == "/session/compare":',
            Path(meter.IMPLEMENTATION_FILE).read_text(),
        )
        self.assertFalse(meter.is_dashboard_page_path("/session/compare"))

    def test_spend_logs_state_validates_and_returns_full_range(self):
        sessions = (
            {
                "id": "multi-day", "title": "Multi day", "project": "/repo/a",
                "provider": "codex", "label": "Codex",
                "availability": {"cost": True},
                "input_tokens": 12000, "output_tokens": 600, "turns": 6,
                "duration_s": 420, "duration_available": True,
                "duration_basis": "observed", "token_estimate": True,
                "_day_cost": {
                    "2026-08-01": 1.25,
                    "2026-08-02": 2.75,
                    "2026-08-03": 8.0,
                },
            },
            {
                "id": "second", "title": "Second", "project": "/repo/b",
                "provider": "claude", "label": "Claude",
                "availability": {"cost": True},
                "_day_cost": {"2026-08-02": 2.0},
            },
        )
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["internal_rows"] = sessions
            with mock.patch.object(
                meter, "cross_session", return_value={"generated_at": 123},
            ):
                payload, status = meter.spend_logs_state(
                    "2026-08-01", "2026-08-02",
                )
                missing, missing_status = meter.spend_logs_state("", "2026-08-02")
                malformed, malformed_status = meter.spend_logs_state(
                    "2026-08-01", "not-a-date",
                )
                reversed_range, reversed_status = meter.spend_logs_state(
                    "2026-08-03", "2026-08-02",
                )
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["generated_at"], 123)
        self.assertEqual(payload["from"], "2026-08-01")
        self.assertEqual(payload["to"], "2026-08-02")
        self.assertEqual(payload["total_sessions"], 2)
        self.assertEqual(payload["total_cost"], 6.0)
        self.assertEqual(payload["sessions"][0]["id"], "multi-day")
        self.assertEqual(payload["sessions"][0]["input_tokens"], 12000)
        self.assertEqual(payload["sessions"][0]["output_tokens"], 600)
        self.assertEqual(payload["sessions"][0]["turns"], 6)
        self.assertEqual(payload["sessions"][0]["duration_s"], 420)
        self.assertTrue(payload["sessions"][0]["duration_available"])
        self.assertEqual(payload["sessions"][0]["duration_basis"], "observed")
        self.assertEqual(payload["sessions"][0]["usage_basis"], "local_estimate")
        for error, error_status in (
            (missing, missing_status),
            (malformed, malformed_status),
            (reversed_range, reversed_status),
        ):
            self.assertEqual(error_status, 400)
            self.assertFalse(error["ok"])
        self.assertIn(
            'elif req_path == "/spend/logs":',
            Path(meter.IMPLEMENTATION_FILE).read_text(),
        )

    def test_aggregates_daily_spend_providers_and_logs(self):
        sessions = [
            {"id": "one", "title": "One", "project": "/repo/a", "provider": "codex",
             "label": "Codex", "_day_cost": {"2026-07-01": 1.25},
             "_wait_samples": [
                 {"day": "2026-07-01", "duration_s": 20},
                 {"day": "2026-07-01", "duration_s": 40},
             ]},
            {"id": "two", "title": "Two", "project": "/repo/b", "provider": "claude",
             "label": "Claude Code", "_day_cost": {"2026-07-01": 0.75, "2026-06-30": 0.5},
             "_wait_samples": [{"day": "2026-07-01", "duration_s": 30}]},
        ]
        days = meter.daily_summaries(sessions)
        self.assertEqual(days[0]["day"], "2026-07-01")
        self.assertEqual(days[0]["cost"], 2.0)
        self.assertEqual(days[0]["sessions"], 2)
        self.assertEqual(days[0]["projects"], 2)
        self.assertNotIn("tool_tokens", days[0])
        self.assertNotIn("flagged_tokens", days[0])
        provider = days[0]["providers"][0]
        self.assertEqual(provider["provider"], "codex")
        self.assertEqual(provider["cost"], 1.25)
        self.assertEqual(provider["wait_s"], 60.0)
        self.assertEqual(provider["wait_samples"], 2)
        self.assertEqual(provider["usage_basis"], "reported")
        self.assertEqual(days[0]["wait_time"]["total_s"], 90)
        self.assertEqual(days[0]["wait_time"]["avg_s"], 30)
        self.assertEqual(days[0]["wait_time"]["max_s"], 40)

    def test_unbounded_daily_history_has_compact_spend_projection(self):
        sessions = [{
            "id": "one", "title": "Private title", "project": "/private/repo",
            "provider": "codex", "label": "Codex",
            "_day_cost": {
                f"2026-07-{day:02d}": float(day) for day in range(1, 32)
            },
        }]
        legacy = meter.daily_summaries(sessions)
        complete = meter.daily_summaries(sessions, limit=None)
        spend = meter.spend_projection(complete)
        self.assertEqual(len(legacy), 30)
        self.assertEqual(len(complete), 31)
        self.assertEqual(spend[0]["day"], "2026-07-31")
        self.assertEqual(spend[0]["providers"][0]["provider"], "codex")
        self.assertEqual(spend[0]["providers"][0]["cost"], 31.0)
        self.assertEqual(
            set(spend[0]),
            {"day", "cost", "providers", "coverage", "provenance",
             "usage_basis", "availability"},
        )
        serialized = json.dumps(spend)
        self.assertNotIn("Private title", serialized)
        self.assertNotIn("/private/repo", serialized)
        self.assertNotIn("top_sessions", serialized)

    def test_cross_session_publishes_spend_history_and_keeps_daily(self):
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess.update({"data": None, "at": 0, "sessions": []})
            result = meter.cross_session(sources=[])
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual(result["spend"], {"days": []})
        self.assertEqual(result["daily"], [])


class MonthlyBudgetTests(unittest.TestCase):
    def test_budget_settings_persist_session_overrides_without_changing_defaults(self):
        """A session cap survives settings writes and remains distinct from its default."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            result = meter.set_budget_settings({
                "default_session_budget": 10,
                "session_budgets": {"session-1": 25.5},
                "allocations": {},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
            stored = json.loads(path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(result["budgets"].get("default_session_budget"), 10.0)
        self.assertEqual(result["budgets"].get("session_budgets"), {"session-1": 25.5})
        self.assertEqual(stored["budgets"].get("session_budgets"), {"session-1": 25.5})

    def test_general_budget_save_preserves_existing_session_overrides(self):
        """The Settings form must not erase caps it does not display or submit."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            meter.set_session_budget_override("session-1", 25, path=str(path))
            result = meter.set_budget_settings({
                "default_session_budget": 12,
                "allocations": {},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
            stored = json.loads(path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(stored["budgets"]["default_session_budget"], 12.0)
        self.assertEqual(stored["budgets"]["session_budgets"], {"session-1": 25.0})

    def test_concurrent_session_compare_and_set_allows_one_writer(self):
        """Competing confirmed writes must serialize to success plus stale response."""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            ready = threading.Barrier(2)
            results = []

            def change(budget):
                ready.wait()
                results.append(meter.set_session_budget_override(
                    "session-1", budget, expected_current_budget_usd=10, path=path,
                ))

            first = threading.Thread(target=change, args=(12,))
            second = threading.Thread(target=change, args=(15,))
            first.start()
            second.start()
            first.join()
            second.join()

        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual(sum("changed" in result.get("error", "") for result in results), 1)

    def test_default_session_budget_compare_and_set_rejects_a_stale_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            first = meter.set_default_session_budget_value(
                12, expected_current_budget_usd=10, path=path,
            )
            stale = meter.set_default_session_budget_value(
                15, expected_current_budget_usd=10, path=path,
            )

        self.assertTrue(first["ok"])
        self.assertFalse(stale["ok"])
        self.assertIn("changed", stale["error"])

    def test_agent_budget_projection_excludes_unrequested_global_configuration(self):
        source = {"id": "session-1", "provider": "codex", "client": "Codex"}
        state = {"total_cost": 3, "availability": {"cost": True}}
        settings = meter.normalize_budget_settings({
            "default_session_budget": 10,
            "session_budgets": {"session-1": 5},
            "allocations": {}, "thresholds": [80, 90, 100],
            "native_notifications": True,
        })
        with mock.patch.object(meter, "resolve_agent_source", return_value=(source, "explicit")), \
                mock.patch.object(meter, "recompute", return_value=state), \
                mock.patch.object(meter, "budget_settings", return_value=settings):
            result = meter.agent_budget(session_id="session-1")

        self.assertEqual(result["session"]["budget_usd"], 5)
        self.assertNotIn("default_session_budget_usd", result)
        self.assertNotIn("session_budget_overrides", result)
        self.assertNotIn("lower bound", result["caveat"])

    def test_agent_budget_caveat_marks_partial_family_spend_as_lower_bound(self):
        source = {"id": "ses_root", "provider": "opencode", "client": "OpenCode"}
        state = {"total_cost": 1.0, "availability": {"cost": True}}
        family = [
            {"id": "ses_root", "provider": "opencode", "cost": 1.0,
             "availability": {"cost": True}},
            {"id": "ses_child", "provider": "opencode", "cost": 0.0,
             "is_child_session": True, "root_session_id": "ses_root",
             "availability": {"cost": False}},
        ]
        settings = meter.normalize_budget_settings({})
        with mock.patch.object(meter, "resolve_agent_source", return_value=(source, "explicit")), \
                mock.patch.object(meter, "recompute", return_value=state), \
                mock.patch.object(meter, "budget_settings", return_value=settings), \
                mock.patch.dict(meter._xsess, {"sessions": family}):
            result = meter.agent_budget(session_id="ses_root")

        self.assertTrue(result["session"]["cost_partial"])
        self.assertIn("Spend is a lower bound", result["caveat"])

    def test_session_override_compare_and_set_rejects_a_stale_budget(self):
        """A delayed agent must not silently replace a newer session cap."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            first = meter.set_session_budget_override(
                "session-1", 12, expected_current_budget_usd=10, path=str(path),
            )
            stale = meter.set_session_budget_override(
                "session-1", 15, expected_current_budget_usd=10, path=str(path),
            )
            effective, source = meter.effective_session_budget(
                "session-1", meter.budget_settings(str(path)),
            )

        self.assertTrue(first["ok"])
        self.assertFalse(stale["ok"])
        self.assertIn("changed", stale["error"])
        self.assertEqual((effective, source), (12.0, "session_override"))

    def test_missing_default_session_budget_migrates_to_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "budgets": {
                    "currency": "USD",
                    "allocations": {},
                    "thresholds": [80, 90, 100],
                    "native_notifications": True,
                },
            }))

            loaded = meter.budget_settings(str(path))

        self.assertEqual(loaded.get("default_session_budget"), 10)

    def test_default_session_budget_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"model_pricing": {"claude": {}}}))
            too_small = meter.set_budget_settings({
                "default_session_budget": 0,
                "allocations": {},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
            result = meter.set_budget_settings({
                "default_session_budget": 25.5,
                "allocations": {},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
            stored = json.loads(path.read_text())

        self.assertFalse(too_small["ok"])
        self.assertIn("Default session budget", too_small["error"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["budgets"]["default_session_budget"], 25.5)
        self.assertEqual(stored["budgets"]["default_session_budget"], 25.5)
        self.assertIn("model_pricing", stored)

    def test_settings_derive_total_from_allocations_and_preserve_other_machine_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"model_pricing": {"claude": {}}}))
            invalid = meter.set_budget_settings({
                "allocations": {"claude": 60_000_000, "codex": 50_000_000},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
            result = meter.set_budget_settings({
                "allocations": {"claude": 50, "codex": 30, "cursor": 0},
                "thresholds": [75, 90, 100],
                "native_notifications": False,
            }, str(path))
            stored = json.loads(path.read_text())
        self.assertFalse(invalid["ok"])
        self.assertTrue(result["ok"])
        self.assertEqual(stored["budgets"]["monthly_total"], 80)
        self.assertEqual(
            stored["budgets"]["allocations"],
            {"claude": 50, "codex": 30, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0, "agy": 0},
        )
        self.assertIn("model_pricing", stored)

    def test_missing_runtime_budgets_default_to_zero_and_explicit_values_are_preserved(self):
        legacy = {
            "currency": "USD",
            "monthly_total": 100,
            "allocations": {},
            "thresholds": [80, 90, 100],
            "native_notifications": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"budgets": legacy}))
            loaded = meter.budget_settings(str(path))
            saved = meter.set_budget_settings({
                "allocations": {"claude": 0, "codex": 1490, "cursor": 0},
                "thresholds": [80, 90, 100],
                "native_notifications": True,
            }, str(path))
        self.assertEqual(loaded["monthly_total"], 0)
        self.assertEqual(
            loaded["allocations"],
            {"claude": 0, "codex": 0, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0, "agy": 0},
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["budgets"]["monthly_total"], 1490)
        self.assertEqual(
            saved["budgets"]["allocations"],
            {"claude": 0, "codex": 1490, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0, "agy": 0},
        )

    def test_monthly_rollup_keeps_runtime_costs_and_partial_coverage(self):
        sessions = [
            {
                "id": "reported", "provider": "claude",
                "_day_cost": {"2026-07-01": 20, "2026-07-03": 10},
                "_model_daily": [{"day": "2026-07-01"}],
                "availability": {"cost": True},
            },
            {
                "id": "estimated", "provider": "cursor", "token_estimate": True,
                "_day_cost": {"2026-07-02": 5},
                "_model_daily": [{"day": "2026-07-02"}],
                "availability": {"cost": True},
            },
            {
                "id": "missing", "provider": "codex",
                "_model_daily": [{"day": "2026-07-04"}],
                "availability": {"cost": False},
            },
        ]
        rows = meter.monthly_summaries(sessions)
        self.assertEqual(rows[0]["month"], "2026-07")
        self.assertEqual(rows[0]["cost"], 35)
        self.assertEqual(rows[0]["active_days"], 3)
        self.assertEqual(rows[0]["sessions"], 3)
        self.assertFalse(rows[0]["coverage"]["cost"]["complete"])
        self.assertEqual(rows[0]["provenance"]["estimated_cost"], 5)
        self.assertEqual(
            {row["provider"]: row["cost"] for row in rows[0]["providers"]},
            {"claude": 30, "cursor": 5, "codex": 0},
        )

    def test_budget_status_projects_after_three_spend_days_and_marks_lower_bound(self):
        months = [{
            "month": "2026-07", "cost": 60, "active_days": 3, "observed_days": 4,
            "providers": [
                {"provider": "claude", "cost": 40},
                {"provider": "codex", "cost": 20},
            ],
            "coverage": {"cost": {
                "covered_sessions": 2, "total_sessions": 3, "complete": False,
            }},
            "provenance": {
                "estimated_sessions": 0, "estimated_cost": 0,
                "usage_basis": "reported",
            },
        }]
        status = meter.monthly_budget_status(months, {
            "allocations": {"claude": 50, "codex": 30, "cursor": 0},
            "thresholds": [50, 80, 100],
            "native_notifications": True,
        }, now=meter.datetime.datetime(2026, 7, 10, tzinfo=meter.datetime.timezone.utc))
        self.assertEqual(status["spend"], 60)
        self.assertEqual(status["budget"], 80)
        self.assertEqual(status["remaining"], 20)
        self.assertEqual(status["unallocated"], 0)
        self.assertAlmostEqual(status["projected_spend"], 186)
        self.assertTrue(status["lower_bound"])
        self.assertEqual(status["thresholds_crossed"], [50])
        self.assertEqual(status["next_threshold"], 80)
        claude = next(row for row in status["runtimes"] if row["provider"] == "claude")
        self.assertEqual(claude["percent"], 0.8)

    def test_antigravity_character_estimates_are_shown_but_do_not_drive_alerts(self):
        def month(agy_estimated_cost):
            return [{
                "month": "2026-07", "cost": 110, "active_days": 4,
                "providers": [
                    {"provider": "agy", "cost": 90,
                     "provenance": {"estimated_sessions": 1, "estimated_cost": agy_estimated_cost}},
                    {"provider": "codex", "cost": 20, "provenance": {"estimated_sessions": 0}},
                ],
                "coverage": {"cost": {"covered_sessions": 2, "total_sessions": 2, "complete": True}},
                "provenance": {"estimated_sessions": 1},
            }]

        settings = {
            "allocations": {"agy": 50, "codex": 50},
            "thresholds": [50, 80, 100],
            "native_notifications": True,
        }
        now = meter.datetime.datetime(2026, 7, 10, tzinfo=meter.datetime.timezone.utc)
        estimated = meter.monthly_budget_status(month(80), settings, now=now)
        measured = meter.monthly_budget_status(month(0), settings, now=now)

        # The budget card shows the full spend, estimates included.
        self.assertEqual(estimated["spend"], 110)
        self.assertAlmostEqual(estimated["percent"], 1.1)
        agy = next(row for row in estimated["runtimes"] if row["provider"] == "agy")
        self.assertEqual(agy["spend"], 90)
        # Alerts are measured without the character estimates.
        self.assertEqual(estimated["alert_excluded_spend"], 80)
        self.assertAlmostEqual(estimated["alert_percent"], 0.3)
        self.assertAlmostEqual(agy["alert_percent"], 0.2)
        self.assertEqual(estimated["thresholds_crossed"], [])
        # Everything on the card follows the full spend shown.
        self.assertEqual(estimated["state"], "over")
        self.assertIsNone(estimated["next_threshold"])
        self.assertEqual([row["provider"] for row in estimated["exceeded_runtimes"]], ["agy"])
        self.assertEqual(measured["alert_excluded_spend"], 0)
        self.assertAlmostEqual(measured["alert_percent"], 1.1)
        self.assertEqual(measured["thresholds_crossed"], [50, 80, 100])

    def test_shown_spend_drives_the_warning_state_while_notifications_wait(self):
        months = [{
            "month": "2026-07", "cost": 60, "active_days": 4,
            "providers": [
                {"provider": "agy", "cost": 40,
                 "provenance": {"estimated_sessions": 1, "estimated_cost": 40}},
                {"provider": "codex", "cost": 20, "provenance": {"estimated_sessions": 0}},
            ],
            "coverage": {"cost": {"covered_sessions": 2, "total_sessions": 2, "complete": True}},
            "provenance": {"estimated_sessions": 1},
        }]
        status = meter.monthly_budget_status(months, {
            "allocations": {"agy": 50, "codex": 50},
            "thresholds": [50, 80, 100],
            "native_notifications": True,
        }, now=meter.datetime.datetime(2026, 7, 10, tzinfo=meter.datetime.timezone.utc))

        self.assertEqual(status["state"], "warning")
        self.assertEqual(status["thresholds_crossed"], [])
        self.assertEqual(status["next_threshold"], 80)

    def test_runtime_overrun_is_reported_while_overall_budget_is_on_track(self):
        months = [{
            "month": "2026-07", "cost": 1501, "active_days": 4,
            "providers": [{"provider": "codex", "cost": 1501}],
            "coverage": {"cost": {
                "covered_sessions": 1, "total_sessions": 1, "complete": True,
            }},
            "provenance": {"estimated_sessions": 0},
        }]
        status = meter.monthly_budget_status(months, {
            "allocations": {"claude": 1000, "codex": 1490, "cursor": 1000},
            "thresholds": [80, 90, 100],
            "native_notifications": True,
        }, now=meter.datetime.datetime(2026, 7, 10, tzinfo=meter.datetime.timezone.utc))
        self.assertEqual(status["state"], "on_track")
        self.assertTrue(status["runtime_exceeded"])
        self.assertTrue(status["attention"])
        self.assertEqual(len(status["exceeded_runtimes"]), 1)
        self.assertEqual(status["exceeded_runtimes"][0]["provider"], "codex")
        self.assertEqual(status["exceeded_runtimes"][0]["over_by"], 11)
        codex = next(row for row in status["runtimes"] if row["provider"] == "codex")
        self.assertTrue(codex["exceeded"])


class McpDocumentationContractTests(unittest.TestCase):
    def test_maintained_docs_describe_the_read_only_query_surface(self):
        root = Path(__file__).resolve().parents[1]
        documents = {
            path: (root / path).read_text()
            for path in (
                "README.md",
                "specs/USER_GUIDE.md",
                "specs/ARCHITECTURE.md",
                "specs/SECURITY.md",
            )
        }
        for path, content in documents.items():
            with self.subTest(path=path):
                self.assertIn("read-only", content.lower())
                for tool in ("sessions", "trace", "stats", "schema"):
                    self.assertIn(tool, content)
                self.assertIn("not raw trace content", content.lower())

        combined = "\n".join(documents.values()).lower()
        for prohibited_claim in (
            "returns raw prompts",
            "returns raw responses",
            "raw tool payloads are available",
            "trace paths are available",
        ):
            self.assertNotIn(prohibited_claim, combined)


if __name__ == "__main__":
    unittest.main()
