"""Allowlisted MCP projections of the Git and Work page aggregates.

Every result is built from named numeric, enum, and model fields only. Project
and repository labels, commit or branch text, file names, request text, and
salted keys never enter a projection, even when the source payload holds them.
"""

import re

from token_meter.mcp.contracts import (
    MCPQueryError,
    bounded_page,
    normalize_limit,
    read_cursor,
)
from token_meter.mcp.projections import safe_number


SCHEMA_VERSION = "1.0"
PAGE_BYTES = 60_000
GIT_RANGES = ("today", "yesterday", "7", "30", "90", "month", "last_month", "all")
GIT_VIEWS = ("summary", "days")
WORK_PERIODS = ("1d", "7d", "30d", "90d", "month", "3", "6", "12", "0")
WORK_VIEWS = ("summary", "models", "right_sizing", "suggestions", "trend")
WORK_TYPES = (
    "feature", "debug", "refactor", "test", "review", "plan", "explore", "ops",
    "docs", "other", "unclear",
)
COMPLEXITIES = ("routine", "everyday", "complex")
TIERS = ("light", "standard", "premium")
EFFORTS = ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OUTCOMES = ("single_shot", "accepted", "recovered", "ended_on_pushback", "unclear", "pending")
TAGS = (
    "marathon", "long_thread", "big_spend", "team", "overkill", "underpowered",
    "rescued", "stuck", "one_shot",
)
FLAGS = ("possible_overspend", "possible_false_economy", "possible_overthinking")
MAX_AREA_CHARS = 60
SUGGESTION_KINDS = (
    "premium_routine", "light_complex", "effort_routine", "switch_model", "family_upgrade", "long_threads",
)
MAX_SUGGESTION_MODELS = 3
# Model and runtime identifiers as traces spell them, including context markers such as [1m].
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/@+ ()\[\]-]+")

GIT_CAVEAT = (
    "Changed lines are additions plus deletions in commits Token Meter saw pushed from local "
    "reflogs; they measure volume, not quality. Spend is a labelled estimate for sessions in "
    "measured repositories. Period changes are neutral differences."
)
WORK_CAVEAT = (
    "Labels come from the local Work insights classifier and are estimates. Pushback is the "
    "share of labeled follow-up requests that pushed back on the previous reply. Spend is a "
    "labelled estimate."
)


def _identifier(value, maximum=120):
    value = str(value or "")
    if not value or len(value) > maximum or value[0] in "/~. " or not _IDENTIFIER.fullmatch(value):
        return ""
    return value


def _number(value, digits=None):
    number = safe_number(value)
    if number is None:
        return None
    return round(number, digits) if digits is not None else number


def _count(value):
    number = safe_number(value, integer=True)
    return number if number is not None and number >= 0 else None


def _usd(value):
    return _number(value, 6)


def _enum(value, allowed):
    value = str(value or "")
    return value if value in allowed else None


def _flags(value, allowed):
    return {key: bool(value.get(key)) for key in allowed} if isinstance(value, dict) else {
        key: False for key in allowed
    }


def _rate(value):
    if not isinstance(value, dict):
        return None
    return {
        "rate": _number(value.get("rate"), 4),
        "samples": _count(value.get("samples")),
        "few_samples": bool(value.get("few_samples")),
    }


def _page(rows, cursor, limit, query, key):
    """Page dated rows; the cursor binds to the window, not to values that move while sessions run."""
    query = dict(query, limit=limit)
    revision = [rows[0][key], rows[-1][key], len(rows)] if rows else []
    offset = read_cursor(cursor, query, revision) if cursor else 0
    return bounded_page(
        rows, offset=offset, limit=limit, query=query, revision=revision,
        max_bytes=PAGE_BYTES,
    )


def normalize_git_arguments(range_key=None, view=None, cursor=None, limit=None):
    range_key = "7" if range_key is None else str(range_key)
    if range_key not in GIT_RANGES:
        raise MCPQueryError("invalid_argument", "range is unsupported")
    view = "summary" if view is None else str(view)
    if view not in GIT_VIEWS:
        raise MCPQueryError("invalid_argument", "view is unsupported")
    if cursor is not None and view != "days":
        raise MCPQueryError("invalid_argument", "cursor applies only to view=days")
    return range_key, view, normalize_limit(limit, 31, 100)


