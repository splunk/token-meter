"""Local, bounded goal records and deterministic projections of session summaries."""

import datetime
import hashlib
import math
import re
import uuid

from token_meter.models.tiers import (
    TIER_CATALOG_VERSION, TIER_IDS, model_tier, model_tier_key,
    public_model_label, tier_override_allowed,
)


GOAL_TYPES = {"objective", "model_mix", "daily_spend", "model_spend"}
AGENTS = {"claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes"}


def session_key(row):
    provider = str(row.get("provider") or "")
    identifier = str(row.get("id") or "")
    return f"{provider}:{hashlib.sha256((provider + ':' + identifier).encode()).hexdigest()}"


def _text(value, label, limit, required=True, multiline=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if (required and not value) or len(value) > limit or any(ord(char) < 32 and not (multiline and char in "\r\n") for char in value):
        raise ValueError(f"{label} must be 1–{limit} printable characters." if required else f"{label} is too long or contains control characters.")
    return value


def _integer(value, label, maximum):
    number = _number(value, label, maximum=maximum)
    if not number.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return int(number)


def _date(value, label):
    value = _text(value, label, 10)
    try:
        if datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValueError(f"{label} must be a YYYY-MM-DD date.") from None
    return value


def _number(value, label, *, positive=True, maximum=1000000):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(number) or number > maximum or (number <= 0 if positive else number < 0):
        raise ValueError(f"{label} is outside the allowed range.")
    return number


def _agent(value):
    if value not in AGENTS:
        raise ValueError("Choose a supported agent.")
    return value


def normalize_goal(value, existing=None):
    if not isinstance(value, dict):
        raise ValueError("A goal record is required.")
    kind = value.get("type")
    if kind not in GOAL_TYPES:
        raise ValueError("Choose a supported goal type.")
    title = _text(value.get("title"), "Goal title", 100)
    goal = {"id": existing["id"] if existing else uuid.uuid4().hex,
            "type": kind, "title": title,
            "created_at": existing["created_at"] if existing else datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "ended_at": _date(existing["ended_at"], "Ended") if existing and existing.get("ended_at") else None}
    if kind == "objective":
        finish_mode = value.get("finish_mode") or "manual"
        if finish_mode not in ("manual", "end_date"):
            raise ValueError("Choose how this goal finishes.")
        start = _date(value.get("start"), "Start") if finish_mode == "end_date" else None
        end = _date(value.get("deadline"), "End") if finish_mode == "end_date" else None
        if start and end and start > end:
            raise ValueError("Start must be on or before end.")
        agents = value.get("agents")
        if not isinstance(agents, list) or not agents or len(agents) > len(AGENTS):
            raise ValueError("Choose at least one agent.")
        goal.update({
            "agents": sorted({_agent(agent) for agent in agents}),
            "priority_label": _text(value.get("priority_label") or "", "Business priority", 60, required=False),
            "outcome": _text(value.get("outcome") or "", "Legacy goal description", 500, required=False, multiline=True),
            "start": start, "deadline": end,
            "finish_mode": finish_mode,
            "cap_usd": _number(value.get("cap_usd"), "Spend cap"),
            "completed": finish_mode == "manual" and value.get("completed") is True,
            "links_confirmed": finish_mode == "manual" and value.get("links_confirmed") is True,
            "completed_at": ((existing.get("completed_at") or datetime.date.today().isoformat())
                             if finish_mode == "manual" and value.get("completed") is True else None),
            "links_confirmed_at": ((existing.get("links_confirmed_at") or datetime.date.today().isoformat())
                                   if finish_mode == "manual" and value.get("links_confirmed") is True else None),
        })
    elif kind in ("daily_spend", "model_spend"):
        start, end = _date(value.get("start"), "Start"), _date(value.get("deadline"), "End")
        if start > end:
            raise ValueError("Start must be on or before end.")
        goal.update({"agent": _agent(value.get("agent")), "start": start, "deadline": end,
                     "cap_usd": _number(value.get("cap_usd"), "Spend limit")})
        if kind == "model_spend":
            model_key = value.get("model_key")
            if not isinstance(model_key, str) or not re.fullmatch(r"[0-9a-f]{64}", model_key):
                raise ValueError("Choose an observed model for this agent.")
            goal["model_key"] = model_key
    elif kind == "model_mix":
        start, end = _date(value.get("start"), "Start"), _date(value.get("deadline"), "Deadline")
        if start > end:
            raise ValueError("Start must be on or before deadline.")
        mode = value.get("mode") or "minimum_models"
        if mode not in ("minimum_models", "target_shares", "tier_shares"):
            raise ValueError("Choose a Model Mix target mode.")
        shares = value.get("target_shares") or {}
        if mode == "target_shares":
            if not isinstance(shares, dict) or not shares or len(shares) > 8:
                raise ValueError("Choose 1–8 model share targets.")
            shares = {_text(model, "Model", 120): _number(target, "Model share", maximum=100)
                      for model, target in shares.items()}
            if sum(shares.values()) > 100:
                raise ValueError("Model share targets cannot total more than 100%.")
        else:
            shares = {}
        if mode == "tier_shares":
            targets = value.get("tier_targets")
            if not isinstance(targets, dict) or set(targets) != set(TIER_IDS):
                raise ValueError("Enter a target for each model tier.")
            targets = {tier: _number(targets[tier], f"{tier.replace('_', '-')} target",
                                     positive=False, maximum=100) for tier in TIER_IDS}
            if not math.isclose(sum(targets.values()), 100, abs_tol=0.000001):
                raise ValueError("Model tier targets must total 100%.")
            version = value.get("tier_catalog_version", TIER_CATALOG_VERSION)
            if type(version) is not int or version != TIER_CATALOG_VERSION:
                raise ValueError("This model tier catalog version is unavailable.")
            overrides = value.get("tier_overrides") or {}
            if not isinstance(overrides, dict) or len(overrides) > 100:
                raise ValueError("Too many model tier assignments.")
            if any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
                   or tier not in TIER_IDS for key, tier in overrides.items()):
                raise ValueError("Choose a valid model tier assignment.")
            goal.update({
                "tier_targets": targets,
                "tolerance_pp": _number(value.get("tolerance_pp"), "Model mix tolerance",
                                        positive=False, maximum=20),
                "workload_note": _text(value.get("workload_note") or "", "Workload note", 200,
                                       required=False, multiline=True),
                "tier_catalog_version": version,
                "tier_overrides": dict(overrides),
            })
        goal.update({"agent": _agent(value.get("agent")), "start": start, "deadline": end,
                     "mode": mode, "target_shares": shares,
                     "minimum_models": _integer(value.get("minimum_models"), "Model count", 100) if mode == "minimum_models" else None,
                     "minimum_share": _number(value.get("minimum_share"), "Minimum share", maximum=100) if mode == "minimum_models" else None})
    return goal


