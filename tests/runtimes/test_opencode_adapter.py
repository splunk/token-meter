import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.opencode import OpenCodeRuntimeAdapter
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


if __name__ == "__main__":
    unittest.main()
