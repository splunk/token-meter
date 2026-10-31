"""Content-free notification rules shared by every Token Meter companion.

The server decides when quota, budget, and live-hint alerts fire and records
which ones a companion has claimed. Companions only claim and display events.
"""

import copy
import math
import re

STATE_VERSION = 1
USER_QUOTA_THRESHOLDS = (80, 90, 95)
DEFAULT_QUOTA_THRESHOLD = 80
DEFAULT_BUDGET_THRESHOLDS = (80, 90, 100)
RESET_ADVANCE_S = 60
MAX_EVENTS = 50
EVENT_TTL_S = 24 * 3600
CLAIM_WINDOW_S = 15 * 60
MAX_EXCEEDED_MONTHS = 24
MAX_HINT_IDS = 200
MAX_HINTS_PER_PASS = 12
_CONSUMER = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}")


def empty_state():
    return {
        "version": STATE_VERSION,
        "quota": {},
        "budget": {},
        "budget_exceeded_months": [],
        "hints": {"seen": [], "seeded": False, "enabled": False},
        "events": [],
    }


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _thresholds(values):
    out = set()
    for value in values if isinstance(values, list) else ():
        number = _number(value)
        if number is not None:
            out.add(int(number))
    return sorted(out)


def normalize_state(raw):
    """Return a valid state; anything unreadable starts over from first sighting."""
    state = empty_state()
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return state
    for key, row in (raw.get("quota") or {}).items() if isinstance(raw.get("quota"), dict) else ():
        if isinstance(key, str) and isinstance(row, dict) and _number(row.get("last_used_percent")) is not None:
            state["quota"][key] = {
                "last_used_percent": _number(row["last_used_percent"]),
                "reset_at": _number(row.get("reset_at")),
                "fired": _thresholds(row.get("fired")),
            }
    for key, row in (raw.get("budget") or {}).items() if isinstance(raw.get("budget"), dict) else ():
        if (isinstance(key, str) and isinstance(row, dict) and isinstance(row.get("month"), str)
                and _number(row.get("last_percent")) is not None):
            state["budget"][key] = {
                "month": row["month"],
                "last_percent": _number(row["last_percent"]),
                "fired": _thresholds(row.get("fired")),
            }
    months = raw.get("budget_exceeded_months")
    if isinstance(months, list):
        state["budget_exceeded_months"] = sorted({m for m in months if isinstance(m, str)})[-MAX_EXCEEDED_MONTHS:]
    hints = raw.get("hints")
    if isinstance(hints, dict):
        seen = [h for h in hints.get("seen") or [] if isinstance(h, str)] if isinstance(hints.get("seen"), list) else []
        state["hints"] = {
            "seen": seen[-MAX_HINT_IDS:],
            "seeded": hints.get("seeded") is True,
            "enabled": hints.get("enabled") is True,
        }
    events = raw.get("events")
    if isinstance(events, list):
        state["events"] = [
            dict(event) for event in events
            if isinstance(event, dict) and isinstance(event.get("id"), str)
            and _number(event.get("created_at")) is not None
        ]
    return state


def money(value):
    value = float(value or 0)
    return f"${value:.2f}" if abs(value) >= 1 else f"${value:.3f}"


