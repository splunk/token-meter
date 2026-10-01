"""Runtime-neutral work insights: allocation, outcomes, work-type economics, rework, right-sizing.

Pure functions over cached session summary rows and content-free classifier labels.
"""

import collections
import datetime
import statistics

UNCLEAR = "Unclear"
PENDING = "Pending"
MIN_RATE_SAMPLES = 20
MAX_MONTHS = 24
COMPLEXITY_GROUPS = (
    ("routine", ("routine",)),
    ("everyday", ("everyday",)),
    ("complex", ("complex", "high_impact")),
)
TIERS = ("light", "standard", "premium")
WORK_TYPE_ORDER = ("debug", "feature", "refactor", "docs", "explore", "review", "ops", "other", "unclear")


def work_identity(row):
    """Per-trace identity: one session id can span several rollout files (resumes, forks)."""
    return f"{row.get('id') or ''}\0{row.get('path') or ''}"


def _trace_key(row):
    return str(row.get("session") or row.get("id") or "")


def is_child_row(row):
    """A row is a child run only when none of its agent records is a root (a parent keeps its children)."""
    records = [r for r in row.get("_agent_records") or () if isinstance(r, dict)]
    return bool(records) and all(r.get("parent_id") for r in records)


def turn_days(row):
    events = (row.get("_language_signal_events") or {}).get("positive") or []
    return [str(event.get("day") or "") for event in events]


def primary_model(row):
    stats = [s for s in row.get("model_stats") or [] if isinstance(s, dict) and s.get("model")]
    if stats:
        return max(stats, key=lambda s: (float(s.get("cost") or 0), int(s.get("tokens") or 0)))["model"]
    models = row.get("models") or []
    return models[0] if models else ""


def price_tiers(rows, output_price, with_prices=False):
    """Map (runtime, model) to light/standard/premium by terciles of catalog output price."""
    prices = {}
    for row in rows:
        model = primary_model(row)
        if not model:
            continue
        key = (row.get("runtime") or "", model)
        if key not in prices:
            price = output_price(model, row.get("provider") or "")
            if price and price > 0:
                prices[key] = float(price)
    distinct = sorted(set(prices.values()))
    if not distinct:
        return ({}, {}) if with_prices else {}
    if len(distinct) == 1:
        tiers = {key: "standard" for key in prices}
    else:
        def tier(price):
            position = distinct.index(price) / (len(distinct) - 1)
            return "light" if position < 1 / 3 else "premium" if position > 2 / 3 else "standard"

        tiers = {key: tier(price) for key, price in prices.items()}
    if not with_prices:
        return tiers
    by_tier = collections.defaultdict(list)
    for key, name in tiers.items():
        by_tier[name].append(prices[key])
    return tiers, {name: statistics.median(values) for name, values in by_tier.items()}


def _month(day):
    return day[:7] if len(day) >= 7 else ""


DAY_RANGES = (1, 7, 30)
MONTH_RANGES = (3, 6, 12, 0)


def parse_period(value):
    """``(grain, count)`` for a History choice: ``1d``/``7d``/``30d`` are days, 3/6/12/0 months; else None."""
    text = str(value if value is not None else "").strip()
    if text.endswith("d") and text[:-1].isdigit() and int(text[:-1]) in DAY_RANGES:
        return "day", int(text[:-1])
    if text.isdigit() and int(text) in MONTH_RANGES:
        return "month", int(text)
    return None


def _period(months):
    """Like ``parse_period`` but accepts any whole number of months (the app validates choices)."""
    if isinstance(months, int) and not isinstance(months, bool) and months >= 0:
        return "month", months
    return parse_period(months) or ("month", 6)


def _bucket(day, grain):
    """The period bucket a ``YYYY-MM-DD`` day falls in: its month, or the day itself."""
    day = str(day or "")
    if grain == "day":
        return day[:10] if len(day) >= 10 else ""
    return _month(day)


