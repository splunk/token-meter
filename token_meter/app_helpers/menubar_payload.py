"""Compact native menu-bar payload helpers."""

import datetime
import math

from token_meter.domain.usage import format_usd
from token_meter.domain.cache_window import cache_window_projection as _domain_cache_window_projection
from token_meter.app_helpers.common import (
    compact_duration_ms,
    compact_text,
    folded_child_root_id,
    is_operational_warning,
    low_yield_should_warn,
    metric_available,
)


MENUBAR_CONTEXT_SOFT_PCT = 0.65


MENUBAR_CONTEXT_WATCH_PCT = 0.70


MENUBAR_CONTEXT_INTERVENE_PCT = 0.85


MENUBAR_COST_SPIKE = 0.50


def menubar_activity(st):
    trace = st.get("trace") or []
    preferred = {
        "tool_call", "tool_result", "message", "reasoning", "complete",
        "context", "goal", "start", "user",
    }
    event = None
    for ev in reversed(trace[-24:]):
        if ev.get("kind") in preferred:
            event = ev
            break
    if event is None and trace:
        event = trace[-1]
    if not event:
        return {
            "kind": "idle",
            "title": "Waiting for activity",
            "detail": "No trace events yet.",
            "time": "",
            "execution": None,
        }

    kind = event.get("kind") or "activity"
    label = event.get("label") or kind.replace("_", " ").title()
    detail = event.get("detail") or ""
    tool = event.get("tool")
    execution = event.get("execution")
    duration_ms = event.get("duration_ms")

    if kind == "tool_call":
        title = f"Running {label}"
    elif kind == "tool_result":
        title = f"Received {label}"
    elif kind == "reasoning":
        title = "Reasoning"
    elif kind == "message":
        title = label if label != "Agent update" else "Agent update"
    elif kind == "complete":
        title = f"Completed #{execution}" if execution else "Execution complete"
    elif kind == "context":
        title = label
    elif kind == "user":
        title = "User message"
    else:
        title = label

    bits = []
    duration = compact_duration_ms(duration_ms)
    if not duration and isinstance(detail, str) and detail.endswith("ms") and detail[:-2].isdigit():
        duration = compact_duration_ms(detail[:-2])
    if duration:
        bits.append(f"in {duration}" if kind == "complete" else duration)
    elif detail:
        bits.append(detail)
    if execution:
        if kind == "complete":
            pass
        else:
            bits.append(f"#{execution}")
    if tool and tool not in title:
        bits.append(tool)
    if event.get("cost"):
        bits.append(format_usd(event["cost"]))
    detail = " · ".join(bits)
    return {
        "kind": kind,
        "title": compact_text(title, 64),
        "detail": compact_text(detail, 120),
        "time": event.get("time") or "",
        "execution": execution,
        "tool": tool,
    }


