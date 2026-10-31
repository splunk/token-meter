"""Runtime-neutral work insights: allocation, outcomes, work-type economics, rework, right-sizing.

Pure functions over cached session summary rows and content-free classifier labels.

Three units, each used where it fits:

- A **request** is one typed request and everything the agent did until the next one. It carries its own
  cost share, model, labels (area, work type, complexity), and the pushback label of the request after it.
  Spend modules (where the spend went, right-sizing, effort) add up requests.
- A **task** is a run of consecutive requests with the same kind of work. Whether it was resolved comes
  from the pushback on its follow-ups and on the request that ended it. Cost per resolved task, model
  fit, the scorecard, and model-switch advice use tasks.
- A **session** keeps what only a session has: its start time, duration, child runs, how it ended, and
  its tags.
"""

import collections
import datetime
import math
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
COMPLEXITY_OF = {member: name for name, members in COMPLEXITY_GROUPS for member in members}
TIERS = ("light", "standard", "premium")
COMPLEXITY_ORDER = ("routine", "everyday", "complex")
WORK_TYPE_ORDER = ("feature", "debug", "refactor", "test", "review", "plan", "explore", "ops", "docs", "other",
                   "unclear")
LABEL_FIELDS = ("work_type", "area", "area_guess", "complexity")
JUDGED = ("accepted", "recovered", "ended_on_pushback")
RESOLVED = ("accepted", "recovered")


def work_identity(row):
    """Per-trace identity: one session id can span several rollout files (resumes, forks)."""
    identity = f"{row.get('id') or ''}\0{row.get('path') or ''}"
    if row.get("_work_lineage"):
        identity += f"\0{row['_work_lineage']}"
    return identity


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


def _row_models(row):
    models = [primary_model(row)]
    models.extend(s.get("model") for s in row.get("model_stats") or [] if isinstance(s, dict))
    models.extend(r.get("model") for r in row.get("_work_requests") or [] if isinstance(r, dict))
    return [m for m in dict.fromkeys(models) if m]


def price_tiers(rows, output_price, with_prices=False):
    """Map (runtime, model) to light/standard/premium by terciles of catalog output price."""
    prices = {}
    for row in rows:
        for model in _row_models(row):
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


DAY_RANGES = (1, 7, 30, 90)
MONTH_RANGES = (3, 6, 12, 0)
MONTH_TO_DATE = "month"


