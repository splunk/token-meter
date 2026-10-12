"""Explicit, content-free projections from normalized sessions to public shapes."""

from collections import Counter
from datetime import datetime
from typing import Mapping

from token_meter.contracts import EvidenceBasis, EvidenceValue, NormalizedSession


AGENT_FIELDS = (
    "id", "parent_id", "session_id", "runtime", "client", "kind", "depth",
    "label", "role", "model", "activity_state", "started_at", "ended_at",
    "last_activity_at", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "tokens", "tokens_available",
    "cost", "cost_available", "executions", "attempts", "retries",
    "failed_attempts", "tool_calls", "work_time_s", "group_cost_share",
    "navigable", "attention_level",
)
AGENT_TOTAL_FIELDS = (
    "agents", "tokens", "known_tokens", "tokens_available", "cost",
    "known_cost", "cost_available", "cost_covered_agents",
    "token_covered_agents", "input_tokens", "known_input_tokens",
    "output_tokens", "known_output_tokens", "cache_read_tokens",
    "known_cache_read_tokens", "cache_write_tokens",
    "known_cache_write_tokens", "reasoning_tokens",
    "known_reasoning_tokens", "executions", "cost_per_agent",
    "median_cost", "p95_cost", "output_per_dollar", "parent_sessions",
    "covered_group_cost", "covered_child_cost",
    "group_cost_covered_sessions", "agent_spend_share",
    "group_cost_coverage", "attention_agents",
    "unresolved_agents", "unresolved_known_cost",
)
AGENT_ROLE_ACTIVITY_FIELDS = (
    "complete_agents", "incomplete_agents", "working_agents",
)
AGENT_COHORT_IDENTITY_FIELDS = (
    "id", "runtime", "model", "depth", "kind", "role",
)
MAX_AGENT_USAGE_SCOPES = 768
MAX_AGENT_USAGE_INVENTORY = 1000
MAX_AGENT_ROLE_DAYS = 4000
AGENT_USAGE_INVENTORY_FIELDS = (
    "id", "root_session_id", "project", "runtime", "client", "kind",
    "depth", "label", "role", "model", "activity_state",
    "last_activity_at", "tokens", "tokens_available", "cost",
    "cost_available", "work_time_s", "executions", "attempts", "retries",
    "failed_attempts", "tool_calls",
)


def _timestamp(value):
    return float(value.timestamp()) if isinstance(value, datetime) else None


def _value(evidence):
    if not isinstance(evidence, EvidenceValue):
        raise TypeError("public projections require normalized evidence values")
    return evidence.value


def _available(evidence):
    return evidence.basis is not EvidenceBasis.UNAVAILABLE


def _availability(session):
    usage = session.usage
    timing = session.timing
    return {
        "input_tokens": _available(usage.input_tokens),
        "output_tokens": _available(usage.output_tokens),
        "cache_read_tokens": _available(usage.cache_read_tokens),
        "cache_write_tokens": _available(usage.cache_write_tokens),
        "cost": _available(usage.cost_usd),
        "active_time": _available(timing.active_seconds),
        "wait_time": _available(timing.wait_seconds),
        "ttft": _available(timing.ttft_seconds),
    }


def _estimated_fields(session):
    fields = (
        ("input_tokens", session.usage.input_tokens),
        ("output_tokens", session.usage.output_tokens),
        ("cache_read_tokens", session.usage.cache_read_tokens),
        ("cache_write_tokens", session.usage.cache_write_tokens),
        ("cost", session.usage.cost_usd),
        ("active_time", session.timing.active_seconds),
        ("wait_time", session.timing.wait_seconds),
        ("ttft", session.timing.ttft_seconds),
    )
    return [
        name for name, evidence in fields
        if evidence.basis in (EvidenceBasis.ESTIMATED, EvidenceBasis.INFERRED)
    ]


def _total_tokens(session):
    values = (
        session.usage.input_tokens,
        session.usage.output_tokens,
    )
    known = [int(value.value) for value in values if _available(value)]
    return sum(known) if known else None


def _tool_categories(session):
    counts = Counter(tool.category for tool in session.tools)
    return {key: counts[key] for key in sorted(counts)}


def session_projection(session):
    """Project one normalized session to the stable compatibility field names."""
    if not isinstance(session, NormalizedSession):
        raise TypeError("session projection requires a NormalizedSession")
    source = session.source
    model = source.model_ref
    usage = {
        "input_tokens": _value(session.usage.input_tokens),
        "output_tokens": _value(session.usage.output_tokens),
        "cache_read_input_tokens": _value(session.usage.cache_read_tokens),
        "cache_creation_input_tokens": _value(session.usage.cache_write_tokens),
    }
    duration_fields = (
        ("cache_creation_5m_input_tokens", session.usage.cache_write_5m_tokens),
        ("cache_creation_1h_input_tokens", session.usage.cache_write_1h_tokens),
        (
            "cache_creation_unspecified_input_tokens",
            session.usage.cache_write_unspecified_tokens,
        ),
    )
    if any(_available(evidence) for _name, evidence in duration_fields):
        usage.update({name: _value(evidence) for name, evidence in duration_fields})
    return {
        "provider": source.runtime_id,
        "client": source.client_id,
        "id": source.session_id,
        "label": source.display_label,
        "model": model.model_id if model else None,
        "model_provider": model.provider_id if model else None,
        "account_provider": source.account_provider_id,
        "started_at": _timestamp(session.started_at),
        "ended_at": _timestamp(session.ended_at),
        "total_tokens": _total_tokens(session),
        "total_cost": _value(session.usage.cost_usd),
        "usage": usage,
        "timing": {
            "active_s": _value(session.timing.active_seconds),
            "wait_s": _value(session.timing.wait_seconds),
            "ttft_s": _value(session.timing.ttft_seconds),
        },
        "availability": _availability(session),
        "estimated": _estimated_fields(session),
        "tool_categories": _tool_categories(session),
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in session.warnings
        ],
    }