def normalize_store(raw):
    if not isinstance(raw, dict):
        return {"items": [], "links": {}, "labels": {}}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    valid_items = []
    for item in items[:100]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or len(item["id"]) != 32:
            continue
        try:
            valid_items.append(normalize_goal(item, item))
        except (KeyError, TypeError, ValueError):
            continue
    return {"items": valid_items,
            "links": {str(key): value for key, value in list(links.items())[:500]
                      if isinstance(key, str) and len(key) <= 260 and isinstance(value, str)},
            "labels": {key: value for key, value in list(labels.items())[:500]
                       if isinstance(key, str) and len(key) <= 260 and isinstance(value, str)
                       and len(value) <= 120 and all(ord(char) >= 32 for char in value)}}


def apply_action(store, action, sessions):
    """Return a new record after a validated user action; never store trace text."""
    store = normalize_store(store)
    items = [dict(item) for item in store["items"]]
    links = dict(store["links"])
    labels = dict(store["labels"])
    operation = action.get("operation")
    goal_id = action.get("id")
    old = next((item for item in items if item.get("id") == goal_id), None)
    if operation == "save":
        if goal_id and old is None:
            raise ValueError("Goal was not found.")
        if old is None and len(items) >= 100:
            raise ValueError("The goal limit has been reached.")
        record = normalize_goal(action.get("goal"), old)
        if record["type"] == "model_spend":
            observed_keys = {model_tier_key(record["agent"], model)
                             for row in sessions.values() if row.get("provider") == record["agent"]
                             for model in [*(row.get("models") or []),
                                           *(stat.get("model") for stat in row.get("model_stats") or []),
                                           *(stat.get("model") for stat in row.get("_model_daily") or [])]
                             if tier_override_allowed(model)}
            preserved = bool(old and old.get("type") == "model_spend"
                             and old.get("agent") == record["agent"]
                             and old.get("model_key") == record["model_key"])
            if record["model_key"] not in observed_keys and not preserved:
                raise ValueError("Choose an observed model for this agent.")
        if record["type"] == "model_mix":
            observed = {model for row in sessions.values() if row.get("provider") == record["agent"]
                        for model in [*(row.get("models") or []),
                                      *(stat.get("model") for stat in row.get("_model_daily") or [])]
                        if model and not str(model).lower().startswith("unknown")}
            if not set(record["target_shares"]).issubset(observed):
                raise ValueError("Choose models observed with this agent.")
            if record["mode"] == "tier_shares":
                observed_keys = {model_tier_key(record["agent"], model) for model in observed
                                 if tier_override_allowed(model) and model_tier(model) is None}
                preserved = (set(old.get("tier_overrides") or {}) if old and
                             old.get("agent") == record["agent"] else set())
                if not set(record["tier_overrides"]).issubset(observed_keys | preserved):
                    raise ValueError("Choose observed, unclassified models for tier assignments.")
        if old and old.get("type") != record["type"]:
            raise ValueError("Goal type cannot be changed.")
        if old:
            if record["type"] == "objective":
                if old.get("outcome") != record["outcome"]:
                    record["completed"] = False
                    record["completed_at"] = None
                if (record.get("start") and record.get("completed_at")
                        and record["completed_at"] < record["start"]):
                    record["completed"] = False
                    record["completed_at"] = None
                if (record.get("start") and record.get("links_confirmed_at")
                        and record["links_confirmed_at"] < record["start"]):
                    record["links_confirmed"] = False
                    record["links_confirmed_at"] = None
                if old.get("agents") != record["agents"]:
                    record["links_confirmed"] = False
                    record["links_confirmed_at"] = None
            items[items.index(old)] = record
        else:
            items.append(record)
        if "session_keys" in action:
            if record["type"] != "objective":
                raise ValueError("Only business objectives can link sessions.")
            requested = action["session_keys"]
            if not isinstance(requested, list) or len(requested) > 500:
                raise ValueError("Choose no more than 500 sessions.")
            requested = [_text(key, "Session key", 260) for key in requested]
            if len(set(requested)) != len(requested):
                raise ValueError("A session can only be selected once.")
            selected = set(requested)
            target_id = record["id"]
            before = {key for key, linked in links.items() if linked == target_id}
            for key in selected:
                session = sessions.get(key)
                if session is None and key in before:
                    continue  # Preserve an existing link whose trace is temporarily unavailable.
                if session is None or session.get("provider") not in record["agents"]:
                    raise ValueError("Choose sessions from the objective's selected agents.")
            purposes = action.get("session_purposes") or {}
            if not isinstance(purposes, dict) or not set(purposes).issubset(selected):
                raise ValueError("Session purposes must match selected sessions.")
            clean_purposes = {key: _text(label, "Session purpose", 120, required=False)
                              for key, label in purposes.items()}
            if len(links) - len(before - selected) + sum(key not in links for key in selected) > 500:
                raise ValueError("The session link limit has been reached.")
            for key in before - selected:
                links.pop(key, None)
                labels.pop(key, None)
            for key in selected:
                previous = links.get(key)
                if previous != target_id:
                    links[key] = target_id
                    if previous:
                        for item in items:
                            if item["id"] == previous and item["type"] == "objective":
                                item["links_confirmed"] = False
                                item["links_confirmed_at"] = None
                if key in clean_purposes:
                    if clean_purposes[key]:
                        labels[key] = clean_purposes[key]
                    else:
                        labels.pop(key, None)
            if before != selected:
                record["links_confirmed"] = False
                record["links_confirmed_at"] = None
        if record["type"] == "objective":
            invalid = [key for key, linked in links.items() if linked == record["id"]
                       and sessions.get(key) is not None
                       and sessions[key].get("provider") not in record["agents"]]
            if invalid:
                raise ValueError("Remove sessions from excluded agents before editing this objective.")
    elif operation == "end":
        if not old:
            raise ValueError("Goal was not found.")
        old["ended_at"] = old.get("ended_at") or datetime.date.today().isoformat()
    elif operation == "reopen":
        if not old:
            raise ValueError("Goal was not found.")
        old["ended_at"] = None
        if old["type"] == "objective":
            old["completed"] = False
            old["completed_at"] = None
            old["links_confirmed"] = False
            old["links_confirmed_at"] = None
    elif operation == "delete":
        if not old:
            raise ValueError("Goal was not found.")
        items.remove(old)
        links = {key: linked for key, linked in links.items() if linked != goal_id}
        labels = {key: label for key, label in labels.items() if key in links}
    elif operation == "link":
        key = _text(action.get("session_key"), "Session key", 260)
        session = sessions.get(key)
        if not session:
            raise ValueError("Session was not found in the current local inventory.")
        previous = links.get(key)
        if goal_id in (None, ""):
            links.pop(key, None)
        elif not old or old.get("type") != "objective" or session.get("provider") not in old.get("agents", []):
            raise ValueError("Choose an objective that includes this session's agent.")
        else:
            if key not in links and len(links) >= 500:
                raise ValueError("The session link limit has been reached.")
            links[key] = goal_id
        if previous != links.get(key):
            if key not in links:
                labels.pop(key, None)
            for item in items:
                if item.get("id") in (previous, links.get(key)) and item.get("type") == "objective":
                    item["links_confirmed"] = False
                    item["links_confirmed_at"] = None
    elif operation == "label":
        key = _text(action.get("session_key"), "Session key", 260)
        if key not in sessions or key not in links:
            raise ValueError("Link the session to an objective before naming its purpose.")
        label = _text(action.get("label", ""), "Session purpose", 120, required=False)
        if label:
            labels[key] = label
        else:
            labels.pop(key, None)
    else:
        raise ValueError("Unsupported goal action.")
    return {"items": items, "links": links, "labels": labels}


