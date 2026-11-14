#!/usr/bin/env python3
"""Bounded stdio MCP server for Token Meter. Standard library only."""

import json
import os
import sys

import meter
from token_meter.mcp.delivery import GIT_RANGES, GIT_VIEWS, WORK_PERIODS, WORK_VIEWS


SERVER_NAME = "tokenmeter"
SERVER_TITLE = "Token Meter"
SERVER_VERSION = "0.2.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
SERVER_INSTRUCTIONS = (
    "Token Meter is a local source of cost, efficiency, and structural trace evidence for supported agent runtimes. "
    "Use check for a decision about the caller's current run, usage for aggregate historical change, "
    "capabilities for optional skill-pack hygiene, sessions to select runs, trace for standardized or "
    "sanitized runtime-native structure, stats for comparable aggregates, git_stats for pushed-code "
    "delivery against spend, work_stats for labeled work mix and model fit, and schema to discover fields. "
    "Prefer calling check at meaningful phase "
    "boundaries or when the user asks about cost, context, tool output, or whether to continue. Never imply "
    "continuous monitoring: tools run only when called. Results omit prompts, messages, reasoning text, tool "
    "arguments, tool results, credentials, config values, filesystem paths, project names, and session titles; "
    "the one configured value returned is work_stats' Work area names, which the user chose in Settings. "
    "Native trace output preserves only allowlisted structure and numeric evidence; it is not byte-faithful raw data. "
    "Follow pagination cursors when more rows are needed. All tools except explicit budget setters are read-only. "
    "Budget setters require confirm: true and must be used only for direct user requests."
)

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

COMMON_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "answer", "evidence", "recommended_action", "caveat", "dashboard_url", "as_of", "data_scope"],
    "properties": {
        "ok": {"type": "boolean"},
        "answer": {"type": "string"},
        "evidence": {"type": "array", "maxItems": 3, "items": {"type": "object"}},
        "recommended_action": {"type": "string"},
        "caveat": {"type": "string"},
        "dashboard_url": {"type": "string"},
        "as_of": {"type": "string"},
        "data_scope": {"type": "string"},
        "approximate_fields": {"type": "array", "items": {"type": "string"}},
        "truncated": {"type": "boolean"},
    },
    "additionalProperties": True,
}

QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "data_scope"],
    "properties": {
        "ok": {"type": "boolean"},
        "schema_version": {"type": "string"},
        "as_of": {"type": ["number", "string"]},
        "data_scope": {"type": "string"},
        "page": {"type": "object"},
    },
    "additionalProperties": True,
}

TRACE_EVENT_TYPES = [
    "start", "user", "model", "reasoning", "tool_call", "tool_result",
    "usage", "context", "compaction", "coordination", "complete", "error",
]

STATS_METRICS = [
    "session_count", "execution_count", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "cache_write_5m_tokens",
    "cache_write_1h_tokens", "cache_write_unspecified_tokens", "total_tokens",
    "cost_usd",
    "active_seconds", "wait_seconds", "ttft_seconds", "model_calls",
    "tool_calls", "tool_result_tokens", "attempts", "retries",
    "failed_attempts", "context_latest", "context_peak",
]

STATS_DIMENSIONS = [
    "runtime", "client", "model_provider", "model", "day", "session_id",
    "tool_category", "tool_name",
]