def state_projection(session):
    row = session_projection(session)
    return {
        "ok": True,
        "source": {
            "provider": row["provider"],
            "client": row["client"],
            "id": row["id"],
            "label": row["label"],
            "model": row["model"],
            "model_provider": row["model_provider"],
        },
        "total_tokens": row["total_tokens"],
        "total_cost": row["total_cost"],
        "availability": row["availability"],
    }


def model_stats_projection(session):
    row = session_projection(session)
    runtime = row["provider"]
    provider = row["model_provider"] or "unknown-model-provider"
    model = row["model"] or "unknown-model"
    return {"models": [{
        "id": "{}:{}:{}".format(runtime, provider, model),
        "runtime": runtime,
        "model": model,
        "model_provider": provider,
        "sessions": 1,
        "input_tokens": row["usage"]["input_tokens"],
        "output_tokens": row["usage"]["output_tokens"],
        "total_tokens": row["total_tokens"],
        "total_cost": row["total_cost"],
    }]}


def _catalog_projection(catalog):
    if not isinstance(catalog, Mapping):
        return {}
    result = {}
    for runtime_id, raw in list(catalog.items())[:16]:
        if not isinstance(raw, Mapping):
            continue
        result[str(runtime_id)] = {
            "label": str(raw.get("label") or "Unknown Runtime")[:120],
            "symbol": str(raw.get("symbol") or "runtime.generic")[:120],
            "color": str(raw.get("color") or "runtime-neutral")[:120],
            "capabilities": [str(value)[:64] for value in list(
                raw.get("capabilities") or ()
            )[:16]],
        }
    return result


def menubar_projection(session, runtime_catalog):
    row = session_projection(session)
    return {
        "ok": True,
        "total_tokens": row["total_tokens"],
        "total_cost": row["total_cost"],
        "source": {
            "provider": row["provider"],
            "id": row["id"],
            "label": row["label"],
            "model": row["model"],
        },
        "runtime_catalog": _catalog_projection(runtime_catalog),
    }


def mcp_projection(session):
    row = session_projection(session)
    usage = {}
    timing = {}
    for public, legacy in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_read_tokens", "cache_read_input_tokens"),
        ("cache_write_tokens", "cache_creation_input_tokens"),
    ):
        value = row["usage"][legacy]
        if value is not None:
            usage[public] = value
    for public, legacy in (
        ("cache_write_5m_tokens", "cache_creation_5m_input_tokens"),
        ("cache_write_1h_tokens", "cache_creation_1h_input_tokens"),
        (
            "cache_write_unspecified_tokens",
            "cache_creation_unspecified_input_tokens",
        ),
    ):
        value = row["usage"].get(legacy)
        if value is not None:
            usage[public] = value
    if row["total_cost"] is not None:
        usage["cost_usd"] = row["total_cost"]
    for public, legacy in (
        ("active_seconds", "active_s"),
        ("wait_seconds", "wait_s"),
        ("ttft_seconds", "ttft_s"),
    ):
        value = row["timing"][legacy]
        if value is not None:
            timing[public] = value
    return {
        "runtime": row["provider"],
        "model": {"provider": row["model_provider"], "id": row["model"]},
        "usage": usage,
        "timing": timing,
        "tool_categories": row["tool_categories"],
        "availability": row["availability"],
    }


def _agent_totals_projection(value):
    value = value if isinstance(value, Mapping) else {}
    return {key: value.get(key) for key in AGENT_TOTAL_FIELDS}


def _agent_attention_projection(value):
    result = []
    for item in list(value or ())[:100]:
        if not isinstance(item, Mapping):
            continue
        reasons = []
        for reason in list(item.get("reasons") or ())[:8]:
            if not isinstance(reason, Mapping):
                continue
            reasons.append({
                "code": str(reason.get("code") or "")[:80],
                "explanation": str(reason.get("explanation") or "")[:320],
            })
        result.append({
            "agent_id": str(item.get("agent_id") or "")[:240],
            "level": "needs_attention",
            "reasons": reasons,
        })
    return result


