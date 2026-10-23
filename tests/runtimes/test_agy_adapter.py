import json
import sqlite3
import tempfile
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
                "preview TEXT, raw_summary TEXT, workspace_uris TEXT)"
            )
            con.executemany(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                [
                    (conv_id, "SECRET_TITLE_CANARY", "SECRET_PREVIEW_CANARY",
                     "SECRET_SUMMARY_CANARY", json.dumps(uris) if uris is not None else None)
                    for conv_id, uris in rows
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
            transcript = self._write_transcript(root)
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
        self.assertEqual(plan.disposition.value, "trash")
        self.assertIn(transcript.resolve(), [Path(loc.value).resolve() for loc in plan.targets])


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


if __name__ == "__main__":
    unittest.main()