TOOLS = [
    {
        "name": "check",
        "title": "Check current run",
        "description": (
            "Answer a decision about the caller's matched current run: whether to continue, cost, context, "
            "tool-result efficiency, next-phase readiness, or one retained execution. Returns a verdict, at "
            "most three evidence points, one action, a caveat, and a dashboard link."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "enum": ["continue", "cost", "context", "tools", "next_phase"], "default": "continue"},
                "execution": {"type": "integer", "minimum": 1, "description": "Optional retained execution number to inspect."},
                "session_id": {"type": "string", "minLength": 1, "maxLength": 240, "description": "Optional Token Meter session id when the user explicitly selected a run."},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "usage",
        "title": "Review aggregate usage",
        "description": (
            "Review anonymous aggregate spend, model mix, tool-result volume, or day-over-day change. "
            "Historical run titles, project names, session ids, and paths are never returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": ["today", "7d", "14d"], "default": "7d"},
                "focus": {"type": "string", "enum": ["spend", "models", "tools", "changes"], "default": "changes"},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "budget",
        "title": "Read session budget",
        "description": (
            "Read the effective cap for the caller's matched current run or one selected session. "
            "Returns the cap source, estimated spend, remaining amount, and threshold state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "set_session_budget",
        "title": "Set session budget",
        "description": (
            "Set the cap for the caller's matched current run or one selected session. "
            "Use only for a direct user request; confirm must be true."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["budget_usd", "confirm"],
            "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 240},
                "budget_usd": {"type": "number", "minimum": 0.5, "maximum": 100000000},
                "expected_current_budget_usd": {"type": "number", "minimum": 0.5, "maximum": 100000000},
                "confirm": {"const": True},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "set_default_session_budget",
        "title": "Set default session budget",
        "description": (
            "Set the default cap applied when a session has no override. "
            "Use only for a direct user request; confirm must be true."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["budget_usd", "confirm"],
            "properties": {
                "budget_usd": {"type": "number", "minimum": 0.5, "maximum": 100000000},
                "expected_current_budget_usd": {"type": "number", "minimum": 0.5, "maximum": 100000000},
                "confirm": {"const": True},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "capabilities",
        "title": "Review optional capabilities",
        "description": (
            "Review named user-installed skill packs with bounded usage evidence. "
            "On a freshly started server it answers from the sessions read so far; coverage reports how many, "
            "and a partial answer is labeled Partial review. Never returns environment variables, credentials, config values, tool arguments, or tool results, "
            "and cannot change configuration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["current", "all"], "default": "current"},
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 5, "default": 5,
                    "description": "Maximum candidate skill packs to return; must be from 1 through 5.",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "sessions",
        "title": "List trace sessions",
        "description": (
            "List content-free session metadata for trace selection. Defaults to the caller's current project; "
            "scope=all still omits titles, project names, and paths. Results are bounded and cursor-paginated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["current_project", "all"], "default": "current_project"},
                "runtime": {"type": "string", "maxLength": 120},
                "client": {"type": "string", "maxLength": 120},
                "model": {"type": "string", "maxLength": 160},
                "state": {"type": "string", "enum": ["current", "completed", "historical"]},
                "start": {"type": "string", "maxLength": 64, "description": "Inclusive ISO-8601 activity timestamp."},
                "end": {"type": "string", "maxLength": 64, "description": "Inclusive ISO-8601 activity timestamp."},
                "cursor": {"type": "string", "maxLength": 2048},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "trace",
        "title": "Query one session trace",
        "description": (
            "Query standardized trace evidence or sanitized runtime-native structure for one selected session. "
            "Returns numeric, timing, event-type, tool-identity, context, coverage, and warning evidence only; "
            "never returns prompts, responses, reasoning, arguments, results, paths, or arbitrary native payloads."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "minLength": 1, "maxLength": 240},
                "view": {"type": "string", "enum": ["standardized", "native_structure"], "default": "standardized"},
                "sections": {
                    "type": "array", "maxItems": 7, "uniqueItems": True,
                    "items": {"type": "string", "enum": ["session", "executions", "events", "tools", "context", "coverage", "warnings"]},
                },
                "execution": {"type": "integer", "minimum": 1},
                "event_types": {
                    "type": "array", "maxItems": 12, "uniqueItems": True,
                    "items": {"type": "string", "enum": TRACE_EVENT_TYPES},
                },
                "cursor": {"type": "string", "maxLength": 2048},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "stats",
        "title": "Aggregate trace statistics",
        "description": (
            "Aggregate comparable token, cost, timing, context, attempt, model-call, and tool evidence across "
            "sessions. Query tool metrics separately from execution or session metrics. Every metric includes "
            "coverage so measured zero remains distinct from unavailable evidence. Start and end scope execution "
            "and tool records by their execution timestamps."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["metrics"],
            "properties": {
                "metrics": {
                    "type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                    "items": {"type": "string", "enum": STATS_METRICS},
                },
                "group_by": {
                    "type": "array", "maxItems": 3, "uniqueItems": True,
                    "items": {"type": "string", "enum": STATS_DIMENSIONS},
                },
                "runtime": {"type": "string", "maxLength": 120},
                "client": {"type": "string", "maxLength": 120},
                "model": {"type": "string", "maxLength": 160},
                "state": {"type": "string", "enum": ["current", "completed", "historical"]},
                "session_id": {"type": "string", "maxLength": 240},
                "start": {
                    "type": "string", "maxLength": 64,
                    "description": "Inclusive ISO-8601 execution timestamp; records without a timestamp are excluded.",
                },
                "end": {
                    "type": "string", "maxLength": 64,
                    "description": "Inclusive ISO-8601 execution timestamp; records without a timestamp are excluded.",
                },
                "sort_by": {"type": "string", "enum": STATS_METRICS},
                "sort_direction": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "cursor": {"type": "string", "maxLength": 2048},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "git_stats",
        "title": "Aggregate Git delivery",
        "description": (
            "Aggregate the Git page: pushed added, deleted, and changed lines, commits, covered spend, "
            "spend per 1k changed lines, output efficiency, neutral period-over-period change, spend-share "
            "model attribution, and an estimated split of commits by kind of work (feature, fix, docs, and so "
            "on) across all measured repositories. view=days returns paginated daily rows. "
            "Never returns repository or project names, branches, commit messages, or file paths. Unmeasured "
            "lines or cost are null with availability flags, never zero. Returns available=false with reason "
            "history_scan_incomplete while local session history is still being read; call again shortly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "range": {"type": "string", "enum": list(GIT_RANGES), "default": "7"},
                "view": {"type": "string", "enum": list(GIT_VIEWS), "default": "summary"},
                "cursor": {"type": "string", "maxLength": 2048},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 31},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "work_stats",
        "title": "Aggregate Work insights",
        "description": (
            "Aggregate the Work page from persisted Work insights labels: spend and requests by area, kind "
            "of work, outcome, pushback, session tags, model scorecard and fit, right-sizing by tier and "
            "reasoning effort, the Work page's model suggestions with estimated savings (view=suggestions), "
            "and a paginated trend. Returns available=false with a reason when Work insights are off, the "
            "classifier has not labeled anything yet, label storage is unreadable, or local session history is "
            "still being read (history_scan_incomplete). Area names are the "
            "user's configured Work areas. Never returns request or reply text, file names, project names, "
            "or session keys."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string", "enum": list(WORK_PERIODS), "default": "6",
                    "description": "1d/7d/30d/90d are days, month is this month to date, 3/6/12 are months, 0 is all history.",
                },
                "runtime": {"type": "string", "maxLength": 40},
                "view": {"type": "string", "enum": list(WORK_VIEWS), "default": "summary"},
                "cursor": {"type": "string", "maxLength": 2048},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 24},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "schema",
        "title": "Describe trace query fields",
        "description": (
            "Describe the stable fields, metrics, dimensions, availability semantics, limits, and optional "
            "runtime support for the session and trace query surface."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "enum": ["sessions", "standardized_trace", "native_structure", "stats", "git_stats", "work_stats"],
                    "default": "stats",
                },
                "runtime": {"type": "string", "maxLength": 120},
            },
            "additionalProperties": False,
        },
        "outputSchema": QUERY_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
]


def caller_context():
    return {
        "runtime": os.environ.get("TOKEN_METER_CALLER", ""),
        "project": os.environ.get("TOKEN_METER_PROJECT") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
    }


def jsonrpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_error(message, code=None):
    payload = {"ok": False, "error": str(message)}
    if code:
        payload["error_code"] = str(code)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": True,
    }


def call_tool(name, arguments):
    # Parallel fills during one call share a single worker pool, closed on return.
    with meter.shared_index_pool():
        return _call_tool(name, arguments)


def _call_tool(name, arguments):
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        return tool_error("Tool arguments must be an object.")
    try:
        if name == "check":
            allowed = {"focus", "execution", "session_id"}
            if set(arguments) - allowed:
                raise ValueError("check received an unsupported argument")
            data = meter.application().agent_api.check(
                caller=caller_context(), **arguments
            )
        elif name == "usage":
            allowed = {"window", "focus"}
            if set(arguments) - allowed:
                raise ValueError("usage received an unsupported argument")
            data = meter.application().agent_api.usage(**arguments)
        elif name == "budget":
            allowed = {"session_id"}
            if set(arguments) - allowed:
                raise ValueError("budget received an unsupported argument")
            data = meter.application().agent_api.budget(
                caller=caller_context(), **arguments
            )
        elif name == "set_session_budget":
            allowed = {
                "session_id", "budget_usd", "expected_current_budget_usd",
                "confirm",
            }
            if set(arguments) - allowed:
                raise ValueError("set_session_budget received an unsupported argument")
            data = meter.application().agent_api.set_session_budget(
                caller=caller_context(), **arguments
            )
        elif name == "set_default_session_budget":
            allowed = {"budget_usd", "expected_current_budget_usd", "confirm"}
            if set(arguments) - allowed:
                raise ValueError("set_default_session_budget received an unsupported argument")
            data = meter.application().agent_api.set_default_session_budget(**arguments)
        elif name == "capabilities":
            allowed = {"scope", "limit"}
            if set(arguments) - allowed:
                raise ValueError("capabilities received an unsupported argument")
            data = meter.application().agent_api.capabilities(
                caller=caller_context(), **arguments
            )
        elif name == "sessions":
            allowed = {
                "scope", "runtime", "client", "model", "state", "start",
                "end", "cursor", "limit",
            }
            if set(arguments) - allowed:
                raise ValueError("sessions received an unsupported argument")
            data = meter.application().agent_api.sessions(
                caller=caller_context(), **arguments
            )
        elif name == "trace":
            allowed = {
                "session_id", "view", "sections", "execution",
                "event_types", "cursor", "limit",
            }
            if set(arguments) - allowed:
                raise ValueError("trace received an unsupported argument")
            data = meter.application().agent_api.trace(**arguments)
        elif name == "stats":
            allowed = {
                "metrics", "group_by", "runtime", "client", "model",
                "state", "session_id", "start", "end", "sort_by",
                "sort_direction", "cursor", "limit",
            }
            if set(arguments) - allowed:
                raise ValueError("stats received an unsupported argument")
            data = meter.application().agent_api.stats(**arguments)
        elif name == "git_stats":
            allowed = {"range", "view", "cursor", "limit"}
            if set(arguments) - allowed:
                raise ValueError("git_stats received an unsupported argument")
            data = meter.application().agent_api.git_stats(**arguments)
        elif name == "work_stats":
            allowed = {"period", "runtime", "view", "cursor", "limit"}
            if set(arguments) - allowed:
                raise ValueError("work_stats received an unsupported argument")
            data = meter.application().agent_api.work_stats(**arguments)
        elif name == "schema":
            allowed = {"subject", "runtime"}
            if set(arguments) - allowed:
                raise ValueError("schema received an unsupported argument")
            data = meter.application().agent_api.schema(**arguments)
        else:
            return tool_error(f"Unknown tool: {name}", "method_not_found")
    except ValueError as exc:
        return tool_error(exc, getattr(exc, "code", "invalid_argument"))
    except Exception:
        return tool_error(
            "Token Meter could not build this insight. Check the local dashboard and try again.",
            "internal_error",
        )
    if name in {"sessions", "stats"}:
        meter.flush_mcp_evidence_cache()
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": False,
    }


def dispatch(request, initialized=False):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return jsonrpc_error(request.get("id") if isinstance(request, dict) else None, -32600, "Invalid Request"), initialized
    request_id = request.get("id")
    method = request["method"]
    params = request.get("params") or {}
    if method.startswith("notifications/"):
        return None, initialized
    if method == "initialize":
        if not isinstance(params, dict):
            return jsonrpc_error(request_id, -32602, "Invalid params"), initialized
        requested_version = str(params.get("protocolVersion") or "")
        protocol_version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return jsonrpc_result(request_id, {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        }), True
    if method == "ping":
        return jsonrpc_result(request_id, {}), initialized
    if not initialized:
        return jsonrpc_error(request_id, -32002, "Server is not initialized"), initialized
    if method == "tools/list":
        return jsonrpc_result(request_id, {"tools": TOOLS}), initialized
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return jsonrpc_error(request_id, -32602, "Invalid params"), initialized
        return jsonrpc_result(request_id, call_tool(params["name"], params.get("arguments"))), initialized
    return jsonrpc_error(request_id, -32601, "Method not found"), initialized


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    initialized = False
    for raw_line in stdin:
        if len(raw_line) > 1_048_576:
            response = jsonrpc_error(None, -32700, "Parse error")
        else:
            try:
                request = json.loads(raw_line)
            except (TypeError, json.JSONDecodeError):
                response = jsonrpc_error(None, -32700, "Parse error")
            else:
                response, initialized = dispatch(request, initialized=initialized)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()


if __name__ == "__main__":
    meter.enable_mcp_evidence_persistence()
    meter.enable_mcp_parallel_index()
    try:
        serve()
    except BrokenPipeError:
        pass
    except Exception as exc:
        print(f"tokenmeter MCP stopped: {exc}", file=sys.stderr)
        raise
