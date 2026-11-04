import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import DeletionDisposition, DetailLevel, DiscoveryContext, EvidenceBasis
from token_meter.runtimes.grok import GrokRuntimeAdapter


SENTINEL_PROMPT = "SENTINEL_PROMPT_DO_NOT_LEAK"
SENTINEL_TOOL = "SENTINEL_TOOL_PAYLOAD"
SENTINEL_LOG = "SENTINEL_LOG_SECRET"
SESSION_ID = "sess0001"


def _summary(session_id, title="Add Grok support", kind="interactive", model="grok-4.7",
             declared=None, cwd="/repo/app"):
    return {
        "info": {"id": declared if declared is not None else session_id, "cwd": cwd},
        "created_at": 1_762_200_000,
        "updated_at": 1_762_200_300,
        "last_active_at": 1_762_200_300,
        "current_model_id": model,
        "generated_title": title,
        "session_kind": kind,
        "reasoning_effort": "high",
    }


def _write_session(root, session_id=SESSION_ID, title="Add Grok support", kind="interactive",
                   model_ids=("grok-4.7-build",), calls=None, tools=None, extra_log=(),
                   declared=None, summary_model="grok-4.7", cwd="/repo/app"):
    session = root / "sessions" / "%2Frepo" / session_id
    session.mkdir(parents=True)
    (session / "summary.json").write_text(json.dumps(_summary(
        session_id, title, kind, summary_model, declared, cwd,
    )))
    history = []
    history.append({"type": "user", "content": SENTINEL_PROMPT})
    for model_id in model_ids:
        history.append({
            "type": "assistant",
            "model_id": model_id,
            "content": SENTINEL_PROMPT,
        })
    (session / "chat_history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in history)
    )
    tool_rows = tools if tools is not None else [{
        "type": "tool_completed",
        "tool_name": "read_file",
        "outcome": "success",
        "ts": 1_762_200_100,
        "arguments": SENTINEL_TOOL,
    }]
    (session / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tool_rows)
    )
    if calls is None:
        calls = [{
            "prompt_tokens": 1000,
            "cached_prompt_tokens": 800,
            "completion_tokens": 100,
            "reasoning_tokens": 40,
            "model_elapsed_ms": 1200,
            "ttft_ms": 100,
            "loop_index": 0,
        }]
    log = root / "logs" / "unified.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"msg": "auth.token", "secret": SENTINEL_LOG, "sid": session_id})]
    lines.append("{not-json")
    for ctx in calls:
        lines.append(json.dumps({
            "ts": 1_762_200_200,
            "msg": "shell.turn.inference_done",
            "sid": session_id,
            "ctx": ctx,
        }))
    lines.extend(extra_log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return session


def _adapter(root):
    return GrokRuntimeAdapter(
        root / "sessions",
        root / "logs" / "unified.jsonl",
        project_resolver=lambda value: value,
        compatibility=meter._grok_compatibility(),
    )


class GrokRuntimeAdapterTests(unittest.TestCase):
    def test_discovers_a_session_and_drops_imports_mismatches_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root)
            _write_session(root, "import001", kind="claude_import", calls=[])
            _write_session(root, "mismatch1", declared="otherid1", calls=[])
            adapter = _adapter(root)
            records = adapter.discover_legacy(DiscoveryContext(str(root)))
            encoded = json.dumps(records)

        self.assertEqual([row["id"] for row in records], [SESSION_ID])
        record = records[0]
        self.assertEqual(record["provider"], "grok")
        self.assertEqual(record["path"], "grok:{}".format(SESSION_ID))
        self.assertEqual(record["model"], "grok-4.7")
        self.assertEqual(record["model_provider"], "xai")
        self.assertEqual(record["project"], "/repo/app")
        self.assertEqual(record["title"], "Add Grok support")
        self.assertEqual(record["reasoning_effort"], "high")
        self.assertNotIn("/", record["path"].split(":", 1)[0])
        for sentinel in (SENTINEL_PROMPT, SENTINEL_TOOL, SENTINEL_LOG):
            self.assertNotIn(sentinel, encoded)

    def test_prices_fresh_tokens_and_the_build_suffix_at_public_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root)
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)
            row = adapter.summarize_legacy(record)
            loaded = adapter.load(adapter.discover(DiscoveryContext(str(root)))[0], DetailLevel.SUMMARY)
            encoded = json.dumps({"state": state, "row": row}, default=str)

        self.assertAlmostEqual(state["total_cost"], 0.0014)
        self.assertEqual(state["tokens"]["input"], 200)
        self.assertEqual(state["tokens"]["cache_read"], 800)
        self.assertEqual(state["tokens"]["output"], 100)
        self.assertEqual(state["tokens"]["cache_write"], 0)
        self.assertAlmostEqual(row["cost"], 0.0014)
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["output_tokens"], 100)
        self.assertTrue(row["availability"]["cost"])
        self.assertEqual(loaded.pricing_basis, "xai_public_rates")
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.0014)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(loaded.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cache_write_tokens.value)
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read_file")
        for sentinel in (SENTINEL_PROMPT, SENTINEL_TOOL, SENTINEL_LOG):
            self.assertNotIn(sentinel, encoded)

    def test_long_context_doubles_input_cache_and_output_at_the_published_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[{
                "prompt_tokens": 200_000,
                "cached_prompt_tokens": 0,
                "completion_tokens": 10,
                "reasoning_tokens": 0,
                "model_elapsed_ms": 1000,
                "loop_index": 0,
            }], tools=[])
            doubled = _adapter(root).recompute_legacy(
                _adapter(root).discover_legacy(DiscoveryContext(str(root)))[0]
            )
        self.assertAlmostEqual(doubled["total_cost"], 0.80012)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[{
                "prompt_tokens": 199_999,
                "cached_prompt_tokens": 0,
                "completion_tokens": 10,
                "reasoning_tokens": 0,
                "model_elapsed_ms": 1000,
                "loop_index": 0,
            }], tools=[])
            standard = _adapter(root).recompute_legacy(
                _adapter(root).discover_legacy(DiscoveryContext(str(root)))[0]
            )
        self.assertAlmostEqual(standard["total_cost"], 199_999 * 2 / 1_000_000 + 10 * 6 / 1_000_000)

    def test_missing_inference_log_stays_unavailable_rather_than_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[])
            (root / "logs" / "unified.jsonl").unlink()
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            row = adapter.summarize_legacy(record)

        self.assertEqual(record["id"], SESSION_ID)
        self.assertIsNone(adapter.recompute_legacy(record))
        self.assertEqual(row["cost"], 0.0)
        self.assertFalse(row["availability"]["cost"])
        self.assertFalse(row["availability"]["tokens"])

    def test_cache_above_prompt_is_not_priced_as_zero_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[{
                "prompt_tokens": 10,
                "cached_prompt_tokens": 20,
                "completion_tokens": 5,
                "model_elapsed_ms": 100,
                "loop_index": 0,
            }], tools=[])
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)
            row = adapter.summarize_legacy(record)

        self.assertFalse(state["availability"]["cost"])
        self.assertFalse(state["availability"]["tokens"])
        self.assertEqual(state["total_cost"], 0.0)
        self.assertEqual(state["tokens"]["cache_read"], 0)
        self.assertFalse(row["availability"]["cost"])

    def test_secret_or_path_titles_fall_back_to_a_generic_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, title="sk-live-secret-token-value-0123456789", calls=[])
            _write_session(root, "sess0002", title="/Users/secret/plan.md", calls=[])
            records = {
                row["id"]: row
                for row in _adapter(root).discover_legacy(DiscoveryContext(str(root)))
            }

        self.assertEqual(records[SESSION_ID]["title"], "Grok session")
        self.assertEqual(records["sess0002"]["title"], "Grok session")
        encoded = json.dumps(list(records.values()))
        self.assertNotIn("sk-live", encoded)
        self.assertNotIn("/Users/secret", encoded)

    def test_multiple_models_leave_cost_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, model_ids=("grok-4.6", "grok-4.7-build"))
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)
            loaded = adapter.load(adapter.discover(DiscoveryContext(str(root)))[0], DetailLevel.SUMMARY)

        self.assertGreater(record["model_count"], 1)
        self.assertFalse(state["availability"]["cost"])
        self.assertIn("multiple_models", [warning.code for warning in loaded.warnings])
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)

    def test_public_path_and_deletion_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _write_session(root, calls=[])
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            plan = adapter.deletion_plan(record)

        self.assertTrue(record["path"].startswith("grok:"))
        self.assertNotIn(str(session), json.dumps(record))
        self.assertEqual(plan.disposition, DeletionDisposition.DENY)
        self.assertEqual(plan.targets, ())
        self.assertIn("grok", meter.session_action_capability()["read_only_providers"])
        source = {"provider": "grok", "id": SESSION_ID}
        with mock.patch.object(meter, "find_session", return_value=source), \
                mock.patch.object(meter, "all_session_sources", return_value=[source]), \
                mock.patch.object(meter, "trash_session_log") as trash:
            result = meter.request_session_delete(SESSION_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "read_only_provider")
        self.assertIn("Grok", result["error"])
        trash.assert_not_called()

    def test_registry_lists_grok_last_and_routes_recompute(self):
        self.assertEqual(meter.runtime_registry().runtime_ids[-1], "grok")
        source = {"provider": "grok", "id": SESSION_ID}
        expected = {"provider": "grok", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected
        with mock.patch.object(meter, "_grok_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)
        adapter.load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
