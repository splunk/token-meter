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
        self.assertEqual(share, {"lower_5": 3.0, "raise_5": 13.0, "hold": 8.0})
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


class WorkGoalTests(unittest.TestCase):
    FEATURE = {"kind": "work_type", "key": "feature"}

    @staticmethod
    def query(days):
        """A Work evidence query: day counts for one kind of work, filtered to the window."""
        def run(scope, start, end):
            if scope.get("kind") != "work_type":
                return {"ok": False}
            return {"ok": True, "days": {d: c for d, c in days.get(scope["key"], {}).items() if start <= d <= end}}
        return run

    def store(self, *items):
        return {"items": [dict(item, id=f"{index + 1:032x}", created_at=f"2026-10-0{index + 1}T00:00:00+00:00")
                          for index, item in enumerate(items)]}

    def project(self, store, days, today=TODAY):
        return goals.project(store, [], AGENTS, available, self.query(days), set(), {}, today, detail=False)["goals"]

    def test_work_goals_need_a_kind_of_work_and_agent_human_needs_a_range(self):
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("work_pushback", 20), AGENTS)  # all-apps scope is not a kind of work
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("work_pushback", 20, scope={"kind": "work_type", "key": "unclear"}), AGENTS)
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("agent_per_human", 10, "at_least", scope=self.FEATURE), AGENTS)
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("agent_per_human", 30, "between", scope=self.FEATURE, target_high=20), AGENTS)
        clean = goals.normalize_goal(goal("agent_per_human", 15, "between", scope=self.FEATURE, target_high=30), AGENTS)
        self.assertEqual((clean["scope"], clean["target"], clean["target_high"]), (self.FEATURE, 15.0, 30.0))
        self.assertNotIn("target_high", goals.normalize_goal(goal("work_pushback", 20, scope=self.FEATURE), AGENTS))

    def test_work_goals_accept_all_kinds_of_work_and_chosen_models(self):
        key = goals.model_key("codex", "gpt-5.6")
        everything = goals.normalize_goal(goal("work_pushback", 20, scope={"kind": "work_type", "key": ""}), AGENTS)
        self.assertEqual(everything["scope"], {"kind": "work_type", "key": ""})
        chosen = goals.normalize_goal(goal("work_pushback", 20, scope={"kind": "work_type", "key": "debug",
                                                                       "keys": [key, key]}), AGENTS)
        self.assertEqual(chosen["scope"], {"kind": "work_type", "key": "debug", "keys": [key]})
        with self.assertRaises(ValueError):
            goals.normalize_goal(goal("work_pushback", 20, scope={"kind": "work_type", "key": "", "keys": ["x"]}), AGENTS)

    def test_a_range_answers_only_to_the_pushback_goal_with_the_same_kind_and_models(self):
        key = goals.model_key("codex", "gpt-5.6")
        rows = [session("codex", "s1", [day("gpt-5.6", "2026-10-05", 1.0, 10)])]
        every = {"kind": "work_type", "key": ""}
        mine = {"kind": "work_type", "key": "", "keys": [key]}
        ratio = dict(goal("agent_per_human", 15, "between", scope=mine, target_high=30), type="metric",
                     target_option="custom")
        other = dict(goal("work_pushback", 20, scope=every), type="metric", target_option="custom")
        same = dict(goal("work_pushback", 20, scope=mine), type="metric", target_option="custom")
        counts = {"labeled": 40, "pushback": 4, "human": 40, "agent": 800}
        query = lambda scope, start, end: {"ok": True, "days": {"2026-10-05": counts}}
        run = lambda store: next(g for g in goals.project(store, rows, AGENTS, available, query, set(), {}, TODAY,
                                                          detail=False)["goals"] if g["metric"] == "agent_per_human")
        alone = run(self.store(other, ratio))
        self.assertEqual(alone["status_reason"], "Needs a pushback goal for the same kind of work and models")
        linked = run(self.store(other, same, ratio))
        self.assertEqual((linked["status"], linked["measurement"]["guard"]["goal_id"]), ("on_track", "2".zfill(32)))
        self.assertEqual(linked["scope_label"], "All kinds of work · Codex · gpt-5.6")

    def test_measure_sums_work_day_counts_and_needs_twenty_samples_for_complete(self):
        days = {"feature": {"2026-10-02": {"labeled": 15, "pushback": 3, "human": 10, "agent": 150},
                            "2026-10-03": {"labeled": 5, "pushback": 1, "human": 10, "agent": 250},
                            "2026-09-30": {"labeled": 99, "pushback": 99, "human": 99, "agent": 99}}}
        pushback = goals.measure("work_pushback", self.FEATURE, "2026-10-01", "2026-10-31", [], available,
                                 self.query(days))
        self.assertEqual((pushback["value"], pushback["complete"], pushback["estimated"]), (20.0, True, True))
        ratio = goals.measure("agent_per_human", self.FEATURE, "2026-10-01", "2026-10-02", [], available,
                              self.query(days))
        self.assertEqual((ratio["value"], ratio["complete"], ratio["reason"]),
                         (15.0, False, "Fewer than 20 human turns with model calls"))
        off = goals.measure("work_pushback", self.FEATURE, "2026-10-01", "2026-10-31", [], available,
                            lambda *_: {"ok": False, "reason": "Turn on Work insights to measure Work goals"})
        self.assertEqual((off["value"], off["reason"]), (None, "Turn on Work insights to measure Work goals"))
        trend = goals.series("agent_per_human", self.FEATURE, "2026-10-01", "2026-10-03", [], available,
                             self.query(days))
        self.assertEqual([point["value"] for point in trend], [None, 15.0, 20.0])

    def test_range_options_band_the_baseline(self):
        options = {o["id"]: o for o in goals.target_options("agent_per_human", 20.0)}
        self.assertEqual((options["around"]["target"], options["around"]["target_high"]), (16.0, 24.0))
        self.assertEqual(options["raise"]["comparison"], "between")
        self.assertEqual(goals.target_options("agent_per_human", 0), [])
        pushback = {o["id"]: o["target"] for o in goals.target_options("work_pushback", 25.0)}
        self.assertEqual((pushback["lower_5"], pushback["hold"]), (20.0, 25.0))

    def test_agent_human_counts_only_while_pushback_stays_under_its_goal(self):
        ratio = dict(goal("agent_per_human", 15, "between", scope=self.FEATURE, target_high=30), type="metric",
                     target_option="custom")
        ceiling = dict(goal("work_pushback", 20, scope=self.FEATURE), type="metric", target_option="custom")
        calm = {"feature": {"2026-10-05": {"labeled": 40, "pushback": 4, "human": 40, "agent": 800}}}
        rough = {"feature": {"2026-10-05": {"labeled": 40, "pushback": 16, "human": 40, "agent": 800}}}
        alone = self.project(self.store(ratio), calm)[0]
        self.assertEqual((alone["status"], alone["status_reason"]),
                         ("no_result", "Needs a pushback goal for the same kind of work and models"))
        on_track = next(g for g in self.project(self.store(ceiling, ratio), calm) if g["metric"] == "agent_per_human")
        self.assertEqual((on_track["status"], on_track["measurement"]["guard"]["value"]), ("on_track", 10.0))
        self.assertEqual(on_track["scope_label"], "Feature work")
        at_risk = next(g for g in self.project(self.store(ceiling, ratio), rough) if g["metric"] == "agent_per_human")
        self.assertEqual((at_risk["status"], at_risk["status_reason"]), ("at_risk", "Pushback is above its goal"))
        ended = datetime.date(2026, 11, 2)
        missed = next(g for g in self.project(self.store(ceiling, ratio), rough, ended) if g["metric"] == "agent_per_human")
        met = next(g for g in self.project(self.store(ceiling, ratio), calm, ended) if g["metric"] == "agent_per_human")
        self.assertEqual((missed["status"], met["status"]), ("missed", "met"))
        outside = {"feature": {"2026-10-05": {"labeled": 40, "pushback": 4, "human": 40, "agent": 1600}}}
        wide = next(g for g in self.project(self.store(ceiling, ratio), outside, ended) if g["metric"] == "agent_per_human")
        self.assertEqual((wide["status"], wide["status_reason"]), ("missed", "Above the range"))  # 40 : 1 vs 15–30

    def test_project_lists_kinds_of_work_and_work_detail_has_no_session_drivers(self):
        data = goals.project({}, [], AGENTS, available, self.query({}), set(), {}, TODAY, detail=False)
        self.assertEqual(data["work_types"][:2], [{"id": "", "label": "All kinds of work"},
                                                  {"id": "feature", "label": "Feature work"}])
        self.assertNotIn("unclear", [item["id"] for item in data["work_types"]])
        self.assertEqual(data["metrics"]["agent_per_human"]["direction"], "between")
        ceiling = dict(goal("work_pushback", 20, scope=self.FEATURE), type="metric", target_option="custom")
        days = {"feature": {"2026-10-05": {"labeled": 40, "pushback": 4, "human": 40, "agent": 800}}}
        detail = goals.goal_detail(self.store(ceiling), "1".zfill(32), [], AGENTS, available, self.query(days),
                                   set(), {}, TODAY)
        self.assertEqual((detail["drivers"], detail["contributors"]["items"]), ({}, []))
        self.assertEqual(detail["goal"]["guardrail"]["metric"], "agent_per_human")
        self.assertEqual(detail["trend"][-1]["value"], 10.0)


