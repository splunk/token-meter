"""Native read-only adapter for Grok Build local session evidence."""

import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from token_meter.contracts import (
    DeletionPlan,
    DetailLevel,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    ParseWarning,
    RuntimeDescriptor,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    ToolEvent,
    UsageEvidence,
)
from token_meter.domain.timing import merge_execution_intervals, performance_summary


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 32 * 1024 * 1024
MAX_EVENT_ROWS = 20_000
MAX_SOURCES = 2_000
MAX_TURNS = 2_000
MAX_TOOLS = 2_000
COST_TICKS_PER_USD = 1_000_000_000.0
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def _file_signature(path):
    try:
        stat = os.stat(path)
        return str(stat.st_mtime_ns), str(stat.st_size)
    except OSError:
        return "0", "0"


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _timestamp(value):
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        value = float(value)
        return value / 1000.0 if value > 10_000_000_000 else value
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _read_json(path, limit=MAX_JSON_BYTES):
    try:
        if os.path.getsize(path) > limit:
            return None, True
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None, False
    return (value, False) if isinstance(value, dict) else (None, False)


_EVENT_TYPES = frozenset((
    "turn_started", "turn_ended", "first_token",
    "tool_started", "tool_completed",
))


def _read_events(path):
    rows = []
    corrupt = 0
    truncated = False
    try:
        if os.path.getsize(path) > MAX_EVENT_BYTES:
            return (), 0, True
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                if not any(kind in line for kind in _EVENT_TYPES):
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    corrupt += 1
                    continue
                if not isinstance(row, dict):
                    corrupt += 1
                    continue
                kind = str(row.get("type") or "")
                if kind not in _EVENT_TYPES:
                    continue
                if len(rows) >= MAX_EVENT_ROWS:
                    truncated = True
                    break
                rows.append({
                    "type": kind,
                    "ts": _timestamp(row.get("ts")),
                    "turn_number": _integer(row.get("turn_number")),
                    "model_id": str(row.get("model_id") or ""),
                    "tool_name": str(row.get("tool_name") or ""),
                    "outcome": str(row.get("outcome") or ""),
                    "duration_ms": _number(row.get("duration_ms")),
                })
    except OSError:
        return (), 0, False
    return tuple(rows), corrupt, truncated


def _session_totals(usage):
    session = usage.get("session") if isinstance(usage, dict) else None
    if not isinstance(session, dict):
        return None
    input_tokens = _integer(session.get("inputTokens"))
    output_tokens = _integer(session.get("outputTokens"))
    cache_read = _integer(session.get("cachedReadTokens"))
    cache_write = _integer(session.get("cacheCreationTokens"))
    cost = _cost_from_ticks(session.get("costUsdTicks"))
    token_available = input_tokens is not None and output_tokens is not None
    cache_available = cache_read is not None and cache_write is not None
    if not token_available and cost is None:
        return None
    return {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "cache_read_tokens": cache_read or 0,
        "cache_write_tokens": cache_write or 0,
        "cost": cost,
        "token_available": token_available,
        "cache_available": cache_available,
        "cost_available": cost is not None,
    }


def _normalize_model(value):
    value = str(value or "").strip().lower()
    return value or "unknown-model"


def model_ref_for(model):
    model = _normalize_model(model)
    if model.startswith("grok-"):
        return ModelRef("xai", model)
    return ModelRef("unknown-model-provider", model)


def _normalize_tool_name(value):
    value = str(value or "tool").strip()
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").lower()
    return value or "tool"


def _tool_category(name):
    name = str(name or "").lower()
    if any(part in name for part in ("command", "shell", "bash", "terminal", "exec")):
        return "shell"
    if any(part in name for part in ("read", "write", "file", "directory", "edit", "search_replace")):
        return "filesystem"
    if any(part in name for part in ("grep", "search", "find", "glob")):
        return "search"
    if any(part in name for part in ("browser", "web", "url")):
        return "browser"
    if any(part in name for part in ("fetch", "retrieve", "lookup")):
        return "retrieval"
    return "other"


def _cost_from_ticks(ticks):
    ticks = _integer(ticks)
    if ticks is None:
        return None
    return ticks / COST_TICKS_PER_USD


