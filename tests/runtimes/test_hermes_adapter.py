import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis, session_source_public_dict
from token_meter.runtimes.hermes import HermesRuntimeAdapter


class HermesRuntimeAdapterTests(unittest.TestCase):
    def test_environment_precedence_is_state_db_then_home_then_default(self):
        self.assertEqual(meter.hermes_state_db_path({"HERMES_STATE_DB": "/one", "HERMES_HOME": "/two"}), "/one")
        self.assertEqual(meter.hermes_state_db_path({"HERMES_HOME": "/two"}), "/two/state.db")
        self.assertTrue(meter.hermes_state_db_path({}).endswith(".hermes/state.db"))

    def _create_store(self, root, *, estimated_cost=0.0042):
        path = Path(root) / "state.db"
        con = sqlite3.connect(path)
        con.execute("""CREATE TABLE sessions (
            id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT, started_at REAL,
            ended_at REAL, last_activity_at REAL, input_tokens INTEGER,
            output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
            cost_status TEXT, cost_source TEXT, billing_mode TEXT, tool_call_count INTEGER, api_call_count INTEGER,
            cwd TEXT, title TEXT
        )""")
        con.execute("""INSERT INTO sessions VALUES (
            'hermes-session', 'claude-test', 'anthropic', 100.0, 112.0, 112.0,
            100, 20, 10, 5, 3, ?, NULL, 'estimated', 'recorded', NULL, 2, 1,
            '/private/project', 'sensitive title excluded'
        )""", (estimated_cost,))
        con.commit()
        con.close()
        return path

    def test_normalizes_aggregated_sqlite_evidence_without_session_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            adapter = HermesRuntimeAdapter(db_path)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.runtime_id, "hermes")
        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(source.model_ref.model_id, "claude-test")
        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.output_tokens.value, 20)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 10)
        self.assertEqual(loaded.usage.cache_write_tokens.value, 5)
        self.assertEqual(loaded.usage.cost_usd.value, 0.0042)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(loaded.tools, ())
        self.assertEqual(loaded.turns, ())
        self.assertNotIn("locator", session_source_public_dict(source))
        self.assertNotIn("private/project", repr(loaded))
        self.assertNotIn("sensitive title", repr(loaded))

    def test_unavailable_cost_remains_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp, estimated_cost=None)
            adapter = HermesRuntimeAdapter(db_path)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cost_usd.value)

    def test_default_zero_cost_without_a_recorded_status_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp, estimated_cost=0.0)
            con = sqlite3.connect(db_path)
            con.execute("UPDATE sessions SET cost_status = NULL, cost_source = NULL")
            con.commit()
            con.close()
            source = HermesRuntimeAdapter(db_path).discover(DiscoveryContext(home="/home/test"))[0]
            loaded = HermesRuntimeAdapter(db_path).load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)

    def test_cost_status_without_a_qualifying_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp, estimated_cost=0.0)
            con = sqlite3.connect(db_path)
            con.execute("UPDATE sessions SET cost_source = 'unknown'")
            con.commit()
            con.close()
            source = HermesRuntimeAdapter(db_path).discover(DiscoveryContext(home="/home/test"))[0]
            loaded = HermesRuntimeAdapter(db_path).load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)

    def test_opens_the_store_read_only_and_denies_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            adapter = HermesRuntimeAdapter(db_path)
            with self.assertRaises(sqlite3.DatabaseError):
                with adapter._connection() as con:
                    con.execute("DELETE FROM sessions")
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]

        self.assertEqual(adapter.deletion_plan(source).disposition.value, "deny")
        self.assertIn("hermes", meter.session_action_capability()["read_only_providers"])

    def test_server_delete_boundary_rejects_hermes_before_trash(self):
        source = {"provider": "hermes", "id": "hermes-session"}
        with mock.patch.object(meter, "find_session", return_value=source), \
                mock.patch.object(meter, "trash_session_log") as trash:
            result = meter.request_session_delete("hermes-session")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        self.assertIn("Hermes Agent", result["error"])
        trash.assert_not_called()

    def test_default_authorizer_denies_raw_queries_but_allows_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path); con.execute("CREATE TABLE messages (content TEXT)"); con.commit(); con.close()
            adapter = HermesRuntimeAdapter(db_path)
            self.assertTrue(adapter.discover(DiscoveryContext(home="/home/test")))
            with adapter._connection() as con:
                with self.assertRaises(sqlite3.DatabaseError): con.execute("SELECT cwd FROM sessions")
                with self.assertRaises(sqlite3.DatabaseError): con.execute("SELECT content FROM messages")
                with self.assertRaises(sqlite3.DatabaseError): con.execute("PRAGMA table_info(messages)")

    def test_query_boundary_never_authorizes_raw_trace_columns_or_tables(self):
        reads = []
        forbidden = {"cwd", "messages", "fts", "system_prompts", "tool_calls", "content"}
        def authorizer(action, arg1, arg2, *_):
            reads.append((arg1, arg2))
            return sqlite3.SQLITE_DENY if str(arg1 or "").lower() in forbidden or str(arg2 or "").lower() in forbidden else sqlite3.SQLITE_OK
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            adapter = HermesRuntimeAdapter(
                db_path, authorizer=authorizer,
            )
            sources = adapter.discover(DiscoveryContext(home="/home/test"))
            self.assertTrue(sources)
        observed = " ".join("{} {}".format(a or "", b or "") for a, b in reads).lower()
        for forbidden in ("cwd", "messages", "fts", "system_prompts", "tool_calls", "content"):
            self.assertNotIn(forbidden, observed)

    def test_subscription_included_cost_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp, estimated_cost=None)
            con = sqlite3.connect(db_path)
            con.execute("UPDATE sessions SET cost_status='included', cost_source='none', billing_mode='subscription_included'")
            con.commit(); con.close()
            source = HermesRuntimeAdapter(db_path).discover(DiscoveryContext(home="/home/test"))[0]
            loaded = HermesRuntimeAdapter(db_path).load(source, DetailLevel.SUMMARY)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cost_usd.value)

    def test_partial_older_schema_degrades_without_opening_message_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            con.execute("CREATE TABLE messages (content TEXT, tool_calls TEXT)")
            con.execute("INSERT INTO sessions VALUES ('older-session')")
            con.commit()
            con.close()
            adapter = HermesRuntimeAdapter(db_path)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.model_ref.model_id, "unknown-model")
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(loaded.tools, ())

    def test_wal_sidecar_changes_the_source_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            adapter = HermesRuntimeAdapter(db_path)
            before = adapter.current_revision(None)
            wal = Path(str(db_path) + "-wal")
            wal.write_bytes(b"changed")
            after = adapter.current_revision(None)

        self.assertNotEqual(before, after)

    def test_legacy_composition_projects_aggregate_usage_without_a_database_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.hermes_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.hermes_summary(source)
                cached_summary = meter.session_summary(source)

        self.assertEqual(source["provider"], "hermes")
        self.assertEqual(source["project"], "")
        self.assertFalse(source["path"].startswith("/"))
        self.assertEqual(state["tokens"], {
            "input": 100, "cache_write": 5, "cache_read": 10, "output": 20,
        })
        self.assertTrue(state["availability"]["cost"])
        self.assertFalse(state["availability"]["timing"])
        self.assertEqual(summary["provider"], "hermes")
        self.assertEqual(cached_summary["provider"], "hermes")
        self.assertEqual(summary["tokens"], 135)
        self.assertEqual(summary["model_stats"][0]["reasoning_tokens"], 3)
        self.assertEqual(state["analyses"]["reasoning"]["think_turns"], 1)
        self.assertEqual(summary["_day_cost"], {})
        self.assertNotIn("private/project", repr({"source": source, "state": state, "summary": summary}))

    def test_legacy_state_projects_one_chart_point_and_the_recorded_execution_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("UPDATE sessions SET api_call_count = 3")
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                state = meter.recompute(meter.hermes_session_sources()[0])

        self.assertEqual(state["turns"], 3)
        self.assertEqual(len(state["series"]), 1)
        self.assertEqual(state["series"][0]["i"], 1)
        self.assertEqual(state["series"][0]["in"], 100)
        self.assertEqual(state["series"][0]["out"], 20)
        self.assertEqual(state["series"][0]["cache_read"], 10)
        self.assertEqual(state["series"][0]["cache_write"], 5)
        self.assertEqual(state["series"][0]["reasoning"], 3)
        self.assertEqual(len(state["executions"]), 1)

    def test_zero_reasoning_tokens_do_not_create_a_reasoning_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("UPDATE sessions SET reasoning_tokens = 0")
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.hermes_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.hermes_summary(source)

        self.assertEqual(state["analyses"]["reasoning"]["think_turns"], 0)
        self.assertEqual(summary["model_stats"][0]["reasoning_executions"], 0)

    def test_zero_per_model_reasoning_tokens_do_not_convert_api_calls_to_reasoning_executions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("""CREATE TABLE session_model_usage (
                session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER,
                output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
                cost_status TEXT, cost_source TEXT, api_call_count INTEGER
            )""")
            con.execute("INSERT INTO session_model_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                "hermes-session", "claude-test", "anthropic", 100, 20, 10, 5,
                0, .0042, None, "estimated", "recorded", 7,
            ))
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])

        self.assertEqual(summary["model_stats"][0]["executions"], 7)
        self.assertEqual(summary["model_stats"][0]["reasoning_tokens"], 0)
        self.assertEqual(summary["model_stats"][0]["reasoning_executions"], 0)

    def test_out_of_range_timestamps_degrade_to_unavailable_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute(
                "UPDATE sessions SET started_at = ?, ended_at = ?, last_activity_at = ?",
                (1e300, 1e300, 1e300),
            )
            con.commit()
            con.close()
            adapter = HermesRuntimeAdapter(db_path)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.activity_mtime, 0.0)
        self.assertIsNone(loaded.started_at)
        self.assertIsNone(loaded.ended_at)

    def test_per_model_aggregates_keep_two_models_separate_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("""CREATE TABLE session_model_usage (
                session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER,
                output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
                cost_status TEXT, cost_source TEXT, api_call_count INTEGER
            )""")
            con.executemany("INSERT INTO session_model_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                ("hermes-session", "claude-first", "anthropic", 40, 10, 4, 2, 1, 0.0015, None, "estimated", "recorded", 1),
                ("hermes-session", "claude-second", "anthropic", 60, 10, 6, 3, 2, 0.0027, None, "estimated", "recorded", 1),
            ])
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])

        self.assertEqual(summary["tokens"], 135)
        self.assertAlmostEqual(summary["cost"], 0.0042)
        self.assertEqual({row["model"] for row in summary["model_stats"]}, {"anthropic:anthropic:claude-first", "anthropic:anthropic:claude-second"})
        self.assertEqual(sum(row["tokens"] for row in summary["model_stats"]), 135)

    def test_session_summary_reconciles_cost_when_activity_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp, estimated_cost=None)
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), mock.patch.object(meter, "_hermes_native_adapters", {}), mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.hermes_session_sources()[0]
                self.assertFalse(meter.session_summary(source)["availability"]["cost"])
                con = sqlite3.connect(db_path); con.execute("UPDATE sessions SET estimated_cost_usd=0.0042, cost_status='estimated', cost_source='recorded'"); con.commit(); con.close()
                refreshed = meter.session_summary(meter.hermes_session_sources()[0])
        self.assertTrue(refreshed["availability"]["cost"])
        self.assertAlmostEqual(refreshed["cost"], 0.0042)

    def test_partial_model_rows_fall_back_without_undercounting_session_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.execute("INSERT INTO session_model_usage VALUES ('hermes-session','claude-test','anthropic',40,10,4,2,1,0.001,NULL,'estimated','recorded',3)")
            con.commit(); con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), mock.patch.object(meter, "_hermes_native_adapters", {}), mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])
        self.assertEqual(summary["tokens"], 135)
        self.assertAlmostEqual(summary["cost"], 0.0042)

    def test_mixed_model_cost_keeps_unknown_model_uncovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp); con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [("hermes-session","known","anthropic",40,10,4,2,1,.0015,None,"estimated","recorded",2),("hermes-session","unknown","anthropic",60,10,6,3,2,None,None,"unknown","none",3)])
            con.commit(); con.close()
            with mock.patch.object(meter,"HERMES_STATE_DB",str(db_path)), mock.patch.object(meter,"_hermes_native_adapters",{}), mock.patch.object(meter,"_RUNTIME_REGISTRY",None): summary=meter.hermes_summary(meter.hermes_session_sources()[0])
        unknown=next(row for row in summary["model_stats"] if row["model"] == "anthropic:anthropic:unknown")
        self.assertFalse(unknown["availability"]["cost"])
        self.assertNotIn("anthropic:anthropic:unknown", summary["_model_cost"])
        self.assertTrue(summary["availability"]["cost"])

    def test_model_api_call_counts_are_summed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path=self._create_store(tmp); con=sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [("hermes-session","claude-test","anthropic",50,10,5,2,1,.002,None,"estimated","recorded",2),("hermes-session","claude-test","anthropic",50,10,5,3,2,.0022,None,"estimated","recorded",4)])
            con.commit(); con.close()
            with mock.patch.object(meter,"HERMES_STATE_DB",str(db_path)), mock.patch.object(meter,"_hermes_native_adapters",{}), mock.patch.object(meter,"_RUNTIME_REGISTRY",None): summary=meter.hermes_summary(meter.hermes_session_sources()[0])
        self.assertEqual(summary["model_stats"][0]["executions"], 6)

    def test_same_model_under_distinct_billing_routes_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                ("hermes-session", "claude-test", "anthropic", 40, 10, 4, 2, 1, .0015, None, "estimated", "recorded", 2),
                ("hermes-session", "claude-test", "bedrock", 60, 10, 6, 3, 2, .0027, None, "estimated", "recorded", 4),
            ])
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])

        self.assertEqual(
            {row["model"] for row in summary["model_stats"]},
            {"anthropic:anthropic:claude-test", "anthropic:amazon:claude-test"},
        )
        self.assertEqual(sum(row["tokens"] for row in summary["model_stats"]), 135)
        self.assertEqual(sum(row["executions"] for row in summary["model_stats"]), 6)

    def test_same_model_and_route_under_distinct_billing_modes_stays_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, billing_mode TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                ("hermes-session", "claude-test", "anthropic", "official_docs_snapshot", 50, 10, 5, 2, 1, .0042, None, "estimated", "recorded", 2),
                ("hermes-session", "claude-test", "anthropic", "subscription_included", 50, 10, 5, 3, 2, 0.0, None, "included", "none", 4),
            ])
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])

        self.assertEqual(summary["tokens"], 135)
        self.assertAlmostEqual(summary["cost"], .0042)
        self.assertEqual(len(summary["model_stats"]), 2)
        covered = [row for row in summary["model_stats"] if row["availability"]["cost"]]
        uncovered = [row for row in summary["model_stats"] if not row["availability"]["cost"]]
        self.assertEqual(len(covered), 1)
        self.assertEqual(len(uncovered), 1)
        self.assertAlmostEqual(covered[0]["cost"], .0042)
        self.assertEqual(uncovered[0]["cost"], 0.0)
        self.assertEqual(len(summary["_model_cost"]), 1)

    def test_unknown_billing_route_keeps_reasoning_and_execution_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._create_store(tmp)
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
            con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "hermes-session", "claude-test", "private-route", 100, 20, 10, 5,
                3, .0042, None, "estimated", "recorded", 7,
            ))
            con.commit()
            con.close()
            with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                    mock.patch.object(meter, "_hermes_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                summary = meter.hermes_summary(meter.hermes_session_sources()[0])

        self.assertEqual(summary["model_stats"][0]["reasoning_tokens"], 3)
        self.assertEqual(summary["model_stats"][0]["executions"], 7)


if __name__ == "__main__":
    unittest.main()
