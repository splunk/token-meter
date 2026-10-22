"""Side-by-side comparison of a few selected sessions with content-free insights."""

import os
import re
import statistics

from token_meter.domain.usage import format_usd

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


def _money(value):
    return format_usd(value)


def _duration(seconds):
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _compact(value):
    value = float(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        return f"{value / 1000:.0f}K"
    return f"{value:.0f}"


def _letters(sessions):
    return ", ".join(s["letter"] for s in sessions)


def _join_letters(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _available(sessions, key):
    return [s for s in sessions if s.get(key) is not None]


def _extremes(sessions, key):
    rows = _available(sessions, key)
    if len(rows) < 2:
        return None, None
    low = min(rows, key=lambda s: s[key])
    high = max(rows, key=lambda s: s[key])
    return low, high


def _setup_insight(sessions, same_title):
    fields = (
        ("app", lambda s: s["label"]),
        ("model", lambda s: s["primary_model"] or ", ".join(s["models"])),
        ("reasoning effort", lambda s: s["reasoning_effort"] or "default"),
        ("project", lambda s: s["project"] or "local"),
    )
    diffs = []
    for name, getter in fields:
        groups = {}
        for session in sessions:
            groups.setdefault(getter(session), []).append(session["letter"])
        if len(groups) > 1:
            detail = "; ".join(f"{', '.join(letters)}: {value}" for value, letters in groups.items())
            diffs.append(f"{name} ({detail})")
    if diffs:
        return {"kind": "setup", "tone": "info", "text": "Setup differs by " + ", ".join(diffs) + "."}
    tail = (" — if these ran the same prompt, remaining differences reflect run-to-run variance."
            if same_title else ".")
    return {"kind": "setup", "tone": "info", "text": "Identical setup: same app, model, effort, and project" + tail}


def _cost_insights(sessions):
    out = []
    low, high = _extremes(sessions, "cost")
    if low and high and high["cost"] > 0:
        if low["cost"] > 0 and high["cost"] / low["cost"] >= 1.15:
            out.append({"kind": "cost", "tone": "warn", "text": (
                f"{high['letter']} cost {high['cost'] / low['cost']:.1f}× {low['letter']} "
                f"({_money(high['cost'])} vs {_money(low['cost'])}).")})
        elif low["cost"] > 0:
            out.append({"kind": "cost", "tone": "good", "text": (
                f"Costs are within 15% of each other ({_money(low['cost'])}–{_money(high['cost'])}).")})
        gap = high["cost"] - low["cost"]
        if gap > 0 and low.get("cost_parts") and high.get("cost_parts"):
            deltas = {key: high["cost_parts"].get(key, 0) - low["cost_parts"].get(key, 0)
                      for key in high["cost_parts"]}
            key, delta = max(deltas.items(), key=lambda item: item[1])
            if delta > gap:
                out.append({"kind": "cost_driver", "tone": "info", "text": (
                    f"The biggest cost difference is {COST_PART_LABELS[key]}: {high['letter']} spent "
                    f"{_money(delta)} more there than {low['letter']}, partly offset elsewhere "
                    f"for a net gap of {_money(gap)}.")})
            elif delta > 0 and delta / gap >= 0.4:
                out.append({"kind": "cost_driver", "tone": "info", "text": (
                    f"Most of the {_money(gap)} gap between {low['letter']} and {high['letter']} "
                    f"is {COST_PART_LABELS[key]} (+{_money(delta)}).")})
    return out


def _ratio_insight(sessions, key, kind, min_ratio, render):
    low, high = _extremes(sessions, key)
    if not low or not high or not low[key] or high[key] / low[key] < min_ratio:
        return []
    return [{"kind": kind, "tone": "info", "text": render(low, high)}]


def _variance_insight(sessions, same_title):
    if not same_title:
        return []
    parts = []
    for key, label in (("cost", "cost"), ("duration_s", "active time"), ("turns", "executions")):
        values = [s[key] for s in sessions if s.get(key) is not None]
        if len(values) >= 3 and statistics.mean(values) > 0:
            parts.append(f"{label} ±{100 * statistics.pstdev(values) / statistics.mean(values):.0f}%")
    if not parts:
        return []
    return [{"kind": "variance", "tone": "info", "text": (
        f"Across {len(sessions)} runs with matching titles, the spread is " + ", ".join(parts) + ".")}]


def _evidence_insights(sessions):
    out = []
    missing = [s["letter"] for s in sessions if s["cost"] is None]
    if missing:
        out.append({"kind": "evidence", "tone": "warn", "text": (
            f"Cost is unavailable for {_join_letters(missing)}, so "
            f"{'it is' if len(missing) == 1 else 'they are'} excluded from cost comparisons.")})
    estimated = [s["letter"] for s in sessions if s["usage_basis"] != "reported" or s["cost_approx"]]
    if estimated:
        out.append({"kind": "evidence", "tone": "info", "text": (
            f"{_join_letters(estimated)} {'uses' if len(estimated) == 1 else 'use'} estimated usage; "
            "treat those differences as approximate.")})
    truncated = [s["letter"] for s in sessions if s["trace_truncated"]]
    if truncated:
        out.append({"kind": "evidence", "tone": "info", "text": (
            f"The execution trace for {_join_letters(truncated)} is truncated; the chart shows the retained part.")})
    return out


def compare_sessions(entries):
    sessions = [dict(entry, letter=LETTERS[index]) for index, entry in enumerate(entries[:MAX_COMPARE_SESSIONS])]
    keys = {s["title_key"] for s in sessions}
    same_title = len(sessions) >= 2 and len(keys) == 1 and "" not in keys
    if len(sessions) < 2:
        return {"sessions": sessions, "same_title": False, "best": {}, "insights": []}
    best = {}
    for key, lower in BEST_METRICS:
        rows = _available(sessions, key)
        if len(rows) < 2 or len({row[key] for row in rows}) < 2:
            continue
        pick = min(rows, key=lambda s: s[key]) if lower else max(rows, key=lambda s: s[key])
        best[key] = pick["key"]

    insights = []
    if same_title:
        insights.append({"kind": "prompt", "tone": "info", "text": (
            f"All {len(sessions)} sessions have matching titles, which usually means the same opening prompt.")})
    insights.append(_setup_insight(sessions, same_title))
    insights += _cost_insights(sessions)
    insights += _ratio_insight(sessions, "duration_s", "speed", 1.25, lambda low, high: (
        f"{low['letter']} finished in {_duration(low['duration_s'])} of active time, "
        f"{high['duration_s'] / low['duration_s']:.1f}× faster than {high['letter']} ({_duration(high['duration_s'])})."))
    insights += _ratio_insight(sessions, "turns", "executions", 1.5, lambda low, high: (
        f"{high['letter']} needed {high['turns']} executions vs {low['turns']} for {low['letter']}."))
    low, high = _extremes(sessions, "cache_hit_ratio")
    if low and high and high["cache_hit_ratio"] - low["cache_hit_ratio"] >= 0.15:
        insights.append({"kind": "cache", "tone": "info", "text": (
            f"{high['letter']} served {100 * high['cache_hit_ratio']:.0f}% of input from cache "
            f"vs {100 * low['cache_hit_ratio']:.0f}% for {low['letter']}.")})
    insights += _ratio_insight(sessions, "context_peak", "context", 1.5, lambda low, high: (
        f"{high['letter']}'s peak context reached {_compact(high['context_peak'])} tokens, "
        f"{high['context_peak'] / low['context_peak']:.1f}× {low['letter']}'s."))
    near_limit = [
        f"{s['letter']} ({100 * s['context_peak'] / s['context_window']:.0f}%)" for s in sessions
        if s.get("context_window") and s.get("context_peak") and s["context_peak"] / s["context_window"] >= 0.8
    ]
    if near_limit:
        insights.append({"kind": "context", "tone": "warn", "text": (
            f"{_join_letters(near_limit)} came within 20% of the context window at peak.")})
    erroring = [s for s in sessions if s.get("tool_errors")]
    clean = [s["letter"] for s in sessions if s.get("tool_errors") == 0]
    for session in erroring:
        tail = f"; {_join_letters(clean)} had none" if clean else ""
        insights.append({"kind": "tools", "tone": "warn", "text": (
            f"{session['letter']} hit {session['tool_errors']} tool "
            f"{'error' if session['tool_errors'] == 1 else 'errors'}{tail}.")})
    low, high = _extremes(sessions, "tool_calls")
    if low and high and high["tool_calls"] >= 5 and high["tool_calls"] >= 1.5 * max(1, low["tool_calls"]):
        insights.append({"kind": "tools", "tone": "info", "text": (
            f"{high['letter']} made {high['tool_calls']} tool calls vs {low['tool_calls']} for {low['letter']}.")})
    insights += _variance_insight(sessions, same_title)
    insights += _evidence_insights(sessions)
    insights.append({"kind": "caveat", "tone": "info", "text": (
        "Token Meter measures effort, not answer quality — check each result before picking a winner.")})
    return {"sessions": sessions, "same_title": same_title, "best": best, "insights": insights}


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
