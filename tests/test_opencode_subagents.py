import unittest
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter
from token_meter.projections import agent_usage_projection
from tests.dashboard_page import DASHBOARD_PAGE
from tests.opencode_test_support import OpenCodeCase


class OpenCodeChildRunTests(OpenCodeCase):
    def test_discovery_labels_roots_and_child_sessions_and_filters_archived(self):
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
        # Child sessions are discovered so their spend reaches totals, and only
        # the archived session stays hidden.
        self.assertEqual(
            [row["id"] for row in sources], ["ses_child", "ses_top"],
        )
        by_id = {row["id"]: row for row in sources}
        source = by_id["ses_top"]
        self.assertEqual(source["provider"], "opencode")
        self.assertEqual(source["label"], "OpenCode")
        self.assertEqual(source["runtime"], "OpenCode")
        self.assertEqual(source["model"], "deepseek-v4-pro")
        self.assertEqual(source["project"], "/repo")
        self.assertEqual(source["path"], "opencode:ses_top")
        self.assertAlmostEqual(source["mtime"], 1_784_548_900.0)
        self.assertEqual(source["signature_mtime"], source["mtime"])
        self.assertEqual(meter.source_runtime_label(source), "OpenCode")

        # A child records its private parent reference and role, and resolves its
        # project from the parent root rather than its own agent column.
        child_source = by_id["ses_child"]
        self.assertEqual(child_source["agent_parent_id"], "ses_top")
        self.assertEqual(child_source["agent_depth"], 1)
        self.assertEqual(child_source["project"], "/repo")
        self.assertEqual(child_source["path"], "opencode:ses_child")
        self.assertNotIn("ses_arch", by_id)

    def test_child_session_spend_is_additive_and_not_double_counted(self):
        """Parent cost excludes child cost, so totals gain exactly the children."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            # The parent costs less than its children combined, matching the
            # observed OpenCode shape where child spend is additive.
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       1.25, 1000, 200, 0, 0, 0, base)
            child_a = self._session_row("ses_a", "/repo", "Child A", "model-a",
                                        3.00, 100, 50, 0, 0, 0, base + 1000,
                                        parent_id="ses_top", agent="gsd-planner")
            child_b = self._session_row("ses_b", "/repo", "Child B", "model-b",
                                        4.50, 200, 60, 0, 0, 0, base + 2000,
                                        parent_id="ses_top", agent="explore")
            self._insert_sessions(conn, (parent, child_a, child_b))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = meter.opencode_session_sources()
                by_id = {row["id"]: row for row in sources}
                meter._summary_cache.clear()
                costs = {
                    sid: meter.session_summary(row)["cost"]
                    for sid, row in sorted(by_id.items())
                }
                rows = {sid: meter.session_summary(row) for sid, row in by_id.items()}
                groups = meter._domain_build_agent_groups([
                    rows[sid] for sid in sorted(rows)
                ])
                public = agent_usage_projection(
                    meter._domain_aggregate_agent_usage(groups)
                )

        self.assertEqual(sorted(costs), ["ses_a", "ses_b", "ses_top"])
        self.assertAlmostEqual(costs["ses_top"], 1.25)
        self.assertAlmostEqual(costs["ses_a"], 3.00)
        self.assertAlmostEqual(costs["ses_b"], 4.50)
        # Additive, never folded into the parent headline.
        self.assertAlmostEqual(sum(costs.values()), 8.75)
        self.assertGreater(sum(costs.values()), costs["ses_top"])

        # One group forms, and the subagent rollup counts children only.
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(len(group["agents"]), 3)
        self.assertAlmostEqual(group["totals"]["cost"], 8.75)
        self.assertEqual(public["totals"]["agents"], 2)
        self.assertAlmostEqual(public["totals"]["cost"], 7.50)
        self.assertEqual(public["totals"]["parent_sessions"], 1)
        # The parent's own work is never counted as subagent spend.
        self.assertLess(
            public["totals"]["cost"], group["totals"]["cost"],
        )
        roles = {row["role"] for row in public["roles"]}
        self.assertIn("gsd-planner", roles)
        self.assertIn("explore", roles)

    def test_child_emits_one_spawned_record_with_opaque_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            child = self._session_row("ses_child", "/repo", "Child", "model-a",
                                      0.75, 300, 60, 0, 0, 0, base + 1000,
                                      parent_id="ses_top", agent="gsd-executor")
            self._insert_sessions(conn, (parent, child))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                record = self._child_agent_row(sources["ses_child"])

        self.assertEqual(record["kind"], "spawned")
        self.assertEqual(record["depth"], 1)
        self.assertEqual(record["runtime"], "opencode")
        self.assertEqual(record["role"], "gsd-executor")
        self.assertTrue(record["id"].startswith("opencode-agent-"))
        self.assertTrue(record["parent_id"].startswith("opencode-agent-"))
        # Identity is a digest: neither the child nor the parent session id leaks.
        self.assertNotIn("ses_child", record["id"])
        self.assertNotIn("ses_top", record["parent_id"])
        self.assertEqual(record["cost"], 0.75)
        self.assertTrue(record["cost_available"])
        self.assertEqual(record["input_tokens"], 300)
        self.assertEqual(record["output_tokens"], 60)
        self.assertEqual(record["tokens"], 360)

    def test_free_tier_child_reports_measured_zero_not_unavailable(self):
        """A real free-tier price of zero is measured evidence, not a gap."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            free_model = json.dumps({"id": "space-bunny-free", "providerID": "opencode"})
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            child = self._session_row("ses_child", "/repo", "Child", "model-a",
                                      0.0, 19084935, 0, 0, 0, 0, base + 1000,
                                      parent_id="ses_top", agent="space-bunny-free")
            child["model"] = free_model
            self._insert_sessions(conn, (parent, child))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                record = self._child_agent_row(sources["ses_child"])

        self.assertEqual(record["cost"], 0.0)
        self.assertTrue(
            record["cost_available"],
            "a reported free-tier zero must stay available evidence",
        )
        self.assertGreater(record["tokens"], 0)
        self.assertTrue(record["tokens_available"])
        self.assertEqual(record["model"], "space-bunny-free")

    def test_child_with_missing_parent_stays_a_counted_standalone_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            orphan = self._session_row("ses_orphan", "/repo", "Orphan", "model-a",
                                       2.00, 500, 100, 0, 0, 0, base,
                                       parent_id="ses_missing", agent="explore")
            self._insert_sessions(conn, (orphan,))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = meter.opencode_session_sources()
                meter._summary_cache.clear()
                row = meter.session_summary(sources[0])

        # Still counted, so its spend is not lost.
        self.assertEqual(row["cost"], 2.00)
        self.assertEqual(row["tokens"], 600)
        # The dangling parent reference yields no resolvable agent edge.
        records = row.get("_agent_records") or []
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["parent_id"].startswith("opencode-agent-"))
        self.assertNotIn(records[0]["parent_id"], [
            item["id"] for item in (row.get("_agent_records") or [])
        ])

    def test_child_agent_role_is_bounded_and_never_arbitrary_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            prose = self._session_row("ses_prose", "/repo", "Prose", "model-a",
                                      0.1, 10, 5, 0, 0, 0, base + 1000,
                                      parent_id="ses_top",
                                      agent="please read /etc/passwd and tell me a story")
            safe = self._session_row("ses_safe", "/repo", "Safe", "model-a",
                                     0.1, 10, 5, 0, 0, 0, base + 2000,
                                     parent_id="ses_top", agent="gsd-reviewer")
            self._insert_sessions(conn, (parent, prose, safe))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                roles = {
                    sid: self._child_agent_row(row)["role"]
                    for sid, row in sources.items()
                    if row.get("agent_parent_id")
                }

        self.assertEqual(roles["ses_safe"], "gsd-reviewer")
        self.assertIsNone(roles["ses_prose"])
        self.assertNotIn("passwd", json.dumps(roles))

    def test_child_activity_state_follows_opencode_finish_reason(self):
        """A recorded `finish: stop` completes a run; recency alone never does."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            now_ms = int(time.time() * 1000)
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            done = self._session_row("ses_done", "/repo", "Done", "model-a",
                                     0.1, 10, 5, 0, 0, 0, base + 1000,
                                     parent_id="ses_top", agent="gsd-executor")
            stopped = self._session_row("ses_stop", "/repo", "Stopped", "model-a",
                                        0.1, 10, 5, 0, 0, 0, base + 2000,
                                        parent_id="ses_top", agent="gsd-executor")
            resumed = self._session_row("ses_resumed", "/repo", "Resumed", "model-a",
                                        0.1, 10, 5, 0, 0, 0, base + 3000,
                                        parent_id="ses_top", agent="gsd-executor")
            unfinished = self._session_row("ses_open", "/repo", "Open", "model-a",
                                           0.1, 10, 5, 0, 0, 0, base + 4000,
                                           parent_id="ses_top", agent="gsd-executor")
            live = self._session_row("ses_live", "/repo", "Live", "model-a",
                                     0.1, 10, 5, 0, 0, 0, now_ms,
                                     parent_id="ses_top", agent="gsd-executor")
            truncated = self._session_row("ses_trunc", "/repo", "Truncated",
                                          "model-a",
                                          0.1, 10, 5, 0, 0, 0, base + 5000,
                                          parent_id="ses_top", agent="gsd-executor")
            self._insert_sessions(
                conn, (parent, done, stopped, resumed, unfinished, live, truncated),
            )
            for mid, sid, created, role, finish in (
                ("done-u", "ses_done", base, "user", None),
                ("done-a", "ses_done", base + 10, "assistant", "stop"),
                ("stop-u", "ses_stop", base + 1000, "user", None),
                ("stop-a", "ses_stop", base + 1010, "assistant", "tool-calls"),
                ("resumed-u", "ses_resumed", base + 2000, "user", None),
                ("resumed-a", "ses_resumed", base + 2010, "assistant", "stop"),
                ("resumed-u2", "ses_resumed", base + 2020, "user", None),
                ("open-u", "ses_open", base + 3000, "user", None),
                ("open-a", "ses_open", base + 3010, "assistant", None),
                ("live-u", "ses_live", now_ms - 1000, "user", None),
                ("live-a", "ses_live", now_ms - 500, "assistant", "tool-calls"),
                ("trunc-u", "ses_trunc", base + 4000, "user", None),
                ("trunc-a", "ses_trunc", base + 4010, "assistant", "length"),
            ):
                data = {"role": role, "time": {"created": created}}
                if finish is not None:
                    data["finish"] = finish
                conn.execute(
                    "INSERT INTO message VALUES (?,?,?,?,?)",
                    (mid, sid, created, created, json.dumps(data)),
                )
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                states = {
                    sid: self._child_agent_row(row)["activity_state"]
                    for sid, row in sources.items()
                    if row.get("agent_parent_id")
                }

        # `stop` is the only confirmed finish, and only when nothing resumes it.
        self.assertEqual(states["ses_done"], "complete")
        self.assertEqual(states["ses_resumed"], "incomplete")
        # Mid-loop, missing, and length-truncated turns stay nonterminal.
        self.assertEqual(states["ses_stop"], "incomplete")
        self.assertEqual(states["ses_open"], "incomplete")
        # A nonterminal run that is still active reads as working, not complete.
        self.assertEqual(states["ses_live"], "working")
        # A length-truncated turn is not a confirmed finish either.
        self.assertEqual(states["ses_trunc"], "incomplete")

    def test_child_sessions_reach_aggregate_totals_without_a_second_session(self):
        """canonical_agent_sources selects each child once and keeps accounting."""
        sources = [
            {"provider": "opencode", "id": "ses_top", "path": "opencode:ses_top",
             "mtime": 10.0, "signature_mtime": 10.0},
            {"provider": "opencode", "id": "ses_child", "path": "opencode:ses_child",
             "mtime": 20.0, "signature_mtime": 20.0,
             "agent_parent_id": "ses_top", "agent_depth": 1},
            {"provider": "claude", "id": "claude-1", "path": "/trace.jsonl",
             "mtime": 5.0, "signature_mtime": 5.0},
        ]
        selected = meter.canonical_agent_sources(sources)

        self.assertEqual(
            sorted(row["id"] for row in selected),
            ["claude-1", "ses_child", "ses_top"],
        )
        # Accounting sources are untouched by the agent selection.
        self.assertEqual(
            sorted(row["id"] for row in meter.canonical_aggregation_sources(sources)),
            ["claude-1", "ses_child", "ses_top"],
        )

    def test_old_child_reports_incomplete_not_complete(self):
        """A child with no terminal evidence is a stale nonterminal trace."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            # One hour old: past the working window, with no terminal evidence.
            old_child = self._session_row("ses_old", "/repo", "Old child", "model-a",
                                          0.1, 10, 5, 0, 0, 0, base - 3_600_000,
                                          parent_id="ses_top", agent="explore")
            self._insert_sessions(conn, (parent, old_child))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = {row["id"]: row for row in meter.opencode_session_sources()}
                meter._summary_cache.clear()
                record = self._child_agent_row(sources["ses_old"])

        self.assertEqual(record["activity_state"], "incomplete")
        self.assertNotEqual(record["activity_state"], "complete")
        self.assertNotEqual(record["activity_state"], "recent")

    def test_parent_with_only_archived_children_emits_no_root_record(self):
        """A parent whose children are all archived has no live child group."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = self._build_db(root)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            archived_child = self._session_row(
                "ses_arch", "/repo", "Archived child", "model-a",
                0.1, 10, 5, 0, 0, 0, base + 1000,
                parent_id="ses_top", agent="explore", archived=base + 2000,
            )
            self._insert_sessions(conn, (parent, archived_child))
            conn.commit()
            conn.close()
            with mock.patch.object(meter, "OPENCODE_DB", str(root / "opencode.db")):
                sources = meter.opencode_session_sources()
                meter._summary_cache.clear()
                rows = [meter.session_summary(s) for s in sources]

        # The archived child is not discovered, so the parent has no live
        # children and must not emit a root record for a group that cannot form.
        self.assertEqual([row["id"] for row in rows], ["ses_top"])
        self.assertFalse(rows[0].get("_agent_records"))

    def test_child_parent_id_stays_out_of_allowlisted_projections(self):
        """Raw parent linkage never reaches sessions, /logs rows, or Subagents."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       0.5, 100, 20, 0, 0, 0, base)
            child = self._session_row("ses_child", "/repo", "Child", "model-a",
                                      0.75, 300, 60, 0, 0, 0, base + 1000,
                                      parent_id="ses_top", agent="explore")
            orphan = self._session_row("ses_orphan", "/repo", "Orphan", "model-a",
                                       0.25, 30, 6, 0, 0, 0, base + 2000,
                                       parent_id="ses_private_missing", agent="explore")
            db_path = self._family_db(root, base, (parent, child, orphan))
            _sources, cross, family_rows = self._cross(db_path)

        by_id = {row["id"]: row for row in cross["sessions"]}
        public = json.dumps({
            "sessions": cross["sessions"], "family": family_rows,
            "agent_usage": agent_usage_projection(cross["agent_usage"]),
        }, default=str)
        self.assertNotIn("child_parent_id", public)
        # The unlisted parent reference of an orphan never leaves the server.
        self.assertNotIn("ses_private_missing", public)
        # Only the resolved, listed root id is published on a child row.
        self.assertEqual(by_id["ses_child"]["root_session_id"], "ses_top")
        self.assertTrue(by_id["ses_child"]["is_child_session"])
        self.assertNotIn("root_session_id", by_id["ses_orphan"])
        self.assertNotIn("root_session_id", by_id["ses_top"])

    def test_archived_root_excludes_its_whole_family_from_totals(self):
        """An archived root hides its children and grandchildren, as before."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            rows = (
                self._session_row("ses_arch", "/old", "Archived", "model-a",
                                  1.00, 10, 5, 0, 0, 0, base, archived=base + 9000),
                self._session_row("ses_arch_child", "/old", "Archived child", "model-a",
                                  2.00, 10, 5, 0, 0, 0, base + 1000,
                                  parent_id="ses_arch", agent="explore"),
                self._session_row("ses_arch_grand", "/old", "Archived grandchild", "model-a",
                                  4.00, 10, 5, 0, 0, 0, base + 2000,
                                  parent_id="ses_arch_child", agent="explore"),
                self._session_row("ses_live", "/repo", "Live", "model-a",
                                  0.50, 10, 5, 0, 0, 0, base + 3000),
                self._session_row("ses_live_child", "/repo", "Live child", "model-a",
                                  0.25, 10, 5, 0, 0, 0, base + 4000,
                                  parent_id="ses_live", agent="explore"),
            )
            db_path = self._family_db(root, base, rows)
            sources, cross, _family = self._cross(db_path)

        ids = sorted(row["id"] for row in sources)
        self.assertEqual(ids, ["ses_live", "ses_live_child"])
        self.assertAlmostEqual(cross["total_cost"], 0.75)
        self.assertEqual(cross["total_sessions"], 2)
        # Nothing is left looking like an unattributed child of an archived parent.
        self.assertEqual(cross["agent_usage"]["totals"]["unresolved_agents"], 0)
        self.assertNotIn("ses_arch", json.dumps(cross["sessions"]))

    def test_missing_parent_is_the_only_unresolved_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            rows = (
                self._session_row("ses_live", "/repo", "Live", "model-a",
                                  0.50, 10, 5, 0, 0, 0, base),
                self._session_row("ses_orphan", "/repo", "Orphan", "model-a",
                                  0.40, 10, 5, 0, 0, 0, base + 1000,
                                  parent_id="ses_gone", agent="explore"),
            )
            db_path = self._family_db(root, base, rows)
            _sources, cross, _family = self._cross(db_path)

        self.assertAlmostEqual(cross["total_cost"], 0.90)
        self.assertEqual(cross["agent_usage"]["totals"]["unresolved_agents"], 1)
        self.assertAlmostEqual(
            cross["agent_usage"]["totals"]["unresolved_known_cost"], 0.40,
        )

    def test_grandchild_resolves_root_project_depth_and_root_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            rows = (
                self._session_row("ses_root", "/repo/root", "Root", "model-a",
                                  0.50, 10, 5, 0, 0, 0, base),
                self._session_row("ses_mid", "/repo/mid", "Mid", "model-a",
                                  0.25, 10, 5, 0, 0, 0, base + 1000,
                                  parent_id="ses_root", agent="explore"),
                self._session_row("ses_leaf", "/repo/leaf", "Leaf", "model-a",
                                  0.125, 10, 5, 0, 0, 0, base + 2000,
                                  parent_id="ses_mid", agent="gsd-reviewer"),
            )
            db_path = self._family_db(root, base, rows)
            sources, cross, _family = self._cross(db_path)
            with mock.patch.object(meter, "OPENCODE_DB", str(db_path)):
                meter._summary_cache.clear()
                by_source = {row["id"]: row for row in sources}
                leaf_record = self._child_agent_row(by_source["ses_leaf"])
                meter._summary_cache.clear()

        by_source = {row["id"]: row for row in sources}
        self.assertEqual(by_source["ses_leaf"]["project"], "/repo/root")
        self.assertEqual(by_source["ses_mid"]["project"], "/repo/root")
        self.assertEqual(by_source["ses_leaf"]["agent_depth"], 2)
        self.assertEqual(by_source["ses_mid"]["agent_depth"], 1)
        self.assertEqual(by_source["ses_leaf"]["agent_root_id"], "ses_root")
        self.assertTrue(by_source["ses_mid"]["agent_has_children"])
        self.assertEqual(leaf_record["depth"], 2)
        sessions = {row["id"]: row for row in cross["sessions"]}
        self.assertEqual(sessions["ses_leaf"]["root_session_id"], "ses_root")
        self.assertEqual(sessions["ses_leaf"]["child_depth"], 2)
        self.assertAlmostEqual(cross["total_cost"], 0.875)
        self.assertEqual(cross["agent_usage"]["totals"]["unresolved_agents"], 0)
        self.assertEqual(cross["agent_usage"]["totals"]["agents"], 2)

    def test_parent_cost_excludes_children_so_headline_is_parent_plus_children(self):
        """Pin the additive assumption against message-level parent evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            rows = (
                self._session_row("ses_top", "/repo", "Root", "model-a",
                                  1.25, 10, 5, 0, 0, 0, base),
                self._session_row("ses_a", "/repo", "A", "model-a",
                                  3.00, 10, 5, 0, 0, 0, base + 1000,
                                  parent_id="ses_top", agent="explore"),
            )
            db_path = self._family_db(root, base, rows)
            _sources, cross, family = self._cross(db_path)

        costs = {row["id"]: row["cost"] for row in cross["sessions"]}
        self.assertAlmostEqual(costs["ses_top"], 1.25)
        self.assertAlmostEqual(cross["total_cost"], 4.25)
        self.assertFalse(any("_cost_includes_children" in row for row in family))

    def test_parent_cost_above_own_messages_is_kept_and_children_added(self):
        """A parent reporting more than its own message sum is never reduced.

        Reverted messages leave a parent's session cost above its remaining
        message-level cost; that gap must not be read as included child spend.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = 1_784_548_900_000
            parent = self._session_row("ses_top", "/repo", "Root", "model-a",
                                       3.00, 10, 5, 0, 0, 0, base)
            child = self._session_row("ses_a", "/repo", "A", "model-a",
                                      0.50, 10, 5, 0, 0, 0, base + 1000,
                                      parent_id="ses_top", agent="explore")
            conn = self._build_db(root)
            self._insert_sessions(conn, (parent, child))
            self._assistant(conn, "m-top", "ses_top", 1.00, base)
            self._assistant(conn, "m-a", "ses_a", 0.50, base + 1)
            conn.commit()
            conn.close()
            _sources, cross, family = self._cross(root / "opencode.db")

        costs = {row["id"]: row["cost"] for row in cross["sessions"]}
        self.assertAlmostEqual(costs["ses_top"], 3.00)
        self.assertAlmostEqual(costs["ses_a"], 0.50)
        self.assertAlmostEqual(sum(row["cost"] for row in family), 3.50)

    def test_live_child_runs_fold_into_one_current_root_session(self):
        """Root + active child + grandchild form one current session and one cap."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now_ms = int(time.time() * 1000)
            # The root itself is idle; only its descendants are active.
            rows = (
                self._session_row("ses_root", "/repo", "Root", "model-a",
                                  1.00, 10, 5, 0, 0, 0, now_ms - 40 * 60_000),
                self._session_row("ses_child", "/repo", "Child", "model-a",
                                  2.00, 20, 10, 0, 0, 0, now_ms - 30_000,
                                  parent_id="ses_root", agent="explore"),
                self._session_row("ses_grand", "/repo", "Grand", "model-a",
                                  4.00, 40, 20, 0, 0, 0, now_ms - 20_000,
                                  parent_id="ses_child", agent="explore"),
            )
            db_path = self._family_db(root, now_ms - 50 * 60_000, rows)
            sources, cross, family = self._cross(db_path)

        current = cross["current_sessions"]
        self.assertEqual([row["id"] for row in current], ["ses_root"])
        self.assertAlmostEqual(current[0]["cost"], 7.00)
        self.assertEqual(current[0]["turns"], 3)
        self.assertNotIn("subagent_runs", current[0])
        self.assertFalse(current[0]["cost_partial"])
        self.assertEqual(current[0]["activity_state"], "working")

        # The native recent-session list offers the root, never a child run.
        recent = meter.menubar_recent_sessions(sources, summaries=current)
        self.assertEqual([row["id"] for row in recent], ["ses_root"])
        # The watcher's live source is the root even though a child is newest.
        self.assertEqual(meter.newest_current_source(sources)["id"], "ses_root")

        # A child run has no separate cap: it reports the root's cap, and the
        # root cap's spend includes every child run.
        settings = meter.normalize_budget_settings({})
        with mock.patch.dict(meter._xsess, {"sessions": family}):
            root_snapshot = meter.session_budget_snapshot(
                {"id": "ses_root"}, {"total_cost": 1.0, "availability": {"cost": True}},
                settings,
            )
            child_snapshot = meter.session_budget_snapshot(
                {"id": "ses_grand"}, {"total_cost": 4.0, "availability": {"cost": True}},
                settings,
            )
        self.assertEqual(root_snapshot["id"], "ses_root")
        self.assertAlmostEqual(root_snapshot["spend_usd"], 7.0)
        self.assertNotIn("subagent_runs", root_snapshot)
        self.assertFalse(root_snapshot["cost_partial"])
        self.assertEqual(child_snapshot["id"], "ses_root")
        self.assertAlmostEqual(child_snapshot["spend_usd"], 7.0)

    def test_session_cap_spend_combines_family_cost_availability(self):
        """Measured family spend is kept and any unavailable member is partial."""
        def family(root_available, child_available):
            return [
                {"id": "ses_root", "provider": "opencode", "cost": 1.0,
                 "availability": {"cost": root_available}},
                {"id": "ses_child", "provider": "opencode", "cost": 2.5,
                 "is_child_session": True, "root_session_id": "ses_root",
                 "availability": {"cost": child_available}},
            ]

        settings = meter.normalize_budget_settings({})
        with mock.patch.dict(meter._xsess, {"sessions": family(False, True)}):
            unavailable_root = meter.session_budget_snapshot(
                {"id": "ses_root"},
                {"total_cost": 0.0, "availability": {"cost": False}},
                settings,
            )
        self.assertAlmostEqual(unavailable_root["spend_usd"], 2.5)
        self.assertTrue(unavailable_root["cost_available"])
        self.assertTrue(unavailable_root["cost_partial"])

        with mock.patch.dict(meter._xsess, {"sessions": family(True, False)}):
            unavailable_child = meter.session_budget_snapshot(
                {"id": "ses_root"},
                {"total_cost": 1.0, "availability": {"cost": True}},
                settings,
            )
        self.assertAlmostEqual(unavailable_child["spend_usd"], 1.0)
        self.assertTrue(unavailable_child["cost_partial"])

        with mock.patch.dict(meter._xsess, {"sessions": family(False, False)}):
            unmeasured = meter.session_budget_snapshot(
                {"id": "ses_root"},
                {"total_cost": 0.0, "availability": {"cost": False}},
                settings,
            )
        self.assertIsNone(unmeasured["spend_usd"])
        self.assertFalse(unmeasured["cost_available"])
        self.assertEqual(unmeasured["threshold_state"], "unavailable")

        # A session with no child runs keeps its existing payload shape.
        with mock.patch.dict(meter._xsess, {"sessions": []}):
            solo = meter.session_budget_snapshot(
                {"id": "ses_solo"},
                {"total_cost": 1.0, "availability": {"cost": True}},
                settings,
            )
        self.assertNotIn("cost_partial", solo)


