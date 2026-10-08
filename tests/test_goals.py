import datetime
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.services import goal_coaching, goals
from token_meter.services.git_delivery import GitDeliveryService

AGENTS = {"claude": "Claude", "codex": "Codex"}
TODAY = datetime.date(2026, 10, 15)


def available(row, metric):
    return (row.get("availability") or {}).get(metric) is not False


def day(model, date, cost, output, executions=1, **extra):
    return {"model": model, "day": date, "cost": cost, "executions": executions,
            "input_tokens": extra.pop("input_tokens", 0), "output_tokens": output,
            "cost_covered_executions": executions, "cost_covered_output_tokens": output,
            "cost_covered_cost": cost, "io_covered_executions": executions,
            "reasoning_tokens": 0, "reasoning_output_tokens": 0, "reasoning_executions": 0, **extra}


def session(provider, ident, days, **extra):
    day_cost = {}
    for row in days:
        day_cost[row["day"]] = day_cost.get(row["day"], 0) + row["cost"]
    return {"provider": provider, "id": ident, "start": days[0]["day"] + "T12:00:00",
            "last": days[-1]["day"] + "T12:00:00", "cost": sum(day_cost.values()),
            "_day_cost": day_cost, "_model_daily": days,
            "model_stats": [{"executions": sum(row["executions"] for row in days)}], **extra}


def goal(metric, target, comparison="at_most", scope=None, start="2026-10-01", end="2026-10-31", **extra):
    return {"id": "a" * 32, "type": "metric", "metric": metric, "scope": scope or {"kind": "all", "key": ""},
            "comparison": comparison, "target": target, "period": "month", "start": start, "end": end,
            "created_at": "2026-10-01T00:00:00+00:00", **extra}


class GoalMeasurementTests(unittest.TestCase):
    def test_output_per_dollar_sums_numerators_and_denominators(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 1.0, 1000)]),
                session("codex", "2", [day("gpt-5.6-sol", "2026-10-03", 3.0, 1000)])]
        result = goals.measure("output_per_dollar", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31",
                               rows, available)
        self.assertEqual(result["value"], 500.0)
        self.assertTrue(result["complete"])

    def test_spend_uses_selected_dates_and_agent_only(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-30", 5.0, 10),
                                       day("gpt-5.6-sol", "2026-10-01", 2.0, 10)]),
                session("claude", "2", [day("claude-opus-5", "2026-10-02", 7.0, 10)])]
        result = goals.measure("spend", {"kind": "agent", "key": "codex"}, "2026-10-01", "2026-10-31",
                               rows, available)
        self.assertEqual(result["value"], 2.0)

    def test_missing_cost_is_incomplete_not_zero(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 0.0, 10)],
                        availability={"cost": False})]
        result = goals.measure("spend", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31", rows, available)
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "Cost data incomplete")

    def test_frontier_share_is_weighted_by_spend(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 3.0, 10, executions=1),
                                       day("gpt-6-luna", "2026-10-02", 1.0, 10, executions=9)])]
        result = goals.measure("frontier_share", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31",
                               rows, available)
        self.assertEqual(result["value"], 75.0)

    def test_unclassified_spend_withholds_a_result_that_it_could_flip(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-02", 4.0, 10),
                                       day("mystery-model", "2026-09-02", 2.0, 10),
                                       day("gpt-6-luna", "2026-09-02", 4.0, 10)])]
        item = goal("frontier_share", 50, start="2026-09-01", end="2026-09-30")
        evidence = goals.measure("frontier_share", item["scope"], item["start"], item["end"], rows, available)
        self.assertEqual(goals.goal_status(item, evidence, TODAY), ("no_result", "Unclassified models could change the result"))

    def test_reasoning_ratio_is_a_percentage_of_reported_output(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 1.0, 100, reasoning_tokens=25,
                                           reasoning_output_tokens=100, reasoning_executions=1)])]
        result = goals.measure("reasoning_ratio", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31",
                               rows, available)
        self.assertEqual(result["value"], 25.0)
        self.assertTrue(result["complete"])

    def test_context_load_excludes_executions_without_input_and_output(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 1.0, 100, input_tokens=400),
                                       day("gpt-6-luna", "2026-10-03", 1.0, 100, input_tokens=900,
                                           io_covered_executions=0)])]
        result = goals.measure("context_load", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31",
                               rows, available)
        self.assertEqual(result["value"], 4.0)
        self.assertFalse(result["complete"])

    def test_cost_per_1k_lines_needs_fifty_comparable_lines(self):
        def git_query(lines):
            return lambda scope, start, end: {"ok": True, "project_rows": [
                {"changed_lines": lines, "added": lines, "deleted": 0, "covered_cost": 2.0,
                 "availability": {"cost": True, "code_pushed": True, "partial": False}},
                {"changed_lines": 500, "added": 500, "deleted": 0, "covered_cost": 0.0,
                 "availability": {"cost": False, "code_pushed": True, "partial": False}}]}
        scope = {"kind": "all", "key": ""}
        self.assertIsNone(goals.measure("cost_per_1k_lines", scope, "2026-10-01", "2026-10-31", [],
                                        available, git_query(49))["value"])
        self.assertEqual(goals.measure("cost_per_1k_lines", scope, "2026-10-01", "2026-10-31", [],
                                       available, git_query(100))["value"], 20.0)


