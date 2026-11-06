import unittest
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter


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
                if subagent:
                    header["subagentInfo"] = {
                        "parentComposerId": "newer", "subagentTypeName": "explore",
                    }
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
            nested = (projects / "Users-test-repo" / "agent-transcripts" / "newer" /
                      "subagents" / "nested.jsonl")
            nested.parent.mkdir(parents=True)
            nested.write_text(json.dumps({"role": "user"}) + "\n")
            os.utime(nested, (1_784_548_000, 1_784_548_000))
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
                    mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "no-agy-brain")), \
                    mock.patch.object(meter, "CLAUDE_DESKTOP_DATA_ROOTS", []), \
                    mock.patch.object(meter, "claude_desktop_index", return_value={}):
                sources = meter.all_session_sources()
            self.assertEqual({row["id"] for row in sources}, {"older", "newer", "child", "nested"})
            self.assertEqual(len(sources), 4)
            by_id = {row["id"]: row for row in sources}
            parent_agent = meter.cursor_agent_id("newer")
            self.assertEqual(by_id["newer"]["agent_kind"], "root")
            self.assertEqual(by_id["newer"]["agent_id"], parent_agent)
            self.assertIsNone(by_id["newer"]["agent_parent_id"])
            self.assertEqual(by_id["child"]["agent_kind"], "spawned")
            self.assertEqual(by_id["child"]["agent_parent_id"], parent_agent)
            self.assertEqual(by_id["child"]["agent_role"], "explore")
            self.assertEqual(by_id["nested"]["agent_kind"], "spawned")
            self.assertEqual(by_id["nested"]["agent_parent_id"], parent_agent)
            self.assertTrue(by_id["nested"]["project"].endswith("test/repo"))
            self.assertNotIn("newer", by_id["newer"]["agent_id"])
            self.assertEqual(max(sources, key=lambda row: row["mtime"])["id"], "newer")
            newer = next(row for row in sources if row["id"] == "newer")
            self.assertEqual(newer["path"], str(duplicate))
            self.assertEqual(newer["model"], "composer-2.5")
            self.assertEqual(newer["project"], "/repo")
            self.assertEqual(newer["signature_mtime"], newer["mtime"])
            self.assertTrue(newer["request_revision"])

    def test_cursor_forked_subagent_skips_inherited_bubbles_and_joins_parent_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.vscdb"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT, createdAt INTEGER, lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER, checkpointAt INTEGER, value TEXT)")
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            base_ms = 1_784_548_800_000

            def bubble(sid, bid, kind, offset, text):
                conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (
                    f"bubbleId:{sid}:{bid}", json.dumps({
                        "type": kind, "bubbleId": bid, "createdAt": base_ms + offset,
                        "text": text, "modelInfo": {"modelName": "gpt-5.6-sol"},
                        **({"turnDurationMs": 1000} if kind == 2 else {}),
                    })))

            shared = [("u1", 1, 0, "parent ask"), ("a1", 2, 1000, "p" * 400)]
            for sid, header, own in (
                ("parent", {}, []),
                ("fork", {"subagentInfo": {
                    "parentComposerId": "parent", "forkedFromComposerId": "parent",
                    "conversationLengthAtSpawn": 2, "subagentTypeName": "generalPurpose",
                }}, [("u2", 1, 5000, "child ask"), ("a2", 2, 6000, "c" * 40)]),
            ):
                rows = shared + own
                for bid, kind, offset, text in rows:
                    bubble(sid, bid, kind, offset, text)
                conn.execute("INSERT INTO composerHeaders VALUES (?, 'w', ?, ?, 0, ?, 0, ?)", (
                    sid, base_ms, base_ms + 7000, 1 if own else 0, json.dumps(header)))
                conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (
                    f"composerData:{sid}", json.dumps({
                        "modelConfig": {"modelName": "gpt-5.6-sol"},
                        "contextTokensUsed": 1000,
                        "fullConversationHeadersOnly": [{"bubbleId": row[0]} for row in rows],
                    })))
            conn.commit()
            conn.close()
            adapter = meter._cursor_adapter_for(
                projects_root=str(Path(tmp) / "projects"), database_path=str(db_path),
                request_logs=str(Path(tmp) / "logs"),
            )
            fork = adapter.snapshot("fork")
            self.assertEqual([item["bubbleId"] for item in fork["bubbles"]], ["u2", "a2"])
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE cursorDiskKV SET value=? WHERE key='composerData:parent'", (
                json.dumps({"fullConversationHeadersOnly": [{"bubbleId": "u1"}, {"bubbleId": "other"}]}),))
            conn.commit()
            conn.close()
            adapter.reset_metadata_cache()
            partial = adapter.snapshot("fork")
            self.assertEqual([item["bubbleId"] for item in partial["bubbles"]], ["a1", "u2", "a2"])
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE cursorDiskKV SET value=? WHERE key='composerData:parent'", (
                json.dumps({"fullConversationHeadersOnly": [{"bubbleId": "u1"}, {"bubbleId": "a1"}]}),))
            conn.commit()
            conn.close()

            parent_source = {**self.source("/tmp/parent.jsonl", "parent"),
                             "agent_id": meter.cursor_agent_id("parent"),
                             "agent_kind": "root", "agent_parent_id": None, "agent_role": ""}
            fork_source = {**self.source("/tmp/fork.jsonl", "fork"),
                           "agent_id": meter.cursor_agent_id("fork"), "agent_kind": "spawned",
                           "agent_parent_id": meter.cursor_agent_id("parent"),
                           "agent_role": "generalPurpose"}
            with mock.patch.object(meter, "cursor_snapshot", side_effect=adapter.snapshot_legacy), \
                    mock.patch.object(meter, "cursor_request_spans", return_value=[]), \
                    mock.patch.object(meter, "load", return_value=[]):
                rows = [adapter.summarize_legacy(parent_source),
                        adapter.summarize_legacy(fork_source)]
        fork_record = rows[1]["_agent_records"][0]
        self.assertEqual(fork_record["output_tokens"], 10)
        self.assertEqual(fork_record["role"], "generalPurpose")
        self.assertEqual(fork_record["session_id"], "fork")
        self.assertEqual(fork_record["activity_state"], "complete")
        groups = meter._domain_build_agent_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["root_session_id"], "parent")
        self.assertEqual(groups[0]["coverage"]["relationships"], "complete")
        self.assertEqual([agent["kind"] for agent in groups[0]["agents"]], ["root", "spawned"])
        self.assertEqual(groups[0]["child_totals"]["tokens"], 1010)

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

    def test_cursor_turn_timing_attributes_each_span_to_the_turn_it_started_in(self):
        base = 1_784_548_800.0
        starts = [base, base + 76.5, base + 86.6]
        spans = [
            {"name": "agent.request.attempt", "start_ts": base + 0.2,
             "end_ts": base + 76.6, "duration_s": 76.4, "request_id": "a", "error": False},
            {"name": "agent.request.attempt", "start_ts": base + 76.7,
             "end_ts": base + 101.6, "duration_s": 24.9, "request_id": "b", "error": False},
            {"name": "agent.request.attempt", "start_ts": base + 84.1,
             "end_ts": base + 90.0, "duration_s": 5.9, "request_id": "c", "error": False},
        ]
        timings = [
            meter.cursor_turn_timing(
                spans, start, starts[index + 1] if index + 1 < len(starts) else 0,
                start + 1,
            )
            for index, start in enumerate(starts)
        ]
        self.assertEqual([round(item["active_s"], 1) for item in timings], [76.4, 24.9, 5.9])
        self.assertEqual([item["attempts"] for item in timings], [1, 1, 1])
        self.assertAlmostEqual(timings[1]["wait_s"], 25.1, places=3)
        self.assertAlmostEqual(timings[2]["end_ts"], base + 90.0, places=3)

        retry = {"name": "agent.request.attempt", "start_ts": base + 80.0,
                 "end_ts": base + 82.0, "duration_s": 2.0, "request_id": "turn-a",
                 "error": False}
        claimed = frozenset(("turn-a", "turn-b"))
        turn_a = meter.cursor_turn_timing(
            [spans[0], retry], base, base + 10, base + 1,
            request_id="turn-a", claimed_request_ids=claimed,
        )
        turn_b = meter.cursor_turn_timing(
            [spans[0], retry], base + 10, 0, base + 11,
            request_id="turn-b", claimed_request_ids=claimed,
        )
        self.assertEqual((turn_a["attempts"], turn_b["attempts"]), (2, 0))
        self.assertAlmostEqual(turn_a["end_ts"], base + 82.0, places=3)

    def test_cursor_tool_identity_classifies_orchestration_interaction_and_mcp_tools(self):
        expected = {
            "task_v2": ("Subagent", "orchestration", "tool"),
            "update_current_step": ("Update step", "planning", "tool"),
            "ask_question": ("Ask question", "interaction", "tool"),
            "switch_mode": ("Switch mode", "interaction", "tool"),
            "set_active_branch": ("Set branch", "workspace", "tool"),
            "search_conversations": ("Search chats", "search", "tool"),
            "get_mcp_tools": ("MCP tool lookup", "tool_search", "tool"),
            "mcp-cursor-app-control-open_resource": (
                "open_resource", "cursor-app-control", "mcp",
            ),
        }
        for name, (display, namespace, kind) in expected.items():
            with self.subTest(name=name):
                ident = meter.cursor_tool_identity(name)
                self.assertEqual(ident["name"], name)
                self.assertEqual(
                    (ident["display"], ident["namespace"], ident["kind"]),
                    (display, namespace, kind),
                )
        self.assertEqual(meter.cursor_tool_identity("Read")["name"], "read_file_v2")
        self.assertEqual(meter.cursor_tool_identity("mcp-")["kind"], "tool")

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

    def test_cursor_subagent_prices_the_speed_its_parent_spawn_call_recorded(self):
        def child():
            data = self.snapshot()
            data["header"]["subagentInfo"] = {"parentComposerId": "parent", "toolCallId": "spawn-1"}
            data["composer"]["modelConfig"]["selectedModels"] = [{"modelId": "composer-2.5"}]
            for bubble in data["bubbles"]:
                bubble.pop("modelInfo", None)
            return data

        def parent(spawn_model, tool_call_id="spawn-1"):
            return {"available": True, "header": {}, "composer": {}, "bubbles": [
                {"type": 2, "bubbleId": "spawn", "toolFormerData": {
                    "name": "task_v2", "toolCallId": tool_call_id,
                    "params": json.dumps({"model": spawn_model, "prompt": "delegate"}),
                }},
            ]}

        cases = (
            ("composer-2.5-fast", "spawn-1", 3.0),
            ("composer-2.5-standard", "spawn-1", 0.5),
            ("composer-2.5", "spawn-1", None),
            ("grok-4.6-fast", "spawn-1", None),
            ("composer-2.5-fast", "other-call", None),
        )
        for spawn_model, tool_call_id, input_rate in cases:
            with self.subTest(spawn_model=spawn_model, tool_call_id=tool_call_id):
                snapshots = {"cursor-session": child(), "parent": parent(spawn_model, tool_call_id)}
                with mock.patch.object(meter, "cursor_snapshot", side_effect=snapshots.__getitem__), \
                        mock.patch.object(meter, "cursor_request_spans", return_value=self.spans()):
                    state = meter.recompute_cursor(self.source())
                execution = state["executions"][0]
                if input_rate is None:
                    self.assertFalse(state["availability"]["cost"])
                    self.assertEqual(execution["cost"], 0)
                    continue
                self.assertTrue(state["availability"]["cost"])
                self.assertAlmostEqual(execution["cost_breakdown"]["input"], 80448 * input_rate / 1e6)

    def test_cursor_pricing_uses_published_third_party_and_grok_rates(self):
        after_sol_update = 1_788_000_000
        cases = {
            ("claude-opus-5-5", ""): (4.0, 5.0, 0.2, 20.0),
            ("claude-opus-5-5-medium", ""): (4.0, 5.0, 0.2, 20.0),
            ("gpt-5.6-sol", ""): (4.0, 5.0, 0.4, 20.0),
            ("gpt-5.6-sol-xhigh", ""): (4.0, 5.0, 0.4, 20.0),
            ("grok-4.7", "standard"): (2.0, 0.0, 0.5, 6.0),
            ("grok-4.7", "fast"): (4.0, 0.0, 1.0, 12.0),
            ("grok-4.7", "500k-standard"): (4.0, 0.0, 1.0, 12.0),
            ("grok-4.7", "500k-fast"): (6.0, 0.0, 1.5, 18.0),
        }
        for (model, variant), rates in cases.items():
            with self.subTest(model=model, variant=variant):
                price, _ = meter.price_for(model, "cursor", variant, at=after_sol_update)
                self.assertEqual(
                    (price["input"], price["cache_write"], price["cache_read"], price["output"]),
                    rates,
                )
        before, _ = meter.price_for("gpt-5.6-sol", "cursor", "", at=1_786_000_000)
        self.assertEqual((before["input"], before["output"]), (5.0, 30.0))
        for model, variant in (("claude-opus-5-5", "fast"), ("gpt-5.6-sol", "max"),
                               ("gpt-5.6-sol", "max-fast"), ("composer-2.5", "max"),
                               ("composer-2.5", "max-fast"), ("claude-opus-5-5-turbo", ""),
                               ("claude-opus-5-5-turbo", "max")):
            with self.subTest(unpriced=model, variant=variant):
                price, _ = meter.price_for(model, "cursor", variant)
                self.assertEqual(price, meter.ZERO_PRICE)

    def test_cursor_max_mode_bills_the_api_rate_plus_twenty_percent(self):
        cases = {
            ("claude-opus-5-5", "max"): (4.8, 6.0, 0.24, 24.0),
            ("claude-opus-5-5-medium", "max"): (4.8, 6.0, 0.24, 24.0),
            ("claude-opus-5-5", "max-fast"): (9.6, 12.0, 0.48, 48.0),
        }
        for (model, variant), rates in cases.items():
            with self.subTest(model=model, variant=variant):
                price, _ = meter.price_for(model, "cursor", variant)
                self.assertEqual(
                    tuple(round(price[key], 6) for key in ("input", "cache_write", "cache_read", "output")),
                    rates,
                )
        cost = meter.cost_of({"input_tokens": 1_000_000, "output_tokens": 100_000},
                             "claude-opus-5-5", "cursor", "max")
        self.assertAlmostEqual(sum(cost.values()), 4.8 + 2.4)

    def test_cursor_model_name_drops_effort_suffix_only_for_selected_model(self):
        def composer(name, model_id):
            return {"modelConfig": {"modelName": name, "selectedModels": [{"modelId": model_id}]}}
        self.assertEqual(meter.cursor_model(composer("gpt-5.6-sol-xhigh", "gpt-5.6-sol")),
                         "gpt-5.6-sol")
        self.assertEqual(meter.cursor_model(composer("claude-opus-5-5-medium", "claude-opus-5-5")),
                         "claude-opus-5-5")
        self.assertEqual(meter.cursor_model(composer("gpt-5.6-sol-turbo", "gpt-5.6-sol")),
                         "gpt-5.6-sol-turbo")
        self.assertEqual(meter.cursor_model(composer("grok-4.6-high", "composer-2.5")),
                         "grok-4.6-high")

    def test_cursor_price_variant_reads_fast_max_and_context_parameters(self):
        def composer(model, params, max_mode=False):
            return {"modelConfig": {"modelName": model, "maxMode": max_mode, "selectedModels": [{
                "modelId": model,
                "parameters": [{"id": key, "value": value} for key, value in params.items()],
            }]}}
        self.assertEqual(meter.cursor_price_variant(
            composer("claude-opus-5-5", {"fast": "false", "effort": "medium"}), "claude-opus-5-5"), "")
        self.assertEqual(meter.cursor_price_variant(
            composer("claude-opus-5-5", {"fast": "true"}), "claude-opus-5-5"), "fast")
        self.assertEqual(meter.cursor_price_variant(
            composer("gpt-5.6-sol", {}, max_mode=True), "gpt-5.6-sol"), "max")
        self.assertEqual(meter.cursor_price_variant(
            composer("composer-2.5", {"fast": "true"}, max_mode=True), "composer-2.5"), "max-fast")
        self.assertEqual(meter.cursor_price_variant(
            composer("claude-opus-5-5", {"fast": "false", "context": "300k"}, max_mode=True),
            "claude-opus-5-5"), "max")
        self.assertEqual(meter.cursor_price_variant(
            composer("claude-opus-5-5", {"fast": "true", "context": "1m"}, max_mode=True),
            "claude-opus-5-5-high"), "max-fast")
        self.assertEqual(meter.cursor_price_variant(
            composer("grok-4.7", {"fast": "true", "context": "500k"}), "grok-4.7"), "500k-fast")
        self.assertEqual(meter.cursor_price_variant(
            composer("grok-4.7", {"fast": "false"}), "grok-4.7"), "standard")

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
        # Live-session selection uses the end of the last execution, not the database file time.
        self.assertEqual(row["last_activity_ts"], float(state["timing"]["end_ts"]))
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
        self.assertEqual(stats["io_covered_input_tokens"], 100)
        self.assertEqual(stats["io_covered_output_tokens"], 20)
        self.assertEqual(stats["cost_covered_executions"], 1)
        self.assertEqual(stats["cost_covered_output_tokens"], 20)
        self.assertAlmostEqual(stats["cost_covered_cost"], 0.1)
        self.assertEqual(stats["reasoning_executions"], 1)
        self.assertEqual(stats["reasoning_unavailable_executions"], 1)

        aggregate = meter.aggregate_model_stats([row])["models"][0]
        self.assertFalse(aggregate["coverage"]["cost"]["complete"])
        self.assertFalse(aggregate["coverage"]["reasoning_tokens"]["complete"])
        self.assertEqual(aggregate["io_covered_executions"], 1)
        self.assertEqual(aggregate["io_covered_input_tokens"], 100)
        self.assertEqual(aggregate["io_covered_output_tokens"], 20)
        self.assertEqual(aggregate["daily"][0]["io_covered_input_tokens"], 100)
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


if __name__ == "__main__":
    unittest.main()
