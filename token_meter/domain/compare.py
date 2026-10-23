"""Content-free side-by-side comparison of a few selected sessions."""

import os
import re

MIN_COMPARE_SESSIONS = 1
MAX_COMPARE_SESSIONS = 4
MAX_ID_LENGTH = 240
MAX_SERIES_POINTS = 120
MAX_TOP_TOOLS = 5
LETTERS = "ABCD"

COST_PART_LABELS = {
    "input": "fresh input",
    "cache_write": "cache writes",
    "cache_read": "cache reads",
    "output": "output",
    "server_tools": "server tools",
}

# (metric, lower_is_better)
BEST_METRICS = (
    ("cost", True),
    ("duration_s", True),
    ("turns", True),
    ("tokens", True),
    ("output_tps", False),
    ("cache_hit_ratio", False),
    ("context_peak", True),
    ("wait_avg_s", True),
    ("tool_errors", True),
)


def normalize_compare_ids(raw):
    ids = []
    for part in str(raw or "").split(","):
        sid = part.strip()
        if not sid:
            continue
        if len(sid) > MAX_ID_LENGTH:
            return [], "Session ids are too long."
        if sid not in ids:
            ids.append(sid)
    if len(ids) < MIN_COMPARE_SESSIONS:
        return [], "Select a session to compare."
    if len(ids) > MAX_COMPARE_SESSIONS:
        return [], f"Compare at most {MAX_COMPARE_SESSIONS} sessions at a time."
    return ids, None


def trace_key(row):
    """Opaque per-trace selector; ids are shared by forks and file names repeat across runtimes."""
    path = str((row or {}).get("path") or "")
    if not path:
        return str((row or {}).get("id") or "")
    # Two FNV-1a passes over UTF-16 code units, mirrored by compareKeyFor in page.html.
    first, second = 0x811C9DC5, 0x9DC5811C
    data = path.encode("utf-16-le", "surrogatepass")
    for index in range(0, len(data), 2):
        unit = data[index] | (data[index + 1] << 8)
        first = ((first ^ unit) * 0x01000193) & 0xFFFFFFFF
        second = ((second ^ unit) * 0x5BD1E995) & 0xFFFFFFFF
    return f"t{first:08x}{second:08x}"


def trace_stem(row):
    path = str((row or {}).get("path") or "")
    return os.path.basename(path).rsplit(".", 1)[0] if path else ""


def title_key(title):
    text = re.sub(r"\s+", " ", str(title or "")).strip().rstrip("…").rstrip(".").strip().lower()
    return "" if text in ("", "(untitled log)") else text


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _series(state, cost_available, tokens_available=True):
    rows = [row for row in (state.get("series") or []) if isinstance(row, dict)]
    points, cost, tokens = [], 0.0, 0
    for index, row in enumerate(rows, 1):
        cost += _num(row.get("cost")) or 0.0
        tokens += int(_num(row.get("in")) or 0) + int(_num(row.get("out")) or 0)
        points.append({
            "i": int(_num(row.get("i")) or index),
            "cost": round(cost, 6) if cost_available else None,
            "tokens": tokens if tokens_available else None,
        })
    if len(points) <= MAX_SERIES_POINTS:
        return points
    step = (len(points) - 1) / (MAX_SERIES_POINTS - 1)
    return [points[round(k * step)] for k in range(MAX_SERIES_POINTS)]


