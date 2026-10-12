import unittest
import time

from token_meter.contracts import EvidenceBasis, EvidenceValue, TimingEvidence
from token_meter.domain.insights import enrich_insights, normalize_insights
from token_meter.domain.timing import (
    merge_execution_intervals,
    performance_summary,
    timing_snapshot,
    wait_time_summary,
)
from token_meter.domain.tools import (
    capability_control_groups,
    optional_capability_summary,
    summarize_tool_evidence,
    tool_identity,
)


class TimingDomainTests(unittest.TestCase):
    def test_measured_inferred_and_unavailable_timing_remain_distinct(self):
        evidence = TimingEvidence(
            active_seconds=EvidenceValue(12.0, EvidenceBasis.MEASURED),
            wait_seconds=EvidenceValue(5.0, EvidenceBasis.INFERRED),
            ttft_seconds=EvidenceValue.unavailable(),
        )

        self.assertEqual(timing_snapshot(evidence), {
            "active": {"available": True, "seconds": 12.0, "basis": "measured"},
            "wait": {"available": True, "seconds": 5.0, "basis": "inferred"},
            "ttft": {"available": False, "seconds": None, "basis": "unavailable"},
        })

    def test_interval_wait_and_throughput_goldens_are_runtime_neutral(self):
        self.assertEqual(
            merge_execution_intervals(((0, 5), (3, 8), (10, 12), (12, 15))),
            13.0,
        )
        waits = wait_time_summary([
            {"duration_s": 2, "timing_basis": "reported", "user_pause_s": 0},
            {"duration_s": 8, "timing_basis": "observed", "user_pause_s": 3},
        ])
        self.assertEqual(waits["avg_s"], 5.0)
        self.assertEqual(waits["reported_samples"], 1)
        self.assertEqual(waits["observed_samples"], 1)
        self.assertEqual(waits["user_pause_s"], 3.0)
        throughput = performance_summary([
            {"ts": 1, "output_tokens": 100, "duration_s": 10, "tool_calls": 0},
            {"ts": 2, "output_tokens": 50, "duration_s": 20, "tool_calls": 1},
        ], total_output_tokens=200)
        self.assertEqual(throughput["basis"], "end_to_end")
        self.assertEqual(throughput["output_tps"], 5.0)
        self.assertEqual(throughput["sample_count"], 2)
        self.assertEqual(throughput["timing_coverage"], 0.75)

    def test_later_tool_bearing_output_updates_completed_session_speed(self):
        throughput = performance_summary([
            {
                "ts": 1, "output_tokens": 100, "duration_s": 10,
                "generation_s": 5, "tool_calls": 0,
            },
            {
                "ts": 2, "output_tokens": 300, "duration_s": 20,
                "generation_s": 15, "tool_calls": 2,
            },
        ], total_output_tokens=400)

        self.assertEqual(throughput["basis"], "end_to_end")
        self.assertEqual(throughput["sample_count"], 2)
        self.assertEqual(throughput["measured_output_tokens"], 400)
        self.assertAlmostEqual(throughput["output_tps"], 400 / 30)
        self.assertEqual(throughput["latest_output_tps"], 15)
        self.assertEqual(throughput["timing_coverage"], 1)


class ToolDomainTests(unittest.TestCase):
    def test_tool_evidence_retains_per_tool_calendar_day_totals(self):
        first_day = int(time.mktime((2026, 8, 20, 12, 0, 0, 0, 0, -1)))
        second_day = int(time.mktime((2026, 8, 21, 12, 0, 0, 0, 0, -1)))

        evidence = summarize_tool_evidence([
            {
                **tool_identity("search"), "output_tokens": 120,
                "ts": first_day, "args_fingerprint": "one", "error": False,
            },
            {
                **tool_identity("search"), "output_tokens": 80,
                "ts": second_day, "args_fingerprint": "two", "error": False,
            },
        ])

        self.assertEqual(evidence["tools"][0]["daily"], [
            {
                "day": "2026-08-20", "calls": 1, "output_tokens": 120,
                "flagged_tokens": 0, "errors": 0, "oversized_calls": 0,
                "repeat_calls": 0, "nested_calls": 0,
            },
            {
                "day": "2026-08-21", "calls": 1, "output_tokens": 80,
                "flagged_tokens": 0, "errors": 0, "oversized_calls": 0,
                "repeat_calls": 0, "nested_calls": 0,
            },
        ])

    def test_native_skill_evidence_is_not_inferred_from_text_or_tool_name(self):
        evidence = summarize_tool_evidence([
            {**tool_identity("Skill"), "output_tokens": 2, "skills": ["named-skill"]},
            {**tool_identity("skill_mentioned_in_text"), "output_tokens": 3, "skills": []},
        ])

        self.assertEqual(evidence["skills"], [{
            "name": "named-skill", "activations": 1, "last_ts": 0,
        }])

    def test_partial_measurement_cannot_create_review_candidate(self):
        groups = capability_control_groups([], [{
            "id": "skill:one",
            "name": "one",
            "runtime": "synthetic-runtime",
            "plugin_id": "pack",
            "mutable": True,
            "enabled": True,
            "used": False,
            "measurement": "unknown",
        }])
        groups[0]["scanned_sessions"] = 10

        summary = optional_capability_summary(groups)

        self.assertEqual(summary["review_candidates"], [])
        self.assertEqual(summary["unknown_evidence_packs"], 1)


class InsightDomainTests(unittest.TestCase):
    def test_insights_are_deduplicated_and_partial_tools_do_not_warn(self):
        rows = enrich_insights(
            [],
            executions=[],
            tool_data={"advertised": 20, "unique_used": 0, "loaded_known": False},
            context_window=0,
            context_latest=0,
            context_peak=0,
        )
        self.assertEqual(rows, [])
        self.assertEqual(len(normalize_insights([
            {"key": "same", "kind": "warn", "priority": 2},
            {"key": "same", "kind": "neutral", "priority": 90},
        ])), 1)


if __name__ == "__main__":
    unittest.main()