class GoalDefaultTests(unittest.TestCase):
    ROWS = [session("codex", "1", [day("gpt-5.6-sol", "2026-09-10", 30.0, 3000, input_tokens=9000)])]

    def seed(self, raw):
        defaults = goals.default_goals(self.ROWS, available, None, TODAY)
        return goals.apply_action(raw, {"operation": "seed"}, AGENTS, set(), TODAY, defaults=defaults)

    def test_seed_adds_simple_monthly_defaults_for_each_page_once(self):
        store = self.seed({})
        self.assertEqual({g["metric"] for g in store["items"]}, {"spend", "output_per_dollar", "context_load"})
        self.assertTrue(all(g["period"] == "month" and g["repeat"] and g["target_option"] == "hold"
                            and g["scope"]["kind"] == "all" and g["title"] is None for g in store["items"]))
        self.assertEqual(sorted(store["seeded"]),
                         ["efficiency:context_load", "efficiency:output_per_dollar", "spend:spend"])
        again = self.seed(store)
        self.assertEqual([g["id"] for g in again["items"]], [g["id"] for g in store["items"]])

    def test_deleted_default_is_not_recreated(self):
        store = self.seed({})
        spend_id = next(g["id"] for g in store["items"] if g["metric"] == "spend")
        store = goals.apply_action(store, {"operation": "delete", "id": spend_id}, AGENTS, set(), TODAY)
        self.assertNotIn("spend", {g["metric"] for g in self.seed(store)["items"]})

    def test_seed_skips_pages_that_already_have_goals(self):
        store = self.seed({"items": [goal("reasoning_ratio", 10, ended_at="2026-10-05")]})
        self.assertEqual(sorted(g["metric"] for g in store["items"]), ["reasoning_ratio", "spend"])
        self.assertIn("efficiency:output_per_dollar", store["seeded"])

    def test_existing_pages_are_recorded_so_emptying_them_later_adds_nothing(self):
        raw = {"items": [goal("spend", 10), goal("cost_per_1k_lines", 20, id="b" * 32),
                         goal("output_per_dollar", 100, "at_least", id="c" * 32)]}
        self.assertTrue(goals.project(raw, self.ROWS, AGENTS, available, None, set(), {}, TODAY,
                                      detail=False)["seed_pending"])
        store = self.seed(raw)
        store = goals.apply_action(store, {"operation": "delete", "id": "a" * 32}, AGENTS, set(), TODAY)
        self.assertNotIn("spend", {g["metric"] for g in self.seed(store)["items"]})

    def test_unavailable_baseline_waits_and_pending_reports_it(self):
        store = self.seed({})
        self.assertNotIn("git:cost_per_1k_lines", store["seeded"])
        projected = goals.project(store, self.ROWS, AGENTS, available, None, set(), {}, TODAY, detail=False)
        self.assertTrue(projected["seed_pending"])
        self.assertNotIn("starters", projected)
        done = goals.project({**store, "seeded": [*store["seeded"], "git:cost_per_1k_lines"]}, self.ROWS,
                             AGENTS, available, None, set(), {}, TODAY, detail=False)
        self.assertFalse(done["seed_pending"])

    def test_saves_and_deletes_keep_the_seeded_record(self):
        store = self.seed({})
        draft = {k: v for k, v in goal("tokens", 10).items() if k not in ("id", "type", "created_at")}
        saved = goals.apply_action(store, {"operation": "save", "goal": draft}, AGENTS, set(), TODAY)
        removed = goals.apply_action(saved, {"operation": "delete", "id": saved["items"][0]["id"]},
                                     AGENTS, set(), TODAY)
        self.assertEqual(saved["seeded"], store["seeded"])
        self.assertEqual(removed["seeded"], store["seeded"])

    def test_store_keeps_only_known_seeded_keys(self):
        store = goals.normalize_store({"items": [], "seeded": ["spend:spend", "bogus", 4, "spend:spend"]}, AGENTS)
        self.assertEqual(store["seeded"], ["spend:spend"])

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
        self.assertEqual(result["project_names"], ["big · bbbbbb", "small · aaaaaa"])
        self.assertEqual(result["counted_share"], 20.0)

    def test_repository_preview_reports_omitted_and_unpaired_evidence(self):
        project_rows = [{"project": f"repo-{index} · abcdef", "changed_lines": 100,
                         "covered_cost": 1.0, "availability": {"cost": True, "code_pushed": True}}
                        for index in range(9)]
        project_rows.append({"project": "missing · abcdef", "covered_cost": 20.0,
                             "availability": {"cost": True, "code_pushed": False}})
        git = lambda scope, start, end: {"ok": True, "project_rows": project_rows, "stale": True}
        detail = goals.drivers("cost_per_1k_lines", {"kind": "all", "key": ""},
                              "2026-10-01", "2026-10-15", [], available, AGENTS, git)
        self.assertEqual(len(detail["by_repository"]), 8)
        self.assertEqual(detail["repository_coverage"]["omitted"], 1)
        self.assertEqual(detail["repository_coverage"]["rows"][-1]["availability"],
                         {"cost": True, "code_pushed": False, "partial": False})
        self.assertTrue(detail["repository_coverage"]["stale"])
        measured = goals.measure("cost_per_1k_lines", {"kind": "all", "key": ""},
                                 "2026-10-01", "2026-10-15", [], available, git)
        self.assertEqual(measured["value"], 10.0)
        self.assertEqual(measured["projects"], 9)

    def test_hold_uses_a_measured_zero_and_preserves_metric_direction(self):
        self.assertEqual(goals.target_options("spend", 0),
                         [{"id": "hold", "label": "Hold current level", "comparison": "at_most", "target": 0}])
        for metric in ("output_per_dollar", "context_load", "reasoning_ratio", "frontier_share", "tokens"):
            hold = next(option for option in goals.target_options(metric, 10) if option["id"] == "hold")
            self.assertEqual(hold["target"], 10)
            self.assertEqual(hold["comparison"], "at_least" if metric == "output_per_dollar" else "at_most")
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-09-02", 0, 100)])]
        reference = goals.baseline("spend", {"kind": "all", "key": ""}, "month",
                                   datetime.date(2026, 10, 1), datetime.date(2026, 10, 31),
                                   rows, available, None, TODAY)
        self.assertEqual((reference["source"], reference["value"]), ("previous", 0))

    def test_model_lookup_includes_unpriced_models_outside_the_shortlist(self):
        rows = [session("codex", str(index), [day(f"model-{index}", "2026-10-02", 1, 100)])
                for index in range(16)]
        rows.append(session("claude", "unpriced", [day("unpriced-model", "2026-10-02", 0, 100)],
                            availability={"cost": False, "tokens": True}))
        choices = goals.model_choices(rows, goals.model_inventory(rows), AGENTS, [], TODAY,
                                      runtime="claude", query="unpriced")
        self.assertEqual(len(choices["models"]), 1)
        self.assertEqual(choices["models"][0]["runtime"], "claude")
        self.assertEqual(choices["models"][0]["label"], "Claude · unpriced-model")

    def test_tokens_include_cache_once_and_work_without_prices(self):
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 2, 100,
                                           input_tokens=900, cache_read_tokens=800,
                                           reasoning_tokens=50, reasoning_output_tokens=100,
                                           reasoning_executions=1)]),
                session("claude", "2", [day("unpriced", "2026-10-02", 0, 200, input_tokens=800)],
                        availability={"cost": False, "tokens": True})]
        result = goals.measure("tokens", {"kind": "all", "key": ""},
                               "2026-10-01", "2026-10-15", rows, available)
        self.assertEqual(result["value"], 2000)
        self.assertTrue(result["complete"])
        breakdown = goals.drivers("tokens", {"kind": "all", "key": ""},
                                   "2026-10-01", "2026-10-15", rows, available, AGENTS)
        self.assertEqual(breakdown["token_drivers"]["total"], 2000)
        self.assertEqual([row["share"] for row in breakdown["token_drivers"]["by_model"]], [50, 50])
        contributors = goals.contributors("tokens", {"kind": "all", "key": ""},
                                           "2026-10-01", "2026-10-15", rows, available, AGENTS)
        self.assertEqual(contributors["basis"], "tokens")
        self.assertEqual(contributors["sessions"], 2)

    def test_tokens_keep_partial_counts_and_reject_unsafe_targets(self):
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 0, 100,
                                           input_tokens=0, io_covered_executions=0,
                                           token_covered_executions=1, input_evidence=False,
                                           output_evidence=True)], availability={"tokens": False})]
        result = goals.measure("tokens", {"kind": "all", "key": ""},
                               "2026-10-01", "2026-10-15", rows, available)
        self.assertEqual(result["value"], 100)
        self.assertFalse(result["complete"])
        for target in (True, -1, 1.5, float("nan"), 2 ** 53):
            with self.assertRaises(ValueError):
                goals.normalize_goal(goal("tokens", target), AGENTS)
        saved = goals.normalize_goal(goal("tokens", 3_000_000_001), AGENTS)
        self.assertEqual(saved["target"], 3_000_000_001)
        self.assertIsInstance(saved["target"], int)

    def test_token_projection_and_custom_deadline_share_the_saved_window(self):
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 0, 1000, input_tokens=0)])]
        record = goal("tokens", 3000, period="custom", start="2026-10-01", end="2026-10-30")
        item = goals.project({"items": [record]}, rows, AGENTS, available, None, set(), {}, TODAY)["goals"][0]
        self.assertEqual(item["measurement"]["projected"], 2000)
        self.assertEqual((item["start"], item["end"]), ("2026-10-01", "2026-10-30"))
        self.assertEqual(item["status"], "on_track")
        copy = goals.apply_action({"items": [record]}, {"operation": "save", "goal": {
            **record, "start": "2026-10-15", "end": "2026-11-13", "target_option": "custom"}},
            AGENTS, {}, TODAY)["items"]
        self.assertNotEqual(copy[0]["id"], copy[1]["id"])
        self.assertEqual(copy[0]["end"], "2026-10-30")
        self.assertEqual(copy[1]["target"], 3000)

    def test_token_pace_is_a_whole_number_without_float_drift(self):
        rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 0, 1000, input_tokens=0)])]
        exact = goal("tokens", 2000, period="custom", start="2026-10-01", end="2026-10-30")
        item = goals.project({"items": [exact]}, rows, AGENTS, available, None, set(), {}, TODAY)["goals"][0]
        self.assertIsInstance(item["measurement"]["projected"], int)
        self.assertEqual(item["measurement"]["projected"], 2000)
        self.assertEqual(item["status"], "on_track")
        uneven = goal("tokens", 3000, period="custom", start="2026-10-01", end="2026-10-31")
        item = goals.project({"items": [uneven]}, rows, AGENTS, available, None, set(), {}, TODAY)["goals"][0]
        self.assertEqual(item["measurement"]["projected"], 2067)
        start, end = datetime.date(2026, 10, 1), datetime.date(2026, 10, 31)
        prior = goals.baseline("tokens", {"kind": "all", "key": ""}, "month", start, end, rows,
                               available, None, TODAY)
        self.assertEqual(prior["source"], "last_90")
        self.assertIsInstance(prior["value"], int)
        self.assertEqual(prior["value"], 344)

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
        choices = goals.model_choices(rows, inventory, AGENTS, [pinned], TODAY)["models"]
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

    def test_multiple_models_sum_selected_evidence_once_across_runtimes(self):
        rows = [session("codex", "1", [day("shared-model", "2026-10-02", 1, 100, input_tokens=200),
                                       day("other-model", "2026-10-02", 90, 90000)]),
                session("claude", "2", [day("shared-model", "2026-10-02", 3, 900, input_tokens=600)]),
                session("codex", "3", [day("excluded-model", "2026-10-02", 20, 1000)])]
        keys = [goals.model_key("codex", "shared-model"), goals.model_key("claude", "shared-model")]
        scope = {"kind": "models", "keys": keys}
        record = goal("output_per_dollar", 80, comparison="at_least", scope=scope)
        active = {goals.session_key(row) for row in rows}
        detail = goals.goal_detail({"items": [record]}, record["id"], rows, AGENTS, available,
                                   None, active, {}, TODAY)
        self.assertEqual(detail["goal"]["measurement"]["value"], 250)
        self.assertEqual(detail["goal"]["measurement"]["active"], 2)
        self.assertEqual(detail["goal"]["scope"]["keys"], sorted(keys))
        self.assertEqual(detail["contributors"]["sessions"], 2)
        self.assertEqual(sum(row["cost"] for row in detail["contributors"]["items"]), 4)
        self.assertEqual(len(detail["drivers"]["by_model"]), 2)
        self.assertEqual(len(detail["drivers"]["by_agent"]), 2)
        tokens = goals.project({"items": [goal("tokens", 5000, scope=scope)]}, rows, AGENTS,
                               available, None, active, {}, TODAY)
        self.assertEqual(tokens["goals"][0]["measurement"]["value"], 1800)
        native = goals.native_summary(tokens, TODAY)["items"][0]
        self.assertEqual((native["scope_kind"], native["model_count"]), ("models", 2))

    def test_model_set_validation_and_missing_saved_selection(self):
        rows = [session("codex", "1", [day("known-model", "2026-10-02", 2, 200)])]
        known = goals.model_key("codex", "known-model")
        missing = goals.model_key("claude", "missing-model")
        record = goal("spend", 10, scope={"kind": "models", "keys": [missing, known, known]})
        normalized = goals.normalize_goal(record, AGENTS)
        self.assertEqual(normalized["scope"]["keys"], sorted([known, missing]))
        data = goals.project({"items": [record]}, rows, AGENTS, available, None, set(), {}, TODAY)
        self.assertEqual(data["goals"][0]["status"], "no_result")
        self.assertIsNone(data["goals"][0]["measurement"]["value"])
        detail = goals.goal_detail({"items": [record]}, record["id"], rows, AGENTS, available,
                                   None, set(), {}, TODAY)
        self.assertTrue(all(point["value"] is None for point in detail["trend"]))
        self.assertEqual(detail["contributors"]["items"], [])
        preview = goals.preview({"metric": "spend", "scope_kind": "models",
                                 "scope_keys": json.dumps([known, missing]), "period": "month"},
                                AGENTS, goals.model_inventory(rows), rows, available, None, TODAY)
        self.assertIsNone(preview["baseline"]["value"])
        with self.assertRaises(ValueError):
            goals.apply_action({}, {"operation": "save", "goal": record}, AGENTS, {known}, TODAY)
        saved = goals.apply_action({"items": [record]}, {"operation": "save", "id": record["id"],
                                  "goal": {**record, "target": 20}}, AGENTS, {known}, TODAY)
        self.assertEqual(saved["items"][0]["target"], 20)
        for keys in ([], ["invalid"], [known] * 33):
            with self.assertRaises(ValueError):
                goals.normalize_goal(goal("spend", 10, scope={"kind": "models", "keys": keys}), AGENTS)
        for metric in ("cost_per_1k_lines", "frontier_share"):
            with self.assertRaises(ValueError):
                goals.normalize_goal(goal(metric, 10, scope={"kind": "models", "keys": [known, missing]}), AGENTS)

    def test_delivery_contributors_reconcile_all_repositories_not_sessions(self):
        projects = [{"project": f"same-name · {index:06d}", "changed_lines": 100,
                     "covered_cost": 2, "availability": {"cost": True, "code_pushed": True}}
                    for index in range(9)]
        projects.append({"project": "zero · abcdef", "changed_lines": 0, "covered_cost": 1,
                         "availability": {"cost": True, "code_pushed": True}})
        projects.append({"project": "spend-only · abcdef", "covered_cost": 100,
                         "availability": {"cost": True, "code_pushed": False}})
        git = lambda scope, start, end: {"ok": True, "project_rows": projects,
                                        "coverage": {"selected_repositories": 11, "codes": ["scan_limited"]}}
        record = goal("cost_per_1k_lines", 30)
        rows = [session("codex", "outside", [day("model", "2026-10-02", 500, 100)])]
        detail = goals.goal_detail({"items": [record]}, record["id"], rows, AGENTS, available,
                                   git, set(), {}, TODAY)
        contributors = detail["contributors"]
        self.assertEqual(contributors["repositories"], 10)
        self.assertEqual(len(contributors["items"]), 5)
        self.assertEqual((contributors["total_cost"], contributors["total_lines"]), (19, 900))
        self.assertAlmostEqual(detail["goal"]["measurement"]["value"], 19000 / 900)
        self.assertEqual(contributors["items"][0]["label"], "same-name · 000000")
        coverage = detail["drivers"]["repository_coverage"]
        self.assertEqual((coverage["included"], coverage["discovered"], coverage["omitted"]), (10, 11, 2))
        self.assertEqual(coverage["limits"], ["scan_limited"])

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

    def test_seed_writes_defaults_once_and_skips_unchanged_writes(self):
        rows = GoalDefaultTests.ROWS
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter, "cross_session", return_value={}), \
                    mock.patch.object(meter, "goal_evidence",
                                      return_value=(rows, set(), {}, lambda scope, start, end: {"ok": False})), \
                    mock.patch.object(meter, "atomic_write_text", wraps=meter.atomic_write_text) as write:
                first = meter.set_goals_action({"operation": "seed"}, path)
                second = meter.set_goals_action({"operation": "seed"}, path)
            stored = json.loads(Path(path).read_text())
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(write.call_count, 1)
        self.assertEqual(len(stored["goals"]["items"]), 3)
        self.assertTrue(first["seed_pending"])

    def test_invalid_goal_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "cross_session", return_value={}), \
                    mock.patch.object(meter, "goal_evidence", side_effect=self.evidence):
                result = meter.set_goals_action({"operation": "save", "goal": {"metric": "unsupported"}}, str(path))
            self.assertFalse(result["ok"])
            self.assertFalse(path.exists())

    def test_goals_route_forwards_preview_fields_and_requires_action_token(self):
        with mock.patch.object(meter, "goals_state", return_value={"ok": True}) as state, \
                mock.patch.object(meter, "set_goals_action", return_value={"ok": True}) as action:
            payload, status = self.request("GET", "/goals?preview=1&metric=spend&scope_kind=all&period=month")
            self.request("GET", "/goals?goal=" + "a" * 32)
            self.request("GET", "/goals?models=1&runtime=codex&query=gpt")
            keys = json.dumps(["a" * 64, "b" * 64], separators=(",", ":"))
            self.request("GET", "/goals?preview=1&metric=spend&scope_kind=models&period=month&scope_keys=" + keys)
            denied, denied_status = self.request("POST", "/goals", json.dumps({"operation": "end"}),
                                                 {"Content-Type": "application/json"})
            allowed, allowed_status = self.request("POST", "/goals", json.dumps({"operation": "end", "id": "x"}),
                                                   {"Content-Type": "application/json",
                                                    "X-Token-Meter-Action": meter._ACTION_TOKEN})
        self.assertEqual((payload, status), ({"ok": True}, 200))
        self.assertEqual(state.call_args_list[0].args[0]["metric"], "spend")
        self.assertEqual(state.call_args_list[1].args[0]["goal"], "a" * 32)
        self.assertEqual({key: state.call_args_list[2].args[0][key] for key in ("models", "runtime", "query")},
                         {"models": "1", "runtime": "codex", "query": "gpt"})
        self.assertEqual(state.call_args_list[0].args[0]["period"], "month")
        self.assertEqual(state.call_args_list[3].args[0]["scope_keys"], keys)
        self.assertEqual(denied_status, 403)
        self.assertEqual((allowed, allowed_status), ({"ok": True}, 200))
        action.assert_called_once_with({"operation": "end", "id": "x"})

    def test_dashboard_native_and_tray_sources_expose_goals(self):
        root = Path(meter.__file__).parent
        page = (root / "page.html").read_text()
        swift = (root / "menubar" / "TokenMeterMenuBar.swift").read_text()
        tray = (root / "menubar" / "token_meter_tray.py").read_text()
        for marker in ("id=efficiency-goals", "if(h==='goals')setHashRoute('efficiency-goals'",
                       "data-goal-new=spend-goals", "data-goal-new=git-goals", "data-goal-new=efficiency-goals",
                       "filter(([id])=>goalHome(id).section===section)",
                       "seedDefaultGoals()", "id=goals-repeat", "id=goal-detail",
                       "id=goals-model-picker", "id=goals-trend-chart", "Lines pushed", "aria-describedby=goals-trend-status",
                       "h.startsWith('efficiency-goals/')", "class=goalTitleLink", "aria-pressed=${goalDraft.option===id"):
            self.assertIn(marker, page)
        self.assertNotIn("id=tab-goals", page)
        self.assertIn("goalsItem.submenu = makeGoalsMenu()", swift)
        self.assertIn('URL(string: "http://127.0.0.1:8722/#efficiency-goals")', swift)
        self.assertIn("def goal_menu_title(goal):", tray)
        self.assertIn('self.open_url("#efficiency-goals", include_pinned_session=False)', tray)


