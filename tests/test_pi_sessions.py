import unittest
import datetime
import json
import tempfile
from pathlib import Path
from unittest import mock
import meter
from token_meter.contracts import DiscoveryContext
from token_meter.runtimes import pi as pi_runtime


class PiDocumentationTests(unittest.TestCase):
    def test_docs_explain_pi_evidence_and_privacy_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        guide = (root / "specs/USER_GUIDE.md").read_text()
        architecture = (root / "specs/ARCHITECTURE.md").read_text()
        security = (root / "specs/SECURITY.md").read_text()

        self.assertIn("PI_CODING_AGENT_DIR", guide)
        self.assertIn("Pi coding-agent sessions", guide)
        self.assertIn("does not import cloud transcripts", guide)
        guide_copy = " ".join(guide.split())
        self.assertIn("Wait is inferred from the user-to-assistant timestamps", guide_copy)
        self.assertIn("semantic token classification remain unavailable", guide_copy)
        self.assertIn("Pi adapter", architecture)
        self.assertIn("application-profile", architecture)
        self.assertIn("application-profile", security)


class PiRuntimeTests(unittest.TestCase):
    """Pi sessions are local JSONL records with no content projection."""

    def _write_session(self, agent_root, *, legacy=False, provider="anthropic",
                       model="claude-test", result_error=False, reasoning_tokens=None):
        directory = Path(agent_root) if legacy else (
            Path(agent_root) / "sessions" / "--repo--"
        )
        directory.mkdir(parents=True)
        path = directory / "pi-session.jsonl"
        rows = [
            {"type": "session", "version": 1, "id": "pi-session",
             "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"},
            {"type": "model_change", "id": "model-change", "parentId": None,
             "timestamp": "2026-09-04T10:00:01Z", "provider": provider,
             "modelId": model},
            {"type": "message", "id": "user", "parentId": "model-change",
             "timestamp": "2026-09-04T10:00:02Z",
             "message": {"role": "user", "content": []}},
            {"type": "message", "id": "assistant", "parentId": "user",
             "timestamp": "2026-09-04T10:00:05Z", "message": {
                "role": "assistant", "model": model, "provider": provider,
                 "content": [{"type": "toolCall", "id": "call", "name": "read",
                              "arguments": {}}],
                 "usage": {"input": 100, "output": 20, "cacheRead": 10,
                           "cacheWrite": 5, "totalTokens": 135,
                           "cost": {"input": 0.001, "output": 0.002,
                                    "cacheRead": 0.0001, "cacheWrite": 0.0002}},
             }},
            {"type": "message", "id": "result", "parentId": "assistant",
             "timestamp": "2026-09-04T10:00:06Z",
             "message": {"role": "toolResult", "toolCallId": "call", "toolName": "read",
                         **({"isError": True} if result_error else {})},
             },
        ]
        if reasoning_tokens is not None:
            rows[3]["message"]["usage"]["reasoning"] = reasoning_tokens
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def test_discovers_and_projects_pi_usage_as_a_local_cost_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                sources = meter.pi_session_sources()
                state = meter.recompute(sources[0])
                summary = meter.pi_summary(sources[0])

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["provider"], "pi")
        self.assertEqual(sources[0]["model"], "claude-test")
        self.assertEqual(sources[0]["model_provider"], "anthropic")
        self.assertEqual(sources[0]["project"], "/repo")
        self.assertEqual(sources[0]["title"], "Pi session")
        self.assertEqual(state["tokens"], {
            "input": 100, "cache_write": 5, "cache_read": 10, "output": 20,
        })
        self.assertEqual(state["total_tokens"], 135)
        self.assertAlmostEqual(state["total_cost"], 0.0033)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(state["availability"]["cost"])
        self.assertFalse(state["semantic_available"])
        self.assertEqual(state["semantic"], {
            "reasoning": 0, "output": 0, "retrieval": 0, "coordination": 0,
        })
        self.assertFalse(state["cache"]["savings_available"])
        self.assertIsNone(state["cache"]["saved"])
        self.assertIsNone(state["cache_saved"])
        self.assertTrue(state["wait_time"]["available"])
        self.assertEqual(state["executions"][0]["tools"][0]["name"], "read")
        self.assertEqual(state["executions"][0]["context_tokens"], 115)
        self.assertEqual(state["context"]["latest"], 115)
        self.assertTrue(state["availability"]["context"])
        self.assertTrue(state["availability"]["throughput"])
        self.assertTrue(state["throughput"]["available"])
        self.assertEqual(state["throughput"]["basis"], "end_to_end")
        self.assertAlmostEqual(state["throughput"]["output_tps"], 20 / 3)
        self.assertEqual(summary["context"]["latest"], 115)
        self.assertEqual(summary["_context_samples"], [115])
        self.assertTrue(summary["throughput"]["available"])
        self.assertNotIn("arguments", json.dumps(state))

    def test_redacts_pi_bedrock_application_profile_without_inferring_a_model(self):
        raw_profile = (
            "arn:aws:bedrock:us-west-2:123456789012:"
            "application-inference-profile/private-profile"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, provider="amazon-bedrock", model=raw_profile)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.pi_summary(source)

        self.assertEqual(source["model"], "aws-bedrock-profile")
        self.assertEqual(source["model_provider"], "amazon")
        public = json.dumps({"source": source, "state": state, "summary": summary})
        self.assertNotIn(raw_profile, public)
        self.assertNotIn("123456789012", public)
        self.assertTrue(state["cost_approx"])
        self.assertTrue(summary["wait_time"]["available"])

    def test_keeps_pi_cache_savings_unavailable_without_a_price_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                with mock.patch.object(
                    meter, "price_for",
                    side_effect=AssertionError("Pi must not infer cache savings from pricing"),
                ):
                    state = meter.recompute(source)

        self.assertFalse(state["cache"]["savings_available"])
        self.assertIsNone(state["cache"]["saved"])

    def test_discovers_legacy_pi_sessions_at_the_agent_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, legacy=True)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}):
                sources = meter.pi_session_sources()

        self.assertEqual([source["id"] for source in sources], ["pi-session"])

    def test_ignores_non_pi_jsonl_without_poisoning_discovery_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            root.mkdir()
            (root / "not-a-pi-session.jsonl").write_text("{}\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}):
                self.assertEqual(meter.pi_session_sources(), [])
                self.assertEqual(meter.pi_session_sources(), [])

    def test_records_pi_tool_result_errors_instead_of_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, result_error=True)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                native = meter._pi_native_adapter().discover(None)[0]
                session = meter._pi_native_adapter().load(native, meter.DetailLevel.FULL)

        self.assertTrue(state["executions"][0]["tools"][0]["error"])
        self.assertTrue(state["tools"]["total_errors"])
        self.assertEqual(state["executions"][0]["tools"][0]["result_available"], True)
        self.assertEqual(session.tools[0].status, "error")
        self.assertEqual(
            [event["severity"] for event in state["trace"] if event["kind"] == "tool_call"],
            ["warn"],
        )

    def test_keeps_successful_pi_tool_results_as_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                native = meter._pi_native_adapter().discover(None)[0]
                session = meter._pi_native_adapter().load(native, meter.DetailLevel.FULL)

        self.assertFalse(state["executions"][0]["tools"][0]["error"])
        self.assertFalse(state["tools"]["total_errors"])
        self.assertEqual(session.tools[0].status, "success")

    def test_projects_pi_reasoning_tokens_as_an_output_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, reasoning_tokens=12)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)

        execution = state["executions"][0]
        self.assertEqual(execution["reasoning_tokens"], 12)
        self.assertEqual(state["series"][0]["reasoning"], 12)
        self.assertTrue(state["series"][0]["think"])
        # Reasoning is a reported subset of output, never an extra bucket.
        self.assertEqual(state["total_tokens"], 135)
        self.assertEqual(state["tokens"], {
            "input": 100, "cache_write": 5, "cache_read": 10, "output": 20,
        })

    def test_clamps_pi_reasoning_tokens_to_reported_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_session(root, reasoning_tokens=999)
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                state = meter.recompute(meter.pi_session_sources()[0])

        self.assertEqual(state["executions"][0]["reasoning_tokens"], 20)
        self.assertTrue(state["series"][0]["think"])

    def test_keeps_pi_context_and_pace_unavailable_without_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            path = self._write_session(root)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            del rows[3]["message"]["usage"]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                source = meter.pi_session_sources()[0]
                state = meter.recompute(source)
                summary = meter.pi_summary(source)

        self.assertEqual(state["context"]["latest"], 0)
        self.assertFalse(state["availability"]["context"])
        self.assertFalse(state["throughput"]["available"])
        self.assertFalse(state["availability"]["throughput"])
        self.assertEqual(summary["context"]["latest"], 0)
        self.assertFalse(summary["throughput"]["available"])

    def test_keeps_cost_unavailable_when_a_pi_turn_lacks_its_local_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            path = self._write_session(root)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            del rows[3]["message"]["usage"]["cost"]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                state = meter.recompute(meter.pi_session_sources()[0])

        self.assertFalse(state["availability"]["cost"])
        self.assertEqual(state["total_cost"], 0)


