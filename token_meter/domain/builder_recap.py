"""Privacy-bounded, local-calendar aggregates for AI Builder Recaps."""

import datetime
import math
import re
import time


VALID_RECAP_RANGES = (7, 30, 90)
_MAX_RECAP_CHANGED_LINES = (1 << 53) - 1
_MAX_DAILY_CHANGED_LINES = _MAX_RECAP_CHANGED_LINES // max(VALID_RECAP_RANGES)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_UNKNOWN = frozenset({"", "unknown", "unknown-model", "unknown model", "n/a"})
_PUBLIC_RUNTIMES = frozenset({
    "Claude", "Claude Code", "Claude Desktop", "Claude-3P", "Claude 3P",
    "Codex", "Cursor", "OpenCode", "Kiro", "Kiro CLI", "Pi",
    "Hermes Agent", "Hermes Claude",
})
_MODEL_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,79}")
_PUBLIC_MODEL_FAMILIES = (
    "starcoder", "chatgpt", "codestral", "deepseek", "devstral",
    "composer", "anthropic", "mistral", "minimax", "granite",
    "claude", "gemini", "command", "hermes", "mixtral", "openai",
    "sonnet", "haiku", "llama", "cursor", "codex", "gemma", "qwen",
    "gpt", "opus", "grok", "nova", "kimi", "phi", "glm", "o1", "o3", "o4",
)
_PUBLIC_MODEL_MODIFIERS = frozenset({
    "alpha", "astra", "base", "beta", "chat", "code", "coder", "codex",
    "distill", "embed", "embedding", "exp", "experimental", "fable", "fast",
    "flash", "free", "haiku", "instruct", "it", "large", "latest", "lite",
    "luna", "max", "medium", "micro", "mini", "mythos", "nano", "nemo",
    "omni", "opus", "oss", "plus", "preview", "pro", "reasoning", "small",
    "sol", "sonnet", "standard", "terra", "thinking", "turbo", "vision", "vl",
    *_PUBLIC_MODEL_FAMILIES,
})
_PUBLIC_MODEL_VERSION = re.compile(r"(?:[vrkm]?\d+[a-z]?)", re.I)
_PRIVATE_MODEL_HINT = re.compile(
    r"(?:^|[._:+-])(?:account|client|customer|internal|org|organization|"
    r"project|repo|repository|team|tenant|workspace)(?:[._:+-]|$)", re.I,
)
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_PUBLIC_TOOLS = {
    "apply_patch": "Apply patch",
    "browser": "Browser",
    "edit_file": "Edit file",
    "execute_command": "Run command",
    "fetch_url": "Fetch URL",
    "list_files": "List files",
    "read_file": "Read file",
    "run_command": "Run command",
    "search_code": "Search code",
    "search_files": "Search files",
    "shell": "Shell",
    "web_search": "Web search",
    "write_file": "Write file",
}
_UNSAFE_LABEL = re.compile(
    r"(?:api[ _-]?key|secret|password|credential|bearer|authorization|"
    r"access[ _-]?token|auth[ _-]?token|account|email|@|"
    r"(?:^|[?&;\s])(?:token|key)\s*=|(?:^|[-_])sk[-_])", re.I,
)


def _date(value):
    value = str(value or "")
    if not _DAY.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _period_windows(range_days, today):
    current_start = today - datetime.timedelta(days=range_days - 1)
    previous_end = current_start - datetime.timedelta(days=1)
    previous_start = previous_end - datetime.timedelta(days=range_days - 1)
    return current_start, today, previous_start, previous_end


def _window_days(start, end):
    return [start + datetime.timedelta(days=offset)
            for offset in range((end - start).days + 1)]


