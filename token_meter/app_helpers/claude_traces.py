"""Claude trace helpers used by the composition module."""

import os
from collections import defaultdict
from urllib.parse import quote

from token_meter.domain.usage import normalize_reported_token_count as _domain_normalize_reported_token_count
from token_meter.quotas import anthropic as anthropic_quotas
from token_meter.app_helpers.common import (
    USER_TURN_CONTEXT_CHARS,
    _CLAUDE_FAST_PREMIUM_RULES,
    _close_claude_user_pauses,
    _dedupe_user_turns,
    _same_path,
    _track_claude_user_pause,
    _wait_sample,
    compact_text,
    observable_output_chars,
    parse_iso,
    tool_result_is_error,
)


def _claude_user_prompt(obj):
    if obj.get("type") != "user":
        return False
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
        for block in content
    )


def _claude_effective_duration(group, duration_s, timing_basis):
    """Remove trace-visible human-response pauses from observed wall time."""
    duration_s = float(duration_s or 0)
    paused_s = float((group or {}).get("user_pause_s") or 0)
    excluded_s = min(duration_s, paused_s) if timing_basis == "observed" else 0.0
    return max(0.0, duration_s - excluded_s), excluded_s


_CLAUDE_FAST_STANDARD_RULES = frozenset(("claude-opus-4-6",))


_CLAUDE_INFERENCE_GEO_RULES = frozenset((
    "claude-mythos-5", "claude-mythos-5-1", "claude-mythos-5-2",
    "claude-fable-5", "claude-fable-5-1", "claude-fable-5-2",
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5",
    "claude-sonnet-4-6", "claude-haiku-5-5",
))


def _claude_quote_supports_dimensions(usage, quote):
    if not quote.available or not usage.get("billing_available"):
        return False
    matched_rule = quote.matched_rule
    if (usage.get("speed") == "fast" and
            matched_rule not in (
                _CLAUDE_FAST_PREMIUM_RULES | _CLAUDE_FAST_STANDARD_RULES
            )):
        return False
    if (usage.get("inference_geo") in ("global", "us") and
            matched_rule not in _CLAUDE_INFERENCE_GEO_RULES):
        return False
    return True


def claude_wait_samples(objs):
    """Return completed Claude prompt-to-response wait samples.

    Wait time is deliberately end to end: reasoning, tool use, and model output
    all count because the user is still waiting for the turn to finish.
    """
    samples = []
    current = None

    def close_turn(end_ts=0, duration_ms=0, allow_observed=False):
        nonlocal current
        group = current
        current = None
        if not group:
            return
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            basis = "reported"
        elif allow_observed:
            end_ts = end_ts or group.get("last_ts") or 0
            duration_s = float(end_ts or 0) - float(group.get("start_ts") or 0)
            basis = "observed"
        else:
            return
        wall_duration_s = duration_s
        duration_s, user_pause_s = _claude_effective_duration(group, duration_s, basis)
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "claude", wall_duration_s, user_pause_s)
        if sample:
            samples.append(sample)

    for obj in objs:
        otype = obj.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if _claude_user_prompt(obj):
            close_turn(allow_observed=bool(current and current.get("terminal")))
            current = {
                "start_ts": ts, "last_ts": ts, "models": set(),
                "tool_ids": set(), "tool_calls": 0, "output_tokens": 0,
                "seen_usage": set(), "terminal": False,
                "user_pause_starts": {}, "user_pause_s": 0.0,
            }
            continue
        if otype == "user" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            _close_claude_user_pauses(current, msg, ts)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            continue
        if otype == "assistant" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            model = msg.get("model")
            if model:
                current["models"].add(model)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            message_id = msg.get("id") or obj.get("uuid")
            if message_id not in current["seen_usage"]:
                current["seen_usage"].add(message_id)
                output_tokens, _ = _domain_normalize_reported_token_count(
                    (msg.get("usage") or {}).get("output_tokens")
                )
                current["output_tokens"] += output_tokens
            for block in msg.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id") or (block.get("name"), len(current["tool_ids"]))
                current["tool_ids"].add(tool_id)
                _track_claude_user_pause(current, block, ts)
            current["tool_calls"] = len(current["tool_ids"])
            if msg.get("stop_reason") and msg.get("stop_reason") != "tool_use":
                current["terminal"] = True
            continue
        if otype == "system" and obj.get("subtype") == "turn_duration":
            close_turn(ts, obj.get("durationMs"), allow_observed=True)
    if current and current.get("terminal"):
        close_turn(current.get("last_ts") or 0, allow_observed=True)
    return samples