def _nonnegative_projection_int(value):
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def agent_group_projection(group):
    """Project one browser-only agent group through a strict allowlist."""
    if not isinstance(group, Mapping):
        return None
    agents = []
    for item in list(group.get("agents") or ())[:100]:
        if not isinstance(item, Mapping):
            continue
        agents.append({key: item.get(key) for key in AGENT_FIELDS})
    coverage = group.get("coverage") if isinstance(group.get("coverage"), Mapping) else {}
    return {
        "root_session_id": str(group.get("root_session_id") or "")[:240],
        "selected_agent_id": str(group.get("selected_agent_id") or "")[:240],
        "coverage": {
            key: str(coverage.get(key) or "unavailable")[:32]
            for key in ("relationships", "tokens", "cost")
        },
        "totals": _agent_totals_projection(group.get("totals")),
        "child_totals": _agent_totals_projection(group.get("child_totals")),
        "agents": agents,
        "attention": _agent_attention_projection(group.get("attention")),
        "hidden_agent_count": _nonnegative_projection_int(
            group.get("hidden_agent_count")
        ),
    }


def _agent_cohort_projection(rows):
    result = []
    for item in list(rows or ())[:240]:
        if not isinstance(item, Mapping):
            continue
        row = {
            key: item.get(key)
            for key in AGENT_COHORT_IDENTITY_FIELDS if key in item
        }
        row.update(_agent_totals_projection(item))
        row.update({
            key: item.get(key)
            for key in AGENT_ROLE_ACTIVITY_FIELDS if key in item
        })
        result.append(row)
    return result


def _agent_usage_body_projection(usage):
    usage = usage if isinstance(usage, Mapping) else {}
    return {
        "totals": _agent_totals_projection(usage.get("totals")),
        **{
            key: _agent_cohort_projection(usage.get(key))
            for key in ("runtimes", "models", "depths", "kinds", "roles")
        },
    }


def agent_usage_projection(usage):
    """Project bounded content-free child-agent analysis for the browser."""
    usage = usage if isinstance(usage, Mapping) else {}
    result = _agent_usage_body_projection(usage)
    scopes = []
    raw_scopes = list(usage.get("scopes") or ())
    for item in raw_scopes[:MAX_AGENT_USAGE_SCOPES]:
        if not isinstance(item, Mapping):
            continue
        row = {
            "window": str(item.get("window") or "all")[:16],
            "runtime": str(item.get("runtime") or "")[:40],
            "project": str(item.get("project") or "")[:1000],
            **_agent_usage_body_projection(item),
        }
        if isinstance(item.get("comparison"), Mapping):
            row["comparison"] = _agent_usage_body_projection(
                item.get("comparison")
            )
        scopes.append(row)
    result["scopes"] = scopes
    result["scope_count"] = len(raw_scopes)
    result["scope_truncated"] = (
        bool(usage.get("scope_truncated"))
        or len(raw_scopes) > MAX_AGENT_USAGE_SCOPES
    )
    raw_role_days = list(usage.get("role_days") or ())
    role_days = []
    for item in raw_role_days[:MAX_AGENT_ROLE_DAYS]:
        if not isinstance(item, Mapping):
            continue
        row = {
            "day": str(item.get("day") or "")[:10],
            "project": str(item.get("project") or "")[:1000],
            "runtime": str(item.get("runtime") or "")[:40],
            "kind": str(item.get("kind") or "")[:40],
            "role": str(item.get("role") or "")[:64],
            **_agent_totals_projection(item),
        }
        row.update({
            key: item.get(key)
            for key in AGENT_ROLE_ACTIVITY_FIELDS if key in item
        })
        role_days.append(row)
    result["role_days"] = role_days
    result["role_day_count"] = max(
        len(raw_role_days),
        _nonnegative_projection_int(usage.get("role_day_count")),
    )
    result["role_days_truncated"] = (
        bool(usage.get("role_days_truncated"))
        or len(raw_role_days) > MAX_AGENT_ROLE_DAYS
    )
    raw_inventory = list(usage.get("inventory") or ())
    inventory = []
    for item in raw_inventory[:MAX_AGENT_USAGE_INVENTORY]:
        if not isinstance(item, Mapping):
            continue
        row = {
            key: item.get(key) for key in AGENT_USAGE_INVENTORY_FIELDS
        }
        row["attention"] = []
        for reason in list(item.get("attention") or ())[:8]:
            if not isinstance(reason, Mapping):
                continue
            row["attention"].append({
                "code": str(reason.get("code") or "")[:80],
                "explanation": str(reason.get("explanation") or "")[:320],
            })
        inventory.append(row)
    result["inventory"] = inventory
    result["inventory_count"] = max(
        len(raw_inventory),
        _nonnegative_projection_int(usage.get("inventory_count")),
    )
    result["inventory_truncated"] = (
        bool(usage.get("inventory_truncated"))
        or len(raw_inventory) > MAX_AGENT_USAGE_INVENTORY
        or result["inventory_count"] > len(inventory)
    )
    return result


def projection_bundle(session, runtime_catalog):
    """Build every public projection explicitly from the same normalized input."""
    return {
        "session": session_projection(session),
        "state": state_projection(session),
        "model_stats": model_stats_projection(session),
        "menubar": menubar_projection(session, runtime_catalog),
        "mcp": mcp_projection(session),
    }
