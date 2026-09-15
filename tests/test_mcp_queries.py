import json
import copy
import unittest


def synthetic_source_and_state(secret="SENTINEL-PRIVATE", session_id="session-1"):
    source = {
        "id": session_id,
        "provider": "codex",
        "client": "codex_cli",
        "model": "gpt-5.6",
        "model_provider": "openai",
        "path": "/private/{}/trace.jsonl".format(secret),
        "project": "/private/{}".format(secret),
        "title": secret,
        "label": secret,
        "mtime": 1_787_254_100.0,
    }
    state = {
        "provider": "codex",
        "client": "codex_cli",
        "primary_model": "gpt-5.6",
        "total_tokens": 170,
        "total_cost": 0.12,
        "cost_approx": True,
        "tokens": {
            "input": 150,
            "output": 20,
            "cache_read": 50,
            "cache_write": 0,
        },
        "timing": {
            "start_ts": 1_787_254_000.0,
            "end_ts": 1_787_254_050.0,
            "duration_s": 42,
            "duration_available": True,
        },
        "availability": {
            "tokens": True,
            "input_tokens": True,
            "output_tokens": True,
            "cost": True,
            "cache": True,
            "context": True,
            "timing": True,
            "tool_results": True,
        },
        "source": {
            "id": session_id,
            "provider": "codex",
            "client": "codex_cli",
            "model_provider": "openai",
            "path": source["path"],
            "project": source["project"],
            "title": secret,
        },
        "executions": [{
            "idx": 1,
            "ts": 1_787_254_010.0,
            "model": "gpt-5.6",
            "cost": 0.12,
            "duration_ms": 42_000,
            "wait_s": 50.0,
            "ttft_s": 1.5,
            "context_tokens": 1_000,
            "context_window": 2_000,
            "context_pct": 0.5,
            "model_calls": 2,
            "attempts": 2,
            "failed_attempts": 1,
            "retries": 1,
            "tokens": {
                "input": 100,
                "output": 20,
                "cache_read": 50,
                "cache_write": 0,
                "fresh_input": 50,
                "retrieval": 400,
            },
            "tools": [{
                "name": "exec_command",
                "namespace": "shell",
                "category": "execution",
                "output_tokens": 400,
                "error": False,
                "arguments": secret,
                "result": secret,
            }],
            "user_message": secret,
            "reasoning_summary": secret,
        }],
        "trace": [{
            "ts": 1_787_254_020.0,
            "kind": "tool_result",
            "native_type": "tool_result",
            "native_subtype": "function_call_output",
            "status": "completed",
            "label": "exec_command",
            "detail": secret,
            "execution": 1,
            "tool": "exec_command",
            "tokens": 400,
            "cost": 0.01,
            "severity": "neutral",
            "payload": {"content": secret, "settings": {"token": secret}},
        }],
        "tools": {
            "total_calls": 1,
            "total_errors": 0,
            "total_output_tokens": 400,
            "by_name": [{
                "name": "exec_command",
                "namespace": "shell",
                "display": "Command",
                "category": "execution",
                "calls": 1,
                "output_tokens": 400,
                "errors": 0,
                "arguments": secret,
            }],
        },
        "context": {
            "latest": 1_000,
            "peak": 1_100,
            "window": 2_000,
            "latest_pct": 0.5,
            "peak_pct": 0.55,
        },
        "user_inputs": [secret],
        "reasoning": secret,
        "settings": {"credential": secret},
        "trace_truncated": True,
    }
    return source, state