def _tier_mix_metrics(item, rows, current_keys):
    """Use dated model executions, retaining gaps as unavailable evidence."""
    counts = {tier: 0 for tier in TIER_IDS}
    model_counts = {}
    unclassified = 0
    assignable_unclassified = 0
    undated = 0
    active = 0
    for row in rows:
        if row.get("provider") != item["agent"]:
            continue
        start_day = str(row.get("start") or row.get("last") or "")[:10]
        last_day = str(row.get("last") or row.get("start") or "")[:10]
        overlaps = bool(start_day and last_day and start_day <= item["deadline"]
                        and last_day >= item["start"])
        daily_total = 0
        in_window = False
        for daily in row.get("_model_daily") or []:
            executions = max(0, int(daily.get("executions") or 0))
            daily_total += executions
            day = str(daily.get("day") or "")
            if executions and overlaps and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                undated += executions
            if not (item["start"] <= day <= item["deadline"]) or not executions:
                continue
            in_window = True
            model = str(daily.get("model") or "").strip()
            key = model_tier_key(item["agent"], model)
            override = (item.get("tier_overrides") or {}).get(key) if tier_override_allowed(model) else None
            tier = override or model_tier(model, item["tier_catalog_version"])
            if tier in counts:
                counts[tier] += executions
            else:
                unclassified += executions
                if tier_override_allowed(model):
                    assignable_unclassified += executions
            model_row = model_counts.setdefault(model, {"model": public_model_label(model),
                                                       "tier": tier or "unclassified",
                                                       "source": "user" if override else "catalog" if tier else "unclassified",
                                                       "executions": 0})
            model_row["executions"] += executions
        if overlaps:
            total = max(int(row.get("turns") or 0), sum(max(0, int(stat.get("executions") or 0))
                                                       for stat in row.get("model_stats") or []))
            undated += max(0, total - daily_total)
        if (in_window or overlaps) and session_key(row) in current_keys:
            active += 1
    denominator = sum(counts.values()) + unclassified
    actual = {tier: counts[tier] / denominator * 100 if denominator else None for tier in TIER_IDS}
    deviation = {tier: abs(actual[tier] - item["tier_targets"][tier]) if denominator else None
                 for tier in TIER_IDS}
    target_met = bool(denominator and not unclassified and not undated
                      and all(value <= item["tolerance_pp"] + 0.000001 for value in deviation.values()))
    today = datetime.date.today().isoformat()
    status = ("ended" if item.get("ended_at") else
              "in progress" if today <= item["deadline"] and (denominator or undated) else
              "awaiting evidence" if today <= item["deadline"] or not denominator else
              "incomplete evidence" if undated or unclassified > assignable_unclassified else
              "needs classification" if unclassified else
              "awaiting completion" if active else
              "met" if target_met else "missed target")
    return {"executions": denominator, "tier_counts": counts, "tier_shares": actual,
            "tier_deviation_pp": deviation, "unclassified_executions": unclassified,
            "assignable_unclassified_executions": assignable_unclassified,
            "undated_executions": undated, "classified_executions": denominator - unclassified,
            "active_sessions": active, "target_met": target_met, "status": status,
            "model_breakdown": sorted(model_counts.values(), key=lambda value: (-value["executions"], value["model"]))[:500]}


