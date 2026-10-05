"""Runtime-neutral work insights: allocation, outcomes, work-type economics, rework, right-sizing.

Pure functions over cached session summary rows and content-free classifier labels.
"""

import collections
import datetime
import re
import statistics

UNCLEAR = "Unclear"
PENDING = "Pending"
NO_TEXT = "No request text"  # the trace has no typed request the classifier could read
OUTSIDE = "Outside history"  # unlabeled and older than the labeling history setting
UNLABELED = (NO_TEXT, OUTSIDE, PENDING)
MIN_RATE_SAMPLES = 20
MAX_MONTHS = 24
COMPLEXITY_GROUPS = (
    ("routine", ("routine",)),
    ("everyday", ("everyday",)),
    ("complex", ("complex", "high_impact")),
)
TIERS = ("light", "standard", "premium")
COMPLEXITY_ORDER = ("routine", "everyday", "complex")
WORK_TYPE_ORDER = ("feature", "debug", "refactor", "test", "review", "plan", "explore", "ops", "docs", "other",
                   "unclear")


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
    return [str(day or "") for day in row.get("_work_turn_days") or ()]


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
        return ({}, {}, {}) if with_prices else {}
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
    return tiers, {name: statistics.median(values) for name, values in by_tier.items()}, prices


def _month(day):
    return day[:7] if len(day) >= 7 else ""


DAY_RANGES = (1, 7, 30)
MONTH_RANGES = (3, 6, 12, 0)
MONTH_TO_DATE = "month"


def parse_period(value):
    """``(grain, count)`` for a History choice: ``1d``/``7d``/``30d`` are days, 3/6/12/0 months; else None.

    ``month`` is this calendar month to date, by day; its count resolves against today.
    """
    text = str(value if value is not None else "").strip()
    if text == "month":
        return "day", MONTH_TO_DATE
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
                        runtime="", project="", today="", corrections_for=None, pending_keys=None,
                        label_since=""):
    """Aggregate labeled sessions. ``months`` is a History choice (see ``parse_period``); 0 is all history."""
    grain, count = _period(months)
    area_names = [a["name"] for a in areas]
    tiers, tier_prices, model_prices = price_tiers(rows, output_price, with_prices=True)
    sessions, runtime_options, project_options = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain, label_since)
    buckets = _window(sessions, grain, count, today)
    segments = area_names + [UNCLEAR, *UNLABELED]
    return _aggregate(sessions, buckets, set(buckets), segments, area_names, tiers, tier_prices,
                      runtime_options, project_options, today, corrections_for, grain, model_prices)


def _root_agent_id(row):
    for record in row.get("_agent_records") or ():
        if isinstance(record, dict) and not record.get("parent_id") and record.get("id"):
            return str(record["id"])
    return ""


def _child_counts(rows):
    """Child runs under each root agent, whether recorded in the parent trace or in their own files."""
    parent_of = {}
    for row in rows:
        for record in row.get("_agent_records") or ():
            if isinstance(record, dict) and record.get("id"):
                parent_of[str(record["id"])] = str(record.get("parent_id") or "")
    counts = collections.Counter()
    for agent, parent in parent_of.items():
        if not parent:
            continue
        node, seen = parent, {agent}
        while parent_of.get(node) and node not in seen:
            seen.add(node)
            node = parent_of[node]
        counts[node] += 1
    return counts


def _prepare_sessions(rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain="month",
                      label_since=""):
    sessions = []
    child_counts = _child_counts(rows)
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
        days = [d for d in turn_days(row) if d]
        start_day = (row.get("start") or "")[:10]
        area = entry.get("area") or PENDING
        if area not in area_names and area not in (UNCLEAR, PENDING):
            area = PENDING
        if area == PENDING:
            if not days:
                area = NO_TEXT
            elif label_since and max(days) < label_since:  # the classifier skips by newest activity
                area = OUTSIDE
        sessions.append({
            "row": row, "entry": entry, "area": area, "area_guess": bool(entry.get("area_guess")) and area in area_names,
            "work_type": entry.get("work_type") or "",
            "complexity": entry.get("complexity") or "",
            "days": days, "start": _bucket(start_day or (days[0] if days else ""), grain),
            "turns": len(turn_days(row)),
            "corrections": int(entry.get("corrections") or 0),
            "correction_labels": int(entry.get("correction_labels") or 0),
            "tier": tiers.get((row.get("runtime") or "", primary_model(row))),
            "model": primary_model(row),
            "effort": str(row.get("reasoning_effort") or "").lower(),
            "duration": int(row.get("duration_s") or 0) if row.get("duration_available") else 0,
            "children": child_counts.get(_root_agent_id(row), 0),
            "sequence": [],
            "pending": bool(pending_keys) and key_for(work_identity(row)) in pending_keys,
        })
    return sessions, runtime_options, project_options


