import unittest
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
import meter


class SessionSummaryStatsTests(unittest.TestCase):
    def source(self, provider, model=None):
        return {
            "id": "session", "path": "/tmp/session.jsonl", "provider": provider,
            "client": provider, "label": provider.title(), "project": "/repo",
            "mtime": 1, "title": "Summary stats", "model": model,
        }

    def test_claude_summary_exposes_input_output_and_model_stats(self):
        objs = [{
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 20,
                          "cache_read_input_tokens": 30, "output_tokens": 10},
                "stop_reason": "end_turn",
            },
        }]
        row = meter.claude_summary(self.source("claude"), objs)
        self.assertEqual(row["input_tokens"], 150)
        self.assertEqual(row["output_tokens"], 10)
        self.assertEqual(row["model_stats"][0]["model"], "claude-sonnet-4-6")
        self.assertEqual(row["model_stats"][0]["executions"], 1)
        self.assertEqual(row["model_stats"][0]["reasoning_executions"], 0)
        self.assertEqual(row["model_stats"][0]["reasoning_unavailable_executions"], 1)
        self.assertEqual(row["primary_model"], "claude-sonnet-4-6")
        self.assertEqual(row["context"]["latest"], 150)
        self.assertIsNone(row["context"]["window"])
        self.assertEqual(row["_context_samples"], [150])
        self.assertTrue(row["terminal"])
        self.assertEqual(row["usage_basis"], "reported")

    @staticmethod
    def _branch_record(index, branch, sidechain=False):
        return {
            "type": "assistant", "timestamp": "2026-07-02T00:00:{:02d}.000Z".format(index),
            "gitBranch": branch, **({"isSidechain": True} if sidechain else {}),
            "message": {"id": "msg-{}".format(index), "model": "claude-sonnet-4-6", "content": [],
                        "usage": {"input_tokens": 10, "output_tokens": 1}},
        }

    def test_claude_summary_picks_majority_branch_and_ignores_sidechains(self):
        objs = [
            self._branch_record(1, "feat/one"),
            self._branch_record(2, "feat/two"),
            self._branch_record(3, "feat/one"),
            *(self._branch_record(10 + index, "side/agent", sidechain=True) for index in range(4)),
        ]
        row = meter.claude_summary(self.source("claude"), objs)
        self.assertEqual(row["_branch"], "feat/one")

    def test_claude_summary_branch_tie_goes_to_the_latest_branch(self):
        objs = [
            self._branch_record(1, "feat/old"),
            self._branch_record(2, "feat/new"),
        ]
        row = meter.claude_summary(self.source("claude"), objs)
        self.assertEqual(row["_branch"], "feat/new")

    def test_claude_summary_caps_long_branch_names(self):
        row = meter.claude_summary(self.source("claude"), [self._branch_record(1, "b" * 500)])
        self.assertEqual(row["_branch"], "b" * 200)

    def test_codex_summary_reads_branch_only_from_session_meta(self):
        objs = [
            {"type": "session_meta", "timestamp": "2026-07-02T00:00:00.000Z",
             "payload": {"id": "s", "git": {"branch": "fix/codex"}}},
            {"type": "session_meta", "timestamp": "2026-07-02T00:00:00.100Z",
             "payload": {"id": "parent", "git": {"branch": "parent/branch"}}},
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:01.000Z",
             "payload": {"model": "gpt-5.6", "git": {"branch": "not/meta"}}},
        ]
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), objs)
        self.assertEqual(row["_branch"], "fix/codex")

    def test_runtime_without_branch_evidence_has_no_branch_key(self):
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), [
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:01.000Z", "payload": {"model": "gpt-5.6"}},
        ])
        self.assertNotIn("_branch", row)
        self.assertNotIn("branch", row)

    def test_claude_summary_keeps_thinking_visible_without_inventing_reasoning_tokens(self):
        objs = [
            {
                "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
                "message": {
                    "id": "msg-1", "model": "claude-opus-4-8",
                    "content": [{"type": "thinking", "thinking": "hidden"}],
                    "usage": {"input_tokens": 100, "output_tokens": 40},
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
                "message": {
                    "id": "msg-1", "model": "claude-opus-4-8",
                    "content": [{"type": "text", "text": "answer"}],
                    "usage": {"input_tokens": 100, "output_tokens": 40},
                    "stop_reason": "end_turn",
                },
            },
        ]

        row = meter.claude_summary(self.source("claude"), objs)
        stats = row["model_stats"][0]
        self.assertEqual(stats["executions"], 1)
        self.assertEqual(stats["reasoning_tokens"], 0)
        self.assertEqual(stats["reasoning_output_tokens"], 0)
        self.assertEqual(stats["reasoning_executions"], 0)
        self.assertEqual(stats["reasoning_unavailable_executions"], 1)
        self.assertEqual(stats["thinking_executions"], 1)
        self.assertEqual(stats["thinking_covered_executions"], 0)
        aggregate = meter.aggregate_model_stats([row])["models"][0]
        self.assertEqual(aggregate["reasoning_tokens"], 0)
        self.assertEqual(aggregate["reasoning_output_tokens"], 0)
        self.assertFalse(aggregate["availability"]["reasoning_tokens"])
        self.assertFalse(aggregate["coverage"]["reasoning_tokens"]["complete"])
        self.assertEqual(aggregate["thinking_executions"], 1)
        self.assertEqual(aggregate["thinking_covered_executions"], 0)
        self.assertEqual(aggregate["daily"][0]["reasoning_tokens"], 0)
        self.assertEqual(aggregate["daily"][0]["thinking_executions"], 1)
        self.assertEqual(aggregate["daily"][0]["thinking_covered_executions"], 0)

    def test_claude_summary_accepts_separate_thinking_token_stat(self):
        row = meter.claude_summary(self.source("claude"), [{
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "thinking", "thinking": "hidden"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "thinking_tokens": 30,
                },
                "stop_reason": "end_turn",
            },
        }])

        stats = row["model_stats"][0]
        self.assertEqual(stats["reasoning_tokens"], 30)
        self.assertEqual(stats["reasoning_output_tokens"], 40)
        self.assertEqual(stats["reasoning_executions"], 1)
        self.assertEqual(stats["thinking_executions"], 1)
        self.assertEqual(stats["thinking_covered_executions"], 1)

    def test_claude_split_messages_preserve_separate_thinking_token_stat(self):
        thinking = {
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "thinking", "thinking": "hidden"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "thinking_tokens": 30,
                },
                "stop_reason": None,
            },
        }
        answer = {
            "type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
            "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 100, "output_tokens": 40},
                "stop_reason": "end_turn",
            },
        }

        for records in ([thinking, answer], [answer, thinking]):
            with self.subTest(exact_stat_first=records[0] is thinking):
                row = meter.claude_summary(self.source("claude"), records)
                stats = row["model_stats"][0]
                self.assertEqual(stats["reasoning_tokens"], 30)
                self.assertEqual(stats["reasoning_output_tokens"], 40)
                self.assertEqual(stats["thinking_covered_executions"], 1)

                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "claude-thinking.jsonl"
                    path.write_text("".join(
                        json.dumps(record) + "\n" for record in records
                    ))
                    selected = meter.recompute_claude({
                        **self.source("claude"),
                        "path": str(path),
                        "session": path.name,
                    })
                self.assertEqual(selected["semantic"]["reasoning"], 30)
                self.assertEqual(selected["semantic"]["output"], 10)

    def test_claude_selected_session_never_substitutes_output_for_thinking_tokens(self):
        def recompute(thinking_tokens=None, block_type="thinking"):
            usage = {"input_tokens": 100, "output_tokens": 40}
            if thinking_tokens is not None:
                usage["thinking_tokens"] = thinking_tokens
            records = [
                {
                    "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
                    "message": {
                        "id": "msg-1", "model": "claude-opus-4-8",
                        "content": [{"type": block_type, "thinking": "hidden"}],
                        "usage": usage, "stop_reason": "end_turn",
                    },
                },
                {
                    "type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
                    "message": {
                        "id": "msg-1", "model": "claude-opus-4-8",
                        "content": [{"type": "text", "text": "answer"}],
                        "usage": usage, "stop_reason": "end_turn",
                    },
                },
            ]
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "claude-thinking.jsonl"
                path.write_text("".join(json.dumps(record) + "\n" for record in records))
                return meter.recompute_claude({
                    **self.source("claude"), "path": str(path), "session": path.name,
                })

        unavailable = recompute()
        self.assertEqual(unavailable["semantic"]["reasoning"], 0)
        self.assertEqual(unavailable["semantic"]["output"], 40)
        self.assertEqual(unavailable["analyses"]["reasoning"]["tokens"], 0)
        self.assertEqual(unavailable["analyses"]["reasoning"]["think_turns"], 0)
        self.assertEqual(unavailable["series"][0]["reasoning"], 0)
        self.assertEqual(unavailable["executions"][0]["reasoning_tokens"], 0)
        self.assertEqual(unavailable["executions"][0]["tokens"]["reasoning"], 0)
        self.assertFalse(any(
            event["kind"] == "reasoning" for event in unavailable["trace"]
        ))
        message = next(
            event for event in unavailable["trace"] if event["kind"] == "message"
        )
        self.assertEqual(message["reasoning_tokens"], 0)

        redacted = recompute(block_type="redacted_thinking")
        self.assertEqual(redacted["semantic"]["reasoning"], 0)
        self.assertEqual(redacted["executions"][0]["reasoning_tokens"], 0)

        exact = recompute(30)
        self.assertEqual(exact["semantic"]["reasoning"], 30)
        self.assertEqual(exact["semantic"]["output"], 10)
        self.assertEqual(exact["analyses"]["reasoning"]["tokens"], 30)
        self.assertEqual(exact["analyses"]["reasoning"]["think_turns"], 1)
        self.assertEqual(exact["series"][0]["reasoning"], 30)
        self.assertEqual(exact["executions"][0]["reasoning_tokens"], 30)
        self.assertEqual(exact["executions"][0]["tokens"]["reasoning"], 30)

    def test_claude_summary_keeps_missing_model_pricing_unavailable(self):
        row = meter.claude_summary(self.source("claude"), [{
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "content": [],
                "usage": {"input_tokens": 100_000, "output_tokens": 1_000},
                "stop_reason": "end_turn",
            },
        }])

        self.assertEqual(row["primary_model"], "unknown-model")
        self.assertFalse(row["availability"]["cost"])
        self.assertEqual(row["cost"], 0.0)

    def test_claude_and_codex_malformed_token_counts_fail_closed(self):
        for value in ("bad", True, -1, 1.5, float("inf"), 10 ** 400):
            with self.subTest(runtime="claude", value_type=type(value).__name__):
                claude = meter.claude_summary(self.source("claude"), [
                    {"type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
                     "message": {"content": "start"}},
                    {"type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
                     "message": {
                         "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                         "usage": {"input_tokens": value, "output_tokens": value},
                         "stop_reason": "end_turn",
                     }},
                    {"type": "user", "timestamp": "2026-07-02T00:00:02.000Z",
                     "message": {"content": "next"}},
                ])
                self.assertFalse(claude["availability"]["tokens"])
                self.assertFalse(claude["availability"]["input_tokens"])
                self.assertFalse(claude["availability"]["output_tokens"])
                self.assertFalse(claude["availability"]["cost"])
                self.assertEqual(claude["model_stats"][0]["token_covered_executions"], 0)
                self.assertEqual(claude["model_stats"][0]["io_covered_executions"], 0)
                self.assertEqual(claude["model_stats"][0]["cost_covered_executions"], 0)

            with self.subTest(runtime="codex", value_type=type(value).__name__):
                codex = meter.codex_summary(self.source("codex", "gpt-5.6-sol"), [{
                    "timestamp": "2026-07-02T00:00:00.000Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": {
                        "last_token_usage": {
                            "input_tokens": value, "output_tokens": value,
                        },
                    }},
                }])
                self.assertFalse(codex["availability"]["tokens"])
                self.assertFalse(codex["availability"]["input_tokens"])
                self.assertFalse(codex["availability"]["output_tokens"])
                self.assertFalse(codex["availability"]["cost"])
                self.assertEqual(codex["model_stats"][0]["token_covered_executions"], 0)
                self.assertEqual(codex["model_stats"][0]["io_covered_executions"], 0)
                self.assertEqual(codex["model_stats"][0]["cost_covered_executions"], 0)

        output_only = meter.codex_summary(
            self.source("codex", "gpt-5.6-sol"), [{
                "timestamp": "2026-07-02T00:00:00.000Z",
                "type": "event_msg",
                "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": "bad", "output_tokens": 20},
                }},
            }],
        )
        self.assertFalse(output_only["availability"]["cache"])
        self.assertFalse(output_only["model_stats"][0]["availability"]["cache"])
        aggregate = meter.aggregate_model_stats([output_only])["models"][0]
        self.assertFalse(aggregate["availability"]["cache"])
        self.assertFalse(aggregate["coverage"]["cache"]["complete"])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-malformed.jsonl"
            records = [
                {"type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
                 "message": {"content": "start"}},
                {"type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
                 "message": {
                     "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                     "usage": {"input_tokens": "bad", "output_tokens": "bad"},
                     "stop_reason": "end_turn",
                 }},
                {"type": "user", "timestamp": "2026-07-02T00:00:02.000Z",
                 "message": {"content": "next"}},
            ]
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            state = meter.recompute_claude({
                **self.source("claude"), "path": str(path), "session": path.name,
            })
        self.assertIsNotNone(state)
        self.assertFalse(state["availability"]["tokens"])
        self.assertFalse(state["availability"]["cost"])

    def test_claude_summary_keeps_priced_model_coverage_in_mixed_session(self):
        def message(message_id, model, timestamp, output_tokens):
            return {
                "type": "assistant", "timestamp": timestamp,
                "message": {
                    "id": message_id, "model": model, "content": [],
                    "usage": {
                        "input_tokens": 100, "output_tokens": output_tokens,
                    },
                    "stop_reason": "end_turn",
                },
            }

        row = meter.claude_summary(self.source("claude"), [
            message("msg-known", "claude-sonnet-4-6", "2026-07-02T00:00:01.000Z", 20),
            message("msg-unknown", "unknown-model", "2026-07-02T00:00:02.000Z", 30),
        ])

        self.assertFalse(row["availability"]["cost"])
        stats = {item["model"]: item for item in row["model_stats"]}
        self.assertTrue(stats["claude-sonnet-4-6"]["availability"]["cost"])
        self.assertEqual(stats["claude-sonnet-4-6"]["cost_covered_executions"], 1)
        self.assertEqual(stats["claude-sonnet-4-6"]["cost_covered_output_tokens"], 20)
        self.assertFalse(stats["unknown-model"]["availability"]["cost"])
        self.assertEqual(stats["unknown-model"]["cost_covered_executions"], 0)
        daily = {item["model"]: item for item in row["_model_daily"]}
        self.assertTrue(daily["claude-sonnet-4-6"]["availability"]["cost"])
        self.assertFalse(daily["unknown-model"]["availability"]["cost"])

        aggregate = {
            item["model"]: item for item in meter.aggregate_model_stats([row])["models"]
        }
        known = aggregate["claude-sonnet-4-6"]
        self.assertTrue(known["availability"]["cost"])
        self.assertEqual(known["cost_covered_executions"], 1)
        self.assertEqual(known["cost_covered_output_tokens"], 20)
        self.assertTrue(known["daily"][0]["availability"]["cost"])
        self.assertFalse(aggregate["unknown-model"]["availability"]["cost"])

    def test_claude_summary_ignores_zero_usage_synthetic_marker_for_cost_coverage(self):
        def message(message_id, model, input_tokens, output_tokens):
            return {
                "type": "assistant", "timestamp": "2026-07-02T00:00:01.000Z",
                "message": {
                    "id": message_id, "model": model, "content": [],
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": output_tokens,
                    },
                    "stop_reason": "end_turn",
                },
            }

        row = meter.claude_summary(self.source("claude"), [
            message("msg-known", "claude-sonnet-4-6", 100, 20),
            message("msg-synthetic", "<synthetic>", 0, 0),
        ])

        self.assertTrue(row["availability"]["cost"])
        self.assertFalse(row["cost_approx"])
        self.assertGreater(row["cost"], 0)
        self.assertEqual(row["primary_model"], "claude-sonnet-4-6")
        self.assertEqual(row["models"], ["sonnet-4-6"])
        self.assertEqual(row["context"]["latest"], 100)
        self.assertEqual(row["_context_samples"], [100])
        self.assertEqual(row["turns"], 1)
        self.assertTrue(row["terminal"])
        from token_meter.domain.aggregates import aggregate_cross_session_rows
        self.assertEqual(
            aggregate_cross_session_rows([row])["total_executions"], 1,
        )

    def test_claude_summary_synthetic_marker_does_not_extend_timing(self):
        synthetic = self.synthetic_record()
        synthetic["timestamp"] = "2026-07-02T00:01:00.000Z"
        row = meter.claude_summary(self.source("claude"), [{
            "type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {"content": "test request"},
        }, self.claude_usage_row("2026-07-02T00:00:01.000Z"), synthetic])

        self.assertEqual(row["duration_s"], 1)
        self.assertEqual(len(row["_wait_samples"]), 1)
        self.assertEqual(row["_wait_samples"][0]["duration_s"], 1)
        self.assertEqual(row["_wait_samples"][0]["model"], "claude-sonnet-4-6")

    def synthetic_record(self, usage=None):
        if usage is None:
            usage = {
                "input_tokens": 0, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": 0,
            }
        return {
            "type": "assistant", "timestamp": "2026-07-02T00:01:00.000Z",
            "message": {
                "id": "msg-synthetic", "model": "<synthetic>", "content": [],
                "usage": usage,
                "stop_reason": "stop_sequence",
            },
        }

    def test_claude_synthetic_marker_requires_valid_complete_zero_usage(self):
        cases = {
            "malformed": {
                "input_tokens": "bad", "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": "bad",
            },
            "missing-output": {
                "input_tokens": 0, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        for name, usage in cases.items():
            with self.subTest(name=name):
                objs = [self.claude_usage_row(), self.synthetic_record(usage)]

                summary = meter.claude_summary(self.source("claude"), objs)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "session.jsonl"
                    path.write_text("".join(json.dumps(row) + "\n" for row in objs))
                    state = meter.recompute_claude({
                        **self.source("claude"), "path": str(path),
                        "session": path.name,
                    })

                self.assertEqual(summary["turns"], 2)
                self.assertEqual(summary["primary_model"], "<synthetic>")
                self.assertFalse(summary["availability"]["cost"])
                self.assertFalse(state["availability"]["cost"])
                if name == "malformed":
                    self.assertFalse(summary["availability"]["tokens"])
                    self.assertFalse(state["availability"]["tokens"])

    def test_claude_summary_keeps_other_angle_bracket_models(self):
        other = self.synthetic_record()
        other["message"]["model"] = "<local-model>"

        row = meter.claude_summary(
            self.source("claude"), [self.claude_usage_row(), other],
        )

        self.assertEqual(row["turns"], 2)
        self.assertEqual(row["primary_model"], "<local-model>")
        self.assertIn("<local-model>", row["models"])

    def test_claude_only_synthetic_marker_keeps_usage_unavailable(self):
        objs = [self.synthetic_record()]

        summary = meter.claude_summary(self.source("claude"), objs)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps(objs[0]) + "\n")
            state = meter.recompute_claude({
                **self.source("claude"), "path": str(path),
                "session": path.name,
            })

        self.assertEqual(summary["turns"], 0)
        self.assertFalse(summary["availability"]["tokens"])
        self.assertFalse(summary["availability"]["cost"])
        self.assertEqual(state["executions"], [])
        self.assertFalse(state["availability"]["tokens"])
        self.assertFalse(state["availability"]["cost"])

    def test_claude_summary_prices_pseudo_model_records_that_report_tokens(self):
        objs = [
            self.claude_usage_row(),
            self.synthetic_record({"input_tokens": 40, "output_tokens": 5}),
        ]

        row = meter.claude_summary(self.source("claude"), objs)

        self.assertFalse(row["availability"]["cost"])
        self.assertEqual(row["primary_model"], "<synthetic>")

    def test_claude_recompute_keeps_cost_available_with_synthetic_records(self):
        records = [{
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5_000,
                          "cache_read_input_tokens": 5_000, "output_tokens": 10,
                          "cache_creation": {
                              "ephemeral_5m_input_tokens": 5_000,
                              "ephemeral_1h_input_tokens": 0,
                          }},
                "stop_reason": "end_turn",
            },
        }, self.synthetic_record()]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            source = {
                **self.source("claude"), "path": str(path),
                "session": path.name,
            }
            state = meter.recompute_claude(source)

        self.assertTrue(state["availability"]["cost"])
        self.assertFalse(state["cost_approx"])
        self.assertGreater(state["total_cost"], 0)
        self.assertEqual(len(state["executions"]), 1)

    def test_unknown_model_keeps_cache_money_unavailable(self):
        record = {
            "type": "assistant", "timestamp": "2026-07-02T00:00:00.000Z",
            "message": {
                "id": "msg-1", "content": [],
                "usage": {
                    "input_tokens": 10, "cache_creation_input_tokens": 5_000,
                    "cache_read_input_tokens": 5_000, "output_tokens": 10,
                },
                "stop_reason": "end_turn",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps(record) + "\n")
            source = {
                **self.source("claude"), "path": str(path),
                "session": path.name,
            }
            state = meter.recompute_claude(source)

        self.assertFalse(state["availability"]["cost"])
        self.assertTrue(state["availability"]["cache"])
        self.assertEqual(state["cache"]["total"], 10_000)
        self.assertIsNone(state["cache"]["saved"])
        self.assertIsNone(state["cache"]["cost"])
        self.assertIsNone(state["cache_saved"])

    def claude_source_without_title(self):
        return {
            "id": "session", "path": "/tmp/session.jsonl", "provider": "claude",
            "client": "claude", "label": "Claude", "project": "/repo",
            "mtime": 1, "title": None, "model": None,
        }

    def claude_usage_row(self, ts="2026-07-02T00:00:00.000Z"):
        return {
            "type": "assistant", "timestamp": ts,
            "message": {
                "id": "msg-1", "model": "claude-sonnet-4-6", "content": [],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        }

    def test_claude_summary_prefers_custom_title_record_over_prompt_heuristic(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
             "message": {"content": "What does this function do?"}},
            self.claude_usage_row(),
            {"type": "custom-title", "customTitle": "release-triage",
             "sessionId": "session"},
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["title"], "release-triage")
        self.assertEqual(row["session_name"], "release-triage")

    def test_claude_summary_counts_loaded_and_used_skills_and_mcp_servers(self):
        usage = self.claude_usage_row()
        usage["message"]["content"] = [
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "brainstorming"}},
            {"type": "tool_use", "id": "t2", "name": "mcp__context7__query-docs", "input": {}},
            {"type": "tool_use", "id": "t3", "name": "mcp__webex__webex_rooms", "input": {}},
        ]
        objs = [
            {"type": "attachment", "attachment": {
                "type": "skill_listing", "isInitial": True, "skillCount": 3,
                "names": ["brainstorming", "pdf", "docx"],
                "content": "PRIVATE SKILL LISTING TEXT"}},
            {"type": "attachment", "attachment": {
                "type": "deferred_tools_delta", "addedLines": ["PRIVATE LINE"],
                "addedNames": ["CronCreate", "mcp__context7__query-docs",
                               "mcp__context7__resolve-library-id", "mcp__outlook__outlook_send"],
                "removedNames": []}},
            {"type": "attachment", "attachment": {
                "type": "mcp_instructions_delta", "addedNames": ["context7"],
                "addedBlocks": ["PRIVATE INSTRUCTIONS"], "removedNames": []}},
            usage,
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["capabilities"], {
            "skills": {"loaded": 3, "used": 1},
            "mcp_servers": {"loaded": 3, "used": 2},
        })
        self.assertNotIn("PRIVATE", json.dumps(row["capabilities"]))
        self.assertNotIn("PRIVATE", json.dumps(
            {k: v for k, v in row.items() if not k.startswith("_")}, default=str,
        ))

    def test_claude_session_state_counts_capabilities_from_main_trace(self):
        usage = self.claude_usage_row()
        usage["message"]["content"] = [
            {"type": "tool_use", "id": "t1", "name": "mcp__docs__search", "input": {}},
        ]
        records = [
            {"type": "attachment", "attachment": {
                "type": "skill_listing", "names": ["pdf", "docx"], "content": "PRIVATE"}},
            {"type": "attachment", "attachment": {
                "type": "deferred_tools_delta", "addedNames": ["mcp__jira__get"]}},
            usage,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-capabilities.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            state = meter.recompute_claude({
                **self.source("claude"), "path": str(path), "session": path.name,
            })
        self.assertEqual(state["capabilities"], {
            "skills": {"loaded": 2, "used": 0, "basis": "session"},
            "mcp_servers": {"loaded": 2, "used": 1, "basis": "session"},
        })
        self.assertNotIn("PRIVATE", json.dumps(meter.dashboard_state_payload(state), default=str))

    def test_codex_skill_listing_and_namespaced_mcp_calls_are_counted(self):
        from token_meter.runtimes.codex import codex_mcp_tool_name, _loaded_skill_names
        objs = [
            {"type": "world_state", "payload": {"state": {"host_skills": {
                "body": "- pdf: PRIVATE (file: /h/.codex/skills/pdf/SKILL.md)\n"
                        "- docx: PRIVATE (file: /h/.codex/skills/docx/SKILL.md)"}}}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
                {"type": "input_text", "text": "<skills_instructions>- xlsx (file: /p/skills/xlsx/SKILL.md)</skills_instructions>"},
            ]}},
        ]
        self.assertEqual(_loaded_skill_names(objs, meter.skill_names_from_value), {"pdf", "docx", "xlsx"})
        self.assertIsNone(_loaded_skill_names([], meter.skill_names_from_value))
        self.assertEqual(codex_mcp_tool_name("js", "mcp__cua_repl"), "mcp__cua_repl__js")
        self.assertEqual(codex_mcp_tool_name("exec", None), "exec")
        calls = meter.codex_tool_call_evidence([{"timestamp": "2026-07-02T00:00:00Z", "payload": {
            "type": "function_call", "name": "js", "namespace": "mcp__cua_repl", "call_id": "c1", "arguments": "{}",
        }}])
        self.assertEqual((calls[0]["kind"], calls[0]["namespace"]), ("mcp", "cua_repl"))

    def test_configured_capabilities_fill_unrecorded_loads_with_label(self):
        with mock.patch.object(meter, "configured_capabilities", return_value=({"a", "b", "c"}, set())):
            result = meter.with_configured_capabilities({
                "skills": {"loaded": None, "used": 1},
                "mcp_servers": {"loaded": None, "used": 2},
            }, "cursor", "/repo")
        self.assertEqual(result, {
            "skills": {"loaded": 3, "used": 1, "basis": "configured"},
            "mcp_servers": {"loaded": 2, "used": 2, "basis": "configured"},
        })
        with mock.patch.object(meter, "configured_capabilities", return_value=(None, None)):
            result = meter.with_configured_capabilities({
                "skills": {"loaded": 9, "used": 1}, "mcp_servers": {"loaded": None, "used": 0},
            }, "kiro", "")
        self.assertEqual(result["skills"], {"loaded": 9, "used": 1, "basis": "session"})
        self.assertEqual(result["mcp_servers"], {"loaded": None, "used": 0, "basis": "unavailable"})

    def test_cursor_configured_capabilities_read_skill_dirs_and_mcp_json(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            for root, name in (("skills", "pdf"), ("skills-cursor", "canvas")):
                path = Path(home) / ".cursor" / root / name
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("x")
            (Path(home) / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"docs": {}, "jira": {}}}))
            (Path(project) / ".cursor").mkdir()
            (Path(project) / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"local": {}}}))
            with mock.patch.dict(os.environ, {"HOME": home}):
                skills, servers = meter._scan_configured_capabilities("cursor", project)
        self.assertEqual(skills, {"pdf", "canvas"})
        self.assertEqual(servers, {"docs", "jira", "local"})

    def test_claude_summary_without_load_records_keeps_loaded_unknown(self):
        row = meter.claude_summary(self.claude_source_without_title(), [self.claude_usage_row()])
        self.assertEqual(row["capabilities"], {
            "skills": {"loaded": None, "used": 0},
            "mcp_servers": {"loaded": None, "used": 0},
        })

    def test_session_capabilities_uses_codex_catalog_mcp_namespaces(self):
        from token_meter.domain.tools import session_capabilities
        evidence = {
            "skills": [],
            "tools": [{"name": "mcp__docs__search", "namespace": "docs", "kind": "mcp"}],
            "catalog": [
                {"name": "mcp__docs__search", "namespace": "docs", "kind": "mcp"},
                {"name": "mcp__jira__get", "namespace": "jira", "kind": "mcp"},
                {"name": "exec_command", "namespace": "shell", "kind": "tool"},
            ],
        }
        self.assertEqual(session_capabilities(evidence), {
            "skills": {"loaded": None, "used": 0},
            "mcp_servers": {"loaded": 2, "used": 1},
        })
        self.assertEqual(session_capabilities({"catalog": [
            {"name": "exec_command", "namespace": "shell", "kind": "tool"},
        ]})["mcp_servers"], {"loaded": None, "used": 0})

    def test_claude_summary_uses_ai_title_when_no_custom_title(self):
        objs = [
            self.claude_usage_row(),
            {"type": "ai-title", "aiTitle": "Process codex reviews for issue #40",
             "sessionId": "session"},
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["title"], "Process codex reviews for issue #40")
        self.assertEqual(row["session_name"], "Process codex reviews for issue #40")

    def test_claude_summary_desktop_title_outranks_custom_and_ai_title(self):
        source = self.claude_source_without_title()
        source["title"] = "Desktop sidecar title"
        objs = [
            self.claude_usage_row(),
            {"type": "custom-title", "customTitle": "release-triage",
             "sessionId": "session"},
            {"type": "ai-title", "aiTitle": "Process codex reviews",
             "sessionId": "session"},
        ]
        row = meter.claude_summary(source, objs)
        self.assertEqual(row["title"], "Desktop sidecar title")
        self.assertEqual(row["session_name"], "Desktop sidecar title")

    def test_claude_summary_keeps_last_custom_title_when_multiple_records(self):
        objs = [
            self.claude_usage_row(),
            {"type": "custom-title", "customTitle": "first-name",
             "sessionId": "session"},
            {"type": "custom-title", "customTitle": "second-name",
             "sessionId": "session"},
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["title"], "second-name")
        self.assertEqual(row["session_name"], "second-name")

    def test_claude_summary_falls_back_to_untitled_when_no_declared_title_and_no_human_prompt(self):
        objs = [
            self.claude_usage_row(),
            {"type": "user", "isMeta": True,
             "timestamp": "2026-07-02T00:00:00.000Z",
             "message": {"content": "Base directory for this skill: /Users/pat"}},
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["title"], "(untitled log)")
        self.assertEqual(row["session_name"], "")

    def test_claude_summary_prompt_fallback_title_does_not_populate_session_name(self):
        objs = [
            self.claude_usage_row(),
            {"type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
             "message": {"content": "What does this function do?"}},
        ]
        row = meter.claude_summary(self.claude_source_without_title(), objs)
        self.assertEqual(row["title"], "What does this function do?")
        self.assertEqual(row["session_name"], "")

    def test_codex_summary_carries_live_throughput_into_current_sessions(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
                }},
            }},
        ]

        row = meter.codex_summary(self.source("codex", "gpt-5.6"), objs)

        self.assertFalse(row["throughput"]["available"])
        self.assertIn("live_throughput", row)
        self.assertEqual(row["live_throughput"]["output_tps"], 10)
        self.assertEqual(row["live_throughput"]["completed_steps"], 1)

    def test_codex_summary_records_the_last_reply_not_later_bookkeeping(self):
        import calendar
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z", "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 200, "output_tokens": 40, "total_tokens": 240}}}},
            # Resuming the session later writes context lines but no reply.
            {"type": "turn_context", "timestamp": "2026-07-01T05:00:00.000Z", "payload": {"model": "gpt-5.6"}},
        ]
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), objs)
        self.assertEqual(row["last_activity_ts"], calendar.timegm((2026, 7, 1, 0, 0, 4)))
        self.assertIsNone(meter.codex_summary(self.source("codex", "gpt-5.6"), objs[:1])["last_activity_ts"])

    def test_codex_summary_keeps_missing_model_pricing_unavailable(self):
        row = meter.codex_summary(self.source("codex"), [{
            "timestamp": "2026-07-02T00:00:01.000Z",
            "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 100_000, "output_tokens": 1_000,
                "total_tokens": 101_000,
            }}},
        }])

        self.assertEqual(row["primary_model"], "unknown-model")
        self.assertFalse(row["availability"]["cost"])
        self.assertEqual(row["cost"], 0.0)

    def test_codex_summary_keeps_priced_model_coverage_in_mixed_session(self):
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), [
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-02T00:00:01.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                }},
            }},
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:02.000Z",
             "payload": {"model": "unknown-model"}},
            {"timestamp": "2026-07-02T00:00:03.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                }},
            }},
        ])

        self.assertFalse(row["availability"]["cost"])
        stats = {item["model"]: item for item in row["model_stats"]}
        self.assertTrue(stats["gpt-5.6"]["availability"]["cost"])
        self.assertEqual(stats["gpt-5.6"]["cost_covered_executions"], 1)
        self.assertEqual(stats["gpt-5.6"]["cost_covered_output_tokens"], 20)
        self.assertFalse(stats["unknown-model"]["availability"]["cost"])
        self.assertEqual(stats["unknown-model"]["cost_covered_executions"], 0)
        daily = {item["model"]: item for item in row["_model_daily"]}
        self.assertTrue(daily["gpt-5.6"]["availability"]["cost"])
        self.assertFalse(daily["unknown-model"]["availability"]["cost"])

        aggregate = {
            item["model"]: item for item in meter.aggregate_model_stats([row])["models"]
        }
        self.assertTrue(aggregate["gpt-5.6"]["availability"]["cost"])
        self.assertEqual(aggregate["gpt-5.6"]["cost_covered_executions"], 1)
        self.assertEqual(aggregate["gpt-5.6"]["cost_covered_output_tokens"], 20)
        self.assertTrue(aggregate["gpt-5.6"]["daily"][0]["availability"]["cost"])
        self.assertEqual(
            aggregate["gpt-5.6"]["daily"][0]["cost_covered_output_tokens"], 20,
        )
        self.assertFalse(aggregate["unknown-model"]["availability"]["cost"])

    def test_codex_summary_exposes_input_output_and_model_stats(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-02T00:00:00.000Z",
             "payload": {"model": "gpt-5.6", "effort": "xhigh"}},
            {"timestamp": "2026-07-02T00:00:00.500Z", "payload": {
                "type": "task_started", "model_context_window": 200000,
            }},
            {"timestamp": "2026-07-02T00:00:01.000Z", "payload": {
                "type": "token_count", "info": {"model_context_window": 200000,
                                                "last_token_usage": {
                    "input_tokens": 100, "cached_input_tokens": 40,
                    "output_tokens": 20, "reasoning_output_tokens": 8,
                    "total_tokens": 120,
                }},
            }},
            {"timestamp": "2026-07-02T00:00:02.000Z", "payload": {
                "type": "task_complete",
            }},
        ]
        row = meter.codex_summary(self.source("codex", "gpt-5.6"), objs)
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 20)
        self.assertEqual(row["model_stats"][0]["model"], "gpt-5.6")
        self.assertEqual(row["model_stats"][0]["tokens"], 120)
        self.assertEqual(row["model_stats"][0]["reasoning_tokens"], 8)
        self.assertEqual(row["model_stats"][0]["reasoning_output_tokens"], 20)
        self.assertEqual(row["model_stats"][0]["reasoning_executions"], 1)
        self.assertEqual(row["model_stats"][0]["reasoning_unavailable_executions"], 0)
        self.assertEqual(row["primary_model"], "gpt-5.6")
        self.assertEqual(row["reasoning_effort"], "xhigh")
        self.assertEqual(row["session_name"], "Summary stats")
        self.assertEqual(row["context"]["latest"], 100)
        self.assertEqual(row["context"]["window"], 200000)
        self.assertEqual(row["context"]["latest_pct"], 0.0005)
        self.assertEqual(row["_context_samples"], [100])
        self.assertTrue(row["terminal"])
        self.assertEqual(row["usage_basis"], "reported")

    def test_codex_summary_prices_each_usage_event_across_a_cutover(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-30T00:00:00.000Z",
             "payload": {"model": "gpt-5.6-terra"}},
            {"timestamp": "2026-07-30T00:00:01.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100_000, "output_tokens": 100_000,
                    "total_tokens": 200_000,
                }},
            }},
            {"timestamp": "2026-07-31T00:00:00.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100_000, "output_tokens": 100_000,
                    "total_tokens": 200_000,
                }},
            }},
        ]
        row = meter.codex_summary(self.source("codex", "gpt-5.6-terra"), objs)
        self.assertAlmostEqual(row["cost"], 4.9)
        self.assertEqual(len(row["_day_cost"]), 2)
        for actual, expected in zip(sorted(row["_day_cost"].values()), (1.4, 3.5)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(row["_model_cost"]["gpt-5.6-terra"], 4.9)


class CurrentSessionSummaryTests(unittest.TestCase):
    def row(self, session_id, mtime, **overrides):
        row = {
            "id": session_id,
            "path": f"/private/traces/{session_id}.jsonl",
            "title": f"private prompt for {session_id}",
            "provider": "codex",
            "client": "codex",
            "runtime": "Codex",
            "label": "Codex",
            "project": "/Users/person/secret/repository",
            "session_name": f"Session {session_id}",
            "primary_model": "gpt-5.6",
            "reasoning_effort": "xhigh",
            "models": ["gpt-5.6"],
            "cost": 1.25,
            "cost_approx": True,
            "availability": {"cost": True, "context": True},
            "usage_basis": "reported",
            "context": {
                "latest": 50000, "window": 200000,
                "latest_pct": 0.25, "estimated": False,
            },
            "_context_samples": [20000, 35000, 50000],
            "turns": 3,
            "mtime": mtime,
            "terminal": False,
            "throughput": {
                "available": True, "output_tps": 24.5, "basis": "tool_free",
            },
        }
        row.update(overrides)
        return row

    def test_opening_a_session_without_a_new_reply_does_not_make_it_live(self):
        now = 50_000
        opened = self.row("opened", now - 5, last_activity_ts=now - 3 * 3600)  # file touched, last reply hours ago
        replying = self.row("replying", now - 5, last_activity_ts=now - 20)
        quiet = self.row("quiet", now - 5, last_activity_ts=now - 600)
        no_replies_yet = self.row("brand-new", now - 5)  # no reply recorded: the trace time still counts
        result = {row["id"]: row for row in meter.current_session_summaries(
            [opened, replying, quiet, no_replies_yet], now=now)}
        self.assertNotIn("opened", result)
        self.assertEqual(result["replying"]["activity_state"], "working")
        self.assertEqual((result["quiet"]["activity_state"], result["quiet"]["idle_s"]), ("recent", 600))
        self.assertIn("brand-new", result)

    def test_filters_orders_limits_and_sanitizes_card_rows(self):
        now = 10_000
        rows = [
            self.row(f"session-{index}", now - index * 10)
            for index in range(10)
        ]
        rows.append(self.row("too-old", now - meter.CURRENT_SESSION_MAX_AGE_S - 1))
        result = meter.current_session_summaries(rows, now=now)

        self.assertEqual([row["id"] for row in result], [f"session-{index}" for index in range(10)])
        self.assertEqual(result[0]["project"], "repository")
        self.assertEqual(result[0]["session_name"], "Session session-0")
        self.assertEqual(result[0]["reasoning_effort"], "xhigh")
        self.assertEqual(result[0]["throughput"]["output_tps"], 24.5)
        self.assertIsNone(result[0]["output_per_dollar"])
        self.assertEqual(result[0]["context"]["samples"], [20000, 35000, 50000])
        self.assertNotIn("_context_samples", result[0])
        self.assertNotIn("path", result[0])
        self.assertNotIn("title", result[0])
        self.assertNotIn("private prompt", json.dumps(result))
        self.assertNotIn("/Users/person", json.dumps(result))

    def test_returns_every_live_session_up_to_the_payload_ceiling(self):
        now = 10_000
        rows = [
            self.row(f"session-{index}", now - index)
            for index in range(meter.CURRENT_SESSION_LIMIT + 5)
        ]
        result = meter.current_session_summaries(rows, now=now)

        self.assertGreater(meter.CURRENT_SESSION_LIMIT, 8)
        self.assertEqual(len(result), meter.CURRENT_SESSION_LIMIT)
        self.assertEqual(result[0]["id"], "session-0")

    def test_projects_output_per_covered_dollar_without_model_details(self):
        row = self.row(
            "mixed-coverage", 49_990,
            availability={
                "cost": False, "tokens": True, "output_tokens": True,
                "context": True,
            },
            model_stats=[
                {
                    "model": "priced-model", "cost_covered_executions": 2,
                    "cost_covered_output_tokens": 600,
                    "cost_covered_cost": 2.0,
                },
                {
                    "model": "unpriced-model", "cost_covered_executions": 0,
                    "cost_covered_output_tokens": 0,
                    "cost_covered_cost": 0.0,
                },
            ],
        )

        result = meter.current_session_summaries([row], now=50_000)[0]

        self.assertEqual(result["output_per_dollar"], 300.0)
        self.assertTrue(result["availability"]["output_per_dollar"])
        self.assertNotIn("model_stats", result)
        self.assertNotIn("priced-model", json.dumps(result))

    def test_projects_only_valid_claude_desktop_resume_ids(self):
        resumable = self.row("resumable", 49_990)
        resumable["desktop_resume_id"] = "0f9f0062-51be-4131-acfa-51034ef23f99"
        forged = self.row("forged", 49_980)
        forged["desktop_resume_id"] = "0f9f0062-51be-4131-acfa-51034ef23f99&q=x"
        plain = self.row("plain", 49_970)
        results = {
            row["id"]: row
            for row in meter.current_session_summaries([resumable, forged, plain], now=50_000)
        }
        self.assertEqual(results["resumable"]["desktop_resume_id"], "0f9f0062-51be-4131-acfa-51034ef23f99")
        self.assertIsNone(results["forged"]["desktop_resume_id"])
        self.assertIsNone(results["plain"]["desktop_resume_id"])

    def test_projects_bounded_capability_counts_with_unknown_loads(self):
        known = self.row("known", 49_990)
        known["capabilities"] = {
            "skills": {"loaded": 75, "used": 2, "names": ["private"]},
            "mcp_servers": {"loaded": 20, "used": 1},
        }
        unknown = self.row("unknown", 49_980)
        results = {row["id"]: row for row in meter.current_session_summaries([known, unknown], now=50_000)}
        self.assertEqual(results["known"]["capabilities"], {
            "skills": {"loaded": 75, "used": 2, "basis": "unavailable"},
            "mcp_servers": {"loaded": 20, "used": 1, "basis": "unavailable"},
        })
        self.assertEqual(results["unknown"]["capabilities"], {
            "skills": {"loaded": None, "used": 0, "basis": "unavailable"},
            "mcp_servers": {"loaded": None, "used": 0, "basis": "unavailable"},
        })

    def test_output_per_dollar_requires_output_and_nonzero_cost_evidence(self):
        rows = [
            self.row(
                "missing-output", 59_990, output_tokens=250,
                availability={"cost": True, "output_tokens": False, "context": True},
            ),
            self.row(
                "zero-cost", 59_980, cost=0.0, output_tokens=250,
                availability={"cost": True, "output_tokens": True, "context": True},
            ),
            self.row(
                "covered", 59_970, cost=2.0, output_tokens=250,
                availability={"cost": True, "output_tokens": True, "context": True},
            ),
        ]

        result = {
            row["id"]: row
            for row in meter.current_session_summaries(rows, now=60_000)
        }

        self.assertIsNone(result["missing-output"]["output_per_dollar"])
        self.assertFalse(result["missing-output"]["availability"]["output_per_dollar"])
        self.assertIsNone(result["zero-cost"]["output_per_dollar"])
        self.assertFalse(result["zero-cost"]["availability"]["output_per_dollar"])
        self.assertEqual(result["covered"]["output_per_dollar"], 125.0)
        self.assertTrue(result["covered"]["availability"]["output_per_dollar"])

    def test_uses_working_waiting_and_recent_activity_states(self):
        now = 20_000
        rows = [
            self.row("working", now - 10, terminal=False),
            self.row("waiting", now - 20, terminal=True),
            self.row("recent", now - meter.CURRENT_SESSION_WORKING_S - 1, terminal=False),
        ]
        result = {
            row["id"]: row
            for row in meter.current_session_summaries(rows, now=now)
        }
        self.assertEqual(result["working"]["activity_state"], "working")
        self.assertEqual(result["waiting"]["activity_state"], "waiting")
        self.assertEqual(result["recent"]["activity_state"], "recent")
        self.assertEqual(result["working"]["context"]["latest_pct"], 0.25)

    def test_duplicate_session_segments_prefer_active_work(self):
        now = 25_000
        rows = [
            self.row("resumed", now - 15, terminal=False, turns=8),
            self.row("resumed", now - 5, terminal=True, turns=120),
        ]
        result = meter.current_session_summaries(rows, now=now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "resumed")
        self.assertEqual(result[0]["activity_state"], "working")
        self.assertEqual(result[0]["turns"], 8)

    def test_keeps_context_tokens_without_inventing_a_window(self):
        row = self.row(
            "claude-session", 29_990, provider="claude", client="claude_code",
            runtime="Claude Code", context={
                "latest": 118000, "window": None,
                "latest_pct": None, "estimated": False,
            },
        )
        result = meter.current_session_summaries([row], now=30_000)[0]
        self.assertEqual(result["context"]["latest"], 118000)
        self.assertIsNone(result["context"]["window"])
        self.assertIsNone(result["context"]["latest_pct"])

    def test_context_samples_are_numeric_recent_and_bounded(self):
        row = self.row(
            "long-session", 39_990,
            _context_samples=[*range(40), "invalid", -1, None],
        )
        result = meter.current_session_summaries([row], now=40_000)[0]
        self.assertEqual(len(result["context"]["samples"]),
                         meter.CURRENT_SESSION_CONTEXT_SAMPLES)
        self.assertEqual(result["context"]["samples"], list(range(8, 40)))
        self.assertTrue(all(isinstance(value, int)
                            for value in result["context"]["samples"]))


class SelectedSessionStateCacheTests(unittest.TestCase):
    def test_reuses_unchanged_state_and_recomputes_after_trace_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text('{"type":"session_meta"}\n')
            source = {
                "provider": "codex", "id": "session", "path": str(path),
                "mtime": path.stat().st_mtime,
            }
            meter._session_state_cache.clear()
            calls = []

            def build(_source):
                calls.append(len(calls) + 1)
                return {"source": {"id": "session"}, "version": calls[-1]}

            try:
                with mock.patch.object(meter, "recompute", side_effect=build):
                    first = meter.cached_session_state(source)
                    first["version"] = 99
                    second = meter.cached_session_state(source)
                    path.write_text(path.read_text() + '{"type":"event"}\n')
                    third = meter.cached_session_state(source)
            finally:
                meter._session_state_cache.clear()

        self.assertEqual(calls, [1, 2])
        self.assertEqual(second["version"], 1)
        self.assertEqual(third["version"], 2)

    def test_concurrent_misses_share_one_recompute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text('{"type":"session_meta"}\n')
            source = {
                "provider": "codex", "id": "session", "path": str(path),
                "mtime": path.stat().st_mtime,
            }
            meter._session_state_cache.clear()
            calls = []
            started = threading.Event()
            release = threading.Event()

            def build(_source):
                calls.append(1)
                started.set()
                release.wait(5)
                return {"source": {"id": "session"}}

            results = []
            try:
                with mock.patch.object(meter, "recompute", side_effect=build):
                    threads = [
                        threading.Thread(
                            target=lambda: results.append(meter.cached_session_state(source))
                        )
                        for _ in range(8)
                    ]
                    threads[0].start()
                    started.wait(5)
                    # A live transcript grows while later pollers queue.
                    with path.open("a") as handle:
                        handle.write('{"type":"event"}\n')
                    for thread in threads[1:]:
                        thread.start()
                    time.sleep(0.2)
                    release.set()
                    for thread in threads:
                        thread.join(5)
            finally:
                meter._session_state_cache.clear()
                meter._session_state_build_locks.clear()

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [{"source": {"id": "session"}}] * 8)