class WorkGoalEvidenceTests(unittest.TestCase):
    def test_goal_evidence_filters_work_counts_by_kind_and_models(self):
        october = {"labeled": 3, "pushback": 1, "human": 2, "agent": 9}
        days = {("feature", "codex", "gpt-5.6"): {"2026-10-02": october, "2026-11-02": october},
                ("feature", "claude", "opus"): {"2026-10-02": {"labeled": 1, "pushback": 0, "human": 1, "agent": 4}},
                ("unclear", "codex", "gpt-5.6"): {"2026-10-03": {"labeled": 2, "pushback": 2, "human": 0, "agent": 0}}}
        codex = goals.model_key("codex", "gpt-5.6")
        with mock.patch.object(meter, "_work_goal_days", return_value=days) as work_days:
            _models, query = meter._prepare_goal_evidence([], mock.Mock())
            window = ("2026-10-01", "2026-10-31")
            feature = query({"kind": "work_type", "key": "feature"}, *window)
            self.assertEqual(feature, {"ok": True, "days": {"2026-10-02": {"labeled": 4, "pushback": 1,
                                                                           "human": 3, "agent": 13}}})
            everything = query({"kind": "work_type", "key": ""}, *window)
            self.assertEqual(sorted(everything["days"]), ["2026-10-02", "2026-10-03"])  # Unclear counts in all work
            chosen = query({"kind": "work_type", "key": "", "keys": [codex]}, *window)
            self.assertEqual(chosen["days"]["2026-10-02"], october)
            self.assertEqual(work_days.call_count, 1)  # computed once per evidence refresh
        with mock.patch.object(meter, "_work_goal_days", return_value=None):
            _models, query = meter._prepare_goal_evidence([], mock.Mock())
            self.assertEqual(query({"kind": "work_type", "key": "feature"}, *window),
                             {"ok": False, "reason": "Turn on Work insights to measure Work goals"})


class GitWindowTests(unittest.TestCase):
    def test_goal_git_query_uses_published_candidates_without_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            service = GitDeliveryService(os.path.join(directory, "ledger.sqlite3"))
            rows = [session("codex", "1", [day("gpt-6-sol", "2026-10-02", 3.0, 100)],
                            project=directory)]
            label = Path(directory).name + " · " + service.project_suffix(directory)
            candidate = {"project": label, "root": directory, "repo_key": "a" * 64}
            with mock.patch.object(meter, "cached_git_delivery_candidates", return_value=(candidate,)), \
                    mock.patch.object(meter, "git_delivery_service", return_value=service), \
                    mock.patch.object(meter, "git_delivery_candidates", side_effect=AssertionError("fresh discovery")):
                _models, query = meter._prepare_goal_evidence(rows, service)
                result = query({"kind": "all", "key": ""}, "2026-10-01", "2026-10-15")
            self.assertEqual(result["project_rows"][0]["project"], label)
            self.assertEqual(result["project_rows"][0]["covered_cost"], 3.0)
            self.assertTrue(result["stale"])

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
