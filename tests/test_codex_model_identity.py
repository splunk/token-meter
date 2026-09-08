import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter


class CodexModelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trace = self.root / "rollout-auto-review.jsonl"
        self.trace.write_text('{"local":"trace"}\n', encoding="utf-8")
        self.identity_path = self.root / "session-model-identities.json"

    def tearDown(self):
        self.temp.cleanup()

    def source(self, model="unknown-model", **overrides):
        source = {
            "provider": "codex",
            "label": "Codex",
            "id": "logical-session-id",
            "session": self.trace.name,
            "path": str(self.trace),
            "project": "~/private-project",
            "mtime": self.trace.stat().st_mtime,
            "physical_trace_id": "physical-child-id",
            "parent_thread_id": "physical-parent-id",
            "observed_model": "codex-auto-review",
            "model": model,
            "model_provider": "openai",
        }
        source.update(overrides)
        return source

    def verified_evidence(self):
        mtime_ns, size = meter.file_signature(str(self.trace))
        return {
            "model": "gpt-5.6-sol",
            "model_provider": "openai",
            "revision": [str(mtime_ns), str(size)],
            "parent_id": "physical-parent-id",
            "inherited_prefix": 1,
        }

    def test_verified_parent_identity_survives_parent_disappearance_at_same_revision(self):
        source = self.source(model="gpt-5.6-sol")
        with mock.patch.object(
            meter, "codex_auto_review_evidence", return_value=self.verified_evidence(),
        ):
            live = meter.enrich_codex_model_identities(
                [source], path=str(self.identity_path),
            )

        self.assertEqual(live[0]["model"], "gpt-5.6-sol")
        self.assertEqual(live[0]["model_identity"]["source"], "verified_parent")
        self.assertEqual(live[0]["_verified_inherited_prefix"], 1)
        stored = json.loads(self.identity_path.read_text(encoding="utf-8"))
        encoded = json.dumps(stored)
        self.assertNotIn("physical-child-id", encoded)
        self.assertNotIn("physical-parent-id", encoded)
        self.assertNotIn("model_pricing", encoded)

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            retained = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )

        self.assertEqual(retained[0]["model"], "gpt-5.6-sol")
        self.assertEqual(retained[0]["model_identity"]["source"], "verified_history")
        self.assertEqual(retained[0]["_verified_inherited_prefix"], 1)

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            cyclic = meter.enrich_codex_model_identities(
                [self.source(model_resolution_reason="cyclic")],
                path=str(self.identity_path),
            )
        self.assertEqual(cyclic[0]["model"], "unknown-model")
        self.assertEqual(cyclic[0]["model_identity"]["source"], "unresolved")

    def test_verified_parent_identity_fails_closed_when_child_trace_changes(self):
        with mock.patch.object(
            meter, "codex_auto_review_evidence", return_value=self.verified_evidence(),
        ):
            meter.enrich_codex_model_identities(
                [self.source(model="gpt-5.6-sol")], path=str(self.identity_path),
            )
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('{"new":"local evidence"}\n')

        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            changed = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )

        self.assertEqual(changed[0]["model"], "unknown-model")
        self.assertEqual(changed[0]["model_identity"]["source"], "unresolved")
        self.assertTrue(changed[0]["model_identity"]["assignable"])

    def test_user_assignment_is_scoped_to_one_session_and_does_not_change_pricing(self):
        settings_path = self.root / "settings.json"
        settings_path.write_text(
            json.dumps({"model_pricing": {"codex": {"saved-custom": []}}}),
            encoding="utf-8",
        )
        before = settings_path.read_text(encoding="utf-8")
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            unresolved = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        key = unresolved[0]["model_identity"]["key"]

        assigned = meter.set_session_model_identity(
            key,
            model="gpt-5.6-sol",
            sources=unresolved,
            path=str(self.identity_path),
        )

        self.assertTrue(assigned["ok"])
        self.assertTrue(assigned["changed"])
        self.assertEqual(settings_path.read_text(encoding="utf-8"), before)
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            applied = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        self.assertEqual(applied[0]["model"], "gpt-5.6-sol")
        self.assertEqual(applied[0]["model_identity"]["source"], "user_assigned")

        removed = meter.set_session_model_identity(
            key,
            remove=True,
            sources=applied,
            path=str(self.identity_path),
        )
        self.assertTrue(removed["ok"])
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            cleared = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )
        self.assertEqual(cleared[0]["model"], "unknown-model")
        self.assertEqual(cleared[0]["model_identity"]["source"], "unresolved")

    def test_public_identity_projection_is_opaque_and_content_free(self):
        with mock.patch.object(meter, "codex_auto_review_evidence", return_value=None):
            source = meter.enrich_codex_model_identities(
                [self.source()], path=str(self.identity_path),
            )[0]
        row = meter.summary_row(
            source, "Safe title", 0.0, 0, 1, {"unknown-model"},
            None, None, {}, {}, {}, True,
        )
        identity = row["model_identity"]

        self.assertEqual(identity["kind"], "auto_review")
        self.assertEqual(identity["source"], "unresolved")
        self.assertTrue(identity["assignable"])
        self.assertRegex(identity["key"], r"^[a-f0-9]{64}$")
        encoded = json.dumps(identity)
        self.assertNotIn("physical-child-id", encoded)
        self.assertNotIn("physical-parent-id", encoded)
        self.assertNotIn(str(self.trace), encoded)

    def test_assignment_rejects_a_raw_trace_identity(self):
        result = meter.set_session_model_identity(
            "physical-child-id", model="gpt-5.6-sol", sources=[],
            path=str(self.identity_path),
        )

        self.assertFalse(result["ok"])

    def test_local_action_route_accepts_only_the_opaque_session_key(self):
        handler = object.__new__(meter.H)
        handler.path = "/settings/session-model-identity"
        handler.headers = {
            "Content-Type": "application/json",
            "Content-Length": "115",
            "X-Token-Meter-Action": meter._ACTION_TOKEN,
            "Origin": "http://127.0.0.1:8722",
        }
        key = "a" * 64
        body = json.dumps({
            "session_key": key,
            "model": "gpt-5.6-sol",
            "provider": "codex",
        }).encode("utf-8")
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler._send = mock.Mock()
        handler.send_error = mock.Mock()
        with (
            mock.patch.object(
                meter, "set_session_model_identity",
                return_value={"ok": True, "changed": True, "session_key": key},
            ) as set_identity,
            mock.patch.object(meter, "newest_source", return_value=None),
            mock.patch.object(meter, "refresh_cross_session_state", return_value={}),
            mock.patch.object(meter, "STATE", {}),
        ):
            handler.do_POST()

        set_identity.assert_called_once_with(
            key, model="gpt-5.6-sol", provider="codex", remove=False,
        )
        self.assertFalse(handler.send_error.called)
        payload = json.loads(handler._send.call_args.args[0])
        self.assertTrue(payload["ok"])


class HermesModelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity_path = self.root / "session-model-identities.json"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def source(**overrides):
        source = {
            "provider": "hermes",
            "client": "hermes",
            "label": "Hermes Agent",
            "id": "private-hermes-session-id",
            "session": "private-hermes-session-id",
            "path": "hermes:private-hermes-session-id",
            "project": "",
            "mtime": 100.0,
            "model": "unknown-model",
            "model_provider": "unknown-model-provider",
            "account_provider": "amazon",
            "model_identity_hint": {
                "label": "Bedrock application profile",
                "candidates": ["claude-sonnet-5"],
            },
        }
        source.update(overrides)
        return source

    def test_unknown_hermes_model_is_assignable_with_only_safe_public_hint(self):
        enriched = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )[0]

        identity = enriched["model_identity"]
        self.assertEqual(identity["kind"], "missing_model")
        self.assertEqual(identity["runtime"], "hermes")
        self.assertEqual(identity["source"], "unresolved")
        self.assertEqual(identity["observed_label"], "Bedrock application profile")
        self.assertEqual(identity["candidates"], ["claude-sonnet-5"])
        self.assertTrue(identity["assignable"])
        self.assertRegex(identity["key"], r"^[a-f0-9]{64}$")
        encoded = json.dumps(identity)
        self.assertNotIn("private-hermes-session-id", encoded)
        self.assertNotIn("arn:", encoded)

    def test_hermes_assignment_is_scoped_to_one_opaque_session_key(self):
        unresolved = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )
        key = unresolved[0]["model_identity"]["key"]

        assigned = meter.set_session_model_identity(
            key,
            model="claude-sonnet-5",
            provider="hermes",
            sources=unresolved,
            path=str(self.identity_path),
        )

        self.assertTrue(assigned["ok"])
        applied = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )[0]
        self.assertEqual(applied["model"], "claude-sonnet-5")
        self.assertEqual(applied["model_provider"], "anthropic")
        self.assertEqual(applied["account_provider"], "amazon")
        self.assertEqual(applied["model_identity"]["source"], "user_assigned")
        self.assertEqual(
            applied["model_identity"]["assigned_model"], "claude-sonnet-5",
        )
        stored = self.identity_path.read_text(encoding="utf-8")
        self.assertNotIn("private-hermes-session-id", stored)
        self.assertNotIn("arn:", stored)

        removed = meter.set_session_model_identity(
            key,
            remove=True,
            sources=[applied],
            path=str(self.identity_path),
        )
        self.assertTrue(removed["ok"])
        cleared = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )[0]
        self.assertEqual(cleared["model"], "unknown-model")
        self.assertEqual(cleared["model_identity"]["source"], "unresolved")

    def test_hermes_assignment_rejects_cross_provider_and_unknown_models(self):
        unresolved = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )
        key = unresolved[0]["model_identity"]["key"]

        wrong_provider = meter.set_session_model_identity(
            key,
            model="gpt-5.6-sol",
            provider="codex",
            sources=unresolved,
            path=str(self.identity_path),
        )
        unknown = meter.set_session_model_identity(
            key,
            model="unknown-model",
            provider="hermes",
            sources=unresolved,
            path=str(self.identity_path),
        )

        self.assertFalse(wrong_provider["ok"])
        self.assertFalse(unknown["ok"])

    def test_hermes_assignment_requires_a_safe_bedrock_candidate(self):
        for hint in (None, {
            "label": "Bedrock application profile",
            "candidates": [],
        }):
            with self.subTest(hint=hint):
                source = self.source(model_identity_hint=hint)
                enriched = meter.enrich_session_model_identities(
                    [source], path=str(self.identity_path),
                )[0]
                identity = enriched["model_identity"]

                self.assertFalse(identity["assignable"])
                result = meter.set_session_model_identity(
                    identity["key"], model="claude-sonnet-5",
                    provider="hermes", sources=[enriched],
                    path=str(self.identity_path),
                )
                self.assertFalse(result["ok"])

    def test_hermes_assignment_rejects_a_model_outside_safe_candidates(self):
        unresolved = meter.enrich_session_model_identities(
            [self.source()], path=str(self.identity_path),
        )
        identity = unresolved[0]["model_identity"]

        result = meter.set_session_model_identity(
            identity["key"], model="claude-opus-5", provider="hermes",
            sources=unresolved, path=str(self.identity_path),
        )

        self.assertFalse(result["ok"])

    def test_local_action_route_forwards_the_hermes_provider(self):
        handler = object.__new__(meter.H)
        handler.path = "/settings/session-model-identity"
        key = "b" * 64
        body = json.dumps({
            "session_key": key,
            "model": "claude-sonnet-5",
            "provider": "hermes",
        }).encode("utf-8")
        handler.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Token-Meter-Action": meter._ACTION_TOKEN,
            "Origin": "http://127.0.0.1:8722",
        }
        handler.rfile = io.BytesIO(body)
        handler._send = mock.Mock()
        handler.send_error = mock.Mock()
        with (
            mock.patch.object(
                meter, "set_session_model_identity",
                return_value={"ok": True, "changed": True, "session_key": key},
            ) as set_identity,
            mock.patch.object(meter, "newest_source", return_value=None),
            mock.patch.object(meter, "refresh_cross_session_state", return_value={}),
            mock.patch.object(meter, "STATE", {}),
        ):
            handler.do_POST()

        set_identity.assert_called_once_with(
            key, model="claude-sonnet-5", provider="hermes", remove=False,
        )
        self.assertFalse(handler.send_error.called)
        self.assertTrue(json.loads(handler._send.call_args.args[0])["ok"])

    def test_public_projection_drops_untrusted_hint_fields(self):
        source = self.source(model_identity_hint={
            "label": "private profile name",
            "candidates": [
                "claude-sonnet-5", "claude-sonnet-private",
                "arn:private", "family-name",
            ],
        })

        identity = meter.enrich_session_model_identities(
            [source], path=str(self.identity_path),
        )[0]["model_identity"]

        self.assertNotIn("observed_label", identity)
        self.assertEqual(identity["candidates"], ["claude-sonnet-5"])


class CodexModelIdentityDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(__file__).resolve().parents[1] / "page.html").read_text(
            encoding="utf-8"
        )

    def test_all_sessions_offers_a_scoped_identity_assignment(self):
        for marker in (
            "session-model-identity-dialog",
            "/settings/session-model-identity",
            "Assign model",
            "This affects only this saved session",
            "does not change model prices",
        ):
            self.assertIn(marker, self.page)

    def test_hermes_model_recovery_is_available_on_list_and_session_surfaces(self):
        for marker in (
            "Add model",
            "session-model-identity-action",
            "Bedrock application profile",
            "Claude model ID",
            "Configured public",
            "'alias':'aliases'",
            "forceAllSessionRowRefresh=true",
            "if(pinned)await refreshSelectedSession({show:true})",
        ):
            self.assertIn(marker, self.page)
        visible_session_meta = re.search(
            r'<div class=previewRunMeta data-current-detail>(.*?)</div>',
            self.page,
            re.DOTALL,
        )
        self.assertIsNotNone(visible_session_meta)
        self.assertIn(
            'id=session-model-identity-action', visible_session_meta.group(1),
        )

    def test_hermes_aggregate_chart_explains_execution_coverage(self):
        for marker in (
            "recorded executions",
            "one Hermes session aggregate",
            "per-execution token breakdown unavailable",
            "const xTickIndexes=[...new Set",
        ):
            self.assertIn(marker, self.page)


if __name__ == "__main__":
    unittest.main()
