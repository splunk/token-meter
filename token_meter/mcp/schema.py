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
        "description": (
            "Reported or estimated API-equivalent execution cost, after any "
            "configured discount."
        ),
    },
    "discount_usd": {
        "unit": "USD", "source": "execution", "reduction": "sum",
        "description": (
            "Amount configured discounts removed from the list-price estimate; "
            "cost_usd plus discount_usd is the list-price estimate."
        ),
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


GIT_STATS_FIELDS = {
    "range": {"type": "enum", "description": "today, yesterday, 7, 30, 90, month, last_month, or all."},
    "view": {"type": "enum", "description": "summary (totals, change, models) or days (paginated)."},
    "available": {"type": "boolean", "description": "False until at least one repository is measured."},
    "current.added_lines": {"unit": "lines", "description": "Pushed added lines; null when unmeasured."},
    "current.deleted_lines": {"unit": "lines", "description": "Pushed deleted lines; null when unmeasured."},
    "current.changed_lines": {"unit": "lines", "description": "Added plus deleted pushed lines."},
    "current.commits": {"unit": "commits", "description": "Commits first seen in a successful push."},
    "current.covered_cost_usd": {
        "unit": "USD", "description": "Estimated spend in measured repositories with cost evidence.",
    },
    "current.spend_per_1k_lines_usd": {"unit": "USD", "description": "Covered spend per 1,000 changed lines."},
    "current.output_tokens_per_usd": {"unit": "tokens/USD", "description": "Output tokens per covered dollar."},
    "current.lines_per_1k_output_tokens": {"unit": "lines", "description": "Changed lines per 1,000 output tokens."},
    "current.reasoning_share_of_output": {"unit": "ratio", "description": "Reasoning tokens over output tokens."},
    "change": {"unit": "percent", "description": "Neutral change from the equal-length previous period."},
    "models": {"description": "Model, runtime, effort, covered spend, and spend-share attributed lines."},
    "coverage": {"description": "Selected, measured, and comparable repository counts and spend coverage."},
}

WORK_STATS_FIELDS = {
    "period": {"type": "enum", "description": "1d, 7d, 30d, 90d, month, 3, 6, 12, or 0 (all history)."},
    "view": {"type": "enum", "description": "summary, models, right_sizing, or trend (paginated)."},
    "available": {
        "type": "boolean",
        "description": "False with reason unsupported, work_insights_off, or classifier_not_run.",
    },
    "status": {"description": "Whether Work insights are supported, on, paused, and how many labels exist."},
    "coverage": {"description": "Sessions, requests, and turns in the period and how many are labeled."},
    "areas": {"description": "Requests, sessions started, and estimated spend per configured area."},
    "work_types": {"description": "Tasks, spend, resolved rate, and pushback per kind of work."},
    "outcomes": {"description": "Sessions and spend per outcome enum."},
    "pushback": {"unit": "ratio", "description": "Share of labeled follow-ups that pushed back."},
    "tags": {"description": "Sessions and spend per session tag enum."},
    "scorecard": {"description": "Per model and runtime: tasks, spend, resolved rate, pushback."},
    "fit": {"description": "Kind of work by model: cost per task and pushback."},
    "right_sizing": {"description": "Complexity by price tier and by reasoning effort, with mismatch flags."},
    "trend": {"description": "Per bucket: requests, sessions, spend, spend by area, outcomes."},
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
    elif subject in ("git_stats", "work_stats"):
        fields = GIT_STATS_FIELDS if subject == "git_stats" else WORK_STATS_FIELDS
        common.update({
            "fields": {name: dict(value) for name, value in fields.items()},
            "content_included": False,
            "default_limit": 31 if subject == "git_stats" else 24,
            "maximum_limit": 100,
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
