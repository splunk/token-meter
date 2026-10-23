import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import meter
from tests.runtime_projection_privacy import assert_runtime_trace_privacy
from token_meter.contracts import (
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    PriceQuote,
    session_source_public_dict,
)
from token_meter.models.catalog import GEMINI_FLASH_PRICE_UPDATE_AT
from token_meter.runtimes import agy as agy_runtime
from token_meter.runtimes.agy import AgyRuntimeAdapter, agy_agent_id


def priced_quote(query):
    if query.model.provider_id != "anthropic":
        return PriceQuote.unavailable(query.model)
    return PriceQuote(
        query.model, 5.0, 25.0, 0.50, 6.25,
        EvidenceBasis.MEASURED, "claude-opus-4-6",
    )


class AgyRuntimeAdapterTests(unittest.TestCase):
    def fixture_adapter(self, root):
        return AgyRuntimeAdapter(
            root / "brain",
            project_resolver=lambda value: value.replace(str(root), "[root]"),
            quote_resolver=priced_quote,
        )

    def _write_transcript(
        self,
        root,
        conv_id="conv-root-1",
        *,
        model_label="Claude Opus 4.6 (Thinking)",
        workspace="/repo/token-meter",
        child_id=None,
        cache_write_tokens=100,
    ):
        conv_dir = root / "brain" / conv_id
        log_dir = conv_dir / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        transcript = log_dir / "transcript.jsonl"
        full_transcript = log_dir / "transcript_full.jsonl"

        user_content = (
            f"<USER_INFORMATION>\nActive Workspaces:\n- {workspace}\n</USER_INFORMATION>\n"
            f"<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
            f"Gemini 2.5 Pro to {model_label}. No need to comment on this change if the "
            f"user doesn't ask about it. If reporting what model you are, please use a "
            f"human readable name instead of the exact string.\n</USER_SETTINGS_CHANGE>\n"
            "<USER_REQUEST>\nFix the bug in secret_file.py with SECRET_PROMPT_CANARY\n</USER_REQUEST>"
        )
        rows = [
            {
                "step_index": 1,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-10-22T20:00:00Z",
                "content": user_content,
            },
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-10-22T20:00:05Z",
                "content": "SECRET_ASSISTANT_RESPONSE_CANARY",
                "thinking": "SECRET_THINKING_CANARY",
                "thinking_duration": "2.5s",
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_tokens": 400,
                "cache_write_tokens": cache_write_tokens,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "arguments": {"Path": f"{workspace}/secret_file.py"},
                    }
                ],
            },
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "GENERIC",
                "status": "DONE",
                "created_at": "2026-10-22T20:00:06Z",
                "content": "SECRET_TOOL_OUTPUT_CANARY",
            },
        ]
        if cache_write_tokens is None:
            del rows[1]["cache_write_tokens"]
        if child_id:
            subagents_dir = conv_dir / ".system_generated" / "subagents"
            subagents_dir.mkdir(parents=True, exist_ok=True)
            (subagents_dir / f"{child_id}.json").write_text(json.dumps({
                "conversationId": child_id,
                "subagentDescriptor": {
                    "typeName": "research",
                    "role": "Codebase Researcher",
                },
                "workspaceUris": [f"file://{workspace}"],
            }))
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        full_transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return transcript

    def _write_summaries(self, root, rows):
        path = root / "conversation_summaries.db"
        with sqlite3.connect(path) as con:
            con.execute(
                "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, "
                "preview TEXT, raw_summary TEXT, workspace_uris TEXT, last_modified_time INTEGER)"
            )
            con.executemany(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (conv_id, "SECRET_TITLE_CANARY", "SECRET_PREVIEW_CANARY",
                     "SECRET_SUMMARY_CANARY", json.dumps(uris) if uris is not None else None,
                     index)
                    for index, (conv_id, uris) in enumerate(rows)
                ],
            )
        return path

    def _write_usage_turn(self, root, conv_id, model_label, input_tokens, cache_read_tokens,
                          output_tokens):
        log_dir = root / "brain" / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "type": "USER_INPUT",
                "created_at": "2026-10-22T20:00:00Z",
                "content": (
                    "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
                    f"Gemini 3.6 Flash to {model_label}.\n</USER_SETTINGS_CHANGE>"
                ),
            },
            {
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-10-22T20:00:05Z",
                "input_tokens": input_tokens,
                "cache_read_tokens": cache_read_tokens,
                "output_tokens": output_tokens,
            },
        ]
        (log_dir / "transcript.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

    def test_discovers_metadata_and_normalizes_claude_model_and_measured_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            sources = adapter.discover(DiscoveryContext(str(root)))
            self.assertEqual(len(sources), 1)
            source = sources[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.runtime_id, "agy")
        self.assertEqual(source.session_id, "conv-root-1")
        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(source.model_ref.model_id, "claude-opus-4-6")
        self.assertEqual(loaded.usage.input_tokens.value, 1000)
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(loaded.usage.output_tokens.value, 200)
        self.assertEqual(loaded.usage.output_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 400)
        self.assertEqual(loaded.usage.cache_write_tokens.value, 100)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.010825)
        self.assertEqual([(tool.name, tool.category, tool.status) for tool in loaded.tools], [
            ("read_file", "filesystem", "success"),
        ])
        self.assertNotIn("locator", session_source_public_dict(source))
        encoded = repr(loaded)
        for canary in (
            "SECRET_PROMPT_CANARY",
            "SECRET_ASSISTANT_RESPONSE_CANARY",
            "SECRET_THINKING_CANARY",
            "SECRET_TOOL_OUTPUT_CANARY",
        ):
            self.assertNotIn(canary, encoded)

    def test_unavailable_quote_keeps_measured_tokens_and_unavailable_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, model_label="Gemini 2.5 Pro")
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.model_ref.provider_id, "google")
        self.assertEqual(source.model_ref.model_id, "gemini-2.5-pro")
        self.assertEqual(loaded.usage.input_tokens.value, 1000)
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cost_usd.value)

    def test_builtin_gemini_pricing_estimates_cost_for_gemini_pro_and_flash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, conv_id="gemini-flash", model_label="Gemini 3.8 Flash (High)")
            adapter = AgyRuntimeAdapter(
                root / "brain",
                project_resolver=lambda value: value,
                quote_resolver=meter.price_quote,
            )
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.model_ref.provider_id, "google")
        self.assertEqual(source.model_ref.model_id, "gemini-3.8-flash")
        self.assertEqual(loaded.usage.input_tokens.value, 1000)
        self.assertEqual(loaded.usage.output_tokens.value, 200)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertGreater(loaded.usage.cost_usd.value, 0.0)

    def test_legacy_transcript_without_token_fields_estimates_tokens_and_links_invoke_subagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_dir = root / "brain" / "legacy-parent" / ".system_generated" / "logs"
            child_dir = root / "brain" / "legacy-child-1111-2222" / ".system_generated" / "logs"
            parent_dir.mkdir(parents=True, exist_ok=True)
            child_dir.mkdir(parents=True, exist_ok=True)
            parent_rows = [
                {
                    "step_index": 1,
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-08-01T10:00:00Z",
                    "content": (
                        "<USER_INFORMATION>\nActive Workspaces:\n- /repo/token-meter\n</USER_INFORMATION>\n"
                        "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
                        "Gemini 3.6 Flash to Gemini 3.8 Flash.\n</USER_SETTINGS_CHANGE>\n"
                        "<USER_REQUEST>\nResearch the architecture.\n</USER_REQUEST>"
                    ),
                },
                {
                    "step_index": 2,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-08-01T10:00:05Z",
                    "content": "Launching subagent now.",
                    "thinking": "Delegating to research subagent.",
                    "tool_calls": [
                        {
                            "id": "call-sub-1",
                            "name": "default_api:invoke_subagent",
                            "args": {"TypeName": "research", "Role": "Architecture Researcher"},
                        }
                    ],
                },
                {
                    "step_index": 3,
                    "type": "INVOKE_SUBAGENT",
                    "status": "DONE",
                    "created_at": "2026-08-01T10:00:06Z",
                    "content": (
                        '{"result":{"conversationId":"legacy-child-1111-2222",'
                        '"logAbsoluteUri":"file:///Users/test/.gemini/antigravity/brain/'
                        'legacy-child-1111-2222/.system_generated/logs/transcript.jsonl"}}'
                    ),
                },
            ]
            child_rows = [
                {
                    "step_index": 1,
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "created_at": "2026-08-01T10:00:07Z",
                    "content": "<USER_REQUEST>\nInspect codebase.\n</USER_REQUEST>",
                },
                {
                    "step_index": 2,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": "2026-08-01T10:00:12Z",
                    "content": "Architecture summary complete.",
                    "thinking": "Synthesizing findings.",
                },
            ]
            (parent_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(r) for r in parent_rows) + "\n"
            )
            (child_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(r) for r in child_rows) + "\n"
            )
            adapter = AgyRuntimeAdapter(
                root / "brain",
                project_resolver=lambda value: value,
                quote_resolver=meter.price_quote,
                compatibility=meter._agy_compatibility(),
            )
            legacy_sources = {row["id"]: row for row in adapter.discover_legacy(DiscoveryContext(str(root)))}
            native_sources = {row.session_id: row for row in adapter.discover(DiscoveryContext(str(root)))}
            loaded_parent = adapter.load(native_sources["legacy-parent"], DetailLevel.FULL)
            parent_state = adapter.recompute_legacy(legacy_sources["legacy-parent"])
            child_summary = adapter.summarize_legacy(legacy_sources["legacy-child-1111-2222"])

        self.assertTrue(legacy_sources["legacy-parent"]["token_estimate"])
        self.assertEqual(loaded_parent.usage.input_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(loaded_parent.usage.output_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(loaded_parent.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertGreater(loaded_parent.usage.input_tokens.value, 0)
        self.assertGreater(loaded_parent.usage.output_tokens.value, 0)
        self.assertGreater(loaded_parent.usage.cost_usd.value, 0.0)
        self.assertTrue(parent_state["source"]["token_estimate"])
        self.assertTrue(parent_state["availability"]["cost"])
        self.assertEqual(legacy_sources["legacy-child-1111-2222"]["agent_kind"], "spawned")
        self.assertEqual(
            legacy_sources["legacy-child-1111-2222"]["agent_parent_id"],
            agy_agent_id("legacy-parent"),
        )
        self.assertEqual(legacy_sources["legacy-child-1111-2222"]["agent_role"], "research")
        self.assertEqual(
            legacy_sources["legacy-child-1111-2222"]["agent_label"],
            "Architecture Researcher",
        )
        self.assertEqual(legacy_sources["legacy-child-1111-2222"]["model"], "gemini-3.8-flash")
        self.assertTrue(child_summary["token_estimate"])
        self.assertGreater(child_summary["cost"], 0.0)

    def test_subagent_descriptor_links_spawned_child_to_parent_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, conv_id="parent-conv", child_id="child-conv")
            self._write_transcript(root, conv_id="child-conv")
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._agy_compatibility()
            sources = adapter.discover_legacy(DiscoveryContext(str(root)))
            by_id = {row["id"]: row for row in sources}
            parent_summary = adapter.summarize_legacy(by_id["parent-conv"])
            child_summary = adapter.summarize_legacy(by_id["child-conv"])

        self.assertEqual(by_id["parent-conv"]["agent_kind"], "root")
        self.assertEqual(by_id["parent-conv"]["agent_id"], agy_agent_id("parent-conv"))
        self.assertIsNone(by_id["parent-conv"]["agent_parent_id"])
        self.assertEqual(by_id["child-conv"]["agent_kind"], "spawned")
        self.assertEqual(by_id["child-conv"]["agent_parent_id"], agy_agent_id("parent-conv"))
        self.assertEqual(by_id["child-conv"]["agent_role"], "research")
        self.assertEqual(by_id["child-conv"]["agent_label"], "Codebase Researcher")
        self.assertEqual(parent_summary["_agent_records"][0]["id"], agy_agent_id("parent-conv"))
        self.assertEqual(child_summary["_agent_records"][0]["parent_id"], agy_agent_id("parent-conv"))

    def test_mcp_trace_views_are_structural_and_content_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._agy_compatibility()
            source = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.load(source, DetailLevel.FULL)

            assert_runtime_trace_privacy(
                self,
                source,
                state,
                runtime="agy",
                model="claude-opus-4-6",
                tool="read_file",
                native_types=("user", "tool_call", "assistant"),
                forbidden=(
                    "SECRET_PROMPT_CANARY",
                    "SECRET_ASSISTANT_RESPONSE_CANARY",
                    "SECRET_THINKING_CANARY",
                    "SECRET_TOOL_OUTPUT_CANARY",
                    str(root),
                ),
            )

    def test_corrupt_partial_trace_fails_bounded_without_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "brain" / "broken-conv" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "transcript.jsonl").write_text("{broken\n{}\n")
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            session = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(session.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(session.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIn("corrupt_rows", {warning.code for warning in session.warnings})

    def test_legacy_state_summary_and_deletion_plan_use_registered_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            meter._agy_native_adapters.clear()
            with mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "brain")):
                source = meter.agy_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.session_summary(source)
                native_source = meter.runtime_registry().get("agy").discover(DiscoveryContext(str(root)))[0]
                plan = meter.runtime_registry().get("agy").deletion_plan(native_source)

        self.assertEqual(state["provider"], "agy")
        self.assertFalse(state["source"]["token_estimate"])
        self.assertTrue(state["source"]["approximate_cost"])
        self.assertEqual(state["primary_model"], "claude-opus-4-6")
        self.assertTrue(state["availability"]["cost"])
        self.assertEqual(summary["provider"], "agy")
        self.assertFalse(bool(summary.get("token_estimate")))
        self.assertTrue(summary["cost_approx"])
        self.assertEqual(summary["input_tokens"], 1000)
        self.assertEqual(summary["output_tokens"], 200)
        # Antigravity's live state is read-only to Token Meter.
        self.assertEqual(plan.disposition.value, "deny")
        self.assertEqual(plan.targets, ())


    def test_missing_cache_write_stays_unavailable_instead_of_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, cache_write_tokens=None)
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._agy_compatibility()
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)
            state = adapter.recompute_legacy(adapter.discover_legacy(DiscoveryContext(str(root)))[0])

        self.assertEqual(loaded.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cache_write_tokens.value)
        self.assertEqual(loaded.usage.cache_read_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 400)
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertFalse(state["availability"]["cache"])
        self.assertTrue(state["availability"]["cost"])

    def test_project_is_not_inferred_from_free_text_user_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "brain" / "free-text-conv" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)
            rows = [
                {
                    "type": "USER_INPUT",
                    "created_at": "2026-10-22T20:00:00Z",
                    "content": (
                        "<ADDITIONAL_METADATA>\nOpen file: file:///private-project/notes.md\n"
                        "</ADDITIONAL_METADATA>"
                    ),
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-10-22T20:00:05Z",
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
            ]
            (log_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n"
            )
            adapter = self.fixture_adapter(root)
            source = adapter.discover_legacy(DiscoveryContext(str(root)))[0]

        self.assertEqual(source["project"], "")
        self.assertNotIn("private-project", repr(source))

    def test_revision_changes_when_the_transcript_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            before = adapter.current_revision(source)
            with transcript.open("a") as handle:
                handle.write(json.dumps({"type": "GENERIC", "content": "more"}) + "\n")
            after = adapter.current_revision(source)

        self.assertEqual(before, source.revision)
        self.assertNotEqual(before, after)

    def test_oversized_transcript_loads_as_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            with mock.patch("token_meter.runtimes.agy.MAX_JSON_BYTES", 16):
                loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.input_tokens.value)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIn("usage_unavailable", {warning.code for warning in loaded.warnings})

    def test_row_cap_reports_bounded_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            with mock.patch("token_meter.runtimes.agy.MAX_ROWS", 2):
                source = adapter.discover(DiscoveryContext(str(root)))[0]
                loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.input_tokens.value, 1000)
        self.assertIn("history_truncated", {warning.code for warning in loaded.warnings})


    def test_project_comes_only_from_antigravity_workspace_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, conv_id="with-metadata")
            self._write_transcript(root, conv_id="without-metadata")
            self._write_summaries(root, [
                ("with-metadata", ["file:///work/recorded-app"]),
                ("without-metadata", None),
            ])
            adapter = self.fixture_adapter(root)
            sources = {row["id"]: row for row in adapter.discover_legacy(DiscoveryContext(str(root)))}

        self.assertEqual(sources["with-metadata"]["project"], "/work/recorded-app")
        # The transcript's tool arguments name /repo/token-meter; that is not evidence of a project.
        self.assertEqual(sources["without-metadata"]["project"], "")
        for canary in ("SECRET_TITLE_CANARY", "SECRET_PREVIEW_CANARY", "SECRET_SUMMARY_CANARY"):
            self.assertNotIn(canary, repr(sources))

    def test_summary_reader_cannot_read_content_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._write_summaries(root, [("conv", ["file:///work/app"])])
            adapter = self.fixture_adapter(root)
            con = adapter._summary_connection(path)
            try:
                for column in ("title", "preview", "raw_summary"):
                    with self.subTest(column=column):
                        with self.assertRaises(sqlite3.DatabaseError):
                            con.execute("SELECT {} FROM conversation_summaries".format(column)).fetchall()
            finally:
                con.close()

    def test_child_descriptor_workspace_is_not_given_to_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, conv_id="parent-conv", child_id="child-conv")
            self._write_transcript(root, conv_id="child-conv")
            adapter = self.fixture_adapter(root)
            sources = {row["id"]: row for row in adapter.discover_legacy(DiscoveryContext(str(root)))}

        self.assertEqual(sources["child-conv"]["project"], "/repo/token-meter")
        self.assertEqual(sources["parent-conv"]["project"], "")

    def test_unreadable_summaries_database_leaves_project_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            (root / "conversation_summaries.db").write_bytes(b"not a sqlite database")
            adapter = self.fixture_adapter(root)
            sources = adapter.discover_legacy(DiscoveryContext(str(root)))

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["project"], "")

    def test_gemini_pro_long_context_rate_applies_per_request_above_200k(self):
        def quote(query):
            return meter.price_quote(query, path=str(root / "settings.json"))

        costs = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_usage_turn(root, "pro-at-limit", "Gemini 2.5 Pro", 199_600, 400, 1_000)
            self._write_usage_turn(root, "pro-over-limit", "Gemini 2.5 Pro", 199_601, 400, 1_000)
            self._write_usage_turn(root, "flash-long", "Gemini 3.8 Flash", 299_600, 400, 1_000)
            adapter = AgyRuntimeAdapter(root / "brain", quote_resolver=quote)
            for source in adapter.discover(DiscoveryContext(str(root))):
                costs[source.session_id] = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value

        self.assertAlmostEqual(
            costs["pro-at-limit"],
            (199_600 * 1.25 + 400 * 0.125 + 1_000 * 10.0) / 1_000_000,
        )
        self.assertAlmostEqual(
            costs["pro-over-limit"],
            (199_601 * 2.50 + 400 * 0.25 + 1_000 * 15.0) / 1_000_000,
        )
        self.assertAlmostEqual(
            costs["flash-long"],
            (299_600 * 0.75 + 400 * 0.075 + 1_000 * 3.75) / 1_000_000,
        )


    def _write_parent_with_invocations(self, root, invocations):
        """Write a parent transcript with (role_args, child_id_or_None) invocation pairs."""
        log_dir = root / "brain" / "parent-conv" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z", "content": "go"}]
        for index, (args, child_id) in enumerate(invocations):
            rows.append({
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-10-22T20:00:0{}Z".format(index + 1),
                "input_tokens": 10,
                "output_tokens": 5,
                "tool_calls": [{"name": "invoke_subagent", "args": args}],
            })
            content = (
                '{"result":{"logAbsoluteUri":"file:///x/brain/' + child_id
                + '/.system_generated/logs/transcript.jsonl"}}'
            ) if child_id else '{"error":"failed"}'
            rows.append({"type": "INVOKE_SUBAGENT", "content": content})
        (log_dir / "transcript.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

    def test_subagent_names_reject_prose_and_credential_shaped_text(self):
        prose = "Fix the payroll export bug for Acme Corp"
        secret = "sk-ant-api03-SYNTHETICSECRETVALUE"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_parent_with_invocations(root, [
                ({"TypeName": secret, "Role": prose}, "child-from-call"),
            ])
            self._write_transcript(root, conv_id="child-from-call")
            self._write_transcript(root, conv_id="desc-parent", child_id="child-from-descriptor")
            self._write_transcript(root, conv_id="child-from-descriptor")
            descriptor = (
                root / "brain" / "desc-parent" / ".system_generated" / "subagents"
                / "child-from-descriptor.json"
            )
            data = json.loads(descriptor.read_text())
            data["subagentDescriptor"] = {"typeName": "ghp_SYNTHETIC0TOKEN", "role": prose}
            descriptor.write_text(json.dumps(data))
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._agy_compatibility()
            sources = {row["id"]: row for row in adapter.discover_legacy(DiscoveryContext(str(root)))}
            summaries = [adapter.summarize_legacy(row) for row in sources.values()]

        for child_id in ("child-from-call", "child-from-descriptor"):
            with self.subTest(child_id=child_id):
                child = sources[child_id]
                self.assertEqual(child["agent_kind"], "spawned")
                self.assertIsNone(child["agent_role"])
                self.assertEqual(child["agent_label"], "")
                self.assertEqual(child["title"], "Antigravity session")
        encoded = repr((sources, summaries))
        for canary in (prose, secret, "ghp_SYNTHETIC0TOKEN"):
            self.assertNotIn(canary, encoded)

    def test_failed_subagent_call_does_not_shift_later_child_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_parent_with_invocations(root, [
                ({"TypeName": "research", "Role": "Failed Researcher"}, None),
                ({"TypeName": "review", "Role": "Code Reviewer"}, "second-child"),
            ])
            self._write_transcript(root, conv_id="second-child")
            adapter = self.fixture_adapter(root)
            sources = {row["id"]: row for row in adapter.discover_legacy(DiscoveryContext(str(root)))}

        self.assertEqual(sources["second-child"]["agent_role"], "review")
        self.assertEqual(sources["second-child"]["agent_label"], "Code Reviewer")

    def test_subagent_descriptors_are_cached_by_file_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root, conv_id="parent-conv", child_id="child-conv")
            self._write_transcript(root, conv_id="child-conv")
            adapter = self.fixture_adapter(root)
            with mock.patch.object(
                agy_runtime, "_read_json", wraps=agy_runtime._read_json
            ) as read_json:
                adapter.discover_legacy(DiscoveryContext(str(root)))
                first = read_json.call_count
                adapter.discover_legacy(DiscoveryContext(str(root)))

        self.assertEqual(first, 1)
        self.assertEqual(read_json.call_count, 1)

    def test_oversized_transcript_is_listed_with_unavailable_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            adapter = self.fixture_adapter(root)
            with mock.patch.object(agy_runtime, "MAX_JSON_BYTES", 16):
                sources = adapter.discover(DiscoveryContext(str(root)))
                loaded = adapter.load(sources[0], DetailLevel.SUMMARY)

        self.assertEqual(len(sources), 1)
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIn("history_truncated", {warning.code for warning in loaded.warnings})

    def test_windows_and_unc_workspace_uris_keep_their_drive_and_host(self):
        self.assertEqual(
            agy_runtime._workspace_from_uris(["file:///C:/Users/dev/work/app"]),
            "C:/Users/dev/work/app",
        )
        self.assertEqual(
            agy_runtime._workspace_from_uris(["file://server/share/app"]),
            "//server/share/app",
        )
        self.assertEqual(agy_runtime._workspace_from_uris(["relative/path"]), "")

    def test_model_selection_text_must_name_a_known_model_family(self):
        prose = (
            "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
            "Gemini 2.5 Pro to my private quarterly plan.\n</USER_SETTINGS_CHANGE>"
        )
        known = (
            "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
            "Gemini 2.5 Pro to Gemini 3.8 Flash (High).\n</USER_SETTINGS_CHANGE>"
        )

        self.assertEqual(agy_runtime._extract_model_and_effort(prose), ("unknown-model", ""))
        self.assertEqual(agy_runtime._extract_model_and_effort(known), ("gemini-3.8-flash", "high"))

    def test_capped_summary_rows_keep_the_newest_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_summaries(root, [
                ("older-conv", ["file:///work/older"]),
                ("newer-conv", ["file:///work/newer"]),
            ])
            adapter = self.fixture_adapter(root)
            with mock.patch.object(agy_runtime, "MAX_SOURCES", 1):
                workspaces = adapter._summary_workspaces()

        self.assertEqual(workspaces, {"newer-conv": "/work/newer"})

    def test_gemini_override_keeps_the_long_context_rate(self):
        prices = {"input": 2.0, "output": 20.0, "cache_write": 0.0, "cache_read": 0.2}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = str(root / "settings.json")
            self.assertTrue(meter.set_model_price(
                "google", "gemini-2.5-pro", prices, path=settings, apply_to_all_history=True,
            )["ok"])
            self._write_usage_turn(root, "pro-over-limit", "Gemini 2.5 Pro", 199_601, 400, 1_000)
            adapter = AgyRuntimeAdapter(
                root / "brain", quote_resolver=lambda query: meter.price_quote(query, path=settings),
            )
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            cost = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value

        self.assertAlmostEqual(
            cost, (199_601 * 2.0 * 2 + 400 * 0.2 * 2 + 1_000 * 20.0 * 1.5) / 1_000_000,
        )


    def test_server_delete_boundary_rejects_antigravity_before_trash(self):
        self.assertIn("agy", meter.session_action_capability()["read_only_providers"])
        source = {"provider": "agy", "id": "agy-session"}
        with mock.patch.object(meter, "find_session", return_value=source), \
                mock.patch.object(meter, "trash_session_log") as trash:
            result = meter.request_session_delete("agy-session")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        self.assertIn("Antigravity", result["error"])
        trash.assert_not_called()

    def test_server_delete_leaves_a_discovered_antigravity_transcript_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = self._write_transcript(root)
            meter._agy_native_adapters.clear()
            with mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "brain")), \
                    mock.patch.object(meter, "all_session_sources", side_effect=meter.agy_session_sources), \
                    mock.patch.object(meter, "trash_session_log") as trash:
                result = meter.request_session_delete("conv-root-1")
            still_there = transcript.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        self.assertTrue(still_there)
        trash.assert_not_called()

    def test_model_selection_prose_starting_with_a_family_word_is_rejected(self):
        for label in ("Gemini my secret plan is ready", "o1 my private note", "Claude please review"):
            with self.subTest(label=label):
                text = (
                    "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
                    "Gemini 2.5 Pro to {}.\n</USER_SETTINGS_CHANGE>".format(label)
                )
                self.assertEqual(agy_runtime._extract_model_and_effort(text), ("unknown-model", ""))

    def test_unreadable_summaries_database_is_retried_on_the_next_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_summaries(root, [("conv", ["file:///work/app"])])
            adapter = self.fixture_adapter(root)
            real_connection = adapter._summary_connection
            with mock.patch.object(
                adapter, "_summary_connection",
                side_effect=[sqlite3.OperationalError("database is locked"), real_connection(
                    adapter._summary_database_path()
                )],
            ):
                first = adapter._summary_workspaces()
                second = adapter._summary_workspaces()

        self.assertEqual(first, {})
        self.assertEqual(second, {"conv": "/work/app"})

    def test_undated_request_uses_the_price_in_effect_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "brain" / "undated" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)
            rows = [
                {
                    "type": "USER_INPUT",
                    "content": (
                        "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from "
                        "Gemini 3.6 Flash to Gemini 3.8 Flash.\n</USER_SETTINGS_CHANGE>"
                    ),
                },
                {"type": "PLANNER_RESPONSE", "input_tokens": 1_000_000, "output_tokens": 0},
            ]
            (log_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n"
            )
            settings = str(root / "settings.json")
            adapter = AgyRuntimeAdapter(
                root / "brain", quote_resolver=lambda query: meter.price_quote(query, path=settings),
            )
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            with mock.patch.object(
                agy_runtime.time, "time", return_value=GEMINI_FLASH_PRICE_UPDATE_AT - 86_400
            ):
                before = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value
            with mock.patch.object(
                agy_runtime.time, "time", return_value=GEMINI_FLASH_PRICE_UPDATE_AT
            ):
                after = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value

        self.assertAlmostEqual(before, 0.75)
        self.assertAlmostEqual(after, 1.50)


    def test_cached_summary_picks_up_a_project_recorded_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_transcript(root)
            meter._agy_native_adapters.clear()
            with mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "brain")), \
                    mock.patch.object(meter, "_summary_cache", {}):
                before = meter.session_summary(meter.agy_session_sources()[0])
                self._write_summaries(root, [("conv-root-1", ["file:///work/recorded-later"])])
                after = meter.session_summary(meter.agy_session_sources()[0])

        self.assertEqual(before["project"], "")
        self.assertIn("recorded-later", after["project"])

    def test_trash_helper_refuses_antigravity_even_without_the_server_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = self._write_transcript(root)
            meter._agy_native_adapters.clear()
            mover = mock.Mock()
            with mock.patch.object(meter, "AGY_BRAIN_DIR", str(root / "brain")):
                result = meter.trash_session_log(
                    "conv-root-1", sources=meter.agy_session_sources(),
                    trash_dir=str(root / "trash"), mover=mover,
                )
            still_there = transcript.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        mover.assert_not_called()
        self.assertTrue(still_there)

    def test_tool_ids_use_validated_step_indexes_and_bounded_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "brain" / "odd-steps" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)
            rows = [
                {"type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z", "content": "go"},
                {
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-10-22T20:00:05Z",
                    "step_index": "STEP_INDEX_CANARY",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "tool_calls": [{"name": "x" * 500, "args": {}}],
                },
            ]
            (log_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n"
            )
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._agy_compatibility()
            state = adapter.recompute_legacy(adapter.discover_legacy(DiscoveryContext(str(root)))[0])

        tool = state["executions"][0]["tools"][0]
        self.assertEqual(tool["id"], "step-1-tool-1")
        self.assertNotIn("STEP_INDEX_CANARY", repr(state))
        self.assertLessEqual(len(tool["name"]), 64)

    def test_a_conversation_with_both_transcript_layouts_is_listed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self._write_transcript(root)
            legacy = root / "brain" / "conv-root-1" / "transcript.jsonl"
            legacy.write_text(current.read_text())
            adapter = self.fixture_adapter(root)
            sources = adapter.discover_legacy(DiscoveryContext(str(root)))

        self.assertEqual([row["id"] for row in sources], ["conv-root-1"])
        self.assertEqual(sources[0]["path"], str(current))


    def test_unrecognized_selection_makes_later_requests_unpriced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "brain" / "switch" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)

            def selection(label):
                return {
                    "type": "USER_INPUT",
                    "created_at": "2026-10-22T20:00:00Z",
                    "content": (
                        "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` "
                        "from Gemini 3.6 Flash to {}.\n</USER_SETTINGS_CHANGE>".format(label)
                    ),
                }

            planner = {
                "type": "PLANNER_RESPONSE", "created_at": "2026-10-22T20:00:05Z",
                "input_tokens": 1000, "output_tokens": 10,
            }
            rows = [selection("Gemini 2.5 Pro"), planner, selection("Gemini 5 Helium"), planner]
            (log_dir / "transcript.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n"
            )
            settings = str(root / "settings.json")
            adapter = AgyRuntimeAdapter(
                root / "brain", quote_resolver=lambda query: meter.price_quote(query, path=settings),
            )
            parsed = adapter._parsed(str(log_dir / "transcript.jsonl"))

        self.assertEqual([turn["model"] for turn in parsed["turns"]], ["gemini-2.5-pro", "unknown-model"])
        self.assertIsNotNone(parsed["turns"][0]["cost"])
        self.assertIsNone(parsed["turns"][1]["cost"])


    def _write_rows(self, root, conv_id, rows, legacy=False):
        log_dir = root / "brain" / conv_id
        if not legacy:
            log_dir = log_dir / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "transcript.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_requests_before_the_first_selection_use_its_from_model(self):
        def selection(previous, chosen):
            return {
                "type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z",
                "content": (
                    "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` "
                    "from {} to {}.\n</USER_SETTINGS_CHANGE>".format(previous, chosen)
                ),
            }

        planner = {
            "type": "PLANNER_RESPONSE", "created_at": "2026-10-22T20:00:05Z",
            "input_tokens": 1_000_000, "output_tokens": 0,
        }
        user = {"type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z", "content": "hi"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            known = self._write_rows(root, "known-from", [
                user, planner, selection("Gemini 2.5 Flash", "Gemini 2.5 Pro"), planner,
            ])
            unknown = self._write_rows(root, "unknown-from", [
                user, planner, selection("A private label", "Gemini 2.5 Pro"), planner,
            ])
            settings = str(root / "settings.json")
            adapter = AgyRuntimeAdapter(
                root / "brain", quote_resolver=lambda query: meter.price_quote(query, path=settings),
            )
            known_turns = adapter._parsed(str(known), fallback_model="gemini-2.5-pro")["turns"]
            unknown_turns = adapter._parsed(str(unknown), fallback_model="gemini-2.5-pro")["turns"]

        self.assertEqual([turn["model"] for turn in known_turns], ["gemini-2.5-flash", "gemini-2.5-pro"])
        self.assertAlmostEqual(sum(known_turns[0]["cost"].values()), 0.30)
        self.assertEqual(unknown_turns[0]["model"], "unknown-model")
        self.assertIsNone(unknown_turns[0]["cost"])

    def test_an_empty_current_layout_falls_back_to_the_legacy_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_rows(root, "migrated", [])
            legacy = self._write_rows(root, "migrated", [
                {"type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z", "content": "hi"},
                {"type": "PLANNER_RESPONSE", "created_at": "2026-10-22T20:00:05Z",
                 "input_tokens": 10, "output_tokens": 5},
            ], legacy=True)
            adapter = self.fixture_adapter(root)
            sources = adapter.discover_legacy(DiscoveryContext(str(root)))

        self.assertEqual([row["id"] for row in sources], ["migrated"])
        self.assertEqual(sources[0]["path"], str(legacy))

    def test_step_index_zero_is_kept_and_oversized_values_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def planner(step_index):
                return {
                    "type": "PLANNER_RESPONSE", "created_at": "2026-10-22T20:00:05Z",
                    "step_index": step_index, "input_tokens": 10, "output_tokens": 5,
                    "tool_calls": [{"name": "read_file", "args": {}}],
                }

            path = self._write_rows(root, "steps", [
                {"type": "USER_INPUT", "created_at": "2026-10-22T20:00:00Z", "content": "hi"},
                planner(0), planner(10 ** 300),
            ])
            turns = self.fixture_adapter(root)._parsed(str(path))["turns"]

        self.assertEqual([turn["tools"][0]["id"] for turn in turns], ["step-0-tool-1", "step-2-tool-1"])


    def test_model_selection_parsing_stays_fast_on_crafted_prompt_text(self):
        def block(body):
            return "<USER_SETTINGS_CHANGE>" + body + "</USER_SETTINGS_CHANGE>"

        # Each block fits under MAX_SETTINGS_BLOCK_CHARS, so it reaches the parser.
        spaces_after_to = block("changed setting `Model Selection` from a to" + " " * 4_000 + "`")
        crafted = (
            spaces_after_to * agy_runtime.MAX_SETTINGS_BLOCKS,
            block("changed setting `Model Selection` from" + " " * 4_000 + "`"),
            block("changed setting `Model Selection` from x " + "to y " * 800),
            block("changed setting `Model Selection` from " * 100),
        )
        self.assertEqual(
            len(agy_runtime._settings_blocks(crafted[0])), agy_runtime.MAX_SETTINGS_BLOCKS,
        )
        for text in crafted:
            with self.subTest(length=len(text)):
                started = time.perf_counter()
                agy_runtime._extract_model_and_effort(text)
                agy_runtime._initial_selected_model([{"type": "USER_INPUT", "content": text}])
                self.assertLess(time.perf_counter() - started, 0.5)

        started = time.perf_counter()
        agy_runtime._normalize_model("(" * 40_000)
        self.assertLess(time.perf_counter() - started, 0.5)

        normal = (
            "noise " * 20_000 + "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` "
            "from Gemini 2.5 Flash to Gemini 2.5 Pro.\n</USER_SETTINGS_CHANGE>"
        )
        self.assertEqual(agy_runtime._extract_model_and_effort(normal), ("gemini-2.5-pro", ""))
        self.assertEqual(
            agy_runtime._initial_selected_model([{"type": "USER_INPUT", "content": normal}]),
            "gemini-2.5-flash",
        )

    def test_selection_tags_are_case_insensitive_and_to_is_a_whole_word(self):
        lowercase = (
            "<user_settings_change>The user changed setting `Model Selection` from "
            "Gemini 2.5 Flash to Gemini 2.5 Pro.</user_settings_change>"
        )
        auto = (
            "<USER_SETTINGS_CHANGE>The user changed setting `Model Selection` from "
            "Auto to Gemini 3.8 Flash (High). No need to comment.</USER_SETTINGS_CHANGE>"
        )

        self.assertEqual(agy_runtime._extract_model_and_effort(lowercase), ("gemini-2.5-pro", ""))
        self.assertEqual(agy_runtime._extract_model_and_effort(auto), ("gemini-3.8-flash", "high"))
        self.assertEqual(
            agy_runtime._initial_selected_model([{"type": "USER_INPUT", "content": auto}]),
            "unknown-model",
        )

if __name__ == "__main__":
    unittest.main()