def _shift(key, delta, grain):
    if grain == "day":
        return (datetime.date.fromisoformat(key) + datetime.timedelta(days=delta)).isoformat()
    return _month_shift(key, delta)


def _rate(corrections, samples):
    if samples <= 0:
        return None
    return {"rate": corrections / samples, "samples": samples, "few_samples": samples < MIN_RATE_SAMPLES}


OUTCOMES = ("single_shot", "accepted", "recovered", "ended_on_pushback", "unclear", "pending")
MAX_FIT_MODELS = 5
MAX_TREND_MODELS = 6
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")
HIGH_EFFORTS = ("xhigh", "max", "ultra")


def session_outcome(turns, sequence, pending=False, labeled=False, follow_ups=None):
    """Classify a session from its ordered follow-up pushback labels.

    A session is classified only once it has no queued or backlogged work (``pending``);
    sessions whose labels are all low-confidence are Unclear rather than counted as accepted.
    A ``labeled`` session with no follow-up labels is a single shot only when the classifier
    found no classifiable follow-ups (``follow_ups`` 0: greetings, image- or wrapper-only
    turns). When it has follow-ups that were never labeled or the count is unknown, the
    outcome is Unclear. A session with no labels at all is still waiting for the classifier.
    """
    if turns <= 1:
        return "single_shot"
    if pending:
        return "pending"
    if not sequence:
        if not labeled:
            return "pending"
        return "single_shot" if follow_ups == 0 else "unclear"
    confident = [value for _ordinal, value in sequence if value is not None]
    if not confident:
        return "unclear"
    if not any(confident):
        return "accepted"
    return "ended_on_pushback" if confident[-1] else "recovered"


def _outcome(s):
    return session_outcome(s["turns"], s["sequence"], s["pending"], labeled=bool(s["entry"]),
                           follow_ups=s["entry"].get("follow_ups"))


def _month_shift(month, delta):
    year, number = divmod(int(month[:4]) * 12 + int(month[5:7]) - 1 + delta, 12)
    return f"{year:04d}-{number + 1:02d}"


def build_work_insights(rows, labels, key_for, areas, output_price, months=6,
                        runtime="", project="", today="", corrections_for=None, pending_keys=None):
    """Aggregate labeled sessions. ``months`` is a History choice (see ``parse_period``); 0 is all history."""
    grain, count = _period(months)
    area_names = [a["name"] for a in areas]
    tiers, tier_prices = price_tiers(rows, output_price, with_prices=True)
    sessions, runtime_options, project_options = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain)
    buckets = _window(sessions, grain, count, today)
    # The comparison period is the same number of calendar buckets just before the current window.
    previous = {_shift(buckets[0], -step, grain) for step in range(1, count + 1)} if count and buckets else set()
    segments = area_names + [UNCLEAR, PENDING]
    return _aggregate(sessions, buckets, set(buckets), segments, area_names, tiers, tier_prices,
                      runtime_options, project_options, today, corrections_for, previous, grain)


def _prepare_sessions(rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain="month"):
    sessions = []
    runtime_options, project_options = set(), collections.Counter()
    for row in rows:
        if is_child_row(row):
            continue
        if row.get("runtime"):
            runtime_options.add(row["runtime"])
        if row.get("project"):
            project_options[row["project"]] += 1
        if runtime and (row.get("runtime") or "") != runtime:
            continue
        if project and (row.get("project") or "") != project:
            continue
        entry = labels.get(key_for(work_identity(row))) or {}
        area = entry.get("area") or PENDING
        if area not in area_names and area not in (UNCLEAR, PENDING):
            area = PENDING
        days = [d for d in turn_days(row) if d]
        start_day = (row.get("start") or "")[:10]
        sessions.append({
            "row": row, "entry": entry, "area": area,
            "work_type": entry.get("work_type") or "",
            "complexity": entry.get("complexity") or "",
            "days": days, "start": _bucket(start_day or (days[0] if days else ""), grain),
            "turns": len(turn_days(row)),
            "corrections": int(entry.get("corrections") or 0),
            "correction_labels": int(entry.get("correction_labels") or 0),
            "tier": tiers.get((row.get("runtime") or "", primary_model(row))),
            "model": primary_model(row),
            "effort": str(row.get("reasoning_effort") or "").lower(),
            "sequence": [],
            "pending": bool(pending_keys) and key_for(work_identity(row)) in pending_keys,
        })
    return sessions, runtime_options, project_options