def _daily_spend_metrics(item, rows, current_keys, metric_available):
    """Measure the highest observed local-calendar-day cost for one agent."""
    days = {}
    missing_cost_sessions = 0
    undated_cost_sessions = 0
    active = 0
    estimated = False
    relevant_sessions = 0
    for row in rows:
        if row.get("provider") != item["agent"]:
            continue
        start_day = str(row.get("start") or row.get("last") or "")[:10]
        last_day = str(row.get("last") or row.get("start") or "")[:10]
        overlaps = bool(start_day and last_day and start_day <= item["deadline"]
                        and last_day >= item["start"])
        costs = row.get("_day_cost") or {}
        in_window = {str(day): max(0.0, float(cost or 0)) for day, cost in costs.items()
                     if item["start"] <= str(day) <= item["deadline"]}
        if not overlaps and not in_window:
            continue
        relevant_sessions += 1
        if session_key(row) in current_keys:
            active += 1
        for day, cost in in_window.items():
            days[day] = days.get(day, 0.0) + cost
        if not metric_available(row, "cost"):
            missing_cost_sessions += 1
        estimated = estimated or bool(row.get("cost_approx"))
        dated_total = sum(max(0.0, float(cost or 0)) for cost in costs.values())
        session_total = max(0.0, float(row.get("cost") or 0))
        dated_executions = sum(max(0, int(daily.get("executions") or 0))
                               for daily in row.get("_model_daily") or [])
        if (session_total > dated_total + 0.01
                or int(row.get("turns") or 0) > dated_executions):
            undated_cost_sessions += 1
    highest = max(days.values()) if days else None
    breach = highest is not None and highest >= item["cap_usd"]
    today = datetime.date.today().isoformat()
    status = ("over cap" if breach else "ended" if item.get("ended_at") else
              "in progress" if today <= item["deadline"] and relevant_sessions else
              "awaiting evidence" if today <= item["deadline"] or not relevant_sessions else
              "incomplete evidence" if missing_cost_sessions or undated_cost_sessions else
              "awaiting completion" if active else
              "met" if days else "awaiting evidence")
    return {"highest_daily_spend": highest, "observed_days": len(days),
            "relevant_sessions": relevant_sessions, "missing_cost_sessions": missing_cost_sessions,
            "undated_cost_sessions": undated_cost_sessions, "active_sessions": active,
            "cost_basis": "estimated" if estimated else "reported",
            "status": status,
            "daily_spend": [{"day": day, "cost": days[day]}
                            for day in sorted(days, reverse=True)[:100]],
            "daily_spend_scope": "most recent 100 observed days; use chart windows for the full selected period"}


