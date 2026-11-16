import datetime
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter
from token_meter.projections import agent_usage_projection
from tests.opencode_test_support import OpenCodeCase


class OpenCodeTests(OpenCodeCase):
    def test_reported_cost_rejects_unrepresentable_numbers(self):
        for value in (10 ** 400, float("inf"), float("nan"), -1, True, "1"):
            self.assertEqual(meter._opencode_reported_cost({"cost": value}), (0.0, False))
        self.assertEqual(meter._opencode_reported_cost({"cost": 0}), (0.0, True))
        self.assertEqual(meter._opencode_reported_cost({"cost": 1.25}), (1.25, True))

    def test_anthropic_model_uses_anthropic_rates_for_cache_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._pricing_state(tmp, "claude-sonnet-4-6", "anthropic")

        self.assertEqual(state["executions"][0]["model_provider"], "anthropic")
        self.assertAlmostEqual(state["total_cost"], 0.0123)
        self.assertFalse(state["cost_approx"])
        self.assertTrue(state["cache"]["savings_available"])
        self.assertAlmostEqual(state["cache"]["saved"], 0.0135)
        self.assertAlmostEqual(state["cache_saved"], 0.0135)
        self.assertGreater(state["cache"]["cost"], 0)

    def test_cache_savings_use_session_provider_without_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._pricing_state(
                tmp, "claude-sonnet-4-6", "anthropic", messages=False,
            )

        self.assertEqual(state["executions"], [])
        self.assertTrue(state["cache"]["savings_available"])
        self.assertAlmostEqual(state["cache"]["saved"], 0.0135)

    def test_cost_unavailable_marks_cache_savings_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._pricing_state(
                tmp, "claude-sonnet-4-6", "anthropic", cost=None,
            )

        self.assertFalse(state["availability"]["cost"])
        self.assertFalse(state["cache"]["savings_available"])
        self.assertIsNone(state["cache"]["saved"])

    def test_openai_model_uses_openai_rates_for_cache_savings(self):
        for model in ("gpt-6-astra", "openai/gpt-6-astra"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as tmp:
                state = self._pricing_state(tmp, model, "openai")

            self.assertAlmostEqual(state["total_cost"], 0.0123)
            self.assertTrue(state["cache"]["savings_available"])
            self.assertAlmostEqual(state["cache"]["saved"], 0.045)

    def test_cursor_model_uses_cursor_rates_for_cache_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._pricing_state(tmp, "claude-opus-5-5", "cursor")

        self.assertAlmostEqual(state["total_cost"], 0.0123)
        self.assertTrue(state["cache"]["savings_available"])
        self.assertAlmostEqual(state["cache"]["saved"], 0.019)

    def test_explicit_opencode_price_overrides_upstream_fallback(self):
        prices = {"opencode": {"claude-sonnet-4-6": {
            "input": 9.0, "output": 9.0, "cache_write": 9.0, "cache_read": 1.0,
        }}}
        with tempfile.TemporaryDirectory() as tmp:
            state = self._pricing_state(
                tmp, "claude-sonnet-4-6", "anthropic", prices=prices,
            )

        self.assertAlmostEqual(state["total_cost"], 0.0123)
        self.assertTrue(state["cache"]["savings_available"])
        self.assertAlmostEqual(state["cache"]["saved"], 0.04)

    def test_unpriced_opencode_models_report_unavailable_cache_savings(self):
        for model, provider_id in (
            ("mystery-model-9", "unknown-provider"),
            ("claude-opus-4-8", "opencode"),
            ("claude-opus-4-8", "opencode-go"),
            ("claude-opus-4-8", "openrouter"),
            ("claude-opus-4-8", "amazon-bedrock"),
        ):
            with self.subTest(model=model, provider_id=provider_id), \
                    tempfile.TemporaryDirectory() as tmp:
                state = self._pricing_state(tmp, model, provider_id)

            self.assertAlmostEqual(state["total_cost"], 0.0123)
            self.assertIsNone(state["cache"]["saved"])
            self.assertFalse(state["cache"]["savings_available"])
            self.assertIsNone(state["cache_saved"])
            self.assertNotIn(
                "cache-saved", {row.get("key") for row in state["insights"]},
            )

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
                    mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "no-agy-brain")), \
                    mock.patch.object(meter, "GROK_SESSIONS_DIR", str(root / "no-grok-sessions")), \
                    mock.patch.object(meter, "GROK_LOG_PATH", str(root / "no-grok-log.jsonl")), \
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

    def test_agent_record_never_projects_private_identity_or_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/secret/workdir", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            child = self._session_row("ses_child", "/secret/workdir", "Child", "model-a",
                                      0.75, 300, 60, 0, 0, 0, base + 1000,
                                      parent_id="ses_top", agent="explore")
            self._insert_sessions(conn, (parent, child))
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                ("m1", "ses_child", base, base,
                 json.dumps({"role": "user", "content": "private prompt text"})),
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("p1", "m1", "ses_child", base, base,
                 json.dumps({"type": "tool", "tool": "read", "input": "secret arg"})),
            )
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                record = self._child_agent_row(sources["ses_child"])
                row = meter.session_summary(sources["ses_child"])
                public = agent_usage_projection(
                    meter._domain_aggregate_agent_usage(
                        meter._domain_build_agent_groups([row])
                    )
                )

        serialized = json.dumps({"record": record, "public": public}, default=str)
        for private in (
            "/secret/workdir", "private prompt text", "secret arg", "workdir",
        ):
            self.assertNotIn(private, serialized)
        # The agent and parent identities are opaque digests, never raw ids.
        self.assertNotIn("ses_child", record["id"])
        self.assertNotIn("ses_top", record["parent_id"])
        # The established public session id stays, so the row is navigable.
        self.assertEqual(record["session_id"], "ses_child")

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
            # Naive local noon, because the adapter groups cost by local day
            # (time.localtime). A UTC-built instant lands on the next day east
            # of UTC, which would move the cost out of the asserted bucket.
            jan = int(datetime.datetime(2026, 1, 31, 12).timestamp() * 1000)
            feb = int(datetime.datetime(2026, 2, 1, 12).timestamp() * 1000)
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