def _day_costs(s):
    """Spend by day; rows without a daily split (some runtimes) count their cost on the start day."""
    day_cost = s["row"].get("_day_cost") or {}
    if day_cost or not _cost(s):
        return day_cost
    day = (s["row"].get("start") or "")[:10] or (s["days"][0] if s["days"] else "")
    return {day: _cost(s)} if day else {}


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
    if count == MONTH_TO_DATE:
        count = int(end[8:10]) if grain == "day" and len(end) == 10 else 1
    else:
        count = min(count, MAX_MONTHS if grain == "month" else max(DAY_RANGES))
    return [_shift(end, step - count + 1, grain) for step in range(count)]


def _attach_sequences(sessions, corrections_for):
    if corrections_for is None:
        return
    for s in sessions:
        if s["turns"] > 1 and s["correction_labels"]:
            s["sequence"] = corrections_for(work_identity(s["row"]), s["turns"])


def _aggregate(sessions, all_months, month_set, segments, area_names, tiers, tier_prices,
               runtime_options, project_options, today, corrections_for, grain="month", model_prices=None):
    allocation = []
    for month in all_months:
        bucket = {"month": month, "partial": bool(today and _bucket(today, grain) == month),
                  "turns": dict.fromkeys(segments, 0), "sessions": dict.fromkeys(segments, 0),
                  "spend": dict.fromkeys(segments, 0.0), "guess_spend": {}, "guess_sessions": {}}
        allocation.append(bucket)
    by_month = {b["month"]: b for b in allocation}
    for s in sessions:
        for day in s["days"]:
            bucket = by_month.get(_bucket(day, grain))
            if bucket:
                bucket["turns"][s["area"]] += 1
        if s["start"] in by_month:
            by_month[s["start"]]["sessions"][s["area"]] += 1
            if s["area_guess"]:
                guesses = by_month[s["start"]]["guess_sessions"]
                guesses[s["area"]] = guesses.get(s["area"], 0) + 1
        for day, cost in _day_costs(s).items():
            bucket = by_month.get(_bucket(str(day), grain))
            if bucket:
                bucket["spend"][s["area"]] += float(cost or 0)
                if s["area_guess"]:
                    bucket["guess_spend"][s["area"]] = round(bucket["guess_spend"].get(s["area"], 0.0) + float(cost or 0), 6)
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
    model_fit = _model_fit(in_window)
    scorecard = _model_scorecard(in_window, tiers)
    opportunities = _opportunities(cells, effort, tier_prices)
    tag_context = _tag_context(in_window)
    rhythm = _rhythm(in_window)

    labeled_sessions = sum(1 for s in in_window if s["area"] not in UNLABELED)
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
        "model_fit": model_fit,
        "model_scorecard": scorecard,
        "opportunities": opportunities,
        "tags": _tag_summary(in_window, tag_context),
        "rhythm": rhythm,
        "recommendations": _recommendations(in_window, opportunities, tiers, model_prices or {}),
        "coverage": {
            "sessions": len(in_window), "labeled_sessions": labeled_sessions,
            "no_text_sessions": sum(1 for s in in_window if s["area"] == NO_TEXT),
            "outside_sessions": sum(1 for s in in_window if s["area"] == OUTSIDE),
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


TAG_ORDER = ("marathon", "long_thread", "big_spend", "team", "overkill", "underpowered", "rescued", "stuck",
             "one_shot")
LONG_THREAD_TURNS = 30
MIN_TAG_POPULATION = 10
MARATHON_FLOOR_S = 3_600
TEAM_MIN_CHILDREN = 3


def _top_decile_floor(values):
    """Value the top 10% must exceed; None below the minimum population."""
    if len(values) < MIN_TAG_POPULATION:
        return None
    ordered = sorted(values)
    return ordered[int(0.9 * len(ordered)) - 1]


def _tag_context(in_window):
    """Thresholds for relative tags, computed over the sessions started in the selected period."""
    costs = [_cost(s) for s in in_window if _cost(s) > 0]
    durations = [s["duration"] for s in in_window if s["duration"]]
    return {"marathon_s": max(MARATHON_FLOOR_S, _top_decile_floor(durations) or 0),
            "long_run": _top_decile_floor(durations),
            "big_spend": _top_decile_floor(costs),
            "team_children": TEAM_MIN_CHILDREN}


def session_tags(s, context, outcome):
    tags = []
    long_run = context["long_run"]
    if s["duration"] >= MARATHON_FLOOR_S and (long_run is None or s["duration"] > long_run):
        tags.append("marathon")
    if s["turns"] >= LONG_THREAD_TURNS:
        tags.append("long_thread")
    if context["big_spend"] is not None and _cost(s) > context["big_spend"] > 0:
        tags.append("big_spend")
    if s["children"] >= context["team_children"]:
        tags.append("team")
    if s["complexity"] == "routine" and (s["tier"] == "premium" or s["effort"] in HIGH_EFFORTS):
        tags.append("overkill")
    if s["complexity"] in ("complex", "high_impact") and s["tier"] == "light" and s["corrections"] > 0:
        tags.append("underpowered")
    tags.extend({"recovered": ["rescued"], "ended_on_pushback": ["stuck"], "single_shot": ["one_shot"]}.get(outcome, []))
    return tags


def _tag_summary(in_window, context):
    stats = {tag: {"sessions": 0, "spend": 0.0, "judged": 0, "resolved": 0} for tag in TAG_ORDER}
    total = sum(_cost(s) for s in in_window)
    for s in in_window:
        outcome = _outcome(s)
        for tag in session_tags(s, context, outcome):
            item = stats[tag]
            item["sessions"] += 1
            item["spend"] += _cost(s)
            item["judged"] += outcome in ("accepted", "recovered", "ended_on_pushback")
            item["resolved"] += outcome in ("accepted", "recovered")
    return {
        "items": [{"tag": tag, "sessions": v["sessions"], "spend": round(v["spend"], 6),
                   "share": v["spend"] / total if total else None,
                   "resolved_rate": v["resolved"] / v["judged"] if v["judged"] else None,
                   "judged_sessions": v["judged"]}
                  for tag, v in stats.items() if v["sessions"]],
        "thresholds": context,
    }


DAY_BANDS = (("night", 0, 6), ("morning", 6, 12), ("afternoon", 12, 18), ("evening", 18, 24))


def _start_time(s):
    try:
        return datetime.datetime.strptime(str(s["row"].get("start") or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _rhythm(in_window):
    """Session starts by local weekday and hour, and pushback by time of day."""
    cells = collections.defaultdict(lambda: [0, 0.0])
    bands = {name: [0, 0.0, 0, 0] for name, _lo, _hi in DAY_BANDS}
    for s in in_window:
        started = _start_time(s)
        if started is None:
            continue
        cell = cells[(started.weekday(), started.hour)]
        cell[0] += 1
        cell[1] += _cost(s)
        band = next(name for name, lo, hi in DAY_BANDS if lo <= started.hour < hi)
        bands[band][0] += 1
        bands[band][1] += _cost(s)
        bands[band][2] += s["corrections"]
        bands[band][3] += s["correction_labels"]
    return {
        "sessions": sum(v[0] for v in cells.values()),
        "cells": [{"weekday": d, "hour": h, "sessions": n, "spend": round(sp, 6)}
                  for (d, h), (n, sp) in sorted(cells.items())],
        "bands": [{"band": name, "sessions": n, "spend": round(sp, 6), "pushback": _rate(c, labeled)}
                  for name, (n, sp, c, labeled) in bands.items()],
    }


MIN_SWITCH_JUDGED = 5
MIN_BASELINE_JUDGED = 10
MIN_FAMILY_JUDGED = 5
MAX_NAMED_MODELS = 3
SWITCH_COST_RATIO = 0.7
SWITCH_RATE_SLACK = 0.05
SHORT_THREAD_TURNS = 10
MIN_THREAD_SESSIONS = 5
LONG_THREAD_RATIO = 1.5
MAX_RECOMMENDATIONS = 8
MIN_RECOMMENDATION_SESSIONS = 3


def _pushback_not_worse(candidate, current):
    """Pushback within five points of the current model's, when both have labeled follow-ups."""
    if not candidate or not current:
        return candidate is None or current is None
    return candidate["rate"] <= current["rate"] + SWITCH_RATE_SLACK


def _switch_recommendations(in_window, tiers, prices):
    """Per kind of work and complexity: a model that resolves about as often for much less per resolved session.

    Comparing within one complexity level keeps a cheaper model that only saw easier requests from looking better.
    """
    complexity_of = {member: name for name, members in COMPLEXITY_GROUPS for member in members}
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for s in in_window:
        level = complexity_of.get(s["complexity"])
        if s["work_type"] and s["work_type"] != "unclear" and s["model"] and level:
            groups[(s["work_type"], level)][(s["model"], s["row"].get("runtime") or "")].append(s)
    out = []
    for work_type, level in ((w, c) for w in WORK_TYPE_ORDER for c, _members in COMPLEXITY_GROUPS):
        stats = []
        for (model, runtime), group in groups.get((work_type, level), {}).items():
            outcomes = [_outcome(s) for s in group]
            judged = sum(o in ("accepted", "recovered", "ended_on_pushback") for o in outcomes)
            resolved = [s for s, o in zip(group, outcomes) if o in ("accepted", "recovered")]
            if judged < MIN_SWITCH_JUDGED or not resolved:
                continue
            stats.append({"model": model, "runtime": runtime, "sessions": len(group), "judged": judged,
                          "pushback": _rate(sum(s["corrections"] for s in group),
                                            sum(s["correction_labels"] for s in group)),
                          "spend": sum(_cost(s) for s in group), "rate": len(resolved) / judged,
                          "resolved": len(resolved), "cost": sum(_cost(s) for s in resolved) / len(resolved)})
        if len(stats) < 2:
            continue
        usual = max(stats, key=lambda x: (x["sessions"], x["spend"]))
        if usual["judged"] < MIN_BASELINE_JUDGED:
            continue
        usual_price = prices.get((usual["runtime"], usual["model"]))

        def eligible(x):
            price = prices.get((x["runtime"], x["model"]))
            # A per-token cheaper model is required, so easier sessions alone cannot make a model look cheaper.
            return (x is not usual and price is not None and usual_price is not None and price < usual_price
                    and not (level == "routine" and tiers.get((x["runtime"], x["model"])) == "premium")
                    and x["rate"] >= usual["rate"] - SWITCH_RATE_SLACK
                    and _pushback_not_worse(x["pushback"], usual["pushback"])
                    and x["cost"] <= usual["cost"] * SWITCH_COST_RATIO)
        better = [x for x in stats if eligible(x)]
        if not better:
            continue
        alt = min(better, key=lambda x: x["cost"])
        harder = [c for c, _members in COMPLEXITY_GROUPS
                  if COMPLEXITY_ORDER.index(c) > COMPLEXITY_ORDER.index(level)]
        tried_harder = any(s["model"] == alt["model"] and (s["row"].get("runtime") or "") == alt["runtime"]
                           and complexity_of.get(s["complexity"]) in harder for s in in_window)
        out.append({"kind": "switch_model", "work_type": work_type, "complexity": level,
                    "to_untested_harder": bool(harder) and not tried_harder,
                    "from_pushback": usual["pushback"]["rate"] if usual["pushback"] else None,
                    "to_pushback": alt["pushback"]["rate"] if alt["pushback"] else None,
                    "model": usual["model"], "runtime": usual["runtime"],
                    "to_model": alt["model"], "to_runtime": alt["runtime"],
                    "from_cost": usual["cost"], "to_cost": alt["cost"],
                    "from_rate": usual["rate"], "to_rate": alt["rate"],
                    "sessions": usual["sessions"], "spend": round(usual["spend"], 6),
                    "saving": round((usual["cost"] - alt["cost"]) * usual["resolved"], 6)})
    return out


def _long_thread_recommendation(in_window):
    """Long sessions re-send a growing context; compare their cost per request with short sessions."""
    priced = [s for s in in_window if _cost(s) > 0 and s["turns"] > 0]
    long = [s for s in priced if s["turns"] >= LONG_THREAD_TURNS]
    short = [s for s in priced if s["turns"] <= SHORT_THREAD_TURNS]
    tagged = sum(1 for s in in_window if s["turns"] >= LONG_THREAD_TURNS)
    if len(long) < MIN_THREAD_SESSIONS or len(short) < MIN_THREAD_SESSIONS:
        return None
    long_turns, short_turns = sum(s["turns"] for s in long), sum(s["turns"] for s in short)
    long_rate = sum(_cost(s) for s in long) / long_turns
    short_rate = sum(_cost(s) for s in short) / short_turns
    if short_rate <= 0 or long_rate < LONG_THREAD_RATIO * short_rate:
        return None
    spend = sum(_cost(s) for s in long)
    return {"kind": "long_threads", "threshold_turns": LONG_THREAD_TURNS, "sessions": tagged,
            "spend": round(spend, 6), "ratio": long_rate / short_rate,
            "long_cost_per_turn": long_rate, "short_cost_per_turn": short_rate,
            "saving": round(max(0.0, spend - long_turns * short_rate), 6)}


def _model_name_parts(name):
    text = str(name or "").lower().rsplit("/", 1)[-1]
    text = re.sub(r"\[[^\]]*\]$", "", text)  # context-size markers such as [1m]
    text = re.sub(r"@[\w.-]*$", "", text)  # Vertex-style release suffixes such as @20250101
    while re.match(r"^[a-z]+\.(?=[a-z])", text):  # vendor or region prefixes such as us.anthropic.
        text = re.sub(r"^[a-z]+\.", "", text, count=1)
    text = re.sub(r"-v\d+(?::\d+)?$", "", text)
    text = re.sub(r"-\d{8}$", "", text)  # release date stamps
    parts = [part for part in re.split(r"[-_]", text) if part]
    numbers = [part for part in parts if re.fullmatch(r"\d+(?:\.\d+)*", part)]
    family = "-".join(part for part in parts if part not in numbers)
    version = tuple(int(piece) for part in numbers for piece in part.split("."))
    return family, version


def model_family(name):
    """Model name without version numbers or vendor prefixes: claude-opus-4-8 and claude-opus-5-5 share one family."""
    return _model_name_parts(name)[0]


def model_version(name):
    """Version numbers in a model name as a tuple, e.g. (4, 8) for claude-opus-4-8; empty when there are none."""
    return _model_name_parts(name)[1]


def _model_outcomes(group):
    outcomes = [_outcome(s) for s in group]
    judged = sum(o in ("accepted", "recovered", "ended_on_pushback") for o in outcomes)
    resolved = sum(o in ("accepted", "recovered") for o in outcomes)
    return judged, (resolved / judged if judged else None)


def _family_recommendations(in_window, prices):
    """A newer, cheaper version from the same model family and app that resolves about as often for this user."""
    groups = collections.defaultdict(list)
    for s in in_window:
        if s["model"]:
            groups[(s["row"].get("runtime") or "", s["model"])].append(s)
    stats = {}
    for key, group in groups.items():
        judged, rate = _model_outcomes(group)
        if key in prices and judged >= MIN_FAMILY_JUDGED:
            stats[key] = {"judged": judged, "rate": rate, "sessions": len(group),
                          "spend": sum(_cost(s) for s in group), "price": prices[key]}
    out = []
    for (runtime, model), current in stats.items():
        version = model_version(model)
        if current["judged"] < MIN_BASELINE_JUDGED or not version:
            continue
        siblings = [(key, other) for key, other in stats.items()
                    if key[0] == runtime and key[1] != model and model_family(key[1]) == model_family(model)
                    and model_version(key[1]) > version
                    and other["price"] < current["price"] and other["rate"] >= current["rate"] - SWITCH_RATE_SLACK]
        if not siblings or current["spend"] <= 0:
            continue
        (_runtime, to_model), alt = min(siblings, key=lambda item: (item[1]["price"], -item[1]["rate"]))
        out.append({"kind": "family_upgrade", "model": model, "runtime": runtime, "to_model": to_model,
                    "to_runtime": runtime, "from_price": current["price"], "to_price": alt["price"],
                    "from_rate": current["rate"], "to_rate": alt["rate"], "sessions": current["sessions"],
                    "spend": round(current["spend"], 6),
                    "saving": round(current["spend"] * (1 - alt["price"] / current["price"]), 6)})
    return out


def _named_models(sessions, limit=MAX_NAMED_MODELS):
    spend = collections.Counter()
    for s in sessions:
        if s["model"]:
            spend[(s["model"], s["row"].get("runtime") or "")] += _cost(s) or 1e-9
    return [{"model": model, "runtime": runtime} for (model, runtime), _ in spend.most_common(limit)]


def _recommendations(in_window, opportunities, tiers, prices):
    """Ranked ways to spend less on models; savings are estimates and can overlap between items."""
    recs = [dict(item, saving=round(item["spend"] - item["estimate"], 6) if item["estimate"] is not None else None)
            for item in opportunities if item["sessions"] >= MIN_RECOMMENDATION_SESSIONS]
    for item in recs:
        if item["kind"] == "premium_routine":
            routine = [s for s in in_window if s["complexity"] == "routine"]
            item["from_models"] = _named_models([s for s in routine if s["tier"] == "premium"])
            # One mid-priced model per app the premium routine work ran in, so advice stays within that app.
            item["to_models"] = []
            for runtime in dict.fromkeys(m["runtime"] for m in item["from_models"]):
                choice = _named_models([s for s in in_window if s["tier"] == "standard"
                                        and (s["row"].get("runtime") or "") == runtime], 1)
                item["to_models"].extend(choice)
    recs.extend(_switch_recommendations(in_window, tiers, prices))
    recs.extend(_family_recommendations(in_window, prices))
    long = _long_thread_recommendation(in_window)
    if long:
        recs.append(long)
    recs.sort(key=lambda item: (-(item["saving"] or 0), -item["spend"]))
    return recs[:MAX_RECOMMENDATIONS]


MAX_DRILL_SESSIONS = 50
MAX_DRILL_IDS = 2000
DRILL_FILTERS = ("month", "start_month", "area", "work_type", "complexity", "tier", "effort", "outcome",
                 "model", "model_runtime", "tag")


def find_sessions(rows, labels, key_for, areas, output_price, filters, months=6, runtime="", project="",
                  corrections_for=None, pending_keys=None, limit=MAX_DRILL_SESSIONS, ids_only=False, today="",
                  label_since=""):
    """Sessions behind one Work module cell, ranked by spend; same windowing and labels as the aggregates.

    ``month`` selects sessions active in that month (as the allocation counts turns and spend);
    ``start_month`` selects sessions started in that month (as outcomes and session counts do);
    every other filter applies to sessions started in the selected period.
    """
    area_names = [a["name"] for a in areas]
    tiers = price_tiers(rows, output_price)
    grain, count = _period(months)
    sessions, _runtimes, _projects = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain, label_since)
    all_months = _window(sessions, grain, count, today)
    window = set(all_months)
    tag_context = _tag_context([s for s in sessions if s["start"] in window])
    month = filters.get("month") or ""
    start_month = filters.get("start_month") or ""
    if start_month:
        candidates = [s for s in sessions if start_month in all_months and s["start"] == start_month]
    elif month:
        candidates = [s for s in sessions if month in all_months and month in _session_buckets(s, grain)]
    else:
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
        if filters.get("effort") and s["effort"] not in filters["effort"].split(","):
            continue
        if filters.get("model") and (s["model"] != filters["model"]
                                     or (s["row"].get("runtime") or "") != filters.get("model_runtime", "")):
            continue
        matched.append(s)
    _attach_sequences(matched, corrections_for)
    if filters.get("outcome"):
        matched = [s for s in matched
                   if _outcome(s) == filters["outcome"]]
    for s in matched:
        s["tags"] = session_tags(s, tag_context, _outcome(s))
    if filters.get("tag"):
        matched = [s for s in matched if filters["tag"] in s["tags"]]
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
            "area_guess": s["area_guess"],
            "work_type": s["work_type"],
            "complexity": s["complexity"],
            "outcome": _outcome(s),
            "corrections": s["corrections"],
            "labeled_turns": s["correction_labels"],
            "tags": s["tags"],
        } for s in matched[:limit]],
    }