def _git_totals(value):
    """One period's totals; unmeasured code or cost is null, never a measured zero."""
    if not isinstance(value, dict):
        return None
    availability = value.get("availability") or {}
    efficiency = value.get("efficiency") or {}
    efficiency_availability = efficiency.get("availability") or {}
    code = bool(availability.get("code_pushed"))
    cost = bool(availability.get("cost"))
    return {
        "added_lines": _count(value.get("added")) if code else None,
        "deleted_lines": _count(value.get("deleted")) if code else None,
        "changed_lines": _count(value.get("changed_lines")) if code else None,
        "commits": _count(value.get("commits")) if code else None,
        "comparable_changed_lines": _count(value.get("comparable_changed_lines")) if code and cost else None,
        "covered_cost_usd": _usd(value.get("covered_cost")) if cost else None,
        "spend_per_1k_lines_usd": _usd(value.get("spend_per_1k")),
        "output_tokens_per_usd": _number(efficiency.get("output_per_dollar"), 2),
        "lines_per_1k_output_tokens": _number(efficiency.get("delivery_yield"), 4),
        "reasoning_share_of_output": _number(efficiency.get("reasoning_ratio"), 4),
        "availability": {
            "code_pushed": code,
            "cost": cost,
            "spend_per_1k_lines": bool(availability.get("spend_per_1k")),
            "output_tokens_per_usd": bool(efficiency_availability.get("output_per_dollar")),
            "lines_per_1k_output_tokens": bool(efficiency_availability.get("delivery_yield")),
            "reasoning_share_of_output": bool(efficiency_availability.get("reasoning_ratio")),
            "partial": bool(availability.get("partial")),
        },
    }


def _git_day(value):
    if not isinstance(value, dict):
        return None
    day = str(value.get("day") or "")
    if len(day) != 10:
        return None
    availability = value.get("availability") or {}
    efficiency = value.get("efficiency") or {}
    code = bool(availability.get("code_pushed"))
    cost = bool(availability.get("cost"))
    return {
        "day": day,
        "added_lines": _count(value.get("added")) if code else None,
        "deleted_lines": _count(value.get("deleted")) if code else None,
        "changed_lines": _count(value.get("changed_lines")) if code else None,
        "commits": _count(value.get("commits")) if code else None,
        "covered_cost_usd": _usd(value.get("covered_cost")) if cost else None,
        "spend_per_1k_lines_usd": _usd(value.get("spend_per_1k")),
        "rolling_7d_spend_per_1k_lines_usd": _usd(value.get("rolling_spend_per_1k")),
        "output_tokens_per_usd": _number(efficiency.get("output_per_dollar"), 2),
        "lines_per_1k_output_tokens": _number(efficiency.get("delivery_yield"), 4),
        "reasoning_share_of_output": _number(efficiency.get("reasoning_ratio"), 4),
        "availability": {"code_pushed": code, "cost": cost},
    }


def _git_model(value):
    if not isinstance(value, dict):
        return None
    model = _identifier(value.get("model"), 120)
    if not model:
        return None
    return {
        "model": model,
        "runtime": _identifier(value.get("runtime"), 60) or None,
        "reasoning_effort": _enum(value.get("reasoning_effort"), EFFORTS),
        "covered_cost_usd": _usd(value.get("covered_cost")),
        "attributed_lines": _number(value.get("attributed_lines"), 2),
        "repositories": _count(value.get("projects")),
    }