def parse_period(value):
    """``(grain, count)`` for a History choice: ``1d``/``7d``/``30d``/``90d`` are days, 3/6/12/0 months; else None.

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
# A request with no recorded effort ran at the app's default, whose level is unknown: shown, never ranked.
DEFAULT_EFFORT = "default"
EFFORT_PROVIDERS = ("claude", "codex")  # the only traces that record a reasoning effort
EFFORT_ORDER = (DEFAULT_EFFORT, "low", "medium", "high", "xhigh", "max", "ultra")
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


def task_outcome(pushbacks):
    """Classify a task from the pushback labels on its follow-ups and on the request that ended it.

    ``pushbacks`` holds True, False, or None (low confidence) per labeled follow-up, in order. A task with
    no labeled follow-up is unjudged: nothing shows whether its result held up.
    """
    if not pushbacks:
        return "unjudged"
    confident = [value for value in pushbacks if value is not None]
    if not confident:
        return "unclear"
    if not any(confident):
        return "accepted"
    return "ended_on_pushback" if confident[-1] else "recovered"


def _month_shift(month, delta):
    year, number = divmod(int(month[:4]) * 12 + int(month[5:7]) - 1 + delta, 12)
    return f"{year:04d}-{number + 1:02d}"


def build_work_insights(rows, labels, key_for, areas, output_price, months=6,
                        runtime="", project="", today="", corrections_for=None, pending_keys=None,
                        label_since="", requests_for=None):
    """Aggregate labeled work. ``months`` is a History choice (see ``parse_period``); 0 is all history.

    ``requests_for(identity, count)`` returns per-request labels (see the service's ``session_requests``);
    without it, every request inherits its session's labels.
    """
    grain, count = _period(months)
    area_names = [a["name"] for a in areas]
    tiers, tier_prices, model_prices = price_tiers(rows, output_price, with_prices=True)
    sessions, runtime_options, project_options = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain, label_since,
        requests_for, corrections_for)
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


def _day_costs(row, days):
    """Spend by day; rows without a daily split (some runtimes) count their cost on the start day."""
    day_cost = row.get("_day_cost") or {}
    cost = float(row.get("cost") or 0)
    if day_cost or not cost:
        return {str(day): float(value or 0) for day, value in day_cost.items()}
    day = (row.get("start") or "")[:10] or (days[0] if days else "")
    return {day: cost} if day else {}


def _request_day_costs(row, days):
    """Each request's spend by the day it happened (``{day: cost}``), adding up to the session's cost.

    The runtime's per-request split is used when it has one; its total is scaled to the session's
    reported cost (runtimes can price a session differently from its messages). Otherwise each day's
    cost is spread over that day's requests, and a day without requests goes to the latest request
    before it, or the first. The day stays the day the cost happened, not the request's start day.
    """
    total = float(row.get("cost") or 0)
    slices = row.get("_work_requests") or []
    if slices and len(slices) == len(days):
        split = []
        for item, day in zip(slices, days):
            spread = collections.Counter()
            for when, value in (item.get("days") or {}).items():
                spread[when or day] += float(value or 0)
            if not spread and float(item.get("cost") or 0):
                spread[day] += float(item.get("cost") or 0)
            split.append(spread)
        counted = sum(sum(spread.values()) for spread in split)
        if counted > 0:
            if total > 0 and abs(counted - total) > 1e-6:
                split = [collections.Counter({d: v * total / counted for d, v in spread.items()}) for spread in split]
            return [dict(spread) for spread in split], slices
    split = [collections.Counter() for _ in days]
    if not days:
        return split, []
    by_day = collections.defaultdict(list)
    for index, day in enumerate(days):
        by_day[day].append(index)
    ordered = sorted((day, index) for index, day in enumerate(days) if day)
    for when, value in _day_costs(row, [d for d in days if d]).items():
        owners = by_day.get(when)
        if not owners:
            earlier = [index for d, index in ordered if d <= when]
            owners = [earlier[-1] if earlier else (ordered[0][1] if ordered else 0)]
        for index in owners:
            split[index][when] += float(value or 0) / len(owners)
    return [dict(spread) for spread in split], []


def _carry_labels(own, fallback):
    """Fill requests without labels from the previous labeled request (leading ones from the first)."""
    filled, last = [], None
    for labels in own:
        if labels.get("work_type") or labels.get("area"):
            current = dict(last or fallback or {})
            current.update({k: v for k, v in labels.items() if k in LABEL_FIELDS and v not in (None, "")})
            if "area" in labels:
                current["area_guess"] = bool(labels.get("area_guess"))
            last = current
            filled.append((current, True))
        else:
            filled.append((last, False))
    first = next((labels for labels, _own in filled if labels), None) or fallback or {}
    return [(labels if labels is not None else dict(first), own_flag) for labels, own_flag in filled]


def _session_requests(row, entry, own, days, area_names, tiers, unlabeled, session):
    """Build a session's requests: cost share, model, tier, effort, labels, and pushback context."""
    runtime = row.get("runtime") or ""
    model = primary_model(row)
    effort = str(row.get("reasoning_effort") or "").lower()
    unset = DEFAULT_EFFORT if row.get("provider") in EFFORT_PROVIDERS else ""
    if not days:
        days = [(row.get("start") or "")[:10]]
        own = [{}]
    day_costs, slices = _request_day_costs(row, days)
    fallback = {k: entry[k] for k in LABEL_FIELDS if k in entry}
    labeled = _carry_labels(list(own) + [{}] * (len(days) - len(own)), fallback)
    requests = []
    for ordinal, (day, spread, (labels, own_flag)) in enumerate(zip(days, day_costs, labeled)):
        item = slices[ordinal] if ordinal < len(slices) else {}
        area = labels.get("area") or unlabeled
        if area not in area_names and area not in (UNCLEAR, *UNLABELED):
            area = unlabeled
        request_model = item.get("model") or model
        mine = own[ordinal] if ordinal < len(own) else {}
        requests.append({
            "ordinal": ordinal, "day": day or (row.get("start") or "")[:10], "cost": sum(spread.values()),
            "day_costs": spread,
            "model": request_model, "runtime": runtime, "tier": tiers.get((runtime, request_model)),
            "effort": str(item.get("effort") or effort).lower() or unset,
            "work_type": labels.get("work_type") or "",
            "area": area, "area_guess": bool(labels.get("area_guess")) and area in area_names,
            "complexity": labels.get("complexity") or "",
            "labeled": own_flag, "has_pushback": "pushback" in mine, "pushback": mine.get("pushback"),
            "model_calls": item.get("model_calls"),
            "session": session,
        })
    for current, following in zip(requests, requests[1:]):
        current["next_labeled"] = following["has_pushback"]
        current["next_pushback"] = following["pushback"]
    if requests:
        requests[-1]["next_labeled"], requests[-1]["next_pushback"] = False, None
    return requests


def _tasks(requests):
    """Group consecutive requests with the same kind of work; judge each from the pushback that followed."""
    tasks = []
    for request in requests:
        if tasks and tasks[-1]["work_type"] == request["work_type"]:
            tasks[-1]["requests"].append(request)
        else:
            tasks.append({"work_type": request["work_type"], "requests": [request]})
    for task in tasks:
        members = task["requests"]
        spend = sum(r["cost"] for r in members)
        pushbacks = [r["pushback"] for r in members[1:] if r["has_pushback"]]
        if members[-1]["next_labeled"]:
            pushbacks.append(members[-1]["next_pushback"])
        model_cost = collections.Counter()
        complexity_cost = collections.Counter()
        area_cost = collections.Counter()
        for r in members:
            model_cost[r["model"]] += r["cost"] or 1e-9
            if r["complexity"]:
                complexity_cost[r["complexity"]] += r["cost"] or 1e-9
            area_cost[r["area"]] += r["cost"] or 1e-9
        first = members[0]
        model = model_cost.most_common(1)[0][0] if model_cost else ""
        task.update({
            "cost": spend, "day": first["day"], "model": model, "runtime": first["runtime"],
            "tier": next((r["tier"] for r in members if r["model"] == model), first["tier"]),
            "effort": first["effort"],
            "complexity": complexity_cost.most_common(1)[0][0] if complexity_cost else "",
            "area": area_cost.most_common(1)[0][0] if area_cost else first["area"],
            "outcome": task_outcome(pushbacks),
            "corrections": sum(1 for value in pushbacks if value),
            "correction_labels": len(pushbacks),
            "session": first["session"],
        })
    return tasks


def session_labels(row, entry, own, area_names):
    """A session's headline labels: the area, kind of work, and complexity that cost the most across its requests."""
    days = [d or (row.get("start") or "")[:10] for d in turn_days(row)]
    requests = _session_requests(row, entry or {}, list(own or ()), days, area_names, {}, PENDING, None)
    area = _dominant(requests, "area") or (entry or {}).get("area") or ""
    guessed = sum(r["cost"] or 1e-9 for r in requests if r["area"] == area and r["area_guess"])
    total = sum(r["cost"] or 1e-9 for r in requests if r["area"] == area)
    return {"area": area if area not in UNLABELED else "", "area_guess": total > 0 and guessed * 2 > total,
            "work_type": _dominant(requests, "work_type"), "complexity": _dominant(requests, "complexity")}


def _dominant(requests, field):
    weights = collections.Counter()
    for r in requests:
        if r[field]:
            weights[r[field]] += r["cost"] or 1e-9
    return weights.most_common(1)[0][0] if weights else ""


def _prepare_sessions(rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain="month",
                      label_since="", requests_for=None, corrections_for=None):
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
        identity = work_identity(row)
        entry = labels.get(key_for(identity)) or {}
        all_days = turn_days(row)
        days = [d for d in all_days if d]
        start_day = (row.get("start") or "")[:10]
        area = entry.get("area") or PENDING
        if area not in area_names and area not in (UNCLEAR, PENDING):
            area = PENDING
        unlabeled = PENDING
        if area == PENDING:
            if not days:
                area = unlabeled = NO_TEXT
            elif label_since and max(days) < label_since:  # the classifier skips by newest activity
                area = unlabeled = OUTSIDE
        own = list(requests_for(identity, len(all_days))) if requests_for is not None and all_days else []
        if requests_for is None and corrections_for is not None and len(all_days) > 1 \
                and entry.get("correction_labels"):
            # Without per-request labels, requests inherit the session's labels and keep their pushback.
            own = [{} for _ in all_days]
            for ordinal, value in corrections_for(identity, len(all_days)):
                if 0 <= ordinal < len(own):
                    own[ordinal]["pushback"] = value
        s = {
            "row": row, "entry": entry,
            "days": days, "start": _bucket(start_day or (days[0] if days else ""), grain),
            "turns": len(all_days),
            "corrections": int(entry.get("corrections") or 0),
            "correction_labels": int(entry.get("correction_labels") or 0),
            "model": primary_model(row),
            "tier": tiers.get((row.get("runtime") or "", primary_model(row))),
            "effort": str(row.get("reasoning_effort") or "").lower(),
            "duration": int(row.get("duration_s") or 0) if row.get("duration_available") else 0,
            "children": child_counts.get(_root_agent_id(row), 0),
            "sequence": [],
            "pending": bool(pending_keys) and key_for(identity) in pending_keys,
        }
        # Requests carry their turn's day; a day-less turn (rare) falls back to the session start.
        s["requests"] = _session_requests(row, entry, own or [], [d or start_day for d in all_days],
                                          area_names, tiers, unlabeled, s)
        s["tasks"] = _tasks(s["requests"])
        s["area"] = _dominant(s["requests"], "area") or area
        s["work_type"] = _dominant(s["requests"], "work_type")
        s["complexity"] = _dominant(s["requests"], "complexity")
        guess_cost = sum(r["cost"] or 1e-9 for r in s["requests"] if r["area"] == s["area"] and r["area_guess"])
        area_cost = sum(r["cost"] or 1e-9 for r in s["requests"] if r["area"] == s["area"])
        s["area_guess"] = s["area"] in area_names and area_cost > 0 and guess_cost * 2 > area_cost
        sessions.append(s)
    return sessions, runtime_options, project_options


def _window_costs(sessions, grain, window):
    """Set each request's and task's spend to the part that happened in the window (``cost``)."""
    for s in sessions:
        for r in s["requests"]:
            r["cost"] = sum(v for d, v in r["day_costs"].items() if _bucket(d, grain) in window)
            r["in_window"] = r["cost"] > 0 or _bucket(r["day"], grain) in window
        for t in s["tasks"]:
            t["cost"] = sum(r["cost"] for r in t["requests"])
            t["in_window"] = any(r["in_window"] for r in t["requests"])


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


def _request_rework(requests):
    """Pushback on the request that followed: how often the user had to push back on this work."""
    labeled = [r for r in requests if r["next_labeled"]]
    return _rate(sum(1 for r in labeled if r["next_pushback"]), len(labeled))


def _task_rework(tasks):
    return _rate(sum(t["corrections"] for t in tasks), sum(t["correction_labels"] for t in tasks))


TASK_OUTCOMES = ("accepted", "recovered", "ended_on_pushback", "unclear", "unjudged")
MAX_BREAKDOWN_MODELS = 8


def _compares_models(work_type):
    """Only labeled kinds of work rank models; Unclear and unlabeled requests mix unrelated work."""
    return bool(work_type) and work_type != "unclear"


def _model_extremes(requests):
    """The lowest- and highest-pushback model (scoped by runtime) over these requests, or (None, None).

    A model counts once it has enough labeled follow-ups and sessions to rate, the same bar as Model fit;
    two eligible models with different rates are needed before either is called best or worst.
    """
    groups = collections.defaultdict(list)
    for r in requests:
        if r["model"]:
            groups[(r["model"], r["runtime"])].append(r)
    eligible = []
    for (model, runtime), group in groups.items():
        rate = _request_rework(group)
        if rate and not rate["few_samples"] and _sessions_in(group) >= MIN_FIT_SESSIONS:
            eligible.append({"model": model, "runtime": runtime, "rate": rate["rate"], "samples": rate["samples"]})
    if len(eligible) < 2:
        return None, None
    best = min(eligible, key=lambda item: (item["rate"], -item["samples"], item["model"]))
    worst = max(eligible, key=lambda item: (item["rate"], item["samples"], item["model"]))
    return (best, worst) if best["rate"] < worst["rate"] else (None, None)


def _breakdown_row(key, requests, tasks, total_spend, extremes=True):
    """Spend, outcomes, human/agent turns, pushback, and best/worst model for one kind of work."""
    spend = sum(r["cost"] for r in requests)
    judged = [t for t in tasks if t["outcome"] in JUDGED]
    resolved = [t for t in tasks if t["outcome"] in RESOLVED]
    outcomes = collections.Counter(t["outcome"] for t in tasks)
    # Human turns are typed requests; agent turns are the model calls answering them, where the runtime split them.
    answered = [r for r in requests if r["model_calls"] is not None]
    agent_turns = sum(r["model_calls"] for r in answered)
    best, worst = _model_extremes(requests) if extremes else (None, None)
    return {
        "key": key, "spend": round(spend, 6), "share": spend / total_spend if total_spend > 0 else None,
        "requests": len(requests), "sessions": _sessions_in(requests),
        "tasks": len(tasks), "judged_tasks": len(judged), "resolved_tasks": len(resolved),
        "resolved_rate": len(resolved) / len(judged) if judged else None,
        "cost_per_resolved": sum(t["cost"] for t in resolved) / len(resolved) if resolved else None,
        "outcomes": {outcome: outcomes[outcome] for outcome in TASK_OUTCOMES if outcomes[outcome]},
        "human_turns": len(answered), "agent_turns": agent_turns,
        "agent_per_human": agent_turns / len(answered) if answered else None,
        "pushback": _request_rework(requests), "few_samples": len(requests) < MIN_RATE_SAMPLES,
        "best_model": best, "worst_model": worst,
    }


def _breakdown_rows(requests, tasks, extremes=True):
    total = sum(r["cost"] for r in requests)
    request_groups, task_groups = collections.defaultdict(list), collections.defaultdict(list)
    for r in requests:
        request_groups[r["work_type"]].append(r)
    for t in tasks:
        task_groups[t["work_type"]].append(t)
    rows = [_breakdown_row(key, members, task_groups.get(key, []), total, extremes and _compares_models(key))
            for key, members in request_groups.items()]
    return _breakdown_row("", requests, tasks, total, False), sorted(rows, key=lambda row: -row["spend"])


def _agent_trend(requests, grain):
    """Agent turns (model calls) per human turn by week, or by day for short ranges."""
    weekly = collections.defaultdict(lambda: [0, 0])
    for r in requests:
        point = _trend_point(r["day"], grain) if r["model_calls"] is not None else ""
        if point:
            weekly[point][0] += 1
            weekly[point][1] += r["model_calls"]
    return {"grain": "day" if grain == "day" else "week",
            "points": [{"week": week, "human_turns": human, "agent_turns": agent, "agent_per_human": agent / human,
                        "few_samples": human < MIN_RATE_SAMPLES}
                       for week, (human, agent) in sorted(weekly.items())]}


def _breakdown(requests, tasks, grain="month"):
    """One row per kind of work, so spend, outcomes, human/agent turns, and pushback read side by side.

    ``models`` repeats the rows for each of the most-used models (scoped by runtime), for the model filter;
    those rows skip best/worst model, which compares models with each other. ``agent_trend`` is agent turns
    per human turn by week (by day for short ranges).
    """
    overall, rows = _breakdown_rows(requests, tasks)
    spend = collections.Counter()
    for r in requests:
        if r["model"]:
            spend[(r["model"], r["runtime"])] += r["cost"] or 1e-9
    models = []
    for (model, runtime), _value in spend.most_common(MAX_BREAKDOWN_MODELS):
        mine = [r for r in requests if (r["model"], r["runtime"]) == (model, runtime)]
        own_tasks = [t for t in tasks if (t["model"], t["runtime"]) == (model, runtime)]
        model_overall, model_rows = _breakdown_rows(mine, own_tasks, extremes=False)
        models.append({"model": model, "runtime": runtime, "overall": model_overall, "work_type": model_rows,
                       "agent_trend": _agent_trend(mine, grain)})
    return {"overall": overall, "work_type": rows, "models": models, "agent_trend": _agent_trend(requests, grain)}


def _aggregate(sessions, all_months, month_set, segments, area_names, tiers, tier_prices,
               runtime_options, project_options, today, corrections_for, grain="month", model_prices=None):
    allocation = []
    for month in all_months:
        bucket = {"month": month, "partial": bool(today and _bucket(today, grain) == month),
                  "turns": dict.fromkeys(segments, 0), "sessions": dict.fromkeys(segments, 0),
                  "spend": dict.fromkeys(segments, 0.0), "guess_spend": {}, "guess_sessions": {}}
        allocation.append(bucket)
    by_month = {b["month"]: b for b in allocation}
    _window_costs(sessions, grain, month_set)
    requests = [r for s in sessions for r in s["requests"] if r["in_window"]]
    for r in requests:
        counted = by_month.get(_bucket(r["day"], grain))
        if counted:
            counted["turns"][r["area"]] += 1
        for day, value in r["day_costs"].items():
            bucket = by_month.get(_bucket(day, grain))
            if bucket is None:
                continue
            bucket["spend"][r["area"]] += value
            if r["area_guess"]:
                bucket["guess_spend"][r["area"]] = round(bucket["guess_spend"].get(r["area"], 0.0) + value, 6)
    for s in sessions:
        if s["start"] in by_month:
            by_month[s["start"]]["sessions"][s["area"]] += 1
            if s["area_guess"]:
                guesses = by_month[s["start"]]["guess_sessions"]
                guesses[s["area"]] = guesses.get(s["area"], 0) + 1
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
    tasks = [t for s in sessions for t in s["tasks"] if t["in_window"]]
    labeled_requests = requests
    labeled_tasks = [t for t in tasks if t["work_type"]]

    economics = []
    by_type = collections.defaultdict(list)
    for t in labeled_tasks:
        by_type[t["work_type"]].append(t)
    for work_type in WORK_TYPE_ORDER:
        group = by_type.get(work_type)
        if not group:
            continue
        spend = sum(t["cost"] for t in group)
        judged = [t for t in group if t["outcome"] in JUDGED]
        resolved = [t for t in group if t["outcome"] in RESOLVED]
        economics.append({
            "work_type": work_type, "tasks": len(group), "spend": round(spend, 6),
            "cost_per_task": spend / len(group),
            "judged_tasks": len(judged),
            "resolved_tasks": len(resolved),
            "resolved_rate": len(resolved) / len(judged) if judged else None,
            "cost_per_resolved": (sum(t["cost"] for t in resolved) / len(resolved)) if resolved else None,
            "median_requests": statistics.median([len(t["requests"]) for t in group]),
            "rework": _task_rework(group),
        })

    weekly = collections.defaultdict(lambda: [0, 0])
    models = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in labeled_requests:
        if r["next_labeled"]:
            week = _trend_point(r["day"], grain)
            if week:
                weekly[week][0] += bool(r["next_pushback"])
                weekly[week][1] += 1
            key = (r["model"], r["runtime"])
            models[key][0] += bool(r["next_pushback"])
            models[key][1] += 1
        models[(r["model"], r["runtime"])][2] += r["cost"]
    top_models = sorted((key for key in models if models[key][1]), key=lambda key: -models[key][1])[:MAX_TREND_MODELS]
    by_model = {key: collections.defaultdict(lambda: [0, 0]) for key in top_models}
    for r in labeled_requests:
        key = (r["model"], r["runtime"])
        if key in by_model and r["next_labeled"]:
            week = _trend_point(r["day"], grain)
            by_model[key][week][0] += bool(r["next_pushback"])
            by_model[key][week][1] += 1
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
        "overall": _request_rework(labeled_requests),
    }

    cells = []
    for group_name, members in COMPLEXITY_GROUPS:
        for tier in TIERS:
            group = [r for r in labeled_requests if r["complexity"] in members and r["tier"] == tier]
            spend = sum(r["cost"] for r in group)
            cells.append({"complexity": group_name, "tier": tier, "requests": len(group),
                          "sessions": _sessions_in(group), "spend": round(spend, 6),
                          "rework": _request_rework(group)})
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

    effort = _effort(labeled_requests)
    flagged = _flagged_spend(labeled_requests, cells, effort)
    model_fit = _model_fit(labeled_tasks, labeled_requests)
    scorecard = _model_scorecard([t for t in tasks if t["model"]], labeled_requests, tiers)
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
        "effort_by_model": _effort_by_model(labeled_requests),
        "opportunities": opportunities,
        "tags": _tag_summary(in_window, tag_context),
        "rhythm": rhythm,
        "breakdown": _breakdown(requests, tasks, grain),
        "recommendations": _recommendations(in_window, labeled_tasks, labeled_requests, opportunities, tiers,
                                            model_prices or {}),
        "coverage": {
            "sessions": len(in_window), "labeled_sessions": labeled_sessions,
            "no_text_sessions": sum(1 for s in in_window if s["area"] == NO_TEXT),
            "outside_sessions": sum(1 for s in in_window if s["area"] == OUTSIDE),
            "turns": later_turns, "labeled_turns": min(labeled_turns, later_turns),
            "requests": len(requests),
            "labeled_requests": sum(1 for r in requests if r["labeled"]),
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


def _model_fit(tasks, requests=()):
    """Kind of work × model: cost per task and pushback, for the models used most.

    Pushback credits each request to the model that answered it, the same rule as the scorecard and the card.
    """
    labeled = [t for t in tasks if t["work_type"] and t["work_type"] != "unclear" and t["model"]]
    usage = collections.Counter((t["model"], t["runtime"]) for t in labeled)
    models = [key for key, _ in usage.most_common(MAX_FIT_MODELS)]
    work_types = [w for w in WORK_TYPE_ORDER if w != "unclear" and any(t["work_type"] == w for t in labeled)]
    cells = []
    for work_type in work_types:
        row_cells = []
        for model, runtime in models:
            group = [t for t in labeled if t["work_type"] == work_type and (t["model"], t["runtime"]) == (model, runtime)]
            spend = sum(t["cost"] for t in group)
            row_cells.append({"work_type": work_type, "model": model, "runtime": runtime, "tasks": len(group),
                              "sessions": _sessions_in(group),
                              "cost_per_task": spend / len(group) if group else None,
                              "rework": _request_rework([r for r in requests if r["work_type"] == work_type
                                                         and (r["model"], r["runtime"]) == (model, runtime)]),
                              "best": False})
        eligible = [c for c in row_cells if c["rework"] and not c["rework"]["few_samples"]
                    and c["sessions"] >= MIN_FIT_SESSIONS]
        if len(eligible) >= 2:
            min(eligible, key=lambda c: (c["rework"]["rate"], c["cost_per_task"] or 0))["best"] = True
        cells.extend(row_cells)
    return {"work_types": work_types,
            "models": [{"model": m, "runtime": r, "tasks": usage[(m, r)]} for m, r in models],
            "cells": cells}


MAX_SCORECARD_MODELS = 8


def _model_scorecard(tasks, requests, tiers):
    """Per model (scoped by runtime): spend, tasks, pushback, resolved share, and cost per resolved task."""
    groups = collections.defaultdict(list)
    for t in tasks:
        if t["model"]:
            groups[(t["model"], t["runtime"])].append(t)
    spend_by = collections.Counter()
    requests_by = collections.defaultdict(list)
    for r in requests:
        spend_by[(r["model"], r["runtime"])] += r["cost"]
        requests_by[(r["model"], r["runtime"])].append(r)
    by_type = collections.defaultdict(list)
    for r in requests:
        if _compares_models(r["work_type"]):
            by_type[r["work_type"]].append(r)
    best_at, worst_at = collections.defaultdict(list), collections.defaultdict(list)
    for work_type in WORK_TYPE_ORDER:
        best, worst = _model_extremes(by_type[work_type]) if by_type.get(work_type) else (None, None)
        if best:
            best_at[(best["model"], best["runtime"])].append(work_type)
            worst_at[(worst["model"], worst["runtime"])].append(work_type)
    rows = []
    for (model, runtime), group in groups.items():
        judged = [t for t in group if t["outcome"] in JUDGED]
        resolved = [t for t in group if t["outcome"] in RESOLVED]
        spend = spend_by[(model, runtime)] or sum(t["cost"] for t in group)
        rows.append({
            "model": model, "runtime": runtime, "tier": tiers.get((runtime, model)),
            "tasks": len(group), "spend": round(spend, 6),
            "cost_per_task": sum(t["cost"] for t in group) / len(group),
            "judged_tasks": len(judged), "judged_sessions": _sessions_in(judged),
            "resolved_rate": len(resolved) / len(judged) if judged else None,
            "cost_per_resolved": sum(t["cost"] for t in resolved) / len(resolved) if resolved else None,
            # Pushback credits each request to the model that answered it, the same rule as Work's other pushback.
            "rework": _request_rework(requests_by[(model, runtime)]),
            # Kinds of work where this model drew the least or most pushback among the models rated for it.
            "best_at": best_at[(model, runtime)], "worst_at": worst_at[(model, runtime)],
        })
    rows.sort(key=lambda item: (-item["spend"], -item["tasks"], item["model"]))
    return rows[:MAX_SCORECARD_MODELS]


def _effort(requests):
    """Complexity × reasoning effort: spend, requests, pushback; high effort on routine work is flagged."""
    efforts = [e for e in EFFORT_ORDER if any(r["effort"] == e for r in requests)]
    rows = []
    for group_name, members in COMPLEXITY_GROUPS:
        for effort in efforts:
            group = [r for r in requests if r["complexity"] in members and r["effort"] == effort]
            spend = sum(r["cost"] for r in group)
            rows.append({"complexity": group_name, "effort": effort, "requests": len(group),
                         "sessions": _sessions_in(group),
                         "spend": round(spend, 6), "rework": _request_rework(group),
                         "flag": "possible_overthinking" if group_name == "routine" and effort in HIGH_EFFORTS
                         and spend > 0 else ""})
    return {"efforts": efforts, "cells": rows}


# Effort levels in the order they buy more thinking; a model only shows the levels it actually used.
EFFORT_RANK = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
# A lower level counts as safe when its pushback is no worse than the next level's by more than this.
SAFE_LOWER_MARGIN = 0.05
# Many cells are compared at once (models × rows × levels), so verdicts use 99% rather than 95%.
VERDICT_Z = 2.576


def _two_proportion(low, high):
    """(adjusted difference low - high, its half-width, pooled z) for two pushback rates.

    The difference and its interval use the Agresti-Caffo adjustment (one pushback and one not added to each
    side), so two levels with no pushback at all do not look certain; the z test uses the plain pooled rate.
    """
    n1, n2 = low["samples"], high["samples"]
    x1, x2 = low["rate"] * n1, high["rate"] * n2
    a1, a2 = (x1 + 1) / (n1 + 2), (x2 + 1) / (n2 + 2)
    spread = VERDICT_Z * math.sqrt(a1 * (1 - a1) / (n1 + 2) + a2 * (1 - a2) / (n2 + 2))
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return a1 - a2, spread, ((x1 / n1 - x2 / n2) / se if se else 0.0)


def _effort_by_model(requests):
    """Per model (scoped by runtime): complexity × that model's own effort levels, with pushback and cost.

    Effort means different things on different models, so levels are only ever compared within one model,
    one complexity row at a time, between neighbouring levels. "default" gets a column but is never compared;
    requests with an unknown effort string are left out.
    """
    by_model = collections.defaultdict(list)
    unrecorded = collections.Counter()
    for r in requests:
        if r["complexity"] not in COMPLEXITY_OF:
            continue  # not labeled yet: it cannot land in a cell
        key = (r["model"], r["runtime"])
        # "none" is a real setting (no extra reasoning), so it counts as a recorded level.
        if r["effort"] in EFFORT_RANK or r["effort"] == DEFAULT_EFFORT:
            by_model[key].append(r)
        else:
            unrecorded[key] += r["cost"]
    ranked = sorted(by_model, key=lambda key: -sum(r["cost"] for r in by_model[key]))[:MAX_FIT_MODELS]
    models = []
    for model, runtime in ranked:
        group = by_model[(model, runtime)]
        efforts = [e for e in (DEFAULT_EFFORT, *EFFORT_RANK) if any(r["effort"] == e for r in group)]
        rows, verdicts = [], []
        for name, members in COMPLEXITY_GROUPS:
            cells = []
            for effort in efforts:
                cell = [r for r in group if r["complexity"] in members and r["effort"] == effort]
                spend = sum(r["cost"] for r in cell)
                # Unpriced requests add nothing, so a cell with no spend has no known cost rather than $0.
                cells.append({"effort": effort, "requests": len(cell), "spend": round(spend, 6),
                              "cost_per_request": spend / len(cell) if spend > 0 else None,
                              "rework": _request_rework(cell)})
            rows.append({"complexity": name, "cells": cells})
            verdicts += _effort_verdicts(name, [cell for cell in cells if cell["effort"] != DEFAULT_EFFORT])
        models.append({"model": model, "runtime": runtime, "efforts": efforts, "rows": rows,
                       "spend": round(sum(r["cost"] for r in group), 6), "verdicts": verdicts})
    return {"models": models, "unrecorded_spend": round(sum(unrecorded.values()), 6),
            "unrecorded_models": len(unrecorded), "margin": SAFE_LOWER_MARGIN}


def _effort_verdicts(complexity, cells):
    """Safe-to-lower and worth-raising calls between neighbouring levels with enough labeled follow-ups."""
    verdicts = []
    def known(cell):
        return cell["rework"] and not cell["rework"]["few_samples"] and cell["cost_per_request"]

    # Only levels next to each other among the model's own levels; a thin level in between means no call.
    for low, high in zip(cells, cells[1:]):
        if not (known(low) and known(high)):
            continue
        diff, spread, z = _two_proportion(low["rework"], high["rework"])
        base = {"complexity": complexity, "lower": low["effort"], "higher": high["effort"],
                "lower_rate": low["rework"]["rate"], "higher_rate": high["rework"]["rate"],
                "lower_samples": low["rework"]["samples"], "higher_samples": high["rework"]["samples"]}
        if z > VERDICT_Z:
            verdicts.append({"kind": "raise", **base})
        elif diff + spread < SAFE_LOWER_MARGIN and low["cost_per_request"] < high["cost_per_request"]:
            gap = high["cost_per_request"] - low["cost_per_request"]
            verdicts.append({"kind": "lower", **base, "saving_per_request": gap,
                             "saving": round(gap * high["requests"], 6)})
    return verdicts


def _flagged_spend(requests, cells, effort):
    """Spend on requests in any mismatched cell, counting a request once even when tier and effort both flag it."""
    flagged_tiers = {(c["complexity"], c["tier"]) for c in cells if c["flag"]}
    flagged_efforts = {(c["complexity"], c["effort"]) for c in (effort or {}).get("cells", []) if c["flag"]}
    labeled = [r for r in requests if r["complexity"] in COMPLEXITY_OF]
    hit = [r for r in labeled
           if (COMPLEXITY_OF[r["complexity"]], r["tier"]) in flagged_tiers
           or (COMPLEXITY_OF[r["complexity"]], r["effort"]) in flagged_efforts]
    spend, total = sum(r["cost"] for r in hit), sum(r["cost"] for r in labeled)
    return {"requests": len(hit), "sessions": len({id(r["session"]) for r in hit}), "spend": round(spend, 6),
            "labeled_spend": round(total, 6), "share": spend / total if total else None}


def _opportunities(cells, effort, tier_prices):
    """Flagged right-sizing cells as one ranked list, with a savings estimate where prices allow."""
    rows = []
    ratio = (tier_prices["standard"] / tier_prices["premium"]
             if tier_prices.get("premium") and tier_prices.get("standard") else None)
    for cell in cells:
        if cell["flag"] == "possible_overspend":
            rows.append({"kind": "premium_routine", "complexity": cell["complexity"], "tier": cell["tier"],
                         "requests": cell["requests"], "sessions": cell["sessions"], "spend": cell["spend"],
                         "estimate": round(cell["spend"] * ratio, 6) if ratio else None})
        elif cell["flag"] == "possible_false_economy":
            rows.append({"kind": "light_complex", "complexity": cell["complexity"], "tier": cell["tier"],
                         "requests": cell["requests"], "sessions": cell["sessions"], "spend": cell["spend"],
                         "rework": cell["rework"],
                         "estimate": None})
    for cell in (effort or {}).get("cells", []):
        if cell["flag"] == "possible_overthinking":
            rows.append({"kind": "effort_routine", "complexity": cell["complexity"], "effort": cell["effort"],
                         "requests": cell["requests"], "sessions": cell["sessions"], "spend": cell["spend"],
                         "estimate": None})
    return sorted(rows, key=lambda row: -row["spend"])


TAG_ORDER = ("marathon", "long_thread", "big_spend", "team", "overkill", "underpowered", "rescued", "stuck",
             "one_shot")
LONG_THREAD_TURNS = 30
MIN_TAG_POPULATION = 10
MARATHON_FLOOR_S = 3_600
TEAM_MIN_CHILDREN = 3
OVERKILL_SHARE = 0.5


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


def _overkill(r):
    return r["complexity"] == "routine" and (r["tier"] == "premium" or r["effort"] in HIGH_EFFORTS)


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
    requests = s.get("requests") or []
    total = sum(r["cost"] for r in requests)
    routine_heavy = sum(r["cost"] for r in requests if _overkill(r))
    if (total > 0 and routine_heavy > OVERKILL_SHARE * total) or (
            total <= 0 and requests and sum(1 for r in requests if _overkill(r)) * 2 > len(requests)):
        tags.append("overkill")
    if any(t["complexity"] in ("complex", "high_impact") and t["tier"] == "light" and t["corrections"] > 0
           for t in s.get("tasks") or ()):
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
            item["judged"] += outcome in JUDGED
            item["resolved"] += outcome in RESOLVED
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
MIN_RECOMMENDATION_REQUESTS = 3
# One long session can hold many tasks with correlated follow-ups; evidence must also span sessions.
MIN_SWITCH_SESSIONS = 3
MIN_BASELINE_SESSIONS = 5
MIN_FAMILY_SESSIONS = 3
MIN_FIT_SESSIONS = 3
MIN_RECOMMENDATION_SESSIONS = 2


def _sessions_in(items):
    return len({id(item["session"]) for item in items})


def _pushback_not_worse(candidate, current):
    """Pushback within five points of the current model's, when both have labeled follow-ups."""
    if not candidate or not current:
        return candidate is None or current is None
    return candidate["rate"] <= current["rate"] + SWITCH_RATE_SLACK


def _task_stats(group):
    judged = [t for t in group if t["outcome"] in JUDGED]
    resolved = [t for t in group if t["outcome"] in RESOLVED]
    return judged, resolved


def _switch_recommendations(tasks, tiers, prices, requests=()):
    """Per kind of work and complexity: a model that resolves about as often for much less per resolved task.

    Comparing within one complexity level keeps a cheaper model that only saw easier requests from looking better.
    """
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for t in tasks:
        level = COMPLEXITY_OF.get(t["complexity"])
        if t["work_type"] and t["work_type"] != "unclear" and t["model"] and level:
            groups[(t["work_type"], level)][(t["model"], t["runtime"])].append(t)
    out = []
    for work_type, level in ((w, c) for w in WORK_TYPE_ORDER for c, _members in COMPLEXITY_GROUPS):
        stats = []
        for (model, runtime), group in groups.get((work_type, level), {}).items():
            judged, resolved = _task_stats(group)
            if len(judged) < MIN_SWITCH_JUDGED or _sessions_in(judged) < MIN_SWITCH_SESSIONS or not resolved:
                continue
            stats.append({"model": model, "runtime": runtime, "tasks": len(group), "judged": len(judged),
                          "sessions": _sessions_in(judged),
                          "pushback": _request_rework([r for r in requests if r["work_type"] == work_type
                                                       and COMPLEXITY_OF.get(r["complexity"]) == level
                                                       and (r["model"], r["runtime"]) == (model, runtime)]),
                          "spend": sum(t["cost"] for t in group), "rate": len(resolved) / len(judged),
                          "resolved": len(resolved), "cost": sum(t["cost"] for t in resolved) / len(resolved)})
        if len(stats) < 2:
            continue
        usual = max(stats, key=lambda x: (x["tasks"], x["spend"]))
        if usual["judged"] < MIN_BASELINE_JUDGED or usual["sessions"] < MIN_BASELINE_SESSIONS:
            continue
        usual_price = prices.get((usual["runtime"], usual["model"]))

        def eligible(x):
            price = prices.get((x["runtime"], x["model"]))
            # A per-token cheaper model is required, so easier tasks alone cannot make a model look cheaper.
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
        tried_harder = any(t["model"] == alt["model"] and t["runtime"] == alt["runtime"]
                           and COMPLEXITY_OF.get(t["complexity"]) in harder for t in tasks)
        out.append({"kind": "switch_model", "work_type": work_type, "complexity": level,
                    "to_untested_harder": bool(harder) and not tried_harder,
                    "from_pushback": usual["pushback"]["rate"] if usual["pushback"] else None,
                    "to_pushback": alt["pushback"]["rate"] if alt["pushback"] else None,
                    "model": usual["model"], "runtime": usual["runtime"],
                    "to_model": alt["model"], "to_runtime": alt["runtime"],
                    "from_cost": usual["cost"], "to_cost": alt["cost"],
                    "from_rate": usual["rate"], "to_rate": alt["rate"],
                    "tasks": usual["tasks"], "spend": round(usual["spend"], 6),
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


def _family_recommendations(tasks, requests, prices):
    """A newer, cheaper version from the same model family and app that resolves about as often for this user."""
    groups = collections.defaultdict(list)
    for t in tasks:
        if t["model"]:
            groups[(t["runtime"], t["model"])].append(t)
    spend_by = collections.Counter()
    for r in requests:
        spend_by[(r["runtime"], r["model"])] += r["cost"]
    stats = {}
    for key, group in groups.items():
        judged, resolved = _task_stats(group)
        if key in prices and len(judged) >= MIN_FAMILY_JUDGED and _sessions_in(judged) >= MIN_FAMILY_SESSIONS:
            stats[key] = {"judged": len(judged), "rate": len(resolved) / len(judged), "tasks": len(group),
                          "sessions": _sessions_in(judged),
                          "spend": spend_by[key] or sum(t["cost"] for t in group), "price": prices[key]}
    out = []
    for (runtime, model), current in stats.items():
        version = model_version(model)
        if current["judged"] < MIN_BASELINE_JUDGED or current["sessions"] < MIN_BASELINE_SESSIONS or not version:
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
                    "from_rate": current["rate"], "to_rate": alt["rate"], "tasks": current["tasks"],
                    "spend": round(current["spend"], 6),
                    "saving": round(current["spend"] * (1 - alt["price"] / current["price"]), 6)})
    return out


def _named_models(requests, limit=MAX_NAMED_MODELS):
    spend = collections.Counter()
    for r in requests:
        if r["model"]:
            spend[(r["model"], r["runtime"])] += r["cost"] or 1e-9
    return [{"model": model, "runtime": runtime} for (model, runtime), _ in spend.most_common(limit)]


def _recommendations(in_window, tasks, requests, opportunities, tiers, prices):
    """Ranked ways to spend less on models; savings are estimates and can overlap between items."""
    recs = [dict(item, saving=round(item["spend"] - item["estimate"], 6) if item["estimate"] is not None else None)
            for item in opportunities if item["requests"] >= MIN_RECOMMENDATION_REQUESTS
            and item["sessions"] >= MIN_RECOMMENDATION_SESSIONS]
    for item in recs:
        if item["kind"] == "premium_routine":
            routine = [r for r in requests if r["complexity"] == "routine"]
            item["from_models"] = _named_models([r for r in routine if r["tier"] == "premium"])
            # One mid-priced model per app the premium routine work ran in, so advice stays within that app.
            item["to_models"] = []
            for runtime in dict.fromkeys(m["runtime"] for m in item["from_models"]):
                choice = _named_models([r for r in requests if r["tier"] == "standard" and r["runtime"] == runtime], 1)
                item["to_models"].extend(choice)
    recs.extend(_switch_recommendations(tasks, tiers, prices, requests))
    recs.extend(_family_recommendations(tasks, requests, prices))
    long = _long_thread_recommendation(in_window)
    if long:
        recs.append(long)
    recs.sort(key=lambda item: (-(item["saving"] or 0), -item["spend"]))
    return recs[:MAX_RECOMMENDATIONS]


MAX_DRILL_SESSIONS = 50
MAX_DRILL_IDS = 2000
DRILL_FILTERS = ("month", "start_month", "area", "work_type", "complexity", "tier", "effort", "outcome",
                 "model", "model_runtime", "tag")
REQUEST_FILTERS = ("area", "work_type", "complexity", "tier", "effort", "model")


def _request_matches(r, filters):
    if filters.get("area") and r["area"] != filters["area"]:
        return False
    if filters.get("work_type") and (r["work_type"] or "") != filters["work_type"]:
        return False
    if filters.get("complexity") and r["complexity"] not in dict(COMPLEXITY_GROUPS).get(filters["complexity"], ()):
        return False
    if filters.get("tier") and r["tier"] != filters["tier"]:
        return False
    if filters.get("effort") and r["effort"] not in filters["effort"].split(","):
        return False
    if filters.get("model") and (r["model"] != filters["model"] or r["runtime"] != filters.get("model_runtime", "")):
        return False
    return True


GOAL_WORK_TYPES = tuple(work_type for work_type in WORK_TYPE_ORDER if _compares_models(work_type))


def goal_daily(rows, labels, key_for, areas, requests_for=None, corrections_for=None):
    """Per (kind of work, provider, model) and request day: labeled follow-ups, pushbacks, human and agent turns.

    Content-free counts for Work goals. Every request counts, so "all kinds of work" matches the Work page's All
    work row; a goal for one kind of work takes only its labeled kind. A request counts as a labeled follow-up
    when the request after it has a pushback label; human and agent turns count only where the runtime split model
    calls by request. The provider and model scope a goal to chosen models, the same way other goals do.
    """
    area_names = [a["name"] for a in areas]
    sessions, _runtimes, _projects = _prepare_sessions(rows, labels, key_for, area_names, {}, "", "", None, "day",
                                                       "", requests_for, corrections_for)
    out = collections.defaultdict(dict)
    for s in sessions:
        provider = str(s["row"].get("provider") or "")
        for r in s["requests"]:
            if not r["day"]:
                continue
            group = (r["work_type"], provider, r["model"])
            bucket = out[group].setdefault(r["day"], {"labeled": 0, "pushback": 0, "human": 0, "agent": 0})
            if r["next_labeled"]:
                bucket["labeled"] += 1
                bucket["pushback"] += bool(r["next_pushback"])
            if r["model_calls"] is not None:
                bucket["human"] += 1
                bucket["agent"] += r["model_calls"]
    return dict(out)


def find_sessions(rows, labels, key_for, areas, output_price, filters, months=6, runtime="", project="",
                  corrections_for=None, pending_keys=None, limit=MAX_DRILL_SESSIONS, ids_only=False, today="",
                  label_since="", requests_for=None):
    """Sessions behind one Work module cell, ranked by the spend that matches; same windowing and labels.

    Request filters (area, kind of work, complexity, tier, effort, model) select the requests that match;
    a session is shown when any of its requests in the window match, with that matching spend.
    ``month`` narrows to requests in that bucket; ``start_month`` to sessions started in it; ``outcome``
    and ``tag`` are session-level.
    """
    area_names = [a["name"] for a in areas]
    tiers = price_tiers(rows, output_price)
    grain, count = _period(months)
    sessions, _runtimes, _projects = _prepare_sessions(
        rows, labels, key_for, area_names, tiers, runtime, project, pending_keys, grain, label_since, requests_for,
        corrections_for)
    all_months = _window(sessions, grain, count, today)
    window = set(all_months)
    month = filters.get("month") or ""
    # Spend counts what happened in the window, or in the one bucket a ``month`` drill names.
    _window_costs(sessions, grain, {month} if month and month in window else window)
    tag_context = _tag_context([s for s in sessions if s["start"] in window])
    start_month = filters.get("start_month") or ""
    request_filtered = any(filters.get(name) for name in REQUEST_FILTERS)
    matched = []
    for s in sessions:
        if start_month:
            if start_month not in all_months or s["start"] != start_month:
                continue
            for r in s["requests"]:
                r["cost"] = sum(r["day_costs"].values())
            in_scope = s["requests"]
        elif month:
            if month not in all_months:
                continue
            in_scope = [r for r in s["requests"] if r["in_window"]]
        else:
            in_scope = [r for r in s["requests"] if r["in_window"]]
            if not request_filtered and s["start"] not in window:
                in_scope = []
        hits = [r for r in in_scope if _request_matches(r, filters)]
        if not hits:
            continue
        s["match_spend"] = sum(r["cost"] for r in hits)
        s["match_requests"] = len(hits)
        matched.append(s)
    _attach_sequences(matched, corrections_for)
    if filters.get("outcome"):
        matched = [s for s in matched if _outcome(s) == filters["outcome"]]
    for s in matched:
        s["tags"] = session_tags(s, tag_context, _outcome(s))
    if filters.get("tag"):
        matched = [s for s in matched if filters["tag"] in s["tags"]]
    matched.sort(key=lambda s: (-s["match_spend"], -_cost(s), s["row"].get("last") or ""))
    spend = round(sum(s["match_spend"] for s in matched), 6)
    if ids_only:
        return {"total": len(matched), "spend": spend,
                "truncated": len(matched) > MAX_DRILL_IDS,
                "keys": list(dict.fromkeys(_trace_key(s["row"]) for s in matched))[:MAX_DRILL_IDS]}
    return {
        "total": len(matched),
        "spend": spend,
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
            "match_cost": round(s["match_spend"], 6),
            "match_requests": s["match_requests"],
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


def live_session_hints(rows, current, labels, key_for, output_price, corrections_for=None, requests_for=None):
    """Suggestions for running sessions, keyed by trace key; content-free and bounded.

    Right-sizing checks for one session: routine work on a premium model or at high effort when it is most of
    the spend of requests with their own label, complex work on a light model (latest labeled request) that has
    had pushback in this session, the last two labeled follow-ups both pushback, and 30+ requests.
    Model-switch advice that needs outcome evidence stays on the Work page.
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
        runtime = row.get("runtime") or ""
        entry = labels.get(key_for(work_identity(row))) or {}
        turns = len(turn_days(row))
        own = requests_for(work_identity(row), turns) if requests_for is not None and turns else []
        # The latest labeled request, with the model and effort it ran on (not a newer unlabeled request's).
        index = next((i for i in range(len(own or []) - 1, -1, -1) if own[i].get("complexity")), None)
        complexity = (own[index] if index is not None else entry).get("complexity") or ""
        slices = row.get("_work_requests") or []
        current = slices[index if index is not None else -1] if slices and len(slices) == turns else {}
        model = (current.get("model") or primary_model(row))[:MAX_HINT_MODEL]
        tier = tiers.get((runtime, model))
        hints = []
        # One short routine follow-up is not routine work: the routine hints need most of the labeled spend.
        start = (row.get("start") or "")[:10]
        requests = [r for r in _session_requests(row, entry, own or [], [d or start for d in turn_days(row)], (),
                                                 tiers, PENDING, None) if r["complexity"]]
        if any(r["labeled"] for r in requests):
            # Only requests with their own label: one not labeled yet would inherit the label before it.
            requests = [r for r in requests if r["labeled"]]
        labeled_spend = sum(r["cost"] or 1e-9 for r in requests)
        premium = [r for r in requests if r["complexity"] == "routine" and r["tier"] == "premium"]
        if premium and sum(r["cost"] or 1e-9 for r in premium) * 2 > labeled_spend:
            hints.append({"kind": "premium_routine", "model": _dominant(premium, "model")[:MAX_HINT_MODEL],
                          "to_model": (standard.get(runtime) or "")[:MAX_HINT_MODEL]})
        high = [r for r in requests if r["complexity"] == "routine" and r["effort"] in HIGH_EFFORTS]
        if high and sum(r["cost"] or 1e-9 for r in high) * 2 > labeled_spend:
            hints.append({"kind": "effort_routine", "effort": _dominant(high, "effort")})
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
        to = hint["to_model"] or "a mid-priced model"
        return {"title": "Mostly routine work on a premium model",
                "detail": f"Most of this session's spend is routine requests. Next time, start work like this on {to}."}
    if kind == "effort_routine":
        return {"title": "Mostly routine work at high effort",
                "detail": f"Most of this session's spend is routine requests at {hint['effort']} effort. "
                          "Next time, try medium for work like this."}
    return {"title": "Long session",
            "detail": f"{hint['turns']} requests so far, and each re-sends the conversation. "
                      "A fresh session with a short summary costs less."}
