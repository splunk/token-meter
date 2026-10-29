"""Native read-only adapter for Kiro session and agent-execution evidence."""

import glob
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
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
    PriceQuery,
    RuntimeDescriptor,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    ToolEvent,
    TurnSummary,
    UsageEvidence,
)
from token_meter.domain.cache_window import CacheRequestTracker
from token_meter.domain.timing import merge_execution_intervals
from token_meter.domain.usage import format_usd


CHARS_PER_TOKEN = 4
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000
MAX_SOURCES = 2_000
MAX_TURNS = 2_000
MAX_TOOLS = 2_000


def default_agent_storage_root(home, environment=None):
    """Return Kiro's host-local extension store without a platform dependency."""
    environment = dict(environment or {})
    override = environment.get("KIRO_AGENT_STORAGE")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    local_app_data = environment.get("LOCALAPPDATA")
    if local_app_data:
        return os.path.join(local_app_data, "Kiro", "User", "globalStorage", "kiro.kiroagent")
    if sys.platform == "darwin":
        return os.path.join(
            home, "Library", "Application Support", "Kiro", "User",
            "globalStorage", "kiro.kiroagent",
        )
    config_home = environment.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(config_home, "Kiro", "User", "globalStorage", "kiro.kiroagent")


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


def _compact(value, limit=90):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def _safe_json_chars(value):
    """Count serialized evidence without retaining its private value."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError, OverflowError):
        return 0


def _content_chars(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_content_chars(item) for item in value)
    if isinstance(value, dict):
        if "text" in value:
            return _content_chars(value.get("text"))
        if "content" in value:
            return _content_chars(value.get("content"))
        if str(value.get("kind") or value.get("type") or "").lower() in (
                "text", "markdown"):
            return _content_chars(value.get("data"))
    return 0


def _message_payload(row):
    """Normalize Kiro desktop and CLI message envelopes."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
    if payload.get("type"):
        return payload
    kind = {
        "prompt": "user",
        "assistantmessage": "assistant",
    }.get(str(row.get("kind") or "").strip().lower())
    data = row.get("data")
    if kind is None or not isinstance(data, dict):
        return payload
    normalized = dict(data)
    normalized["type"] = kind
    metadata = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    normalized["timestamp"] = metadata.get("timestamp") or data.get("timestamp")
    return normalized


def _token_estimate(characters):
    return int(math.ceil(max(0, int(characters or 0)) / CHARS_PER_TOKEN))


def _normalize_model(value):
    value = str(value or "").strip().lower()
    if value.startswith("claude-"):
        value = re.sub(r"(?<=\d)\.(?=\d)", "-", value)
    return value or "unknown-model"


def model_ref_for(value):
    """Resolve Kiro's model independently from the Kiro runtime identity."""
    model_id = _normalize_model(value)
    if model_id.startswith("claude-"):
        provider_id = "anthropic"
    elif model_id.startswith(("gpt-", "o1", "o3", "o4")):
        provider_id = "openai"
    elif model_id.startswith(("amazon.", "amazon-", "nova-")):
        provider_id = "amazon"
    else:
        provider_id = "unknown-model-provider"
    return ModelRef(provider_id, model_id)


def _normalize_tool_name(value):
    value = str(value or "tool").strip()
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").lower()
    aliases = {
        "readfiles": "read_files", "readfile": "read_file",
        "runcommand": "run_command", "writefile": "write_file",
        "searchfiles": "search_files", "listdirectory": "list_directory",
        "grepsearch": "grep_search", "intentclassification": "intent_classification",
    }
    return aliases.get(value.replace("_", ""), value or "tool")


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


def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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


def _directory_revision(path):
    digest = hashlib.sha256()
    count = 0
    try:
        entries = sorted(os.scandir(path), key=lambda item: item.name)
    except OSError:
        entries = ()
    for entry in entries:
        if count >= MAX_ROWS or not entry.is_file(follow_symlinks=False):
            continue
        if entry.name.endswith((".json", ".jsonl")):
            continue
        mtime, size = _file_signature(entry.path)
        digest.update(entry.name.encode("utf-8", "replace"))
        digest.update(mtime.encode("ascii", "replace"))
        digest.update(size.encode("ascii", "replace"))
        count += 1
    return str(count), digest.hexdigest()


