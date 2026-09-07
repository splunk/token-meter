"""Positive-allowlist projections for detailed MCP trace queries."""

import math
import re
import time
from collections.abc import Mapping


SCHEMA_VERSION = "1.0"
ALL_TRACE_SECTIONS = (
    "session", "executions", "events", "tools", "context", "coverage",
    "warnings",
)
SAFE_EVENT_TYPES = {
    "start": "start",
    "user": "user",
    "message": "model",
    "model": "model",
    "reasoning": "reasoning",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "usage": "usage",
    "context": "context",
    "goal": "coordination",
    "coordination": "coordination",
    "complete": "complete",
    "error": "error",
}
EVENT_LABELS = {
    "start": "Execution started",
    "user": "User event",
    "model": "Model event",
    "reasoning": "Reasoning event",
    "tool_call": "Tool call",
    "tool_result": "Tool result",
    "usage": "Usage checkpoint",
    "context": "Context event",
    "compaction": "Context compacted",
    "coordination": "Coordination event",
    "complete": "Execution completed",
    "error": "Error event",
}
SAFE_NATIVE_TYPES = {
    "assistant",
    "custom_tool_call",
    "custom_tool_call_output",
    "event_msg",
    "function_call",
    "function_call_output",
    "message",
    "part",
    "request",
    "response_item",
    "session_meta",
    "system",
    "task_complete",
    "task_started",
    "token_count",
    "tool_call",
    "tool_result",
    "turn_context",
    "usage",
    "user",
    "web_search_call",
    "web_search_end",
}
SAFE_NATIVE_SUBTYPES = {
    "agent_message",
    "custom_tool_call",
    "custom_tool_call_output",
    "function_call",
    "function_call_output",
    "reasoning",
    "task_complete",
    "task_started",
    "token_count",
    "tool_call",
    "tool_result",
    "turn_duration",
    "user_message",
    "web_search_call",
    "web_search_end",
}
SAFE_STATUSES = {
    "completed", "error", "failed", "in_progress", "success", "unknown",
}
SAFE_SEVERITIES = {"neutral", "start", "warn", "error", "success", "idle"}
IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9_.:/@+ -]+")


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def safe_identity(value, maximum=120):
    value = str(value or "").strip()
    if not value or len(value) > maximum:
        return ""
    return value if IDENTITY_PATTERN.fullmatch(value) else ""