def _session_buckets(s, grain):
    return {b for b in [s["start"], *(_bucket(d, grain) for d in s["days"]),
                        *(_bucket(d, grain) for d in (s["row"].get("_day_cost") or {}))] if b}


def _window(sessions, grain, count, today=""):
    """The ``count`` calendar months or days ending today (or the latest data bucket without a today).

    Buckets without data stay in the window as empty columns. A ``count`` of 0 keeps every data month.
    """
    data = sorted(set().union(*(_session_buckets(s, grain) for s in sessions))) if sessions else []
    if not count:
        return data[-MAX_MONTHS:]
    end = _bucket(today or "", grain) or (data[-1] if data else "")
    if not end:
        return []
    count = min(count, MAX_MONTHS if grain == "month" else max(DAY_RANGES))
    return [_shift(end, step - count + 1, grain) for step in range(count)]


def _attach_sequences(sessions, corrections_for):
    if corrections_for is None:
        return
    for s in sessions:
        if s["turns"] > 1 and s["correction_labels"]:
            s["sequence"] = corrections_for(work_identity(s["row"]), s["turns"])


def _aggregate(sessions, all_months, month_set, segments, area_names, tiers, tier_prices,
               runtime_options, project_options, today, corrections_for, previous_months=frozenset(),
               grain="month"):
    allocation = []
    for month in all_months:
        bucket = {"month": month, "partial": bool(today and _bucket(today, grain) == month),
                  "turns": dict.fromkeys(segments, 0), "sessions": dict.fromkeys(segments, 0),
                  "spend": dict.fromkeys(segments, 0.0)}
        allocation.append(bucket)
    by_month = {b["month"]: b for b in allocation}
    for s in sessions:
        for day in s["days"]:
            bucket = by_month.get(_bucket(day, grain))
            if bucket:
                bucket["turns"][s["area"]] += 1
        if s["start"] in by_month:
            by_month[s["start"]]["sessions"][s["area"]] += 1
        for day, cost in (s["row"].get("_day_cost") or {}).items():
            bucket = by_month.get(_bucket(str(day), grain))
            if bucket:
                bucket["spend"][s["area"]] += float(cost or 0)
    for bucket in allocation:
        for measure in ("turns", "sessions", "spend"):
            values = bucket[measure]
            bucket[measure] = {k: (round(v, 6) if measure == "spend" else v) for k, v in values.items() if v}
            bucket[measure + "_total"] = round(sum(values.values()), 6) if measure == "spend" else sum(values.values())

    in_window = [s for s in sessions if s["start"] in month_set]
    _attach_sequences(in_window, corrections_for)
    for s in in_window:
        bucket = by_month.get(s["start"])
        if bucket is not None:
            outcome = _outcome(s)
            bucket.setdefault("outcomes", {}).setdefault(outcome, 0)
            bucket["outcomes"][outcome] += 1
            bucket.setdefault("outcome_spend", {}).setdefault(outcome, 0.0)
            bucket["outcome_spend"][outcome] = round(bucket["outcome_spend"][outcome] + _cost(s), 6)
    earlier = [s for s in sessions if s["start"] in previous_months]
    _attach_sequences(earlier, corrections_for)
    economics = []
    by_type = collections.defaultdict(list)
    for s in in_window:
        if s["work_type"]:
            by_type[s["work_type"]].append(s)
    for work_type in WORK_TYPE_ORDER:
        group = by_type.get(work_type)
        if not group:
            continue
        spend = sum(float(s["row"].get("cost") or 0) for s in group)
        outcomes_here = [_outcome(s) for s in group]
        judged = sum(o in ("accepted", "recovered", "ended_on_pushback") for o in outcomes_here)
        resolved = [s for s, o in zip(group, outcomes_here) if o in ("accepted", "recovered")]
        economics.append({
            "work_type": work_type, "sessions": len(group), "spend": round(spend, 6),
            "cost_per_session": spend / len(group),
            "judged_sessions": judged,
            "resolved_sessions": len(resolved),
            "resolved_rate": len(resolved) / judged if judged else None,
            "cost_per_resolved": (sum(_cost(s) for s in resolved) / len(resolved)) if resolved else None,
            "median_turns": statistics.median([s["turns"] for s in group]),
            "rework": _rate(sum(s["corrections"] for s in group), sum(s["correction_labels"] for s in group)),
        })

    weekly = collections.defaultdict(lambda: [0, 0])
    models = collections.defaultdict(lambda: [0, 0, 0.0])
    for s in in_window:
        if s["correction_labels"]:
            week = _trend_point(s["days"][0], grain) if s["days"] else ""
            if week:
                weekly[week][0] += s["corrections"]
                weekly[week][1] += s["correction_labels"]
            key = (s["model"], s["row"].get("runtime") or "")
            models[key][0] += s["corrections"]
            models[key][1] += s["correction_labels"]
            models[key][2] += float(s["row"].get("cost") or 0)
    top_models = sorted(models, key=lambda key: -models[key][1])[:MAX_TREND_MODELS]
    by_model = {key: collections.defaultdict(lambda: [0, 0]) for key in top_models}
    for s in in_window:
        key = (s["model"], s["row"].get("runtime") or "")
        if key in by_model and s["correction_labels"] and s["days"]:
            week = _trend_point(s["days"][0], grain)
            by_model[key][week][0] += s["corrections"]
            by_model[key][week][1] += s["correction_labels"]
    rework = {
        "grain": "day" if grain == "day" else "week",
        "weekly": [{"week": w, **_rate(c, n)} for w, (c, n) in sorted(weekly.items()) if n],
        "weekly_by_model": [{"model": m, "runtime": r,
                             "weeks": [{"week": w, **_rate(c, n)} for w, (c, n) in sorted(by_model[(m, r)].items()) if n]}
                            for m, r in top_models],
        "models": sorted(
            [{"model": m, "runtime": r, "spend": round(sp, 6), **_rate(c, n)}
             for (m, r), (c, n, sp) in models.items() if n],
            key=lambda item: -item["samples"])[:20],
        "overall": _rate(sum(s["corrections"] for s in in_window), sum(s["correction_labels"] for s in in_window)),
    }

    cells = []
    for group_name, members in COMPLEXITY_GROUPS:
        for tier in TIERS:
            group = [s for s in in_window if s["complexity"] in members and s["tier"] == tier]
            spend = sum(float(s["row"].get("cost") or 0) for s in group)
            cells.append({"complexity": group_name, "tier": tier, "sessions": len(group), "spend": round(spend, 6),
                          "rework": _rate(sum(s["corrections"] for s in group),
                                          sum(s["correction_labels"] for s in group))})
    rates = [c["rework"]["rate"] for c in cells if c["rework"] and not c["rework"]["few_samples"]]
    median_rate = statistics.median(rates) if rates else None
    for cell in cells:
        cell["flag"] = ""
        if cell["complexity"] == "routine" and cell["tier"] == "premium" and cell["spend"] > 0:
            cell["flag"] = "possible_overspend"
        elif (cell["complexity"] == "complex" and cell["tier"] == "light" and cell["rework"]
              and not cell["rework"]["few_samples"] and median_rate is not None
              and cell["rework"]["rate"] > median_rate):
            cell["flag"] = "possible_false_economy"

    effort = _effort(in_window)
    flagged = _flagged_spend(in_window, cells, effort, tiers)
    kpis = {"current": _kpis(in_window), "previous": _kpis(earlier) if earlier else None,
            "previous_months": sorted(previous_months)}
    model_fit = _model_fit(in_window)
    scorecard = _model_scorecard(in_window, tiers)
    opportunities = _opportunities(cells, effort, tier_prices)

    labeled_sessions = sum(1 for s in in_window if s["area"] != PENDING)
    labeled_turns = sum(s["correction_labels"] for s in in_window)
    later_turns = sum(max(0, s["turns"] - 1) for s in in_window)
    return {
        "grain": grain,
        "months": all_months,
        "areas": segments,
        "allocation": allocation,
        "economics": economics,
        "rework": rework,
        "right_sizing": {"cells": cells, "tiers_known": bool(tiers), "effort": effort, "flagged": flagged},
        "kpis": kpis,
        "model_fit": model_fit,
        "model_scorecard": scorecard,
        "opportunities": opportunities,
        "coverage": {
            "sessions": len(in_window), "labeled_sessions": labeled_sessions,
            "turns": later_turns, "labeled_turns": min(labeled_turns, later_turns),
        },
        "filters": {
            "runtimes": sorted(runtime_options),
            "projects": [name for name, _ in project_options.most_common(50)],
        },
    }


