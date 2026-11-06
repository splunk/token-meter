import unittest
import datetime
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
import meter


class ModelPerformanceTests(unittest.TestCase):
    def setUp(self):
        # Matched-pace comparisons are cached per model pair and persisted, so
        # each test starts from a clean in-memory and on-disk cache and never
        # writes into the developer's home directory.
        self._pace_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._pace_temp.cleanup)
        self._pace_cache_path = Path(self._pace_temp.name) / "matched-pace-cache.json"
        patcher = mock.patch.object(
            meter, "TOKEN_METER_MATCHED_PACE_CACHE", str(self._pace_cache_path),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(meter._matched_pace_pair_cache.clear)
        self.addCleanup(
            setattr, meter, "_matched_pace_pair_cache_loaded", False,
        )
        self.addCleanup(meter._matched_pace_build_state.update, building=False)
        self.addCleanup(setattr, meter, "_matched_pace_persist", False)
        meter._matched_pace_pair_cache.clear()
        meter._matched_pace_pair_cache_loaded = False
        meter._matched_pace_build_state["building"] = False
        meter._matched_pace_persist = True
        meter._matched_pace_cache.update(signature=None, data=None)
        self.addCleanup(meter._matched_pace_cache.update, signature=None, data=None)

    def test_parse_iso_reuses_bounded_timestamp_conversions(self):
        meter.parse_iso.cache_clear()
        timestamp = "2026-08-14T04:30:00.123Z"

        first = meter.parse_iso(timestamp)
        before = meter.parse_iso.cache_info()
        second = meter.parse_iso(timestamp)
        after = meter.parse_iso.cache_info()

        self.assertEqual(first, second)
        self.assertEqual(after.hits, before.hits + 1)
        self.assertLessEqual(after.maxsize, 65536)

    def test_codex_performance_without_model_identity_stays_unknown(self):
        objs = [
            {"timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:01.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 10,
                    "total_tokens": 110,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:02.000Z", "payload": {
                "type": "task_complete", "duration_ms": 2000,
            }},
        ]

        samples = meter.codex_performance_samples(objs)

        self.assertEqual(samples[0]["model"], "unknown-model")

    def test_claude_tool_free_turn_uses_reported_turn_duration(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-01T00:00:00.000Z",
             "message": {"content": "hello"}},
            {"type": "assistant", "timestamp": "2026-07-01T00:00:01.900Z", "message": {
                "id": "msg-1", "model": "claude-sonnet-5", "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 20, "output_tokens": 10}, "stop_reason": "end_turn",
            }},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-07-01T00:00:02.000Z",
             "durationMs": 2000},
        ]
        samples = meter.claude_performance_samples(objs)
        summary = meter.performance_summary(samples, 10)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["tool_calls"], 0)
        self.assertEqual(samples[0]["peak_input_tokens"], 20)
        self.assertEqual(samples[0]["model_calls"], 1)
        self.assertEqual(summary["basis"], "tool_free")
        self.assertEqual(summary["output_tps"], 5)

    def test_claude_desktop_uses_observed_turn_timing_without_duration_records(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "inspect the project"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:02.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "checking"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:04.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "user", "timestamp": "2026-07-13T00:00:05.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:10.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "finished"}],
                "usage": {"input_tokens": 25, "output_tokens": 20}, "stop_reason": "end_turn",
            }},
        ]
        samples = meter.claude_performance_samples(objs)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["timing_basis"], "observed")
        self.assertEqual(samples[0]["duration_s"], 10)
        self.assertEqual(samples[0]["output_tokens"], 50)
        self.assertEqual(samples[0]["tool_calls"], 1)
        self.assertEqual(meter.performance_summary(samples, 50)["output_tps"], 5)

    def test_claude_observed_throughput_excludes_user_input_pause(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "start"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:10.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8", "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "content": [{"type": "tool_use", "id": "question-1", "name": "AskUserQuestion"}],
            }},
            {"type": "user", "timestamp": "2026-07-13T00:01:40.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "question-1", "content": "answer"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:01:50.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8", "stop_reason": "end_turn",
                "usage": {"input_tokens": 25, "output_tokens": 20},
                "content": [{"type": "text", "text": "finished"}],
            }},
        ]
        sample = meter.claude_performance_samples(objs)[0]
        self.assertEqual(sample["wall_duration_s"], 110)
        self.assertEqual(sample["user_pause_s"], 90)
        self.assertEqual(sample["duration_s"], 20)

    def test_codex_tool_free_speed_excludes_time_to_first_token(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:09.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "cached_input_tokens": 50,
                    "output_tokens": 100, "total_tokens": 300,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000, "time_to_first_token_ms": 2000,
            }},
        ]
        samples = meter.codex_performance_samples(objs, "gpt-5.6")
        summary = meter.performance_summary(samples, 100)
        self.assertEqual(samples[0]["generation_s"], 8)
        self.assertEqual(samples[0]["peak_input_tokens"], 200)
        self.assertEqual(samples[0]["cache_read_tokens"], 50)
        self.assertEqual(samples[0]["uncached_input_tokens"], 150)
        self.assertEqual(samples[0]["model_calls"], 1)
        self.assertEqual(summary["output_tps"], 12.5)
        self.assertEqual(summary["avg_ttft_ms"], 2000)

    def test_codex_live_speed_uses_completed_steps_before_task_complete(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "cached_input_tokens": 50,
                    "output_tokens": 40, "total_tokens": 240,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:05.000Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
            }},
            {"timestamp": "2026-07-01T00:00:10.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 250, "cached_input_tokens": 100,
                    "output_tokens": 60, "total_tokens": 310,
                }},
            }},
        ]

        live = meter.codex_live_performance_summary(objs)

        self.assertFalse(meter.performance_summary(
            meter.codex_performance_samples(objs, "gpt-5.6"), 100,
        )["available"])
        self.assertTrue(live["available"])
        self.assertEqual(live["basis"], "live_end_to_end")
        self.assertEqual(live["completed_steps"], 2)
        self.assertEqual(live["measured_output_tokens"], 100)
        self.assertEqual(live["measured_seconds"], 10)
        self.assertEqual(live["output_tps"], 10)

    def test_codex_live_speed_disappears_when_task_completes(self):
        objs = [
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:05.000Z", "payload": {
                "type": "task_complete", "duration_ms": 5000,
            }},
        ]

        live = meter.codex_live_performance_summary(objs)

        self.assertFalse(live["available"])
        self.assertEqual(live["completed_steps"], 0)
        self.assertEqual(live["output_tps"], 0)

    def test_tool_bearing_task_falls_back_to_end_to_end_throughput(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:02.000Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
            }},
            {"timestamp": "2026-07-01T00:00:09.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                }},
            }},
            {"timestamp": "2026-07-01T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000, "time_to_first_token_ms": 1000,
            }},
        ]
        summary = meter.performance_summary(meter.codex_performance_samples(objs), 100)
        self.assertEqual(summary["basis"], "end_to_end")
        self.assertEqual(summary["tool_free_samples"], 0)
        self.assertEqual(summary["output_tps"], 10)

    def test_cross_log_model_aggregation_is_weighted_and_keeps_daily_io(self):
        sessions = [{
            "provider": "codex",
            "model_stats": [{"model": "gpt-5.6", "cost": 1.0, "tokens": 330,
                             "input_tokens": 300, "output_tokens": 30, "executions": 2}],
            "_model_daily": [{"model": "gpt-5.6", "day": "2026-07-01", "cost": 1.0,
                              "input_tokens": 300, "output_tokens": 30, "executions": 2}],
            "_performance_samples": [
                {"model": "gpt-5.6", "day": "2026-07-01", "ts": 10,
                 "output_tokens": 10, "duration_s": 2, "generation_s": 1, "tool_calls": 0},
                {"model": "gpt-5.6", "day": "2026-07-01", "ts": 20,
                 "output_tokens": 20, "duration_s": 5, "generation_s": 3, "tool_calls": 0},
            ],
            "_wait_samples": [
                {"model": "gpt-5.6", "day": "2026-07-01", "duration_s": 8},
                {"model": "gpt-5.6", "day": "2026-07-01", "duration_s": 12},
            ],
        }]
        result = meter.aggregate_model_stats(sessions)
        row = result["models"][0]
        self.assertEqual(row["output_tps"], 7.5)
        self.assertEqual(row["timing_coverage"], 1)
        self.assertEqual(row["daily"][0]["input_tokens"], 300)
        self.assertEqual(row["daily"][0]["throughput_samples"], 2)
        self.assertEqual(row["avg_wait_s"], 10)
        self.assertEqual(row["median_wait_s"], 10)
        self.assertEqual(row["p95_wait_s"], 12)
        self.assertEqual(row["daily"][0]["wait_durations_s"], [8, 12])
        self.assertEqual(row["daily"][0]["max_wait_s"], 12)

    def test_model_efficiency_counters_preserve_reasoning_cache_and_missing_evidence(self):
        stats = {}
        meter.add_model_summary(stats, "gpt-5.6", {
            "input_tokens": 100,
            "cache_read_input_tokens": 60,
            "cache_creation_input_tokens": 20,
            "output_tokens": 40,
            "reasoning_output_tokens": 12,
            "reasoning_available": True,
        }, 0.5)
        meter.add_model_summary(stats, "gpt-5.6", {
            "input_tokens": 50,
            "output_tokens": 10,
        }, 0.1)

        self.assertEqual(stats["gpt-5.6"], {
            "cost": 0.6,
            "tokens": 280,
            "input_tokens": 230,
            "output_tokens": 50,
            "cache_read_tokens": 60,
            "cache_write_tokens": 20,
            "reasoning_tokens": 12,
            "reasoning_output_tokens": 40,
            "reasoning_executions": 1,
            "reasoning_unavailable_executions": 1,
            "thinking_executions": 0,
            "thinking_covered_executions": 0,
            "token_covered_executions": 2,
            "io_covered_executions": 2,
            "io_covered_input_tokens": 230,
            "io_covered_output_tokens": 50,
            "executions": 2,
            "input_evidence": True,
            "output_evidence": True,
        })

    def test_reasoning_requires_numeric_evidence(self):
        from token_meter.runtimes.codex import codex_usage as native_codex_usage

        for value in (
            None, False, True, "12", -1, 12.5, float("inf"), float("nan"),
            10 ** 400,
        ):
            codex = native_codex_usage({
                "output_tokens": 20, "reasoning_output_tokens": value,
            })
            opencode = meter._opencode_usage({
                "tokens": {"output": 20, "reasoning": value},
            })
            self.assertFalse(codex["reasoning_available"])
            self.assertEqual(codex["reasoning_output_tokens"], 0)
            self.assertFalse(opencode["reasoning_available"])
            self.assertEqual(opencode["reasoning_tokens"], 0)
            self.assertEqual(meter._opencode_context_tokens(opencode), 20)
        self.assertTrue(native_codex_usage({
            "output_tokens": 20, "reasoning_output_tokens": 0,
        })["reasoning_available"])
        self.assertTrue(meter._opencode_usage({
            "tokens": {"output": 20, "reasoning": 0},
        })["reasoning_available"])
        self.assertEqual(native_codex_usage({
            "output_tokens": 20, "reasoning_output_tokens": 12.0,
        })["reasoning_output_tokens"], 12)
        for normalizer in (native_codex_usage, meter.codex_usage):
            oversized = normalizer({
                "input_tokens": 10,
                "output_tokens": 20,
                "reasoning_output_tokens": 21,
            })
            self.assertFalse(oversized["reasoning_available"])
            self.assertEqual(oversized["reasoning_output_tokens"], 0)

        stats = {}
        meter.add_model_summary(stats, "gpt-5.6", {
            "input_tokens": 10,
            "output_tokens": 20,
            "reasoning_output_tokens": 21,
            "reasoning_available": True,
        }, 0.1)
        self.assertEqual(stats["gpt-5.6"]["reasoning_tokens"], 0)
        self.assertEqual(stats["gpt-5.6"]["reasoning_executions"], 0)
        self.assertEqual(stats["gpt-5.6"]["reasoning_unavailable_executions"], 1)

    def test_model_aggregation_exposes_efficiency_counts_and_coverage(self):
        sessions = [{
            "id": "session-a", "provider": "codex", "runtime": "Codex",
            "availability": meter.metric_availability("codex"),
            "model_stats": [{
                "model": "gpt-5.6", "cost": 2.0, "tokens": 360,
                "input_tokens": 300, "output_tokens": 60, "executions": 3,
                "cache_read_tokens": 120, "cache_write_tokens": 10,
                "reasoning_tokens": 15, "reasoning_output_tokens": 50,
                "reasoning_executions": 2,
                "reasoning_unavailable_executions": 1,
            }],
            "_model_daily": [{
                "model": "gpt-5.6", "day": "2026-08-27", "cost": 2.0,
                "input_tokens": 300, "output_tokens": 60, "executions": 3,
                "cache_read_tokens": 120, "cache_write_tokens": 10,
                "reasoning_tokens": 15, "reasoning_output_tokens": 50,
                "reasoning_executions": 2,
                "reasoning_unavailable_executions": 1,
            }],
            "_performance_samples": [{
                "model": "gpt-5.6", "day": "2026-08-27", "ts": 10,
                "input_tokens": 300, "output_tokens": 60,
                "duration_s": 3, "generation_s": 3,
                "tool_calls": 2, "model_calls": 2,
                "attempts": 3, "retries": 2, "failed_attempts": 1,
            }],
            "_wait_samples": [],
        }]

        row = meter.aggregate_model_stats(sessions)["models"][0]

        self.assertEqual(row["cache_read_tokens"], 120)
        self.assertEqual(row["cache_write_tokens"], 10)
        self.assertEqual(row["cost_covered_output_tokens"], 60)
        self.assertEqual(row["cost_covered_executions"], 3)
        self.assertEqual(row["cache_covered_input_tokens"], 300)
        self.assertEqual(row["token_covered_executions"], 3)
        self.assertEqual(row["reasoning_tokens"], 15)
        self.assertEqual(row["reasoning_output_tokens"], 50)
        self.assertEqual(row["reasoning_executions"], 2)
        self.assertEqual(row["reasoning_unavailable_executions"], 1)
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(row["retries"], 2)
        self.assertEqual(row["failed_attempts"], 1)
        self.assertEqual(row["attempt_samples"], 1)
        self.assertEqual(row["coverage"]["reasoning_tokens"], {
            "covered_executions": 2,
            "total_executions": 3,
            "complete": False,
        })
        self.assertFalse(row["coverage"]["attempts"]["complete"])
        self.assertEqual(row["daily"][0]["reasoning_tokens"], 15)
        self.assertEqual(row["daily"][0]["attempts"], 3)

    def test_efficiency_denominators_exclude_uncovered_cost_and_cache_rows(self):
        covered = {
            "id": "covered", "provider": "codex", "runtime": "Codex",
            "availability": meter.metric_availability(
                "codex", cost=True, tokens=True, cache=True,
            ),
            "model_stats": [{
                "model": "gpt-5.6", "cost": 2.0, "tokens": 360,
                "input_tokens": 300, "output_tokens": 60, "executions": 1,
                "cache_read_tokens": 120,
            }],
            "_model_daily": [{
                "model": "gpt-5.6", "day": "2026-08-27", "cost": 2.0,
                "input_tokens": 300, "output_tokens": 60, "executions": 1,
                "cache_read_tokens": 120,
            }],
        }
        uncovered = {
            "id": "uncovered", "provider": "codex", "runtime": "Codex",
            "availability": meter.metric_availability(
                "codex", cost=False, tokens=True, cache=False,
            ),
            "model_stats": [{
                "model": "gpt-5.6", "cost": 0.0, "tokens": 1100,
                "input_tokens": 1000, "output_tokens": 100,
                "executions": 1,
            }],
            "_model_daily": [{
                "model": "gpt-5.6", "day": "2026-08-27", "cost": 0.0,
                "input_tokens": 1000, "output_tokens": 100,
                "executions": 1,
            }],
        }

        row = meter.aggregate_model_stats([covered, uncovered])["models"][0]

        self.assertEqual(row["output_tokens"], 160)
        self.assertEqual(row["cost_covered_output_tokens"], 60)
        self.assertEqual(row["cache_covered_input_tokens"], 300)
        self.assertFalse(row["coverage"]["cost"]["complete"])
        self.assertFalse(row["coverage"]["cache"]["complete"])

    def test_input_only_model_evidence_keeps_output_unavailable(self):
        availability = meter.metric_availability(
            "opencode", cost=True, tokens=True, input_tokens=True,
            output_tokens=False, cache=True,
        )
        stats = {
            "model": "model-a", "cost": 1.0, "input_tokens": 10,
            "output_tokens": 0, "executions": 1,
            "token_covered_executions": 0, "cost_covered_executions": 0,
            "cost_covered_output_tokens": 0,
            "cache_covered_input_tokens": 10,
            "availability": availability,
        }
        row = meter.aggregate_model_stats([{
            "id": "input-only", "provider": "opencode", "runtime": "OpenCode",
            "availability": meter.metric_availability(
                "opencode", cost=True, tokens=True, input_tokens=True,
                output_tokens=True, cache=True,
            ),
            "model_stats": [stats],
            "_model_daily": [{**stats, "day": "2026-08-27"}],
            "_wait_samples": [{
                "model": "model-a", "day": "2026-08-27",
                "duration_s": 1, "output_tokens": 0,
            }],
        }])["models"][0]

        self.assertTrue(row["availability"]["tokens"])
        self.assertTrue(row["availability"]["input_tokens"])
        self.assertFalse(row["availability"]["output_tokens"])
        self.assertFalse(row["daily"][0]["availability"]["output_tokens"])

    def test_mixed_speed_coverage_counts_all_completed_output(self):
        summary = meter.performance_summary([
            {"output_tokens": 10, "duration_s": 2, "generation_s": 1, "tool_calls": 0, "ts": 1},
            {"output_tokens": 90, "duration_s": 9, "generation_s": 8, "tool_calls": 1, "ts": 2},
        ], 100)
        self.assertEqual(summary["basis"], "end_to_end")
        self.assertAlmostEqual(summary["output_tps"], 100 / 11)
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["timing_coverage"], 1)

    def test_model_aggregation_omits_rows_without_io(self):
        availability = meter.metric_availability(
            "claude", cost=False, tokens=True, input_tokens=True,
            output_tokens=True, cache=False,
        )
        result = meter.aggregate_model_stats([{
            "provider": "claude", "availability": availability,
            "model_stats": [{"model": "<synthetic>", "input_tokens": 0,
                             "output_tokens": 0, "executions": 2, "cost": 0,
                             "token_covered_executions": 2,
                             "availability": availability}],
        }])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["total_models"], 0)

    def test_model_aggregation_keeps_explicitly_covered_zero_io(self):
        availability = meter.metric_availability(
            "opencode", cost=True, tokens=True, input_tokens=True,
            output_tokens=True, cache=True,
        )
        stats = {
            "model": "model-a", "cost": 1.0,
            "input_tokens": 0, "output_tokens": 0, "executions": 1,
            "token_covered_executions": 1, "cost_covered_executions": 1,
            "cost_covered_output_tokens": 0,
            "cache_covered_input_tokens": 0,
            "availability": availability,
        }

        result = meter.aggregate_model_stats([{
            "id": "covered-zero", "provider": "opencode", "runtime": "OpenCode",
            "availability": availability, "model_stats": [stats],
            "_model_daily": [{**stats, "day": "2026-08-27"}],
        }])

        self.assertEqual(result["total_models"], 1)
        row = result["models"][0]
        self.assertEqual(row["input_tokens"], 0)
        self.assertEqual(row["output_tokens"], 0)
        self.assertEqual(row["cost_covered_output_tokens"], 0)
        self.assertTrue(row["availability"]["input_tokens"])
        self.assertTrue(row["availability"]["output_tokens"])
        self.assertTrue(row["daily"][0]["availability"]["output_tokens"])

    def test_model_aggregation_splits_same_model_by_runtime(self):
        def session(runtime, output):
            return {
                "provider": "claude", "runtime": runtime,
                "model_stats": [{"model": "claude-opus", "cost": 1, "tokens": 100,
                                 "input_tokens": 90, "output_tokens": output, "executions": 1}],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
            }
        result = meter.aggregate_model_stats([
            session("Claude Code", 10), session("Claude-3P", 20),
        ])
        self.assertEqual(len(result["models"]), 2)
        self.assertEqual(
            {(row["model"], row["runtime"], row["id"]) for row in result["models"]},
            {
                ("claude-opus", "Claude Code", "claude-opus::Claude Code::unavailable"),
                ("claude-opus", "Claude-3P", "claude-opus::Claude-3P::unavailable"),
            },
        )

    def test_model_aggregation_gives_unreported_effort_an_explicit_identity(self):
        result = meter.aggregate_model_stats([{
            "provider": "codex", "runtime": "Codex",
            "model_stats": [{
                "model": "gpt-5.6", "cost": 1, "tokens": 100,
                "input_tokens": 80, "output_tokens": 20, "executions": 1,
            }],
            "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
        }])
        self.assertEqual(result["models"][0]["id"], "gpt-5.6::Codex::unavailable")

    def test_model_aggregation_keeps_trace_reported_session_effort_per_runtime(self):
        def session(effort):
            return {
                "provider": "codex", "runtime": "Codex",
                "reasoning_effort": effort,
                "model_stats": [{
                    "model": "gpt-5.6", "cost": 1, "tokens": 100,
                    "input_tokens": 80, "output_tokens": 20, "executions": 1,
                }],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
            }

        rows = meter.aggregate_model_stats([
            session("high"), session("xhigh"), session("private client note 42"),
            session(""),
        ])["models"]

        self.assertEqual(
            {(row.get("reasoning_effort"), tuple(row.get("reasoning_efforts") or ()))
             for row in rows},
            {("high", ("high",)), ("xhigh", ("xhigh",)), (None, ())},
        )

    def test_model_aggregation_scopes_effort_to_single_model_sessions_and_days(self):
        def stats(model):
            return {
                "model": model, "cost": 1, "tokens": 100,
                "input_tokens": 80, "output_tokens": 20, "executions": 1,
            }

        result = meter.aggregate_model_stats([
            {
                "provider": "codex", "runtime": "Codex", "reasoning_effort": "high",
                "model_stats": [stats("gpt-5.6")],
                "_model_daily": [{**stats("gpt-5.6"), "day": "2026-08-30"}],
            },
            {
                "provider": "codex", "runtime": "Codex", "reasoning_effort": "xhigh",
                "model_stats": [stats("gpt-5.6")],
                "_model_daily": [{**stats("gpt-5.6"), "day": "2026-08-31"}],
            },
            {
                "provider": "codex", "runtime": "Codex", "reasoning_effort": "ultra",
                "primary_model": "gpt-a",
                "model_stats": [stats("gpt-a"), stats("gpt-b")],
                "_model_daily": [
                    {**stats("gpt-a"), "day": "2026-08-31"},
                    {**stats("gpt-b"), "day": "2026-08-31"},
                ],
            },
        ])
        by_model_effort = {
            (row["model"], row.get("reasoning_effort")): row
            for row in result["models"]
        }

        self.assertEqual(
            by_model_effort[("gpt-5.6", "high")]["daily"][0]["reasoning_efforts"],
            ["high"],
        )
        self.assertEqual(
            by_model_effort[("gpt-5.6", "xhigh")]["daily"][0]["reasoning_efforts"],
            ["xhigh"],
        )
        self.assertEqual(by_model_effort[("gpt-a", "ultra")]["reasoning_efforts"], ["ultra"])
        self.assertEqual(by_model_effort[("gpt-b", None)]["reasoning_efforts"], [])

    def test_project_model_stats_scopes_exactly_and_omits_session_identity(self):
        def session(session_id, project, output):
            return {
                "id": session_id, "path": f"/private/logs/{session_id}.jsonl",
                "title": f"Private title {session_id}",
                "provider": "codex", "runtime": "Codex", "project": project,
                "availability": meter.metric_availability("codex"),
                "model_stats": [{
                    "model": "gpt-5.6", "cost": 1, "tokens": 100 + output,
                    "input_tokens": 100, "output_tokens": output, "executions": 1,
                }],
                "_model_daily": [{
                    "model": "gpt-5.6", "day": "2026-07-30", "cost": 1,
                    "input_tokens": 100, "output_tokens": output, "executions": 1,
                }],
                "_performance_samples": [], "_wait_samples": [],
            }

        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["internal_rows"] = (
                session("secret-a", "/repo/a", 10),
                session("secret-b", "/repo/b", 40),
            )
            meter._xsess["project_model_stats"] = {}
            with mock.patch.object(meter, "cross_session",
                                   return_value={"generated_at": 123}):
                payload, status = meter.project_model_stats("/repo/a")
                missing, missing_status = meter.project_model_stats("")
                unknown, unknown_status = meter.project_model_stats("/repo/unknown")
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["generated_at"], 123)
        self.assertEqual(payload["model_stats"]["models"][0]["output_tokens"], 10)
        self.assertNotIn("projects", payload["model_stats"])
        encoded = json.dumps(payload)
        self.assertNotIn("/repo/a", encoded)
        self.assertNotIn("/private/logs", encoded)
        self.assertNotIn("secret-a", encoded)
        self.assertNotIn("Private title", encoded)
        self.assertEqual(missing_status, 400)
        self.assertFalse(missing["ok"])
        self.assertEqual(unknown_status, 404)
        self.assertFalse(unknown["ok"])
        self.assertIn(
            'elif req_path == "/model-stats":',
            Path(meter.IMPLEMENTATION_FILE).read_text(),
        )

    def test_project_model_stats_groups_non_folder_labels_as_other_local_sessions(self):
        def session(session_id, project):
            return {
                "id": session_id, "project": project, "provider": "codex",
                "runtime": "Codex", "model_stats": [{
                    "model": "gpt-5.6", "cost": 1, "tokens": 100,
                    "input_tokens": 80, "output_tokens": 20, "executions": 1,
                }],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
            }

        saved_cache = dict(meter._xsess)
        try:
            meter._xsess.update({
                "internal_rows": (session("cli", "CLI"), session("none", "No project")),
                "project_model_stats": {},
            })
            with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}):
                payload, status = meter.project_model_stats("__other_local_sessions__")
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(status, 200)
        self.assertEqual(payload["model_stats"]["total_models"], 1)

    def test_model_project_options_are_sorted_and_bounded(self):
        sessions = [
            {"project": f"/repo/{index:04d}", "provider": "codex",
             "model_stats": [], "_model_daily": [], "_performance_samples": [],
             "_wait_samples": []}
            for index in range(meter.MODEL_PROJECT_OPTION_LIMIT + 1)
        ]
        result = meter.aggregate_model_stats(reversed(sessions))
        self.assertEqual(len(result["projects"]), meter.MODEL_PROJECT_OPTION_LIMIT)
        self.assertEqual(result["projects"][0], "/repo/0000")
        self.assertTrue(result["projects_truncated"])

    def test_cursor_samples_are_excluded_from_matched_pace(self):
        samples = [{
            "model": "gpt-5.6", "day": "2026-07-20", "ts": index + 1,
            "input_tokens": 100, "peak_input_tokens": 100, "output_tokens": 20,
            "duration_s": 2, "generation_s": 2, "tool_calls": 0, "model_calls": 1,
        } for index in range(25)]
        cursor = {
            "id": "cursor", "provider": "cursor", "runtime": "Cursor",
            "token_estimate": True,
            "availability": meter.metric_availability(
                "cursor", cost=True, tokens=True, input_tokens=True,
                output_tokens=True, throughput=True, timing=True,
            ),
            "model_stats": [{"model": "gpt-5.6", "cost": 1, "tokens": 120,
                              "input_tokens": 100, "output_tokens": 20, "executions": 1}],
            "_model_daily": [], "_performance_samples": samples, "_wait_samples": [],
        }
        codex = {
            **cursor, "id": "codex", "provider": "codex", "runtime": "Codex",
            "token_estimate": False, "availability": meter.metric_availability("codex"),
        }
        result = meter.aggregate_model_stats([cursor, codex])
        self.assertEqual({row["id"] for row in result["models"]},
                         {"gpt-5.6::Cursor::unavailable", "gpt-5.6::Codex::unavailable"})
        self.assertFalse(any(
            any("::Cursor" in value for value in (pair["a_id"], pair["b_id"]))
            for pairs in result["matched_pace"]["windows"].values() for pair in pairs
        ))

    def test_runtime_label_distinguishes_claude_desktop_roots(self):
        standard = {
            "provider": "claude", "client": "claude_desktop",
            "metadata_path": str(Path(meter.CLAUDE_DESKTOP_DATA_ROOTS[0]) / "sessions" / "one.json"),
        }
        third_party = {
            "provider": "claude", "client": "claude_desktop",
            "metadata_path": str(Path(meter.CLAUDE_DESKTOP_DATA_ROOTS[1]) / "sessions" / "two.json"),
        }
        self.assertEqual(meter.source_runtime_label(standard), "Claude Desktop")
        self.assertEqual(meter.source_runtime_label(third_party), "Claude-3P")
        self.assertEqual(meter.source_runtime_label({"provider": "claude", "client": "claude_code"}),
                         "Claude Code")
        self.assertEqual(meter.source_runtime_label({"provider": "codex"}), "Codex")
        self.assertEqual(meter.source_runtime_label({"provider": "cursor"}), "Cursor")

    def test_matched_pace_has_exact_today_and_yesterday_windows(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(day, start):
            return [{
                "duration_s": 10, "ts": start + index, "day": day,
                "input_tokens": 10000, "peak_input_tokens": 10000,
                "cache_read_tokens": 0, "output_tokens": 1000,
                "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {
            runtime: (
                samples("2026-08-11", offset)
                + samples("2026-08-10", offset + 100)
                + samples("2026-07-01", offset + 200)
            )
            for runtime, offset in (("alpha", 0), ("beta", 1000))
        }

        windows = meter.matched_pace_windows(groups, now_ts=now)["windows"]

        self.assertEqual(
            list(windows),
            ["today", "yesterday", "7", "30", "90", "month", "last_month", "all"],
        )
        self.assertEqual(windows["month"][0]["a_samples"], 40)
        for name in ("today", "yesterday"):
            self.assertEqual(len(windows[name]), 1)
            self.assertEqual(windows[name][0]["a_samples"], 20)
            self.assertEqual(windows[name][0]["b_samples"], 20)
        self.assertEqual(windows["7"][0]["a_samples"], 40)
        self.assertEqual(windows["7"][0]["b_samples"], 40)
        self.assertEqual(windows["all"][0]["a_samples"], 60)
        self.assertEqual(windows["all"][0]["b_samples"], 60)
        self.assertEqual(windows["last_month"][0]["a_samples"], 20)
        self.assertEqual(windows["last_month"][0]["b_samples"], 20)
        self.assertEqual(windows["month"][0]["a_samples"], 40)
        self.assertEqual(windows["month"][0]["b_samples"], 40)

    def test_matched_pace_reuses_unchanged_inputs_and_invalidates_changed_samples(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {"alpha": samples(10, 0), "beta": samples(20, 100)}
        original = meter.matched_pace_comparison
        with mock.patch.object(
            meter, "matched_pace_comparison", wraps=original,
        ) as comparison:
            first = meter.matched_pace_windows(groups, now_ts=now)
            first_call_count = comparison.call_count
            second = meter.matched_pace_windows(groups, now_ts=now)
            unchanged_call_count = comparison.call_count
            changed_groups = {**groups, "beta": samples(30, 100)}
            changed = meter.matched_pace_windows(changed_groups, now_ts=now)

        self.assertGreater(first_call_count, 0)
        self.assertEqual(unchanged_call_count, first_call_count)
        self.assertEqual(second, first)
        # Published snapshots are never mutated, so the cached windows are shared.
        self.assertIs(second, first)
        self.assertGreater(comparison.call_count, unchanged_call_count)
        self.assertEqual(first["windows"]["today"][0]["pace_ratio"], 2)
        self.assertEqual(changed["windows"]["today"][0]["pace_ratio"], 3)

    def test_matched_pace_rebuilds_only_pairs_touching_a_changed_model(self):
        """A new turn for one model must not recompute every model pair."""
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        names = ("alpha", "beta", "gamma", "delta")
        groups = {name: samples(10 + index, index * 100)
                  for index, name in enumerate(names)}
        original = meter.matched_pace_comparison
        with mock.patch.object(
            meter, "matched_pace_comparison", wraps=original,
        ) as comparison:
            meter.matched_pace_windows(groups, now_ts=now)
            first_call_count = comparison.call_count
            changed_groups = {**groups, "gamma": samples(99, 500)}
            meter.matched_pace_windows(changed_groups, now_ts=now)
            after_change = comparison.call_count

        # Four models form six pairs across every window. Only the three pairs
        # that include the changed model may be recomputed.
        windows = len(meter.MATCHED_PACE_WINDOW_KEYS)
        self.assertEqual(first_call_count, 6 * windows)
        self.assertEqual(after_change - first_call_count, 3 * windows)

    def test_matched_pace_cached_pairs_match_a_cold_computation(self):
        """Per-pair caching must not change any reported comparison."""
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {name: samples(10 + index, index * 100)
                  for index, name in enumerate(("alpha", "beta", "gamma"))}
        with_cached_pairs = meter.matched_pace_windows(groups, now_ts=now)
        meter._matched_pace_pair_cache.clear()
        meter._matched_pace_cache["signature"] = None
        meter._matched_pace_cache["data"] = None
        cold = meter.matched_pace_windows(groups, now_ts=now)

        self.assertEqual(cold, with_cached_pairs)

    def test_matched_pace_reuses_a_persisted_pair_cache_after_restart(self):
        """A restart must reuse stored pairs instead of rebuilding every pair."""
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {name: samples(10 + index, index * 100)
                  for index, name in enumerate(("alpha", "beta", "gamma"))}
        original = meter.matched_pace_comparison
        with mock.patch.object(
            meter, "matched_pace_comparison", wraps=original,
        ) as comparison:
            first = meter.matched_pace_windows(groups, now_ts=now)
            first_calls = comparison.call_count
            # Simulate a process restart: drop in-memory state, keep the file.
            meter._matched_pace_pair_cache.clear()
            meter._matched_pace_pair_cache_loaded = False
            meter._matched_pace_cache["signature"] = None
            meter._matched_pace_cache["data"] = None
            restarted = meter.matched_pace_windows(groups, now_ts=now)
            restarted_calls = comparison.call_count - first_calls

        self.assertGreater(first_calls, 0)
        self.assertTrue(self._pace_cache_path.exists())
        self.assertEqual(
            restarted_calls, 0,
            "a persisted pair cache must avoid recomputing unchanged pairs",
        )
        self.assertEqual(restarted, first)

    def test_matched_pace_recovers_from_an_unusable_cache_file(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {"alpha": samples(10, 0), "beta": samples(20, 100)}
        for payload in (
            "{ not json",
            json.dumps({"schema": 999, "pairs": "unexpected"}),
            json.dumps({
                "schema": meter.MATCHED_PACE_CACHE_SCHEMA,
                "pairs": [{"a_id": 5}, "junk"],
            }),
        ):
            with self.subTest(payload=payload[:24]):
                self._pace_cache_path.write_text(payload)
                meter._matched_pace_pair_cache.clear()
                meter._matched_pace_pair_cache_loaded = False
                meter._matched_pace_cache["signature"] = None
                meter._matched_pace_cache["data"] = None
                result = meter.matched_pace_windows(groups, now_ts=now)
                self.assertEqual(len(result["windows"]["today"]), 1)

    def test_matched_pace_rebuild_releases_the_cache_lock(self):
        """A slow rebuild must not block unrelated cross-session requests."""
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()

        def samples(duration, offset):
            return [{
                "duration_s": duration, "ts": now + offset + index,
                "day": "2026-08-11", "input_tokens": 10000,
                "peak_input_tokens": 10000, "cache_read_tokens": 0,
                "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
            } for index in range(20)]

        groups = {"alpha": samples(10, 0), "beta": samples(20, 100)}
        entered = threading.Event()
        release = threading.Event()
        real_build = meter._build_matched_pace_windows
        acquired = False

        def blocking_build(*args, **kwargs):
            entered.set()
            release.wait(timeout=10)
            return real_build(*args, **kwargs)

        with mock.patch.object(
            meter, "_build_matched_pace_windows", side_effect=blocking_build,
        ):
            worker = threading.Thread(
                target=meter.matched_pace_windows,
                args=(groups,), kwargs={"now_ts": now}, daemon=True,
            )
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                acquired = meter._matched_pace_cache_lock.acquire(timeout=5)
                if acquired:
                    meter._matched_pace_cache_lock.release()
            finally:
                release.set()
                worker.join(timeout=10)

        self.assertTrue(
            acquired,
            "matched_pace_windows must not hold the cache lock while rebuilding",
        )

    @staticmethod
    def _pace_groups(now, names=("alpha", "beta", "gamma"), day_offsets=(0,)):
        """Build 20 timed turns per model on each day offset from ``now``."""
        def samples(duration, offset):
            rows = []
            for day_offset in day_offsets:
                ts = now - day_offset * 86400
                day = datetime.date.fromtimestamp(ts).isoformat()
                rows.extend({
                    "duration_s": duration + day_offset, "ts": ts + offset + index,
                    "day": day, "input_tokens": 10000,
                    "peak_input_tokens": 10000, "cache_read_tokens": 0,
                    "output_tokens": 1000, "tool_calls": 0, "model_calls": 1,
                } for index in range(20))
            return rows

        return {name: samples(10 + index, index * 100)
                for index, name in enumerate(names)}

    @staticmethod
    def _drop_pace_memory(loaded=False):
        """Drop in-memory matched-pace state; ``loaded=False`` simulates restart."""
        meter._matched_pace_pair_cache.clear()
        meter._matched_pace_pair_cache_loaded = loaded
        meter._matched_pace_cache["signature"] = None
        meter._matched_pace_cache["data"] = None

    def _cold_pace(self, groups, now):
        self._drop_pace_memory(loaded=True)
        return meter.matched_pace_windows(groups, now_ts=now)

    def test_matched_pace_pair_cache_does_not_reuse_previous_day_windows(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        tomorrow = now + 86400
        groups = self._pace_groups(now, day_offsets=(0, 1))
        meter.matched_pace_windows(groups, now_ts=now)
        rolled = meter.matched_pace_windows(groups, now_ts=tomorrow)
        cold = self._cold_pace(groups, tomorrow)

        self.assertEqual(rolled, cold)
        # The previous "today" samples are now "yesterday"; nothing is today.
        self.assertFalse(any(row["available"] for row in rolled["windows"]["today"]))
        self.assertTrue(all(row["available"] for row in rolled["windows"]["yesterday"]))

    def test_matched_pace_persisted_pairs_do_not_survive_a_day_rollover(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        tomorrow = now + 86400
        groups = self._pace_groups(now, day_offsets=(0, 1))
        meter.matched_pace_windows(groups, now_ts=now)
        self.assertTrue(self._pace_cache_path.exists())
        self._drop_pace_memory()
        reloaded = meter.matched_pace_windows(groups, now_ts=tomorrow)
        cold = self._cold_pace(groups, tomorrow)

        self.assertEqual(reloaded, cold)

    def test_matched_pace_rejects_incomplete_or_non_finite_persisted_entries(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        expected = meter.matched_pace_windows(groups, now_ts=now)
        stored = json.loads(self._pace_cache_path.read_text())

        def corrupt_missing(entry):
            entry["windows"].pop("today")
            entry["windows"].pop("yesterday")

        def corrupt_nan(entry):
            entry["windows"]["today"]["coverage"] = float("nan")

        def corrupt_infinite(entry):
            entry["windows"]["all"]["pace_ratio"] = float("inf")

        def corrupt_type(entry):
            entry["windows"]["7"]["matched_pairs"] = "12"

        for corrupt in (corrupt_missing, corrupt_nan, corrupt_infinite, corrupt_type):
            with self.subTest(corruption=corrupt.__name__):
                payload = json.loads(json.dumps(stored))
                for entry in payload["pairs"]:
                    corrupt(entry)
                self._pace_cache_path.write_text(json.dumps(payload))
                self._drop_pace_memory()
                result = meter.matched_pace_windows(groups, now_ts=now)
                self.assertEqual(result, expected)
                self.assertEqual(meter._matched_pace_pair_cache_loaded, True)
                self.assertEqual(len(meter._matched_pace_pair_cache), 3)

    def test_matched_pace_ignores_an_undecodable_cache_file(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        expected = self._cold_pace(groups, now)
        self._pace_cache_path.write_bytes(b'{"schema": \xff\xfe\x80 }')
        self._drop_pace_memory()

        self.assertEqual(meter.matched_pace_windows(groups, now_ts=now), expected)

    def test_project_scoped_matched_pace_leaves_the_global_pair_cache_intact(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        global_groups = self._pace_groups(now, names=("alpha", "beta", "gamma", "delta"))
        project_groups = {
            name: rows[:15] for name, rows in global_groups.items()
            if name in ("alpha", "beta")
        }
        first = meter.matched_pace_windows(global_groups, now_ts=now)
        persisted = self._pace_cache_path.read_bytes()
        project = meter.matched_pace_windows(
            project_groups, now_ts=now, persistent=False,
        )
        self.assertEqual(self._pace_cache_path.read_bytes(), persisted)
        self.assertEqual(project, self._cold_pace(project_groups, now))
        # Restore the global in-memory state the cold check discarded.
        self._drop_pace_memory()
        meter.matched_pace_windows(global_groups, now_ts=now)
        meter.matched_pace_windows(project_groups, now_ts=now, persistent=False)
        meter._matched_pace_cache["signature"] = None

        original = meter.matched_pace_comparison
        with mock.patch.object(
            meter, "matched_pace_comparison", wraps=original,
        ) as comparison:
            again = meter.matched_pace_windows(global_groups, now_ts=now)
        self.assertEqual(comparison.call_count, 0)
        self.assertEqual(again, first)
        self.assertEqual(self._pace_cache_path.read_bytes(), persisted)

    def test_project_model_stats_uses_the_non_persistent_pace_scope(self):
        row = {
            "id": "s", "project": "/repo/a", "provider": "codex",
            "runtime": "Codex", "model_stats": [{
                "model": "gpt-5.6", "cost": 1, "tokens": 100,
                "input_tokens": 80, "output_tokens": 20, "executions": 1,
            }],
            "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
        }
        saved_cache = dict(meter._xsess)
        original = meter.matched_pace_windows
        try:
            meter._xsess.update({"internal_rows": (row,), "project_model_stats": {}})
            with mock.patch.object(meter, "cross_session",
                                   return_value={"generated_at": 1}), \
                    mock.patch.object(meter, "matched_pace_windows",
                                      wraps=original) as pace:
                _, status = meter.project_model_stats("/repo/a")
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(status, 200)
        self.assertEqual(pace.call_count, 1)
        self.assertIs(pace.call_args.kwargs.get("persistent"), False)

    def test_matched_pace_skips_the_write_when_no_pair_changed(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        meter.matched_pace_windows(groups, now_ts=now)
        self.assertTrue(self._pace_cache_path.exists())
        # Force a rebuild that finds every pair already cached: a restart that
        # reloads the file, then a changed whole-result signature.
        self._drop_pace_memory()
        with mock.patch.object(meter, "atomic_write_text") as write:
            meter.matched_pace_windows(groups, now_ts=now)
            meter._matched_pace_cache["signature"] = None
            meter.matched_pace_windows(groups, now_ts=now)
        write.assert_not_called()

        changed = {**groups, "gamma": groups["gamma"][:-1]}
        with mock.patch.object(meter, "atomic_write_text") as write:
            meter.matched_pace_windows(changed, now_ts=now)
        self.assertEqual(write.call_count, 1)

    def test_matched_pace_pair_cache_is_bounded_on_load_and_in_memory(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now, names=("alpha", "beta", "gamma", "delta"))
        with mock.patch.object(meter, "MATCHED_PACE_CACHE_MAX_PAIRS", 2):
            first = meter.matched_pace_windows(groups, now_ts=now)
            self.assertEqual(len(meter._matched_pace_pair_cache), 2)
            self.assertEqual(len(json.loads(
                self._pace_cache_path.read_text())["pairs"]), 2)
            self.assertEqual(first, self._cold_pace(groups, now))
        meter._matched_pace_cache["signature"] = None
        meter.matched_pace_windows(groups, now_ts=now)
        stored = json.loads(self._pace_cache_path.read_text())
        self.assertEqual(len(stored["pairs"]), 6)
        with mock.patch.object(meter, "MATCHED_PACE_CACHE_MAX_PAIRS", 3):
            self._drop_pace_memory()
            with mock.patch.object(meter, "_build_matched_pace_windows",
                                   return_value=({}, False)):
                meter.matched_pace_windows(groups, now_ts=now)
            self.assertEqual(len(meter._matched_pace_pair_cache), 3)

    def test_matched_pace_skips_entries_with_huge_integers_without_raising(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        expected = meter.matched_pace_windows(groups, now_ts=now)
        stored = json.loads(self._pace_cache_path.read_text())
        huge = "9" * 400
        # json.loads decodes a bare 400-digit literal as a Python int, for
        # which math.isfinite raises OverflowError.
        for field in ("pace_ratio", "matched_pairs"):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(stored))
                payload["pairs"][0]["windows"]["all"][field] = "HUGE"
                text = json.dumps(payload).replace('"HUGE"', huge)
                self._pace_cache_path.write_text(text)
                self._drop_pace_memory()
                original = meter.matched_pace_comparison
                with mock.patch.object(
                    meter, "matched_pace_comparison", wraps=original,
                ) as comparison:
                    result = meter.matched_pace_windows(groups, now_ts=now)
                self.assertEqual(result, expected)
                self.assertEqual(result, self._cold_pace(groups, now))
                # Only the corrupted pair is recomputed; the others are kept.
                self.assertEqual(
                    comparison.call_count, len(meter.MATCHED_PACE_WINDOW_KEYS),
                )

    def test_matched_pace_persisted_entries_cannot_inject_output_fields(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        expected = meter.matched_pace_windows(groups, now_ts=now)
        stored = json.loads(self._pace_cache_path.read_text())

        def inject_key(window):
            window["note"] = "/Users/x/secret"

        def inject_reason(window):
            window["reason"] = "/Users/x/secret"

        def long_reason(window):
            window["reason"] = "only 1 comparable turns; needs 20" + "x" * 300

        def negative_ratio(window):
            window["pace_ratio"] = -1.5

        def negative_count(window):
            window["a_samples"] = -3

        def absurd_ratio(window):
            window["ci_high"] = 1e300

        def missing_key(window):
            window.pop("reason")

        for corrupt in (inject_key, inject_reason, long_reason, negative_ratio,
                        negative_count, absurd_ratio, missing_key):
            with self.subTest(corruption=corrupt.__name__):
                payload = json.loads(json.dumps(stored))
                for entry in payload["pairs"]:
                    corrupt(entry["windows"]["7"])
                self._pace_cache_path.write_text(json.dumps(payload))
                self._drop_pace_memory()
                result = meter.matched_pace_windows(groups, now_ts=now)
                self.assertEqual(result, expected)
                self.assertNotIn("/Users/x/secret", json.dumps(result))
                self.assertNotIn("note", json.dumps(result))

    def test_matched_pace_over_cap_rebuilds_do_not_rewrite_the_cache(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now, names=("alpha", "beta", "gamma", "delta"))
        with mock.patch.object(meter, "MATCHED_PACE_CACHE_MAX_PAIRS", 2):
            first = meter.matched_pace_windows(groups, now_ts=now)
            kept = set(meter._matched_pace_pair_cache)
            with mock.patch.object(meter, "atomic_write_text") as write:
                for _ in range(3):
                    meter._matched_pace_cache["signature"] = None
                    again = meter.matched_pace_windows(groups, now_ts=now)
                    self.assertEqual(again, first)
            write.assert_not_called()
            self.assertEqual(set(meter._matched_pace_pair_cache), kept)
            self.assertEqual(first, self._cold_pace(groups, now))

    def test_matched_pace_concurrent_callers_trigger_one_build(self):
        now = datetime.datetime(2026, 8, 11, 12, 0, 0).timestamp()
        groups = self._pace_groups(now)
        callers = 4
        waiting = set()
        waiting_lock = threading.Lock()
        all_waiting = threading.Event()

        class CountingCondition(type(meter._matched_pace_build_condition)):
            def wait(self, timeout=None):
                with waiting_lock:
                    waiting.add(threading.get_ident())
                    if len(waiting) >= callers - 1:
                        all_waiting.set()
                return super().wait(timeout)

        real_build = meter._build_matched_pace_windows
        builds = []

        def gated_build(*args, **kwargs):
            builds.append(threading.get_ident())
            all_waiting.wait(timeout=10)
            return real_build(*args, **kwargs)

        results = [None] * callers

        def call(index):
            results[index] = meter.matched_pace_windows(groups, now_ts=now)

        with mock.patch.object(meter, "_matched_pace_build_condition",
                               CountingCondition(threading.Lock())), \
                mock.patch.object(meter, "_build_matched_pace_windows",
                                  side_effect=gated_build):
            threads = [threading.Thread(target=call, args=(index,), daemon=True)
                       for index in range(callers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertTrue(all_waiting.is_set())
        self.assertEqual(len(builds), 1)
        self.assertTrue(all(result is not None for result in results))
        self.assertTrue(all(result == results[0] for result in results))

    def test_matched_pace_reports_ratio_confidence_and_coverage(self):
        def sample(duration, ts):
            return {
                "duration_s": duration, "ts": ts, "day": "2026-07-13",
                "input_tokens": 100000, "peak_input_tokens": 50000,
                "cache_read_tokens": 70000, "output_tokens": 2000,
                "tool_calls": 4, "model_calls": 3,
            }
        left = [sample(10, index * 60) for index in range(24)]
        right = [sample(20, index * 60 + 1) for index in range(24)]
        comparison = meter.matched_pace_comparison("left", left, "right", right)
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["matched_pairs"], 24)
        self.assertEqual(comparison["coverage"], 1)
        self.assertEqual(comparison["pace_ratio"], 2)
        self.assertEqual(comparison["ci_low"], 2)
        self.assertEqual(comparison["ci_high"], 2)

    def test_matched_pace_withholds_sparse_or_different_tool_shapes(self):
        base = {
            "duration_s": 10, "ts": 1, "day": "2026-07-13",
            "input_tokens": 10000, "peak_input_tokens": 10000,
            "output_tokens": 1000, "model_calls": 1, "cache_read_tokens": 0,
        }
        sparse = meter.matched_pace_comparison(
            "a", [{**base, "tool_calls": 0}] * 19,
            "b", [{**base, "tool_calls": 0}] * 19,
        )
        different = meter.matched_pace_comparison(
            "a", [{**base, "tool_calls": 0}] * 20,
            "b", [{**base, "tool_calls": 1}] * 20,
        )
        self.assertFalse(sparse["available"])
        self.assertIn("20 timed turns", sparse["reason"])
        self.assertFalse(different["available"])
        self.assertEqual(different["matched_pairs"], 0)
        self.assertIn("comparable turns", different["reason"])

    def test_matched_pace_completes_under_large_sample_volume(self):
        def sample(idx, duration):
            return {
                "duration_s": duration, "ts": 1000000 + idx * 60,
                "day": "2026-08-01",
                "input_tokens": 100000, "peak_input_tokens": 100000,
                "cache_read_tokens": 70000, "output_tokens": 2000,
                "tool_calls": 4, "model_calls": 3,
            }
        left = [sample(i, 10) for i in range(2000)]
        right = [sample(i, 20) for i in range(2000)]
        session_rows = [
            {
                "id": f"ses_{i}", "provider": "opencode",
                "runtime": "OpenCode", "project": f"project-{i % 10}",
                "availability": {"cost": True, "tokens": True, "cache": True},
                "model_stats": [
                    {"model": f"model-{i % 3}", "cost": 1.0, "tokens": 5000,
                     "input_tokens": 3000, "output_tokens": 2000,
                     "executions": 10, "availability": {"cost": True, "tokens": True}},
                ],
                "_model_daily": [],
                "_performance_samples": left if i == 0 else [],
                "_wait_samples": [],
                "_tool_evidence": meter.summarize_tool_evidence([]),
            }
            for i in range(50)
        ]
        session_rows.append({
            "id": "ses_big", "provider": "opencode",
            "runtime": "OpenCode", "project": "big-project",
            "availability": {"cost": True, "tokens": True, "cache": True},
            "model_stats": [
                {"model": "model-0", "cost": 50.0, "tokens": 250000,
                 "input_tokens": 150000, "output_tokens": 100000,
                 "executions": 500, "availability": {"cost": True, "tokens": True}},
            ],
            "_model_daily": [],
            "_performance_samples": right,
            "_wait_samples": [],
            "_tool_evidence": meter.summarize_tool_evidence([]),
        })
        start = time.time()
        result = meter.aggregate_model_stats(session_rows)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0,
                        f"aggregate_model_stats took {elapsed:.1f}s with 4000 samples; "
                        "matched_pace_comparison may be unbounded")
        self.assertGreaterEqual(len(result.get("models", [])), 1)


if __name__ == "__main__":
    unittest.main()