class SessionRouteTests(unittest.TestCase):
    def test_dashboard_accepts_root_and_unique_session_paths(self):
        self.assertTrue(meter.is_dashboard_page_path("/"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/019f16fa-dc6c-7a62-839c-25c15dca4e75"))
        self.assertTrue(meter.is_dashboard_page_path("/sessions/claude%20session/"))

    def test_dashboard_rejects_api_nested_and_empty_session_paths(self):
        self.assertFalse(meter.is_dashboard_page_path("/session"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/"))
        self.assertFalse(meter.is_dashboard_page_path("/sessions/one/two"))

    def test_dashboard_serves_only_explicitly_bundled_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "page.html"
            font = root / "assets" / "fonts" / "Tektur-Variable.ttf"
            logo = root / "assets" / "brand" / "logo-splunk-by-cisco-rgb-w.png"
            effects = root / "assets" / "session-effects.js"
            dithering = root / "assets" / "vendor" / "paper-shaders" / "shaders" / "dithering.js"
            pulsing_border = root / "assets" / "vendor" / "paper-shaders" / "shaders" / "pulsing-border.js"
            font.parent.mkdir(parents=True)
            logo.parent.mkdir(parents=True)
            effects.parent.mkdir(parents=True, exist_ok=True)
            dithering.parent.mkdir(parents=True)
            page.write_text("dashboard")
            font.write_bytes(b"font")
            logo.write_bytes(b"logo")
            effects.write_text("export {}")
            dithering.write_text("export {}")
            pulsing_border.write_text("export {}")
            with mock.patch.object(meter, "page_path", return_value=str(page)):
                self.assertEqual(
                    meter.dashboard_asset_path("/assets/fonts/Tektur-Variable.ttf"),
                    str(font),
                )
                self.assertEqual(
                    meter.dashboard_asset_path("/assets/brand/logo-splunk-by-cisco-rgb-w.png"),
                    str(logo),
                )
                self.assertEqual(
                    meter.dashboard_asset_content_type("/assets/fonts/Tektur-Variable.ttf"),
                    "font/ttf",
                )
                self.assertEqual(
                    meter.dashboard_asset_content_type("/assets/brand/logo-splunk-by-cisco-rgb-w.png"),
                    "image/png",
                )
                self.assertEqual(
                    meter.dashboard_asset_path("/assets/session-effects.js"),
                    str(effects),
                )
                self.assertIsNone(meter.dashboard_asset_path(
                    "/assets/vendor/paper-shaders/shaders/dithering.js"
                ))
                self.assertEqual(
                    meter.dashboard_asset_path(
                        "/assets/vendor/paper-shaders/shaders/pulsing-border.js"
                    ),
                    str(pulsing_border),
                )
                self.assertEqual(
                    meter.dashboard_asset_content_type("/assets/session-effects.js"),
                    "text/javascript; charset=utf-8",
                )
                self.assertIsNone(meter.dashboard_asset_path("/assets/fonts/OFL-Tektur.txt"))
                self.assertIsNone(meter.dashboard_asset_path("/assets/brand/SOURCE.md"))
                self.assertIsNone(meter.dashboard_asset_path(
                    "/assets/vendor/paper-shaders/shaders/neuro-noise.js"
                ))
                self.assertIsNone(meter.dashboard_asset_path(
                    "/assets/vendor/paper-shaders/shaders/god-rays.js"
                ))
                self.assertIsNone(meter.dashboard_asset_path("/assets/../meter.py"))


class SessionTraceKeyProjectionTests(unittest.TestCase):
    def test_spend_logs_and_current_sessions_carry_the_trace_key(self):
        rows = [
            {"id": "task-1", "session": "rollout-a.jsonl", "provider": "codex",
             "_day_cost": {"2026-08-11": 2.0}, "mtime": 990, "turns": 1},
            {"id": "task-1", "session": "rollout-b.jsonl", "provider": "codex",
             "_day_cost": {"2026-08-11": 3.0}, "mtime": 995, "turns": 1},
        ]
        spend = meter.spend_log_summaries(rows, "2026-08-01", "2026-08-31")
        self.assertEqual(
            sorted(row["session"] for row in spend),
            ["rollout-a.jsonl", "rollout-b.jsonl"],
        )
        current = meter.current_session_summaries(rows, now=1000)
        self.assertEqual([row["session"] for row in current], ["rollout-b.jsonl"])


if __name__ == "__main__":
    unittest.main()
