import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import (
    DeletionDisposition,
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    PriceQuote,
)
from token_meter.runtimes.grok import (
    GrokRuntimeAdapter,
    default_log_path,
    default_sessions_dir,
)


SENTINEL_PROMPT = "SENTINEL_PROMPT_DO_NOT_LEAK"
SENTINEL_TOOL = "SENTINEL_TOOL_PAYLOAD"
SENTINEL_LOG = "SENTINEL_LOG_SECRET"
SENTINEL_SUMMARY = "SENTINEL_SUMMARY_DO_NOT_LEAK"
SENTINEL_RESPONSE = "SENTINEL_RESPONSE_DO_NOT_LEAK"
SESSION_ID = "sess0001"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "grok"
FIXTURE_SESSION_ID = "019a0000-0000-7000-8000-000000000001"


def _summary(session_id, title="Add Grok support", kind="interactive", model="grok-4.7",
             declared=None, cwd="/repo/app", created_at=1_762_200_000):
    return {
        "info": {"id": declared if declared is not None else session_id, "cwd": cwd},
        "created_at": created_at,
        "updated_at": created_at + 300,
        "last_active_at": created_at + 300,
        "current_model_id": model,
        "generated_title": title,
        "session_kind": kind,
        "reasoning_effort": "high",
    }


