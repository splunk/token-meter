"""Explicit, content-free projections from normalized sessions to public shapes."""

from collections import Counter
from datetime import datetime
from typing import Mapping

from token_meter.contracts import EvidenceBasis, EvidenceValue, NormalizedSession


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


def projection_bundle(session, runtime_catalog):
    """Build every public projection explicitly from the same normalized input."""
    return {
        "session": session_projection(session),
        "state": state_projection(session),
        "model_stats": model_stats_projection(session),
        "menubar": menubar_projection(session, runtime_catalog),
        "mcp": mcp_projection(session),
    }
