"""Codex trace helpers used by the composition module."""

import json
import os
import re
import time

from token_meter.domain.usage import normalize_reported_token_count as _domain_normalize_reported_token_count
from token_meter.domain import work_evidence as _work_evidence
from token_meter.quotas.common import quota_http_json
from token_meter.quotas import openai as openai_quotas
from token_meter.app_helpers.common import (
    USER_TURN_CONTEXT_CHARS,
    _dedupe_user_turns,
    _wait_sample,
    parse_iso,
    text_from_content,
    usage_io_tokens,
)


CODEX_AUTH = os.path.expanduser("~/.codex/auth.json")


def codex_id_from_path(path, meta=None):
    if meta and meta.get("session_id"):
        return meta["session_id"]
    base = os.path.basename(path).rsplit(".", 1)[0]
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", base)
    return match.group(1) if match else base


def codex_performance_samples(objs, default_model=None):
    """Return completed Codex task samples with model-attributable timing."""
    model = default_model or "unknown-model"
    samples = []
    current = None

    def ensure_current(ts=0):
        nonlocal current
        if current is None:
            current = {"started_ts": ts or 0, "usage": {}, "tool_calls": 0}
        return current

    def close_task(payload, ts):
        nonlocal current
        task = current
        current = None
        if not task or len(task["usage"]) != 1:
            return
        duration_ms = payload.get("duration_ms")
        ttft_ms = payload.get("time_to_first_token_ms")
        try:
            duration_ms = float(duration_ms or 0)
            ttft_ms = float(ttft_ms or 0)
        except (TypeError, ValueError):
            return
        if duration_ms <= 0:
            return
        sample_model, counts = next(iter(task["usage"].items()))
        output_tokens = int(counts.get("output_tokens") or 0)
        if output_tokens <= 0:
            return
        duration_s = duration_ms / 1000.0
        generation_s = (duration_ms - ttft_ms) / 1000.0 if 0 < ttft_ms < duration_ms else duration_s
        samples.append({
            "provider": "codex", "model": sample_model,
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0, "input_tokens": int(counts.get("input_tokens") or 0),
            "output_tokens": output_tokens, "duration_s": duration_s,
            "peak_input_tokens": int(counts.get("peak_input_tokens") or 0),
            "uncached_input_tokens": int(counts.get("uncached_input_tokens") or 0),
            "cache_read_tokens": int(counts.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(counts.get("cache_write_tokens") or 0),
            "model_calls": int(counts.get("model_calls") or 0),
            "generation_s": generation_s, "ttft_s": max(0.0, ttft_ms / 1000.0),
            "tool_calls": int(task.get("tool_calls") or 0), "timing_basis": "task_complete",
        })

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            continue
        if ptype == "task_started":
            current = {"started_ts": ts, "usage": {}, "tool_calls": 0}
            continue
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            ensure_current(ts)["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if not raw:
                continue
            usage = codex_usage(raw)
            task = ensure_current(ts)
            row = task["usage"].setdefault(model, {
                "input_tokens": 0, "output_tokens": 0, "peak_input_tokens": 0,
                "uncached_input_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "model_calls": 0,
            })
            input_count, output_count = usage_io_tokens(usage)
            row["input_tokens"] += input_count
            row["output_tokens"] += output_count
            row["peak_input_tokens"] = max(row["peak_input_tokens"], input_count)
            row["uncached_input_tokens"] += int(usage.get("input_tokens") or 0)
            row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
            row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
            row["model_calls"] += 1
            continue
        if ptype == "task_complete":
            close_task(payload, ts)
    return samples


def codex_live_performance_summary(objs):
    """Return provisional pace from completed checkpoints in the active task.

    Final throughput remains gated on ``task_complete`` in
    ``codex_performance_samples``. This separate summary lets the native menu
    show useful progress sooner without admitting an unfinished task into
    historical/model analytics. The denominator stops at the latest completed
    token checkpoint, so the displayed value changes only when another step
    completes rather than decaying while a tool is still running.
    """
    current = None

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if ptype == "task_started":
            current = {
                "started_ts": ts,
                "latest_checkpoint_ts": 0,
                "output_tokens": 0,
                "completed_steps": 0,
            }
            continue
        if ptype == "task_complete":
            current = None
            continue
        if ptype != "token_count" or not current:
            continue
        raw = ((payload.get("info") or {}).get("last_token_usage") or {})
        if not raw:
            continue
        output_tokens = usage_io_tokens(codex_usage(raw))[1]
        if output_tokens <= 0 or not ts:
            continue
        current["output_tokens"] += output_tokens
        current["completed_steps"] += 1
        current["latest_checkpoint_ts"] = max(
            float(current.get("latest_checkpoint_ts") or 0), ts,
        )

    empty = {
        "available": False,
        "output_tps": 0,
        "basis": "unavailable",
        "completed_steps": 0,
        "measured_output_tokens": 0,
        "measured_seconds": 0,
    }
    if not current:
        return empty
    started_ts = float(current.get("started_ts") or 0)
    checkpoint_ts = float(current.get("latest_checkpoint_ts") or 0)
    measured_seconds = checkpoint_ts - started_ts
    measured_output = int(current.get("output_tokens") or 0)
    completed_steps = int(current.get("completed_steps") or 0)
    if measured_seconds <= 0 or measured_output <= 0 or completed_steps <= 0:
        return empty
    return {
        "available": True,
        "output_tps": measured_output / measured_seconds,
        "basis": "live_end_to_end",
        "completed_steps": completed_steps,
        "measured_output_tokens": measured_output,
        "measured_seconds": measured_seconds,
    }


def codex_wait_samples(objs, default_model=None):
    """Return completed Codex task prompt-to-response wait samples."""
    samples = []
    model = default_model or "unknown-model"
    current = None

    def close_task(end_ts=0, duration_ms=0, allow_observed=False):
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
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "codex")
        if sample:
            samples.append(sample)

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            if current:
                current["models"].add(model)
            continue
        if ptype == "task_started":
            close_task(allow_observed=False)
            current = {
                "start_ts": ts, "last_ts": ts, "models": {model},
                "tool_calls": 0, "output_tokens": 0,
            }
            continue
        if not current:
            continue
        current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            current["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if raw:
                current["output_tokens"] += usage_io_tokens(codex_usage(raw))[1]
            continue
        if ptype == "task_complete":
            close_task(ts, payload.get("duration_ms"), allow_observed=True)
    return samples


def codex_usage(raw):
    raw = raw or {}
    input_total, input_reported = _domain_normalize_reported_token_count(
        raw.get("input_tokens")
    )
    cached, cache_reported = _domain_normalize_reported_token_count(
        raw.get("cached_input_tokens", 0)
    )
    output_tokens, output_reported = _domain_normalize_reported_token_count(
        raw.get("output_tokens")
    )
    total_tokens, _ = _domain_normalize_reported_token_count(
        raw.get("total_tokens", 0)
    )
    input_available = input_reported and cache_reported and cached <= input_total
    reasoning_tokens, reasoning_available = _domain_normalize_reported_token_count(
        raw.get("reasoning_output_tokens")
    )
    reasoning_available = (
        output_reported
        and reasoning_available
        and reasoning_tokens <= output_tokens
    )
    return {
        "input_tokens": max(0, input_total - cached) if input_available else 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cached if input_available else 0,
        "output_tokens": output_tokens if output_reported else 0,
        "input_available": input_available,
        "output_available": output_reported,
        "reasoning_output_tokens": reasoning_tokens if reasoning_available else 0,
        "reasoning_available": reasoning_available,
        "total_tokens": total_tokens,
    }


def _codex_fallback_user_text(payload):
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content")
    blocks = content if isinstance(content, list) else [content]
    human_blocks = []
    for block in blocks:
        text = text_from_content([block] if isinstance(content, list) else block)
        stripped = text.strip()
        if not stripped or stripped.startswith((
                "<recommended_plugins>",
                "# AGENTS.md instructions",
                "<environment_context>",
        )):
            continue
        human_blocks.append(text)
    return "\n".join(human_blocks) if human_blocks else None


def codex_user_turns(objs, default_model=None):
    """Prefer canonical user_message events; fall back for older Codex logs."""
    current_model = default_model or "unknown-model"
    event_turns = []
    fallback_turns = []
    context = ""
    for obj in objs or []:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "turn_context":
            current_model = payload.get("model") or current_model
            continue
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if payload.get("type") == "agent_message" and isinstance(payload.get("message"), str):
            if payload["message"].strip():
                context = payload["message"].strip()[-USER_TURN_CONTEXT_CHARS:]
            continue
        if payload.get("type") == "message" and payload.get("role") == "assistant":
            # Current Codex rollouts carry assistant text only as response_item messages.
            said = text_from_content(payload.get("content")).strip()
            if said:
                context = said[-USER_TURN_CONTEXT_CHARS:]
            continue
        if payload.get("type") == "user_message":
            event_turns.append({"ts": ts, "text": payload.get("message") or "",
                                "model": current_model, "context": context})
            continue
        text = _codex_fallback_user_text(payload)
        if text is not None:
            fallback_turns.append({"ts": ts, "text": text, "model": current_model, "context": context})
    return _dedupe_user_turns(event_turns or fallback_turns)


def codex_work_actions(objs):
    """Tool calls in a Codex rollout as Work evidence, including tools called from code-mode scripts."""
    actions = []
    for obj in objs or ():
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        if ptype not in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
            continue
        ts = parse_iso(obj.get("timestamp", "")) or 0
        name = str(payload.get("name") or "")
        if ptype == "web_search_call":
            actions.append(_work_evidence.tool_action("web_search", None, ts))
        elif ptype == "local_shell_call":
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            actions.append(_work_evidence.tool_action("shell", {"command": action.get("command")}, ts))
        elif ptype == "custom_tool_call" and name == "exec":
            actions.extend(_work_evidence.code_actions(payload.get("input"), ts))
        elif ptype == "function_call" and name == "js":
            arguments = payload.get("arguments")
            try:
                code = json.loads(arguments).get("code") if isinstance(arguments, str) else None
            except (ValueError, AttributeError):
                code = None
            actions.extend(_work_evidence.code_actions(code or arguments, ts))
        else:
            actions.append(_work_evidence.tool_action(name, payload.get("arguments") or payload.get("input"), ts))
    return actions


def codex_approval_policy_label(value):
    """Return a compact label for legacy strings and structured Codex policies."""
    if isinstance(value, dict):
        value = next(iter(value), "")
    return str(value or "").replace("_", " ")


def _codex_limit_label(value):
    return openai_quotas.limit_label(value)


def _codex_window(provider_id, raw, kind, label, now=None):
    return openai_quotas._window(provider_id, raw, kind, label, now=now)


def codex_oauth_quota(now=None, opener=None):
    return openai_quotas.oauth_quota(
        CODEX_AUTH, quota_http_json, now=now, opener=opener
    )
