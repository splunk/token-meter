import unittest
import datetime
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DiscoveryContext
from token_meter.runtimes.codex import CodexRuntimeAdapter


class LegacyJsonlLoadTests(unittest.TestCase):
    def test_load_skips_corrupt_bytes_truncated_rows_and_non_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_bytes(
                b'{"type":"user","value":1}\n'
                b'\xff\n'
                b'[]\n'
                b'"text"\n'
                b'42\n'
                b'null\n'
                b'{"type":"assistant","value":2}\n'
                b'{"truncated":\n'
            )

            rows = meter.load(str(path))

        self.assertEqual(rows, [
            {"type": "user", "value": 1},
            {"type": "assistant", "value": 2},
        ])

    def test_load_returns_empty_when_the_trace_cannot_be_opened(self):
        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            self.assertEqual(meter.load("/unreadable/session.jsonl"), [])


class SourceDiscoveryCacheTests(unittest.TestCase):
    def test_codex_metadata_prefix_is_reused_after_large_trace_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            rows = [
                {
                    "type": "session_meta",
                    "payload": {"id": "session", "cwd": "/repo"},
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "gpt-stable"},
                },
            ]
            rows.extend({"type": "event", "payload": {"index": idx}}
                        for idx in range(121))
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))

            with mock.patch.object(meter, "_codex_native_adapters", {}):
                first = meter.codex_meta(str(path))
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"type": "event", "payload": {}}) + "\n")
                with mock.patch(
                    "builtins.open",
                    side_effect=AssertionError(
                        "append-only growth must reuse the completed metadata prefix"
                    ),
                ):
                    second = meter.codex_meta(str(path))

        self.assertEqual(first, second)
        self.assertEqual(second["model"], "gpt-stable")

    def test_codex_metadata_is_reused_until_the_trace_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            session_meta = {
                "type": "session_meta",
                "payload": {"id": "session", "cwd": "/repo"},
            }
            first_turn = {
                "type": "turn_context",
                "payload": {"model": "gpt-first"},
            }
            path.write_text(
                json.dumps(session_meta) + "\n" + json.dumps(first_turn) + "\n"
            )
            with mock.patch.object(meter, "_codex_native_adapters", {}):
                first = meter.codex_meta(str(path))
                with mock.patch(
                    "builtins.open",
                    side_effect=AssertionError("unchanged metadata should come from cache"),
                ):
                    second = meter.codex_meta(str(path))

                second_turn = {
                    "type": "turn_context",
                    "payload": {"model": "gpt-second"},
                }
                path.write_text(
                    json.dumps(session_meta) + "\n" + json.dumps(second_turn) + "\n"
                )
                third = meter.codex_meta(str(path))

        self.assertEqual(first["model"], "gpt-first")
        self.assertEqual(second["model"], "gpt-first")
        self.assertEqual(third["model"], "gpt-second")

    def test_codex_index_rename_refreshes_cached_session_name_without_trace_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            trace = sessions / "2026" / "08" / "10" / "session.jsonl"
            trace.parent.mkdir(parents=True)
            session_id = "codex-session"
            trace.write_text(json.dumps({
                "type": "session_meta",
                "timestamp": "2026-08-10T10:00:00Z",
                "payload": {"id": session_id, "cwd": "/repo"},
            }) + "\n")
            os.utime(trace, (1_784_548_800, 1_784_548_800))
            index = root / "session_index.jsonl"
            index.write_text(json.dumps({
                "id": session_id, "thread_name": "Review token-meter PR 14",
            }) + "\n")
            empty_summary_cache = {}
            with mock.patch.object(meter, "CODEX_SESSIONS", str(sessions)), \
                    mock.patch.object(meter, "CODEX_INDEX", str(index)), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")), \
                    mock.patch.object(meter, "CURSOR_PROJECTS", str(root / "no-cursor")), \
                    mock.patch.object(meter, "OPENCODE_DB", str(root / "no-opencode.db")), \
                    mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                    mock.patch.object(meter, "_summary_cache", empty_summary_cache):
                first_source = meter.all_session_sources()[0]
                first_signature = meter.source_mtime_signature([first_source])
                first_state_signature = meter.session_state_signature(first_source)
                first_summary = meter.session_summary(first_source)
                with index.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "id": session_id, "thread_name": "Review token-meter PR 14 (2)",
                    }) + "\n")
                second_source = meter.all_session_sources()[0]
                second_signature = meter.source_mtime_signature([second_source])
                second_state_signature = meter.session_state_signature(second_source)
                second_summary = meter.session_summary(second_source)

        self.assertEqual(first_source["mtime"], second_source["mtime"])
        self.assertEqual(first_summary["session_name"], "Review token-meter PR 14")
        self.assertEqual(second_source["title"], "Review token-meter PR 14 (2)")
        self.assertNotEqual(first_signature, second_signature)
        self.assertNotEqual(first_state_signature, second_state_signature)
        self.assertEqual(second_summary["session_name"], "Review token-meter PR 14 (2)")

    def test_shared_path_cache_retains_one_complete_adapter_pattern_set(self):
        cache = meter._recursive_path_cache
        cache.clear()
        try:
            with mock.patch("glob.glob", return_value=[]):
                for index in range(37):
                    cache.paths(f"/nonexistent/runtime-pattern-{index}/*", now=1.0)
            self.assertEqual(cache.entry_count, 37)
        finally:
            cache.clear()


class CursorTraceTests(unittest.TestCase):
    def source(self, path="/tmp/cursor-session.jsonl", session_id="cursor-session"):
        return {
            "provider": "cursor", "client": "cursor", "label": "Cursor",
            "runtime": "Cursor", "id": session_id,
            "session": Path(path).name, "path": path, "project": "/repo",
            "mtime": 1, "title": "Cursor run", "model": "composer-2.5",
        }

    def snapshot(self):
        base_ms = 1_784_548_800_000
        return {
            "available": True,
            "header": {"name": "Cursor run"},
            "composer": {
                "modelConfig": {
                    "modelName": "composer-2.5",
                    "selectedModels": [{"modelId": "composer-2.5", "parameters": [
                        {"id": "fast", "value": "true"},
                    ]}],
                },
                "contextTokensUsed": 80448,
                "contextTokenLimit": 200000,
                "promptTokenBreakdown": {"categories": [
                    {"id": "rules", "label": "Rules", "estimatedTokens": 1234},
                ]},
            },
            "bubbles": [
                {"type": 1, "bubbleId": "user-1", "createdAt": base_ms,
                 "text": "inspect the repository", "requestId": "request-1",
                 "modelInfo": {"modelName": "composer-2.5"},
                 "contextWindowStatusAtCreation": {"tokensUsed": 78000, "tokenLimit": 200000}},
                {"type": 2, "bubbleId": "assistant-1", "createdAt": base_ms + 46000,
                 "thinking": "trace-visible", "thinkingDurationMs": 3200,
                 "toolFormerData": {
                     "name": "ripgrep_raw_search", "toolCallId": "tool-1",
                     "params": {"query": "needle"}, "additionalData": {"lines": "x" * 80},
                     "status": "error",
                 },
                 "text": "finished", "turnDurationMs": 46624},
            ],
        }

    def spans(self):
        base = 1_784_548_800.0
        return [
            {"name": "client.ttft", "start_ts": base, "end_ts": base + 4.118,
             "duration_s": 4.118, "request_id": "request-1", "error": False},
            {"name": "agent.request.attempt", "start_ts": base, "end_ts": base + 20,
             "duration_s": 20, "request_id": "attempt-1", "error": True},
            {"name": "agent.request.attempt", "start_ts": base + 21, "end_ts": base + 46,
             "duration_s": 25, "request_id": "attempt-2", "error": False},
            {"name": "ComposerChatService.submitChatMaybeAbortCurrent", "start_ts": base,
             "end_ts": base + 46.624, "duration_s": 46.624,
             "request_id": "request-1", "error": False},
        ]

    def test_cursor_discovery_uses_sqlite_metadata_and_keeps_activity_order_session_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "projects"
            db_path = root / "state.vscdb"
            logs = root / "logs"
            logs.mkdir()
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, checkpointAt INTEGER, value TEXT)")
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            rows = [
                ("older", 1_784_548_800_000, 0, "gpt-5.6-sol"),
                ("newer", 1_784_548_900_000, 0, "composer-2.5"),
                ("child", 1_784_549_000_000, 1, "composer-2.5"),
            ]
            for sid, updated, subagent, model in rows:
                header = {"name": sid.title(), "workspaceIdentifier": {"uri": {"fsPath": "/repo"}}}
                composer = {"modelConfig": {"modelName": model}}
                conn.execute("INSERT INTO composerHeaders VALUES (?, ?, ?, ?, 0, ?, 0, ?)",
                             (sid, "workspace", updated - 1000, updated, subagent, json.dumps(header)))
                conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                             (f"composerData:{sid}", json.dumps(composer)))
                trace_dir = projects / "Users-test-repo" / "agent-transcripts" / sid
                trace_dir.mkdir(parents=True)
                transcript = trace_dir / f"{sid}.jsonl"
                transcript.write_text(json.dumps({"role": "user"}) + "\n")
                os.utime(transcript, (updated / 1000, updated / 1000))
            duplicate = projects / "empty-window" / "agent-transcripts" / "newer" / "newer.jsonl"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text(json.dumps({"role": "user", "replica": "newest"}) + "\n")
            os.utime(duplicate, (1_784_551_000, 1_784_551_000))
            conn.commit()
            conn.close()
            request_log = logs / "cursor.requestTraces.log"
            request_log.write_text(
                "2026-07-20T10:00:01Z span_completed name=client.ttft "
                "composerId=newer durationMs=100 requestId=r1 traceId=t1 error=false\n"
            )
            os.utime(request_log, (1_784_550_000, 1_784_550_000))
            with mock.patch.object(meter, "CURSOR_PROJECTS", str(projects)), \
                    mock.patch.object(meter, "CURSOR_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "CURSOR_REQUEST_LOGS", str(logs)), \
                    mock.patch.object(meter, "OPENCODE_DB", str(root / "no-opencode.db")), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")), \
                    mock.patch.object(meter, "CODEX_SESSIONS", str(root / "no-codex")), \
                    mock.patch.object(meter, "CODEX_INDEX", str(root / "no-index")), \
                    mock.patch.object(meter, "KIRO_SESSIONS", str(root / "no-kiro")), \
                    mock.patch.object(meter, "KIRO_AGENT_STORAGE", str(root / "no-kiro-agent")), \
                    mock.patch.object(meter, "PI_AGENT_DIR", str(root / "no-pi-agent")), \
                    mock.patch.object(meter, "HERMES_STATE_DB", str(root / "no-hermes.db")), \
                    mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}):
                sources = meter.all_session_sources()
            self.assertEqual({row["id"] for row in sources}, {"older", "newer"})
            self.assertEqual(len(sources), 2)
            self.assertEqual(max(sources, key=lambda row: row["mtime"])["id"], "newer")
            newer = next(row for row in sources if row["id"] == "newer")
            self.assertEqual(newer["path"], str(duplicate))
            self.assertEqual(newer["model"], "composer-2.5")
            self.assertEqual(newer["project"], "/repo")
            self.assertEqual(newer["signature_mtime"], newer["mtime"])
            self.assertTrue(newer["request_revision"])

    def test_cursor_recompute_exposes_context_tools_reasoning_wait_ttft_and_retries(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        self.assertEqual(state["provider"], "cursor")
        self.assertEqual(state["primary_model"], "composer-2.5")
        self.assertEqual(state["context"]["latest"], 80448)
        self.assertEqual(state["context"]["window"], 200000)
        self.assertTrue(state["context"]["estimated"])
        self.assertEqual(state["context"]["breakdown"][0]["estimated_tokens"], 1234)
        self.assertTrue(state["availability"]["cost"])
        self.assertTrue(state["availability"]["tokens"])
        self.assertFalse(state["availability"]["cache"])
        self.assertTrue(state["availability"]["throughput"])
        self.assertTrue(state["availability"]["context"])
        self.assertTrue(state["availability"]["timing"])
        self.assertTrue(state["availability"]["tool_results"])
        execution = state["executions"][0]
        self.assertEqual(execution["tools"][0]["name"], "ripgrep_raw_search")
        self.assertTrue(execution["tools"][0]["error"])
        self.assertGreater(execution["tokens"]["retrieval"], 0)
        self.assertAlmostEqual(execution["wait_duration_ms"], 46624, places=3)
        self.assertAlmostEqual(execution["ttft_ms"], 4118, places=3)
        self.assertEqual((execution["attempts"], execution["failed_attempts"], execution["retries"]),
                         (2, 1, 1))
        self.assertEqual(execution["reasoning_duration_ms"], 3200)
        self.assertEqual(execution["reasoning_tokens"], 4)
        self.assertEqual(execution["tokens"]["input"], 80448)
        self.assertEqual(execution["tokens"]["output"], 6)
        self.assertEqual(execution["pricing_variant"], "fast")
        self.assertAlmostEqual(execution["cost"], 0.241434, places=6)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(state["token_estimate"])

    def test_cursor_transcript_fallback_survives_missing_database(self):
        transcript = [
            {"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>hello</user_query>"}]}},
            {"role": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "one", "name": "Read", "input": {"path": "README.md"}},
                {"type": "text", "text": "done"},
            ]}},
            {"type": "turn_ended", "status": "success"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fallback.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in transcript))
            with mock.patch.object(meter, "cursor_snapshot", return_value={"available": False}), \
                    mock.patch.object(meter, "cursor_request_spans") as spans:
                state = meter.recompute_cursor(self.source(str(path), "fallback"))
        spans.assert_not_called()
        self.assertEqual(state["turns"], 1)
        self.assertTrue(state["cursor_enrichment"]["transcript_fallback"])
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read_file_v2")
        self.assertFalse(state["availability"]["tool_results"])

    def test_cursor_pricing_uses_persisted_composer_variant_and_fails_closed_without_it(self):
        price, approximate = meter.price_for("composer-2.5", "cursor")
        self.assertEqual(price, meter.ZERO_PRICE)
        self.assertTrue(approximate)
        fast, approximate = meter.price_for("composer-2.5", "cursor", "fast")
        standard, _ = meter.price_for("composer-2.5", "cursor", "standard")
        self.assertEqual((fast["input"], fast["output"]), (3.0, 15.0))
        self.assertEqual((standard["input"], standard["output"]), (0.5, 2.5))
        self.assertTrue(approximate)
        self.assertEqual(meter.cursor_price_variant(self.snapshot()["composer"], "composer-2.5"),
                         "fast")

    def test_cursor_pricing_rejects_composer_lookalikes_and_accepts_qualified_alias(self):
        composer = self.snapshot()["composer"]
        composer["modelConfig"]["selectedModels"].insert(0, {
            "modelId": "some-other-model",
            "parameters": [{"id": "fast", "value": "false"}],
        })
        qualified, approximate = meter.price_for(
            "cursor.composer-2.5", "cursor", "fast"
        )
        self.assertTrue(approximate)
        self.assertEqual((qualified["input"], qualified["output"]), (3.0, 15.0))
        self.assertEqual(
            meter.cursor_price_variant(composer, "cursor.composer-2.5"), "fast"
        )

        for model in ("composer-2.5ish", "composer-2.5-future"):
            with self.subTest(model=model):
                price, unavailable = meter.price_for(model, "cursor", "fast")
                self.assertTrue(unavailable)
                self.assertEqual(price, meter.ZERO_PRICE)
                self.assertEqual(meter.cursor_price_variant(self.snapshot()["composer"], model), "")

    def test_cursor_agent_check_reports_local_estimate_and_caveat(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        with mock.patch.object(meter, "resolve_agent_source", return_value=(self.source(), "matched")), \
                mock.patch.object(meter, "recompute", return_value=state):
            result = meter.agent_check(focus="cost", caller={"runtime": "cursor", "project": "/repo"})
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["evidence"][0]["value"], 0.2414, places=4)
        self.assertTrue(result["availability"]["cost"])
        self.assertIn("local", result["caveat"].lower())
        self.assertIn("cost", result["approximate_fields"])

    def test_cursor_summary_and_model_rollup_include_local_estimates(self):
        with mock.patch.object(meter, "cursor_snapshot", return_value=self.snapshot()), \
                mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
            state = meter.recompute_cursor(self.source())
        with mock.patch.object(meter, "recompute_cursor", return_value=state):
            row = meter.cursor_summary(self.source())
        aggregate = meter.aggregate_model_stats([row])
        model = aggregate["models"][0]
        self.assertEqual(row["turns"], 1)
        self.assertEqual(row["models"], ["composer-2.5"])
        self.assertTrue(row["availability"]["cost"])
        self.assertTrue(row["cost_approx"])
        self.assertEqual(row["input_tokens"], 80448)
        self.assertEqual(row["output_tokens"], 6)
        self.assertEqual(row["usage_basis"], "local_estimate")
        self.assertEqual(row["provenance"]["estimated_sessions"], 1)
        self.assertEqual(model["model"], "composer-2.5")
        self.assertEqual(model["runtime"], "Cursor")
        self.assertEqual(model["usage_basis"], "local_estimate")
        self.assertFalse(model["availability"]["cache"])
        self.assertEqual(model["coverage"]["cache"]["covered_sessions"], 0)
        self.assertEqual(model["executions"], 1)
        self.assertEqual(model["median_peak_input_tokens"], 80448)
        self.assertEqual(model["median_tool_calls"], 1)
        self.assertAlmostEqual(model["median_wait_s"], 46.624, places=3)
        self.assertAlmostEqual(model["avg_ttft_ms"], 4118, places=3)
        self.assertTrue(model["availability"]["tokens"])
        self.assertEqual(model["input_tokens"], 80448)
        self.assertEqual(model["output_tokens"], 6)
        self.assertEqual(model["reasoning_tokens"], 4)
        self.assertEqual(model["reasoning_output_tokens"], 6)
        self.assertEqual(model["reasoning_executions"], 1)
        self.assertTrue(model["availability"]["reasoning_tokens"])
        self.assertEqual((model["attempts"], model["retries"], model["failed_attempts"]),
                         (2, 1, 1))
        self.assertTrue(model["availability"]["attempts"])

    def test_cursor_summary_keeps_priced_model_coverage_in_mixed_session(self):
        session_availability = meter.metric_availability(
            "cursor", cost=False, tokens=True, input_tokens=True,
            output_tokens=True,
        )

        def execution(model, output_tokens, cost, cost_available, ts):
            return {
                "model": model, "ts": ts, "cost": cost,
                "tokens": {"input": 100, "output": output_tokens},
                "context_tokens": 100, "reasoning_tokens": 0,
                "availability": meter.metric_availability(
                    "cursor", cost=cost_available, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "tools": [], "tool_count": 0, "model_calls": 1,
            }

        state = {
            "availability": session_availability,
            "executions": [
                execution("composer-2.5", 20, 0.1, True, 1_784_548_801),
                execution("unknown-model", 30, 0.0, False, 1_784_548_802),
            ],
            "total_cost": 0.1, "total_tokens": 250,
            "tokens": {"input": 200, "output": 50},
            "cost_approx": True, "token_estimate": True,
            "timing": {}, "wait_time": {}, "context": {},
        }
        with mock.patch.object(meter, "recompute_cursor", return_value=state):
            row = meter.cursor_summary(self.source())

        self.assertFalse(row["availability"]["cost"])
        stats = {item["model"]: item for item in row["model_stats"]}
        self.assertTrue(stats["composer-2.5"]["availability"]["cost"])
        self.assertEqual(stats["composer-2.5"]["cost_covered_executions"], 1)
        self.assertEqual(stats["composer-2.5"]["cost_covered_output_tokens"], 20)
        self.assertFalse(stats["unknown-model"]["availability"]["cost"])
        daily = {item["model"]: item for item in row["_model_daily"]}
        self.assertTrue(daily["composer-2.5"]["availability"]["cost"])
        self.assertFalse(daily["unknown-model"]["availability"]["cost"])

        aggregate = {
            item["model"]: item for item in meter.aggregate_model_stats([row])["models"]
        }
        self.assertTrue(aggregate["composer-2.5"]["availability"]["cost"])
        self.assertEqual(
            aggregate["composer-2.5"]["cost_covered_output_tokens"], 20,
        )
        self.assertFalse(aggregate["unknown-model"]["availability"]["cost"])

    def test_cursor_efficiency_coverage_requires_output_evidence(self):
        session_availability = meter.metric_availability(
            "cursor", cost=True, tokens=True, input_tokens=True,
            output_tokens=False,
        )

        def execution(output_tokens, output_available, cost, ts):
            return {
                "model": "composer-2.5", "ts": ts, "cost": cost,
                "tokens": {"input": 100, "output": output_tokens},
                "context_tokens": 100, "reasoning_tokens": output_tokens // 2,
                "availability": meter.metric_availability(
                    "cursor", cost=True, tokens=True, input_tokens=True,
                    output_tokens=output_available,
                ),
                "tools": [], "tool_count": 0, "model_calls": 1,
            }

        state = {
            "availability": session_availability,
            "executions": [
                execution(20, True, 0.1, 1_784_548_801),
                execution(0, False, 0.05, 1_784_548_802),
            ],
            "total_cost": 0.15, "total_tokens": 220,
            "tokens": {"input": 200, "output": 20},
            "cost_approx": True, "token_estimate": True,
            "timing": {}, "wait_time": {}, "context": {},
        }
        with mock.patch.object(meter, "recompute_cursor", return_value=state):
            row = meter.cursor_summary(self.source())

        stats = row["model_stats"][0]
        self.assertEqual(stats["executions"], 2)
        self.assertEqual(stats["token_covered_executions"], 1)
        self.assertEqual(stats["io_covered_executions"], 1)
        self.assertEqual(stats["cost_covered_executions"], 1)
        self.assertEqual(stats["cost_covered_output_tokens"], 20)
        self.assertAlmostEqual(stats["cost_covered_cost"], 0.1)
        self.assertEqual(stats["reasoning_executions"], 1)
        self.assertEqual(stats["reasoning_unavailable_executions"], 1)

        aggregate = meter.aggregate_model_stats([row])["models"][0]
        self.assertFalse(aggregate["coverage"]["cost"]["complete"])
        self.assertFalse(aggregate["coverage"]["reasoning_tokens"]["complete"])
        self.assertEqual(aggregate["io_covered_executions"], 1)
        self.assertAlmostEqual(aggregate["cost_covered_cost"], 0.1)

    def test_usage_provenance_distinguishes_reported_estimated_and_mixed(self):
        reported = {"id": "reported", "provider": "codex", "cost": 1, "tokens": 10}
        estimated = {
            "id": "estimated", "provider": "cursor", "cost": 2, "tokens": 20,
            "token_estimate": True,
            "availability": meter.metric_availability("cursor", cost=True, tokens=True),
        }
        self.assertEqual(meter.usage_provenance([reported])["usage_basis"], "reported")
        local = meter.usage_provenance([estimated])
        self.assertEqual(local["usage_basis"], "local_estimate")
        self.assertEqual(local["estimated_cost"], 2)
        mixed = meter.usage_provenance([reported, estimated])
        self.assertEqual(mixed["usage_basis"], "mixed")
        self.assertEqual((mixed["reported_sessions"], mixed["estimated_sessions"]), (1, 1))

    def test_cursor_sparse_context_is_interpolated_between_persisted_checkpoints(self):
        groups = [
            {"context": {}},
            {"context": {"tokensUsed": 60000, "tokenLimit": 200000}},
            {"context": {}},
        ]
        rows = meter.cursor_context_estimates(groups, 90000, 200000)
        self.assertEqual([row["tokens"] for row in rows], [30000, 60000, 90000])
        self.assertTrue(rows[0]["interpolated"])
        self.assertFalse(rows[1]["interpolated"])

    def test_mixed_runtime_coverage_is_explicitly_partial(self):
        covered = {"availability": meter.metric_availability("codex")}
        cursor = {"availability": meter.metric_availability("cursor")}
        self.assertEqual(meter.metric_coverage([covered, cursor], "cost"), {
            "covered_sessions": 1, "total_sessions": 2, "complete": False,
        })


class ExecutionTimingTests(unittest.TestCase):
    def test_merges_overlapping_execution_windows(self):
        seconds = meter._merge_execution_intervals([(0, 10), (5, 15), (20, 25)])
        self.assertEqual(seconds, 20)

    def test_uses_claude_reported_turn_durations(self):
        objs = [
            {"type": "user", "timestamp": "2026-06-30T00:00:00.000Z", "message": {"content": "first"}},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-06-30T00:00:10.000Z", "durationMs": 10000},
            {"type": "user", "timestamp": "2026-06-30T00:01:00.000Z", "message": {"content": "second"}},
            {"type": "system", "subtype": "turn_duration", "timestamp": "2026-06-30T00:01:20.000Z", "durationMs": 20000},
        ]
        timing = meter.execution_timing("claude", objs)
        self.assertEqual(timing["duration_s"], 30)
        self.assertEqual(timing["reported_executions"], 2)
        self.assertEqual(timing["basis"], "reported")

    def test_claude_observed_fallback_excludes_between_prompt_idle_gap(self):
        objs = [
            {"type": "user", "timestamp": "2026-06-30T00:00:00.000Z", "message": {"content": "first"}},
            {"type": "assistant", "timestamp": "2026-06-30T00:00:10.000Z", "message": {"content": "done"}},
            {"type": "user", "timestamp": "2026-06-30T01:00:00.000Z", "message": {"content": "second"}},
            {"type": "assistant", "timestamp": "2026-06-30T01:00:20.000Z", "message": {"content": "done"}},
        ]
        timing = meter.execution_timing("claude", objs)
        self.assertEqual(timing["duration_s"], 30)
        self.assertEqual(timing["observed_executions"], 2)
        self.assertEqual(timing["basis"], "observed")

    def test_combines_codex_reported_and_open_execution_time(self):
        objs = [
            {"timestamp": "2026-06-30T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-06-30T00:00:30.000Z", "payload": {"type": "task_complete", "duration_ms": 20000}},
            {"timestamp": "2026-06-30T00:01:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-06-30T00:01:15.000Z", "payload": {"type": "agent_message"}},
        ]
        timing = meter.execution_timing("codex", objs)
        self.assertEqual(timing["duration_s"], 35)
        self.assertEqual(timing["reported_executions"], 1)
        self.assertEqual(timing["observed_executions"], 1)
        self.assertEqual(timing["basis"], "reported + observed")

    def test_codex_context_accepts_structured_approval_policy(self):
        self.assertEqual(meter.codex_approval_policy_label("on_request"), "on request")
        self.assertEqual(
            meter.codex_approval_policy_label({
                "granular": {
                    "sandbox_approval": False,
                    "request_permissions": True,
                },
            }),
            "granular",
        )


class WaitTimeTests(unittest.TestCase):
    def test_claude_wait_is_prompt_to_completion_and_dedupes_split_messages(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-13T00:00:00.000Z",
             "message": {"content": "do the work"}},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:02.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "working"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:03.000Z", "message": {
                "id": "msg-1", "model": "claude-opus-4-8",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Read"}],
                "usage": {"input_tokens": 20, "output_tokens": 30}, "stop_reason": "tool_use",
            }},
            {"type": "user", "timestamp": "2026-07-13T00:00:04.000Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}],
            }},
            {"type": "assistant", "timestamp": "2026-07-13T00:00:09.000Z", "message": {
                "id": "msg-2", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "finished"}],
                "usage": {"input_tokens": 25, "output_tokens": 20}, "stop_reason": "end_turn",
            }},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-07-13T00:00:10.000Z", "durationMs": 10000},
        ]
        samples = meter.claude_wait_samples(objs)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["duration_s"], 10)
        self.assertEqual(samples[0]["tool_calls"], 1)
        self.assertEqual(samples[0]["output_tokens"], 50)
        self.assertEqual(samples[0]["timing_basis"], "reported")

    def test_codex_wait_uses_reported_duration_and_includes_tool_time(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-13T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-13T00:00:00.000Z", "payload": {"type": "task_started"}},
            {"timestamp": "2026-07-13T00:00:02.000Z", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "call-1",
            }},
            {"timestamp": "2026-07-13T00:00:08.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                }},
            }},
            {"timestamp": "2026-07-13T00:00:10.000Z", "payload": {
                "type": "task_complete", "duration_ms": 10000,
            }},
        ]
        sample = meter.codex_wait_samples(objs)[0]
        self.assertEqual(sample["duration_s"], 10)
        self.assertEqual(sample["tool_calls"], 1)
        self.assertEqual(sample["output_tokens"], 40)
        self.assertEqual(sample["model"], "gpt-5.6")

    def test_claude_observed_wait_excludes_time_waiting_for_user_input(self):
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
        sample = meter.claude_wait_samples(objs)[0]
        self.assertEqual(sample["wall_duration_s"], 110)
        self.assertEqual(sample["user_pause_s"], 90)
        self.assertEqual(sample["duration_s"], 20)
        self.assertEqual(meter.wait_time_summary([sample])["user_pause_s"], 90)

    def test_wait_summary_reports_average_p95_and_longest(self):
        summary = meter.wait_time_summary([
            {"duration_s": 2, "timing_basis": "reported"},
            {"duration_s": 6, "timing_basis": "observed"},
            {"duration_s": 10, "timing_basis": "reported"},
        ])
        self.assertEqual(summary["total_s"], 18)
        self.assertEqual(summary["avg_s"], 6)
        self.assertEqual(summary["median_s"], 6)
        self.assertEqual(summary["p95_s"], 10)
        self.assertEqual(summary["max_s"], 10)


class ModelPerformanceTests(unittest.TestCase):
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

        self.assertEqual(list(windows), ["today", "yesterday", "7", "30", "90", "all"])
        for name in ("today", "yesterday"):
            self.assertEqual(len(windows[name]), 1)
            self.assertEqual(windows[name][0]["a_samples"], 20)
            self.assertEqual(windows[name][0]["b_samples"], 20)
        self.assertEqual(windows["7"][0]["a_samples"], 40)
        self.assertEqual(windows["7"][0]["b_samples"], 40)
        self.assertEqual(windows["all"][0]["a_samples"], 60)
        self.assertEqual(windows["all"][0]["b_samples"], 60)

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
        self.assertGreater(comparison.call_count, unchanged_call_count)
        self.assertEqual(first["windows"]["today"][0]["pace_ratio"], 2)
        self.assertEqual(changed["windows"]["today"][0]["pace_ratio"], 3)

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


class FrustrationSignalTests(unittest.TestCase):
    def test_language_signal_settings_migrate_friction_and_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "frustration_terms": ["damn"],
                "model_pricing": {"claude": {"custom": {
                    "input": 1, "output": 2, "cache_write": 0, "cache_read": 0,
                }}},
            }))
            loaded = meter.language_signal_settings(str(path))
            saved = meter.set_language_signal_terms(
                {"positive": ["Perfect", "thanks"], "friction": ["damn"]},
                str(path),
            )
            stored = json.loads(path.read_text())
        self.assertEqual(loaded["friction"], ["damn"])
        self.assertEqual(loaded["positive"], meter.DEFAULT_POSITIVE_TERMS)
        self.assertTrue(saved["ok"])
        self.assertEqual(stored["language_signal_terms"], {
            "positive": ["perfect", "thanks"], "friction": ["damn"],
        })
        self.assertNotIn("frustration_terms", stored)
        self.assertIn("model_pricing", stored)

    def test_positive_and_friction_are_aggregated_independently_without_text(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-07T08:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:00:02.000Z",
             "payload": {"type": "user_message", "message": "Perfect, thank you"}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:02:00.000Z",
             "payload": {"type": "user_message", "message": "damn this is broken"}},
        ]
        rollups, events = meter.analyze_language_signals(
            "codex", objs, {"positive": ["perfect", "thank you"], "friction": ["damn"]}
        )
        self.assertEqual(rollups["positive"]["utterances"], 1)
        self.assertEqual(rollups["positive"]["matches"], 2)
        self.assertEqual(rollups["friction"]["utterances"], 1)
        self.assertEqual(rollups["friction"]["matches"], 1)
        self.assertNotIn("text", events["positive"][0])
        self.assertNotIn("text", events["friction"][0])

    def test_matches_whole_terms_and_counts_repeated_hits(self):
        counts = meter.frustration_term_counts(
            "Fuck, fuck this bullshit. Classify is safe.", ["fuck", "bullshit", "ass"]
        )
        self.assertEqual(counts, {"fuck": 2, "bullshit": 1})

    def test_codex_prefers_canonical_user_events_and_tracks_model(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-07T08:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"type": "response_item", "timestamp": "2026-07-07T08:00:01.000Z",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "# AGENTS.md instructions\nshit appears in config"}
             ]}},
            {"type": "response_item", "timestamp": "2026-07-07T08:00:02.000Z",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "fuck this"}
             ]}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:00:02.000Z",
             "payload": {"type": "user_message", "message": "fuck this"}},
            {"type": "event_msg", "timestamp": "2026-07-07T08:02:00.000Z",
             "payload": {"type": "user_message", "message": "looks good"}},
        ]
        summary, events = meter.analyze_frustration("codex", objs, ["fuck", "shit"])
        self.assertEqual(summary["user_turns"], 2)
        self.assertEqual(summary["utterances"], 1)
        self.assertEqual(summary["matches"], 1)
        self.assertEqual(summary["rate"], 0.5)
        self.assertEqual(summary["models"][0]["model"], "gpt-5.6")
        self.assertNotIn("text", events[0])

    def test_claude_skips_tool_and_sidechain_user_records(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-07T08:00:00.000Z",
             "message": {"content": "this is fucking shit"}},
            {"type": "assistant", "timestamp": "2026-07-07T08:00:02.000Z",
             "message": {"model": "claude-opus-4-8", "content": []}},
            {"type": "user", "timestamp": "2026-07-07T08:00:03.000Z",
             "sourceToolAssistantUUID": "tool-call", "message": {"content": [
                 {"type": "tool_result", "content": "idiot in command output"}
             ]}},
            {"type": "user", "timestamp": "2026-07-07T08:00:04.000Z",
             "isSidechain": True, "message": {"content": "bullshit from an agent"}},
            {"type": "user", "timestamp": "2026-07-07T08:01:00.000Z",
             "message": {"content": "try again"}},
            {"type": "assistant", "timestamp": "2026-07-07T08:01:02.000Z",
             "message": {"model": "claude-sonnet-5", "content": []}},
        ]
        summary, _ = meter.analyze_frustration(
            "claude", objs, ["fucking", "shit", "idiot", "bullshit"]
        )
        self.assertEqual(summary["user_turns"], 2)
        self.assertEqual(summary["utterances"], 1)
        self.assertEqual(summary["matches"], 2)
        self.assertEqual([row["model"] for row in summary["models"]],
                         ["claude-opus-4-8", "claude-sonnet-5"])

    def test_aggregates_sessions_into_days_weeks_and_models(self):
        event_one = {"ts": 1, "day": "2026-07-06", "week": "2026-07-06",
                     "model": "gpt-5.6", "utterance": True, "matches": 2,
                     "term_counts": {"fuck": 2}}
        event_two = {"ts": 2, "day": "2026-07-07", "week": "2026-07-06",
                     "model": "claude-opus-4-8", "utterance": False, "matches": 0,
                     "term_counts": {}}
        sessions = [
            {"_frustration_events": [event_one], "frustration": {"user_turns": 1, "utterances": 1}},
            {"_frustration_events": [event_two], "frustration": {"user_turns": 1, "utterances": 0}},
        ]
        result = meter.aggregate_frustration(sessions, ["fuck"])
        self.assertEqual(result["user_turns"], 2)
        self.assertEqual(result["utterances"], 1)
        self.assertEqual(result["rate"], 0.5)
        self.assertEqual(result["matches"], 2)
        self.assertEqual(result["affected_sessions"], 1)
        self.assertEqual(result["weekly"][0]["week"], "2026-07-06")
        self.assertEqual(len(result["daily"]), 2)
        self.assertEqual(len(result["models"]), 2)

    def test_aggregate_keeps_same_model_separate_by_runtime(self):
        event = {"ts": 1, "day": "2026-07-20", "week": "2026-07-20",
                 "model": "gpt-5.6", "utterance": True, "matches": 1,
                 "term_counts": {"shit": 1}}
        sessions = [
            {"provider": "codex", "runtime": "Codex", "_frustration_events": [event],
             "frustration": {"user_turns": 1, "utterances": 1}},
            {"provider": "cursor", "runtime": "Cursor", "_frustration_events": [event],
             "frustration": {"user_turns": 1, "utterances": 1}},
        ]
        result = meter.aggregate_frustration(sessions, ["shit"])
        self.assertEqual({(row["id"], row["runtime"]) for row in result["models"]}, {
            ("gpt-5.6::Codex", "Codex"), ("gpt-5.6::Cursor", "Cursor"),
        })

    def test_persists_machine_wide_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            result = meter.set_frustration_terms("Damn, custom phrase, damn", str(path))
            loaded = meter.frustration_settings(str(path))
        self.assertTrue(result["ok"])
        self.assertEqual(loaded["terms"], ["damn", "custom phrase"])


class PricingTests(unittest.TestCase):
    def test_builtin_pricing_exposes_reviewed_primary_sources(self):
        pricing = meter.model_pricing_settings()
        self.assertEqual(pricing["reviewed_on"], "2026-09-07")
        self.assertEqual(
            [source["provider"] for source in pricing["sources"]],
            ["anthropic", "openai", "cursor"],
        )
        self.assertEqual(
            [source["url"] for source in pricing["sources"]],
            [
                "https://platform.claude.com/docs/en/about-claude/pricing",
                "https://developers.openai.com/api/docs/models/compare",
                "https://cursor.com/docs/models-and-pricing",
            ],
        )

    def test_current_anthropic_builtin_prices_match_primary_source(self):
        self.assertEqual(meter.CLAUDE_PRICE["claude-fable-5"], {
            "input": 10.0, "output": 50.0,
            "cache_write": 12.5, "cache_read": 1.0,
        })
        self.assertEqual(meter.CLAUDE_PRICE["claude-opus-4-8"], {
            "input": 5.0, "output": 25.0,
            "cache_write": 6.25, "cache_read": 0.5,
        })

    def test_opus_5_uses_published_api_rates(self):
        price, approximate = meter.price_for("claude-opus-5", "claude")
        self.assertEqual(price, {
            "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5,
        })
        self.assertFalse(approximate)
        row = next(
            item for item in meter.model_pricing_settings()["models"]
            if item["provider"] == "claude" and item["model"] == "claude-opus-5"
        )
        self.assertTrue(row["builtin"])
        self.assertFalse(row["overridden"])
        self.assertEqual(row["source"], "built-in")

    def test_sonnet_5_uses_current_standard_api_rates(self):
        price, approximate = meter.price_for("claude-sonnet-5", "claude")
        self.assertEqual(price, {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2})
        self.assertFalse(approximate)

    def test_unknown_models_do_not_inherit_runtime_default_prices(self):
        for provider, model, default_price in (
            ("claude", "claude-future-unknown", meter.CLAUDE_PRICE[meter.DEFAULT_CLAUDE_MODEL]),
            ("codex", "gpt-future-unknown", meter.OPENAI_PRICE[meter.DEFAULT_OPENAI_MODEL]),
            ("opencode", "future-model", {
                "input": 2.0, "output": 10.0,
                "cache_write": 2.5, "cache_read": 0.2,
            }),
        ):
            with self.subTest(provider=provider, model=model):
                price, unavailable = meter.price_for(model, provider)
                self.assertEqual(price, meter.ZERO_PRICE)
                self.assertNotEqual(price, default_price)
                self.assertTrue(unavailable)

    def test_codex_path_discovery_does_not_invent_a_default_model(self):
        path = os.path.expanduser("~/.codex/sessions/model-less.jsonl")
        with (
            mock.patch.object(meter, "all_session_sources", return_value=[]),
            mock.patch.object(meter, "codex_meta", return_value={}),
            mock.patch.object(meter, "safe_mtime", return_value=0),
        ):
            source = meter.source_from_path(path)

        self.assertEqual(source["model"], "unknown-model")

    def test_gpt_5_6_uses_current_sol_api_rates(self):
        price, approximate = meter.price_for("gpt-5.6", "codex")
        expected = {
            "input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.4,
        }
        self.assertEqual(price, expected)
        self.assertFalse(approximate)
        explicit, explicit_approximate = meter.price_for("gpt-5.6-sol", "codex")
        self.assertEqual(explicit, price)
        self.assertFalse(explicit_approximate)
        for model in ("gpt-5.6", "gpt-5.6-sol"):
            with self.subTest(model=model):
                row = next(
                    item for item in meter.model_pricing_settings()["models"]
                    if item["provider"] == "codex" and item["model"] == model
                )
                self.assertEqual(row["prices"], expected)
                self.assertEqual(row["source"], "built-in")

    def test_gpt_5_6_current_tier_prices_match_official_model_catalog(self):
        expected = {
            "gpt-5.6-terra": {
                "input": 2.0, "output": 12.0, "cache_write": 2.5, "cache_read": 0.2,
            },
            "gpt-5.6-luna": {
                "input": 0.2, "output": 1.2, "cache_write": 0.25, "cache_read": 0.02,
            },
        }
        for model, prices in expected.items():
            with self.subTest(model=model):
                actual, approximate = meter.price_for(model, "codex")
                self.assertEqual(actual, prices)
                self.assertFalse(approximate)

    def test_gpt_6_astra_uses_official_api_rates_and_appears_in_settings(self):
        expected = {
            "input": 10.0, "output": 50.0,
            "cache_write": 0.0, "cache_read": 1.0,
        }
        actual, unavailable = meter.price_for("gpt-6-astra", "codex")

        self.assertEqual(actual, expected)
        self.assertFalse(unavailable)
        row = next(
            item for item in meter.model_pricing_settings()["models"]
            if item["provider"] == "codex" and item["model"] == "gpt-6-astra"
        )
        self.assertTrue(row["builtin"])
        self.assertFalse(row["overridden"])
        self.assertEqual(row["source"], "built-in")
        self.assertEqual(row["prices"], expected)

    def test_gpt_5_6_price_cutover_preserves_older_session_estimates(self):
        previous = {
            "input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.5,
        }
        for model, current_input in (("gpt-5.6-terra", 2.0), ("gpt-5.6-luna", 0.2)):
            with self.subTest(model=model):
                old, old_approximate = meter.price_for(
                    model, "codex", at=meter.GPT_56_PRICE_UPDATE_AT - 1,
                )
                current, current_approximate = meter.price_for(
                    model, "codex", at=meter.GPT_56_PRICE_UPDATE_AT,
                )
                self.assertEqual(old, previous)
                self.assertFalse(old_approximate)
                self.assertEqual(current["input"], current_input)
                self.assertFalse(current_approximate)

    def test_gpt_5_6_long_context_multiplier_starts_at_price_cutover(self):
        usage = {
            "input_tokens": meter.GPT_56_LONG_CONTEXT_TOKENS + 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100_000,
        }
        old = meter.cost_of(
            usage, "gpt-5.6-terra", "codex", at=meter.GPT_56_PRICE_UPDATE_AT - 1,
        )
        current = meter.cost_of(
            usage, "gpt-5.6-terra", "codex", at=meter.GPT_56_PRICE_UPDATE_AT,
        )
        self.assertAlmostEqual(old["input"], usage["input_tokens"] * 5.0 / 1e6)
        self.assertAlmostEqual(old["output"], 3.0)
        self.assertAlmostEqual(current["input"], usage["input_tokens"] * 2.0 * 2.0 / 1e6)
        self.assertAlmostEqual(current["output"], 1.8)

    def test_custom_model_price_persists_and_drives_cost_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                result = meter.set_model_price(
                    "openai", "gpt-custom-1",
                    {"input": 1.25, "output": 6.5, "cache_write": 0, "cache_read": 0.2},
                )
                price, approximate = meter.price_for("gpt-custom-1", "codex")
                cost = meter.cost_of({
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cache_creation_input_tokens": 1_000_000,
                    "cache_read_input_tokens": 1_000_000,
                }, "gpt-custom-1", "codex")
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(price, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertFalse(approximate)
        self.assertEqual(cost, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertIn("gpt-custom-1", saved["model_pricing"]["codex"])
        self.assertIsInstance(saved["model_pricing"]["codex"]["gpt-custom-1"], list)
        self.assertIsNotNone(
            saved["model_pricing"]["codex"]["gpt-custom-1"][0]["effective_from"]
        )

    def test_model_price_batch_persists_multiple_rows_with_one_atomic_write(self):
        changes = [
            {
                "provider": "claude", "model": "claude-opus-5",
                "prices": {"input": 6, "output": 30, "cache_write": 7.5, "cache_read": 0.6},
            },
            {
                "provider": "codex", "model": "gpt-5.4",
                "prices": {"input": 3, "output": 18, "cache_write": 0, "cache_read": 0.3},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original_write = meter.atomic_write_text
            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=original_write) as write:
                result = meter.set_model_prices(
                    changes, path=str(path), effective_from=100,
                )
            saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["effective_scope"], "selected_time")
        self.assertEqual(len(result["changes"]), 2)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(
            saved["model_pricing"]["claude"]["claude-opus-5"][0]["effective_from"],
            100.0,
        )
        self.assertEqual(
            saved["model_pricing"]["codex"]["gpt-5.4"][0]["effective_from"],
            100.0,
        )

    def test_invalid_model_price_batch_is_rejected_without_any_write(self):
        changes = [
            {
                "provider": "codex", "model": "gpt-valid",
                "prices": {"input": 1, "output": 4, "cache_write": 0, "cache_read": 0.1},
            },
            {
                "provider": "codex", "model": "gpt-invalid",
                "prices": {"input": -1, "output": 4, "cache_write": 0, "cache_read": 0.1},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"keep": "unchanged"}\n')
            before = path.read_text()
            with mock.patch.object(meter, "atomic_write_text") as write:
                result = meter.set_model_prices(changes, path=str(path))
            after = path.read_text()
        self.assertFalse(result["ok"])
        self.assertEqual(before, after)
        write.assert_not_called()

    def test_model_price_batch_rejects_duplicate_provider_model_entries(self):
        price = {"input": 1, "output": 4, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            result = meter.set_model_prices([
                {"provider": "openai", "model": "gpt-duplicate", "prices": price},
                {"provider": "codex", "model": "gpt-duplicate", "prices": price},
            ], path=str(path))
        self.assertFalse(result["ok"])
        self.assertIn("duplicate", result["error"].lower())
        self.assertFalse(path.exists())

    def test_builtin_override_can_be_restored_without_losing_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"frustration_terms": ["damn"]}))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                changed = meter.set_model_price(
                    "claude", "claude-sonnet-5",
                    {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4},
                    effective_from=100,
                )
                price, approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=150,
                )
                pricing = meter.model_pricing_settings()
                restored = meter.set_model_price(
                    "claude", "claude-sonnet-5", remove=True,
                    effective_from=200,
                )
                default_price, default_approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=200,
                )
                historical_price, _ = meter.price_for(
                    "claude-sonnet-5", "claude", at=150,
                )
                saved = json.loads(path.read_text())
        row = next(
            item for item in pricing["models"]
            if item["provider"] == "claude" and item["model"] == "claude-sonnet-5"
        )
        self.assertTrue(changed["ok"])
        self.assertEqual(price["input"], 4.0)
        self.assertFalse(approximate)
        self.assertEqual(row["source"], "override")
        self.assertTrue(restored["ok"])
        self.assertEqual(default_price, meter.CLAUDE_PRICE["claude-sonnet-5"])
        self.assertFalse(default_approximate)
        self.assertEqual(historical_price["input"], 4.0)
        self.assertEqual(saved["frustration_terms"], ["damn"])
        periods = saved["model_pricing"]["claude"]["claude-sonnet-5"]
        self.assertEqual(periods[0]["effective_from"], 100.0)
        self.assertEqual(periods[1], {"effective_from": 200.0, "use_builtin": True})

    def test_legacy_override_remains_all_history_and_migrates_on_next_edit(self):
        legacy = {"input": 7, "output": 21, "cache_write": 8, "cache_read": 0.7}
        updated = {"input": 4, "output": 12, "cache_write": 5, "cache_read": 0.4}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "model_pricing": {"codex": {"gpt-5.6-terra": legacy}},
                "language_signal_terms": {"positive": ["great"], "friction": ["damn"]},
            }))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                before_migration, _ = meter.price_for("gpt-5.6-terra", "codex", at=1)
                result = meter.set_model_price(
                    "codex", "gpt-5.6-terra", updated, effective_from=500,
                )
                before_cutoff, _ = meter.price_for("gpt-5.6-terra", "codex", at=499)
                at_cutoff, _ = meter.price_for("gpt-5.6-terra", "codex", at=500)
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(before_migration, {key: float(value) for key, value in legacy.items()})
        self.assertEqual(before_cutoff, before_migration)
        self.assertEqual(at_cutoff, {key: float(value) for key, value in updated.items()})
        self.assertEqual(saved["language_signal_terms"]["positive"], ["great"])
        periods = saved["model_pricing"]["codex"]["gpt-5.6-terra"]
        self.assertIsNone(periods[0]["effective_from"])
        self.assertEqual(periods[1]["effective_from"], 500.0)

    def test_custom_model_can_be_retired_without_losing_historical_price(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                added = meter.set_model_price(
                    "codex", "gpt-retired", prices, effective_from=100,
                )
                retired = meter.set_model_price(
                    "codex", "gpt-retired", remove=True, effective_from=200,
                )
                old, old_approximate = meter.price_for("gpt-retired", "codex", at=150)
                current, current_approximate = meter.price_for("gpt-retired", "codex", at=200)
                pricing = meter.model_pricing_settings()
                saved = json.loads(path.read_text())
        row = next(item for item in pricing["models"] if item["model"] == "gpt-retired")
        self.assertTrue(added["ok"])
        self.assertTrue(retired["ok"])
        self.assertEqual(old, {key: float(value) for key, value in prices.items()})
        self.assertFalse(old_approximate)
        self.assertTrue(current_approximate)
        self.assertNotEqual(current, old)
        self.assertFalse(row["active"])
        self.assertEqual(row["source"], "retired")
        self.assertEqual(row["effective_from"], 200.0)
        self.assertEqual(row["prices"], old)
        self.assertEqual(pricing["retired_custom_models"], 1)
        self.assertEqual(
            saved["model_pricing"]["codex"]["gpt-retired"][-1],
            {"effective_from": 200.0, "inactive": True},
        )

    def test_apply_to_all_history_is_explicit_and_replaces_timeline(self):
        first = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        global_price = {"input": 2, "output": 6, "cache_write": 0, "cache_read": 0.2}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                meter.set_model_price("codex", "gpt-global", first, effective_from=100)
                result = meter.set_model_price(
                    "codex", "gpt-global", global_price, apply_to_all_history=True,
                )
                historical, approximate = meter.price_for("gpt-global", "codex", at=1)
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(result["apply_to_all_history"])
        self.assertIsNone(result["effective_from"])
        self.assertFalse(approximate)
        self.assertEqual(historical, {key: float(value) for key, value in global_price.items()})
        self.assertEqual(len(saved["model_pricing"]["codex"]["gpt-global"]), 1)
        self.assertIsNone(saved["model_pricing"]["codex"]["gpt-global"][0]["effective_from"])

    def test_identical_current_price_does_not_add_redundant_period(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            first = meter.set_model_price(
                "codex", "gpt-idempotent", prices, path=str(path), effective_from=100,
            )
            repeated = meter.set_model_price(
                "codex", "gpt-idempotent", prices, path=str(path), effective_from=200,
            )
            saved = json.loads(path.read_text())
        self.assertTrue(first["changed"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(len(saved["model_pricing"]["codex"]["gpt-idempotent"]), 1)

    def test_session_cost_can_span_manual_price_periods(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        override = {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                meter.set_model_price(
                    "claude", "claude-sonnet-5", override, effective_from=100,
                )
                before = meter.cost_of(usage, "claude-sonnet-5", "claude", at=99)
                after = meter.cost_of(usage, "claude-sonnet-5", "claude", at=100)
        self.assertEqual(before["input"], 2.0)
        self.assertEqual(after["input"], 4.0)
        self.assertEqual(before["input"] + after["input"], 6.0)

    def test_oversized_manual_history_is_ignored_without_repricing_builtin(self):
        prices = {"input": 9, "output": 9, "cache_write": 9, "cache_read": 9}
        periods = [
            {"effective_from": index, "prices": prices}
            for index in range(meter.MAX_MODEL_PRICE_PERIODS + 1)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "model_pricing": {"claude": {"claude-sonnet-5": periods}},
            }))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                price, approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=meter.MAX_MODEL_PRICE_PERIODS + 2,
                )
                row = next(
                    item for item in meter.model_pricing_settings()["models"]
                    if item["provider"] == "claude" and item["model"] == "claude-sonnet-5"
                )
        self.assertEqual(price, meter.CLAUDE_PRICE["claude-sonnet-5"])
        self.assertFalse(approximate)
        self.assertEqual(row["periods"], 0)

    def test_model_pricing_http_action_forwards_bounded_scope_fields(self):
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        self.assertIn('if isinstance(payload.get("changes"), list)', source)
        self.assertIn('set_model_prices(', source)
        self.assertIn(
            'apply_to_all_history=payload.get("apply_to_all_history") is True',
            source,
        )
        self.assertIn('effective_from=payload.get("effective_from")', source)

    def test_future_or_conflicting_effective_scope_is_rejected(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            future = meter.set_model_price(
                "codex", "gpt-future", prices, path=str(path),
                effective_from=meter.time.time() + 600,
            )
            conflicting = meter.set_model_price(
                "codex", "gpt-conflict", prices, path=str(path),
                effective_from=100, apply_to_all_history=True,
            )
        self.assertFalse(future["ok"])
        self.assertIn("future", future["error"])
        self.assertFalse(conflicting["ok"])
        self.assertIn("either", conflicting["error"])
        self.assertFalse(path.exists())

    def test_invalid_custom_model_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            bad_model = meter.set_model_price(
                "codex", "model with spaces",
                {"input": 1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
            bad_price = meter.set_model_price(
                "codex", "gpt-custom",
                {"input": -1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
        self.assertFalse(bad_model["ok"])
        self.assertFalse(bad_price["ok"])
        self.assertFalse(path.exists())

    def test_longest_model_prefix_wins(self):
        price, approximate = meter.price_for("gpt-5.4-mini-2026-07-01", "codex")
        self.assertEqual(price, meter.OPENAI_PRICE["gpt-5.4-mini"])
        self.assertFalse(approximate)


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


class CodexLineageAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.trace_root = self.sessions / "2026" / "08" / "11"
        self.trace_root.mkdir(parents=True)
        self.index = self.root / "session_index.jsonl"
        self.index.write_text("")
        self.context = DiscoveryContext(home=str(self.root))
        self.adapter = CodexRuntimeAdapter(
            self.sessions,
            self.index,
            compatibility=meter._codex_compatibility(),
            default_model="gpt-5.6",
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_trace(self, name, rows, mtime):
        path = self.trace_root / f"rollout-{name}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def meta(physical_id, logical_id="task-1", **extra):
        return {
            "timestamp": "2026-08-11T00:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": physical_id,
                "session_id": logical_id,
                "cwd": "/work/project",
                **extra,
            },
        }

    @staticmethod
    def turn(timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "turn_context",
            "payload": {"model": "gpt-5.6", "cwd": "/work/project"},
        }

    @staticmethod
    def tool(name, call_id, timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": name,
                "call_id": call_id,
                "arguments": {},
            },
        }

    @staticmethod
    def tokens(input_tokens, output_tokens, total_input, total_output, timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
                "total_token_usage": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                },
            }},
        }

    def root_and_child_sources(self):
        self.write_trace("root", [
            self.meta("root-1"),
            self.turn("2026-08-11T00:00:01Z"),
            {"timestamp": "2026-08-11T00:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T00:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:03Z"),
            {"timestamp": "2026-08-11T00:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
        ], 10)
        self.write_trace("child", [
            self.meta("child-1", forked_from_id="root-1"),
            self.meta("root-1"),
            self.turn("2026-08-11T01:00:01Z"),
            {"timestamp": "2026-08-11T01:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T01:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:03Z"),
            {"timestamp": "2026-08-11T01:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T02:00:01Z"),
            {"timestamp": "2026-08-11T02:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("child_tool", "child-call", "2026-08-11T02:00:02Z"),
            self.tokens(50, 5, 150, 15, "2026-08-11T02:00:03Z"),
        ], 20)
        return {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

    def root_child_and_grandchild_sources(self):
        self.root_and_child_sources()
        self.write_trace("grandchild", [
            self.meta("grandchild-1", forked_from_id="child-1"),
            self.turn("2026-08-11T03:00:01Z"),
            {"timestamp": "2026-08-11T03:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T03:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T03:00:03Z"),
            {"timestamp": "2026-08-11T03:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T04:00:01Z"),
            {"timestamp": "2026-08-11T04:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("child_tool", "child-call", "2026-08-11T04:00:02Z"),
            self.tokens(50, 5, 150, 15, "2026-08-11T04:00:03Z"),
            {"timestamp": "2026-08-11T04:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T05:00:01Z"),
            {"timestamp": "2026-08-11T05:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("grandchild_tool", "grandchild-call", "2026-08-11T05:00:02Z"),
            self.tokens(20, 2, 170, 17, "2026-08-11T05:00:03Z"),
        ], 30)
        return {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

    def test_legacy_detail_and_summary_use_the_same_corrected_child_rows(self):
        sources = self.root_and_child_sources()

        detail = self.adapter.recompute_legacy(sources["child-1"])
        summary = self.adapter.summarize_legacy(sources["child-1"])

        self.assertEqual(detail["total_tokens"], 55)
        self.assertEqual(summary["tokens"], 55)
        self.assertEqual(detail["total_tokens"], summary["tokens"])
        self.assertAlmostEqual(detail["total_cost"], summary["cost"])
        self.assertEqual(len(detail["executions"]), 1)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(
            [tool["name"] for tool in detail["executions"][0]["tools"]],
            ["child_tool"],
        )
        self.assertNotIn("complete", [event["kind"] for event in detail["trace"]])

    def test_live_and_completed_throughput_use_only_corrected_child_rows(self):
        sources = self.root_and_child_sources()

        summary = self.adapter.summarize_legacy(sources["child-1"])

        self.assertFalse(summary["throughput"]["available"])
        self.assertTrue(summary["live_throughput"]["available"])
        self.assertEqual(summary["live_throughput"]["completed_steps"], 1)
        self.assertEqual(summary["live_throughput"]["measured_output_tokens"], 5)
        self.assertEqual(summary["live_throughput"]["measured_seconds"], 2)
        self.assertEqual(summary["live_throughput"]["output_tps"], 2.5)

    def test_auto_review_child_inherits_its_linked_parent_model(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
        ], 10)
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }
        native = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value.endswith("rollout-auto-review.jsonl")
        )
        detail = self.adapter.recompute_legacy(sources["auto-review-1"])
        summary = self.adapter.summarize_legacy(sources["auto-review-1"])

        self.assertEqual(sources["auto-review-1"]["model"], "gpt-5.6")
        self.assertEqual(native.model_ref.model_id, "gpt-5.6")
        self.assertEqual(detail["primary_model"], "gpt-5.6")
        self.assertTrue(detail["availability"]["cost"])
        self.assertGreater(detail["total_cost"], 0)
        self.assertEqual(summary["primary_model"], "gpt-5.6")
        self.assertTrue(summary["availability"]["cost"])
        self.assertGreater(summary["cost"], 0)

    def test_auto_review_child_without_a_linked_parent_stays_unknown(self):
        self.write_trace("orphaned-auto-review", [
            self.meta("orphaned-auto-review-1", parent_thread_id="missing-parent"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        source = self.adapter.discover_legacy(self.context)[0]

        self.assertEqual(source["model"], "unknown-model")

    def test_auto_review_fork_without_a_desktop_parent_stays_unknown(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
        ], 10)
        self.write_trace("forked-auto-review", [
            self.meta("forked-auto-review-1", forked_from_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

        self.assertEqual(sources["forked-auto-review-1"]["model"], "unknown-model")

    def test_auto_review_child_with_a_cyclic_parent_stays_unknown(self):
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 10)
        self.write_trace("parent", [
            self.meta("parent-1", parent_thread_id="auto-review-1"),
            {
                "timestamp": "2026-08-11T01:00:02.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

        self.assertEqual(sources["auto-review-1"]["model"], "unknown-model")

    def test_auto_review_child_deduplicates_an_inherited_parent_token_row(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:02Z"),
        ], 10)
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }
        summary = self.adapter.summarize_legacy(sources["auto-review-1"])

        self.assertEqual(summary["tokens"], 0)
        self.assertEqual(summary["turns"], 0)

    def test_cross_session_daily_models_spend_and_budget_share_corrected_totals(self):
        sources = self.root_child_and_grandchild_sources()
        saved_xsess = dict(meter._xsess)
        saved_summaries = dict(meter._summary_cache)
        budget_now = datetime.datetime(2026, 8, 11, tzinfo=datetime.timezone.utc)
        original_budget_status = meter.monthly_budget_status
        try:
            meter._xsess.update({
                "data": None,
                "at": 0,
                "sessions": [],
                "internal_rows": (),
                "project_model_stats": {},
            })
            meter._summary_cache.clear()
            with mock.patch.object(
                meter, "_codex_native_adapter", return_value=self.adapter,
            ), mock.patch.object(
                meter,
                "monthly_budget_status",
                side_effect=lambda months, settings: original_budget_status(
                    months, settings, now=budget_now,
                ),
            ):
                result = meter.cross_session(sources=list(sources.values()))
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_xsess)
            meter._summary_cache.clear()
            meter._summary_cache.update(saved_summaries)

        self.assertEqual(result["total_tokens"], 187)
        self.assertEqual(result["total_executions"], 3)
        self.assertAlmostEqual(
            result["total_cost"],
            sum(session["cost"] for session in result["sessions"]),
        )
        self.assertEqual(sum(
            row["input_tokens"] + row["output_tokens"]
            for row in result["model_stats"]["models"]
        ), 187)
        self.assertEqual(sum(row["tokens"] for row in result["daily"]), 187)
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["daily"]),
            result["total_cost"],
        )
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["spend"]["days"]),
            result["total_cost"],
        )
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["monthly"]),
            result["total_cost"],
        )
        self.assertEqual(result["budget"]["month"], "2026-08")
        self.assertAlmostEqual(result["budget"]["spend"], result["total_cost"])
        encoded = json.dumps(result)
        for private_field in (
            "physical_trace_id",
            "logical_session_id",
            "forked_from_id",
            "parent_thread_id",
            "lineage_parent_id",
            "lineage_revision",
        ):
            self.assertNotIn(private_field, encoded)

    def test_legacy_cache_signature_changes_with_lineage_revision(self):
        source = {
            "path": str(self.trace_root / "rollout-child.jsonl"),
            "mtime": 10,
            "title": "Child",
            "lineage_revision": ("unresolved", "parent-1"),
        }

        before = meter.source_revision_signature(source)
        after = meter.source_revision_signature({
            **source,
            "lineage_revision": ("resolved", "parent-1", "1", "2"),
        })

        self.assertNotEqual(before, after)


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

    def test_filters_orders_limits_and_sanitizes_card_rows(self):
        now = 10_000
        rows = [
            self.row(f"session-{index}", now - index * 10)
            for index in range(10)
        ]
        rows.append(self.row("too-old", now - meter.CURRENT_SESSION_MAX_AGE_S - 1))
        result = meter.current_session_summaries(rows, now=now)

        self.assertEqual(len(result), meter.CURRENT_SESSION_LIMIT)
        self.assertEqual(result[0]["id"], "session-0")
        self.assertEqual(result[-1]["id"], "session-7")
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
            logo = root / "assets" / "brand" / "logo-splunk-acc-rgb-w.png"
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
                    meter.dashboard_asset_path("/assets/brand/logo-splunk-acc-rgb-w.png"),
                    str(logo),
                )
                self.assertEqual(
                    meter.dashboard_asset_content_type("/assets/fonts/Tektur-Variable.ttf"),
                    "font/ttf",
                )
                self.assertEqual(
                    meter.dashboard_asset_content_type("/assets/brand/logo-splunk-acc-rgb-w.png"),
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


class SessionDeleteTests(unittest.TestCase):
    def test_moves_only_exact_discovered_jsonl_to_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "logs"
            trash_dir = Path(tmp) / "Trash"
            source_dir.mkdir()
            path = source_dir / "session-one.jsonl"
            path.write_text('{"type":"test"}\n')
            source = {
                "id": "session-one", "session": path.name, "path": str(path),
                "provider": "codex", "project": "/repo", "title": "One", "mtime": 1,
            }
            result = meter.trash_session_log("session-one", sources=[source], trash_dir=str(trash_dir))
            trashed = trash_dir / result["trash_name"]
            self.assertTrue(result["ok"])
            self.assertFalse(path.exists())
            self.assertTrue(trashed.exists())
            self.assertEqual(trashed.read_text(), '{"type":"test"}\n')
            self.assertNotIn(str(path), json.dumps(result))

    def test_rejects_alias_and_unknown_session_ids(self):
        source = {
            "id": "canonical", "session": "alias.jsonl", "path": "/tmp/alias.jsonl",
            "provider": "codex", "project": "/repo", "mtime": 1,
        }
        self.assertEqual(meter.trash_session_log("alias.jsonl", sources=[source])["error_code"], "not_found")
        self.assertEqual(meter.trash_session_log("missing", sources=[source])["error_code"], "not_found")

    def test_rejects_ambiguous_logical_id_without_deleting_either_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.jsonl"
            duplicate = root / "duplicate.jsonl"
            canonical.write_text('{}\n')
            duplicate.write_text('{}\n')
            source = {
                "id": "shared", "session": canonical.name,
                "path": str(canonical), "provider": "claude",
                "project": "/repo", "mtime": 2,
                "_aggregation_key": "claude:shared",
                "_aggregation_canonical": True,
                "_duplicate_paths": (str(canonical), str(duplicate)),
            }

            result = meter.trash_session_log(
                "shared", sources=[source], trash_dir=str(root / "Trash")
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "ambiguous_id")
            self.assertTrue(canonical.exists())
            self.assertTrue(duplicate.exists())

    def test_cursor_delete_moves_only_transcript_and_preserves_shared_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "projects" / "repo" / "agent-transcripts" / "cursor-one" / "cursor-one.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text('{"role":"user"}\n')
            database = root / "state.vscdb"
            database.write_bytes(b"shared cursor database")
            source = {
                "id": "cursor-one", "session": transcript.name, "path": str(transcript),
                "provider": "cursor", "project": "/repo", "title": "Cursor", "mtime": 1,
            }
            result = meter.trash_session_log(
                "cursor-one", sources=[source], trash_dir=str(root / "Trash")
            )
            self.assertTrue(result["ok"])
            self.assertFalse(transcript.exists())
            self.assertTrue(database.exists())
            self.assertEqual(database.read_bytes(), b"shared cursor database")

    def test_publish_after_delete_selects_newest_remaining_session(self):
        sources = [{"id": "older", "path": "/tmp/older.jsonl", "mtime": 1},
                   {"id": "newer", "path": "/tmp/newer.jsonl", "mtime": 2}]
        published = []
        with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                mock.patch.object(meter, "cross_session", return_value={"sessions": []}), \
                mock.patch.object(meter, "recompute", return_value={"source": {"id": "newer"}}), \
                mock.patch.object(meter, "publish", side_effect=published.append):
            selected = meter.publish_after_session_delete()
        self.assertEqual(selected, "newer")
        self.assertEqual(published[0]["source"]["id"], "newer")


class LiveCrossSessionRefreshTests(unittest.TestCase):
    def test_membership_probe_uses_two_seconds_with_a_four_second_fallback(self):
        self.assertFalse(meter.source_membership_probe_due(2.99, 1.0))
        self.assertTrue(meter.source_membership_probe_due(3.0, 1.0))
        self.assertFalse(meter.source_membership_fallback_due(4.99, 1.0))
        self.assertTrue(meter.source_membership_fallback_due(5.0, 1.0))

    def test_full_source_metadata_discovery_retains_the_ten_second_cadence(self):
        self.assertTrue(meter.source_discovery_refresh_due(False, 1.0, 0.0))
        self.assertFalse(meter.source_discovery_refresh_due(True, 10.99, 1.0))
        self.assertTrue(meter.source_discovery_refresh_due(True, 11.0, 1.0))

    def test_inventory_probe_ignores_active_growth_and_detects_a_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "project" / "active.jsonl"
            inactive = root / "project" / "inactive.jsonl"
            active.parent.mkdir()
            active.write_text("{}\n")
            inactive.write_text("{}\n")
            sources = [
                {"path": str(active), "provider": "codex"},
                {"path": str(inactive), "provider": "codex"},
            ]
            targets = meter.source_inventory_probe_targets(
                sources, roots=[str(root)], extra_files=[],
            )
            before = meter.source_inventory_probe_signature(
                targets, current_path=str(active),
            )

            with active.open("a") as handle:
                handle.write("{}\n")
            active_only = meter.source_inventory_probe_signature(
                targets, current_path=str(active),
            )
            with inactive.open("a") as handle:
                handle.write("{}\n")
            resumed = meter.source_inventory_probe_signature(
                targets, current_path=str(active),
            )

        self.assertEqual(active_only, before)
        self.assertNotEqual(resumed, before)

    def test_inventory_probe_detects_a_new_nested_session_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "project" / "existing.jsonl"
            existing.parent.mkdir()
            existing.write_text("{}\n")
            targets = meter.source_inventory_probe_targets(
                [{"path": str(existing), "provider": "claude"}],
                roots=[str(root)], extra_files=[],
            )
            before = meter.source_inventory_probe_signature(targets)

            new_session = root / "project" / "new.jsonl"
            new_session.write_text("{}\n")
            after = meter.source_inventory_probe_signature(targets)

        self.assertNotEqual(after, before)

    def test_known_source_activity_refreshes_without_adapter_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            active_path = Path(tmp) / "active.jsonl"
            inactive_path = Path(tmp) / "inactive.jsonl"
            active_path.write_text("{}\n")
            inactive_path.write_text("{}\n")
            os.utime(active_path, (20, 20))
            os.utime(inactive_path, (20, 20))
            sources = [
                {
                    "provider": "codex", "id": "active",
                    "path": str(active_path), "mtime": 10,
                },
                {
                    "provider": "codex", "id": "inactive",
                    "path": str(inactive_path), "mtime": 10,
                },
            ]

            refreshed = meter.refresh_known_source_activity(
                sources, current_path=str(active_path),
            )

        self.assertEqual(refreshed[0]["mtime"], 20)
        self.assertEqual(refreshed[1]["mtime"], 10)
        self.assertEqual(sources[0]["mtime"], 10)

    def test_unchanged_known_activity_reuses_the_inventory_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            active_path = Path(tmp) / "active.jsonl"
            active_path.write_text("{}\n")
            os.utime(active_path, (20, 20))
            sources = [{
                "provider": "codex", "id": "active",
                "path": str(active_path), "mtime": 20,
            }]

            refreshed = meter.refresh_known_source_activity(
                sources, current_path=str(active_path),
            )

        self.assertIs(refreshed, sources)

    def test_current_session_rebuilds_immediately_then_coalesces_changes(self):
        initial = (100, 20)
        changed = (101, 21)

        self.assertTrue(meter.current_session_refresh_due(
            initial, None, 10.0, 0.0,
        ))
        self.assertFalse(meter.current_session_refresh_due(
            changed, initial, 11.49, 10.0,
        ))
        self.assertTrue(meter.current_session_refresh_due(
            changed, initial, 11.5, 10.0,
        ))
        self.assertFalse(meter.current_session_refresh_due(
            initial, initial, 20.0, 10.0,
        ))

    def test_session_detail_reuses_cached_cross_session_snapshot(self):
        handler = object.__new__(meter.H)
        handler.path = "/session?id=session-1"
        sent = []
        handler._send = lambda body, *_args, **_kwargs: sent.append(json.loads(body))
        cached = {"current_sessions": []}
        rebuilder = mock.Mock(return_value={"current_sessions": []})
        state = {"source": {"id": "session-1"}, "timing": {}}
        source = {"id": "session-1", "path": "/logs/exact.jsonl"}
        summary = {
            "model_stats": [{"model": "exact-model", "executions": 2}],
            "usage_basis": "reported",
            "provenance": {"usage_basis": "reported"},
        }
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["data"] = cached
            with mock.patch.object(meter, "find_session", return_value=source), \
                    mock.patch.object(meter, "cached_session_state", return_value=state), \
                    mock.patch.object(meter, "session_summary", return_value=summary) as selected_summary, \
                    mock.patch.object(meter, "cross_session", rebuilder), \
                    mock.patch.object(meter, "attach_cross_session") as attach:
                handler.do_GET()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        rebuilder.assert_not_called()
        attach.assert_called_once_with(state, cached)
        selected_summary.assert_called_once_with(source)
        self.assertEqual(sent[0]["selected_stats"], summary)

    def test_full_sse_queue_keeps_subscriber_and_coalesces_to_latest_state(self):
        q_ = meter.queue.Queue(maxsize=2)
        q_.put_nowait("stale-one")
        q_.put_nowait("stale-two")
        self.assertTrue(meter.enqueue_latest(q_, "latest"))
        self.assertEqual(q_.qsize(), 1)
        self.assertEqual(q_.get_nowait(), "latest")

    def test_source_signature_changes_when_a_background_log_changes(self):
        before = meter.source_mtime_signature([
            {"path": "/logs/current.jsonl", "mtime": 20},
            {"path": "/logs/background.jsonl", "mtime": 10},
        ])
        after = meter.source_mtime_signature([
            {"path": "/logs/current.jsonl", "mtime": 20},
            {"path": "/logs/background.jsonl", "mtime": 11},
        ])
        self.assertNotEqual(before, after)

    def test_session_membership_bypasses_the_cross_session_refresh_throttle(self):
        before = meter.source_identity_signature([
            {"id": "current", "provider": "codex", "path": "/logs/current.jsonl"},
        ])
        after = meter.source_identity_signature([
            {"id": "current", "provider": "codex", "path": "/logs/current.jsonl"},
            {"id": "new", "provider": "claude", "path": "/logs/new.jsonl"},
        ])

        self.assertNotEqual(before, after)
        self.assertTrue(meter.cross_session_refresh_due(True, True, 10.1, 10.0))
        self.assertFalse(meter.cross_session_refresh_due(True, False, 10.1, 10.0))
        self.assertFalse(meter.cross_session_refresh_due(True, False, 24.9, 10.0))
        self.assertTrue(meter.cross_session_refresh_due(True, False, 25.0, 10.0))

    def test_publishing_inventory_evicts_only_disappeared_summary_paths(self):
        cache = {
            "/logs/live.jsonl": {"signature": (1, ""), "row": {"id": "live"}},
            "/logs/stale.jsonl": {"signature": (1, ""), "row": {"id": "stale"}},
        }
        saved_inventory = dict(meter._SOURCE_INVENTORY)
        try:
            with mock.patch.object(meter, "_summary_cache", cache):
                meter.publish_source_inventory([
                    {"path": "/logs/live.jsonl", "provider": "codex"},
                ])
        finally:
            meter._SOURCE_INVENTORY.clear()
            meter._SOURCE_INVENTORY.update(saved_inventory)

        self.assertEqual(list(cache), ["/logs/live.jsonl"])

    def test_partial_discovery_failure_preserves_missing_summary_paths(self):
        cache = {
            "/logs/live.jsonl": {"signature": (1, ""), "row": {"id": "live"}},
            "/logs/temporarily-missing.jsonl": {
                "signature": (1, ""), "row": {"id": "missing"},
            },
        }
        saved_inventory = dict(meter._SOURCE_INVENTORY)
        try:
            with mock.patch.object(meter, "_summary_cache", cache), \
                    mock.patch.object(meter, "_RUNTIME_DISCOVERY_FAILURES", (object(),)):
                meter.publish_source_inventory([
                    {"path": "/logs/live.jsonl", "provider": "codex"},
                ])
        finally:
            meter._SOURCE_INVENTORY.clear()
            meter._SOURCE_INVENTORY.update(saved_inventory)

        self.assertEqual(
            list(cache), ["/logs/live.jsonl", "/logs/temporarily-missing.jsonl"],
        )

    def test_cross_session_refresh_replaces_cached_snapshot_before_publish(self):
        saved_cache = dict(meter._xsess)
        published = []
        fresh = {"sessions": [{"id": "fresh"}], "capabilities": {}}
        state = {"provider": "codex", "tools": {}, "source": {"id": "current"}}
        try:
            meter._xsess["data"], meter._xsess["at"] = {"sessions": [{"id": "stale"}]}, 123
            result = meter.refresh_cross_session_state(
                state, builder=lambda: fresh, publisher=published.append,
            )
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertIs(result, fresh)
        self.assertEqual(published[0]["xsession"]["sessions"][0]["id"], "fresh")

    def test_logs_inventory_keeps_full_rows_beyond_state_preview(self):
        saved_cache = dict(meter._xsess)
        preview = [{"id": "recent"}]
        full = [{"id": "recent"}, {"id": "older-claude", "client": "claude_code"}]
        try:
            meter._xsess.update({
                "data": {"generated_at": 123, "sessions": preview, "total_sessions": 2},
                "at": meter.time.time(),
                "sessions": full,
            })
            result = meter.log_sessions_state()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual(result["sessions"], full)
        self.assertEqual(result["total_sessions"], 2)
        self.assertEqual(result["generated_at"], 123)

    def test_logs_inventory_returns_loading_without_rebuilding_cold_history(self):
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess.update({"data": None, "at": 0, "sessions": []})
            with mock.patch.object(
                meter, "cross_session",
                side_effect=AssertionError("logs request must not rebuild cold history"),
            ):
                result = meter.log_sessions_state()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertTrue(result["loading"])
        self.assertIsNone(result["total_sessions"])
        self.assertEqual(result["sessions"], [])

    def test_cross_session_separates_runtime_models_and_reported_alert_basis(self):
        def row(source):
            estimated = source["provider"] == "cursor"
            cost = 2.0 if estimated else 1.0
            tokens = 200 if estimated else 100
            return {
                **source, "turns": 1, "cost": cost, "tokens": tokens,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "models": ["gpt-5.6"], "token_estimate": estimated,
                "availability": meter.metric_availability(
                    source["provider"], cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"gpt-5.6": cost}, "_model_tok": {"gpt-5.6": tokens},
                "_day_cost": {"2026-07-20": cost}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [], "_wait_samples": [],
                "_tool_evidence": {}, "frustration": {}, "_frustration_events": [],
            }

        sources = [
            {"id": "codex", "path": "/tmp/codex", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/repo", "mtime": 2},
            {"id": "cursor", "path": "/tmp/cursor", "provider": "cursor",
             "runtime": "Cursor", "label": "Cursor", "project": "/repo", "mtime": 1},
        ]
        rows = {source["id"]: row(source) for source in sources}
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["data"], meter._xsess["at"] = None, 0
            with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.object(meter, "session_summary",
                                      side_effect=lambda source: rows[source["id"]]), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                result = meter.cross_session()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual({item["id"] for item in result["model_mix"]},
                         {"gpt-5.6::Codex", "gpt-5.6::Cursor"})
        self.assertEqual(result["usage_basis"], "mixed")
        self.assertEqual(result["reported_cost"], 1.0)
        self.assertEqual(result["estimated_cost"], 2.0)
        self.assertEqual(result["trend"][0]["reported_cost"], 1.0)
        self.assertEqual(result["trend"][0]["anomaly_basis"], "reported_only")

    def test_cross_session_counts_adapter_owned_duplicate_identity_once(self):
        def row(source):
            return {
                **source, "turns": 1, "cost": 1.0, "tokens": 100,
                "input_tokens": 90, "output_tokens": 10,
                "models": ["claude-test"], "token_estimate": False,
                "availability": meter.metric_availability(
                    "claude", cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"claude-test": 1.0},
                "_model_tok": {"claude-test": 100},
                "_day_cost": {"2026-08-18": 1.0}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [],
                "_wait_samples": [], "_tool_evidence": {},
                "frustration": {}, "_frustration_events": [],
            }

        canonical_path = "/tmp/claude-canonical.jsonl"
        duplicate_path = "/tmp/claude-duplicate.jsonl"
        sources = [
            {
                "id": "shared", "path": canonical_path, "provider": "claude",
                "runtime": "Claude", "label": "Claude Desktop",
                "project": "/repo", "mtime": 2,
                "_aggregation_key": "claude:shared",
                "_aggregation_canonical": True,
            },
            {
                "id": "shared", "path": duplicate_path, "provider": "claude",
                "runtime": "Claude", "label": "Claude Desktop",
                "project": "/repo", "mtime": 1,
                "_aggregation_key": "claude:shared",
            },
        ]
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["data"], meter._xsess["at"] = None, 0
            with mock.patch.object(meter, "session_summary", side_effect=row), \
                    mock.patch.object(meter, "capability_inventory", return_value={}), \
                    mock.patch.object(
                        meter, "aggregate_language_signals",
                        wraps=meter.aggregate_language_signals,
                    ) as language_signals:
                result = meter.cross_session(sources=sources)
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(result["total_sessions"], 1)
        self.assertEqual(result["total_cost"], 1.0)
        self.assertEqual(result["total_tokens"], 100)
        self.assertNotIn(duplicate_path, json.dumps(result))
        self.assertEqual(language_signals.call_count, 1)


class DashboardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter.__file__).with_name("page.html").read_text()
        cls.session_effects = (
            Path(meter.__file__).with_name("assets") / "session-effects.js"
        ).read_text()

    def test_current_detail_excludes_all_secondary_panels(self):
        self.assertNotIn("data-panel=efficiency", self.page)
        self.assertNotIn("id=panel-efficiency", self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertNotIn("efficiency:'summary'", self.page)

    def test_dashboard_uses_splunk_favicon_and_bundled_wordmark(self):
        self.assertIn('rel=icon type="image/svg+xml"', self.page)
        self.assertIn("data:image/svg+xml", self.page)
        self.assertIn("M47.03 36.16v-7.58L10 10", self.page)
        self.assertIn('class=logo role=img aria-label=Splunk', self.page)
        self.assertIn('/assets/brand/logo-splunk-acc-rgb-w.png', self.page)
        self.assertIn('name=theme-color content="#07090c"', self.page)

    def test_current_header_keeps_one_line_session_start_message_visible(self):
        self.assertIn('class="card previewStartStrip"', self.page)
        self.assertIn("id=preview-start", self.page)
        self.assertIn("function sessionStartMessage(s)", self.page)
        self.assertIn("$('preview-start').textContent=startMessage", self.page)
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            "<div class=view id=view-models>", 1
        )[0]
        self.assertLess(current.index("id=preview-start"), current.index("id=preview-run-chart-slot"))
        self.assertIn("text-overflow:ellipsis;white-space:nowrap", self.page)

    def test_current_output_card_shows_trace_backed_output_speed(self):
        for marker in ("id=preview-speed", "s.throughput||{}", "speedFmt(throughput.output_tps)",
                       "tool-free", "end-to-end", "reasoning and thinking output",
                       "external tool-result tokens"):
            self.assertIn(marker, self.page)
        self.assertIn(".previewSpeed .v{color:var(--accent)", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_unavailable_cost_value_explains_why_it_is_not_zero(self):
        match = re.search(
            r"const COST_UNAVAILABLE_TIP=.*?^function setCostValue\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs an unavailable-cost helper")
        script = """
const element={classList:{values:new Set(),toggle(name,on){on?this.values.add(name):this.values.delete(name);}},attributes:{},setAttribute(name,value){this.attributes[name]=String(value);},removeAttribute(name){delete this.attributes[name];}};
const $=id=>element;
""" + match.group(0) + """
setCostValue('cost','$1.25',true);
const available={text:element.textContent,classes:[...element.classList.values],attributes:{...element.attributes}};
setCostValue('cost','$1.25',false);
console.log(JSON.stringify({available,unavailable:{text:element.textContent,classes:[...element.classList.values],attributes:{...element.attributes}}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["available"], {
            "text": "$1.25", "classes": [], "attributes": {},
        })
        self.assertEqual(rendered["unavailable"]["text"], "--")
        self.assertEqual(rendered["unavailable"]["classes"], ["fieldtip"])
        self.assertIn("not $0", rendered["unavailable"]["attributes"]["data-tip"])
        self.assertIn("locally stored usage", rendered["unavailable"]["attributes"]["aria-description"])

    def test_cross_session_cost_placeholders_share_the_unavailable_cost_explanation(self):
        for renderer, helper in (
            ("function logRowContent(s){", "costValueHtml("),
            ("function renderAllSessionStats(sessions){", "costValueHtml("),
            ("function renderCurrentDaySummary(xs){", "setCostValue("),
            ("function renderSpend(xs){", "setCostValue("),
            ("function renderCurrentSessions(state=LATEST){", "costValueHtml("),
        ):
            section = self.page.split(renderer, 1)[1].split("\nfunction ", 1)[0]
            self.assertIn(helper, section, renderer)

    def test_current_session_cost_tooltip_does_not_add_a_nested_tab_stop(self):
        session_cards = self.page.split("function renderCurrentSessions(state=LATEST){", 1)[1].split(
            "const currentSessionGrid=$('current-session-grid');", 1
        )[0]
        self.assertIn("costValueHtml(money(row.cost)+(estimate?' est':''),costAvailable,false)", session_cards)
        self.assertIn("const costTipAttrs=costAvailable?'':costUnavailableAttrs();", session_cards)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_active_session_surfaces_prefer_live_throughput(self):
        match = re.search(
            r"function sessionDisplayThroughput\(session\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one shared speed selector")
        script = match.group(0) + """
const completed = {throughput:{available:true,output_tps:85.4},live_throughput:{available:false}};
const active = {throughput:{available:true,output_tps:85.4},live_throughput:{available:true,output_tps:41.2}};
console.log(JSON.stringify({
  completed: sessionDisplayThroughput(completed).output_tps,
  active: sessionDisplayThroughput(active).output_tps,
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "completed": 85.4,
            "active": 41.2,
        })
        self.assertGreaterEqual(self.page.count("sessionDisplayThroughput(s)"), 2)
        self.assertIn("sessionDisplayThroughput(row)", self.page)

    def test_session_detail_is_one_run_surface_without_subtabs(self):
        for marker in (
            "id=current-tabs", "data-current-panel=", "id=preview-surface-activity",
            "id=preview-surface-tools", "id=preview-surface-insights",
            "id=preview-surface-alerts", "id=panel-activity", "id=panel-tools",
            "id=panel-insights", "id=panel-alerts", "function renderTrace(",
            "function renderTools(",
        ):
            self.assertNotIn(marker, self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];", self.page)
        for removed_route in ("activity", "tools", "alerts"):
            self.assertIn(f"{removed_route}:'summary'", self.page)
        self.assertNotIn("insights:'summary'", self.page)
        self.assertNotIn("efficiency:'summary'", self.page)

    def test_codex_session_detail_links_to_the_desktop_thread(self):
        for marker in (
            'id=session-desktop-link',
            'aria-label="Open this session in the Codex desktop app"',
            'function codexDesktopSessionHref(session)',
            "provider==='codex'&&id",
            'codex://threads/${encodeURIComponent(id)}',
            'function renderCodexDesktopSessionLink(session)',
            'link.hidden=!href',
            "link.removeAttribute('href')",
            'renderCodexDesktopSessionLink(s);',
        ):
            self.assertIn(marker, self.page)

    def test_browser_operational_alerts_are_budget_only(self):
        self.assertNotIn("function isNotifiableInsight(i)", self.page)
        self.assertNotIn("fireNotification('Token insight'", self.page)
        self.assertNotIn("id=spike", self.page)
        self.assertNotIn("tm_spike", self.page)
        self.assertNotIn("s.last_turn_cost>spike", self.page)
        self.assertIn(
            "fireNotification('Token Meter session budget'",
            self.page,
        )
        self.assertIn(
            "fireNotification(isExceeded?'Token Meter budget exceeded':'Token Meter monthly budget'",
            self.page,
        )
        self.assertIn("n.title!=='Token insight'", self.page)
        self.assertIn(
            "String(n.body||'').startsWith('Last execution cost ')",
            self.page,
        )
        settings = self.page[self.page.index("id=view-settings"):]
        rail = self.page[self.page.index("<div class=top aria-label="):self.page.index("<div id=session-depot")]
        for marker in (
            "class=budgetBrowserAlerts", "class=budgetBrowserAlertsCopy",
            "Browser delivery",
        ):
            self.assertNotIn(marker, settings)
        for marker in (
            "id=panel-alerts", "id=notify role=switch", "id=notify-test",
            "id=notify-log", 'aria-label="Toggle browser budget alerts"',
        ):
            self.assertNotIn(marker, self.page)
        self.assertNotIn("id=notify", rail)

    def test_budget_alert_controls_share_the_set_budgets_header(self):
        config = self.page[
            self.page.index('id=budget-config'):
            self.page.index('class="budgetFormGroup budgetRuntimeFields"')
        ]
        header = config[
            config.index("class=budgetConfigHead"):
            config.index("class=budgetFormGroup")
        ]

        self.assertIn("Set budgets</h2>", header)
        self.assertIn("class=budgetAlertInline", header)
        self.assertIn("class=budgetThresholds", header)
        self.assertIn("id=budget-input-threshold-early", header)
        self.assertIn("id=budget-input-threshold-mid", header)
        self.assertIn("id=budget-input-threshold-full", header)
        self.assertIn("class=budgetNotifySwitch", header)
        self.assertIn("class=sw aria-hidden=true", header)
        self.assertIn("id=budget-input-notifications", header)
        self.assertNotIn("Default for sessions without a saved cap.", config)
        self.assertNotIn("Browser delivery", config)

    def test_monthly_budget_alerts_recover_missed_exceeded_state(self):
        for marker in (
            "function budgetExceededAlertState()",
            "tm_monthly_budget_exceeded_alerts",
            "previous=state[status.month]",
            "Array.isArray(previous)?previous",
            "status.state==='over'||Number(status.percent||0)>=1",
            "exceededState=budgetExceededAlertState()",
            "monthExceeded.inApp===true",
            "monthExceeded.browser===true",
            "deliverMissedBrowserAlert",
            "notifyOn&&canNotify&&Notification.permission==='granted'",
            "Token Meter budget exceeded",
            "requireInteraction:isExceeded",
            "pushAppNotice(title,body",
            "state[status.month]=[...new Set([...seen,...crossed])]",
            "inApp:inAppNotified||alertExceeded",
            "browser:browserNotified||deliverMissedBrowserAlert",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            "status.settings?.native_notifications===false",
            self.page,
        )
        self.assertNotIn(
            "if(!Array.isArray(seen)){state[status.month]=crossed",
            self.page,
        )

    def test_old_current_summary_is_only_a_hidden_renderer_depot(self):
        for marker in (
            "id=session-depot hidden", "id=usage-details", "tm_usage_details_open",
            "id=cost", "id=input-tok", "id=output-tok", "id=output-tps",
            "id=ov-duration", "id=ov-context",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("class=view id=session-depot", self.page)
        self.assertNotIn("class=\"view on\" id=session-depot", self.page)

    def test_current_summary_keeps_session_budget_slider(self):
        summary = self.page[
            self.page.index("id=panel-summary"):
            self.page.index("<div class=foot>")
        ]
        for marker in (
            "id=session-budget-control", "id=budget-slider type=range",
            "id=budget type=number", "id=session-budget-spend",
            "function syncSessionBudgetControls", "tm_session_budgets",
            "$('budget-slider').addEventListener('input'",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("id=budget-slider type=range", summary)
        self.assertIn("id=budget type=number", summary)
        self.assertNotIn("id=panel-alerts", self.page)
        self.assertNotIn("Only budget crossings create alerts.", self.page)
        self.assertNotIn(
            "Set a live-run cap without changing the machine-wide monthly budget.",
            summary,
        )

    def test_execution_chart_marks_a_single_observed_value(self):
        chart = self.page[
            self.page.index("function drawIO(series)"):
            self.page.index("function renderOverview(s)")
        ]
        self.assertIn("const validPoints=[]", chart)
        self.assertIn("validPoints.length===1", chart)
        self.assertIn("const singlePoint=", chart)

    def test_settings_default_session_budget_preserves_saved_session_caps(self):
        for marker in (
            "id=budget-input-session-default",
            "Default session budget",
            "function sessionBudgetForSession(s)",
            "defaultSessionBudget=Number(settings.default_session_budget||10)",
            "Number.isFinite(saved)&&saved>0?saved:defaultSessionBudget",
            "default_session_budget:budgetNumber('budget-input-session-default')",
        ):
            self.assertIn(marker, self.page)

    def test_promoted_current_is_dense_complete_and_settings_stay_dedicated(self):
        for marker in (
            "id=tab-session", 'class="view on" id=view-session',
            "id=preview-run-chart-slot",
            "id=preview-run-budget-slot", "id=preview-token-split-slot",
            "id=preview-surface-run",
            "id=session-token-split-home", "id=session-token-split-module",
            "class=\"card previewStartStrip\"", "class=settingsPageLayout",
            "class=\"card settingsMap\"", "data-settings-target=agent-access",
            "function restoreCurrentModules()", "function showCurrentPanel(panel)",
            "function renderCurrentRun(s)",
            "if(h==='preview-settings')", "setHashRoute('settings',{replace:true,apply:false})",
            "const legacyCurrentRoutes={preview:'summary','preview-run':'summary'",
        ):
            self.assertIn(marker, self.page)
        for metric_id in (
            "preview-speed", "preview-wait",
            "preview-context", "preview-executions", "preview-tools",
            "preview-tool-results", "preview-cache-rate", "preview-cache-saved",
            "preview-burn", "preview-cost-task", "preview-started-at",
            "preview-last-at",
        ):
            self.assertIn(f"id={metric_id}", self.page)
        for removed_id in (
            "preview-input", "preview-output", "preview-thinking", "preview-avg-cost",
        ):
            self.assertNotIn(f"id={removed_id}", self.page)
        for unique_id in (
            "iochart", "sembar", "session-token-split-module", "budget", "agent-access",
            "frustration-settings", "model-pricing-settings", "update-settings",
        ):
            self.assertEqual(
                len(re.findall(rf"\bid={re.escape(unique_id)}(?:\s|>)", self.page)),
                1,
                f"{unique_id} must be moved, not cloned",
            )
        self.assertNotIn("id=tab-preview", self.page)
        self.assertNotIn("id=view-preview", self.page)
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            "<div class=view id=view-models>", 1
        )[0]
        self.assertNotIn("Settings map", current)
        self.assertNotIn("id=agent-access", current)
        self.assertNotIn("id=frustration-settings", current)
        self.assertNotIn("id=model-pricing-settings", current)
        self.assertNotIn("What needs attention", current)
        self.assertNotIn("Open original Current", current)
        self.assertNotIn("Experimental", current)
        self.assertNotIn("Current preview", current)
        self.assertNotIn("previewKpi fieldtip", current)
        self.assertLess(current.index("id=preview-start"), current.index("id=preview-run-chart-slot"))
        self.assertLess(
            current.index("id=preview-run-chart-slot"),
            current.index("id=preview-token-split-slot"),
        )
        self.assertIn("previewSpeed", current)
        self.assertIn("text-overflow:ellipsis;white-space:nowrap", self.page)
        self.assertIn(
            "if(t!=='session')restoreCurrentModules();",
            self.page,
        )
        self.assertIn(
            "mountCurrentModule('preview-run-chart-slot','session-chart-module')",
            self.page,
        )
        self.assertIn(
            "mountCurrentModule('preview-token-split-slot','session-token-split-module')",
            self.page,
        )
        self.assertIn(
            "['session-token-split-home','session-token-split-module']",
            self.page,
        )
        for marker in (
            "id=current-tabs", "data-current-panel=", "id=preview-activity-slot",
            "id=preview-tools-slot", "id=preview-efficiency-slot",
            "id=preview-alerts-slot", "id=preview-surface-activity",
            "id=preview-surface-tools", "id=preview-surface-insights",
            "id=preview-surface-alerts", "id=session-activity-home",
            "id=session-tools-home", "id=session-insights-home",
            "id=session-alerts-home", "id=panel-activity", "id=panel-tools",
            "id=panel-insights", "id=panel-alerts",
            "mountCurrentModule(`preview-${panel}-slot`,`panel-${panel}`)",
            "const button=event.target.closest('[data-current-panel]');",
        ):
            self.assertNotIn(marker, self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];", self.page)
        self.assertIn("const PANEL_KEYS=['summary'];", self.page)
        self.assertNotIn("mountCurrentModule('preview-settings", self.page)

    def test_model_stats_is_a_first_class_top_level_route(self):
        for marker in ("id=tab-models", "id=view-models", "id=m-speed", "id=m-chart",
                       "id=m-table", "renderModelStats", "aggregateModelDays"):
            self.assertIn(marker, self.page)
        self.assertRegex(self.page, r"id=tab-models[^>]*>.*?<span class=tabLabel>Models</span>")
        self.assertIn("if(routedSessionId()&&history.pushState)", self.page)
        self.assertIn("$('tab-models').onclick=()=>openTopLevelRoute('models')", self.page)
        self.assertIn("if(h==='models'||h==='frustration')", self.page)
        self.assertLess(self.page.index("id=tab-session"), self.page.index("id=tab-models"))
        self.assertNotIn("Timing evidence", self.page)
        self.assertIn("Observed output pace is a secondary diagnostic.", self.page)
        self.assertIn("colspan=8", self.page)

    def test_selected_session_token_split_fills_the_chart_column_gap(self):
        run_grid = self.page.split('<div class=previewRunGrid>', 1)[1].split(
            '<aside class=previewDecision>', 1
        )[0]
        self.assertIn('class=previewChartColumn', run_grid)
        self.assertIn('id=preview-run-chart-slot', run_grid)
        self.assertIn('id=preview-token-split-slot', run_grid)
        self.assertLess(
            run_grid.index('id=preview-run-chart-slot'),
            run_grid.index('id=preview-token-split-slot'),
        )
        self.assertIn(
            ".previewChartColumn{display:grid;align-content:start;gap:12px;min-width:0}",
            self.page,
        )

    def test_efficiency_is_the_only_top_level_name_and_route(self):
        for marker in (
            "id=tab-efficiency", "id=view-efficiency", "data-page-signal=efficiency",
            "id=e-project", "id=e-model-picker", "id=e-model-options", "id=e-model-summary",
            "id=e-range", "id=e-output-dollar", "id=e-reasoning-ratio",
            "id=e-output-dollar-chart", "id=e-reasoning-chart",
            "id=e-context-load", "id=e-output-execution", "id=e-model-table",
            "data-efficiency-sort=cost", "data-efficiency-sort=output_per_dollar",
            "function renderEfficiency", "function efficiencyMetrics",
            "function renderEfficiencyChart", "function sortEfficiencyRows",
            "y2=\"${pad.top}\"", "r=\"3.5\"",
            "$('tab-efficiency').onclick=()=>openTopLevelRoute('efficiency')",
            "if(h==='efficiency')", "renderActiveEfficiency()",
            "tm_efficiency_range",
        ):
            self.assertIn(marker, self.page)
        self.assertRegex(
            self.page,
            r"id=tab-efficiency[^>]*>.*?<span class=tabLabel>Efficiency</span>",
        )
        nav_ids = [
            "tab-session", "tab-daily", "tab-models", "tab-efficiency",
            "tab-learn", "tab-capabilities", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in nav_ids]
        self.assertEqual(positions, sorted(positions))
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1
        )[0]
        self.assertIn("Output / $", efficiency)
        self.assertIn("Reasoning ratio", efficiency)
        self.assertIn("Mechanical efficiency", efficiency)
        self.assertIn("Spend", efficiency)
        self.assertNotIn("Cache leverage", efficiency)
        self.assertNotIn("Retry waste", efficiency)
        self.assertNotIn("Efficiency score", efficiency)
        self.assertNotIn("recommendation", efficiency.lower())
        for stale in (
            "id=tab-insights", "id=view-insights", "route:'insights'",
            "tm_insights_range", "function renderInsights",
        ):
            self.assertNotIn(stale, self.page)

    def test_efficiency_table_keeps_model_app_and_effort_as_columns(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1
        )[0]
        for header in (">Model<", ">App<", ">Reasoning effort<"):
            self.assertIn(header, efficiency)
        self.assertIn(
            "<td><div class=modelName title=\"${esc(row.model)}\">${esc(row.model)}</div></td><td>${esc(row.runtime)}",
            self.page,
        )

    def test_models_table_labels_effort_qualified_runtime_rows(self):
        self.assertIn(
            "querySelectorAll('.modelRuntime').forEach((cell,index)=>cell.append(document.createTextNode(` · ${modelEffortLabel(tableRows[index])}`)))",
            self.page,
        )

    def test_selected_session_has_a_compact_two_metric_efficiency_module(self):
        summary = self.page.split('<div class="session-panel on" id=panel-summary>', 1)[1].split(
            '<details class=usageDetails', 1
        )[0]
        for marker in (
            "id=session-efficiency", "id=session-output-dollar",
            "id=session-output-dollar-note", "id=session-reasoning-ratio",
            "id=session-reasoning-ratio-note", "function renderSessionEfficiency",
            "function sessionEfficiencySource", "aggregateModelDays(source.model_stats||[])",
            "id=preview-run-efficiency-slot",
            "mountCurrentModule('preview-run-efficiency-slot','session-efficiency')",
            "['session-efficiency-home','session-efficiency']",
        ):
            self.assertIn(marker, self.page)
        self.assertLess(summary.index("id=session-efficiency"), summary.index("id=session-budget-home"))
        session_efficiency = summary.split("id=session-efficiency", 1)[1].split(
            "id=session-budget-home", 1
        )[0]
        self.assertEqual(session_efficiency.count("class=sessionEfficiencyMetric"), 2)
        self.assertIn("Output / $", session_efficiency)
        self.assertIn("Reasoning ratio", session_efficiency)
        self.assertNotIn("<svg", session_efficiency)
        self.assertNotIn("By model", session_efficiency)

    def test_selected_session_efficiency_is_inside_the_run_status_card(self):
        run_surface = self.page.split('id=preview-surface-run', 1)[1].split(
            '<div class=previewKpis', 1
        )[0]
        status_card = run_surface.split('id=preview-status-card', 1)[1].split(
            '</section>', 1
        )[0]
        self.assertIn('id=preview-run-efficiency-slot', status_card)
        self.assertLess(
            status_card.index('id=preview-run-efficiency-slot'),
            status_card.index('class=previewPressure'),
        )
        self.assertIn(
            ".previewStatus .previewRunEfficiencySlot .sessionEfficiency{grid-template-columns:repeat(2",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_uses_the_matching_cross_session_summary(self):
        match = re.search(
            r"function sessionEfficiencySource\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = r"""
const stateSessionId=state=>state?.source?.id||null;
""" + match.group(0) + r"""
const matched={source:{id:'selected',path:'/sessions/right.jsonl'},model_stats:[],xsession:{sessions:[
  {id:'other',path:'/sessions/other.jsonl',model_stats:[{model:'wrong-id'}]},
  {id:'selected',path:'/sessions/wrong.jsonl',model_stats:[{model:'wrong-trace'}]},
  {id:'selected',path:'/sessions/right.jsonl',model_stats:[{model:'right'}],usage_basis:'reported'},
]}};
const direct={source:{id:'direct'},model_stats:[{model:'fallback'}],xsession:{sessions:[]}};
const stale={source:{id:'selected',path:'/sessions/missing.jsonl'},model_stats:[{model:'fail-closed'}],xsession:{sessions:[
  {id:'selected',path:'/sessions/wrong.jsonl',model_stats:[{model:'wrong-trace'}]},
]}};
const attached={source:{id:'selected',path:'/sessions/right.jsonl'},selected_stats:{model_stats:[{model:'attached-exact'}],usage_basis:'reported'},xsession:{sessions:[
  {id:'selected',path:'/sessions/right.jsonl',model_stats:[{model:'stale-cross'}]},
]}};
console.log(JSON.stringify({matched:sessionEfficiencySource(matched),direct:sessionEfficiencySource(direct),stale:sessionEfficiencySource(stale),attached:sessionEfficiencySource(attached)}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["matched"]["model_stats"][0]["model"], "right")
        self.assertEqual(payload["matched"]["usage_basis"], "reported")
        self.assertEqual(payload["direct"]["model_stats"][0]["model"], "fallback")
        self.assertEqual(payload["stale"]["model_stats"][0]["model"], "fail-closed")
        self.assertEqual(payload["attached"]["model_stats"][0]["model"], "attached-exact")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_recovers_explicit_reasoning_coverage_from_summary_counts(self):
        match = re.search(
            r"function normalizeSessionEfficiencyAggregate\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  exact:normalizeSessionEfficiencyAggregate({
    executions:10,reasoning_executions:8,reasoning_tokens:30,
    reasoning_output_tokens:80,availability:{reasoning_tokens:false},
  }),
  structural:normalizeSessionEfficiencyAggregate({
    executions:10,reasoning_executions:0,reasoning_tokens:0,
    reasoning_output_tokens:0,thinking_executions:4,availability:{},
  }),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["exact"]["availability"]["reasoning_tokens"])
        self.assertFalse(payload["structural"]["availability"]["reasoning_tokens"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_metrics_are_bounded_and_keep_unavailable_evidence_null(self):
        match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one shared efficiency formula")
        script = match.group(0) + r"""
const complete = efficiencyMetrics({
  input_tokens:1000, output_tokens:200, cost:2, executions:2,
  token_covered_executions:2, io_covered_executions:2,
  cost_covered_output_tokens:200, cost_covered_cost:2,
  cache_read_tokens:500, reasoning_tokens:50, reasoning_output_tokens:100,
  reasoning_executions:1, reasoning_unavailable_executions:1,
  attempts:4, retries:1, failed_attempts:1, attempt_samples:1,
  availability:{tokens:true,cost:true,cache:true,reasoning_tokens:true,attempts:true},
});
const missing = efficiencyMetrics({
  input_tokens:100, output_tokens:20, cost:0, executions:0,
  availability:{tokens:true,cost:false,cache:false,reasoning_tokens:false,attempts:false},
});
const partial = efficiencyMetrics({
  input_tokens:110, output_tokens:110, cost:.1, executions:3,
  token_covered_executions:2, io_covered_executions:1,
  cost_covered_output_tokens:10, cost_covered_cost:.1,
  coverage:{tokens:{complete:false}},
  availability:{tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const coveredZero = efficiencyMetrics({
  input_tokens:10, output_tokens:0, cost:1, executions:1,
  token_covered_executions:1, io_covered_executions:1,
  cost_covered_executions:1, cost_covered_output_tokens:0,
  cost_covered_cost:1,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const inputOnly = efficiencyMetrics({
  input_tokens:10, output_tokens:0, cost:1, executions:1,
  token_covered_executions:0, io_covered_executions:0,
  cost_covered_executions:0,
  cost_covered_output_tokens:0,
  availability:{tokens:true,input_tokens:true,output_tokens:false,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
const zeroInput = efficiencyMetrics({
  input_tokens:0, output_tokens:10, cost:1, executions:1,
  token_covered_executions:1, io_covered_executions:1,
  cost_covered_executions:1, cost_covered_output_tokens:10,
  cost_covered_cost:1,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false,reasoning_tokens:false,attempts:false},
});
console.log(JSON.stringify({complete,missing,partial,coveredZero,inputOnly,zeroInput}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["complete"], {
            "output_per_dollar": 100,
            "reasoning_ratio": 0.5,
            "context_load": 5,
            "cache_leverage": 0.5,
            "retry_waste": 0.25,
            "failure_rate": 0.25,
            "output_per_execution": 100,
        })
        self.assertEqual(payload["missing"], {
            "output_per_dollar": None,
            "reasoning_ratio": None,
            "context_load": 5,
            "cache_leverage": None,
            "retry_waste": None,
            "failure_rate": None,
            "output_per_execution": None,
        })
        self.assertEqual(payload["partial"]["output_per_dollar"], 100)
        self.assertEqual(payload["partial"]["output_per_execution"], 55)
        self.assertIsNone(payload["partial"]["context_load"])
        self.assertEqual(payload["coveredZero"]["output_per_dollar"], 0)
        self.assertEqual(payload["coveredZero"]["output_per_execution"], 0)
        self.assertIsNone(payload["inputOnly"]["output_per_dollar"])
        self.assertIsNone(payload["inputOnly"]["output_per_execution"])
        self.assertEqual(payload["zeroInput"]["context_load"], 0)

    def test_efficiency_uses_one_range_for_overall_and_per_model_statistics(self):
        for marker in (
            "const EFFICIENCY_RANGES=['today','yesterday','7','30','90','all']",
            "tm_efficiency_range", "tm_efficiency_project", "efficiencyRangeWindow", "efficiencyModelRows",
            "aggregateModelDays(efficiencyModelRows", "efficiencyMetrics(overall)",
            "efficiencyMetrics(row.window)", "efficiencyTrendDays",
            "Thinking / reasoning coverage", "thinking_executions",
            "token ratio unavailable", "e-reasoning-chart",
            "includes local estimates", "token_covered_executions",
            "Compare similar work · no quality judgment.",
        ):
            self.assertIn(marker, self.page)

        self.assertIn(
            "metric.output_per_dollar===null?'unavailable':data.coverage?.cost?.complete?'covered':'partial'",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_filters_keep_runtime_identities_distinct(self):
        match = re.search(
            r"function filterEfficiencyModels\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs to filter by model-runtime ID")
        script = match.group(0) + r"""
const rows=[
  {id:'gpt-5.6::Codex',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Cursor',model:'gpt-5.6',runtime:'Cursor'},
  {id:'claude-opus::Claude Code',model:'claude-opus',runtime:'Claude Code'},
];
console.log(JSON.stringify({
  all:filterEfficiencyModels(rows,[]).map(row=>row.id),
  selected:filterEfficiencyModels(rows,['gpt-5.6::Cursor']).map(row=>row.id),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "all": ["gpt-5.6::Codex", "gpt-5.6::Cursor", "claude-opus::Claude Code"],
            "selected": ["gpt-5.6::Cursor"],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_legacy_model_runtime_filter_expands_to_effort_rows(self):
        match = re.search(
            r"function migrateModelRuntimeFilters\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Legacy model filters need effort-row migration")
        script = match.group(0) + r"""
const rows=[
  {id:'gpt-5.6::Codex::high',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Codex::xhigh',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Codex::unavailable',model:'gpt-5.6',runtime:'Codex'},
  {id:'gpt-5.6::Cursor::unavailable',model:'gpt-5.6',runtime:'Cursor'},
];
console.log(JSON.stringify({
  legacy:migrateModelRuntimeFilters(['gpt-5.6::Codex'],rows),
  unavailable:migrateModelRuntimeFilters(['gpt-5.6::Codex::unavailable'],rows),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "legacy": [
                "gpt-5.6::Codex::high", "gpt-5.6::Codex::xhigh",
                "gpt-5.6::Codex::unavailable",
            ],
            "unavailable": ["gpt-5.6::Codex::unavailable"],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_toggle_waits_for_the_selected_project_scope(self):
        match = re.search(
            r"function rerenderActiveEfficiency\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs to avoid rendering stale project stats")
        script = match.group(0) + r"""
let activeEfficiencyStats={models:['all-projects']};
let activeEfficiencyScope='';
let efficiencyProject='project-b';
let rendered=0,requested=0;
function renderEfficiency(){rendered++;}
function renderActiveEfficiency(){requested++;}
rerenderActiveEfficiency();
activeEfficiencyScope='project-b';
rerenderActiveEfficiency();
console.log(JSON.stringify({rendered,requested}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"rendered": 1, "requested": 1})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_effort_label_keeps_known_and_unavailable_budgets_separate(self):
        match = re.search(r"function modelEffortLabel\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Model rows need an explicit effort-group label")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  known:modelEffortLabel({reasoning_effort:'xhigh'}),
  opus:modelEffortLabel({thinking_executions:2}),
  missing:modelEffortLabel({}),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "known": "xhigh",
            "opus": "thinking unavailable",
            "missing": "unavailable",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_delta_compares_latest_two_covered_days(self):
        match = re.search(r"function efficiencyTrendDelta\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Efficiency charts need a comparable-day delta helper")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  up:efficiencyTrendDelta([
    {metrics:{output_per_dollar:100}}, {metrics:{output_per_dollar:null}},
    {metrics:{output_per_dollar:125}},
  ],'output_per_dollar'),
  down:efficiencyTrendDelta([
    {metrics:{reasoning_ratio:.5}}, {metrics:{reasoning_ratio:.4}},
  ],'reasoning_ratio'),
  unavailable:efficiencyTrendDelta([
    {metrics:{output_per_dollar:0}}, {metrics:{output_per_dollar:25}},
  ],'output_per_dollar'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "up": {"text": "↑ 25.0% vs prior covered day", "direction": "up"},
            "down": {"text": "↓ 20.0% vs prior covered day", "direction": "down"},
            "unavailable": {"text": "No comparable prior day", "direction": ""},
        })
        self.assertIn('id=e-output-dollar-change', self.page)
        self.assertIn('id=e-reasoning-change', self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_charts_smooth_daily_segments_and_default_to_seven_days(self):
        match = re.search(
            r"function efficiencyChartSmoothPath\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(
            match, "Efficiency charts need a smooth path helper for daily segments",
        )
        script = match.group(0) + r"""
const rows=[
  {index:0,value:50}, {index:1,value:30}, {index:2,value:10},
];
console.log(JSON.stringify({
  smooth:efficiencyChartSmoothPath(rows,row=>row.index*10,value=>value),
  pair:efficiencyChartSmoothPath(rows.slice(0,2),row=>row.index*10,value=>value),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "smooth": "M0.0 50.0 C3.3 43.3 6.7 36.7 10.0 30.0 C13.3 23.3 16.7 16.7 20.0 10.0",
            "pair": "M0.0 50.0 L10.0 30.0",
        })
        self.assertIn("let efficiencyRange=localStorage.getItem('tm_efficiency_range')||'7';", self.page)
        self.assertIn("if(!EFFICIENCY_RANGES.includes(efficiencyRange))efficiencyRange='7';", self.page)
        self.assertIn('<option value=7 selected>Last 7 days</option>', self.page)
        self.assertIn(
            "renderEfficiencyTrendDelta('e-reasoning-change',reasoningDelta,'down',!comparison);",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_period_comparison_matches_today_yesterday_and_range_windows(self):
        date_key = re.search(r"function dateKeyAgo\(.*\n", self.page)
        self.assertIsNotNone(date_key, "Efficiency windows need date-key arithmetic")
        functions = ["function localDateKey(date){return String(date.getFullYear())+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0');}", date_key.group(0)]
        for name in (
            "modelRangeWindow", "efficiencyComparisonWindows", "efficiencyPeriodDelta",
        ):
            match = re.search(rf"function {name}\(.*?\n\}}", self.page, re.DOTALL)
            self.assertIsNotNone(match, f"Efficiency needs {name} for matched comparisons")
            functions.append(match.group(0))
        script = "\n".join(functions) + r"""
const now=new Date(2026,8,1,15,20,0);
const today=efficiencyComparisonWindows('today',now);
const yesterday=efficiencyComparisonWindows('yesterday',now);
const seven=efficiencyComparisonWindows('7',now);
console.log(JSON.stringify({
  all:efficiencyComparisonWindows('all',now),
  today:{current:today.current.days,prior:today.prior.days,label:today.label},
  yesterday:{current:yesterday.current.days,prior:yesterday.prior.days,label:yesterday.label},
  seven:{current:seven.current.days,prior:seven.prior.days,label:seven.label},
  increase:efficiencyPeriodDelta({output_per_dollar:125},{output_per_dollar:100},'output_per_dollar','yesterday'),
  unavailable:efficiencyPeriodDelta({output_per_dollar:25},{output_per_dollar:0},'output_per_dollar','yesterday'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "all": None,
            "today": {
                "current": ["2026-09-01"], "prior": ["2026-08-31"],
                "label": "yesterday",
            },
            "yesterday": {
                "current": ["2026-08-31"], "prior": ["2026-08-30"],
                "label": "the day before",
            },
            "seven": {
                "current": ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01"],
                "prior": ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25"],
                "label": "prior 7 days",
            },
            "increase": {"text": "↑ 25.0% vs yesterday", "direction": "up"},
            "unavailable": {"text": "No comparable prior period", "direction": ""},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_badge_hides_without_a_matching_period(self):
        tone_match = re.search(
            r"function efficiencyTrendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        render_match = re.search(
            r"function renderEfficiencyTrendDelta\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(tone_match)
        self.assertIsNotNone(render_match)
        script = tone_match.group(0) + "\n" + render_match.group(0) + r"""
const badge={hidden:false,dataset:{},primary:{textContent:''},secondary:{textContent:''},querySelector(selector){return selector==='strong'?this.primary:this.secondary;}};
const $=id=>badge;
renderEfficiencyTrendDelta('e-output-dollar-change',{text:'No comparable prior period',direction:''},'up',true);
console.log(JSON.stringify({hidden:badge.hidden,direction:badge.dataset.direction}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"hidden": True, "direction": ""})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_badge_keeps_the_prior_day_relation(self):
        tone_match = re.search(
            r"function efficiencyTrendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        match = re.search(
            r"function renderEfficiencyTrendDelta\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(tone_match)
        self.assertIsNotNone(match, "Efficiency badges need one renderer for comparison copy")
        script = tone_match.group(0) + "\n" + match.group(0) + r"""
const badge={dataset:{},primary:{textContent:''},secondary:{textContent:''},querySelector(selector){return selector==='strong'?this.primary:this.secondary;}};
const $=id=>badge;
renderEfficiencyTrendDelta('e-output-dollar-change',{text:'↓ 7.5% vs prior covered day',direction:'down'});
const available={primary:badge.primary.textContent,secondary:badge.secondary.textContent,direction:badge.dataset.direction};
renderEfficiencyTrendDelta('e-output-dollar-change',{text:'No comparable prior day',direction:''});
console.log(JSON.stringify({available,unavailable:{primary:badge.primary.textContent,secondary:badge.secondary.textContent,direction:badge.dataset.direction}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "available": {
                "primary": "↓ 7.5%",
                "secondary": "vs prior covered day",
                "direction": "down",
            },
            "unavailable": {
                "primary": "No comparable prior day",
                "secondary": "",
                "direction": "",
            },
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_comparison_tone_respects_each_metric_direction(self):
        match = re.search(
            r"function efficiencyTrendTone\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency badges need an explicit metric direction rule")
        script = match.group(0) + r"""
console.log(JSON.stringify({
  outputUp:efficiencyTrendTone({direction:'up'},'up'),
  outputDown:efficiencyTrendTone({direction:'down'},'up'),
  contextDown:efficiencyTrendTone({direction:'down'},'down'),
  contextUp:efficiencyTrendTone({direction:'up'},'down'),
  unavailable:efficiencyTrendTone({direction:''},'up'),
  neutral:efficiencyTrendTone({direction:'up'},''),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "outputUp": "improving",
            "outputDown": "degrading",
            "contextDown": "improving",
            "contextUp": "degrading",
            "unavailable": "",
            "neutral": "",
        })

    def test_efficiency_headlines_keep_prior_day_comparisons_beside_values(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1,
        )[0]
        for value_id, comparison_id in (
            ("e-output-dollar", "e-output-dollar-change"),
            ("e-reasoning-ratio", "e-reasoning-change"),
        ):
            self.assertRegex(
                efficiency,
                rf'<div class=efficiencyValueRow><div class="v mono" id={value_id}>.*?</div><div class=efficiencyTrendDelta id={comparison_id}[^>]*>',
            )
        self.assertIn(
            ".efficiencyValueRow,.efficiencySupportValueRow{display:flex;align-items:center;justify-content:space-between",
            self.page,
        )
        self.assertIn(
            ".efficiencyTrendDelta{display:grid;justify-items:start;flex:0 0 auto",
            self.page,
        )
        self.assertIn(
            ".efficiencyTrendDelta{display:grid;justify-items:start;flex:0 0 auto;gap:2px;width:132px",
            self.page,
        )
        self.assertIn(
            ".efficiencySupportDelta{width:132px;padding:6px 9px}",
            self.page,
        )
        self.assertIn(
            ".efficiencySupportDelta strong{font-size:16px}",
            self.page,
        )
        self.assertIn(
            ".efficiencySupportDelta span{font-size:10px}",
            self.page,
        )
        self.assertNotIn(
            ".efficiencyHeadline.reasoning .efficiencyTrendDelta{", self.page,
            "directional reasoning badges must not be forced to the neutral violet tone",
        )
        for marker in (
            "id=e-context-load-change",
            "id=e-output-execution-change",
            "efficiencySupportDelta",
        ):
            self.assertIn(marker, efficiency)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_presentation_labels_each_metric(self):
        match = re.search(
            r"function efficiencyChartMetricPresentation\(.*?\n\}", self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(
            match, "Efficiency charts need metric-specific visible labels",
        )
        script = r"""
function compactNumber(value){return value>=1000?(value/1000).toFixed(1).replace(/\.0$/,'')+'K':String(value);}
function pct(value){return (value*100).toFixed(1)+'%';}
function efficiencyDecimal(value){return String(value)+'x';}
""" + match.group(0) + r"""
console.log(JSON.stringify({
  dollar:efficiencyChartMetricPresentation(5200,'output_per_dollar'),
  reasoning:efficiencyChartMetricPresentation(.34,'reasoning_ratio'),
  context:efficiencyChartMetricPresentation(256,'context_load'),
  execution:efficiencyChartMetricPresentation(484,'output_per_execution'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "dollar": {"axis": "5.2K", "detail": "5,200 output tokens / $"},
            "reasoning": {"axis": "34.0%", "detail": "34.0% reasoning"},
            "context": {"axis": "256x", "detail": "256x context / output"},
            "execution": {"axis": "484", "detail": "484 output tokens / exec"},
        })

    def test_efficiency_support_cards_include_compact_daily_charts(self):
        efficiency = self.page.split("<div class=view id=view-efficiency>", 1)[1].split(
            "<div class=view id=view-learn>", 1,
        )[0]
        for metric, label in (
            ("context-load", "Daily context load"),
            ("output-execution", "Daily output per execution"),
        ):
            self.assertIn(
                f'id=e-{metric}-chart viewBox="0 0 520 96" preserveAspectRatio=none role=img tabindex=0 aria-label="{label}"',
                efficiency,
            )
        self.assertIn(".efficiencySupportItem .efficiencyChart{height:56px}", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_project_filter_groups_non_folder_labels(self):
        match = re.search(r"function projectFilterValue\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(match, "Project filters need folder-only values")
        script = "const OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__';\n" + match.group(0) + r"""
console.log(JSON.stringify([
  projectFilterValue('~/Documents/github/token-meter'),
  projectFilterValue('/repo/a'), projectFilterValue('CLI'),
  projectFilterValue('No project'), projectFilterValue('/private/tmp/run'),
  projectFilterValue('~/Documents/Codex/2026-08-31/task'),
]));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [
            "~/Documents/github/token-meter", "/repo/a",
            "__other_local_sessions__", "__other_local_sessions__",
            "__other_local_sessions__",
            "__other_local_sessions__",
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_saved_non_folder_project_filters_migrate_to_other_local_sessions(self):
        value_match = re.search(r"function projectFilterValue\(.*?\n\}", self.page, re.DOTALL)
        filter_match = re.search(r"function storedModelFilterId\(.*?\n\}", self.page, re.DOTALL)
        migration_match = re.search(r"function migrateStoredProjectFilters\(.*?\n\}", self.page, re.DOTALL)
        self.assertIsNotNone(value_match, "Project filters need folder-only values")
        self.assertIsNotNone(filter_match, "Stored project filter values need a safe aggregate-ID boundary")
        self.assertIsNotNone(migration_match, "Stored project filters need a grouping migration")
        script = "const OTHER_LOCAL_SESSIONS_PROJECT='__other_local_sessions__';\n" + value_match.group(0) + "\n" + filter_match.group(0) + "\n" + migration_match.group(0) + r"""
const storage=new Map([
 ['tm_model_project','CLI'],
 ['tm_efficiency_project','No project'],
 ['tm_global_project','~/Documents/Codex/2026-08-31/task'],
 ['tm_model_project_filters',JSON.stringify({CLI:['gpt::Codex','gpt-5.6::Codex::xhigh','/Users/alice/private-project','gpt::Codex\t','gpt::Codex\u0000']})],
 ['tm_efficiency_project_filters',JSON.stringify({'No project':['gpt::Codex']})],
]);
globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
['tm_model_project','tm_efficiency_project','tm_global_project'].forEach(key=>migrateStoredProjectFilters(key));
migrateStoredProjectFilters('tm_model_project_filters',true);
migrateStoredProjectFilters('tm_efficiency_project_filters',true);
console.log(JSON.stringify(Object.fromEntries(storage)));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        stored = json.loads(result.stdout)
        other = "__other_local_sessions__"
        self.assertEqual(stored["tm_model_project"], other)
        self.assertEqual(stored["tm_efficiency_project"], other)
        self.assertEqual(stored["tm_global_project"], other)
        self.assertEqual(json.loads(stored["tm_model_project_filters"]), {other: ["gpt::Codex", "gpt-5.6::Codex::xhigh"]})
        self.assertEqual(json.loads(stored["tm_efficiency_project_filters"]), {other: ["gpt::Codex"]})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_range_aggregation_keeps_only_efforts_in_range(self):
        match = re.search(
            r"function aggregateModelDays\(rows\)\{.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match, "Efficiency needs range-scoped model aggregation")
        script = r"""
const MODEL_COUNTER_KEYS=[],MODEL_ARRAY_KEYS=[];
const metricAvailable=()=>false,metricPartial=()=>false,hasLocalEstimate=()=>false;
const usageBasis=()=> 'reported',modelWaitDistribution=()=>({median_wait_s:0,p95_wait_s:0});
const modelMedian=()=>0;
""" + match.group(0) + r"""
console.log(JSON.stringify(aggregateModelDays([
  {reasoning_efforts:['high']}, {reasoning_efforts:['xhigh', 'high']},
]).reasoning_efforts));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), ["high", "xhigh"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_inspection_reports_exact_values_and_missing_days(self):
        index_match = re.search(
            r"function efficiencyChartPointerIndex\(.*?\n\}", self.page, re.DOTALL,
        )
        detail_match = re.search(
            r"function efficiencyChartInspection\(.*?\n\}", self.page, re.DOTALL,
        )
        presentation_match = re.search(
            r"function efficiencyChartMetricPresentation\(.*?\n\}", self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(index_match)
        self.assertIsNotNone(detail_match)
        self.assertIsNotNone(presentation_match)
        script = "\n".join((
            "function compactNumber(value){return String(Math.round(value));}",
            "function pct(value){return (value*100).toFixed(1)+'%';}",
            "function efficiencyDecimal(value){return String(value)+'x';}",
            index_match.group(0), presentation_match.group(0), detail_match.group(0), r"""
const samples=[
  {day:'2026-08-25',index:0,value:5143.4},
  {day:'2026-08-26',index:1,value:null},
  {day:'2026-08-27',index:2,value:.3421},
];
const rect={left:100,width:520};
console.log(JSON.stringify({
  first:efficiencyChartPointerIndex(109,rect,3),
  middle:efficiencyChartPointerIndex(360,rect,3),
  last:efficiencyChartPointerIndex(611,rect,3),
  output:efficiencyChartInspection(samples[0],'output_per_dollar'),
  missing:efficiencyChartInspection(samples[1],'output_per_dollar'),
  reasoning:efficiencyChartInspection(samples[2],'reasoning_ratio'),
}));
""",
        ))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "first": 0,
            "middle": 1,
            "last": 2,
            "output": {
                "date": "Aug 25",
                "value": "5,143 output tokens / $",
            },
            "missing": {"date": "Aug 26", "value": "No data"},
            "reasoning": {"date": "Aug 27", "value": "34.2% reasoning"},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_hover_survives_live_refresh(self):
        names = (
            "efficiencyChartPointerIndex",
            "efficiencyChartMetricPresentation",
            "efficiencyChartInspection",
            "efficiencyChartAnnouncement",
            "efficiencyChartSmoothPath",
            "renderEfficiencyChart",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"function {name}\(.*?\n\}}", self.page, re.DOTALL,
            )
            self.assertIsNotNone(match)
            functions.append(match.group(0))
        script = "\n".join((*functions, r"""
class AttrNode {
  constructor(){this.attrs={};}
  setAttribute(name,value){this.attrs[name]=String(value);}
}
class InspectNode extends AttrNode {
  constructor(){super();this.line=new AttrNode();this.dot=new AttrNode();}
  querySelector(selector){return selector==='line'?this.line:this.dot;}
}
class SvgNode extends AttrNode {
  constructor(parent){super();this.parentElement=parent;this.dataset={};this.hovered=false;this.inspect=null;}
  set innerHTML(value){this.html=value;this.inspect=new InspectNode();}
  get innerHTML(){return this.html;}
  querySelector(selector){return selector==='.efficiencyChartInspect'?this.inspect:null;}
  getBoundingClientRect(){return {left:0,width:520};}
  matches(selector){return selector===':hover'&&this.hovered;}
}
const tip={hidden:true,textContent:'',style:{}},status={textContent:''};
const parent={querySelector(selector){return selector==='.efficiencyChartTip'?tip:status;}};
const svg=new SvgNode(parent),$=()=>svg,esc=value=>String(value);
const pct=value=>`${Math.round(value*100)}%`,compactNumber=value=>String(Math.round(value));
const trend=[
  {day:'2026-08-25',metrics:{output_per_dollar:5143.4}},
  {day:'2026-08-26',metrics:{output_per_dollar:7777}},
  {day:'2026-08-27',metrics:{output_per_dollar:9123}},
];
renderEfficiencyChart('chart',trend,'output_per_dollar','Output per covered dollar');
svg.hovered=true;
svg.onpointermove({clientX:260});
const first={hidden:tip.hidden,text:tip.textContent,ratio:svg.dataset.efficiencyHoverRatio};
renderEfficiencyChart('chart',trend,'output_per_dollar','Output per covered dollar');
const rerender={hidden:tip.hidden,text:tip.textContent,visibility:svg.inspect.attrs.visibility};
svg.onpointermove({clientX:510});
const moved={hidden:tip.hidden,text:tip.textContent};
svg.hovered=false;
svg.onpointerleave();
const left={hidden:tip.hidden,ratio:svg.dataset.efficiencyHoverRatio||null,visibility:svg.inspect.attrs.visibility};
svg.hovered=true;
svg.dataset.efficiencyHoverRatio='0.5';
renderEfficiencyChart('chart',[],'output_per_dollar','Output per covered dollar');
const empty={hidden:tip.hidden,ratio:svg.dataset.efficiencyHoverRatio||null};
console.log(JSON.stringify({first,rerender,moved,left,empty}));
"""))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "first": {
                "hidden": False,
                "text": "Aug 26 · 7,777 output tokens / $",
                "ratio": "0.5",
            },
            "rerender": {
                "hidden": False,
                "text": "Aug 26 · 7,777 output tokens / $",
                "visibility": "visible",
            },
            "moved": {
                "hidden": False,
                "text": "Aug 27 · 9,123 output tokens / $",
            },
            "left": {"hidden": True, "ratio": None, "visibility": "hidden"},
            "empty": {"hidden": True, "ratio": None},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_inspection_is_keyboard_accessible(self):
        match = re.search(
            r"function efficiencyChartKeyIndex\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  left:efficiencyChartKeyIndex('ArrowLeft',2,5),
  leftBound:efficiencyChartKeyIndex('ArrowLeft',0,5),
  right:efficiencyChartKeyIndex('ArrowRight',2,5),
  rightBound:efficiencyChartKeyIndex('ArrowRight',4,5),
  home:efficiencyChartKeyIndex('Home',3,5),
  end:efficiencyChartKeyIndex('End',1,5),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "left": 1,
            "leftBound": 0,
            "right": 3,
            "rightBound": 4,
            "home": 0,
            "end": 4,
        })
        for marker in (
            "tabindex=0",
            "aria-live=polite",
            "svg.onfocus",
            "svg.onkeydown",
            "svg.onblur",
            "Use Left and Right arrow keys to inspect daily values.",
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_chart_accessibility_uses_one_fresh_announcement_path(self):
        match = re.search(
            r"function efficiencyChartAnnouncement\(.*?\n\}", self.page, re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
const detail={date:'Aug 26',value:'No data'};
console.log(JSON.stringify({
  keyboard:efficiencyChartAnnouncement(detail,true),
  pointer:efficiencyChartAnnouncement(detail,false),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "keyboard": "Aug 26 · No data",
            "pointer": "",
        })
        self.assertNotIn("aria-describedby=e-output-dollar-status", self.page)
        self.assertNotIn("aria-describedby=e-reasoning-status", self.page)
        self.assertIn(
            "status.textContent=efficiencyChartAnnouncement(detail,announce)",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_reasoning_cell_falls_back_to_measured_thinking_share(self):
        match = re.search(
            r"function efficiencyReasoningPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = r"""
const f=value=>Math.round(value||0).toLocaleString('en-US');
const pct=value=>Math.round((value||0)*100)+'%';
""" + match.group(0) + r"""
console.log(JSON.stringify({
  exact:efficiencyReasoningPresentation(
    {executions:10,reasoning_executions:8,thinking_executions:8},
    {reasoning_ratio:.375}
  ),
  thinking:efficiencyReasoningPresentation(
    {executions:960,reasoning_executions:0,thinking_executions:402},
    {reasoning_ratio:null}
  ),
  missing:efficiencyReasoningPresentation(
    {executions:4,reasoning_executions:0,thinking_executions:0},
    {reasoning_ratio:null}
  ),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "exact": {"value": "38%", "note": "8 / 10 exec"},
            "thinking": {
                "value": "42% thinking execs",
                "note": "token ratio unavailable",
            },
            "missing": {"value": "--", "note": "unavailable"},
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_efficiency_presentation_preserves_coverage_and_thinking_fallback(self):
        metric_match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}", self.page, re.DOTALL,
        )
        reasoning_match = re.search(
            r"function efficiencyReasoningPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        summary_match = re.search(
            r"function efficiencySummaryPresentation\(.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(metric_match)
        self.assertIsNotNone(reasoning_match)
        self.assertIsNotNone(summary_match)
        script = r"""
const f=value=>Math.round(value||0).toLocaleString('en-US');
const pct=value=>Math.round((value||0)*100)+'%';
const compactNumber=value=>Math.round(value||0).toLocaleString('en-US');
const hasLocalEstimate=row=>Boolean(row?.estimated);
""" + "\n".join((
            metric_match.group(0), reasoning_match.group(0), summary_match.group(0),
        )) + r"""
const exact=efficiencySummaryPresentation({
  output_tokens:200,cost:2,executions:10,token_covered_executions:10,
  cost_covered_executions:10,cost_covered_output_tokens:200,cost_covered_cost:2,
  reasoning_tokens:30,reasoning_output_tokens:80,reasoning_executions:8,
  thinking_executions:8,availability:{tokens:true,output_tokens:true,cost:true,reasoning_tokens:true},
  coverage:{cost:{complete:true}},
});
const thinking=efficiencySummaryPresentation({
  output_tokens:100,cost:1,executions:10,token_covered_executions:10,
  cost_covered_executions:8,cost_covered_output_tokens:80,cost_covered_cost:1,
  reasoning_executions:0,thinking_executions:4,
  availability:{tokens:true,output_tokens:true,cost:true,reasoning_tokens:false},
  coverage:{cost:{complete:false}},estimated:true,
});
const missing=efficiencySummaryPresentation({
  executions:2,availability:{tokens:false,output_tokens:false,cost:false,reasoning_tokens:false},
});
console.log(JSON.stringify({exact,thinking,missing}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "exact": {
                "output": {"value": "100 / $", "note": "covered"},
                "reasoning": {"value": "38%", "note": "8 / 10 exec"},
            },
            "thinking": {
                "output": {"value": "80 / $", "note": "partial · local est"},
                "reasoning": {
                    "value": "40% thinking execs",
                    "note": "token ratio unavailable · local est",
                },
            },
            "missing": {
                "output": {"value": "--", "note": "unavailable"},
                "reasoning": {"value": "--", "note": "unavailable"},
            },
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_only_marks_thinking_token_splits_unavailable_when_uncovered(self):
        match = re.search(
            r"function thinkingUnavailableExecutions\(row\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group(0) + r"""
console.log(JSON.stringify({
  unavailable:thinkingUnavailableExecutions({thinking_executions:2,thinking_covered_executions:0,reasoning_executions:0}),
  partial:thinkingUnavailableExecutions({thinking_executions:3,thinking_covered_executions:1,reasoning_executions:1}),
  exact:thinkingUnavailableExecutions({thinking_executions:1,thinking_covered_executions:1,reasoning_executions:1}),
  unrelated:thinkingUnavailableExecutions({thinking_executions:1,thinking_covered_executions:0,reasoning_executions:4}),
  codexOnly:thinkingUnavailableExecutions({thinking_executions:0,thinking_covered_executions:0,reasoning_executions:4}),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "unavailable": 2,
            "partial": 2,
            "exact": 0,
            "unrelated": 1,
            "codexOnly": 0,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_efficiency_model_table_defaults_to_spend_descending_and_sorts_nulls_last(self):
        metric_match = re.search(
            r"function efficiencyMetrics\(row\)\{.*?\n\}", self.page, re.DOTALL,
        )
        value_match = re.search(
            r"function efficiencySortValue\(row,key\)\{.*?\n\}", self.page, re.DOTALL,
        )
        sort_match = re.search(
            r"function sortEfficiencyRows\(rows,sort=efficiencySort\)\{.*?\n\}",
            self.page, re.DOTALL,
        )
        self.assertIsNotNone(metric_match)
        self.assertIsNotNone(value_match)
        self.assertIsNotNone(sort_match)
        script = "\n".join((
            metric_match.group(0),
            "const modelRuntimeLabel=row=>`${row.model} · ${row.runtime}`;",
            "const efficiencySort={key:'cost',direction:'desc'};",
            value_match.group(0), sort_match.group(0),
            r"""
const rows=[
  {model:'low',runtime:'Claude Code',window:{cost:1,availability:{cost:true}}},
  {model:'missing',runtime:'Claude Code',window:{cost:0,availability:{cost:false}}},
  {model:'high',runtime:'Codex',window:{cost:4,availability:{cost:true}}},
];
console.log(JSON.stringify({
  spend:sortEfficiencyRows(rows).map(row=>row.model),
  efficiency:sortEfficiencyRows(rows,{key:'output_per_dollar',direction:'desc'}).map(row=>row.model),
}));
""",
        ))
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["spend"], ["high", "low", "missing"])
        self.assertEqual(payload["efficiency"][-1], "missing")

    def test_model_day_merge_preserves_partial_source_coverage(self):
        merge_match = re.search(
            r"function mergeModelDays\(models,window\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        aggregate_match = re.search(
            r"function aggregateModelDays\(rows\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(merge_match, "dashboard needs the shared day merge")
        self.assertIsNotNone(aggregate_match, "dashboard needs the shared day aggregate")
        script = r"""
const MODEL_COUNTER_KEYS=['cost','input_tokens','output_tokens','executions'];
const MODEL_ARRAY_KEYS=[];
const modelDayInRange=()=>true;
const metricAvailable=(row,key)=>row.availability?.[key]===true;
const metricPartial=(row,key)=>row.coverage?.[key]?.complete===false;
const hasLocalEstimate=()=>false;
const usageBasis=()=> 'reported';
const modelWaitDistribution=()=>({median_wait_s:0,p95_wait_s:0});
const modelMedian=()=>0;
""" + aggregate_match.group(0) + "\n" + merge_match.group(0) + r"""
const rows=mergeModelDays([{daily:[{
  day:'2026-08-27',cost:1,input_tokens:10,output_tokens:2,executions:2,
  availability:{tokens:true,input_tokens:true,output_tokens:false,cost:true,cache:true},
  coverage:{
    tokens:{complete:false},cost:{complete:false},cache:{complete:false},
  },
}]}],{});
const selectedSession=aggregateModelDays([{
  cost:1,input_tokens:10,output_tokens:4,executions:2,
  cost_covered_executions:1,cost_covered_output_tokens:2,cost_covered_cost:.5,
  availability:{tokens:true,input_tokens:true,output_tokens:true,cost:true,cache:false},
}]);
console.log(JSON.stringify({merged:rows[0],total:aggregateModelDays(rows),selectedSession}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        coverage = payload["merged"]["coverage"]
        self.assertFalse(coverage["tokens"]["complete"])
        self.assertFalse(coverage["cost"]["complete"])
        self.assertFalse(coverage["cache"]["complete"])
        self.assertTrue(payload["merged"]["availability"]["input_tokens"])
        self.assertFalse(payload["merged"]["availability"]["output_tokens"])
        self.assertTrue(payload["total"]["availability"]["input_tokens"])
        self.assertFalse(payload["total"]["availability"]["output_tokens"])
        self.assertFalse(payload["selectedSession"]["coverage"]["cost"]["complete"])

    def test_model_stats_supports_multi_model_comparison(self):
        for marker in (
            "id=m-model-picker", "id=m-model-options", "id=m-model-summary",
            "tm_model_filters", "MODEL_COLORS", "buildModelTrend",
            "bars show output", "observed tok/s",
            "modelTipMetrics",
            "Typical wait", "median wait", "human pause excluded",
            "modelWaitDistribution", "wait_durations_s", "p95_wait_s",
            "Matched pace", "renderMatchedPace", "modelRuntimeLabel",
            "migrateModelRuntimeFilters", "Typical workload", "median_peak_input_tokens",
            "95% CI", "Select 2 models", "TTFT unavailable",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn('id=m-model aria-label="Models filter"', self.page)
        self.assertNotIn("Speed change", self.page)

    def test_model_colors_encode_runtime_family_and_keep_volume_neutral(self):
        """Flattening runtime families or recoloring volume bars breaks chart meaning."""
        start = self.page.find("// model-color-logic-start")
        end = self.page.find("// model-color-logic-end")
        logic = self.page[start:end] if start >= 0 and end > start else ""
        script = logic + """
const names = [
  'gpt-5.6-sol::Codex',
  'gpt-5.6-terra::Codex',
  'codex-auto-review::Codex',
  'gpt-5.6-luna::Codex',
  'claude-opus-4-8::Claude Code',
  'claude-sonnet-5::Claude Code',
  'claude-haiku-4-5::Claude-3P',
  'claude-opus-4-8::Claude-3P',
  'claude-sonnet-5::Claude-3P',
  'composer-2.5::Cursor',
  'claude-sonnet-4-6::Kiro',
  'qwen3::OpenCode',
  'other::Unknown',
];
console.log(JSON.stringify({
  codex: [modelColor(names[2], names), modelColor(names[3], names), modelColor(names[0], names), modelColor(names[1], names)],
  claude: [modelColor(names[4], names), modelColor(names[5], names)],
  thirdParty: [modelColor(names[6], names), modelColor(names[7], names), modelColor(names[8], names)],
  cursor: modelColor(names[9], names),
  kiro: modelColor(names[10], names),
  openCode: modelColor(names[11], names),
  fallback: modelColor(names[12], names),
  stable: modelColor(names[0], names) === modelColor(names[0], [...names].reverse()),
  paint: modelTrendPaint('#FFAA64'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "codex": ["#55D6ED", "#32B8C8", "#7AA7FF", "#50CFB0"],
            "claude": ["#FFAA64", "#F3C76A"],
            "thirdParty": ["#B9A2FF", "#D28CFF", "#9EAEFF"],
            "cursor": "#65D6A6",
            "kiro": "#92A2F4",
            "openCode": "#FF8290",
            "fallback": "#A9B8C7",
            "stable": True,
            "paint": {
                "barFill": "#536879",
                "barStroke": "#7890A3",
                "line": "#FFAA64",
            },
        })

    def test_model_trend_omits_legend_and_limits_hover_to_relevant_metrics(self):
        self.assertNotIn("id=m-legend", self.page)
        self.assertNotIn("$('m-legend')", self.page)
        for marker in (
            "function modelTrendRowHasUsage(row)",
            "Number(row?.input_tokens||0)>0",
            "Number(row?.output_tokens||0)>0",
            "Number(row?.executions||0)>0",
            ".filter(item=>modelTrendRowHasUsage(item.row))",
            "if(!rows.length){hideTip();return;}",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            '<div class=modelTipName title="${esc(item.model)}"><i class=modelSwatch '
            'style="background:${item.color}"></i>${esc(item.model)}</div>',
            self.page,
        )
        model_trend = self.page.split("function drawModelTrend(chart){", 1)[1].split(
            "function renderMatchedPace", 1
        )[0]
        for marker in (
            "const outputAvailable=metricAvailable(item.row,'tokens')",
            "const selectedAvailable=metric.available(item.row)",
            "<small>output</small>", "<small>${esc(metric.note)}</small>",
            "${outputAvailable?compactNumber(item.row.output_tokens||0):'--'}",
            "${selectedAvailable?metric.format(metric.value(item.row)):'--'}",
        ):
            self.assertIn(marker, model_trend)
        for marker in (
            "<small>input</small>", "<small>executions</small>",
            "<small>avg input</small>", "<small>avg output</small>",
            "<small>tok/s</small>", "<small>typical wait</small>",
        ):
            self.assertNotIn(marker, model_trend)

    def test_model_stats_supports_project_scoped_average_io_trends(self):
        for marker in (
            "id=m-project", "tm_model_project", "tm_model_project_filters",
            "/model-stats?project=", "renderActiveModelStats",
            "modelProjectRequest", "modelProjectLoadingKey",
            "data-model-metric=avg_input", "data-model-metric=avg_output",
            "MODEL_TREND_METRICS", "modelTokensPerExecution",
            "Model trends", "avg input / execution", "avg output / execution",
            "input / exec", "output / exec",
            "Daily model ${metric.note} and output token volume",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("modelProjectCache.set(key,payload.model_stats)", self.page)
        self.assertIn("request!==modelProjectRequest||project!==modelProject", self.page)
        self.assertNotIn(
            "$('m-trend-title').textContent=modelTrendMetric==='wait'",
            self.page,
        )

    def test_model_filter_menus_stay_stable_during_live_refreshes(self):
        for marker in (
            "setLogSelectOptions(projectSelect,projectOptions,modelProject)",
            "setLogHtml($('m-model-options'),modelPickerOptions)",
            "function positionModelPickerMenu()",
            "picker.classList.toggle('opensUp',opensUp)",
            "$('m-model-picker').addEventListener('toggle',()=>requestAnimationFrame(positionModelPickerMenu))",
            ":is(.modelHead,.efficiencyHead).spectrumPageHead{overflow:hidden;z-index:2}.spectrumPageHeadFrame:has(.modelControls){z-index:10}",
            ".modelPicker.opensUp .modelPickerMenu{top:auto;bottom:calc(100% + 6px)}",
        ):
            self.assertIn(marker, self.page)

    def test_efficiency_model_filter_stacks_above_its_chart(self):
        self.assertIn(
            ".spectrumPageHeadFrame:has(.modelControls){z-index:10}",
            self.page,
        )

    def test_model_project_background_refresh_does_not_shift_the_page(self):
        for marker in (
            "function setModelProjectLoading(loading)",
            "projectSelect.setAttribute('aria-busy','true')",
            "setModelProjectLoading(false);",
            "$('m-project-status').textContent='';",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("Loading ${project}", self.page)

    def test_session_card_hover_preserves_the_live_card_node(self):
        for marker in (
            "currentGrid=$('current-session-grid'),interactingCurrentSessionCard=currentGrid.querySelector('.currentSessionCard:hover,.currentSessionCard:focus');",
            "const mountedCurrentSessionIds=[...currentGrid.querySelectorAll('.currentSessionCard[data-current-session-id]')].map(card=>card.dataset.currentSessionId);",
            "if(currentSessionDragId||(interactingCurrentSessionCard&&currentSessionIdsMatch(mountedCurrentSessionIds,rows))){syncCurrentSessionActivity(currentGrid,rows);return;}",
            "card.classList.remove('activity-working','activity-waiting','activity-recent');",
            "body.spectrumApp.sessionRoute #view-session .currentSessionCard:hover{transform:none}",
        ):
            self.assertIn(marker, self.page)

    def test_model_trend_hover_panel_is_scrollable_and_pointer_stable(self):
        model_trend = self.page.split("function drawModelTrend(chart){", 1)[1].split(
            "function renderMatchedPace", 1
        )[0]
        for marker in (
            '#m-chart-tip{pointer-events:auto;overscroll-behavior:contain;',
            "scrollbar-gutter:stable",
            '#m-chart-tip .h{position:sticky;top:0',
            'id=m-chart-tip tabindex=0 aria-label="Model details for hovered day"',
            "const scheduleTipHide=()=>",
            "setTimeout(hideTip,180)",
            "hit.onpointerleave=scheduleTipHide",
            "tip.onpointerenter=cancelTipHide",
            "tip.onpointerleave=scheduleTipHide",
            "rightX+tip.offsetWidth<=rect.width-8",
            "if(dayChanged)tip.scrollTop=0",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("hit.onpointerleave=()=>tip.style.display='none'", model_trend)

    def test_wait_time_is_first_class_across_current_logs_and_models(self):
        for marker in (
            "data-chart=wait", "drawWaitChart", "data-gsort=wait",
            "id=lf-wait", "id=m-wait", "id=m-metric", "data-model-metric=wait",
            "Prompt-to-completed-response", "lower is better",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("wait_time?.total_s", self.page)
        self.assertIn("CURRENT?.wait_time?.samples", self.page)

    def test_session_chart_only_offers_linear_and_cumulative_scales(self):
        scale_controls = self.page.split('<div class=seg id=scale>', 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("data-scale=linear", scale_controls)
        self.assertIn("data-scale=cumulative", scale_controls)
        self.assertNotIn("data-scale=sqrt", scale_controls)
        self.assertNotIn("data-scale=log", scale_controls)
        self.assertIn("const CHART_SCALES=['linear','cumulative'];", self.page)
        self.assertIn("if(!CHART_SCALES.includes(chartScale))", self.page)
        self.assertIn("localStorage.setItem('tm_chart_scale',chartScale);", self.page)
        self.assertNotIn("if(scale==='sqrt')", self.page)
        self.assertNotIn("if(scale==='log')", self.page)

    def test_models_history_supports_exact_local_days(self):
        history = self.page.split(
            'id=m-range aria-label="Models history range"', 1
        )[1].split("</select>", 1)[0]
        expected = (
            '<option value=today>Today</option>',
            '<option value=yesterday>Yesterday</option>',
            '<option value=7>Last 7 days</option>',
            '<option value=30 selected>Last 30 days</option>',
            '<option value=90>Last 90 days</option>',
            '<option value=all>All history</option>',
        )
        for option in expected:
            self.assertIn(option, history)
        self.assertEqual([history.index(option) for option in expected], sorted(
            history.index(option) for option in expected
        ))
        for marker in (
            "const MODEL_RANGES=['today','yesterday','7','30','90','all'];",
            "if(!MODEL_RANGES.includes(modelRange))",
            "function modelRangeWindow(range,now=new Date())",
            "if(range==='today'||range==='yesterday')",
            "function modelDayInRange(day,window)",
            "mergeModelDays(selected,rangeWindow)",
            "buildModelTrend(selected,rangeWindow,names)",
            ".filter(row=>modelDayInRange(row.day,rangeWindow))",
            "modelRangeLabel(modelRange)",
        ):
            self.assertIn(marker, self.page)

    def test_session_chart_supports_cumulative_cost_and_token_views(self):
        for marker in (
            "data-scale=cumulative",
            "Show running estimated cost or token totals through each execution.",
            "const CHART_SCALES=['linear','cumulative'];",
            "if(!CHART_SCALES.includes(chartScale))",
            "b.setAttribute('aria-pressed',selected?'true':'false');",
            "function cumulativeChartParts(parts,mode)",
            "const keys=mode==='cost'?['cost']:['fresh','read','write','cache','total','out'];",
            "keys.forEach(key=>{sums[key]+=Math.max(0,Number(part[key]||0));});",
            "const cumulativeTokens=mode==='tokens'&&chartScale==='cumulative';",
            "const cumulativeCost=mode==='cost'&&chartScale==='cumulative';",
            "const cumulativeValues=cumulativeTokens||cumulativeCost;",
            "parts=cumulativeValues?cumulativeChartParts(rawParts,mode):rawParts",
            "running estimated cost through each execution",
            "running token totals through each execution",
            "cumulative cost",
            "cumulative usd",
            "cumulative tokens",
            "const tokenPrefix=cumulativeTokens?'cumulative ':'';",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("rawParts.map(p=>p.tools)", self.page)
        self.assertIn("rawParts.map(p=>p.reason)", self.page)

    def test_language_signals_are_inside_models_with_machine_wide_settings(self):
        for marker in (
            "id=model-frustration", "id=f-utterances",
            "id=f-rate", "id=f-chart", "id=f-models", "id=f-chats",
            "id=f-chat-mode", "drawFrustrationTrend",
            "id=f-add-terms", "id=positive-terms", "id=frustration-terms",
            "id=frustration-save", "id=f-signal-group",
            "/settings/language-signals", "User language signals", "Positive", "Friction",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(h==='models'||h==='frustration')", self.page)
        self.assertIn("renderFrustration(LATEST.xsession?.language_signals,LATEST.xsession?.sessions)", self.page)
        self.assertIn("$('f-add-terms').onclick=()=>{setHashRoute('settings')", self.page)
        self.assertIn("$('frustration-settings').scrollIntoView", self.page)
        self.assertIn("Add more terms", self.page)
        self.assertIn("Save and recalculate", self.page)
        self.assertNotIn("id=tab-frustration", self.page)
        self.assertNotIn("id=view-frustration", self.page)
        self.assertLess(self.page.index("id=view-models"), self.page.index("id=model-frustration"))
        self.assertLess(self.page.index("id=model-frustration"), self.page.index("id=view-daily"))
        self.assertNotIn("id=f-model-table", self.page)
        self.assertNotIn("id=f-session-table", self.page)

    def test_language_signals_use_compact_uniform_panels(self):
        for marker in (
            'class="modelHead signalHead"',
            'class="modelControls signalControls"',
            "class=signalTrendControls",
            ".frustrationHero .modelKpi{min-height:78px",
            ".frustrationChart{height:220px}",
            ".frustrationBreakdown{grid-template-columns:repeat(3,minmax(0,1fr))",
            ".signalPanel{height:350px;display:flex;flex-direction:column",
            ".signalRankList,.chatSignalList{min-height:0;flex:1;overflow:auto",
            "@media(max-width:900px){.modelControls,.signalControls{grid-template-columns:repeat(3,minmax(0,1fr))}.frustrationBreakdown{grid-template-columns:1fr}",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            'style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;justify-content:flex-end"',
            self.page,
        )

    def test_language_signals_use_shared_spectrum_colors(self):
        for marker in (
            "--signal-primary:var(--spectrum-blue)",
            "--signal-secondary:var(--spectrum-sky)",
            "--signal-edge:rgba(127,219,242,.34)",
            "--signal-wash:rgba(0,188,235,.08)",
            "background:linear-gradient(90deg,var(--signal-primary),var(--signal-secondary))",
            ".termPanel .signalRankTrack i{background:linear-gradient(90deg,var(--signal-secondary),var(--signal-primary))",
            ".frustrationSection .signalRateBadge{border-color:var(--signal-edge)",
            "class=signalMatched",
            "cssVar('--spectrum-blue')",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            '<span><i style="background:var(--warn)"></i>matched',
            self.page,
        )

    def test_model_pricing_is_editable_and_supports_new_models_in_settings(self):
        for marker in (
            "id=model-pricing-settings", "id=model-pricing-rows",
            "id=model-price-scope", "id=model-price-scope-from-now",
            "id=model-price-scope-from-date", "id=model-price-scope-all-history",
            "value=from_now", "value=from_date", "value=all_history",
            "id=model-price-effective-from", "id=model-price-scope-note",
            "id=model-pricing-reviewed", "id=model-pricing-sources",
            "id=model-price-add-form", "id=model-price-provider",
            "id=model-price-model", "id=model-price-input",
            "id=model-price-output", "id=model-price-cache-write",
            "id=model-price-cache-read", "id=model-price-save-changes",
            "id=model-price-save-changes type=button disabled",
            "data-model-price-select", "data-model-price-lifecycle",
            "function renderModelPricing",
            "function modelPriceEffectiveFrom", "function updateModelPriceScope",
            "function confirmModelPriceHistory",
            "function saveModelPriceChanges", "/settings/model-pricing",
            "apply_to_all_history", "From now", "From date", "All history",
            "Save models", "Restore built-in price", "Remove custom model",
            "changes",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("USD per 1 million tokens", self.page)
        self.assertIn("Changes start when you save", self.page)
        self.assertIn("Choose the date", self.page)
        self.assertIn("older session estimates", self.page)
        self.assertIn(".modelPriceScope.history", self.page)
        self.assertIn("badge.hidden=!hasCustomPricing", self.page)
        self.assertIn("source==='built-in'?'':source", self.page)
        self.assertIn("sourceLabel?`<span class=modelPriceSource>", self.page)
        self.assertIn("row.overridden", self.page)
        self.assertIn("pricing.sources", self.page)
        self.assertIn("pricing.reviewed_on", self.page)
        self.assertIn("modelPriceSelectionState(modelPricingSelected.size)", self.page)
        self.assertIn("@media(max-width:700px){.modelPriceTableWrap{overflow:visible", self.page)
        self.assertIn(".modelPriceTable{display:block;min-width:0", self.page)
        self.assertIn("data-label=", self.page)
        self.assertNotIn("data-model-price-save", self.page)
        self.assertNotIn("Built-in defaults", self.page)
        self.assertLess(
            self.page.index("id=model-pricing-settings"),
            self.page.index("id=frustration-settings"),
        )
        self.assertNotIn("id=cursor-source-status", self.page)
        self.assertNotIn("Local trace source", self.page)
        self.assertNotIn("function renderCursorSourceStatus", self.page)
        self.assertIn("h==='settings-budgets'||h==='budgets'", self.page)
        self.assertIn("$('model-pricing-settings').scrollIntoView", self.page)

    def test_model_pricing_uses_explicit_row_selection_without_an_actions_column(self):
        pricing = self.page.split("id=model-pricing-settings", 1)[1].split(
            "id=frustration-settings", 1,
        )[0]
        for marker in (
            "Apply to selected models",
            "id=model-price-selection-count",
            "No models selected",
        ):
            self.assertIn(marker, pricing)
        for marker in (
            "data-model-price-select",
            "data-model-price-lifecycle",
            "function setModelPriceSelected",
            "function updateModelPriceSelectionState",
            "modelPricingSelected",
            "setModelPriceSelected(root,true,false)",
            "keys=[...modelPricingSelected]",
            "Restore built-in price",
            "Remove custom model",
            "Select ${row.model} for price update",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("<th class=num>Actions</th>", pricing)
        self.assertNotIn('data-label="Action"', pricing)
        self.assertNotIn("class=modelPriceActions", self.page)
        self.assertIn(
            ".modelPriceTable tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_model_price_selection_copy_tracks_the_selected_count(self):
        match = re.search(
            r"function modelPriceSelectionState\(count\)\{.*?\n\}",
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs one selection-copy helper")
        script = match.group(0) + """
console.log(JSON.stringify([
  modelPriceSelectionState(0),
  modelPriceSelectionState(1),
  modelPriceSelectionState(3),
]));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [
            {"summary": "No models selected", "button": "Save models", "disabled": True},
            {"summary": "1 model selected", "button": "Save 1 model", "disabled": False},
            {"summary": "3 models selected", "button": "Save 3 models", "disabled": False},
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_settings_polling_pauses_while_the_user_is_selecting_or_editing(self):
        functions = []
        for name in ("settingsInteractionActive", "renderSettings"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
let settingsPointerActive=false;
const view={contains:node=>Boolean(node?.insideSettings)};
const field={insideSettings:true,matches:selector=>selector.includes('input')};
const outside={insideSettings:false,matches:()=>false};
let activeElement=outside;
let selection={isCollapsed:true,rangeCount:0,anchorNode:null,focusNode:null};
const document={get activeElement(){return activeElement;}};
const window={getSelection:()=>selection};
const $=id=>id==='view-settings'?view:null;
let calls=[];
const renderBudgets=()=>calls.push('budgets');
const renderFrustrationSettings=()=>calls.push('signals');
const renderModelPricing=()=>calls.push('pricing');
""" + "\n".join(functions) + """
const xs={language_signals:{},model_pricing:{}};
activeElement=field;
const focused=settingsInteractionActive();
renderSettings(xs);
const focusedCalls=[...calls];
calls=[];
activeElement=outside;
selection={isCollapsed:false,rangeCount:1,anchorNode:{insideSettings:true},focusNode:{insideSettings:true}};
const selected=settingsInteractionActive();
renderSettings(xs);
const selectedCalls=[...calls];
calls=[];
selection={isCollapsed:true,rangeCount:0,anchorNode:null,focusNode:null};
settingsPointerActive=true;
const dragging=settingsInteractionActive();
renderSettings(xs);
const draggingCalls=[...calls];
calls=[];
settingsPointerActive=false;
selection={isCollapsed:true,rangeCount:1,anchorNode:{insideSettings:true},focusNode:{insideSettings:true}};
const idle=settingsInteractionActive();
renderSettings(xs);
console.log(JSON.stringify({focused,focusedCalls,selected,selectedCalls,dragging,draggingCalls,idle,idleCalls:calls}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "focused": True,
            "focusedCalls": [],
            "selected": True,
            "selectedCalls": [],
            "dragging": True,
            "draggingCalls": [],
            "idle": False,
            "idleCalls": ["budgets", "signals", "pricing"],
        })

    def test_execution_overview_separates_activity_from_removable_optimization(self):
        for marker in ("id=ov-activity-tools", "id=ov-optional-use", "id=ov-unused-packs"):
            self.assertIn(marker, self.page)
        self.assertIn("renderCurrentOptimization(s)", self.page)
        self.assertIn("function setCapabilityStatBar(id,value)", self.page)
        self.assertLess(
            self.page.index("function setCapabilityStatBar(id,value)"),
            self.page.index("setCapabilityStatBar('ov-optional-bar'"),
        )
        self.assertIn("if(!bar)return;", self.page)
        self.assertIn("bar.setAttribute('aria-valuetext',label);", self.page)
        self.assertNotIn("renderCapabilityUsage('ov-cap'", self.page)
        self.assertIn("Default tools and MCP servers are read-only evidence", self.page)

    def test_tools_leads_with_stats_review_queue_and_visible_inventory(self):
        self.assertRegex(self.page, r"id=tab-capabilities[^>]*>.*?<span class=tabLabel>Tools</span>")
        tools = self.page.split("id=view-capabilities", 1)[1].split(
            "id=view-settings", 1
        )[0]
        for marker in (
            'aria-label="Capability overview"', "id=c-stat-tools",
            "id=c-stat-mcps", "id=c-stat-skills", "data-cap-jump=tool",
            "id=c-review-queue", "id=c-review-list",
            "id=c-selection-bar", "id=c-disable-selected",
            "id=c-inventory", "id=c-clear-filters", "id=c-active-filters",
            'aria-label="Prompt-load evidence"',
        ):
            self.assertIn(marker, tools)
        self.assertLess(tools.index('aria-label="Capability overview"'), tools.index("id=c-review-queue"))
        self.assertLess(tools.index("id=c-review-queue"), tools.index("id=c-inventory"))
        self.assertRegex(tools, r"<section class=\"card capInventory\" id=c-inventory")
        self.assertNotIn('class="card capInventory" id=c-inventory-details', tools)
        self.assertNotIn("id=c-catalog-details", tools)
        self.assertIn('class="capPageHead spectrumPageHead"', tools)
        self.assertIn("class=spectrumPageHeadCopy", tools)
        tools_head = tools.split('<section class="card capStats"', 1)[0]
        for removed in (
            "hasPageActions", "spectrumPageActions", "capFreshness",
            "c-evidence-status", "c-evidence-updated",
        ):
            self.assertNotIn(removed, tools_head)
        self.assertNotIn("c-evidence-status", self.page)
        self.assertNotIn("c-evidence-updated", self.page)
        for removed in (
            "id=c-decision-panel", "id=c-opt-enabled", "id=c-opt-used",
            "id=c-opt-review", "id=c-mcp-observed",
            'aria-label="Capability evidence summary"', "id=c-review-filter",
            "id=c-browse-all",
        ):
            self.assertNotIn(removed, tools)
        self.assertIn('aria-label="Capability type"', self.page)
        self.assertIn('aria-label="Evidence and configuration"', self.page)
        self.assertIn('aria-label="Search installed capabilities"', self.page)
        self.assertIn("button.setAttribute('aria-pressed'", self.page)
        self.assertIn("function renderCapabilityReviewQueue(cap", self.page)
        self.assertIn("function unusedReviewGroups(cap)", self.page)
        self.assertIn("if(!cap)return;", self.page)
        self.assertIn("fetch('/capabilities/inventory'", self.page)
        self.assertIn("CAPABILITY_PAGE_SIZE=100", self.page)
        self.assertIn("id=c-mobile-sort", self.page)
        self.assertIn("function openCapabilityInventory(type='all')", self.page)
        self.assertIn("button.onclick=()=>openCapabilityInventory(button.dataset.capJump)", self.page)
        self.assertIn("function clearCapabilityFilter(key)", self.page)
        self.assertIn("row.measurement==='instruction'?'Instruction-only':'Evidence unavailable'", self.page)
        self.assertIn("return row.enabled?'Enabled':'Disabled'", self.page)
        self.assertIn("const selectedCapabilityIds=new Set()", self.page)
        self.assertIn("openSelectedDisableDialog", self.page)
        self.assertIn("capabilityRuntime='all'", self.page)
        self.assertIn("lastCapabilityReviewRevision", self.page)
        self.assertIn("cap.review_revision", self.page)
        self.assertIn("cap.inventory_revision", self.page)
        self.assertIn("setCapabilityAvailability('stale'", self.page)
        self.assertIn("data-label=\"Configuration\"", self.page)
        self.assertIn("data-label=\"Evidence\"", self.page)
        self.assertNotIn("Loaded ·", self.page)
        self.assertNotIn("Disable all unused", self.page)
        self.assertNotIn("if(row.unmeasurable) return '<span class=subline>not measurable</span>'", self.page)

    def test_fieldtips_are_portaled_and_viewport_bound(self):
        for marker in (
            "#fieldtip-popup{position:fixed",
            "function initFieldTipPopup()",
            "popup.setAttribute('role','tooltip')",
            "const maxLeft=Math.max(margin,window.innerWidth-margin-width)",
            "below+height<=window.innerHeight-margin||above<margin?below:above",
            "document.addEventListener('pointerover'",
            "window.addEventListener('scroll',scheduleFieldTipPosition,true)",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("content:attr(data-tip)", self.page)

    def test_skill_pack_changes_confirm_exact_control_and_use_verified_state(self):
        self.assertIn("id=cap-dialog", self.page)
        self.assertIn("control_id:controlId", self.page)
        self.assertIn("result.capabilities||cap", self.page)
        self.assertIn("Setting verified.", self.page)
        self.assertIn("row.reviewable!==false", self.page)
        self.assertNotIn("...group,id:group.item_id", self.page)

    def test_sessions_all_spend_learn_and_settings_are_first_class_routes(self):
        for marker in (
            "id=session-scope-tabs", "id=session-scope-current", "id=session-scope-all",
            "id=all-session-history", "id=tab-daily", "id=view-daily",
            "id=tab-learn", "id=view-learn", "id=tab-settings", "id=view-settings",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "id=tab-global", "id=view-global", "data-global-panel",
            "id=tab-budgets", "id=view-budgets", "id=tab-logs", "id=view-logs",
        ):
            self.assertNotIn(removed, self.page)
        self.assertIn("const legacyGlobal=h==='global'||h==='global-logs'", self.page)
        self.assertIn("if(h==='logs'||legacyGlobal)", self.page)
        self.assertIn("'/#sessions-all'", self.page)
        self.assertIn("function openSessionScope(scope", self.page)
        self.assertIn("function setSessionScope(scope)", self.page)
        self.assertIn('class="tabs sessionScopeTabs"', self.page)
        self.assertIn('<span class=tabLabel>Current sessions</span>', self.page)
        self.assertIn('<span class=tabLabel>All sessions</span>', self.page)
        self.assertIn("live · updated ${new Date(generatedAt*1000).toLocaleTimeString", self.page)
        self.assertLess(self.page.index("id=tab-session"), self.page.index("id=tab-daily"))
        self.assertIn("id=s-range", self.page)
        self.assertNotIn("id=learn-glossary", self.page)
        self.assertIn("Review loop", self.page)
        self.assertIn("if(h==='daily')setHashRoute('spend',{replace:true,apply:false})", self.page)
        self.assertIn("if(h==='spend'||h==='daily')", self.page)
        self.assertIn("if(h==='learn'||h==='learn-agent-access')", self.page)
        self.assertIn("h==='settings-budgets'||h==='budgets'", self.page)
        self.assertIn("if(h==='budgets')setHashRoute('settings-budgets'", self.page)
        self.assertIn("activeTop.scrollIntoView({block:'nearest',inline:'center'})", self.page)

    def test_spend_route_and_shell_replace_daily_brief(self):
        for marker in (
            "data-label=Spend aria-label=Spend",
            "<span class=tabLabel>Spend</span>",
            "<h1>Spend</h1>",
            "id=s-range", "data-spend-range=today", "data-spend-range=7",
            "data-spend-range=30", "data-spend-range=month",
            "data-spend-range=custom",
            "id=s-from", "id=s-to", "id=s-total", "id=s-average",
            "id=s-top-runtime", "id=s-highest-day", "id=s-chart",
            "id=s-chart-tip", "id=s-legend", "id=s-platforms",
            "if(h==='daily')setHashRoute('spend',{replace:true,apply:false})",
            "if(h==='spend'||h==='daily')",
            "openTopLevelRoute('spend')",
            "{id:'spend',label:'Spend'",
            "data-learn-route=spend>Open Spend",
            "function renderSpend(xs)",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "<h1>Daily brief</h1>", "id=d-day-select", "id=d-trend-mode",
            "data-daily-trend=wait", "id=d-sessions", "id=d-providers",
            "renderDaily(",
        ):
            self.assertNotIn(removed, self.page)

    def test_spend_fits_chart_and_restores_range_logs(self):
        for marker in (
            "class=spendEvidenceGrid",
            "Highest-cost logs",
            "id=s-log-day",
            "id=s-log-count",
            "id=s-logs",
            "function spendAxisLabel(",
            "function renderSpendLogs(payload,window)",
            "function loadSpendLogs(window,generatedAt)",
            "fetch(`/spend/logs?from=${encodeURIComponent(window.start)}&to=${encodeURIComponent(window.end)}`",
            "data-spend-session=",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            ".spendChart{min-height:270px;margin-top:12px;overflow:hidden",
            self.page,
        )

    def test_spend_evidence_cards_share_one_fixed_height(self):
        """Removing either card from the shared height contract breaks alignment."""
        for marker in (
            ".spendEvidenceGrid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);align-items:stretch",
            ".spendLogsCard,.spendPlatformCard{height:360px;display:flex;min-height:0;flex-direction:column",
            "class=spendPlatformBody id=s-platforms",
            ".spendPlatformBody{min-height:0;flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable",
        ):
            self.assertIn(marker, self.page)

    def test_spend_platform_split_keeps_amounts_inside_narrow_cards(self):
        """Restoring the old wide column minimums clips amounts at laptop width."""
        self.assertIn(
            ".spendPlatformRow{display:grid;grid-template-columns:minmax(78px,.48fr) minmax(72px,1fr) minmax(82px,auto);align-items:center;gap:10px",
            self.page,
        )
        self.assertIn(".spendPlatformValue{min-width:82px", self.page)

    def test_spend_hover_detail_is_transient_and_bars_stay_mounted(self):
        for marker in (
            ".spendChartTip{position:absolute;pointer-events:none",
            ".spendDay:hover .spendBarValue{opacity:1}",
            "function hideSpendTip()",
            "$('s-chart-inner').addEventListener('pointerleave',hideSpendTip)",
            "if(!same){",
            "updateSpendBar(button,rows[index],index,rows.length,axisMax,chartWidth)",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            ".spendDay:hover .spendBarValue,.spendDay:focus-visible .spendBarValue,.spendDay.selected .spendBarValue",
            self.page,
        )
        select_day = self.page.split("function selectSpendDay(day", 1)[1].split(
            "function renderSpend(xs)", 1,
        )[0]
        self.assertNotIn("showSpendTip", select_day)
        render_spend = self.page.split("function renderSpend(xs)", 1)[1].split(
            "function validateSpendCustomDraft", 1,
        )[0]
        self.assertIn(
            "const hovered=$('s-chart-inner').querySelector('.spendDay:hover');",
            render_spend,
        )
        self.assertIn(
            "if(hovered)showSpendTip(hovered.dataset.spendDay,hovered);else hideSpendTip();",
            render_spend,
        )

    def test_spend_chart_uses_roving_focus_without_persistent_day_selection(self):
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const rows = [{day:'2026-08-10'}, {day:'2026-08-11'}, {day:'2026-08-12'}];
console.log(JSON.stringify({
  initial: spendDayNavigation(rows, ''),
  moved: spendDayNavigation(rows, '2026-08-11'),
  empty: spendDayNavigation([], ''),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "initial": {
                "focusDay": "2026-08-12",
                "buttons": [
                    {"day": "2026-08-10", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-11", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-12", "tabIndex": 0, "selected": False},
                ],
            },
            "moved": {
                "focusDay": "2026-08-11",
                "buttons": [
                    {"day": "2026-08-10", "tabIndex": -1, "selected": False},
                    {"day": "2026-08-11", "tabIndex": 0, "selected": False},
                    {"day": "2026-08-12", "tabIndex": -1, "selected": False},
                ],
            },
            "empty": {"focusDay": "", "buttons": []},
        })

    def test_spend_y_axis_and_range_logs_are_bounded(self):
        for marker in (
            "id=s-y-axis",
            "function spendNiceMax(value)",
            "function renderSpendAxis(axisMax)",
            ".spendLogsCard,.spendPlatformCard{height:360px",
            "overflow-y:auto",
            "scrollbar-gutter:stable",
            ".spendLogRow{height:68px",
            ".spendLogValue{grid-column:2/4;grid-row:2",
            ".spendLogRow .tbtn{grid-column:3;grid-row:1",
            "$('s-log-count').textContent=`${f(rows.length)} logs`",
        ):
            self.assertIn(marker, self.page)
        render_logs = self.page.split("function renderSpendLogs(payload,window)", 1)[1].split(
            "function loadSpendLogs", 1,
        )[0]
        self.assertNotIn(".slice(", render_logs)

        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        result = subprocess.run(
            [
                "node", "-e",
                logic + "\nconsole.log(JSON.stringify([0,.7,18,189,200.00222974000008,458.253518,1000].map(spendNiceMax)));",
            ],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [1, 1, 20, 210, 220, 500, 1100])
        self.assertIn("flex:1 1 0;min-width:0", self.page)
        self.assertIn(
            ".spendDayLabel{position:absolute;bottom:0;left:50%;width:max-content",
            self.page,
        )
        self.assertIn(
            ".spendDay[data-axis-edge=last] .spendDayLabel{right:0;left:auto;transform:none}",
            self.page,
        )
        self.assertNotIn("inner.style.width=`max(100%", self.page)
        self.assertNotIn(
            ".spendChart{min-height:270px;margin-top:12px;overflow-x:auto",
            self.page,
        )

    def test_spend_average_reference_uses_every_calendar_day(self):
        """Dropping zero-spend days makes the chart line disagree with the KPI."""
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1,
        )[0]
        script = logic + """
const rows = [
  {day:'2026-08-01', cost:1},
  {day:'2026-08-02', cost:2},
  {day:'2026-08-03', cost:0},
  {day:'2026-08-04', cost:1},
];
console.log(JSON.stringify({
  reference: spendAverageReference(rows, 4),
  empty: spendAverageReference([], 4),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "reference": {"value": 1, "ratio": 0.25},
            "empty": {"value": 0, "ratio": 0},
        })

    def test_spend_uses_exact_calendar_ranges_and_stacked_runtime_bars(self):
        for marker in (
            "// spend-range-logic-start",
            "const SPEND_RUNTIME_COLORS={claude:'#f26722',codex:'#04a4b0',cursor:'#a974f7',opencode:'#fa5762',kiro:'#868ec2',unknown:'#889099'};",
            "function spendRangeWindow(range,from='',to='',now=new Date())",
            "function normalizeSpendRangeChoice(value)",
            "function spendCalendarRows(days,window)",
            "function spendRuntimeKey(provider)",
            "function spendRuntimeTotals(rows)",
            "class=spendStackSegment",
            "data-spend-day=",
            "function spendDayNavigation(rows,focusDay='')",
            "function showSpendTip(",
            "addEventListener('pointerover',handleSpendInspect)",
            "xs?.spend?.days||xs?.daily||[]",
        ):
            self.assertIn(marker, self.page)

        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const now = new Date(2026, 7, 12, 12, 0, 0);
const today = spendRangeWindow('today', '', '', now);
const seven = spendRangeWindow('7', '', '', now);
const thirty = spendRangeWindow('30', '', '', now);
const month = spendRangeWindow('month', '', '', now);
const monthFirst = spendRangeWindow('month', '', '', new Date(2026, 8, 1, 12, 0, 0));
const january = spendRangeWindow('month', '', '', new Date(2027, 0, 9, 12, 0, 0));
const custom = spendRangeWindow('custom', '2026-08-01', '2026-08-03', now);
const invalid = spendRangeWindow('custom', '2026-08-04', '2026-08-03', now);
const rows = spendCalendarRows([
  {day:'2026-08-03',cost:3,providers:[{provider:'claude',cost:3}]},
  {day:'2026-08-01',cost:2,providers:[{provider:'codex',cost:2}]},
], custom);
console.log(JSON.stringify({
  today, seven, thirty, month, monthFirst, january, custom, invalid,
  savedRanges: ['today','7','30','month','custom','unexpected'].map(normalizeSpendRangeChoice),
  rowDays: rows.map(row=>row.day),
  rowCosts: rows.map(row=>row.cost),
  keys: ['Claude Code','codex','Cursor IDE','OpenCode','Kiro CLI','other'].map(spendRuntimeKey),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["today"]["start"], "2026-08-12")
        self.assertEqual(payload["today"]["end"], "2026-08-12")
        self.assertEqual(payload["seven"]["start"], "2026-08-06")
        self.assertEqual(payload["seven"]["end"], "2026-08-12")
        self.assertEqual(payload["seven"]["dayCount"], 7)
        self.assertEqual(payload["thirty"]["start"], "2026-07-14")
        self.assertEqual(payload["thirty"]["dayCount"], 30)
        self.assertEqual(payload["month"], {
            "valid": True, "start": "2026-08-01", "end": "2026-08-12",
            "dayCount": 12, "error": "",
        })
        self.assertEqual(payload["monthFirst"], {
            "valid": True, "start": "2026-09-01", "end": "2026-09-01",
            "dayCount": 1, "error": "",
        })
        self.assertEqual(payload["january"], {
            "valid": True, "start": "2027-01-01", "end": "2027-01-09",
            "dayCount": 9, "error": "",
        })
        self.assertEqual(
            payload["savedRanges"],
            ["today", "7", "30", "month", "custom", "7"],
        )
        self.assertEqual(payload["custom"]["dayCount"], 3)
        self.assertFalse(payload["invalid"]["valid"])
        self.assertEqual(payload["rowDays"], [
            "2026-08-01", "2026-08-02", "2026-08-03",
        ])
        self.assertEqual(payload["rowCosts"], [2, 0, 3])
        self.assertEqual(payload["keys"], [
            "claude", "codex", "cursor", "opencode", "kiro", "unknown",
        ])

    def test_spend_session_analytics_excludes_unavailable_evidence(self):
        logic = self.page.split("// spend-range-logic-start", 1)[1].split(
            "// spend-range-logic-end", 1
        )[0]
        script = logic + """
const rows = [
  {id:'a', cost:40, input_tokens:1000, turns:1, duration_s:10,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'b', cost:30, input_tokens:2000, turns:2, duration_s:20,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'c', cost:20, input_tokens:3000, turns:3, duration_s:30,
   duration_available:true, availability:{cost:true,input_tokens:true}},
  {id:'d', cost:10, input_tokens:4000, turns:4, duration_s:999,
   duration_available:false, availability:{cost:true,input_tokens:true}},
  {id:'e', cost:99, input_tokens:5000, turns:5, duration_s:50,
   duration_available:true, availability:{cost:false,input_tokens:true}},
];
const analytics = spendSessionAnalytics(rows);
console.log(JSON.stringify({
  concentration: analytics.concentration,
  cost: analytics.distributions.cost,
  input: analytics.distributions.input,
  executions: analytics.distributions.executions,
  active: analytics.distributions.active,
  spendPoints: spendScatterRows(rows, 'cost').map(row => row.id),
  inputPoints: spendScatterRows(rows, 'input').map(row => row.id),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["concentration"], {
            "covered": 4, "total": 100, "top_count": 1,
            "top_cost": 40, "top_share": 0.4,
        })
        self.assertEqual(payload["cost"], {
            "count": 4, "p10": 13, "p25": 17.5, "median": 25,
            "mean": 25, "p75": 32.5, "p90": 37,
        })
        self.assertEqual(payload["input"], {
            "count": 5, "p10": 1400, "p25": 2000, "median": 3000,
            "mean": 3000, "p75": 4000, "p90": 4600,
        })
        self.assertEqual(payload["executions"], {
            "count": 5, "p10": 1.4, "p25": 2, "median": 3,
            "mean": 3, "p75": 4, "p90": 4.6,
        })
        self.assertEqual(payload["active"], {
            "count": 4, "p10": 13, "p25": 17.5, "median": 25,
            "mean": 27.5, "p75": 35, "p90": 44,
        })
        self.assertEqual(payload["spendPoints"], ["a", "b", "c"])
        self.assertEqual(payload["inputPoints"], ["a", "b", "c", "e"])

    def test_spend_route_exposes_concentration_shape_and_openable_outliers(self):
        spend = self.page.split("id=view-daily", 1)[1].split("id=view-learn", 1)[0]
        for marker in (
            "id=s-concentration", "id=s-concentration-bar", "id=s-shape",
            "id=s-scatter", "id=s-scatter-svg", "data-scatter-metric=cost",
            "data-scatter-metric=input", "data-spend-insight-session=",
        ):
            self.assertIn(marker, spend if marker.startswith("id=s-") or marker.startswith("data-scatter") else self.page)
        self.assertIn("function renderSpendSessionInsights(payload,window)", self.page)
        self.assertIn("selectSession(button.dataset.spendInsightSession)", self.page)

    def test_non_current_views_keep_visible_copy_terse(self):
        expected_subtitles = {
            "models": ["Cost, speed, and context."],
            "learn": ["The Token Meter review loop."],
            "capabilities": ["Tools, MCP servers, and skills."],
            "settings": ["Budgets, connections, pricing, and updates."],
        }
        boundaries = (
            ("models", "daily"),
            ("learn", "capabilities"),
            ("capabilities", "settings"),
            ("settings", None),
        )
        for view, next_view in boundaries:
            section = self.page.split(f"id=view-{view}", 1)[1]
            section = section.split(
                f"id=view-{next_view}" if next_view else "<dialog class=onboardingDialog",
                1,
            )[0]
            paragraphs = [
                re.sub(r"<[^>]+>", "", value).strip()
                for value in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", section, re.S)
            ]
            self.assertEqual(
                [value for value in paragraphs if value],
                expected_subtitles[view],
                f"{view} should carry only its concise page subtitle",
            )
            self.assertNotIn("class=foot", section)

        spend = self.page.split("id=view-daily", 1)[1].split("id=view-learn", 1)[0]
        self.assertIn("Estimated agent spend over time.", spend)
        self.assertIn("Daily spend by platform · line shows average.", spend)
        self.assertNotIn("class=foot", spend)

        for marker in (
            'aria-description="Observed model output divided by attributable timing.',
            'aria-description="Track configured positive and friction phrases',
            'aria-description="Set a budget for each runtime',
            'aria-description="Give Codex and Claude read-only, on-demand access',
            "#fieldtip-popup{position:fixed",
            "No use observed",
        ):
            self.assertIn(marker, self.page)

    def test_models_and_git_keep_secondary_copy_in_accessible_help(self):
        models = self.page.split("id=view-models", 1)[1].split("id=view-daily", 1)[0]
        for marker in (
            "id=m-change-label", "id=m-wait-label", "id=m-input-label",
            "id=m-output-label", "function setModelKpiHelp(labelId,detail)",
            "setModelKpiHelp('m-speed-label',speedDetail)",
            "setModelKpiHelp('m-wait-label',waitDetail)",
            "setModelKpiHelp('m-input-label',inputDetail)",
            "setModelKpiHelp('m-output-label',outputDetail)",
            ".modelCellTip.fieldtip{display:table-cell}",
            ".modelCellTip>span{display:none}",
            "querySelectorAll('.workloadCell,.speedCell,.waitCell')",
            "cell.classList.add('fieldtip','modelCellTip')",
            "cell.setAttribute('aria-description',detail)",
            "cell.dataset.tip=detail",
            "cell.removeAttribute('title')",
        ):
            self.assertIn(marker, self.page)

        git = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        hover_copy = (
            "Period-specific spend and Git coverage; unavailable is not zero.",
            "Added + deleted text lines from successful pushes. Select a day for spend details.",
            "Conversion, typical days, and outliers. Statistics only · no quality judgment.",
            "Ranked local Git and covered-spend observations.",
            "Median, range, and high day · ratios require 50+ pushed lines.",
            "Log-scaled daily evidence · diagonal shows average Spend / 1K.",
            "Low-volume ratios are context only and excluded from distributions.",
        )
        for copy in hover_copy:
            self.assertIn(f'aria-description="{copy}"', git)
            self.assertIn(f'data-tip="{copy}"', git)
            self.assertNotIn(f"<p>{copy}</p>", git)
        self.assertIn(
            "Local Git evidence &middot; Text changes only &middot; Not a quality score.",
            git,
        )
        self.assertIn('class="fieldtip" id=d-evidence-ratio tabindex=0', git)
        self.assertIn("function setDeliveryEvidenceRatio(text,detail)", self.page)
        self.assertIn(
            "setDeliveryEvidenceRatio(selected?`${comparable} / ${selected} comparable`",
            self.page,
        )
        self.assertIn("ratio.dataset.tip=detail", self.page)

    def test_settings_monthly_budget_derives_total_from_runtime_budgets(self):
        for marker in (
            "id=budget-settings", "data-settings-target=budget-settings",
            "id=budget-spend", "id=budget-total", "id=budget-remaining",
            "id=budget-projected", "id=budget-runtimes", "id=budget-bars",
            "id=budget-progress-markers", "id=budget-allocation-note",
            "id=budget-plan-jump",
            "class=budgetDashboard", "class=budgetLeadHeadActions",
            "class=\"card pad budgetLead\"",
            "class=\"card pad budgetRuntimeCard budgetConfig\"",
            "class=budgetLeadBody", "class=budgetDetailGrid",
            "class=budgetSpendSummary", "class=budgetSpendLimit",
            "class=budgetReadouts", "class=budgetRuntimeValue",
            "class=budgetRuntimeTrack",
            "class=budgetFormGroup", "class=budgetAlertInline",
            "class=\"budgetForm budgetInlineForm\"",
            "class=budgetRuntimeHead", "class=budgetRuntimeInput",
            "class=budgetChartInner", "class=budgetTarget",
            "id=budget-input-claude", "id=budget-input-codex",
            "id=budget-input-cursor", "id=budget-input-opencode",
            "id=budget-input-threshold-early",
            "id=budget-runtime-spend-claude",
            "id=budget-runtime-spend-codex",
            "id=budget-runtime-spend-cursor",
            "id=budget-runtime-spend-opencode",
            "id=budget-runtime-track-claude",
            "id=budget-runtime-track-opencode",
            "/settings/budgets", "tm_monthly_budget_alerts",
            "Partial cost coverage: recorded spend is a lower bound.",
            "Calculated budget",
            "The sum of the registered runtime budgets.",
            "Runtime budgets are added to calculate the monthly total.",
            "function ensureBudgetRuntimeRows()",
            "planJump.textContent=configured?'Edit budgets':'Set budgets'",
            "config.scrollIntoView({behavior:",
            "const config=$('budget-config'),input=config.querySelector('.budgetRuntimeInput input')",
            "input.focus({preventScroll:true})",
            "function budgetAllocationsFromInputs()",
            "function previewCalculatedBudget()",
            "const payload={currency:'USD',default_session_budget:",
            "per session by default · ${money(total)} per month.",
            "Save budgets",
            "Set budgets</h2>",
            "Spent this month</span><span>Budget (USD)",
            "@media(min-width:901px){.budgetDetailGrid{align-items:stretch}",
            ".budgetHistory .budgetChartInner{flex:1;display:grid",
            "DEFAULT_RUNTIME_BUDGET=0",
            "value=0",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("class=budgetHero", self.page)
        self.assertNotIn("class=budgetGrid", self.page)
        self.assertNotIn("budgetConfigInitialized", self.page)
        self.assertNotIn("<details class=\"card budgetConfig\"", self.page)
        self.assertNotIn("<h2>Budget plan</h2>", self.page)
        self.assertNotIn("<span class=budgetRuntimeName>Unallocated</span>", self.page)
        self.assertNotIn("$('budget-runtimes').innerHTML", self.page)
        self.assertNotIn("id=tab-budgets", self.page)
        self.assertNotIn("id=view-budgets", self.page)
        self.assertNotIn("no runtime allocation", self.page)
        self.assertNotIn("id=budget-input-total", self.page)
        self.assertNotIn("Monthly total (USD)", self.page)
        self.assertNotIn("Runtime allocations cannot exceed", self.page)
        self.assertNotIn("budget-runtime-meta-", self.page)
        self.assertNotIn("const meta=allocated?", self.page)
        self.assertEqual(self.page.count(" id=budget-config>"), 1)
        settings = self.page[self.page.index("id=view-settings"):]
        self.assertLess(settings.index("class=budgetDetailGrid"), settings.index("id=budget-config"))
        self.assertLess(settings.index(">Monthly spend</h2>"), settings.index("id=budget-config"))
        self.assertLess(settings.index("id=budget-settings"), settings.index("id=agent-access"))

    def test_software_updates_keep_checks_and_install_preferences_independent(self):
        for marker in (
            "data-settings-target=update-settings",
            "settings-updates",
            "id=update-settings",
            "id=update-enabled",
            "id=update-enabled type=checkbox checked",
            "id=update-auto-install type=checkbox checked",
            "Check for updates every 10 minutes",
            "Automatically install available updates",
            "Enabled by default",
            "id=update-check",
            "id=update-notice",
            "New update available",
            "/settings/updates",
            "/updates/status",
            "/updates/check",
            "/updates/install",
            "setInterval(refreshSoftwareUpdateStatus,60000)",
            "status.available&&status.can_update",
            "softwareUpdateTarget=SOFTWARE_UPDATE.latest_revision",
            "softwareUpdateTarget&&state==='attention'",
            "setTimeout(()=>location.reload(),350)",
            "Checks fetch revision metadata only",
            "const autoInstall=enabled&&$('update-auto-install').checked",
            "{enabled,auto_install:autoInstall}",
            "$('update-auto-install').disabled=!enabled",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(".updateNotice{position:fixed;right:18px;bottom:18px", self.page)
        self.assertIn(".softwareUpdates{display:grid;gap:9px;padding:12px 16px!important}", self.page)
        self.assertIn(
            ".softwareUpdateActions{display:grid;grid-template-columns:auto minmax(0,1fr) auto",
            self.page,
        )
        self.assertIn(
            "<span class=softwareUpdateMeta id=update-settings-meta></span>",
            self.page,
        )
        self.assertIn(".softwareUpdateActions .tbtn:disabled{opacity:.45;cursor:not-allowed}", self.page)
        self.assertIn("body.classList.toggle('updateReady',showNotice)", self.page)
        self.assertNotIn("setInterval(()=>postSoftwareUpdate('/updates/install'", self.page)
        self.assertIn("if(h==='settings-updates')requestAnimationFrame(()=>$('update-settings').scrollIntoView({block:'start'}))", self.page)
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertLess(
            settings.index("data-settings-target=frustration-settings"),
            settings.index("data-settings-target=update-settings"),
        )
        self.assertLess(
            settings.index("id=frustration-settings"),
            settings.index("id=update-settings"),
        )

    def test_settings_sections_use_matching_titles_and_compact_supporting_copy(self):
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        for title, target in (
            ("Monthly budget", "budget-settings"),
            ("Agent connection", "agent-access"),
            ("Model pricing", "model-pricing-settings"),
            ("Language signals", "frustration-settings"),
            ("Software updates", "update-settings"),
        ):
            self.assertIn(
                f"data-settings-target={target}><b>{title}</b>", settings,
            )
            self.assertIn(f">{title}</h2>", settings)
        for removed in (
            "id=agent-access-badge", "class=mcpMark", "class=agentPromise",
            "class=agentTools", "class=agentPrivacy", "id=frustration-term-count",
            "<div class=softwareUpdatePromise",
        ):
            self.assertNotIn(removed, settings)
        self.assertNotIn('href="#learn-agent-access"', settings)
        self.assertIn("id=learn-agent-access", self.page)
        self.assertIn("Connections stay local and read-only.", settings)
        self.assertIn('aria-describedby=positive-terms-count', settings)
        self.assertIn('aria-describedby=frustration-terms-count', settings)
        self.assertIn('id=positive-terms-count>0 of 64 phrases · 64 remaining', settings)
        self.assertIn('id=frustration-terms-count>0 of 64 phrases · 64 remaining', settings)
        language = settings.split("id=frustration-settings", 1)[1].split(
            "id=update-settings", 1
        )[0]
        self.assertNotIn("Machine-wide", language)
        self.assertIn(
            ".settingsMap:before,.agentAccess:before,.budgetLead:before{display:none}",
            self.page,
        )

    def test_current_surfaces_runtime_budget_overruns(self):
        for marker in (
            "id=current-budget-warning",
            "id=current-budget-warning-msg",
            "id=current-budget-settings",
            "Open budget settings",
            "function renderCurrentBudgetWarning(status)",
            "status?.exceeded_runtimes||[]",
            "renderCurrentBudgetWarning(s.xsession?.budget||LATEST?.xsession?.budget)",
            "runtimeAttention?`${runtimeExceeded.map(row=>row.label).join(', ')} over budget`",
            "row.exceeded===true",
        ):
            self.assertIn(marker, self.page)

    def test_primary_navigation_and_command_palette_share_the_same_workflow_order(self):
        tab_ids = [
            "tab-session", "tab-daily", "tab-models",
            "tab-efficiency", "tab-git", "tab-learn", "tab-capabilities", "tab-settings",
        ]
        positions = [self.page.index(f"id={tab_id}") for tab_id in tab_ids]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "id=command-palette", "id=command-search",
            "const NAV_COMMANDS=[", "directKey:'Digit1'", "directKey:'Digit8'",
            "key==='k'", "event.key==='Escape'", "event.key==='ArrowDown'",
            "event.key==='Enter'",
            "class=tabs aria-label=\"Primary navigation\"",
            "button.dataset.label} · Shortcut: Option+${shortcut}",
            "button.setAttribute('aria-current','page')",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(command.latest)goToLatestSession()", self.page)
        self.assertIn("action:'selected-session'", self.page)
        self.assertIn("function openSelectedSessionRoute(route='summary')", self.page)
        self.assertIn("history.pushState({sessionScope:sessionReturnScope},'',sessionRoute(pinned))", self.page)
        self.assertIn("if(command.action==='selected-session'){openSelectedSessionRoute(command.route);return;}", self.page)
        self.assertIn("else openTopLevelRoute(command.route)", self.page)
        self.assertNotIn("shortcut:'⌥", self.page)
        self.assertNotIn("id=command-trigger", self.page)
        self.assertNotIn("Local evidence", self.page)
        self.assertNotIn("class=pulse", self.page)
        self.assertNotIn("@keyframes p{", self.page)
        self.assertNotIn("id=live", self.page)
        self.assertNotIn("id=livetxt", self.page)
        self.assertNotIn("$('live')", self.page)
        self.assertNotIn("$('livetxt')", self.page)
        self.assertNotIn("id=command-alt-key", self.page)
        self.assertNotIn("class=commandShortcut", self.page)

    def test_top_level_shortcuts_follow_visible_rail_order(self):
        expected = [
            ("session", "1"), ("daily", "2"), ("models", "3"),
            ("efficiency", "4"), ("git", "5"), ("learn", "6"),
            ("capabilities", "7"), ("settings", "8"),
        ]
        for tab_id, digit in expected:
            match = re.search(rf'<button[^>]+id=tab-{tab_id}[^>]*>.*?</button>', self.page)
            self.assertIsNotNone(match, tab_id)
            button = match.group(0)
            self.assertIn(f'aria-keyshortcuts="Alt+{digit}"', button)
            self.assertIn(f'Shortcut: Option+{digit}', button)
            self.assertIn(f'<span class=tabShortcut aria-hidden=true>{digit}</span>', button)
        commands = self.page.split("const NAV_COMMANDS=[", 1)[1].split("];", 1)[0]
        for command_id, digit in (
            ("sessions", "1"), ("spend", "2"), ("models", "3"),
            ("efficiency", "4"), ("git", "5"), ("learn", "6"),
            ("capabilities", "7"), ("settings", "8"),
        ):
            self.assertRegex(
                commands,
                rf"id:'{command_id}'.*?directKey:'Digit{digit}'.*?glyph:'{digit}'",
            )
        self.assertIn('&#8997;1&ndash;8', self.page)

    def test_tools_is_in_the_secondary_rail_above_settings(self):
        secondary = self.page.split("<div class=navSecondary>", 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("id=tab-capabilities", secondary)
        self.assertLess(secondary.index("id=tab-learn"), secondary.index("id=tab-capabilities"))
        self.assertLess(secondary.index("id=tab-capabilities"), secondary.index("id=tab-settings"))

    def test_current_onboarding_uses_six_closeable_teaching_lessons(self):
        current = self.page.split('<div class="view on" id=view-session>', 1)[1].split(
            '<div class=view id=view-models>', 1
        )[0]
        current_overview = current.split(
            '<section id=current-sessions-overview', 1
        )[1].split('<section id=all-session-history', 1)[0]
        self.assertIn("id=onboarding-card", current_overview)
        self.assertLess(current_overview.index("id=onboarding-card"),
                        current_overview.index("id=current-session-grid"))
        self.assertNotIn("id=onboarding-card aria-labelledby=onboarding-title aria-live=polite data-current-detail",
                         current)
        self.assertLess(current.index("id=onboarding-card"),
                        current.index("class=previewRunMeta"))
        for marker in (
            "id=onboarding-card", "id=onboarding-toggle",
            "id=onboarding-progress", "id=onboarding-next",
            "id=onboarding-checklist", "aria-valuemax=6",
            "id=learn-onboarding-status", "id=learn-onboarding-action",
            "id=onboarding-dialog", "id=onboarding-dialog-title",
            "id=onboarding-dialog-points", "id=onboarding-dialog-close",
            "Closing this lesson marks the step complete",
            "id=command-coach", "id=command-coach-done",
            "Close it when you are done; no command is required",
            "class=learnShortcut",
            "Command palette",
            "Switch views",
        ):
            self.assertIn(marker, self.page)
        steps = self.page.split("const ONBOARDING_STEPS=[", 1)[1].split(
            "const ONBOARDING_STEP_IDS", 1
        )[0]
        self.assertEqual(steps.count("id:'"), 6)
        self.assertEqual(steps.count("lesson:'"), 6)
        self.assertEqual(steps.count("points:["), 6)
        for step_id in (
            "current", "logs", "daily", "models", "capabilities", "palette",
        ):
            self.assertIn(f"id:'{step_id}'", steps)
        self.assertNotIn("id:'activity'", steps)
        self.assertNotIn("short:'Activity'", steps)
        self.assertNotIn("route:'activity'", steps)
        self.assertIn("short:'Models'", steps)
        self.assertIn("short:'Tools'", steps)
        self.assertIn("route:'models'", steps)
        self.assertIn("route:'capabilities'", steps)
        for marker in (
            "const ONBOARDING_KEY='tm_onboarding_v1'",
            "raw.completed.filter(id=>ONBOARDING_STEP_IDS.has(id))",
            "card.hidden=complete",
            "onboardingState.collapsed&&!complete",
            "completed_at:onboardingState.completedAt||0",
            "function resumeOrRestartOnboarding()",
            "complete?'Replay onboarding':'Resume onboarding'",
            "action:'onboarding'",
            "command.action==='onboarding'",
            "function runNavigationCommand(command,{source='palette'}={})",
            "function openOnboardingLesson(id)",
            "function finishOnboardingLesson()",
            "if(id)commitOnboardingSteps([id]);",
            "setTimeout(()=>openOnboardingLesson(step.id),0)",
            "if(lessonDialog.open&&event.key==='Escape')",
            "if(onboardingNextStep()?.id==='palette')onboardingPaletteLessonArmed=true",
            "const finishPaletteLesson=onboardingPaletteLessonArmed",
            "if(finishPaletteLesson)commitOnboardingSteps(['palette'])",
            "runNavigationCommand(command,{source:'shortcut'})",
        ):
            self.assertIn(marker, self.page)
        resume = self.page.split("function resumeOrRestartOnboarding(){", 1)[1].split(
            "$('onboarding-toggle').onclick", 1
        )[0]
        self.assertIn("openCurrentSessions();", resume)
        self.assertNotIn("goToLatestSession();", resume)
        self.assertIn("The six-step guide is back in Current sessions.", resume)
        self.assertNotIn("const teachPalette=", self.page)
        self.assertNotIn("openOnboardingLesson('palette')", self.page)
        self.assertNotIn("function markOnboardingRoute(", self.page)
        self.assertNotIn("function onboardingStepForRoute(", self.page)
        self.assertNotIn("commitOnboardingSteps([step.id]);", self.page)
        self.assertNotIn("onboarding-dismiss", self.page)
        self.assertNotIn("Dismiss onboarding", self.page)

    def test_all_sessions_supports_app_project_and_time_range_filters(self):
        for marker in ("id=g-app", "id=g-project", "id=g-time", "App filter",
                       "Projects filter", "Time range filter", "class=logsToolbarStatus", "id=g-active-filters",
                       "id=g-clear", "id=lf-cost", "id=lf-input", "id=lf-output", "id=lf-models"):
            self.assertIn(marker, self.page)
        clear_wrapper = re.search(r"<div class=filterClear>(.*?)</div>", self.page)
        self.assertIsNotNone(clear_wrapper)
        self.assertIn("id=g-clear", clear_wrapper.group(1))
        self.assertNotIn("id=g-count", clear_wrapper.group(1))
        self.assertLess(self.page.index("id=g-clear"), self.page.index("id=g-sort"))
        for value in ("value=24h", "value=7d", "value=30d", "value=90d"):
            self.assertIn(value, self.page)
        self.assertIn("globalApp&&appFilterGroup(s)!==globalApp", self.page)
        self.assertIn("const appFilterGroup=session=>runtimeId(session)", self.page)
        self.assertIn("const appFilterLabel=session=>runtimeMeta(session).label", self.page)
        self.assertIn("['claude_code','claude_desktop'].includes(globalApp)", self.page)
        self.assertIn("globalProject&&projectFilterValue(s.project)!==globalProject", self.page)
        self.assertIn("Other local sessions", self.page)
        self.assertIn("Date.now()/1000-rangeSeconds", self.page)
        self.assertIn("tm_global_app", self.page)
        self.assertIn("tm_global_project", self.page)
        self.assertIn("tm_global_time", self.page)
        self.assertIn("renderAllSessionStats(sessions)", self.page)
        self.assertIn("session.model_stats", self.page)
        self.assertIn("globalSearch='';globalApp='';globalProject='';globalTime='all'", self.page)
        self.assertIn("['tm_global_search','tm_global_app','tm_global_project','tm_global_time']", self.page)
        self.assertIn("fetch('/logs',{cache:'no-store'})", self.page)
        self.assertIn("allSessionInventory?mergeAllSessionInventory(allSessionInventory,xs.sessions)", self.page)

    def test_all_sessions_live_updates_preserve_mounted_rows(self):
        for marker in (
            "const logHtmlCache=new WeakMap()",
            "function setLogText(element,value)",
            "function setLogAttribute(element,name,value)",
            "function setLogHtml(element,html)",
            "function logRowRenderKey(s,active)",
            "function reconcileLogRows(sessions,maxCost,liveSessionIds=new Set())",
            "const interactingLogRow=forceAllSessionRowRefresh?null:root.querySelector('.srow:hover,.srow:focus-within');",
            "if(interactingLogRow)return;",
            "function mergeAllSessionInventory(inventory,liveSessions)",
            "(liveSessions||[]).forEach(row=>rows.set(String(row.id),row))",
            "const liveSessionIds=new Set((xs.current_sessions||[]).map(session=>String(session.id||'')));",
            "const live=liveSessionIds.has(id)",
            "const className=`srow${active?' active':''}${live?' live':''}`",
            ".srow.active,.srow.live{border-color:rgba(0,188,235,.62)",
            "const existing=new Map([...root.children]",
            "if(row.className!==className)row.className=className",
            "if(row.getAttribute('aria-label')!==ariaLabel)",
            "if(row._logRenderKey!==renderKey)",
            "else root.insertBefore(row,cursor)",
            "reconcileLogRows(sessions,maxCost,liveSessionIds)",
            "$('slist').onclick=event=>",
            "$('slist').onkeydown=event=>",
            "const inventoryStale=!allSessionInventory",
            "if(inventoryStale)loadAllSessions()",
            "else renderAllSessions(LATEST.xsession)",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            "body.spectrumApp.sessionRoute #view-session .srow:hover{transform:none}",
            self.page,
        )
        self.assertNotIn("$('slist').innerHTML=sessions.length?sessions.map", self.page)

    def test_current_and_all_sessions_share_the_defined_app_badge_helper(self):
        self.assertIn("const appBadgeClass=session=>", self.page)
        self.assertIn("'badge app '+appBadgeClass(s)", self.page)
        self.assertIn("${appBadgeClass(s)}", self.page)
        self.assertNotIn("providerBadgeClass", self.page)

    def test_current_sessions_keep_opencode_identity(self):
        self.assertIn(
            "const provider=runtimeId(row)",
            self.page,
        )
        self.assertIn('class="card currentSessionCard provider-${esc(provider)}', self.page)
        self.assertIn("Object.entries(RUNTIME_CATALOG).filter(([id])=>id!=='unknown-runtime')", self.page)

    def test_session_delete_actions_require_confirmation_and_use_trash_endpoint(self):
        for marker in ("id=session-delete", "data-delete-session", "id=session-delete-dialog",
                       "id=session-delete-confirm", "Move to Trash", "/session/delete"):
            self.assertIn(marker, self.page)
        self.assertIn("openSessionDeleteDialog", self.page)
        self.assertIn("event.stopPropagation()", self.page)
        self.assertIn("X-Token-Meter-Action", self.page)
        self.assertIn("Provider metadata and configuration are not changed", self.page)
        self.assertIn("function sessionDeleteAvailable(target,actions)", self.page)
        self.assertIn("actions?.read_only_providers", self.page)
        self.assertIn("!sessionDeleteAvailable(target,actions)", self.page)

    def test_selected_candidate_action_has_confirmation_and_exact_control_ids(self):
        self.assertIn("id=c-disable-selected", self.page)
        self.assertIn("id=bulk-dialog", self.page)
        self.assertIn("/capability/disable-unused", self.page)
        self.assertIn('data-cap-select="${esc(row.id)}"', self.page)
        self.assertIn("selectedCapabilityIds.add(input.dataset.capSelect)", self.page)
        self.assertIn("unusedReviewGroups(cap).filter(row=>selectedCapabilityIds.has(row.id))", self.page)
        self.assertIn("pendingBulkDisable={controlIds,fingerprints:", self.page)
        self.assertIn("capabilityGroupFingerprint(row)", self.page)
        self.assertIn("$('bulk-dialog-controls').textContent=controlIds.join('\\n')", self.page)
        self.assertIn("control_ids:controlIds", self.page)
        self.assertIn("Review evidence changed", self.page)
        self.assertIn('aria-label="Select ${esc(capabilityPackName(row))} pack"', self.page)
        self.assertIn("MCP servers, runtime packs, built-ins, standalone skills, and used groups are excluded.", self.page)
        self.assertIn("Disable selected", self.page)
        self.assertNotIn("id=c-disable-unused", self.page)

    def test_capability_polling_uses_stable_revisions_and_lazy_inventory(self):
        review = self.page.split("function renderCapabilityReviewQueue", 1)[1].split(
            "function renderCapabilityTable", 1
        )[0]
        inventory = self.page.split("function renderCapabilityTable", 1)[1].split(
            "let pendingCapabilityAction", 1
        )[0]
        self.assertIn("revision===lastCapabilityReviewRevision", review)
        self.assertIn("lastCapabilityReviewRevision=revision", review)
        self.assertIn("if(!cap)return;", inventory)
        self.assertNotIn("c-inventory-details", inventory)
        self.assertNotIn("$('c-inventory-details').addEventListener('toggle'", self.page)
        self.assertIn("fetch('/capabilities/inventory',{cache:'no-store'})", self.page)
        self.assertIn("countWord(inventoryCount,'capability','capabilities')", self.page)
        self.assertIn("capabilityInventorySnapshot.inventory_revision===cap.inventory_revision", self.page)
        self.assertIn("renderCapabilityTable(merged,{force});", self.page)
        self.assertIn("if(inventory)renderCapabilityTable(inventory,{force:true});", self.page)
        self.assertIn("capabilityLastGoodAt=Date.now()", self.page)
        self.assertIn("setCapabilityAvailability('unavailable'", self.page)
        self.assertIn("setCapabilityAvailability('stale'", self.page)
        self.assertIn("capabilityEvidenceState!=='current'", self.page)
        self.assertIn("Confirmation closed", self.page)
        self.assertIn("if(capabilitySort==='returned')capabilitySort='use'", self.page)

    def test_agent_access_has_a_dedicated_settings_tab(self):
        for marker in ("id=agent-discovery", "id=agent-access", "id=agent-clients",
                       "id=agent-dialog", "/agent-access/status", "/agent-access/toggle",
                       "class=\"card settingsMap\"", "class=settingsSignalGrid"):
            self.assertIn(marker, self.page)
        self.assertIn("id=learn-agent-access", self.page)
        self.assertIn("No prompts, messages, reasoning, tool content", self.page)
        for tool in ("mcp__tokenmeter__check", "mcp__tokenmeter__usage",
                     "mcp__tokenmeter__capabilities"):
            self.assertNotIn(tool, self.page)
        self.assertLess(self.page.index("id=view-capabilities"), self.page.index("id=view-settings"))
        self.assertLess(self.page.index("id=view-settings"), self.page.index("id=agent-access"))
        self.assertIn("setHashRoute('settings')", self.page)
        self.assertIn("tm_agent_discovery_dismissed", self.page)
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertLess(settings.index("data-settings-target=agent-access"),
                        settings.index("data-settings-target=model-pricing-settings"))
        self.assertLess(settings.index("data-settings-target=model-pricing-settings"),
                        settings.index("data-settings-target=frustration-settings"))
        self.assertLess(settings.index("id=agent-access"),
                        settings.index("id=model-pricing-settings"))
        self.assertLess(settings.index("id=model-pricing-settings"),
                        settings.index("id=frustration-settings"))

    def test_settings_has_no_agent_defaults_surface_or_loader(self):
        settings = self.page.split("<div class=view id=view-settings>", 1)[1].split(
            "</div>\n</div>\n<dialog class=commandPalette", 1
        )[0]
        self.assertNotIn("id=agent-default-settings", settings)
        self.assertNotIn("id=agent-defaults", settings)
        self.assertNotIn("function renderAgentDefaults", self.page)
        self.assertNotIn("function loadAgentDefaultSettings", self.page)
        self.assertNotIn("/settings/agent-defaults", self.page)

    def test_agent_access_status_loads_only_when_settings_is_opened(self):
        startup = self.page[self.page.rindex(
            "setInterval(refreshLiveState,LIVE_STATE_POLL_MS);"
        ):]
        self.assertNotIn("loadAgentAccess();", startup)
        settings_route = self.page.split(
            "if(h==='settings'||h==='model-pricing'", 1
        )[1].split("if(!h||h==='session')", 1)[0]
        self.assertIn("loadAgentAccess();", settings_route)

    def test_token_chart_fills_use_the_same_colors_as_legend_and_tooltip(self):
        self.assertIn("const band=(top,bottom,col,opacity)=>", self.page)
        self.assertIn(
            "band(cacheRead,zeros,CHART.cacheRead,.18)+"
            "band(cacheTop,cacheRead,CHART.cacheWrite,.18)",
            self.page,
        )
        self.assertIn('fill-opacity="${opacity}"', self.page)
        self.assertNotIn("cacheReadFill", self.page)
        self.assertNotIn("cacheWriteFill", self.page)

    def test_agent_access_conflict_has_an_explicit_repair_flow(self):
        for marker in (
            "row.conflict?'Repair':'Connect'",
            "const repairing=!!(enabled&&row.conflict)",
            "Replace only the existing user-level tokenmeter entry",
            "`${row.disconnect_command}\\n${row.connect_command}`",
            "repair:repairing",
            "result.repaired?'Connection repaired'",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("Resolve manually", self.page)
        self.assertNotIn("||row.conflict", self.page[self.page.index("const disabled="):self.page.index("const detail=", self.page.index("const disabled="))])

    def test_learn_omits_removed_model_and_agent_guidance(self):
        for marker in (
            "id=learn-model-guide",
            "Compare model speed without fooling yourself",
            "Ask your agent",
            "Should I keep this run going?",
            "Why was the last phase expensive?",
            "What should I change before the next phase?",
        ):
            self.assertNotIn(marker, self.page)

    def test_model_comparison_has_tooltips(self):
        for marker in (
            "modelHelp", "id=m-coverage", 'aria-label="Output timing coverage"',
            "same model can appear more than once",
            "A ratio above 1 favors the named faster runtime",
            "The median is primary because the average can be pulled upward",
            "95% confidence interval", "Matched pace",
            "model runtime", "Observed output pace", "Typical workload",
            "Typical wait", "semantic difficulty",
            "The 95% confidence interval crosses 1.00",
            "$('m-coverage').setAttribute('aria-valuenow'",
            "stripNativeFieldTipTitles", "removeAttribute('title')",
            "aria-description",
        ):
            self.assertIn(marker, self.page)
        self.assertIn('<div class=filterControl><span>Models</span>', self.page)
        self.assertIn('<label class=filterControl><span>History</span>', self.page)
        self.assertNotIn("Select model-runtime histories, not just model names.", self.page)
        self.assertNotIn("Limits tokens, executions, workload summaries", self.page)

    def test_custom_fieldtips_remove_delayed_native_tooltips_globally(self):
        self.assertIn("root.querySelectorAll('.fieldtip[title]')", self.page)
        self.assertIn("stripNativeFieldTipTitles();", self.page)
        self.assertIn('return `class=fieldtip tabindex=0 aria-description="${safe}" data-tip="${safe}"`', self.page)
        self.assertNotIn('return `class=fieldtip tabindex=0 title="${safe}"', self.page)

    def test_spend_uses_session_routing_without_restoring_daily_wait_health(self):
        self.assertNotIn("Tool-result health", self.page)
        self.assertNotIn("Tool results need attention", self.page)
        self.assertNotIn("function openDailySession", self.page)
        self.assertNotIn("data-daily-session", self.page)
        self.assertIn("function renderSpend(xs)", self.page)
        self.assertIn("data-spend-session=", self.page)
        self.assertIn("if(row)selectSession(row.dataset.id)", self.page)

    def test_selected_session_stays_pinned_while_other_live_state_changes(self):
        self.assertIn("function followLatestSession", self.page)
        self.assertIn("function refreshSelectedSession", self.page)
        self.assertIn("encodeURIComponent(id)+'&live=1'", self.page)
        self.assertLess(
            self.page.index("selectedPollId=''"),
            self.page.index("applyLocationRoute();"),
        )
        self.assertIn("const SELECTED_SESSION_POLL_MS=2000", self.page)
        self.assertIn(
            "setInterval(()=>refreshSelectedSession({show:true}),SELECTED_SESSION_POLL_MS)",
            self.page,
        )
        self.assertIn("pinned=id;\n if(show)applyHashRoute();\n return refreshSelectedSession", self.page)
        self.assertIn(
            "const sessionSurfaceActive=$('view-session').classList.contains('on');\n"
            " if(!pinned&&sessionSurfaceActive&&currentPanel!=='sessions')renderSession(LATEST);",
            self.page,
        )
        self.assertNotIn("if(LATEST&&id===stateSessionId(LATEST))", self.page)
        self.assertNotIn("if(pinned&&pinned===stateSessionId(LATEST))", self.page)

    def test_sessions_overview_is_bounded_routed_and_accessible(self):
        for marker in (
            "id=preview-surface-sessions",
            "id=current-session-grid",
            "function renderCurrentSessions",
            "function openCurrentSessions",
            "'/#sessions'",
            "data-current-session-id",
            '<h1 id=session-page-title>Sessions</h1>',
            'class=currentSessionsCount id=current-session-count-label',
            "filterView.countLabel",
            "Working",
            "Waiting",
            "Recent",
            "provider-claude",
            "provider-codex",
            "provider-cursor",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("const CURRENT_PANEL_KEYS=['sessions','run'];",
                      self.page)
        self.assertNotIn("data-current-panel=sessions", self.page)
        self.assertNotIn('id=current-tabs', self.page)
        self.assertRegex(self.page, r"id=tab-session[^>]*>.*?<span class=tabLabel>Sessions</span>")
        self.assertIn("if(h==='sessions'||h==='sessions-all'||h==='current-sessions')", self.page)
        self.assertIn("history.replaceState(null,'','/#sessions')", self.page)
        self.assertIn("function currentSessionModelName", self.page)
        self.assertIn("row.session_name||row.project||'Untitled session'", self.page)
        self.assertIn("`${runtime} / ${model}${effort?` ${effort}`:''}`", self.page)
        self.assertIn("<span>Speed</span>", self.page)
        self.assertIn("speedFmt(throughput.output_tps)} tok/s", self.page)
        self.assertIn("<span>Output / $</span>", self.page)
        self.assertIn("row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar)", self.page)
        self.assertIn(
            "const outputPerDollar=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar);",
            self.page,
        )
        self.assertNotIn(
            "const outputPerDollar=row.output_per_dollar==null?'--':compactNumber(row.output_per_dollar)+(estimate||row.token_estimate?' est':'');",
            self.page,
        )
        self.assertIn(".currentSessionMetrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))", self.page)
        self.assertIn(
            "body.sessionRoute #view-session .currentSessionMetrics{grid-template-columns:1.02fr .9fr 1.18fr 1fr;gap:0;margin-top:24px;padding-top:18px",
            self.page,
        )
        self.assertIn(
            "body.sessionRoute #view-session .currentSessionMetric{padding:0 14px",
            self.page,
        )
        self.assertNotIn("id=unpin", self.page)
        self.assertNotIn("Back to current sessions", self.page)
        self.assertNotIn("Back to all sessions", self.page)
        self.assertNotIn("$('unpin')", self.page)
        self.assertNotIn("Reorder: drag or ⌥ + arrows", self.page)
        self.assertNotIn("currentSessionsMoveHint", self.page)
        self.assertNotIn("currentSessionOpen", self.page)
        self.assertNotIn(">Open →</span>", self.page)

    def test_current_session_activity_filters_are_accessible_and_wired(self):
        for marker in (
            'id=current-session-activity-filters role=group aria-label="Filter current sessions by activity"',
            'data-current-activity-filter=all aria-pressed=true>All</button>',
            'data-current-activity-filter=working aria-pressed=false>Working</button>',
            'data-current-activity-filter=recent aria-pressed=false>Recent</button>',
            "let currentSessionRuntimeFilter='all',currentSessionActivityFilter='all';",
            "function renderCurrentSessionActivityFilters(selection)",
            "currentSessionFilterView(orderedRows,currentSessionRuntimeFilter,currentSessionActivityFilter)",
            "renderCurrentSessionActivityFilters(filterView.activitySelection);",
            "currentSessionActivityFilter=button.dataset.currentActivityFilter||'all';",
            "currentSessionsFilteredEmptyHtml(filterView.activitySelection)",
            "function keepCurrentSessionFilterFocusVisible(event)",
            "button.scrollIntoView({block:'nearest',inline:'nearest'});",
            "currentSessionRuntimeFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);",
            "currentSessionActivityFilters.addEventListener('focusin',keepCurrentSessionFilterFocusVisible);",
        ):
            self.assertIn(marker, self.page)
        self.assertIn(
            "$('current-session-activity-filters').hidden=true;",
            self.page,
        )
        for marker in (
            ".currentSessionFilterGroups{display:flex;align-items:center;gap:10px;min-width:0;overflow-x:auto",
            ".currentSessionFilterGroup[hidden]{display:none}",
            "body.sessionRoute #view-session .currentSessionRuntimeFilters,body.sessionRoute #view-session .currentSessionActivityFilters{display:flex;min-width:0}",
            "body.sessionRoute #view-session .currentSessionFilterGroups{display:grid;width:100%;max-width:none;overflow:visible}",
            "body.sessionRoute #view-session .currentSessionFilterGroup{min-width:0;max-width:100%;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin}",
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_runtime_view_discovers_filters_and_preserves_valid_selection(self):
        match = re.search(
            r"^function currentSessionRuntimeView\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionRuntimeView")
        rows = [
            {"id": "claude-code", "provider": "claude"},
            {"id": "codex", "provider": "codex"},
            {"id": "claude-desktop", "provider": "claude"},
        ]
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex',cursor:'Cursor'})[row.provider];
""" + match.group(0) + "\nconst rows=" + json.dumps(rows) + ";\n" + """
const all=currentSessionRuntimeView(rows,'all');
const claude=currentSessionRuntimeView(rows,'claude');
rows.push({id:'cursor',provider:'cursor'});
const emerged=currentSessionRuntimeView(rows,'claude');
const vanished=currentSessionRuntimeView(rows.filter(row=>row.provider!=='claude'),'claude');
console.log(JSON.stringify({all,claude,emerged,vanished}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["all"], {
            "options": [
                {"id": "claude", "label": "Claude", "count": 2},
                {"id": "codex", "label": "Codex", "count": 1},
            ],
            "selection": "all",
            "rows": rows[:3],
            "countLabel": "3 active",
        })
        self.assertEqual(
            [row["id"] for row in rendered["claude"]["rows"]],
            ["claude-code", "claude-desktop"],
        )
        self.assertEqual(rendered["claude"]["countLabel"], "2 of 3 active")
        self.assertEqual(rendered["emerged"]["selection"], "claude")
        self.assertEqual(
            rendered["emerged"]["options"],
            [
                {"id": "claude", "label": "Claude", "count": 2},
                {"id": "codex", "label": "Codex", "count": 1},
                {"id": "cursor", "label": "Cursor", "count": 1},
            ],
        )
        self.assertEqual(rendered["vanished"]["selection"], "all")
        self.assertEqual(rendered["vanished"]["countLabel"], "2 active")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_filter_view_composes_runtime_and_sticky_activity_selection(self):
        names = (
            "currentSessionActivity",
            "currentSessionRuntimeView",
            "currentSessionFilterView",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        rows = [
            {"id": "claude-working", "provider": "claude", "activity_state": "working"},
            {"id": "claude-recent", "provider": "claude", "activity_state": "recent"},
            {"id": "codex-working", "provider": "codex", "activity_state": "working"},
            {"id": "codex-waiting", "provider": "codex", "activity_state": "waiting"},
        ]
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex'})[row.provider];
""" + "\n".join(functions) + "\nconst rows=" + json.dumps(rows) + ";\n" + """
const codexWorking=currentSessionFilterView(rows,'codex','working');
const claudeRecent=currentSessionFilterView(rows,'claude','recent');
const zeroWorking=currentSessionFilterView(
 rows.filter(row=>row.activity_state!=='working'),
 'all',
 'working',
);
const invalidActivity=currentSessionFilterView(rows,'all','waiting');
console.log(JSON.stringify({codexWorking,claudeRecent,zeroWorking,invalidActivity}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(
            [row["id"] for row in rendered["codexWorking"]["rows"]],
            ["codex-working"],
        )
        self.assertEqual(rendered["codexWorking"]["selection"], "codex")
        self.assertEqual(rendered["codexWorking"]["activitySelection"], "working")
        self.assertEqual(rendered["codexWorking"]["countLabel"], "1 of 4 active")
        self.assertEqual(
            [row["id"] for row in rendered["claudeRecent"]["rows"]],
            ["claude-recent"],
        )
        self.assertEqual(rendered["zeroWorking"]["rows"], [])
        self.assertEqual(rendered["zeroWorking"]["activitySelection"], "working")
        self.assertEqual(rendered["zeroWorking"]["countLabel"], "0 of 2 active")
        self.assertEqual(rendered["invalidActivity"]["activitySelection"], "all")
        self.assertEqual(rendered["invalidActivity"]["countLabel"], "4 active")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_sessions_filtered_empty_copy_describes_the_selected_activity(self):
        match = re.search(
            r"^function currentSessionsFilteredEmptyHtml\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionsFilteredEmptyHtml")
        script = match.group(0) + """
console.log(JSON.stringify({
 working:currentSessionsFilteredEmptyHtml('working'),
 recent:currentSessionsFilteredEmptyHtml('recent'),
}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertIn("No working sessions right now", rendered["working"])
        self.assertIn("No recent sessions right now", rendered["recent"])
        for html in rendered.values():
            self.assertIn("Filters are still active", html)
            self.assertNotIn("Your next live session appears here", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_keyboard_move_reorders_the_visible_runtime_sequence(self):
        match = re.search(
            r"^function currentSessionKeyboardMove\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionKeyboardMove")
        script = match.group(0) + """
const moved=currentSessionKeyboardMove(
 ['claude-1','codex-1','claude-2'],
 ['claude-1','claude-2'],
 'claude-1',
 1,
);
console.log(JSON.stringify(moved));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "ids": ["codex-1", "claude-2", "claude-1"],
            "index": 1,
            "total": 2,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_keyboard_move_uses_the_combined_filter_sequence(self):
        names = (
            "currentSessionActivity",
            "currentSessionKeyboardMove",
            "currentSessionRuntimeView",
            "currentSessionFilterView",
            "moveCurrentSessionCardBy",
        )
        functions = []
        for name in names:
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
const appFilterGroup=row=>row.provider;
const appFilterLabel=row=>({claude:'Claude',codex:'Codex'})[row.provider];
const LATEST={xsession:{current_sessions:[
 {id:'claude-working',provider:'claude',activity_state:'working'},
 {id:'codex-working',provider:'codex',activity_state:'working'},
 {id:'claude-recent',provider:'claude',activity_state:'recent'},
]}};
const currentSessionRuntimeFilter='claude';
const currentSessionActivityFilter='working';
const orderedCurrentSessionRows=rows=>rows;
let saved=null;
const setCurrentSessionOrder=ids=>{saved=ids};
""" + "\n".join(functions) + """
const result=moveCurrentSessionCardBy('claude-working',1);
console.log(JSON.stringify({result,saved}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {"result": None, "saved": None})

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_ids_match_detects_a_stale_filtered_grid(self):
        match = re.search(
            r"^function currentSessionIdsMatch\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionIdsMatch")
        script = match.group(0) + """
const stable=currentSessionIdsMatch(
 ['claude-1','claude-2'],
 [{id:'claude-1'},{id:'claude-2'}],
);
const stale=currentSessionIdsMatch(
 ['claude-1','claude-2'],
 [{id:'codex-1'}],
);
console.log(JSON.stringify({stable,stale}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertTrue(rendered["stable"])
        self.assertFalse(rendered["stale"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_current_session_filter_focus_survives_emergence_and_falls_back_on_disappearance(self):
        match = re.search(
            r"^function currentSessionFilterFocusTarget\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs currentSessionFilterFocusTarget")
        script = match.group(0) + """
const emerged=currentSessionFilterFocusTarget(
 {selection:'claude',options:[{id:'claude'},{id:'codex'},{id:'cursor'}]},
 'claude',
);
const vanished=currentSessionFilterFocusTarget(
 {selection:'all',options:[{id:'codex'}]},
 'claude',
);
console.log(JSON.stringify({emerged,vanished}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["emerged"], "claude")
        self.assertEqual(rendered["vanished"], "all")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_flow_snapshot_uses_real_activity_and_marks_only_new_arrivals(self):
        match = re.search(
            r"^function sessionFlowSnapshot\(.*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs a pure sessionFlowSnapshot")
        script = match.group(0) + """
const unavailable=sessionFlowSnapshot(undefined,[],false);
const first=sessionFlowSnapshot([
 {id:'working',activity_state:'working'},
 {id:'waiting',activity_state:'waiting'},
 {id:'recent',activity_state:'recent'},
],[],false);
const next=sessionFlowSnapshot([
 {id:'waiting',activity_state:'waiting'},
 {id:'arriving',activity_state:'working'},
],first.ids,true);
const idle=sessionFlowSnapshot([],next.ids,true);
console.log(JSON.stringify({unavailable,first,next,idle}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["unavailable"], {
            "evidence": "unavailable", "mode": "idle", "count": 0,
            "working": 0, "waiting": 0, "recent": 0,
            "ids": [], "arriving": [],
        })
        self.assertEqual(rendered["first"], {
            "evidence": "available", "mode": "working", "count": 3,
            "working": 1, "waiting": 1, "recent": 1,
            "ids": ["working", "waiting", "recent"], "arriving": [],
        })
        self.assertEqual(rendered["next"], {
            "evidence": "available", "mode": "working", "count": 2,
            "working": 1, "waiting": 1, "recent": 0,
            "ids": ["waiting", "arriving"], "arriving": ["arriving"],
        })
        self.assertEqual(rendered["idle"], {
            "evidence": "available", "mode": "idle", "count": 0,
            "working": 0, "waiting": 0, "recent": 0,
            "ids": [], "arriving": [],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_page_signal_presentations_share_one_animation_timeline(self):
        functions = []
        for name in ("pageSignalTiming", "sessionSignalPresentation", "pageSignalPresentation"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        script = """
let pageSignalClockStartedAt=1000;
let clockNow=11000;
const performance={now:()=>clockNow};
""" + "\n".join(functions) + """
let latestSessionSignalSnapshot={evidence:'available',mode:'working'};
const pageSignalReducedMotionQuery={matches:false};
const signals=['sessions','spend','models','capabilities','efficiency','learn','settings'];
const routes=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
const states={};
for(const mode of ['working','waiting','recent','idle']){
 states[mode]=sessionSignalPresentation({evidence:'available',mode},false);
}
states.unavailable=sessionSignalPresentation({evidence:'unavailable',mode:'idle'},false);
latestSessionSignalSnapshot={evidence:'unavailable',mode:'idle'};
const unavailable=pageSignalPresentation({dataset:{pageSignal:'sessions'}});
clockNow=17000;
latestSessionSignalSnapshot={evidence:'available',mode:'working'};
const continued=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
pageSignalReducedMotionQuery.matches=true;
const reduced=Object.fromEntries(signals.map(signal=>[
 signal,pageSignalPresentation({dataset:{pageSignal:signal}}),
]));
console.log(JSON.stringify({routes,states,unavailable,continued,reduced}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        timing = {
            "heroSpeed": 0.018,
            "heroFrame": 180,
            "borderSpeed": 0.016,
            "borderFrame": 9160,
        }
        for presentation in rendered["routes"].values():
            self.assertEqual(
                {key: presentation[key] for key in timing},
                timing,
            )
        states = rendered["states"]
        for mode in ("working", "waiting", "recent", "idle"):
            self.assertEqual({key: states[mode][key] for key in timing}, timing)
        self.assertGreater(states["working"]["heroStrength"], states["idle"]["heroStrength"])
        self.assertEqual(rendered["unavailable"]["heroSpeed"], 0)
        self.assertEqual(rendered["unavailable"]["borderSpeed"], 0)
        self.assertEqual(rendered["unavailable"]["heroFrame"], timing["heroFrame"])
        self.assertEqual(rendered["unavailable"]["borderFrame"], timing["borderFrame"])
        continued_timing = {
            "heroSpeed": 0.018,
            "heroFrame": 288,
            "borderSpeed": 0.016,
            "borderFrame": 9256,
        }
        for presentation in rendered["continued"].values():
            self.assertEqual(
                {key: presentation[key] for key in continued_timing},
                continued_timing,
            )
        for presentation in rendered["reduced"].values():
            self.assertEqual(presentation["heroSpeed"], 0)
            self.assertEqual(presentation["borderSpeed"], 0)
            self.assertEqual(presentation["heroFrame"], continued_timing["heroFrame"])
            self.assertEqual(presentation["borderFrame"], continued_timing["borderFrame"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_page_signal_route_change_disposes_a_partially_mounted_effect(self):
        page_path = Path(meter.__file__).with_name("page.html")
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{return this.children.find(child=>selector.includes(child.className))||null;}}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null;
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
let live=[];
const borderResolvers=[];
function handle(label){{
 live.push(label);
 return {{update(){{}},dispose(){{live=live.filter(value=>value!==label);}}}};
}}
let currentMount='A';
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(){{return Promise.resolve(handle(`hero:${{currentMount}}`));}},
  mountPulsingBorder(){{
   const label=`border:${{currentMount}}`;
   return new Promise(resolve=>borderResolvers.push(()=>resolve(handle(label))));
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A';
 const mountA=syncActivePageSignalEffects();
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const afterAHero=[...live];
 currentHead=b;currentMount='B';
 const mountB=syncActivePageSignalEffects();
 await Promise.resolve();await Promise.resolve();await Promise.resolve();
 const afterRouteChange=[...live];
 borderResolvers.shift()();
 await Promise.resolve();await Promise.resolve();
 const afterStaleBorder=[...live];
 borderResolvers.shift()();
 await Promise.all([mountA,mountB]);
 console.log(JSON.stringify({{afterAHero,afterRouteChange,afterStaleBorder,complete:live}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["afterAHero"], ["hero:A"])
        self.assertEqual(rendered["afterRouteChange"], ["hero:B"])
        self.assertEqual(rendered["afterStaleBorder"], ["hero:B"])
        self.assertEqual(rendered["complete"], ["hero:B", "border:B"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_stale_same_header_mount_cannot_clear_the_new_ready_marker(self):
        page_path = Path(meter.__file__).with_name("page.html")
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{
  const wanted=selector.slice(1);
  for(const child of this.children){{
   if(child.className===wanted)return child;
   const nested=child.querySelector(selector);
   if(nested)return nested;
  }}
  return null;
 }}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null,currentMount='',live=[],borderQueue=[];
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
function handle(label,host){{
 live.push(label);host.dataset.pageEffect='ready';let disposed=false;
 return {{
  update(){{}},
  dispose(){{
   if(!disposed){{disposed=true;live=live.filter(value=>value!==label);}}
   delete host.dataset.pageEffect;
  }},
 }};
}}
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(host){{return Promise.resolve(handle(`hero:${{currentMount}}`,host));}},
  mountPulsingBorder(host){{
   const label=`border:${{currentMount}}`;
   return new Promise(resolve=>borderQueue.push({{label,resolve:()=>resolve(handle(label,host))}}));
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
const ticks=async(count=5)=>{{while(count--)await Promise.resolve();}};
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A1';const mountA1=syncActivePageSignalEffects();await ticks();
 currentHead=b;currentMount='B';const mountB=syncActivePageSignalEffects();await ticks();
 currentHead=a;currentMount='A2';const mountA2=syncActivePageSignalEffects();await ticks();
 const aHero=a.querySelector('.pageDither');
 const before={{live:[...live],heroState:aHero.dataset.pageEffect||null}};
 borderQueue.find(row=>row.label==='border:A1').resolve();await mountA1;await ticks();
 const afterStale={{live:[...live],heroState:aHero.dataset.pageEffect||null}};
 borderQueue.find(row=>row.label==='border:B').resolve();await mountB;await ticks();
 borderQueue.find(row=>row.label==='border:A2').resolve();await mountA2;
 console.log(JSON.stringify({{before,afterStale,complete:{{live,heroState:aHero.dataset.pageEffect||null}}}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["before"], {
            "live": ["hero:A2"], "heroState": "ready",
        })
        self.assertEqual(rendered["afterStale"], {
            "live": ["hero:A2"], "heroState": "ready",
        })
        self.assertEqual(rendered["complete"], {
            "live": ["hero:A2", "border:A2"], "heroState": "ready",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_shared_border_readiness_cancels_stale_mounts_before_construction(self):
        page_path = Path(meter.__file__).with_name("page.html")
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{
 let start=page.indexOf(`function ${{name}}(`);
 if(start<0)throw new Error(name);
 if(page.slice(start-6,start)==='async ')start-=6;
 let i=page.indexOf('{{',start),depth=0;
 for(;i<page.length;i++){{
  if(page[i]==='{{')depth++;
  else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);
 }}
}}
class El{{
 constructor(name){{this.name=name;this.dataset={{}};this.children=[];this.isConnected=true;this.className='';}}
 querySelector(selector){{
  const wanted=selector.slice(1);
  for(const child of this.children){{
   if(child.className===wanted)return child;
   const nested=child.querySelector(selector);
   if(nested)return nested;
  }}
  return null;
 }}
 appendChild(child){{this.children.push(child);return child;}}
 append(...children){{this.children.push(...children);}}
 setAttribute(){{}}
 replaceChildren(){{this.children=[];}}
 closest(selector){{return selector.includes('sessionDetail')?null:this;}}
}}
let currentHead=null,currentMount='',live=[];
let releaseSharedBorder;
const sharedBorderReady=new Promise(resolve=>{{releaseSharedBorder=resolve;}});
const document={{querySelector(){{return currentHead;}},createElement(){{return new El('created');}}}};
let activePageSignalHead=null,activePageHeroHandle=null,activePageBorderHandle=null;
let activePageSignalMounting=false,activePageSignalGeneration=0,pageEffectsDisabled=false;
function handle(label,host){{
 live.push(label);host.dataset.pageEffect='ready';host.paperShaderMount=label;let disposed=false;
 return {{
  update(){{}},
  dispose(){{
   if(disposed)throw new Error(`double dispose ${{label}}`);
   disposed=true;live=live.filter(value=>value!==label);
   delete host.dataset.pageEffect;delete host.paperShaderMount;
  }},
 }};
}}
function loadPageEffectsModule(){{
 return Promise.resolve({{
  mountDitherField(host){{return Promise.resolve(handle(`hero:${{currentMount}}`,host));}},
  async mountPulsingBorder(host,presentation,isCurrent){{
   const label=`border:${{currentMount}}`;
   await sharedBorderReady;
   if(isCurrent&&!isCurrent())return null;
   return handle(label,host);
  }},
 }});
}}
function pageSignalPresentation(){{return {{}};}}
eval(extract('ensurePageSignalHosts'));
eval(extract('disposeActivePageSignalEffects'));
if(page.includes('function pageSignalHandle('))eval(extract('pageSignalHandle'));
eval(extract('syncActivePageSignalEffects'));
const ticks=async(count=8)=>{{while(count--)await Promise.resolve();}};
(async()=>{{
 const a=new El('A'),b=new El('B');
 a.dataset.pageSignal='models';b.dataset.pageSignal='spend';
 currentHead=a;currentMount='A1';const mountA1=syncActivePageSignalEffects();await ticks();
 currentHead=b;currentMount='B';const mountB=syncActivePageSignalEffects();await ticks();
 currentHead=a;currentMount='A2';const mountA2=syncActivePageSignalEffects();await ticks();
 const aHero=a.querySelector('.pageDither'),aBorder=a.querySelector('.pageHeroBorder');
 releaseSharedBorder();
 await Promise.all([mountA1,mountB,mountA2]);await ticks();
 console.log(JSON.stringify({{
  live,
  heroState:aHero.dataset.pageEffect||null,
  borderState:aBorder.dataset.pageEffect||null,
  borderOwner:aBorder.paperShaderMount||null,
  disabled:pageEffectsDisabled,
 }}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered, {
            "live": ["hero:A2", "border:A2"],
            "heroState": "ready",
            "borderState": "ready",
            "borderOwner": "border:A2",
            "disabled": False,
        })

    def test_top_level_pages_share_one_dither_header_system(self):
        for marker in (
            "data-page-signal=sessions",
            "data-page-signal=spend",
            "data-page-signal=models",
            "data-page-signal=capabilities",
            "data-page-signal=learn",
            "data-page-signal=settings",
            "class=spectrumPageHeadFrame",
            "class=\"spectrumPageHeadFrame hasPageActions\"",
            "class=spectrumPageSubtitle",
            "class=spectrumPageActions",
            "function ensurePageSignalHosts(head)",
            "function syncActivePageSignalEffects()",
            "document.querySelector('.view.on .spectrumPageHead[data-page-signal]')",
            "activePageHeroHandle?.dispose()",
            "activePageBorderHandle?.dispose()",
            "requestAnimationFrame(syncActivePageSignalEffects)",
            "function syncSessionFlow(snapshot)",
            "import('/assets/session-effects.js')",
            "if(label.textContent!==nextLabel)",
            "data-session-flow",
            ".currentSessionCard.is-arriving",
            "@keyframes currentSessionArrive",
            "@media(prefers-reduced-motion:reduce)",
        ):
            self.assertIn(marker, self.page)
        for rejected_marker in (
            "sessionFlowField",
            "sessionFlowLine",
            "sessionFlowPacket",
            "currentSessionFlow",
            "sessionAmbient",
            "sessionGodRays",
            "session-god-rays",
            "sessionPrimaryBorder",
            "session-primary-border",
            "syncPrimarySessionBorder",
            "primaryWorkingSessionId",
            "neuro-noise",
            "neuroNoise",
            "currentSessionStateShift",
            "<animateMotion",
            "offset-path:path",
        ):
            self.assertNotIn(rejected_marker, self.page)

    def test_top_level_headers_use_fixed_shared_heights_and_concise_subtitles(self):
        for marker in (
            ".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;height:138px",
            "@media(max-width:900px){.spectrumPageHead{height:126px",
            "@media(max-width:520px){.spectrumPageHead{height:116px",
            ".spectrumPageSubtitle{max-width:52ch;margin:7px 0 0;color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
            ".spectrumPageActions{position:absolute;z-index:5;right:30px;bottom:24px",
            ".spectrumPageActions{position:static;display:flex;width:100%;max-width:none",
            "Live local traces · last 30 minutes.",
            "Estimated agent spend over time.",
            "Cost, speed, and context.",
            "Tools, MCP servers, and skills.",
            "Local token efficiency.",
            "Pushed code &times; covered spend.",
            "The Token Meter review loop.",
            "Budgets, connections, pricing, and updates.",
        ):
            self.assertIn(marker, self.page)
        self.assertEqual(self.page.count("data-page-signal="), 8)
        self.assertEqual(self.page.count("class=spectrumPageSubtitle"), 8)
        self.assertNotIn(".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;min-height:138px", self.page)

    def test_shared_header_effect_adapter_exposes_generic_mounts(self):
        for marker in (
            "orderedDitherRainFragmentShader",
            "export async function mountDitherField",
            "export async function mountPulsingBorder(element, presentation, isCurrent = () => true)",
            "if (!isCurrent()) return null;",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("export async function mountSessionHero", self.session_effects)
        self.assertNotIn("export async function mountSessionBorder", self.session_effects)

    def test_desktop_action_headers_keep_titles_inside_their_text_slot(self):
        titles = re.findall(
            r'class="spectrumPageHeadFrame hasPageActions".*?<h1(?:\s[^>]*)?>([^<]+)</h1>',
            self.page,
            re.DOTALL,
        )
        self.assertEqual(
            titles, ["Sessions", "Models", "Spend", "Efficiency", "Git"],
        )
        self.assertTrue(all(len(title) <= 12 for title in titles))

    def test_mobile_header_action_rows_scroll_instead_of_stacking(self):
        for marker in (
            ".spectrumPageActions .modelControls{display:grid;width:100%;grid-template-columns:repeat(3,minmax(0,1fr))",
            ".spectrumPageActions .spendRangeControls .seg{display:flex;width:100%",
            ".spectrumPageActions .spendRangeControls .seg button{flex:0 0 auto}",
            ".spectrumPageActions .spendRangeControls .seg::-webkit-scrollbar{display:none}",
            ".spectrumPageActions .spendRangeControls [data-spend-range=custom]{grid-column:auto}",
            "data-spend-range=month>Month</button>",
            "data-spend-range=custom>Custom</button>",
            ".spectrumPageActions .spendRangeControls .seg button{padding-inline:8px}",
        ):
            self.assertIn(marker, self.page)

    def test_session_shader_border_tracks_the_hero_edges(self):
        for marker in (
            "u_marginLeft: 0.002",
            "u_marginRight: 0.002",
            "u_marginTop: 0.002",
            "u_marginBottom: 0.002",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("u_marginLeft: 0.014", self.session_effects)
        self.assertNotIn("u_marginTop: 0.024", self.session_effects)

    def test_session_dither_uses_downward_ordered_pixel_streams(self):
        for marker in (
            "orderedDitherRainFragmentShader",
            "const int bayer4x4[16]",
            "float fallingPhase",
            "cell.y + u_time * speed",
            "float streamHead",
        ):
            self.assertIn(marker, self.session_effects)
        self.assertNotIn("DitheringShapes.", self.session_effects)

    def test_collapsed_navigation_keeps_session_scope_labels_visible(self):
        self.assertIn(
            "body.spectrumApp .top .brandCopy,body.spectrumApp .top .tabLabel,"
            "body.spectrumApp .top .tabShortcut{display:none}",
            self.page,
        )
        self.assertIn("body.spectrumApp .top .brandCopy{display:grid}", self.page)
        self.assertIn("body.spectrumApp .top .tabLabel{display:inline}", self.page)
        self.assertNotIn(".brandCopy,.tabLabel,.tabShortcut{display:none}", self.page)

    def test_sessions_overview_shows_exact_day_spend_summary_and_tokenomics_link(self):
        for marker in (
            'class=currentDaySummary aria-label="Today\'s usage summary"',
            "id=current-day-spend",
            "id=current-day-sessions",
            "id=current-day-vs-yesterday",
            "Spend today",
            "Sessions today",
            "Vs yesterday",
            "function renderCurrentDaySummary(xs)",
            "if(!Array.isArray(xs?.daily))",
            "const todayKey=localDayKey(),yesterdayDate=new Date()",
            "days.find(row=>row.day===todayKey)",
            "days.find(row=>row.day===yesterdayKey)",
            "if(todayPartial)comparisonNote='Withheld for partial coverage'",
            "else if(yesterdayPartial)comparisonNote='Withheld · yesterday is partial'",
            'href="https://www.splunk.com/en_us/products/tokenomics.html"',
            'target=_blank rel="noopener noreferrer"',
            'aria-label="Get Enterprise Tokenomics from Splunk (opens in a new tab)"',
            ">Get Enterprise Tokenomics<",
            "id=current-month-spend",
            ">View month spend<",
        ):
            self.assertIn(marker, self.page)
        self.assertLess(self.page.index("class=currentDaySummary"), self.page.index("id=current-session-grid"))
        self.assertIn("renderCurrentDaySummary(state?.xsession);", self.page)
        self.assertIn(".currentDayReadouts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))", self.page)
        self.assertIn(".currentDayMetric:nth-child(3){grid-column:1/-1", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_sessions_month_spend_action_selects_and_persists_month_before_routing(self):
        match = re.search(
            r"^function persistSpendRange\(\)[^\n]*\n"
            r"^function openMonthSpend\(\).*?^\}\n",
            self.page,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "dashboard needs a month-spend action")
        script = """
const SPEND_RANGE_KEY='tm_spend_range_v1';
let spendRangeChoice='7',spendCustomFrom='2026-08-01',spendCustomTo='2026-08-03';
const writes=[],routes=[];
const localStorage={setItem(key,value){writes.push([key,JSON.parse(value)]);}};
const setHashRoute=route=>routes.push(route);
""" + match.group(0) + """
openMonthSpend();
console.log(JSON.stringify({spendRangeChoice,writes,routes}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "spendRangeChoice": "month",
            "writes": [["tm_spend_range_v1", {
                "range": "month",
                "from": "2026-08-01",
                "to": "2026-08-03",
            }]],
            "routes": ["spend"],
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_empty_current_sessions_render_live_arrival_and_real_seven_day_history(self):
        functions = []
        for name in ("currentSessionsIdleHistory", "currentSessionsEmptyHtml"):
            match = re.search(
                rf"^function {name}\(.*?^\}}\n",
                self.page,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"dashboard needs {name}")
            functions.append(match.group(0))
        fixture = {"daily": [
            {"day": "2026-08-18", "sessions": 1, "cost": 1.25,
             "providers": [{"provider": "codex"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "local_estimate"},
            {"day": "2026-08-17", "sessions": 5, "cost": 2.0,
             "providers": [{"provider": "codex"}, {"provider": "claude"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "reported"},
            {"day": "2026-08-12", "sessions": 2, "cost": 50.0,
             "providers": [{"provider": "claude"}],
             "availability": {"cost": False},
             "coverage": {"cost": {"complete": False}},
             "usage_basis": "unavailable"},
            {"day": "2026-08-11", "sessions": 99, "cost": 99.0,
             "providers": [{"provider": "outside-window"}],
             "availability": {"cost": True},
             "coverage": {"cost": {"complete": True}},
             "usage_basis": "reported"},
        ]}
        script = """
const f=value=>String(value);
const money=value=>'$'+Number(value).toFixed(2);
const esc=value=>String(value);
const countWord=(count,singular,plural=singular+'s')=>Number(count)===1?singular:plural;
""" + "\n".join(functions) + "\nconst xs=" + json.dumps(fixture) + ";\n" + """
const history=currentSessionsIdleHistory(xs,'2026-08-18');
const html=currentSessionsEmptyHtml(xs,'2026-08-18');
const firstRunHtml=currentSessionsEmptyHtml({daily:[]},'2026-08-18');
console.log(JSON.stringify({history,html,firstRunHtml}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["history"], {
            "days": [
                {"day": "2026-08-12", "sessions": 2},
                {"day": "2026-08-13", "sessions": 0},
                {"day": "2026-08-14", "sessions": 0},
                {"day": "2026-08-15", "sessions": 0},
                {"day": "2026-08-16", "sessions": 0},
                {"day": "2026-08-17", "sessions": 5},
                {"day": "2026-08-18", "sessions": 1},
            ],
            "sessions": 8,
            "cost": 3.25,
            "costAvailable": True,
            "costPartial": True,
            "costEstimated": True,
            "runtimes": ["claude", "codex"],
        })
        html = rendered["html"]
        for marker in (
            "Your next live session appears here",
            "Next live session",
            "Cost",
            "Output / $",
            "Context in use",
            "Speed",
            "8 past sessions",
            "At least $3.25 est tracked",
            "2 runtimes",
            "data-current-empty-history",
            "Explore 8 past sessions",
            'href="https://github.com/splunk/token-meter/issues"',
            "Share feedback",
        ):
            self.assertIn(marker, html)
        self.assertIn("No past sessions yet", rendered["firstRunHtml"])
        self.assertIn("Your seven-day activity will build here.", rendered["firstRunHtml"])
        self.assertNotIn("data-current-empty-history", rendered["firstRunHtml"])

    def test_session_cards_use_compact_readable_metrics(self):
        for marker in (
            ".currentSessionGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}",
            ".currentSessionGrid{gap:11px}",
            ".currentSessionCard{min-height:190px;padding:16px 18px 14px}",
            ".currentSessionIdentity h3{font-size:18px}",
            ".currentSessionMetric b{margin-top:5px;font-size:17px}",
            "font-variant-numeric:tabular-nums",
            ".currentSessionMetric b.mono{font-size:17px}",
            ".currentSessionMetric b,.currentSessionMetric b.mono{font-size:19px}",
            "@media(max-width:700px){.currentSessionGrid{grid-template-columns:1fr;gap:9px}",
            ".currentSessionCard{min-height:184px;padding:14px 15px 12px}",
            ".currentSessionMetric b,.currentSessionMetric b.mono{font-size:15px",
            "@media(max-width:700px){.currentSessionMetric b,.currentSessionMetric b.mono{font-size:16.5px}",
        ):
            self.assertIn(marker, self.page)

    def test_session_cards_show_context_pressure_or_window_fallback(self):
        for marker in (
            "function currentSessionContextSparkline(row)",
            "Array.isArray(context.samples)",
            "if(!windowSize)return {",
            "currentSessionContextUnavailable",
            "window --",
            "Context window unavailable;",
            "Context window and latest token count unavailable.",
            "const plotted=samples.slice(-24)",
            "const values=plotted.map(value=>value/windowSize)",
            "const width=96,height=22",
            "currentSessionContextBar",
            "value>=.85?' high':value>=.7?' watch'",
            "<svg class=currentSessionContextSpark",
            "class=currentSessionContextValue",
            "const contextSpark=currentSessionContextSparkline(row)",
            "${contextSpark.html}",
            "<span>Context in use</span>",
            ">Context in use</div><div class=\"v mono\" id=ov-context>",
            ">Context in use</div><div class=\"v mono\" id=preview-context>",
            "cacheSavingsAvailable?money(cache.saved||s.cache_saved||0):'--'",
            "const cardDescription=`${contextSpark.summary}",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("const values=windowSize?plotted.map(value=>value/windowSize):plotted", self.page)
        self.assertNotIn("currentSessionContextCaption", self.page)
        self.assertNotIn("currentSessionContextGuide", self.page)

    def test_session_cards_do_not_have_native_hover_tooltips(self):
        session_cards = self.page.split("function renderCurrentSessions(state=LATEST){", 1)[1].split(
            "const currentSessionGrid=$('current-session-grid');", 1
        )[0]
        for marker in (
            'class=currentSessionRuntime title=',
            '<h3 title=',
            '<p title=',
            '<b class=mono title=',
            "const contextNote=",
        ):
            self.assertNotIn(marker, session_cards)
        self.assertIn('aria-description="${esc(cardDescription)}"', session_cards)

    def test_redundant_hover_tips_are_removed_from_dashboard_labels(self):
        for marker in (
            '<h1 class=fieldtip id=session-page-title',
            '<h1 class=fieldtip tabindex=0 aria-description="Local AI app logs',
            '<h1 class=fieldtip tabindex=0 aria-description="Compare model runtimes',
            '<h1 class=fieldtip tabindex=0 aria-description="What changed',
            '<h1 class=fieldtip tabindex=0 aria-description="A practical review loop',
            '<h1 class=fieldtip tabindex=0 aria-description="Review local tool',
            '<h1 class=fieldtip tabindex=0 aria-description="Machine-wide controls',
            'class="currentSessionsCount fieldtip"',
            'class="previewStartText fieldtip"',
            'class="card previewKpi fieldtip',
            'class="learnShortcut fieldtip"',
            '<h2 class=fieldtip tabindex=0 aria-description="Where the day’s covered spend',
        ):
            self.assertNotIn(marker, self.page)
        for marker in (
            '<h1 id=session-page-title>Sessions</h1>',
            '<h2>All sessions</h2>',
            '<h1>Models</h1>',
            '<h1>Spend</h1>',
            '<h1>Learn</h1>',
            '<h1>Tools</h1>',
            '<h1>Settings</h1>',
            '<div class="card previewKpi">',
            'data-tip="Observed model output divided by attributable timing.',
            'data-tip="Budget minus observed spend.',
        ):
            self.assertIn(marker, self.page)

    def test_sessions_use_the_distilled_token_meter_spectrum_system(self):
        for marker in (
            "shared-spectrum-system-v1",
            "session-spectrum-field-v2",
            "THESIS: Sessions are live instruments",
            "<body class=spectrumApp>",
            "--spectrum-cyan:#00bceb",
            "--spectrum-blue:#1ba0e1",
            "--spectrum-sky:#7fdbf2",
            "--spectrum-violet:#c7a7ff",
            "--spectrum-orange:#ffb457",
            "body.sessionRoute{",
            "--session-cyan:var(--spectrum-cyan)",
            'class="previewHead spectrumPageHead sessionFlowHead"',
            'class="previewHeadCopy spectrumPageHeadCopy"',
            ".spectrumPageHead:before",
            ".spectrumPageHead{position:relative;isolation:isolate;display:flex;width:100%;max-width:none;height:138px;align-items:flex-start",
            ".currentSessionCard:before{content:none}",
            ".currentSessionCard.activity-working",
            ".currentSessionGrip",
            "@media(prefers-reduced-motion:reduce)",
            "document.body.classList.toggle('sessionRoute',t==='session');",
            "$('view-session').classList.toggle('sessionOverview',overview);",
            "$('view-session').classList.toggle('sessionDetail',!overview);",
            "$('session-page-title').textContent=overview?'Sessions':sessionDisplayName(CURRENT);",
            "function sessionDisplayName(s)",
            "activity-${activity}",
            "<svg class=currentSessionGrip",
            'font-family:"Tektur Local"',
            'src:url("/assets/fonts/Tektur-Variable.ttf")',
            "filterView.countLabel",
            "concept-roll seed a5c0fdde",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("id=current-eyebrow", self.page)
        self.assertNotIn("miraiVerticalNote", self.page)
        self.assertNotIn("miraiGaugeScale", self.page)
        self.assertNotIn("直近30分", self.page)
        self.assertNotIn("稼働状況", self.page)
        self.assertNotIn("session-solar-field-v1", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewHead{min-height:152px}", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewStatus:before", self.page)
        self.assertNotIn("body.sessionRoute #view-session.sessionDetail .previewStatus:after", self.page)

    def test_run_status_card_has_no_decorative_context_circle(self):
        self.assertNotIn("#view-session.sessionDetail .previewStatus:before", self.page)
        self.assertNotIn("--context-pressure", self.page)
        self.assertNotIn(".style.setProperty('--context-pressure'", self.page)
        self.assertIn('id=preview-context-bar', self.page)
        self.assertIn("$('preview-context-bar').querySelector('i').style.width=", self.page)

    def test_top_level_views_share_the_spectrum_design_primitives(self):
        for marker in (
            "shared-spectrum-system-v1",
            "spectrum-professional-finish-v2",
            "--spectrum-surface-quiet:",
            "--spectrum-surface-elevated:",
            "--spectrum-edge-light:",
            "--spectrum-edge-hover:",
            "--spectrum-depth-focus:",
            "--spectrum-active-hover:",
            "body.spectrumApp .card{",
            "body.spectrumApp .tbtn{",
            "body.spectrumApp .seg{",
            "body.spectrumApp .tab.on,body.spectrumApp .seg button.on",
            "body.spectrumApp :is(input:not([type=checkbox]):not([type=range]),select,textarea,.modelPicker>summary)",
            "body.spectrumApp .spectrumPageHead:after",
            "--spectrum-page:radial-gradient",
            "--spectrum-card:radial-gradient",
            "--spectrum-control:radial-gradient",
            "--spectrum-active:linear-gradient",
            '<div class="previewHead spectrumPageHead sessionFlowHead"',
            '<header class="capPageHead spectrumPageHead"',
            '<div class="modelHead spectrumPageHead"',
            '<div class="dailyHead spectrumPageHead"',
            '<div class="learnHead spectrumPageHead"',
            '<div class="learnHead settingsPageHead spectrumPageHead"',
            "<h1>Tools</h1>",
            "<span class=allSessionsHint id=g-hint>Recent activity</span>",
            "vertical-navigation-rail-v1",
            "body.spectrumApp .wrap{--navigation-rail-width:184px",
            "grid-template-columns:var(--navigation-rail-width) minmax(0,1320px)",
            "body.spectrumApp .top{grid-column:1;grid-row:1;position:sticky",
            "body.spectrumApp .top .tabs{display:flex;min-width:0;max-width:100%;overflow:visible;flex:1;flex-direction:column",
            "body.spectrumApp .top .tab{display:grid;width:100%;min-height:42px",
            "class=tabIcon",
            "class=navPrimary",
            "class=navSecondary",
            ".navPrimary,.navSecondary{display:flex;flex-direction:column;gap:4px}.navSecondary{margin-top:auto",
            "@media(max-width:1180px){body.spectrumApp .wrap{--navigation-rail-width:68px",
            "@media(max-width:760px){body.spectrumApp .wrap{display:block",
            "body.spectrumApp .top .tabs{grid-column:1/-1;grid-row:2;min-width:0;width:100%;overflow-x:auto;flex:none;flex-direction:row",
            ".navPrimary,.navSecondary{display:contents}",
            "@media(max-width:760px){#view-models{overflow-x:clip}}",
            "body.spectrumApp .settingsMap{grid-template-columns:minmax(140px,.65fr) repeat(6,minmax(118px,1fr))",
        ):
            self.assertIn(marker, self.page)
        for marker in (
            "data-settings-target=budget-settings><span>01",
            "data-settings-target=agent-access><span>02",
            "data-settings-target=model-pricing-settings><span>03",
            "data-settings-target=frustration-settings><span>04",
            "data-settings-target=update-settings><span>05",
            "&#8249;",
            "&#8250;",
        ):
            self.assertNotIn(marker, self.page)

    def test_sessions_is_default_new_cards_lead_and_known_cards_keep_manual_order(self):
        for marker in (
            "$('tab-session').onclick=()=>{window.scrollTo(0,0);openCurrentSessions();};",
            "label:'Sessions'",
            "action:'sessions',directKey:'Digit1'",
            "setHashRoute('sessions',{replace:true,apply:false})",
            "const CURRENT_SESSION_ORDER_KEY='tm_current_session_order_v1'",
            "const CURRENT_SESSION_ORDER_MIGRATION_KEY='tm_current_session_newest_first_v1'",
            "function orderedCurrentSessionRows(rows)",
            "function currentSessionActivity(row)",
            "function syncCurrentSessionActivity(root,rows)",
            "if(!Array.isArray(sourceRows))",
            "function moveCurrentSessionCard(sourceId,targetId,after=false)",
            "function moveCurrentSessionCardBy(sourceId,delta)",
            "function focusCurrentSessionCard(id)",
            "draggable=true",
            "event.dataTransfer.effectAllowed='move'",
            "currentSessionGrid.addEventListener('dragover'",
            "currentSessionGrid.addEventListener('drop'",
            "currentSessionGrid.addEventListener('dragend'",
            "currentSessionGrid.addEventListener('keydown'",
            "event.altKey",
            "Press ⌥ and an arrow key to move.",
            "id=current-session-order-status",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("if(!id||byId.has(id))return;", self.page)
        self.assertIn(
            "const known=currentSessionOrder.filter(id=>byId.has(id)),seen=new Set(known);",
            self.page,
        )
        self.assertIn(
            "if(currentSessionDragId||(interactingCurrentSessionCard&&currentSessionIdsMatch(mountedCurrentSessionIds,rows))){syncCurrentSessionActivity(currentGrid,rows);return;}",
            self.page,
        )
        self.assertIn(
            "const unseen=sourceIds.filter(id=>!seen.has(id));\n"
            " let next=[...unseen,...known];",
            self.page,
        )
        self.assertIn(
            "if(currentSessionOrderNeedsNewestMigration&&sourceIds.length){\n"
            "  const newest=sourceIds[0];\n"
            "  next=[newest,...next.filter(id=>id!==newest)];",
            self.page,
        )
        self.assertNotIn("next.push(id);seen.add(id)", self.page)

    def test_cursor_provenance_is_integrated_across_dashboard_views(self):
        for marker in (
            "const hasLocalEstimate", "const estimateSuffix",
            "local token proxies are not comparable", "stroke-dasharray",
            "id=c-runtime-filter", "Input context proxy",
            "visible text estimate", "hasLocalEstimate(row)",
        ):
            self.assertIn(marker, self.page)

    def test_handler_swallows_browser_disconnects(self):
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        self.assertIn("except (BrokenPipeError, ConnectionResetError):", source)

    def test_dashboard_live_updates_do_not_hold_event_stream_connections(self):
        self.assertNotIn("new EventSource('/events')", self.page)
        self.assertIn("const LIVE_STATE_POLL_MS=1000", self.page)
        self.assertIn("setInterval(refreshLiveState,LIVE_STATE_POLL_MS)", self.page)
        self.assertIn("if(statePollBusy)return", self.page)
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        events = source.index('elif req_path == "/events":')
        self.assertIn("self.send_response(204)", source[events:events + 700])

    def test_http_server_tolerates_browser_connection_bursts(self):
        self.assertTrue(meter.TokenMeterHTTPServer.daemon_threads)
        self.assertGreaterEqual(meter.TokenMeterHTTPServer.request_queue_size, 32)

    def test_dynamic_responses_cannot_be_reused_from_an_http_cache(self):
        handler = mock.Mock()
        meter.H._send(handler, "{}", "application/json")

        handler.send_header.assert_any_call("Cache-Control", "no-store, max-age=0")
        handler.send_header.assert_any_call("Pragma", "no-cache")
        handler.send_header.assert_any_call("Expires", "0")


class MenubarSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(meter.__file__).with_name("menubar").joinpath("TokenMeterMenuBar.swift").read_text()

    def test_spend_action_opens_cross_session_spend_route(self):
        self.assertIn('case .spend: return "Spend"', self.source)
        self.assertIn('case .spend:\n                self.openDailyBrief()', self.source)
        self.assertIn('@objc private func openDailyBrief()', self.source)
        self.assertIn('openDashboardPanel("spend", includePinnedSession: false)', self.source)

    def test_dashboard_opens_sessions_unless_a_session_is_pinned(self):
        self.assertIn(
            'tokenMeterDashboardURL = URL(string: "http://127.0.0.1:8722/#sessions")',
            self.source,
        )
        self.assertIn("if pinnedSessionID?.isEmpty == false", self.source)
        self.assertIn('openDashboardPanel("summary")', self.source)
        self.assertIn('openDashboardPanel("sessions", includePinnedSession: false)', self.source)

    def test_model_prices_setting_opens_dashboard_pricing_editor(self):
        self.assertIn('NSMenuItem(title: "Model Prices", action: #selector(openModelPrices)', self.source)
        self.assertIn('@objc private func openModelPrices()', self.source)
        self.assertIn('openDashboardPanel("model-pricing", includePinnedSession: false)', self.source)

    def test_native_menu_does_not_advertise_removed_session_trace_view(self):
        for marker in (
            'NSMenuItem(title: "Open Trace"', '#selector(openTrace)',
            '@objc private func openTrace()', 'openDashboardPanel("activity")',
        ):
            self.assertNotIn(marker, self.source)

    def test_output_speed_remains_available_without_a_standing_menu_row(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertNotIn('addMetricRow("Output', rebuild)
        self.assertIn(r'· \(outputSpeedLabel) · \(model)', self.source)
        self.assertIn('formatTokenRate(rate)', self.source)
        self.assertIn(
            'return "\\(formatTokenRate(rate)) tok/s\\(estimatedTokens ? " est" : "")"',
            self.source,
        )
        self.assertNotIn('tok/s\\(live)', self.source)
        self.assertIn('print(snapshot.outputSpeedLabel)', self.source)

    def test_selected_session_cost_and_context_are_not_menu_rows(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertIn('return "\\(costLabel) · \\(contextLabel) · \\(outputSpeedLabel) · \\(model)"', self.source)
        self.assertNotIn('return "\\(verdict.prefix) \\(formatMoney(totalCost))', self.source)
        self.assertNotIn('addMetricRow("Cost", snapshot.costLabel', rebuild)
        self.assertNotIn('"Context",\n                contextDetail', rebuild)
        self.assertNotIn('addMetricRow("Status"', rebuild)
        self.assertNotIn('"Now"', rebuild)
        self.assertNotIn('"Action"', rebuild)

    def test_cursor_uses_provider_identity_and_estimated_usage_labels(self):
        self.assertIn('catalog[provider] ?? catalog["unknown-runtime"]', self.source)
        self.assertIn('case "runtime.cursor": return "cursorarrow"', self.source)
        self.assertIn('let costAvailable = metricAvailable(availability, "cost")', self.source)
        self.assertIn('let tokensAvailable = metricAvailable(availability, "tokens")', self.source)
        self.assertIn('let estimatedTokens = bool(source["token_estimate"])', self.source)
        self.assertIn('var menuBarTodaySpendLabel: String', self.source)

    def test_live_polling_bypasses_cached_menubar_responses(self):
        self.assertIn('cachePolicy: .reloadIgnoringLocalCacheData', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")', self.source)
        self.assertIn('request.setValue("no-cache", forHTTPHeaderField: "Pragma")', self.source)

    def test_native_menu_surfaces_updates_and_installs_without_the_dashboard(self):
        for marker in (
            "struct SoftwareUpdateSnapshot",
            'dict["software_update"] as? [String: Any]',
            'title: "New update available",',
            'action: #selector(installSoftwareUpdate),',
            'NSMenuItem(title: "Updating Token Meter...", action: nil',
            'title: "Update needs attention",',
            'action: #selector(openUpdateSettings),',
            '@objc private func installSoftwareUpdate()',
            'request.httpMethod = "POST"',
            'request.setValue("application/json", forHTTPHeaderField: "Content-Type")',
            'request.setValue(softwareUpdate.actionToken, forHTTPHeaderField: "X-Token-Meter-Action")',
            'request.httpBody = Data("{}".utf8)',
            '@objc private func openUpdateSettings()',
            'tokenMeterUpdateSettingsURL',
        ):
            self.assertIn(marker, self.source)
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        self.assertLess(
            rebuild.index("addQuickActions()"),
            rebuild.index("addSoftwareUpdateItem()"),
        )
        self.assertLess(
            rebuild.index("addSoftwareUpdateItem()"),
            rebuild.index("addSessionPicker()"),
        )
        self.assertIn("if self.softwareUpdate.isUpdating", self.source)
        self.assertIn('print("native-update=available updating attention")', self.source)

    def test_status_item_owns_a_clean_native_menu(self):
        app = self.source[
            self.source.index("final class TokenMeterMenuBar"):
            self.source.index("private func string(")
        ]
        self.assertIn('private let menu = NSMenu(title: "Token Meter")', app)
        self.assertIn('statusItem.menu = menu', app)
        self.assertIn('menu.delegate = self', app)
        self.assertIn('func menuNeedsUpdate(_ menu: NSMenu)', app)
        self.assertIn('func menuWillOpen(_ menu: NSMenu)', app)
        self.assertIn('func menuDidClose(_ menu: NSMenu)', app)
        self.assertIn('private var menuRefreshPending = false', app)
        self.assertIn('guard !menuIsOpen else', app)
        self.assertIn('func runMenuSmoke() throws', app)
        self.assertIn('TOKEN_METER_MENUBAR_MENU_SMOKE', self.source)
        self.assertIn('native-menu=ready sessions=direct follow-latest=direct', self.source)
        self.assertIn('native-quick-actions=Dashboard,Spend,Tools,Settings height=26', self.source)
        self.assertNotIn('private let popover = NSPopover()', app)
        self.assertNotIn('popover.show(relativeTo: button.bounds', app)
        self.assertIn('NSSegmentedControl', app)

    def test_native_menu_exposes_direct_session_following(self):
        self.assertIn('let selectedSessionID = string(selection["selected_id"])', self.source)
        self.assertIn('let selectedSession = recentSessions.first { $0.id == snapshot.selectedSessionID }', self.source)
        self.assertIn('NSMenuItem(title: "Follow latest", action: #selector(followLatest)', self.source)
        self.assertIn('for session in recentSessions.prefix(5)', self.source)
        self.assertIn('NSMenuItem(title: session.menuTitle, action: #selector(pinSession(_:))', self.source)
        self.assertIn('item.state = pinnedSessionID == session.id ? .on : .off', self.source)
        self.assertIn('item.representedObject = session.id', self.source)
        self.assertIn('@objc private func followLatest()', self.source)
        self.assertIn('@objc private func pinSession(_ sender: NSMenuItem)', self.source)
        self.assertIn('let maximumNameLength = 36', self.source)

    def test_provider_limits_and_budgets_use_separate_native_submenus(self):
        self.assertIn('NSMenuItem(title: "Provider limits (Beta)", action: nil', self.source)
        self.assertIn('description: "Provider limits (Beta)"', self.source)
        self.assertIn('limitsItem.submenu = makeLimitsMenu()', self.source)
        self.assertIn('private func makeLimitsMenu() -> NSMenu', self.source)
        self.assertIn('for window in provider.windows', self.source)
        self.assertIn('"\\(window.label) · \\(window.percentLabel) used"', self.source)
        self.assertIn('coverageNote: string(dict["coverage_note"]) ?? ""', self.source)
        self.assertIn('coverage=\\(coverage)', self.source)
        self.assertIn('case "opencode": return "gearshape.2"', self.source)
        self.assertIn('let budget = double(row["allocation"])', self.source)
        self.assertIn('NSMenuItem(title: "Budgets", action: nil', self.source)
        self.assertIn('budgetsItem.submenu = makeBudgetsMenu()', self.source)
        self.assertIn('private func makeBudgetsMenu() -> NSMenu', self.source)
        self.assertIn('let overallTitle = budget.configured', self.source)
        self.assertIn('"Overall · \\(formatMoney(budget.spend)) of \\(formatMoney(budget.budget)) · \\(Int(budget.percent.rounded()))% used"', self.source)
        self.assertIn('for scope in budget.scopes where scope.id != "overall"', self.source)
        self.assertIn('"\\(scope.label) · \\(formatMoney(scope.spend)) of \\(formatMoney(scope.budget)) · \\(Int(scope.percent.rounded()))% used"', self.source)
        self.assertIn('"\\(scope.label) · Not set"', self.source)
        self.assertIn('action: #selector(openBudgetSettings)', self.source)
        self.assertIn('Native budgets omitted the OpenCode monthly budget.', self.source)
        self.assertIn('overallBudgetItems.count == 1', self.source)
        self.assertIn('budgetActionItems.count == runtimeBudgetScopes.count + 1', self.source)

        limits = self.source[
            self.source.index("    private func makeLimitsMenu()"):
            self.source.index("    private func makeBudgetsMenu()")
        ]
        self.assertNotIn("monthlyBudget", limits)
        self.assertNotIn("budgetScope", limits)
        self.assertNotIn("openBudgetSettings", limits)

    def test_menu_bar_settings_are_visible_and_quota_threshold_is_explicit(self):
        self.assertIn('case .settings: return "Settings"', self.source)
        self.assertIn('case .settings:\n                self.openSettings()', self.source)
        self.assertIn('NSMenuItem(title: "Menu bar settings", action: nil', self.source)
        self.assertIn('settingsItem.submenu = makeSettingsMenu()', self.source)
        self.assertNotIn('NSMenuItem(title: "More", action: nil', self.source)
        self.assertIn('NSMenuItem(title: "Open Settings", action: #selector(openSettings)', self.source)
        self.assertIn('addAction("Quit Token Meter", #selector(quit))', self.source)
        self.assertNotIn('Quit Token Meter Menubar', self.source)
        self.assertIn('NSMenuItem(title: "Quota alert threshold (\\(quotaAlertThreshold)%)"', self.source)
        self.assertIn('NSMenu(title: "Quota alert threshold")', self.source)
        self.assertIn('NSMenuItem(title: "\\(threshold)% used"', self.source)
        self.assertNotIn('NSMenuItem(title: "Warn at"', self.source)

    def test_configurable_title_and_quota_notifications_have_safe_defaults(self):
        self.assertIn('enum TitleMetric: String, CaseIterable', self.source)
        self.assertIn('metrics = [.cost, .speed]', self.source)
        self.assertIn('for metric in TitleMetric.allCases', self.source)
        self.assertIn('#selector(toggleTitleMetric(_:))', self.source)
        self.assertIn('TitleMetric.allCases.filter(titleMetrics.contains).map(\\.rawValue)', self.source)
        self.assertIn('let presentation = selectedStatusTitlePresentation()', self.source)
        self.assertIn('let title = presentation.accessibilityTitle', self.source)
        self.assertIn('let text = snapshot.menuBarCostLabel', self.source)
        self.assertIn('let text = snapshot.menuBarOutputSpeedLabel', self.source)
        self.assertIn('let text = snapshot.menuBarContextLabel', self.source)
        self.assertIn('let text = snapshot.model', self.source)
        self.assertIn('case .todaySpend:', self.source)
        self.assertIn('let text = snapshot.menuBarTodaySpendLabel', self.source)
        self.assertIn('case .todaySpend: return "Today spend"', self.source)
        self.assertIn(
            'return "\\(String(format: "$%.0f", todaySpendTotalCost))\\(todaySpendEstimated ? " est" : "")"',
            self.source,
        )
        self.assertIn('parts[parts.count - 1].text = "· " + parts[parts.count - 1].text', self.source)
        self.assertIn('metrics.insert(.todaySpend)', self.source)
        self.assertIn('todaySpendTitlePreferenceDefaultsKey', self.source)
        self.assertIn('todaySpendEstimated ? " est" : ""', self.source)
        self.assertIn('metrics = [.limits]', self.source)
        self.assertIn(
            'tokenMeterDefaults.set(true, forKey: todaySpendTitlePreferenceDefaultsKey)',
            self.source,
        )
        self.assertIn('let accessibilityText = limitsStatusTitle()', self.source)
        self.assertIn('symbol: runtimeCatalog[constrained.provider.id]?.symbol', self.source)
        self.assertNotIn('private func compactStatusTitle()', self.source)
        self.assertIn('private func limitsStatusTitle() -> String?', self.source)
        self.assertIn('return "\\(constrained.provider.label) \\(constrained.window.percentLabel)"', self.source)
        self.assertNotIn('constrained.window.compactKind', self.source)
        self.assertIn('Native menu still exposes hover tooltips.', self.source)
        self.assertIn('guard statusItem.button?.toolTip == nil, !menuContainsToolTip(menu)', self.source)
        self.assertIn('if tokenMeterDefaults.object(forKey: quotaAlertsEnabledDefaultsKey) == nil { return true }', self.source)
        self.assertIn('let thresholds = Array(Set([quotaAlertThreshold, 95, 100])).sorted()', self.source)
        self.assertIn('guard var previous = quotaNotificationStates[key] else', self.source)
        self.assertIn('quotaAlertsEnabled && quotaObservationEstablished', self.source)
        self.assertIn('quotaObservationEstablished = true', self.source)
        self.assertIn('process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")', self.source)
        self.assertIn('"--", title, body', self.source)

    def test_status_item_uses_a_compact_cross_display_hit_target(self):
        self.assertIn('NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)', self.source)
        self.assertIn('private func splunkChevronImage(', self.source)
        self.assertIn('private func statusTitleImage(_ presentation: StatusTitlePresentation) -> NSImage', self.source)
        self.assertIn('private func runtimeMarkImage(symbol: String', self.source)
        self.assertIn('var providerSymbol: String', self.source)
        self.assertIn('var providerAccessibilityText: String', self.source)
        self.assertIn('symbol: presentation.providerSymbol', self.source)
        self.assertIn('let providerSymbol = runtimeCatalog[snapshot.provider]?.symbol ?? "runtime.generic"', self.source)
        self.assertIn('let presentationWithoutLimits = selectedStatusTitlePresentation()', self.source)
        self.assertIn('presentationWithoutLimits.providerSymbol == expectedProviderSymbol', self.source)
        self.assertIn('button.image = splunkChevronImage()', self.source)
        self.assertNotIn('systemSymbolName: "waveform.path.ecg"', self.source)
        self.assertIn('button.imagePosition = .imageOnly', self.source)
        self.assertIn('.foregroundColor: NSColor.black', self.source)
        self.assertIn('image.isTemplate = true', self.source)
        self.assertIn('let titleImage = statusTitleImage(presentation)', self.source)
        self.assertIn('statusItem.length = titleImage.size.width + 8', self.source)
        self.assertIn('button.contentTintColor = nil', self.source)
        self.assertIn('button.setAccessibilityLabel("Token Meter")', self.source)
        self.assertIn('button.setAccessibilityValue(title)', self.source)
        self.assertNotIn('button.toolTip =', self.source)
        self.assertIn('button.contentTintColor = budgetExceeded', self.source)
        self.assertIn('enum StatusDisplayMode: String, CaseIterable', self.source)
        self.assertIn('StatusDisplayMode.text.rawValue', self.source)
        self.assertIn('screen.visibleFrame.width < 1200 ? .icon : .text', self.source)
        self.assertIn('private func selectedStatusTitle() -> String', self.source)
        self.assertIn('let renderedTitle = statusItem.button?.accessibilityValue() as? String', self.source)
        self.assertIn('renderedTitle == expectedTitle', self.source)
        self.assertIn('titleMetrics = Set(TitleMetric.allCases)', self.source)
        self.assertIn('titleMetrics = originalTitleMetrics', self.source)
        self.assertIn('statusItem.autosaveName = statusItemAutosaveName', self.source)
        self.assertIn('"NSStatusItem Preferred Position \\(statusItemAutosaveName)"', self.source)
        self.assertIn('statusItemInitialPreferredPosition = 50', self.source)
        self.assertIn('object(forKey: statusItemPreferredPositionDefaultsKey) == nil', self.source)
        self.assertNotIn('statusTitleColor(for:', self.source)
        self.assertNotIn('NSWorkspace.didActivateApplicationNotification', self.source)

    def test_native_menu_omits_cost_and_context_below_recent_sessions(self):
        rebuild = self.source[
            self.source.index("    private func rebuildMenu()"):
            self.source.index("    private var activeShortcutKeyCode")
        ]
        sessions_position = rebuild.index('addSessionPicker()')
        limits_position = rebuild.index('let limitsItem = NSMenuItem(title: "Provider limits (Beta)"')
        self.assertLess(sessions_position, limits_position)
        self.assertNotIn('addMetricRow("Cost", snapshot.costLabel', rebuild)
        self.assertNotIn('"Context",\n                contextDetail', rebuild)
        self.assertNotIn('addMetricRow("Tokens"', rebuild)
        self.assertNotIn('addMetricRow("Cache"', rebuild)
        self.assertNotIn('addMetricRow("Output', rebuild)
        self.assertNotIn('ExecutionTraceView', rebuild)

    def test_global_toggle_shortcut_is_configurable_and_persisted(self):
        self.assertIn('import Carbon.HIToolbox', self.source)
        self.assertIn('enum MenuBarShortcut: String, CaseIterable', self.source)
        self.assertIn('case .controlOptionT: return "⌃⌥T"', self.source)
        self.assertIn('private let globalShortcutDefaultsKey = "TokenMeterGlobalShortcut"', self.source)
        self.assertIn('RegisterEventHotKey(', self.source)
        self.assertIn('delegate.openMenu()', self.source)
        self.assertIn('NSMenuItem(title: "Keyboard shortcut"', self.source)
        self.assertIn('case .custom: return "Custom…"', self.source)
        self.assertIn('@objc private func configureCustomShortcut(_ sender: NSMenuItem)', self.source)
        self.assertIn('NSEvent.addLocalMonitorForEvents(matching: .keyDown)', self.source)
        self.assertIn('tokenMeterDefaults.set(shortcut.rawValue, forKey: globalShortcutDefaultsKey)', self.source)

    def test_native_menu_header_has_no_splunk_wordmark(self):
        header = self.source[
            self.source.index("    private func addHeader()"):
            self.source.index("    private func addSessionPicker()")
        ]
        self.assertIn('titleText = selectedSession?.identifier ?? snapshot.menuTitle', header)
        self.assertIn('"Following latest"', header)
        self.assertIn('"Following this session"', header)
        self.assertNotIn('splunkWordmarkImage', header)
        self.assertNotIn('logo', header)

    def test_monthly_budget_status_and_transition_alerts_are_native(self):
        for marker in (
            'struct MonthlyBudget',
            'let budgetExceeded = monthlyBudget?.anyExceeded == true',
            'description: budgetExceeded ? "Budget alert" : "Budgets"',
            'budgetsItem.submenu = makeBudgetsMenu()',
            'tokenMeterBudgetSettingsURL',
            'action: #selector(openBudgetSettings)', 'private func evaluateBudgetNotifications()',
            'budgetNotificationStatesDefaultsKey', 'previous.month == budget.month',
            'firedThresholds: Set(budget.thresholds.filter',
            'if budget.nativeNotifications', 'monthly budget reached',
            'var exceeded: Bool { configured && percent >= 100 }',
            'var anyExceeded: Bool { exceeded || !exceededRuntimeScopes.isEmpty }',
            'if let scope = exceededRuntimeScopes.first',
            'return "\\(scope.label) · \\(Int(scope.percent.rounded()))%"',
            'budgetExceededMonthsDefaultsKey',
            'budgetExceededNotificationMonths',
            'title: "Overall monthly budget exceeded"',
            'warning: monthlyBudget?.anyExceeded == true',
            'button.contentTintColor = budgetExceeded',
            'NSColor.systemRed',
            'button.setAccessibilityLabel("Token Meter")',
            'let activeTitle = budget?.anyExceeded == true ? "⚠︎ \\(baseTitle)" : baseTitle',
            'print("budget-state=\\(budget?.compactLabel ?? "unconfigured") exceeded=\\(budget?.anyExceeded == true)")',
        ):
            self.assertIn(marker, self.source)
        self.assertNotIn("· over budget", self.source)
        self.assertNotIn(
            'monthlyBudget?.exceeded == true ? NSColor.systemRed : NSColor.labelColor',
            self.source,
        )
        self.assertNotIn(
            'valueColor: budget.exceeded ? .systemRed : .labelColor',
            self.source,
        )


class TrayLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        tray_path = Path(meter.__file__).with_name("menubar").joinpath("token_meter_tray.py")
        spec = importlib.util.spec_from_file_location("token_meter_tray", tray_path)
        cls.tray = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tray)

    def test_dashboard_url_opens_sessions_unless_pinned(self):
        self.assertEqual(
            self.tray.dashboard_url("#sessions", pinned_session=None, include_pinned_session=False),
            "http://127.0.0.1:8722/#sessions",
        )
        self.assertEqual(
            self.tray.dashboard_url("#summary", pinned_session="abc"),
            "http://127.0.0.1:8722/sessions/abc#summary",
        )

    def test_tray_status_title_uses_selected_metrics_and_budget_warning(self):
        state = {
            "availability": {"cost": True, "throughput": True, "cache": True, "tokens": True},
            "source": {"label": "Claude", "token_estimate": False},
            "total_cost": 1.25,
            "model": "claude-test",
            "context": {"latest_pct": 0.42},
            "throughput": {"available": True, "output_tps": 12.3},
        }
        providers = [{
            "id": "claude",
            "label": "Claude",
            "status": "ok",
            "stale": False,
            "windows": [{"id": "weekly", "kind": "weekly", "label": "Weekly", "used_percent": 88}],
        }]
        budget = {
            "configured": True,
            "percent": 110,
            "spend": 11,
            "budget": 10,
            "month": "2026-07",
            "lower_bound": False,
            "scopes": [{
                "id": "overall", "label": "Overall", "spend": 11, "budget": 10, "percent": 110,
            }],
        }
        title = self.tray.tray_status_title(
            state, {"cost", "speed", "limits"}, providers, budget,
        )
        self.assertTrue(title.startswith("⚠︎ "))
        self.assertIn("$1.25", title)
        self.assertIn("12.3 tok/s", title)
        self.assertIn("Claude 88% · weekly", title)

    def test_budget_notifications_fire_on_threshold_crossing(self):
        budget = {
            "configured": True,
            "month": "2026-07",
            "percent": 0.85,
            "spend": 85,
            "budget": 100,
            "lower_bound": False,
            "native_notifications": True,
            "thresholds": [80, 90, 100],
            "scopes": [{
                "id": "overall", "label": "Overall", "spend": 85, "budget": 100, "percent": 85,
            }],
        }
        settings = dict(self.tray.DEFAULT_STATE)
        settings["budget_notification_states"] = {
            "overall": {
                "month": "2026-07",
                "last_percent": 75,
                "fired_thresholds": [],
            },
        }
        first = self.tray.evaluate_budget_notifications(budget, settings)
        second = self.tray.evaluate_budget_notifications(budget, settings)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0], "Overall monthly budget reached 80%")
        self.assertEqual(second, [])


class TraySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(meter.__file__).with_name("menubar").joinpath("token_meter_tray.py").read_text()
        )

    def test_linux_tray_matches_native_dashboard_routing_and_actions(self):
        for marker in (
            "def open_dashboard(self):",
            'self.open_url("#sessions", include_pinned_session=False)',
            'self.open_url("#summary")',
            "Open Budget Settings",
            "#settings-budgets",
            "Model Prices",
            "#model-pricing",
        ):
            self.assertIn(marker, self.source)

    def test_linux_tray_does_not_advertise_removed_session_trace_view(self):
        self.assertNotIn('"Open Trace"', self.source)
        self.assertNotIn('self.open_url("#activity")', self.source)

    def test_linux_tray_exposes_tabs_title_metrics_and_notifications(self):
        for marker in (
            '("overview", "All")',
            '("claude", "Claude")',
            "Menu bar title",
            "Quota notifications",
            "Warn at",
            "evaluate_quota_notifications",
            "evaluate_budget_notifications",
            '["notify-send", title, body]',
            "Output speed:",
            "Monthly budget:",
            "Most constrained:",
        ):
            self.assertIn(marker, self.source)


class ProviderQuotaTests(unittest.TestCase):
    def tearDown(self):
        meter.reset_provider_quota_cache()

    def test_pace_forecast_distinguishes_runout_reserve_and_early_window(self):
        now = 1_000_000.0
        base = {"window_seconds": 1000, "reset_at": now + 500}

        runout = meter.quota_pace({**base, "used_percent": 70}, now=now)
        reserve = meter.quota_pace({**base, "used_percent": 30}, now=now)
        on_pace = meter.quota_pace({**base, "used_percent": 50}, now=now)
        early = meter.quota_pace({"window_seconds": 1000, "reset_at": now + 980,
                                  "used_percent": 20}, now=now)

        self.assertEqual(runout["state"], "deficit")
        self.assertFalse(runout["will_last_to_reset"])
        self.assertIn("runs out in", runout["summary"])
        self.assertEqual(reserve["state"], "reserve")
        self.assertTrue(reserve["will_last_to_reset"])
        self.assertEqual(on_pace["state"], "on_pace")
        self.assertIsNone(early)

    def test_codex_parser_preserves_main_and_named_spark_windows(self):
        now = 1_000_000.0
        payload = {
            "rateLimits": {
                "planType": "business",
                "primary": {"usedPercent": 12, "windowDurationMins": 300,
                            "resetsAt": now + 1800},
                "secondary": {"usedPercent": 34, "windowDurationMins": 10080,
                              "resetsAt": now + 3600},
            },
            "rateLimitsByLimitId": {
                "codex-spark": {
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {"usedPercent": 56, "windowDurationMins": 300,
                                "resetsAt": now + 1800},
                    "secondary": {"usedPercent": 78, "windowDurationMins": 10080,
                                  "resetsAt": now + 3600},
                },
            },
        }

        result = meter.parse_codex_quota(payload, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "business")
        self.assertEqual(
            [(row["label"], row["used_percent"]) for row in result["windows"]],
            [("Session", 12.0), ("Weekly", 34.0),
             ("Spark session", 56.0), ("Spark weekly", 78.0)],
        )
        self.assertEqual(result["coverage_note"], "")

    def test_codex_parser_does_not_fabricate_absent_main_windows(self):
        result = meter.parse_codex_quota({
            "rate_limit": {"primary_window": None, "secondary_window": None},
            "additional_rate_limits": [{
                "metered_feature": "codex_spark",
                "limit_name": "GPT-5.3-Codex-Spark",
                "rate_limit": {
                    "primary_window": {"used_percent": 4, "limit_window_seconds": 18000,
                                       "reset_at": 1_001_000},
                    "secondary_window": {"used_percent": 9, "limit_window_seconds": 604800,
                                         "reset_at": 1_002_000},
                },
            }],
        }, source="Codex OAuth API", now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Spark session", "Spark weekly"])
        self.assertIn("Regular Session and Weekly limits were not reported by Codex",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_codex_app_server_uses_wrapper_safe_launchagent_environment(self):
        process = mock.Mock()
        process.stdin = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = 0
        selector = mock.Mock()
        with mock.patch.object(meter, "provider_cli_path", return_value="/nvm/bin/codex"), \
                mock.patch.object(meter, "agent_client_environment", return_value={"PATH": "/nvm/bin"}) as environment, \
                mock.patch.object(meter.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(meter.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(meter, "_rpc_read_response", side_effect=[{}, {"rateLimits": {}}]):
            result = meter.codex_app_server_rate_limits()

        environment.assert_called_once_with("/nvm/bin/codex")
        self.assertEqual(popen.call_args.kwargs["env"], {"PATH": "/nvm/bin"})
        self.assertEqual(result, {"rateLimits": {}})

    def test_claude_parser_maps_reported_and_scoped_windows(self):
        now = 1_000_000.0
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": now + 3600},
            "seven_day": {"utilization": 23, "resets_at": now + 7200},
            "seven_day_opus": {"utilization": 41, "resets_at": now + 7200},
            "limits": [{
                "kind": "weekly_scoped", "is_active": True, "percent": 52,
                "resets_at": now + 7200,
                "scope": {"model": {"display_name": "Sonnet 4.5"}},
            }],
        }, credentials={"subscriptionType": "max"}, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"], "max")
        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Weekly", "Opus weekly", "Sonnet 4.5 weekly"])
        self.assertEqual(result["coverage_note"], "")

    def test_claude_scoped_weekly_does_not_hide_missing_regular_weekly_limit(self):
        result = meter.parse_claude_quota({
            "five_hour": {"utilization": 8, "resets_at": 1_001_000},
            "limits": [{
                "kind": "weekly_scoped", "percent": 52, "resets_at": 1_002_000,
                "scope": {"model": {"display_name": "Sonnet"}},
            }],
        }, now=1_000_000)

        self.assertEqual([row["label"] for row in result["windows"]],
                         ["Session", "Sonnet weekly"])
        self.assertIn("Weekly limit was not reported by Claude", result["coverage_note"])

    def test_third_party_claude_auth_is_explicitly_unavailable(self):
        with mock.patch.object(meter, "claude_auth_status", return_value={
            "loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock",
        }), mock.patch.object(meter, "claude_oauth_credentials") as credentials:
            result = meter.load_claude_quota(now=1_000_000)

        credentials.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["plan"], "Bedrock")
        self.assertIn("not exposed", result["error"])
        self.assertIn("Session and Weekly limits were not reported by Claude",
                      result["coverage_note"])

    def test_cursor_parser_labels_monthly_individual_cap(self):
        now = 1_000_000.0
        result = meter.parse_cursor_quota({
            "membershipType": "enterprise",
            "billingCycleStart": now - 1000,
            "billingCycleEnd": now + 2000,
            "individualUsage": {
                "plan": {"used": 0, "limit": 0},
                "overall": {"used": 987, "limit": 100000},
            },
        }, now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["windows"][0]["kind"], "monthly")
        self.assertEqual(result["windows"][0]["label"], "Plan")
        self.assertAlmostEqual(result["windows"][0]["used_percent"], 0.99, places=2)
        self.assertEqual(result["windows"][0]["window_seconds"], 3000)
        self.assertIn("Session and Weekly limits were not reported by Cursor",
                      result["coverage_note"])
        self.assertIn("missing does not mean 0%", result["coverage_note"])

    def test_cache_marks_old_provider_data_stale_and_recomputes_pace(self):
        row = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 50,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        with meter._quota_lock:
            meter._quota_cache["codex"] = {**row, "fetched_at": 1_000_000,
                                            "attempted_at": 1_000_000}

        result = meter.provider_quota_snapshots(
            now=1_000_601, loaders={"codex": mock.Mock()}, start_refresh=False,
        )[0]

        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["stale"])
        self.assertEqual(result["age_seconds"], 601)

    def test_empty_cache_returns_loading_and_starts_only_one_worker(self):
        loader = mock.Mock()
        worker = mock.Mock()
        with mock.patch.object(meter.threading, "Thread", return_value=worker) as thread:
            first = meter.provider_quota_snapshots(now=1_000_000, loaders={"codex": loader})
            second = meter.provider_quota_snapshots(now=1_000_001, loaders={"codex": loader})

        self.assertEqual(first[0]["status"], "loading")
        self.assertEqual(second[0]["status"], "loading")
        thread.assert_called_once()
        worker.start.assert_called_once()

    def test_refresh_retains_last_good_windows_and_sanitizes_unexpected_errors(self):
        good = meter.quota_provider("codex", "Codex", "ok", "test", windows=[
            meter.quota_window("codex", "codex-session", "session", "Session", 44,
                               window_seconds=1000, reset_at=1_001_000, now=1_000_000),
        ])
        meter.refresh_provider_quota("codex", lambda now: good, now=1_000_000)

        def fail(now):
            raise RuntimeError("secret bearer token")

        result = meter.refresh_provider_quota("codex", fail, now=1_000_100)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["windows"][0]["used_percent"], 44.0)
        self.assertEqual(result["fetched_at"], 1_000_000)
        self.assertEqual(result["attempted_at"], 1_000_100)
        self.assertEqual(result["error"], "Provider quota refresh failed.")
        self.assertNotIn("secret", result["error"])


class HealthStateTests(unittest.TestCase):
    def test_health_uses_cached_inventory_without_discovering_sessions(self):
        inventory = {
            "ready": True,
            "sources": (),
            "count": 2400,
            "clients": {"codex": 2300, "claude_code": 100},
            "updated_at": 1,
        }
        with mock.patch.object(meter, "STATE", {"source": {"id": "ready"}}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(meter, "page_path", return_value="/runtime/page.html"), \
                mock.patch.object(
                    meter, "all_session_sources",
                    side_effect=AssertionError("health must not discover sessions"),
                ):
            payload, status = meter.health_state()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["state_ready"])
        self.assertTrue(payload["inventory_ready"])
        self.assertEqual(payload["sources"], 2400)
        self.assertEqual(payload["source_clients"], {"codex": 2300, "claude_code": 100})

    def test_health_marks_undiscovered_inventory_unavailable_instead_of_zero(self):
        inventory = {
            "ready": False, "sources": (), "count": None, "clients": {}, "updated_at": None,
        }
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(meter, "page_path", return_value="/runtime/page.html"):
            payload, status = meter.health_state()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["state_ready"])
        self.assertFalse(payload["inventory_ready"])
        self.assertIsNone(payload["sources"])
        self.assertEqual(payload["source_clients"], {})

    def test_current_state_returns_loading_without_competing_with_watcher(self):
        inventory = {
            "ready": True, "sources": (), "count": 5000, "clients": {"codex": 5000},
            "updated_at": 1,
        }
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), \
                mock.patch.object(
                    meter, "newest_source",
                    side_effect=AssertionError("state request must not rediscover sessions"),
                ), \
                mock.patch.object(
                    meter, "recompute",
                    side_effect=AssertionError("state request must not rebuild history"),
                ):
            payload = meter.current_state()

        self.assertTrue(payload["loading"])
        self.assertIn("5,000 local sessions", payload["message"])

    def test_watcher_publishes_ready_empty_state_when_no_logs_exist(self):
        published = []
        inventory_updates = []
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "all_session_sources", return_value=[]), \
                mock.patch.object(
                    meter, "publish_source_inventory",
                    side_effect=lambda sources: inventory_updates.append(list(sources)),
                ), \
                mock.patch.object(
                    meter, "refresh_cross_session_state",
                    return_value={"sessions": [], "total_sessions": 0},
                ), \
                mock.patch.object(meter, "publish", side_effect=published.append), \
                mock.patch.object(meter.time, "monotonic", return_value=3), \
                mock.patch.object(meter.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                meter.watcher()

        self.assertEqual(inventory_updates, [[]])
        self.assertEqual(len(published), 1)
        self.assertFalse(published[0]["loading"])
        self.assertEqual(published[0]["xsession"]["total_sessions"], 0)


class MenubarSessionTests(unittest.TestCase):
    def test_menubar_state_projects_today_cross_session_spend(self):
        today = datetime.date.today().isoformat()
        source = {
            "id": "live", "provider": "codex", "label": "Codex",
            "path": "/tmp/live.jsonl", "session": "live.jsonl", "project": "/repo",
        }
        state = {
            "provider": "codex", "source": source, "session": "live.jsonl",
            "project": "/repo", "context": {}, "cache": {}, "executions": [], "insights": [],
        }
        cross = {
            "daily": [{
                "day": today, "cost": 1.25,
                "availability": {"cost": True},
                "provenance": {"estimated_cost": 0.25},
            }],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(meter, "cached_session_sources", return_value=([source], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": cross}, clear=False):
            payload = meter.menubar_state()

        self.assertEqual(payload["today_spend"], {
            "available": True,
            "total_cost": 1.25,
            "estimated": True,
        })

    def test_menubar_today_spend_keeps_unavailable_cost_unavailable(self):
        today = datetime.date.today()

        projection = meter.menubar_today_spend({"daily": [{
            "day": today.isoformat(), "cost": 0,
            "availability": {"cost": False},
        }]}, today=today)

        self.assertEqual(projection, {
            "available": False,
            "total_cost": None,
            "estimated": False,
        })

    def test_recommendations_always_target_the_single_run_surface(self):
        states = (
            {"context": {"latest_pct": 0.9}, "executions": [], "insights": []},
            {"context": {}, "last_turn_cost": 0, "executions": [],
             "insights": [{"kind": "warn", "text": "Check this signal"}]},
        )
        for state in states:
            with self.subTest(state=state):
                self.assertEqual(meter.menubar_recommendation(state)["target"], "summary")

    def test_menubar_reuses_watcher_state_for_the_live_selected_session(self):
        source = {
            "id": "live", "provider": "codex", "label": "Codex",
            "path": "/tmp/live.jsonl", "session": "live.jsonl",
            "project": "/repo", "mtime": 1,
        }
        state = {
            "provider": "codex", "source": source, "session": "live.jsonl",
            "project": "/repo", "context": {}, "cache": {},
            "executions": [], "insights": [],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(
                    meter, "cached_session_sources", return_value=([source], True),
                ), \
                mock.patch.object(
                    meter, "recompute",
                    side_effect=AssertionError("menu poll must reuse watcher state"),
                ), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("live")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selection"]["selected_id"], "live")

    def test_context_pulse_is_bounded_numeric_only_and_does_not_leak_trace_content(self):
        state = {
            "executions": [
                {"context_pct": -0.5, "detail": "private prompt"},
                {"context_pct": 0.251234, "tool_input": "private arguments"},
                {"context_pct": 1.5, "path": "/private/logs/trace.jsonl"},
                {"context_pct": None},
                {"context_pct": "not a number"},
                {"context_pct": True},
            ],
        }
        pulse = meter.menubar_context_pulse(state, limit=2)

        self.assertEqual(pulse, [0.2512, 1.0])
        encoded = json.dumps(pulse)
        self.assertNotIn("private", encoded)
        self.assertNotIn("trace.jsonl", encoded)

    def test_recent_sessions_are_limited_and_keep_an_older_pin_visible(self):
        sources = [{
            "id": f"s{idx}", "provider": "codex" if idx % 2 else "claude",
            "label": "Codex" if idx % 2 else "Claude Code", "path": f"/tmp/s{idx}.jsonl",
            "session": f"s{idx}.jsonl", "project": f"/repo/project-{idx}", "mtime": idx,
            "title": f"Task {idx}",
        } for idx in range(7)]

        latest = meter.menubar_recent_sessions(sources)
        pinned = meter.menubar_recent_sessions(sources, selected_id="s0")

        self.assertEqual([row["id"] for row in latest], ["s6", "s5", "s4", "s3", "s2"])
        self.assertEqual([row["id"] for row in pinned], ["s0", "s6", "s5", "s4", "s3"])
        self.assertEqual(pinned[0]["name"], "Task 0")
        self.assertNotIn("project", pinned[0])

    def test_recent_sessions_use_sanitized_summary_name_when_discovery_has_no_title(self):
        sources = [{
            "id": "claude-session", "provider": "claude", "label": "Claude Code",
            "path": "/tmp/claude-session.jsonl", "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "mtime": 10, "title": None,
        }]
        summaries = [{
            "id": "claude-session", "provider": "claude",
            "session_name": "Default model configuration opus 5",
        }]

        recent = meter.menubar_recent_sessions(sources, summaries=summaries)

        self.assertEqual(recent[0]["name"], "Default model configuration opus 5")

    def test_menubar_state_joins_recent_session_names_from_cross_session_summaries(self):
        source = {
            "id": "claude-session", "provider": "claude", "label": "Claude Code",
            "path": "/tmp/claude-session.jsonl", "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "mtime": 10, "title": None,
        }
        state = {
            "provider": "claude", "source": source, "session": "claude-session.jsonl",
            "project": "/repo/token-meter", "context": {}, "cache": {}, "trace": [],
            "insights": [], "executions": [], "throughput": {}, "live_throughput": {},
        }
        cross = {
            "current_sessions": [{
                "id": "claude-session", "provider": "claude",
                "session_name": "Default model configuration opus 5",
            }],
            "sessions": [],
        }
        with mock.patch.object(meter, "STATE", state), \
                mock.patch.object(meter, "cached_session_sources", return_value=([source], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": cross}, clear=False):
            payload = meter.menubar_state()

        self.assertEqual(
            payload["recent_sessions"][0]["name"],
            "Default model configuration opus 5",
        )

    def test_menubar_state_uses_requested_session_and_marks_pin(self):
        sources = [{
            "id": "pinned", "provider": "codex", "label": "Codex", "path": "/tmp/pinned.jsonl",
            "session": "pinned.jsonl", "project": "/repo/pinned-project", "mtime": 1, "title": "Pinned task",
        }]
        state = {
            "provider": "codex", "source": sources[0], "session": "pinned.jsonl",
            "project": "/repo/pinned-project", "context": {}, "cache": {}, "trace": [], "insights": [],
            "executions": [{"idx": 1, "model": "gpt-5.6-sol", "context_pct": 0.2}],
            "throughput": {"available": True, "output_tps": 42.5, "basis": "end_to_end",
                           "sample_count": 2, "timing_coverage": 0.75},
            "live_throughput": {"available": True, "output_tps": 18.25,
                                "basis": "live_end_to_end", "completed_steps": 3,
                                "measured_output_tokens": 365, "measured_seconds": 20},
        }
        with mock.patch.object(meter, "STATE", {"source": {"id": "live"}}), \
                mock.patch.object(meter, "cached_session_sources", return_value=(sources, True)), \
                mock.patch.object(meter, "cached_session_state", return_value=state), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("pinned")

        self.assertTrue(payload["selection"]["pinned"])
        self.assertFalse(payload["selection"]["missing"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertEqual(payload["recent_sessions"][0]["name"], "Pinned task")
        self.assertEqual(payload["model"], "gpt-5.6-sol")
        self.assertEqual(payload["provider_quotas"], [])
        self.assertEqual(payload["context_pulse"], [0.2])
        self.assertEqual(payload["project"], "pinned-project")
        self.assertEqual(payload["source"]["project"], "pinned-project")
        self.assertNotIn("/repo/pinned-project", json.dumps(payload))
        self.assertEqual(payload["throughput"], {
            "available": True, "output_tps": 42.5, "basis": "end_to_end",
            "sample_count": 2, "timing_coverage": 0.75,
        })
        self.assertEqual(payload["live_throughput"], {
            "available": True, "output_tps": 18.25, "basis": "live_end_to_end",
            "completed_steps": 3, "measured_output_tokens": 365,
            "measured_seconds": 20,
        })

    def test_cold_start_does_not_rebuild_all_history_for_each_menu_poll(self):
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "cached_session_sources", return_value=([], False)), \
                mock.patch.object(
                    meter, "all_session_sources",
                    side_effect=AssertionError("menu polling must not discover sessions"),
                ), \
                mock.patch.object(meter, "recompute") as recompute, \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state()

        recompute.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["provider_quotas"], [])

    def test_cold_start_does_not_recompute_a_persisted_pinned_session(self):
        sources = [{
            "id": "pinned", "provider": "codex", "label": "Codex",
            "path": "/tmp/pinned.jsonl", "session": "pinned.jsonl",
            "project": "/repo", "mtime": 1,
        }]
        with mock.patch.object(meter, "STATE", {}), \
                mock.patch.object(meter, "cached_session_sources", return_value=(sources, True)), \
                mock.patch.object(meter, "recompute") as recompute, \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]):
            payload = meter.menubar_state("pinned")

        recompute.assert_not_called()
        self.assertTrue(payload["selection"]["pinned"])
        self.assertEqual(payload["selection"]["selected_id"], "pinned")
        self.assertFalse(payload["selection"]["missing"])

    def test_menubar_uses_published_monthly_budget_when_cross_cache_rotates(self):
        budget = {
            "month": "2026-07", "configured": True, "budget": 1000,
            "spend": 420, "percent": 0.42, "settings": {
                "monthly_total": 1000, "allocations": {}, "thresholds": [80, 90, 100],
                "native_notifications": True,
            },
        }
        with mock.patch.object(meter, "STATE", {"xsession": {"budget": budget}}), \
                mock.patch.object(meter, "cached_session_sources", return_value=([], True)), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": None}, clear=False):
            payload = meter.menubar_state()
        self.assertEqual(payload["budget"], budget)


class DynamicCatalogTests(unittest.TestCase):
    def test_flattens_namespace_and_legacy_functions(self):
        catalog = meter.normalize_dynamic_tools([
            {
                "type": "namespace",
                "name": "codex_app",
                "tools": [
                    {"name": "open_page", "deferLoading": False, "description": "Open a page"},
                    {"name": "create_thread", "deferLoading": True, "description": "Create a thread"},
                ],
            },
            {"name": "mcp__jira__search", "namespace": "jira", "deferLoading": True},
        ])
        self.assertEqual([row["name"] for row in catalog], ["open_page", "create_thread", "mcp__jira__search"])
        self.assertEqual(catalog[0]["namespace"], "codex_app")
        self.assertEqual(catalog[2]["namespace"], "jira")
        self.assertEqual(catalog[2]["kind"], "mcp")
        self.assertEqual(meter.catalog_counts(catalog), {"advertised": 3, "eager": 1, "deferred": 2})

    def test_embedded_image_bytes_are_not_counted_as_text(self):
        chars = meter.observable_output_chars({"image_url": "data:image/png;base64," + "A" * 10000})
        self.assertLess(chars, 100)


class ClaudeDesktopDiscoveryTests(unittest.TestCase):
    def test_default_discovery_scans_standard_and_third_party_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            third_party = Path(tmp) / "Claude-3p"
            standard_metadata = standard / "claude-code-sessions" / "account" / "workspace" / "local_standard.json"
            third_party_metadata = third_party / "local-agent-mode-sessions" / "account" / "workspace" / "local_bedrock.json"
            standard_metadata.parent.mkdir(parents=True)
            third_party_metadata.parent.mkdir(parents=True)
            standard_metadata.write_text(json.dumps({"cliSessionId": "standard-cli"}))
            third_party_metadata.write_text(json.dumps({"cliSessionId": "bedrock-cli"}))

            with mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard), str(third_party)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(standard_metadata), str(third_party_metadata)})

    def test_default_discovery_recurses_below_known_session_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            third_party = Path(tmp) / "Claude-3p"
            nested_metadata = (
                standard / "local-agent-mode-sessions" / "account" / "workspace" /
                "additional-level" / "local_nested.json"
            )
            unrelated_metadata = standard / "unrelated" / "local_ignore.json"
            nested_metadata.parent.mkdir(parents=True)
            unrelated_metadata.parent.mkdir(parents=True)
            nested_metadata.write_text(json.dumps({"cliSessionId": "nested-cli"}))
            unrelated_metadata.write_text(json.dumps({"cliSessionId": "ignore-cli"}))

            with mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard), str(third_party)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(nested_metadata)})

    def test_default_discovery_prunes_the_non_session_skills_plugin_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            standard = Path(tmp) / "Claude"
            valid_metadata = (
                standard / "local-agent-mode-sessions" / "account" /
                "workspace" / "extra" / "local_session.json"
            )
            plugin_metadata = (
                standard / "local-agent-mode-sessions" / "skills-plugin" /
                "bundle" / "nested" / "local_not-a-session.json"
            )
            valid_metadata.parent.mkdir(parents=True)
            plugin_metadata.parent.mkdir(parents=True)
            valid_metadata.write_text(json.dumps({"cliSessionId": "session"}))
            plugin_metadata.write_text(json.dumps({"cliSessionId": "plugin"}))

            with mock.patch.object(
                    meter, "CLAUDE_DESKTOP_DATA_ROOTS", [str(standard)]):
                paths = set(meter.claude_desktop_metadata_paths())

        self.assertEqual(paths, {str(valid_metadata)})

    def test_indexes_desktop_metadata_by_cli_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "account" / "workspace" / "local_desktop-session.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "sessionId": "local_desktop-session",
                "cliSessionId": "cli-session-id",
                "cwd": "/tmp/project",
                "title": "Desktop project task",
                "model": "claude-fable-5",
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)
        self.assertIn("cli-session-id", idx)
        self.assertEqual(idx["cli-session-id"]["label"], "Claude Desktop")
        self.assertEqual(idx["cli-session-id"]["desktop_session_id"], "local_desktop-session")
        self.assertEqual(idx["cli-session-id"]["cwd"], "/tmp/project")
        self.assertEqual(idx["cli-session-id"]["title"], "Desktop project task")

    def test_discovers_no_project_agent_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            trace = root / "local_agent-session" / ".claude" / "projects" / "outputs" / "cli-agent-id.jsonl"
            trace.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "title": "No-project task",
                "lastActivityAt": 123456,
            }))
            trace.write_text("{}\n")
            idx = meter.claude_desktop_index(tmp)
            sources = meter.claude_local_agent_sources(idx)
        self.assertEqual(idx["cli-agent-id"]["source_kind"], "agent")
        self.assertEqual(idx["cli-agent-id"]["project"], "No project")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["client"], "claude_desktop")
        self.assertEqual(sources[0]["title"], "No-project task")

    def test_agent_uses_user_selected_folder_as_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "local-agent-mode-sessions" / "account" / "org"
            metadata = root / "local_agent-session.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({
                "sessionId": "local_agent-session",
                "cliSessionId": "cli-agent-id",
                "cwd": str(root / "local_agent-session" / "outputs"),
                "userSelectedFolders": ["/tmp/selected-project"],
                "lastActivityAt": 123456,
            }))
            idx = meter.claude_desktop_index(tmp)

        self.assertEqual(idx["cli-agent-id"]["cwd"], "/tmp/selected-project")
        self.assertEqual(idx["cli-agent-id"]["project"], "/tmp/selected-project")


class ToolEvidenceTests(unittest.TestCase):
    def test_tool_summary_reconciles_types_calls_and_per_execution_peak(self):
        executions = [
            {"idx": 1, "tools": [{"name": "exec", "namespace": "exec"}]},
            {"idx": 2, "tools": [
                {"name": "exec", "namespace": "exec"},
                {"name": "wait", "namespace": "wait", "skills": ["browser"]},
            ]},
        ]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["activity"]["observed_unique"], 2)
        self.assertEqual(summary["activity"]["total_calls"], 3)
        self.assertEqual(summary["activity"]["peak_calls_per_execution"], 2)
        self.assertEqual(summary["skills"], [{"name": "browser", "activations": 1}])
        self.assertFalse(summary["execution_rows_truncated"])

    def test_tool_summary_discloses_latest_execution_window(self):
        executions = [{"idx": idx, "tools": [{"name": "exec", "namespace": "exec"}]}
                      for idx in range(1, 82)]
        summary = meter.tool_summary(executions)
        self.assertEqual(summary["total_calls"], 81)
        self.assertEqual(summary["execution_rows_total"], 81)
        self.assertEqual(summary["execution_rows_shown"], 80)
        self.assertEqual(summary["execution_calls_shown"], 80)
        self.assertTrue(summary["execution_rows_truncated"])

    def test_flags_tokens_once_across_oversize_repeat_and_error_rules(self):
        calls = [
            {**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("exec_command"), "output_tokens": 100, "ts": 110,
             "args_fingerprint": "same", "error": False},
            {**meter.tool_identity("mcp__jira__search"), "output_tokens": 50, "ts": 120,
             "args_fingerprint": "jira", "error": True},
        ]
        evidence = meter.summarize_tool_evidence(calls)
        self.assertEqual(evidence["total_output_tokens"], 9150)
        self.assertEqual(evidence["flagged_tokens"], 9150)
        self.assertEqual(evidence["oversized_calls"], 1)
        self.assertEqual(evidence["repeat_calls"], 1)
        self.assertEqual(evidence["errors"], 1)

    def test_global_aggregation_finds_reported_unused_mcp(self):
        catalog = [{
            "name": "mcp__salesforce__query", "namespace": "salesforce", "kind": "mcp",
            "defer_loading": False, "definition_tokens": 100,
        }]
        rows = []
        for idx in range(5):
            calls = []
            if idx == 0:
                calls = [{**meter.tool_identity("exec_command"), "output_tokens": 9000, "ts": 100,
                          "args_fingerprint": "one", "error": False}]
            rows.append({
                "id": f"s{idx}", "path": f"/tmp/s{idx}", "provider": "codex",
                "project": "/repo", "_tool_evidence": meter.summarize_tool_evidence(calls, catalog),
            })
        waste = meter.global_tool_waste(rows)
        self.assertEqual(waste["total_calls"], 1)
        self.assertEqual(waste["oversized_calls"], 1)
        candidate = next(row for row in waste["by_name"] if row["name"] == "mcp__salesforce__query")
        self.assertEqual(candidate["recommendation"], "disable")
        self.assertEqual(candidate["advertised_sessions"], 5)
        self.assertEqual(candidate["sessions_used"], 0)

    def test_tracks_eager_definition_tax_and_skill_trace_references(self):
        calls = [{**meter.tool_identity("exec_command"), "output_tokens": 10, "ts": 100,
                  "args_fingerprint": "x", "skills": ["execution-plan"]}]
        catalog = [
            {"name": "unused_eager", "namespace": "app", "kind": "tool",
             "defer_loading": False, "definition_tokens": 120},
            {"name": "later", "namespace": "app", "kind": "tool",
             "defer_loading": True, "definition_tokens": 80},
        ]
        evidence = meter.summarize_tool_evidence(calls, catalog)
        self.assertEqual(evidence["definition_tokens"], 200)
        self.assertEqual(evidence["unused_eager_definition_tokens"], 120)
        self.assertEqual(evidence["skills"][0]["name"], "execution-plan")

    def test_plain_tool_names_stay_separate_by_runtime(self):
        call = {**meter.tool_identity("read_file"), "output_tokens": 10, "ts": 100,
                "args_fingerprint": "x", "error": False}
        rows = [
            {"id": "codex", "provider": "codex", "runtime": "Codex", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
            {"id": "cursor", "provider": "cursor", "runtime": "Cursor", "project": "/repo",
             "_tool_evidence": meter.summarize_tool_evidence([call])},
        ]
        waste = meter.global_tool_waste(rows)
        tools = [row for row in waste["inventory_tools"] if row["name"] == "read_file"]
        self.assertEqual({row["id"] for row in tools}, {"codex::read_file", "cursor::read_file"})
        self.assertEqual({row["runtime"] for row in tools}, {"Codex", "Cursor"})
        self.assertEqual(waste["provider_sessions"], {"codex": 1, "cursor": 1})
        self.assertEqual(waste["runtime_sessions"], {"Codex": 1, "Cursor": 1})

    def test_skill_name_is_inferred_from_skill_descriptor_path(self):
        value = {"cmd": "sed -n '1,80p' /tmp/skills/execution-plan/SKILL.md"}
        self.assertEqual(meter.skill_names_from_value(value), ["execution-plan"])

    def test_native_skill_tool_input_records_the_invoked_skill(self):
        value = {"skill": "documentation-skills:sharepoint-docs", "args": "find the policy"}
        self.assertEqual(
            meter.skill_names_from_value(value, "Skill"),
            ["sharepoint-docs"],
        )
        self.assertEqual(meter.skill_names_from_value(value, "OtherTool"), [])

    def test_native_claude_skill_call_excludes_its_pack_from_review_candidates(self):
        objs = [{
            "type": "assistant", "timestamp": "2026-08-05T00:00:00Z",
            "message": {
                "id": "message-1", "content": [{
                    "type": "tool_use", "id": "tool-1", "name": "Skill",
                    "input": {"skill": "sharepoint-docs"},
                }],
            },
        }]
        calls = meter.claude_tool_call_evidence(objs)
        evidence = meter.summarize_tool_evidence(calls)
        waste = meter.global_tool_waste([{
            "id": "claude-session", "provider": "claude", "runtime": "Claude",
            "project": "/repo", "_tool_evidence": evidence,
        }])
        usage = next(row for row in waste["skills"] if row["name"] == "sharepoint-docs")
        groups = meter.capability_control_groups([], [{
            "id": "sharepoint-docs", "name": "sharepoint-docs", "runtime": "Claude",
            "plugin_id": "documentation-skills@skills-marketplace", "mutable": True,
            "enabled": True, "reviewable": True, "used": bool(usage),
            "activations": usage["activations"], "last_used": usage["last_used"],
        }])
        summary = meter.optional_capability_summary(groups)

        self.assertEqual(calls[0]["skills"], ["sharepoint-docs"])
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["review_candidates"], [])

    def test_skill_frontmatter_parses_name_description_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\ndescription: Ultra-compressed communication mode\n---\n\nbody")
            path = f.name
        try:
            fm = meter._skill_frontmatter(path)
            self.assertEqual(fm, {"name": "caveman", "description": "Ultra-compressed communication mode"})
        finally:
            os.unlink(path)

    def test_skill_frontmatter_parses_allowed_tools(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall-manager\nallowed-tools: Bash, Read\n---\n\nbody")
            path = f.name
        try:
            fm = meter._skill_frontmatter(path)
            self.assertEqual(fm["allowed-tools"], "Bash, Read")
        finally:
            os.unlink(path)

    def test_skill_frontmatter_returns_empty_for_missing_file(self):
        fm = meter._skill_frontmatter("/nonexistent/path/SKILL.md")
        self.assertEqual(fm, {})

    def test_skill_has_measurable_capabilities_with_allowed_tools(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall\ndescription: Manage firewall\nallowed-tools: Bash\n---\n\nbody")
            path = f.name
        try:
            self.assertTrue(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_skill_without_capability_metadata_has_unknown_measurement(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\ndescription: Be terse\n---\n\nbody")
            path = f.name
        try:
            self.assertFalse(meter._skill_has_measurable_capabilities(path))
            self.assertEqual(meter._skill_measurability(path), "unknown")
        finally:
            os.unlink(path)

    def test_skill_requires_mcp_block_is_measurable(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: sourcegraph-search\nrequires-mcp:\n  - sourcegraph\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "measurable")
            self.assertTrue(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_skill_allowed_tools_block_is_measurable(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: firewall\nallowed-tools:\n  - Bash\n  - Read\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "measurable")
        finally:
            os.unlink(path)

    def test_skill_empty_allowed_tools_is_instruction_only(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nname: caveman\nallowed-tools: []\n---\n\nbody")
            path = f.name
        try:
            self.assertEqual(meter._skill_measurability(path), "instruction")
            self.assertFalse(meter._skill_has_measurable_capabilities(path))
        finally:
            os.unlink(path)

    def test_instruction_only_skill_pack_excluded_from_review_candidates(self):
        skill_items = [
            {"id": "skill:claude:caveman:caveman", "name": "caveman", "runtime": "Claude",
             "plugin_id": "caveman@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "instruction", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        self.assertEqual(groups[0]["unmeasurable"], True)
        self.assertEqual(groups[0]["measurement"], "instruction")
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unmeasurable_packs"], 1)
        self.assertEqual(summary["instruction_packs"], 1)
        self.assertEqual(summary["unknown_evidence_packs"], 0)
        self.assertEqual(summary["review_candidates"], [])

    def test_unknown_skill_pack_excluded_from_review_candidates(self):
        skill_items = [
            {"id": "skill:claude:unknown:unknown", "name": "unknown", "runtime": "Claude",
             "plugin_id": "unknown@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "unknown", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "unknown")
        self.assertTrue(groups[0]["unmeasurable"])
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unknown_evidence_packs"], 1)
        self.assertEqual(summary["review_candidates"], [])

    def test_mixed_measurement_pack_is_not_a_review_candidate(self):
        skill_items = [
            {"id": "instruction", "name": "caveman", "runtime": "Claude",
             "plugin_id": "mixed@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "instruction", "reviewable": True},
            {"id": "tool", "name": "sourcegraph", "runtime": "Claude",
             "plugin_id": "mixed@skills-marketplace", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "measurable", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "unknown")
        self.assertTrue(groups[0]["unmeasurable"])
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["review_candidates"], [])

    def test_measurable_skill_pack_remains_review_candidate(self):
        skill_items = [
            {"id": "skill:codex:browser:browser", "name": "browser", "runtime": "Codex",
             "plugin_id": "browser@personal", "mutable": True, "enabled": True,
             "used": False, "activations": 0, "measurement": "measurable", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        self.assertEqual(groups[0]["unmeasurable"], False)
        self.assertEqual(groups[0]["measurement"], "measurable")
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["unused"], 1)
        self.assertEqual(summary["unmeasurable_packs"], 0)
        self.assertEqual(len(summary["review_candidates"]), 1)

    def test_used_skill_pack_never_unmeasurable(self):
        skill_items = [
            {"id": "skill:claude:caveman:caveman", "name": "caveman", "runtime": "Claude",
             "plugin_id": "caveman@skills-marketplace", "mutable": True, "enabled": True,
             "used": True, "activations": 3, "measurement": "instruction", "reviewable": True},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(groups[0]["measurement"], "measurable")
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["unmeasurable_packs"], 0)
        self.assertEqual(len(summary["review_candidates"]), 0)

    def test_token_meter_diagnostics_are_accounted_but_never_recommended_for_cleanup(self):
        calls = [{**meter.tool_identity("mcp__tokenmeter__check"), "output_tokens": 50000,
                  "ts": 100, "args_fingerprint": "one", "error": False}]
        rows = [{
            "id": "s1", "path": "/tmp/s1", "provider": "codex", "project": "/repo",
            "_tool_evidence": meter.summarize_tool_evidence(calls),
        }]
        waste = meter.global_tool_waste(rows)
        diagnostic = next(row for row in waste["by_name"] if row["namespace"] == "tokenmeter")
        self.assertTrue(diagnostic["diagnostic"])
        self.assertEqual(diagnostic["recommendation"], "keep")
        self.assertFalse(any("tokenmeter" in row.get("key", "") for row in waste["insights"]))


class AgentDataContractTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "safe-session", "provider": "codex", "client": "codex", "label": "Codex",
            "path": "/private/logs/secret.jsonl", "session": "secret.jsonl",
            "project": "/Users/test/work/repository", "mtime": meter.time.time(),
        }
        self.state = {
            "provider": "codex", "source": self.source, "session": "secret.jsonl",
            "project": self.source["project"], "total_cost": 1.25, "cost_approx": True,
            "total_tokens": 120000, "turns": 4, "last_turn_cost": 0.21,
            "context": {"latest": 40000, "window": 200000, "latest_pct": 0.2},
            "tools": {"total_output_tokens": 9000, "flagged_tokens": 1000, "by_name": []},
            "executions": [{"idx": 4, "cost": 0.21, "tokens": {
                "input": 40000, "output": 900, "retrieval": 1000,
            }, "context_pct": 0.2}],
            "trace": [
                {"execution": 4, "kind": "user", "label": "User message", "detail": "private prompt"},
                {"execution": 4, "kind": "tool_call", "label": "search", "detail": "private args"},
                {"execution": 4, "kind": "tool_result", "label": "search", "detail": "private result", "tokens": 1000},
            ],
            "insights": [], "ended": False,
        }

    def test_check_matches_runtime_and_project_and_omits_content(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(execution=4, caller={
                "runtime": "codex", "project": "/Users/test/work/repository/subdir",
            })
        encoded = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertNotIn("project", result["selected_session"])
        self.assertNotIn("repository", encoded)
        self.assertEqual(result["data_scope"], "matched_current_run")
        self.assertNotIn("private prompt", encoded)
        self.assertNotIn("private args", encoded)
        self.assertNotIn("private result", encoded)
        self.assertNotIn("/private/logs", encoded)
        self.assertLessEqual(len(result["evidence"]), 3)
        self.assertLessEqual(len(result["execution"]["activity"]), 5)
        selected_tools = next(row for row in result["evidence"] if row["label"] == "Selected execution tool results")
        self.assertEqual(selected_tools["value"], 1000)

    def test_default_check_does_not_present_run_wide_tool_tokens_as_latest(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })
        latest_tools = next(row for row in result["evidence"] if row["label"] == "Latest execution tool results")
        self.assertEqual(latest_tools["value"], 1000)
        self.assertNotEqual(latest_tools["value"], self.state["tools"]["total_output_tokens"])

    def test_cost_check_answers_the_cost_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="cost", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("$1.25", result["answer"])
        self.assertIn("cost", result["recommended_action"].lower())

    def test_context_check_answers_the_context_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="context", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("20%", result["answer"])
        self.assertIn("context", result["recommended_action"].lower())

    def test_check_dashboard_url_targets_the_single_run_surface(self):
        state = {**self.state, "context": {"latest": 180000, "window": 200000,
                                           "latest_pct": 0.9}}
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=state):
            result = meter.agent_check(caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertTrue(result["dashboard_url"].endswith("#summary"))

    def test_tools_check_answers_the_tool_volume_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="tools", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("9,000", result["answer"])
        self.assertIn("tool", result["recommended_action"].lower())

    def test_next_phase_check_summarizes_all_readiness_signals(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="next_phase", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("20%", result["answer"])
        self.assertIn("$1.25", result["answer"])
        self.assertIn("9,000", result["answer"])
        self.assertIn("next phase", result["recommended_action"].lower())

    def test_missing_context_percentage_is_not_described_as_zero(self):
        state = {"context": {"latest_pct": None}, "last_turn_cost": 0,
                 "insights": [], "executions": [], "ended": False}
        recommendation = meter.menubar_recommendation(state)
        verdict = meter.menubar_verdict(state, recommendation)
        self.assertIn("not reported", verdict["detail"])
        self.assertNotIn("Context is 0%", verdict["detail"])

    def test_dashboard_omits_removed_session_tab_payloads(self):
        state = {
            "source": {"id": "session", "provider": "codex"},
            "trace": [{"kind": "usage"}],
            "insights": [{"kind": "warn"}],
            "tools": {
                "total_calls": 7,
                "total_output_tokens": 1000,
                "unique_used": 1,
                "by_namespace": [{"namespace": "shell"}],
                "by_name": [{"name": "exec"}],
                "by_execution": [{"execution": 2}],
            },
        }
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            projected = meter.dashboard_state_payload(state)

        self.assertNotIn("trace", projected)
        self.assertNotIn("insights", projected)
        self.assertEqual(projected["tools"]["total_calls"], 7)
        self.assertEqual(projected["tools"]["total_output_tokens"], 1000)
        self.assertEqual(projected["tools"]["unique_used"], 1)
        for removed in ("by_namespace", "by_name", "by_execution"):
            self.assertNotIn(removed, projected["tools"])
        self.assertEqual(state["trace"], [{"kind": "usage"}])

    def test_check_refuses_to_fall_back_across_projects(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]):
            result = meter.agent_check(caller={"runtime": "codex", "project": "/another/repository"})
        self.assertFalse(result["ok"])
        self.assertIn("did not fall back", result["caveat"])

    def test_claude_trace_cwd_preserves_hyphenated_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps({
                "type": "user", "cwd": "/Users/test/Documents/github/token-meter",
            }) + "\n")
            cwd = meter.claude_trace_cwd(str(path))
        self.assertEqual(cwd, "/Users/test/Documents/github/token-meter")

    def test_current_run_resolution_prefers_exact_project_over_newer_parent(self):
        now = meter.time.time()
        exact = {**self.source, "id": "exact", "project": "/Users/test/work/repository", "mtime": now - 10}
        parent = {**self.source, "id": "parent", "project": "/Users/test/work", "mtime": now}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[parent, exact],
        )
        self.assertEqual(selected["id"], "exact")
        self.assertEqual(resolution, "matched")

    def test_current_run_resolution_rejects_stale_implicit_match(self):
        stale = {**self.source, "mtime": meter.time.time() - meter.AGENT_CURRENT_MAX_AGE_S - 1}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[stale],
        )
        self.assertIsNone(selected)
        self.assertIn("No recent Codex run", resolution)

    def test_usage_is_aggregate_only_and_ranked_categories_are_bounded(self):
        cross = {
            "daily": [{"day": time_day, "cost": 2.0, "sessions": 2,
                       "providers": [{"provider": "codex", "cost": 2.0}],
                       "tool_tokens": 1000, "flagged_tokens": 300}
                      for time_day in ("2026-07-01", "2026-06-30")],
            "sessions": [{"cost_approx": True, "title": "private title", "project": "/private/repo"}],
            "model_mix": [{"model": f"model-{idx}", "cost": idx, "tokens": idx * 100}
                          for idx in range(8)],
            "tool_waste": {"by_name": [
                {"name": f"tool-{idx}", "namespace": "safe", "output_tokens": idx * 100, "calls": idx}
                for idx in range(8)
            ]},
        }
        with mock.patch.object(meter, "cross_session", return_value=cross):
            result = meter.agent_usage(window="7d", focus="models")
        encoded = json.dumps(result)
        self.assertEqual(result["data_scope"], "anonymous_aggregate_history")
        self.assertLessEqual(len(result["categories"]), 5)
        self.assertTrue(result["dashboard_url"].endswith("/#spend"))
        self.assertNotIn("private title", encoded)
        self.assertNotIn("/private/repo", encoded)

    def test_usage_model_categories_are_scoped_to_calendar_window(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)
        outside_seven_days = today - datetime.timedelta(days=7)
        daily = [
            {"day": today.isoformat(), "cost": 2.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 2.0}]},
            {"day": recent.isoformat(), "cost": 3.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 3.0}]},
            {"day": outside_seven_days.isoformat(), "cost": 5.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 5.0}]},
        ]
        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_model_daily": [
                {"model": "model-current", "day": today.isoformat(), "cost": 2.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
                {"model": "model-week", "day": recent.isoformat(), "cost": 3.0,
                 "input_tokens": 200, "output_tokens": 30, "executions": 1},
                {"model": "model-fortnight", "day": outside_seven_days.isoformat(), "cost": 5.0,
                 "input_tokens": 300, "output_tokens": 50, "executions": 1},
            ],
        }
        cross = {
            "daily": daily, "sessions": [internal],
            "model_mix": [{"model": "all-time", "cost": 999.0, "tokens": 999999}],
            "tool_waste": {"by_name": []},
        }

        with mock.patch.object(meter, "cross_session", return_value=cross), \
                mock.patch.dict(meter._xsess, {"internal_rows": (internal,)}):
            today_result = meter.agent_usage(window="today", focus="models")
            week_result = meter.agent_usage(window="7d", focus="models")
            fortnight_result = meter.agent_usage(window="14d", focus="models")

        self.assertEqual([row["model"] for row in today_result["categories"]], ["model-current"])
        self.assertEqual(
            {row["model"] for row in week_result["categories"]},
            {"model-current", "model-week"},
        )
        self.assertEqual(
            {row["model"] for row in fortnight_result["categories"]},
            {"model-current", "model-week", "model-fortnight"},
        )
        self.assertEqual([today_result["days_observed"], week_result["days_observed"],
                          fortnight_result["days_observed"]], [1, 2, 3])

    def test_usage_tool_categories_and_evidence_share_the_selected_window(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)
        outside_seven_days = today - datetime.timedelta(days=7)

        def midday(day):
            return int(time.mktime((*day.timetuple()[:3], 12, 0, 0, 0, 0, -1)))

        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_tool_evidence": meter.summarize_tool_evidence([
                {**meter.tool_identity("search"), "output_tokens": 120,
                 "ts": midday(today), "args_fingerprint": "today", "error": False},
                {**meter.tool_identity("fetch"), "output_tokens": 80,
                 "ts": midday(recent), "args_fingerprint": "recent", "error": False},
                {**meter.tool_identity("search"), "output_tokens": 500,
                 "ts": midday(outside_seven_days), "args_fingerprint": "older", "error": False},
            ]),
        }
        cross = {
            "daily": [
                {"day": today.isoformat(), "cost": 2.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 2.0}]},
                {"day": recent.isoformat(), "cost": 3.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 3.0}]},
                {"day": outside_seven_days.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
            ],
            "sessions": [internal], "model_mix": [],
            "tool_waste": {"by_name": [
                {"name": "search", "namespace": "unknown", "output_tokens": 620, "calls": 2},
                {"name": "fetch", "namespace": "unknown", "output_tokens": 80, "calls": 1},
            ]},
        }

        with mock.patch.object(meter, "cross_session", return_value=cross), \
                mock.patch.dict(meter._xsess, {"internal_rows": (internal,)}):
            today_result = meter.agent_usage(window="today", focus="tools")
            week_result = meter.agent_usage(window="7d", focus="tools")
            fortnight_result = meter.agent_usage(window="14d", focus="tools")

        def tool_evidence(result):
            return next(row["value"] for row in result["evidence"]
                        if row["label"] == "Trace-observed tool results")

        self.assertEqual(today_result["categories"], [{
            "name": "search", "namespace": "search", "returned_tokens": 120, "calls": 1,
        }])
        self.assertEqual(tool_evidence(today_result), 120)
        self.assertEqual(tool_evidence(week_result), 200)
        self.assertEqual(tool_evidence(fortnight_result), 700)
        self.assertEqual(
            {row["name"]: row["returned_tokens"] for row in week_result["categories"]},
            {"search": 120, "fetch": 80},
        )

    def test_usage_headline_and_action_answer_the_requested_focus(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)

        def midday(day):
            return int(time.mktime((*day.timetuple()[:3], 12, 0, 0, 0, 0, -1)))

        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_model_daily": [
                {"model": "model-top", "day": today.isoformat(), "cost": 5.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
                {"model": "model-top", "day": recent.isoformat(), "cost": 5.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
            ],
            "_tool_evidence": meter.summarize_tool_evidence([
                {**meter.tool_identity("search"), "output_tokens": 9000,
                 "ts": midday(today), "args_fingerprint": "today", "error": False},
            ]),
        }
        cross = {
            "daily": [
                {"day": today.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
                {"day": recent.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
            ],
            "sessions": [internal], "model_mix": [], "tool_waste": {"by_name": []},
        }

        with mock.patch.object(meter, "cross_session", return_value=cross), \
                mock.patch.dict(meter._xsess, {"internal_rows": (internal,)}):
            spend = meter.agent_usage(window="7d", focus="spend")
            models = meter.agent_usage(window="7d", focus="models")
            tools = meter.agent_usage(window="7d", focus="tools")

        self.assertIn("$10.00", spend["answer"])
        self.assertIn("spend", spend["recommended_action"].lower())
        self.assertIn("model-top", models["answer"])
        self.assertIn("model", models["recommended_action"].lower())
        self.assertIn("9,000", tools["answer"])
        self.assertIn("tool", tools["recommended_action"].lower())
        self.assertEqual(len({spend["answer"], models["answer"], tools["answer"]}), 3)

    def test_capability_result_names_only_requested_evidence(self):
        cross = {"capabilities": {
            "summary": {"optional": {
                "enabled": 2, "unused": 1,
                "review_candidates": ["skill_pack:Codex:docs@personal"],
            }},
            "control_groups": [
                {"id": "skill_pack:Codex:docs@personal", "name": "docs@personal",
                 "control_type": "skill_pack", "runtime": "Codex", "mutable": True,
                 "reviewable": True, "used": False, "enabled": True, "activations": 0,
                 "environment": {"TOKEN": "secret"}},
                {"name": "tokenmeter", "control_type": "skill_pack", "runtime": "Codex",
                 "used": False, "enabled": True},
            ],
        }}
        with mock.patch.object(meter, "cross_session", return_value=cross):
            result = meter.agent_capabilities(scope="all", limit=5)
        encoded = json.dumps(result)
        self.assertEqual([row["name"] for row in result["candidates"]], ["docs@personal"])
        self.assertNotIn("TOKEN", encoded)
        self.assertNotIn("secret", encoded)

    def test_all_scope_capabilities_returns_only_counted_review_candidates(self):
        candidate_id = "skill_pack:Codex:z-review"
        cross = {"capabilities": {
            "summary": {"optional": {
                "enabled": 1, "unused": 1, "review_candidates": [candidate_id],
            }},
            "control_groups": [
                {"id": "skill_pack:Codex:a-disabled", "name": "a-disabled",
                 "control_type": "skill_pack", "runtime": "Codex", "mutable": True,
                 "reviewable": True, "used": False, "enabled": False},
                {"id": candidate_id, "name": "z-review", "control_type": "skill_pack",
                 "runtime": "Codex", "mutable": True, "reviewable": True,
                 "used": False, "enabled": True},
            ],
        }}

        with mock.patch.object(meter, "cross_session", return_value=cross):
            result = meter.agent_capabilities(scope="all", limit=5)

        self.assertEqual([row["name"] for row in result["candidates"]], ["z-review"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates_returned"], 1)

    def test_agent_result_has_a_hard_serialized_bound(self):
        result = meter.bounded_agent_result({
            "answer": "a" * 10000, "evidence": [{"label": "x", "value": "y" * 10000}] * 10,
            "recommended_action": "z" * 10000, "caveat": "c" * 10000,
        })
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["evidence"]), 2)


class SoftwareUpdateTests(unittest.TestCase):
    def enabled_settings(self, root):
        path = Path(root) / "settings.json"
        path.write_text(json.dumps({"updates": {"enabled": True}, "keep": "value"}))
        return path

    def runner(self, outputs, calls):
        def run(command, **kwargs):
            args = tuple(command[3:])
            calls.append(args)
            value = outputs.get(args)
            if isinstance(value, int):
                return meter.subprocess.CompletedProcess(command, value, "", "failed")
            if value is None:
                raise AssertionError(f"Unexpected git command: {args}")
            return meter.subprocess.CompletedProcess(command, 0, value, "")
        return run

    def test_windows_background_git_checks_do_not_create_a_console_window(self):
        observed = {}

        def runner(command, **kwargs):
            observed.update(kwargs)
            return meter.subprocess.CompletedProcess(command, 0, "main\n", "")

        windows = meter.platform_services(
            "windows", environment={"USERPROFILE": r"C:\Users\example"},
            home=r"C:\Users\example",
        )
        with mock.patch.object(meter, "_PLATFORM_SERVICES", windows):
            self.assertEqual(
                meter._run_update_git(r"C:\Token Meter", ["rev-parse", "HEAD"],
                                      runner=runner),
                "main",
            )

        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_update_setting_defaults_on_and_preserves_an_explicit_off_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"model_pricing": {"claude": {}}}))
            initial = meter.update_settings(str(path))
            invalid = meter.set_update_settings({"enabled": "yes"}, str(path))
            result = meter.set_update_settings(
                {"enabled": False, "auto_install": True}, str(path)
            )
            stored = json.loads(path.read_text())
            explicit = meter.update_settings(str(path))
        self.assertTrue(initial["enabled"])
        self.assertTrue(initial["auto_install"])
        self.assertEqual(initial["interval_seconds"], 600)
        self.assertFalse(invalid["ok"])
        self.assertTrue(result["ok"])
        self.assertFalse(explicit["enabled"])
        self.assertFalse(explicit["auto_install"])
        self.assertEqual(
            stored["updates"], {"enabled": False, "auto_install": False}
        )
        self.assertIn("model_pricing", stored)

    def test_auto_install_can_be_disabled_without_disabling_update_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"keep": "value"}))
            result = meter.set_update_settings(
                {"enabled": True, "auto_install": False}, str(path)
            )
            stored = json.loads(path.read_text())
            explicit = meter.update_settings(str(path))
        self.assertTrue(result["ok"])
        self.assertEqual(
            explicit,
            {"enabled": True, "auto_install": False, "interval_seconds": 600},
        )
        self.assertEqual(
            stored,
            {"keep": "value", "updates": {"enabled": True, "auto_install": False}},
        )

    def test_menubar_update_projection_is_bounded_and_actionable(self):
        status = {
            "enabled": True,
            "auto_install": False,
            "state": "available",
            "available": True,
            "can_update": True,
            "current_revision": "a" * 40,
            "latest_revision": "b" * 40,
            "message": "/private/source checkout has an update",
            "path": "/private/source",
            "actions": {
                "token": "local_action_token-123",
                "check": True,
                "install": True,
            },
        }
        projected = meter.menubar_software_update(status)
        self.assertEqual(projected, {
            "enabled": True,
            "auto_install": False,
            "state": "available",
            "available": True,
            "can_update": True,
            "current_revision": "a" * 40,
            "latest_revision": "b" * 40,
            "actions": {
                "token": "local_action_token-123",
                "install": True,
            },
        })
        self.assertNotIn("message", projected)
        self.assertNotIn("path", projected)

    def test_update_watcher_preserves_terminal_result_for_one_interval_then_checks(self):
        wake = mock.Mock()
        wake.wait.side_effect = [False, RuntimeError("stop watcher")]
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "complete"}),
              mock.patch.object(meter, "check_for_software_update") as check):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        self.assertEqual(
            wake.wait.call_args_list,
            [mock.call(meter.UPDATE_CHECK_INTERVAL_S), mock.call(meter.UPDATE_CHECK_INTERVAL_S)],
        )
        check.assert_called_once_with()

    def test_update_watcher_starts_a_safe_available_update_when_enabled(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": "b" * 40,
        }
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": True},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "current"}),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ) as check,
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        check.assert_called_once_with()
        start.assert_called_once_with()

    def test_update_watcher_keeps_checking_when_auto_install_is_disabled(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": "b" * 40,
        }
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": False},
              ),
              mock.patch.object(meter, "_update_status_record", return_value={"phase": "current"}),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ) as check,
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        check.assert_called_once_with()
        start.assert_not_called()

    def test_update_watcher_does_not_repeat_the_same_failed_revision(self):
        wake = mock.Mock()
        wake.wait.side_effect = [RuntimeError("stop watcher")]
        target = "b" * 40
        available = {
            "available": True,
            "can_update": True,
            "latest_revision": target,
        }
        record = {"phase": "available", "failed_revision": target}
        with (mock.patch.object(meter, "_update_wake", wake),
              mock.patch.object(
                  meter, "update_settings",
                  return_value={"enabled": True, "auto_install": True},
              ),
              mock.patch.object(meter, "_update_status_record", return_value=record),
              mock.patch.object(
                  meter, "check_for_software_update", return_value=available,
              ),
              mock.patch.object(meter, "start_software_update") as start):
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.software_update_watcher()
        start.assert_not_called()

    def test_automatic_check_fetches_and_reports_a_clean_fast_forward_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            calls = []
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/main\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
                ("fetch", "--quiet", "--prune", "--no-tags", "origin"): "",
                ("rev-parse", "HEAD"): "a" * 40,
                ("rev-parse", "@{upstream}"): "b" * 40,
                ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): "0\t2\n",
                ("status", "--porcelain"): "",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, calls),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "available")
        self.assertTrue(status["available"])
        self.assertTrue(status["can_update"])
        self.assertEqual(status["current_revision"], "a" * 40)
        self.assertEqual(status["latest_revision"], "b" * 40)
        self.assertEqual(status["behind"], 2)
        self.assertEqual(status["next_check_at"], 1834)
        self.assertIn(("fetch", "--quiet", "--prune", "--no-tags", "origin"), calls)
        self.assertNotIn(tmp, json.dumps(status))

    def test_available_update_fails_closed_when_checkout_is_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/main\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
                ("fetch", "--quiet", "--prune", "--no-tags", "origin"): "",
                ("rev-parse", "HEAD"): "a" * 40,
                ("rev-parse", "@{upstream}"): "b" * 40,
                ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): "0 1",
                ("status", "--porcelain"): " M page.html\n",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, []),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "attention")
        self.assertTrue(status["available"])
        self.assertFalse(status["can_update"])
        self.assertTrue(status["dirty"])
        self.assertIn("local changes", status["message"])

    def test_automatic_update_rejects_a_checkout_not_on_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            outputs = {
                ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                    "origin/feature\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "feature\n",
            }
            with mock.patch.object(meter.shutil, "which", return_value="/usr/bin/git"):
                status = meter.check_for_software_update(
                    checkout=tmp,
                    runner=self.runner(outputs, []),
                    now=1234,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
        self.assertEqual(status["state"], "attention")
        self.assertFalse(status["can_update"])
        self.assertIn("main", status["message"])

    def test_linux_helper_failure_has_an_actionable_bounded_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "failed",
                "error_code": "linux_helper_unavailable",
                "checked_at": 1234,
                "available": False,
                "can_update": False,
            }, str(status_path))
            status = meter.software_update_status(
                settings_path=str(settings_path), status_path=str(status_path),
            )

        self.assertEqual(status["state"], "attention")
        self.assertEqual(
            status["message"],
            "The installed Linux update helper is unavailable. Reinstall Token Meter to repair automatic updates.",
        )
        self.assertNotIn(tmp, json.dumps(status))

    def test_explicit_install_starts_only_the_bounded_detached_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "available",
                "current_revision": "a" * 40,
                "latest_revision": "b" * 40,
                "checked_at": 1234,
                "available": True,
                "can_update": True,
                "ahead": 0,
                "behind": 1,
                "dirty": False,
            }, str(status_path))
            popen = mock.Mock()
            with (mock.patch.object(meter, "source_checkout_path", return_value=tmp),
                  mock.patch.object(meter.os.path, "isfile", return_value=True),
                  mock.patch.object(meter.os, "access", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            command = popen.call_args.args[0]
            kwargs = popen.call_args.kwargs
        self.assertTrue(result["ok"])
        self.assertTrue(command[0].endswith("/scripts/update"))
        self.assertEqual(command[1:], [tmp, str(status_path)])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn(tmp, json.dumps(result))

    def test_explicit_install_retries_a_failed_current_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            target = "b" * 40
            meter._persist_update_status({
                "phase": "failed",
                "error_code": "install_failed",
                "current_revision": target,
                "latest_revision": target,
                "previous_revision": "a" * 40,
                "failed_revision": target,
                "checked_at": 1234,
                "available": False,
                "can_update": False,
            }, str(status_path))
            popen = mock.Mock()
            with (mock.patch.object(meter, "source_checkout_path", return_value=tmp),
                  mock.patch.object(meter.os.path, "isfile", return_value=True),
                  mock.patch.object(meter.os, "access", return_value=True)):
                result = meter.start_software_update(
                    popen=popen,
                    settings_path=str(settings_path),
                    status_path=str(status_path),
                )
            retry_environment = popen.call_args.kwargs["env"]
        self.assertTrue(result["ok"])
        self.assertEqual(
            retry_environment["TOKEN_METER_UPDATE_RETRY_REVISION"], target
        )

    def test_manual_check_cannot_overwrite_an_install_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self.enabled_settings(tmp)
            status_path = Path(tmp) / "update-status.json"
            meter._persist_update_status({
                "phase": "installing",
                "current_revision": "b" * 40,
                "latest_revision": "b" * 40,
                "available": True,
                "can_update": False,
            }, str(status_path))
            result = meter.trigger_software_update_check(
                settings_path=str(settings_path),
                status_path=str(status_path),
            )
            stored = json.loads(status_path.read_text())
        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["error"])
        self.assertEqual(stored["phase"], "installing")


class InstallationTests(unittest.TestCase):
    def test_menu_bar_launcher_builds_a_stable_background_app_bundle(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run-menubar").read_text()
        info = plistlib.loads((root / "menubar" / "Info.plist").read_bytes())
        self.assertIn('APP="$ROOT/.build/Token Meter Menu Bar.app"', script)
        self.assertIn('ditto "$ROOT/menubar/Info.plist" "$INFO"', script)
        self.assertIn('swiftc "$ROOT/menubar/TokenMeterMenuBar.swift" -o "$BIN"', script)
        self.assertIn('exec "$BIN"', script)
        self.assertEqual(info["CFBundleIdentifier"], "com.token-meter.menubar")
        self.assertEqual(info["CFBundleExecutable"], "token-meter-menubar")
        self.assertTrue(info["LSUIElement"])

    def test_user_installer_waits_for_both_supervised_runtime_jobs_and_returns_control(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install").read_text()
        manifest = (root / "runtime-manifest.txt").read_text()
        server_start = script.index(
            '"$INSTALL_ROOT/scripts/install-launch-agent" server-only'
        )
        readiness_check = script.index('"state_ready": true')
        menubar_start = script.index(
            '"$INSTALL_ROOT/scripts/install-launch-agent" menubar-only'
        )
        self.assertLess(server_start, readiness_check)
        self.assertLess(readiness_check, menubar_start)
        self.assertIn('$HOME/Library/Application Support/Token Meter/runtime', script)
        self.assertIn('TOKEN_METER_READINESS_TIMEOUT_SECONDS:-600', script)
        self.assertIn('curl -fsS --max-time 5 "$HEALTH_URL"', script)
        self.assertNotIn('curl -fsS --max-time 1 "$HEALTH_URL"', script)
        self.assertIn('launchctl print "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl print "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('"$INSTALL_ROOT/meter.py"', script)
        self.assertIn('"$INSTALL_ROOT/scripts/run-menubar"', script)
        self.assertIn('ditto "$source_path" "$install_path"', script)
        self.assertIn('python3 -m token_meter.packaging parity', script)
        self.assertIn('assets/brand/logo-splunk-acc-rgb-w.png', manifest)
        self.assertIn('menubar/Info.plist', manifest)
        self.assertIn(
            'printf \'%s\\n\' "$UPDATE_SOURCE_ROOT" > "$INSTALL_ROOT/SOURCE_CHECKOUT"',
            script,
        )
        self.assertIn('git clone --quiet --no-local "$SOURCE_ROOT"', script)
        self.assertIn(
            'git -C "$MANAGED_SOURCE_ROOT" fetch --quiet --no-tags',
            script,
        )
        self.assertIn(
            'git -C "$MANAGED_SOURCE_ROOT" merge --quiet --ff-only FETCH_HEAD',
            script,
        )
        self.assertIn('remote set-url origin "$source_remote_url"', script)
        self.assertIn('"branch.$managed_branch.merge" "refs/heads/$source_branch"', script)
        self.assertIn('source_slug=', script)
        self.assertIn('source_slug="${source_slug%.git}"', script)
        self.assertIn('MANAGED_SOURCE_ROOT="$INSTALL_PARENT/source-$source_slug"', script)
        self.assertIn('Preserving the existing managed checkout at:', script)
        self.assertIn("Token Meter installation complete.", script)
        self.assertNotIn('exec "$INSTALL_ROOT/scripts/install-launch-agent"', script)

    def test_update_helper_requires_clean_fast_forward_then_reuses_installer(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "update").read_text()
        self.assertIn("git -C \"$SOURCE_ROOT\" fetch --quiet --prune --no-tags", script)
        self.assertIn("git -C \"$SOURCE_ROOT\" status --porcelain", script)
        self.assertIn("git -C \"$SOURCE_ROOT\" merge --ff-only '@{upstream}'", script)
        self.assertIn('branch="$(git -C "$SOURCE_ROOT" rev-parse --abbrev-ref HEAD', script)
        self.assertIn('[[ "$branch" != "main" || "${upstream##*/}" != "main" ]]', script)
        self.assertIn('TOKEN_METER_UPDATE_RETRY_REVISION', script)
        self.assertIn('record["failed_revision"]', script)
        self.assertIn('INSTALL_SCRIPT="$SOURCE_ROOT/scripts/install"', script)
        self.assertIn('INSTALL_SCRIPT="$SOURCE_ROOT/scripts/install-linux"', script)
        self.assertIn('TOKEN_METER_INSTALL_ROOT="$RUNTIME_ROOT" "$INSTALL_SCRIPT"', script)
        self.assertNotIn("reset --hard", script)
        self.assertNotIn("sudo ", script)

    def test_update_entrypoint_dispatches_linux_to_linux_helper(self):
        root = Path(__file__).resolve().parents[1]
        entrypoint = (root / "scripts" / "update").read_text()
        linux_helper = (root / "scripts" / "update-linux").read_text()
        manifest = (root / "runtime-manifest.txt").read_text()

        self.assertIn('case "$(uname -s)" in', entrypoint)
        self.assertIn('LINUX_HELPER="$ENTRYPOINT_ROOT/scripts/update-linux"', entrypoint)
        self.assertIn('[[ ! -x "$LINUX_HELPER" ]]', entrypoint)
        self.assertIn('"error_code": "linux_helper_unavailable"', entrypoint)
        self.assertIn('exec env TOKEN_METER_UPDATE_LINUX_HELPER=1 "$LINUX_HELPER" "$@"', entrypoint)
        self.assertIn('Darwin)', entrypoint)
        self.assertIn('supported platforms are macOS and Linux.', entrypoint)
        self.assertIn('[[ "$(uname -s)" == "Linux" ]]', linux_helper)
        self.assertIn(
            'exec env TOKEN_METER_UPDATE_LINUX_HELPER=1 "$ENTRYPOINT_ROOT/scripts/update" "$@"',
            linux_helper,
        )
        self.assertNotIn('git -C "$SOURCE_ROOT"', linux_helper)
        self.assertIn('required scripts/update-linux', manifest)

    def test_windows_update_helper_requires_main_and_supports_failed_retry(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "update-windows.ps1").read_text()
        self.assertIn('$Branch = (& $Git.Source -C $SourceRoot rev-parse --abbrev-ref HEAD', script)
        self.assertIn('$Branch -ne "main"', script)
        self.assertIn('$env:TOKEN_METER_UPDATE_RETRY_REVISION', script)
        self.assertIn('failed_revision =', script)

    def test_launch_agents_supervise_server_and_menu_bar_independently(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "install-launch-agent").read_text()
        self.assertIn('SERVER_LABEL="com.token-meter.server"', script)
        self.assertIn('MENUBAR_LABEL="com.token-meter.menubar"', script)
        self.assertIn("<string>$SERVER_PROGRAM</string>", script)
        self.assertIn("<string>$MENUBAR_PROGRAM</string>", script)
        self.assertIn("Verify the replacement survives that", script)
        self.assertGreaterEqual(
            script.count('launchctl print "gui/$UID/$label"'), 3,
        )
        self.assertEqual(script.count("<key>KeepAlive</key>"), 2)
        self.assertEqual(script.count("<key>SuccessfulExit</key>"), 1)
        self.assertEqual(script.count("<key>SuccessfulExit</key>\n    <false/>"), 1)
        self.assertNotIn("start-token-meter", script)
        self.assertIn("all|server-only|menubar-only", script)
        self.assertIn("server-only)", script)
        self.assertIn("menubar-only)", script)
        self.assertNotIn("kickstart -k", script)
        self.assertIn("attempt <= 10", script)
        self.assertIn('launchctl print "gui/$UID/$label"', script)
        self.assertIn("sleep 0.2", script)
        for label in ("SERVER_LABEL", "MENUBAR_LABEL"):
            self.assertIn(f'launchctl bootout "gui/$UID/${label}"', script)

    def test_foreground_launcher_waits_for_indexing_before_starting_menubar(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "start-token-meter").read_text()
        readiness_check = script.index('"state_ready": true')
        menubar_start = script.index('exec "$ROOT/scripts/run-menubar"')
        self.assertLess(readiness_check, menubar_start)
        self.assertIn('TOKEN_METER_READINESS_TIMEOUT_SECONDS:-600', script)
        self.assertIn('curl -fsS --max-time 5 "$HEALTH_URL"', script)
        self.assertNotIn('curl -fsS --max-time 1 "$HEALTH_URL"', script)

    def test_uninstaller_removes_both_supervised_jobs(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "uninstall-launch-agent").read_text()
        self.assertIn('launchctl bootout "gui/$UID/$SERVER_LABEL"', script)
        self.assertIn('launchctl bootout "gui/$UID/$MENUBAR_LABEL"', script)
        self.assertIn('rm -f "$MENUBAR_PLIST" "$SERVER_PLIST"', script)

    def test_linux_installer_uses_xdg_runtime_systemd_and_appindicator_tray(self):
        root = Path(__file__).resolve().parents[1]
        entrypoint = (root / "scripts" / "install").read_text()
        installer = (root / "scripts" / "install-linux").read_text()
        systemd = (root / "scripts" / "install-systemd-user").read_text()
        runner = (root / "scripts" / "run-menubar").read_text()
        tray = (root / "menubar" / "token_meter_tray.py").read_text()
        self.assertIn('exec "$SOURCE_ROOT/scripts/install-linux" "$@"', entrypoint)
        self.assertNotIn("install-systemd-user", entrypoint)
        self.assertNotIn("launchctl", installer)
        self.assertNotIn("install-launch-agent", installer)
        self.assertIn("XDG_DATA_HOME", installer)
        self.assertIn("install-systemd-user", installer)
        self.assertIn('"$INSTALL_ROOT/scripts/install-systemd-user" server-only', installer)
        self.assertIn('"$INSTALL_ROOT/scripts/install-systemd-user" menubar-only', installer)
        self.assertIn("systemctl --user is-active", installer)
        self.assertIn("token-meter-server.service", systemd)
        self.assertIn("token-meter-tray.service", systemd)
        self.assertIn("all|server-only|menubar-only", systemd)
        self.assertEqual(systemd.count("Restart=on-failure"), 2)
        self.assertIn("Linux)", runner)
        self.assertIn("AyatanaAppIndicator3", tray)
        self.assertIn("AppIndicator3", tray)
        self.assertIn("XDG_CONFIG_HOME", tray)
        self.assertIn("refresh_menu_content", tray)
        self.assertIn("self.indicator.set_menu(self.menu)", tray)
        self.assertNotIn("self.indicator.set_menu(menu)", tray)
        self.assertIn("Recent sessions", tray)
        self.assertIn("Provider limits", tray)
        self.assertIn("Open Budget Settings", tray)
        self.assertIn("Menu bar title", tray)
        self.assertIn("def open_dashboard(self):", tray)

    def test_linux_runtime_uses_xdg_desktop_and_trash_paths(self):
        if not meter.IS_LINUX:
            self.skipTest("Linux-specific default paths")
        self.assertTrue(meter.CURSOR_STATE_DB.startswith(meter.XDG_CONFIG_HOME))
        self.assertTrue(all(path.startswith(meter.XDG_CONFIG_HOME)
                            for path in meter.CLAUDE_DESKTOP_DATA_ROOTS))
        self.assertEqual(meter.session_action_capability()["destination"], "Trash")


class RemovedAgentDefaultsEndpointTests(unittest.TestCase):
    def test_removed_agent_defaults_endpoint_returns_not_found(self):
        for method in ("do_GET", "do_POST"):
            with self.subTest(method=method):
                handler = object.__new__(meter.H)
                handler.path = "/settings/agent-defaults"
                errors, responses = [], []
                handler.send_error = lambda status: errors.append(status)
                handler._send = lambda *args, **kwargs: responses.append((args, kwargs))
                if method == "do_POST":
                    handler.headers = {}
                getattr(handler, method)()
                self.assertEqual(errors, [404])
                self.assertEqual(responses, [])


@unittest.skip("Agent defaults feature removed")
class AgentDefaultSettingsTests(unittest.TestCase):
    def test_agent_default_status_does_not_project_model_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "existing-codex-model"\nmodel_reasoning_effort = "high"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "existing-claude-model",
                "modelOverrides": {"alias": "existing-provider-model"},
                "effortLevel": "high",
            }))

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        serialized = json.dumps(status)
        for client in status["clients"]:
            self.assertNotIn("model", client["defaults"])
            self.assertNotIn("hidden_model", client)
        for configured_model in (
            "existing-codex-model", "existing-claude-model", "existing-provider-model",
        ):
            self.assertNotIn(configured_model, serialized)

    def test_saving_agent_behavior_defaults_preserves_existing_model_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "existing-codex-model"\nmodel_reasoning_effort = "low"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "existing-claude-model",
                "modelOverrides": {"alias": "existing-provider-model"},
                "effortLevel": "low",
            }))

            codex = meter.set_agent_default_settings(
                "codex", {
                    "model": "legacy-request-model", "preserve_model": False,
                    "reasoning_effort": "high", "context_limit": 128000,
                },
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {
                    "model": "legacy-request-model", "preserve_model": False,
                    "reasoning_effort": "xhigh", "context_limit": 500000,
                },
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertIn('model = "existing-codex-model"', codex_text)
        self.assertEqual(claude_json["model"], "existing-claude-model")
        self.assertEqual(
            claude_json["modelOverrides"], {"alias": "existing-provider-model"},
        )
        self.assertNotIn("model", codex["defaults"])
        self.assertNotIn("model", claude["defaults"])

    def test_agent_default_save_rejects_concurrent_model_changes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "initial-codex-model"\nmodel_reasoning_effort = "low"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "initial-claude-model",
                "modelOverrides": {"alias": "initial-provider-model"},
                "effortLevel": "low",
            }))
            original_write = meter.atomic_write_text

            def concurrent_codex_write(path, text, *args, **kwargs):
                codex_path.write_text(
                    codex_path.read_text().replace(
                        "initial-codex-model", "concurrent-codex-model",
                    )
                )
                return original_write(path, text, *args, **kwargs)

            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=concurrent_codex_write):
                codex = meter.set_agent_default_settings(
                    "codex", {"reasoning_effort": "high", "context_limit": 128000},
                    codex_config=str(codex_path), claude_settings=str(claude_path),
                )

            def concurrent_claude_write(path, text, *args, **kwargs):
                concurrent = json.loads(claude_path.read_text())
                concurrent["model"] = "concurrent-claude-model"
                concurrent["modelOverrides"] = {"alias": "concurrent-provider-model"}
                claude_path.write_text(json.dumps(concurrent))
                return original_write(path, text, *args, **kwargs)

            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=concurrent_claude_write):
                claude = meter.set_agent_default_settings(
                    "claude", {"reasoning_effort": "xhigh", "context_limit": 500000},
                    codex_config=str(codex_path), claude_settings=str(claude_path),
                )

            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(codex["ok"])
        self.assertFalse(claude["ok"])
        self.assertIn('model = "concurrent-codex-model"', codex_text)
        self.assertEqual(claude_json["model"], "concurrent-claude-model")
        self.assertEqual(
            claude_json["modelOverrides"], {"alias": "concurrent-provider-model"},
        )
        for secret in (
            "concurrent-codex-model", "concurrent-claude-model",
            "concurrent-provider-model",
        ):
            self.assertNotIn(secret, json.dumps({"codex": codex, "claude": claude}))

    def test_codex_defaults_report_cli_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "gpt-5.6-terra"\n')
            claude_path.write_text("{}\n")

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            saved = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "high", "context_limit": 128000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        codex = status["clients"][0]
        self.assertEqual(codex["scope"], "cli")
        self.assertEqual(codex["label"], "Codex CLI")
        self.assertEqual(saved["scope"], "cli")
        self.assertIn("Codex CLI defaults saved", saved["message"])
        self.assertNotIn("Desktop", saved["message"])

    def test_agent_default_settings_update_only_the_supported_client_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                '# Keep this comment.\nmodel = "old-model"\n'
                'model_reasoning_effort = "low"\n\n'
                '[mcp_servers.local]\ncommand = "keep-me"\n'
            )
            claude_path.write_text(json.dumps({
                "model": "old-model",
                "effortLevel": "low",
                "env": {"KEEP": "value"},
                "enabledPlugins": {"keep@personal": True},
            }))

            codex = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "high", "context_limit": 128000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "xhigh", "context_limit": 500000},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertTrue(codex["ok"])
        self.assertTrue(codex["verified"])
        self.assertTrue(claude["ok"])
        self.assertTrue(claude["verified"])
        self.assertIn('model = "old-model"', codex_text)
        self.assertIn('model_reasoning_effort = "high"', codex_text)
        self.assertIn("model_context_window = 128000", codex_text)
        self.assertIn('[mcp_servers.local]\ncommand = "keep-me"', codex_text)
        self.assertEqual(claude_json["model"], "old-model")
        self.assertEqual(claude_json["effortLevel"], "xhigh")
        self.assertEqual(claude_json["env"], {
            "KEEP": "value", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "500000",
        })
        self.assertEqual(claude_json["enabledPlugins"], {"keep@personal": True})

    def test_clearing_agent_default_settings_removes_only_the_saved_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text(
                'model = "gpt-5.6-terra"\nmodel_reasoning_effort = "high"\n'
                'model_context_window = 256000\n[features]\nskills = true\n'
            )
            claude_path.write_text(json.dumps({
                "model": "claude-sonnet-4-6", "effortLevel": "high",
                "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "256000", "KEEP": "value"},
            }))

            codex = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "", "context_limit": None},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            claude = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "", "context_limit": None},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )
            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertTrue(codex["ok"])
        self.assertTrue(claude["ok"])
        self.assertIn('model = "gpt-5.6-terra"', codex_text)
        self.assertNotIn("model_reasoning_effort", codex_text)
        self.assertNotIn("model_context_window", codex_text)
        self.assertIn("[features]\nskills = true", codex_text)
        self.assertEqual(claude_json["model"], "claude-sonnet-4-6")
        self.assertNotIn("effortLevel", claude_json)
        self.assertEqual(claude_json["env"], {"KEEP": "value"})

    def test_agent_default_settings_reject_an_invalid_context_limit_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "keep"\n')
            claude_path.write_text(json.dumps({"model": "keep"}))

            result = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "high", "context_limit": 99999},
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

            codex_text = codex_path.read_text()
            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(result["ok"])
        self.assertIn("Context limit", result["error"])
        self.assertEqual(codex_text, 'model = "keep"\n')
        self.assertEqual(claude_json, {"model": "keep"})

    def test_agent_default_settings_reject_a_fractional_context_limit_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = Path(tmp) / "settings.json"
            claude_path.write_text(json.dumps({"model": "keep"}))

            result = meter.set_agent_default_settings(
                "claude", {"reasoning_effort": "high", "context_limit": 128000.5},
                claude_settings=str(claude_path),
            )

            claude_json = json.loads(claude_path.read_text())

        self.assertFalse(result["ok"])
        self.assertIn("whole number", result["error"])
        self.assertEqual(claude_json, {"model": "keep"})

    def test_agent_defaults_accept_only_documented_persistent_reasoning_efforts(self):
        for effort in ("minimal", "low", "medium", "high", "xhigh"):
            with self.subTest(effort=effort):
                defaults = meter.normalize_agent_default_settings(
                    "codex", {"reasoning_effort": effort, "context_limit": 128000},
                )
                self.assertEqual(defaults["reasoning_effort"], effort)
        for client, efforts in (("codex", ("none", "max", "ultra")),
                                ("claude", ("minimal", "none", "max", "ultra"))):
            for effort in efforts:
                with self.subTest(client=client, effort=effort):
                    with self.assertRaisesRegex(ValueError, "not supported"):
                        meter.normalize_agent_default_settings(
                            client, {"reasoning_effort": effort, "context_limit": 128000},
                        )

    def test_codex_agent_defaults_preserve_triple_quoted_root_content_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            codex_path.write_text(
                'developer_instructions = """\n[keep this literal instruction]\n"""\n'
                'model = "gpt#old"\n[features]\nskills = true\n'
            )

            result = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "minimal", "context_limit": 128000},
                codex_config=str(codex_path),
            )
            text = codex_path.read_text()

        self.assertTrue(result["ok"])
        self.assertIn('developer_instructions = """\n[keep this literal instruction]\n"""', text)
        self.assertIn('model = "gpt#old"', text)
        self.assertLess(text.index("model_context_window = 128000"), text.index("[features]"))
        self.assertIn("[features]\nskills = true", text)

    def test_codex_agent_defaults_preserve_multiline_arrays_before_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            codex_path.write_text('matrix = [\n  [1, 2]\n]\n[features]\nskills = true\n')

            result = meter.set_agent_default_settings(
                "codex", {"reasoning_effort": "minimal", "context_limit": 128000},
                codex_config=str(codex_path),
            )
            text = codex_path.read_text()

        self.assertTrue(result["ok"])
        self.assertIn("matrix = [\n  [1, 2]\n]\n", text)
        self.assertLess(text.index("model_context_window = 128000"), text.index("[features]"))
        self.assertIn("[features]\nskills = true", text)

    def test_agent_default_status_redacts_unvalidated_or_sensitive_config_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / "config.toml"
            claude_path = Path(tmp) / "settings.json"
            codex_path.write_text('model = "/very/secret/path"\nmodel_reasoning_effort = "secret"\n')
            claude_path.write_text(json.dumps({
                "model": "arn:aws:bedrock:private-account:application/profile",
                "effortLevel": "super-secret-token",
                "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "999999999999999999999"},
            }))

            status = meter.agent_default_settings(
                codex_config=str(codex_path), claude_settings=str(claude_path),
            )

        serialized = json.dumps(status)
        self.assertEqual(status["clients"][0]["defaults"], {
            "reasoning_effort": None, "context_limit": None,
        })
        self.assertEqual(status["clients"][1]["defaults"], {
            "reasoning_effort": None, "context_limit": None,
        })
        for secret in ("/very/secret/path", "private-account", "super-secret-token", "999999999999999999999"):
            self.assertNotIn(secret, serialized)

    def test_legacy_model_fields_are_ignored_when_saving_other_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_path = Path(tmp) / "settings.json"
            hidden_model = "arn:aws:bedrock:private-account:application/profile"
            claude_path.write_text(json.dumps({"model": hidden_model, "effortLevel": "high"}))

            result = meter.set_agent_default_settings(
                "claude", {"model": "", "preserve_model": True, "reasoning_effort": "xhigh",
                           "context_limit": 500000},
                claude_settings=str(claude_path),
            )
            saved = json.loads(claude_path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(saved["model"], hidden_model)
        self.assertEqual(saved["effortLevel"], "xhigh")
        self.assertNotIn("private-account", json.dumps(result))


class AgentAccessTests(unittest.TestCase):
    def test_client_environment_prepends_wrapper_runtime_directory(self):
        with mock.patch.dict(meter.os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            env = meter.agent_client_environment("/Users/test/.nvm/versions/node/v24/bin/codex")
        self.assertEqual(env["PATH"].split(":" )[0], "/Users/test/.nvm/versions/node/v24/bin")
        self.assertIn("/usr/bin", env["PATH"].split(":"))

    def test_client_environment_keeps_symlink_directory_for_sibling_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper_dir = Path(tmp) / "node" / "bin"
            target_dir = Path(tmp) / "package" / "bin"
            wrapper_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            target = target_dir / "codex.js"
            target.write_text("#!/usr/bin/env node\n")
            wrapper = wrapper_dir / "codex"
            wrapper.symlink_to(target)
            env = meter.agent_client_environment(str(wrapper))
        self.assertEqual(env["PATH"].split(":")[0], str(wrapper_dir))

    def test_client_discovery_checks_user_bin_when_service_path_is_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / ".local" / "bin" / "claude"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            with mock.patch.object(meter.os.path, "expanduser",
                                   side_effect=lambda value: tmp if value == "~" else value):
                found = meter.agent_client_executable("claude", which=lambda name: None)
        self.assertEqual(found, str(binary))

    def test_connection_commands_use_fixed_vectors_and_user_scope(self):
        launcher = "/Applications/Token Meter/bin/token-meter-mcp"
        codex = meter.agent_access_command("codex", True, launcher=launcher, cli_path="/usr/bin/codex")
        claude = meter.agent_access_command("claude", True, launcher=launcher, cli_path="/usr/bin/claude")
        self.assertEqual(codex, ["/usr/bin/codex", "mcp", "add", "--env",
                                "TOKEN_METER_CALLER=codex", "tokenmeter", "--", launcher])
        self.assertEqual(claude, ["/usr/bin/claude", "mcp", "add", "--transport", "stdio",
                                 "--scope", "user", "tokenmeter", "--env",
                                 "TOKEN_METER_CALLER=claude", "--", launcher])

    def test_codex_status_requires_exact_launcher_and_caller_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)

            class Completed:
                returncode = 0
                stdout = json.dumps({"enabled": True, "transport": {
                    "type": "stdio", "command": str(launcher), "args": [],
                    "env": {"TOKEN_METER_CALLER": "codex"},
                }})
                stderr = ""

            status = meter.agent_access_client_status(
                "codex", launcher=str(launcher), runner=lambda *a, **k: Completed(),
                which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(status["connected"])
        self.assertFalse(status["conflict"])
        self.assertNotIn("/usr/bin/codex", status["connect_command"])

    def test_windows_codex_status_check_does_not_create_a_console_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            observed = {}

            def runner(command, **kwargs):
                observed.update(kwargs)
                return meter.subprocess.CompletedProcess(command, 1, "", "missing")

            windows = meter.platform_services(
                "windows", environment={"USERPROFILE": r"C:\Users\example"},
                home=r"C:\Users\example",
            )
            with mock.patch.object(meter, "_PLATFORM_SERVICES", windows):
                meter.agent_access_client_status(
                    "codex", launcher=str(launcher), runner=runner,
                    which=lambda name: fr"C:\Tools\{name}.exe",
                )

        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_windows_connection_change_does_not_create_a_console_window(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}
        after = {**before, "configured": True, "connected": True}
        states = iter((before, after))
        observed = {}

        def runner(command, **kwargs):
            observed.update(kwargs)
            return meter.subprocess.CompletedProcess(command, 0, "", "")

        windows = meter.platform_services(
            "windows", environment={"USERPROFILE": r"C:\Users\example"},
            home=r"C:\Users\example",
        )
        with mock.patch.object(meter, "_PLATFORM_SERVICES", windows), \
                mock.patch.object(meter, "agent_access_launcher",
                                  return_value=r"C:\Token Meter\token-meter-mcp.cmd"), \
                mock.patch.object(meter, "agent_client_executable",
                                  return_value=r"C:\Tools\codex.exe"):
            result = meter.set_agent_access(
                "codex", True, runner=runner,
                status_getter=lambda client: next(states),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(observed["creationflags"], 0x08000000)
        self.assertTrue(observed["close_fds"])

    def test_claude_status_reads_only_the_user_scope_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": str(launcher), "args": [],
                "env": {"TOKEN_METER_CALLER": "claude"},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(status["connected"])

    def test_different_existing_entry_is_reported_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "token-meter-mcp"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            config = Path(tmp) / ".claude.json"
            config.write_text(json.dumps({"mcpServers": {"tokenmeter": {
                "type": "stdio", "command": "/tmp/different", "args": [], "env": {},
            }}}))
            status = meter.agent_access_client_status(
                "claude", launcher=str(launcher), claude_config_path=str(config),
                which=lambda name: f"/usr/bin/{name}")
        self.assertFalse(status["connected"])
        self.assertTrue(status["conflict"])
        self.assertEqual(status["status"], "Existing entry differs from this install")

    def test_conflicting_entry_requires_explicit_repair_confirmation(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}
        runner = mock.Mock()
        result = meter.set_agent_access("codex", True, runner=runner,
                                        status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflict"])
        self.assertIn("Confirm repair", result["error"])
        runner.assert_not_called()

    def test_repair_replaces_only_named_entry_and_verifies_connection(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}
        after = {**before, "configured": True, "connected": True, "conflict": False}
        states = iter((before, after))
        commands = []

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv, **kwargs):
            commands.append(argv)
            self.assertNotIn("shell", kwargs)
            return Completed()

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"), \
                mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/codex"):
            result = meter.set_agent_access("codex", True, repair=True, runner=runner,
                                            status_getter=lambda client: next(states))
        self.assertTrue(result["ok"])
        self.assertTrue(result["repaired"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(commands, [
            ["/usr/bin/codex", "mcp", "remove", "tokenmeter"],
            ["/usr/bin/codex", "mcp", "add", "--env", "TOKEN_METER_CALLER=codex",
             "tokenmeter", "--", "/tmp/token-meter-mcp"],
        ])

    def test_repair_stops_when_existing_entry_cannot_be_removed(self):
        before = {"label": "Claude Code", "detected": True, "available": True,
                  "configured": True, "connected": False, "conflict": True}

        class Completed:
            returncode = 1
            stdout = ""
            stderr = "entry is locked\n"

        runner = mock.Mock(return_value=Completed())
        with mock.patch.object(meter, "agent_client_executable", return_value="/usr/bin/claude"):
            result = meter.set_agent_access("claude", True, repair=True, runner=runner,
                                            status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertTrue(result["conflict"])
        self.assertIn("remove the existing tokenmeter entry", result["error"])
        self.assertIn("entry is locked", result["error"])
        runner.assert_called_once()

    def test_connection_change_is_verified_after_cli_success(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}
        after = {**before, "configured": True, "connected": True}
        states = iter((before, after))
        observed = {}

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        def runner(argv, **kwargs):
            observed["argv"] = argv
            observed["kwargs"] = kwargs
            return Completed()

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"):
            result = meter.set_agent_access("codex", True, runner=runner,
                                            status_getter=lambda client: next(states))
        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(observed["argv"][-3:], ["tokenmeter", "--", "/tmp/token-meter-mcp"])
        self.assertNotIn("shell", observed["kwargs"])

    def test_connection_failure_includes_bounded_cli_reason(self):
        before = {"label": "Codex", "detected": True, "available": True,
                  "configured": False, "connected": False, "conflict": False}

        class Completed:
            returncode = 127
            stdout = ""
            stderr = "env: node: No such file or directory\n"

        with mock.patch.object(meter, "agent_access_launcher", return_value="/tmp/token-meter-mcp"), \
                mock.patch.object(meter, "agent_client_executable", return_value="/tmp/codex"):
            result = meter.set_agent_access("codex", True, runner=lambda *a, **k: Completed(),
                                            status_getter=lambda client: before)
        self.assertFalse(result["ok"])
        self.assertIn("env: node", result["error"])


class CapabilityConfigTests(unittest.TestCase):
    def test_skill_catalog_cache_avoids_rescanning_but_refreshes_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "custom" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text("---\nname: custom\n---\n")
            codex_pattern = os.path.join(
                os.path.expanduser("~/.codex/skills"), "**", "SKILL.md",
            )

            def matching_glob(pattern, recursive=False):
                del recursive
                return [str(skill)] if pattern == codex_pattern else []

            meter.invalidate_discovered_skill_cache()
            try:
                with mock.patch.object(meter.glob, "glob", side_effect=matching_glob), \
                        mock.patch.object(
                            meter, "_skill_measurability", return_value="unknown",
                        ) as measurability:
                    first = meter.discovered_skills([{
                        "name": "custom", "providers": ["codex"],
                        "activations": 1, "sessions_used": 1,
                        "last_used": "2026-08-18",
                    }])
                    second = meter.discovered_skills([{
                        "name": "custom", "providers": ["codex"],
                        "activations": 2, "sessions_used": 2,
                        "last_used": "2026-08-19",
                    }])
            finally:
                meter.invalidate_discovered_skill_cache()

        self.assertEqual(measurability.call_count, 1)
        self.assertEqual(first[0]["activations"], 1)
        self.assertEqual(second[0]["activations"], 2)
        self.assertEqual(second[0]["measurement"], "measurable")

    def test_skill_identity_separates_runtime_origin_and_plugin(self):
        identities = {
            meter.skill_identity("Codex", "browser", "codex:built-in"),
            meter.skill_identity("Codex", "browser", "codex:user"),
            meter.skill_identity("Codex", "browser", "codex:plugin:openai-bundled", "browser@openai-bundled"),
            meter.skill_identity("Claude", "browser", "claude:plugin:skills-marketplace", "browser@skills-marketplace"),
        }
        self.assertEqual(len(identities), 4)

    def test_optional_summary_counts_only_mutable_skill_packs(self):
        mcp_items = [
            {"name": "context7", "mutable": True, "enabled": True, "used": False,
             "codex_enabled": True, "claude_enabled": False},
            {"name": "core", "mutable": False, "enabled": True, "used": False},
        ]
        skill_items = [
            {"name": "browser", "runtime": "Codex", "plugin_id": "browser@bundled",
             "mutable": True, "enabled": True, "used": True, "activations": 2},
            {"name": "local", "runtime": "Codex", "plugin_id": "",
             "mutable": False, "enabled": True, "used": False},
        ]
        groups = meter.capability_control_groups(mcp_items, skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["unused"], 0)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["skill_packs_enabled"], 1)

    def test_runtime_skill_packs_are_not_review_candidates(self):
        skill_items = [
            {"id": "runtime-skill", "name": "browser", "runtime": "Codex",
             "plugin_id": "browser@openai-bundled", "mutable": True, "enabled": True,
             "used": False, "reviewable": False, "origin": "runtime_pack"},
            {"id": "user-skill", "name": "custom", "runtime": "Codex",
             "plugin_id": "custom@personal", "mutable": True, "enabled": True,
             "used": False, "reviewable": True, "origin": "user_plugin"},
        ]
        groups = meter.capability_control_groups([], skill_items)
        summary = meter.optional_capability_summary(groups)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["review_candidates"], ["skill_pack:Codex:custom@personal"])

    def test_observed_tools_do_not_claim_configuration_enabled(self):
        waste = {"inventory_tools": [{
            "id": "codex::exec", "name": "exec", "display": "exec",
            "kind": "tool", "runtime": "Codex", "namespace": "shell",
            "calls": 2, "output_tokens": 120, "last_used": "2026-08-10",
            "advertised_sessions": 0, "eager_sessions": 0, "deferred_sessions": 0,
        }]}
        with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                mock.patch.object(meter, "discovered_skills", return_value=[]), \
                mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                mock.patch.object(meter, "claude_local_agent_sources", return_value=[]):
            capabilities = meter.capability_inventory(waste)
        row = capabilities["items"][0]
        self.assertIsNone(row["enabled"])
        self.assertEqual(row["configuration"], "Unknown")
        self.assertEqual(row["state"], "Observed only")
        self.assertIsNone(capabilities["summary"]["tools"]["enabled"])
        self.assertEqual(capabilities["summary"]["tools"]["observed"], 1)

    def test_capability_revisions_are_stable_across_generation_time(self):
        waste = {"inventory_tools": [{
            "id": "codex::exec", "name": "exec", "display": "exec",
            "kind": "tool", "runtime": "Codex", "namespace": "shell",
            "calls": 2, "output_tokens": 120, "last_used": "2026-08-10",
            "advertised_sessions": 1, "eager_sessions": 1, "deferred_sessions": 0,
        }]}
        with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                mock.patch.object(meter, "discovered_skills", return_value=[]), \
                mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                mock.patch.object(meter, "claude_local_agent_sources", return_value=[]), \
                mock.patch.object(meter.time, "time", side_effect=[100, 200, 300]):
            first = meter.capability_inventory(waste)
            second = meter.capability_inventory(waste)
            changed = meter.capability_inventory({
                **waste,
                "inventory_tools": [{**waste["inventory_tools"][0], "calls": 3}],
            })
        self.assertNotEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(first["review_revision"], second["review_revision"])
        self.assertEqual(first["inventory_revision"], second["inventory_revision"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertNotEqual(second["inventory_revision"], changed["inventory_revision"])
        self.assertNotEqual(second["revision"], changed["revision"])

    def test_dashboard_state_omits_heavy_capability_rows_and_tool_waste(self):
        capabilities = {
            "items": [{"id": "tool:exec"}],
            "summary": {"tools": {"available": 1}},
            "control_groups": [], "inventory_revision": "inventory-a",
        }
        state = {"xsession": {
            "capabilities": capabilities,
            "tool_waste": {"inventory_tools": [{"private": "trace detail"}]},
            "total_sessions": 2,
        }}
        payload = meter.dashboard_state_payload(state)
        public_capabilities = payload["xsession"]["capabilities"]
        self.assertNotIn("items", public_capabilities)
        self.assertEqual(public_capabilities["inventory_count"], 1)
        self.assertNotIn("tool_waste", payload["xsession"])
        self.assertIn("items", state["xsession"]["capabilities"])
        self.assertIn("tool_waste", state["xsession"])
        self.assertIn(
            'elif req_path == "/capabilities/inventory":',
            Path(meter.IMPLEMENTATION_FILE).read_text(),
        )

    def test_pack_without_runtime_logs_is_not_a_review_candidate(self):
        base = {
            "id": "skill_pack:Claude:custom@personal", "name": "custom@personal",
            "control_type": "skill_pack", "enabled": True, "used": False,
            "mutable": True, "reviewable": True, "unmeasurable": False,
        }
        unscanned = meter.optional_capability_summary([{**base, "scanned_sessions": 0}])
        scanned = meter.optional_capability_summary([{**base, "scanned_sessions": 3}])
        self.assertEqual(unscanned["review_candidates"], [])
        self.assertEqual(unscanned["unscanned_packs"], 1)
        self.assertEqual(scanned["review_candidates"], [base["id"]])
        self.assertEqual(scanned["unscanned_packs"], 0)

    def test_claude_desktop_and_3p_logs_do_not_cover_claude_code_packs(self):
        skill = {
            "id": "skill:claude:custom@personal:custom", "name": "custom",
            "runtime": "Claude", "source": "User-installed plugin",
            "plugin_id": "custom@personal", "mutable": True, "enabled": True,
            "used": False, "reviewable": True, "measurement": "measurable",
            "unmeasurable": False, "activations": 0, "last_used": "Never",
            "setting_path": "~/.claude/settings.json",
        }
        for runtime in ("Claude Desktop", "Claude-3P"):
            with self.subTest(runtime=runtime):
                waste = meter.global_tool_waste([{
                    "id": runtime, "provider": "claude", "runtime": runtime,
                    "project": "/repo", "_tool_evidence": {},
                }])
                with mock.patch.object(meter, "codex_mcp_states", return_value={}), \
                        mock.patch.object(meter, "claude_mcp_states", return_value={}), \
                        mock.patch.object(meter, "discovered_skills", return_value=[skill]), \
                        mock.patch.object(meter, "claude_desktop_index", return_value={}), \
                        mock.patch.object(meter, "claude_local_agent_sources", return_value=[]):
                    capabilities = meter.capability_inventory(waste)
                self.assertEqual(capabilities["control_groups"][0]["scanned_sessions"], 0)
                self.assertEqual(
                    capabilities["summary"]["optional"]["review_candidates"], []
                )

    def test_bulk_disable_accepts_only_exact_review_candidates(self):
        capabilities = {
            "summary": {"optional": {"review_candidates": ["skill_pack:Claude:custom@personal"]}},
            "control_groups": [
                {"id": "skill_pack:Claude:custom@personal", "name": "custom@personal",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": True},
                {"id": "skill_pack:Codex:browser@openai-bundled", "name": "browser@openai-bundled",
                 "control_type": "skill_pack", "enabled": True, "used": False,
                 "mutable": True, "reviewable": False},
            ],
        }
        changed = []

        def fake_setter(control, enabled):
            changed.append((control["id"], enabled))
            return {"ok": True, "verified": True}

        result = meter.disable_capability_controls(
            ["skill_pack:Claude:custom@personal"], capabilities, fake_setter)
        rejected = meter.disable_capability_controls(
            ["skill_pack:Codex:browser@openai-bundled"], capabilities, fake_setter)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(changed, [("skill_pack:Claude:custom@personal", False)])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["invalid_control_ids"], ["skill_pack:Codex:browser@openai-bundled"])

    def test_session_optional_summary_excludes_mcp_and_default_activity(self):
        capabilities = {"control_groups": [
            {"id": "skill_pack:Codex:browser@bundled", "control_type": "skill_pack",
             "name": "browser@bundled", "runtime": "Codex", "enabled": True,
             "mutable": True, "members": ["browser"]},
        ], "summary": {"optional": {"review_candidate_names": []}}}
        state = {"provider": "codex", "tools": {
            "by_name": [
                {"name": "exec", "namespace": "exec", "kind": "tool", "calls": 3},
                {"name": "mcp__context7__query", "namespace": "context7", "kind": "mcp", "calls": 1},
            ],
            "skills": [{"name": "browser", "activations": 1}],
        }}
        summary = meter.session_optional_capabilities(state, capabilities)
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["used"], 1)
        self.assertEqual(summary["mcp_enabled"], 0)
        self.assertEqual(summary["avoidable_eager_definition_tokens"], 0)
        self.assertNotIn("context7", [row["name"] for row in summary["groups"]])

    def test_mcp_parser_ignores_nested_env_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[mcp_servers.node_repl]\nenabled = false\n[mcp_servers.node_repl.env]\nTOKEN = 'x'\n")
            rows = meter.toml_named_sections(str(path), "mcp_servers")
        self.assertEqual(list(rows), ["node_repl"])

    def test_codex_plugin_toggle_changes_only_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[plugins."docs@runtime"]\nenabled = true\nsource = "keep"\n[features]\nskills = true\n')
            with mock.patch.object(meter, "CODEX_CONFIG", str(path)):
                result = meter.set_codex_plugin_enabled("docs@runtime", False)
            text = path.read_text()
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertIn("enabled = false", text)
        self.assertIn('source = "keep"', text)

    def test_claude_plugin_toggle_verifies_written_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"enabledPlugins": {"docs@personal": True}}))
            with (mock.patch.object(meter, "CLAUDE_SETTINGS", str(path)),
                  mock.patch.object(meter, "claude_plugin_installations",
                                    return_value={"docs@personal": {"installPath": tmp}})):
                result = meter.set_claude_plugin_enabled("docs@personal", False)
            written = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertFalse(written["enabledPlugins"]["docs@personal"])


class DailySummaryTests(unittest.TestCase):
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
            {"claude": 50, "codex": 30, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0},
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
            {"claude": 0, "codex": 0, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0},
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["budgets"]["monthly_total"], 1490)
        self.assertEqual(
            saved["budgets"]["allocations"],
            {"claude": 0, "codex": 1490, "cursor": 0, "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0},
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


class PiDocumentationTests(unittest.TestCase):
    def test_docs_explain_pi_evidence_and_privacy_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text()
        guide = (root / "specs/USER_GUIDE.md").read_text()
        architecture = (root / "specs/ARCHITECTURE.md").read_text()
        security = (root / "specs/SECURITY.md").read_text()

        self.assertIn("Pi coding-agent sessions", readme)
        self.assertIn("Wait time\nis inferred", readme)
        self.assertIn("semantic token classification", readme)
        self.assertIn("PI_CODING_AGENT_DIR", guide)
        self.assertIn("Pi coding-agent sessions", guide)
        self.assertIn("does not import cloud transcripts", guide)
        self.assertIn("Pi adapter", architecture)
        self.assertIn("application-profile", architecture)
        self.assertIn("application-profile", security)


class PiRuntimeTests(unittest.TestCase):
    """Pi sessions are local JSONL records with no content projection."""

    def _write_session(self, agent_root, *, legacy=False, provider="anthropic",
                       model="claude-test", result_error=False, reasoning_tokens=None):
        directory = Path(agent_root) if legacy else (
            Path(agent_root) / "sessions" / "--repo--"
        )
        directory.mkdir(parents=True)
        path = directory / "pi-session.jsonl"
        rows = [
            {"type": "session", "version": 1, "id": "pi-session",
             "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"},
            {"type": "model_change", "id": "model-change", "parentId": None,
             "timestamp": "2026-09-04T10:00:01Z", "provider": provider,
             "modelId": model},
            {"type": "message", "id": "user", "parentId": "model-change",
             "timestamp": "2026-09-04T10:00:02Z",
             "message": {"role": "user", "content": []}},
            {"type": "message", "id": "assistant", "parentId": "user",
             "timestamp": "2026-09-04T10:00:05Z", "message": {
                "role": "assistant", "model": model, "provider": provider,
                 "content": [{"type": "toolCall", "id": "call", "name": "read",
                              "arguments": {}}],
                 "usage": {"input": 100, "output": 20, "cacheRead": 10,
                           "cacheWrite": 5, "totalTokens": 135,
                           "cost": {"input": 0.001, "output": 0.002,
                                    "cacheRead": 0.0001, "cacheWrite": 0.0002}},
             }},
            {"type": "message", "id": "result", "parentId": "assistant",
             "timestamp": "2026-09-04T10:00:06Z",
             "message": {"role": "toolResult", "toolCallId": "call", "toolName": "read",
                         **({"isError": True} if result_error else {})},
             },
        ]
        if reasoning_tokens is not None:
            rows[3]["message"]["usage"]["reasoning"] = reasoning_tokens
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def test_discovers_and_projects_pi_usage_as_a_local_cost_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                sources = meter.pi_session_sources()
                state = meter.recompute(sources[0])
                summary = meter.pi_summary(sources[0])

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["provider"], "pi")
        self.assertEqual(sources[0]["model"], "claude-test")
        self.assertEqual(sources[0]["model_provider"], "anthropic")
        self.assertEqual(sources[0]["project"], "/repo")
        self.assertEqual(sources[0]["title"], "Pi session")
        self.assertEqual(state["tokens"], {
            "input": 100, "cache_write": 5, "cache_read": 10, "output": 20,
        })
        self.assertEqual(state["total_tokens"], 135)
        self.assertAlmostEqual(state["total_cost"], 0.0033)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(state["availability"]["cost"])
        self.assertFalse(state["semantic_available"])
        self.assertEqual(state["semantic"], {
            "reasoning": 0, "output": 0, "retrieval": 0, "coordination": 0,
        })
        self.assertFalse(state["cache"]["savings_available"])
        self.assertIsNone(state["cache"]["saved"])
        self.assertIsNone(state["cache_saved"])
        self.assertTrue(state["wait_time"]["available"])
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read")
        self.assertEqual(state["executions"][0]["context_tokens"], 115)
        self.assertEqual(state["context"]["latest"], 115)
        self.assertTrue(state["availability"]["context"])
        self.assertTrue(state["availability"]["throughput"])
        self.assertTrue(state["throughput"]["available"])
        self.assertEqual(state["throughput"]["basis"], "end_to_end")
        self.assertAlmostEqual(state["throughput"]["output_tps"], 20 / 3)
        self.assertEqual(summary["context"]["latest"], 115)
        self.assertEqual(summary["_context_samples"], [115])
        self.assertTrue(summary["throughput"]["available"])
        self.assertNotIn("arguments", json.dumps(state))

    def test_redacts_pi_bedrock_application_profile_without_inferring_a_model(self):
        raw_profile = (
            "arn:aws:bedrock:us-west-2:123456789012:"
            "application-inference-profile/private-profile"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, provider="amazon-bedrock", model=raw_profile)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.pi_summary(source)

        self.assertEqual(source["model"], "aws-bedrock-profile")
        self.assertEqual(source["model_provider"], "amazon")
        public = json.dumps({"source": source, "state": state, "summary": summary})
        self.assertNotIn(raw_profile, public)
        self.assertNotIn("123456789012", public)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(summary["wait_time"]["available"])

    def test_keeps_pi_cache_savings_unavailable_without_a_price_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                with mock.patch.object(
                    meter, "price_for",
                    side_effect=AssertionError("Pi must not infer cache savings from pricing"),
                ):
                    state = meter.recompute(source)

        self.assertFalse(state["cache"]["savings_available"])
        self.assertIsNone(state["cache"]["saved"])

    def test_discovers_legacy_pi_sessions_at_the_agent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, legacy=True)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}):
                sources = meter.pi_session_sources()

        self.assertEqual([source["id"] for source in sources], ["pi-session"])

    def test_ignores_non_pi_jsonl_without_poisoning_discovery_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            root.mkdir()
            (root / "not-a-pi-session.jsonl").write_text("{}\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}):
                self.assertEqual(meter.pi_session_sources(), [])
                self.assertEqual(meter.pi_session_sources(), [])

    def test_records_pi_tool_result_errors_instead_of_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, result_error=True)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                native = meter._pi_native_adapter().discover(None)[0]
                session = meter._pi_native_adapter().load(native, meter.DetailLevel.FULL)

        self.assertTrue(state["executions"][0]["tools"][0]["error"])
        self.assertTrue(state["tools"]["total_errors"])
        self.assertEqual(state["executions"][0]["tools"][0]["result_available"], True)
        self.assertEqual(session.tools[0].status, "error")
        self.assertEqual(
            [event["severity"] for event in state["trace"] if event["kind"] == "tool_call"],
            ["warn"],
        )

    def test_keeps_successful_pi_tool_results_as_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                native = meter._pi_native_adapter().discover(None)[0]
                session = meter._pi_native_adapter().load(native, meter.DetailLevel.FULL)

        self.assertFalse(state["executions"][0]["tools"][0]["error"])
        self.assertFalse(state["tools"]["total_errors"])
        self.assertEqual(session.tools[0].status, "success")

    def test_projects_pi_reasoning_tokens_as_an_output_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, reasoning_tokens=12)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)

        execution = state["executions"][0]
        self.assertEqual(execution["reasoning_tokens"], 12)
        self.assertEqual(state["series"][0]["reasoning"], 12)
        self.assertTrue(state["series"][0]["think"])
        # Reasoning is a reported subset of output, never an extra bucket.
        self.assertEqual(state["total_tokens"], 135)
        self.assertEqual(state["tokens"], {
            "input": 100, "cache_write": 5, "cache_read": 10, "output": 20,
        })

    def test_clamps_pi_reasoning_tokens_to_reported_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, reasoning_tokens=999)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                state = meter.recompute(meter.pi_session_sources()[0])

        self.assertEqual(state["executions"][0]["reasoning_tokens"], 20)
        self.assertTrue(state["series"][0]["think"])

    def test_keeps_pi_context_and_pace_unavailable_without_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            path = self._write_session(root)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            del rows[3]["message"]["usage"]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.pi_summary(source)

        self.assertEqual(state["context"]["latest"], 0)
        self.assertFalse(state["availability"]["context"])
        self.assertFalse(state["throughput"]["available"])
        self.assertFalse(state["availability"]["throughput"])
        self.assertEqual(summary["context"]["latest"], 0)
        self.assertFalse(summary["throughput"]["available"])

    def test_keeps_cost_unavailable_when_a_pi_turn_lacks_its_local_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            path = self._write_session(root)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            del rows[3]["message"]["usage"]["cost"]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                state = meter.recompute(meter.pi_session_sources()[0])

        self.assertFalse(state["availability"]["cost"])
        self.assertEqual(state["total_cost"], 0)


class OpenCodeTests(unittest.TestCase):
    """OpenCode is discovered from its read-only SQLite database."""

    def test_reported_cost_rejects_unrepresentable_numbers(self):
        for value in (10 ** 400, float("inf"), float("nan"), -1, True, "1"):
            self.assertEqual(meter._opencode_reported_cost({"cost": value}), (0.0, False))
        self.assertEqual(meter._opencode_reported_cost({"cost": 0}), (0.0, True))
        self.assertEqual(meter._opencode_reported_cost({"cost": 1.25}), (1.25, True))

    def _build_db(self, root):
        db_path = Path(root) / "opencode.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, "
                     "slug TEXT, directory TEXT, title TEXT, version TEXT, time_created INTEGER, "
                     "time_updated INTEGER, time_archived INTEGER, agent TEXT, model TEXT, cost REAL, "
                     "tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, "
                     "tokens_cache_read INTEGER, tokens_cache_write INTEGER)")
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
                     "time_updated INTEGER, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                     "time_created INTEGER, time_updated INTEGER, data TEXT)")
        conn.execute("CREATE TABLE todo (id TEXT PRIMARY KEY, session_id TEXT)")
        return conn

    def _session_row(self, sid, directory, title, model, cost, inp, out, rea, cr, cw,
                     updated, parent_id=None, archived=None, agent="build"):
        return {
            "id": sid, "project_id": "proj", "parent_id": parent_id, "slug": sid,
            "directory": directory, "title": title, "version": "1.18.14",
            "time_created": updated - 1000, "time_updated": updated,
            "time_archived": archived, "agent": agent,
            "model": json.dumps({"id": model, "providerID": "opencode-go", "variant": "high"}),
            "cost": cost, "tokens_input": inp, "tokens_output": out,
            "tokens_reasoning": rea, "tokens_cache_read": cr, "tokens_cache_write": cw,
        }

    def _message(self, mid, sid, data, created):
        return (mid, sid, created, created, json.dumps(data))

    def test_discovery_labels_and_filters_children_and_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.5, 1000, 200, 50, 300, 0, 1_784_548_900_000)
            child = self._session_row("ses_child", "/repo", "subagent work", "deepseek-v4-flash",
                                      0.1, 100, 50, 0, 0, 0, 1_784_548_950_000, parent_id="ses_top")
            archived = self._session_row("ses_arch", "/repo", "old", "deepseek-v4-pro",
                                         0.2, 100, 50, 0, 0, 0, 1_784_548_980_000, archived=1_784_549_000_000)
            for row in (top, child, archived):
                conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (row["id"], row["project_id"], row["parent_id"], row["slug"],
                              row["directory"], row["title"], row["version"], row["time_created"],
                              row["time_updated"], row["time_archived"], row["agent"], row["model"],
                              row["cost"], row["tokens_input"], row["tokens_output"],
                              row["tokens_reasoning"], row["tokens_cache_read"], row["tokens_cache_write"]))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(db_path := root / "opencode.db")):
                sources = meter.opencode_session_sources()
        self.assertEqual([row["id"] for row in sources], ["ses_top"])
        source = sources[0]
        self.assertEqual(source["provider"], "opencode")
        self.assertEqual(source["label"], "OpenCode")
        self.assertEqual(source["runtime"], "OpenCode")
        self.assertEqual(source["model"], "deepseek-v4-pro")
        self.assertEqual(source["project"], "/repo")
        self.assertEqual(source["path"], "opencode:ses_top")
        self.assertAlmostEqual(source["mtime"], 1_784_548_900.0)
        self.assertEqual(source["signature_mtime"], source["mtime"])
        self.assertEqual(meter.source_runtime_label(source), "OpenCode")

    def test_discovery_joins_all_session_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.5, 1000, 200, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (top["id"], top["project_id"], top["parent_id"], top["slug"],
                          top["directory"], top["title"], top["version"], top["time_created"],
                          top["time_updated"], top["time_archived"], top["agent"], top["model"],
                          top["cost"], top["tokens_input"], top["tokens_output"],
                          top["tokens_reasoning"], top["tokens_cache_read"], top["tokens_cache_write"]))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")), \
                    mock.patch.object(meter, "CODEX_SESSIONS", str(root / "no-codex")), \
                    mock.patch.object(meter, "CODEX_INDEX", str(root / "no-index")), \
                    mock.patch.object(meter, "CURSOR_PROJECTS", str(root / "no-cursor")), \
                    mock.patch.object(meter, "KIRO_SESSIONS", str(root / "no-kiro")), \
                    mock.patch.object(meter, "KIRO_AGENT_STORAGE", str(root / "no-kiro-agent")), \
                    mock.patch.object(meter, "PI_AGENT_DIR", str(root / "no-pi-agent")), \
                    mock.patch.object(meter, "HERMES_STATE_DB", str(root / "no-hermes.db")), \
                    mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}):
                sources = meter.all_session_sources()
        self.assertEqual([row["id"] for row in sources], ["ses_top"])
        found = meter.find_session("ses_top", sources)
        self.assertEqual(found["path"], "opencode:ses_top")

    def test_discovery_signature_follows_message_and_part_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            top = self._session_row("ses_top", "/repo", "Live", "model-a",
                                    0, 0, 0, 0, 0, 0, base)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                         ("m1", "ses_top", base, base + 10_000,
                          json.dumps({"role": "user"})))
            conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                         ("p1", "m1", "ses_top", base, base + 20_000,
                          json.dumps({"type": "text"})))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                source = meter.opencode_session_sources()[0]
        self.assertEqual(source["mtime"], base / 1000)
        self.assertEqual(source["signature_mtime"], (base + 20_000) / 1000)

    def test_discovery_reuses_idle_database_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Idle", "model-a",
                                    0, 0, 0, 0, 0, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_native_adapters", {}):
                first = meter.opencode_session_sources()
                with mock.patch.object(
                    meter.OpenCodeRuntimeAdapter, "connection",
                    side_effect=AssertionError("unchanged database should use the inventory cache"),
                ):
                    second = meter.opencode_session_sources()
        self.assertEqual(first, second)

    def test_opencode_paths_and_model_catalog_are_xdg_aware_and_refreshable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models_path = root / "models.json"
            with mock.patch.object(meter, "OPENCODE_DATA_ROOT", str(root / "data" / "opencode")), \
                    mock.patch.object(meter, "OPENCODE_DB", "relative.db"):
                self.assertEqual(
                    meter.opencode_db_path(), str(root / "data" / "opencode" / "relative.db")
                )
            with mock.patch.object(meter, "OPENCODE_MODELS_PATH", str(models_path)), \
                    mock.patch.object(meter, "_opencode_native_adapters", {}):
                self.assertEqual(meter._load_opencode_models(), {})
                models_path.write_text(json.dumps({
                    "provider-a": {"models": {"shared": {"limit": {"context": 100}}}},
                    "provider-b": {"models": {"shared": {"limit": {"context": 200}}}},
                }))
                self.assertEqual(meter._opencode_model_window("shared", "provider-a"), 100)
                self.assertEqual(meter._opencode_model_window("shared", "provider-b"), 200)
                self.assertIsNone(meter._opencode_model_window("shared"))
                models_path.write_text(json.dumps({
                    "provider-a": {"models": {"shared": {"limit": {"context": 300}}}},
                }))
                self.assertEqual(meter._opencode_model_window("shared", "provider-a"), 300)
                self.assertEqual(meter._opencode_model_window("shared"), 300)

    def _sample_messages(self):
        base = 1_784_548_800_000
        return [
            self._message("msg1", "ses_top", {
                "role": "assistant", "agent": "build", "modelID": "deepseek-v4-pro",
                "providerID": "opencode-go",
                "tokens": {"input": 1000, "output": 200, "reasoning": 50,
                           "cache": {"write": 0, "read": 300}},
                "cost": 0.01, "finish": "tool-calls",
                "time": {"created": base, "completed": base + 5000},
            }, base),
            self._message("msg2", "ses_top", {
                "role": "assistant", "agent": "build", "modelID": "deepseek-v4-pro",
                "providerID": "opencode-go",
                "tokens": {"input": 500, "output": 100, "reasoning": 0,
                           "cache": {"write": 0, "read": 0}},
                "cost": 0.005, "finish": "turn_complete",
                "time": {"created": base + 60_000, "completed": base + 70_000},
            }, base + 60_000),
            self._message("msg3", "ses_top", {"role": "user", "text": "hi"}, base + 10_000),
        ]

    def test_recompute_opencode_reads_authoritative_tokens_and_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.015, 1500, 300, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (top["id"], top["project_id"], top["parent_id"], top["slug"],
                          top["directory"], top["title"], top["version"], top["time_created"],
                          top["time_updated"], top["time_archived"], top["agent"], top["model"],
                          top["cost"], top["tokens_input"], top["tokens_output"],
                          top["tokens_reasoning"], top["tokens_cache_read"], top["tokens_cache_write"]))
            for mid, sid, created, updated, data in self._sample_messages():
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, sid, created, updated, data))
            conn.execute("INSERT INTO part VALUES ('p0','msg3','ses_top',1,1,?)", (json.dumps({
                "type": "text", "text": "  Follow   up  ",
            }),))
            conn.execute("INSERT INTO part VALUES ('p1','msg1','ses_top',1,1,?)", (json.dumps({
                "type": "tool", "tool": "read", "callID": "call-1",
                "state": {"status": "completed", "input": {"filePath": "/repo/a.py"},
                          "output": "line one\nline two"},
            }),))
            conn.execute("INSERT INTO part VALUES ('p2','msg1','ses_top',1,1,?)", (json.dumps({
                "type": "reasoning", "text": "think", "time": {"start": 1_784_548_800_100, "end": 1_784_548_800_900},
            }),))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=200000):
                state = meter.recompute_opencode({
                    "provider": "opencode", "id": "ses_top", "model": "deepseek-v4-pro",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo",
                })
        self.assertEqual(state["provider"], "opencode")
        self.assertEqual(state["turns"], 2)
        self.assertEqual(state["tokens"], {"input": 1500, "cache_write": 0, "cache_read": 300, "output": 300})
        self.assertEqual(state["total_tokens"], 2150)
        self.assertAlmostEqual(state["total_cost"], 0.015, places=6)
        self.assertEqual(state["primary_model"], "deepseek-v4-pro")
        self.assertTrue(state["availability"]["tokens"])
        self.assertTrue(state["availability"]["cost"])
        self.assertTrue(state["availability"]["context"])
        self.assertTrue(state["timing"]["duration_available"])
        self.assertEqual(state["timing"]["execution_count"], 2)
        # Bucket split preserves the authoritative total.
        self.assertAlmostEqual(sum(state["cost"][k] for k in ("input", "cache_write", "cache_read", "output")),
                               state["total_cost"], places=6)
        self.assertEqual(len(state["executions"]), 2)
        self.assertEqual(state["executions"][0]["tool_count"], 1)
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read")
        self.assertGreater(state["executions"][0]["tools"][0]["output_tokens"], 0)
        self.assertEqual(state["executions"][0]["reasoning_tokens"], 50)
        self.assertEqual(state["executions"][0]["context_tokens"], 1550)
        self.assertEqual(state["executions"][0]["duration_ms"], 5000)
        self.assertEqual([row["user_message"] for row in state["series"]], ["", "Follow up"])
        self.assertEqual([row["user_input"] for row in state["executions"]], ["", "Follow up"])
        user_events = [event for event in state["trace"] if event["kind"] == "user"]
        self.assertEqual([event["detail"] for event in user_events], ["Follow up"])
        self.assertGreater(state["throughput"]["available"], False)

    def test_recompute_dispatch_routes_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.015, 1500, 300, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (top["id"], top["project_id"], top["parent_id"], top["slug"],
                          top["directory"], top["title"], top["version"], top["time_created"],
                          top["time_updated"], top["time_archived"], top["agent"], top["model"],
                          top["cost"], top["tokens_input"], top["tokens_output"],
                          top["tokens_reasoning"], top["tokens_cache_read"], top["tokens_cache_write"]))
            for mid, sid, created, updated, data in self._sample_messages():
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, sid, created, updated, data))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "CLAUDE_PROJECTS", str(root / "no-claude")), \
                    mock.patch.object(meter, "CODEX_SESSIONS", str(root / "no-codex")), \
                    mock.patch.object(meter, "CODEX_INDEX", str(root / "no-index")), \
                    mock.patch.object(meter, "CURSOR_PROJECTS", str(root / "no-cursor")), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}):
                state = meter.recompute({
                    "provider": "opencode", "id": "ses_top", "model": "deepseek-v4-pro",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo",
                })
        self.assertIsNotNone(state)
        self.assertEqual(state["provider"], "opencode")

    def test_recompute_handles_empty_and_zero_cost_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            top = self._session_row("ses_top", "/repo", "New session", "model-a",
                                    0, 0, 0, 0, 0, 0, base)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (
                "u1", "ses_top", base, base,
                json.dumps({"role": "user", "time": {"created": base}}),
            ))
            conn.commit()
            conn.close()
            source = {
                "provider": "opencode", "id": "ses_top", "model": "model-a",
                "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                "path": "opencode:ses_top", "project": "/repo",
            }
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=1000):
                empty_state = meter.recompute_opencode(source)
            self.assertEqual(empty_state["turns"], 0)
            self.assertFalse(empty_state["timing"]["duration_available"])
            self.assertTrue(empty_state["availability"]["cost"])

            conn = sqlite3.connect(root / "opencode.db")
            data = {
                "role": "assistant", "modelID": "model-a", "providerID": "provider-a",
                "tokens": {"input": 1, "output": 0, "reasoning": 1,
                           "cache": {"read": 0, "write": 0}},
                "cost": 0.0, "time": {"created": base + 1000, "completed": base + 2000},
            }
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                         ("a1", "ses_top", base + 1000, base + 2000, json.dumps(data)))
            conn.execute("UPDATE session SET tokens_input=1,tokens_reasoning=1,time_updated=? WHERE id=?",
                         (base, "ses_top"))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=1000):
                zero_state = meter.recompute_opencode(source)
            self.assertTrue(zero_state["availability"]["cost"])
            self.assertTrue(zero_state["executions"][0]["availability"]["cost"])
            self.assertEqual(zero_state["total_cost"], 0)
            self.assertEqual(zero_state["total_tokens"], 2)

    def test_detail_history_reports_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Long", "deepseek-v4-pro",
                                    0.015, 1500, 300, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            for row in self._sample_messages():
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", row)
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "OPENCODE_DETAIL_MESSAGE_LIMIT", 1), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=200000):
                state = meter.recompute_opencode({
                    "provider": "opencode", "id": "ses_top", "model": "deepseek-v4-pro",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo",
                })
        self.assertTrue(state["execution_history_truncated"])
        self.assertEqual(state["execution_history_limit"], 1)
        self.assertEqual(len(state["executions"]), 1)

    def test_opencode_database_is_read_only_and_not_a_delete_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.015, 1500, 300, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (top["id"], top["project_id"], top["parent_id"], top["slug"],
                          top["directory"], top["title"], top["version"], top["time_created"],
                          top["time_updated"], top["time_archived"], top["agent"], top["model"],
                          top["cost"], top["tokens_input"], top["tokens_output"],
                          top["tokens_reasoning"], top["tokens_cache_read"], top["tokens_cache_write"]))
            for mid, sid, created, updated, data in self._sample_messages():
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, sid, created, updated, data))
            conn.execute("INSERT INTO part VALUES ('p1','msg1','ses_top',1,1,'{}')")
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                conn = meter._opencode_db_connection()
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM session WHERE id='ses_top'")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 3)
                conn.close()
        self.assertFalse(hasattr(meter, "remove_opencode_session"))
        self.assertIn("opencode", meter.session_action_capability()["read_only_providers"])

    def test_opencode_summary_reports_latest_context_pct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            top = self._session_row("ses_top", "/repo", "Explain it", "deepseek-v4-pro",
                                    0.015, 1500, 300, 50, 300, 0, 1_784_548_900_000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (top["id"], top["project_id"], top["parent_id"], top["slug"],
                          top["directory"], top["title"], top["version"], top["time_created"],
                          top["time_updated"], top["time_archived"], top["agent"], top["model"],
                          top["cost"], top["tokens_input"], top["tokens_output"],
                          top["tokens_reasoning"], top["tokens_cache_read"], top["tokens_cache_write"]))
            for mid, sid, created, updated, data in self._sample_messages():
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", (mid, sid, created, updated, data))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=200000):
                row = meter.opencode_summary({
                    "provider": "opencode", "id": "ses_top", "model": "deepseek-v4-pro",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo", "title": "Explain it",
                    "mtime": 1_784_548_900.0,
                })
        self.assertEqual(row["context"]["window"], 200000)
        self.assertEqual(row["context"]["latest"], 600)
        self.assertAlmostEqual(row["context"]["latest_pct"], 600 / 200000)
        self.assertFalse(row["context"]["estimated"])
        self.assertEqual(row["_context_samples"], [1550, 600])
        self.assertTrue(row["availability"]["context"])

    def test_opencode_summary_processes_only_newest_500_messages_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_800_000
            top = self._session_row(
                "ses_top", "/repo", "Long history", "model-a",
                5.01, 501, 501, 0, 0, 0, base + 501_000,
            )
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            for index in range(501):
                created = base + index * 1_000
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", self._message(
                    f"a{index:04d}", "ses_top", {
                        "role": "assistant", "modelID": "model-a",
                        "providerID": "provider-a",
                        "tokens": {"input": 1, "output": 1, "reasoning": 0,
                                   "cache": {"read": 0, "write": 0}},
                        "cost": 0.01,
                        "time": {"created": created, "completed": created + 500},
                    }, created,
                ))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=1_000):
                row = meter.opencode_summary({
                    "provider": "opencode", "id": "ses_top", "model": "model-a",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo",
                    "mtime": (base + 501_000) / 1_000,
                })

        self.assertEqual(row["turns"], 500)
        self.assertEqual(len(row["_performance_samples"]), 500)
        self.assertEqual(row["_performance_samples"][0]["ts"], (base + 1_500) / 1_000)
        self.assertEqual(row["_performance_samples"][-1]["ts"], (base + 500_500) / 1_000)
        self.assertEqual(row["tokens"], 1_002)

    def test_opencode_summary_attributes_models_and_calendar_days_per_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            jan = int(datetime.datetime(2026, 1, 31, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            feb = int(datetime.datetime(2026, 2, 1, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            top = self._session_row("ses_top", "/repo", "Across months", "model-b",
                                    0.1, 30, 7, 5, 3, 2, feb + 1000)
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(top.values()))
            messages = [
                self._message("a1", "ses_top", {
                    "role": "assistant", "modelID": "model-a", "providerID": "provider-a",
                    "tokens": {"input": 10, "output": 4, "reasoning": 2,
                               "cache": {"read": 3, "write": 0}},
                    "cost": 0.1, "time": {"created": jan, "completed": jan + 1000},
                }, jan),
                self._message("a2", "ses_top", {
                    "role": "assistant", "modelID": "model-b", "providerID": "provider-b",
                    "tokens": {"input": 20, "output": 3, "reasoning": 3,
                               "cache": {"read": 0, "write": 2}},
                    "cost": 0.0, "time": {"created": feb, "completed": feb + 1000},
                }, feb),
            ]
            for row in messages:
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", row)
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=1000):
                row = meter.opencode_summary({
                    "provider": "opencode", "id": "ses_top", "model": "model-b",
                    "label": "OpenCode", "runtime": "OpenCode", "session": "ses_top",
                    "path": "opencode:ses_top", "project": "/repo", "mtime": feb / 1000,
                })
        self.assertEqual(row["turns"], 2)
        self.assertEqual(row["tokens"], 47)
        self.assertEqual(row["_day_cost"], {"2026-01-31": 0.1, "2026-02-01": 0.0})
        self.assertEqual(row["_model_tok"], {"model-a": 19, "model-b": 28})
        self.assertEqual({item["model"]: item["executions"] for item in row["model_stats"]},
                         {"model-a": 1, "model-b": 1})
        model_b = next(item for item in row["model_stats"] if item["model"] == "model-b")
        model_a = next(item for item in row["model_stats"] if item["model"] == "model-a")
        self.assertTrue(model_b["availability"]["cost"])
        self.assertEqual((model_a["cache_read_tokens"], model_a["cache_write_tokens"]),
                         (3, 0))
        self.assertEqual((model_a["reasoning_tokens"], model_a["reasoning_output_tokens"]),
                         (2, 6))
        self.assertEqual(model_a["reasoning_executions"], 1)
        self.assertEqual((model_b["cache_read_tokens"], model_b["cache_write_tokens"]),
                         (0, 2))
        self.assertEqual((model_b["reasoning_tokens"], model_b["reasoning_output_tokens"]),
                         (3, 6))
        self.assertEqual(model_b["reasoning_executions"], 1)
        self.assertEqual({item["day"] for item in row["_model_daily"]},
                         {"2026-01-31", "2026-02-01"})

    def test_opencode_efficiency_denominators_exclude_uncovered_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            ts = int(datetime.datetime(
                2026, 8, 27, 12, tzinfo=datetime.timezone.utc,
            ).timestamp() * 1000)
            top = self._session_row(
                "ses_coverage", "/repo", "Mixed evidence", "model-a",
                0.1, 110, 110, 0, 0, 0, ts + 4000,
            )
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(top.values()),
            )
            messages = [
                self._message("a1", "ses_coverage", {
                    "role": "assistant", "modelID": "model-a",
                    "tokens": {"input": 10, "output": 10}, "cost": 0.1,
                    "time": {"created": ts, "completed": ts + 1000},
                }, ts),
                self._message("a2", "ses_coverage", {
                    "role": "assistant", "modelID": "model-a",
                    "tokens": {"input": 100, "output": 100},
                    "time": {"created": ts + 1000, "completed": ts + 2000},
                }, ts + 1000),
                self._message("a3", "ses_coverage", {
                    "role": "assistant", "modelID": "model-a",
                    "time": {"created": ts + 2000, "completed": ts + 3000},
                }, ts + 2000),
                self._message("a4", "ses_coverage", {
                    "role": "assistant", "modelID": "model-a", "tokens": {},
                    "time": {"created": ts + 3000, "completed": ts + 3500},
                }, ts + 3000),
                self._message("a5", "ses_coverage", {
                    "role": "assistant", "modelID": "model-a",
                    "tokens": {"input": "50", "output": "20"},
                    "time": {"created": ts + 3500, "completed": ts + 4000},
                }, ts + 3500),
            ]
            for message in messages:
                conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", message)
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")), \
                    mock.patch.object(meter, "_opencode_model_window", return_value=1000):
                session = meter.opencode_summary({
                    "provider": "opencode", "id": "ses_coverage",
                    "model": "model-a", "label": "OpenCode", "runtime": "OpenCode",
                    "session": "ses_coverage", "path": "opencode:ses_coverage",
                    "project": "/repo", "mtime": ts / 1000,
                })

        stats = session["model_stats"][0]
        self.assertEqual(stats["executions"], 5)
        self.assertEqual(stats["input_tokens"], 110)
        self.assertEqual(stats["output_tokens"], 110)
        self.assertEqual(stats["token_covered_executions"], 2)
        self.assertEqual(stats["cache_covered_input_tokens"], 110)
        self.assertEqual(stats["cost_covered_executions"], 1)
        self.assertEqual(stats["cost_covered_output_tokens"], 10)
        aggregate = meter.aggregate_model_stats([session])["models"][0]
        self.assertEqual(aggregate["cost_covered_output_tokens"], 10)
        self.assertEqual(aggregate["token_covered_executions"], 2)
        self.assertFalse(aggregate["coverage"]["cost"]["complete"])
        self.assertEqual(aggregate["daily"][0]["cost_covered_output_tokens"], 10)
        self.assertEqual(aggregate["daily"][0]["token_covered_executions"], 2)
        self.assertFalse(aggregate["daily"][0]["coverage"]["tokens"]["complete"])

    def test_opencode_budget_defaults_and_distribute_cost(self):
        with mock.patch.object(meter, "OPENCODE_DB", str(Path("/nonexistent/opencode.db"))):
            settings = meter.normalize_budget_settings({"allocations": {}})
        self.assertEqual(settings["allocations"]["opencode"], meter.DEFAULT_RUNTIME_BUDGET)
        self.assertEqual(settings["allocations"]["kiro"], meter.DEFAULT_RUNTIME_BUDGET)
        usage = {"input_tokens": 1000, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 500, "output_tokens": 100, "reasoning_tokens": 50}
        split = meter._opencode_distribute(0.02, usage)
        self.assertAlmostEqual(sum(split.values()), 0.02, places=6)
        self.assertEqual(meter._opencode_distribute(0.0, usage)["output"], 0.0)


if __name__ == "__main__":
    unittest.main()