def git_stats_projection(payload, range_key, view, cursor=None, limit=31, now=None):
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise MCPQueryError("evidence_unavailable", "Git delivery evidence is unavailable")
    coverage = payload.get("coverage") or {}
    measured = _count(coverage.get("measured_repositories")) or 0
    days = [row for row in (_git_day(item) for item in payload.get("days") or ()) if row]
    result = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "as_of": now,
        "data_scope": "aggregate_git_delivery",
        "range": range_key,
        "view": view,
        "window": {
            "start": days[0]["day"] if days else None,
            "end": days[-1]["day"] if days else None,
        },
        "available": measured > 0,
        "coverage": {
            "selected_repositories": _count(coverage.get("selected_repositories")),
            "measured_repositories": measured,
            "comparable_repositories": _count(coverage.get("comparable_repositories")),
            "available_spend_usd": _usd(coverage.get("available_spend")),
            "covered_spend_usd": _usd(coverage.get("covered_spend")),
            "spend_coverage": _number(coverage.get("spend_coverage"), 4),
            "last_checked_at": _count(coverage.get("last_checked")),
        },
        "caveat": GIT_CAVEAT,
    }
    if not result["available"]:
        result["reason"] = "no_measured_repositories"
    if view == "days":
        page = _page(days, cursor, limit, {"tool": "git_stats", "range": range_key, "view": view}, "day")
        result["days"] = page.pop("items")
        result["page"] = page
        return result
    comparison = payload.get("comparison") or {}
    model_coverage = payload.get("model_coverage") or {}
    result.update({
        "current": _git_totals(payload.get("overall")),
        "previous": _git_totals(payload.get("previous")),
        "change": {
            "changed_lines_pct": _number(comparison.get("code_pushed_pct"), 2),
            "spend_per_1k_lines_pct": _number(comparison.get("spend_per_1k_pct"), 2),
            "output_tokens_per_usd_pct": _number(comparison.get("output_per_dollar_pct"), 2),
            "lines_per_1k_output_tokens_pct": _number(comparison.get("delivery_yield_pct"), 2),
            "reasoning_share_of_output_pp": _number(comparison.get("reasoning_ratio_pp"), 2),
            "available": bool(comparison.get("available")),
        },
        "models": [row for row in (_git_model(item) for item in payload.get("model_rows") or ()) if row],
        "model_attribution": {
            "available": bool(model_coverage.get("available")),
            "estimate": True,
            "method": "project_spend_share",
            "comparable_lines": _count(model_coverage.get("comparable_lines")),
            "attributed_lines": _count(model_coverage.get("attributed_lines")),
            "unattributed_lines": _count(model_coverage.get("unattributed_lines")),
            "truncated": bool(model_coverage.get("truncated")),
        },
    })
    return result


def normalize_work_arguments(period=None, runtime=None, view=None, cursor=None, limit=None):
    period = "6" if period is None else str(period)
    if period not in WORK_PERIODS:
        raise MCPQueryError("invalid_argument", "period is unsupported")
    runtime = "" if runtime is None else str(runtime).strip()
    if len(runtime) > 40:
        raise MCPQueryError("invalid_argument", "runtime is too long")
    view = "summary" if view is None else str(view)
    if view not in WORK_VIEWS:
        raise MCPQueryError("invalid_argument", "view is unsupported")
    if cursor is not None and view != "trend":
        raise MCPQueryError("invalid_argument", "cursor applies only to view=trend")
    return period, runtime, view, normalize_limit(limit, 24, 100)


def work_status_projection(status):
    status = status if isinstance(status, dict) else {}
    return {
        "supported": bool(status.get("supported")),
        "enabled": bool(status.get("enabled")),
        "paused": bool(status.get("paused")),
        "labeled_sessions_total": _count(status.get("labels")),
        "pending_requests": _count(status.get("pending")),
    }


def work_unavailable(status, reason, period, runtime, view, now=None):
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "as_of": now,
        "data_scope": "aggregate_work_insights",
        "period": period,
        "runtime": runtime or None,
        "view": view,
        "available": False,
        "reason": reason,
        "status": work_status_projection(status),
        "caveat": {
            "unsupported": "Work insights are not supported on this platform.",
            "work_insights_off": "Work insights are off, so no work labels exist. Turn them on in Settings.",
            "classifier_not_run": "Work insights are on, but the classifier has not labeled any session yet.",
            "label_storage_unavailable": "Work insights are on, but their label storage cannot be read right now.",
        }[reason],
    }


def _area(value, areas):
    value = str(value or "")
    return value if value in areas and len(value) <= MAX_AREA_CHARS else None


def _sum_by(buckets, measure, areas, rounder):
    totals = {}
    for bucket in buckets:
        for key, amount in (bucket.get(measure) or {}).items():
            if key in areas:
                totals[key] = totals.get(key, 0) + (safe_number(amount) or 0)
    return {key: rounder(value) for key, value in totals.items()}