class KiroRuntimeAdapter:
    """Own Kiro discovery, safe parsing, revisions, and compatibility output."""

    descriptor = RuntimeDescriptor(
        "kiro",
        "Kiro",
        frozenset(("sessions", "models", "tools")),
        "runtime.generic",
        "runtime-neutral",
        None,
    )

    def __init__(self, sessions_root, agent_storage_root=None, project_resolver=None,
                 quote_resolver=None, compatibility=None, path_cache=None):
        self.sessions_root = Path(os.path.abspath(os.path.expanduser(str(sessions_root))))
        self.agent_storage_root = (
            Path(os.path.abspath(os.path.expanduser(str(agent_storage_root))))
            if agent_storage_root else None
        )
        self.project_resolver = project_resolver or (lambda value: value)
        self.quote_resolver = quote_resolver
        self.compatibility = dict(compatibility or {})
        self.path_cache = path_cache
        self._metadata_cache = {}
        self._agent_identity_cache = {}

    def _glob(self, pattern):
        if self.path_cache is not None:
            return self.path_cache.paths(pattern)
        return tuple(glob.glob(pattern))

    def metadata(self, session_dir):
        path = os.path.join(str(session_dir), "session.json")
        signature = _file_signature(path)
        cached = self._metadata_cache.get(path)
        if cached and cached[0] == signature:
            return dict(cached[1])
        data = _read_json(path) or {}
        paths = data.get("workspacePaths")
        paths = paths if isinstance(paths, list) else []
        result = {
            "id": str(data.get("id") or ""),
            "title": _compact(data.get("title"), 90),
            "model": _normalize_model(data.get("modelId")),
            "agent_mode": _compact(data.get("agentMode") or "vibe", 30),
            "autopilot": bool(data.get("autopilot")),
            "workspace_paths": tuple(
                str(path) for path in paths[:8] if isinstance(path, str) and path
            ),
            "last_modified_at": _timestamp(data.get("lastModifiedAt")),
        }
        self._metadata_cache[path] = (signature, result)
        if len(self._metadata_cache) > MAX_SOURCES:
            self._metadata_cache.pop(next(iter(self._metadata_cache)), None)
        return dict(result)

    def _agent_identity(self, path):
        signature = _file_signature(path)
        cached = self._agent_identity_cache.get(path)
        if cached and cached[0] == signature:
            return dict(cached[1])
        row = _read_json(path) or {}
        input_value = row.get("input") if isinstance(row.get("input"), dict) else {}
        input_data = input_value.get("data") if isinstance(input_value.get("data"), dict) else {}
        workspace_paths = input_data.get("workspacePaths")
        workspace_paths = workspace_paths if isinstance(workspace_paths, list) else []
        result = {
            "model": _normalize_model(input_data.get("modelId") or row.get("modelId")),
            "workspace_paths": tuple(
                str(value) for value in workspace_paths[:8]
                if isinstance(value, str) and value
            ),
        }
        self._agent_identity_cache[path] = (signature, result)
        if len(self._agent_identity_cache) > MAX_SOURCES:
            self._agent_identity_cache.pop(next(iter(self._agent_identity_cache)), None)
        return dict(result)

    def _message_records(self):
        records = []
        patterns = (
            str(self.sessions_root / "*" / "*" / "messages.jsonl"),
            str(self.sessions_root / "cli" / "*.jsonl"),
        )
        for path in (item for pattern in patterns for item in self._glob(pattern)):
            if len(records) >= MAX_SOURCES:
                break
            path = os.path.abspath(path)
            session_dir = os.path.dirname(path)
            cli = os.path.basename(os.path.dirname(path)) == "cli"
            metadata = {} if cli else self.metadata(session_dir)
            workspace = "cli" if cli else os.path.basename(os.path.dirname(session_dir))
            session_id = (
                os.path.basename(path).rsplit(".", 1)[0] if cli else
                metadata.get("id") or os.path.basename(session_dir)
            )
            workspace_paths = metadata.get("workspace_paths") or ()
            project = workspace_paths[0] if workspace_paths else ("CLI" if cli else workspace)
            model = metadata.get("model") or "claude-sonnet-4-6"
            title = metadata.get("title") or ""
            if title.lower() in ("new session", "untitled"):
                title = ""
            records.append({
                "provider": "kiro",
                "client": "kiro_cli" if cli else "kiro",
                "label": "Kiro CLI" if cli else "Kiro",
                "runtime": "Kiro",
                "id": str(session_id),
                "session": os.path.basename(path),
                "path": path,
                "project": self.project_resolver(project),
                "mtime": max(_mtime(path), float(metadata.get("last_modified_at") or 0)),
                "signature_mtime": max(_mtime(path), _mtime(os.path.join(session_dir, "session.json"))),
                "title": title or None,
                "model": model,
                "model_provider": model_ref_for(model).provider_id,
                "agent_mode": metadata.get("agent_mode") or "vibe",
                "autopilot": bool(metadata.get("autopilot")),
                "source_kind": "messages",
                "metadata_path": os.path.join(session_dir, "session.json"),
            })
        return records

    def _agent_records(self):
        if self.agent_storage_root is None:
            return []
        records = []
        pattern = str(self.agent_storage_root / "*" / "*")
        for session_dir in self._glob(pattern):
            if len(records) >= MAX_SOURCES or not os.path.isdir(session_dir):
                continue
            execution_paths = tuple(
                path for path in self._glob(os.path.join(session_dir, "*"))
                if os.path.isfile(path) and not path.endswith((".json", ".jsonl"))
            )
            if not execution_paths:
                continue
            newest = max(execution_paths, key=_mtime)
            identity = self._agent_identity(newest)
            workspace_paths = identity.get("workspace_paths") or ()
            workspace = os.path.basename(os.path.dirname(session_dir))
            session_id = os.path.basename(session_dir)
            model = identity.get("model") or "unknown-model"
            if model == "unknown-model":
                model = "claude-sonnet-4-6"
            project = next(
                (str(value) for value in workspace_paths if isinstance(value, str) and value),
                workspace,
            )
            records.append({
                "provider": "kiro", "client": "kiro_agent", "label": "Kiro",
                "runtime": "Kiro", "id": "kiro-agent-{}-{}".format(workspace, session_id),
                "session": session_id, "path": os.path.abspath(session_dir),
                "project": self.project_resolver(project), "mtime": _mtime(newest),
                "signature_mtime": _mtime(newest), "title": None, "model": model,
                "model_provider": model_ref_for(model).provider_id,
                "agent_mode": "vibe", "autopilot": True, "source_kind": "agent",
            })
        return records

    def _legacy_records(self):
        by_id = {}
        for record in self._message_records() + self._agent_records():
            key = (record["id"], record["path"])
            by_id[key] = record
        return tuple(sorted(
            by_id.values(), key=lambda row: (-float(row.get("mtime") or 0), row["id"])
        )[:MAX_SOURCES])

    def discover_legacy(self, context):
        del context
        return self._legacy_records()

    def discover(self, context):
        del context
        result = []
        for record in self._legacy_records():
            model = model_ref_for(record.get("model"))
            locator_kind = "directory" if record.get("source_kind") == "agent" else "jsonl"
            source = SessionSource(
                runtime_id=self.descriptor.runtime_id,
                client_id=record["client"],
                session_id=record["id"],
                display_label=record["label"],
                project=record.get("project"),
                locator=SourceLocator(locator_kind, record["path"]),
                activity_mtime=record["mtime"],
                revision=self._revision(record["path"], locator_kind, record.get("metadata_path")),
                model_ref=model,
                account_provider_id=None,
            )
            result.append(source)
        return tuple(result)

    def _revision(self, path, locator_kind, metadata_path=None):
        if locator_kind == "directory":
            return SourceRevision(("agent", *_directory_revision(path)))
        return SourceRevision((
            "messages", *_file_signature(path), *_file_signature(metadata_path or "")
        ))

    def current_revision(self, source):
        if isinstance(source, SessionSource):
            return self._revision(source.locator.value, source.locator.kind)
        kind = "directory" if source.get("source_kind") == "agent" else "jsonl"
        return self._revision(source.get("path", ""), kind, source.get("metadata_path"))

    def _message_turns(self, path):
        rows, corrupt, available, truncated = _read_jsonl(path)
        turns = []
        current = None
        recognized = 0

        def start(ts=0.0):
            return {
                "start": ts, "end": ts, "input_chars": 0, "output_chars": 0,
                "tools": [], "model": None,
            }

        def finish():
            if current is not None and (
                    current["input_chars"] or current["output_chars"] or current["tools"]):
                turns.append(current)

        for row in rows:
            payload = _message_payload(row)
            kind = str(payload.get("type") or "").lower()
            ts = _timestamp(row.get("timestamp") or payload.get("timestamp"))
            if kind == "user":
                finish()
                current = start(ts)
                current["input_chars"] += _content_chars(payload.get("content"))
                recognized += 1
                continue
            if kind == "turn_start" and current is None:
                current = start(ts)
            if current is None and kind in ("assistant", "tool_call", "tool_result"):
                current = start(ts)
            if current is None:
                continue
            if ts:
                current["start"] = current["start"] or ts
                current["end"] = max(current["end"] or 0, ts)
            if payload.get("modelId"):
                current["model"] = _normalize_model(payload.get("modelId"))
            if kind == "assistant":
                current["output_chars"] += _content_chars(payload.get("content"))
                recognized += 1
            elif kind == "tool_call":
                name = _normalize_tool_name(payload.get("toolName"))
                current["input_chars"] += _safe_json_chars(payload.get("args"))
                current["tools"].append({
                    "id": str(payload.get("toolCallId") or ""), "name": name,
                    "category": _tool_category(name), "output_tokens": 0,
                    "result_available": False, "error": False, "ts": ts,
                })
                recognized += 1
            elif kind == "tool_result":
                chars = _content_chars(payload.get("content"))
                current["input_chars"] += chars
                call_id = str(payload.get("toolCallId") or "")
                match = next(
                    (tool for tool in reversed(current["tools"])
                     if not call_id or tool["id"] == call_id), None,
                )
                if match is not None:
                    match["output_tokens"] = _token_estimate(chars)
                    match["result_available"] = True
                    match["error"] = payload.get("success") is False
                recognized += 1
        finish()
        return turns[:MAX_TURNS], corrupt, available, truncated or len(turns) > MAX_TURNS, recognized

    def _agent_turns(self, path):
        rows = []
        corrupt = 0
        for execution_path in sorted(self._glob(os.path.join(path, "*")), key=_mtime):
            if len(rows) >= MAX_TURNS:
                break
            if not os.path.isfile(execution_path) or execution_path.endswith((".json", ".jsonl")):
                continue
            row = _read_json(execution_path)
            if row is None:
                corrupt += 1
                continue
            if row.get("status") not in ("succeed", "running", "aborted", "failed"):
                continue
            rows.append(row)
        turns = []
        for row in rows:
            input_data = row.get("input") if isinstance(row.get("input"), dict) else {}
            input_data = input_data.get("data") if isinstance(input_data.get("data"), dict) else {}
            input_chars = 0
            for message in input_data.get("messages") or ():
                if isinstance(message, dict) and message.get("role") == "user":
                    input_chars += _content_chars(message.get("content"))
            output_chars = 0
            tools = []
            for action in row.get("actions") or ():
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("actionType") or "")
                if action_type == "say":
                    output = action.get("output")
                    output_chars += _content_chars(output)
                    continue
                name = _normalize_tool_name(action_type)
                input_chars += _safe_json_chars(action.get("input"))
                output_count = _safe_json_chars(action.get("output"))
                input_chars += output_count
                state = str(action.get("actionState") or "").lower()
                tools.append({
                    "id": str(action.get("actionId") or ""), "name": name,
                    "category": _tool_category(name),
                    "output_tokens": _token_estimate(output_count),
                    "result_available": action.get("output") is not None,
                    "error": state in ("failed", "error"),
                    "ts": _timestamp(row.get("endTime") or row.get("startTime")),
                })
            turns.append({
                "start": _timestamp(row.get("startTime")),
                "end": _timestamp(row.get("endTime") or row.get("startTime")),
                "input_chars": input_chars, "output_chars": output_chars,
                "tools": tools, "model": _normalize_model(
                    input_data.get("modelId") or row.get("modelId")
                ),
            })
        return turns, corrupt, True, len(rows) >= MAX_TURNS, len(turns)

    def _parsed(self, path, locator_kind):
        parsed = (
            self._agent_turns(path) if locator_kind == "directory"
            else self._message_turns(path)
        )
        turns, corrupt, available, truncated, recognized = parsed
        result = []
        for index, turn in enumerate(turns[:MAX_TURNS], 1):
            result.append({
                **turn,
                "index": index,
                "input_tokens": _token_estimate(turn.get("input_chars")),
                "output_tokens": _token_estimate(turn.get("output_chars")),
                "tools": tuple(turn.get("tools") or ())[:MAX_TOOLS],
            })
        return {
            "turns": tuple(result), "corrupt": corrupt, "available": available,
            "truncated": truncated, "recognized": recognized,
        }

    def _source_from_legacy(self, source):
        model = model_ref_for(source.get("model"))
        kind = "directory" if source.get("source_kind") == "agent" else "jsonl"
        return SessionSource(
            "kiro", source.get("client") or "kiro", source.get("id") or "kiro-session",
            source.get("label") or "Kiro", source.get("project"),
            SourceLocator(kind, source.get("path") or "kiro:missing"),
            source.get("mtime") or 0, self._revision(
                source.get("path") or "", kind, source.get("metadata_path")
            ), model, None,
        )

    def _cost(self, model, input_tokens, output_tokens, observed_at=0.0):
        if self.quote_resolver is None:
            return None
        when = datetime.fromtimestamp(observed_at, timezone.utc) if observed_at else None
        try:
            quote = self.quote_resolver(PriceQuery(model, when))
        except Exception:
            return None
        if not getattr(quote, "available", False):
            return None
        value = (
            input_tokens * float(quote.input_per_million or 0)
            + output_tokens * float(quote.output_per_million or 0)
        ) / 1_000_000.0
        return self._discount(value, observed_at)[0]

    @staticmethod
    def _priced_at(turn):
        """The one time that prices, discounts, and dates a turn: its end, else its start."""
        return turn.get("end") or turn.get("start") or 0

    def _session_cost(self, model_ref, turns):
        """Sum each turn priced and discounted at its own time; unavailable if any turn is."""
        if not turns:
            return self._cost(model_ref, 0, 0, 0)
        total = 0.0
        for turn in turns:
            value = self._cost(model_ref, turn["input_tokens"], turn["output_tokens"], self._priced_at(turn))
            if value is None:
                return None
            total += value
        return total

    def _discount(self, value, observed_at):
        """Kiro bills its own credits: only a Kiro discount applies, on any channel."""
        discount = (self.compatibility or {}).get("discount_amount")
        if not callable(discount):
            return value, 0.0
        result = discount(value, "kiro", "any", observed_at or None)
        return (result, result.discount_usd) if isinstance(value, dict) else result

    @staticmethod
    def _available(value, available, basis=EvidenceBasis.ESTIMATED):
        return EvidenceValue(value, basis) if available else EvidenceValue.unavailable()

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != self.descriptor.runtime_id:
            raise ValueError("source belongs to another runtime")
        parsed = self._parsed(source.locator.value, source.locator.kind)
        turns = parsed["turns"]
        evidence_available = bool(parsed["available"] and parsed["recognized"] and turns)
        input_tokens = sum(turn["input_tokens"] for turn in turns)
        output_tokens = sum(turn["output_tokens"] for turn in turns)
        intervals = [
            (turn["start"], turn["end"]) for turn in turns
            if turn["start"] and turn["end"] >= turn["start"]
        ]
        duration = merge_execution_intervals(intervals)
        tools = tuple(
            ToolEvent(tool["name"], tool["category"],
                      "error" if tool.get("error") else "success")
            for turn in turns for tool in turn["tools"]
        )[:MAX_TOOLS]
        cost = self._session_cost(source.model_ref, turns) if source.model_ref else None
        warning_codes = []
        if parsed["corrupt"]:
            warning_codes.append("corrupt_rows")
        if not evidence_available:
            warning_codes.append("usage_unavailable")
        if parsed["truncated"]:
            warning_codes.append("history_truncated")
        messages = {
            "corrupt_rows": "Malformed Kiro rows were ignored.",
            "usage_unavailable": "Kiro token evidence was unavailable.",
            "history_truncated": "Detailed Kiro history was bounded.",
        }
        started = min((turn["start"] for turn in turns if turn["start"]), default=0)
        ended = max((turn["end"] for turn in turns if turn["end"]), default=0)
        return NormalizedSession(
            source=source, started_at=_date(started), ended_at=_date(ended),
            usage=UsageEvidence(
                self._available(input_tokens, evidence_available),
                self._available(output_tokens, evidence_available),
                self._available(0, evidence_available),
                self._available(0, evidence_available),
                self._available(cost or 0.0, cost is not None),
            ),
            timing=TimingEvidence(
                self._available(duration, bool(intervals), EvidenceBasis.INFERRED),
                self._available(duration, bool(intervals), EvidenceBasis.INFERRED),
                EvidenceValue.unavailable(),
            ),
            tools=tools,
            turns=tuple(
                TurnSummary(
                    turn["index"], _date(turn["start"]), _date(turn["end"]),
                    EvidenceValue(turn["output_tokens"], EvidenceBasis.ESTIMATED),
                ) for turn in turns
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
        kind = "directory" if source.get("source_kind") == "agent" else "jsonl"
        return self._parsed(source.get("path") or "", kind)["turns"]

    def _legacy_turn(self, source, turn):
        model = model_ref_for(turn.get("model") or source.get("model"))
        value = self._cost(
            model, turn["input_tokens"], turn["output_tokens"], self._priced_at(turn)
        )
        cost_available = value is not None
        input_cost = 0.0
        output_cost = 0.0
        if cost_available and self.quote_resolver is not None:
            priced_at = self._priced_at(turn)
            when = datetime.fromtimestamp(priced_at, timezone.utc) if priced_at else None
            quote = self.quote_resolver(PriceQuery(model, when))
            input_cost = turn["input_tokens"] * float(quote.input_per_million or 0) / 1_000_000
            output_cost = turn["output_tokens"] * float(quote.output_per_million or 0) / 1_000_000
        breakdown = {
            "input": input_cost, "cache_write": 0.0,
            "cache_read": 0.0, "output": output_cost,
        }
        if cost_available:
            breakdown = self._discount(breakdown, self._priced_at(turn))[0]
        return model, breakdown, cost_available

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        turns = self._legacy_rows(source)
        if not turns:
            return None
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok = defaultdict(int)
        model_cost = defaultdict(float)
        series = []
        executions = []
        trace = []
        wait_samples = []
        intervals = []
        all_cost_available = True
        for turn in turns:
            model, breakdown, cost_available = self._legacy_turn(source, turn)
            all_cost_available = all_cost_available and cost_available
            execution_cost = sum(breakdown.values())
            tools = [{
                "id": tool.get("id") or "", "call_id": tool.get("id") or "",
                "name": tool["name"], "display": tool["name"].replace("_", " ").title(),
                "namespace": tool["category"], "kind": "tool", "args_chars": 0,
                "output_chars": int(tool.get("output_tokens") or 0) * CHARS_PER_TOKEN,
                "output_tokens": int(tool.get("output_tokens") or 0),
                "result_available": bool(tool.get("result_available")),
                "error": bool(tool.get("error")), "skills": [],
            } for tool in turn["tools"]]
            usage = {
                "input_tokens": turn["input_tokens"],
                "output_tokens": turn["output_tokens"],
            }
            total_tokens = turn["input_tokens"] + turn["output_tokens"]
            availability = compat["metric_availability"](
                "kiro", cost=cost_available, tokens=True, input_tokens=True,
                output_tokens=True, cache=False, throughput=False, context=False,
                timing=bool(turn["start"] and turn["end"] >= turn["start"]),
                tool_results=any(tool["result_available"] for tool in tools),
            )
            series.append({
                "i": turn["index"], "in": turn["input_tokens"],
                "out": turn["output_tokens"], "cost": execution_cost,
                "fresh_input": turn["input_tokens"], "cache": 0,
                "cache_read": 0, "cache_write": 0, "think": False,
                "tools": len(tools), "side": False, "reasoning": 0,
                "reasoning_ms": 0, "context_pct": None,
                "context_tokens": turn["input_tokens"], "user_message": "",
                "user_input": "", "availability": availability,
            })
            duration = max(0.0, float(turn["end"] or 0) - float(turn["start"] or 0))
            executions.append({
                "id": "{}:{}".format(source["id"], turn["index"]),
                "idx": turn["index"], "ts": turn["end"] or turn["start"],
                "time": time.strftime("%H:%M", time.localtime(turn["end"] or turn["start"] or 0)),
                "model": model.model_id,
                "tokens": {
                    "input": turn["input_tokens"], "output": turn["output_tokens"],
                    "reasoning": 0,
                    "retrieval": sum(tool["output_tokens"] for tool in tools),
                    "fresh_input": turn["input_tokens"], "cache": 0,
                    "cache_read": 0, "cache_write": 0, "total": total_tokens,
                },
                "cost": execution_cost,
                "discount_usd": float(getattr(breakdown, "discount_usd", 0.0)),
                "cost_breakdown": dict(breakdown), "tools": tools,
                "tool_count": len(tools), "model_calls": 1,
                "reasoning_tokens": 0, "reasoning_duration_ms": 0,
                "context_tokens": turn["input_tokens"], "context_window": 0,
                "context_pct": None, "duration_ms": duration * 1000 if duration else None,
                "wait_duration_ms": duration * 1000 if duration else None,
                "summary": "Execution {}: {} tools · {}".format(
                    turn["index"], len(tools),
                    "{} est".format(format_usd(execution_cost))
                    if cost_available else "cost unavailable",
                ),
                "user_message": "", "user_input": "", "availability": availability,
            })
            trace.append(compat["trace_event"](
                turn["start"], "user", "User input", "Content excluded",
                turn["index"], severity="start", model=model.model_id,
                native_type="user", native_subtype="user_message",
            ))
            for tool in tools:
                trace.append(compat["trace_event"](
                    turn["end"], "tool_call", tool["display"], "Payload excluded",
                    turn["index"], tool=tool["name"], severity="tool",
                    model=model.model_id,
                    native_type="tool_call", native_subtype="tool_call",
                ))
            trace.append(compat["trace_event"](
                turn["end"], "complete", "Execution complete", "",
                turn["index"], severity="good", model=model.model_id,
                cost=execution_cost if cost_available else None,
                native_type="assistant", native_subtype="agent_message",
            ))
            tot["input"] += turn["input_tokens"]
            tot["output"] += turn["output_tokens"]
            for key in cost:
                cost[key] += breakdown[key]
            model_tok[model.model_id] += total_tokens
            model_cost[model.model_id] += execution_cost
            if duration:
                intervals.append((turn["start"], turn["end"]))
                wait_samples.append({
                    "provider": "kiro", "model": model.model_id,
                    "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
                    "ts": turn["end"], "start_ts": turn["start"],
                    "duration_s": duration, "tool_calls": len(tools),
                    "output_tokens": turn["output_tokens"],
                    "context_tokens": turn["input_tokens"], "model_calls": 1,
                    "timing_basis": "inferred",
                })
        total_tokens = sum(tot.values())
        total_cost = sum(cost.values())
        tool_data = compat["tool_summary"](executions)
        primary_model = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        analyses = compat["analysis_block"](
            tot, total_cost, 0, 0, 0.0, model_tok, model_cost,
            tool_data, 0.0, 0, len(executions),
        )
        active = merge_execution_intervals(intervals)
        source = dict(source)
        source.update({
            "token_estimate": True, "estimate_basis": "visible_text_chars",
            "context_latest": executions[-1]["context_tokens"] if executions else 0,
        })
        availability = compat["metric_availability"](
            "kiro", cost=all_cost_available, tokens=True, input_tokens=True,
            output_tokens=True, cache=False, throughput=False, context=False,
            timing=bool(intervals),
            tool_results=any(tool.get("result_available")
                             for execution in executions for tool in execution["tools"]),
        )
        biggest = max(
            ({"cost": execution["cost"], "idx": execution["idx"]}
             for execution in executions), key=lambda row: row["cost"], default=None,
        ) if all_cost_available else None
        state = compat["build_state"](
            source, tot, cost, total_tokens, total_cost, series, executions, trace,
            {"reasoning": 0, "output": tot["output"],
             "retrieval": tool_data["total_output_tokens"], "coordination": 0},
            analyses, [], min((turn["start"] for turn in turns if turn["start"]), default=0),
            max((turn["end"] for turn in turns if turn["end"]), default=0),
            0, biggest, 0, True, primary_model,
            "Local Kiro visible-text estimate; public model rates when available.",
            {"duration_s": active, "available": bool(intervals),
             "reported_executions": 0, "observed_executions": len(intervals),
             "execution_count": len(executions), "basis": "inferred"},
            wait_samples, availability=availability,
        )
        state["throughput"] = {"available": False}
        state["token_estimate"] = True
        state["estimation"] = {
            "basis": "visible message text divided by {} chars/token".format(CHARS_PER_TOKEN),
            "input": "visible user, tool argument, and tool result character estimate",
            "output": "visible assistant character estimate",
            "excluded": ["cache accounting", "hidden reasoning", "system prompts", "internal context"],
        }
        state["context"]["estimated"] = True
        state["kiro_info"] = {
            "agent_mode": source.get("agent_mode") or "vibe",
            "autopilot": bool(source.get("autopilot")),
        }
        return state

    def summarize_legacy(self, source, unused=None):
        del unused
        compat = self._require_compatibility()
        turns = self._legacy_rows(source)
        model_cost = defaultdict(float)
        model_tok = defaultdict(int)
        model_stats = {}
        model_daily = {}
        day_cost = defaultdict(float)
        tool_calls = []
        total_cost = 0.0
        input_tokens = 0
        output_tokens = 0
        all_cost_available = bool(turns)
        intervals = []
        models = set()
        cache_tracker = CacheRequestTracker(
            cache_reported=False, discount_price_list="kiro", discount_channel="any",
        )
        for turn in turns:
            model, breakdown, cost_available = self._legacy_turn(source, turn)
            cache_tracker.observe(
                turn.get("end") or turn.get("start"), model.model_id, {},
                start_ts=turn.get("start"),
            )
            value = sum(breakdown.values())
            discount = float(getattr(breakdown, "discount_usd", 0.0))
            all_cost_available = all_cost_available and cost_available
            usage = {
                "input_tokens": turn["input_tokens"],
                "output_tokens": turn["output_tokens"],
            }
            total = turn["input_tokens"] + turn["output_tokens"]
            input_tokens += turn["input_tokens"]
            output_tokens += turn["output_tokens"]
            total_cost += value
            model_cost[model.model_id] += value
            model_tok[model.model_id] += total
            models.add(model.model_id)
            compat["add_model_summary"](
                model_stats, model.model_id, usage, value,
                cost_available=cost_available, discount=discount,
            )
            compat["add_model_daily"](
                model_daily, model.model_id, usage, value,
                self._priced_at(turn),
                cost_available=cost_available, discount=discount,
            )
            if self._priced_at(turn):
                day_cost[time.strftime("%Y-%m-%d", time.localtime(self._priced_at(turn)))] += value
            if turn.get("start") and turn.get("end") >= turn.get("start"):
                intervals.append((turn["start"], turn["end"]))
            for tool in turn["tools"]:
                tool_calls.append({
                    "name": tool["name"], "display": tool["name"].replace("_", " ").title(),
                    "namespace": tool["category"], "kind": "tool",
                    "output_tokens": int(tool.get("output_tokens") or 0),
                    "error": bool(tool.get("error")), "ts": tool.get("ts") or turn.get("end"),
                    "skills": [],
                })
        availability = compat["metric_availability"](
            "kiro", cost=all_cost_available, tokens=bool(turns),
            input_tokens=bool(turns), output_tokens=bool(turns), cache=False,
            throughput=False, context=False, timing=bool(intervals),
            tool_results=any(call["output_tokens"] for call in tool_calls),
        )
        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = compat["metric_availability"](
                "kiro",
                cost=int(stats.get("cost_covered_executions") or 0) > 0,
                tokens=True, input_tokens=True, output_tokens=True,
                cache=False, throughput=False, context=False, timing=False,
                tool_results=False,
            )
        active = merge_execution_intervals(intervals)
        first_ts = min((turn["start"] for turn in turns if turn["start"]), default=0)
        last_ts = max((turn["end"] for turn in turns if turn["end"]), default=0)
        row = compat["summary_row"](
            source, source.get("title"), total_cost, input_tokens + output_tokens,
            len(turns), models, first_ts, last_ts, model_cost, model_tok, day_cost,
            True, {"duration_s": active, "available": bool(intervals),
                   "basis": "inferred"}, input_tokens, output_tokens, model_stats,
            list(model_daily.values()), [], [], availability,
            cache_request=cache_tracker.facts(),
        )
        row["primary_model"] = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        row["context"] = {"latest": 0, "window": None, "latest_pct": None, "estimated": True}
        row["_context_samples"] = []
        row["terminal"] = False
        row["token_estimate"] = True
        row["_tool_evidence"] = compat["summarize_tool_evidence"](tool_calls)
        return row

    def deletion_plan(self, source):
        if not isinstance(source, SessionSource):
            return DeletionPlan.deny("Kiro deletion requires a normalized session source.")
        path = os.path.abspath(source.locator.value)
        roots = [str(self.sessions_root)]
        if self.agent_storage_root is not None:
            roots.append(str(self.agent_storage_root))
        if not any(os.path.commonpath((path, root)) == root for root in roots):
            return DeletionPlan.deny("Kiro source is outside adapter-owned roots.")
        target = path
        if source.locator.kind == "jsonl" and os.path.basename(path) == "messages.jsonl":
            target = os.path.dirname(path)
        return DeletionPlan(
            DeletionDisposition.TRASH,
            "Move this Kiro session evidence to Trash.",
            (SourceLocator("directory" if os.path.isdir(target) else "jsonl", target),),
        )


class KiroRuntimeAdapterProxy:
    descriptor = KiroRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Kiro adapter")
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