def synthetic_query_service():
    from token_meter.contracts import RuntimeDescriptor
    from token_meter.mcp.service import MCPQueryService

    first_source, first_state = synthetic_source_and_state(session_id="session-1")
    first_source["project"] = "/repo/a"
    first_state["trace"].append({
        "ts": 1_787_254_030.0,
        "kind": "usage",
        "native_type": "token_count",
        "execution": 1,
        "tokens": 20,
    })
    second_source = copy.deepcopy(first_source)
    second_source.update({
        "id": "session-2",
        "path": "/private/second.jsonl",
        "project": "/repo/b",
        "mtime": first_source["mtime"] - 10,
    })
    second_state = copy.deepcopy(first_state)
    second_state["source"]["id"] = "session-2"
    second_state["executions"][0]["tokens"]["input"] = 200
    second_state["total_tokens"] = 270
    sources = [first_source, second_source]
    states = {"session-1": first_state, "session-2": second_state}
    revisions = {"session-1": ("rev-1",), "session-2": ("rev-2",)}

    def summary(source):
        state = states[source["id"]]
        return {
            "primary_model": state["primary_model"],
            "terminal": False,
            "availability": state["availability"],
            "tokens": state["total_tokens"],
            "input_tokens": state["executions"][0]["tokens"]["input"],
            "output_tokens": state["executions"][0]["tokens"]["output"],
            "cost": state["total_cost"],
            "duration_s": state["timing"]["duration_s"],
        }

    service = MCPQueryService(
        sources=lambda: list(sources),
        find_session=lambda session_id, rows: next(
            (row for row in rows if row["id"] == session_id), None,
        ),
        summary=summary,
        state=lambda source: copy.deepcopy(states[source["id"]]),
        revision=lambda source: revisions[source["id"]],
        project_key=lambda value: str(value or "").lower(),
        runtime_descriptors=lambda: (RuntimeDescriptor(
            "codex", "Codex", frozenset(("sessions",)),
            "runtime.generic", "runtime-neutral",
        ),),
        now=lambda: 1_787_254_200.0,
    )
    service.revisions = revisions
    service.states = states
    return service


class MCPQueryContractTests(unittest.TestCase):
    def test_cursor_is_bound_to_query_and_revision(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(7, {"runtime": "codex"}, ("rev-1",))

        self.assertEqual(
            read_cursor(cursor, {"runtime": "codex"}, ("rev-1",)),
            7,
        )
        with self.assertRaises(MCPQueryError) as raised:
            read_cursor(cursor, {"runtime": "codex"}, ("rev-2",))
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_cursor_rejects_a_different_query_and_malformed_value(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            make_cursor,
            read_cursor,
        )

        cursor = make_cursor(1, {"runtime": "codex"}, ("rev-1",))

        for value, query in (
            (cursor, {"runtime": "claude"}),
            ("not-a-cursor", {"runtime": "codex"}),
        ):
            with self.subTest(value=value, query=query):
                with self.assertRaises(MCPQueryError) as raised:
                    read_cursor(value, query, ("rev-1",))
                self.assertEqual(raised.exception.code, "invalid_argument")

    def test_bounded_page_returns_complete_prefix_and_cursor(self):
        from token_meter.mcp.contracts import bounded_page, read_cursor

        query = {"view": "standardized"}
        revision = ("rev",)
        page = bounded_page(
            [
                {"value": "x" * 80},
                {"value": "y" * 80},
                {"value": "z" * 80},
            ],
            offset=0,
            limit=2,
            query=query,
            revision=revision,
            max_bytes=400,
        )

        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["truncated"])
        self.assertEqual(read_cursor(
            page["next_cursor"], query, revision,
        ), 1)

    def test_limits_and_string_lists_are_strictly_bounded(self):
        from token_meter.mcp.contracts import (
            MCPQueryError,
            normalize_limit,
            normalize_string_list,
        )

        self.assertEqual(normalize_limit(None, 20, 100), 20)
        self.assertEqual(
            normalize_string_list(
                ["events", "tools"], "sections",
                {"events", "tools", "context"}, 3,
            ),
            ("events", "tools"),
        )
        for value in (0, 101, "many"):
            with self.subTest(limit=value):
                with self.assertRaises(MCPQueryError) as raised:
                    normalize_limit(value, 20, 100)
                self.assertEqual(raised.exception.code, "invalid_argument")
        with self.assertRaises(MCPQueryError):
            normalize_string_list(
                ["events", "events"], "sections", {"events"}, 3,
            )


