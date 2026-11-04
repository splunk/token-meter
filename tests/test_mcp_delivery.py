import datetime
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import meter
import token_meter_mcp as server
from token_meter.domain import work as work_domain
from token_meter.mcp import delivery
from token_meter.mcp import schema as delivery_schema
from token_meter.mcp.contracts import MCPQueryError
from token_meter.services import git_delivery


CANARY_PROJECT = "secret-client-repo"
CANARY_PATH = "/Users/someone/code/secret-client-repo"
CANARY_TEXT = "please fix the login bug in auth.py"
CANARY_FILE = "auth.py"
CANARY_KEY = "f00dfacecafebeef"
FORBIDDEN = (CANARY_PROJECT, CANARY_PATH, CANARY_TEXT, CANARY_FILE, CANARY_KEY, "/Users/")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class GitStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.today = datetime.date(2026, 10, 20)
        noon = datetime.datetime(2026, 10, 20, 12).timestamp()
        self.service = git_delivery.GitDeliveryService(
            os.path.join(self.tmp.name, "git.sqlite3"), now=lambda: noon,
        )
        self.repo_key = "r" * 64
        self.service.ledger.map_project(self.service._hash(CANARY_PATH), self.repo_key)
        self.service.ledger.set_repository_coverage(self.repo_key, True, noon)
        for offset, (added, deleted) in enumerate(((120, 30), (40, 10))):
            stamp = (datetime.datetime(2026, 10, 20, 9) - datetime.timedelta(days=offset)).timestamp()
            self.service.ledger.record(self.repo_key, "{:064x}".format(offset + 1), stamp, added, deleted)

    def query(self, range_key="7", cost_available=True):
        label = "{} · abc123".format(CANARY_PROJECT)
        candidates = ({"root": CANARY_PATH, "project": label, "repo_key": self.repo_key},)
        spend = [{"project": label, "day": "2026-10-20", "cost_available": cost_available,
                  "covered_cost": 3.0, "output_available": True, "efficiency_covered_cost": 3.0,
                  "covered_output_tokens": 30_000}]
        models = [{"project": label, "day": "2026-10-20", "model": "claude-opus-4-1",
                   "runtime": "Claude", "reasoning_effort": "high", "covered_cost": 3.0}]
        return self.service.query("", range_key, spend, [label], candidates, model_spend_rows=models)

    def test_summary_matches_the_git_page_totals_without_labels(self):
        payload = self.query()
        result = delivery.git_stats_projection(payload, "7", "summary", now=1)
        self.assertTrue(result["available"])
        self.assertEqual(result["current"]["changed_lines"], payload["overall"]["changed_lines"])
        self.assertEqual(result["current"]["changed_lines"], 200)
        self.assertEqual(result["current"]["commits"], 2)
        self.assertEqual(result["current"]["covered_cost_usd"], 3.0)
        self.assertEqual(result["current"]["spend_per_1k_lines_usd"], 15.0)
        self.assertEqual(result["models"][0]["model"], "claude-opus-4-1")
        self.assertEqual(result["models"][0]["attributed_lines"], 200.0)
        self.assertEqual(result["coverage"]["measured_repositories"], 1)
        text = encoded(result)
        self.assertIn(CANARY_PROJECT, encoded(payload))
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, text)
        self.assertNotIn("project", {key for key in result})
        self.assertNotIn("projects", text)

    def test_unmeasured_evidence_is_null_and_explained_not_zero(self):
        empty = git_delivery.GitDeliveryService(
            os.path.join(self.tmp.name, "empty.sqlite3"), now=lambda: 1_790_000_000,
        )
        payload = empty.query("", "7", [], ["x · 000000"], ())
        result = delivery.git_stats_projection(payload, "7", "summary", now=1)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_measured_repositories")
        for field in ("added_lines", "deleted_lines", "changed_lines", "commits", "covered_cost_usd"):
            self.assertIsNone(result["current"][field], field)
        self.assertFalse(result["current"]["availability"]["code_pushed"])

        uncosted = delivery.git_stats_projection(self.query(cost_available=False), "7", "summary")
        self.assertEqual(uncosted["current"]["changed_lines"], 200)
        self.assertIsNone(uncosted["current"]["covered_cost_usd"])
        self.assertIsNone(uncosted["current"]["spend_per_1k_lines_usd"])

    def test_days_view_pages_with_bound_cursors(self):
        payload = self.query("30")
        first = delivery.git_stats_projection(payload, "30", "days", limit=10)
        self.assertEqual(len(first["days"]), 10)
        self.assertEqual(first["page"]["total"], 30)
        self.assertNotIn("current", first)
        rest = delivery.git_stats_projection(
            payload, "30", "days", cursor=first["page"]["next_cursor"], limit=10,
        )
        self.assertEqual(rest["days"][0]["day"], (self.today - datetime.timedelta(days=19)).isoformat())
        last = rest["days"][-1]
        self.assertGreaterEqual(len(rest["days"]), 1)
        self.assertIn("availability", last)
        with self.assertRaises(MCPQueryError) as wrong_limit:
            delivery.git_stats_projection(payload, "30", "days", cursor=first["page"]["next_cursor"], limit=5)
        self.assertEqual(wrong_limit.exception.code, "invalid_argument")
        payload["days"][-1]["covered_cost"] += 1.5
        moved = delivery.git_stats_projection(payload, "30", "days", cursor=first["page"]["next_cursor"], limit=10)
        self.assertEqual(moved["days"], rest["days"])
        payload["days"].pop()
        with self.assertRaises(MCPQueryError) as stale:
            delivery.git_stats_projection(payload, "30", "days", cursor=first["page"]["next_cursor"], limit=10)
        self.assertEqual(stale.exception.code, "stale_cursor")

    def test_arguments_are_validated(self):
        for kwargs in ({"range_key": "14"}, {"view": "projects"}, {"view": "summary", "cursor": "x"},
                       {"limit": 0}, {"limit": 101}, {"view": "days", "limit": True}):
            with self.assertRaises(MCPQueryError):
                delivery.normalize_git_arguments(**kwargs)
        self.assertEqual(delivery.normalize_git_arguments(), ("7", "summary", 31))

    def test_trace_model_spellings_survive(self):
        payload = self.query()
        payload["model_rows"] = [dict(payload["model_rows"][0], model=name) for name in (
            "claude-opus-4-1[1m]", "gpt-5 (high)", CANARY_PATH + "\n", "x" * 121)]
        result = delivery.git_stats_projection(payload, "7", "summary")
        self.assertEqual([row["model"] for row in result["models"]], ["claude-opus-4-1[1m]", "gpt-5 (high)"])

    def test_ledger_candidates_match_the_watcher_totals(self):
        other_key = "s" * 64
        other_root, worktree = "/Users/someone/code/other", CANARY_PATH + "/.worktrees/w"
        self.service.ledger.map_project(self.service._hash(other_root), other_key)
        self.service.ledger.map_project(self.service._hash(worktree), self.repo_key)
        self.service.ledger.set_repository_coverage(other_key, True, 1)
        self.service.ledger.record(other_key, "{:064x}".format(99),
                                   datetime.datetime(2026, 10, 19, 9).timestamp(), 7, 3)
        rows = ({"project": worktree}, {"project": other_root}, {"project": CANARY_PATH})
        labels = {CANARY_PATH: "a · 1", worktree: "w · 2", other_root: "o · 3"}
        watcher = (
            {"root": CANARY_PATH, "project": "a · 1", "repo_key": self.repo_key,
             "aliases": ({"root": worktree, "project": "w · 2"},)},
            {"root": other_root, "project": "o · 3", "repo_key": other_key},
        )
        spend = [{"project": label, "day": "2026-10-19", "cost_available": True, "covered_cost": 1.0}
                 for label in ("w · 2", "o · 3")]

        def project(candidates):
            payload = self.service.query("", "7", spend, sorted(c["project"] for c in candidates), candidates)
            return delivery.git_stats_projection(payload, "7", "summary")

        with mock.patch.object(meter, "git_delivery_service", return_value=self.service), \
                mock.patch.object(meter, "delivery_project_label", side_effect=labels.get), \
                mock.patch.object(meter.os.path, "isdir", return_value=True):
            ledger = meter.git_delivery_ledger_candidates(rows)
        self.assertEqual(project(ledger), project(watcher))
        self.assertEqual(project(ledger)["current"]["changed_lines"], 210)
        self.assertEqual(project(ledger)["current"]["covered_cost_usd"], 2.0)

    def test_change_stays_neutral(self):
        result = delivery.git_stats_projection(self.query(), "7", "summary")
        text = encoded(result).lower()
        for tone in ("good", "bad", "better", "worse", "improv", "regress", "tone"):
            self.assertNotIn(tone, text)

    def test_mcp_process_reads_ledger_mapping_without_running_git(self):
        runner = mock.Mock(side_effect=AssertionError("git must not run"))
        self.service._runner = runner
        rows = ({"project": CANARY_PATH}, {"project": CANARY_PATH}, {"project": "/nowhere/else"})
        with mock.patch.object(meter, "git_delivery_service", return_value=self.service), \
                mock.patch.object(meter.os.path, "isdir", side_effect=lambda path: path == CANARY_PATH):
            candidates = meter.git_delivery_ledger_candidates(rows)
        self.assertEqual([c["repo_key"] for c in candidates], [self.repo_key])
        with mock.patch.object(meter, "git_delivery_service", return_value=self.service), \
                mock.patch.object(meter.os.path, "isdir", return_value=False):
            self.assertEqual(meter.git_delivery_ledger_candidates(rows), ())
        runner.assert_not_called()

    def test_agent_git_stats_end_to_end_is_content_free(self):
        rows = ({"project": CANARY_PATH},)
        empty_inventory = {"ready": False, "revision": 0}
        with mock.patch.object(meter, "git_delivery_service", return_value=self.service), \
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows,
                                               "at": time.time()}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", empty_inventory), \
                mock.patch.object(meter, "delivery_spend_rows", return_value=[]), \
                mock.patch.object(meter, "delivery_model_spend_rows", return_value=[]), \
                mock.patch.object(meter.os.path, "isdir", return_value=True):
            result = meter.agent_git_stats(range="7")
        self.assertTrue(result["available"])
        self.assertEqual(result["current"]["changed_lines"], 200)
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, encoded(result))


