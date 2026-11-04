import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import meter
import token_meter_mcp as server
from token_meter.domain import work as work_domain
from token_meter.mcp import delivery
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
        payload["days"][0]["added"] += 1
        with self.assertRaises(MCPQueryError) as stale:
            delivery.git_stats_projection(payload, "30", "days", cursor=first["page"]["next_cursor"], limit=10)
        self.assertEqual(stale.exception.code, "stale_cursor")

    def test_arguments_are_validated(self):
        for kwargs in ({"range_key": "14"}, {"view": "projects"}, {"view": "summary", "cursor": "x"}):
            with self.assertRaises(MCPQueryError):
                delivery.normalize_git_arguments(**kwargs)

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
                mock.patch.dict(meter._xsess, {"data": {"ok": True}, "internal_rows": rows}), \
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
        return [row("a", 2.0, "gpt-5.6"), row("b", 6.0, "cheap"), row("c", 1.0, "gpt-5.6")]

    def insights(self):
        labels = {
            "a": {"area": "Product engineering", "work_type": "debug", "complexity": "routine",
                  "corrections": 1, "correction_labels": 2},
            "b": {"area": "Personal", "work_type": "docs", "complexity": "complex"},
        }
        prices = {"gpt-5.6": 10.0, "cheap": 1.0}
        insights = work_domain.build_work_insights(
            self.rows(), labels, lambda ident: ident.split("\0")[0], self.AREAS,
            lambda m, p: prices.get(m), today="2026-09-30")
        insights["recommendations"] = [{"text": CANARY_TEXT, "file": CANARY_FILE}]
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
        self.assertEqual(areas["Product engineering"]["requests"], 3)
        self.assertEqual(areas["Personal"]["spend_usd"], 6.0)
        self.assertEqual(result["coverage"]["sessions"], insights["coverage"]["sessions"])
        self.assertEqual(result["coverage"]["labeled_sessions"], 2)
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
        self.assertEqual(rest["trend"][-1]["spend_by_area_usd"], {"Product engineering": 2.0, "Personal": 6.0, "Pending": 1.0})

    def test_off_and_unrun_states_say_so_instead_of_returning_numbers(self):
        settings = {"enabled": False, "paused_until": 0}
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
        for result, reason in ((unsupported, "unsupported"), (off, "work_insights_off"), (unrun, "classifier_not_run")):
            self.assertTrue(result["ok"])
            self.assertFalse(result["available"])
            self.assertEqual(result["reason"], reason)
            self.assertFalse({"areas", "work_types", "scorecard", "trend", "coverage"} & set(result))
        self.assertEqual(unrun["status"]["pending_requests"], 12)

    def test_agent_work_stats_uses_persisted_labels(self):
        service = mock.Mock()
        service.status.return_value = {"labels": 2, "pending": 0}
        settings = {"enabled": True, "paused_until": 0, "areas": self.AREAS, "backfill_days": 0}
        with mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=settings), \
                mock.patch.object(meter, "work_insights_service", return_value=service), \
                mock.patch.object(meter, "_work_insights_aggregate", return_value=(self.insights(), None)) as build:
            result = meter.agent_work_stats(period="6")
            missing = mock.patch.object(
                meter, "_work_insights_aggregate", return_value=(None, ({"ok": False}, 404)))
            with missing, self.assertRaises(MCPQueryError) as error:
                meter.agent_work_stats(runtime="Nope")
        build.assert_called_once_with(settings, "6", "")
        self.assertTrue(result["available"])
        self.assertEqual(error.exception.code, "not_found")

    def test_arguments_are_validated(self):
        for kwargs in ({"period": "14d"}, {"view": "text"}, {"runtime": "x" * 41}, {"cursor": "abc"}):
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
