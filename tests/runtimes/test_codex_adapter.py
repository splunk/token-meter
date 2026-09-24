import json
import os
import tempfile
import unittest
from pathlib import Path

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes import codex as codex_runtime
from token_meter.runtimes.codex import CodexRuntimeAdapter
from tests.runtime_projection_privacy import assert_runtime_trace_privacy


class CodexRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.index = self.root / "session_index.jsonl"
        self.trace = self.sessions / "2026" / "08" / "11" / "rollout-session-1.jsonl"
        self.trace.parent.mkdir(parents=True)
        self.context = DiscoveryContext(home=str(self.root))
        self.rows = [
            {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta", "payload": {
                "id": "session-1", "cwd": "/work/project", "model_provider": "openai",
                "dynamic_tools": [{"name": "read", "description": "private definition"}],
            }},
            {"timestamp": "2026-08-11T00:00:01Z", "type": "turn_context", "payload": {
                "model": "gpt-test", "effort": "high", "cwd": "/work/project",
            }},
            {"timestamp": "2026-08-11T00:00:02Z", "type": "event_msg", "payload": {
                "type": "task_started", "model_context_window": 1000,
            }},
            {"timestamp": "2026-08-11T00:00:03Z", "type": "event_msg", "payload": {
                "type": "user_message", "message": "private prompt",
            }},
            {"timestamp": "2026-08-11T00:00:04Z", "type": "response_item", "payload": {
                "type": "function_call", "name": "read", "call_id": "call-1",
                "arguments": {"secret": "argument"},
            }},
            {"timestamp": "2026-08-11T00:00:05Z", "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "call-1",
                "output": "private tool output",
            }},
            {"timestamp": "2026-08-11T00:00:06Z", "type": "event_msg", "payload": {
                "type": "token_count", "info": {"model_context_window": 1000,
                    "last_token_usage": {"input_tokens": 100, "output_tokens": 20,
                                         "cached_input_tokens": 80}},
            }},
            {"timestamp": "2026-08-11T00:00:08Z", "type": "event_msg", "payload": {
                "type": "task_complete", "duration_ms": 6000,
                "time_to_first_token_ms": 1200,
            }},
        ]
        self._write(self.rows)
        self.index.write_text(json.dumps({
            "id": "session-1", "thread_name": "Safe title",
        }) + "\n")
        os.utime(self.trace, (2, 2))
        os.utime(self.index, (3, 3))
        self.adapter = CodexRuntimeAdapter(self.sessions, self.index)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, rows):
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def _write_trace(self, name, rows, mtime=2):
        path = self.trace.parent / f"rollout-{name}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def _session_meta(physical_id, logical_id=None, **extra):
        payload = {"id": physical_id, "cwd": "/work/project", **extra}
        if logical_id is not None:
            payload["session_id"] = logical_id
        return {
            "timestamp": "2026-08-11T00:00:00Z",
            "type": "session_meta",
            "payload": payload,
        }

    def test_first_session_meta_owns_physical_logical_and_parent_identity(self):
        child_path = self._write_trace("child", [
            self._session_meta(
                "child-1", "task-1", forked_from_id="root-1",
            ),
            self._session_meta("root-1"),
        ])

        legacy_sources = self.adapter.discover_legacy(self.context)
        child = next(
            source for source in legacy_sources
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["id"], "task-1")
        self.assertEqual(child["physical_trace_id"], "child-1")
        self.assertEqual(child["logical_session_id"], "task-1")
        self.assertEqual(child["forked_from_id"], "root-1")
        self.assertIsNone(child["parent_thread_id"])
        self.assertEqual(child["lineage_parent_id"], "root-1")
        native_ids = {
            source.locator.value: source.session_id
            for source in self.adapter.discover(self.context)
        }
        self.assertEqual(native_ids[str(child_path)], "task-1")

    def test_numeric_usage_signature_keeps_only_finite_numeric_fields(self):
        signature = getattr(codex_runtime, "_numeric_usage_signature", None)

        self.assertIsNotNone(signature)
        self.assertEqual(signature({
            "output_tokens": 5,
            "input_tokens": 10.5,
            "cached_input_tokens": True,
            "label": "10",
            "nested": {"input_tokens": 3},
            "infinite": float("inf"),
            "not_a_number": float("nan"),
        }), (
            ("input_tokens", 10.5),
            ("output_tokens", 5),
        ))

    def test_token_fingerprints_use_model_and_usage_but_not_timestamps(self):
        token_events = getattr(codex_runtime, "_token_events", None)
        rows = (
            {"timestamp": "2026-08-11T00:00:00Z", "type": "turn_context",
             "payload": {"model": "gpt-test"}},
            {"timestamp": "2026-08-11T00:00:01Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 10, "output_tokens": 5},
                 "total_token_usage": {"input_tokens": 100, "output_tokens": 50},
                 "provider_note": "ignored",
             }}},
        )
        restamped = (
            {**rows[0], "timestamp": "2026-08-11T01:00:00Z"},
            {**rows[1], "timestamp": "2026-08-11T01:00:01Z"},
        )

        self.assertIsNotNone(token_events)
        self.assertEqual(token_events(rows), (
            (
                1,
                "gpt-test",
                (("input_tokens", 10), ("output_tokens", 5)),
                (("input_tokens", 100), ("output_tokens", 50)),
            ),
        ))
        self.assertEqual(token_events(rows), token_events(restamped))

    def test_token_fingerprint_match_requires_exact_model_last_and_total_shape(self):
        matches = getattr(codex_runtime, "_token_events_match", None)
        last = (("input_tokens", 10), ("output_tokens", 5))
        total = (("input_tokens", 100), ("output_tokens", 50))
        base = (1, "gpt-test", last, total)

        self.assertIsNotNone(matches)
        self.assertTrue(matches(base, (8, "gpt-test", last, total)))
        self.assertFalse(matches(base, (8, "gpt-other", last, total)))
        self.assertFalse(matches(base, (
            8, "gpt-test", (("input_tokens", 11), ("output_tokens", 5)), total,
        )))
        self.assertFalse(matches(base, (8, "gpt-test", last, ())))
        self.assertFalse(matches((1, "gpt-test", last, ()), base))
        self.assertTrue(matches(
            (1, "gpt-test", last, ()),
            (8, "gpt-test", last, ()),
        ))

    def test_token_fingerprint_cache_is_lru_bounded_and_prunes_deleted_paths(self):
        def token_rows(value):
            return (
                {"type": "turn_context", "payload": {"model": "gpt-test"}},
                {"type": "event_msg", "payload": {
                    "type": "token_count", "info": {
                        "last_token_usage": {
                            "input_tokens": value, "output_tokens": 1,
                        },
                        "total_token_usage": {
                            "input_tokens": value, "output_tokens": 1,
                        },
                    },
                }},
            )

        paths = [
            self._write_trace(f"cache-{value}", token_rows(value), mtime=value)
            for value in (10, 20, 30)
        ]
        try:
            adapter = CodexRuntimeAdapter(
                self.sessions, self.index, token_event_cache_limit=2,
            )
        except TypeError:
            self.fail("CodexRuntimeAdapter must accept a bounded token event cache")
        events_for_path = getattr(adapter, "_token_events_for_path", None)

        self.assertIsNotNone(events_for_path)
        events_for_path(paths[0])
        events_for_path(paths[1])
        events_for_path(paths[0])
        events_for_path(paths[2])

        self.assertEqual(
            tuple(adapter._token_event_cache),
            (str(paths[0]), str(paths[2])),
        )
        paths[0].unlink()
        adapter.discover(self.context)
        self.assertNotIn(str(paths[0]), adapter._token_event_cache)

    @staticmethod
    def _turn(model="gpt-test", timestamp="2026-08-11T00:00:01Z"):
        return {
            "timestamp": timestamp,
            "type": "turn_context",
            "payload": {"model": model, "cwd": "/work/project"},
        }

    @staticmethod
    def _token_event(input_tokens, output_tokens, total_input=None,
                     total_output=None, timestamp="2026-08-11T00:00:02Z"):
        info = {
            "last_token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }
        if total_input is not None and total_output is not None:
            info["total_token_usage"] = {
                "input_tokens": total_input,
                "output_tokens": total_output,
            }
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": info},
        }

    @staticmethod
    def _tool_call(name, timestamp="2026-08-11T00:00:01.500Z"):
        return {
            "timestamp": timestamp,
            "type": "response_item",
            "payload": {"type": "function_call", "name": name},
        }

    def _native_load(self, path):
        source = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value == str(path)
        )
        return self.adapter.load(source, DetailLevel.FULL)

    def test_direct_child_native_load_removes_inherited_execution_chunk(self):
        root_path = self._write_trace("root", [
            self._session_meta("root-1", "task-1"),
            self._turn(),
            {"timestamp": "2026-08-11T00:00:01Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self._tool_call("inherited_tool"),
            self._token_event(100, 10, 100, 10),
            {"timestamp": "2026-08-11T00:00:03Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 2000}},
        ], mtime=10)
        child_path = self._write_trace("child", [
            self._session_meta("child-1", "task-1", forked_from_id="root-1"),
            self._session_meta("root-1", "task-1"),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            {"timestamp": "2026-08-11T01:00:01Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self._tool_call("inherited_tool", "2026-08-11T01:00:01.500Z"),
            self._token_event(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
            {"timestamp": "2026-08-11T01:00:03Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 2000}},
            self._turn(timestamp="2026-08-11T02:00:01Z"),
            {"timestamp": "2026-08-11T02:00:01Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self._tool_call("child_tool", "2026-08-11T02:00:01.500Z"),
            self._token_event(50, 5, 150, 15, "2026-08-11T02:00:02Z"),
            {"timestamp": "2026-08-11T02:00:03Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 2000}},
        ], mtime=20)

        sources = {
            source.locator.value: source
            for source in self.adapter.discover(self.context)
        }
        root = self.adapter.load(sources[str(root_path)], DetailLevel.FULL)
        child = self.adapter.load(sources[str(child_path)], DetailLevel.FULL)

        self.assertEqual(root.usage.input_tokens.value, 100)
        self.assertEqual(root.usage.output_tokens.value, 10)
        self.assertEqual(child.usage.input_tokens.value, 50)
        self.assertEqual(child.usage.output_tokens.value, 5)
        self.assertEqual([tool.name for tool in child.tools], ["child_tool"])
        self.assertEqual(len(child.turns), 1)

    def test_nested_child_keeps_only_work_added_after_its_direct_parent(self):
        root_path = self._write_trace("nested-root", [
            self._session_meta("nested-root-1", "task-nested"),
            self._turn(),
            self._token_event(100, 10, 100, 10),
        ], mtime=10)
        child_path = self._write_trace("nested-child", [
            self._session_meta(
                "nested-child-1", "task-nested",
                forked_from_id="nested-root-1",
            ),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            self._token_event(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
            self._turn(timestamp="2026-08-11T02:00:01Z"),
            self._token_event(50, 5, 150, 15, "2026-08-11T02:00:02Z"),
        ], mtime=20)
        grandchild_path = self._write_trace("nested-grandchild", [
            self._session_meta(
                "nested-grandchild-1", "task-nested",
                forked_from_id="nested-child-1",
            ),
            self._turn(timestamp="2026-08-11T03:00:01Z"),
            self._token_event(100, 10, 100, 10, "2026-08-11T03:00:02Z"),
            self._turn(timestamp="2026-08-11T04:00:01Z"),
            self._token_event(50, 5, 150, 15, "2026-08-11T04:00:02Z"),
            self._turn(timestamp="2026-08-11T05:00:01Z"),
            self._token_event(25, 3, 175, 18, "2026-08-11T05:00:02Z"),
        ], mtime=30)

        sources = {
            source.locator.value: source
            for source in self.adapter.discover(self.context)
        }
        usage = {}
        for path in (root_path, child_path, grandchild_path):
            result = self.adapter.load(sources[str(path)], DetailLevel.FULL)
            usage[str(path)] = (
                result.usage.input_tokens.value,
                result.usage.output_tokens.value,
            )

        self.assertEqual(usage[str(root_path)], (100, 10))
        self.assertEqual(usage[str(child_path)], (50, 5))
        self.assertEqual(usage[str(grandchild_path)], (25, 3))
        self.assertEqual(
            sum(input_tokens + output_tokens
                for input_tokens, output_tokens in usage.values()),
            193,
        )

    def test_lineage_cycle_retains_all_usage(self):
        first_path = self._write_trace("cycle-first", [
            self._session_meta(
                "cycle-first-1", "cycle-task",
                forked_from_id="cycle-second-1",
            ),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        second_path = self._write_trace("cycle-second", [
            self._session_meta(
                "cycle-second-1", "cycle-task",
                forked_from_id="cycle-first-1",
            ),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            self._token_event(10, 2, 10, 2, "2026-08-11T01:00:02Z"),
        ], mtime=20)

        sources = {
            source.locator.value: source
            for source in self.adapter.discover(self.context)
        }
        first = self.adapter.load(sources[str(first_path)], DetailLevel.FULL)
        second = self.adapter.load(sources[str(second_path)], DetailLevel.FULL)

        self.assertEqual(first.usage.input_tokens.value, 10)
        self.assertEqual(first.usage.output_tokens.value, 2)
        self.assertEqual(second.usage.input_tokens.value, 10)
        self.assertEqual(second.usage.output_tokens.value, 2)

    def test_adjacent_exact_cumulative_snapshot_counts_once(self):
        path = self._write_trace("adjacent-total", [
            self._session_meta("adjacent-total-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
            {"timestamp": "2026-08-11T00:00:02.500Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 1000}},
            self._token_event(10, 2, 10, 2, "2026-08-11T00:00:03Z"),
        ])
        source = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value == str(path)
        )

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 2)
        self.assertEqual(len(result.turns), 1)

    def test_adjacent_same_last_usage_without_totals_counts_twice(self):
        path = self._write_trace("adjacent-no-total", [
            self._session_meta("adjacent-no-total-1"),
            self._turn(),
            self._token_event(10, 2),
            self._token_event(10, 2, timestamp="2026-08-11T00:00:03Z"),
        ])
        source = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value == str(path)
        )

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 20)
        self.assertEqual(result.usage.output_tokens.value, 4)
        self.assertEqual(len(result.turns), 2)

    def test_missing_parent_retains_all_usage(self):
        path = self._write_trace("missing-parent", [
            self._session_meta(
                "missing-child-1", forked_from_id="absent-parent-1",
            ),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ])

        result = self._native_load(path)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 2)

    def test_duplicate_parent_identity_retains_all_usage(self):
        for suffix, mtime in (("first", 10), ("second", 20)):
            self._write_trace(f"duplicate-parent-{suffix}", [
                self._session_meta("duplicate-parent-1"),
                self._turn(),
                self._token_event(10, 2, 10, 2),
            ], mtime=mtime)
        child_path = self._write_trace("duplicate-parent-child", [
            self._session_meta(
                "duplicate-child-1", forked_from_id="duplicate-parent-1",
            ),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            self._token_event(10, 2, 10, 2, "2026-08-11T01:00:02Z"),
        ], mtime=30)

        result = self._native_load(child_path)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 2)

    def test_self_parent_retains_all_usage(self):
        path = self._write_trace("self-parent", [
            self._session_meta("self-parent-1", forked_from_id="self-parent-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ])

        result = self._native_load(path)

        self.assertEqual(result.usage.input_tokens.value, 10)
        self.assertEqual(result.usage.output_tokens.value, 2)

    def test_model_or_usage_mismatch_prevents_prefix_removal(self):
        self._write_trace("mismatch-parent", [
            self._session_meta("mismatch-parent-1"),
            self._turn(model="gpt-parent"),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        cases = (
            ("model", "gpt-child", 10, 10),
            ("usage", "gpt-parent", 11, 11),
        )
        for offset, (name, model, input_tokens, total_input) in enumerate(cases, 20):
            path = self._write_trace(f"mismatch-child-{name}", [
                self._session_meta(
                    f"mismatch-child-{name}-1",
                    forked_from_id="mismatch-parent-1",
                ),
                self._turn(model=model, timestamp="2026-08-11T01:00:01Z"),
                self._token_event(
                    input_tokens, 2, total_input, 2,
                    "2026-08-11T01:00:02Z",
                ),
            ], mtime=offset)
            with self.subTest(name=name):
                result = self._native_load(path)
                self.assertEqual(result.usage.input_tokens.value, input_tokens)
                self.assertEqual(result.usage.output_tokens.value, 2)

    def test_nested_source_parent_thread_id_is_used_when_fork_id_is_absent(self):
        child_path = self._write_trace("nested-parent-field", [
            self._session_meta("nested-source-child-1", source={
                "subagent": {"thread_spawn": {
                    "parent_thread_id": "nested-source-parent-1",
                }},
            }),
        ])

        child = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["parent_thread_id"], "nested-source-parent-1")
        self.assertEqual(child["lineage_parent_id"], "nested-source-parent-1")

    def test_parent_appearance_changes_child_lineage_revision(self):
        child_path = self._write_trace("revision-child", [
            self._session_meta(
                "revision-child-1", forked_from_id="revision-parent-1",
            ),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        before_source = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        native_source = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value == str(child_path)
        )

        self.assertIn("lineage_revision", before_source)
        before = before_source["lineage_revision"]
        native_before = self.adapter.current_revision(native_source)
        self._write_trace("revision-parent", [
            self._session_meta("revision-parent-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=20)
        after = next(
            source["lineage_revision"]
            for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        native_after = self.adapter.current_revision(native_source)

        self.assertNotEqual(before, after)
        self.assertNotEqual(native_before, native_after)
        self.assertEqual(before[0], "unresolved")
        self.assertEqual(after[:2], ("resolved", "revision-parent-1"))

    def test_parent_append_does_not_change_child_lineage_revision(self):
        parent_path = self._write_trace("append-parent", [
            self._session_meta("append-parent-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        child_path = self._write_trace("append-child", [
            self._session_meta(
                "append-child-1", forked_from_id="append-parent-1",
            ),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            self._token_event(10, 2, 10, 2, "2026-08-11T01:00:02Z"),
        ], mtime=20)
        before = next(
            source["lineage_revision"]
            for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        with parent_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 5, "output_tokens": 1,
                }},
            }}) + "\n")
        os.utime(parent_path, (30, 30))

        after = next(
            source["lineage_revision"]
            for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(before, after)

    def test_parent_replacement_changes_child_lineage_revision(self):
        parent_path = self._write_trace("replace-parent", [
            self._session_meta("replace-parent-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        child_path = self._write_trace("replace-child", [
            self._session_meta(
                "replace-child-1", forked_from_id="replace-parent-1",
            ),
            self._turn(timestamp="2026-08-11T01:00:01Z"),
            self._token_event(10, 2, 10, 2, "2026-08-11T01:00:02Z"),
        ], mtime=20)
        before = next(
            source["lineage_revision"]
            for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        replacement = parent_path.with_suffix(".replacement")
        replacement.write_text(parent_path.read_text())
        os.replace(replacement, parent_path)
        os.utime(parent_path, (30, 30))

        after = next(
            source["lineage_revision"]
            for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertNotEqual(before, after)

    def test_discovers_identity_model_provider_and_external_title(self):
        sources = self.adapter.discover(DiscoveryContext(home=str(self.root)))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.project, "/work/project")
        self.assertEqual(source.model_ref.provider_id, "openai")
        self.assertEqual(source.model_ref.model_id, "gpt-test")
        self.assertEqual(source.activity_mtime, 2.0)

    def test_model_provider_mapping_is_not_derived_from_runtime_id(self):
        rows = list(self.rows)
        rows[0] = {**rows[0], "payload": {
            **rows[0]["payload"], "model_provider": "custom-provider",
        }}
        self._write(rows)

        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        self.assertEqual(source.runtime_id, "codex")
        self.assertEqual(source.model_ref.provider_id, "custom-provider")

    def test_normalized_load_is_measured_and_content_free(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 20)
        self.assertEqual(result.usage.output_tokens.value, 20)
        self.assertEqual(result.usage.cache_read_tokens.value, 80)
        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(result.timing.active_seconds.value, 6.0)
        self.assertEqual(result.timing.ttft_seconds.value, 1.2)
        self.assertEqual([tool.name for tool in result.tools], ["read"])
        self.assertEqual(len(result.turns), 1)
        encoded = repr(result)
        for private in ("private prompt", "private tool output", "argument",
                        "private definition"):
            self.assertNotIn(private, encoded)

    def test_mcp_trace_views_are_structural_and_content_free(self):
        self.adapter.compatibility = meter._codex_compatibility()
        source = self.adapter.discover_legacy(self.context)[0]
        state = self.adapter.load(source, DetailLevel.FULL)

        assert_runtime_trace_privacy(
            self, source, state, runtime="codex", model="gpt-test",
            tool="read", native_types=("event_msg", "response_item"),
            forbidden=(
                "private prompt", "private tool output", "argument",
                "private definition", "/work/project",
            ),
        )

    def test_legacy_recompute_uses_current_response_user_message_without_injected_context(self):
        rows = [row for row in self.rows
                if (row.get("payload") or {}).get("type") != "user_message"]
        rows[3:3] = [
            {"timestamp": "2026-08-11T00:00:02.100Z", "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "<recommended_plugins>private plugin context</recommended_plugins>"},
                    {"type": "input_text", "text": "# AGENTS.md instructions\nprivate project instructions"},
                    {"type": "input_text", "text": "<environment_context>private environment</environment_context>"},
                ],
            }},
            {"timestamp": "2026-08-11T00:00:02.200Z", "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "Show the message that started this session"},
                ],
            }},
        ]
        self._write(rows)
        self.adapter.compatibility = meter._codex_compatibility()
        source = self.adapter.discover_legacy(self.context)[0]

        state = self.adapter.recompute_legacy(source)

        expected = "Show the message that started this session"
        self.assertEqual(state["series"][0]["user_message"], expected)
        self.assertEqual(state["executions"][0]["user_message"], expected)
        self.assertEqual(
            [event["detail"] for event in state["trace"] if event["kind"] == "user"],
            [expected],
        )
        encoded = repr(state)
        for injected in ("private plugin context", "private project instructions",
                         "private environment"):
            self.assertNotIn(injected, encoded)

    def test_legacy_recompute_prefers_canonical_user_message_over_response_fallback(self):
        rows = list(self.rows)
        rows.insert(3, {
            "timestamp": "2026-08-11T00:00:02.500Z", "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "fallback message"},
                ],
            },
        })
        self._write(rows)
        self.adapter.compatibility = meter._codex_compatibility()
        source = self.adapter.discover_legacy(self.context)[0]

        state = self.adapter.recompute_legacy(source)

        self.assertEqual(state["series"][0]["user_message"], "private prompt")
        self.assertEqual(state["executions"][0]["user_message"], "private prompt")

    def test_corrupt_partial_trace_is_bounded_and_unavailable(self):
        self.trace.write_text("not-json\n" + json.dumps(self.rows[0]) + "\n")
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["corrupt_rows", "usage_unavailable"],
        )

    def test_native_load_rejects_malformed_and_oversized_usage(self):
        rows = json.loads(json.dumps(self.rows))
        rows[6]["payload"]["info"]["last_token_usage"] = {
            "input_tokens": "bad",
            "cached_input_tokens": 0,
            "output_tokens": 10 ** 400,
        }
        self._write(rows)
        source = self.adapter.discover(self.context)[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cache_read_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.turns[0].output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([warning.code for warning in result.warnings], [
            "usage_unavailable",
        ])

    def test_trace_or_index_title_changes_revision(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        before = self.adapter.current_revision(source)
        self.trace.write_text(self.trace.read_text() + "{}\n")
        os.utime(self.trace, (4, 4))
        after_trace = self.adapter.current_revision(source)
        self.index.write_text(json.dumps({
            "id": "session-1", "thread_name": "Renamed",
        }) + "\n")
        os.utime(self.index, (5, 5))
        after_index = self.adapter.current_revision(source)

        self.assertNotEqual(before, after_trace)
        self.assertNotEqual(after_trace, after_index)

    def test_auto_review_model_requires_an_actual_direct_parent_model(self):
        self._write_trace("identity-root", [
            self._session_meta("identity-root-1"),
            self._turn(),
        ], mtime=10)
        self._write_trace("identity-middle", [
            self._session_meta("identity-middle-1", parent_thread_id="identity-root-1"),
            self._turn(model="codex-auto-review"),
        ], mtime=20)
        leaf_path = self._write_trace("identity-leaf", [
            self._session_meta("identity-leaf-1", parent_thread_id="identity-middle-1"),
            self._turn(model="codex-auto-review"),
        ], mtime=30)

        sources = {
            source["path"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

        middle = next(
            source for source in sources.values()
            if source["physical_trace_id"] == "identity-middle-1"
        )
        leaf = sources[str(leaf_path)]
        self.assertEqual(middle["model"], "gpt-test")
        self.assertEqual(middle["model_resolution"], "verified_parent")
        self.assertEqual(leaf["model"], "unknown-model")
        self.assertEqual(leaf["model_resolution"], "unresolved")

    def test_auto_review_model_rejects_a_direct_parent_without_a_model(self):
        self._write_trace("identity-model-less-parent", [
            self._session_meta("identity-model-less-parent-1"),
        ], mtime=10)
        child_path = self._write_trace("identity-model-less-child", [
            self._session_meta(
                "identity-model-less-child-1",
                parent_thread_id="identity-model-less-parent-1",
            ),
            self._turn(model="codex-auto-review"),
        ], mtime=20)

        child = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["model"], "unknown-model")
        self.assertEqual(child["model_resolution"], "unresolved")
        self.assertEqual(child["model_resolution_reason"], "parent_unavailable")

    def test_auto_review_model_rejects_cyclic_lineage_even_with_a_direct_model(self):
        child_path = self._write_trace("identity-cycle-child", [
            self._session_meta(
                "identity-cycle-child-1", parent_thread_id="identity-cycle-parent-1",
            ),
            self._turn(model="codex-auto-review"),
        ], mtime=10)
        self._write_trace("identity-cycle-parent", [
            self._session_meta(
                "identity-cycle-parent-1", forked_from_id="identity-cycle-child-1",
            ),
            self._turn(),
        ], mtime=20)

        child = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["model"], "unknown-model")
        self.assertEqual(child["model_resolution_reason"], "cyclic")
        self.assertIsNone(self.adapter.auto_review_evidence(child))

    def test_auto_review_model_rejects_a_direct_parent_cycle_masked_by_a_fork(self):
        self._write_trace("identity-cycle-fork", [
            self._session_meta("identity-cycle-fork-1"),
            self._turn(),
        ], mtime=10)
        child_path = self._write_trace("identity-cycle-masked-child", [
            self._session_meta(
                "identity-cycle-masked-child-1",
                forked_from_id="identity-cycle-fork-1",
                parent_thread_id="identity-cycle-masked-parent-1",
            ),
            self._turn(model="codex-auto-review"),
        ], mtime=20)
        self._write_trace("identity-cycle-masked-parent", [
            self._session_meta(
                "identity-cycle-masked-parent-1",
                parent_thread_id="identity-cycle-masked-child-1",
            ),
            self._turn(),
        ], mtime=30)

        child = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["lineage_parent_id"], "identity-cycle-fork-1")
        self.assertEqual(child["model"], "unknown-model")
        self.assertEqual(child["model_resolution_reason"], "cyclic")
        self.assertIsNone(self.adapter.auto_review_evidence(child))

    def test_auto_review_model_rejects_a_non_openai_parent_provider(self):
        self._write_trace("identity-provider-parent", [
            self._session_meta("identity-provider-parent-1", model_provider="custom"),
            self._turn(),
        ], mtime=10)
        child_path = self._write_trace("identity-provider-child", [
            self._session_meta(
                "identity-provider-child-1",
                parent_thread_id="identity-provider-parent-1",
            ),
            self._turn(model="codex-auto-review"),
        ], mtime=20)

        child = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )

        self.assertEqual(child["model"], "unknown-model")
        self.assertEqual(child["model_resolution"], "unresolved")

    def test_verified_auto_review_prefix_survives_missing_parent_only_at_same_revision(self):
        parent_path = self._write_trace("evidence-parent", [
            self._session_meta("evidence-parent-1"),
            self._turn(),
            self._token_event(10, 2, 10, 2),
        ], mtime=10)
        child_path = self._write_trace("evidence-child", [
            self._session_meta("evidence-child-1", parent_thread_id="evidence-parent-1"),
            self._turn(model="codex-auto-review", timestamp="2026-08-11T01:00:01Z"),
            self._token_event(10, 2, 10, 2, "2026-08-11T01:00:02Z"),
        ], mtime=20)
        live_source = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        evidence = self.adapter.auto_review_evidence(live_source)

        self.assertEqual(evidence["model"], "gpt-test")
        self.assertEqual(evidence["inherited_prefix"], 1)
        parent_path.unlink()
        historical_source = next(
            source for source in self.adapter.discover_legacy(self.context)
            if source["path"] == str(child_path)
        )
        historical_source.update({
            "model": evidence["model"],
            "observed_model": "codex-auto-review",
            "_verified_inherited_prefix": evidence["inherited_prefix"],
            "_verified_inherited_prefix_revision": evidence["revision"],
        })
        rows, _corrupt, available = self.adapter.load_rows(str(child_path))

        self.assertTrue(available)
        corrected = self.adapter._accounting_rows(historical_source, rows)
        self.assertEqual(codex_runtime._token_events(corrected), ())

        with child_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self._token_event(
                3, 1, 13, 3, "2026-08-11T01:00:03Z",
            )) + "\n")
        changed_rows, _corrupt, available = self.adapter.load_rows(str(child_path))

        self.assertTrue(available)
        unchanged = self.adapter._accounting_rows(historical_source, changed_rows)
        self.assertEqual(len(codex_runtime._token_events(unchanged)), 2)


if __name__ == "__main__":
    unittest.main()