def _rounded(value):
    """Round half away from zero, matching the native companions."""
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def compact_duration(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h" + (f" {remaining_minutes}m" if remaining_minutes else "")
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d" + (f" {remaining_hours}h" if remaining_hours else "")


def _reset_label(reset_at, now):
    if reset_at is None:
        return "reset time unavailable"
    remaining = reset_at - now
    if remaining <= 0:
        return "reset pending"
    return f"resets in {compact_duration(remaining)}"


def _event(event_id, kind, severity, title, body, link, now):
    return {
        "id": event_id, "kind": kind, "severity": severity,
        "title": title, "body": body, "link": link,
        "created_at": int(now), "claimed": False, "claimed_by": None,
    }


def _quota_events(state, quotas, prefs, now, established):
    events = []
    observed_fresh = False
    enabled = prefs.get("quota_alerts") is not False
    threshold = prefs.get("quota_threshold")
    if threshold not in USER_QUOTA_THRESHOLDS:
        threshold = DEFAULT_QUOTA_THRESHOLD
    levels = sorted({threshold, 95, 100})
    for provider in quotas or ():
        if not isinstance(provider, dict):
            continue
        if provider.get("status") != "ok" or provider.get("stale"):
            continue
        provider_id = str(provider.get("id") or "")
        if not provider_id:
            continue
        observed_fresh = True
        provider_label = str(provider.get("label") or provider_id.capitalize())
        for window in provider.get("windows") or ():
            if not isinstance(window, dict):
                continue
            window_id = str(window.get("id") or "")
            used = _number(window.get("used_percent"))
            if not window_id or used is None:
                continue
            used = max(0.0, min(100.0, used))
            reset_at = _number(window.get("reset_at"))
            window_label = str(window.get("label") or "Quota")
            key = f"{provider_id}:{window_id}"
            previous = state["quota"].get(key)
            if previous is None:
                state["quota"][key] = {"last_used_percent": used, "reset_at": reset_at, "fired": []}
                continue
            fired = set(previous["fired"])
            old_reset = previous["reset_at"]
            reset_advanced = old_reset is not None and reset_at is not None and reset_at > old_reset + RESET_ADVANCE_S
            if reset_advanced:
                if (enabled and established and previous["last_used_percent"] >= threshold
                        and used < threshold):
                    events.append(_event(
                        f"quota-reset:{provider_id}:{window_id}:{int(reset_at)}", "quota_reset", "info",
                        f"{provider_label} quota reset",
                        f"{window_label} is back to {_rounded(used)}% used.", "#sessions", now,
                    ))
                fired = set()
            elif enabled and established:
                crossed = [t for t in levels if previous["last_used_percent"] < t <= used and t not in fired]
                if crossed:
                    hit = max(crossed)
                    severity = "exhausted" if hit >= 100 else ("critical" if hit >= 95 else "warning")
                    # Without a reset time the crossing itself is the identity, so a later re-cross is not deduped away.
                    reset_part = f"none-{int(now)}" if reset_at is None else str(int(reset_at))
                    events.append(_event(
                        f"quota:{provider_id}:{window_id}:{reset_part}:{hit}", "quota", severity,
                        f"{provider_label} quota {severity}",
                        f"{window_label} reached {_rounded(used)}% used; {_reset_label(reset_at, now)}.",
                        "#sessions", now,
                    ))
                    fired.update(crossed)
            state["quota"][key] = {"last_used_percent": used, "reset_at": reset_at, "fired": sorted(fired)}
    return events, observed_fresh


def _alert_percent(row):
    """Alerts use the alert basis when the budget provides one."""
    value = row.get("alert_percent")
    return _number(row.get("percent") if value is None else value)


def _budget_scopes(budget):
    percent = (_alert_percent(budget) or 0.0) * 100
    scopes = [{"id": "overall", "label": "Overall", "percent": percent}]
    for row in budget.get("runtimes") or ():
        if not isinstance(row, dict) or not row.get("provider"):
            continue
        provider = str(row["provider"])
        allocation = _number(row.get("allocation")) or 0.0
        scopes.append({
            "id": provider,
            "label": str(row.get("label") or provider.capitalize()),
            "percent": (_alert_percent(row) or 0.0) * 100 if allocation > 0 else 0.0,
        })
    return scopes


def _budget_events(state, budget, prefs, now, fresh_state=False):
    if not isinstance(budget, dict) or budget.get("configured") is not True:
        return []
    events = []
    enabled = prefs.get("budget_alerts") is not False
    thresholds = _thresholds(list(prefs.get("budget_thresholds") or [])) or list(DEFAULT_BUDGET_THRESHOLDS)
    month = str(budget.get("month") or "")
    spend = _number(budget.get("spend")) or 0.0
    total = _number(budget.get("budget")) or 0.0
    percent = (_alert_percent(budget) or 0.0) * 100
    shown_percent = (_number(budget.get("percent")) or 0.0) * 100
    prefix = "At least " if budget.get("lower_bound") else ""
    exceeded = percent >= 100
    months = set(state["budget_exceeded_months"])
    if fresh_state and enabled and exceeded:
        # Brand-new state (upgrade or unreadable file): the old companions already announced this month.
        months.add(month)
        state["budget_exceeded_months"] = sorted(months)[-MAX_EXCEEDED_MONTHS:]
    if enabled and exceeded and month not in months:
        events.append(_event(
            f"budget-exceeded:{month}", "budget_exceeded", "critical",
            "Overall monthly budget exceeded",
            f"{prefix}{money(spend)} of {money(total)} recorded for {month} ({_rounded(shown_percent)}% used).",
            "#spend-budgets", now,
        ))
        months.add(month)
        state["budget_exceeded_months"] = sorted(months)[-MAX_EXCEEDED_MONTHS:]
    for scope in _budget_scopes(budget):
        previous = state["budget"].get(scope["id"])
        if previous is None or previous["month"] != month:
            state["budget"][scope["id"]] = {
                "month": month, "last_percent": scope["percent"],
                "fired": [t for t in thresholds if scope["percent"] >= t],
            }
            continue
        fired = set(previous["fired"])
        if enabled:
            crossed = [t for t in thresholds if previous["last_percent"] < t <= scope["percent"] and t not in fired]
            overall_reported = scope["id"] == "overall" and exceeded and month in months
            if crossed and not overall_reported:
                hit = max(crossed)
                body = (
                    f"{prefix}{money(spend)} of {money(total)} recorded for {month}."
                    if scope["id"] == "overall"
                    else f"{scope['label']} reached {_rounded(scope['percent'])}% of its allocation."
                )
                events.append(_event(
                    f"budget:{scope['id']}:{month}:{hit}", "budget",
                    "critical" if hit >= 100 else "warning",
                    f"{scope['label']} monthly budget reached {hit}%", body, "#spend-budgets", now,
                ))
            fired.update(crossed)
        state["budget"][scope["id"]] = {"month": month, "last_percent": scope["percent"], "fired": sorted(fired)}
    return events


def _hint_events(state, hints, enabled, now):
    record = state["hints"]
    if enabled and not record["enabled"]:
        record["seeded"] = False
    record["enabled"] = bool(enabled)
    events = []
    seen = list(record["seen"])
    for hint in list(hints or ())[:MAX_HINTS_PER_PASS]:
        if not isinstance(hint, dict):
            continue
        hint_id, title, body = hint.get("id"), hint.get("title"), hint.get("body")
        if not (isinstance(hint_id, str) and hint_id and isinstance(title, str) and isinstance(body, str)):
            continue
        if hint_id in seen:
            continue
        if record["seeded"]:
            events.append(_event(f"hint:{hint_id}", "live_hint", "info",
                                 f"Token Meter: {title}", body, "#sessions", now))
        seen.append(hint_id)
    record["seen"] = seen[-MAX_HINT_IDS:]
    record["seeded"] = True
    return events


def evaluate(state, observation, prefs, now, *, quota_established):
    """Advance rule state for one observation and return (state, new_events, observed_fresh_quota)."""
    state = normalize_state(copy.deepcopy(state))
    fresh_state = state == empty_state()
    observation = observation if isinstance(observation, dict) else {}
    prefs = prefs if isinstance(prefs, dict) else {}
    quota_events, observed_fresh = _quota_events(
        state, observation.get("quotas"), prefs, now, quota_established,
    )
    candidates = (
        quota_events
        + _budget_events(state, observation.get("budget"), prefs, now, fresh_state)
        + _hint_events(state, observation.get("hints"), observation.get("hints_enabled") is True, now)
    )
    known = {event["id"] for event in state["events"]}
    new_events = []
    for event in candidates:
        if event["id"] not in known:
            known.add(event["id"])
            new_events.append(event)
    retained = [e for e in state["events"] + new_events if now - e["created_at"] <= EVENT_TTL_S]
    retained.sort(key=lambda e: e["created_at"], reverse=True)
    state["events"] = retained[:MAX_EVENTS]
    return state, new_events, observed_fresh


def claimable(event, now):
    """True while an event can still be claimed; older or claimed events are history only."""
    return not event.get("claimed") and now - event["created_at"] <= CLAIM_WINDOW_S


def valid_consumer(value):
    return isinstance(value, str) and _CONSUMER.fullmatch(value) is not None


def claim(state, event_id, consumer, now):
    """Mark an event delivered by the first consumer to ask; later claims are refused."""
    state = normalize_state(copy.deepcopy(state))
    for event in state["events"]:
        if event["id"] != event_id:
            continue
        if event.get("claimed"):
            return state, {"ok": True, "claimed": False, "reason": "claimed"}
        if not claimable(event, now):
            return state, {"ok": True, "claimed": False, "reason": "expired"}
        event["claimed"] = True
        event["claimed_by"] = consumer
        event["claimed_at"] = int(now)
        return state, {"ok": True, "claimed": True}
    return state, {"ok": True, "claimed": False, "reason": "unknown"}
