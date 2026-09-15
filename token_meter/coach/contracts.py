"""Small, content-free contracts shared by the Coach transports."""

import math


GOAL_METRICS = {
    "cost_per_execution": {
        "direction": "down",
        "label": "Lower cost per execution",
        "unit": "USD/execution",
    },
    "output_per_dollar": {
        "direction": "up",
        "label": "Increase output per dollar",
        "unit": "tokens/USD",
    },
    "wait_per_execution": {
        "direction": "down",
        "label": "Lower wait per execution",
        "unit": "seconds/execution",
    },
    "retry_rate": {
        "direction": "down",
        "label": "Lower retry rate",
        "unit": "%",
    },
    "tool_tokens_per_execution": {
        "direction": "down",
        "label": "Lower tool output per execution",
        "unit": "tokens/execution",
    },
    "context_peak": {
        "direction": "down",
        "label": "Lower peak context",
        "unit": "tokens",
    },
}
GOAL_RUNTIMES = {
    "all", "claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes",
}
GOAL_WINDOWS = {7, 14, 30}
RECOMMENDATIONS = {
    "keep_course",
    "collect_more_data",
    "test_lower_cost_model",
    "reduce_retries",
    "reduce_tool_output",
    "reduce_wait",
    "reduce_context",
}


def _strict_integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer".format(name))
    if value < minimum or value > maximum:
        raise ValueError("{} must be from {} through {}".format(
            name, minimum, maximum,
        ))
    return value


def normalize_goal(value):
    """Return the complete allowlisted goal contract or raise ``ValueError``."""
    if not isinstance(value, dict):
        raise ValueError("goal must be an object")
    metric = str(value.get("metric") or "").strip().lower()
    if metric not in GOAL_METRICS:
        raise ValueError("goal metric is unsupported")
    target_percent = _strict_integer(
        value.get("target_percent"), "target_percent", 5, 80,
    )
    window_days = _strict_integer(
        value.get("window_days"), "window_days", 7, 30,
    )
    if window_days not in GOAL_WINDOWS:
        raise ValueError("window_days must be 7, 14, or 30")
    runtime = str(value.get("runtime") or "").strip().lower()
    if runtime not in GOAL_RUNTIMES:
        raise ValueError("goal runtime is unsupported")
    review_weekday = _strict_integer(
        value.get("review_weekday"), "review_weekday", 0, 6,
    )
    weekly_enabled = value.get("weekly_enabled")
    if not isinstance(weekly_enabled, bool):
        raise ValueError("weekly_enabled must be a boolean")
    definition = GOAL_METRICS[metric]
    return {
        "metric": metric,
        "direction": definition["direction"],
        "target_percent": target_percent,
        "window_days": window_days,
        "runtime": runtime,
        "review_weekday": review_weekday,
        "weekly_enabled": weekly_enabled,
        "label": definition["label"],
        "unit": definition["unit"],
    }


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coverage(stats, names):
    rows = stats.get("coverage") if isinstance(stats, dict) else {}
    rows = rows if isinstance(rows, dict) else {}
    normalized = []
    for name in names:
        row = rows.get(name) if isinstance(rows.get(name), dict) else {}
        normalized.append((
            max(0, int(row.get("covered") or 0)),
            max(0, int(row.get("unavailable") or 0)),
        ))
    covered = min((row[0] for row in normalized), default=0)
    unavailable = max((row[1] for row in normalized), default=0)
    if covered <= 0:
        return "unavailable", 0, unavailable
    return ("partial" if unavailable else "complete"), covered, unavailable


def _snapshot(metric, value, coverage, covered, unavailable, as_of):
    return {
        "metric": metric,
        "value": round(value, 6) if value is not None else None,
        "unit": GOAL_METRICS[metric]["unit"],
        "coverage": coverage,
        "covered": covered,
        "unavailable": unavailable,
        "as_of": as_of,
    }


