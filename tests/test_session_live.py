import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock
import meter


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

    def rollout_pair(self, root):
        first, second = root / "rollout-a.jsonl", root / "rollout-b.jsonl"
        first.write_text('{"a":1}\n')
        second.write_text('{"b":1}\n')
        sources = [
            {"id": "shared", "session": first.name, "path": str(first),
             "provider": "codex", "project": "/repo", "mtime": 5},
            {"id": "shared", "session": second.name, "path": str(second),
             "provider": "codex", "project": "/repo", "mtime": 1},
        ]
        return first, second, sources

    def test_logical_id_spanning_rollouts_is_ambiguous_without_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, sources = self.rollout_pair(root)

            result = meter.trash_session_log(
                "shared", sources=sources, trash_dir=str(root / "Trash"),
            )

            self.assertEqual(result["error_code"], "ambiguous_id")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_trace_key_moves_exactly_the_selected_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, sources = self.rollout_pair(root)

            result = meter.trash_session_log(
                "shared", trace="rollout-b.jsonl", sources=sources,
                trash_dir=str(root / "Trash"),
            )

            self.assertTrue(result["ok"])
            self.assertTrue(first.exists())
            self.assertFalse(second.exists())

    def test_trace_key_must_belong_to_the_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, sources = self.rollout_pair(root)
            for session_id, trace in (("other", "rollout-b.jsonl"), ("shared", "rollout-c.jsonl"),
                                      ("shared", "../rollout-b.jsonl")):
                result = meter.trash_session_log(
                    session_id, trace=trace, sources=sources,
                    trash_dir=str(root / "Trash"),
                )
                self.assertEqual(result["error_code"], "not_found")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_trace_key_longer_than_the_bound_is_rejected(self):
        result = meter.trash_session_log("shared", trace="x" * 241, sources=[])
        self.assertEqual(result["error_code"], "invalid_id")

    def test_delete_route_checks_the_provider_of_the_exact_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, sources = self.rollout_pair(root)
            sources[1]["provider"] = "opencode"
            with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.object(meter, "trash_session_log",
                                      return_value={"ok": True}) as trash:
                refused = meter.request_session_delete("shared", "rollout-b.jsonl")
                allowed = meter.request_session_delete("shared", "rollout-a.jsonl")
            self.assertEqual(refused["error_code"], "read_only_provider")
            self.assertTrue(allowed["ok"])
            trash.assert_called_once_with("shared", sources=sources, trace="rollout-a.jsonl")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_id_only_delete_of_a_read_only_multi_file_session_reports_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, sources = self.rollout_pair(Path(tmp))
            for source in sources:
                source["provider"] = "opencode"
            with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.object(meter, "trash_session_log") as trash:
                result = meter.request_session_delete("shared")
            self.assertEqual(result["error_code"], "read_only_provider")
            trash.assert_not_called()
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_id_only_delete_of_a_mixed_provider_session_stays_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, sources = self.rollout_pair(root)
            sources[1]["provider"] = "opencode"
            with mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.object(meter, "trash_session_log",
                                      return_value={"ok": True}) as trash:
                result = meter.request_session_delete("shared")
            self.assertEqual(result["error_code"], "ambiguous_id")
            trash.assert_not_called()
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_delete_route_forwards_the_trace_key(self):
        source = (Path(meter.__file__).resolve().parent / "token_meter" / "app.py").read_text()
        self.assertIn(
            'request_session_delete(payload.get("session_id"), payload.get("trace"))', source,
        )

    def test_trace_delete_keeps_the_claude_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, duplicate = root / "canonical.jsonl", root / "duplicate.jsonl"
            canonical.write_text("{}\n")
            duplicate.write_text("{}\n")
            source = {
                "id": "shared", "session": canonical.name, "path": str(canonical),
                "provider": "claude", "project": "/repo", "mtime": 2,
                "_aggregation_key": "claude:shared", "_aggregation_canonical": True,
                "_duplicate_paths": (str(canonical), str(duplicate)),
            }
            result = meter.trash_session_log(
                "shared", trace=canonical.name, sources=[source],
                trash_dir=str(root / "Trash"),
            )
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
        handler.headers = {"Host": "127.0.0.1:8722"}
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
            with mock.patch.object(meter, "_summary_cache", cache), \
                    mock.patch.object(meter, "_RUNTIME_DISCOVERY_FAILURES", ()):
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

    def test_logs_inventory_replaces_trace_paths_with_opaque_keys(self):
        from token_meter.domain.compare import trace_key
        saved_cache = dict(meter._xsess)
        private = "/Users/private-user/.claude/projects/p/abc.jsonl"
        full = [{"id": "abc", "path": private, "project": "~/p"}, {"id": "no-path"}]
        try:
            meter._xsess.update({
                "data": {"generated_at": 1, "sessions": full[:1], "total_sessions": 2},
                "at": meter.time.time(),
                "sessions": full,
            })
            result = meter.log_sessions_state()
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertNotIn("private-user", json.dumps(result))
        self.assertNotIn("path", result["sessions"][0])
        self.assertEqual(result["sessions"][0]["key"], trace_key({"path": private}))
        self.assertEqual(result["sessions"][0]["project"], "~/p")
        self.assertEqual(result["sessions"][1], {"id": "no-path"})
        self.assertEqual(full[0]["path"], private)

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
                "_tool_evidence": {},
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
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                result = meter.cross_session(sources=sources)
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(result["total_sessions"], 1)
        self.assertEqual(result["total_cost"], 1.0)
        self.assertEqual(result["total_tokens"], 100)
        self.assertNotIn(duplicate_path, json.dumps(result))

    def test_cross_session_projects_subagent_usage_and_selected_group(self):
        sources = [
            {"id": "root", "path": "/tmp/root", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/repo",
             "mtime": 2},
            {"id": "child", "path": "/tmp/child", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/repo",
             "mtime": 3},
        ]

        def summary(source):
            child = source["id"] == "child"
            cost = 2.0 if child else 1.0
            tokens = 200 if child else 100
            agent_record = {
                "id": source["id"],
                "parent_id": "root" if child else None,
                "session_id": source["id"],
                "runtime": "codex", "client": "Codex",
                "kind": "spawned" if child else "root",
                "depth": 1 if child else 0, "label": "Worker" if child else "",
                "role": "researcher" if child else None,
                "model": "gpt-5.6-sol", "activity_state": "working" if child else "recent",
                "started_at": 1, "ended_at": None, "last_activity_at": 2,
                "tokens": tokens, "tokens_available": True,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "cost": cost, "cost_available": True,
                "executions": 1, "attempts": 1, "retries": 0,
                "failed_attempts": 0, "tool_calls": 0,
                "agent_path": "/private/agent.md",
            }
            return {
                **source, "turns": 1, "cost": cost, "tokens": tokens,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "models": ["gpt-5.6-sol"], "token_estimate": False,
                "availability": meter.metric_availability(
                    "codex", cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"gpt-5.6-sol": cost},
                "_model_tok": {"gpt-5.6-sol": tokens},
                "_day_cost": {"2026-08-18": cost}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [],
                "_wait_samples": [], "_tool_evidence": {},
                "_agent_records": [agent_record],
            }

        saved_cache = dict(meter._xsess)
        try:
            meter._xsess.update({"data": None, "at": 0, "agent_groups": ()})
            with mock.patch.object(meter, "session_summary", side_effect=summary), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                cross = meter.cross_session(sources=sources)
            state = meter.attach_cross_session({"source": {"id": "child"}}, cross)
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(cross["total_cost"], 3.0)
        self.assertEqual(cross["total_tokens"], 300)
        self.assertEqual(cross["agent_usage"]["totals"]["agents"], 1)
        self.assertEqual(cross["agent_usage"]["totals"]["cost"], 2.0)
        self.assertEqual(
            cross["agent_usage"]["models"][0]["id"],
            "gpt-5.6-sol::codex::spawned",
        )
        self.assertEqual(
            cross["agent_usage"]["inventory"][0]["role"], "researcher",
        )
        self.assertEqual(
            cross["agent_usage"]["inventory"][0]["root_session_id"], "root",
        )
        self.assertFalse(cross["agent_usage"]["inventory_truncated"])
        self.assertEqual(state["agent_group"]["root_session_id"], "root")
        self.assertEqual(state["agent_group"]["selected_agent_id"], "child")
        self.assertEqual(len(state["agent_group"]["agents"]), 2)
        encoded = json.dumps({"cross": cross, "group": state["agent_group"]})
        self.assertNotIn("agent_path", encoded)
        self.assertNotIn("/private/agent.md", encoded)
        self.assertNotIn("_all_agents", encoded)
        self.assertNotIn("_session_ids", encoded)

    def test_cross_session_uses_physical_agents_without_changing_accounting_totals(self):
        sources = [
            {"id": "shared", "path": "/tmp/root-old", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/luna-3",
             "physical_trace_id": "physical-root", "mtime": 1},
            {"id": "shared", "path": "/tmp/root-new", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/luna-3",
             "physical_trace_id": "physical-root", "mtime": 2},
            {"id": "shared", "path": "/tmp/child-a", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/luna-3",
             "physical_trace_id": "physical-child-a", "mtime": 3},
            {"id": "shared", "path": "/tmp/child-b", "provider": "codex",
             "runtime": "Codex", "label": "Codex", "project": "/luna-3",
             "physical_trace_id": "physical-child-b", "mtime": 4},
        ]

        def summary(source):
            suffix = source["path"].rsplit("/", 1)[-1]
            is_root = suffix.startswith("root")
            cost = {"root-old": 1.0, "root-new": 2.0,
                    "child-a": 3.0, "child-b": 4.0}[suffix]
            tokens = int(cost * 100)
            agent_id = "agent-root" if is_root else f"agent-{suffix}"
            record = {
                "id": agent_id,
                "parent_id": None if is_root else "agent-root",
                "session_id": "shared" if is_root else None,
                "runtime": "codex", "client": "Codex",
                "kind": "root" if is_root else "spawned",
                "depth": 0 if is_root else 1,
                "model": "gpt-5.6-sol", "activity_state": "recent",
                "started_at": 1, "ended_at": 2, "last_activity_at": 2,
                "tokens": tokens, "tokens_available": True,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "cost": cost,
                "cost_available": True, "executions": 1, "attempts": 1,
                "retries": 0, "failed_attempts": 0, "tool_calls": 0,
            }
            return {
                **source, "turns": 1, "cost": cost, "tokens": tokens,
                "input_tokens": tokens - 10, "output_tokens": 10,
                "models": ["gpt-5.6-sol"], "token_estimate": False,
                "availability": meter.metric_availability(
                    "codex", cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"gpt-5.6-sol": cost},
                "_model_tok": {"gpt-5.6-sol": tokens},
                "_day_cost": {"2026-09-22": cost}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [],
                "_wait_samples": [], "_tool_evidence": {},
                "_agent_records": [record],
            }

        saved_cache = dict(meter._xsess)
        try:
            meter._xsess.update({"data": None, "at": 0, "agent_groups": ()})
            with mock.patch.object(meter, "session_summary", side_effect=summary), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                cross = meter.cross_session(sources=sources)
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

        self.assertEqual(cross["total_sessions"], 4)
        self.assertEqual(cross["total_cost"], 10.0)
        self.assertEqual(cross["total_tokens"], 1000)
        self.assertEqual(cross["agent_usage"]["totals"]["agents"], 2)
        self.assertEqual(cross["agent_usage"]["totals"]["cost"], 7.0)
        project_scopes = [
            scope for scope in cross["agent_usage"]["scopes"]
            if scope.get("project") == "/luna-3"
            and scope["window"] == "all" and scope["runtime"] == "codex"
        ]
        self.assertEqual(len(project_scopes), 1)
        self.assertEqual(project_scopes[0]["totals"]["agents"], 2)


if __name__ == "__main__":
    unittest.main()