class WorkStatsTests(unittest.TestCase):
    AREAS = [{"name": "Product engineering", "description": "d"}, {"name": "Personal", "description": "d"}]

    def rows(self):
        def row(row_id, cost, model):
            return {"id": row_id, "project": CANARY_PROJECT, "runtime": "Codex", "provider": "codex",
                    "cost": cost, "start": "2026-09-10 10:00", "_day_cost": {"2026-09-10": cost},
                    "model_stats": [{"model": model, "cost": cost, "tokens": 100}],
                    "_work_turn_days": ["2026-09-10"] * 3, "title": CANARY_TEXT, "path": CANARY_PATH}
        return [row("a", 2.0, "gpt-5.6"), row("b", 6.0, "cheap"), row("c", 1.0, "gpt-5.6"),
                row("d", 1.5, "gpt-5.6")]

    def insights(self):
        labels = {
            "a": {"area": "Product engineering", "work_type": "debug", "complexity": "routine",
                  "corrections": 1, "correction_labels": 2},
            "b": {"area": "Personal", "work_type": "docs", "complexity": "complex"},
            "d": {"area": "Product engineering", "work_type": "debug", "complexity": "routine"},
        }
        prices = {"gpt-5.6": 10.0, "cheap": 1.0}
        insights = work_domain.build_work_insights(
            self.rows(), labels, lambda ident: ident.split("\0")[0], self.AREAS,
            lambda m, p: prices.get(m), today="2026-09-30")
        insights["recommendations"][0]["note"] = CANARY_TEXT
        insights["recommendations"].append({"kind": "free_text", "text": CANARY_TEXT, "file": CANARY_FILE})
        insights["filters"]["projects"].append(CANARY_PROJECT)
        insights["allocation"][-1]["spend"][CANARY_FILE] = 9.0
        return insights

    def status(self):
        return {"supported": True, "enabled": True, "paused": False, "labels": 2, "pending": 1}

    def test_every_view_is_allowlisted_and_content_free(self):
        insights = self.insights()
        self.assertIn(CANARY_PROJECT, encoded(insights))
        for view in delivery.WORK_VIEWS:
            result = delivery.work_stats_projection(insights, self.status(), "6", "", view, now=1)
            self.assertTrue(result["available"], view)
            text = encoded(result)
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, text, view)
            self.assertNotIn("recommendations", result)
            self.assertNotIn("filters", result)

    def test_summary_matches_the_work_page_aggregate(self):
        insights = self.insights()
        result = delivery.work_stats_projection(insights, self.status(), "6", "", "summary")
        areas = {row["area"]: row for row in result["areas"]}
        self.assertEqual(areas["Product engineering"]["requests"], 6)
        self.assertEqual(areas["Personal"]["spend_usd"], 6.0)
        self.assertEqual(result["coverage"]["sessions"], insights["coverage"]["sessions"])
        self.assertEqual(result["coverage"]["labeled_sessions"], 3)
        types = {row["work_type"] for row in result["work_types"]}
        self.assertEqual(types, {"debug", "docs"})
        self.assertEqual(result["status"]["labeled_sessions_total"], 2)

        models = delivery.work_stats_projection(insights, self.status(), "6", "", "models")
        self.assertEqual({row["model"] for row in models["scorecard"]}, {"gpt-5.6", "cheap"})
        sizing = delivery.work_stats_projection(insights, self.status(), "6", "", "right_sizing")
        flagged = [cell for cell in sizing["right_sizing"]["by_tier"] if cell["flag"]]
        self.assertEqual([(c["complexity"], c["tier"], c["flag"]) for c in flagged],
                         [("routine", "premium", "possible_overspend")])

    def test_trend_pages_buckets(self):
        insights = self.insights()
        first = delivery.work_stats_projection(insights, self.status(), "6", "", "trend", limit=4)
        self.assertEqual(first["page"]["returned"], 4)
        rest = delivery.work_stats_projection(
            insights, self.status(), "6", "", "trend", cursor=first["page"]["next_cursor"], limit=4)
        self.assertEqual([row["bucket"] for row in rest["trend"]], ["2026-08", "2026-09"])
        self.assertEqual(rest["trend"][-1]["spend_by_area_usd"],
                         {"Product engineering": 3.5, "Personal": 6.0, "Pending": 1.0})

    def test_suggestions_view_carries_the_work_page_suggestions(self):
        insights = self.insights()
        result = delivery.work_stats_projection(insights, self.status(), "6", "", "suggestions")
        premium = next(row for row in result["suggestions"] if row["kind"] == "premium_routine")
        source = next(row for row in insights["recommendations"] if row.get("kind") == "premium_routine")
        self.assertEqual(premium["spend_usd"], source["spend"])
        self.assertEqual(premium["from_models"], [{"model": "gpt-5.6", "runtime": "Codex"}])
        self.assertEqual(len(result["suggestions"]), len(insights["recommendations"]) - 1)
        self.assertNotIn("note", premium)
        synthetic = {"recommendations": [
            {"kind": "switch_model", "work_type": "debug", "complexity": "everyday", "model": "big",
             "runtime": "Claude", "to_model": "small", "to_runtime": "Claude", "from_cost": 2.0, "to_cost": 0.5,
             "from_rate": 0.8, "to_rate": 0.79, "from_pushback": 0.1, "to_pushback": 0.12, "tasks": 30,
             "spend": 60.0, "saving": 36.0, "to_untested_harder": True, "reason": CANARY_TEXT},
            {"kind": "family_upgrade", "model": "m-4", "runtime": "Claude", "to_model": "m-5",
             "to_runtime": "Claude", "from_price": 75.0, "to_price": 25.0, "from_rate": 0.7, "to_rate": 0.7,
             "tasks": 12, "spend": 9.0, "saving": 6.0},
            {"kind": "long_threads", "threshold_turns": 30, "sessions": 6, "spend": 40.0, "ratio": 2.5,
             "long_cost_per_turn": 0.5, "short_cost_per_turn": 0.2, "saving": 24.0},
            {"kind": "switch_model", "work_type": CANARY_TEXT, "model": "big", "to_model": "small"},
        ], "effort_by_model": {"models": [{"model": "big", "runtime": "Claude", "verdicts": [
            {"kind": "lower", "complexity": "routine", "lower": "medium", "higher": "high", "lower_rate": 0.1,
             "higher_rate": 0.1, "lower_samples": 40, "higher_samples": 50, "saving_per_request": 0.2,
             "saving": 10.0},
            {"kind": "lower", "complexity": "routine", "lower": CANARY_FILE, "higher": "high"},
        ]}]}}
        out = delivery.work_stats_projection(synthetic, self.status(), "6", "", "suggestions")
        self.assertEqual([row["kind"] for row in out["suggestions"]],
                         ["switch_model", "family_upgrade", "long_threads"])
        self.assertTrue(out["suggestions"][0]["untested_on_harder_work"])
        self.assertEqual(out["suggestions"][1]["to_output_usd_per_mtok"], 25.0)
        self.assertEqual([(row["kind"], row["lower_effort"]) for row in out["effort_changes"]], [("lower", "medium")])
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, encoded(out))

    def test_off_and_unrun_states_say_so_instead_of_returning_numbers(self):
        settings = {"enabled": False, "paused_until": 0}
        # The cheap state checks answer before any session-history scan starts.
        no_scan = mock.patch.object(meter, "agent_history_rows", side_effect=AssertionError("scan started"))
        no_scan.start()
        self.addCleanup(no_scan.stop)
        with mock.patch.object(meter, "work_insights_supported", return_value=False):
            unsupported = meter.agent_work_stats()
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=settings), \
                mock.patch.object(meter, "work_insights_service") as service:
            off = meter.agent_work_stats()
            service.assert_not_called()
        service = mock.Mock()
        service.status.return_value = {"labels": 0, "pending": 12, "state": "running"}
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=dict(settings, enabled=True)), \
                mock.patch.object(meter, "work_insights_service", return_value=service), \
                mock.patch.object(meter, "work_setup") as setup:
            unrun = meter.agent_work_stats(view="models")
            setup.assert_not_called()
        service.run_forever.assert_not_called()
        service.observe.assert_not_called()
        service.status.return_value = {"labels": 0, "pending": 0, "state": "storage_error"}
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=dict(settings, enabled=True)), \
                mock.patch.object(meter, "work_insights_service", return_value=service):
            broken = meter.agent_work_stats()
        for result, reason in ((unsupported, "unsupported"), (off, "work_insights_off"),
                               (unrun, "classifier_not_run"), (broken, "label_storage_unavailable")):
            self.assertTrue(result["ok"])
            self.assertFalse(result["available"])
            self.assertEqual(result["reason"], reason)
            self.assertFalse({"areas", "work_types", "scorecard", "trend", "coverage"} & set(result))
        self.assertEqual(unrun["status"]["pending_requests"], 12)

    def test_history_incomplete_results_carry_coverage_and_no_totals(self):
        history = {"complete": False, "sessions_scanned": 12, "sessions_total": None, "refreshing": True}
        git = delivery.git_history_incomplete("30", "days", history, now=5)
        self.assertEqual(git, {
            "ok": True, "schema_version": delivery.SCHEMA_VERSION, "as_of": 5,
            "data_scope": "aggregate_git_delivery", "range": "30", "view": "days",
            "available": False, "reason": "history_scan_incomplete",
            "history": {"complete": False, "sessions_scanned": 12, "sessions_total": None},
            "caveat": delivery.HISTORY_INCOMPLETE_CAVEAT,
        })
        work = delivery.work_unavailable(
            {"supported": True, "enabled": True, "labels": 3}, "history_scan_incomplete",
            "30d", "", "suggestions", now=5, history=history)
        self.assertEqual(work["reason"], "history_scan_incomplete")
        self.assertEqual(work["caveat"], delivery.HISTORY_INCOMPLETE_CAVEAT)
        self.assertEqual(work["history"], git["history"])
        self.assertNotIn("history", delivery.work_unavailable({}, "work_insights_off", "6", "", "summary"))
        for bad in (-1, True, "many"):
            self.assertIsNone(delivery.history_projection({"sessions_total": bad})["sessions_total"], bad)
        self.assertEqual(delivery.history_projection(None),
                         {"complete": False, "sessions_scanned": None, "sessions_total": None})
        documented = {"git_stats": delivery_schema.GIT_STATS_FIELDS, "work_stats": delivery_schema.WORK_STATS_FIELDS}
        for tool, fields in documented.items():
            self.assertIn("history", fields, tool)
            self.assertIn("history_scan_incomplete", fields["available"]["description"], tool)

    def test_agent_work_stats_uses_persisted_labels(self):
        service = mock.Mock()
        service.status.return_value = {"labels": 2, "pending": 0}
        settings = {"enabled": True, "paused_until": 0, "areas": self.AREAS, "backfill_days": 0}
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=settings), \
                mock.patch.object(meter, "work_insights_service", return_value=service), \
                mock.patch.object(meter, "agent_history_rows", return_value=(
                    ("row",), {"complete": True, "sessions_scanned": 1, "sessions_total": 1})), \
                mock.patch.object(meter, "_work_insights_aggregate", return_value=(self.insights(), None)) as build:
            result = meter.agent_work_stats(period="6")
            missing = mock.patch.object(
                meter, "_work_insights_aggregate", return_value=(None, ({"ok": False}, 404)))
            with missing, self.assertRaises(MCPQueryError) as error:
                meter.agent_work_stats(runtime="Nope")
        build.assert_called_once_with(settings, "6", "", source_rows=("row",))
        self.assertTrue(result["available"])
        self.assertEqual(error.exception.code, "not_found")

    def test_arguments_are_validated(self):
        for kwargs in ({"period": "14d"}, {"view": "text"}, {"runtime": "x" * 41}, {"cursor": "abc"},
                       {"limit": 0}, {"view": "summary", "limit": 500}):
            with self.assertRaises(MCPQueryError):
                delivery.normalize_work_arguments(**kwargs)