def _model_spend_metrics(item, rows, current_keys, metric_available):
    """Measure dated spend for one exact agent-scoped observed model."""
    spend = 0.0
    executions = 0
    missing_cost_executions = 0
    undated_executions = 0
    active = 0
    estimated = False
    model_label = "Model unavailable in current inventory"
    for row in rows:
        if row.get("provider") != item["agent"]:
            continue
        selected_daily = [daily for daily in row.get("_model_daily") or []
                          if model_tier_key(item["agent"], daily.get("model") or "") == item["model_key"]]
        selected_stats = [stat for stat in row.get("model_stats") or []
                          if model_tier_key(item["agent"], stat.get("model") or "") == item["model_key"]]
        for model in [*(row.get("models") or []),
                      *(stat.get("model") for stat in selected_stats),
                      *(daily.get("model") for daily in selected_daily)]:
            if model and model_tier_key(item["agent"], model) == item["model_key"]:
                model_label = public_model_label(model)
                break
        start_day = str(row.get("start") or row.get("last") or "")[:10]
        last_day = str(row.get("last") or row.get("start") or "")[:10]
        overlaps = bool(start_day and last_day and start_day <= item["deadline"]
                        and last_day >= item["start"])
        in_window = [daily for daily in selected_daily
                     if item["start"] <= str(daily.get("day") or "") <= item["deadline"]]
        if (in_window or selected_stats and overlaps) and session_key(row) in current_keys:
            active += 1
        for daily in in_window:
            count = max(0, int(daily.get("executions") or 0))
            executions += count
            covered = (min(count, max(0, int(daily["cost_covered_executions"])))
                       if "cost_covered_executions" in daily
                       else count if metric_available(row, "cost") else 0)
            missing_cost_executions += count - covered
            spend += max(0.0, float(daily.get("cost") or 0))
            estimated = estimated or bool(row.get("cost_approx"))
        if selected_stats and overlaps:
            total = sum(max(0, int(stat.get("executions") or 0)) for stat in selected_stats)
            dated = sum(max(0, int(daily.get("executions") or 0)) for daily in selected_daily)
            undated_executions += max(0, total - dated)
    breach = spend >= item["cap_usd"]
    today = datetime.date.today().isoformat()
    status = ("over cap" if breach else "ended" if item.get("ended_at") else
              "in progress" if today <= item["deadline"] and (executions or undated_executions) else
              "awaiting evidence" if today <= item["deadline"] else
              "incomplete evidence" if missing_cost_executions or undated_executions else
              "awaiting evidence" if not executions else
              "awaiting completion" if active else "met")
    return {"observed_spend": spend, "executions": executions,
            "cost_covered_executions": executions - missing_cost_executions,
            "missing_cost_executions": missing_cost_executions,
            "undated_executions": undated_executions, "active_sessions": active,
            "model_label": model_label, "cost_basis": "estimated" if estimated else "reported",
            "status": status}


