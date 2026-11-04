import io
import json
import unittest
from unittest import mock

import token_meter_mcp as server
from token_meter.mcp.contracts import MCPQueryError


class McpProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_and_advertises_bounded_tools(self):
        response, initialized = server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertTrue(initialized)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["serverInfo"]["name"], "tokenmeter")
        self.assertIn("confirm: true", result["instructions"])

        listed, _ = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, initialized=True)
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "check", "usage", "budget", "set_session_budget",
                "set_default_session_budget", "capabilities", "sessions",
                "trace", "stats", "git_stats", "work_stats", "schema",
            ],
        )
        for tool in tools:
            self.assertFalse(tool["annotations"]["destructiveHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        for tool in tools:
            self.assertEqual(
                tool["annotations"]["readOnlyHint"],
                tool["name"] not in {"set_session_budget", "set_default_session_budget"},
            )
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

    def test_budget_tools_expose_read_and_explicit_mutation_contracts(self):
        """A budget cap must be inspectable and writable only deliberately."""
        payload = {
            "ok": True,
            "data_scope": "session_budget",
            "session": {"id": "session-1", "budget_usd": 5.0,
                        "source": "session_override", "spend_usd": 3.0,
                        "remaining_usd": 2.0, "percent_used": 60.0},
        }
        agent_api = mock.Mock()
        agent_api.budget.return_value = payload
        agent_api.set_session_budget.return_value = payload
        agent_api.set_default_session_budget.return_value = payload
        application = mock.Mock(agent_api=agent_api)

        with mock.patch.object(server.meter, "application", return_value=application):
            listed, _ = server.dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                initialized=True,
            )
            tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
            self.assertIn("budget", tools)
            self.assertIn("set_session_budget", tools)
            self.assertIn("set_default_session_budget", tools)
            self.assertTrue(tools["budget"]["annotations"]["readOnlyHint"])
            self.assertFalse(tools["set_session_budget"]["annotations"]["readOnlyHint"])
            self.assertTrue(tools["set_session_budget"]["inputSchema"]["properties"]["confirm"]["const"])

            read = server.call_tool("budget", {"session_id": "session-1"})
            changed = server.call_tool("set_session_budget", {
                "session_id": "session-1", "budget_usd": 5,
                "expected_current_budget_usd": 10, "confirm": True,
            })
            default_changed = server.call_tool("set_default_session_budget", {
                "budget_usd": 12, "expected_current_budget_usd": 10,
                "confirm": True,
            })

        self.assertFalse(read["isError"])
        self.assertEqual(read["structuredContent"], payload)
        self.assertFalse(changed["isError"])
        self.assertFalse(default_changed["isError"])
        agent_api.budget.assert_called_once_with(
            session_id="session-1", caller=mock.ANY,
        )
        agent_api.set_session_budget.assert_called_once_with(
            session_id="session-1", budget_usd=5,
            expected_current_budget_usd=10, confirm=True, caller=mock.ANY,
        )
        agent_api.set_default_session_budget.assert_called_once_with(
            budget_usd=12, expected_current_budget_usd=10, confirm=True,
        )

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
                         ["check", "usage", "budget", "set_session_budget",
                          "set_default_session_budget", "capabilities", "sessions",
                          "trace", "stats", "git_stats", "work_stats", "schema"])


if __name__ == "__main__":
    unittest.main()