class GoalTargetAndStatusTests(unittest.TestCase):
    def test_target_options_use_relative_changes_and_percentage_points(self):
        spend = {o["id"]: o["target"] for o in goals.target_options("spend", 200.0)}
        self.assertAlmostEqual(spend["cut_10"], 180.0)
        share = {o["id"]: o["target"] for o in goals.target_options("frontier_share", 8.0)}
        self.assertEqual(share, {"lower_5": 3.0, "raise_5": 13.0})
        self.assertEqual(goals.target_options("spend", None), [])

    def test_spend_baseline_is_normalized_to_month_length(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-10", 30.0, 10)])]
        prior = goals.baseline("spend", {"kind": "all", "key": ""}, "month", datetime.date(2026, 10, 1),
                               datetime.date(2026, 10, 31), rows, available, None, TODAY)
        self.assertAlmostEqual(prior["value"], 31.0)

    def test_baseline_falls_back_to_ninety_days_then_this_period(self):
        start, end = datetime.date(2026, 10, 1), datetime.date(2026, 10, 31)
        scope = {"kind": "all", "key": ""}
        older = [session("codex", "1", [day("gpt-5.6-sol", "2026-08-01", 62.0, 10)])]
        prior = goals.baseline("spend", scope, "month", start, end, older, available, None, TODAY)
        self.assertEqual((prior["source"], prior["label"]), ("last_90", "Last 90 days"))
        self.assertAlmostEqual(prior["value"], 62.0 / 90 * 31)
        current = [session("codex", "2", [day("gpt-5.6-sol", "2026-10-15", 15.0, 10)])]
        prior = goals.baseline("output_per_dollar", scope, "month", start, end, current, available, None, TODAY)
        self.assertEqual((prior["source"], prior["label"]), ("so_far", "So far this month"))
        self.assertEqual(goals.target_options("spend", 100.0)[2]["label"], "Hold current level")

    def test_open_spend_goal_uses_pace_and_fails_once_above_target(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 10.0, 10)])]
        evidence = goals.measure("spend", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-15", rows, available)
        evidence["projected"] = evidence["value"] / 15 * 31
        self.assertEqual(goals.goal_status(goal("spend", 30), evidence, TODAY)[0], "on_track")
        self.assertEqual(goals.goal_status(goal("spend", 15), evidence, TODAY)[0], "at_risk")
        self.assertEqual(goals.goal_status(goal("spend", 5), evidence, TODAY)[0], "missed")

    def test_ended_goal_with_incomplete_evidence_has_no_result(self):
        item = goal("output_per_dollar", 100, "at_least", start="2026-09-01", end="2026-09-30")
        evidence = {"value": 500.0, "complete": False, "observations": 2, "reason": "Cost data incomplete"}
        self.assertEqual(goals.goal_status(item, evidence, TODAY), ("no_result", "Cost data incomplete"))
        evidence["complete"] = True
        self.assertEqual(goals.goal_status(item, evidence, TODAY)[0], "met")


class GoalStoreTests(unittest.TestCase):
    def test_store_drops_prototype_records_without_raising(self):
        raw = {"items": [{"id": "b" * 32, "type": "objective"}, goal("spend", 10)], "links": {"x": "y"}}
        store = goals.normalize_store(raw, AGENTS)
        self.assertEqual(len(store["items"]), 1)
        self.assertEqual(store["dropped"], 1)

    def test_save_end_and_delete(self):
        draft = {k: v for k, v in goal("spend", 10).items() if k not in ("id", "type", "created_at")}
        store = goals.apply_action({}, {"operation": "save", "goal": draft}, AGENTS, set(), TODAY)
        goal_id = store["items"][0]["id"]
        store = goals.apply_action(store, {"operation": "end", "id": goal_id}, AGENTS, set(), TODAY)
        self.assertEqual(store["items"][0]["ended_at"], "2026-10-15")
        store = goals.apply_action(store, {"operation": "delete", "id": goal_id}, AGENTS, set(), TODAY)
        self.assertEqual(store["items"], [])

    def test_one_direction_metrics_reject_the_other_direction(self):
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("output_per_dollar", 100, "at_most"), AGENTS)
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("cost_per_1k_lines", 20, "at_least"), AGENTS)
        self.assertEqual(goals.normalize_goal(goal("cost_per_1k_lines", 20, "at_most"), AGENTS)["comparison"], "at_most")

    def test_pushed_lines_reject_agent_scope_and_mismatched_month(self):
        draft = goal("cost_per_1k_lines", 10, "at_least", scope={"kind": "agent", "key": "codex"})
        with self.assertRaises(ValueError):
            goals.normalize_goal(draft, AGENTS)
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("spend", 10, end="2026-10-30"), AGENTS)