def safe_number(value, integer=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if integer else number


def _put_number(result, key, value, integer=False):
    number = safe_number(value, integer=integer)
    if number is not None:
        result[key] = number


def _availability(value):
    return {
        str(key): bool(item)
        for key, item in _mapping(value).items()
        if str(key) in {
            "tokens", "input_tokens", "output_tokens", "cost", "cache",
            "context", "timing", "tool_results", "throughput",
        }
    }


def _session_state(summary, last_activity, now):
    terminal = summary.get("terminal")
    if terminal is True:
        return "completed"
    if terminal is False and last_activity and now - last_activity <= 300:
        return "current"
    return "historical"


def session_projection(source, summary=None, now=None):
    source = _mapping(source)
    summary = _mapping(summary)
    now = time.time() if now is None else float(now)
    last_activity = safe_number(source.get("mtime")) or 0.0
    models = summary.get("models") if isinstance(summary.get("models"), list) else []
    model = safe_identity(
        summary.get("primary_model") or source.get("model")
        or (models[0] if len(models) == 1 else ""),
        160,
    )
    availability = _availability(
        summary.get("availability") or source.get("availability")
    )
    result = {
        "id": safe_identity(source.get("id"), 240),
        "runtime": safe_identity(source.get("provider")),
        "client": safe_identity(source.get("client") or source.get("provider")),
        "model_provider": safe_identity(
            source.get("model_provider") or summary.get("model_provider")
        ) or None,
        "model": model or None,
        "state": _session_state(summary, last_activity, now),
        "last_activity_at": last_activity or None,
        "availability": availability,
    }
    _put_number(result, "total_tokens", summary.get("tokens"), integer=True)
    _put_number(result, "input_tokens", summary.get("input_tokens"), integer=True)
    _put_number(result, "output_tokens", summary.get("output_tokens"), integer=True)
    if availability.get("cost") is not False:
        _put_number(result, "cost_usd", summary.get("cost"))
    _put_number(result, "active_seconds", summary.get("duration_s"))
    return result


def _trace_session_projection(source, state):
    state = _mapping(state)
    timing = _mapping(state.get("timing"))
    state_source = _mapping(state.get("source"))
    availability = _availability(state.get("availability"))
    result = {
        "id": safe_identity(source.get("id"), 240),
        "runtime": safe_identity(state.get("provider") or source.get("provider")),
        "client": safe_identity(
            state.get("client") or source.get("client") or source.get("provider")
        ),
        "model_provider": safe_identity(
            state_source.get("model_provider") or source.get("model_provider")
        ) or None,
        "model": safe_identity(
            state.get("primary_model") or source.get("model"), 160,
        ) or None,
        "state": "completed" if state.get("ended") else "current",
        "availability": availability,
        "estimated_fields": sorted(
            key for key, estimated in {
                "cost_usd": (
                    bool(state.get("cost_approx"))
                    and availability.get("cost") is not False
                ),
                "tokens": bool(state_source.get("token_estimate")),
            }.items() if estimated
        ),
    }
    for key, value, integer in (
        ("started_at", timing.get("start_ts"), False),
        ("ended_at", timing.get("end_ts"), False),
        ("total_tokens", state.get("total_tokens"), True),
        ("active_seconds", timing.get("duration_s"), False),
    ):
        _put_number(result, key, value, integer=integer)
    if availability.get("cost") is not False:
        _put_number(result, "cost_usd", state.get("total_cost"))
    return result


def _execution_projection(row, cost_available=True):
    row = _mapping(row)
    tokens = _mapping(row.get("tokens"))
    result = {
        "index": safe_number(row.get("idx"), integer=True),
        "model": safe_identity(row.get("model"), 160) or None,
        "tokens": {},
        "timing": {},
        "context": {},
        "counts": {},
        "status": "completed",
    }
    for public, candidates in (
        ("input", ("input",)),
        ("output", ("output",)),
        ("cache_read", ("cache_read", "cache")),
        ("cache_write", ("cache_write",)),
        ("cache_write_5m", ("cache_write_5m",)),
        ("cache_write_1h", ("cache_write_1h",)),
        ("cache_write_unspecified", ("cache_write_unspecified",)),
        ("fresh_input", ("fresh_input",)),
        ("tool_result", ("retrieval",)),
    ):
        value = next((tokens.get(key) for key in candidates if key in tokens), None)
        _put_number(result["tokens"], public, value, integer=True)
    known_total = [
        result["tokens"].get(key)
        for key in ("input", "output")
        if result["tokens"].get(key) is not None
    ]
    if known_total:
        result["tokens"]["total"] = sum(known_total)
    active_seconds = row.get("active_s")
    if active_seconds is None and row.get("duration_ms") is not None:
        duration = safe_number(row.get("duration_ms"))
        active_seconds = duration / 1000.0 if duration is not None else None
    for key, value in (
        ("active_seconds", active_seconds),
        ("wait_seconds", row.get("wait_s")),
        ("ttft_seconds", row.get("ttft_s")),
    ):
        _put_number(result["timing"], key, value)
    for key, value, integer in (
        ("tokens", row.get("context_tokens"), True),
        ("window", row.get("context_window"), True),
        ("percentage", row.get("context_pct"), False),
    ):
        _put_number(result["context"], key, value, integer=integer)
    tools = row.get("tools") if isinstance(row.get("tools"), list) else []
    for key, value in (
        ("model_calls", row.get("model_calls")),
        ("tool_calls", row.get("tool_calls", len(tools))),
        ("attempts", row.get("attempts")),
        ("retries", row.get("retries")),
        ("failed_attempts", row.get("failed_attempts")),
    ):
        _put_number(result["counts"], key, value, integer=True)
    _put_number(result, "timestamp", row.get("ts"))
    if cost_available:
        _put_number(result, "cost_usd", row.get("cost"))
    return result


def _event_type(row):
    kind = str(row.get("kind") or "")
    if kind == "context" and str(row.get("label") or "") == "Context compacted":
        return "compaction"
    return SAFE_EVENT_TYPES.get(kind, "")


def _event_projection(row, sequence, cost_available=True):
    row = _mapping(row)
    event_type = _event_type(row)
    if not event_type:
        return None
    result = {
        "id": "event-{}".format(sequence),
        "sequence": sequence,
        "type": event_type,
        "label": EVENT_LABELS[event_type],
    }
    for key, value, integer in (
        ("timestamp", row.get("ts"), False),
        ("execution", row.get("execution"), True),
        ("tokens", row.get("tokens"), True),
        ("duration_ms", row.get("duration_ms"), False),
    ):
        _put_number(result, key, value, integer=integer)
    if cost_available:
        _put_number(result, "cost_usd", row.get("cost"))
    model = safe_identity(row.get("model"), 160)
    tool = safe_identity(row.get("tool") or (
        row.get("label") if event_type in {"tool_call", "tool_result"} else ""
    ))
    severity = str(row.get("severity") or "")
    status = str(row.get("status") or "")
    if model:
        result["model"] = model
    if tool:
        result["tool"] = tool
    if severity in SAFE_SEVERITIES:
        result["severity"] = severity
    if status in SAFE_STATUSES:
        result["status"] = status
    return result


def _tool_projection(row):
    row = _mapping(row)
    name = safe_identity(row.get("name"))
    if not name:
        return None
    result = {
        "name": name,
        "namespace": safe_identity(row.get("namespace")) or None,
        "category": safe_identity(row.get("category")) or None,
    }
    for key, value in (
        ("calls", row.get("calls")),
        ("output_tokens", row.get("output_tokens")),
        ("errors", row.get("errors")),
    ):
        _put_number(result, key, value, integer=True)
    return result


def _context_projection(value):
    value = _mapping(value)
    result = {}
    for key, source, integer in (
        ("latest", "latest", True),
        ("peak", "peak", True),
        ("window", "window", True),
        ("latest_percentage", "latest_pct", False),
        ("peak_percentage", "peak_pct", False),
    ):
        _put_number(result, key, value.get(source), integer=integer)
    return result


def standardized_trace_projection(source, state, sections=None,
                                  execution=None, event_types=None):
    source = _mapping(source)
    state = _mapping(state)
    sections = tuple(sections or ALL_TRACE_SECTIONS)
    event_types = set(event_types or ())
    cost_available = _availability(state.get("availability")).get("cost") is not False
    result = {"schema_version": SCHEMA_VERSION}
    if "session" in sections:
        result["session"] = _trace_session_projection(source, state)
    if "executions" in sections:
        result["executions"] = [
            projected for row in (state.get("executions") or [])
            if execution is None or safe_number(row.get("idx"), integer=True) == execution
            for projected in (_execution_projection(row, cost_available),)
        ]
    if "events" in sections:
        events = []
        for sequence, row in enumerate(state.get("trace") or [], 1):
            if execution is not None and safe_number(
                _mapping(row).get("execution"), integer=True,
            ) != execution:
                continue
            projected = _event_projection(row, sequence, cost_available)
            if projected and (not event_types or projected["type"] in event_types):
                events.append(projected)
        result["events"] = events
    if "tools" in sections:
        by_name = _mapping(state.get("tools")).get("by_name") or []
        result["tools"] = [
            item for item in (_tool_projection(row) for row in by_name) if item
        ]
    if "context" in sections:
        result["context"] = _context_projection(state.get("context"))
    if "coverage" in sections:
        availability = _availability(state.get("availability"))
        result["coverage"] = {
            "availability": availability,
            "estimated_fields": sorted(
                key for key, estimated in {
                    "cost_usd": (
                        bool(state.get("cost_approx"))
                        and availability.get("cost") is not False
                    ),
                    "tokens": bool(_mapping(state.get("source")).get("token_estimate")),
                }.items() if estimated
            ),
            "trace_truncated": bool(state.get("trace_truncated")),
        }
    if "warnings" in sections:
        result["warnings"] = ([{
            "code": "trace_truncated",
            "message": "Detailed trace history was bounded.",
        }] if state.get("trace_truncated") else [])
    return result


def native_structure_projection(source, state, execution=None, event_types=None):
    del source
    event_types = set(event_types or ())
    cost_available = _availability(_mapping(state).get("availability")).get("cost") is not False
    rows = []
    for sequence, event in enumerate(_mapping(state).get("trace") or [], 1):
        event = _mapping(event)
        event_execution = safe_number(event.get("execution"), integer=True)
        if execution is not None and event_execution != execution:
            continue
        semantic_type = _event_type(event)
        if event_types and semantic_type not in event_types:
            continue
        native_type = str(event.get("native_type") or event.get("kind") or "")
        native_subtype = str(event.get("native_subtype") or "")
        if native_type not in SAFE_NATIVE_TYPES:
            native_type = str(event.get("kind") or "")
        if native_type not in SAFE_NATIVE_TYPES:
            native_type = "event"
        result = {
            "id": "event-{}".format(sequence),
            "sequence": sequence,
            "native_type": native_type,
            "numeric": {},
        }
        if native_subtype in SAFE_NATIVE_SUBTYPES:
            result["native_subtype"] = native_subtype
        if event_execution is not None:
            result["execution"] = event_execution
        timestamp = safe_number(event.get("ts"))
        if timestamp is not None:
            result["timestamp"] = timestamp
        for key, value, integer in (
            ("tokens", event.get("tokens"), True),
            ("duration_ms", event.get("duration_ms"), False),
            ("context_tokens", event.get("context_tokens"), True),
            ("context_window", event.get("context_window"), True),
            ("tools_loaded", event.get("tools_loaded"), True),
        ):
            _put_number(result["numeric"], key, value, integer=integer)
        if cost_available:
            _put_number(result["numeric"], "cost_usd", event.get("cost"))
        status = str(event.get("status") or "")
        if status in SAFE_STATUSES:
            result["status"] = status
        model = safe_identity(event.get("model"), 160)
        tool = safe_identity(event.get("tool") or (
            event.get("label") if semantic_type in {"tool_call", "tool_result"} else ""
        ))
        if model:
            result["model"] = model
        if tool:
            result["tool"] = tool
        rows.append(result)
    return rows