def _work_summary(insights, areas):
    buckets = [item for item in insights.get("allocation") or () if isinstance(item, dict)]
    turns = _sum_by(buckets, "turns", areas, int)
    sessions = _sum_by(buckets, "sessions", areas, int)
    spend = _sum_by(buckets, "spend", areas, _usd)
    guessed = _sum_by(buckets, "guess_spend", areas, _usd)
    outcomes, outcome_spend = {}, {}
    for bucket in buckets:
        for key, amount in (bucket.get("outcomes") or {}).items():
            if key in OUTCOMES:
                outcomes[key] = outcomes.get(key, 0) + (_count(amount) or 0)
        for key, amount in (bucket.get("outcome_spend") or {}).items():
            if key in OUTCOMES:
                outcome_spend[key] = outcome_spend.get(key, 0.0) + (safe_number(amount) or 0.0)
    breakdown = (insights.get("breakdown") or {}).get("overall") or {}
    rework = insights.get("rework") or {}
    return {
        "areas": [
            {
                "area": area,
                "requests": turns.get(area, 0),
                "sessions_started": sessions.get(area, 0),
                "spend_usd": spend.get(area, 0.0),
                "guessed_spend_usd": guessed.get(area, 0.0),
            }
            for area in areas if area in turns or area in sessions or area in spend
        ],
        "work_types": [
            {
                "work_type": work_type,
                "tasks": _count(row.get("tasks")),
                "spend_usd": _usd(row.get("spend")),
                "cost_per_task_usd": _usd(row.get("cost_per_task")),
                "judged_tasks": _count(row.get("judged_tasks")),
                "resolved_tasks": _count(row.get("resolved_tasks")),
                "resolved_rate": _number(row.get("resolved_rate"), 4),
                "cost_per_resolved_usd": _usd(row.get("cost_per_resolved")),
                "median_requests": _number(row.get("median_requests"), 2),
                "pushback": _rate(row.get("rework")),
            }
            for row in insights.get("economics") or ()
            if isinstance(row, dict) and (work_type := _enum(row.get("work_type"), WORK_TYPES))
        ],
        "outcomes": [
            {"outcome": key, "sessions": outcomes.get(key, 0), "spend_usd": _usd(outcome_spend.get(key, 0.0))}
            for key in OUTCOMES if key in outcomes
        ],
        "pushback": _rate(rework.get("overall")),
        "turns": {
            "human_turns": _count(breakdown.get("human_turns")),
            "agent_turns": _count(breakdown.get("agent_turns")),
            "agent_turns_per_human_turn": _number(breakdown.get("agent_per_human"), 3),
        },
        "tags": [
            {
                "tag": tag,
                "sessions": _count(row.get("sessions")),
                "spend_usd": _usd(row.get("spend")),
                "spend_share": _number(row.get("share"), 4),
                "judged_sessions": _count(row.get("judged_sessions")),
                "resolved_rate": _number(row.get("resolved_rate"), 4),
            }
            for row in (insights.get("tags") or {}).get("items") or ()
            if isinstance(row, dict) and (tag := _enum(row.get("tag"), TAGS))
        ],
    }


def _model_key(row):
    model = _identifier(row.get("model"), 120) if isinstance(row, dict) else ""
    return (model, _identifier(row.get("runtime"), 60) or None) if model else None


def _work_models(insights):
    pushback, scorecard, fit = [], [], []
    for row in (insights.get("rework") or {}).get("models") or ():
        key = _model_key(row)
        if key:
            pushback.append({
                "model": key[0], "runtime": key[1], "spend_usd": _usd(row.get("spend")),
                "pushback": _rate(row),
            })
    for row in insights.get("model_scorecard") or ():
        key = _model_key(row)
        if key:
            scorecard.append({
                "model": key[0], "runtime": key[1], "tier": _enum(row.get("tier"), TIERS),
                "tasks": _count(row.get("tasks")), "spend_usd": _usd(row.get("spend")),
                "cost_per_task_usd": _usd(row.get("cost_per_task")),
                "judged_tasks": _count(row.get("judged_tasks")),
                "resolved_rate": _number(row.get("resolved_rate"), 4),
                "cost_per_resolved_usd": _usd(row.get("cost_per_resolved")),
                "pushback": _rate(row.get("rework")),
                "lowest_pushback_for": [w for w in row.get("best_at") or () if w in WORK_TYPES],
                "highest_pushback_for": [w for w in row.get("worst_at") or () if w in WORK_TYPES],
            })
    for row in (insights.get("model_fit") or {}).get("cells") or ():
        key = _model_key(row)
        work_type = _enum(row.get("work_type"), WORK_TYPES) if key else None
        if work_type:
            fit.append({
                "work_type": work_type, "model": key[0], "runtime": key[1],
                "tasks": _count(row.get("tasks")), "sessions": _count(row.get("sessions")),
                "cost_per_task_usd": _usd(row.get("cost_per_task")),
                "pushback": _rate(row.get("rework")), "lowest_pushback": bool(row.get("best")),
            })
    return {"pushback_by_model": pushback, "scorecard": scorecard, "fit": fit}