class GoalStarterTests(unittest.TestCase):
    def test_starter_falls_back_to_highest_spend_agent(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-10", 30.0, 3000)])]
        starter = goals.starters(rows, AGENTS, available, None, TODAY)[1]
        self.assertEqual(starter["title"], "Improve Codex Output / $")
        self.assertAlmostEqual(starter["target"], 115.0)

    def test_native_summary_has_no_titles(self):
        data = {"ok": True, "goals": [{**goal("spend", 10, title="Private name"), "status": "on_track",
                                        "measurement": {"value": 1.0, "confidence": "complete"}}]}
        summary = goals.native_summary(data, TODAY)
        self.assertNotIn("Private name", str(summary))
        self.assertEqual(summary["items"][0]["status"], "on_track")


class GoalRefinementTests(unittest.TestCase):
    def test_reasoning_ratio_leaves_out_sessions_that_never_report_reasoning(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-02", 1.0, 100, reasoning_tokens=20,
                                           reasoning_output_tokens=100, reasoning_executions=1)]),
                session("claude", "2", [day("claude-opus-5", "2026-09-03", 1.0, 100)])]
        item = goal("reasoning_ratio", 30, start="2026-09-01", end="2026-09-30")
        evidence = goals.measure("reasoning_ratio", item["scope"], item["start"], item["end"], rows, available)
        self.assertEqual(evidence["value"], 20.0)
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["reasoning_excluded"], 1)
        self.assertEqual(goals.goal_status(item, evidence, TODAY)[0], "met")

    def test_pushed_lines_report_repositories_and_share_of_spend_counted(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 10.0, 10)])]
        git = lambda scope, start, end: {"ok": True, "project_rows": [
            {"project": "small · aaaaaa", "changed_lines": 60, "added": 50, "deleted": 10, "covered_cost": 1.0,
             "availability": {"cost": True, "code_pushed": True, "partial": False}},
            {"project": "big · bbbbbb", "changed_lines": 140, "added": 100, "deleted": 40, "covered_cost": 1.0,
             "availability": {"cost": True, "code_pushed": True, "partial": False}}]}
        result = goals.measure("cost_per_1k_lines", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-31",
                               rows, available, git)
        self.assertEqual(result["project_names"], ["big", "small"])
        self.assertEqual(result["counted_share"], 20.0)

    def test_locally_priced_cost_is_not_labeled_estimated(self):
        priced = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 1.0, 10)], cost_approx=True)]
        guessed = [session("codex", "2", [day("gpt-5.6-sol", "2026-10-02", 1.0, 10)], token_estimate=True)]
        scope = {"kind": "all", "key": ""}
        self.assertFalse(goals.measure("spend", scope, "2026-10-01", "2026-10-31", priced, available)["estimated"])
        self.assertTrue(goals.measure("spend", scope, "2026-10-01", "2026-10-31", guessed, available)["estimated"])

    def test_series_reports_value_to_date(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 2.0, 10),
                                       day("gpt-5.6-sol", "2026-10-04", 3.0, 10)])]
        points = goals.series("spend", {"kind": "all", "key": ""}, "2026-10-01", "2026-10-04", rows, available)
        self.assertEqual([p["value"] for p in points], [0.0, 2.0, 2.0, 5.0])

    def test_repeating_goal_rolls_forward_and_keeps_history(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-10", 30.0, 10)])]
        raw = {"items": [goal("spend", 20, start="2026-09-01", end="2026-09-30", repeat=True)]}
        data = goals.project(raw, rows, AGENTS, available, None, set(), {}, TODAY)
        item = data["goals"][0]
        self.assertEqual((item["start"], item["end"]), ("2026-10-01", "2026-10-31"))
        self.assertEqual(item["history"], [{"start": "2026-09-01", "end": "2026-09-30",
                                            "value": 30.0, "status": "missed"}])
        self.assertEqual(item["guardrail"]["metric"], "output_per_dollar")

    def test_model_choices_rank_main_models_by_recent_spend(self):
        rows = [session("codex", str(i), [day(f"model-{i}", "2026-10-02", float(i), 10)]) for i in range(1, 21)]
        inventory = goals.model_inventory(rows)
        pinned_key = goals.model_key("codex", "model-1")
        pinned = goal("spend", 1, scope={"kind": "model", "key": pinned_key})
        choices = goals._model_choices(rows, inventory, AGENTS, [pinned], TODAY)
        self.assertEqual(choices[0]["label"], "Codex · model-20")
        self.assertEqual(choices[0]["recent_spend"], 20.0)
        self.assertEqual(len(choices), goals.MODEL_CHOICES + 1)
        self.assertEqual(choices[-1]["key"], pinned_key)

    def test_model_scope_counts_only_that_models_cost_and_output(self):
        rows = [session("codex", "1", [day("gpt-5.6-sol", "2026-10-02", 3.0, 300),
                                       day("gpt-6-luna", "2026-10-02", 1.0, 900)])]
        scope = {"kind": "model", "key": goals.model_key("codex", "gpt-6-luna"),
                 "runtime": "codex", "model": "gpt-6-luna"}
        self.assertEqual(goals.measure("spend", scope, "2026-10-01", "2026-10-31", rows, available)["value"], 1.0)
        self.assertEqual(goals.measure("output_per_dollar", scope, "2026-10-01", "2026-10-31", rows,
                                       available)["value"], 900.0)
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("frontier_share", 50, scope={"kind": "model", "key": scope["key"]}), AGENTS)

    def test_editing_a_repeating_goal_keeps_its_series_anchor(self):
        raw = {"items": [goal("spend", 20, start="2026-09-01", end="2026-09-30", repeat=True)]}
        edit = {k: v for k, v in raw["items"][0].items() if k not in ("type", "created_at")}
        edit.update(start="2026-10-01", end="2026-10-31", target=25)
        store = goals.apply_action(raw, {"operation": "save", "id": "a" * 32, "goal": edit}, AGENTS, set(), TODAY)
        self.assertEqual((store["items"][0]["start"], store["items"][0]["target"]), ("2026-09-01", 25.0))


