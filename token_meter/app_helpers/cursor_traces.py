"""Cursor trace helpers used by the composition module."""

import datetime
import math
import re

from token_meter.models.catalog import CURSOR_VARIANT_MODEL_IDS
from token_meter.models.pricing import model_alias_matches as _catalog_model_alias_matches
from token_meter.runtimes.cursor import cursor_model_name
from token_meter.app_helpers.common import (
    CHARS_PER_TOKEN,
    _merge_execution_intervals,
    parse_iso,
    text_from_content,
)


def cursor_model(composer, header=None):
    return cursor_model_name(composer, header)


def cursor_model_parameters(composer, model=None):
    """Return persisted parameters for the selected Cursor model."""
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    selected = config.get("selectedModels") if isinstance(config, dict) else []
    for row in selected or []:
        if not isinstance(row, dict):
            continue
        params = {
            str(item.get("id")): item.get("value")
            for item in (row.get("parameters") or []) if isinstance(item, dict) and item.get("id")
        }
        if not model or str(row.get("modelId") or "") == str(model):
            return params
    return {}


def cursor_price_variant(composer, model, spawn_model=""):
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    if isinstance(config, dict) and config.get("maxMode") is True:
        for row in config.get("selectedModels") or []:
            model_id = str(row.get("modelId") or "") if isinstance(row, dict) else ""
            if model_id and _catalog_model_alias_matches(model, "cursor", model_id):
                if str(cursor_model_parameters(composer, model_id).get("fast")).lower() == "true":
                    return "max-fast"
                break
        return "max"
    for cursor_model_id in CURSOR_VARIANT_MODEL_IDS:
        if not _catalog_model_alias_matches(model, "cursor", cursor_model_id):
            continue
        params = cursor_model_parameters(composer, cursor_model_id)
        fast = str(params.get("fast")).lower()
        if fast not in ("true", "false"):
            # A subagent's own config may omit speed; its parent's spawn call names the exact variant.
            fast = {cursor_model_id + "-fast": "true", cursor_model_id + "-standard": "false"}.get(
                str(spawn_model or "").lower(), fast)
        context = str(params.get("context") or "").lower()
        prefix = (
            "500k-" if cursor_model_id == "grok-4.7" and context in ("500k", "500000")
            else ""
        )
        if fast == "true":
            return prefix + "fast"
        if fast == "false":
            return prefix + "standard"
        return ""
    selected = config.get("selectedModels") if isinstance(config, dict) else []
    for row in selected or []:
        model_id = str(row.get("modelId") or "") if isinstance(row, dict) else ""
        if model_id and _catalog_model_alias_matches(model, "cursor", model_id):
            if str(cursor_model_parameters(composer, model_id).get("fast")).lower() == "true":
                return "fast"
            break
    return ""


def cursor_user_turns(objs, default_model=None):
    """Extract human turns from Cursor's durable transcript without wrappers."""
    turns = []
    for row in objs or []:
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        text = text_from_content(cursor_message_content(row))
        match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, flags=re.DOTALL)
        turns.append({"ts": 0, "text": match.group(1) if match else text,
                      "model": default_model or "unknown"})
    return turns


def cursor_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return float(parse_iso(value) or 0)
    return 0.0


def cursor_message_content(row):
    message = row.get("message") if isinstance(row, dict) else {}
    content = message.get("content") if isinstance(message, dict) else []
    return content if isinstance(content, list) else []


def cursor_transcript_groups(rows, source):
    """Reconstruct coarse turns when Cursor SQLite enrichment is unavailable."""
    groups = []
    current = None
    for row in rows or []:
        role = row.get("role") if isinstance(row, dict) else None
        if role == "user":
            if current:
                groups.append(current)
            user_text = text_from_content(cursor_message_content(row))
            query = re.search(r"<user_query>\s*(.*?)\s*</user_query>", user_text, flags=re.DOTALL)
            current = {
                "user_text": query.group(1) if query else user_text,
                "model": source.get("model") or "unknown", "bubbles": [],
                "completed": False,
            }
            continue
        if role == "assistant":
            if current is None:
                current = {"user_text": "", "model": source.get("model") or "unknown",
                           "bubbles": [], "completed": False}
            current["bubbles"].append({"type": 2, "content": cursor_message_content(row)})
            continue
        if row.get("type") == "turn_ended" and current:
            current["completed"] = row.get("status") in (None, "success", "completed")
            groups.append(current)
            current = None
    if current:
        groups.append(current)
    return groups


