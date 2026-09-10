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
    session_source_public_dict,
)
from token_meter.runtimes.grok import GrokRuntimeAdapter, COST_TICKS_PER_USD


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "grok"
ROOT = Path(__file__).resolve().parents[2]


class GrokRuntimeAdapterTests(unittest.TestCase):
    def _home(self, tmp):
        dest = Path(tmp) / ".grok"
        shutil.copytree(FIXTURES, dest)
        return dest

    def test_normalizes_measured_tokens_and_grok_recorded_cost_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = GrokRuntimeAdapter(self._home(tmp))
            source = adapter.discover(DiscoveryContext(home=tmp))[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.runtime_id, "grok")
        self.assertIn("quota", GrokRuntimeAdapter.descriptor.capabilities)
        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(source.display_label, "Grok")
        self.assertEqual(source.model_ref.provider_id, "xai")
        self.assertEqual(source.model_ref.model_id, "grok-4.6")
        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.output_tokens.value, 20)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 10)
        self.assertEqual(loaded.usage.cache_write_tokens.value, 5)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 1500000000 / COST_TICKS_PER_USD)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual([(tool.name, tool.status) for tool in loaded.tools], [("read_file", "success")])
        encoded = json.dumps(session_source_public_dict(source)) + repr(loaded)
        self.assertNotIn("private grok prompt", encoded)
        self.assertNotIn("private grok response", encoded)
        self.assertNotIn("private grok title from prompt", encoded)
        self.assertNotIn("private grok summary of the user task", encoded)
        self.assertNotIn("locator", session_source_public_dict(source))

    def test_session_totals_stay_measured_when_extra_event_turns_have_no_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            events = home / "sessions" / "--repo--" / "session-1" / "events.jsonl"
            events.write_text(
                events.read_text(encoding="utf-8")
                + json.dumps({
                    "ts": "2026-09-04T10:02:00Z", "type": "turn_started",
                    "turn_number": 2, "model_id": "grok-4.6",
                }) + "\n"
                + json.dumps({"ts": "2026-09-04T10:02:01Z", "type": "turn_ended"}) + "\n",
                encoding="utf-8",
            )
            adapter = GrokRuntimeAdapter(home)
            source = adapter.discover(DiscoveryContext(home=tmp))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.output_tokens.value, 20)
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.MEASURED)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 1500000000 / COST_TICKS_PER_USD)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)

    def test_missing_usage_keeps_the_session_with_unavailable_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            (home / "sessions" / "--repo--" / "session-1" / "usage.json").unlink()
            adapter = GrokRuntimeAdapter(home)
            source = adapter.discover(DiscoveryContext(home=tmp))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.session_id, "session-1")
        self.assertEqual(loaded.usage.input_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual([tool.name for tool in loaded.tools], ["read_file"])

    def test_ignores_sessions_outside_the_owned_grok_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = GrokRuntimeAdapter(Path(tmp) / "empty-home")
            self.assertEqual(adapter.discover(DiscoveryContext(home=tmp)), ())

    def test_ignores_short_session_ids_and_never_opens_chat_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            short = home / "sessions" / "--repo--" / "short"
            short.mkdir()
            (short / "summary.json").write_text(
                json.dumps({"info": {"id": "short", "cwd": "/repo"}}),
                encoding="utf-8",
            )
            adapter = GrokRuntimeAdapter(home)
            discovered = adapter.discover(DiscoveryContext(home=tmp))
            loaded = adapter.load(discovered[0], DetailLevel.SUMMARY)

        self.assertEqual([source.session_id for source in discovered], ["session-1"])
        self.assertEqual(loaded.source.session_id, "session-1")

    def test_denies_deletion_and_server_delete_before_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = GrokRuntimeAdapter(self._home(tmp))
            source = adapter.discover(DiscoveryContext(home=tmp))[0]
            plan = adapter.deletion_plan(source)

        self.assertEqual(plan.disposition.value, "deny")
        self.assertIn("grok", meter.session_action_capability()["read_only_providers"])
        with mock.patch.object(meter, "find_session", return_value={
            "provider": "grok", "id": "session-1",
        }), mock.patch.object(meter, "trash_session_log") as trash:
            result = meter.request_session_delete("session-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        self.assertIn("Grok", result["error"])
        trash.assert_not_called()


class GrokDocumentationTests(unittest.TestCase):
    def test_docs_explain_grok_evidence_and_privacy_boundaries(self):
        readme = (ROOT / "README.md").read_text()
        guide = (ROOT / "specs" / "USER_GUIDE.md").read_text()
        architecture = (ROOT / "specs" / "ARCHITECTURE.md").read_text()

        self.assertIn("Grok Build", readme)
        self.assertIn("GROK_HOME", guide)
        self.assertIn("Grok Build sessions", guide)
        self.assertIn("chat_history.jsonl", guide)
        self.assertIn("Grok adapter", architecture)
        self.assertIn("usage.json", architecture)