def _decode_cwd(encoded, group_dir):
    cwd_file = os.path.join(group_dir, ".cwd")
    if os.path.isfile(cwd_file) and not os.path.islink(cwd_file):
        try:
            with open(cwd_file, encoding="utf-8") as handle:
                value = handle.readline().strip()
            if value.startswith("/"):
                return value
        except OSError:
            pass
    decoded = unquote(str(encoded or ""), errors="replace")
    return decoded if decoded.startswith("/") else ""


class GrokRuntimeAdapter:
    """Discover Grok Build session directories and expose no message content."""

    descriptor = RuntimeDescriptor(
        "grok",
        "Grok",
        frozenset(("sessions", "models", "tools", "quota")),
        "runtime.generic",
        "runtime-neutral",
        None,
    )

    def __init__(self, grok_home, project_resolver=None, compatibility=None):
        self.grok_home = Path(os.path.abspath(os.path.expanduser(str(grok_home))))
        self.project_resolver = project_resolver or (lambda value: value)
        self.compatibility = dict(compatibility or {})
        self._metadata_cache = {}

    def _owned_path(self, path):
        path = os.path.realpath(os.path.abspath(os.path.expanduser(str(path or ""))))
        root = os.path.realpath(str(self.grok_home))
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    def _session_dirs(self):
        root = self.grok_home / "sessions"
        if not root.is_dir():
            return ()
        found = []
        try:
            groups = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            return ()
        for group in groups:
            if not group.is_dir() or group.is_symlink() or not self._owned_path(group):
                continue
            try:
                children = sorted(group.iterdir(), key=lambda path: path.name)
            except OSError:
                continue
            for child in children:
                if len(found) >= MAX_SOURCES:
                    return tuple(found)
                if not child.is_dir() or child.is_symlink():
                    continue
                if not SESSION_ID_RE.fullmatch(child.name):
                    continue
                summary = child / "summary.json"
                if summary.is_file() and not summary.is_symlink() and self._owned_path(summary):
                    found.append(child)
        return tuple(found)

    def _metadata(self, session_dir):
        summary_path = str(session_dir / "summary.json")
        usage_path = str(session_dir / "usage.json")
        signals_path = str(session_dir / "signals.json")
        events_path = str(session_dir / "events.jsonl")
        signature = (
            *_file_signature(summary_path),
            *_file_signature(usage_path),
            *_file_signature(signals_path),
            *_file_signature(events_path),
        )
        cached = self._metadata_cache.get(summary_path)
        if cached and cached[0] == signature:
            return dict(cached[1]) if cached[1] else None
        summary, _ = _read_json(summary_path)
        info = summary.get("info") if isinstance(summary, dict) else None
        session_id = ""
        cwd = ""
        if isinstance(info, dict):
            session_id = str(info.get("id") or "").strip()
            cwd = str(info.get("cwd") or "").strip()
        session_id = session_id or session_dir.name
        if not SESSION_ID_RE.fullmatch(session_id):
            result = None
        else:
            if not cwd:
                cwd = _decode_cwd(session_dir.parent.name, str(session_dir.parent))
            model = ""
            if isinstance(summary, dict):
                model = str(summary.get("current_model_id") or "")
            usage, _ = _read_json(usage_path)
            if not model and isinstance(usage, dict):
                session = usage.get("session") if isinstance(usage.get("session"), dict) else {}
                model = str(session.get("primaryModelId") or "")
            model_ref = model_ref_for(model)
            mtime = max(
                _mtime(summary_path), _mtime(usage_path),
                _mtime(signals_path), _mtime(events_path),
            )
            result = {
                "provider": "grok", "client": "grok", "label": "Grok", "runtime": "Grok",
                "id": session_id, "session": session_id,
                "path": summary_path,
                "project": self.project_resolver(cwd) or "",
                "mtime": mtime, "signature_mtime": mtime, "title": "Grok session",
                "model": model_ref.model_id, "model_provider": model_ref.provider_id,
                "source_kind": "grok_session",
            }
        self._metadata_cache[summary_path] = (signature, result)
        if len(self._metadata_cache) > MAX_SOURCES:
            self._metadata_cache.pop(next(iter(self._metadata_cache)), None)
        return dict(result) if result else None

    def _legacy_records(self):
        records = [self._metadata(path) for path in self._session_dirs()]
        records = [record for record in records if record is not None]
        return tuple(sorted(
            records,
            key=lambda record: (-float(record.get("mtime") or 0), record["id"], record["path"]),
        ))

    def discover_legacy(self, context):
        del context
        return self._legacy_records()

    def discover(self, context):
        del context
        result = []
        for record in self._legacy_records():
            model = model_ref_for(record.get("model"))
            result.append(SessionSource(
                runtime_id=self.descriptor.runtime_id,
                client_id="grok",
                session_id=record["id"],
                display_label="Grok",
                project=record.get("project") or None,
                locator=SourceLocator("file", record["path"]),
                activity_mtime=record["mtime"],
                revision=self._revision(record["path"]),
                model_ref=model,
                account_provider_id=None,
            ))
        return tuple(result)

    def _revision(self, summary_path):
        session_dir = os.path.dirname(summary_path)
        return SourceRevision((
            "grok-session",
            *_file_signature(summary_path),
            *_file_signature(os.path.join(session_dir, "usage.json")),
            *_file_signature(os.path.join(session_dir, "events.jsonl")),
        ))

    def current_revision(self, source):
        path = source.locator.value if isinstance(source, SessionSource) else source.get("path", "")
        return self._revision(path)

    def _parsed(self, summary_path):
        if not self._owned_path(summary_path):
            return {"turns": (), "corrupt": 0, "truncated": False}
        session_dir = os.path.dirname(summary_path)
        usage, usage_truncated = _read_json(os.path.join(session_dir, "usage.json"))
        events, corrupt, events_truncated = _read_events(os.path.join(session_dir, "events.jsonl"))
        signals, _ = _read_json(os.path.join(session_dir, "signals.json"))
        summary, _ = _read_json(summary_path)
        model = ""
        if isinstance(summary, dict):
            model = str(summary.get("current_model_id") or "")
        session_usage = usage.get("session") if isinstance(usage, dict) else None
        if not model and isinstance(session_usage, dict):
            model = str(session_usage.get("primaryModelId") or "")
        if not model and isinstance(signals, dict):
            model = str(signals.get("primaryModelId") or "")
        model = model_ref_for(model).model_id
        event_turns = self._event_turns(events, model)
        usage_turns = []
        if isinstance(usage, dict):
            for row in usage.get("turns") or ():
                if not isinstance(row, dict) or len(usage_turns) >= MAX_TURNS:
                    continue
                usage_turns.append(self._usage_turn(row, model, len(usage_turns) + 1))
            if not usage_turns and isinstance(session_usage, dict):
                usage_turns.append(self._usage_turn(session_usage, model, 1))
        turns = self._merge_turns(usage_turns, event_turns, model)
        return {
            "turns": tuple(turns[:MAX_TURNS]),
            "session_totals": _session_totals(usage),
            "corrupt": corrupt,
            "truncated": bool(usage_truncated or events_truncated),
        }

    def _usage_turn(self, row, default_model, index):
        start = _timestamp(row.get("startedAt") or row.get("started_at"))
        end = _timestamp(row.get("endedAt") or row.get("ended_at"))
        input_tokens = _integer(row.get("inputTokens"))
        output_tokens = _integer(row.get("outputTokens"))
        cache_read = _integer(row.get("cachedReadTokens"))
        cache_write = _integer(row.get("cacheCreationTokens"))
        reasoning = _integer(row.get("reasoningTokens")) or 0
        cost = _cost_from_ticks(row.get("costUsdTicks"))
        token_available = input_tokens is not None and output_tokens is not None
        cache_available = cache_read is not None and cache_write is not None
        context_tokens = 0
        if token_available and cache_available:
            context_tokens = input_tokens + cache_read + cache_write
        return {
            "index": _integer(row.get("turnNumber")) or index,
            "start": start, "end": end or start,
            "model": model_ref_for(row.get("primaryModelId") or default_model).model_id,
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "reasoning_tokens": min(reasoning, output_tokens or reasoning),
            "cache_read_tokens": cache_read or 0,
            "cache_write_tokens": cache_write or 0,
            "token_available": token_available,
            "cache_available": cache_available,
            "context_available": token_available and cache_available,
            "context_tokens": context_tokens,
            "cost": cost,
            "ttft_s": None,
            "tools": [],
        }

    def _event_turns(self, events, default_model):
        turns = []
        current = None
        pending_first = None
        for event in events:
            kind = event["type"]
            ts = event["ts"]
            if kind == "turn_started" or (current is None and kind != "turn_ended"):
                if kind == "turn_started" or current is None:
                    if kind == "turn_started":
                        current = {
                            "index": event["turn_number"] or (len(turns) + 1),
                            "start": ts, "end": ts,
                            "model": model_ref_for(event["model_id"] or default_model).model_id,
                            "tools": [], "ttft_s": None,
                        }
                        pending_first = ts
                        turns.append(current)
            if current is None:
                continue
            if kind == "first_token" and pending_first and ts >= pending_first and current["ttft_s"] is None:
                current["ttft_s"] = max(0.0, ts - pending_first)
            if kind == "tool_completed" and event["tool_name"] and len(current["tools"]) < MAX_TOOLS:
                current["tools"].append({
                    "id": "{}:{}".format(current["index"], len(current["tools"]) + 1),
                    "name": _normalize_tool_name(event["tool_name"]),
                    "category": _tool_category(event["tool_name"]),
                    "result_available": True,
                    "error": event["outcome"] in {"error", "failed", "failure"},
                })
            if ts:
                current["end"] = max(current["end"] or ts, ts)
            if kind == "turn_ended":
                pending_first = None
                current = None
        return turns

    def _merge_turns(self, usage_turns, event_turns, default_model):
        if not usage_turns and not event_turns:
            return [{
                "index": 1, "start": 0.0, "end": 0.0, "model": default_model,
                "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "token_available": False, "cache_available": False,
                "context_available": False, "context_tokens": 0, "cost": None,
                "ttft_s": None, "tools": [],
            }]
        if not usage_turns:
            merged = []
            for event in event_turns:
                merged.append({
                    "index": event["index"], "start": event["start"], "end": event["end"],
                    "model": event["model"] or default_model,
                    "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "token_available": False, "cache_available": False,
                    "context_available": False, "context_tokens": 0, "cost": None,
                    "ttft_s": event.get("ttft_s"), "tools": event.get("tools") or [],
                })
            return merged
        by_index = {turn["index"]: turn for turn in event_turns}
        merged = []
        for usage in usage_turns:
            event = by_index.get(usage["index"])
            if event:
                if not usage["start"]:
                    usage["start"] = event["start"]
                if not usage["end"] or usage["end"] < usage["start"]:
                    usage["end"] = event["end"] or usage["start"]
                usage["ttft_s"] = event.get("ttft_s")
                usage["tools"] = event.get("tools") or []
                if event.get("model"):
                    usage["model"] = event["model"]
            merged.append(usage)
        used = {turn["index"] for turn in usage_turns}
        for event in event_turns:
            if event["index"] in used:
                continue
            merged.append({
                "index": event["index"], "start": event["start"], "end": event["end"],
                "model": event["model"] or default_model,
                "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "token_available": False, "cache_available": False,
                "context_available": False, "context_tokens": 0, "cost": None,
                "ttft_s": event.get("ttft_s"), "tools": event.get("tools") or [],
            })
        merged.sort(key=lambda row: (row["start"] or 0, row["index"]))
        for index, turn in enumerate(merged, 1):
            turn["index"] = index
        return merged

    @staticmethod
    def _available(value, available, basis=EvidenceBasis.MEASURED):
        return EvidenceValue(value, basis) if available else EvidenceValue.unavailable()

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != self.descriptor.runtime_id:
            raise ValueError("source belongs to another runtime")
        parsed = self._parsed(source.locator.value)
        turns = parsed["turns"]
        session_totals = parsed.get("session_totals") or {}
        usage_turns = [turn for turn in turns if turn["token_available"]]
        cache_turns = [turn for turn in turns if turn["cache_available"]]
        cost_turns = [turn for turn in turns if turn["cost"] is not None]
        tokens_available = bool(session_totals.get("token_available") or usage_turns)
        cache_available = bool(session_totals.get("cache_available") or cache_turns)
        cost_available = bool(session_totals.get("cost_available") or cost_turns)
        intervals = [
            (turn["start"], turn["end"]) for turn in turns
            if turn["start"] and turn["end"] >= turn["start"]
        ]
        tools = []
        for turn in turns:
            for tool in turn["tools"]:
                tools.append(ToolEvent(tool["name"], tool["category"], tool.get("error") and "error" or "success"))
        warning_codes = []
        if parsed["truncated"]:
            warning_codes.append("truncated")
        if not tokens_available:
            warning_codes.append("partial")
        started = min((turn["start"] for turn in turns if turn["start"]), default=0) or None
        ended = max((turn["end"] for turn in turns if turn["end"]), default=0) or None
        if session_totals.get("token_available"):
            input_tokens = session_totals["input_tokens"]
            output_tokens = session_totals["output_tokens"]
        else:
            input_tokens = sum(turn["input_tokens"] for turn in usage_turns)
            output_tokens = sum(turn["output_tokens"] for turn in usage_turns)
        if session_totals.get("cache_available"):
            cache_read = session_totals["cache_read_tokens"]
            cache_write = session_totals["cache_write_tokens"]
        else:
            cache_read = sum(turn["cache_read_tokens"] for turn in cache_turns)
            cache_write = sum(turn["cache_write_tokens"] for turn in cache_turns)
        if session_totals.get("cost_available"):
            cost = session_totals["cost"] or 0.0
        else:
            cost = sum(turn["cost"] or 0.0 for turn in cost_turns)
        del detail
        return NormalizedSession(
            source=source,
            started_at=datetime.fromtimestamp(started) if started else None,
            ended_at=datetime.fromtimestamp(ended) if ended else None,
            usage=UsageEvidence(
                input_tokens=self._available(input_tokens, tokens_available),
                output_tokens=self._available(output_tokens, tokens_available),
                cache_read_tokens=self._available(cache_read, cache_available),
                cache_write_tokens=self._available(cache_write, cache_available),
                cost_usd=self._available(cost, cost_available, EvidenceBasis.ESTIMATED),
            ),
            timing=TimingEvidence(
                active_seconds=self._available(
                    merge_execution_intervals(intervals), bool(intervals), EvidenceBasis.INFERRED,
                ),
                wait_seconds=EvidenceValue.unavailable(),
                ttft_seconds=self._available(
                    next((turn["ttft_s"] for turn in reversed(turns) if turn.get("ttft_s") is not None), None),
                    any(turn.get("ttft_s") is not None for turn in turns),
                    EvidenceBasis.MEASURED,
                ),
            ),
            tools=tuple(tools),
            turns=(),
            pricing_basis="Grok-recorded local cost; Token Meter did not price this runtime.",
            capabilities=self.descriptor.capabilities,
            warnings=tuple(ParseWarning(code, "Grok evidence is incomplete.") for code in warning_codes),
            detail=DetailLevel.SUMMARY,
        )

    def _require_compatibility(self):
        required = (
            "add_model_daily", "add_model_summary", "analysis_block", "build_state",
            "context_sample_limit", "metric_availability", "summarize_tool_evidence",
            "summary_row", "tool_identity", "tool_summary", "trace_event",
        )
        missing = [name for name in required if name not in self.compatibility]
        if missing:
            raise RuntimeError("Grok adapter is missing compatibility helpers: {}".format(
                ", ".join(missing)
            ))
        return self.compatibility

    def _legacy_rows(self, source):
        return self._parsed(source.get("path") or "")["turns"]

    def _legacy_usage(self, turn):
        return {
            "input_tokens": turn["input_tokens"],
            "output_tokens": turn["output_tokens"],
            "cache_read_input_tokens": turn["cache_read_tokens"],
            "cache_creation_input_tokens": turn["cache_write_tokens"],
        }

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        parsed = self._parsed(source.get("path") or "")
        turns = parsed["turns"]
        if not turns:
            return None
        session_totals = parsed.get("session_totals") or {}
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace, wait_samples, intervals = [], [], [], [], []
        all_tokens_available = bool(
            session_totals.get("token_available")
            or any(turn["token_available"] for turn in turns)
        )
        all_cache_available = bool(
            session_totals.get("cache_available")
            or any(turn["cache_available"] for turn in turns)
        )
        all_cost_available = bool(
            session_totals.get("cost_available")
            or any(turn["cost"] is not None for turn in turns)
        )
        for turn in turns:
            execution_cost = float(turn["cost"] or 0.0)
            cost_available = turn["cost"] is not None
            usage = self._legacy_usage(turn)
            tools = []
            for tool in turn["tools"]:
                ident = compat["tool_identity"](tool["name"])
                tools.append({
                    **ident, "id": tool["id"], "call_id": tool["id"],
                    "args_chars": 0, "output_chars": 0, "output_tokens": 0,
                    "result_available": bool(tool.get("result_available")),
                    "error": bool(tool.get("error")), "skills": [],
                })
            total = sum(usage.values())
            timing_available = bool(turn["start"] and turn["end"] >= turn["start"])
            availability = compat["metric_availability"](
                "grok", cost=cost_available, tokens=turn["token_available"],
                input_tokens=turn["token_available"], output_tokens=turn["token_available"],
                cache=turn["cache_available"], throughput=False,
                context=turn["context_available"], timing=timing_available,
                tool_results=any(tool["result_available"] for tool in tools),
            )
            duration = max(0.0, turn["end"] - turn["start"]) if timing_available else 0.0
            series.append({
                "i": turn["index"], "in": usage["input_tokens"], "out": usage["output_tokens"],
                "cost": execution_cost, "fresh_input": usage["input_tokens"],
                "cache": usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"],
                "cache_read": usage["cache_read_input_tokens"],
                "cache_write": usage["cache_creation_input_tokens"],
                "think": bool(turn["reasoning_tokens"]),
                "tools": len(tools), "side": False,
                "reasoning": turn["reasoning_tokens"], "reasoning_ms": 0,
                "context_pct": None, "context_tokens": turn["context_tokens"],
                "user_message": "", "user_input": "", "availability": availability,
            })
            executions.append({
                "id": "{}:{}".format(source["id"], turn["index"]), "idx": turn["index"],
                "ts": turn["end"] or turn["start"],
                "time": time.strftime("%H:%M", time.localtime(turn["end"] or turn["start"] or 0)),
                "model": turn["model"],
                "tokens": {
                    "input": usage["input_tokens"], "output": usage["output_tokens"],
                    "reasoning": turn["reasoning_tokens"], "retrieval": 0,
                    "fresh_input": usage["input_tokens"],
                    "cache": usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"],
                    "cache_read": usage["cache_read_input_tokens"],
                    "cache_write": usage["cache_creation_input_tokens"], "total": total,
                },
                "cost": execution_cost,
                "cost_breakdown": {
                    "input": execution_cost, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0,
                } if cost_available else {
                    "input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0,
                },
                "tools": tools, "tool_count": len(tools), "model_calls": 1,
                "reasoning_tokens": turn["reasoning_tokens"],
                "reasoning_duration_ms": 0,
                "context_tokens": turn["context_tokens"],
                "context_window": 0, "context_pct": None,
                "duration_ms": duration * 1000 if duration else None,
                "wait_duration_ms": duration * 1000 if duration else None,
                "summary": "Execution {}: {} tools · {}".format(
                    turn["index"], len(tools),
                    "${:.3f} Grok estimate".format(execution_cost)
                    if cost_available else "cost unavailable",
                ),
                "user_message": "", "user_input": "", "availability": availability,
            })
            trace.append(compat["trace_event"](
                turn["start"], "user", "User input", "Content excluded", turn["index"],
                severity="start", model=turn["model"], native_type="user",
                native_subtype="user_message",
            ))
            for tool in tools:
                trace.append(compat["trace_event"](
                    turn["end"], "tool_call", tool["display"], "Payload excluded",
                    turn["index"], tool=tool["name"],
                    severity="warn" if tool.get("error") else "tool",
                    model=turn["model"], native_type="tool_call", native_subtype="tool_call",
                ))
            trace.append(compat["trace_event"](
                turn["end"], "complete", "Execution complete", "", turn["index"],
                severity="good", model=turn["model"],
                cost=execution_cost if cost_available else None,
                native_type="assistant", native_subtype="agent_message",
            ))
            tot["input"] += usage["input_tokens"]
            tot["cache_write"] += usage["cache_creation_input_tokens"]
            tot["cache_read"] += usage["cache_read_input_tokens"]
            tot["output"] += usage["output_tokens"]
            if cost_available:
                cost["input"] += execution_cost
            model_tok[turn["model"]] += total
            model_cost[turn["model"]] += execution_cost
            if timing_available:
                intervals.append((turn["start"], turn["end"]))
                wait_samples.append({
                    "provider": "grok", "model": turn["model"],
                    "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
                    "ts": turn["end"], "start_ts": turn["start"], "duration_s": duration,
                    "generation_s": duration, "ttft_s": turn.get("ttft_s") or 0.0,
                    "tool_calls": len(tools), "model_calls": 1,
                    "output_tokens": usage["output_tokens"],
                    "input_tokens": (usage["input_tokens"]
                                     + usage["cache_read_input_tokens"]
                                     + usage["cache_creation_input_tokens"]),
                    "uncached_input_tokens": usage["input_tokens"],
                    "cache_read_tokens": usage["cache_read_input_tokens"],
                    "cache_write_tokens": usage["cache_creation_input_tokens"],
                    "peak_input_tokens": turn["context_tokens"],
                    "context_tokens": turn["context_tokens"],
                    "timing_basis": "inferred",
                })
        if session_totals.get("token_available"):
            tot = {
                "input": session_totals["input_tokens"],
                "cache_write": session_totals["cache_write_tokens"],
                "cache_read": session_totals["cache_read_tokens"],
                "output": session_totals["output_tokens"],
            }
        if session_totals.get("cost_available"):
            cost = {
                "input": float(session_totals["cost"] or 0.0),
                "cache_write": 0.0, "cache_read": 0.0, "output": 0.0,
            }
        total_tokens, total_cost = sum(tot.values()), sum(cost.values())
        tool_data = compat["tool_summary"](executions)
        primary_model = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        analyses = compat["analysis_block"](
            tot, total_cost, 0, 0, 0.0, model_tok, model_cost, tool_data, 0.0, 0, len(executions),
        )
        active = merge_execution_intervals(intervals)
        source = dict(source)
        source["context_latest"] = executions[-1]["context_tokens"] if executions else 0
        throughput = performance_summary(wait_samples, tot["output"])
        context_available = bool(turns) and all(turn["context_available"] for turn in turns)
        availability = compat["metric_availability"](
            "grok", cost=all_cost_available, tokens=all_tokens_available,
            input_tokens=all_tokens_available, output_tokens=all_tokens_available,
            cache=all_cache_available, throughput=throughput["available"],
            context=context_available, timing=bool(intervals),
            tool_results=any(
                tool.get("result_available")
                for execution in executions for tool in execution["tools"]
            ),
        )
        biggest = max(
            ({"cost": execution["cost"], "idx": execution["idx"]} for execution in executions),
            key=lambda row: row["cost"], default=None,
        ) if all_cost_available else None
        source["cache_savings_available"] = False
        state = compat["build_state"](
            source, tot, cost, total_tokens, total_cost, series, executions, trace,
            {"reasoning": 0, "output": 0, "retrieval": 0, "coordination": 0},
            analyses, [], min((turn["start"] for turn in turns if turn["start"]), default=0),
            max((turn["end"] for turn in turns if turn["end"]), default=0), 0, biggest, 0,
            True, primary_model,
            "Grok-recorded local cost; Token Meter did not price this runtime.",
            {"duration_s": active, "available": bool(intervals), "reported_executions": 0,
             "observed_executions": len(intervals), "execution_count": len(executions),
             "basis": "inferred"},
            wait_samples, availability=availability,
        )
        state["throughput"] = throughput
        state["semantic_available"] = False
        return state

    def summarize_legacy(self, source, unused=None):
        del unused
        compat = self._require_compatibility()
        parsed = self._parsed(source.get("path") or "")
        turns = parsed["turns"]
        session_totals = parsed.get("session_totals") or {}
        model_cost, model_tok, model_stats, model_daily = (
            defaultdict(float), defaultdict(int), {}, {}
        )
        day_cost, tool_calls, intervals, models, wait_samples, context_samples = (
            defaultdict(float), [], [], set(), [], []
        )
        total_cost = input_tokens = output_tokens = 0
        all_tokens_available = bool(
            session_totals.get("token_available")
            or (turns and any(turn["token_available"] for turn in turns))
        )
        all_cache_available = bool(
            session_totals.get("cache_available")
            or (turns and any(turn["cache_available"] for turn in turns))
        )
        all_cost_available = bool(
            session_totals.get("cost_available")
            or (turns and any(turn["cost"] is not None for turn in turns))
        )
        all_context_available = bool(turns) and any(turn["context_available"] for turn in turns)
        for turn in turns:
            usage = self._legacy_usage(turn)
            value = float(turn["cost"] or 0.0)
            total = sum(usage.values())
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            total_cost += value
            model_cost[turn["model"]] += value
            model_tok[turn["model"]] += total
            models.add(turn["model"])
            compat["add_model_summary"](
                model_stats, turn["model"], usage, value,
                cost_available=turn["cost"] is not None,
            )
            compat["add_model_daily"](
                model_daily, turn["model"], usage, value, turn["end"],
                cost_available=turn["cost"] is not None,
            )
            if turn["end"]:
                day_cost[time.strftime("%Y-%m-%d", time.localtime(turn["end"]))] += value
            if turn["start"] and turn["end"] >= turn["start"]:
                intervals.append((turn["start"], turn["end"]))
                duration = turn["end"] - turn["start"]
                context_samples.append(turn["context_tokens"])
                wait_samples.append({
                    "provider": "grok", "model": turn["model"],
                    "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
                    "ts": turn["end"], "start_ts": turn["start"],
                    "duration_s": duration, "generation_s": duration,
                    "ttft_s": turn.get("ttft_s") or 0.0,
                    "tool_calls": len(turn["tools"]), "model_calls": 1,
                    "output_tokens": usage["output_tokens"],
                    "input_tokens": (usage["input_tokens"]
                                     + usage["cache_read_input_tokens"]
                                     + usage["cache_creation_input_tokens"]),
                    "uncached_input_tokens": usage["input_tokens"],
                    "cache_read_tokens": usage["cache_read_input_tokens"],
                    "cache_write_tokens": usage["cache_creation_input_tokens"],
                    "peak_input_tokens": turn["context_tokens"],
                    "context_tokens": turn["context_tokens"],
                    "timing_basis": "inferred",
                })
            for tool in turn["tools"]:
                tool_calls.append({
                    "name": tool["name"], "display": tool["name"].replace("_", " ").title(),
                    "namespace": tool["category"], "kind": "tool", "output_tokens": 0,
                    "error": bool(tool.get("error")), "ts": turn["end"], "skills": [],
                })
        if session_totals.get("token_available"):
            input_tokens = session_totals["input_tokens"]
            output_tokens = session_totals["output_tokens"]
        if session_totals.get("cost_available"):
            total_cost = float(session_totals["cost"] or 0.0)
        throughput = performance_summary(wait_samples, output_tokens)
        availability = compat["metric_availability"](
            "grok", cost=all_cost_available, tokens=all_tokens_available,
            input_tokens=all_tokens_available, output_tokens=all_tokens_available,
            cache=all_cache_available, throughput=throughput["available"],
            context=all_context_available, timing=bool(intervals),
            tool_results=False,
        )
        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = compat["metric_availability"](
                "grok", cost=int(stats.get("cost_covered_executions") or 0) > 0,
                tokens=all_tokens_available, input_tokens=all_tokens_available,
                output_tokens=all_tokens_available, cache=all_cache_available,
                throughput=throughput["available"], context=all_context_available,
                timing=False, tool_results=False,
            )
        row = compat["summary_row"](
            source, None, total_cost, sum(model_tok.values()), len(turns), models,
            min((turn["start"] for turn in turns if turn["start"]), default=0),
            max((turn["end"] for turn in turns if turn["end"]), default=0),
            model_cost, model_tok, day_cost, True,
            {"duration_s": merge_execution_intervals(intervals), "available": bool(intervals),
             "basis": "inferred"}, input_tokens, output_tokens, model_stats,
            list(model_daily.values()), wait_samples, wait_samples, availability,
        )
        row["primary_model"] = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        row["context"] = {
            "latest": context_samples[-1] if context_samples else 0,
            "window": None, "latest_pct": None, "estimated": False,
        }
        row["_context_samples"] = context_samples[-compat["context_sample_limit"]:]
        row["terminal"] = False
        row["_tool_evidence"] = compat["summarize_tool_evidence"](tool_calls)
        return row

    def deletion_plan(self, source):
        return DeletionPlan.deny("Grok sessions are read-only in Token Meter.")


class GrokRuntimeAdapterProxy:
    descriptor = GrokRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Grok adapter")
        return adapter

    def discover(self, context):
        return self._adapter().discover(context)

    def discover_legacy(self, context):
        return self._adapter().discover_legacy(context)

    def current_revision(self, source):
        return self._adapter().current_revision(source)

    def load(self, source, detail):
        return self._adapter().load(source, detail)

    def summarize_legacy(self, source, unused=None):
        return self._adapter().summarize_legacy(source, unused)

    def deletion_plan(self, source):
        return self._adapter().deletion_plan(source)
