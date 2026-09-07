"""Static schema metadata for Token Meter MCP query tools."""


SCHEMA_VERSION = "1.0"

METRICS = {
    "session_count": {
        "unit": "sessions", "source": "session", "reduction": "distinct_count",
        "description": "Distinct sessions in the filtered group.",
    },
    "execution_count": {
        "unit": "executions", "source": "execution", "reduction": "sum",
        "description": "Standardized executions with detailed evidence.",
    },
    "input_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Input or context tokens under the runtime's evidence basis.",
    },
    "output_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Trace-observed model output tokens.",
    },
    "cache_read_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Input tokens served from cache.",
    },
    "cache_write_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Input tokens written to cache.",
    },
    "cache_write_5m_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Input tokens written to a five-minute cache.",
    },
    "cache_write_1h_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Input tokens written to a one-hour cache.",
    },
    "cache_write_unspecified_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Cache-write input tokens whose duration was not reported.",
    },
    "total_tokens": {
        "unit": "tokens", "source": "execution", "reduction": "sum",
        "description": "Known input plus output tokens.",
    },
    "cost_usd": {
        "unit": "USD", "source": "execution", "reduction": "sum",
        "description": "Reported or estimated API-equivalent execution cost.",
    },
    "active_seconds": {
        "unit": "seconds", "source": "execution", "reduction": "sum",
        "description": "Observed or inferred active execution time.",
    },
    "wait_seconds": {
        "unit": "seconds", "source": "execution", "reduction": "sum",
        "description": "Prompt-to-completion wait time when available.",
    },
    "ttft_seconds": {
        "unit": "seconds", "source": "execution", "reduction": "sum",
        "description": "Time to first token when available.",
    },
    "model_calls": {
        "unit": "calls", "source": "execution", "reduction": "sum",
        "description": "Trace-observed model calls.",
    },
    "tool_calls": {
        "unit": "calls", "source": "tool", "reduction": "sum",
        "description": "Trace-observed calls to a tool identity.",
    },
    "tool_result_tokens": {
        "unit": "tokens", "source": "tool", "reduction": "sum",
        "description": "Estimated tokens returned by tool results.",
    },
    "attempts": {
        "unit": "attempts", "source": "execution", "reduction": "sum",
        "description": "Observed request attempts.",
    },
    "retries": {
        "unit": "retries", "source": "execution", "reduction": "sum",
        "description": "Attempts after the first request attempt.",
    },
    "failed_attempts": {
        "unit": "attempts", "source": "execution", "reduction": "sum",
        "description": "Observed failed request attempts.",
    },
    "context_latest": {
        "unit": "tokens", "source": "session", "reduction": "max",
        "description": "Largest latest-context value among sessions in the group.",
    },
    "context_peak": {
        "unit": "tokens", "source": "session", "reduction": "max",
        "description": "Largest observed context peak among sessions in the group.",
    },
}

DIMENSIONS = {
    "runtime": {"type": "string", "grains": ("execution", "tool")},
    "client": {"type": "string", "grains": ("execution", "tool")},
    "model_provider": {"type": ["string", "null"], "grains": ("execution", "tool")},
    "model": {"type": ["string", "null"], "grains": ("execution", "tool")},
    "day": {"type": ["string", "null"], "grains": ("execution", "tool")},
    "session_id": {"type": "string", "grains": ("execution", "tool")},
    "tool_category": {"type": ["string", "null"], "grains": ("tool",)},
    "tool_name": {"type": "string", "grains": ("tool",)},
}

SESSION_FIELDS = {
    "id": "opaque session identifier",
    "runtime": "runtime identifier",
    "client": "client identifier",
    "model_provider": "model provider when known",
    "model": "model identifier when known",
    "state": "current, completed, or historical",
    "last_activity_at": "Unix timestamp",
    "availability": "per-metric Boolean availability",
}

TRACE_FIELDS = {
    "session": "content-free session header and totals",
    "executions": "per-execution token, cost, timing, context, and count evidence",
    "events": "semantic event types without event detail or payloads",
    "tools": "tool identity/category aggregates without arguments or results",
    "context": "latest, peak, window, and percentage evidence",
    "coverage": "availability, estimates, and truncation",
    "warnings": "fixed Token Meter warning codes and messages",
}

NATIVE_FIELDS = {
    "sequence": "response-local event sequence",
    "native_type": "allowlisted runtime-native structural type",
    "native_subtype": "allowlisted runtime-native structural subtype",
    "execution": "standardized execution number",
    "timestamp": "Unix timestamp",
    "numeric": "allowlisted numeric usage, timing, and context fields",
    "status": "allowlisted status enum",
    "model": "sanitized model identity",
    "tool": "sanitized tool identity",
}


def schema_projection(subject, descriptors, runtime=""):
    runtime_ids = tuple(
        str(descriptor.runtime_id) for descriptor in descriptors or ()
    )
    common = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "subject": subject,
        "runtime": runtime or None,
        "runtime_supported": (runtime in runtime_ids) if runtime else None,
        "availability_semantics": {
            "measured": "Observed evidence, including measured zero.",
            "estimated": "Derived from an explicitly labelled local estimate.",
            "inferred": "Derived from incomplete but bounded source evidence.",
            "unavailable": "The runtime did not provide supported evidence.",
        },
    }
    if subject == "sessions":
        common.update({
            "fields": dict(SESSION_FIELDS),
            "filters": [
                "scope", "runtime", "client", "model", "state", "start", "end",
            ],
            "default_limit": 20,
            "maximum_limit": 100,
        })
    elif subject == "standardized_trace":
        common.update({
            "fields": dict(TRACE_FIELDS),
            "event_types": [
                "start", "user", "model", "reasoning", "tool_call",
                "tool_result", "usage", "context", "compaction",
                "coordination", "complete", "error",
            ],
            "default_limit": 50,
            "maximum_limit": 200,
        })
    elif subject == "native_structure":
        common.update({
            "fields": dict(NATIVE_FIELDS),
            "content_included": False,
            "default_limit": 50,
            "maximum_limit": 200,
        })
    else:
        common.update({
            "metrics": {name: dict(value) for name, value in METRICS.items()},
            "dimensions": {
                name: {**value, "grains": list(value["grains"])}
                for name, value in DIMENSIONS.items()
            },
            "maximum_metrics": 8,
            "maximum_dimensions": 3,
            "default_limit": 20,
            "maximum_limit": 100,
        })
    return common