def _work_right_sizing(insights):
    sizing = insights.get("right_sizing") or {}
    flagged = sizing.get("flagged") or {}
    tiers, efforts = [], []
    for row in sizing.get("cells") or ():
        complexity = _enum(row.get("complexity"), COMPLEXITIES) if isinstance(row, dict) else None
        tier = _enum(row.get("tier"), TIERS) if complexity else None
        if tier:
            tiers.append({
                "complexity": complexity, "tier": tier, "requests": _count(row.get("requests")),
                "sessions": _count(row.get("sessions")), "spend_usd": _usd(row.get("spend")),
                "pushback": _rate(row.get("rework")), "flag": _enum(row.get("flag"), FLAGS),
            })
    for row in (sizing.get("effort") or {}).get("cells") or ():
        complexity = _enum(row.get("complexity"), COMPLEXITIES) if isinstance(row, dict) else None
        effort = _enum(row.get("effort"), EFFORTS) if complexity else None
        if effort:
            efforts.append({
                "complexity": complexity, "effort": effort, "requests": _count(row.get("requests")),
                "sessions": _count(row.get("sessions")), "spend_usd": _usd(row.get("spend")),
                "pushback": _rate(row.get("rework")), "flag": _enum(row.get("flag"), FLAGS),
            })
    return {
        "tiers_known": bool(sizing.get("tiers_known")),
        "by_tier": tiers,
        "by_effort": efforts,
        "flagged": {
            "requests": _count(flagged.get("requests")),
            "sessions": _count(flagged.get("sessions")),
            "spend_usd": _usd(flagged.get("spend")),
            "labeled_spend_usd": _usd(flagged.get("labeled_spend")),
            "spend_share": _number(flagged.get("share"), 4),
        },
    }


def _work_trend(insights, areas):
    rows = []
    for bucket in insights.get("allocation") or ():
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("month") or "")
        if len(key) not in (7, 10):
            continue
        rows.append({
            "bucket": key,
            "partial": bool(bucket.get("partial")),
            "requests": _count(bucket.get("turns_total")),
            "sessions_started": _count(bucket.get("sessions_total")),
            "spend_usd": _usd(bucket.get("spend_total")),
            "spend_by_area_usd": {
                area: _usd(value) for area, value in (bucket.get("spend") or {}).items() if _area(area, areas)
            },
            "sessions_by_outcome": {
                key: _count(value) for key, value in (bucket.get("outcomes") or {}).items() if key in OUTCOMES
            },
        })
    return rows


def _named(models):
    out = []
    for item in models or ():
        key = _model_key(item)
        if key:
            out.append({"model": key[0], "runtime": key[1]})
    return out[:MAX_SUGGESTION_MODELS]


