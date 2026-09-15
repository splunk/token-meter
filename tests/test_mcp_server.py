import io
import json
import os
import unittest
from unittest import mock

import token_meter_mcp as server
from token_meter.mcp.contracts import MCPQueryError


class McpProtocolTests(unittest.TestCase):
    def test_warm_and_fallback_preserve_the_same_bounded_caller_for_caller_aware_tools(self):
        # Break caught: warm evidence drops the caller context, allowing a
        # caller-aware tool to select a fresh run outside the caller project.
        class Response:
            status = 200

            def __init__(self, body):
                self.body = body

            def read(self, _size):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        caller = {"runtime": "token-meter-coach", "project": "/private/tmp/coach"}
        environment = {
            "TOKEN_METER_COACH_EVIDENCE_URL": "http://127.0.0.1:8722/coach/evidence",
            "TOKEN_METER_COACH_ACTION_TOKEN": "SENTINEL-ACTION",
            "TOKEN_METER_COACH_EVIDENCE_TOKEN": "SENTINEL-INTERNAL",
            "TOKEN_METER_CALLER": caller["runtime"],
            "TOKEN_METER_PROJECT": caller["project"],
        }
        warm_requests = []

        def warm_opener(request, timeout):
            self.assertEqual(timeout, server.COACH_EVIDENCE_TIMEOUT_SECONDS)
            warm_requests.append(request)
            return Response(b'{"ok":true,"data_scope":"matched_current_run"}')

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            server.meter, "application"
        ) as local:
            warm = server.call_tool("check", {"focus": "continue"}, opener=warm_opener)

        self.assertEqual(warm["structuredContent"], {"ok": True, "data_scope": "matched_current_run"})
        self.assertEqual(json.loads(warm_requests[0].data.decode("utf-8")), {
            "tool": "check", "arguments": {"focus": "continue"}, "caller": caller,
        })
        self.assertEqual(
            warm_requests[0].get_header("X-token-meter-coach-evidence"),
            "SENTINEL-INTERNAL",
        )
        local.assert_not_called()

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            server.meter, "application"
        ) as local:
            local.return_value.agent_api.check.return_value = {"ok": True, "data_scope": "fallback"}
            fallback = server.call_tool(
                "check", {"focus": "continue"},
                opener=lambda _request, timeout: (_ for _ in ()).throw(OSError()),
            )

        self.assertEqual(fallback["structuredContent"], {"ok": True, "data_scope": "fallback"})
        local.return_value.agent_api.check.assert_called_once_with(
            focus="continue", caller=caller,
        )

    def test_warm_coach_response_bypasses_local_application_and_sends_header_only(self):
        class Response:
            status = 200

            def read(self, _size):
                return b'{"ok":true,"data_scope":"usage_7d_spend"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        opened = []

        def opener(request, timeout):
            opened.append((request, timeout))
            return Response()

        with mock.patch.dict(os.environ, {
            "TOKEN_METER_COACH_EVIDENCE_URL": "http://127.0.0.1:8722/coach/evidence",
            "TOKEN_METER_COACH_ACTION_TOKEN": "SENTINEL-BRIDGE",
            "TOKEN_METER_COACH_EVIDENCE_TOKEN": "SENTINEL-INTERNAL",
        }, clear=False), mock.patch.object(server.meter, "application") as local:
            result = server.call_tool("usage", {"window": "7d", "focus": "spend"}, opener=opener)

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], {"ok": True, "data_scope": "usage_7d_spend"})
        local.assert_not_called()
        request, timeout = opened[0]
        self.assertEqual(request.get_header("X-token-meter-action"), "SENTINEL-BRIDGE")
        self.assertNotIn("SENTINEL-BRIDGE", request.data.decode("utf-8"))
        self.assertEqual(timeout, server.COACH_EVIDENCE_TIMEOUT_SECONDS)

    def test_warm_coach_failures_fall_back_to_local_once_and_invalid_calls_do_not_open(self):
        class Response:
            def __init__(self, body, status=200):
                self.body = body
                self.status = status

            def read(self, _size):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        failures = (
            OSError("private failure"),
            TimeoutError("private timeout"),
            Response(b'{"ok":false}', status=503),
            Response(b"x" * (server.MAX_COACH_EVIDENCE_RESPONSE_BYTES + 1)),
            Response(b"not-json"),
            Response(b"[]"),
        )
        with mock.patch.dict(os.environ, {
            "TOKEN_METER_COACH_EVIDENCE_URL": "http://127.0.0.1:8722/coach/evidence",
            "TOKEN_METER_COACH_ACTION_TOKEN": "SENTINEL-BRIDGE",
        }, clear=False):
            for failure in failures:
                with self.subTest(failure=type(failure).__name__), mock.patch.object(
                    server.meter, "application"
                ) as local:
                    local.return_value.agent_api.usage.return_value = {"ok": True, "data_scope": "local"}
                    def opener(_request, timeout, failure=failure):
                        self.assertEqual(timeout, server.COACH_EVIDENCE_TIMEOUT_SECONDS)
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    result = server.call_tool("usage", {"window": "7d"}, opener=opener)
                self.assertEqual(result["structuredContent"], {"ok": True, "data_scope": "local"})
                local.return_value.agent_api.usage.assert_called_once_with(window="7d")

            opened = mock.Mock()
            with mock.patch.object(server.meter, "application") as local:
                invalid = server.call_tool("erase", {}, opener=opened)
                extra = server.call_tool("usage", {"window": "7d", "secret": True}, opener=opened)
            self.assertTrue(invalid["isError"])
            self.assertTrue(extra["isError"])
            opened.assert_not_called()
            local.assert_not_called()

    def test_shared_agent_dispatch_rejects_unknown_and_extra_arguments(self):
        from token_meter.services.agent_api import dispatch_agent_tool

        service = mock.Mock()
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            dispatch_agent_tool(service, "erase", {}, caller={})
        with self.assertRaisesRegex(ValueError, "unsupported argument"):
            dispatch_agent_tool(
                service, "usage", {"window": "7d", "secret": True}, caller={},
            )
        with self.assertRaisesRegex(ValueError, "must be an object"):
            dispatch_agent_tool(service, "usage", ["7d"], caller={})
        self.assertEqual(service.mock_calls, [])

    def test_shared_agent_dispatch_matches_every_advertised_tool(self):
        from token_meter.services.agent_api import dispatch_agent_tool

        service = mock.Mock()
        cases = (
            ("check", {"focus": "continue"}, {"caller": {"runtime": "test"}, "focus": "continue"}),
            ("usage", {"window": "7d"}, {"window": "7d"}),
            ("capabilities", {"limit": 1}, {"caller": {"runtime": "test"}, "limit": 1}),
            ("sessions", {"scope": "all"}, {"caller": {"runtime": "test"}, "scope": "all"}),
            ("trace", {"session_id": "s"}, {"session_id": "s"}),
            ("stats", {"metrics": ["cost_usd"]}, {"metrics": ["cost_usd"]}),
            ("goal", {"focus": "progress"}, {"focus": "progress"}),
            ("schema", {"subject": "stats"}, {"subject": "stats"}),
        )

        for name, arguments, expected in cases:
            with self.subTest(name=name):
                dispatch_agent_tool(service, name, arguments, caller={"runtime": "test"})
                getattr(service, name).assert_called_once_with(**expected)
                getattr(service, name).reset_mock()

    def test_initialize_negotiates_and_advertises_only_read_only_tools(self):
        response, initialized = server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertTrue(initialized)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "tokenmeter")
        self.assertIn("read-only", result["instructions"])

        listed, _ = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, initialized=True)
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "check", "usage", "capabilities", "sessions", "trace",
                "stats", "goal", "schema",
            ],
        )
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        capabilities = next(tool for tool in tools if tool["name"] == "capabilities")
        self.assertEqual(
            capabilities["inputSchema"]["properties"]["limit"],
            {
                "type": "integer", "minimum": 1, "maximum": 5, "default": 5,
                "description": "Maximum candidate skill packs to return; must be from 1 through 5.",
            },
        )

    def test_stats_description_explains_metric_grain_restriction(self):
        listed, _ = server.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            initialized=True,
        )
        stats = next(
            tool for tool in listed["result"]["tools"] if tool["name"] == "stats"
        )
        self.assertIn("query tool metrics separately", stats["description"].lower())
        for field in ("start", "end"):
            self.assertIn(
                "execution timestamp",
                stats["inputSchema"]["properties"][field]["description"].lower(),
            )

    def test_advertised_stats_metrics_and_dimensions_match_the_resolver_schema(self):
        # Break caught: the stdio server advertises a metric or dimension the
        # query service cannot resolve, or omits one it can, so a valid stats
        # call is rejected or an advertised name never returns evidence.
        from token_meter.mcp.schema import DIMENSIONS, METRICS

        self.assertEqual(set(server.STATS_METRICS), set(METRICS))
        self.assertEqual(set(server.STATS_DIMENSIONS), set(DIMENSIONS))
        self.assertIn("reasoning_tokens", server.STATS_METRICS)

    def test_tool_calls_require_initialization(self):
        response, initialized = server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        }, initialized=False)
        self.assertFalse(initialized)
        self.assertEqual(response["error"]["code"], -32002)

    def test_successful_call_returns_matching_text_and_structured_content(self):
        payload = {"ok": True, "answer": "Continue", "evidence": [],
                   "recommended_action": "Keep going", "caveat": "Estimated",
                   "dashboard_url": "http://127.0.0.1:8722/#summary", "as_of": "now",
                   "data_scope": "matched_current_run", "truncated": False}
        with mock.patch.object(server.meter, "agent_check", return_value=payload) as builder:
            response, _ = server.dispatch({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "check", "arguments": {"focus": "continue"}},
            }, initialized=True)
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)
        builder.assert_called_once()

    def test_invalid_arguments_are_a_bounded_tool_error(self):
        response, _ = server.dispatch({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "usage", "arguments": {"window": "forever"}},
        }, initialized=True)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("window must be one of", response["result"]["structuredContent"]["error"])

    def test_query_tool_returns_matching_structured_content(self):
        payload = {
            "ok": True,
            "schema_version": "1.0",
            "subject": "stats",
            "data_scope": "query_schema",
        }
        agent_api = server.meter.application().agent_api
        with mock.patch.object(agent_api, "schema", return_value=payload) as builder:
            result = server.call_tool("schema", {"subject": "stats"})

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        builder.assert_called_once_with(subject="stats")

    def test_query_error_keeps_stable_code_and_sanitized_message(self):
        agent_api = server.meter.application().agent_api
        with mock.patch.object(
            agent_api,
            "trace",
            side_effect=MCPQueryError(
                "session_not_found", "the requested session was not found",
            ),
        ):
            result = server.call_tool("trace", {"session_id": "missing"})

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error_code"], "session_not_found",
        )
        self.assertEqual(
            result["structuredContent"]["error"],
            "the requested session was not found",
        )

    def test_query_tool_rejects_unknown_arguments_before_delegation(self):
        agent_api = server.meter.application().agent_api
        with mock.patch.object(agent_api, "sessions") as builder:
            result = server.call_tool("sessions", {"raw": True})

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error_code"], "invalid_argument",
        )
        builder.assert_not_called()

    def test_goal_tool_is_read_only_and_delegates_only_allowlisted_focus(self):
        payload = {
            "ok": True, "as_of": 1000, "data_scope": "coach_goal_progress",
            "goal": None, "progress": None,
        }
        agent_api = server.meter.application().agent_api
        with mock.patch.object(agent_api, "goal", return_value=payload) as builder:
            result = server.call_tool("goal", {"focus": "progress"})

        tool = next(row for row in server.TOOLS if row["name"] == "goal")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], payload)
        builder.assert_called_once_with(focus="progress")

        with mock.patch.object(agent_api, "goal") as rejected:
            invalid = server.call_tool("goal", {"prompt": "SENTINEL-PRIVATE"})
        self.assertTrue(invalid["isError"])
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(invalid))
        rejected.assert_not_called()

    def test_stdio_transcript_is_one_json_object_per_line(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "unsupported"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        stdout = io.StringIO()
        server.serve(stdin, stdout)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        responses = [json.loads(line) for line in lines]
        self.assertEqual(responses[0]["result"]["protocolVersion"], server.DEFAULT_PROTOCOL_VERSION)
        self.assertEqual([row["id"] for row in responses], [1, 2, 3])
        self.assertEqual([tool["name"] for tool in responses[1]["result"]["tools"]],
                         ["check", "usage", "capabilities", "sessions", "trace",
                          "stats", "goal", "schema"])


if __name__ == "__main__":
    unittest.main()
