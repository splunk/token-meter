import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.opencode import (
    OpenCodeRuntimeAdapter,
    database_schema,
)
from tests.runtime_projection_privacy import assert_runtime_trace_privacy


SCHEMA = """
CREATE TABLE session (
  id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT, agent TEXT,
  model TEXT, time_created INTEGER, time_updated INTEGER, time_archived INTEGER,
  tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
  tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost REAL
);
CREATE TABLE message (
  id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
  time_created INTEGER, time_updated INTEGER
);
CREATE TABLE part (
  id TEXT PRIMARY KEY, session_id TEXT, message_id TEXT, data TEXT,
  time_created INTEGER, time_updated INTEGER
);
"""

# OpenCode's V2 storage: sessions live in session_v2 (title is nullable) and
# messages replace message + part with one session_message table whose assistant
# rows carry tool and reasoning evidence inline in data.content[]. There is no
# V2 part table.
V2_SCHEMA = """
CREATE TABLE session_v2 (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT,
  parent_id TEXT, fork_session_id TEXT, fork_boundary TEXT,
  slug TEXT NOT NULL, directory TEXT NOT NULL, path TEXT,
  title TEXT, version TEXT NOT NULL,
  cost REAL NOT NULL DEFAULT 0,
  tokens_input INTEGER NOT NULL DEFAULT 0,
  tokens_output INTEGER NOT NULL DEFAULT 0,
  tokens_reasoning INTEGER NOT NULL DEFAULT 0,
  tokens_cache_read INTEGER NOT NULL DEFAULT 0,
  tokens_cache_write INTEGER NOT NULL DEFAULT 0,
  agent TEXT, model TEXT,
  time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
  time_archived INTEGER, time_suspended INTEGER, idle_outcome TEXT
);
CREATE TABLE session_message (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
  seq INTEGER NOT NULL, time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL, data TEXT NOT NULL
);
"""


class OpenCodeRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "opencode.db"
        self.models_path = self.root / "models.json"
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("session-1", None, "/work/project", "A safe title", "build",
                 json.dumps({"providerID": "models", "id": "model-1"}),
                 1_000, 4_000, None, 10, 3, 2, 4, 1, 0.25),
            )
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("child", "session-1", "/work/project", "Child", "build", "{}",
                 1_000, 5_000, None, 0, 0, 0, 0, 0, 0.0),
            )
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                ("user-1", "session-1", json.dumps({
                    "role": "user", "content": "private prompt",
                    "time": {"created": 1_000},
                }), 1_000, 1_000),
            )
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                ("assistant-1", "session-1", json.dumps({
                    "role": "assistant", "modelID": "model-1", "providerID": "models",
                    "time": {"created": 2_000, "completed": 4_000},
                    "tokens": {"input": 10, "output": 3, "reasoning": 2,
                               "cache": {"read": 4, "write": 1}},
                    "cost": 0.25, "content": "private response",
                }), 2_000, 4_000),
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("part-1", "session-1", "assistant-1",
                 json.dumps({"type": "tool", "tool": "read", "input": "secret"}),
                 3_000, 4_000),
            )
        self.models_path.write_text(json.dumps({
            "models": {"id": "models", "models": {
                "model-1": {"limit": {"context": 1000}},
            }},
        }))
        self.adapter = OpenCodeRuntimeAdapter(self.db_path, self.models_path)

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, session_id="session-1", *, legacy=False):
        """Select one discovered session by id.

        Discovery now includes child sessions, so tests must name the session
        they mean rather than relying on the most recently updated row.
        """
        discover = self.adapter.discover_legacy if legacy else self.adapter.discover
        context = DiscoveryContext(home=str(self.root))
        rows = discover(context)
        for row in rows:
            identifier = (
                row.get("id") if legacy else row.session_id
            )
            if identifier == session_id:
                return row
        raise AssertionError(
            "session {!r} was not discovered; got {!r}".format(
                session_id,
                [row.get("id") if legacy else row.session_id for row in rows],
            )
        )

    def test_discovers_roots_and_children_with_stable_revision(self):
        sources = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))

        self.assertEqual(
            sorted(source.session_id for source in sources),
            ["child", "session-1"],
        )
        source = self._source("session-1")
        self.assertEqual(source.runtime_id, "opencode")
        self.assertEqual(source.model_ref.model_id, "model-1")
        self.assertEqual(source.model_ref.provider_id, "models")
        self.assertEqual(source.project, "/work/project")
        self.assertNotIn("private", repr(source))

    def test_child_project_is_scoped_to_the_parent_root_directory(self):
        source = self._source("child")

        self.assertEqual(source.project, "/work/project")
        self.assertNotIn("build", repr(source))

    def test_revision_changes_when_message_or_part_changes(self):
        source = self._source("session-1")
        before = self.adapter.current_revision(source)
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE part SET time_updated=9000 WHERE id='part-1'")
        after = self.adapter.current_revision(source)

        self.assertNotEqual(before, after)

    def test_normalized_load_preserves_measured_zero_and_never_loads_content(self):
        source = self._source("session-1")
        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 5)
        self.assertEqual(result.usage.cache_read_tokens.value, 4)
        self.assertEqual(result.usage.cache_write_tokens.value, 1)
        self.assertEqual(result.usage.cost_usd.value, 0.25)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.MEASURED)
        self.assertEqual(result.timing.active_seconds.value, 2.0)
        self.assertEqual([(tool.name, tool.category) for tool in result.tools], [("read", "tool")])
        serialized = repr(result)
        for private in ("private prompt", "private response", "secret"):
            self.assertNotIn(private, serialized)

    def test_mcp_trace_views_are_structural_and_content_free(self):
        self.adapter.compatibility = meter._opencode_compatibility()
        source = self._source("session-1", legacy=True)
        state = self.adapter.load(source, DetailLevel.FULL)

        assert_runtime_trace_privacy(
            self, source, state, runtime="opencode", model="model-1",
            tool="read", native_types=("message", "part"),
            forbidden=(
                "private prompt", "private response", "secret",
                "/work/project",
            ),
        )

    def test_summary_detail_omits_turns_and_null_evidence_stays_unavailable(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE session SET tokens_cache_write=NULL, cost=NULL WHERE id='session-1'"
            )
        source = self._source("session-1")

        result = self.adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(result.turns, ())
        self.assertEqual(result.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.input_tokens.value, 10)

    def test_corrupt_and_partial_databases_fail_bounded(self):
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        partial = self.root / "partial.db"
        with contextlib.closing(sqlite3.connect(partial)) as conn, conn:
            conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")

        for path in (corrupt, partial):
            with self.subTest(path=path.name):
                adapter = OpenCodeRuntimeAdapter(path, self.models_path)
                self.assertEqual(tuple(adapter.discover(DiscoveryContext(home=str(self.root)))), ())

    def test_connection_is_query_only(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM session")

    def test_adapter_closes_owned_database_connections(self):
        connection = self.adapter.connection()
        self.addCleanup(connection.close)

        with mock.patch.object(self.adapter, "connection", return_value=connection):
            tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_legacy_discovery_projection_matches_current_shape(self):
        rows = self.adapter.discover_legacy(DiscoveryContext(home=str(self.root)))

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["id"] for row in rows}, {"session-1", "child"})
        root = self._source("session-1", legacy=True)
        self.assertEqual(root, {
            "provider": "opencode", "client": "opencode", "label": "OpenCode",
            "runtime": "OpenCode", "id": "session-1", "session": "session-1",
            "path": "opencode:session-1", "project": "/work/project", "mtime": 4.0,
            "signature_mtime": 4.0, "title": "A safe title", "model": "model-1",
            "model_provider": "models", "agent": "build", "tools_loaded": 0,
            "agent_parent_id": "", "agent_root_id": "", "agent_role": "build",
            "agent_depth": 0, "agent_has_children": True,
        })
        child = self._source("child", legacy=True)
        self.assertEqual(child["id"], "child")
        self.assertEqual(child["agent_parent_id"], "session-1")
        self.assertEqual(child["agent_root_id"], "session-1")
        self.assertEqual(child["agent_depth"], 1)
        self.assertEqual(child["project"], "/work/project")
        self.assertFalse(child["agent_has_children"])
        self.assertTrue(root["agent_has_children"])

    def test_child_agent_record_uses_bounded_session_title_as_label(self):
        self.adapter.compatibility = meter._opencode_compatibility()
        child = self._source("child", legacy=True)
        row = self.adapter.summarize_legacy(child)

        record = row["_agent_records"][0]
        self.assertEqual(record["kind"], "spawned")
        self.assertEqual(record["label"], "Child")
        self.assertEqual(record["role"], "build")

    def test_child_agent_label_is_empty_when_title_is_unnamed(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE session SET title='New session - 2026' WHERE id='child'")
        self.adapter.compatibility = meter._opencode_compatibility()
        child = self._source("child", legacy=True)
        row = self.adapter.summarize_legacy(child)

        self.assertEqual(row["_agent_records"][0]["label"], "")

    def test_archived_ancestor_excludes_descendants_from_discovery(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE session SET time_archived=9000 WHERE id='session-1'")
        rows = self.adapter.discover_legacy(DiscoveryContext(home=str(self.root)))

        self.assertEqual(rows, ())


    def test_v1_database_selects_the_legacy_read_path(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            self.assertEqual(database_schema(conn), "v1")

        sources = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))
        self.assertEqual(len(sources), 2)
        # The V1 read path still touches the legacy part table for revision
        # and tool evidence; both stay loadable end to end.
        source = self._source("session-1")
        result = self.adapter.load(source, DetailLevel.FULL)
        self.assertEqual(result.usage.cost_usd.value, 0.25)

    def test_v1_message_model_resolution_stays_flat_only(self):
        # Real V1 databases contain messages with a nested model object; the
        # V1 read path must keep the legacy flat-only resolution so provider
        # attribution cannot drift for pre-migration rows. Only the V2 read
        # path reads the nested object.
        from token_meter.runtimes.opencode import message_model_ref

        data = {
            "modelID": "flat-model", "providerID": "flat-provider",
            "model": {"id": "nested-model", "providerID": "nested-provider"},
        }
        self.assertEqual(
            message_model_ref(data, "fallback", "session-provider"),
            ("flat-model", "flat-provider"),
        )
        self.assertEqual(
            message_model_ref(data, "fallback", "session-provider",
                              nested_model=True),
            ("nested-model", "nested-provider"),
        )
        self.assertEqual(
            message_model_ref({"modelID": "flat-model"}, "fallback", "sp"),
            ("flat-model", "sp"),
        )


class OpenCodeV2AdapterTests(unittest.TestCase):
    """Read-path coverage for OpenCode's V2 `session_v2` storage."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "opencode.db"
        self.models_path = self.root / "models.json"
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(V2_SCHEMA)
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_root", "proj", None, "root", "/work/project", "Root work",
                 "2.0.0", 0.25, 10, 3, 2, 4, 1, "build",
                 json.dumps({"id": "model-x", "providerID": "prov",
                             "variant": "high"}),
                 1_000, 9_000),
            )
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_child", "proj", "ses_root", "child", "/work/project",
                 "Child", "2.0.0", 0.0, 0, 0, 0, 0, 0, "build", "{}",
                 2_000, 9_500),
            )
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_quiet", "proj", None, "quiet", "/work/quiet", None,
                 "2.0.0", 0.5, 5, 0, 0, 0, 0, "build", "{}", 3_000, 3_500),
            )
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_unnamed", "proj", None, "unnamed", "/work/other",
                 "New session - 2026", "2.0.0", 0.0, 0, 0, 0, 0, 0, "build",
                 "{}", 4_000, 4_500),
            )
            # An archived root excludes its whole family from discovery.
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated, time_archived) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_arch", "proj", None, "arch", "/work/gone", "Archived",
                 "2.0.0", 0.0, 0, 0, 0, 0, 0, "build", "{}", 1_000, 2_000, 8_000),
            )
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("ses_arch_child", "proj", "ses_arch", "archchild",
                 "/work/gone", "Archived child", "2.0.0", 0.0, 0, 0, 0, 0, 0,
                 "build", "{}", 1_500, 2_500),
            )
            # V2 orders messages by the per-session `seq` counter; the
            # time_created column values here are deliberately non-monotonic so
            # ordering by time would produce the wrong history.
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m1", "ses_root", "user", 1, 1_000, 1_000, json.dumps({
                    "text": "private prompt", "time": {"created": 1_000},
                })),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m2", "ses_root", "assistant", 2, 2_500, 4_000, json.dumps({
                    "time": {"created": 2_000, "streamed": 2_100,
                             "completed": 4_000},
                    "model": {"id": "model-x", "providerID": "prov",
                              "variant": "high"},
                    "cost": 0.25, "finish": "stop",
                    "tokens": {"input": 10, "output": 3, "reasoning": 2,
                               "cache": {"read": 4, "write": 1}},
                    "content": [
                        {"type": "reasoning", "text": "private reasoning",
                         "state": {},
                         "time": {"created": 2_100, "completed": 2_500}},
                        {"type": "tool", "id": "call_1", "name": "read",
                         "executed": True,
                         "time": {"created": 2_500, "ran": 2_500,
                                  "completed": 3_000},
                         "state": {"status": "completed",
                                   "input": {"path": "secret-path"},
                                   "content": [{"type": "text",
                                                "text": "secret output"}],
                                   "metadata": {}}},
                        {"type": "text", "text": "private response"},
                    ],
                })),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m3", "ses_root", "assistant", 3, 2_000, 5_800, json.dumps({
                    "time": {"created": 5_000, "completed": 5_800},
                    "model": {"id": "model-y", "providerID": "prov"},
                    "cost": 0.1, "finish": "tool-calls",
                    "tokens": {"input": 6, "output": 7, "reasoning": 0,
                               "cache": {"read": 0, "write": 0}},
                    "content": [
                        {"type": "tool", "id": "call_2", "name": "bash",
                         "executed": False,
                         "time": {"created": 5_100, "ran": 5_100,
                                  "completed": 5_400},
                         "state": {"status": "error", "input": {},
                                   "content": [], "error": "boom"}},
                    ],
                })),
            )
            # Unknown V2 message types must be ignored, not fail the session.
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m4", "ses_root", "idle", 4, 6_000, 6_000, json.dumps({
                    "time": {"created": 6_000}, "outcome": "interrupted",
                })),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m5", "ses_root", "weird-new-type", 5, 6_500, 6_500, json.dumps({
                    "time": {"created": 6_500},
                })),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m6", "ses_root", "assistant", 6, 6_800, 6_400, json.dumps({
                    "time": {"created": 6_000, "completed": 6_400},
                    "model": {"id": "model-x", "providerID": "prov"},
                    "cost": 0.05, "finish": "stop",
                    "tokens": {"input": 2, "output": 1, "reasoning": 0,
                               "cache": {"read": 0, "write": 0}},
                    "content": [],
                })),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("mc1", "ses_child", "assistant", 1, 2_100, 9_500, json.dumps({
                    "time": {"created": 9_000, "completed": 9_500},
                    "model": {"id": "model-x", "providerID": "prov"},
                    "cost": 0.0, "finish": "stop",
                    "tokens": {"input": 1, "output": 1, "reasoning": 0,
                               "cache": {"read": 0, "write": 0}},
                    "content": [],
                })),
            )
        self.models_path.write_text(json.dumps({
            "models": {"id": "prov", "models": {
                "model-x": {"limit": {"context": 1000}},
                "model-y": {"limit": {"context": 2000}},
            }},
        }))
        self.adapter = OpenCodeRuntimeAdapter(self.db_path, self.models_path)

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, session_id, *, legacy=False):
        discover = self.adapter.discover_legacy if legacy else self.adapter.discover
        context = DiscoveryContext(home=str(self.root))
        rows = discover(context)
        for row in rows:
            identifier = row.get("id") if legacy else row.session_id
            if identifier == session_id:
                return row
        raise AssertionError(
            "session {!r} was not discovered; got {!r}".format(
                session_id,
                [row.get("id") if legacy else row.session_id for row in rows],
            )
        )

    def test_v2_database_is_detected_and_partial_migration_stays_v1(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            self.assertEqual(database_schema(conn), "v2")

        partial = self.root / "partial.db"
        with contextlib.closing(sqlite3.connect(partial)) as conn, conn:
            conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY)")
        with contextlib.closing(sqlite3.connect(partial)) as conn:
            # A lone session_v2 table without the message store is not a V2
            # database; the V1 path keeps the evidence bounded instead.
            self.assertEqual(database_schema(conn), "v1")
        partial_adapter = OpenCodeRuntimeAdapter(partial, self.models_path)
        self.assertEqual(
            tuple(partial_adapter.discover(DiscoveryContext(home=str(self.root)))),
            (),
        )

    def test_v2_discovery_reads_sessions_with_lineage_and_model_refs(self):
        sources = tuple(self.adapter.discover(DiscoveryContext(home=str(self.root))))

        self.assertEqual(
            sorted(source.session_id for source in sources),
            ["ses_child", "ses_quiet", "ses_root", "ses_unnamed"],
        )
        source = self._source("ses_root")
        self.assertEqual(source.runtime_id, "opencode")
        self.assertEqual(source.model_ref.model_id, "model-x")
        self.assertEqual(source.model_ref.provider_id, "prov")
        self.assertEqual(source.project, "/work/project")

        child = self._source("ses_child")
        self.assertEqual(child.project, "/work/project")

        child_legacy = self._source("ses_child", legacy=True)
        self.assertEqual(child_legacy["agent_parent_id"], "ses_root")
        self.assertEqual(child_legacy["agent_root_id"], "ses_root")
        self.assertEqual(child_legacy["agent_depth"], 1)
        root_legacy = self._source("ses_root", legacy=True)
        self.assertEqual(root_legacy["agent_depth"], 0)
        self.assertTrue(root_legacy["agent_has_children"])
        self.assertFalse(child_legacy["agent_has_children"])

    def test_v2_revision_follows_message_updates(self):
        source = self._source("ses_root")
        before = self.adapter.current_revision(source)
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE session_message SET time_updated=12000 WHERE id='m2'"
            )
        after = self.adapter.current_revision(source)

        self.assertNotEqual(before, after)

    def test_v2_load_measures_tokens_cost_and_timing(self):
        source = self._source("ses_root")
        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 5)
        self.assertEqual(result.usage.cache_read_tokens.value, 4)
        self.assertEqual(result.usage.cache_write_tokens.value, 1)
        self.assertEqual(result.usage.cost_usd.value, 0.25)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.MEASURED)
        self.assertEqual(
            sorted((tool.name, tool.category) for tool in result.tools),
            [("bash", "tool"), ("read", "tool")],
        )
        serialized = repr(result)
        for private in ("private prompt", "private response", "private reasoning",
                        "secret output", "secret-path"):
            self.assertNotIn(private, serialized)

    def test_v2_messages_are_ordered_by_seq_not_time_columns(self):
        source = self._source("ses_root")
        result = self.adapter.load(source, DetailLevel.FULL)

        # seq 2 (time_created column 2500) precedes seq 3 (column 2000); the
        # per-session seq counter is the authoritative V2 order.
        self.assertEqual(
            [turn.output_tokens.value for turn in result.turns],
            [5, 7, 1],
        )
        # Active timing comes from the assistant data time bounds, not from the
        # message row columns: (2.0s..4.0s) plus (5.0s..5.8s) plus (6.0s..6.4s).
        self.assertEqual(result.timing.active_seconds.value, 3.2)
        self.assertEqual(result.timing.active_seconds.basis, EvidenceBasis.MEASURED)

    def test_v2_summary_detail_omits_turns(self):
        source = self._source("ses_root")

        result = self.adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(result.turns, ())
        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.cost_usd.basis, EvidenceBasis.MEASURED)

    def test_v2_nullable_and_unnamed_titles(self):
        self.assertIsNone(self._source("ses_quiet", legacy=True)["title"])
        self.assertIsNone(self._source("ses_unnamed", legacy=True)["title"])

    def test_v2_archived_family_is_excluded_from_discovery(self):
        rows = self.adapter.discover_legacy(DiscoveryContext(home=str(self.root)))

        self.assertNotIn(
            "ses_arch", {row["id"] for row in rows},
        )
        self.assertNotIn(
            "ses_arch_child", {row["id"] for row in rows},
        )

    def test_v2_session_without_assistant_messages(self):
        source = self._source("ses_quiet")

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.turns, ())
        self.assertEqual(result.tools, ())
        self.assertEqual(result.usage.input_tokens.value, 5)
        self.assertEqual(result.usage.cost_usd.value, 0.5)
        self.assertEqual(result.timing.active_seconds.basis, EvidenceBasis.UNAVAILABLE)

    def test_v2_recompute_extracts_tools_reasoning_and_models(self):
        self.adapter.compatibility = meter._opencode_compatibility()
        source = self._source("ses_root", legacy=True)
        state = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(len(state["executions"]), 3)
        first, second, third = state["executions"]
        self.assertEqual(first["model"], "model-x")
        self.assertEqual(second["model"], "model-y")
        self.assertEqual(first["tool_count"], 1)
        tool = first["tools"][0]
        self.assertEqual(tool["name"], "read")
        self.assertEqual(tool["id"], "call_1")
        self.assertTrue(tool["result_available"])
        self.assertFalse(tool["error"])
        self.assertGreater(tool["output_tokens"], 0)
        second_tool = second["tools"][0]
        self.assertEqual(second_tool["name"], "bash")
        self.assertTrue(second_tool["error"])
        # Session totals stay authoritative over the bounded message window.
        self.assertEqual(state["total_cost"], 0.25)
        reasoning_events = [
            event for event in state["trace"]
            if event.get("kind") == "reasoning"
        ]
        self.assertEqual(len(reasoning_events), 1)
        self.assertEqual(reasoning_events[0]["duration_ms"], 400)
        self.assertEqual(state["primary_model"], "model-x")
        # Inline V2 content must never leak: only counts and fingerprints appear.
        # (The bounded user-prompt preview is the same deliberate V1 behavior.)
        encoded = repr(state)
        for private in ("private response", "private reasoning",
                        "secret output", "secret-path"):
            self.assertNotIn(private, encoded)

    def test_v2_child_agent_records_and_summary_model_attribution(self):
        self.adapter.compatibility = meter._opencode_compatibility()
        root = self._source("ses_root", legacy=True)
        row = self.adapter.summarize_legacy(root)

        self.assertEqual(row["turns"], 3)
        self.assertEqual(row["cost"], 0.25)
        self.assertEqual(sorted(row["models"]), ["model-x", "model-y"])
        self.assertEqual(row["primary_model"], "model-x")
        by_model = {
            stats["model"]: stats for stats in row["model_stats"]
        }
        self.assertEqual(by_model["model-x"]["input_tokens"], 17)
        self.assertEqual(by_model["model-x"]["output_tokens"], 6)
        self.assertEqual(by_model["model-y"]["input_tokens"], 6)
        self.assertEqual(by_model["model-y"]["output_tokens"], 7)

        child = self._source("ses_child", legacy=True)
        child_row = self.adapter.summarize_legacy(child)
        record = child_row["_agent_records"][0]
        self.assertEqual(record["kind"], "spawned")
        self.assertEqual(record["label"], "Child")
        self.assertEqual(record["role"], "build")
        self.assertEqual(record["model"], "model-x")

    def test_v2_mcp_trace_views_are_structural_and_content_free(self):
        self.adapter.compatibility = meter._opencode_compatibility()
        source = self._source("ses_root", legacy=True)
        state = self.adapter.load(source, DetailLevel.FULL)

        assert_runtime_trace_privacy(
            self, source, state, runtime="opencode", model="model-x",
            tool="read", native_types=("message", "part"),
            forbidden=(
                "private prompt", "private response", "private reasoning",
                "secret output", "secret-path", "/work/project",
            ),
        )

    def test_v2_is_preferred_when_v1_tables_also_exist(self):
        both = self.root / "both.db"
        with contextlib.closing(sqlite3.connect(both)) as conn, conn:
            conn.executescript(SCHEMA + V2_SCHEMA)
            # One session only in the legacy tables, one only in V2.
            conn.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy-only", None, "/work/legacy", "Legacy", "build",
                 json.dumps({"providerID": "prov", "id": "model-x"}),
                 1_000, 4_000, None, 10, 3, 2, 4, 1, 0.25),
            )
            conn.execute(
                "INSERT INTO session_v2 (id, project_id, parent_id, slug, "
                "directory, title, version, cost, tokens_input, tokens_output, "
                "tokens_reasoning, tokens_cache_read, tokens_cache_write, agent, "
                "model, time_created, time_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("v2-only", "proj", None, "v2", "/work/v2", "V2", "2.0.0",
                 0.25, 10, 3, 2, 4, 1, "build",
                 json.dumps({"id": "model-x", "providerID": "prov"}),
                 1_000, 4_000),
            )
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("vm1", "v2-only", "assistant", 1, 2_000, 4_000, json.dumps({
                    "time": {"created": 2_000, "completed": 4_000},
                    "model": {"id": "model-x", "providerID": "prov"},
                    "cost": 0.25, "finish": "stop",
                    "tokens": {"input": 10, "output": 3, "reasoning": 2,
                               "cache": {"read": 4, "write": 1}},
                    "content": [],
                })),
            )
        adapter = OpenCodeRuntimeAdapter(both, self.models_path)
        rows = adapter.discover_legacy(DiscoveryContext(home=str(self.root)))

        # The V2 store is authoritative when both exist: no legacy-only rows,
        # no double counting.
        self.assertEqual({row["id"] for row in rows}, {"v2-only"})
        result = adapter.load(adapter.discover(
            DiscoveryContext(home=str(self.root)))[0], DetailLevel.FULL)
        self.assertEqual(result.usage.cost_usd.value, 0.25)
        self.assertEqual(result.usage.output_tokens.value, 5)

    def test_v2_connection_is_query_only(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM session_v2")


if __name__ == "__main__":
    unittest.main()
