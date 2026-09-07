"""Pure rollups over normalized session evidence.

Discovery, trace loading, caching, settings, and transport deliberately live
outside this module. Runtime identifiers are opaque values and model keys are
always scoped by runtime.
"""

import copy
import re
import time
from collections import defaultdict

from token_meter.domain.usage import (
    make_usage_provenance,
    metric_available,
    normalize_reported_token_count,
    usage_io_token_counts,
    usage_provenance,
)
from token_meter.domain.insights import insight, normalize_insights


WORKLOAD_SAMPLE_LIMIT = 2_000
MATCHED_PACE_SAMPLE_LIMIT = 500
REPORTED_REASONING_EFFORTS = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
})


def _compact_text(value, limit):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def add_model_summary(stats, model, usage, cost, cost_available=None):
    """Accumulate a compatibility model summary from normalized token counts."""
    input_available = usage.get("input_available") is not False
    output_available = usage.get("output_available") is not False
    input_tokens, output_tokens = usage_io_token_counts(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
    )
    input_tokens = input_tokens if input_available else 0
    output_tokens = output_tokens if output_available else 0
    row = stats.setdefault(model or "unknown", {
        "cost": 0.0, "tokens": 0, "input_tokens": 0,
        "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0,
        "reasoning_output_tokens": 0, "reasoning_executions": 0,
        "reasoning_unavailable_executions": 0,
        "thinking_executions": 0, "thinking_covered_executions": 0,
        "token_covered_executions": 0, "io_covered_executions": 0,
        "executions": 0,
    })
    reasoning_tokens, reasoning_reported = normalize_reported_token_count(
        usage.get("reasoning_output_tokens", usage.get("reasoning_tokens"))
    )
    reasoning_available = (
        usage.get("reasoning_available") is True
        and reasoning_reported
        and reasoning_tokens <= output_tokens
    )
    row["cost"] += float(cost or 0)
    row["tokens"] += input_tokens + output_tokens
    row["input_tokens"] += input_tokens
    row["output_tokens"] += output_tokens
    row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
    for target, source in (
        ("cache_write_5m_tokens", "cache_creation_5m_input_tokens"),
        ("cache_write_1h_tokens", "cache_creation_1h_input_tokens"),
        (
            "cache_write_unspecified_tokens",
            "cache_creation_unspecified_input_tokens",
        ),
    ):
        if source in usage:
            row[target] = int(row.get(target) or 0) + int(usage.get(source) or 0)
    if reasoning_available and output_available:
        row["reasoning_tokens"] += min(output_tokens, reasoning_tokens)
        row["reasoning_output_tokens"] += output_tokens
        row["reasoning_executions"] += 1
    else:
        row["reasoning_unavailable_executions"] += 1
    if usage.get("thinking_observed") is True:
        row["thinking_executions"] += 1
        if reasoning_available and output_available:
            row["thinking_covered_executions"] += 1
    if output_available:
        row["token_covered_executions"] += 1
    if input_available and output_available:
        row["io_covered_executions"] += 1
    row["input_evidence"] = row.get("input_evidence") is True or input_available
    row["output_evidence"] = row.get("output_evidence") is True or output_available
    if cost_available is not None:
        row.setdefault("cost_covered_executions", 0)
        row.setdefault("cost_covered_output_tokens", 0)
        row.setdefault("cost_covered_cost", 0.0)
        if cost_available and output_available:
            row["cost_covered_executions"] += 1
            row["cost_covered_output_tokens"] += output_tokens
            row["cost_covered_cost"] += float(cost or 0)
    row["executions"] += 1
    return input_tokens, output_tokens


def add_model_daily(stats, model, usage, cost, timestamp, localtime=time.localtime,
                    cost_available=None):
    """Accumulate model I/O into local calendar days."""
    if not timestamp:
        return
    input_available = usage.get("input_available") is not False
    output_available = usage.get("output_available") is not False
    input_tokens, output_tokens = usage_io_token_counts(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0),
        usage.get("cache_creation_input_tokens", 0),
    )
    input_tokens = input_tokens if input_available else 0
    output_tokens = output_tokens if output_available else 0
    day = time.strftime("%Y-%m-%d", localtime(timestamp))
    key = (model or "unknown", day)
    row = stats.setdefault(key, {
        "model": model or "unknown", "day": day, "cost": 0.0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "reasoning_tokens": 0, "reasoning_output_tokens": 0,
        "reasoning_executions": 0, "reasoning_unavailable_executions": 0,
        "thinking_executions": 0, "thinking_covered_executions": 0,
        "token_covered_executions": 0, "io_covered_executions": 0,
        "executions": 0,
    })
    reasoning_tokens, reasoning_reported = normalize_reported_token_count(
        usage.get("reasoning_output_tokens", usage.get("reasoning_tokens"))
    )
    reasoning_available = (
        usage.get("reasoning_available") is True
        and reasoning_reported
        and reasoning_tokens <= output_tokens
    )
    row["cost"] += float(cost or 0)
    row["input_tokens"] += input_tokens
    row["output_tokens"] += output_tokens
    row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
    for target, source in (
        ("cache_write_5m_tokens", "cache_creation_5m_input_tokens"),
        ("cache_write_1h_tokens", "cache_creation_1h_input_tokens"),
        (
            "cache_write_unspecified_tokens",
            "cache_creation_unspecified_input_tokens",
        ),
    ):
        if source in usage:
            row[target] = int(row.get(target) or 0) + int(usage.get(source) or 0)
    if reasoning_available and output_available:
        row["reasoning_tokens"] += min(output_tokens, reasoning_tokens)
        row["reasoning_output_tokens"] += output_tokens
        row["reasoning_executions"] += 1
    else:
        row["reasoning_unavailable_executions"] += 1
    if usage.get("thinking_observed") is True:
        row["thinking_executions"] += 1
        if reasoning_available and output_available:
            row["thinking_covered_executions"] += 1
    if output_available:
        row["token_covered_executions"] += 1
    if input_available and output_available:
        row["io_covered_executions"] += 1
    row["input_evidence"] = row.get("input_evidence") is True or input_available
    row["output_evidence"] = row.get("output_evidence") is True or output_available
    if cost_available is not None:
        row.setdefault("cost_covered_executions", 0)
        row.setdefault("cost_covered_output_tokens", 0)
        row.setdefault("cost_covered_cost", 0.0)
        if cost_available and output_available:
            row["cost_covered_executions"] += 1
            row["cost_covered_output_tokens"] += output_tokens
            row["cost_covered_cost"] += float(cost or 0)
    row["executions"] += 1


def metric_coverage(rows, metric):
    rows = list(rows or [])
    covered = sum(1 for row in rows if metric_available(row, metric))
    return {
        "covered_sessions": covered,
        "total_sessions": len(rows),
        "complete": covered == len(rows),
    }


def current_session_summaries(rows, now=None, max_age_s=30 * 60, limit=8,
                              working_age_s=90, context_sample_limit=32):
    """Return bounded card-safe recent sessions from normalized rows."""
    now = float(time.time() if now is None else now)
    activity_rank = {"recent": 0, "waiting": 1, "working": 2}
    selected_by_id = {}
    selected_without_id = []
    for row in rows or []:
        mtime = float(row.get("mtime") or 0)
        idle_s = max(0, int(now - mtime))
        if not mtime or idle_s > max_age_s:
            continue
        session_id = str(row.get("id") or "")[:240]
        if idle_s > working_age_s:
            activity_state = "recent"
        elif row.get("terminal"):
            activity_state = "waiting"
        else:
            activity_state = "working"
        candidate = {
            "row": row, "session_id": session_id, "mtime": mtime,
            "idle_s": idle_s, "activity_state": activity_state,
        }
        if not session_id:
            selected_without_id.append(candidate)
            continue
        previous = selected_by_id.get(session_id)
        if previous is None or (
            activity_rank[activity_state], mtime
        ) > (
            activity_rank[previous["activity_state"]], previous["mtime"]
        ):
            selected_by_id[session_id] = candidate

    selected = sorted(
        [*selected_by_id.values(), *selected_without_id],
        key=lambda item: -item["mtime"],
    )
    result = []
    for candidate in selected:
        row = candidate["row"]
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        window = int(context.get("window") or 0) or None
        latest = int(context.get("latest") or 0)
        latest_pct = context.get("latest_pct")
        if latest_pct is None and window:
            latest_pct = latest / window
        context_samples = []
        for value in row.get("_context_samples") or []:
            try:
                numeric = int(value)
            except (TypeError, ValueError):
                continue
            if numeric >= 0:
                context_samples.append(numeric)
        if not context_samples and latest:
            context_samples.append(latest)
        context_samples = context_samples[-context_sample_limit:]
        provider = str(row.get("provider") or "unknown")
        project = str(row.get("project") or "").strip().rstrip("/\\")
        project = project.replace("\\", "/").rsplit("/", 1)[-1] if project else "No project"
        project = _compact_text(project or "No project", 52)
        models = [_compact_text(model, 80) for model in (row.get("models") or [])[:4]]
        primary_model = _compact_text(
            row.get("primary_model") or (models[0] if models else "unknown"), 80,
        )
        availability = row.get("availability") if isinstance(row.get("availability"), dict) else {}
        throughput = row.get("throughput") if isinstance(row.get("throughput"), dict) else {}
        live_throughput = (
            row.get("live_throughput")
            if isinstance(row.get("live_throughput"), dict) else {}
        )
        displayed_throughput = live_throughput if live_throughput.get("available") else throughput
        result.append({
            "id": candidate["session_id"],
            "provider": provider,
            "client": str(row.get("client") or provider)[:80],
            "runtime": _compact_text(row.get("runtime") or row.get("label") or provider, 40),
            "label": _compact_text(row.get("label") or provider, 40),
            "session_name": _compact_text(row.get("session_name") or "", 90),
            "project": project,
            "short_id": str(row.get("id") or "")[:8],
            "primary_model": primary_model,
            "reasoning_effort": _compact_text(row.get("reasoning_effort") or "", 20),
            "models": models,
            "cost": float(row.get("cost") or 0),
            "cost_approx": bool(row.get("cost_approx")),
            "availability": {
                "cost": availability.get("cost") is not False,
                "context": availability.get("context") is not False,
                "throughput": bool(displayed_throughput.get("available")),
            },
            "usage_basis": str(row.get("usage_basis") or
                               (row.get("provenance") or {}).get("usage_basis") or
                               "unavailable"),
            "context": {
                "latest": latest, "window": window, "latest_pct": latest_pct,
                "estimated": bool(context.get("estimated")), "samples": context_samples,
            },
            "throughput": {
                "available": bool(throughput.get("available")),
                "output_tps": float(throughput.get("output_tps") or 0),
                "basis": str(throughput.get("basis") or "unavailable"),
            },
            "live_throughput": {
                "available": bool(live_throughput.get("available")),
                "output_tps": float(live_throughput.get("output_tps") or 0),
                "basis": str(live_throughput.get("basis") or "unavailable"),
                "completed_steps": int(live_throughput.get("completed_steps") or 0),
                "measured_output_tokens": int(
                    live_throughput.get("measured_output_tokens") or 0
                ),
                "measured_seconds": float(
                    live_throughput.get("measured_seconds") or 0
                ),
            },
            "token_estimate": bool(row.get("token_estimate")),
            "turns": int(row.get("turns") or 0),
            "mtime": candidate["mtime"],
            "idle_s": candidate["idle_s"],
            "activity_state": candidate["activity_state"],
        })
        if len(result) >= max(0, int(limit)):
            break
    return result