def _suggestion(row):
    """One Work page model suggestion; estimated savings can overlap between suggestions."""
    kind = _enum(row.get("kind"), SUGGESTION_KINDS) if isinstance(row, dict) else None
    if not kind:
        return None
    item = {"kind": kind, "spend_usd": _usd(row.get("spend")), "estimated_saving_usd": _usd(row.get("saving"))}
    if kind in ("premium_routine", "light_complex", "effort_routine"):
        item.update({
            "complexity": _enum(row.get("complexity"), COMPLEXITIES),
            "requests": _count(row.get("requests")),
            "sessions": _count(row.get("sessions")),
        })
        if kind == "effort_routine":
            item["effort"] = _enum(row.get("effort"), EFFORTS)
        else:
            item["tier"] = _enum(row.get("tier"), TIERS)
        if kind == "light_complex":
            item["pushback"] = _rate(row.get("rework"))
        if kind == "premium_routine":
            item["from_models"] = _named(row.get("from_models"))
            item["to_models"] = _named(row.get("to_models"))
        return item
    if kind == "long_threads":
        item.update({
            "threshold_turns": _count(row.get("threshold_turns")),
            "sessions": _count(row.get("sessions")),
            "cost_ratio_long_to_short": _number(row.get("ratio"), 3),
            "long_cost_per_turn_usd": _usd(row.get("long_cost_per_turn")),
            "short_cost_per_turn_usd": _usd(row.get("short_cost_per_turn")),
        })
        return item
    source = _model_key(row)
    target = _model_key({"model": row.get("to_model"), "runtime": row.get("to_runtime")})
    if not source or not target:
        return None
    item.update({
        "model": source[0], "runtime": source[1], "to_model": target[0], "to_runtime": target[1],
        "tasks": _count(row.get("tasks")),
        "from_resolved_rate": _number(row.get("from_rate"), 4),
        "to_resolved_rate": _number(row.get("to_rate"), 4),
    })
    if kind == "family_upgrade":
        item.update({
            "from_output_usd_per_mtok": _usd(row.get("from_price")),
            "to_output_usd_per_mtok": _usd(row.get("to_price")),
        })
        return item
    work_type = _enum(row.get("work_type"), WORK_TYPES)
    if not work_type:
        return None
    item.update({
        "work_type": work_type,
        "complexity": _enum(row.get("complexity"), COMPLEXITIES),
        "from_cost_per_resolved_usd": _usd(row.get("from_cost")),
        "to_cost_per_resolved_usd": _usd(row.get("to_cost")),
        "from_pushback_rate": _number(row.get("from_pushback"), 4),
        "to_pushback_rate": _number(row.get("to_pushback"), 4),
        "untested_on_harder_work": bool(row.get("to_untested_harder")),
    })
    return item


def _effort_change(model, verdict):
    if not isinstance(verdict, dict):
        return None
    kind = _enum(verdict.get("kind"), ("raise", "lower"))
    lower = _enum(verdict.get("lower"), EFFORTS)
    higher = _enum(verdict.get("higher"), EFFORTS)
    if not (kind and lower and higher):
        return None
    return {
        "kind": kind, "model": model[0], "runtime": model[1],
        "complexity": _enum(verdict.get("complexity"), COMPLEXITIES),
        "lower_effort": lower, "higher_effort": higher,
        "lower_pushback_rate": _number(verdict.get("lower_rate"), 4),
        "higher_pushback_rate": _number(verdict.get("higher_rate"), 4),
        "lower_samples": _count(verdict.get("lower_samples")),
        "higher_samples": _count(verdict.get("higher_samples")),
        "saving_per_request_usd": _usd(verdict.get("saving_per_request")),
        "estimated_saving_usd": _usd(verdict.get("saving")),
    }


def _work_suggestions(insights):
    effort = []
    for entry in (insights.get("effort_by_model") or {}).get("models") or ():
        key = _model_key(entry)
        if key:
            effort.extend(row for row in (_effort_change(key, v) for v in entry.get("verdicts") or ()) if row)
    return {
        "suggestions": [row for row in (_suggestion(item) for item in insights.get("recommendations") or ()) if row],
        "effort_changes": effort,
    }


def work_stats_projection(insights, status, period, runtime, view, cursor=None, limit=24, now=None):
    if not isinstance(insights, dict):
        raise MCPQueryError("evidence_unavailable", "Work evidence is unavailable")
    areas = [area for area in insights.get("areas") or () if isinstance(area, str) and len(area) <= MAX_AREA_CHARS]
    coverage = insights.get("coverage") or {}
    result = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "as_of": now,
        "data_scope": "aggregate_work_insights",
        "period": period,
        "runtime": runtime or None,
        "view": view,
        "grain": _enum(insights.get("grain"), ("day", "month")),
        "available": True,
        "status": work_status_projection(status),
        "coverage": {
            name: _count(coverage.get(name))
            for name in (
                "sessions", "labeled_sessions", "no_text_sessions", "outside_sessions",
                "turns", "labeled_turns", "requests", "labeled_requests",
            )
        },
        "caveat": WORK_CAVEAT,
    }
    if view == "summary":
        result.update(_work_summary(insights, areas))
    elif view == "models":
        result.update(_work_models(insights))
    elif view == "right_sizing":
        result["right_sizing"] = _work_right_sizing(insights)
    elif view == "suggestions":
        result.update(_work_suggestions(insights))
    else:
        query = {"tool": "work_stats", "period": period, "runtime": runtime, "view": view}
        page = _page(_work_trend(insights, areas), cursor, limit, query, "bucket")
        result["trend"] = page.pop("items")
        result["page"] = page
    return result
