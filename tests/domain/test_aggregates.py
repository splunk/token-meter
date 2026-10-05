import unittest

from token_meter.domain.aggregates import (
    aggregate_model_stats,
    aggregate_cross_session_rows,
    current_session_summaries,
    fold_child_session_rows,
    metric_coverage,
    spend_log_summaries,
)


def session(session_id, runtime, model, cost, tokens, *, available=True, mtime=0):
    availability = {
        "cost": available,
        "tokens": available,
        "cache": available,
    }
    return {
        "id": session_id,
        "provider": "provider",
        "runtime": runtime,
        "label": runtime,
        "project": "project",
        "mtime": mtime,
        "turns": 1,
        "cost": cost,
        "tokens": tokens,
        "availability": availability,
        "_model_cost": {model: cost},
        "_model_tok": {model: tokens},
        "_day_cost": {"2026-08-10": cost},
    }


class AggregateDomainTests(unittest.TestCase):
    def test_spend_logs_aggregate_every_session_in_inclusive_range(self):
        rows = [
            {
                "id": "spanning", "title": "Zulu work", "project": "/repo/z",
                "provider": "cursor", "label": "Cursor",
                "path": "/private/logs/spanning.jsonl",
                "availability": {"cost": True}, "token_estimate": True,
                "_day_cost": {
                    "2026-08-01": 1.0,
                    "2026-08-02": 2.0,
                    "2026-08-03": 4.0,
                },
            },
            {
                "id": "tie", "title": "Alpha work", "project": "/repo/a",
                "provider": "codex", "label": "Codex",
                "path": "/private/logs/tie.jsonl",
                "availability": {"cost": True},
                "_day_cost": {"2026-08-02": 3.0},
            },
            {
                "id": "outside", "title": "Outside", "project": "/repo/o",
                "provider": "claude", "label": "Claude",
                "path": "/private/logs/outside.jsonl",
                "availability": {"cost": True},
                "_day_cost": {"2026-07-31": 20.0},
            },
        ]

        result = spend_log_summaries(rows, "2026-08-01", "2026-08-02")

        self.assertEqual([row["id"] for row in result], ["tie", "spanning"])
        self.assertEqual(result[0]["cost"], 3.0)
        self.assertEqual(result[0]["active_days"], 1)
        self.assertEqual(result[1]["cost"], 3.0)
        self.assertEqual(result[1]["active_days"], 2)
        self.assertEqual(result[1]["usage_basis"], "local_estimate")
        self.assertEqual(result[1]["provenance"]["estimated_cost"], 3.0)
        self.assertNotIn("path", result[1])

    def test_model_aggregation_bounds_derived_samples_without_truncating_totals(self):
        samples = [{
            "model": "model-1", "day": "", "ts": index,
            "input_tokens": 100, "peak_input_tokens": 120,
            "context_tokens": 110, "output_tokens": 20,
            "cache_read_tokens": 50, "duration_s": 2,
            "tool_calls": 1, "model_calls": 1,
        } for index in range(2_100)]
        captured_pace_groups = {}

        def capture_pace(groups):
            captured_pace_groups.update(groups)
            return {"windows": {}}

        result = aggregate_model_stats([{
            "id": "session-1", "provider": "provider", "runtime": "runtime",
            "availability": {"cost": True, "tokens": True, "cache": True},
            "model_stats": [{
                "model": "model-1", "cost": 21.0, "tokens": 252_000,
                "input_tokens": 210_000, "output_tokens": 42_000,
                "executions": 2_100,
                "availability": {"cost": True, "tokens": True, "cache": True},
            }],
            "_model_daily": [], "_performance_samples": samples, "_wait_samples": [],
        }], throughput_finalizer=lambda row: row, matched_pace=capture_pace)

        row = result["models"][0]
        self.assertEqual(row["executions"], 2_100)
        self.assertEqual(row["input_tokens"], 210_000)
        self.assertEqual(row["output_tokens"], 42_000)
        self.assertEqual(row["timed_samples"], 2_100)
        self.assertLessEqual(len(row["workload_peak_inputs"]), 2_000)
        self.assertLessEqual(len(row["workload_outputs"]), 2_000)
        self.assertLessEqual(len(captured_pace_groups["model-1::runtime::unavailable"]), 500)

    def test_same_model_in_two_runtimes_stays_separate(self):
        result = aggregate_cross_session_rows([
            session("a", "runtime-a", "model-1", 1.0, 10),
            session("b", "runtime-b", "model-1", 2.0, 20),
        ])

        self.assertEqual(
            [(row["id"], row["cost"], row["tokens"]) for row in result["model_mix"]],
            [("model-1::runtime-b", 2.0, 20), ("model-1::runtime-a", 1.0, 10)],
        )

    def test_unavailable_session_does_not_become_measured_zero(self):
        rows = [
            session("measured", "runtime", "model", 0.0, 0, available=True),
            session("missing", "runtime", "model", 0.0, 0, available=False),
        ]

        self.assertEqual(metric_coverage(rows, "cost"), {
            "covered_sessions": 1,
            "total_sessions": 2,
            "complete": False,
        })
        result = aggregate_cross_session_rows(rows)
        self.assertFalse(result["coverage"]["cost"]["complete"])
        self.assertEqual(result["provenance"]["unavailable_sessions"], 1)
        self.assertTrue(result["availability"]["cost"])

    def test_cross_session_projection_preserves_order_and_reported_trend(self):
        result = aggregate_cross_session_rows([
            session("older", "runtime", "model-a", 1.0, 10, mtime=10),
            session("newer", "runtime", "model-b", 3.0, 30, mtime=20),
        ])

        self.assertEqual([row["id"] for row in result["sessions"]], ["newer", "older"])
        self.assertEqual(result["total_cost"], 4.0)
        self.assertEqual(result["trend"], [{
            "day": "2026-08-10",
            "cost": 4.0,
            "reported_cost": 4.0,
            "estimated_cost": 0.0,
            "provenance": {
                "usage_basis": "reported",
                "reported_sessions": 2,
                "estimated_sessions": 0,
                "unavailable_sessions": 0,
                "estimated_cost": 0.0,
                "estimated_tokens": 0,
            },
            "usage_basis": "reported",
            "anomaly": False,
            "anomaly_basis": "reported_only",
        }])

    def test_current_session_projection_deduplicates_and_hides_paths(self):
        rows = [
            {**session("same", "runtime", "model", 1, 10, mtime=95),
             "path": "/private/secret", "terminal": False},
            {**session("same", "runtime", "model", 2, 20, mtime=99),
             "path": "/private/new-secret", "terminal": True},
        ]

        result = current_session_summaries(rows, now=100, max_age_s=30, limit=8)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["activity_state"], "working")
        self.assertNotIn("path", result[0])

    def test_current_session_projection_exposes_bounded_live_throughput(self):
        row = {
            **session("live", "runtime", "model", 1, 10, mtime=99),
            "terminal": False,
            "throughput": {
                "available": False, "output_tps": 0, "basis": "unavailable",
            },
            "live_throughput": {
                "available": True, "output_tps": 24.5,
                "basis": "live_end_to_end", "completed_steps": 3,
                "measured_output_tokens": 245, "measured_seconds": 10,
                "private_trace_detail": "must not cross the card boundary",
            },
        }

        result = current_session_summaries(
            [row], now=100, max_age_s=30, limit=8,
        )[0]

        self.assertTrue(result["availability"]["throughput"])
        self.assertEqual(result["live_throughput"], {
            "available": True,
            "output_tps": 24.5,
            "basis": "live_end_to_end",
            "completed_steps": 3,
            "measured_output_tokens": 245,
            "measured_seconds": 10.0,
        })
        self.assertNotIn("private_trace_detail", result["live_throughput"])