def _trend_point(day, grain):
    return day[:10] if grain == "day" and len(day) >= 10 else _week_start(day)


def _week_start(day):
    try:
        value = datetime.date.fromisoformat(day)
    except ValueError:
        return ""
    return (value - datetime.timedelta(days=value.weekday())).isoformat()


def _cost(s):
    return float(s["row"].get("cost") or 0)


def _kpis(group):
    """Period KPIs: resolved share, pushback rate, cost per resolved session, spend after first pushback."""
    counts = collections.Counter()
    spend = collections.Counter()
    for s in group:
        outcome = _outcome(s)
        counts[outcome] += 1
        spend[outcome] += _cost(s)
    judged = counts["accepted"] + counts["recovered"] + counts["ended_on_pushback"]
    resolved = counts["accepted"] + counts["recovered"]
    total = sum(spend.values())
    return {
        "sessions": len(group),
        "judged_sessions": judged,
        "resolved_rate": resolved / judged if judged else None,
        "pushback": _rate(sum(s["corrections"] for s in group), sum(s["correction_labels"] for s in group)),
        "cost_per_resolved": (spend["accepted"] + spend["recovered"]) / resolved if resolved else None,
        "ended_spend": round(spend["ended_on_pushback"], 6),
        "ended_share": spend["ended_on_pushback"] / total if total else None,
        "spend": round(total, 6),
        "few_samples": judged < MIN_RATE_SAMPLES,
    }