def metric_snapshot(metric, execution_stats, tool_stats=None):
    """Calculate one goal metric without converting missing evidence to zero."""
    if metric not in GOAL_METRICS:
        raise ValueError("goal metric is unsupported")
    execution_stats = execution_stats if isinstance(execution_stats, dict) else {}
    totals = execution_stats.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    as_of = execution_stats.get("as_of")

    if metric == "context_peak":
        coverage, covered, unavailable = _coverage(execution_stats, ("context_peak",))
        value = _number(totals.get("context_peak"))
    elif metric == "cost_per_execution":
        coverage, covered, unavailable = _coverage(execution_stats, ("cost_usd",))
        numerator = _number(totals.get("cost_usd"))
        value = numerator / covered if numerator is not None and covered else None
    elif metric == "wait_per_execution":
        coverage, covered, unavailable = _coverage(execution_stats, ("wait_seconds",))
        numerator = _number(totals.get("wait_seconds"))
        value = numerator / covered if numerator is not None and covered else None
    elif metric == "retry_rate":
        coverage, covered, unavailable = _coverage(
            execution_stats, ("retries", "attempts"),
        )
        numerator = _number(totals.get("retries"))
        denominator = _number(totals.get("attempts"))
        value = (
            numerator * 100.0 / denominator
            if numerator is not None and denominator is not None and denominator > 0
            else None
        )
    elif metric == "output_per_dollar":
        coverage, covered, unavailable = _coverage(
            execution_stats, ("output_tokens", "cost_usd"),
        )
        numerator = _number(totals.get("output_tokens"))
        denominator = _number(totals.get("cost_usd"))
        value = (
            numerator / denominator
            if (
                coverage == "complete" and numerator is not None
                and denominator is not None and denominator > 0
            )
            else None
        )
    else:
        tool_stats = tool_stats if isinstance(tool_stats, dict) else {}
        tool_totals = tool_stats.get("totals")
        tool_totals = tool_totals if isinstance(tool_totals, dict) else {}
        tool_coverage, tool_covered, tool_unavailable = _coverage(
            tool_stats, ("tool_result_tokens",),
        )
        exec_coverage, exec_covered, exec_unavailable = _coverage(
            execution_stats, ("execution_count",),
        )
        covered = min(tool_covered, exec_covered)
        unavailable = max(tool_unavailable, exec_unavailable)
        coverage = "unavailable" if not covered else (
            "partial" if "partial" in (tool_coverage, exec_coverage) else "complete"
        )
        numerator = _number(tool_totals.get("tool_result_tokens"))
        denominator = _number(totals.get("execution_count"))
        value = (
            numerator / denominator
            if numerator is not None and denominator is not None and denominator > 0
            else None
        )
        as_of = max(
            value for value in (as_of, tool_stats.get("as_of"))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ) if any(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (as_of, tool_stats.get("as_of"))
        ) else as_of

    if value is None:
        coverage = "unavailable"
        covered = 0
    return _snapshot(metric, value, coverage, covered, unavailable, as_of)


def goal_progress(goal, baseline, current):
    """Return relative progress toward a goal, bounded to a percentage."""
    baseline = baseline if isinstance(baseline, dict) else {}
    current = current if isinstance(current, dict) else {}
    base_value = _number(baseline.get("value"))
    current_value = _number(current.get("value"))
    coverage = str(current.get("coverage") or "unavailable")
    if (
        base_value is None or base_value <= 0 or current_value is None
        or coverage not in {"complete", "partial"}
    ):
        return {
            "available": False,
            "progress_percent": None,
            "change_percent": None,
            "coverage": "unavailable",
        }
    change_percent = (current_value - base_value) * 100.0 / base_value
    target = _number(goal.get("target_percent"))
    direction = str(goal.get("direction") or "")
    if target is None or target <= 0 or direction not in {"up", "down"}:
        raise ValueError("goal progress contract is invalid")
    achieved = change_percent if direction == "up" else -change_percent
    return {
        "available": True,
        "progress_percent": round(max(0.0, min(100.0, achieved * 100.0 / target)), 1),
        "change_percent": round(change_percent, 1),
        "coverage": coverage,
    }
