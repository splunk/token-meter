"""Deterministic metric goals over local, content free session evidence."""

import datetime
import hashlib
import math
import re
import uuid

from token_meter.domain.aggregates import session_model_reasoning_efforts
from token_meter.models.tiers import model_tier


MAX_GOALS = 50
MAX_PERIOD_DAYS = 366
MIN_PUSHED_LINES = 50
EARLY_FRACTION = 0.2

METRICS = {
    "spend": {"label": "Spend", "unit": "usd", "scopes": ("all", "agent", "model")},
    "output_per_dollar": {"label": "Output / $", "unit": "tokens_per_usd", "scopes": ("all", "agent", "model"),
                          "direction": "at_least"},
    "cost_per_1k_lines": {"label": "Cost per 1K pushed lines", "unit": "usd_per_1k_lines", "scopes": ("all",),
                          "direction": "at_most"},
    "context_load": {"label": "Context load", "unit": "ratio", "scopes": ("all", "agent", "model")},
    "reasoning_ratio": {"label": "Reasoning ratio", "unit": "percent", "scopes": ("all", "agent", "model")},
    "frontier_share": {"label": "Frontier share", "unit": "percent", "scopes": ("all", "agent")},
}
PERCENT_METRICS = {"reasoning_ratio", "frontier_share"}

# (id, label, comparison, kind, change). Relative changes are percent of the
# baseline; point changes move a percentage metric by percentage points.
TARGET_OPTIONS = {
    "spend": (("cut_10", "Cut 10%", "at_most", "relative", -10),
              ("cut_20", "Cut 20%", "at_most", "relative", -20),
              ("hold", "Hold current level", "at_most", "relative", 0)),
    "output_per_dollar": (("improve_10", "Improve 10%", "at_least", "relative", 10),
                          ("improve_15", "Improve 15%", "at_least", "relative", 15),
                          ("improve_25", "Improve 25%", "at_least", "relative", 25)),
    "context_load": (("lower_10", "Lower 10%", "at_most", "relative", -10),
                     ("lower_20", "Lower 20%", "at_most", "relative", -20),
                     ("raise_10", "Raise 10%", "at_least", "relative", 10)),
    "reasoning_ratio": (("lower_5", "Lower 5 points", "at_most", "points", -5),
                        ("lower_10", "Lower 10 points", "at_most", "points", -10),
                        ("raise_5", "Raise 5 points", "at_least", "points", 5)),
}
TARGET_OPTIONS["cost_per_1k_lines"] = TARGET_OPTIONS["spend"]
TARGET_OPTIONS["frontier_share"] = TARGET_OPTIONS["reasoning_ratio"]

STARTERS = (
    ("Cut {agent} spend 10%", "spend", "codex", "cut_10"),
    ("Improve {agent} Output / $", "output_per_dollar", "claude", "improve_15"),
    ("Cut cost per 1K pushed lines 10%", "cost_per_1k_lines", None, "cut_10"),
)


def session_key(row):
    provider = str(row.get("provider") or "")
    identifier = str(row.get("id") or "")
    return f"{provider}:{hashlib.sha256((provider + ':' + identifier).encode()).hexdigest()}"


def model_key(runtime, model):
    """Opaque settings key for one runtime-scoped model; no model string is persisted."""
    return hashlib.sha256(f"{runtime}:{model}".encode()).hexdigest()


def model_label(model):
    """A bounded display name that never shows an account-bearing Bedrock ARN."""
    label = str(model or "")
    if label.lower().startswith("arn:aws"):
        label = label.split("/", 1)[1] if "/" in label else "Bedrock model"
    return "".join(char for char in label if ord(char) >= 32)[:120]


def model_inventory(rows):
    """Observed (runtime, model) pairs keyed by their opaque goal key."""
    inventory = {}
    for row in rows:
        runtime = row.get("provider")
        for daily in row.get("_model_daily") or ():
            model = str(daily.get("model") or "") if isinstance(daily, dict) else ""
            if runtime and model and not model.lower().startswith("unknown"):
                inventory.setdefault(model_key(runtime, model), {"runtime": runtime, "model": model})
    return inventory


def _text(value, label, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} is too long or contains control characters.")
    return value or None


def _date(value, label):
    try:
        if not isinstance(value, str) or datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValueError(f"{label} must be a YYYY-MM-DD date.") from None
    return datetime.date.fromisoformat(value)