def _new_signal_bucket(**identity):
    return {
        **identity, "user_turns": 0, "utterances": 0, "matches": 0,
        "term_counts": defaultdict(int),
    }


def _add_signal_event(bucket, event):
    bucket["user_turns"] += 1
    bucket["utterances"] += int(bool(event.get("utterance")))
    bucket["matches"] += int(event.get("matches") or 0)
    for term, count in (event.get("term_counts") or {}).items():
        bucket["term_counts"][term] += int(count or 0)


def _finish_signal_bucket(bucket):
    row = dict(bucket)
    term_counts = row.pop("term_counts", {})
    row["rate"] = row["utterances"] / row["user_turns"] if row["user_turns"] else 0.0
    row["terms"] = sorted(
        ({"term": term, "count": count} for term, count in term_counts.items() if count),
        key=lambda item: (-item["count"], item["term"]),
    )
    return row


def rollup_language_signal_events(events):
    """Roll up payload-free lexical signals by day, week, and runtime/model."""
    total = _new_signal_bucket()
    days, weeks, models = {}, {}, {}
    for event in events or []:
        _add_signal_event(total, event)
        day = event.get("day") or ""
        week = event.get("week") or ""
        model = event.get("model") or "unknown"
        runtime = event.get("runtime") or ""
        model_id = event.get("model_id") or f"{model}::{runtime}"
        if day:
            _add_signal_event(days.setdefault(day, _new_signal_bucket(day=day)), event)
        if week:
            _add_signal_event(weeks.setdefault(week, _new_signal_bucket(week=week)), event)
        model_row = models.setdefault(model_id, {
            "total": _new_signal_bucket(id=model_id, model=model, runtime=runtime),
            "daily": {}, "weekly": {},
        })
        _add_signal_event(model_row["total"], event)
        if day:
            _add_signal_event(
                model_row["daily"].setdefault(day, _new_signal_bucket(day=day)), event,
            )
        if week:
            _add_signal_event(
                model_row["weekly"].setdefault(week, _new_signal_bucket(week=week)), event,
            )
    result = _finish_signal_bucket(total)
    result["daily"] = [_finish_signal_bucket(days[key]) for key in sorted(days)]
    result["weekly"] = [_finish_signal_bucket(weeks[key]) for key in sorted(weeks)]
    result["models"] = []
    for model_id in sorted(models):
        data = models[model_id]
        row = _finish_signal_bucket(data["total"])
        row["daily"] = [
            _finish_signal_bucket(data["daily"][key]) for key in sorted(data["daily"])
        ]
        row["weekly"] = [
            _finish_signal_bucket(data["weekly"][key]) for key in sorted(data["weekly"])
        ]
        result["models"].append(row)
    result["models"].sort(key=lambda row: (
        -row["utterances"], -row["user_turns"], row["model"], row.get("runtime") or "",
    ))
    return result