class PiSubagentTests(unittest.TestCase):
    """Pi child runs live inside the parent transcript and must be accounted."""

    CONTENT_CANARY = "PI-CONTENT-CANARY-7731"

    @staticmethod
    def _child_usage(input_tokens=1000, output_tokens=100, cache_read=0,
                     cache_write=0, cost=0.25, turns=2, breakdown=False):
        # The shipped subagent tool reports a scalar cost, so that is the
        # default shape; breakdown=True is the documented Pi Usage object.
        usage = {
            "input": input_tokens,
            "output": output_tokens,
            "cacheRead": cache_read,
            "cacheWrite": cache_write,
            "cost": cost,
            "contextTokens": input_tokens + cache_read + cache_write,
            "turns": turns,
        }
        if breakdown:
            usage["cost"] = {
                "input": cost, "output": 0.0, "cacheRead": 0.0,
                "cacheWrite": 0.0, "total": cost,
            }
        return usage

    def _child_result(self, agent="probe", *, usage=None, exit_code=0,
                      stop_reason="stop", model="opencode-go/deepseek-v4.1-flash",
                      step=None):
        result = {
            "agent": agent,
            "agentSource": "user",
            "task": self.CONTENT_CANARY + "-task",
            "exitCode": exit_code,
            "messages": [{
                "role": "assistant",
                "content": [{"type": "text", "text": self.CONTENT_CANARY}],
            }],
            "stderr": self.CONTENT_CANARY + "-stderr",
            "stopReason": stop_reason,
            "model": model,
        }
        if usage is not None:
            result["usage"] = usage
        if step is not None:
            result["step"] = step
        return result

    def _write_subagent_session(self, agent_root, *, results=None,
                                top_usage=None, include_result=True,
                                arguments=None, tool_name="subagent",
                                mode="single", recent_seconds=None,
                                stop_reason="stop", final_turn_usage=None):
        directory = Path(agent_root) / "sessions" / "--repo--"
        directory.mkdir(parents=True)
        path = directory / "pi-subagent-session.jsonl"
        if recent_seconds is None:
            stamps = [
                "2026-09-04T10:00:00Z", "2026-09-04T10:00:01Z",
                "2026-09-04T10:00:02Z", "2026-09-04T10:00:05Z",
                "2026-09-04T10:00:09Z", "2026-09-04T10:00:12Z",
            ]
        else:
            base = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(seconds=recent_seconds)
            )
            stamps = [
                (base + datetime.timedelta(seconds=offset)).isoformat()
                .replace("+00:00", "Z")
                for offset in (0, 1, 2, 5, 9, 12)
            ]
        tool_call = {
            "type": "toolCall", "id": "call-subagent", "name": tool_name,
            "arguments": arguments or {"agent": "probe", "task": self.CONTENT_CANARY},
        }
        assistant_message = {
            "role": "assistant", "model": "claude-test", "provider": "anthropic",
            "content": [tool_call],
            "usage": {"input": 100, "output": 20, "cacheRead": 10,
                      "cacheWrite": 5, "totalTokens": 135,
                      "cost": {"input": 0.001, "output": 0.002,
                               "cacheRead": 0.0001, "cacheWrite": 0.0002}},
        }
        if stop_reason is not None:
            assistant_message["stopReason"] = stop_reason
        rows = [
            {"type": "session", "version": 3, "id": "pi-session",
             "timestamp": stamps[0], "cwd": "/repo"},
            {"type": "model_change", "id": "model-change", "parentId": None,
             "timestamp": stamps[1], "provider": "anthropic",
             "modelId": "claude-test"},
            {"type": "message", "id": "user", "parentId": "model-change",
             "timestamp": stamps[2],
             "message": {"role": "user", "content": []}},
            {"type": "message", "id": "assistant", "parentId": "user",
             "timestamp": stamps[3], "message": assistant_message},
        ]
        if include_result:
            message = {
                "role": "toolResult", "toolCallId": "call-subagent",
                "toolName": tool_name,
                "content": [{"type": "text", "text": self.CONTENT_CANARY}],
                "details": {
                    "mode": mode, "agentScope": "user", "projectAgentsDir": None,
                    "results": list(results if results is not None else []),
                },
            }
            if top_usage is not None:
                message["usage"] = top_usage
            rows.append({
                "type": "message", "id": "result", "parentId": "assistant",
                "timestamp": stamps[4], "message": message,
            })
        if final_turn_usage is not None:
            rows.append({
                "type": "message", "id": "assistant-final",
                "parentId": "result", "timestamp": stamps[5],
                "message": {
                    "role": "assistant", "model": "claude-test",
                    "provider": "anthropic", "content": [],
                    "stopReason": "stop", "usage": final_turn_usage,
                },
            })
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def _load(self, root):
        with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                mock.patch.object(meter, "_pi_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            source = meter.pi_session_sources()[0]
            return source, meter.recompute(source), meter.pi_summary(source)

    def _records(self, summary):
        return {record["kind"]: record for record in summary["_agent_records"]}

    def test_single_child_run_adds_spend_and_emits_root_and_spawned_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage())],
            )
            source, state, summary = self._load(root)

        # Parent turn 100/20/10/5 plus child 1000/100/0/0.
        self.assertEqual(state["tokens"], {
            "input": 1100, "output": 120, "cache_read": 10, "cache_write": 5,
        })
        self.assertEqual(state["total_tokens"], 1235)
        self.assertAlmostEqual(state["total_cost"], 0.2533)
        # Child work is not presented as a parent model call.
        self.assertEqual(len(state["executions"]), 1)
        self.assertEqual(summary["turns"], 1)

        records = self._records(summary)
        self.assertEqual(set(records), {"root", "spawned"})
        root_record, child = records["root"], records["spawned"]
        self.assertEqual(root_record["session_id"], source["id"])
        self.assertEqual(root_record["tokens"], 135)
        self.assertAlmostEqual(root_record["cost"], 0.0033)
        self.assertTrue(root_record["tokens_available"])
        self.assertEqual(child["parent_id"], root_record["id"])
        self.assertEqual(child["depth"], 1)
        self.assertEqual(child["role"], "probe")
        self.assertEqual(child["model"], "opencode-go/deepseek-v4.1-flash")
        self.assertEqual(child["tokens"], 1100)
        self.assertAlmostEqual(child["cost"], 0.25)
        self.assertTrue(child["tokens_available"])
        self.assertTrue(child["cost_available"])
        self.assertEqual(child["executions"], 2)
        self.assertEqual(child["activity_state"], "complete")
        self.assertEqual(child["started_at"], state["executions"][0]["ts"])
        self.assertEqual(child["session_id"], None)
        self.assertTrue(child["id"].startswith("pi-agent-"))
        self.assertNotIn(source["id"], child["id"])
        # Root plus child equals the session headline exactly.
        self.assertEqual(
            root_record["tokens"] + child["tokens"], state["total_tokens"],
        )
        self.assertAlmostEqual(
            root_record["cost"] + child["cost"], state["total_cost"], places=9,
        )

    def test_shipped_extension_scalar_cost_shape_is_measured(self):
        for reported in (0.0, 0.42):
            with self.subTest(cost=reported), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                # Exact usage shape persisted by the shipped subagent example.
                usage = {
                    "input": 1461, "output": 27, "cacheRead": 0,
                    "cacheWrite": 0, "cost": reported,
                    "contextTokens": 1488, "turns": 1,
                }
                self._write_subagent_session(
                    root, results=[self._child_result(usage=usage)],
                )
                _source, state, summary = self._load(root)

                child = self._records(summary)["spawned"]
                self.assertTrue(child["cost_available"])
                self.assertEqual(child["cost"], reported)
                self.assertTrue(state["availability"]["cost"])
                self.assertAlmostEqual(
                    state["total_cost"], 0.0033 + reported, places=6,
                )

    def test_reported_cost_breakdown_shape_is_still_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, results=[
                self._child_result(
                    usage=self._child_usage(cost=0.25, breakdown=True),
                ),
            ])
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertTrue(child["cost_available"])
        self.assertAlmostEqual(child["cost"], 0.25)
        self.assertAlmostEqual(state["total_cost"], 0.2533)

    def test_child_output_does_not_change_parent_pace_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage())],
            )
            _source, state, summary = self._load(root)

        self.assertTrue(summary["throughput"]["available"])
        self.assertAlmostEqual(
            summary["throughput"]["timing_coverage"], 1.0,
        )
        self.assertAlmostEqual(summary["throughput"]["output_tps"], 20 / 3)
        self.assertAlmostEqual(state["throughput"]["timing_coverage"], 1.0)

    def test_parallel_child_results_each_emit_a_record_and_sum_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, mode="parallel", results=[
                self._child_result("scout", usage=self._child_usage(600, 60, cost=0.1)),
                self._child_result("reviewer", usage=self._child_usage(400, 40, cost=0.2)),
            ])
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(sorted(record["role"] for record in children),
                         ["reviewer", "scout"])
        self.assertEqual(sum(record["tokens"] for record in children), 1100)
        self.assertAlmostEqual(sum(record["cost"] for record in children), 0.3)
        self.assertEqual(state["total_tokens"], 1235)
        self.assertAlmostEqual(state["total_cost"], 0.3033)
        self.assertEqual(len({record["id"] for record in children}), 2)

    def test_top_level_result_usage_without_details_emits_one_aggregate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[], top_usage=self._child_usage(500, 50, cost=0.3),
            )
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["role"], "probe")
        self.assertEqual(children[0]["tokens"], 550)
        self.assertAlmostEqual(children[0]["cost"], 0.3)
        self.assertEqual(state["total_tokens"], 685)

    def test_top_level_breakdown_usage_emits_aggregate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[],
                top_usage=self._child_usage(500, 50, cost=0.3, breakdown=True),
            )
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertTrue(child["cost_available"])
        self.assertAlmostEqual(child["cost"], 0.3)
        self.assertEqual(state["total_tokens"], 685)

    def test_reconciling_details_and_top_level_usage_count_spend_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, mode="parallel",
                results=[
                    self._child_result("scout", usage=self._child_usage(300, 30, cost=0.1)),
                    self._child_result("worker", usage=self._child_usage(200, 20, cost=0.2)),
                ],
                top_usage=self._child_usage(500, 50, cost=0.3),
            )
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(sorted(record["role"] for record in children),
                         ["scout", "worker"])
        self.assertEqual(sum(record["tokens"] for record in children), 550)
        self.assertEqual(state["total_tokens"], 685)
        self.assertAlmostEqual(state["total_cost"], 0.3033)

    def test_unreconciled_details_fall_back_to_one_aggregate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, mode="parallel",
                arguments={"tasks": [
                    {"agent": "scout", "task": self.CONTENT_CANARY},
                    {"agent": "worker", "task": self.CONTENT_CANARY},
                ]},
                results=[
                    self._child_result("scout", usage=self._child_usage(300, 30, cost=0.1)),
                    self._child_result("worker", usage=self._child_usage(200, 20, cost=0.2)),
                ],
                top_usage=self._child_usage(900, 90, cost=0.9),
            )
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(len(children), 1)
        self.assertIsNone(children[0]["role"])
        self.assertEqual(children[0]["tokens"], 990)
        self.assertEqual(state["total_tokens"], 1125)
        self.assertAlmostEqual(state["total_cost"], 0.9033)

    def test_run_without_usage_reports_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, results=[self._child_result()])
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertFalse(child["tokens_available"])
        self.assertFalse(child["cost_available"])
        self.assertIsNone(child["tokens"])
        self.assertIsNone(child["cost"])
        self.assertFalse(summary["availability"]["cost"])
        self.assertFalse(summary["availability"]["tokens"])
        self.assertFalse(state["availability"]["cost"])
        self.assertFalse(state["availability"]["tokens"])
        self.assertEqual(state["total_tokens"], 135)

    def test_failed_child_run_marks_incomplete_and_keeps_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, results=[
                self._child_result("reviewer", usage=self._child_usage(cost=0.05),
                                   exit_code=1, stop_reason="error"),
            ])
            _source, _state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertEqual(child["role"], "reviewer")
        self.assertEqual(child["activity_state"], "incomplete")
        self.assertEqual(child["failed_attempts"], 1)
        self.assertIsNone(child["ended_at"])

    def test_partial_failure_keeps_successful_sibling_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            path = self._write_subagent_session(root, mode="parallel", results=[
                self._child_result("scout", usage=self._child_usage(cost=0.1)),
                self._child_result("worker", usage=self._child_usage(cost=0.0),
                                   exit_code=1, stop_reason="error"),
            ])
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[-1]["message"]["isError"] = True
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            _source, _state, summary = self._load(root)

        children = {
            record["role"]: record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        }
        self.assertEqual(children["scout"]["activity_state"], "complete")
        self.assertEqual(children["worker"]["activity_state"], "incomplete")
        self.assertEqual(children["worker"]["failed_attempts"], 1)

    def test_zero_cost_child_is_a_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage(cost=0.0))],
            )
            _source, _state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertTrue(child["cost_available"])
        self.assertEqual(child["cost"], 0.0)
        self.assertTrue(summary["availability"]["cost"])

    def test_unfinished_child_call_is_incomplete_without_invented_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, include_result=False)
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertEqual(child["activity_state"], "incomplete")
        self.assertEqual(child["failed_attempts"], 0)
        self.assertFalse(child["tokens_available"])
        self.assertFalse(child["cost_available"])
        self.assertEqual(state["total_tokens"], 135)
        self.assertEqual(state["total_cost"], 0.0033)

    def test_fresh_unfinished_child_call_is_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, include_result=False, recent_seconds=10,
            )
            _source, _state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertEqual(child["activity_state"], "working")
        self.assertEqual(child["failed_attempts"], 0)

    def test_finished_session_root_reports_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage())],
            )
            _source, _state, summary = self._load(root)

        # Session-row liveness is intentionally unchanged for this PR.
        self.assertFalse(summary["terminal"])
        root_record = self._records(summary)["root"]
        self.assertEqual(root_record["activity_state"], "complete")
        self.assertEqual(
            root_record["ended_at"], root_record["last_activity_at"],
        )

    def test_realistic_tool_use_then_stop_marks_root_complete(self):
        final_usage = {
            "input": 50, "output": 10, "cacheRead": 0, "cacheWrite": 0,
            "totalTokens": 60,
            "cost": {"input": 0.0005, "output": 0.0005,
                     "cacheRead": 0.0, "cacheWrite": 0.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, stop_reason="toolUse",
                results=[self._child_result(usage=self._child_usage())],
                final_turn_usage=final_usage,
            )
            _source, state, summary = self._load(root)

        self.assertEqual(summary["turns"], 2)
        self.assertEqual(
            self._records(summary)["root"]["activity_state"], "complete",
        )
        self.assertAlmostEqual(state["total_cost"], 0.2543)

    def test_nonterminal_session_root_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, stop_reason="toolUse",
                results=[self._child_result(usage=self._child_usage())],
            )
            _source, _state, summary = self._load(root)

        self.assertFalse(summary["terminal"])
        root_record = self._records(summary)["root"]
        self.assertEqual(root_record["activity_state"], "incomplete")
        self.assertIsNone(root_record["ended_at"])

    def test_empty_results_run_emits_no_agent_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, results=[])
            _source, _state, summary = self._load(root)

        self.assertNotIn("_agent_records", summary)

    def test_child_role_rejects_paths_urls_credentials_and_prose(self):
        bad_roles = [
            "/Users/henry/agents/secret.md",
            "https://example.test/agent",
            "bearer abcdef",
            "api_key=abcdef",
            "a" * 80,
            "two words here",
            "scout\x00hidden",
        ]
        for bad in bad_roles:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(
                    root, results=[self._child_result(bad, usage=self._child_usage())],
                )
                _source, state, summary = self._load(root)
                child = self._records(summary)["spawned"]
                self.assertIsNone(child["role"])
                encoded = json.dumps({"summary": summary, "state": state})
                self.assertNotIn(bad, encoded)

    def test_child_model_rejects_credential_and_path_shapes(self):
        for bad in ("sk-secret-value", "bearer abc", "/Users/henry/model",
                    "https://example.test/model", "model with space",
                    "probe?x=1", "models/llama-3.gguf",
                    "Users/henry/.ssh/id_rsa", "a/../../etc/passwd",
                    "example.com/secret", "localhost:8080/model",
                    "home/henry/model", "C:/Users/henry/model",
                    "example.com:8080/secret", "127.0.0.1:8000/model",
                    "my-server.internal:8000/model", "10.0.0.5:9000/model"):
            with self.subTest(model=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(root, results=[
                    self._child_result(
                        usage=self._child_usage(), model=bad,
                    ),
                ])
                _source, state, summary = self._load(root)

                child = self._records(summary)["spawned"]
                self.assertEqual(child["model"], "unknown-model")
                encoded = json.dumps({"summary": summary, "state": state})
                self.assertNotIn(bad, encoded)

    def test_child_model_accepts_provider_prefixed_ids(self):
        good_models = (
            "@cf/meta/llama-3.1-8b-instruct",
            "opencode-go/deepseek-v4.1-flash",
            "anthropic/claude-opus-4-8",
            "meta-llama/Llama-3.1-70B-Instruct",
            "accounts/fireworks/models/llama-v3p1-70b-instruct",
            "qwen2.5-coder:32b",
            "amazon.nova-pro-v1:0",
        )
        for good in good_models:
            with self.subTest(model=good), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(root, results=[
                    self._child_result(usage=self._child_usage(), model=good),
                ])
                _source, _state, summary = self._load(root)

                child = self._records(summary)["spawned"]
                self.assertEqual(child["model"], good.lower())

    def test_unreported_child_model_is_unknown_not_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(root, results=[
                self._child_result(usage=self._child_usage(), model=""),
            ])
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertEqual(child["model"], "unknown-model")
        self.assertEqual(summary["primary_model"], "claude-test")
        self.assertEqual(summary["_model_cost"]["unknown-model"], 0.25)
        self.assertAlmostEqual(summary["_model_cost"]["claude-test"], 0.0033)
        mix = {row["model"]: row["cost"] for row in state["analyses"]["model_mix"]}
        self.assertEqual(mix["unknown-model"], 0.25)
        self.assertAlmostEqual(mix["claude-test"], 0.0033)

    def test_child_content_never_reaches_summary_state_or_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage())],
            )
            _source, state, summary = self._load(root)

        # The records must exist for this to be a meaningful negative check.
        self.assertTrue(summary.get("_agent_records"))
        encoded = json.dumps({"summary": summary, "state": state})
        self.assertNotIn(self.CONTENT_CANARY, encoded)
        self.assertNotIn("stderr", encoded)
        self.assertNotIn("agentSource", encoded)
        self.assertNotIn("messages", encoded)

    def test_non_subagent_tool_usage_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, tool_name="summarize",
                results=[self._child_result(usage=self._child_usage())],
            )
            _source, state, summary = self._load(root)

        self.assertEqual(state["total_tokens"], 135)
        self.assertNotIn("_agent_records", summary)
        # A non-subagent Pi session keeps its pre-existing session liveness.
        self.assertFalse(summary["terminal"])

    def _write_many_calls(self, agent_root, call_results, *, extra_rows=()):
        """Write one parent turn issuing N subagent calls with given results."""
        directory = Path(agent_root) / "sessions" / "--repo--"
        directory.mkdir(parents=True)
        path = directory / "pi-subagent-many.jsonl"
        calls = [
            {"type": "toolCall", "id": "call-{}".format(index),
             "name": "subagent", "arguments": {"agent": "probe"}}
            for index in range(len(call_results))
        ]
        rows = [
            {"type": "session", "version": 3, "id": "pi-session",
             "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"},
            {"type": "message", "id": "user", "timestamp": "2026-09-04T10:00:01Z",
             "message": {"role": "user", "content": []}},
            {"type": "message", "id": "assistant",
             "timestamp": "2026-09-04T10:00:02Z",
             "message": {
                 "role": "assistant", "model": "claude-test",
                 "provider": "anthropic", "stopReason": "stop",
                 "content": calls,
                 "usage": {"input": 100, "output": 20, "cacheRead": 10,
                           "cacheWrite": 5,
                           "cost": {"input": 0.001, "output": 0.002,
                                    "cacheRead": 0.0001, "cacheWrite": 0.0002}},
             }},
        ]
        for index, results in enumerate(call_results):
            rows.append({
                "type": "message", "id": "result-{}".format(index),
                "timestamp": "2026-09-04T10:00:05Z",
                "message": {
                    "role": "toolResult", "toolCallId": "call-{}".format(index),
                    "toolName": "subagent",
                    "details": {"mode": "single", "results": results},
                },
            })
        rows.extend(extra_rows)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def _native_load(self, root):
        adapter = pi_runtime.PiRuntimeAdapter(root)
        source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
        return adapter.load(source, meter.DetailLevel.SUMMARY)

    def test_call_cap_marks_nested_spend_partial_not_complete(self):
        one_cent = [self._child_result(usage=self._child_usage(10, 1, cost=0.01))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [one_cent] * 600)
            _source, state, summary = self._load(root)
            loaded = self._native_load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertLessEqual(len(children), pi_runtime.MAX_SUBAGENT_RUNS)
        for payload in (state, summary):
            self.assertFalse(payload["availability"]["cost"])
            self.assertFalse(payload["availability"]["tokens"])
            self.assertFalse(payload["availability"]["cache"])
        self.assertIsNone(loaded.usage.cost_usd.value)
        self.assertIsNone(loaded.usage.input_tokens.value)
        self.assertIsNone(loaded.usage.cache_read_tokens.value)
        self.assertIn("history_truncated",
                      [warning.code for warning in loaded.warnings])

    def test_per_call_details_respect_the_run_cap(self):
        single = [self._child_result(usage=self._child_usage(10, 1, cost=0.01))]
        wide = [
            self._child_result(usage=self._child_usage(10, 1, cost=0.01))
            for _ in range(pi_runtime.MAX_SUBAGENT_RUNS)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(
                root, [single] * (pi_runtime.MAX_SUBAGENT_RUNS - 1) + [wide],
            )
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(len(children), pi_runtime.MAX_SUBAGENT_RUNS)
        self.assertFalse(summary["availability"]["cost"])
        self.assertFalse(state["availability"]["cost"])

    def test_calls_within_the_cap_stay_complete(self):
        one_cent = [self._child_result(usage=self._child_usage(10, 1, cost=0.01))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [one_cent] * 3)
            _source, state, summary = self._load(root)

        self.assertTrue(summary["availability"]["cost"])
        self.assertTrue(state["availability"]["cost"])
        self.assertAlmostEqual(state["total_cost"], 0.0333)

    def test_nonempty_results_without_valid_entries_is_an_unavailable_run(self):
        for results in (["not-a-dict"], [None, 7], [["nested"]]):
            with self.subTest(results=results), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(root, results=results)
                _source, state, summary = self._load(root)

                children = [
                    record for record in summary.get("_agent_records", [])
                    if record["kind"] == "spawned"
                ]
                self.assertEqual(len(children), 1)
                self.assertFalse(children[0]["tokens_available"])
                self.assertFalse(children[0]["cost_available"])
                self.assertFalse(summary["availability"]["cost"])
                self.assertFalse(state["availability"]["cost"])

    def test_non_list_results_without_usage_is_an_unavailable_run(self):
        for results in ("not-a-list", {"agent": "probe"}, 7):
            with self.subTest(results=results), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                # Written raw: details.results is the malformed value itself.
                self._write_many_calls(root, [results])
                _source, state, summary = self._load(root)

                children = [
                    record for record in summary.get("_agent_records", [])
                    if record["kind"] == "spawned"
                ]
                self.assertEqual(len(children), 1)
                self.assertFalse(children[0]["tokens_available"])
                self.assertFalse(children[0]["cost_available"])
                self.assertFalse(summary["availability"]["cost"])
                self.assertFalse(state["availability"]["cost"])

    def test_call_cap_surfaces_in_cross_session_agent_inventory(self):
        one_cent = [self._child_result(usage=self._child_usage(10, 1, cost=0.01))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [one_cent] * 600)
            _source, _state, summary = self._load(root)
            cross = self._cross(root)

        self.assertTrue(summary["_agent_records_partial"])
        self.assertNotIn("_agent_records_truncated", summary)
        usage = cross["agent_usage"]
        self.assertEqual(usage["totals"]["agents"], pi_runtime.MAX_SUBAGENT_RUNS)
        self.assertFalse(usage["totals"]["cost_available"])
        self.assertFalse(usage["totals"]["tokens_available"])
        self.assertIsNone(usage["totals"]["cost"])
        self.assertAlmostEqual(
            usage["totals"]["known_cost"], 0.01 * pi_runtime.MAX_SUBAGENT_RUNS,
        )
        # The run cap is partial coverage, not the inventory display limit.
        self.assertFalse(usage["inventory_truncated"])
        for row in usage["roles"] + usage["models"]:
            self.assertFalse(row["cost_available"], row["id"])
            self.assertEqual(
                row["cost_covered_agents"], row["agents"], row["id"],
            )
        public = json.dumps(cross, default=str)
        self.assertNotIn("_agent_records_partial", public)
        self.assertNotIn("_coverage_partial", public)

    def test_per_call_result_cap_surfaces_in_cross_session_inventory(self):
        wide = [
            self._child_result(usage=self._child_usage(10, 1, cost=0.01))
            for _ in range(pi_runtime.MAX_SUBAGENT_RUNS + 5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [wide])
            cross = self._cross(root)

        usage = cross["agent_usage"]
        self.assertFalse(usage["inventory_truncated"])
        self.assertFalse(usage["totals"]["cost_available"])
        self.assertIsNone(usage["totals"]["cost"])
        self.assertGreater(usage["totals"]["known_cost"], 0)

    def test_calls_within_the_cap_keep_cross_session_inventory_complete(self):
        one_cent = [self._child_result(usage=self._child_usage(10, 1, cost=0.01))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [one_cent] * 3)
            _source, _state, summary = self._load(root)
            cross = self._cross(root)

        self.assertNotIn("_agent_records_truncated", summary)
        self.assertNotIn("_agent_records_partial", summary)
        usage = cross["agent_usage"]
        self.assertEqual(usage["totals"]["agents"], 3)
        self.assertTrue(usage["totals"]["cost_available"])
        self.assertTrue(usage["totals"]["tokens_available"])
        self.assertFalse(usage["inventory_truncated"])

    def test_repeated_call_id_does_not_overwrite_finished_evidence(self):
        repeat = {
            "type": "message", "id": "assistant-repeat",
            "timestamp": "2026-09-04T10:00:09Z",
            "message": {
                "role": "assistant", "model": "claude-test",
                "provider": "anthropic", "stopReason": "stop",
                "content": [{"type": "toolCall", "id": "call-0",
                             "name": "subagent",
                             "arguments": {"agent": "other"}}],
                "usage": {"input": 1, "output": 1, "cacheRead": 0,
                          "cacheWrite": 0,
                          "cost": {"input": 0.0, "output": 0.0,
                                   "cacheRead": 0.0, "cacheWrite": 0.0}},
            },
        }
        result = [self._child_result(usage=self._child_usage(cost=0.25))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_many_calls(root, [result], extra_rows=[repeat])
            _source, state, summary = self._load(root)

        children = [
            record for record in summary["_agent_records"]
            if record["kind"] == "spawned"
        ]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["role"], "probe")
        self.assertEqual(children[0]["activity_state"], "complete")
        self.assertAlmostEqual(children[0]["cost"], 0.25)
        self.assertTrue(state["availability"]["cost"])
        self.assertAlmostEqual(state["total_cost"], 0.2533)

    def test_child_without_cache_keys_is_not_a_complete_token_total(self):
        usage = self._child_usage()
        del usage["cacheRead"]
        del usage["cacheWrite"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=usage)],
            )
            _source, state, summary = self._load(root)

        child = self._records(summary)["spawned"]
        self.assertFalse(child["cache_available"])
        self.assertFalse(child["tokens_available"])
        self.assertIsNone(child["tokens"])
        self.assertFalse(summary["availability"]["cache"])
        self.assertFalse(state["availability"]["cache"])

    def _cross(self, root):
        saved = dict(meter._xsess)
        try:
            meter._xsess.update({"data": None, "at": 0, "agent_groups": ()})
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                return meter.cross_session(sources=meter.pi_session_sources())
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved)

    def test_child_model_sanitizer_table(self):
        accepted = (
            "claude-opus-5-5", "anthropic/claude-sonnet-5-5", "openai/gpt-5.6",
            "gpt-5.6-sol", "grok-4.7", "llama3.1:8b", "qwen2.5-coder:32b",
            "amazon.nova-pro-v1:0", "claude-opus-4@20250514",
            "@cf/meta/llama-3.1-8b-instruct",
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "us.anthropic.claude-opus-5-5-v1:0",
            "eu.anthropic.claude-sonnet-5-5-v1:0",
            "meta.llama3-70b-instruct-v1:0", "claude-sonnet-4@20250514",
            "model@latest", "model@v2.1.0", "model@1.2", "model@v1",
            "@cf/meta/llama-3-8b", "accounts/fireworks/models/x",
            "openrouter/anthropic/claude-opus-5-5", "gemini-2.5-pro",
            "deepseek-r1:70b", "qwen2.5-coder:7b", "gpt-oss:20b",
            "phi3:3.8b", "model:latest", "hf.co/unsloth/model",
            "huggingface.co/org/model",
            # Short vendor prefixes followed by an ordinary word stay names.
            "xai-researcher", "hf_transformers",
        )
        rejected = (
            "internal.corp.example.com:8443", "10.0.0.5:11434", "10.0.0.5",
            "localhost:8080", "internal.corp.example.com",
            "alice@corp.example.com", "user@host", "model@corp.example.com",
            "sk-proj-abc123", "openai/sk-proj-abc123",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
            "ghp_abcdefghijklmnop", "gho_abcdefghijklmnop",
            "github_pat_11ABCDEFG", "xoxb-1234-5678-abcd", "xoxp-1234-5678",
            "AKIAIOSFODNN7EXAMPLE", "vendor/AKIAIOSFODNN7EXAMPLE",
            # Host, IP, or host:port in any path segment.
            "ollama/10.0.0.5:11434", "provider/host.example.com:8443/m",
            "hf.co/10.0.0.5:8080/model", "vendor/gateway:8080",
            # Account or host after @ instead of a version.
            "bob@10.0.0.5", "model@1.2.3.4", "a@1host.corp.com",
            "ollama@192.168.1.4", "model@2.example.com",
            # Single-label host with a port-shaped tag.
            "myhost:11434", "gateway:8080",
            # Additional vendor credential prefixes, any case.
            "hf_AbCdEfGhIjKlMnOpQrStUvWx", "HF_AbCdEfGhIjKlMnOpQrStUvWx",
            "xai-AbCdEfGhIjKlMnOpQrStUv", "AIzaSyA1234567890abcdefghij",
            "glpat-abcdefgh", "GLPAT-abcdefgh", "npm_AbCdEfGhIjKlMnOpQrStUv",
            "sk_live_abc123", "sk_test_abc123", "pk_live_abc123",
            "rk_live_abc123", "akiaiosfodnn7example",
            "vendor/glpat-abcdefgh",
        )
        for value in accepted:
            self.assertEqual(pi_runtime._subagent_model_id(value), value)
        for value in rejected:
            self.assertEqual(pi_runtime._subagent_model_id(value), "", value)
        for value in rejected:
            with self.subTest(model=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(root, results=[
                    self._child_result(usage=self._child_usage(), model=value),
                ])
                _source, state, summary = self._load(root)
                cross = self._cross(root)

                child = self._records(summary)["spawned"]
                self.assertEqual(child["model"], "unknown-model")
                encoded = json.dumps(
                    {"summary": summary, "state": state, "cross": cross},
                    default=str,
                )
                self.assertNotIn(value, encoded)
                self.assertNotIn(value.lower(), encoded)

    def test_child_role_rejects_secret_token_shapes(self):
        accepted = (
            "probe", "scout", "task-runner", "worker_2", "skeptic",
            # Short prefixes need a 16+ character key-like run to count.
            "xai-researcher", "hf_helper", "npm_runner", "aizawa",
        )
        rejected = (
            "sk-abcdef", "my-sk-abcdef", "ghp_abcdef", "gho_abcdef",
            "github_pat_11ABC", "xoxb-1234", "xoxp-1234",
            "AKIAIOSFODNN7EXAMPLE", "akiaiosfodnn7example",
            "hf_AbCdEfGhIjKlMnOpQr", "xai-AbCdEfGhIjKlMnOpQr",
            "npm_AbCdEfGhIjKlMnOpQr", "AIzaSyA1234567890abcdef",
            "glpat-abcdef", "sk_live_abc", "sk_test_abc", "pk_live_abc",
            "rk_live_abc", "worker-glpat-x",
        )
        for value in accepted:
            self.assertEqual(pi_runtime._safe_agent_role(value), value)
        for value in rejected:
            self.assertEqual(pi_runtime._safe_agent_role(value), "", value)
        for value in rejected:
            with self.subTest(role=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "agent"
                self._write_subagent_session(
                    root, arguments={"agent": value},
                    results=[self._child_result(value, usage=self._child_usage())],
                )
                _source, state, summary = self._load(root)
                cross = self._cross(root)

                self.assertIsNone(self._records(summary)["spawned"]["role"])
                encoded = json.dumps(
                    {"summary": summary, "state": state, "cross": cross},
                    default=str,
                )
                self.assertNotIn(value, encoded)

    def test_canonical_agent_sources_selects_pi_rows(self):
        source = {
            "id": "pi-session", "path": "/tmp/pi.jsonl", "provider": "pi",
            "runtime": "Pi", "label": "Pi", "project": "/repo", "mtime": 1,
        }
        other = {
            "id": "kiro-session", "path": "/tmp/kiro.json",
            "provider": "kiro", "runtime": "Kiro", "label": "Kiro",
            "project": "/repo", "mtime": 1,
        }
        selected = meter.canonical_agent_sources([source, other])
        self.assertEqual(selected, [source])

    def test_agent_group_and_inventory_include_pi_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            self._write_subagent_session(
                root, results=[self._child_result(usage=self._child_usage())],
            )
            _source, _state, summary = self._load(root)

        groups = meter._domain_build_agent_groups([summary])
        usage = meter._agent_usage_projection(
            meter._domain_aggregate_agent_usage(groups)
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(usage["totals"]["agents"], 1)
        self.assertAlmostEqual(usage["totals"]["cost"], 0.25)
        self.assertEqual(usage["inventory"][0]["role"], "probe")
        self.assertEqual(usage["inventory"][0]["runtime"], "pi")
        self.assertEqual(
            usage["models"][0]["id"],
            "opencode-go/deepseek-v4.1-flash::pi::spawned",
        )


if __name__ == "__main__":
    unittest.main()
