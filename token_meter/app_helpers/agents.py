"""Agent identity and presentation helpers."""

import datetime
import os
import shlex
import time

from token_meter.app_helpers.common import compact_text


def agent_access_command_display(command):
    command = list(command or [])
    if command:
        command[0] = os.path.basename(command[0])
    return shlex.join(command)


def agent_cli_error_detail(completed):
    raw = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""
    return compact_text(lines[-1].replace(os.path.expanduser("~"), "~"), 220)


def _agent_access_matches(command, args, env, launcher, runtime):
    try:
        command_match = os.path.realpath(os.path.expanduser(str(command or ""))) == os.path.realpath(launcher)
    except OSError:
        command_match = False
    return bool(command_match and list(args or []) == [] and str((env or {}).get("TOKEN_METER_CALLER") or "") == runtime)


CLAUDE_CODE_CLIENT = "claude_code"


def _agent_skill_part(client):
    client = str(client or "").strip().lower()
    return {"codex": "codex", "claude": CLAUDE_CODE_CLIENT}.get(client)


AGENT_USAGE_WINDOWS = {"today": 1, "7d": 7, "14d": 14}


def agent_as_of():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def agent_project_key(value):
    value = str(value or "").strip()
    if not value or value == "No project":
        return ""
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def agent_project_name(value):
    value = str(value or "").strip().rstrip("/\\")
    if not value or value == "No project":
        return "No project"
    return compact_text(value.replace("\\", "/").rsplit("/", 1)[-1], 52)


def agent_usage_day_keys(window, today=None):
    today = today or datetime.date.today()
    day_count = AGENT_USAGE_WINDOWS[window]
    return {
        (today - datetime.timedelta(days=offset)).isoformat()
        for offset in range(day_count)
    }


def agent_usage_tool_categories(session_rows, day_keys, limit=5):
    categories = {}
    total_tokens = 0
    flagged_tokens = 0
    for session in session_rows or []:
        evidence = session.get("_tool_evidence") or {}
        total_tokens += sum(
            int(value or 0) for day, value in (evidence.get("day_tokens") or {}).items()
            if day in day_keys
        )
        flagged_tokens += sum(
            int(value or 0) for day, value in (evidence.get("day_flagged") or {}).items()
            if day in day_keys
        )
        provider = session.get("provider") or "unknown"
        for tool in evidence.get("tools") or []:
            name = tool.get("name") or "?"
            namespace = tool.get("namespace") or "unknown"
            kind = tool.get("kind") or "tool"
            diagnostic = kind == "mcp" and (
                namespace == "tokenmeter" or str(name).startswith("mcp__tokenmeter__")
            )
            if diagnostic:
                continue
            key = (name, namespace, "" if kind == "mcp" else provider)
            row = categories.setdefault(key, {
                "name": tool.get("display") or name,
                "namespace": namespace, "returned_tokens": 0, "calls": 0,
            })
            daily_rows = tool.get("daily") or []
            if isinstance(daily_rows, dict):
                daily_rows = daily_rows.values()
            for daily in daily_rows:
                if daily.get("day") not in day_keys:
                    continue
                row["returned_tokens"] += int(daily.get("output_tokens") or 0)
                row["calls"] += int(daily.get("calls") or 0)
    ranked = sorted(
        (row for row in categories.values() if row["calls"] or row["returned_tokens"]),
        key=lambda row: (-row["returned_tokens"], -row["calls"], row["name"]),
    )[:limit]
    return ranked, total_tokens, flagged_tokens
