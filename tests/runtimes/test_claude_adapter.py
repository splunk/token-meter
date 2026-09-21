import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.domain.aggregates import current_session_summaries
from token_meter.runtimes.claude import ClaudeRuntimeAdapter
from tests.runtime_projection_privacy import assert_runtime_trace_privacy


class ClaudeRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "projects"
        self.desktop = self.root / "Claude"
        self.trace = self.projects / "-work-project" / "session-1.jsonl"
        self.trace.parent.mkdir(parents=True)
        self.metadata = (
            self.desktop / "claude-code-sessions" / "account" / "workspace" /
            "local_desktop-1.json"
        )
        self.metadata.parent.mkdir(parents=True)
        self.metadata.write_text(json.dumps({
            "sessionId": "desktop-1", "cliSessionId": "session-1",
            "originCwd": "/work/project", "title": "Safe title",
            "model": "claude-test", "lastActivityAt": 3000,
        }))
        self.rows = [
            {"type": "user", "timestamp": "2026-08-11T00:00:00Z", "cwd": "/work/project",
             "message": {"content": "private prompt"}},
            {"type": "assistant", "timestamp": "2026-08-11T00:00:01Z", "message": {
                "id": "msg-1", "model": "claude-test", "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "cache_read_input_tokens": 80,
                          "cache_creation_input_tokens": 5, "output_tokens": 10},
                "content": [{"type": "thinking", "thinking": "private reasoning"},
                            {"type": "tool_use", "id": "tool-1", "name": "Read",
                             "input": {"secret": "argument"}}],
            }},
            {"type": "assistant", "timestamp": "2026-08-11T00:00:02Z", "message": {
                "id": "msg-1", "model": "claude-test", "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "cache_read_input_tokens": 80,
                          "cache_creation_input_tokens": 5, "output_tokens": 10},
                "content": [{"type": "text", "text": "private response"}],
            }},
            {"type": "user", "timestamp": "2026-08-11T00:00:03Z", "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-1",
                             "content": "private tool output"}],
            }},
            {"type": "assistant", "timestamp": "2026-08-11T00:00:05Z", "message": {
                "id": "msg-2", "model": "claude-test", "stop_reason": "end_turn",
                "usage": {"input_tokens": 25, "output_tokens": 15},
                "content": [{"type": "text", "text": "done"}],
            }},
            {"type": "system", "subtype": "turn_duration",
             "timestamp": "2026-08-11T00:00:06Z", "durationMs": 6000},
        ]
        self._write(self.rows)
        os.utime(self.trace, (2, 2))
        os.utime(self.metadata, (3, 3))
        self.last_event_at = datetime(
            2026, 8, 11, 0, 0, 6, tzinfo=timezone.utc,
        ).timestamp()
        self.adapter = ClaudeRuntimeAdapter(
            self.projects, [self.desktop], default_model="claude-default",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, rows):
        self.trace.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def _add_duplicate_local_agent_trace(self):
        metadata = (
            self.desktop / "local-agent-mode-sessions" / "account" /
            "local_agent.json"
        )
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({
            "sessionId": "local-agent", "cliSessionId": "session-1",
            "title": "Local agent", "model": "claude-test",
            "lastActivityAt": 4000,
        }))
        trace = (
            metadata.with_suffix("") / ".claude" / "projects" /
            "-work-project" / "session-1.jsonl"
        )
        trace.parent.mkdir(parents=True)
        trace.write_text(self.trace.read_text())
        os.utime(metadata, (4, 4))
        os.utime(trace, (4, 4))
        return trace

    def test_discovers_desktop_enriched_code_trace(self):
        sources = self.adapter.discover(DiscoveryContext(home=str(self.root)))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.client_id, "claude_desktop")
        self.assertEqual(source.display_label, "Claude Desktop")
        self.assertEqual(source.project, "/work/project")
        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(source.model_ref.model_id, "claude-test")
        self.assertEqual(source.activity_mtime, self.last_event_at)

    def test_discovers_local_agent_trace_in_sibling_session_directory(self):
        desktop = self.root / "Claude-3p"
        cli_session_id = "87654321-4321-4321-4321-cba987654321"
        desktop_session_id = "local_12345678-1234-1234-1234-123456789abc"
        metadata = (
            desktop / "local-agent-mode-sessions" / "account" / "org" /
            f"{desktop_session_id}.json"
        )
        trace = (
            metadata.parent / "12345678" / ".claude" / "projects" /
            "outputs" / f"{cli_session_id}.jsonl"
        )
        metadata.parent.mkdir(parents=True)
        trace.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({
            "sessionId": desktop_session_id,
            "cliSessionId": cli_session_id,
            "cwd": str(trace.parents[2] / "outputs"),
            "lastActivityAt": 3000,
        }))
        trace.write_text("{}\n")
        adapter = ClaudeRuntimeAdapter(
            self.root / "empty-projects", [desktop],
            default_model="claude-default",
        )

        sources = adapter.discover_legacy(
            DiscoveryContext(home=str(self.root))
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["id"], cli_session_id)
        self.assertEqual(sources[0]["path"], str(trace))
        self.assertEqual(sources[0]["client"], "claude_desktop")

    def test_duplicate_local_agent_trace_has_one_canonical_legacy_source(self):
        duplicate = self._add_duplicate_local_agent_trace()

        sources = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root))
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["path"], str(duplicate))
        self.assertEqual(sources[0]["_aggregation_key"], "claude:session-1")
        self.assertTrue(sources[0]["_aggregation_canonical"])
        self.assertEqual(
            set(sources[0]["_duplicate_paths"]),
            {str(self.trace), str(duplicate)},
        )

    def test_duplicate_canonical_source_is_stable_when_globs_reverse(self):
        duplicate = self._add_duplicate_local_agent_trace()
        original_glob = self.adapter._glob

        with mock.patch.object(
            self.adapter, "_glob",
            side_effect=lambda pattern, recursive=False: tuple(reversed(
                original_glob(pattern, recursive=recursive)
            )),
        ):
            sources = self.adapter.discover_legacy(
                DiscoveryContext(home=str(self.root))
            )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["path"], str(duplicate))

    def test_opening_old_desktop_session_does_not_create_current_activity(self):
        before = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        before_revision = self.adapter.current_revision(before)
        opened_at = self.last_event_at + 3600
        os.utime(self.trace, (opened_at, opened_at))
        os.utime(self.metadata, (opened_at + 1, opened_at + 1))

        after = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        after_legacy = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root))
        )[0]
        after_revision = self.adapter.current_revision(after)
        current = current_session_summaries(
            [{"id": after_legacy["id"], "mtime": after_legacy["mtime"]}],
            now=opened_at + 1,
            max_age_s=30 * 60,
        )

        self.assertEqual(after.activity_mtime, self.last_event_at)
        self.assertEqual(after_legacy["mtime"], self.last_event_at)
        self.assertEqual(after_legacy.get("signature_mtime"), opened_at + 1)
        self.assertNotEqual(before_revision, after_revision)
        self.assertEqual(current, [])

    def test_new_trace_event_advances_activity_despite_unrelated_file_mtime(self):
        new_event_at = datetime(
            2026, 8, 11, 0, 10, 0, tzinfo=timezone.utc,
        ).timestamp()
        self._write([*self.rows, {
            "type": "system", "subtype": "turn_duration",
            "timestamp": "2026-08-11T00:10:00Z", "durationMs": 1,
        }])
        opened_at = new_event_at + 3600
        os.utime(self.trace, (opened_at, opened_at))

        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        self.assertEqual(source.activity_mtime, new_event_at)

    def test_split_message_usage_is_deduplicated_and_content_free(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.value, 45)
        self.assertEqual(result.usage.cache_read_tokens.value, 80)
        self.assertEqual(result.usage.cache_write_tokens.value, 5)
        self.assertEqual(result.usage.output_tokens.value, 25)
        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(len(result.turns), 2)
        self.assertEqual([tool.name for tool in result.tools], ["Read"])
        encoded = repr(result)
        for private in ("private prompt", "private reasoning", "private response",
                        "private tool output", "argument"):
            self.assertNotIn(private, encoded)

    def test_native_load_ignores_complete_zero_usage_synthetic_marker(self):
        self._write([*self.rows, {
            "type": "assistant", "timestamp": "2026-08-11T00:00:07Z",
            "message": {
                "id": "msg-synthetic", "model": "<synthetic>",
                "stop_reason": "stop_sequence", "content": [],
                "usage": {
                    "input_tokens": 0, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0, "output_tokens": 0,
                },
            },
        }])
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(len(result.turns), 2)
        self.assertEqual(result.usage.input_tokens.value, 45)
        self.assertEqual(result.usage.output_tokens.value, 25)
        self.assertEqual(result.ended_at, datetime(
            2026, 8, 11, 0, 0, 6, tzinfo=timezone.utc,
        ).astimezone())

    def test_mcp_trace_views_are_structural_and_content_free(self):
        self.adapter.compatibility = meter._claude_compatibility()
        source = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root)),
        )[0]
        state = self.adapter.load(source, DetailLevel.FULL)

        assert_runtime_trace_privacy(
            self, source, state, runtime="claude", model="claude-test",
            tool="Read", native_types=("assistant", "user"),
            forbidden=(
                "private prompt", "private reasoning", "private response",
                "private tool output", "argument", "/work/project",
            ),
        )

    def test_partial_and_corrupt_trace_preserves_unavailable(self):
        self.trace.write_text("not-json\n" + json.dumps(self.rows[0]) + "\n")
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([warning.code for warning in result.warnings], [
            "corrupt_rows", "usage_unavailable",
        ])

    def test_native_load_rejects_malformed_and_oversized_usage(self):
        self._write([
            self.rows[0],
            {
                "type": "assistant", "timestamp": "2026-08-11T00:00:01Z",
                "message": {
                    "id": "bad-usage", "model": "claude-test",
                    "usage": {
                        "input_tokens": "bad",
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 10 ** 400,
                    },
                    "content": [{"type": "text", "text": "private response"}],
                    "stop_reason": "end_turn",
                },
            },
        ])
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]

        result = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(result.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cache_read_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(result.turns[0].output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([warning.code for warning in result.warnings], [
            "usage_unavailable",
        ])

    def test_trace_and_desktop_metadata_both_change_revision(self):
        source = self.adapter.discover(DiscoveryContext(home=str(self.root)))[0]
        before = self.adapter.current_revision(source)
        self.trace.write_text(self.trace.read_text() + "{}\n")
        os.utime(self.trace, (4, 4))
        after_trace = self.adapter.current_revision(source)
        self.metadata.write_text(json.dumps({
            "sessionId": "desktop-1", "cliSessionId": "session-1",
            "originCwd": "/work/project", "title": "Renamed",
            "lastActivityAt": 5000,
        }))
        os.utime(self.metadata, (5, 5))
        after_metadata = self.adapter.current_revision(source)

        self.assertNotEqual(before, after_trace)
        self.assertNotEqual(after_trace, after_metadata)


if __name__ == "__main__":
    unittest.main()