def compare_entry(summary, state, key=None, open_id=None):
    """Project one session into an allowlisted, content-free comparison record."""
    summary, state = summary or {}, state or {}
    availability = state.get("availability") or summary.get("availability") or {}
    has = lambda key: availability.get(key, True) is not False
    timing = state.get("timing") or {}
    throughput = state.get("throughput") or {}
    wait = state.get("wait_time") or {}
    cache = state.get("cache") or {}
    context = state.get("context") or {}
    tools = state.get("tools") or {}
    analyses = state.get("analyses") or {}
    tokens = state.get("tokens") or {}
    turns = int(_num(state.get("turns")) or _num(summary.get("turns")) or 0)
    cost = _num(state.get("total_cost")) if has("cost") else None
    parts = None
    if cost is not None and isinstance(state.get("cost"), dict):
        parts = {key: round(_num(value) or 0.0, 6) for key, value in state["cost"].items()
                 if key in COST_PART_LABELS}
    total_tokens = int(_num(state.get("total_tokens")) or 0) if has("tokens") else None
    tools_available = has("tool_results") and tools.get("results_available", True) is not False
    duration = _num(timing.get("duration_s")) if timing.get("duration_available") and has("timing") else None
    peak = _num(context.get("peak")) if has("context") else None
    window = _num(context.get("window")) if has("context") else None
    return {
        "key": str(key or trace_key(summary)),
        "id": str(summary.get("id") or ""),
        "open_id": str(open_id or summary.get("id") or "")[:MAX_ID_LENGTH],
        "title": str(summary.get("title") or "(untitled log)")[:160],
        "title_key": title_key(summary.get("title")),
        "label": str(summary.get("label") or summary.get("provider") or ""),
        "provider": str(summary.get("provider") or ""),
        "client": str(summary.get("client") or ""),
        "project": str(summary.get("project") or ""),
        "start": str(summary.get("start") or ""),
        "last": str(summary.get("last") or ""),
        "models": [str(model) for model in (summary.get("models") or [])][:6],
        "primary_model": str(state.get("primary_model") or summary.get("primary_model") or ""),
        "reasoning_effort": str(summary.get("reasoning_effort") or ""),
        "usage_basis": str(summary.get("usage_basis") or "reported"),
        "cost_approx": bool(state.get("cost_approx") or summary.get("cost_approx")),
        "trace_truncated": bool(state.get("trace_truncated")),
        "cost": cost,
        "cost_parts": parts,
        "cost_per_turn": cost / turns if cost is not None and turns else None,
        "tokens": total_tokens,
        "fresh_input_tokens": int(_num(tokens.get("input")) or 0) if has("tokens") else None,
        "output_tokens": int(_num(tokens.get("output")) or 0) if has("tokens") else None,
        "cache_read_tokens": int(_num(tokens.get("cache_read")) or 0) if has("cache") else None,
        "cache_write_tokens": int(_num(tokens.get("cache_write")) or 0) if has("cache") else None,
        "tokens_per_turn": total_tokens / turns if total_tokens is not None and turns else None,
        "turns": turns,
        "subagent_turns": int(_num(state.get("subagent_turns")) or 0),
        "duration_s": duration,
        "wall_duration_s": _num(timing.get("wall_duration_s")),
        "output_tps": _num(throughput.get("output_tps")) if throughput.get("available") else None,
        "wait_avg_s": _num(wait.get("avg_s")) if wait.get("available") else None,
        "wait_total_s": _num(wait.get("total_s")) if wait.get("available") else None,
        "cache_hit_ratio": _num(cache.get("hit_ratio")) if has("cache") else None,
        "cache_saved": _num(cache.get("saved")) if has("cache") and has("cost") else None,
        "context_peak": int(peak) if peak is not None else None,
        "context_window": int(window) if window else None,
        "tool_calls": int(_num(tools.get("total_calls")) or 0) if tools_available else None,
        "tool_errors": int(_num(tools.get("total_errors")) or 0) if tools_available else None,
        "tools_unique": int(_num(tools.get("unique_used")) or 0) if tools_available else None,
        "top_tools": [
            {"name": str(row.get("name") or "")[:80], "calls": int(_num(row.get("calls")) or 0)}
            for row in sorted(
                (row for row in (analyses.get("tool_bloat") or []) if isinstance(row, dict)),
                key=lambda row: -(_num(row.get("calls")) or 0),
            )[:MAX_TOP_TOOLS]
        ],
        "reasoning_share": _num((analyses.get("reasoning") or {}).get("share")),
        "series": _series(state, cost is not None, has("tokens")),
    }


def _available(sessions, key):
    return [s for s in sessions if s.get(key) is not None]


def compare_sessions(entries):
    sessions = [dict(entry, letter=LETTERS[index]) for index, entry in enumerate(entries[:MAX_COMPARE_SESSIONS])]
    keys = {s["title_key"] for s in sessions}
    same_title = len(sessions) >= 2 and len(keys) == 1 and "" not in keys
    if len(sessions) < 2:
        return {"sessions": sessions, "same_title": False, "best": {}}
    best = {}
    for key, lower in BEST_METRICS:
        rows = _available(sessions, key)
        if len(rows) < 2 or len({row[key] for row in rows}) < 2:
            continue
        pick = min(rows, key=lambda s: s[key]) if lower else max(rows, key=lambda s: s[key])
        best[key] = pick["key"]
    return {"sessions": sessions, "same_title": same_title, "best": best}


def matching_sessions(entries, rows, limit=12, open_id=None):
    """Other recorded sessions whose public title matches a selected session's title."""
    keys = {entry.get("title_key") for entry in entries if entry.get("title_key")}
    selected = {entry.get("key") for entry in entries}
    matches = []
    for row in rows or ():
        if not isinstance(row, dict) or trace_key(row) in selected or title_key(row.get("title")) not in keys:
            continue
        cost_available = (row.get("availability") or {}).get("cost", True) is not False
        matches.append({
            "key": trace_key(row),
            "id": str(row.get("id") or ""),
            "open_id": str((open_id(row) if open_id else None) or row.get("id") or "")[:MAX_ID_LENGTH],
            "title": str(row.get("title") or "")[:160],
            "label": str(row.get("label") or row.get("provider") or ""),
            "provider": str(row.get("provider") or ""),
            "start": str(row.get("start") or ""),
            "last": str(row.get("last") or ""),
            "models": [str(model) for model in (row.get("models") or [])][:6],
            "turns": int(_num(row.get("turns")) or 0),
            "cost": _num(row.get("cost")) if cost_available else None,
            "_mtime": _num(row.get("mtime")) or 0.0,
        })
    matches.sort(key=lambda row: -row["_mtime"])
    for row in matches:
        row.pop("_mtime")
    return matches[:limit]