def aggregate_cross_session_rows(session_rows, trend_limit=14, runtime_resolver=None):
    """Build runtime-neutral model, provider, coverage, and trend rollups."""
    rows = list(session_rows or [])
    public_sessions = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    public_sessions.sort(key=lambda row: -float(row.get("mtime") or 0))
    model_mix_rows = {}
    model_name_cost = defaultdict(float)
    day_cost = defaultdict(float)
    provider_rows = defaultdict(list)
    for index, row in enumerate(rows):
        provider = row.get("provider") or "unknown"
        runtime = (
            row.get("runtime")
            or (runtime_resolver(row) if runtime_resolver is not None else None)
            or row.get("label")
            or provider
        )
        provider_rows[provider].append(row)
        session_id = row.get("id") or f"row-{index}"
        model_cost = row.get("_model_cost") or {}
        model_tokens = row.get("_model_tok") or {}
        for model in set(model_cost) | set(model_tokens):
            key = f"{model}::{runtime}"
            item = model_mix_rows.setdefault(key, {
                "id": key, "model": model, "runtime": runtime,
                "providers": set(), "cost": 0.0, "tokens": 0,
                "session_ids": set(), "cost_ids": set(), "token_ids": set(),
                "estimated_ids": set(), "estimated_cost": 0.0, "estimated_tokens": 0,
            })
            cost_value = float(model_cost.get(model) or 0)
            token_value = int(model_tokens.get(model) or 0)
            item["providers"].add(provider)
            item["cost"] += cost_value
            item["tokens"] += token_value
            item["session_ids"].add(session_id)
            if metric_available(row, "cost"):
                item["cost_ids"].add(session_id)
            if metric_available(row, "tokens"):
                item["token_ids"].add(session_id)
            if row.get("token_estimate"):
                item["estimated_ids"].add(session_id)
                item["estimated_cost"] += cost_value
                item["estimated_tokens"] += token_value
            model_name_cost[model] += cost_value
        for day, value in (row.get("_day_cost") or {}).items():
            day_cost[day] += float(value or 0)

    model_mix = []
    for item in model_mix_rows.values():
        provenance = make_usage_provenance(
            item.pop("session_ids"), item.pop("estimated_ids"),
            item.pop("cost_ids") | item.pop("token_ids"),
            item.pop("estimated_cost"), item.pop("estimated_tokens"),
        )
        item["providers"] = sorted(item["providers"])
        item["provenance"] = provenance
        item["usage_basis"] = provenance["usage_basis"]
        model_mix.append(item)
    model_mix.sort(key=lambda item: (-item["cost"], -item["tokens"], item["id"]))

    provenance = usage_provenance(rows)
    trend = []
    for day in sorted(day_cost)[-trend_limit:]:
        day_rows = [row for row in rows if day in (row.get("_day_cost") or {})]
        day_provenance = usage_provenance(day_rows)
        estimated_cost = sum(
            float((row.get("_day_cost") or {}).get(day) or 0)
            for row in day_rows if row.get("token_estimate") and metric_available(row, "cost")
        )
        day_provenance["estimated_cost"] = estimated_cost
        item = {
            "day": day, "cost": day_cost[day],
            "reported_cost": max(0.0, day_cost[day] - estimated_cost),
            "estimated_cost": estimated_cost,
            "provenance": day_provenance,
            "usage_basis": day_provenance["usage_basis"],
        }
        trend.append(item)
    reported_costs = [item["reported_cost"] for item in trend]
    median = sorted(reported_costs)[len(reported_costs) // 2] if reported_costs else 0
    for item in trend:
        item["anomaly"] = bool(median and item["reported_cost"] > 2.5 * median)
        item["anomaly_basis"] = "reported_only"

    coverage = {
        metric: metric_coverage(rows, metric) for metric in ("cost", "tokens", "cache")
    }
    providers = []
    for provider, provider_session_rows in provider_rows.items():
        provider_provenance = usage_provenance(provider_session_rows)
        providers.append({
            "provider": provider,
            "cost": sum(float(row.get("cost") or 0) for row in provider_session_rows),
            "sessions": len(provider_session_rows),
            "coverage": {
                "cost": metric_coverage(provider_session_rows, "cost"),
                "tokens": metric_coverage(provider_session_rows, "tokens"),
            },
            "availability": {
                "cost": any(metric_available(row, "cost") for row in provider_session_rows),
                "tokens": any(metric_available(row, "tokens") for row in provider_session_rows),
            },
            "provenance": provider_provenance,
            "usage_basis": provider_provenance["usage_basis"],
        })
    providers.sort(key=lambda row: -row["cost"])
    total = sum(item["cost"] for item in model_mix)
    return {
        "sessions": public_sessions,
        "model_mix": model_mix,
        "model_name_cost": dict(model_name_cost),
        "trend": trend,
        "total_cost": total,
        "reported_cost": max(0.0, total - provenance["estimated_cost"]),
        "estimated_cost": provenance["estimated_cost"],
        "total_sessions": len(rows),
        "total_executions": sum(int(row.get("turns") or 0) for row in rows),
        "total_tokens": sum(int(row.get("tokens") or 0) for row in rows),
        "coverage": coverage,
        "provenance": provenance,
        "usage_basis": provenance["usage_basis"],
        "availability": {
            "cost": coverage["cost"]["covered_sessions"] > 0,
            "tokens": coverage["tokens"]["covered_sessions"] > 0,
            "input_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "output_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "cache": coverage["cache"]["covered_sessions"] > 0,
            "throughput": coverage["tokens"]["covered_sessions"] > 0,
            "context": True, "timing": False, "tool_results": True,
        },
        "providers": providers,
    }


def daily_summaries(session_rows, limit=30, availability_resolver=None):
    """Aggregate daily spend, usage provenance, and completed wait time."""
    days = {}
    availability_resolver = availability_resolver or (lambda _provider: {
        "cost": True, "tokens": True, "input_tokens": True, "output_tokens": True,
        "cache": True, "throughput": True, "context": True, "timing": True,
        "tool_results": True,
    })

    def day_row(day):
        return days.setdefault(day, {
            "day": day, "cost": 0.0, "tokens": 0,
            "input_tokens": 0, "output_tokens": 0, "providers": {},
            "sessions": {}, "projects": set(), "wait_s": 0.0,
            "wait_samples": 0, "longest_wait_s": 0.0,
            "session_ids": set(), "cost_covered_ids": set(), "token_covered_ids": set(),
            "cache_covered_ids": set(), "estimated_ids": set(),
            "estimated_cost": 0.0, "estimated_tokens": 0,
        })

    cache_duration_fields = (
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "cache_write_unspecified_tokens",
    )

    def add_cache_duration(target, values):
        for field in cache_duration_fields:
            if field in values:
                target[field] = int(target.get(field) or 0) + int(
                    values.get(field) or 0
                )

    def provider_row(row, provider):
        return row["providers"].setdefault(provider, {
            "provider": provider, "cost": 0.0, "tokens": 0,
            "input_tokens": 0, "output_tokens": 0,
            "wait_s": 0.0, "wait_samples": 0,
            "session_ids": set(), "cost_covered_ids": set(), "token_covered_ids": set(),
            "cache_covered_ids": set(), "estimated_ids": set(),
            "estimated_cost": 0.0, "estimated_tokens": 0,
        })

    def session_row(row, session, provider, project):
        session_id = session.get("id") or session.get("path") or "unknown"
        return row["sessions"].setdefault(session_id, {
            "id": session_id, "title": session.get("title") or session_id,
            "project": project, "provider": provider,
            "label": session.get("label") or provider, "cost": 0.0,
            "tokens": 0, "input_tokens": 0, "output_tokens": 0,
            "wait_s": 0.0, "wait_samples": 0, "longest_wait_s": 0.0,
            "availability": session.get("availability") or availability_resolver(provider),
            "token_estimate": bool(session.get("token_estimate")),
        })

    for session in session_rows or []:
        provider = session.get("provider") or "unknown"
        project = session.get("project") or "local"
        session_id = session.get("id") or session.get("path") or "unknown"
        estimated = bool(session.get("token_estimate"))

        def mark_coverage(row):
            row["session_ids"].add(session_id)
            if metric_available(session, "cost"):
                row["cost_covered_ids"].add(session_id)
            if metric_available(session, "tokens"):
                row["token_covered_ids"].add(session_id)
            if metric_available(session, "cache"):
                row["cache_covered_ids"].add(session_id)
            if estimated:
                row["estimated_ids"].add(session_id)

        for day, value in (session.get("_day_cost") or {}).items():
            cost = float(value or 0)
            row = day_row(day)
            row["cost"] += cost
            runtime = provider_row(row, provider)
            runtime["cost"] += cost
            if estimated:
                row["estimated_cost"] += cost
                runtime["estimated_cost"] += cost
            mark_coverage(row)
            mark_coverage(runtime)
            row["projects"].add(project)
            session_row(row, session, provider, project)["cost"] += cost
        for stats in session.get("_model_daily") or []:
            day = stats.get("day") or ""
            if not day:
                continue
            input_tokens = int(stats.get("input_tokens") or 0)
            output_tokens = int(stats.get("output_tokens") or 0)
            tokens = input_tokens + output_tokens
            row = day_row(day)
            runtime = provider_row(row, provider)
            mark_coverage(row)
            mark_coverage(runtime)
            row["tokens"] += tokens
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens
            add_cache_duration(row, stats)
            runtime["tokens"] += tokens
            runtime["input_tokens"] += input_tokens
            runtime["output_tokens"] += output_tokens
            add_cache_duration(runtime, stats)
            if estimated:
                row["estimated_tokens"] += tokens
                runtime["estimated_tokens"] += tokens
            row["projects"].add(project)
            daily_session = session_row(row, session, provider, project)
            daily_session["tokens"] += tokens
            daily_session["input_tokens"] += input_tokens
            daily_session["output_tokens"] += output_tokens
            add_cache_duration(daily_session, stats)
        for sample in session.get("_wait_samples") or []:
            day = sample.get("day") or ""
            duration_s = float(sample.get("duration_s") or 0)
            if not day or duration_s <= 0:
                continue
            row = day_row(day)
            mark_coverage(row)
            row["wait_s"] += duration_s
            row["wait_samples"] += 1
            row["longest_wait_s"] = max(row["longest_wait_s"], duration_s)
            row["projects"].add(project)
            runtime = provider_row(row, provider)
            mark_coverage(runtime)
            runtime["wait_s"] += duration_s
            runtime["wait_samples"] += 1
            daily_session = session_row(row, session, provider, project)
            daily_session["wait_s"] += duration_s
            daily_session["wait_samples"] += 1
            daily_session["longest_wait_s"] = max(daily_session["longest_wait_s"], duration_s)

    keys = sorted((value for value in days if value), reverse=True)
    if limit is not None and limit > 0:
        keys = keys[:limit]
    result = []
    for day in keys:
        row = days[day]
        sessions = []
        for daily_session in row["sessions"].values():
            session_id = daily_session["id"]
            available = set()
            if metric_available(daily_session, "cost"):
                available.add(session_id)
            if metric_available(daily_session, "tokens"):
                available.add(session_id)
            estimated_ids = {session_id} if daily_session.get("token_estimate") else set()
            daily_session["provenance"] = make_usage_provenance(
                {session_id}, estimated_ids, available,
                daily_session["cost"] if estimated_ids else 0,
                daily_session["tokens"] if estimated_ids else 0,
            )
            daily_session["usage_basis"] = daily_session["provenance"]["usage_basis"]
            sessions.append(daily_session)
        sessions.sort(key=lambda value: (-value["cost"], value["title"]))
        providers = []
        for provider in row["providers"].values():
            session_ids = provider.pop("session_ids")
            cost_ids = provider.pop("cost_covered_ids")
            token_ids = provider.pop("token_covered_ids")
            cache_ids = provider.pop("cache_covered_ids")
            estimated_ids = provider.pop("estimated_ids")
            total_sessions = len(session_ids)
            cost_covered = len(cost_ids)
            token_covered = len(token_ids)
            cache_covered = len(cache_ids)
            provider_coverage = {
                "cost": {"covered_sessions": cost_covered, "total_sessions": total_sessions,
                         "complete": cost_covered == total_sessions},
                "tokens": {"covered_sessions": token_covered, "total_sessions": total_sessions,
                           "complete": token_covered == total_sessions},
                "cache": {"covered_sessions": cache_covered, "total_sessions": total_sessions,
                          "complete": cache_covered == total_sessions},
            }
            provider["coverage"] = provider_coverage
            provider["availability"] = {
                "cost": cost_covered > 0, "tokens": token_covered > 0,
                "input_tokens": token_covered > 0, "output_tokens": token_covered > 0,
                "cache": cache_covered > 0, "throughput": token_covered > 0,
                "context": True, "timing": bool(provider["wait_samples"]), "tool_results": True,
            }
            provider["provenance"] = make_usage_provenance(
                session_ids, estimated_ids, cost_ids | token_ids,
                provider.pop("estimated_cost"), provider.pop("estimated_tokens"),
            )
            provider["usage_basis"] = provider["provenance"]["usage_basis"]
            providers.append(provider)
        providers = sorted(providers,
                           key=lambda value: (-value["cost"], -value["wait_s"], value["provider"]))
        session_ids = row.pop("session_ids")
        cost_ids = row.pop("cost_covered_ids")
        token_ids = row.pop("token_covered_ids")
        cache_ids = row.pop("cache_covered_ids")
        estimated_ids = row.pop("estimated_ids")
        total_sessions = len(session_ids)
        cost_covered = len(cost_ids)
        token_covered = len(token_ids)
        cache_covered = len(cache_ids)
        coverage = {
            "cost": {"covered_sessions": cost_covered, "total_sessions": total_sessions,
                     "complete": cost_covered == total_sessions},
            "tokens": {"covered_sessions": token_covered, "total_sessions": total_sessions,
                       "complete": token_covered == total_sessions},
            "cache": {"covered_sessions": cache_covered, "total_sessions": total_sessions,
                      "complete": cache_covered == total_sessions},
        }
        provenance = make_usage_provenance(
            session_ids, estimated_ids, cost_ids | token_ids,
            row.pop("estimated_cost"), row.pop("estimated_tokens"),
        )
        result.append({
            "day": day, "cost": row["cost"], "tokens": row["tokens"],
            "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            **{
                field: int(row.get(field) or 0)
                for field in cache_duration_fields
                if field in row
            },
            "sessions": len(sessions),
            "projects": len(row["projects"]), "providers": providers,
            "coverage": coverage, "provenance": provenance,
            "usage_basis": provenance["usage_basis"],
            "availability": {
                "cost": cost_covered > 0, "tokens": token_covered > 0,
                "input_tokens": token_covered > 0, "output_tokens": token_covered > 0,
                "cache": cache_covered > 0, "throughput": token_covered > 0,
                "context": True, "timing": bool(row["wait_samples"]), "tool_results": True,
            },
            "wait_time": {
                "available": bool(row["wait_samples"]),
                "total_s": row["wait_s"],
                "avg_s": row["wait_s"] / row["wait_samples"] if row["wait_samples"] else 0,
                "max_s": row["longest_wait_s"],
                "sample_count": row["wait_samples"],
            },
            "top_sessions": sessions[:8],
        })
    return result