def _write_session(root, session_id=SESSION_ID, title="Add Grok support", kind="interactive",
                   model_ids=("grok-4.7-build",), calls=None, tools=None, extra_log=(),
                   declared=None, summary_model="grok-4.7", cwd="/repo/app",
                   created_at=1_762_200_000, call_ts=1_762_200_200):
    session = root / "sessions" / "%2Frepo" / session_id
    session.mkdir(parents=True)
    (session / "summary.json").write_text(json.dumps(_summary(
        session_id, title, kind, summary_model, declared, cwd, created_at,
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
        ctx = dict(ctx)
        ts = ctx.pop("ts", call_ts)
        lines.append(json.dumps({
            "ts": ts,
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
        self.assertNotEqual(state["tokens"]["output"], 140)
        self.assertEqual(state["executions"][0]["tokens"]["output"], 100)
        self.assertEqual(state["executions"][0]["tokens"]["cache_write"], 0)
        self.assertEqual(state["tokens"]["cache_write"], 0)
        self.assertEqual(row["model_stats"][0]["model"], "grok-4.7")
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
            row = adapter.summarize_legacy(record)
            loaded = adapter.load(adapter.discover(DiscoveryContext(str(root)))[0], DetailLevel.SUMMARY)

        self.assertGreater(record["model_count"], 1)
        self.assertFalse(state["availability"]["cost"])
        self.assertFalse(state["availability"]["tokens"])
        self.assertEqual(state["tokens"]["output"], 0)
        self.assertIsNone(state["executions"][0]["tokens"]["input"])
        self.assertIsNone(state["executions"][0]["tokens"]["output"])
        self.assertIsNone(state["executions"][0]["tokens"]["total"])
        self.assertEqual(row["model_stats"], [])
        self.assertFalse(row["availability"]["tokens"])
        self.assertIn("multiple_models", [warning.code for warning in loaded.warnings])
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.UNAVAILABLE)
        self.assertEqual(loaded.usage.output_tokens.basis, EvidenceBasis.UNAVAILABLE)

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

    def test_tool_text_cannot_change_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _write_session(root)
            chat = session / "chat_history.jsonl"
            with chat.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "type": "tool_result",
                    "content": 'assistant said {"model_id": "user@example.com"}',
                }) + "\n")
                handle.write(json.dumps({
                    "type": "assistant",
                    "model_id": "grok-4.7",
                    "content": '{"model_id": "other.example.com"}',
                }) + "\n")
                handle.write('noise "assistant" "model_id": "sk-not-a-model"\n')
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)

        self.assertEqual(record["model"], "grok-4.7")
        self.assertEqual(record["model_count"], 1)
        self.assertAlmostEqual(state["total_cost"], 0.0014)
        encoded = json.dumps(record)
        self.assertNotIn("user@example.com", encoded)
        self.assertNotIn("other.example.com", encoded)
        self.assertNotIn("sk-not-a-model", encoded)

    def test_account_host_and_secret_model_ids_fall_back_to_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, model_ids=("user@example.com",))
            _write_session(root, "sess0002", model_ids=("example.com",))
            _write_session(root, "sess0003", model_ids=("sk-live-secret-token-value",))
            _write_session(root, "sess0004", model_ids=("grok-4.20-0309-reasoning",),
                           summary_model="grok-4.20-0309-reasoning")
            records = {
                row["id"]: row
                for row in _adapter(root).discover_legacy(DiscoveryContext(str(root)))
            }

        self.assertEqual(records[SESSION_ID]["model"], "grok-4.7")
        self.assertEqual(records[SESSION_ID]["model_count"], 1)
        self.assertEqual(records["sess0002"]["model"], "grok-4.7")
        self.assertEqual(records["sess0003"]["model"], "grok-4.7")
        self.assertEqual(records["sess0004"]["model"], "grok-4.20-0309-reasoning")
        encoded = json.dumps(list(records.values()))
        self.assertNotIn("user@example.com", encoded)
        self.assertNotIn("example.com", encoded)
        self.assertNotIn("sk-live", encoded)

    def test_early_model_switch_is_not_priced_as_the_last_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _write_session(root, model_ids=())
            filler = json.dumps({"type": "user", "content": "z" * 400}) + "\n"
            lines = [json.dumps({
                "type": "assistant", "model_id": "grok-4.6", "content": "a",
            }) + "\n"]
            while sum(len(line) for line in lines) < 300_000:
                lines.append(filler)
            lines.append(json.dumps({
                "type": "assistant", "model_id": "grok-4.7", "content": "b",
            }) + "\n")
            (session / "chat_history.jsonl").write_text("".join(lines))
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            row = adapter.summarize_legacy(record)

        self.assertGreater(record["model_count"], 1)
        self.assertFalse(row["availability"]["cost"])
        self.assertEqual(row["model_stats"], [])

    def test_unreadable_model_scan_does_not_price_a_partial_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = _write_session(root, model_ids=())
            first = json.dumps({
                "type": "assistant", "model_id": "grok-4.6", "content": "a",
            }) + "\n"
            chat = first + ("x" * 5000) + "\n" + json.dumps({
                "type": "assistant", "model_id": "grok-4.7", "content": "b",
            }) + "\n"
            (session / "chat_history.jsonl").write_text(chat)
            with mock.patch("token_meter.runtimes.grok.MAX_CHAT_BYTES", len(first) + 10):
                adapter = _adapter(root)
                record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
                state = adapter.recompute_legacy(record)
                row = adapter.summarize_legacy(record)
                loaded = adapter.load(
                    adapter.discover(DiscoveryContext(str(root)))[0], DetailLevel.SUMMARY,
                )

        self.assertTrue(record["models_unreliable"])
        self.assertEqual(state["total_cost"], 0.0)
        self.assertFalse(state["availability"]["cost"])
        self.assertIn("too large", state["pricing_note"])
        self.assertEqual(row["model_stats"], [])
        self.assertIn("models_unreliable", [warning.code for warning in loaded.warnings])

    def test_reasoning_above_completion_is_unavailable_not_added(self):
        from token_meter.mcp.projections import standardized_trace_projection
        from token_meter.mcp.service import _execution_records

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[{
                "prompt_tokens": 1000,
                "cached_prompt_tokens": 0,
                "completion_tokens": 100,
                "reasoning_tokens": 140,
                "model_elapsed_ms": 1000,
                "loop_index": 0,
            }], tools=[])
            adapter = _adapter(root)
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)
            row = adapter.summarize_legacy(record)
            trace = standardized_trace_projection(record, state)
            stat_rows, _projection = _execution_records(record, state)

        self.assertFalse(state["availability"]["tokens"])
        self.assertFalse(state["availability"]["cost"])
        self.assertEqual(state["total_cost"], 0.0)
        self.assertEqual(state["tokens"]["output"], 0)
        self.assertIsNone(state["executions"][0]["tokens"]["output"])
        self.assertEqual(row["model_stats"], [])
        self.assertNotIn("total_tokens", trace["session"])
        self.assertNotIn("input", trace["executions"][0]["tokens"])
        self.assertNotIn("output", trace["executions"][0]["tokens"])
        self.assertIsNone(stat_rows[0]["metrics"]["input_tokens"])
        self.assertIsNone(stat_rows[0]["metrics"]["output_tokens"])
        self.assertIsNone(stat_rows[0]["metrics"]["total_tokens"])

    def test_environment_overrides_select_the_session_root_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root)
            sessions = str(root / "sessions")
            log_path = str(root / "logs" / "unified.jsonl")
            self.assertEqual(
                default_sessions_dir(environment={"GROK_SESSIONS_DIR": sessions}),
                os.path.abspath(sessions),
            )
            self.assertEqual(
                default_log_path(environment={"GROK_LOG_PATH": log_path}),
                os.path.abspath(log_path),
            )
            self.assertEqual(
                default_sessions_dir(environment={"GROK_HOME": str(root)}),
                os.path.abspath(str(root / "sessions")),
            )
            self.assertEqual(
                default_log_path(environment={"GROK_HOME": str(root)}),
                os.path.abspath(str(root / "logs" / "unified.jsonl")),
            )
            self.assertEqual(
                default_sessions_dir(environment={
                    "GROK_HOME": str(root),
                    "GROK_SESSIONS_DIR": sessions,
                }),
                os.path.abspath(sessions),
            )
            with mock.patch.dict(os.environ, {
                "GROK_SESSIONS_DIR": sessions,
                "GROK_LOG_PATH": log_path,
            }):
                adapter = GrokRuntimeAdapter(
                    project_resolver=lambda value: value,
                    compatibility=meter._grok_compatibility(),
                )
                records = adapter.discover_legacy(DiscoveryContext(str(root)))
        self.assertEqual([row["id"] for row in records], [SESSION_ID])

    def test_quote_resolver_overrides_the_catalog_rate(self):
        def resolver(query):
            return PriceQuote(
                model=query.model,
                input_per_million=3.0,
                output_per_million=3.0,
                cache_read_per_million=0.0,
                cache_write_per_million=0.0,
                basis=EvidenceBasis.ESTIMATED,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, calls=[{
                "prompt_tokens": 1000,
                "cached_prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "model_elapsed_ms": 1000,
                "loop_index": 0,
            }], tools=[])
            adapter = GrokRuntimeAdapter(
                root / "sessions",
                root / "logs" / "unified.jsonl",
                project_resolver=lambda value: value,
                quote_resolver=resolver,
                compatibility=meter._grok_compatibility(),
            )
            record = adapter.discover_legacy(DiscoveryContext(str(root)))[0]
            state = adapter.recompute_legacy(record)

        self.assertAlmostEqual(state["total_cost"], 0.003)
        self.assertNotAlmostEqual(state["total_cost"], 0.002)

    def test_revision_ignores_another_sessions_log_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root)
            first = _adapter(root).discover_legacy(DiscoveryContext(str(root)))[0]
            _write_session(root, "sess0002")
            second = {
                row["id"]: row
                for row in _adapter(root).discover_legacy(DiscoveryContext(str(root)))
            }
            self.assertEqual(
                second[SESSION_ID]["request_revision"], first["request_revision"],
            )
            log = root / "logs" / "unified.jsonl"
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": 1_762_200_250,
                    "msg": "shell.turn.inference_done",
                    "sid": SESSION_ID,
                    "ctx": {
                        "prompt_tokens": 10,
                        "cached_prompt_tokens": 0,
                        "completion_tokens": 4,
                        "reasoning_tokens": 1,
                    },
                }) + "\n")
            third = {
                row["id"]: row
                for row in _adapter(root).discover_legacy(DiscoveryContext(str(root)))
            }
        self.assertNotEqual(
            third[SESSION_ID]["request_revision"], first["request_revision"],
        )

    def test_oversized_log_keeps_sessions_created_inside_the_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boundary = 1_700_000_100
            _write_session(root, "oldsess01", created_at=boundary - 10_000, calls=[])
            _write_session(root, "bounds001", created_at=boundary, calls=[])
            _write_session(root, "newsess01", created_at=boundary + 100, calls=[])
            _write_session(root, "headsess1", created_at=boundary - 20_000, calls=[])

            def line(session_id, ts):
                return (json.dumps({
                    "ts": ts,
                    "msg": "shell.turn.inference_done",
                    "sid": session_id,
                    "ctx": {
                        "prompt_tokens": 1000,
                        "cached_prompt_tokens": 0,
                        "completion_tokens": 10,
                        "reasoning_tokens": 1,
                        "model_elapsed_ms": 1000,
                        "loop_index": 0,
                    },
                }) + "\n").encode()

            prefix = b"h" * 8000 + b"\n"
            head = line("headsess1", boundary - 20_000)
            discard = b"{\"msg\":\"discard\"}\n"
            retained = b"".join((
                line("oldsess01", boundary),
                line("bounds001", boundary),
                line("newsess01", boundary + 100),
            ))
            (root / "logs" / "unified.jsonl").write_bytes(prefix + head + discard + retained)
            with mock.patch(
                "token_meter.runtimes.grok.MAX_LOG_BYTES", len(discard) + len(retained),
            ):
                adapter = _adapter(root)
                records = {
                    row["id"]: row
                    for row in adapter.discover_legacy(DiscoveryContext(str(root)))
                }
                old = adapter.recompute_legacy(records["oldsess01"])
                bound = adapter.recompute_legacy(records["bounds001"])
                new = adapter.recompute_legacy(records["newsess01"])
                head_state = adapter.recompute_legacy(records["headsess1"])

        expected = 1000 * 2 / 1_000_000 + 10 * 6 / 1_000_000
        self.assertFalse(old["availability"]["cost"])
        self.assertEqual(old["total_cost"], 0.0)
        self.assertIsNone(old["executions"][0]["tokens"]["output"])
        self.assertAlmostEqual(bound["total_cost"], expected)
        self.assertAlmostEqual(new["total_cost"], expected)
        self.assertIsNone(head_state)

    def test_call_cap_is_per_session(self):
        call = {
            "prompt_tokens": 1000,
            "cached_prompt_tokens": 0,
            "completion_tokens": 10,
            "reasoning_tokens": 1,
            "model_elapsed_ms": 1000,
            "loop_index": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_session(root, "heavyses1", calls=[dict(call), dict(call, loop_index=1)])
            _write_session(root, "lightses1", calls=[dict(call)])
            with mock.patch("token_meter.runtimes.grok.MAX_CALLS", 1):
                adapter = _adapter(root)
                records = {
                    row["id"]: row
                    for row in adapter.discover_legacy(DiscoveryContext(str(root)))
                }
                heavy = adapter.recompute_legacy(records["heavyses1"])
                light = adapter.recompute_legacy(records["lightses1"])

        self.assertFalse(heavy["availability"]["cost"])
        self.assertEqual(heavy["total_cost"], 0.0)
        self.assertAlmostEqual(light["total_cost"], 1000 * 2 / 1_000_000 + 10 * 6 / 1_000_000)

    def test_checked_in_objects_keep_list_price_and_drop_transcripts(self):
        """The fixture tree is the on-disk Grok object, not an inline string.

        usage.json records costUsdTicks (1.5e9 ticks = $0.15 at 1e10 per USD)
        and cacheCreationTokens. Billing stays on the inference log at list
        rates. Chat rows use type, and a model id quoted in a tool result or
        on turn_started does not become the session model.
        """
        adapter = GrokRuntimeAdapter(
            FIXTURES / "sessions",
            FIXTURES / "logs" / "unified.jsonl",
            project_resolver=lambda value: value,
            compatibility=meter._grok_compatibility(),
        )
        records = adapter.discover_legacy(DiscoveryContext(str(FIXTURES)))
        self.assertEqual([row["id"] for row in records], [FIXTURE_SESSION_ID])
        record = records[0]
        state = adapter.recompute_legacy(record)
        loaded = adapter.load(
            adapter.discover(DiscoveryContext(str(FIXTURES)))[0],
            DetailLevel.SUMMARY,
        )
        encoded = json.dumps({"record": record, "state": state, "loaded": repr(loaded)})

        self.assertEqual(record["model"], "grok-4.7")
        self.assertEqual(record["title"], "Fixture Grok session")
        self.assertEqual(record["reasoning_effort"], "xhigh")
        self.assertEqual(record["project"], "/tmp/fixture-repo")
        self.assertAlmostEqual(state["total_cost"], 0.0014)
        self.assertEqual(state["tokens"]["input"], 200)
        self.assertEqual(state["tokens"]["cache_read"], 800)
        self.assertEqual(state["tokens"]["output"], 100)
        self.assertEqual(state["tokens"]["cache_write"], 0)
        self.assertEqual(loaded.usage.cache_write_tokens.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(loaded.usage.cache_write_tokens.value)
        self.assertEqual(loaded.usage.cost_usd.basis, EvidenceBasis.ESTIMATED)
        self.assertAlmostEqual(loaded.usage.cost_usd.value, 0.0014)
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read_file")
        for secret in (
            SENTINEL_PROMPT, SENTINEL_TOOL, SENTINEL_LOG, SENTINEL_SUMMARY,
            SENTINEL_RESPONSE, "1500000000", "grok-4.5", "grok-4.1-fast",
        ):
            self.assertNotIn(secret, encoded)

    def test_dashboard_names_grok_in_spend_and_custom_prices(self):
        page = (Path(__file__).resolve().parents[2] / "page.html").read_text()
        self.assertIn(
            "const SPEND_RUNTIME_ORDER=['claude','codex','cursor','opencode','kiro','grok','unknown'];",
            page,
        )
        self.assertIn("grok:'Grok'", page)
        self.assertIn("<option value=grok>Grok / xAI</option>", page)


if __name__ == "__main__":
    unittest.main()