def claude_user_text(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in (None, "text"):
            pieces.append(block.get("text") or block.get("content") or "")
    return " ".join(p for p in pieces if isinstance(p, str))


def _claude_human_text(obj):
    """Return human-authored Claude text, None for tool/meta/user-shaped records."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return None
    if obj.get("sourceToolAssistantUUID") or obj.get("toolUseResult") is not None:
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):
        text_blocks = [
            block.get("text") or block.get("content") or ""
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        if not text_blocks and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content
        ):
            return None
        text = " ".join(value for value in text_blocks if isinstance(value, str))
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    stripped = text.strip()
    meta_prefixes = (
        "<local-command-caveat>", "<local-command-stdout>",
        "<command-name>", "<system-reminder>",
    )
    if stripped.startswith(meta_prefixes):
        return None
    return text


def claude_user_turns(objs, default_model=None):
    turns = []
    pending = []
    current_model = default_model or "unknown-model"
    context = ""
    for obj in objs or []:
        text = _claude_human_text(obj)
        if text is not None:
            turn = {
                "ts": parse_iso(obj.get("timestamp", "")) or 0,
                "text": text,
                "model": None,
                "context": context,
            }
            turns.append(turn)
            pending.append(turn)
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        if isinstance(content, list) and not obj.get("isSidechain"):
            said = " ".join(
                block.get("text") or "" for block in content
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ).strip()
            if said:
                context = said[-USER_TURN_CONTEXT_CHARS:]
        model = msg.get("model") or current_model
        current_model = model
        if pending:
            for turn in pending:
                turn["model"] = model
            pending = []
    for turn in pending:
        turn["model"] = current_model
    return _dedupe_user_turns(turns)


def claude_user_events(objs):
    events = []
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        txt = compact_text(claude_user_text(msg), 220)
        if txt:
            events.append({"ts": parse_iso(obj.get("timestamp", "")) or 0, "text": txt})
    return sorted(events, key=lambda e: e["ts"])


def claude_tool_results(objs):
    chars_by_id = defaultdict(int)
    ts_by_id = {}
    errors_by_id = defaultdict(bool)
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                chars_by_id[tid] += observable_output_chars(block.get("content", ""))
                ts_by_id[tid] = parse_iso(obj.get("timestamp", ""))
                errors_by_id[tid] = errors_by_id[tid] or tool_result_is_error(
                    block.get("content"), block.get("is_error") is True
                )
    return chars_by_id, ts_by_id, errors_by_id


def claude_desktop_access_entry(launcher):
    env = {"TOKEN_METER_CALLER": "claude"}
    # Electron's spawn refuses .cmd/.bat without a shell, so Windows launchers go through cmd.exe.
    if str(launcher).lower().endswith((".cmd", ".bat")):
        return {"command": "cmd.exe", "args": ["/c", launcher], "env": env}
    return {"command": launcher, "args": [], "env": env}


def _claude_desktop_entry_matches(row, launcher):
    if not isinstance(row, dict):
        return False
    env = row.get("env")
    args = row.get("args", [])
    if not isinstance(env, dict) or str(env.get("TOKEN_METER_CALLER") or "") != "claude" or not isinstance(args, list):
        return False
    expected = claude_desktop_access_entry(launcher)
    if expected["command"] == "cmd.exe":
        return (os.path.basename(str(row.get("command") or "")).lower() in ("cmd", "cmd.exe")
                and len(args) == 2 and str(args[0]).lower() == "/c" and _same_path(args[1], launcher))
    return _same_path(row.get("command"), launcher) and args == []


def _claude_part_todo(part, enabled):
    return bool(part.get("detected") and ((not part.get("connected")) if enabled else part.get("configured")))


def _claude_app_stuck(app):
    return bool(app.get("unreadable") or app.get("app_running"))


def _claude_window(field, raw, kind, label, duration, now=None):
    return anthropic_quotas._window(field, raw, kind, label, duration, now=now)
