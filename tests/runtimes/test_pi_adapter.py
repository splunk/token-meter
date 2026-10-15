import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis, session_source_public_dict
from token_meter.runtimes import pi as pi_runtime
from token_meter.runtimes.pi import PiRuntimeAdapter


class PiRuntimeAdapterTests(unittest.TestCase):
    def _write_session(self, agent_dir, *, with_cost=True, provider="anthropic",
                       model="claude-test", child_usage=None, user_text=None):
        path = Path(agent_dir) / "sessions" / "--repo--" / "pi-session.jsonl"
        path.parent.mkdir(parents=True)
        usage = {
            "input": 100, "output": 20, "cacheRead": 10,
            "cacheWrite": 5, "totalTokens": 135,
        }
        if with_cost:
            usage["cost"] = {
                "input": 0.001, "output": 0.002,
                "cacheRead": 0.0001, "cacheWrite": 0.0002,
            }
        content = [{"type": "toolCall", "id": "call", "name": "read",
                    "arguments": {}}]
        if child_usage is not None:
            content.append({
                "type": "toolCall", "id": "call-subagent", "name": "subagent",
                "arguments": {"agent": "probe", "task": "CHILD-TASK-CANARY"},
            })
        user_content = (
            [{"type": "text", "text": user_text}] if user_text is not None else []
        )
        rows = [
            {"type": "session", "id": "pi-session", "cwd": "/repo"},
            {"type": "model_change", "provider": provider, "modelId": model},
            {"type": "message", "timestamp": "2026-09-04T10:00:00Z",
             "message": {"role": "user", "content": user_content}},
            {"type": "message", "timestamp": "2026-09-04T10:00:03Z", "message": {
                "role": "assistant", "provider": provider, "model": model,
                "content": content,
                "usage": usage,
            }},
            {"type": "message", "timestamp": "2026-09-04T10:00:04Z",
             "message": {"role": "toolResult", "toolCallId": "call", "toolName": "read"}},
        ]
        if child_usage is not None:
            details = {
                "mode": "single", "agentScope": "user", "projectAgentsDir": None,
                "results": [{
                    "agent": "probe", "exitCode": 0, "stopReason": "stop",
                    "model": "opencode-go/deepseek-v4.1-flash",
                    "messages": [{"role": "assistant", "content": [
                        {"type": "text", "text": "CHILD-MESSAGE-CANARY"}]}],
                    "stderr": "CHILD-STDERR-CANARY",
                }],
            }
            if isinstance(child_usage, dict):
                details["results"][0]["usage"] = child_usage
            rows.append({
                "type": "message", "timestamp": "2026-09-04T10:00:06Z",
                "message": {
                    "role": "toolResult", "toolCallId": "call-subagent",
                    "toolName": "subagent",
                    "content": [{"type": "text", "text": "CHILD-CONTENT-CANARY"}],
                    "details": details,
                },
            })
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def test_normalizes_measured_tokens_and_estimated_local_cost_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(source.model_ref.provider_id, "anthropic")
        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.output_tokens.value, 20)
        self.assertEqual(loaded.usage.cache_read_tokens.value, 10)
        self.assertEqual(loaded.usage.cache_write_tokens.value, 5)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.0033)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual([(tool.name, tool.status) for tool in loaded.tools], [("read", "success")])
        self.assertNotIn("locator", session_source_public_dict(source))
        self.assertNotIn("arguments", repr(loaded))

    def test_missing_pi_cost_is_unavailable_without_changing_measured_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, with_cost=False)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.input_tokens.value, 100)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cost_usd.value)
        self.assertEqual(loaded.turns, ())

    def test_native_load_includes_child_spend_without_child_content(self):
        # The shipped subagent tool reports cost as a scalar.
        child = {
            "input": 500, "output": 50, "cacheRead": 0, "cacheWrite": 0,
            "cost": 0.25, "turns": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, child_usage=child)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.FULL)

        self.assertEqual(loaded.usage.input_tokens.value, 600)
        self.assertEqual(loaded.usage.output_tokens.value, 70)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.2533)
        encoded = repr(loaded)
        self.assertNotIn("CHILD-TASK-CANARY", encoded)
        self.assertNotIn("CHILD-MESSAGE-CANARY", encoded)
        self.assertNotIn("CHILD-STDERR-CANARY", encoded)
        self.assertNotIn("CHILD-CONTENT-CANARY", encoded)

    def test_native_load_keeps_unreported_child_spend_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, child_usage="unreported")
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertIsNone(loaded.usage.input_tokens.value)
        self.assertIsNone(loaded.usage.output_tokens.value)
        self.assertIsNone(loaded.usage.cost_usd.value)

    def test_redacts_account_bearing_bedrock_profile_from_native_model_identity(self):
        raw_profile = (
            "arn:aws:bedrock:us-west-2:123456789012:"
            "application-inference-profile/private-profile"
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, provider="amazon-bedrock", model=raw_profile)
            adapter = PiRuntimeAdapter(tmp)
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            loaded = adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(source.model_ref.provider_id, "amazon")
        self.assertEqual(source.model_ref.model_id, "aws-bedrock-profile")
        self.assertNotIn(raw_profile, repr(source))
        self.assertNotIn("123456789012", repr(loaded))

    def _summarize_with_work(self, agent_dir, *, enabled=True):
        """Summarize one legacy Pi session with the Work request queue captured.

        Work insights is a user setting, so the feature is forced through the
        compatibility dictionary: the capture must describe the adapter, never
        this machine's settings file.
        """
        adapter = PiRuntimeAdapter(agent_dir)
        compat = meter._pi_compatibility()
        compat["work_insights_enabled"] = lambda: enabled
        box = {"turns": [], "events": [], "actions": []}

        def attach(row, turns, events=(), actions=()):
            box["turns"].extend(turns)
            box["events"].extend(events)
            box["actions"].extend(actions)
            return row
        compat["attach_work_requests"] = attach
        adapter.compatibility = compat
        source = adapter.discover_legacy(DiscoveryContext(home="/home/test"))[0]
        row = adapter.summarize_legacy(source)
        return box, row

    def test_typed_request_becomes_a_work_turn_with_ts_model_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="   please    fix the chart   ")
            box, _row = self._summarize_with_work(tmp)

        turns = box["turns"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "please fix the chart")
        self.assertEqual(turns[0]["model"], "claude-test")
        self.assertEqual(
            turns[0]["ts"], pi_runtime._timestamp("2026-09-04T10:00:00Z"),
        )
        # The priced assistant event rides along for the per-request cost slice.
        self.assertEqual(len(box["events"]), 1)

    def test_a_long_request_is_compacted_to_the_bounded_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="word " * 400)
            box, _row = self._summarize_with_work(tmp)

        preview = box["turns"][0]["text"]
        self.assertEqual(len(preview), 90)
        self.assertTrue(preview.endswith("…"))

    def test_redaction_gate_drops_a_request_carrying_secrets_hosts_or_accounts(self):
        # The same gate Pi applies to every value it surfaces: request text
        # bound for the Work classifier is dropped whole, never partially
        # scrubbed, when it trips a check. Each case trips one predicate.
        cases = {
            "credential": "rotate sk-abcdefghijklmnop123456 before you push",
            "host": "10.0.0.5:11434",
            "account": "email the report to bob@corp.example.com",
        }
        for label, text in cases.items():
            with self.subTest(kind=label), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual(box["turns"], [])
                self.assertNotIn(text, json.dumps(box, default=str))
        # A benign request still reaches the queue, so the gate is not blanket.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="fix the failing chart test")
            box, _row = self._summarize_with_work(tmp)
        self.assertEqual([turn["text"] for turn in box["turns"]],
                         ["fix the failing chart test"])

    def test_work_extraction_is_gated_on_the_feature_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="fix the chart")
            # No request text may even be read while Work insights is off.
            with mock.patch.object(
                    pi_runtime, "_work_request_text",
                    side_effect=AssertionError("gated extraction ran")):
                box, _row = self._summarize_with_work(tmp, enabled=False)

        self.assertEqual(box["turns"], [])
        self.assertEqual(box["events"], [])
        self.assertEqual(box["actions"], [])

    def test_a_session_without_a_typed_request_produces_no_work_turn(self):
        # No user message at all, and a user message whose blocks carry no text.
        for label, text in (("absent", None), ("blank", "")):
            with self.subTest(state=label), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual(box["turns"], [])

    def test_missing_empty_or_malformed_file_still_summarizes(self):
        source = {
            "provider": "pi", "id": "s", "session": "s.jsonl",
            "model": "claude-test", "project": "", "title": "Pi session",
            "label": "Pi", "mtime": 1,
        }
        for state in ("missing", "empty", "malformed"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "s.jsonl"
                if state == "empty":
                    path.write_text("")
                elif state == "malformed":
                    path.write_text("not json\n{\"type\": \"message\",\n\x00\n")
                adapter = PiRuntimeAdapter(tmp)
                compat = meter._pi_compatibility()
                compat["work_insights_enabled"] = lambda: True
                adapter.compatibility = compat
                row = adapter.summarize_legacy(dict(source, path=str(path)))
                self.assertIsInstance(row, dict)

    def test_pi_work_actions_are_content_free(self):
        turns = [{"start": 5.0, "end": 10.0, "tools": [
            {"name": "read", "category": "filesystem"},
            {"name": "apply_patch", "category": "filesystem"},
            {"name": "bash", "category": "shell"},
        ]}]
        actions = meter.pi_work_actions(turns)
        self.assertEqual([action["kind"] for action in actions],
                         ["read", "edit", "shell"])
        self.assertEqual([action["ts"] for action in actions], [10.0, 10.0, 10.0])
        self.assertTrue(all(action["paths"] == [] and action["commands"] == []
                            for action in actions))


if __name__ == "__main__":
    unittest.main()