def _model_fit(in_window):
    labeled = [s for s in in_window if s["work_type"] and s["model"]]
    usage = collections.Counter((s["model"], s["row"].get("runtime") or "") for s in labeled)
    models = [key for key, _ in usage.most_common(MAX_FIT_MODELS)]
    work_types = [w for w in WORK_TYPE_ORDER if w != "unclear" and any(s["work_type"] == w for s in labeled)]
    cells = []
    for work_type in work_types:
        row_cells = []
        for model, runtime in models:
            group = [s for s in labeled if s["work_type"] == work_type
                     and (s["model"], s["row"].get("runtime") or "") == (model, runtime)]
            spend = sum(_cost(s) for s in group)
            cell = {"work_type": work_type, "model": model, "runtime": runtime, "sessions": len(group),
                    "cost_per_session": spend / len(group) if group else None,
                    "rework": _rate(sum(s["corrections"] for s in group), sum(s["correction_labels"] for s in group)),
                    "best": False}
            row_cells.append(cell)
        eligible = [c for c in row_cells if c["rework"] and not c["rework"]["few_samples"]]
        if len(eligible) >= 2:
            min(eligible, key=lambda c: (c["rework"]["rate"], c["cost_per_session"] or 0))["best"] = True
        cells.extend(row_cells)
    return {"work_types": work_types,
            "models": [{"model": m, "runtime": r, "sessions": usage[(m, r)]} for m, r in models],
            "cells": cells}


MAX_SCORECARD_MODELS = 8