def _number(value, label, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise ValueError(f"{label} is outside the allowed range.")
    return number


def _nonnegative(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _month_end(first):
    return (first + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


def period_window(period, today, start=None, end=None):
    """Return the inclusive local calendar window for a goal period."""
    if period == "week":
        first = today - datetime.timedelta(days=today.weekday())
        return first, first + datetime.timedelta(days=6)
    if period == "month":
        first = today.replace(day=1)
        return first, _month_end(first)
    if period != "custom":
        raise ValueError("Choose This week, This month, or Custom.")
    first, last = _date(start, "Start"), _date(end, "End")
    if first > last or (last - first).days >= MAX_PERIOD_DAYS:
        raise ValueError(f"Choose a period of 1 to {MAX_PERIOD_DAYS} days.")
    return first, last


def baseline_window(period, start, end):
    """The previous calendar week/month, or the preceding period of equal length."""
    if period == "month":
        last = start - datetime.timedelta(days=1)
        return last.replace(day=1), last
    length = end - start
    last = start - datetime.timedelta(days=1)
    return last - length, last


def _valid_period(period, start, end):
    if period == "week":
        return start.weekday() == 0 and end == start + datetime.timedelta(days=6)
    if period == "month":
        return start.day == 1 and end == _month_end(start)
    return start <= end and (end - start).days < MAX_PERIOD_DAYS


def normalize_goal(value, agents, existing=None):
    if not isinstance(value, dict):
        raise ValueError("A goal is required.")
    metric = value.get("metric")
    if metric not in METRICS:
        raise ValueError("Choose a supported metric.")
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    kind = scope.get("kind")
    if kind not in METRICS[metric]["scopes"]:
        raise ValueError("Pushed lines are measured across all projects; lines cannot be split by agent or model."
                         if metric == "cost_per_1k_lines" else
                         "Frontier share is always 0% or 100% for one model; choose all agents or one agent."
                         if metric == "frontier_share" and kind == "model" else "Choose a supported scope.")
    key = ""
    if kind == "agent":
        key = scope.get("key")
        if key not in agents:
            raise ValueError("Choose a supported agent.")
    elif kind == "model":
        key = scope.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Choose an observed model.")
    comparison = value.get("comparison")
    if comparison not in ("at_most", "at_least"):
        raise ValueError("Choose At most or At least.")
    if METRICS[metric].get("direction", comparison) != comparison:
        raise ValueError(f"{METRICS[metric]['label']} targets use "
                         f"{'At most' if METRICS[metric]['direction'] == 'at_most' else 'At least'}.")
    target = _number(value.get("target"), "Target", 100 if metric in PERCENT_METRICS else 1e9)
    period = value.get("period")
    if period not in ("week", "month", "custom"):
        raise ValueError("Choose This week, This month, or Custom.")
    start, end = _date(value.get("start"), "Start"), _date(value.get("end"), "End")
    if not _valid_period(period, start, end):
        raise ValueError("The dates do not match the selected period.")
    option = value.get("target_option") or "custom"
    if option != "custom" and option not in {row[0] for row in TARGET_OPTIONS[metric]}:
        raise ValueError("Choose a supported target option.")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "id": existing["id"] if existing else uuid.uuid4().hex,
        "type": "metric", "metric": metric, "scope": {"kind": kind, "key": key},
        "comparison": comparison, "target": target, "target_option": option,
        "period": period, "start": start.isoformat(), "end": end.isoformat(),
        "repeat": value.get("repeat") is True and period in ("week", "month"),
        "title": _text(value.get("title"), "Title", 100),
        "created_at": existing["created_at"] if existing else now,
        "ended_at": existing.get("ended_at") if existing else None,
    }


def normalize_store(raw, agents):
    """Never raise: unsupported or earlier prototype records are counted and dropped."""
    source = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), list) else []
    items, seen, dropped = [], set(), 0
    for item in source:
        try:
            if not isinstance(item, dict) or item.get("type") != "metric":
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{32}", str(item.get("id") or "")) or item["id"] in seen:
                raise ValueError
            clean = normalize_goal(item, agents, item)
            if clean["ended_at"] is not None:
                clean["ended_at"] = _date(clean["ended_at"], "Ended").isoformat()
            datetime.datetime.fromisoformat(str(clean["created_at"]))
        except (ValueError, TypeError, KeyError):
            dropped += 1
            continue
        if len(items) >= MAX_GOALS:
            dropped += 1
            continue
        items.append(clean)
        seen.add(clean["id"])
    return {"items": items, "dropped": dropped}


def apply_action(raw, action, agents, model_keys, today):
    store = normalize_store(raw, agents)
    if not isinstance(action, dict):
        raise ValueError("A goal action is required.")
    operation, goal_id = action.get("operation"), action.get("id")
    old = next((goal for goal in store["items"] if goal["id"] == goal_id), None)
    if operation == "save":
        if goal_id and old is None:
            raise ValueError("Goal was not found.")
        if old is None and len(store["items"]) >= MAX_GOALS:
            raise ValueError(f"Token Meter keeps at most {MAX_GOALS} goals.")
        record = normalize_goal(action.get("goal"), agents, old)
        if record["scope"]["kind"] == "model" and record["scope"]["key"] not in model_keys:
            raise ValueError("The selected model is unavailable.")
        if old and old.get("repeat") and record["repeat"] and old["period"] == record["period"]:
            # The browser edits the current period; keep the series anchor so history survives.
            record["start"], record["end"] = old["start"], old["end"]
        if old:
            store["items"][store["items"].index(old)] = record
        else:
            store["items"].append(record)
    elif operation in ("end", "delete"):
        if old is None:
            raise ValueError("Goal was not found.")
        if operation == "end":
            old["ended_at"] = old.get("ended_at") or today.isoformat()
        else:
            store["items"].remove(old)
    else:
        raise ValueError("Choose a supported goal action.")
    return {"items": store["items"]}


