import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DetailLevel, DiscoveryContext, EvidenceBasis, session_source_public_dict
from token_meter.domain import redaction
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

    def test_summary_carries_the_latest_turn_cache_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp)
            _box, row = self._summarize_with_work(tmp)

        window = row["_cache_window"]
        self.assertEqual(window["basis"], "documented")
        self.assertEqual(window["source"], "anthropic")
        self.assertEqual(window["prefix_tokens"], 100 + 10 + 5)
        self.assertTrue(window["cache_observed"])

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

    def test_redaction_gate_keeps_prose_that_merely_contains_an_at_sign(self):
        # Pi's old matcher matched a version or host pattern against the whole
        # remainder of the string after every "@", so any request whose tail
        # happened to look like a version was dropped whole. The shared
        # token-bounded matcher only rejects a host immediately after the "@",
        # which keeps these ordinary requests and, because the gate never
        # rewrites, reaches the queue verbatim.
        kept = (
            ("scoped_package", "npm install -g @opengsd/gsd-pi@latest"),
            ("mid_sentence_namespace", "try @cf/meta/llama-3.1-8b instead of the current model"),
            ("package_version", "bump pkg@1.2.3 in package.json"),
            ("type_namespace", "add @types/node to the dev dependencies"),
            ("bare_at_sign", "the @ symbol in the readme needs an escape"),
        )
        for label, text in kept:
            with self.subTest(kind=label), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual([turn["text"] for turn in box["turns"]], [text])

    def test_redaction_gate_keeps_a_benign_request_long_enough_to_compact(self):
        # The gate runs before the cap and the compaction, so letting a benign
        # request through must not change how it is compacted: the bounded
        # 90-character preview is what the classifier receives.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="word " * 400 + "@types/node")
            box, _row = self._summarize_with_work(tmp)

        preview = box["turns"][0]["text"]
        self.assertEqual(len(preview), 90)
        self.assertTrue(preview.endswith("…"))

    def test_redaction_gate_still_drops_an_account_address_inside_prose(self):
        # Bounding the match must not weaken it: a user@host address is still
        # dropped whole, even mid-sentence, and its text never reaches the
        # serialized capture. Only obviously fake recipients are used.
        cases = {
            "domain": ("email the report to bob@corp.example.com", "bob@corp.example.com"),
            "dotted_three_label": ("the deploy bot is ci@build01.example.com", "ci@build01.example.com"),
            "ip_and_port": ("the mirror is ci@10.0.0.5:8080", "ci@10.0.0.5:8080"),
            "localhost": ("ping ci@localhost and retry", "ci@localhost"),
        }
        for label, (text, marker) in cases.items():
            with self.subTest(kind=label), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual(box["turns"], [])
                self.assertNotIn(marker, json.dumps(box, default=str))

    def test_redaction_gate_runs_the_shared_credential_and_host_predicates(self):
        # The credential and host checks are the shared predicates the OpenCode
        # adapter runs, so key material and host evidence cannot diverge between
        # the two adapters. Identity, not just name, is what pins that: a local
        # copy or a re-implementation would break this assertion.
        self.assertIs(pi_runtime._has_secret_token, redaction.has_secret_token)
        self.assertIs(pi_runtime._is_host_like, redaction.is_host_like)
        # The request-text gate's account check is that same shared matcher, not
        # a local one. The model-id path keeps its own explicitly named rule
        # (``_model_id_has_account_at``), which is not this request-text gate.
        self.assertIs(pi_runtime.has_account_at, redaction.has_account_at)
        self.assertFalse(hasattr(pi_runtime, "_has_account_at"))

    def test_redaction_gate_still_drops_a_credential_and_a_host(self):
        # Unchanged by bounding the account check: obviously fake key material
        # and host evidence are both still dropped whole, and never appear in
        # the capture.
        secret = "sk-" + "F" * 32
        cases = {
            "credential": "rotate " + secret + " before you push",
            "host_and_port": "10.0.0.5:11434",
            "bare_host": "ollama.example.com",
            "single_label_with_port": "gateway:8080",
        }
        for label, text in cases.items():
            with self.subTest(kind=label), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual(box["turns"], [])
                self.assertNotIn(text, json.dumps(box, default=str))

    def test_subagent_model_ids_reject_account_shapes_and_keep_model_ids(self):
        # The model-id path keeps an explicit allowlist of its own, so a child
        # run must never publish an account address as a model identity, while
        # the version, tag, and namespace shapes real providers emit must
        # survive. Credential and host shapes are still dropped by the shared
        # predicates, which this path runs unchanged.
        rejected = (
            "bob@corp.example.com",
            "ci@build01.example.com",
            "ci@10.0.0.5:8080",
            "provider/host.example.com:8443/model",
        )
        for value in rejected:
            with self.subTest(kind="rejected"):
                self.assertEqual(pi_runtime._subagent_model_id(value), "")

        kept = (
            "model@latest",
            "gemini-2.0-flash@001",
            "llama3.1:8b",
            "org@1.2.3",
            "@cf/meta/llama-3.1-8b",
        )
        for value in kept:
            with self.subTest(kind="kept"):
                self.assertEqual(pi_runtime._subagent_model_id(value), value)

    def test_subagent_model_id_treats_a_bare_word_after_at_as_an_account(self):
        # A model id's "@" introduces a version or a tag, never an account, so
        # a bare word such as "user@host" is rejected. This is the shipped
        # sanitizer table's privacy invariant: see
        # test_meter.PiSubagentTests.test_child_model_sanitizer_table.
        rejected = (
            "user@host",
            "user@internal",
            "alice@mail",
            "qwen@beta",
        )
        for value in rejected:
            with self.subTest(kind="rejected"):
                self.assertEqual(pi_runtime._subagent_model_id(value), "")

        kept = (
            "model@latest",
            "model@default",
            "model@v2.1.0",
            "model@1.2",
            "model@v1",
            "claude-opus-4@20250514",
            "claude-sonnet-4@20250514",
            "@cf/meta/llama-3.1-8b",
        )
        for value in kept:
            with self.subTest(kind="kept"):
                self.assertEqual(pi_runtime._subagent_model_id(value), value)

    def test_model_id_and_request_text_paths_apply_different_at_rules(self):
        # The two paths are deliberately different rules. A bare word after "@"
        # is an account in a model id ("user@host"), but a benign tag or scope
        # in prose, so the request-text gate keeps the shapes 0c60fc8 recovered
        # while the model-id path rejects the same shape. A real account
        # address is dropped by both.
        for value in ("user@host", "qwen@beta", "bob@corp.example.com"):
            with self.subTest(kind="model-id-rejected"):
                self.assertEqual(pi_runtime._subagent_model_id(value), "")

        for text in (
            "npm install -g @opengsd/gsd-pi@latest",
            "try @cf/meta/llama-3.1-8b instead of the current model",
        ):
            with self.subTest(kind="text-kept"), tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, user_text=text)
                box, _row = self._summarize_with_work(tmp)
                self.assertEqual([turn["text"] for turn in box["turns"]], [text])

        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(tmp, user_text="email the report to bob@corp.example.com")
            box, _row = self._summarize_with_work(tmp)
        self.assertEqual(box["turns"], [])

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