def coaching_rows():
    def run(model, day_, cost, output):
        return day(model, day_, cost, output, executions=30, input_tokens=output * 50, cache_read_tokens=output * 10)
    return [
        session("codex", "s1", [run("gpt-5.6-sol", "2026-10-02", 80.0, 200000)], title="Big refactor", reasoning_effort="xhigh"),
        session("codex", "s2", [run("gpt-5.6-sol", "2026-10-03", 10.0, 50000)], title="Quick fix", reasoning_effort="medium"),
        session("codex", "s3", [run("gpt-6-sol", "2026-10-04", 4.0, 100000)], title="Docs", reasoning_effort="medium"),
    ]


class GoalDetailAndCoachingTests(unittest.TestCase):
    def detail(self, metric="spend", rows=None, scope=None):
        raw = {"items": [goal(metric, 100, scope=scope or {"kind": "agent", "key": "codex"})]}
        detail = goals.goal_detail(raw, "a" * 32, rows or coaching_rows(), AGENTS, available, None, set(), {}, TODAY)
        detail["coaching"] = goal_coaching.coach(detail)
        return detail

    def test_detail_reports_daily_trend_drivers_and_contributors(self):
        detail = self.detail()
        self.assertEqual(len(detail["trend"]), 15)
        self.assertEqual([m["model"] for m in detail["drivers"]["by_model"]], ["gpt-5.6-sol", "gpt-6-sol"])
        self.assertEqual([e["effort"] for e in detail["drivers"]["by_effort"]], ["medium", "xhigh"])
        self.assertAlmostEqual(detail["drivers"]["cache_hit"], 20.0)
        self.assertEqual([c["title"] for c in detail["contributors"]["items"]], ["Big refactor", "Quick fix", "Docs"])
        with self.assertRaises(ValueError):
            goals.goal_detail({"items": []}, "b" * 32, [], AGENTS, available, None, set(), {}, TODAY)

    def test_coaching_estimates_use_the_users_own_rates(self):
        tips = {tip["id"]: tip for tip in self.detail()["coaching"]}
        # Half of $90 on gpt-5.6-sol at $40 instead of $360 per 1M output tokens.
        self.assertEqual(tips["model_mix"]["estimate"], 40.0)
        # Half of $80 at xhigh, at $0.33 instead of $2.67 per execution for the same model.
        self.assertEqual(tips["effort"]["estimate"], 35.0)
        self.assertIn("Assumes", tips["effort"]["estimate_basis"])
        # Three sessions in total: "your top 3 sessions" says nothing.
        self.assertNotIn("concentration", tips)

    def test_coaching_without_a_lever_says_so(self):
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 1.0, 100000, cache_read_tokens=0)])]
        tips = self.detail("context_load", rows)["coaching"]
        self.assertEqual([tip["id"] for tip in tips], ["steady"])

    def test_cost_per_1k_lines_coaching_flags_low_repository_coverage(self):
        detail = {"goal": {"metric": "cost_per_1k_lines", "measurement": {"counted_share": 3.0}},
                  "drivers": {"by_repository": []}, "contributors": {}}
        self.assertEqual([tip["id"] for tip in goal_coaching.coach(detail)], ["coverage"])