def spend_projection(daily_rows):
    """Project day-cost evidence without session or project identity."""
    return [{
        "day": row.get("day") or "",
        "cost": float(row.get("cost") or 0),
        "providers": [{
            "provider": provider.get("provider") or "unknown",
            "cost": float(provider.get("cost") or 0),
            "coverage": copy.deepcopy(provider.get("coverage") or {}),
            "provenance": copy.deepcopy(provider.get("provenance") or {}),
            "usage_basis": provider.get("usage_basis") or "unavailable",
            "availability": copy.deepcopy(provider.get("availability") or {}),
        } for provider in (row.get("providers") or [])],
        "coverage": copy.deepcopy(row.get("coverage") or {}),
        "provenance": copy.deepcopy(row.get("provenance") or {}),
        "usage_basis": row.get("usage_basis") or "unavailable",
        "availability": copy.deepcopy(row.get("availability") or {}),
    } for row in (daily_rows or [])]


def spend_log_summaries(session_rows, start_day, end_day,
                        availability_resolver=None):
    """Aggregate every public session with covered spend in an inclusive range."""
    availability_resolver = availability_resolver or (lambda _provider: {
        "cost": True, "tokens": True, "input_tokens": True,
        "output_tokens": True, "cache": True, "throughput": True,
        "context": True, "timing": True, "tool_results": True,
    })
    result = []
    for session in session_rows or []:
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        matching = [
            float(value or 0)
            for day, value in (session.get("_day_cost") or {}).items()
            if start_day <= str(day) <= end_day and float(value or 0) > 0
        ]
        cost = sum(matching)
        if cost <= 0:
            continue
        provider = session.get("provider") or "unknown"
        available = metric_available(session, "cost")
        estimated = bool(session.get("token_estimate"))
        provenance = make_usage_provenance(
            {session_id},
            {session_id} if estimated else (),
            {session_id} if available else (),
            cost if estimated and available else 0,
        )
        result.append({
            "id": session_id,
            "title": session.get("title") or session_id,
            "project": session.get("project") or "No project",
            "provider": provider,
            "label": session.get("label") or provider,
            "cost": cost,
            "input_tokens": int(session.get("input_tokens") or 0),
            "output_tokens": int(session.get("output_tokens") or 0),
            "turns": int(session.get("turns") or 0),
            "duration_s": float(session.get("duration_s") or 0),
            "duration_available": bool(session.get("duration_available")),
            "duration_basis": session.get("duration_basis") or "unavailable",
            "active_days": len(matching),
            "availability": copy.deepcopy(
                session.get("availability") or availability_resolver(provider)
            ),
            "provenance": provenance,
            "usage_basis": provenance["usage_basis"],
        })
    result.sort(key=lambda row: (
        -row["cost"], str(row["title"]).casefold(), row["id"],
    ))
    return result