LIVE_HINT_ORDER = ("pushback_streak", "light_complex", "premium_routine", "effort_routine", "long_thread")
MAX_LIVE_HINTS = 3
MAX_HINT_MODEL = 80


def live_session_hints(rows, current, labels, key_for, output_price, corrections_for=None):
    """Suggestions for running sessions, keyed by trace key; content-free and bounded.

    Per-session versions of the Right-sizing checks: routine work on a premium model or at high effort,
    complex work on a light model that has had pushback in this session, the last two labeled follow-ups
    both pushback, and 30+ requests. Model-switch advice that needs outcome evidence stays on the Work page.
    """
    wanted = {str(c.get("session") or "") for c in current or () if c.get("session")}
    if not wanted:
        return {}
    tiers, _tier_prices, prices = price_tiers(rows, output_price, with_prices=True)
    used = collections.Counter((row.get("runtime") or "", primary_model(row)) for row in rows
                               if not is_child_row(row) and primary_model(row))
    standard = {}
    for (runtime, model), _count in used.most_common():
        if tiers.get((runtime, model)) == "standard":
            standard.setdefault(runtime, model)
    out, newest = {}, {}
    for row in rows:
        key = _trace_key(row)
        # Resumed sessions span several files with one key; the newest file is the live one.
        if key in wanted and not is_child_row(row) and (row.get("mtime") or 0) >= (newest.get(key, {}).get("mtime") or 0):
            newest[key] = row
    for key, row in newest.items():
        runtime, model = row.get("runtime") or "", primary_model(row)[:MAX_HINT_MODEL]
        entry = labels.get(key_for(work_identity(row))) or {}
        complexity = entry.get("complexity") or ""
        tier = tiers.get((runtime, model))
        effort = str(row.get("reasoning_effort") or "").lower()
        turns = len(turn_days(row))
        hints = []
        if complexity == "routine" and tier == "premium":
            hints.append({"kind": "premium_routine", "model": model,
                          "to_model": (standard.get(runtime) or "")[:MAX_HINT_MODEL]})
        if complexity == "routine" and effort in HIGH_EFFORTS:
            hints.append({"kind": "effort_routine", "effort": effort})
        if complexity in ("complex", "high_impact") and tier == "light" and int(entry.get("corrections") or 0) > 0:
            hints.append({"kind": "light_complex", "model": model})
        if corrections_for is not None and turns > 2 and entry.get("correction_labels"):
            confident = [value for _ordinal, value in corrections_for(work_identity(row), turns) if value is not None]
            if len(confident) >= 2 and confident[-1] and confident[-2]:
                hints.append({"kind": "pushback_streak"})
        if turns >= LONG_THREAD_TURNS:
            hints.append({"kind": "long_thread", "turns": turns})
        hints.sort(key=lambda hint: LIVE_HINT_ORDER.index(hint["kind"]))
        if hints:
            out[key] = [dict(hint, **_live_hint_text(hint)) for hint in hints[:MAX_LIVE_HINTS]]
    return out


def _live_hint_text(hint):
    kind = hint["kind"]
    if kind == "pushback_streak":
        return {"title": "Two pushbacks in a row",  # the last two confidently labeled follow-ups
                "detail": "Restate the goal in one message, or start a fresh session with what you learned."}
    if kind == "light_complex":
        return {"title": "Complex work on a light model",
                "detail": f"{hint['model']} is getting pushback on a complex request. A stronger model may finish sooner."}
    if kind == "premium_routine":
        return {"title": "Routine request on a premium model",
                "detail": f"{hint['to_model']} handles routine work for less." if hint["to_model"]
                else "A mid-priced model handles routine work for less."}
    if kind == "effort_routine":
        return {"title": "High reasoning effort on routine work",
                "detail": f"{hint['effort']} effort costs more than a routine request needs. Try medium."}
    return {"title": "Long session",
            "detail": f"{hint['turns']} requests so far, and each re-sends the conversation. "
                      "A fresh session with a short summary costs less."}
