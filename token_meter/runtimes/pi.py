"""Native read-only adapter for Pi coding agent JSONL session evidence."""

import glob
import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from token_meter.contracts import (
    DeletionDisposition,
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
    TurnSummary,
    UsageEvidence,
)
from token_meter.domain.timing import merge_execution_intervals, performance_summary


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000
MAX_SOURCES = 2_000
MAX_TURNS = 2_000
MAX_TOOLS = 2_000


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


def _date(seconds):
    return datetime.fromtimestamp(seconds).astimezone() if seconds else None


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 0 or value != number:
        return None
    return number


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _cost_breakdown(value):
    if not isinstance(value, dict):
        return None
    values = {
        "input": _number(value.get("input")),
        "cache_write": _number(value.get("cacheWrite")),
        "cache_read": _number(value.get("cacheRead")),
        "output": _number(value.get("output")),
    }
    return values if all(item is not None for item in values.values()) else None


def _read_jsonl(path):
    rows = []
    corrupt = 0
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return (), 0, False, True
        with open(path, encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= MAX_ROWS:
                    return tuple(rows), corrupt, True, True
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    corrupt += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    corrupt += 1
    except OSError:
        return (), 0, False, False
    return tuple(rows), corrupt, True, False


def _normalize_model(value):
    value = str(value or "").strip().lower()
    return value or "unknown-model"


def _public_model_id(value):
    """Keep account-bearing resource identifiers out of Pi projections."""
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered.startswith("arn:aws"):
        return "aws-bedrock-profile" if ":bedrock:" in lowered else "private-model-reference"
    return _normalize_model(raw)


def model_ref_for(provider, model):
    """Retain a scoped model identity without assuming Pi's billable provider."""
    provider = str(provider or "").strip().lower()
    model = _public_model_id(model)
    if provider in ("anthropic", "openai", "amazon"):
        model_provider = provider
    elif provider in ("bedrock", "amazon-bedrock", "aws-bedrock"):
        model_provider = "amazon"
    elif model.startswith("claude-"):
        model_provider = "anthropic"
    elif model.startswith(("gpt-", "o1", "o3", "o4")):
        model_provider = "openai"
    else:
        model_provider = "unknown-model-provider"
    return ModelRef(model_provider, model)


def _normalize_tool_name(value):
    value = str(value or "tool").strip()
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").lower()
    return value or "tool"


def _tool_category(name):
    name = str(name or "").lower()
    if any(part in name for part in ("command", "shell", "bash", "terminal", "exec")):
        return "shell"
    if any(part in name for part in ("read", "write", "file", "directory", "patch", "edit")):
        return "filesystem"
    if any(part in name for part in ("grep", "search", "find", "glob")):
        return "search"
    if any(part in name for part in ("browser", "web", "url")):
        return "browser"
    if any(part in name for part in ("fetch", "retrieve", "lookup")):
        return "retrieval"
    return "other"


class PiRuntimeAdapter:
    """Discover only Pi-owned JSONL session files and expose no message content."""

    descriptor = RuntimeDescriptor(
        "pi",
        "Pi",
        frozenset(("sessions", "models", "tools")),
        "runtime.generic",
        "runtime-neutral",
        None,
    )

    def __init__(self, agent_dir, project_resolver=None, compatibility=None,
                 path_cache=None):
        self.agent_dir = Path(os.path.abspath(os.path.expanduser(str(agent_dir))))
        self.project_resolver = project_resolver or (lambda value: value)
        self.compatibility = dict(compatibility or {})
        self.path_cache = path_cache
        self._metadata_cache = {}

    def _glob(self, pattern):
        if self.path_cache is not None:
            return self.path_cache.paths(pattern)
        return tuple(glob.glob(pattern))

    def _paths(self):
        patterns = (
            str(self.agent_dir / "*.jsonl"),
            str(self.agent_dir / "sessions" / "*" / "*.jsonl"),
        )
        paths = []
        for pattern in patterns:
            for path in self._glob(pattern):
                if len(paths) >= MAX_SOURCES:
                    break
                if os.path.isfile(path) and self._owned_path(path) and not os.path.islink(path):
                    paths.append(os.path.abspath(path))
        return tuple(sorted(set(paths)))[:MAX_SOURCES]

    def _owned_path(self, path):
        path = os.path.realpath(os.path.abspath(os.path.expanduser(str(path or ""))))
        root = os.path.realpath(str(self.agent_dir))
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    def _metadata(self, path):
        signature = _file_signature(path)
        cached = self._metadata_cache.get(path)
        if cached and cached[0] == signature:
            return dict(cached[1]) if cached[1] else None
        rows, corrupt, available, truncated = _read_jsonl(path)
        header = rows[0] if rows else {}
        if (
                not isinstance(header, dict)
                or header.get("type") != "session"
                or not str(header.get("id") or "").strip()
        ):
            result = None
        else:
            model = provider = ""
            for row in rows:
                kind = row.get("type")
                if kind == "model_change":
                    model = str(row.get("modelId") or model)
                    provider = str(row.get("provider") or provider)
                elif kind == "message":
                    message = row.get("message")
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        model = str(message.get("model") or model)
                        provider = str(message.get("provider") or provider)
            model_ref = model_ref_for(provider, model)
            result = {
                "provider": "pi", "client": "pi", "label": "Pi", "runtime": "Pi",
                "id": str(header["id"]).strip(), "session": os.path.basename(path),
                "path": path,
                "project": self.project_resolver(str(header.get("cwd") or "")) or "",
                "mtime": _mtime(path), "signature_mtime": _mtime(path), "title": "Pi session",
                "model": model_ref.model_id, "model_provider": model_ref.provider_id,
                "source_kind": "pi_jsonl", "corrupt": corrupt,
                "available": available, "truncated": truncated,
            }
        self._metadata_cache[path] = (signature, result)
        if len(self._metadata_cache) > MAX_SOURCES:
            self._metadata_cache.pop(next(iter(self._metadata_cache)), None)
        return dict(result) if result else None

    def _legacy_records(self):
        records = [self._metadata(path) for path in self._paths()]
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
            model = model_ref_for(record.get("model_provider"), record.get("model"))
            result.append(SessionSource(
                runtime_id=self.descriptor.runtime_id,
                client_id="pi",
                session_id=record["id"],
                display_label="Pi",
                project=record.get("project") or None,
                locator=SourceLocator("jsonl", record["path"]),
                activity_mtime=record["mtime"],
                revision=self._revision(record["path"]),
                model_ref=model,
                account_provider_id=None,
            ))
        return tuple(result)

    def _revision(self, path):
        return SourceRevision(("pi-jsonl", *_file_signature(path)))

    def current_revision(self, source):
        path = source.locator.value if isinstance(source, SessionSource) else source.get("path", "")
        return self._revision(path)

    def _parsed(self, path):
        if not self._owned_path(path):
            return {"turns": (), "corrupt": 0, "available": False, "truncated": False}
        rows, corrupt, available, truncated = _read_jsonl(path)
        header = rows[0] if rows else {}
        if not isinstance(header, dict) or header.get("type") != "session":
            return {"turns": (), "corrupt": corrupt, "available": available,
                    "truncated": truncated}
        turns = []
        pending_user_ts = 0.0
        previous_ts = 0.0
        provider = model = ""
        tools_by_call_id = {}
        for row in rows:
            kind = row.get("type")
            ts = _timestamp(row.get("timestamp"))
            if kind == "model_change":
                provider = str(row.get("provider") or provider)
                model = str(row.get("modelId") or model)
            if kind != "message":
                previous_ts = ts or previous_ts
                continue
            message = row.get("message")
            if not isinstance(message, dict):
                previous_ts = ts or previous_ts
                continue
            role = str(message.get("role") or "").lower()
            if role == "user":
                pending_user_ts = ts or previous_ts
            elif role == "toolresult":
                call = tools_by_call_id.get(str(message.get("toolCallId") or ""))
                if call is not None:
                    call["result_available"] = True
                    if message.get("isError") is True:
                        call["error"] = True
            elif role == "assistant" and len(turns) < MAX_TURNS:
                provider = str(message.get("provider") or provider)
                model = str(message.get("model") or model)
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                input_tokens = _integer(usage.get("input"))
                output_tokens = _integer(usage.get("output"))
                cache_read = _integer(usage.get("cacheRead"))
                cache_write = _integer(usage.get("cacheWrite"))
                # Pi reports reasoning as a subset of the output bucket.
                reasoning_tokens = _integer(usage.get("reasoning")) or 0
                if output_tokens is not None:
                    reasoning_tokens = min(reasoning_tokens, output_tokens)
                else:
                    reasoning_tokens = 0
                # Context in use is the request context Pi billed for, the
                # same basis Claude projects; the window stays unknown.
                context_available = (
                    input_tokens is not None and cache_read is not None
                    and cache_write is not None
                )
                context_tokens = (
                    input_tokens + cache_read + cache_write
                    if context_available else 0
                )
                token_available = input_tokens is not None and output_tokens is not None
                cache_available = cache_read is not None and cache_write is not None
                tools = []
                content = message.get("content")
                for item in content if isinstance(content, list) else ():
                    if not isinstance(item, dict) or item.get("type") != "toolCall":
                        continue
                    tool = {
                        "id": str(item.get("id") or ""),
                        "name": _normalize_tool_name(item.get("name")),
                        "category": _tool_category(item.get("name")),
                        "result_available": False,
                        "error": False,
                    }
                    tools.append(tool)
                    if tool["id"]:
                        tools_by_call_id[tool["id"]] = tool
                start = pending_user_ts or previous_ts or ts
                end = ts or start
                turns.append({
                    "index": len(turns) + 1, "start": start, "end": max(start, end),
                    "model": model_ref_for(provider, model).model_id,
                    "input_tokens": input_tokens or 0,
                    "output_tokens": output_tokens or 0,
                    "reasoning_tokens": reasoning_tokens,
                    "cache_read_tokens": cache_read or 0,
                    "cache_write_tokens": cache_write or 0,
                    "token_available": token_available,
                    "cache_available": cache_available,
                    "context_available": context_available,
                    "context_tokens": context_tokens,
                    "cost": _cost_breakdown(usage.get("cost")),
                    "tools": tools,
                })
                pending_user_ts = 0.0
            previous_ts = ts or previous_ts
        return {
            "turns": tuple(turns), "corrupt": corrupt,
            "available": available, "truncated": truncated,
        }

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
        tokens_available = bool(turns) and all(turn["token_available"] for turn in turns)
        cache_available = bool(turns) and all(turn["cache_available"] for turn in turns)
        cost_available = bool(turns) and all(turn["cost"] is not None for turn in turns)
        intervals = [
            (turn["start"], turn["end"]) for turn in turns
            if turn["start"] and turn["end"] >= turn["start"]
        ]
        active = merge_execution_intervals(intervals)
        warning_codes = []
        if parsed["corrupt"]:
            warning_codes.append("corrupt_rows")
        if not tokens_available:
            warning_codes.append("usage_unavailable")
        if parsed["truncated"]:
            warning_codes.append("history_truncated")
        messages = {
            "corrupt_rows": "Malformed Pi rows were ignored.",
            "usage_unavailable": "Pi token evidence was unavailable.",
            "history_truncated": "Detailed Pi history was bounded.",
        }
        return NormalizedSession(
            source=source,
            started_at=_date(min((turn["start"] for turn in turns if turn["start"]), default=0)),
            ended_at=_date(max((turn["end"] for turn in turns if turn["end"]), default=0)),
            usage=UsageEvidence(
                self._available(sum(turn["input_tokens"] for turn in turns), tokens_available),
                self._available(sum(turn["output_tokens"] for turn in turns), tokens_available),
                self._available(sum(turn["cache_read_tokens"] for turn in turns), cache_available),
                self._available(sum(turn["cache_write_tokens"] for turn in turns), cache_available),
                self._available(
                    sum(sum(turn["cost"].values()) for turn in turns if turn["cost"]),
                    cost_available, EvidenceBasis.ESTIMATED,
                ),
            ),
            timing=TimingEvidence(
                self._available(active, bool(intervals), EvidenceBasis.INFERRED),
                self._available(active, bool(intervals), EvidenceBasis.INFERRED),
                EvidenceValue.unavailable(),
            ),
            tools=tuple(
                ToolEvent(
                    tool["name"], tool["category"],
                    "error" if tool.get("error")
                    else "success" if tool.get("result_available") else None,
                )
                for turn in turns for tool in turn["tools"]
            )[:MAX_TOOLS],
            turns=tuple(
                TurnSummary(turn["index"], _date(turn["start"]), _date(turn["end"]),
                            self._available(turn["output_tokens"], turn["token_available"]))
                for turn in turns
            ) if detail is DetailLevel.FULL else (),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=tuple(ParseWarning(code, messages[code]) for code in warning_codes),
            detail=detail,
        )

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
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
        turns = self._legacy_rows(source)
        if not turns:
            return None
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace, wait_samples, intervals = [], [], [], [], []
        all_tokens_available = all(turn["token_available"] for turn in turns)
        all_cache_available = all(turn["cache_available"] for turn in turns)
        all_cost_available = all(turn["cost"] is not None for turn in turns)
        for turn in turns:
            breakdown = turn["cost"] or {key: 0.0 for key in cost}
            cost_available = turn["cost"] is not None
            execution_cost = sum(breakdown.values())
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
                "pi", cost=cost_available, tokens=turn["token_available"],
                input_tokens=turn["token_available"], output_tokens=turn["token_available"],
                cache=turn["cache_available"], throughput=False, context=False,
                timing=timing_available,
                tool_results=any(tool["result_available"] for tool in tools),
            )
            duration = max(0.0, turn["end"] - turn["start"])
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
                    "reasoning": 0, "retrieval": 0, "fresh_input": usage["input_tokens"],
                    "cache": usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"],
                    "cache_read": usage["cache_read_input_tokens"],
                    "cache_write": usage["cache_creation_input_tokens"], "total": total,
                },
                "cost": execution_cost, "cost_breakdown": breakdown, "tools": tools,
                "tool_count": len(tools), "model_calls": 1,
                "reasoning_tokens": turn["reasoning_tokens"],
                "reasoning_duration_ms": 0,
                "context_tokens": turn["context_tokens"],
                "context_window": 0, "context_pct": None,
                "duration_ms": duration * 1000 if duration else None,
                "wait_duration_ms": duration * 1000 if duration else None,
                "summary": "Execution {}: {} tools · {}".format(
                    turn["index"], len(tools),
                    "${:.3f} Pi estimate".format(execution_cost)
                    if cost_available else "cost unavailable",
                ),
                "user_message": "", "user_input": "", "availability": availability,
            })
            trace.append(compat["trace_event"](
                turn["start"], "user", "User input", "Content excluded", turn["index"],
                severity="start", model=turn["model"], native_type="user", native_subtype="user_message",
            ))
            for tool in tools:
                trace.append(compat["trace_event"](
                    turn["end"], "tool_call", tool["display"], "Payload excluded", turn["index"],
                    tool=tool["name"], severity="warn" if tool.get("error") else "tool",
                    model=turn["model"],
                    native_type="tool_call", native_subtype="tool_call",
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
            for key in cost:
                cost[key] += breakdown[key]
            model_tok[turn["model"]] += total
            model_cost[turn["model"]] += execution_cost
            if timing_available:
                intervals.append((turn["start"], turn["end"]))
                wait_samples.append({
                    "provider": "pi", "model": turn["model"],
                    "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
                    "ts": turn["end"], "start_ts": turn["start"], "duration_s": duration,
                    "generation_s": duration, "ttft_s": 0.0,
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
            "pi", cost=all_cost_available, tokens=all_tokens_available,
            input_tokens=all_tokens_available, output_tokens=all_tokens_available,
            cache=all_cache_available, throughput=throughput["available"],
            context=context_available, timing=bool(intervals),
            tool_results=any(tool.get("result_available") for execution in executions for tool in execution["tools"]),
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
            "Pi-recorded local cost estimate; no Token Meter price was inferred.",
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
        turns = self._legacy_rows(source)
        model_cost, model_tok, model_stats, model_daily = (
            defaultdict(float), defaultdict(int), {}, {}
        )
        day_cost, tool_calls, intervals, models, wait_samples, context_samples = (
            defaultdict(float), [], [], set(), [], []
        )
        total_cost = input_tokens = output_tokens = 0
        all_tokens_available = bool(turns) and all(turn["token_available"] for turn in turns)
        all_cache_available = bool(turns) and all(turn["cache_available"] for turn in turns)
        all_cost_available = bool(turns) and all(turn["cost"] is not None for turn in turns)
        all_context_available = bool(turns) and all(turn["context_available"] for turn in turns)
        for turn in turns:
            usage = self._legacy_usage(turn)
            breakdown = turn["cost"] or {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
            value = sum(breakdown.values())
            total = sum(usage.values())
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            total_cost += value
            model_cost[turn["model"]] += value
            model_tok[turn["model"]] += total
            models.add(turn["model"])
            compat["add_model_summary"](model_stats, turn["model"], usage, value,
                                         cost_available=turn["cost"] is not None)
            compat["add_model_daily"](model_daily, turn["model"], usage, value, turn["end"],
                                       cost_available=turn["cost"] is not None)
            if turn["end"]:
                day_cost[time.strftime("%Y-%m-%d", time.localtime(turn["end"]))] += value
            if turn["start"] and turn["end"] >= turn["start"]:
                intervals.append((turn["start"], turn["end"]))
                duration = turn["end"] - turn["start"]
                context_samples.append(turn["context_tokens"])
                wait_samples.append({
                    "provider": "pi", "model": turn["model"],
                    "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
                    "ts": turn["end"], "start_ts": turn["start"],
                    "duration_s": duration, "generation_s": duration, "ttft_s": 0.0,
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
        throughput = performance_summary(wait_samples, output_tokens)
        availability = compat["metric_availability"](
            "pi", cost=all_cost_available, tokens=all_tokens_available,
            input_tokens=all_tokens_available, output_tokens=all_tokens_available,
            cache=all_cache_available, throughput=throughput["available"],
            context=all_context_available, timing=bool(intervals),
            tool_results=False,
        )
        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = compat["metric_availability"](
                "pi", cost=int(stats.get("cost_covered_executions") or 0) > 0,
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
        if not isinstance(source, SessionSource) or source.locator.kind != "jsonl":
            return DeletionPlan.deny("Pi deletion requires one normalized session trace.")
        path = os.path.abspath(source.locator.value)
        if not self._owned_path(path):
            return DeletionPlan.deny("Pi source is outside the adapter-owned directory.")
        return DeletionPlan(
            DeletionDisposition.TRASH,
            "Move this Pi session trace to Trash.",
            (SourceLocator("jsonl", path),),
        )


class PiRuntimeAdapterProxy:
    descriptor = PiRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Pi adapter")
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