class OpenCodeSubagentDashboardContractTests(unittest.TestCase):
    """The All sessions view keeps child runs out of the list but shows them."""

    def setUp(self):
        self.page = Path(DASHBOARD_PAGE).read_text(
            encoding="utf-8"
        )

    def test_child_sessions_are_filtered_from_the_default_list(self):
        self.assertIn("const view=allSessionsView(workRows,{showChildren:globalShowChildren", self.page)
        self.assertIn("rows=(all||[]).filter(s=>!s.is_child_session&&", self.page)
        self.assertIn("let globalShowChildren=localStorage.getItem('tm_global_children')", self.page)
        self.assertIn("data-gchildren=hide", self.page)
        self.assertIn("data-gchildren=show", self.page)
        # The preference is a stored browser preference and is restored on load.
        self.assertIn("paintGlobalChildren();", self.page)
        # The control matches the g-sort segmented buttons: plain tab-reachable
        # buttons with pressed state, not an ARIA radio group without arrow keys.
        control = re.search(r'id=g-children[^>]*>(.*?)</div>', self.page, re.S).group(0)
        self.assertNotIn("role=radio", control)
        self.assertIn("type=button aria-pressed=true data-gchildren=hide", control)
        self.assertIn("type=button aria-pressed=false data-gchildren=show", control)
        self.assertNotIn("role=radiogroup", self.page)
        self.assertIn("b.setAttribute('aria-pressed',on?'true':'false');", self.page)

    def test_hidden_child_spend_is_still_reported_in_the_row_count_line(self):
        # Filtering rows out of a list must not imply their spend was excluded.
        self.assertIn("renderAllSessionStats(view.statsRows);", self.page)
        self.assertIn("setLogText($('g-count'),allSessionsCountText(view));", self.page)
        self.assertIn("counted, ${parentNote}", self.page)
        self.assertIn("listed under parents", self.page)
        self.assertIn("with no parent session", self.page)

    def test_current_session_card_marks_partial_family_cost_as_lower_bound(self):
        self.assertIn("const costPartial=costAvailable&&!!row.cost_partial;", self.page)
        self.assertIn("costLabel=costPartial?'Cost, at least':'Cost'", self.page)
        self.assertNotIn("(costPartial?'≥':'')", self.page)
        self.assertIn(
            "Cost is a lower bound: some runs in this session family have no cost evidence.",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_session_cap_display_and_alert_use_family_spend(self):
        """An OpenCode family cap reports and alerts on root plus child spend."""
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const metricAvailable=(row,metric)=>row?.availability?.[metric]!==false,money=n=>'$'+Number(n).toFixed(2);
const nodes={{}},el=id=>nodes[id]||(nodes[id]={{value:'',max:'',min:'0.5',textContent:'',style:{{setProperty(){{}}}}}});
const $=el,defaultSessionBudget=10,SESSION_BUDGET_SLIDER_MIN=0.5,SESSION_BUDGET_SLIDER_BASE_MAX=50,formatBudgetInput=n=>String(n);
let cap=10;const dollarInputValue=()=>cap;
eval(extract('sessionCapSpend'));
eval(extract('sessionBudgetSliderMax'));
eval(extract('syncSessionBudgetControls'));
eval(extract('sessionAlert'));
const family=(partial,rootAvailable=true)=>({{provider:'opencode',source:{{id:'ses_root'}},total_cost:1,availability:{{cost:rootAvailable}},
 session_budget:{{id:'ses_root',budget_usd:10,spend_usd:10.5,cost_available:true,cost_partial:partial}}}});
const out={{}};
const complete=family(false);syncSessionBudgetControls(10,complete);
out.familySpend=el('session-budget-spend').textContent;out.familyAlert=sessionAlert(complete);
const partial=family(true,false);syncSessionBudgetControls(10,partial);
out.partialSpend=el('session-budget-spend').textContent;out.partialAlert=sessionAlert(partial);
const claude={{provider:'claude',source:{{id:'c1'}},total_cost:1,availability:{{cost:true}},
 session_budget:{{id:'c1',budget_usd:10,spend_usd:1,cost_available:true}}}};
syncSessionBudgetControls(10,claude);out.claudeSpend=el('session-budget-spend').textContent;out.claudeAlert=sessionAlert(claude);
const claudeOver={{...claude,total_cost:12,session_budget:undefined}};out.claudeOverAlert=sessionAlert(claudeOver);
const unmeasured={{...claude,availability:{{cost:false}}}};syncSessionBudgetControls(10,unmeasured);
out.unmeasuredSpend=el('session-budget-spend').textContent;out.unmeasuredAlert=sessionAlert(unmeasured);
process.stdout.write(JSON.stringify(out));
"""
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        out = json.loads(completed.stdout)
        self.assertEqual(out["familySpend"], "Session spend $10.50")
        self.assertEqual(out["familyAlert"]["type"], "budget")
        self.assertEqual(out["familyAlert"]["current"], 10.5)
        self.assertIn("now $10.50", out["familyAlert"]["msg"])
        # A measured lower bound is still available and is labeled as such.
        self.assertEqual(out["partialSpend"], "Session spend at least $10.50")
        self.assertIn("now at least $10.50", out["partialAlert"]["msg"])
        # Sessions without a folded family keep their own-cost behavior.
        self.assertEqual(out["claudeSpend"], "Session spend $1.00")
        self.assertEqual(out["claudeAlert"]["msg"], "")
        self.assertIn("now $12.00", out["claudeOverAlert"]["msg"])
        self.assertEqual(out["unmeasuredSpend"], "Session spend unavailable")
        self.assertEqual(out["unmeasuredAlert"]["msg"], "")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_all_sessions_view_counts_children_and_respects_filters(self):
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const appFilterGroup=s=>s.provider,projectFilterValue=p=>p||'',metricAvailable=(row,key)=>row?.availability?.[key]!==false,hasLocalEstimate=()=>false;
const f=n=>String(n),money=n=>'$'+Number(n).toFixed(2),countWord=(n,w)=>n===1?w:w+'s';
eval(extract('childRootId'));eval(page.slice(page.indexOf('function allSessionsView('),page.indexOf('function allSessionsCountText(')));eval(extract('allSessionsCountText'));
const all=[
 {{id:'root',provider:'opencode',project:'/a',title:'Root work',cost:1,mtime:100}},
 {{id:'mid',provider:'opencode',project:'/a',title:'Mid run',cost:2,mtime:200,is_child_session:true,root_session_id:'root',child_depth:1}},
 {{id:'leaf',provider:'opencode',project:'/a',title:'Needle leaf',cost:4,mtime:300,is_child_session:true,root_session_id:'root',child_depth:2}},
 {{id:'orphan',provider:'opencode',project:'/a',title:'Orphan',cost:8,mtime:300,is_child_session:true}},
 {{id:'other',provider:'claude',project:'/b',title:'Other',cost:16,mtime:400}},
];
const hidden=allSessionsView(all,{{}});
const listed=allSessionsView(all,{{showChildren:true}});
const project=allSessionsView(all,{{project:'/b'}});
const search=allSessionsView(all,{{query:'needle'}});
const spend=v=>v.statsRows.filter(r=>metricAvailable(r,'cost')).reduce((a,b)=>a+(b.cost||0),0);
const filters={{search:{{query:'needle'}},time:{{rangeStart:250}},project:{{project:'/a'}},searchTime:{{query:'leaf',rangeStart:250}},none:{{}}}};
const parity=Object.fromEntries(Object.entries(filters).map(([name,opts])=>{{
 const h=allSessionsView(all,opts),l=allSessionsView(all,{{...opts,showChildren:true}});
 return [name,{{hidden:spend(h),listed:spend(l),hiddenCovered:h.statsRows.length,listedCovered:l.statsRows.length}}];
}}));
const time=allSessionsView(all,{{rangeStart:250}});
console.log(JSON.stringify({{
 parity,searchText:allSessionsCountText(search),listedText:allSessionsCountText(listed),
 time:{{rows:time.rows.map(r=>r.id),text:allSessionsCountText(time)}},
 hiddenRows:hidden.rows.map(r=>r.id),hiddenStatsCost:hidden.statsRows.reduce((a,b)=>a+b.cost,0),
 listedStatsCost:listed.statsRows.reduce((a,b)=>a+b.cost,0),listedRows:listed.rows.length,
 hidden:{{children:hidden.children,underParent:hidden.underParent,unparented:hidden.unparented,total:hidden.total}},
 hiddenText:allSessionsCountText(hidden),
 project:{{rows:project.rows.map(r=>r.id),children:project.children,text:allSessionsCountText(project)}},
 search:{{rows:search.rows.map(r=>r.id),children:search.children,underParent:search.underParent}},
}}));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["hiddenRows"], ["root", "other"])
        # Header stats include hidden child spend, identical to Listed mode.
        self.assertEqual(data["hiddenStatsCost"], 31)
        self.assertEqual(data["listedStatsCost"], 31)
        self.assertEqual(data["listedRows"], 5)
        # The grandchild counts under its root card even though its parent is hidden.
        self.assertEqual(data["hidden"], {"children": 3, "underParent": 2, "unparented": 1, "total": 2})
        self.assertIn("2 of 2 sessions · $31.00 covered spend · 5 of 5 cost-covered", data["hiddenText"])
        # Hidden and Listed header spend and coverage are equal under every filter,
        # and a root surfaced only by a matching child adds none of its own cost.
        for name, row in data["parity"].items():
            self.assertEqual(row["hidden"], row["listed"], name)
            self.assertEqual(row["hiddenCovered"], row["listedCovered"], name)
        self.assertEqual(data["parity"]["search"]["hidden"], 4)
        self.assertEqual(data["parity"]["time"]["hidden"], 28)
        self.assertEqual(data["parity"]["searchTime"]["hidden"], 4)
        self.assertIn("0 of 2 sessions · $4.00 covered spend · 1 of 1 cost-covered", data["searchText"])
        self.assertIn("1 parent card shown for matching runs", data["searchText"])
        self.assertIn("2 of 2 sessions + 3 subagent runs · $31.00 covered spend · 5 of 5 cost-covered", data["listedText"])
        self.assertNotIn("parent card shown", data["listedText"])
        self.assertEqual(data["time"]["rows"], ["root", "other"])
        self.assertIn("1 of 2 sessions · $28.00 covered spend · 3 of 3 cost-covered", data["time"]["text"])
        self.assertIn("3 subagent runs ($14.00) counted, 2 shown under parent cards · 1 with no parent session", data["hiddenText"])
        # The hidden-children note respects the active project filter.
        self.assertEqual(data["project"]["rows"], ["other"])
        self.assertEqual(data["project"]["children"], 0)
        self.assertNotIn("subagent", data["project"]["text"])
        # Search in Hidden mode finds a child run by surfacing its root card.
        self.assertEqual(data["search"], {"rows": ["root"], "children": 1, "underParent": 1})

    def test_parent_card_renders_an_accessible_child_subsection(self):
        self.assertIn("function childAgentSubsection(s)", self.page)
        self.assertIn('class=subagents', self.page)
        self.assertIn('class=subagentRow type=button data-open-session="${esc(sessionRowKey(child))}"', self.page)
        # Children are real buttons with a complete accessible name, so depth
        # and identity are never carried by indentation alone.
        self.assertIn('aria-label="Open subagent run ${esc(title)}"', self.page)
        # The role is provider-reported metadata, surfaced verbatim and bounded.
        self.assertIn('class=subagentRole>${esc(child.child_agent_role)}<', self.page)
        self.assertIn("Unreported role", self.page)

    def test_child_rows_navigate_without_double_firing_the_parent(self):
        self.assertIn("const childButton=event.target.closest('[data-open-session]');", self.page)
        self.assertIn("event.stopPropagation();", self.page)
        self.assertIn("selectSession(sessionRowKey(childRow))", self.page)

    def test_measured_free_tier_zero_is_not_presented_as_unavailable(self):
        # A real free-tier price renders as $0.0, distinct from missing billing.
        self.assertIn("child.cost>0?costValueHtml", self.page)
        self.assertIn("Measured free-tier price", self.page)
        self.assertIn("cost unavailable", self.page)

    def test_child_subsection_is_bounded_and_reports_the_hidden_count(self):
        self.assertIn("const CHILD_SECTION_LIMIT=6;", self.page)
        self.assertIn("const shown=children.slice(0,CHILD_SECTION_LIMIT);", self.page)
        self.assertIn("more ${countWord(hidden,'run')} not shown", self.page)

    def test_child_changes_invalidate_the_cached_parent_row(self):
        self.assertIn("childAgentsFor(s).map(c=>[c.id,c.cost,c.tokens", self.page)
        self.assertIn("function indexChildSessions(rows)", self.page)
        # Every descendant is keyed by its published root id, never a raw parent id.
        self.assertIn("const key=childRootId(row);", self.page)
        self.assertNotIn("child_parent_id", self.page)

    def test_subagents_page_discloses_unattributed_child_runs(self):
        # A child run whose parent was never discovered is counted in totals but
        # has no parent card, so the explorer must disclose the gap rather than
        # let the rollup look complete.
        self.assertIn('id=subagent-coverage-note', self.page)
        self.assertIn("usage?.totals?.unresolved_agents", self.page)
        self.assertIn("usage?.totals?.unresolved_known_cost", self.page)
        self.assertIn("has':'have'} no parent session; left out of the rollups above.", self.page)
        self.assertIn("counted in global totals", self.page)
        # Archived families are excluded from discovery, so the only remaining
        # cause is a missing parent record; the copy must not blame archiving.
        self.assertNotIn("usually because the parent session was archived", self.page)
        self.assertIn("parent session record is missing", self.page)
        self.assertIn(".subagentCoverageNote[hidden]{display:none}", self.page)

    def test_parent_row_with_children_uses_top_aligned_metrics(self):
        # A parent with a tall child subsection must not center its own
        # headline metrics beside the middle of that subsection.
        self.assertIn(".srow.hasChildren{align-items:start}", self.page)
        self.assertIn("const hasChildren=childAgentsFor(s).length>0;", self.page)
        self.assertIn("${hasChildren?' hasChildren':''}", self.page)

    def test_subagent_subsection_never_overflows_its_parent_at_laptop_width(self):
        # Long role/model/time text must truncate inside the grid track rather
        # than push the page wider at the 1024px laptop width.
        self.assertIn(".subagentMain{min-width:0;overflow:hidden}", self.page)
        self.assertIn(
            ".subagentMain .meta{font-size:var(--fs-11);color:var(--faint);margin-top:2px;"
            "display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
            "min-width:0}",
            self.page,
        )


if __name__ == "__main__":
    unittest.main()