class GoalApplicationTests(unittest.TestCase):
    def evidence(self, cross):
        return [], set(), {}, lambda scope, start, end: {"ok": False}

    def request(self, method, path, body=None, headers=None):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = json.loads(response.read())
            status = response.status
            conn.close()
            return payload, status
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_goal_and_budget_writes_preserve_each_other(self):
        draft = {k: v for k, v in goal("spend", 10).items() if k not in ("id", "type", "created_at")}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", path), \
                    mock.patch.object(meter, "cross_session", return_value={}), \
                    mock.patch.object(meter, "goal_evidence", side_effect=self.evidence):
                budget = meter.set_budget_settings({"allocations": {"codex": 200}, "thresholds": [80, 90, 100],
                                                    "native_notifications": True})
                saved = meter.set_goals_action({"operation": "save", "goal": draft})
                again = meter.set_budget_settings({"allocations": {"codex": 300}, "thresholds": [80, 90, 100],
                                                   "native_notifications": True})
            stored = json.loads(Path(path).read_text())
        self.assertTrue(budget["ok"] and saved["ok"] and again["ok"])
        self.assertEqual(len(stored["goals"]["items"]), 1)
        self.assertEqual(stored["budgets"]["allocations"]["codex"], 300.0)

    def test_invalid_goal_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "cross_session", return_value={}), \
                    mock.patch.object(meter, "goal_evidence", side_effect=self.evidence):
                result = meter.set_goals_action({"operation": "save", "goal": {"metric": "tokens"}}, str(path))
            self.assertFalse(result["ok"])
            self.assertFalse(path.exists())

    def test_goals_route_forwards_preview_fields_and_requires_action_token(self):
        with mock.patch.object(meter, "goals_state", return_value={"ok": True}) as state, \
                mock.patch.object(meter, "set_goals_action", return_value={"ok": True}) as action:
            payload, status = self.request("GET", "/goals?preview=1&metric=spend&scope_kind=all&period=month")
            self.request("GET", "/goals?goal=" + "a" * 32)
            denied, denied_status = self.request("POST", "/goals", json.dumps({"operation": "end"}),
                                                 {"Content-Type": "application/json"})
            allowed, allowed_status = self.request("POST", "/goals", json.dumps({"operation": "end", "id": "x"}),
                                                   {"Content-Type": "application/json",
                                                    "X-Token-Meter-Action": meter._ACTION_TOKEN})
        self.assertEqual((payload, status), ({"ok": True}, 200))
        self.assertEqual(state.call_args_list[0].args[0]["metric"], "spend")
        self.assertEqual(state.call_args_list[1].args[0]["goal"], "a" * 32)
        self.assertEqual(state.call_args_list[0].args[0]["period"], "month")
        self.assertEqual(denied_status, 403)
        self.assertEqual((allowed, allowed_status), ({"ok": True}, 200))
        action.assert_called_once_with({"operation": "end", "id": "x"})

    def test_dashboard_native_and_tray_sources_expose_goals(self):
        root = Path(meter.__file__).parent
        page = (root / "page.html").read_text()
        swift = (root / "menubar" / "TokenMeterMenuBar.swift").read_text()
        tray = (root / "menubar" / "token_meter_tray.py").read_text()
        for marker in ("id=efficiency-goals", "if(h==='goals')setHashRoute('efficiency-goals'",
                       "data-goal-new=spend", "data-goal-new=cost_per_1k_lines",
                       "data-goal-starter-add", "data-goal-undo", "id=goals-repeat", "id=goal-detail",
                       "h.startsWith('efficiency-goals/')", "class=goalTitleLink", "aria-pressed=${goalDraft.option===id"):
            self.assertIn(marker, page)
        self.assertNotIn("id=tab-goals", page)
        self.assertIn("goalsItem.submenu = makeGoalsMenu()", swift)
        self.assertIn('URL(string: "http://127.0.0.1:8722/#efficiency-goals")', swift)
        self.assertIn("def goal_menu_title(goal):", tray)
        self.assertIn('self.open_url("#efficiency-goals", include_pinned_session=False)', tray)


class GitWindowTests(unittest.TestCase):
    def test_explicit_window_matches_fixed_range_and_rejects_bad_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.datetime(2026, 10, 15, 12).timestamp()
            service = GitDeliveryService(os.path.join(directory, "ledger.sqlite3"), now=lambda: now)
            spend = [{"project": "app · abc123", "day": "2026-10-14", "covered_cost": 3.0, "cost_available": True}]
            fixed = service.query("", "7", spend, ["app · abc123"])
            window = service.query_window("", "2026-10-09", "2026-10-15", spend, ["app · abc123"])
            self.assertEqual(fixed["overall"], window["overall"])
            self.assertFalse(service.query_window("", "2026-10-15", "2026-10-01", spend, [])["ok"])


if __name__ == "__main__":
    unittest.main()