def _model_scorecard(in_window, tiers):
    """Per model (scoped by runtime): volume, spend, pushback, resolved share, and cost per resolved session."""
    groups = collections.defaultdict(list)
    for s in in_window:
        if s["model"]:
            groups[(s["model"], s["row"].get("runtime") or "")].append(s)
    rows = []
    for (model, runtime), group in groups.items():
        outcomes = [_outcome(s) for s in group]
        judged = sum(o in ("accepted", "recovered", "ended_on_pushback") for o in outcomes)
        resolved = [s for s, o in zip(group, outcomes) if o in ("accepted", "recovered")]
        spend = sum(_cost(s) for s in group)
        rows.append({
            "model": model, "runtime": runtime, "tier": tiers.get((runtime, model)),
            "sessions": len(group), "spend": round(spend, 6),
            "cost_per_session": spend / len(group),
            "judged_sessions": judged,
            "resolved_rate": len(resolved) / judged if judged else None,
            "cost_per_resolved": sum(_cost(s) for s in resolved) / len(resolved) if resolved else None,
            "rework": _rate(sum(s["corrections"] for s in group), sum(s["correction_labels"] for s in group)),
        })
    rows.sort(key=lambda item: (-item["spend"], -item["sessions"], item["model"]))
    return rows[:MAX_SCORECARD_MODELS]


def _effort(in_window):
    """Complexity × reasoning effort: spend, sessions, pushback; high effort on routine work is flagged."""
    efforts = [e for e in EFFORT_ORDER if any(s["effort"] == e for s in in_window)]
    rows = []
    for group_name, members in COMPLEXITY_GROUPS:
        for effort in efforts:
            group = [s for s in in_window if s["complexity"] in members and s["effort"] == effort]
            spend = sum(_cost(s) for s in group)
            rows.append({"complexity": group_name, "effort": effort, "sessions": len(group),
                         "spend": round(spend, 6),
                         "rework": _rate(sum(s["corrections"] for s in group),
                                         sum(s["correction_labels"] for s in group)),
                         "flag": "possible_overthinking" if group_name == "routine" and effort in HIGH_EFFORTS
                         and spend > 0 else ""})
    return {"efforts": efforts, "cells": rows}


def _flagged_spend(in_window, cells, effort, tiers):
    """Spend on sessions in any mismatched cell, counting a session once even when tier and effort both flag it."""
    groups = dict(COMPLEXITY_GROUPS)
    flagged_tiers = {(c["complexity"], c["tier"]) for c in cells if c["flag"]}
    flagged_efforts = {(c["complexity"], c["effort"]) for c in (effort or {}).get("cells", []) if c["flag"]}
    complexity_of = {member: name for name, members in groups.items() for member in members}
    labeled = [s for s in in_window if s["complexity"] in complexity_of]
    hit = [s for s in labeled
           if (complexity_of[s["complexity"]], s["tier"]) in flagged_tiers
           or (complexity_of[s["complexity"]], s["effort"]) in flagged_efforts]
    spend, total = sum(_cost(s) for s in hit), sum(_cost(s) for s in labeled)
    return {"sessions": len(hit), "spend": round(spend, 6), "labeled_spend": round(total, 6),
            "share": spend / total if total else None}


def _opportunities(cells, effort, tier_prices):
    """Flagged right-sizing cells as one ranked list, with a savings estimate where prices allow."""
    rows = []
    ratio = (tier_prices["standard"] / tier_prices["premium"]
             if tier_prices.get("premium") and tier_prices.get("standard") else None)
    for cell in cells:
        if cell["flag"] == "possible_overspend":
            rows.append({"kind": "premium_routine", "complexity": cell["complexity"], "tier": cell["tier"],
                         "sessions": cell["sessions"], "spend": cell["spend"],
                         "estimate": round(cell["spend"] * ratio, 6) if ratio else None})
        elif cell["flag"] == "possible_false_economy":
            rows.append({"kind": "light_complex", "complexity": cell["complexity"], "tier": cell["tier"],
                         "sessions": cell["sessions"], "spend": cell["spend"], "rework": cell["rework"],
                         "estimate": None})
    for cell in (effort or {}).get("cells", []):
        if cell["flag"] == "possible_overthinking":
            rows.append({"kind": "effort_routine", "complexity": cell["complexity"], "effort": cell["effort"],
                         "sessions": cell["sessions"], "spend": cell["spend"], "estimate": None})
    return sorted(rows, key=lambda row: -row["spend"])


