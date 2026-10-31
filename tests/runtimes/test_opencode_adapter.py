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
    WORK_REQUEST_TEXT_CAP,
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
# messages replace the V1 message table with one session_message table whose
# assistant rows carry tool and reasoning evidence inline in data.content[] and
# whose user rows carry the typed request inline in data.text. V2 has no part
# store; `part` belongs to V1 and is only added here to prove the V2 read path
# does not depend on it.
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

# The V1 part table, present in a migrated database alongside the V2 store.
# Most of its text parts are synthetic tool echoes and injected file blobs.
V2_PART_SCHEMA = """
CREATE TABLE IF NOT EXISTS part (
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
        compat = meter._opencode_compatibility()
        # Work insights is a user setting; force it on so the row never depends
        # on this machine's settings file.
        compat["work_insights_enabled"] = lambda: True
        self.adapter.compatibility = compat
        child = self._source("child", legacy=True)
        row = self.adapter.summarize_legacy(child)

        record = row["_agent_records"][0]
        self.assertEqual(record["kind"], "spawned")
        self.assertEqual(record["label"], "Child")
        self.assertEqual(record["role"], "build")

    def test_child_agent_label_is_empty_when_title_is_unnamed(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE session SET title='New session - 2026' WHERE id='child'")
        compat = meter._opencode_compatibility()
        # Work insights is a user setting; force it on so the row never depends
        # on this machine's settings file.
        compat["work_insights_enabled"] = lambda: True
        self.adapter.compatibility = compat
        child = self._source("child", legacy=True)
        row = self.adapter.summarize_legacy(child)

        self.assertEqual(row["_agent_records"][0]["label"], "")

    def test_work_turns_cover_every_typed_request(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            for index in range(7):
                created = 10_000 + index * 1_000
                conn.execute(
                    "INSERT INTO message VALUES (?,?,?,?,?)",
                    (f"user-extra-{index}", "session-1", json.dumps({
                        "role": "user", "content": f"request {index}",
                        "time": {"created": created},
                    }), created, created),
                )
        captured = []
        compat = meter._opencode_compatibility()
        # Priced events are Work-only extraction, so the adapter must be told the
        # feature is on; otherwise the assertion reads this machine's settings
        # file instead of the adapter's behavior.
        compat["work_insights_enabled"] = lambda: True

        def attach(row, turns, events=(), actions=()):
            captured.extend(turns)
            row["_work_turn_days"] = ["day"] * len(turns)
            row["_work_events"] = len(events)
            return row
        compat["attach_work_requests"] = attach
        self.adapter.compatibility = compat
        row = self.adapter.summarize_legacy(self._source("session-1", legacy=True))

        self.assertEqual(len(captured), 8)
        self.assertEqual(row["_work_turn_days"], ["day"] * 8)
        self.assertEqual(row["_work_events"], 1)  # each assistant message is a priced event for the cost split
        self.assertIn("_cache_window", row)

    def _captured_work_turns(self, session_id="session-1"):
        """Summarize one V1 session with the typed-request queue captured.

        Work insights is a user setting, so force it on: the capture must
        describe the adapter, not this machine's settings file.
        """
        captured = []
        compat = meter._opencode_compatibility()
        compat["work_insights_enabled"] = lambda: True

        def attach(row, turns, events=(), actions=()):
            captured.extend(turns)
            return row
        compat["attach_work_requests"] = attach
        self.adapter.compatibility = compat
        self.adapter.summarize_legacy(self._source(session_id, legacy=True))
        return captured

    def test_v1_string_content_wins_over_sibling_text_and_part_text(self):
        # Pins the precedence, not the fallback: a usable string `content` is the
        # request, and neither a same-message `text` field nor a V1 `part` row
        # may displace it. The part row is a deliberate guard. `part` is where the
        # detail path does read user text from, so a future change that "fixed"
        # summarize_legacy to prefer it has to fail here.
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE message SET data=? WHERE id='user-1'",
                (json.dumps({
                    "role": "user", "content": "private prompt",
                    "text": "stale text field",
                    "time": {"created": 1_000},
                }),),
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("part-user", "session-1", "user-1",
                 json.dumps({"type": "text", "text": "part text is not the request"}),
                 1_000, 1_000),
            )

        captured = self._captured_work_turns()

        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])

    def test_v1_work_turn_falls_back_to_text_when_content_is_unusable(self):
        # V1 behaviour changed with the V2 fix: a user message whose `content` is
        # missing or is not a usable string no longer yields no request at all.
        # It resolves through the message's own `text`, exactly as V2 does.
        unusable = {
            "missing": {},
            "structured_list": {"content": [{"type": "text", "text": "part"}]},
            "blank_string": {"content": "   \n\t "},
            "non_string": {"content": 42},
            "null": {"content": None},
        }
        for label, extra in unusable.items():
            with self.subTest(content=label):
                payload = {"role": "user", "text": "typed request",
                           "time": {"created": 1_000}}
                payload.update(extra)
                with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
                    conn.execute(
                        "UPDATE message SET data=? WHERE id='user-1'",
                        (json.dumps(payload),),
                    )

                captured = self._captured_work_turns()

                self.assertEqual([turn["text"] for turn in captured],
                                 ["typed request"])
                self.assertEqual([turn["ts"] for turn in captured], [1.0])
                self.assertEqual([turn["model"] for turn in captured], ["model-1"])

    def test_v1_work_turn_is_absent_without_any_request_text(self):
        # A user message carrying neither usable `content` nor `text` still
        # yields no request, so the fallback cannot invent one.
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "UPDATE message SET data=? WHERE id='user-1'",
                (json.dumps({"role": "user", "content": {"type": "text"},
                             "time": {"created": 1_000}}),),
            )

        self.assertEqual(self._captured_work_turns(), [])

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

    def _captured_turns(self, session_id="ses_root"):
        """Summarize one session with the typed-request queue captured.

        Work insights are forced on so the capture does not depend on the
        machine's own settings file.
        """
        captured = []
        compat = meter._opencode_compatibility()
        compat["work_insights_enabled"] = lambda: True

        def attach(row, turns, events=(), actions=()):
            captured.extend(turns)
            row["_work_turn_days"] = ["day"] * len(turns)
            return row
        compat["attach_work_requests"] = attach
        self.adapter.compatibility = compat
        row = self.adapter.summarize_legacy(self._source(session_id, legacy=True))
        return captured, row

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
        compat = meter._opencode_compatibility()
        # Work insights is a user setting; force it on so the row never depends
        # on this machine's settings file.
        compat["work_insights_enabled"] = lambda: True
        self.adapter.compatibility = compat
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

    def test_v2_work_turns_read_the_user_prompt_from_the_message_row(self):
        # V2 has no `content` on a user message; the request the user typed is
        # the message row's own `text`, and it is compacted like any other
        # typed request.
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m7", "ses_root", "user", 7, 7_000, 7_000, json.dumps({
                    "text": "word " * 60,
                    "time": {"created": 7_000},
                })),
            )

        captured, row = self._captured_turns()

        self.assertEqual(len(captured), 2)
        first, second = captured
        self.assertEqual(first["text"], "private prompt")
        self.assertEqual(first["ts"], 1.0)
        self.assertEqual(first["model"], "model-x")
        # Long requests stay compacted to the bounded request preview.
        self.assertEqual(second["ts"], 7.0)
        self.assertEqual(len(second["text"]), 90)
        self.assertTrue(second["text"].endswith("…"))
        self.assertEqual(row["_work_turn_days"], ["day", "day"])
        self.assertEqual(row["turns"], 3)

    def test_work_turn_caps_a_very_long_prompt_before_compacting(self):
        # The cap sits on the schema-independent line that queues a request, so
        # pinning it here covers V1 and V2 alike. Truncation is invisible in the
        # stored preview, because whitespace collapsing can only shorten a
        # leading slice. The cap therefore has to be observed on what reaches
        # compaction, or the test cannot tell a capped prompt from an uncapped
        # one.
        head = "alpha "
        tail = "TRAILING-MARKER"
        oversized = head * (WORK_REQUEST_TEXT_CAP // len(head)) + tail
        self.assertGreater(len(oversized), WORK_REQUEST_TEXT_CAP)
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("m8", "ses_root", "user", 8, 8_000, 8_000, json.dumps({
                    "text": oversized,
                    "time": {"created": 8_000},
                })),
            )
        compacted_lengths = []
        captured = []
        compat = meter._opencode_compatibility()
        compat["work_insights_enabled"] = lambda: True

        def spy_compact_text(value, limit=72):
            compacted_lengths.append(len(value or ""))
            return meter.compact_text(value, limit)

        def attach(row, turns, events=(), actions=()):
            captured.extend(turns)
            return row
        compat["compact_text"] = spy_compact_text
        compat["attach_work_requests"] = attach
        self.adapter.compatibility = compat
        self.adapter.summarize_legacy(self._source("ses_root", legacy=True))

        # The oversized prompt reached compaction already cut to the cap, and
        # nothing queued was handed an uncapped prompt.
        self.assertEqual(max(compacted_lengths), WORK_REQUEST_TEXT_CAP)
        self.assertTrue(
            all(length <= WORK_REQUEST_TEXT_CAP for length in compacted_lengths),
            compacted_lengths,
        )
        preview = captured[-1]["text"]
        self.assertEqual(len(preview), 90)
        self.assertTrue(preview.startswith("alpha alpha "))
        self.assertNotIn(tail, preview)

    def test_v2_work_turns_ignore_the_v1_part_table(self):
        # `part` is V1's store. Its rows can share ids with V2 messages by
        # accident, and most of its text parts are synthetic tool echoes and
        # injected file blobs, so a V2 read must never reach into it.
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(V2_PART_SCHEMA)
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("p1", "ses_root", "m1",
                 json.dumps({"type": "text", "synthetic": True,
                             "text": "Called the Read tool with the following "
                                     "input: {'path': 'secret-path'}"}),
                 1_000, 1_000),
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                ("p2", "ses_root", "m1",
                 json.dumps({"type": "text", "text": "stale V1 part text"}),
                 1_100, 1_100),
            )

        captured, _ = self._captured_turns()

        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])

    def _add_v2_user_text(self, seq, created, text):
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                (f"strip-{seq}", "ses_root", "user", seq, created, created,
                 json.dumps({"text": text, "time": {"created": created}})),
            )

    def test_leading_attachment_notice_is_stripped_before_the_preview(self):
        # The migration led with its notice; the classifier must still see the
        # request behind it, not the notice.
        self._add_v2_user_text(
            8, 8_000,
            "[Attachment unavailable after migration: docs/plan.md (text/plain)]\n\n"
            "add a regression test for the spend page",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "add a regression test for the spend page"],
        )

    def test_trailing_attachment_notice_is_stripped(self):
        # The migration appended the notice, sometimes inline rather than on its
        # own line. Both shapes must leave the request alone.
        for seq, text in (
            (9, "run the unit suite\n\n"
                "[Attachment unavailable after migration: README.md (text/plain)]"),
            (10, "run the unit suite "
                "[Attachment unavailable after migration: README.md (text/plain)]"),
        ):
            self._add_v2_user_text(seq, seq * 1_000, text)

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "run the unit suite", "run the unit suite"],
        )

    def test_attachment_notice_covering_the_whole_preview_yields_the_users_words(self):
        # The case that motivated the work: the injection was long enough to be
        # all the classifier would ever have seen, so the preview has to become
        # the user's text rather than the notice.
        notices = "".join(
            "[Attachment unavailable after migration: "
            "docs/generated/module-number-{}.md (text/plain)]\n".format(index)
            for index in range(6)
        )
        self._add_v2_user_text(11, 11_000, notices + "now bump the version constant")

        captured, _ = self._captured_turns()

        self.assertGreater(len(notices), 90)
        self.assertEqual(captured[-1]["text"], "now bump the version constant")

    def test_request_that_is_only_harness_scaffolding_emits_no_turn(self):
        # Scaffolding alone is not a request. Emitting an empty turn would hand
        # the classifier a blank and spend a turn's budget on nothing.
        self._add_v2_user_text(
            12, 12_000,
            "[Attachment unavailable after migration: a.md (text/plain)]\n"
            "[Attachment unavailable after migration: b.md (text/plain)]",
        )

        captured, row = self._captured_turns()

        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])
        self.assertEqual(row["_work_turn_days"], ["day"])

    def test_user_authored_markers_are_not_stripped(self):
        # Over-stripping is a data-loss bug: these are all things a user can
        # plausibly have typed or pasted, and their disappearance would be worse
        # than the injection this fix removes.
        typed = (
            "why does my prompt show [Attachment unavailable after migration: ...]"
            " after migration?",
            "what does <system-reminder> mean in an OpenCode session?",
            "append --target <path> and read <content> from the file",
            "<objective>\nplan the work\n\n<execution_context>\nmake build test lint pass\n",
        )
        for offset, text in enumerate(typed):
            self._add_v2_user_text(13 + offset, (13 + offset) * 1_000, text)

        captured, _ = self._captured_turns()

        # Previews compact whitespace, so compare against the compacted form.
        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt"] + [" ".join(text.split()) for text in typed],
        )

    def test_injection_larger_than_the_preview_cannot_displace_the_request(self):
        # The preview is 90 characters. A long run of injected notices ahead of a
        # short real request must not be what fills that budget, which only
        # holds if stripping runs before compaction.
        notices = "".join(
            "[Attachment unavailable after migration: "
            "vendor/bundle/large-module-{}.ts (text/plain)]\n".format(index)
            for index in range(40)
        )
        self._add_v2_user_text(20, 20_000, notices + "rename the flag to --dry-run")

        captured, _ = self._captured_turns()

        self.assertGreater(len(notices), 90)
        self.assertEqual(captured[-1]["text"], "rename the flag to --dry-run")

    def test_user_text_between_two_same_line_notices_survives(self):
        # Two notices can land on the same line. If each match is allowed to run
        # past the next notice's "[", the words the user typed between them are
        # deleted along with the injection, which is data loss worse than the
        # injection this strips. Each notice must be removed on its own.
        self._add_v2_user_text(
            21, 21_000,
            "fix A [Attachment unavailable after migration: a.md (text/plain)] "
            "then discuss [Attachment unavailable after migration: b.md "
            "(text/plain)] end",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "fix A then discuss end"],
        )

    def test_user_text_between_three_same_line_notices_survives(self):
        # The same hazard as the two-notice case, repeated: everything between
        # the first and last suffix on the line has to survive, not just the
        # first span.
        self._add_v2_user_text(
            22, 22_000,
            "alpha [Attachment unavailable after migration: a.md (text/plain)] "
            "beta [Attachment unavailable after migration: b.md (text/plain)] "
            "gamma [Attachment unavailable after migration: c.md (text/plain)] "
            "delta",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "alpha beta gamma delta"],
        )

    def test_notices_on_separate_lines_are_each_stripped(self):
        # The notice is as often on its own line as inline. A match must stay on
        # one line, so notices on separate lines are removed independently and
        # the request that follows is left intact.
        self._add_v2_user_text(
            23, 23_000,
            "run the unit suite\n"
            "[Attachment unavailable after migration: a.md (text/plain)]\n"
            "[Attachment unavailable after migration: b.md (text/plain)]",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "run the unit suite"],
        )

    def test_payload_path_containing_a_bracket_is_stripped_to_the_media_type(self):
        # A payload path can itself contain "[". Bounding the removal at the
        # first "]" would leave the tail of the notice in the request, so the
        # match has to run to the machine-emitted media-type suffix instead.
        self._add_v2_user_text(
            24, 24_000,
            "see [Attachment unavailable after migration: app/for/[slug] "
            "(text/plain)] here",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt", "see here"],
        )

    def test_only_same_line_scaffolding_still_emits_no_turn(self):
        # Scaffolding alone is not a request even when the notices sit on one
        # line. Emitting an empty turn would spend a turn's budget on nothing.
        self._add_v2_user_text(
            25, 25_000,
            "[Attachment unavailable after migration: a.md (text/plain)] "
            "[Attachment unavailable after migration: b.md (text/plain)]",
        )

        captured, row = self._captured_turns()

        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])
        self.assertEqual(row["_work_turn_days"], ["day"])

    def test_quoted_notice_phrase_without_a_media_type_is_not_stripped(self):
        # A user can quote the phrase without the machine-emitted media-type
        # suffix that a real notice always carries, so the quoted text is the
        # user's own and must not be removed.
        self._add_v2_user_text(
            26, 26_000,
            "why does my prompt show [Attachment unavailable after migration: "
            "...] after migration?",
        )

        captured, _ = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt",
             "why does my prompt show [Attachment unavailable after migration: "
             "...] after migration?"],
        )

    def test_work_extraction_is_gated_on_the_feature_flag(self):
        # Off means off. Mocking the strip step only proves the block was not
        # reached; the request is read out of the row before that. Wrapping the
        # decoded row instead makes the first field access fail, so a build that
        # reads `content` or `text` and then discards it cannot pass either.
        created = 7_000
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                ("gated-1", "ses_root", "user", 7, created, created,
                 json.dumps({"text": "fix the chart",
                             "time": {"created": created}})),
            )
        captured = []
        compat = meter._opencode_compatibility()
        compat["work_insights_enabled"] = lambda: False

        def attach(row, turns, events=(), actions=()):
            captured.extend(turns)
            return row
        compat["attach_work_requests"] = attach
        self.adapter.compatibility = compat
        from token_meter.runtimes import opencode as opencode_module

        class _NoRequestRead(dict):
            """A decoded row that refuses to hand over typed request text."""

            def get(self, key, default=None):
                if key in ("content", "text"):
                    raise AssertionError(
                        "request text was read while Work insights was off"
                    )
                return super().get(key, default)

        real_decode = opencode_module.decode_json

        def guarded_decode(value, default=None):
            decoded = real_decode(value, default)
            return _NoRequestRead(decoded) if isinstance(decoded, dict) else decoded

        with mock.patch.object(opencode_module, "decode_json",
                               side_effect=guarded_decode):
            row = self.adapter.summarize_legacy(
                self._source("ses_root", legacy=True)
            )

        self.assertEqual(captured, [])
        # Gating the prompt costs no other evidence: the summary still completes.
        self.assertEqual(row["turns"], 3)

    def test_clean_requests_are_untouched_by_the_harness_strip(self):
        # No injection means no change: a short request passes through exactly,
        # and a long one still compacts to the bounded 90-character preview.
        self._add_v2_user_text(21, 21_000, "ship the release")
        self._add_v2_user_text(22, 22_000, "word " * 60)

        captured, _ = self._captured_turns()

        self.assertEqual(captured[-2]["text"], "ship the release")
        self.assertEqual(len(captured[-1]["text"]), 90)
        self.assertTrue(captured[-1]["text"].endswith("…"))

    def _assert_request_is_dropped(self, seq, created, text, *, markers=()):
        """Queue one typed request and prove no part of it came back.

        The baseline session already carries one clean request ("private
        prompt"), so a dropped request leaves exactly that one turn, and none
        of the request's own markers may appear in the serialized summary.
        """
        self._add_v2_user_text(seq, created, text)
        captured, row = self._captured_turns()
        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])
        encoded = json.dumps({"turns": captured, "row": row}, default=str)
        for marker in markers:
            self.assertNotIn(marker, encoded)

    def test_redaction_gate_drops_a_request_carrying_a_credential(self):
        # The same gate Pi applies to every value it surfaces: a credential
        # bound for the Work classifier drops the whole request, and the secret
        # never appears in the serialized summary. The token here is obviously
        # fake key material, never a real value.
        secret = "sk-" + "F" * 32
        self._assert_request_is_dropped(
            40, 40_000, "rotate " + secret + " before you push",
            markers=(secret,),
        )

    def test_redaction_gate_drops_a_host_like_request(self):
        # A request that is only a host and port is host evidence, so it is
        # dropped whole rather than handed to the classifier.
        self._assert_request_is_dropped(41, 41_000, "10.0.0.5:11434")

    def test_redaction_gate_drops_an_account_request(self):
        # A user@host account shape is rejected even mid-sentence, because the
        # classifier must never be asked to read an account address.
        self._assert_request_is_dropped(
            42, 42_000, "email the report to bob@corp.example.com",
            markers=("bob@corp.example.com",),
        )

    def test_redaction_gate_keeps_namespaces_and_version_tags(self):
        # The gate bounds itself to the address token after each "@", so
        # ordinary prose that merely contains one is kept: a package namespace,
        # a mid-sentence Cloudflare-style namespace, and a version tag all reach
        # the queue. Pi runs this same matcher, so both adapters keep them.
        kept = (
            "npm install -g @opengsd/gsd-pi@latest",
            "call the model at @cf/meta/llama-3-8b-instruct",
            "bump pkg@1.2.3 in package.json",
        )
        for offset, text in enumerate(kept):
            self._add_v2_user_text(43 + offset, (43 + offset) * 1_000, text)

        captured, _row = self._captured_turns()

        self.assertEqual(
            [turn["text"] for turn in captured],
            ["private prompt"] + list(kept),
        )

    def test_redaction_gate_is_a_noop_for_clean_requests(self):
        # The gate only ever drops; it never rewrites what is queued. A clean
        # short request passes through exactly and a long one still compacts to
        # the bounded 90-character preview.
        self._add_v2_user_text(46, 46_000, "ship the release")
        self._add_v2_user_text(47, 47_000, "word " * 60)

        captured, _row = self._captured_turns()

        self.assertEqual(captured[-2]["text"], "ship the release")
        self.assertEqual(len(captured[-1]["text"]), 90)
        self.assertTrue(captured[-1]["text"].endswith("…"))

    def test_redaction_gate_runs_before_the_cap_and_the_compaction(self):
        # The gate sees the whole typed request rather than the capped slice: a
        # credential-shaped token past the 8 KiB cap still drops the request.
        # If the cap ran first, the secret would be cut off and the request
        # would be queued.
        head = "alpha "
        oversized = head * (WORK_REQUEST_TEXT_CAP // len(head))
        secret = "sk-" + "G" * 32
        text = oversized + " then rotate " + secret
        self.assertGreater(len(text), WORK_REQUEST_TEXT_CAP)

        self._assert_request_is_dropped(
            48, 48_000, text, markers=(secret,),
        )

    def test_v2_summary_succeeds_without_a_part_table(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("part", tables)

        captured, row = self._captured_turns()

        # Nothing about the summary depends on the V1 part table.
        self.assertEqual([turn["text"] for turn in captured], ["private prompt"])
        self.assertEqual(row["turns"], 3)
        self.assertEqual(row["cost"], 0.25)
        self.assertEqual(sorted(row["models"]), ["model-x", "model-y"])

    def test_v2_connection_is_query_only(self):
        with contextlib.closing(self.adapter.connection()) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM session_v2")


if __name__ == "__main__":
    unittest.main()