def _spend_chart(item, rows, metric_available, offset):
    """Return one bounded, dated chart window without changing full-period scoring."""
    start = datetime.date.fromisoformat(item["start"])
    end = min(datetime.date.fromisoformat(item["deadline"]), datetime.date.today())
    if end < start:
        return {"days": [], "has_previous": False, "has_next": False}
    offset = min(max(0, offset), 1000)
    window_end = end - datetime.timedelta(days=offset * 30)
    if window_end < start:
        window_end = start
    window_start = max(start, window_end - datetime.timedelta(days=29))
    amounts = {}
    covered = {}
    for row in rows:
        if row.get("provider") != item["agent"]:
            continue
        if item["type"] == "daily_spend":
            for day, value in (row.get("_day_cost") or {}).items():
                if item["start"] <= str(day) <= item["deadline"]:
                    amounts[day] = amounts.get(day, 0.0) + max(0.0, float(value or 0))
                    covered[day] = covered.get(day, True) and bool(metric_available(row, "cost"))
        else:
            for daily in row.get("_model_daily") or []:
                if model_tier_key(item["agent"], daily.get("model") or "") != item["model_key"]:
                    continue
                day = str(daily.get("day") or "")
                if not item["start"] <= day <= item["deadline"]:
                    continue
                amounts[day] = amounts.get(day, 0.0) + max(0.0, float(daily.get("cost") or 0))
                count = max(0, int(daily.get("executions") or 0))
                cost_covered = (min(count, max(0, int(daily["cost_covered_executions"])))
                                if "cost_covered_executions" in daily else
                                count if metric_available(row, "cost") else 0)
                covered[day] = covered.get(day, True) and cost_covered == count
    prior = sum(value for day, value in amounts.items() if day < window_start.isoformat())
    days = []
    day = window_start
    while day <= window_end:
        key = day.isoformat()
        value = amounts.get(key)
        if item["type"] == "model_spend" and value is not None:
            prior += value
        days.append({"day": key, "cost": value,
                     "cumulative": prior if item["type"] == "model_spend" and value is not None else None,
                     "covered": covered.get(key, False) if value is not None else False})
        day += datetime.timedelta(days=1)
    return {"days": days, "start": window_start.isoformat(), "end": window_end.isoformat(),
            "has_previous": window_start > start, "has_next": window_end < end,
            "offset": offset, "window_days": 30}


