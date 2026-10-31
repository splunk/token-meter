import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import (
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    PriceQuote,
)
from token_meter.runtimes.kiro import KiroRuntimeAdapter
from tests.runtime_projection_privacy import assert_runtime_trace_privacy


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "kiro"


def priced_quote(query):
    if query.model.provider_id != "anthropic":
        return PriceQuote.unavailable(query.model)
    return PriceQuote(
        query.model, 3.0, 15.0, 0.30, 3.75,
        EvidenceBasis.MEASURED, "claude-sonnet-4-6",
    )


class KiroRuntimeAdapterTests(unittest.TestCase):
    def fixture_adapter(self, root):
        return KiroRuntimeAdapter(
            root / "sessions",
            root / "agent-storage",
            project_resolver=lambda value: value.replace(str(root), "[root]"),
            quote_resolver=priced_quote,
        )

    def install_message_fixture(self, root):
        session_dir = root / "sessions" / "workspace-a" / "session-a"
        session_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURE_ROOT / "messages.jsonl", session_dir / "messages.jsonl")
        shutil.copyfile(FIXTURE_ROOT / "session.json", session_dir / "session.json")
        return session_dir

    def install_cli_fixture(self, root):
        cli_dir = root / "sessions" / "cli"
        cli_dir.mkdir(parents=True)
        trace = cli_dir / "kiro-cli-session.jsonl"
        shutil.copyfile(FIXTURE_ROOT / "cli-messages.jsonl", trace)
        return trace

    def test_discovers_metadata_with_runtime_and_model_provider_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.install_message_fixture(root)
            sources = self.fixture_adapter(root).discover(DiscoveryContext(str(root)))

        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.runtime_id, "kiro")
        self.assertEqual(source.session_id, "kiro-session-1")
        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(source.model_ref.model_id, "claude-sonnet-4-6")
        self.assertNotEqual(source.runtime_id, source.model_ref.provider_id)

    def test_safe_metadata_cache_invalidates_when_session_metadata_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = self.install_message_fixture(root)
            adapter = self.fixture_adapter(root)
            first = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            metadata = json.loads((session_dir / "session.json").read_text())
            metadata["title"] = "Changed safe title"
            (session_dir / "session.json").write_text(json.dumps(metadata))
            second = adapter.discover_legacy(DiscoveryContext(str(root)))[0]

        self.assertEqual(first["title"], "Sanitized Kiro session")
        self.assertEqual(second["title"], "Changed safe title")

    def test_normalized_load_is_estimated_bounded_and_content_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.install_message_fixture(root)
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            session = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(session.usage.input_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(session.usage.output_tokens.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(session.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertGreater(session.usage.input_tokens.value, 0)
        self.assertGreater(session.usage.output_tokens.value, 0)
        self.assertEqual([(tool.name, tool.category) for tool in session.tools], [
            ("read_file", "filesystem"),
        ])
        encoded = repr(session)
        for private in ("sanitized user text", "sanitized assistant text",
                        "sanitized tool result"):
            self.assertNotIn(private, encoded)

    def test_mcp_trace_views_are_structural_and_content_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.install_message_fixture(root)
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._kiro_compatibility()
            source = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.load(source, DetailLevel.FULL)

            assert_runtime_trace_privacy(
                self, source, state, runtime="kiro",
                model="claude-sonnet-4-6", tool="read_file",
                native_types=("user", "tool_call", "assistant"),
                forbidden=(
                    "sanitized user text", "sanitized assistant text",
                    "sanitized tool result", str(root),
                ),
            )

    def test_cli_kind_data_schema_reaches_cross_session_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.install_cli_fixture(root)
            meter._kiro_native_adapters.clear()
            saved_cross = dict(meter._xsess)
            try:
                with mock.patch.object(meter, "KIRO_SESSIONS", str(root / "sessions")), \
                        mock.patch.object(meter, "KIRO_AGENT_STORAGE", str(root / "agent-storage")):
                    source = meter.kiro_session_sources()[0]
                    state = meter.recompute(source)
                    summary = meter.session_summary(source)
                    meter._xsess.update({"data": None, "at": 0.0, "sessions": []})
                    cross = meter.cross_session(sources=[source])
            finally:
                meter._xsess.clear()
                meter._xsess.update(saved_cross)

        self.assertEqual(source["client"], "kiro_cli")
        self.assertEqual(summary["turns"], 2)
        self.assertGreater(summary["input_tokens"], 0)
        self.assertGreater(summary["output_tokens"], 0)
        self.assertEqual([row["id"] for row in cross["sessions"]], [source["id"]])
        self.assertEqual(state["turns"], 2)
        self.assertEqual(state["tokens"]["input"], summary["input_tokens"])
        self.assertEqual(state["tokens"]["output"], summary["output_tokens"])
        encoded = json.dumps({"state": state, "summary": summary}, default=str)
        for private in (
            "sanitized CLI user text", "sanitized CLI assistant text",
            "sanitized CLI follow-up", "sanitized CLI completion",
        ):
            self.assertNotIn(private, encoded)

    def test_corrupt_partial_trace_fails_bounded_without_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "sessions" / "workspace" / "partial"
            session_dir.mkdir(parents=True)
            (session_dir / "messages.jsonl").write_text("{broken\n{}\n")
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            session = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(session.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(session.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIn("corrupt_rows", {warning.code for warning in session.warnings})

    def test_agent_execution_storage_is_discovered_without_exposing_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            execution_dir = root / "agent-storage" / "workspace-b" / "session-b"
            execution_dir.mkdir(parents=True)
            (execution_dir / "execution-1").write_text(json.dumps({
                "executionId": "exec-1", "status": "succeed",
                "startTime": 1785578400000, "endTime": 1785578404000,
                "input": {"data": {
                    "modelId": "claude-haiku-4.5",
                    "workspacePaths": ["/private/agent-workspace"],
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": "PRIVATE_AGENT_PROMPT"}
                    ]}],
                }},
                "actions": [{
                    "actionType": "runCommand", "actionId": "action-1",
                    "actionState": "Success",
                    "input": {"command": "PRIVATE_COMMAND"},
                    "output": {"text": "PRIVATE_OUTPUT"},
                }],
            }))
            adapter = self.fixture_adapter(root)
            source = adapter.discover(DiscoveryContext(str(root)))[0]
            session = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(session.tools[0].category, "shell")
        for private in ("PRIVATE_AGENT_PROMPT", "PRIVATE_COMMAND", "PRIVATE_OUTPUT"):
            self.assertNotIn(private, repr(session))

    def test_legacy_state_and_summary_use_the_registered_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.install_message_fixture(root)
            meter._kiro_native_adapters.clear()
            with mock.patch.object(meter, "KIRO_SESSIONS", str(root / "sessions")), \
                    mock.patch.object(meter, "KIRO_AGENT_STORAGE", str(root / "agent-storage")):
                source = meter.kiro_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.session_summary(source)

        self.assertEqual(state["provider"], "kiro")
        self.assertTrue(state["token_estimate"])
        self.assertEqual(state["primary_model"], "claude-sonnet-4-6")
        self.assertTrue(state["availability"]["cost"])
        self.assertEqual(summary["provider"], "kiro")
        self.assertTrue(summary["token_estimate"])
        self.assertGreater(summary["tokens"], 0)
        # Kiro reports no cache use: the provider-documented window, with no prefix or price.
        window = summary["_cache_window"]
        self.assertEqual((window["basis"], window["window_s"]), ("documented", 300))
        self.assertIsNone(window["prefix_tokens"])
        self.assertIsNone(window["cold_cost_delta"])
        self.assertFalse(window["cache_reported"])
        encoded = json.dumps({"state": state, "summary": summary}, default=str)
        for private in ("sanitized user text", "sanitized assistant text",
                        "sanitized tool result"):
            self.assertNotIn(private, encoded)

    def test_mixed_pricing_preserves_priced_model_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = self.fixture_adapter(root)
            adapter.compatibility = meter._kiro_compatibility()
            turns = [
                {"input_tokens": 100, "output_tokens": 20,
                 "start": 1_784_548_800, "end": 1_784_548_801, "tools": []},
                {"input_tokens": 100, "output_tokens": 30,
                 "start": 1_784_548_802, "end": 1_784_548_803, "tools": []},
            ]
            priced = mock.Mock(model_id="claude-sonnet-4-6")
            unknown = mock.Mock(model_id="unknown-model")
            with mock.patch.object(adapter, "_legacy_rows", return_value=turns), \
                    mock.patch.object(adapter, "_legacy_turn", side_effect=[
                        (priced, {"input": 0.04, "output": 0.06}, True),
                        (unknown, {}, False),
                    ]):
                row = adapter.summarize_legacy({
                    "id": "kiro-mixed", "provider": "kiro", "client": "kiro",
                    "runtime": "Kiro", "label": "Kiro", "title": "Mixed",
                    "mtime": 1_784_548_803, "path": str(root / "mixed.jsonl"),
                })

        self.assertFalse(row["availability"]["cost"])
        stats = {item["model"]: item for item in row["model_stats"]}
        self.assertTrue(stats["claude-sonnet-4-6"]["availability"]["cost"])
        self.assertEqual(stats["claude-sonnet-4-6"]["cost_covered_executions"], 1)
        self.assertEqual(stats["claude-sonnet-4-6"]["cost_covered_output_tokens"], 20)
        self.assertAlmostEqual(stats["claude-sonnet-4-6"]["cost_covered_cost"], 0.1)
        self.assertFalse(stats["unknown-model"]["availability"]["cost"])

        daily = {item["model"]: item for item in row["_model_daily"]}
        self.assertTrue(daily["claude-sonnet-4-6"]["availability"]["cost"])
        self.assertFalse(daily["unknown-model"]["availability"]["cost"])

        aggregate = {
            item["model"]: item for item in meter.aggregate_model_stats([row])["models"]
        }
        self.assertTrue(aggregate["claude-sonnet-4-6"]["availability"]["cost"])
        self.assertEqual(
            aggregate["claude-sonnet-4-6"]["cost_covered_output_tokens"], 20,
        )
        self.assertAlmostEqual(
            aggregate["claude-sonnet-4-6"]["cost_covered_cost"], 0.1,
        )
        self.assertFalse(aggregate["unknown-model"]["availability"]["cost"])


if __name__ == "__main__":
    unittest.main()