def monthly_summaries(session_rows, limit=12):
    """Aggregate spend into local calendar months with provider and coverage detail."""
    months = {}
    cache_duration_fields = (
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "cache_write_unspecified_tokens",
    )

    def add_cache_duration(target, values):
        for field in cache_duration_fields:
            if field in values:
                target[field] = int(target.get(field) or 0) + int(
                    values.get(field) or 0
                )

    def month_row(month):
        return months.setdefault(month, {
            "month": month, "cost": 0.0, "days": {},
            "session_ids": set(), "cost_covered_ids": set(), "estimated_ids": set(),
            "estimated_cost": 0.0, "providers": {},
        })

    def provider_row(row, provider):
        return row["providers"].setdefault(provider, {
            "provider": provider, "cost": 0.0, "session_ids": set(),
            "cost_covered_ids": set(), "estimated_ids": set(), "estimated_cost": 0.0,
        })

    def mark_activity(row, session, day):
        provider = session.get("provider") or "unknown"
        session_id = session.get("id") or session.get("path") or "unknown"
        runtime = provider_row(row, provider)
        row["session_ids"].add(session_id)
        runtime["session_ids"].add(session_id)
        if metric_available(session, "cost"):
            row["cost_covered_ids"].add(session_id)
            runtime["cost_covered_ids"].add(session_id)
        if session.get("token_estimate"):
            row["estimated_ids"].add(session_id)
            runtime["estimated_ids"].add(session_id)
        row["days"].setdefault(day, {
            "day": day, "cost": 0.0, "providers": defaultdict(float),
        })
        return runtime

    for session in session_rows or []:
        provider = session.get("provider") or "unknown"
        estimated = bool(session.get("token_estimate"))
        activity_days = {
            str(item.get("day") or "")
            for item in (session.get("_model_daily") or [])
            if item.get("day")
        }
        activity_days.update(
            str(item.get("day") or "")
            for item in (session.get("_wait_samples") or [])
            if item.get("day")
        )
        activity_days.update(
            str(day) for day in (session.get("_day_cost") or {}) if day
        )
        for day in activity_days:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            mark_activity(month_row(day[:7]), session, day)
        for day, raw_cost in (session.get("_day_cost") or {}).items():
            day = str(day or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            cost = float(raw_cost or 0)
            row = month_row(day[:7])
            runtime = mark_activity(row, session, day)
            row["cost"] += cost
            runtime["cost"] += cost
            row["days"][day]["cost"] += cost
            row["days"][day]["providers"][provider] += cost
            if estimated:
                row["estimated_cost"] += cost
                runtime["estimated_cost"] += cost
        for stats in session.get("_model_daily") or []:
            day = str(stats.get("day") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            row = month_row(day[:7])
            runtime = mark_activity(row, session, day)
            add_cache_duration(row, stats)
            add_cache_duration(runtime, stats)
            add_cache_duration(row["days"][day], stats)

    keys = sorted(months, reverse=True)
    if limit is not None and limit > 0:
        keys = keys[:limit]
    result = []
    for month in keys:
        row = months[month]
        session_ids = row.pop("session_ids")
        cost_ids = row.pop("cost_covered_ids")
        estimated_ids = row.pop("estimated_ids")
        providers = []
        for runtime in row["providers"].values():
            runtime_ids = runtime.pop("session_ids")
            runtime_cost_ids = runtime.pop("cost_covered_ids")
            runtime_estimated_ids = runtime.pop("estimated_ids")
            runtime["coverage"] = {
                "cost": {
                    "covered_sessions": len(runtime_cost_ids),
                    "total_sessions": len(runtime_ids),
                    "complete": len(runtime_cost_ids) == len(runtime_ids),
                }
            }
            runtime["provenance"] = make_usage_provenance(
                runtime_ids, runtime_estimated_ids, runtime_cost_ids,
                runtime.pop("estimated_cost"), 0,
            )
            runtime["usage_basis"] = runtime["provenance"]["usage_basis"]
            providers.append(runtime)
        providers.sort(key=lambda item: (-item["cost"], item["provider"]))
        coverage = {
            "cost": {
                "covered_sessions": len(cost_ids),
                "total_sessions": len(session_ids),
                "complete": len(cost_ids) == len(session_ids),
            }
        }
        provenance = make_usage_provenance(
            session_ids, estimated_ids, cost_ids, row.pop("estimated_cost"), 0,
        )
        days = []
        for day in sorted(row["days"]):
            item = row["days"][day]
            item["providers"] = [
                {"provider": provider, "cost": cost}
                for provider, cost in sorted(
                    item["providers"].items(), key=lambda pair: (-pair[1], pair[0])
                )
            ]
            days.append(item)
        result.append({
            "month": month,
            "cost": row["cost"],
            **{
                field: int(row.get(field) or 0)
                for field in cache_duration_fields
                if field in row
            },
            "sessions": len(session_ids),
            "active_days": sum(1 for item in days if item["cost"] > 0),
            "observed_days": len(days),
            "days": days,
            "providers": providers,
            "coverage": coverage,
            "provenance": provenance,
            "usage_basis": provenance["usage_basis"],
            "availability": {"cost": bool(cost_ids)},
        })
    return result


def global_tool_waste(session_rows, runtime_resolver=None):
    by_name = {}
    by_namespace = {}
    by_skill = {}
    day_tokens = defaultdict(int)
    day_flagged = defaultdict(int)
    totals = {
        "total_calls": 0, "total_output_tokens": 0, "flagged_tokens": 0,
        "oversized_calls": 0, "oversized_tokens": 0,
        "repeat_calls": 0, "repeat_tokens": 0,
        "errors": 0, "error_tokens": 0,
        "definition_tokens": 0, "eager_definition_tokens": 0,
        "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
    }
    advertised_names = set()
    used_advertised_names = set()
    provider_sessions = defaultdict(int)
    runtime_sessions = defaultdict(int)

    def evidence_key(name, kind, provider):
        # Shared capability identities remain global; plain tools stay runtime-owned.
        return name if kind == "mcp" else f"{provider}::{name}"

    for session in session_rows:
        evidence = session.get("_tool_evidence") or {}
        project = session.get("project") or "local"
        provider = session.get("provider") or "unknown"
        provider_sessions[str(provider).lower()] += 1
        runtime = session.get("runtime") or (runtime_resolver(session) if runtime_resolver else None) or session.get("label") or provider
        runtime_sessions[str(runtime)] += 1
        session_id = session.get("id") or session.get("path")
        for key in ("total_calls", "total_output_tokens", "flagged_tokens", "oversized_calls",
                    "oversized_tokens", "repeat_calls", "repeat_tokens", "errors", "error_tokens",
                    "definition_tokens", "eager_definition_tokens", "deferred_definition_tokens",
                    "unused_eager_definition_tokens"):
            totals[key] += int(evidence.get(key) or 0)
        for day, value in (evidence.get("day_tokens") or {}).items():
            day_tokens[day] += int(value or 0)
        for day, value in (evidence.get("day_flagged") or {}).items():
            day_flagged[day] += int(value or 0)

        for item in evidence.get("tools") or []:
            name = item.get("name") or "?"
            kind = item.get("kind") or "tool"
            key = evidence_key(name, kind, provider)
            row = by_name.setdefault(key, {
                "id": key,
                "name": name,
                "display": item.get("display") or name,
                "namespace": item.get("namespace") or "unknown",
                "kind": kind, "runtime": runtime,
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
                "definition_tokens": 0, "eager_definition_tokens": 0,
                "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
            })
            for key in ("calls", "output_tokens", "flagged_tokens", "errors", "oversized_calls", "repeat_calls"):
                row[key] += int(item.get(key) or 0)
            row["last_ts"] = max(row["last_ts"], int(item.get("last_ts") or 0))
            row["sessions"].add(session_id)
            row["projects"].add(project)
            row["project_calls"][project] += int(item.get("calls") or 0)
            row["providers"].add(provider)

            namespace = row["namespace"]
            namespace_key = evidence_key(namespace, row["kind"], provider)
            ns = by_namespace.setdefault(namespace_key, {
                "id": namespace_key, "namespace": namespace, "kind": row["kind"],
                "runtime": runtime, "providers": set(), "calls": 0,
                "output_tokens": 0, "flagged_tokens": 0, "errors": 0,
                "sessions": set(), "projects": set(),
            })
            ns["calls"] += int(item.get("calls") or 0)
            ns["output_tokens"] += int(item.get("output_tokens") or 0)
            ns["flagged_tokens"] += int(item.get("flagged_tokens") or 0)
            ns["errors"] += int(item.get("errors") or 0)
            ns["sessions"].add(session_id)
            ns["projects"].add(project)
            ns["providers"].add(provider)

        for item in evidence.get("skills") or []:
            name = item.get("name") or "?"
            row = by_skill.setdefault(name, {
                "name": name, "activations": 0, "last_ts": 0,
                "sessions": set(), "projects": set(), "providers": set(),
            })
            row["activations"] += int(item.get("activations") or 0)
            row["last_ts"] = max(row["last_ts"], int(item.get("last_ts") or 0))
            row["sessions"].add(session_id)
            row["projects"].add(project)
            row["providers"].add(provider)

        session_used = {item.get("name") for item in evidence.get("tools") or []}
        for item in evidence.get("catalog") or []:
            name = item.get("name") or "?"
            kind = item.get("kind") or "tool"
            key = evidence_key(name, kind, provider)
            advertised_names.add(name)
            if name in session_used:
                used_advertised_names.add(name)
            row = by_name.setdefault(key, {
                "id": key,
                "name": name, "display": name,
                "namespace": item.get("namespace") or "unknown",
                "kind": kind, "runtime": runtime,
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
                "definition_tokens": 0, "eager_definition_tokens": 0,
                "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
            })
            row["advertised_sessions"].add(session_id)
            definition_tokens = int(item.get("definition_tokens") or 0)
            row["definition_tokens"] += definition_tokens
            if item.get("defer_loading"):
                row["deferred_sessions"].add(session_id)
                row["deferred_definition_tokens"] += definition_tokens
            else:
                row["eager_sessions"].add(session_id)
                row["eager_definition_tokens"] += definition_tokens
                if name not in session_used:
                    row["unused_eager_definition_tokens"] += definition_tokens

    total_sessions = len(session_rows)
    tool_rows = []
    for row in by_name.values():
        sessions_used = len(row["sessions"])
        advertised_sessions = len(row["advertised_sessions"])
        project_calls = dict(row["project_calls"])
        top_project = max(project_calls, key=project_calls.get) if project_calls else ""
        top_project_calls = project_calls.get(top_project, 0)
        project_share = top_project_calls / row["calls"] if row["calls"] else 0.0
        diagnostic = bool(row["kind"] == "mcp" and (
            row["namespace"] == "tokenmeter" or str(row["name"]).startswith("mcp__tokenmeter__")
        ))
        recommendation = "keep"
        reason = "Observed usage does not cross a trace-waste threshold."
        if diagnostic:
            reason = "Token Meter diagnostic overhead is retained for accounting but excluded from cleanup advice."
        elif row["kind"] == "mcp" and advertised_sessions >= 5 and sessions_used == 0:
            recommendation = "disable"
            reason = f"Reported in {advertised_sessions} sessions and never called."
        elif row["errors"] >= 3 and row["errors"] / max(1, row["calls"]) >= 0.5:
            recommendation = "fix_or_disable"
            reason = f"{row['errors']} of {row['calls']} calls were errors."
        elif row["oversized_calls"] or row["output_tokens"] >= 25000:
            recommendation = "narrow_results"
            reason = f"Returned ~{row['output_tokens']:,} tokens across {row['calls']} calls."
        elif row["repeat_calls"] >= 3:
            recommendation = "reduce_repeats"
            reason = f"Repeated the same arguments in {row['repeat_calls']} consecutive calls."
        elif row["kind"] == "mcp" and sessions_used <= max(1, int(total_sessions * 0.05)):
            recommendation = "scope"
            reason = f"Used in {sessions_used} of {total_sessions} sessions."
        elif project_share >= 0.8 and row["calls"] >= 5 and len(row["projects"]) > 0:
            recommendation = "scope"
            reason = f"{project_share * 100:.0f}% of calls came from {top_project}."

        tool_rows.append({
            "id": row["id"], "name": row["name"], "display": row["display"],
            "namespace": row["namespace"], "kind": row["kind"],
            "runtime": row["runtime"],
            "calls": row["calls"], "output_tokens": row["output_tokens"],
            "flagged_tokens": row["flagged_tokens"], "errors": row["errors"],
            "oversized_calls": row["oversized_calls"], "repeat_calls": row["repeat_calls"],
            "sessions_used": sessions_used, "advertised_sessions": advertised_sessions,
            "eager_sessions": len(row["eager_sessions"]), "deferred_sessions": len(row["deferred_sessions"]),
            "definition_tokens": row["definition_tokens"],
            "eager_definition_tokens": row["eager_definition_tokens"],
            "deferred_definition_tokens": row["deferred_definition_tokens"],
            "unused_eager_definition_tokens": row["unused_eager_definition_tokens"],
            "projects": sorted(row["projects"]), "top_project": top_project,
            "project_share": project_share, "providers": sorted(row["providers"]),
            "last_ts": row["last_ts"],
            "last_used": time.strftime("%Y-%m-%d", time.localtime(row["last_ts"])) if row["last_ts"] else "Never",
            "recommendation": recommendation, "reason": reason,
            "mcp_server": row["namespace"] if row["kind"] == "mcp" else "",
            "diagnostic": diagnostic,
        })

    tool_rows.sort(key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))
    namespace_rows = []
    for row in by_namespace.values():
        namespace_rows.append({
            "id": row["id"], "namespace": row["namespace"], "kind": row["kind"],
            "runtime": row["runtime"], "providers": sorted(row["providers"]),
            "calls": row["calls"], "output_tokens": row["output_tokens"],
            "flagged_tokens": row["flagged_tokens"], "errors": row["errors"],
            "sessions_used": len(row["sessions"]), "projects": sorted(row["projects"]),
        })
    namespace_rows.sort(key=lambda r: (-r["output_tokens"], -r["calls"], r["namespace"]))

    trend = [
        {"day": day, "tokens": day_tokens[day], "flagged_tokens": day_flagged.get(day, 0)}
        for day in sorted(set(day_tokens) | set(day_flagged))
    ][-14:]

    insights = []
    actionable_rows = [row for row in tool_rows if not row.get("diagnostic")]
    disable_candidates = [row for row in actionable_rows if row["recommendation"] == "disable"]
    if disable_candidates:
        candidate = disable_candidates[0]
        insights.append(insight(
            f"global-disable:{candidate['name']}", "warn", "Tools", "MCP disable candidate",
            f"{candidate['display']} was advertised in {candidate['advertised_sessions']} sessions and never called.",
            detail=f"Runtime-reported namespace: {candidate['namespace']}.",
            action="Review and disable the MCP server if it is no longer needed.", priority=8,
        ))

    catalog_count = len(advertised_names)
    catalog_used = len(used_advertised_names)
    if totals["unused_eager_definition_tokens"]:
        eager = totals["eager_definition_tokens"]
        share = totals["unused_eager_definition_tokens"] / max(1, eager)
        insights.append(insight(
            "global-eager-tax", "warn" if share >= 0.5 else "neutral", "Tools", "Unused eager schema tax",
            f"~{totals['unused_eager_definition_tokens']:,} eager tool-definition tokens were loaded in sessions that did not call those tools.",
            detail=f"That is {share * 100:.0f}% of {eager:,} eager definition tokens across runtime-reported catalogs.",
            action="Move rarely used capabilities to deferred loading or disable their provider." if share >= 0.5 else "",
            priority=9,
        ))

    skill_rows = [{
        "name": row["name"], "activations": row["activations"],
        "sessions_used": len(row["sessions"]), "projects": sorted(row["projects"]),
        "providers": sorted(row["providers"]), "last_ts": row["last_ts"],
        "last_used": time.strftime("%Y-%m-%d", time.localtime(row["last_ts"])) if row["last_ts"] else "Never",
    } for row in by_skill.values()]
    skill_rows.sort(key=lambda row: (-row["activations"], row["name"]))

    return {
        **dict(totals),
        "total_sessions": total_sessions,
        "provider_sessions": dict(provider_sessions),
        "runtime_sessions": dict(runtime_sessions),
        "sessions_with_tools": sum(1 for row in session_rows if (row.get("_tool_evidence") or {}).get("total_calls")),
        "by_name": (tool_rows[:20] + [
            row for row in tool_rows[20:] if row["recommendation"] in ("disable", "fix_or_disable")
        ])[:24],
        "inventory_tools": tool_rows[:240],
        "by_namespace": namespace_rows[:16],
        "skills": skill_rows[:80],
        "catalog_unique": catalog_count,
        "catalog_used_unique": catalog_used,
        "catalog_utilization": (catalog_used / catalog_count) if catalog_count else 0.0,
        "trend": trend,
        "insights": normalize_insights(insights, limit=8),
    }


def aggregate_model_stats(session_rows, runtime_resolver=None, throughput_finalizer=None,
                          matched_pace=None, project_option_limit=500,
                          project_resolver=None):
    """Aggregate model I/O by runtime and build workload-matched pace comparisons."""
    session_rows = list(session_rows or [])
    if throughput_finalizer is None or matched_pace is None:
        raise ValueError("aggregate_model_stats requires pure statistic callbacks")
    models = {}
    pace_groups = defaultdict(list)

    def model_row(name, runtime, reasoning_effort=""):
        name = name or "unknown"
        runtime = runtime or "unknown runtime"
        reasoning_effort = reasoning_effort or ""
        row_id = f"{name}::{runtime}::{reasoning_effort or 'unavailable'}"
        return models.setdefault(row_id, {
            "id": row_id, "model": name, "runtime": runtime,
            "reasoning_effort": reasoning_effort or None,
            "providers": set(), "logs": 0,
            "executions": 0, "token_covered_executions": 0,
            "io_covered_executions": 0,
            "cost_covered_executions": 0,
            "input_tokens": 0, "output_tokens": 0,
            "cost_covered_output_tokens": 0,
            "cost_covered_cost": 0.0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cache_covered_input_tokens": 0,
            "reasoning_tokens": 0, "reasoning_output_tokens": 0,
            "reasoning_executions": 0, "reasoning_unavailable_executions": 0,
            "reasoning_efforts": {reasoning_effort} if reasoning_effort else set(),
            "thinking_executions": 0, "thinking_covered_executions": 0,
            "attempts": 0, "retries": 0, "failed_attempts": 0,
            "attempt_samples": 0, "cost": 0.0,
            "timed_output_tokens": 0, "timed_seconds": 0.0, "timed_samples": 0,
            "tool_free_output_tokens": 0, "tool_free_seconds": 0.0, "tool_free_samples": 0,
            "ttft_total_s": 0.0, "ttft_samples": 0, "last_ts": 0, "daily": {},
            "wait_seconds": 0.0, "wait_samples": 0, "max_wait_s": 0.0,
            "wait_durations_s": [], "user_pause_seconds": 0.0,
            "workload_peak_inputs": [], "workload_outputs": [],
            "workload_tool_calls": [], "workload_model_calls": [],
            "workload_cache_ratios": [],
            "_log_ids": set(), "_cost_covered_ids": set(), "_token_covered_ids": set(),
            "_input_covered_ids": set(), "_output_covered_ids": set(),
            "_cache_covered_ids": set(), "_estimated_ids": set(),
            "_estimated_cost": 0.0, "_estimated_tokens": 0,
            "_explicit_output_evidence": False,
        })

    def daily_row(parent, day):
        return parent["daily"].setdefault(day, {
            "day": day, "input_tokens": 0, "output_tokens": 0,
            "cost_covered_output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cache_covered_input_tokens": 0,
            "reasoning_tokens": 0, "reasoning_output_tokens": 0,
            "reasoning_executions": 0, "reasoning_unavailable_executions": 0,
            "reasoning_efforts": set(),
            "thinking_executions": 0, "thinking_covered_executions": 0,
            "attempts": 0, "retries": 0, "failed_attempts": 0,
            "attempt_samples": 0, "executions": 0,
            "token_covered_executions": 0, "io_covered_executions": 0,
            "cost_covered_executions": 0,
            "cost_covered_cost": 0.0,
            "cost": 0.0,
            "timed_output_tokens": 0, "timed_seconds": 0.0, "timed_samples": 0,
            "tool_free_output_tokens": 0, "tool_free_seconds": 0.0, "tool_free_samples": 0,
            "ttft_total_s": 0.0, "ttft_samples": 0,
            "wait_seconds": 0.0, "wait_samples": 0, "max_wait_s": 0.0,
            "wait_durations_s": [], "user_pause_seconds": 0.0,
            "workload_peak_inputs": [], "workload_outputs": [],
            "workload_tool_calls": [], "workload_model_calls": [],
            "workload_cache_ratios": [],
            "_log_ids": set(), "_cost_covered_ids": set(), "_token_covered_ids": set(),
            "_input_covered_ids": set(), "_output_covered_ids": set(),
            "_cache_covered_ids": set(), "_estimated_ids": set(),
            "_estimated_cost": 0.0, "_estimated_tokens": 0,
        })

    def mark_model_coverage(target, session, session_id, availability=None):
        explicit_availability = isinstance(availability, dict)
        first_session_evidence = session_id not in target["_log_ids"]
        availability = availability if explicit_availability else {}

        def evidence_available(metric):
            if metric in availability:
                return availability.get(metric) is True
            return metric_available(session, metric)

        target["_log_ids"].add(session_id)
        if session.get("token_estimate"):
            target["_estimated_ids"].add(session_id)
        if not explicit_availability and not first_session_evidence:
            return
        if evidence_available("cost"):
            target["_cost_covered_ids"].add(session_id)
        token_available = evidence_available("tokens")
        if token_available:
            target["_token_covered_ids"].add(session_id)
        if token_available and evidence_available("input_tokens"):
            target["_input_covered_ids"].add(session_id)
        if token_available and evidence_available("output_tokens"):
            target["_output_covered_ids"].add(session_id)
        if evidence_available("cache"):
            target["_cache_covered_ids"].add(session_id)

    def mark_explicit_output_evidence(target, stats):
        availability = stats.get("availability")
        if (isinstance(availability, dict)
                and availability.get("output_tokens") is True
                and int(stats.get("token_covered_executions") or 0) > 0):
            target["_explicit_output_evidence"] = True

    def session_reasoning_efforts(session, models):
        effort = _compact_text(session.get("reasoning_effort") or "", 20).lower()
        if effort not in REPORTED_REASONING_EFFORTS:
            return {}
        if len(models) == 1:
            return {next(iter(models)): effort}
        primary_model = str(session.get("primary_model") or "")
        return {primary_model: effort} if primary_model in models else {}

    def mark_reasoning_effort(target, effort):
        if effort:
            target["reasoning_efforts"].add(effort)

    for session in session_rows:
        provider = session.get("provider") or "unknown"
        runtime = session.get("runtime") or (runtime_resolver(session) if runtime_resolver else None) or session.get("label") or provider
        session_id = session.get("id") or session.get("path") or f"session-{id(session)}"
        session_models = {
            str(stats.get("model") or "unknown-model")
            for stats in [*(session.get("model_stats") or []),
                          *(session.get("_model_daily") or [])]
        }
        efforts_by_model = session_reasoning_efforts(session, session_models)
        for stats in session.get("model_stats") or []:
            effort = efforts_by_model.get(
                str(stats.get("model") or "unknown-model"), ""
            )
            row = model_row(stats.get("model"), runtime, effort)
            row["providers"].add(provider)
            mark_reasoning_effort(row, effort)
            stats_availability = stats.get("availability") or {}
            mark_model_coverage(row, session, session_id, stats_availability)
            mark_explicit_output_evidence(row, stats)
            executions = int(stats.get("executions") or 0)
            row["executions"] += executions
            if "token_covered_executions" in stats:
                row["token_covered_executions"] += int(
                    stats.get("token_covered_executions") or 0
                )
            elif (stats_availability.get("tokens") is not False
                    and metric_available(session, "tokens")):
                row["token_covered_executions"] += executions
            if "io_covered_executions" in stats:
                row["io_covered_executions"] += int(
                    stats.get("io_covered_executions") or 0
                )
            elif (stats_availability.get("tokens") is not False
                  and stats_availability.get("input_tokens") is not False
                  and stats_availability.get("output_tokens") is not False
                  and metric_available(session, "tokens")):
                row["io_covered_executions"] += executions
            if "cost_covered_executions" in stats:
                row["cost_covered_executions"] += int(
                    stats.get("cost_covered_executions") or 0
                )
            elif (stats_availability.get("cost") is not False
                    and metric_available(session, "cost")):
                row["cost_covered_executions"] += executions
            row["input_tokens"] += int(stats.get("input_tokens") or 0)
            row["output_tokens"] += int(stats.get("output_tokens") or 0)
            if "cost_covered_output_tokens" in stats:
                row["cost_covered_output_tokens"] += int(
                    stats.get("cost_covered_output_tokens") or 0
                )
            elif (stats_availability.get("cost") is not False
                    and metric_available(session, "cost")):
                row["cost_covered_output_tokens"] += int(
                    stats.get("output_tokens") or 0
                )
            if "cost_covered_cost" in stats:
                row["cost_covered_cost"] += float(
                    stats.get("cost_covered_cost") or 0
                )
            elif (stats_availability.get("cost") is not False
                  and metric_available(session, "cost")):
                row["cost_covered_cost"] += float(stats.get("cost") or 0)
            row["cache_read_tokens"] += int(stats.get("cache_read_tokens") or 0)
            row["cache_write_tokens"] += int(stats.get("cache_write_tokens") or 0)
            for field in (
                "cache_write_5m_tokens",
                "cache_write_1h_tokens",
                "cache_write_unspecified_tokens",
            ):
                if field in stats:
                    row[field] = int(row.get(field) or 0) + int(
                        stats.get(field) or 0
                    )
            if "cache_covered_input_tokens" in stats:
                row["cache_covered_input_tokens"] += int(
                    stats.get("cache_covered_input_tokens") or 0
                )
            elif (stats_availability.get("cache") is not False
                    and metric_available(session, "cache")):
                row["cache_covered_input_tokens"] += int(
                    stats.get("input_tokens") or 0
                )
            row["reasoning_tokens"] += int(stats.get("reasoning_tokens") or 0)
            row["reasoning_output_tokens"] += int(
                stats.get("reasoning_output_tokens") or 0
            )
            row["reasoning_executions"] += int(
                stats.get("reasoning_executions") or 0
            )
            row["thinking_executions"] += int(
                stats.get("thinking_executions") or 0
            )
            row["thinking_covered_executions"] += int(
                stats.get("thinking_covered_executions") or 0
            )
            row["reasoning_unavailable_executions"] += int(
                stats.get(
                    "reasoning_unavailable_executions",
                    max(
                        0,
                        int(stats.get("executions") or 0)
                        - int(stats.get("reasoning_executions") or 0),
                    ),
                ) or 0
            )
            row["cost"] += float(stats.get("cost") or 0)
            if session.get("token_estimate"):
                row["_estimated_cost"] += float(stats.get("cost") or 0)
                row["_estimated_tokens"] += int(stats.get("tokens") or 0)
        for stats in session.get("_model_daily") or []:
            day = stats.get("day") or ""
            if not day:
                continue
            effort = efforts_by_model.get(
                str(stats.get("model") or "unknown-model"), ""
            )
            row = model_row(stats.get("model"), runtime, effort)
            row["providers"].add(provider)
            mark_reasoning_effort(row, effort)
            daily = daily_row(row, day)
            mark_reasoning_effort(daily, effort)
            stats_availability = stats.get("availability") or {}
            mark_model_coverage(row, session, session_id, stats_availability)
            mark_model_coverage(daily, session, session_id, stats_availability)
            mark_explicit_output_evidence(row, stats)
            for key in (
                "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens",
                "reasoning_output_tokens", "reasoning_executions",
                "thinking_executions", "thinking_covered_executions",
                "executions",
            ):
                daily[key] += int(stats.get(key) or 0)
            for key in (
                "cache_write_5m_tokens",
                "cache_write_1h_tokens",
                "cache_write_unspecified_tokens",
            ):
                if key in stats:
                    daily[key] = int(daily.get(key) or 0) + int(
                        stats.get(key) or 0
                    )
            executions = int(stats.get("executions") or 0)
            if "token_covered_executions" in stats:
                daily["token_covered_executions"] += int(
                    stats.get("token_covered_executions") or 0
                )
            elif (stats_availability.get("tokens") is not False
                    and metric_available(session, "tokens")):
                daily["token_covered_executions"] += executions
            if "io_covered_executions" in stats:
                daily["io_covered_executions"] += int(
                    stats.get("io_covered_executions") or 0
                )
            elif (stats_availability.get("tokens") is not False
                  and stats_availability.get("input_tokens") is not False
                  and stats_availability.get("output_tokens") is not False
                  and metric_available(session, "tokens")):
                daily["io_covered_executions"] += executions
            if "cost_covered_executions" in stats:
                daily["cost_covered_executions"] += int(
                    stats.get("cost_covered_executions") or 0
                )
            elif (stats_availability.get("cost") is not False
                    and metric_available(session, "cost")):
                daily["cost_covered_executions"] += executions
            if "cost_covered_output_tokens" in stats:
                daily["cost_covered_output_tokens"] += int(
                    stats.get("cost_covered_output_tokens") or 0
                )
            elif (stats_availability.get("cost") is not False
                    and metric_available(session, "cost")):
                daily["cost_covered_output_tokens"] += int(
                    stats.get("output_tokens") or 0
                )
            if "cost_covered_cost" in stats:
                daily["cost_covered_cost"] += float(
                    stats.get("cost_covered_cost") or 0
                )
            elif (stats_availability.get("cost") is not False
                  and metric_available(session, "cost")):
                daily["cost_covered_cost"] += float(stats.get("cost") or 0)
            if "cache_covered_input_tokens" in stats:
                daily["cache_covered_input_tokens"] += int(
                    stats.get("cache_covered_input_tokens") or 0
                )
            elif (stats_availability.get("cache") is not False
                    and metric_available(session, "cache")):
                daily["cache_covered_input_tokens"] += int(
                    stats.get("input_tokens") or 0
                )
            daily["reasoning_unavailable_executions"] += int(
                stats.get(
                    "reasoning_unavailable_executions",
                    max(
                        0,
                        int(stats.get("executions") or 0)
                        - int(stats.get("reasoning_executions") or 0),
                    ),
                ) or 0
            )
            daily["cost"] += float(stats.get("cost") or 0)
            if session.get("token_estimate"):
                daily["_estimated_cost"] += float(stats.get("cost") or 0)
                daily["_estimated_tokens"] += int(stats.get("input_tokens") or 0) + int(stats.get("output_tokens") or 0)
        for sample in session.get("_performance_samples") or []:
            effort = efforts_by_model.get(
                str(sample.get("model") or "unknown-model"), ""
            )
            row = model_row(sample.get("model"), runtime, effort)
            row["providers"].add(provider)
            mark_reasoning_effort(row, effort)
            mark_model_coverage(row, session, session_id)
            day = sample.get("day") or ""
            targets = [row]
            if day:
                daily_target = daily_row(row, day)
                mark_model_coverage(daily_target, session, session_id)
                targets.append(daily_target)
            output_tokens = int(sample.get("output_tokens") or 0)
            duration_s = float(sample.get("duration_s") or 0)
            generation_s = float(sample.get("generation_s") or duration_s)
            ttft_s = float(sample.get("ttft_s") or 0)
            input_tokens = int(sample.get("input_tokens") or 0)
            peak_input_tokens = int(sample.get("peak_input_tokens") or input_tokens)
            tool_calls = int(sample.get("tool_calls") or 0)
            model_calls = max(1, int(sample.get("model_calls") or 1))
            cache_read_tokens = int(sample.get("cache_read_tokens") or 0)
            cache_ratio = min(1.0, cache_read_tokens / input_tokens) if input_tokens else 0.0
            for target in targets:
                target["timed_output_tokens"] += output_tokens
                target["timed_seconds"] += duration_s
                target["timed_samples"] += 1
                if int(sample.get("tool_calls") or 0) == 0:
                    target["tool_free_output_tokens"] += output_tokens
                    target["tool_free_seconds"] += generation_s
                    target["tool_free_samples"] += 1
                if ttft_s > 0:
                    target["ttft_total_s"] += ttft_s
                    target["ttft_samples"] += 1
                context_tokens = int(sample.get("context_tokens") or 0)
                if (context_tokens > 0
                        and len(target["workload_peak_inputs"]) < WORKLOAD_SAMPLE_LIMIT):
                    target["workload_peak_inputs"].append(context_tokens)
                    target["workload_tool_calls"].append(int(sample.get("tool_calls") or 0))
                    target["workload_model_calls"].append(
                        max(1, int(sample.get("model_calls") or 1))
                    )
                if len(target["workload_peak_inputs"]) < WORKLOAD_SAMPLE_LIMIT:
                    target["workload_peak_inputs"].append(peak_input_tokens)
                    target["workload_outputs"].append(output_tokens)
                    target["workload_tool_calls"].append(tool_calls)
                    target["workload_model_calls"].append(model_calls)
                    if metric_available(session, "cache"):
                        target["workload_cache_ratios"].append(cache_ratio)
                attempts = int(sample.get("attempts") or 0)
                if attempts > 0:
                    target["attempts"] += attempts
                    target["retries"] += min(
                        attempts, max(0, int(sample.get("retries") or 0))
                    )
                    target["failed_attempts"] += min(
                        attempts, max(0, int(sample.get("failed_attempts") or 0))
                    )
                    target["attempt_samples"] += 1
            if (not session.get("token_estimate") and duration_s > 0
                    and input_tokens > 0 and output_tokens > 0
                    and len(pace_groups[row["id"]]) < MATCHED_PACE_SAMPLE_LIMIT):
                pace_groups[row["id"]].append({
                    "day": day, "ts": float(sample.get("ts") or 0),
                    "duration_s": duration_s, "input_tokens": input_tokens,
                    "peak_input_tokens": peak_input_tokens,
                    "output_tokens": output_tokens, "tool_calls": tool_calls,
                    "model_calls": model_calls, "cache_read_tokens": cache_read_tokens,
                })
            row["last_ts"] = max(float(row.get("last_ts") or 0), float(sample.get("ts") or 0))
        for sample in session.get("_wait_samples") or []:
            model = sample.get("model") or ""
            if not model or model in ("mixed", "unknown"):
                continue
            effort = efforts_by_model.get(str(model), "")
            row = model_row(model, runtime, effort)
            row["providers"].add(provider)
            mark_reasoning_effort(row, effort)
            mark_model_coverage(row, session, session_id)
            day = sample.get("day") or ""
            targets = [row]
            if day:
                daily_target = daily_row(row, day)
                mark_model_coverage(daily_target, session, session_id)
                targets.append(daily_target)
            duration_s = float(sample.get("duration_s") or 0)
            if duration_s <= 0:
                continue
            for target in targets:
                target["wait_seconds"] += duration_s
                target["wait_samples"] += 1
                target["max_wait_s"] = max(float(target.get("max_wait_s") or 0), duration_s)
                target["wait_durations_s"].append(duration_s)
                target["user_pause_seconds"] += float(sample.get("user_pause_s") or 0)
                ttft_s = float(sample.get("ttft_s") or 0)
                if ttft_s > 0:
                    target["ttft_total_s"] += ttft_s
                    target["ttft_samples"] += 1
                context_tokens = int(sample.get("context_tokens") or 0)
                if (context_tokens > 0
                        and len(target["workload_peak_inputs"]) < WORKLOAD_SAMPLE_LIMIT):
                    target["workload_peak_inputs"].append(context_tokens)
                    target["workload_tool_calls"].append(int(sample.get("tool_calls") or 0))
                    target["workload_model_calls"].append(
                        max(1, int(sample.get("model_calls") or 1))
                    )

    def finalize_coverage(row):
        log_ids = set(row.pop("_log_ids", set()))
        total_logs = len(log_ids)
        cost_ids = set(row.pop("_cost_covered_ids", set()))
        token_ids = set(row.pop("_token_covered_ids", set()))
        input_ids = set(row.pop("_input_covered_ids", set()))
        output_ids = set(row.pop("_output_covered_ids", set()))
        cache_ids = set(row.pop("_cache_covered_ids", set()))
        estimated_ids = set(row.pop("_estimated_ids", set()))
        covered_cost = len(cost_ids)
        covered_tokens = len(token_ids)
        covered_input = len(input_ids)
        covered_output = len(output_ids)
        covered_cache = len(cache_ids)
        executions = int(row.get("executions") or 0)
        cost_covered_executions = int(row.get("cost_covered_executions") or 0)
        token_covered_executions = int(row.get("token_covered_executions") or 0)
        io_covered_executions = int(row.get("io_covered_executions") or 0)
        row["logs"] = total_logs
        row["coverage"] = {
            "cost": {"covered_sessions": covered_cost, "total_sessions": total_logs,
                     "complete": (
                         covered_cost == total_logs
                         and cost_covered_executions >= executions
                     )},
            "tokens": {"covered_sessions": covered_tokens, "total_sessions": total_logs,
                       "complete": (
                           covered_tokens == total_logs
                           and token_covered_executions >= executions
                       )},
            "io": {"covered_executions": io_covered_executions,
                   "total_executions": executions,
                   "complete": io_covered_executions >= executions},
            "cache": {"covered_sessions": covered_cache, "total_sessions": total_logs,
                      "complete": covered_cache == total_logs},
            "reasoning_tokens": {
                "covered_executions": int(row.get("reasoning_executions") or 0),
                "total_executions": int(row.get("executions") or 0),
                "complete": (
                    int(row.get("reasoning_executions") or 0)
                    == int(row.get("executions") or 0)
                ),
            },
            "attempts": {
                "covered_executions": int(row.get("attempt_samples") or 0),
                "total_executions": int(row.get("executions") or 0),
                "complete": (
                    int(row.get("attempt_samples") or 0)
                    >= int(row.get("executions") or 0)
                ),
            },
        }
        row["provenance"] = make_usage_provenance(
            log_ids, estimated_ids, cost_ids | token_ids,
            row.pop("_estimated_cost", 0), row.pop("_estimated_tokens", 0),
        )
        row["usage_basis"] = row["provenance"]["usage_basis"]
        row["availability"] = {
            "cost": covered_cost > 0,
            "tokens": covered_tokens > 0,
            "input_tokens": covered_input > 0,
            "output_tokens": covered_output > 0,
            "cache": covered_cache > 0,
            "reasoning_tokens": int(row.get("reasoning_executions") or 0) > 0,
            "attempts": int(row.get("attempt_samples") or 0) > 0,
            "throughput": covered_output > 0 and int(row.get("throughput_samples") or 0) > 0,
            "context": True,
            "timing": int(row.get("wait_samples") or 0) > 0,
            "tool_results": True,
        }
        return row

    result = []
    for row in models.values():
        explicit_output_evidence = bool(row.pop("_explicit_output_evidence", False))
        placeholder_model = row.get("model") == "<synthetic>"
        if (not row.get("input_tokens") and not row.get("output_tokens")
                and (not explicit_output_evidence or placeholder_model)
                and (not row.get("executions") and not row.get("wait_samples")
                     or len(row.get("_token_covered_ids") or ()) == len(row.get("_log_ids") or ()))):
            continue
        daily = []
        for item in row.pop("daily").values():
            item["reasoning_efforts"] = sorted(item["reasoning_efforts"])[:8]
            daily.append(finalize_coverage(throughput_finalizer(item)))
        daily.sort(key=lambda item: item["day"])
        row["providers"] = sorted(row["providers"])
        row["reasoning_efforts"] = sorted(row["reasoning_efforts"])[:8]
        row["daily"] = daily
        result.append(finalize_coverage(throughput_finalizer(row)))
    result.sort(key=lambda row: (-row["output_tokens"], -row["input_tokens"],
                                 -row["executions"], -row["wait_samples"],
                                 row["model"], row["runtime"]))
    valid_ids = {row["id"] for row in result}
    projects = sorted({
        project_resolver(session.get("project")) if project_resolver else
        str(session.get("project") or "No project")
        for session in session_rows
    }, key=str.casefold)
    return {
        "models": result,
        "total_models": len(result),
        "total_model_names": len({row["model"] for row in result}),
        "first_day": min((day["day"] for row in result for day in row["daily"]), default=""),
        "last_day": max((day["day"] for row in result for day in row["daily"]), default=""),
        "projects": projects[:project_option_limit],
        "projects_truncated": len(projects) > project_option_limit,
        "matched_pace": matched_pace({
            row_id: samples for row_id, samples in pace_groups.items() if row_id in valid_ids
        }),
    }