def project(store, rows, current_keys, budget, metric_available, query="", chart_goal="", chart_offset=0):
    store = normalize_store(store)
    rows = list(rows)
    sessions = {session_key(row): row for row in rows if row.get("id") and row.get("provider") in AGENTS}
    goals = []
    for item in store["items"]:
        goal = dict(item)
        if item["type"] == "objective":
            linked = [row for key, row in sessions.items() if store["links"].get(key) == item["id"]]
            missing = sum(1 for key, linked_id in store["links"].items() if linked_id == item["id"] and key not in sessions)
            cost = sum(max(0, float(row.get("cost") or 0)) for row in linked)
            cost_covered = sum(bool(metric_available(row, "cost")) for row in linked)
            active = sum(session_key(row) in current_keys for row in linked)
            agent_cost = {agent: sum(max(0, float(row.get("cost") or 0)) for row in linked if row.get("provider") == agent)
                          for agent in item.get("agents", [])}
            complete = bool(linked) and not missing and cost_covered == len(linked) and not active
            breach = cost >= float(item["cap_usd"])
            goal["metrics"] = {
                "observed_spend": cost, "remaining_observed": max(0, float(item["cap_usd"]) - cost),
                "session_count": len(linked), "missing_sessions": missing,
                "cost_covered_sessions": cost_covered,
                "active_sessions": active, "agent_spend": agent_cost,
                "cost_basis": "estimated" if any(row.get("cost_approx") for row in linked) else "reported",
                "status": "over cap" if breach else
                          "ended" if item.get("ended_at") else
                          "ended" if item.get("finish_mode") == "end_date" and datetime.date.today().isoformat() > item["deadline"] else
                          "ends on date" if item.get("finish_mode") == "end_date" else
                          "met" if complete and item.get("completed") and item.get("links_confirmed") else
                          "awaiting evidence" if not complete else
                          "needs your confirmation",
            }
        elif item["type"] == "daily_spend":
            goal["metrics"] = _daily_spend_metrics(item, rows, current_keys, metric_available)
        elif item["type"] == "model_spend":
            goal["metrics"] = _model_spend_metrics(item, rows, current_keys, metric_available)
        elif item["type"] == "model_mix":
            if item["mode"] == "tier_shares":
                goal["metrics"] = _tier_mix_metrics(item, rows, current_keys)
                goals.append(goal)
                continue
            selected = [row for row in rows if row.get("provider") == item["agent"]
                        and item["start"] <= str(row.get("last") or "")[:10] <= item["deadline"]]
            active = sum(session_key(row) in current_keys for row in selected)
            counts = {}
            known = 0
            unknown = 0
            for row in selected:
                counted = 0
                for model in row.get("model_stats") or []:
                    executions = max(0, int(model.get("executions") or 0))
                    name = str(model.get("model") or "").strip()
                    if name and not name.lower().startswith("unknown"):
                        counts[name] = counts.get(name, 0) + executions
                        known += executions
                    else:
                        unknown += executions
                    counted += executions
                unknown += max(0, int(row.get("turns") or 0) - counted)
            denominator = known + unknown
            qualifying = sum(1 for count in counts.values() if denominator and item.get("minimum_share")
                             and count / denominator * 100 >= item["minimum_share"])
            targets_met = denominator and all(counts.get(model, 0) / denominator * 100 >= share
                                              for model, share in (item.get("target_shares") or {}).items())
            target_met = (targets_met if item.get("mode") == "target_shares" else
                          qualifying >= (item.get("minimum_models") or 0))
            goal["metrics"] = {"executions": denominator, "attributed_executions": known,
                               "unattributed_executions": unknown, "counts": counts,
                               "active_sessions": active,
                               "qualifying_models": qualifying, "target_met": bool(target_met),
                               "status": "ended" if item.get("ended_at") else
                                         "met" if denominator and not unknown and not active and target_met and datetime.date.today().isoformat() >= item["deadline"] else
                                         "awaiting completion" if active and datetime.date.today().isoformat() >= item["deadline"] else
                                         "missed target" if datetime.date.today().isoformat() > item["deadline"] else
                                         "in progress" if denominator else "awaiting evidence"}
        if item["id"] == chart_goal and item["type"] in {"daily_spend", "model_spend"}:
            goal["chart"] = _spend_chart(item, rows, metric_available, chart_offset)
        goals.append(goal)
    monthly = {"configured": bool(budget.get("configured")), "partial": bool(budget.get("partial")),
               "runtime_exceeded": bool(budget.get("runtime_exceeded")),
               "runtimes": [{"provider": row.get("provider"), "label": row.get("label"),
                             "spend": row.get("spend"), "allocation": row.get("allocation")}
                            for row in (budget.get("runtimes") or [])]}
    observed_models = {agent: sorted({str(model)[:120] for row in rows if row.get("provider") == agent
                                      for model in row.get("models") or []
                                      if model and not str(model).lower().startswith("unknown")})[:500]
                       for agent in AGENTS}
    tier_models = {agent: [{"key": model_tier_key(agent, model), "model": public_model_label(model),
                            "tier": model_tier(model) or "unclassified",
                            "override_allowed": tier_override_allowed(model) and model_tier(model) is None,
                            "spend_selectable": tier_override_allowed(model)}
                           for model in sorted({str(model) for row in rows if row.get("provider") == agent
                                                for model in [*(row.get("models") or []),
                                                              *(stat.get("model") for stat in row.get("model_stats") or []),
                                                              *(stat.get("model") for stat in row.get("_model_daily") or [])]
                                                if model})[:500]] for agent in AGENTS}
    query = str(query or "").strip().lower()[:120]
    ordered = sorted(sessions.items(), key=lambda pair: str(pair[1].get("last") or ""), reverse=True)
    matched = [(key, row) for key, row in ordered if not query or query in " ".join((
        str(row.get("title") or ""), str(row.get("project") or ""),
        str(row.get("provider") or ""), str(row.get("last") or ""),
        str(row.get("id") or ""), str(store["labels"].get(key) or ""),
        " ".join(str(model) for model in row.get("models") or []))).lower()]
    linked_keys = {key for key in store["links"] if key in sessions}
    visible_keys = set(key for key, _ in matched[:100 if query else 200])
    visible_keys.update(linked_keys)
    visible = [(key, row) for key, row in ordered if key in visible_keys]
    return {"ok": True, "goals": goals, "links": store["links"],
            "monthly_budget": monthly, "observed_models": observed_models,
            "tier_models": tier_models,
            "query": query, "total_sessions": len(sessions), "matching_sessions": len(matched),
            "sessions_truncated": len(matched) > (100 if query else 200),
            "sessions": [{
                "key": key, "id": str(row.get("id") or "").replace("\\", "/").split("/")[-1][:120],
                "agent": row.get("provider"),
                "title": str(row.get("title") or "(untitled session)")[:160],
                "purpose": store["labels"].get(key) or "",
                "project": str(row.get("project") or "").replace("\\", "/").rstrip("/").split("/")[-1][:80],
                "last": str(row.get("last") or "")[:20],
                "cost": row.get("cost"), "cost_available": bool(metric_available(row, "cost")),
                "cost_approx": bool(row.get("cost_approx")), "active": key in current_keys,
                "models": [str(model)[:120] for model in (row.get("models") or [])[:8]],
            } for key, row in visible]}