class MCPTraceProjectionTests(unittest.TestCase):
    def test_standardized_trace_is_detailed_and_content_free(self):
        from token_meter.mcp.projections import standardized_trace_projection

        source, state = synthetic_source_and_state()
        result = standardized_trace_projection(
            source,
            state,
            sections=(
                "session", "executions", "events", "tools", "context",
                "coverage", "warnings",
            ),
            execution=None,
            event_types=(),
        )

        encoded = json.dumps(result)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["executions"][0]["tokens"]["input"], 100)
        self.assertEqual(result["events"][0]["type"], "tool_result")
        self.assertEqual(result["tools"][0]["output_tokens"], 400)
        self.assertNotIn("SENTINEL-PRIVATE", encoded)
        self.assertNotIn(source["path"], encoded)
        self.assertNotIn("detail", encoded)
        self.assertNotIn("arguments", encoded)

    def test_native_structure_keeps_types_and_numbers_but_drops_payloads(self):
        from token_meter.mcp.projections import native_structure_projection

        source, state = synthetic_source_and_state()
        rows = native_structure_projection(source, state, None, ())

        self.assertEqual(rows[0]["native_type"], "tool_result")
        self.assertEqual(rows[0]["native_subtype"], "function_call_output")
        self.assertEqual(rows[0]["numeric"]["tokens"], 400)
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(rows))

    def test_execution_and_event_filters_are_applied_after_sanitizing(self):
        from token_meter.mcp.projections import standardized_trace_projection

        source, state = synthetic_source_and_state()

        missing = standardized_trace_projection(
            source, state, ("executions", "events"), 2, (),
        )
        selected = standardized_trace_projection(
            source, state, ("events",), 1, ("tool_result",),
        )

        self.assertEqual(missing["executions"], [])
        self.assertEqual(missing["events"], [])
        self.assertEqual(len(selected["events"]), 1)