def menubar_recommendation(st):
    context = st.get("context") or {}
    pct = context.get("latest_pct") or 0
    insights = st.get("insights") or []
    warn = next((i for i in insights if i.get("kind") == "warn"), None)
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    low_yield_actionable = (not st.get("token_estimate") and
                            low_yield_should_warn(st.get("executions") or [], pct))

    if st.get("ended"):
        return {
            "label": "Pinned log",
            "detail": "This is a frozen log view; return to live to follow newest activity.",
            "severity": "idle",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return {
            "label": "Compact now",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "bad",
            "target": "summary",
        }
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return {
            "label": "Review spike",
            "detail": f"Last execution cost {format_usd(last_cost)}.",
            "severity": "bad",
            "target": "summary",
        }
    if low_yield_actionable:
        return {
            "label": "Summarize soon",
            "detail": "Latest execution replayed large context for low output.",
            "severity": "warn",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return {
            "label": "Summarize soon",
            "detail": f"Context is {pct * 100:.0f}%; prepare before 85%.",
            "severity": "warn",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_SOFT_PCT:
        return {
            "label": "Watch context",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "idle",
            "target": "summary",
        }
    if warn:
        return {
            "label": "Check signal",
            "detail": warn.get("text") or "A warning signal is active.",
            "severity": "warn",
            "target": "summary",
        }
    return {
        "label": "Let it run",
        "detail": "No immediate intervention needed.",
        "severity": "good",
        "target": "summary",
    }


def menubar_verdict(st, recommendation):
    context = st.get("context") or {}
    reported_pct = context.get("latest_pct")
    pct = reported_pct or 0
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    insights = st.get("insights") or []
    operational_warn = next((i for i in insights if i.get("kind") == "warn" and is_operational_warning(i)), None)

    def payload(key, detail):
        labels = {
            "healthy": ("Healthy", "TM", "good"),
            "watch": ("Watch closely", "TM !", "warn"),
            "intervene": ("Intervene now", "TM !!", "bad"),
            "idle": ("Idle", "TM idle", "idle"),
        }
        label, prefix, severity = labels[key]
        return {"key": key, "label": label, "prefix": prefix, "severity": severity, "detail": detail}

    if st.get("ended"):
        return payload("idle", "This is a frozen log view; return to live to follow newest activity.")
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return payload(
            "intervene",
            f"Context is {pct * 100:.0f}% of the model window; compact now.",
        )
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return payload(
            "intervene",
            f"Last execution cost {format_usd(last_cost)}; review the spike before continuing.",
        )
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return payload(
            "watch",
            f"Context is {pct * 100:.0f}% of the model window; prepare to summarize before 85%.",
        )
    if operational_warn:
        detail = operational_warn.get("text") or recommendation.get("detail") or "An operational warning is active."
        return payload("watch", detail)

    detail = (f"Context is {pct * 100:.0f}% and no operational warning needs intervention."
              if reported_pct is not None else
              "Context percentage is not reported; no operational warning needs intervention.")
    return payload("healthy", detail)


def menubar_project_name(value):
    """Return only a display-safe project leaf for the native payload."""
    project = str(value or "").rstrip("/\\")
    if not project or project == "No project":
        return ""
    return compact_text(project.replace("\\", "/").rsplit("/", 1)[-1], 52)


def menubar_session_name(source):
    session_name = compact_text(source.get("session_name") or "", 52).strip()
    if session_name and session_name.lower() not in ("untitled", "untitled session"):
        return session_name
    title = compact_text(source.get("title") or "", 52).strip()
    if title and title.lower() not in ("untitled", "untitled session"):
        return title
    project = menubar_project_name(source.get("project"))
    if project:
        return project
    return str(source.get("id") or "session")[:12]


def menubar_recent_sessions(sources, selected_id=None, limit=5, summaries=None):
    summary_names = {}
    for summary in summaries or ():
        key = (str(summary.get("provider") or ""), str(summary.get("id") or ""))
        name = compact_text(summary.get("session_name") or "", 52).strip()
        if key[1] and name and key not in summary_names:
            summary_names[key] = name

    # A child run is listed through its root session, which inherits the
    # child's newer activity, so the menu never offers a child as its own run.
    source_list = list(sources or [])
    root_keys = {
        (str(row.get("provider") or ""), str(row.get("id") or ""))
        for row in source_list
    }
    child_activity = {}
    for row in source_list:
        root_id = folded_child_root_id(row)
        key = (str(row.get("provider") or ""), root_id)
        if root_id and key in root_keys:
            child_activity[key] = max(
                child_activity.get(key, 0), float(row.get("mtime") or 0),
            )
    listed = []
    for row in source_list:
        key = (str(row.get("provider") or ""), folded_child_root_id(row))
        if key[1] and key in root_keys:
            continue
        own_key = (str(row.get("provider") or ""), str(row.get("id") or ""))
        if own_key in child_activity and child_activity[own_key] > float(row.get("mtime") or 0):
            row = dict(row, mtime=child_activity[own_key])
        listed.append(row)

    ordered, seen = [], set()
    for source in sorted(listed, key=lambda row: -(row.get("mtime") or 0)):
        sid = str(source.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ordered.append(source)

    selected = next((row for row in ordered if row.get("id") == selected_id), None)
    choices = ordered[:max(0, limit)]
    if selected and selected not in choices and limit > 0:
        choices = [selected] + [row for row in ordered if row is not selected][:limit - 1]

    result = []
    for row in choices:
        display_row = dict(row)
        key = (str(row.get("provider") or ""), str(row.get("id") or ""))
        if summary_names.get(key):
            display_row["session_name"] = summary_names[key]
        result.append({
            "id": row.get("id"),
            "provider": row.get("provider"),
            "client": row.get("client") or row.get("provider"),
            "label": row.get("label"),
            "name": menubar_session_name(display_row),
            "mtime": row.get("mtime") or 0,
        })
    return result


def menubar_cache_window(current_sessions, selected_id, root_id=None):
    """Return the selected session's cache window; a folded child run resolves to its root."""
    for wanted in (str(selected_id or ""), str(root_id or "")):
        if not wanted:
            continue
        for summary in current_sessions or ():
            if isinstance(summary, dict) and str(summary.get("id") or "") == wanted:
                return _domain_cache_window_projection(summary.get("cache_window"))
    return None


def menubar_context_pulse(st, limit=18):
    """Return a short numeric context history for the native Run Pulse.

    The native companion needs a visual indication of whether measured context
    is rising or settling, but it must never receive trace text, paths, tool
    inputs, or other session contents. Keep only finite context percentages
    from the most recent completed executions.
    """
    values = []
    for execution in (st.get("executions") or []):
        value = execution.get("context_pct")
        if isinstance(value, bool):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(round(max(0.0, min(1.0, value)), 4))
    return values[-max(1, limit):]


def menubar_today_spend(cross, today=None):
    """Return a bounded current-day spend projection for the native title."""
    day = (today or datetime.date.today()).isoformat()
    daily = cross.get("daily") if isinstance(cross, dict) else []
    row = next(
        (item for item in (daily or [])
         if isinstance(item, dict) and item.get("day") == day),
        None,
    )
    availability = row.get("availability") if isinstance(row, dict) else {}
    available = bool(isinstance(availability, dict) and availability.get("cost") is True)
    provenance = row.get("provenance") if isinstance(row, dict) else {}
    estimated_cost = (
        float(provenance.get("estimated_cost") or 0)
        if isinstance(provenance, dict) else 0.0
    )
    return {
        "available": available,
        "total_cost": float(row.get("cost") or 0) if available else None,
        "estimated": estimated_cost > 0,
    }