def _session_day(row):
    last = str((row or {}).get("last") or "")[:10]
    day = _date(last)
    if day is not None:
        return day
    try:
        mtime = float((row or {}).get("mtime"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(mtime):
        return None
    try:
        return datetime.date.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        return None


def _number(value, default=0):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _known(value):
    return str(value or "").strip().casefold() not in _UNKNOWN


def _public_text(value):
    try:
        value = str(value or "")
    except (TypeError, ValueError):
        return ""
    return value


def _safe_runtime(value):
    value = _public_text(value)
    return value if value in _PUBLIC_RUNTIMES else ""


def _safe_model(value):
    value = _public_text(value)
    if (not _MODEL_LABEL.fullmatch(value) or "://" in value or
            _UNSAFE_LABEL.search(value) or _PRIVATE_MODEL_HINT.search(value)):
        return ""
    folded = value.casefold()
    family = next((prefix for prefix in _PUBLIC_MODEL_FAMILIES
                   if folded.startswith(prefix)), "")
    if not family:
        return ""
    remainder = folded[len(family):]
    if remainder and remainder[0] not in ".:_+-" and not remainder[0].isdigit():
        return ""
    tokens = [token for token in re.split(r"[._:+-]+", remainder) if token]
    if any(token not in _PUBLIC_MODEL_MODIFIERS and
           not _PUBLIC_MODEL_VERSION.fullmatch(token) for token in tokens):
        return ""
    return value if _known(value) else ""


def _safe_tool_label(value):
    """Project known generic tools only; never display provider identifiers."""
    value = _public_text(value)
    is_mcp = value.casefold().startswith("mcp__")
    terminal = value.rsplit("__", 1)[-1]
    if not _TOOL_NAME.fullmatch(terminal) or _UNSAFE_LABEL.search(terminal):
        return ""
    terminal = re.sub(r"_v\d+$", "", terminal, flags=re.I)
    return _PUBLIC_TOOLS.get(terminal.casefold(), "MCP tools" if is_mcp else "Other tools")


def _in_window(day, start, end):
    return day is not None and start <= day <= end


def _period_rollup(rows, start, end):
    """Aggregate only normalized, dated evidence in one inclusive period."""
    result = {
        "active_days": set(), "sessions": set(), "paired_output": 0,
        "paired_cost": 0.0, "timed_output": 0, "timed_seconds": 0.0,
        "timed_samples": 0, "runtimes": set(), "models": set(),
        "tool_calls": 0, "git_days": set(), "durations": [],
        "runtime_sessions": {}, "model_executions": {}, "speed": {},
        "session_output": {}, "session_runtime": {}, "tools": {}, "daily_total": 0,
        "paired_days": 0, "timing_total": 0, "tool_total": 0,
    }
    for index, row in enumerate(rows or []):
        row = row if isinstance(row, dict) else {}
        session_day = _session_day(row)
        session_id = str(row.get("id") or row.get("path") or index)
        runtime = _safe_runtime(row.get("runtime"))
        if runtime:
            result["session_runtime"][session_id] = runtime
        if _in_window(session_day, start, end):
            result["active_days"].add(session_day)
            result["sessions"].add(session_id)
            if runtime:
                result["runtimes"].add(runtime)
                result["runtime_sessions"].setdefault(runtime, set()).add(session_id)
            if row.get("duration_available"):
                duration = _number(row.get("duration_s"))
                if duration > 0:
                    result["durations"].append((duration, runtime))

        for daily in row.get("_model_daily") or []:
            daily = daily if isinstance(daily, dict) else {}
            day = _date(daily.get("day"))
            if not _in_window(day, start, end):
                continue
            result["daily_total"] += 1
            result["active_days"].add(day)
            model = _safe_model(daily.get("model"))
            if runtime:
                result["runtimes"].add(runtime)
            if runtime and model:
                result["models"].add((runtime, model))
            output = int(_number(daily.get("cost_covered_output_tokens")))
            cost = _number(daily.get("cost_covered_cost"))
            if output > 0 and cost > 0:
                result["paired_output"] += output
                result["paired_cost"] += cost
                result["paired_days"] += 1
            if output > 0:
                result["session_output"][session_id] = result["session_output"].get(session_id, 0) + output
            executions = max(0, int(_number(daily.get("executions"))))
            if runtime and model and executions > 0:
                key = (runtime, model)
                result["model_executions"][key] = result["model_executions"].get(key, 0) + executions

        for sample in row.get("_performance_samples") or []:
            sample = sample if isinstance(sample, dict) else {}
            day = _date(sample.get("day"))
            if not _in_window(day, start, end):
                continue
            result["timing_total"] += 1
            result["active_days"].add(day)
            output = int(_number(sample.get("output_tokens")))
            seconds = _number(sample.get("generation_s") or sample.get("duration_s"))
            if output > 0 and seconds > 0:
                result["timed_output"] += output
                result["timed_seconds"] += seconds
                result["timed_samples"] += 1
                model = _safe_model(sample.get("model"))
                if runtime and model:
                    key = (runtime, model)
                    speed = result["speed"].setdefault(key, [0, 0.0, 0])
                    speed[0] += output
                    speed[1] += seconds
                    speed[2] += 1

        for tool in (row.get("_tool_evidence") or {}).get("tools") or []:
            tool = tool if isinstance(tool, dict) else {}
            name = str(tool.get("name") or "").strip()
            display = _safe_tool_label(name)
            namespace = str(tool.get("namespace") or "").strip().casefold()
            kind = str(tool.get("kind") or "").strip().casefold()
            diagnostic = (kind == "mcp" and namespace == "tokenmeter") or name.casefold().startswith("mcp__tokenmeter__")
            safe_tool = bool(display) and not diagnostic
            for daily in tool.get("daily") or []:
                daily = daily if isinstance(daily, dict) else {}
                day = _date(daily.get("day"))
                if _in_window(day, start, end) and safe_tool:
                    result["active_days"].add(day)
                    result["tool_total"] += 1
                    calls = max(0, int(_number(daily.get("calls"))))
                    result["tool_calls"] += calls
                    if calls > 0:
                        result["tools"][display] = result["tools"].get(display, 0) + calls
    return result


def _stat(stat_id, label, family, available, current, previous, unit, basis,
          sample_count, leaders):
    comparable = available and current is not None and previous is not None
    delta = current - previous if comparable else None
    delta_pct = (delta / previous * 100) if comparable and previous != 0 else None
    return {
        "id": stat_id, "label": label, "family": family,
        "available": bool(available), "current": current, "previous": previous,
        "delta": delta, "delta_pct": delta_pct, "unit": unit, "basis": basis,
        "sample_count": int(sample_count), "leaders": list(leaders or []),
    }


def _streak(days):
    longest = current = 0
    previous = None
    for day in sorted(days):
        current = current + 1 if previous and day == previous + datetime.timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


def _activity_days(rows, git_days, start, end):
    rollup = _period_rollup(rows, start, end)
    return [{"day": day.isoformat(), "recorded_session": day in rollup["active_days"]}
            for day in _window_days(start, end)]


def _changed_line_count(value, maximum=_MAX_RECAP_CHANGED_LINES):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= maximum else None
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and 0 <= value <= maximum:
            return int(value)
    return None


def _git_days(git_days, start, end):
    if git_days is None:
        return None
    measured = set()
    active = set()
    line_measured = set()
    commit_measured = set()
    changed_lines = 0
    commits = 0
    for item in git_days or []:
        if not isinstance(item, dict) or item.get("available") is not True:
            continue
        value = item.get("day")
        day = _date(value)
        if not _in_window(day, start, end):
            continue
        measured.add(day)
        if item.get("active") is True:
            active.add(day)
        commit_count = _changed_line_count(
            item.get("commits"), _MAX_DAILY_CHANGED_LINES,
        )
        if commit_count is not None:
            commit_measured.add(day)
            commits += commit_count
        line_count = _changed_line_count(
            item.get("changed_lines"), _MAX_DAILY_CHANGED_LINES,
        )
        if line_count is None:
            continue
        line_measured.add(day)
        changed_lines += line_count
    return {
        "measured": measured, "active": active,
        "line_measured": line_measured, "changed_lines": changed_lines,
        "commit_measured": commit_measured, "commits": commits,
    }


def _efficiency(rollup):
    if rollup["paired_output"] <= 0 or rollup["paired_cost"] <= 0:
        return None
    return rollup["paired_output"] / rollup["paired_cost"]


def _leaders(ranked_values):
    """Return one or two deterministic safe display leaders, or no result."""
    if not ranked_values:
        return None
    best = max(value for value, _ in ranked_values)
    leaders = [leader for value, leader in ranked_values if value == best]
    unique = {}
    for leader in leaders:
        key = tuple(sorted((str(key), str(value)) for key, value in leader.items()))
        unique[key] = leader
    leaders = list(unique.values())
    if len(leaders) > 2:
        return None
    return sorted((dict(leader) for leader in leaders),
                  key=lambda row: tuple(str(row[key]) for key in sorted(row)))


def _ranked(mapping, kind):
    if kind == "runtime":
        return [(len(sessions), {"runtime": runtime})
                for runtime, sessions in mapping.items()]
    if kind == "model":
        return [(value, {"runtime": runtime, "model": model})
                for (runtime, model), value in mapping.items()]
    if kind == "tool":
        return [(value, {"tool": tool}) for tool, value in mapping.items()]
    return [(value, {"runtime": runtime}) for value, runtime in mapping]


def _usage_rows(mapping, kind):
    """Return a capped share ranking built only from sanitized rollup keys."""
    if kind == "agent":
        ranked = [(len(sessions), runtime, "")
                  for runtime, sessions in mapping.items()]
    else:
        ranked = [(count, model, runtime)
                  for (runtime, model), count in mapping.items()]
    ranked = [row for row in ranked if row[0] > 0]
    total = sum(count for count, _, _ in ranked)
    rows = []
    for count, label, runtime in sorted(
            ranked, key=lambda row: (-row[0], row[2], row[1]))[:3]:
        item = {"label": label, "count": count,
                "share": count / total * 100 if total else 0.0}
        if kind == "model":
            item["runtime"] = runtime
        rows.append(item)
    return rows


def _super_stat(stat_id, label, family, current, previous, unit, basis,
                sample_count, ranked_values=(), available=None):
    leaders = _leaders(ranked_values)
    if available is None:
        available = current is not None and (not ranked_values or leaders is not None)
    return _stat(stat_id, label, family, available, current, previous, unit,
                 basis, sample_count, leaders or [])


def _spotlights(current, previous, history):
    current_efficiency = _efficiency(current)
    previous_efficiency = _efficiency(previous)
    historical_efficiency = [_efficiency(previous)] + [_efficiency(window) for window in history]
    record_ready = (current_efficiency is not None and len(historical_efficiency) >= 3
                    and all(value is not None for value in historical_efficiency))
    record = _super_stat(
        "efficiency_record", "New efficiency best", "efficiency",
        current_efficiency, None, "output tokens/$",
        "paired cost-covered output and cost across contiguous periods",
        current["paired_output"], available=record_ready and
        all(current_efficiency > value for value in historical_efficiency))
    change = _super_stat(
        "efficiency_change", "Output efficiency", "efficiency",
        current_efficiency, previous_efficiency, "output tokens/$",
        "paired cost-covered output and cost", current["paired_output"],
        available=current_efficiency is not None and previous_efficiency is not None)
    current_duration = max((value for value, _ in current["durations"]), default=None)
    previous_duration = max((value for value, _ in previous["durations"]), default=None)
    marathon = _super_stat(
        "marathon_session", "Longest session", "activity", current_duration,
        previous_duration, "seconds", "active execution duration", len(current["durations"]),
        _ranked([(value, runtime) for value, runtime in current["durations"]], "duration"),
        current_duration is not None)
    speed_values = []
    for (runtime, model), (output, seconds, samples) in current["speed"].items():
        if samples >= 3 and output >= 1000 and seconds > 0:
            speed_values.append((output / seconds, {"runtime": runtime, "model": model}))
    speed_current = max((value for value, _ in speed_values), default=None)
    speed = _super_stat("speed_champion", "Fastest model", "efficiency", speed_current,
                        None, "output tokens/s", "weighted measured generation samples",
                        sum(values[2] for values in current["speed"].values()), speed_values,
                        speed_current is not None and _leaders(speed_values) is not None)
    streak = _super_stat("build_streak", "Build streak", "consistency",
                         _streak(current["active_days"]), _streak(previous["active_days"]),
                         "days", "dated session evidence", len(current["active_days"]),
                         available=bool(current["active_days"]))
    drivers = _ranked(current["runtime_sessions"], "runtime")
    driver = _super_stat("daily_driver", "Most-used agent", "activity",
                         max((value for value, _ in drivers), default=None), None,
                         "sessions", "distinct selected-period sessions", len(current["sessions"]),
                         drivers)
    models = _ranked(current["model_executions"], "model")
    go_to = _super_stat("go_to_model", "Go-to model", "stack",
                        max((value for value, _ in models), default=None), None,
                        "executions", "selected-period model executions",
                        sum(current["model_executions"].values()), models)
    builds = []
    for session_id, output in current["session_output"].items():
        runtime = current["session_runtime"].get(session_id, "")
        if runtime:
            builds.append((output, {"runtime": runtime}))
    biggest = _super_stat("biggest_build", "Biggest build", "activity",
                          max((value for value, _ in builds), default=None), None,
                          "output tokens", "selected-period covered output", len(builds), builds)
    tools = _ranked(current["tools"], "tool")
    tool = _super_stat("tool_mvp", "Tool MVP", "tools",
                       max((value for value, _ in tools), default=None), None,
                       "calls", "dated safe tool evidence", sum(current["tools"].values()), tools)
    stack_count = len(current["runtimes"]) + len(current["models"])
    explorer = _super_stat("stack_explorer", "Stack explorer", "stack", stack_count,
                           len(previous["runtimes"]) + len(previous["models"]), "items",
                           "known runtime-scoped stack evidence", stack_count,
                           available=stack_count > 0)
    return [record, change, marathon, speed, streak, driver, go_to, biggest, tool, explorer]


def _supporting_stats(current, previous, spotlight_family):
    current_efficiency = _efficiency(current)
    previous_efficiency = _efficiency(previous)
    candidates = [
        _stat("active_days", "Active days", "consistency", True, len(current["active_days"]), len(previous["active_days"]), "days", "dated session evidence", len(current["active_days"]), []),
        _stat("sessions", "AI coding sessions", "activity", True, len(current["sessions"]), len(previous["sessions"]), "sessions", "last recorded activity day", len(current["sessions"]), []),
        _stat("output_pace", "Output pace", "efficiency", current["timed_output"] > 0 and current["timed_seconds"] > 0 and previous["timed_output"] > 0 and previous["timed_seconds"] > 0, current["timed_output"] / current["timed_seconds"] if current["timed_seconds"] else None, previous["timed_output"] / previous["timed_seconds"] if previous["timed_seconds"] else None, "output tokens/s", "dated measured generation samples", current["timed_samples"], []),
        _stat("covered_output_per_dollar", "Covered Output / $", "efficiency", current_efficiency is not None and previous_efficiency is not None, current_efficiency, previous_efficiency, "output tokens/$", "paired cost-covered output and cost", current["paired_output"], []),
        _stat("covered_spend", "Covered equivalent spend", "cost", current["paired_cost"] > 0, current["paired_cost"] if current["paired_cost"] > 0 else None, previous["paired_cost"] if previous["paired_cost"] > 0 else None, "USD", "paired cost-covered output and cost", current["paired_days"], []),
        _stat("runtimes", "Unique runtimes", "stack", True, len(current["runtimes"]), len(previous["runtimes"]), "runtimes", "known runtime session evidence", len(current["runtimes"]), []),
        _stat("models", "Unique models", "stack", True, len(current["models"]), len(previous["models"]), "models", "runtime-scoped known model evidence", len(current["models"]), []),
        _stat("tool_calls", "Tool calls", "tools", True, current["tool_calls"], previous["tool_calls"], "calls", "dated safe tool evidence", current["tool_calls"], []),
    ]
    return [stat for stat in candidates
            if stat["available"] and stat["family"] != spotlight_family]


def _choose_default_spotlight(spotlights):
    by_id = {stat["id"]: stat for stat in spotlights}
    for stat_id in ("efficiency_change", "efficiency_record", "build_streak",
                    "marathon_session", "speed_champion", "daily_driver", "go_to_model",
                    "lines_pushed", "biggest_build", "tool_mvp", "stack_explorer"):
        stat = by_id[stat_id]
        improved = stat_id not in {"build_streak", "marathon_session", "efficiency_change"} or (stat["delta"] is not None and stat["delta"] > 0)
        if stat["available"] and improved:
            return stat_id
    return None


def build_builder_recap(session_rows, git_days, range_days, *, today=None,
                        generated_at=None, previous_git_lines=None,
                        previous_git_commits=None):
    range_days = int(range_days)
    if range_days not in VALID_RECAP_RANGES:
        raise ValueError("range_days must be 7, 30, or 90")
    today = today or datetime.date.today()
    current_start, current_end, previous_start, previous_end = _period_windows(range_days, today)
    current = _period_rollup(session_rows, current_start, current_end)
    previous = _period_rollup(session_rows, previous_start, previous_end)
    history = []
    history_end = previous_start - datetime.timedelta(days=1)
    for _ in range(2):
        history_start = history_end - datetime.timedelta(days=range_days - 1)
        history.append(_period_rollup(session_rows, history_start, history_end))
        history_end = history_start - datetime.timedelta(days=1)
    current_git = _git_days(git_days, current_start, current_end)
    previous_git = _git_days(git_days, previous_start, previous_end)
    current_efficiency = _efficiency(current)
    spotlights = _spotlights(current, previous, history)
    current_lines_measured = bool(current_git and current_git["line_measured"])
    previous_line_count = _changed_line_count(previous_git_lines)
    if previous_line_count is None and previous_git and previous_git["line_measured"]:
        previous_line_count = previous_git["changed_lines"]
    spotlights.append(_stat(
        "lines_pushed", "Lines pushed", "delivery", current_lines_measured,
        current_git["changed_lines"] if current_lines_measured else None,
        previous_line_count,
        "lines", "added plus deleted text lines from successful local pushes",
        len(current_git["line_measured"]) if current_git else 0, [],
    ))
    current_commits_measured = bool(current_git and current_git["commit_measured"])
    previous_commit_count = _changed_line_count(previous_git_commits)
    if previous_commit_count is None and previous_git and previous_git["commit_measured"]:
        previous_commit_count = previous_git["commits"]
    spotlights.append(_stat(
        "commits_pushed", "Commits pushed", "delivery", current_commits_measured,
        current_git["commits"] if current_commits_measured else None,
        previous_commit_count,
        "commits", "own non-merge commits introduced by successful local pushes",
        len(current_git["commit_measured"]) if current_git else 0, [],
    ))
    spotlight_default = _choose_default_spotlight(spotlights)
    spotlight_family = next((stat["family"] for stat in spotlights
                             if stat["id"] == spotlight_default), None)
    supporting = _supporting_stats(current, previous, spotlight_family)
    current_git_measured = bool(current_git and current_git["measured"])
    previous_git_measured = bool(previous_git and previous_git["measured"])
    if current_git_measured and spotlight_family != "delivery":
        supporting.append(_stat(
            "delivery_active_days", "Delivery-active days", "delivery", True,
            len(current_git["active"]),
            len(previous_git["active"]) if previous_git_measured else None,
            "days", "measured local Git push evidence", len(current_git["measured"]), []))
    support_order = {"active_days": 0, "sessions": 1, "delivery_active_days": 2,
                     "output_pace": 3, "covered_output_per_dollar": 4,
                     "covered_spend": 5, "runtimes": 6, "models": 7,
                     "tool_calls": 8}
    supporting = sorted(supporting, key=lambda stat: support_order[stat["id"]])
    return {
        "ok": True,
        "generated_at": int(generated_at if generated_at is not None else time.time()),
        "range_days": range_days,
        "current": {"start_day": current_start.isoformat(), "end_day": current_end.isoformat()},
        "previous": {"start_day": previous_start.isoformat(), "end_day": previous_end.isoformat()},
        "spotlight_default": spotlight_default,
        "spotlights": spotlights,
        "supporting": supporting,
        "activity_days": _activity_days(session_rows, git_days, current_start, current_end),
        "stack": {
            "runtimes": sorted(current["runtimes"]),
            "models": [{"runtime": runtime, "model": model}
                       for runtime, model in sorted(current["models"])],
        },
        "usage": {
            "agents": _usage_rows(current["runtime_sessions"], "agent"),
            "models": _usage_rows(current["model_executions"], "model"),
        },
        "coverage": {
            "cost": {"available": current_efficiency is not None,
                     "eligible": current["paired_days"], "total": current["daily_total"],
                     "basis": "paired cost-covered output and cost"},
            "output": {"available": current["paired_output"] > 0,
                       "eligible": current["paired_days"], "total": current["daily_total"],
                       "basis": "cost-covered output evidence"},
            "timing": {"available": current["timed_samples"] > 0,
                       "eligible": current["timed_samples"], "total": current["timing_total"],
                       "basis": "measured generation samples"},
            "tools": {"available": current["tool_total"] > 0,
                      "eligible": sum(1 for value in current["tools"].values() if value > 0),
                      "total": current["tool_total"], "basis": "dated safe tool evidence"},
            "git": {"projection_available": git_days is not None,
                    "available": current_git_measured,
                    "eligible": len(current_git["measured"]) if current_git else 0, "total": range_days,
                    "basis": "measured local Git push evidence"},
        },
        "privacy": {"content_included": False, "project_identity_included": False},
    }