class MCPQueryServiceTests(unittest.TestCase):
    def test_sessions_returns_paginated_content_free_inventory(self):
        service = synthetic_query_service()

        first = service.sessions(scope="all", runtime="codex", limit=1)
        second = service.sessions(
            scope="all", runtime="codex", limit=1,
            cursor=first["page"]["next_cursor"],
        )

        self.assertEqual(first["sessions"][0]["id"], "session-1")
        self.assertEqual(second["sessions"][0]["id"], "session-2")
        self.assertNotIn("project", json.dumps(first))
        self.assertIsNotNone(first["page"]["next_cursor"])

    def test_sessions_current_project_scope_uses_caller_without_echoing_it(self):
        service = synthetic_query_service()

        result = service.sessions(
            scope="current_project", caller={"project": "/repo/a"},
        )

        self.assertEqual([row["id"] for row in result["sessions"]], ["session-1"])
        self.assertNotIn("/repo/a", json.dumps(result))

    def test_trace_filters_and_rejects_changed_revision_cursor(self):
        from token_meter.mcp.contracts import MCPQueryError

        service = synthetic_query_service()
        first = service.trace(session_id="session-1", limit=1)

        self.assertEqual(first["schema_version"], "1.0")
        self.assertIsNotNone(first["page"]["next_cursor"])
        service.revisions["session-1"] = ("changed",)
        with self.assertRaises(MCPQueryError) as raised:
            service.trace(
                session_id="session-1", limit=1,
                cursor=first["page"]["next_cursor"],
            )
        self.assertEqual(raised.exception.code, "stale_cursor")

    def test_trace_supports_native_view_and_bounded_errors(self):
        from token_meter.mcp.contracts import MCPQueryError

        service = synthetic_query_service()
        result = service.trace(
            session_id="session-1", view="native_structure",
            event_types=("tool_result",), limit=20,
        )

        self.assertEqual(result["records"][0]["native_type"], "tool_result")
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(result))
        for arguments, code in (
            ({"session_id": "missing"}, "session_not_found"),
            ({"session_id": "session-1", "view": "raw"}, "invalid_argument"),
            ({"session_id": "session-1", "execution": 0}, "invalid_argument"),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(MCPQueryError) as raised:
                    service.trace(**arguments)
                self.assertEqual(raised.exception.code, code)

    def test_stats_groups_complete_scope_by_runtime_and_model(self):
        service = synthetic_query_service()

        result = service.stats(
            metrics=("session_count", "input_tokens", "cost_usd"),
            group_by=("runtime", "model"),
            limit=20,
        )

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(
            result["groups"][0]["dimensions"],
            {"runtime": "codex", "model": "gpt-5.6"},
        )
        self.assertEqual(result["groups"][0]["metrics"]["session_count"], 2)
        self.assertEqual(result["groups"][0]["metrics"]["input_tokens"], 300)
        self.assertAlmostEqual(result["groups"][0]["metrics"]["cost_usd"], 0.24)
        self.assertEqual(result["totals"]["session_count"], 2)

    def test_stats_preserves_measured_zero_and_paginates_groups(self):
        service = synthetic_query_service()

        result = service.stats(
            metrics=("cache_write_tokens",),
            group_by=("session_id",),
            limit=1,
        )

        self.assertEqual(result["groups"][0]["metrics"]["cache_write_tokens"], 0)
        self.assertEqual(
            result["groups"][0]["coverage"]["cache_write_tokens"]["covered"],
            1,
        )
        self.assertIsNotNone(result["page"]["next_cursor"])

    def test_stats_reports_reasoning_tokens_without_folding_it_into_total(self):
        service = synthetic_query_service()
        service.states["session-1"]["executions"][0]["tokens"]["reasoning"] = 8
        service.states["session-2"]["executions"][0]["tokens"]["reasoning"] = 12

        result = service.stats(
            metrics=("reasoning_tokens", "output_tokens", "total_tokens"),
            group_by=("model",),
            limit=20,
        )

        metrics = result["groups"][0]["metrics"]
        self.assertEqual(metrics["reasoning_tokens"], 20)
        self.assertEqual(metrics["output_tokens"], 40)
        # Reasoning is a component of output and must not be re-added to total.
        self.assertEqual(metrics["total_tokens"], 340)
        self.assertEqual(
            result["groups"][0]["coverage"]["reasoning_tokens"],
            {"covered": 2, "unavailable": 0},
        )
        self.assertEqual(result["totals"]["reasoning_tokens"], 20)

    def test_reasoning_tokens_from_a_silent_runtime_stay_unavailable(self):
        # Break caught: a runtime that never reports reasoning would otherwise
        # surface a measured zero rather than absent evidence.
        service = synthetic_query_service()

        result = service.stats(
            metrics=("reasoning_tokens",), group_by=("model",), limit=20,
        )

        self.assertIsNone(result["groups"][0]["metrics"]["reasoning_tokens"])
        self.assertIsNone(result["totals"]["reasoning_tokens"])
        self.assertEqual(
            result["coverage"]["reasoning_tokens"],
            {"covered": 0, "unavailable": 2},
        )

    def test_unavailable_session_cost_is_not_projected_as_measured_zero(self):
        service = synthetic_query_service()
        for state in service.states.values():
            state["availability"]["cost"] = False
            state["total_cost"] = 0.0
            state["executions"][0]["cost"] = 0.0

        sessions = service.sessions(scope="all", limit=20)
        trace = service.trace(
            session_id="session-1", sections=("session", "executions"), limit=20,
        )
        stats = service.stats(
            metrics=("cost_usd",), group_by=("session_id",), limit=20,
        )

        self.assertNotIn("cost_usd", sessions["sessions"][0])
        self.assertNotIn("cost_usd", trace["session"])
        self.assertNotIn("cost_usd", trace["executions"][0])
        self.assertNotIn("cost_usd", trace["session"]["estimated_fields"])
        self.assertIsNone(stats["totals"]["cost_usd"])
        self.assertEqual(stats["coverage"]["cost_usd"], {
            "covered": 0,
            "unavailable": 2,
        })

    def test_stats_time_window_filters_executions_inside_matching_sessions(self):
        service = synthetic_query_service()
        state = service.states["session-1"]
        earlier = copy.deepcopy(state["executions"][0])
        earlier.update({"idx": 2, "ts": 1_787_167_610.0, "cost": 0.8})
        earlier["tokens"]["input"] = 900
        state["executions"].insert(0, earlier)

        result = service.stats(
            metrics=("session_count", "execution_count", "input_tokens", "cost_usd"),
            session_id="session-1",
            start="2026-08-20T00:00:00+00:00",
            end="2026-08-20T23:59:59+00:00",
        )

        self.assertEqual(result["totals"]["session_count"], 1)
        self.assertEqual(result["totals"]["execution_count"], 1)
        self.assertEqual(result["totals"]["input_tokens"], 100)
        self.assertAlmostEqual(result["totals"]["cost_usd"], 0.12)

    def test_stats_time_window_filters_tools_by_their_execution_timestamp(self):
        service = synthetic_query_service()
        state = service.states["session-1"]
        state["executions"][0]["tools"][0].pop("category", None)
        state["tools"]["by_name"][0].pop("category", None)
        earlier = copy.deepcopy(state["executions"][0])
        earlier.update({"idx": 2, "ts": 1_787_167_610.0})
        earlier["tools"][0].update({
            "name": "old_tool", "namespace": "old", "output_tokens": 900,
        })
        state["executions"].insert(0, earlier)
        state["tools"]["by_name"].append({
            "name": "old_tool", "namespace": "old", "category": "old",
            "calls": 1, "output_tokens": 900, "errors": 0,
        })

        result = service.stats(
            metrics=("tool_calls", "tool_result_tokens"),
            group_by=("tool_category", "tool_name"),
            session_id="session-1",
            start="2026-08-20T00:00:00+00:00",
            end="2026-08-20T23:59:59+00:00",
        )

        self.assertEqual(result["totals"]["tool_calls"], 1)
        self.assertEqual(result["totals"]["tool_result_tokens"], 400)
        self.assertEqual(result["groups"][0]["dimensions"], {
            "tool_category": "shell", "tool_name": "exec_command",
        })

    def test_stats_model_filter_applies_to_execution_records(self):
        service = synthetic_query_service()
        state = service.states["session-1"]
        alternate = copy.deepcopy(state["executions"][0])
        alternate.update({"idx": 2, "model": "gpt-5.4", "cost": 0.08})
        alternate["tokens"]["input"] = 80
        state["executions"].append(alternate)

        result = service.stats(
            metrics=("execution_count", "input_tokens", "cost_usd"),
            group_by=("model",),
            session_id="session-1",
            model="gpt-5.4",
        )

        self.assertEqual(result["totals"]["execution_count"], 1)
        self.assertEqual(result["totals"]["input_tokens"], 80)
        self.assertAlmostEqual(result["totals"]["cost_usd"], 0.08)
        self.assertEqual(result["groups"][0]["dimensions"]["model"], "gpt-5.4")

    def test_stats_rejects_unknown_or_mixed_grain_queries(self):
        from token_meter.mcp.contracts import MCPQueryError

        service = synthetic_query_service()
        for arguments in (
            {"metrics": ("unknown",)},
            {"metrics": ("tool_calls", "input_tokens")},
            {"metrics": ("input_tokens",), "group_by": (
                "runtime", "model", "day", "session_id",
            )},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(MCPQueryError) as raised:
                    service.stats(**arguments)
                self.assertEqual(raised.exception.code, "invalid_argument")

    def test_schema_describes_units_coverage_and_runtime(self):
        service = synthetic_query_service()

        result = service.schema(subject="stats", runtime="codex")

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["metrics"]["cost_usd"]["unit"], "USD")
        self.assertIn("tool_name", result["dimensions"])
        self.assertEqual(result["runtime"], "codex")
        self.assertTrue(result["runtime_supported"])


if __name__ == "__main__":
    unittest.main()