def cursor_enriched_groups(snapshot):
    groups = []
    current = None
    for bubble in snapshot.get("bubbles") or []:
        if bubble.get("type") == 1:
            if current:
                groups.append(current)
            model_info = bubble.get("modelInfo") if isinstance(bubble.get("modelInfo"), dict) else {}
            current = {
                "user_text": str(bubble.get("text") or ""),
                "model": model_info.get("modelName") or
                         cursor_model(snapshot.get("composer") or {}, snapshot.get("header") or {}),
                "request_id": bubble.get("requestId") or "",
                "start_ts": cursor_timestamp(bubble.get("createdAt")),
                "context": bubble.get("contextWindowStatusAtCreation")
                           if isinstance(bubble.get("contextWindowStatusAtCreation"), dict) else {},
                "bubbles": [], "completed": False,
            }
            continue
        if bubble.get("type") != 2:
            continue
        if current is None:
            current = {
                "user_text": "", "model": cursor_model(
                    snapshot.get("composer") or {}, snapshot.get("header") or {}
                ), "request_id": "", "start_ts": 0, "context": {}, "bubbles": [],
                "completed": False,
            }
        current["bubbles"].append(bubble)
        if bubble.get("turnDurationMs") is not None:
            current["completed"] = True
            current["turn_duration_ms"] = bubble.get("turnDurationMs")
    if current:
        groups.append(current)
    return groups


CURSOR_SPAN_LEAD_S = 5.0


def cursor_turn_timing(spans, start_ts, next_start_ts=0, terminal_ts=0, turn_duration_ms=0,
                       request_id="", claimed_request_ids=frozenset()):
    # Spans carrying a known turn requestId belong to that turn. Others are
    # attributed by start: Cursor opens spans a few seconds before the user
    # bubble's createdAt, and a turn's spans can outlive the next queued turn.
    lower = float(start_ts or 0) - CURSOR_SPAN_LEAD_S
    if next_start_ts:
        upper = max(lower, float(next_start_ts) - CURSOR_SPAN_LEAD_S)
    else:
        upper = float((terminal_ts + 24 * 60 * 60) if terminal_ts else float("inf"))

    def owned(row):
        row_request = row.get("request_id") or ""
        if row_request and row_request in claimed_request_ids:
            return row_request == request_id
        return lower <= row.get("start_ts", 0) < upper

    matches = [row for row in spans or [] if owned(row)]
    submits = [row for row in matches if row.get("name") == "ComposerChatService.submitChatMaybeAbortCurrent"]
    attempts = [row for row in matches if row.get("name") == "agent.request.attempt"]
    rpc_errors = {
        row.get("request_id") for row in matches
        if row.get("name") == "rpc.run" and row.get("error") and row.get("request_id")
    }
    ttfts = [row for row in matches if row.get("name") == "client.ttft" and row.get("duration_s", 0) > 0]
    final_ts = max((row["end_ts"] for row in submits + attempts), default=float(terminal_ts or 0))
    intervals = [(row["start_ts"], row["end_ts"]) for row in attempts]
    try:
        fallback_s = max(0.0, float(turn_duration_ms or 0) / 1000.0)
    except (TypeError, ValueError):
        fallback_s = 0.0
    if not intervals and fallback_s and terminal_ts:
        intervals = [(max(0.0, float(terminal_ts) - fallback_s), float(terminal_ts))]
    active_s = _merge_execution_intervals(intervals)
    if final_ts and start_ts and final_ts >= start_ts:
        wait_s = final_ts - start_ts
    else:
        wait_s = fallback_s
        final_ts = float(terminal_ts or (start_ts + fallback_s if start_ts else 0))
    failed = sum(bool(row.get("error") or row.get("request_id") in rpc_errors) for row in attempts)
    return {
        "end_ts": final_ts,
        "wait_s": max(0.0, wait_s),
        "active_s": max(0.0, active_s),
        "active_intervals": intervals,
        "timing_basis": "request_trace" if (submits or attempts) else
                        ("turn_duration" if fallback_s else "unavailable"),
        "ttft_s": min((row["duration_s"] for row in ttfts), default=0.0),
        "attempts": len(attempts),
        "failed_attempts": failed,
        "retries": max(0, len(attempts) - 1),
    }


def cursor_prompt_breakdown(composer):
    raw = composer.get("promptTokenBreakdown") if isinstance(composer, dict) else {}
    categories = raw.get("categories") if isinstance(raw, dict) else []
    return [
        {
            "id": str(row.get("id") or "unknown"),
            "label": str(row.get("label") or row.get("id") or "Unknown"),
            "estimated_tokens": int(row.get("estimatedTokens") or 0),
        }
        for row in (categories or []) if isinstance(row, dict)
    ]