MAX_DRILL_SESSIONS = 50
MAX_DRILL_IDS = 2000
DRILL_FILTERS = ("month", "start_month", "area", "work_type", "complexity", "tier", "effort", "outcome",
                 "model", "model_runtime")


def find_sessions(rows, labels, key_for, areas, output_price, filters, months=6, runtime="", project="",
                  corrections_for=None, pending_keys=None, limit=MAX_DRILL_SESSIONS, ids_only=False, today=""):
    """Sessions behind one Work module cell, ranked by spend; same windowing and labels as the aggregates.

    ``month`` selects sessions active in that month (as the allocation counts turns and spend);
    ``start_month`` selects sessions started in that month (as outcomes and session counts do);
    every other filter applies to sessions started in the selected period.
    """
    area_names = [a["name"] for a in areas]
    tiers = price_tiers(rows, output_price)
    grain, count = _period(months)
    sessions, _runtimes, _projects = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain)
    all_months = _window(sessions, grain, count, today)
    month = filters.get("month") or ""
    start_month = filters.get("start_month") or ""
    if start_month:
        candidates = [s for s in sessions if start_month in all_months and s["start"] == start_month]
    elif month:
        candidates = [s for s in sessions if month in all_months and month in _session_buckets(s, grain)]
    else:
        window = set(all_months)
        candidates = [s for s in sessions if s["start"] in window]
    groups = dict(COMPLEXITY_GROUPS)
    matched = []
    for s in candidates:
        if filters.get("area") and s["area"] != filters["area"]:
            continue
        if filters.get("work_type") and (s["work_type"] or "") != filters["work_type"]:
            continue
        if filters.get("complexity") and s["complexity"] not in groups.get(filters["complexity"], ()):
            continue
        if filters.get("tier") and s["tier"] != filters["tier"]:
            continue
        if filters.get("effort") and s["effort"] != filters["effort"]:
            continue
        if filters.get("model") and (s["model"] != filters["model"]
                                     or (s["row"].get("runtime") or "") != filters.get("model_runtime", "")):
            continue
        matched.append(s)
    _attach_sequences(matched, corrections_for)
    if filters.get("outcome"):
        matched = [s for s in matched
                   if _outcome(s) == filters["outcome"]]
    matched.sort(key=lambda s: (-_cost(s), s["row"].get("last") or ""))
    if ids_only:
        return {"total": len(matched), "spend": round(sum(_cost(s) for s in matched), 6),
                "truncated": len(matched) > MAX_DRILL_IDS,
                "keys": list(dict.fromkeys(_trace_key(s["row"]) for s in matched))[:MAX_DRILL_IDS]}
    return {
        "total": len(matched),
        "spend": round(sum(_cost(s) for s in matched), 6),
        "truncated": len(matched) > limit,
        "sessions": [{
            "id": s["row"].get("id") or "",
            "session": _trace_key(s["row"]),
            "title": str(s["row"].get("session_name") or s["row"].get("title") or "")[:90],
            "runtime": s["row"].get("runtime") or "",
            "project": s["row"].get("project") or "",
            "start": s["row"].get("start") or "",
            "last": s["row"].get("last") or "",
            "cost": round(_cost(s), 6),
            "turns": s["turns"],
            "model": s["model"],
            "area": s["area"],
            "work_type": s["work_type"],
            "complexity": s["complexity"],
            "outcome": _outcome(s),
            "corrections": s["corrections"],
            "labeled_turns": s["correction_labels"],
        } for s in matched[:limit]],
    }