class McpSurfaceTests(unittest.TestCase):
    def test_tools_are_listed_read_only_with_schema_subjects(self):
        tools = {tool["name"]: tool for tool in server.TOOLS}
        for name in ("git_stats", "work_stats"):
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[name]["inputSchema"]["additionalProperties"])
        subjects = tools["schema"]["inputSchema"]["properties"]["subject"]["enum"]
        self.assertIn("git_stats", subjects)
        self.assertIn("work_stats", subjects)
        schema = meter.application().agent_api.schema(subject="work_stats")
        self.assertIn("right_sizing", schema["fields"])
        self.assertFalse(schema["content_included"])

    def test_calls_delegate_and_reject_unknown_arguments(self):
        agent_api = meter.application().agent_api
        payload = {"ok": True, "data_scope": "aggregate_git_delivery"}
        with mock.patch.object(agent_api, "git_stats", return_value=payload) as git, \
                mock.patch.object(agent_api, "work_stats", return_value=payload) as work:
            self.assertFalse(server.call_tool("git_stats", {"range": "30", "view": "days"})["isError"])
            self.assertFalse(server.call_tool("work_stats", {"period": "3", "view": "models"})["isError"])
            self.assertTrue(server.call_tool("git_stats", {"project": "x"})["isError"])
            self.assertTrue(server.call_tool("work_stats", {"project": "x"})["isError"])
        git.assert_called_once_with(range="30", view="days")
        work.assert_called_once_with(period="3", view="models")

    def test_invalid_values_return_stable_error_codes(self):
        result = server.call_tool("git_stats", {"range": "forever"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error_code"], "invalid_argument")


if __name__ == "__main__":
    unittest.main()