class FoldedChildAvailabilityTests(unittest.TestCase):
    @staticmethod
    def family(root_cost_available, child_cost_available):
        root = session("root", "OpenCode", "model-a", 1.0, 10,
                       available=root_cost_available, mtime=90)
        child = session("child", "OpenCode", "model-a", 2.5, 20,
                        available=child_cost_available, mtime=95)
        root["provider"] = child["provider"] = "opencode"
        child.update(is_child_session=True, root_session_id="root")
        return [root, child]

    def test_measured_child_cost_survives_an_unavailable_root(self):
        folded = fold_child_session_rows(self.family(False, True))
        self.assertEqual([row["id"] for row in folded], ["root"])
        self.assertEqual(folded[0]["cost"], 2.5)
        self.assertEqual(folded[0]["tokens"], 20)
        self.assertTrue(folded[0]["availability"]["cost"])
        self.assertTrue(folded[0]["cost_partial"])
        summary = current_session_summaries(self.family(False, True), now=100)[0]
        self.assertTrue(summary["availability"]["cost"])
        self.assertEqual(summary["cost"], 2.5)
        self.assertTrue(summary["cost_partial"])

    def test_unavailable_child_cost_is_not_presented_as_complete(self):
        folded = fold_child_session_rows(self.family(True, False))[0]
        self.assertEqual(folded["cost"], 1.0)
        self.assertTrue(folded["availability"]["cost"])
        self.assertTrue(folded["cost_partial"])

    def test_fully_measured_family_is_complete_and_unmeasured_stays_unavailable(self):
        complete = fold_child_session_rows(self.family(True, True))[0]
        self.assertEqual(complete["cost"], 3.5)
        self.assertFalse(complete["cost_partial"])
        self.assertNotIn("subagent_runs", complete)
        unmeasured = fold_child_session_rows(self.family(False, False))[0]
        self.assertFalse(unmeasured["availability"]["cost"])
        self.assertEqual(unmeasured["cost"], 0)
        self.assertFalse(unmeasured["cost_partial"])

    def test_current_row_without_folded_children_has_no_partial_flag(self):
        claude = {**session("solo", "Claude", "model-a", 1.0, 10, mtime=95),
                  "provider": "claude"}
        summary = current_session_summaries([claude], now=100)[0]
        self.assertNotIn("cost_partial", summary)
        folded = current_session_summaries(self.family(True, True), now=100)[0]
        self.assertFalse(folded["cost_partial"])


if __name__ == "__main__":
    unittest.main()