def _local_day(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _overlaps(row, start, end):
    first = _local_day(row.get("start") or row.get("last"))
    last = _local_day(row.get("last") or row.get("start"))
    if not first or not last:
        days = [str(day) for day in row.get("_day_cost") or {}]
        days += [str(d.get("day") or "") for d in row.get("_model_daily") or [] if isinstance(d, dict)]
        days = [day for day in days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
        if not days:
            return True  # Undated evidence may belong to this period.
        first, last = min(days), max(days)
    return first <= end and last >= start


def _in_scope(row, scope):
    kind = scope["kind"]
    return (kind == "all" or kind == "agent" and row.get("provider") == scope["key"]
            or kind == "model" and row.get("provider") == scope.get("runtime"))


def _active_count(rows, scope, start, end, active_keys):
    return sum(1 for row in rows if _in_scope(row, scope) and session_key(row) in active_keys
               and _overlaps(row, start, end))


_TOTALS = ("cost", "out", "out_cost", "inp", "io_out", "rsn", "rsn_out", "frontier", "unclassified",
           "tier_cost", "exec", "cost_exec", "io_exec", "rsn_exec", "rsn_scope_exec")


def _collect(scope, start, end, rows, metric_available):
    """One pass over scoped rows into per day totals that every metric can finish from."""
    days = {}
    flags = {"observations": 0, "cost_missing": 0, "undated_cost": 0, "undated_exec": 0,
             "estimated": False, "reasoning_excluded": 0}

    def bucket(day):
        return days.setdefault(day, dict.fromkeys(_TOTALS, 0.0))

    model = scope.get("model") if scope["kind"] == "model" else None
    for row in rows:
        if not _in_scope(row, scope):
            continue
        daily = [d for d in row.get("_model_daily") or [] if isinstance(d, dict)]
        stats = [stat for stat in row.get("model_stats") or [] if isinstance(stat, dict)]
        if model is not None:
            # A model goal reads only that model's dated executions and their own cost.
            daily = [d for d in daily if str(d.get("model") or "") == model]
            stats = [stat for stat in stats if str(stat.get("model") or "") == model]
            if not daily:
                continue
            day_costs = {}
            for d in daily:
                day_costs[str(d.get("day"))] = day_costs.get(str(d.get("day")), 0.0) + _nonnegative(d.get("cost"))
        else:
            day_costs = {str(day): _nonnegative(cost) for day, cost in (row.get("_day_cost") or {}).items()}
        in_days = [d for d in daily if start <= str(d.get("day") or "") <= end]
        in_costs = {day: cost for day, cost in day_costs.items() if start <= day <= end}
        if not (in_days or in_costs or model is None and _overlaps(row, start, end)):
            continue
        flags["observations"] += 1
        # Same rule as Spend provenance: locally priced cost is normal, estimated tokens are not.
        flags["estimated"] = flags["estimated"] or bool(row.get("token_estimate"))
        cost_ok = metric_available(row, "cost")
        tokens_ok = metric_available(row, "tokens")
        stats_executions = sum(_count(stat.get("executions")) for stat in stats)
        if stats_executions > sum(_count(d.get("executions")) for d in daily):
            flags["undated_exec"] += 1
        if cost_ok:
            for day, cost in in_costs.items():
                bucket(day)["cost"] += cost
            if model is None and _nonnegative(row.get("cost")) > sum(day_costs.values()) + 0.01:
                flags["undated_cost"] += 1
        elif in_days or in_costs:
            flags["cost_missing"] += 1
        # A runtime that never reports a reasoning split cannot make this metric incomplete.
        reports_reasoning = any(_count(d.get("reasoning_executions")) for d in daily)
        if in_days and not reports_reasoning:
            flags["reasoning_excluded"] += 1
        for d in in_days:
            t = bucket(str(d.get("day")))
            count = _count(d.get("executions"))
            cost = _nonnegative(d.get("cost"))
            t["exec"] += count
            if cost_ok:
                t["cost_exec"] += min(count, _count(d.get("cost_covered_executions", count)))
                t["out"] += _count(d.get("cost_covered_output_tokens", d.get("output_tokens")))
                t["out_cost"] += _nonnegative(d.get("cost_covered_cost", cost))
                tier = model_tier(d.get("model"))
                t["tier_cost"] += cost
                t["frontier"] += cost if tier == "frontier" else 0.0
                t["unclassified"] += cost if tier is None else 0.0
            io = min(count, _count(d.get("io_covered_executions", count if tokens_ok else 0)))
            if count and io == count:
                t["io_exec"] += count
                t["inp"] += _count(d.get("input_tokens"))
                t["io_out"] += _count(d.get("output_tokens"))
            if reports_reasoning:
                t["rsn_scope_exec"] += count
                t["rsn_exec"] += min(count, _count(d.get("reasoning_executions")))
                t["rsn_out"] += _count(d.get("reasoning_output_tokens"))
                t["rsn"] += min(_count(d.get("reasoning_tokens")), _count(d.get("reasoning_output_tokens")))
    return days, flags


def _sum(buckets):
    total = dict.fromkeys(_TOTALS, 0.0)
    for bucket in buckets:
        for key in _TOTALS:
            total[key] += bucket[key]
    return total


def _finish(metric, t, flags):
    observations = flags["observations"]
    if metric == "spend":
        missing = observations and flags["cost_missing"] >= observations
        value, num, den = (None if missing else t["cost"]), t["cost"], 1.0
        complete = not (flags["cost_missing"] or flags["undated_cost"])
        reason = ("Cost data incomplete" if flags["cost_missing"] else
                  "Some cost has no date" if flags["undated_cost"] else "")
    elif metric == "output_per_dollar":
        num, den = t["out"], t["out_cost"]
        value = num / den if den > 0 and t["cost_exec"] else None
        complete = not (flags["cost_missing"] or flags["undated_cost"] or flags["undated_exec"]) \
            and t["cost_exec"] >= t["exec"]
        reason = "" if complete else "Cost data incomplete"
    elif metric == "context_load":
        num, den = t["inp"], t["io_out"]
        value = num / den if den > 0 else None
        complete = not flags["undated_exec"] and t["io_exec"] >= t["exec"]
        reason = "" if complete else "Input and output data incomplete"
    elif metric == "reasoning_ratio":
        num, den = t["rsn"] * 100, t["rsn_out"]
        value = num / den if den > 0 else None
        complete = not flags["undated_exec"] and t["rsn_exec"] >= t["rsn_scope_exec"] > 0
        reason = ("No sessions in scope report reasoning tokens" if not t["rsn_scope_exec"] else
                  "" if complete else "Reasoning is not reported for some executions")
    else:
        num, den = t["frontier"] * 100, t["tier_cost"]
        value = num / den if den > 0 else None
        complete = not (flags["cost_missing"] or flags["undated_cost"])
        reason = "" if complete else "Cost data incomplete"
    if value is None and not reason:
        reason = "No data in this period yet"
    result = {"value": value, "numerator": num, "denominator": den, "complete": bool(complete),
              "estimated": flags["estimated"], "observations": observations, "reason": reason}
    if metric == "frontier_share" and den > 0:
        result["unclassified_share"] = t["unclassified"] * 100 / den
    if metric == "reasoning_ratio":
        result["reasoning_excluded"] = flags["reasoning_excluded"]
    return result


def _collection(scope, start, end, rows, metric_available, cache):
    key = (scope["kind"], scope["key"], start, end)
    if cache is None or key not in cache:
        collected = _collect(scope, start, end, rows, metric_available)
        if cache is None:
            return collected
        cache[key] = collected
    return cache[key]


def _git_measure(scope, start, end, git_query):
    data = git_query(scope, start, end) if git_query else {"ok": False}
    if not data.get("ok"):
        return {"value": None, "numerator": 0, "denominator": 0, "covered_cost": 0, "complete": False,
                "estimated": False, "observations": 0, "added": 0, "deleted": 0, "projects": 0,
                "project_names": [], "reason": "Git history unavailable"}
    comparable = [row for row in data.get("project_rows") or []
                  if row.get("availability", {}).get("cost") and row.get("availability", {}).get("code_pushed")]
    lines = sum(_count(row.get("changed_lines")) for row in comparable)
    cost = sum(_nonnegative(row.get("covered_cost")) for row in comparable)
    partial = any(row.get("availability", {}).get("partial") for row in comparable)
    stale = bool(data.get("stale"))
    value = 1000 * cost / lines if lines >= MIN_PUSHED_LINES and cost > 0 else None
    reason = ("Git history is stale" if stale else
              f"Fewer than {MIN_PUSHED_LINES} pushed lines" if lines < MIN_PUSHED_LINES else
              "No covered cost for pushed projects" if cost <= 0 else
              "Git or cost data incomplete" if partial else "")
    return {"value": value, "numerator": 1000 * cost, "denominator": lines, "covered_cost": cost,
            "complete": value is not None and not partial and not stale,
            "estimated": False, "observations": len(comparable),
            "added": sum(_count(row.get("added")) for row in comparable),
            "deleted": sum(_count(row.get("deleted")) for row in comparable),
            "projects": len(comparable),
            "project_names": [str(row.get("project") or "").split(" · ", 1)[0] for row in
                              sorted(comparable, key=lambda row: -_count(row.get("changed_lines")))][:8],
            "reason": reason}


def measure(metric, scope, start, end, rows, metric_available, git_query=None, cache=None):
    """Sum numerators and denominators over the whole period; gaps never become zero."""
    if metric == "cost_per_1k_lines":
        result = _git_measure(scope, start, end, git_query)
        # Show how much of all agent spend the repositories cover; the rest cannot count.
        days, _flags = _collection({"kind": "all", "key": ""}, start, end, rows, metric_available, cache)
        spend = _sum(days.values())["cost"]
        result["total_spend"] = spend
        result["counted_share"] = min(100.0, result["covered_cost"] / spend * 100) if spend > 0 else None
        return result
    days, flags = _collection(scope, start, end, rows, metric_available, cache)
    return _finish(metric, _sum(days.values()), flags)


def series(metric, scope, start, through, rows, metric_available, git_query=None, cache=None, points=40):
    """Value to date at evenly spaced days, for a compact trend line."""
    first, last = datetime.date.fromisoformat(start), datetime.date.fromisoformat(through)
    if last < first:
        return []
    span = (last - first).days + 1
    step = max(1, math.ceil(span / points))
    marks = {(first + datetime.timedelta(days=index)).isoformat() for index in range(0, span, step)}
    marks.add(through)
    result = []
    if metric == "cost_per_1k_lines":
        data = git_query(scope, start, through) if git_query else {}
        by_day = {row.get("day"): row for row in data.get("days") or []} if data.get("ok") else {}
        lines = cost = 0.0
        for index in range(span):
            day = (first + datetime.timedelta(days=index)).isoformat()
            row = by_day.get(day) or {}
            lines += _count(row.get("comparable_changed_lines"))
            cost += _nonnegative(row.get("covered_cost"))
            if day in marks:
                result.append({"day": day, "value": 1000 * cost / lines if lines >= MIN_PUSHED_LINES and cost > 0 else None})
        return result
    days, flags = _collection(scope, start, through, rows, metric_available, cache)
    running = dict.fromkeys(_TOTALS, 0.0)
    for index in range(span):
        day = (first + datetime.timedelta(days=index)).isoformat()
        for key in _TOTALS:
            running[key] += (days.get(day) or {}).get(key, 0.0)
        if day in marks:
            result.append({"day": day, "value": _finish(metric, running, flags)["value"]})
    return result


def target_options(metric, baseline):
    if baseline is None:
        return []
    options = []
    for option_id, label, comparison, kind, change in TARGET_OPTIONS[metric]:
        if kind == "relative":
            if baseline <= 0:
                continue
            target = baseline * (1 + change / 100)
        else:
            target = baseline + change
            if not 0 <= target <= 100:
                continue
        options.append({"id": option_id, "label": label, "comparison": comparison, "target": target})
    return options


FALLBACK_DAYS = 90


def baseline(metric, scope, period, start, end, rows, metric_available, git_query, today, cache=None):
    """The value targets start from: the previous period, else the last 90 days, else this period so far.

    Spend is scaled to the goal's period length, so the so far value becomes a projection."""
    name = {"week": "week", "month": "month"}.get(period, "period")
    first, last = baseline_window(period, start, end)
    yesterday = today - datetime.timedelta(days=1)
    candidates = (
        ("previous", f"Last {name}" if name != "period" else "Previous period", first, last),
        ("last_90", f"Last {FALLBACK_DAYS} days", today - datetime.timedelta(days=FALLBACK_DAYS), yesterday),
        ("so_far", f"So far this {name}", start, min(end, today)),
    )
    period_days = (end - start).days + 1
    fallback = None
    for source, label, window_start, window_end in candidates:
        if window_end < window_start or (source != "so_far" and window_end >= today):
            continue
        evidence = measure(metric, scope, window_start.isoformat(), window_end.isoformat(), rows,
                           metric_available, git_query, cache)
        value = evidence["value"]
        if value is not None and metric == "spend":
            value = value / ((window_end - window_start).days + 1) * period_days
        result = {"source": source, "label": label, "start": window_start.isoformat(),
                  "end": window_end.isoformat(), "value": value,
                  "complete": evidence["complete"], "estimated": evidence["estimated"],
                  "reason": "" if value is not None else evidence["reason"] or "No data"}
        if metric == "cost_per_1k_lines":
            result.update(projects=evidence["project_names"], project_count=evidence["projects"],
                          counted_share=evidence["counted_share"])
        fallback = fallback or result
        if value is not None and (value > 0 or metric in PERCENT_METRICS):
            return result
    return fallback or {"source": None, "label": "", "start": start.isoformat(), "end": end.isoformat(),
                        "value": None, "complete": False, "estimated": False, "reason": "Not started"}


def preview(request, agents, models, rows, metric_available, git_query, today):
    """Read only: baseline and target options for a draft goal."""
    metric = request.get("metric")
    if metric not in METRICS:
        raise ValueError("Choose a supported metric.")
    kind, key = request.get("scope_kind") or "all", request.get("scope_key") or ""
    if kind not in METRICS[metric]["scopes"] or (kind == "agent" and key not in agents) \
            or (kind == "model" and key not in models):
        raise ValueError("Choose a supported scope.")
    period = request.get("period")
    # A saved week or month keeps its own dates when edited later.
    anchor = _date(request["start"], "Start") if period in ("week", "month") and request.get("start") else today
    start, end = period_window(period, anchor, request.get("start"), request.get("end"))
    scope = {"kind": kind, "key": key if kind != "all" else "", **(models.get(key, {}) if kind == "model" else {})}
    prior = baseline(metric, scope, period, start, end, rows, metric_available, git_query, today)
    return {"ok": True, "start": start.isoformat(), "end": end.isoformat(), "baseline": prior,
            "options": target_options(metric, prior["value"])}


def goal_status(goal, evidence, today):
    """Return (status, reason). Status is on_track, at_risk, met, missed, no_result, or ended."""
    if goal.get("ended_at"):
        return "ended", "Ended early"
    start, end = datetime.date.fromisoformat(goal["start"]), datetime.date.fromisoformat(goal["end"])
    value, target, at_most = evidence["value"], goal["target"], goal["comparison"] == "at_most"
    if today < start:
        return "no_result", "Starts " + goal["start"]
    if goal["metric"] == "spend" and at_most and value is not None and value > target:
        return "missed", "Spend is above the target"
    if today <= end:
        if value is None:
            return "no_result", evidence["reason"] or "No data in this period yet"
        probe = evidence.get("projected", value)
        on_track = probe <= target if at_most else probe >= target
        return ("on_track" if on_track else "at_risk"), ""
    if evidence.get("active"):
        return "no_result", "Sessions are still running"
    if evidence["observations"] == 0:
        return "no_result", "No sessions in this period"
    if value is None or not evidence["complete"]:
        return "no_result", evidence["reason"] or "Data incomplete"
    if goal["metric"] == "frontier_share":
        # Unclassified spend could be frontier spend; claim a result only if it cannot flip it.
        high = value + evidence.get("unclassified_share", 0)
        if (high <= target) if at_most else (value >= target):
            return "met", ""
        if (value > target) if at_most else (high < target):
            return "missed", ""
        return "no_result", "Unclassified models could change the result"
    return ("met" if (value <= target if at_most else value >= target) else "missed"), ""


GUARDRAILS = {"spend": "output_per_dollar"}
MAX_HISTORY = 6


def _window(goal, today):
    """A repeating goal's current window rolls forward; its stored dates anchor the series."""
    start, end = datetime.date.fromisoformat(goal["start"]), datetime.date.fromisoformat(goal["end"])
    if goal.get("repeat") and not goal.get("ended_at") and today > end:
        start, end = period_window(goal["period"], today)
    return start, end


def _history(goal, start, rows, metric_available, git_query, today, cache):
    windows = []
    first = datetime.date.fromisoformat(goal["start"])
    while first < start and len(windows) < 60:
        window = period_window(goal["period"], first)
        windows.append(window)
        first = window[1] + datetime.timedelta(days=1)
    result = []
    for first, last in windows[-MAX_HISTORY:]:
        evidence = measure(goal["metric"], goal["scope"], first.isoformat(), last.isoformat(), rows,
                           metric_available, git_query, cache)
        status, _reason = goal_status({**goal, "start": first.isoformat(), "end": last.isoformat(),
                                       "ended_at": None}, evidence, today)
        result.append({"start": first.isoformat(), "end": last.isoformat(),
                       "value": evidence["value"], "status": status})
    return result


def _evaluate(goal, rows, metric_available, git_query, active_keys, today, cache, detail):
    start, end = _window(goal, today)
    through = min(end, today)
    scope = goal["scope"]
    current = {**goal, "start": start.isoformat(), "end": end.isoformat()}
    if through >= start:
        evidence = dict(measure(goal["metric"], scope, current["start"], through.isoformat(), rows,
                                metric_available, git_query, cache))
    else:
        evidence = {"value": None, "numerator": 0, "denominator": 0, "complete": False,
                    "estimated": False, "observations": 0, "reason": "Not started"}
    total_days = (end - start).days + 1
    elapsed = max(0, min(total_days, (today - start).days + 1))
    evidence["active"] = _active_count(rows, scope, current["start"], current["end"], active_keys)
    if goal["metric"] == "spend" and evidence["value"] is not None and 0 < elapsed < total_days:
        evidence["projected"] = evidence["value"] / elapsed * total_days
    evidence["confidence"] = ("estimated" if evidence["estimated"] else
                              "complete" if evidence["complete"] else "partial")
    status, reason = goal_status(current, evidence, today)
    item = {**current, "measurement": evidence, "status": status, "status_reason": reason,
            "series_start": goal["start"],
            "progress": {"elapsed_days": elapsed, "total_days": total_days,
                         "early": today <= end and elapsed < total_days * EARLY_FRACTION}}
    if not detail:
        return item
    item["baseline"] = baseline(goal["metric"], scope, goal["period"], start, end, rows,
                                metric_available, git_query, today, cache)
    if through >= start:
        item["series"] = series(goal["metric"], scope, current["start"], through.isoformat(), rows,
                                metric_available, git_query, cache)
        guard = GUARDRAILS.get(goal["metric"], "spend")
        guard_baseline = baseline(guard, scope, goal["period"], start, end, rows, metric_available,
                                  git_query, today, cache)
        item["guardrail"] = {
            "metric": guard,
            "value": measure(guard, scope, current["start"], through.isoformat(), rows,
                             metric_available, git_query, cache)["value"],
            "baseline": guard_baseline["value"], "baseline_label": guard_baseline["label"],
        }
    if goal.get("repeat"):
        item["history"] = _history(goal, start, rows, metric_available, git_query, today, cache)
    return item


def starters(rows, agents, metric_available, git_query, today, cache=None):
    """Pratik's three starters, falling back to the highest spend agent with a usable baseline."""
    cache = {} if cache is None else cache
    start, end = period_window("month", today)
    first, last = baseline_window("month", start, end)
    spend = {agent: measure("spend", {"kind": "agent", "key": agent}, first.isoformat(), last.isoformat(),
                            rows, metric_available, cache=cache)["value"] or 0 for agent in agents}
    ranked = sorted((agent for agent in agents if spend[agent] > 0), key=lambda agent: -spend[agent])
    result = []
    for template, metric, preferred, option_id in STARTERS:
        candidates = ([{"kind": "all", "key": ""}] if preferred is None else
                      [{"kind": "agent", "key": agent}
                       for agent in dict.fromkeys([preferred, *ranked]) if agent in agents])
        chosen, first_reason = None, ""
        for scope in candidates:
            prior = baseline(metric, scope, "month", start, end, rows, metric_available, git_query, today, cache)
            option = next((o for o in target_options(metric, prior["value"]) if o["id"] == option_id), None)
            if option:
                chosen = (scope, prior, option)
                break
            first_reason = first_reason or prior["reason"]
        scope = chosen[0] if chosen else (candidates[0] if candidates else {"kind": "all", "key": ""})
        title = template.format(agent=agents.get(scope["key"], "")) if scope["kind"] == "agent" else template
        result.append({
            "title": title, "metric": metric, "scope": scope, "period": "month",
            "start": start.isoformat(), "end": end.isoformat(), "enabled": chosen is not None,
            "target_option": option_id,
            "comparison": chosen[2]["comparison"] if chosen else None,
            "target": chosen[2]["target"] if chosen else None,
            "baseline": chosen[1] if chosen else None,
            "reason": f"Based on {chosen[1]['label'].lower()}" if chosen else f"Unavailable: {first_reason or 'no data'}",
        })
    return result


MODEL_CHOICES = 15
MODEL_SPEND_DAYS = 60


def _model_choices(rows, inventory, agents, goals, today):
    """The models a user mainly pays for: recent spend first, plus any a saved goal still uses."""
    since = (today - datetime.timedelta(days=MODEL_SPEND_DAYS - 1)).isoformat()
    spend = {}
    for row in rows:
        for daily in row.get("_model_daily") or ():
            if isinstance(daily, dict) and str(daily.get("day") or "") >= since:
                key = model_key(row.get("provider"), str(daily.get("model") or ""))
                spend[key] = spend.get(key, 0.0) + _nonnegative(daily.get("cost"))
    pinned = {goal["scope"]["key"] for goal in goals if goal["scope"]["kind"] == "model"}
    ranked = sorted((key for key in inventory if spend.get(key, 0) > 0 or key in pinned),
                    key=lambda key: (-spend.get(key, 0), inventory[key]["model"]))
    shown = ranked[:MODEL_CHOICES] + [key for key in ranked[MODEL_CHOICES:] if key in pinned]
    return [{"key": key, "runtime": inventory[key]["runtime"],
             "label": f"{agents.get(inventory[key]['runtime'], inventory[key]['runtime'])} · {model_label(inventory[key]['model'])}",
             "recent_spend": round(spend.get(key, 0), 2)} for key in shown]


def project(raw, rows, agents, metric_available, git_query, active_keys, budgets, today,
            detail=True, cache=None):
    """Measurements use complete scoped history, not a session preview. Detail adds charts and baselines."""
    store = normalize_store(raw, agents)
    rows = list(rows)
    cache = {} if cache is None else cache
    inventory = model_inventory(rows)
    allocations = {key: float(value) for key, value in ((budgets or {}).get("allocations") or {}).items()}
    goals = []
    for goal in store["items"]:
        scope = goal["scope"]
        if scope["kind"] == "model" and scope["key"] not in inventory:
            item = {**goal, "status": "no_result", "status_reason": "Model not seen in local history", "progress": {},
                    "series_start": goal["start"],
                    "measurement": {"value": None, "complete": False, "estimated": False, "observations": 0,
                                    "confidence": "partial", "active": 0, "reason": "Model not seen in local history"}}
        else:
            resolved = {**goal, "scope": {**scope, **inventory.get(scope["key"], {})}} if scope["kind"] == "model" else goal
            item = _evaluate(resolved, rows, metric_available, git_query, active_keys, today, cache, detail)
            item["scope"] = scope
        model = inventory.get(scope["key"]) if scope["kind"] == "model" else None
        item["scope_label"] = ("All projects" if goal["metric"] == "cost_per_1k_lines"
                               else "All agents" if scope["kind"] == "all"
                               else agents.get(scope["key"], scope["key"]) if scope["kind"] == "agent"
                               else f"{agents.get(model['runtime'], model['runtime'])} · {model_label(model['model'])}"
                               if model else "Model unavailable")
        if goal["metric"] == "spend" and goal["period"] == "month" and scope["kind"] in ("all", "agent"):
            reference = sum(allocations.values()) if scope["kind"] == "all" else allocations.get(scope["key"], 0)
            if reference > 0:
                item["budget_reference"] = reference
        goals.append(item)
    observed = sorted({row.get("provider") for row in rows if row.get("provider") in agents})
    return {"ok": True, "today": today.isoformat(), "goals": goals, "dropped": store["dropped"],
            "budget_allocations": allocations,
            "metrics": {key: {"label": value["label"], "unit": value["unit"], "scopes": list(value["scopes"]),
                              "direction": value.get("direction"),
                              "options": [{"id": o[0], "label": o[1], "comparison": o[2]}
                                          for o in TARGET_OPTIONS[key]]}
                        for key, value in METRICS.items()},
            "agents": [{"id": agent, "label": agents[agent]} for agent in observed],
            "models": _model_choices(rows, inventory, agents, store["items"], today),
            "model_spend_days": MODEL_SPEND_DAYS}


HIGH_EFFORTS = ("high", "xhigh", "ultra", "max")
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "ultra", "max")
MAX_DRIVERS = 8


def _scoped_daily(row, scope, start, end):
    daily = [d for d in row.get("_model_daily") or [] if isinstance(d, dict)
             and start <= str(d.get("day") or "") <= end]
    if scope["kind"] == "model":
        daily = [d for d in daily if str(d.get("model") or "") == scope.get("model")]
    return daily


def _model_names(row):
    return {str(d.get("model") or "") for d in row.get("_model_daily") or [] if isinstance(d, dict)}


def drivers(metric, scope, start, end, rows, metric_available, agents, git_query=None):
    """Ranked breakdowns of what produced a goal's value in its window."""
    if metric == "cost_per_1k_lines":
        data = git_query(scope, start, end) if git_query else {"ok": False}
        repos = []
        for row in data.get("project_rows") or [] if data.get("ok") else []:
            availability = row.get("availability") or {}
            if availability.get("cost") and availability.get("code_pushed"):
                lines, cost = _count(row.get("changed_lines")), _nonnegative(row.get("covered_cost"))
                repos.append({"label": str(row.get("project") or "").split(" · ", 1)[0], "lines": lines,
                              "cost": cost, "value": 1000 * cost / lines if lines else None})
        repos.sort(key=lambda repo: -repo["cost"])
        return {"by_repository": repos[:MAX_DRIVERS]}
    models, efforts, by_agent = {}, {}, {}
    cache_read = processed_input = 0
    for row in rows:
        if not _in_scope(row, scope) or not metric_available(row, "cost"):
            continue
        daily = _scoped_daily(row, scope, start, end)
        if not daily:
            continue
        runtime = row.get("provider")
        effort_by_model = session_model_reasoning_efforts(row, _model_names(row))
        for d in daily:
            name = str(d.get("model") or "unknown")
            cost, count = _nonnegative(d.get("cost")), _count(d.get("executions"))
            totals = models.setdefault((runtime, name), dict.fromkeys(
                ("cost", "exec", "out", "out_cost", "inp", "io_out", "rsn", "rsn_out"), 0.0))
            totals["cost"] += cost
            totals["exec"] += count
            totals["out"] += _count(d.get("cost_covered_output_tokens", d.get("output_tokens")))
            totals["out_cost"] += _nonnegative(d.get("cost_covered_cost", cost))
            if count and _count(d.get("io_covered_executions", count)) >= count:
                totals["inp"] += _count(d.get("input_tokens"))
                totals["io_out"] += _count(d.get("output_tokens"))
            if _count(d.get("reasoning_executions")):
                totals["rsn"] += min(_count(d.get("reasoning_tokens")), _count(d.get("reasoning_output_tokens")))
                totals["rsn_out"] += _count(d.get("reasoning_output_tokens"))
            effort = effort_by_model.get(name)
            if effort:
                bucket = efforts.setdefault((runtime, name, effort), {"cost": 0.0, "exec": 0})
                bucket["cost"] += cost
                bucket["exec"] += count
            by_agent[runtime] = by_agent.get(runtime, 0.0) + cost
            cache_read += _count(d.get("cache_read_tokens"))
            processed_input += _count(d.get("input_tokens"))
    total = sum(item["cost"] for item in models.values())

    def value(totals):
        if metric == "output_per_dollar":
            return totals["out"] / totals["out_cost"] if totals["out_cost"] else None
        if metric == "context_load":
            return totals["inp"] / totals["io_out"] if totals["io_out"] else None
        if metric == "reasoning_ratio":
            return totals["rsn"] * 100 / totals["rsn_out"] if totals["rsn_out"] else None
        return None

    by_model = [{"key": model_key(runtime, name), "runtime": runtime,
                 "label": f"{agents.get(runtime, runtime)} · {model_label(name)}", "model": model_label(name),
                 "tier": model_tier(name) or "unclassified", "cost": item["cost"],
                 "share": item["cost"] * 100 / total if total else 0.0, "executions": int(item["exec"]),
                 "cost_per_million_output": item["cost"] * 1e6 / item["out"] if item["out"] else None,
                 "value": value(item)}
                for (runtime, name), item in models.items()]
    by_model.sort(key=lambda item: -item["cost"])
    effort_totals = {}
    for (_runtime, _name, effort), item in efforts.items():
        effort_totals[effort] = effort_totals.get(effort, 0.0) + item["cost"]
    effort_total = sum(effort_totals.values())
    return {
        "total_cost": total,
        "by_model": by_model[:MAX_DRIVERS],
        "by_agent": sorted(({"label": agents.get(runtime, runtime), "cost": cost,
                             "share": cost * 100 / total if total else 0.0}
                            for runtime, cost in by_agent.items()), key=lambda item: -item["cost"])
        if scope["kind"] == "all" else [],
        "by_effort": [{"effort": effort, "cost": effort_totals[effort],
                       "share": effort_totals[effort] * 100 / effort_total if effort_total else 0.0}
                      for effort in EFFORT_ORDER if effort in effort_totals],
        "effort_by_model": [{"key": model_key(runtime, name), "model": model_label(name), "effort": effort,
                             "cost": item["cost"], "executions": item["exec"]}
                            for (runtime, name, effort), item in efforts.items()],
        "cache_hit": cache_read * 100 / processed_input if processed_input else None,
    }


def contributors(metric, scope, start, end, rows, metric_available, agents, limit=5):
    """The sessions that moved a goal's number most in its window; insight only, never scope."""
    items = []
    for row in rows:
        if not _in_scope(row, scope):
            continue
        daily = _scoped_daily(row, scope, start, end)
        if not daily:
            continue
        cost_known = metric_available(row, "cost")
        by_model = {}
        for d in daily:
            by_model[str(d.get("model") or "unknown")] = by_model.get(str(d.get("model") or "unknown"), 0.0) \
                + _nonnegative(d.get("cost"))
        top = max(by_model, key=by_model.get)
        cost = sum(by_model.values()) if cost_known else None
        input_tokens = sum(_count(d.get("input_tokens")) for d in daily)
        reasoning = sum(_count(d.get("reasoning_tokens")) for d in daily)
        weight = {"context_load": input_tokens, "reasoning_ratio": reasoning}.get(metric, cost or 0)
        if not weight:
            continue
        items.append({"id": str(row.get("id") or "")[:240],
                      "title": model_label(row.get("title") or row.get("session_name") or "")[:80] or "Untitled session",
                      "agent": agents.get(row.get("provider"), row.get("provider")), "model": model_label(top),
                      "effort": session_model_reasoning_efforts(row, _model_names(row)).get(top),
                      "last_day": max(str(d.get("day")) for d in daily),
                      "executions": sum(_count(d.get("executions")) for d in daily),
                      "cost": cost, "input_tokens": input_tokens, "reasoning_tokens": reasoning, "weight": weight})
    total = sum(item["weight"] for item in items)
    items.sort(key=lambda item: -item["weight"])
    for item in items:
        item["share"] = item["weight"] * 100 / total if total else 0.0
    return {"items": items[:limit], "sessions": len(items),
            "basis": {"context_load": "input tokens", "reasoning_ratio": "reasoning tokens"}.get(metric, "cost")}


def goal_detail(raw, goal_id, rows, agents, metric_available, git_query, active_keys, budgets, today):
    """Measurement for one goal's detail view: its card, a daily trend, drivers, and contributors."""
    rows = list(rows)
    data = project(raw, rows, agents, metric_available, git_query, active_keys, budgets, today)
    item = next((goal for goal in data["goals"] if goal["id"] == goal_id), None)
    if item is None:
        raise ValueError("Goal was not found.")
    scope = dict(item["scope"])
    if scope["kind"] == "model":
        scope.update(model_inventory(rows).get(scope["key"], {"runtime": None, "model": None}))
    through = min(item["end"], today.isoformat())
    window = (item["start"], through) if through >= item["start"] else None
    return {"ok": True, "today": today.isoformat(), "goal": item, "metrics": data["metrics"],
            "trend": series(item["metric"], scope, *window, rows, metric_available, git_query,
                            points=MAX_PERIOD_DAYS) if window else [],
            "drivers": drivers(item["metric"], scope, *window, rows, metric_available, agents, git_query)
            if window else {},
            "contributors": contributors(item["metric"], scope, *window, rows, metric_available, agents)
            if window else {"items": [], "sessions": 0, "basis": "cost"}}


def native_summary(data, today):
    """A bounded numeric projection with no user-authored text or titles."""
    open_goals = [goal for goal in data.get("goals") or []
                  if not goal.get("ended_at") and goal["end"] >= today.isoformat()]
    return {"ready": data.get("ok") is True, "additional_count": max(0, len(open_goals) - 3),
            "items": [{"id": goal["id"], "metric": goal["metric"], "scope_kind": goal["scope"]["kind"],
                       "agent": goal["scope"]["key"] if goal["scope"]["kind"] == "agent" else None,
                       "model": goal["scope_label"].split(" · ", 1)[-1] if goal["scope"]["kind"] == "model" else None,
                       "value": goal["measurement"].get("value"), "target": goal["target"],
                       "comparison": goal["comparison"], "status": goal["status"],
                       "confidence": goal["measurement"].get("confidence"), "end": goal["end"]}
                      for goal in open_goals[:3]]}