def cursor_context_estimates(groups, latest_tokens=0, latest_window=0):
    """Fill sparse Cursor context checkpoints without calling them billable input."""
    rows = []
    for group in groups or []:
        context = group.get("context") if isinstance(group.get("context"), dict) else {}
        rows.append({
            "tokens": max(0, int(context.get("tokensUsed") or 0)),
            "window": max(0, int(context.get("tokenLimit") or 0)),
            "interpolated": False,
        })
    if not rows:
        return rows
    if latest_tokens:
        rows[-1]["tokens"] = max(0, int(latest_tokens))
    if latest_window:
        rows[-1]["window"] = max(0, int(latest_window))

    known_tokens = [index for index, row in enumerate(rows) if row["tokens"] > 0]
    for index, row in enumerate(rows):
        if row["tokens"] > 0:
            continue
        previous = max((known for known in known_tokens if known < index), default=None)
        following = min((known for known in known_tokens if known > index), default=None)
        if following is not None and previous is not None:
            span = following - previous
            share = (index - previous) / span
            start = rows[previous]["tokens"]
            row["tokens"] = max(1, round(start + (rows[following]["tokens"] - start) * share))
        elif following is not None:
            row["tokens"] = max(1, round(rows[following]["tokens"] * (index + 1) / (following + 1)))
        elif previous is not None:
            row["tokens"] = rows[previous]["tokens"]
        row["interpolated"] = bool(row["tokens"])

    known_windows = [index for index, row in enumerate(rows) if row["window"] > 0]
    for index, row in enumerate(rows):
        if row["window"] > 0:
            continue
        nearest = min(known_windows, key=lambda known: abs(known - index)) if known_windows else None
        if nearest is not None:
            row["window"] = rows[nearest]["window"]
    return rows


def cursor_visible_output(bubbles):
    """Estimate only model-authored text that Cursor persisted in its bubbles."""
    assistant_chars = 0
    reasoning_chars = 0
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        seen = set()
        text = bubble.get("text")
        if isinstance(text, str) and text.strip():
            seen.add(text)
            assistant_chars += len(text)
        for block in bubble.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            value = block.get("text")
            if isinstance(value, str) and value.strip() and value not in seen:
                seen.add(value)
                assistant_chars += len(value)
        thinking = bubble.get("thinking")
        if isinstance(thinking, str) and thinking.strip() and thinking not in seen:
            reasoning_chars += len(thinking)
    return {
        "assistant_chars": assistant_chars,
        "reasoning_chars": reasoning_chars,
        "assistant_tokens": math.ceil(assistant_chars / CHARS_PER_TOKEN) if assistant_chars else 0,
        "reasoning_tokens": math.ceil(reasoning_chars / CHARS_PER_TOKEN) if reasoning_chars else 0,
    }


def cursor_model_call_count(bubbles):
    call_ids = set()
    has_model_activity = False
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        tool_data = bubble.get("toolFormerData")
        if isinstance(tool_data, dict):
            call_id = tool_data.get("modelCallId")
            if call_id:
                call_ids.add(str(call_id))
            has_model_activity = has_model_activity or bool(tool_data.get("name"))
        has_model_activity = has_model_activity or bool(str(bubble.get("text") or "").strip())
        has_model_activity = has_model_activity or any(
            isinstance(block, dict) and block.get("type") in ("text", "tool_use")
            for block in (bubble.get("content") or [])
        )
    return max(len(call_ids), int(has_model_activity))


def cursor_pricing_note(model, variant, supported):
    basis = "one context snapshot per execution plus trace-visible model text"
    if not supported:
        return f"Local Cursor token estimate ({basis}); no configured public rate for {model}."
    rate = "selected-model public API rates"
    if variant == "max":
        rate += " plus Cursor's 20% Max Mode markup"
    elif variant == "max-fast":
        rate = "selected-model public fast-mode API rates plus Cursor's 20% Max Mode markup"
    for cursor_model_id in CURSOR_VARIANT_MODEL_IDS:
        if _catalog_model_alias_matches(model, "cursor", cursor_model_id):
            rate = (f"{cursor_model_id.replace('-', ' ').title()} "
                    f"{variant.replace('-', ' ').title()} public rates")
            break
    return f"Local Cursor estimate ({basis}), priced with {rate}; cache and hidden model work are excluded."
