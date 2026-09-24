"""Native adapter for Codex JSONL session evidence."""

import glob
import json
import math
import os
import re
import time
from collections import OrderedDict, defaultdict
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
from token_meter.domain.usage import normalize_reported_token_count


DEFAULT_MODEL = "gpt-5.6-sol"
AUTO_REVIEW_MODEL = "codex-auto-review"
MAX_DETAIL_TURNS = 2_000
MAX_TOOL_EVENTS = 2_000
TOKEN_EVENT_CACHE_LIMIT = 2_048
UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def _file_signature(path):
    try:
        stat = os.stat(path)
        return (str(stat.st_mtime_ns), str(stat.st_size))
    except OSError:
        return ("0", "0")


def _file_identity(path):
    try:
        stat = os.stat(path)
        return (str(stat.st_dev), str(stat.st_ino))
    except OSError:
        return ("0", "0")


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _datetime(value):
    seconds = _timestamp(value) if isinstance(value, str) else value
    return datetime.fromtimestamp(seconds).astimezone() if seconds else None


def _safe_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _numeric_usage_signature(value):
    if not isinstance(value, dict):
        return ()
    fields = []
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        if isinstance(raw, float) and not math.isfinite(raw):
            continue
        fields.append((str(key), raw))
    return tuple(sorted(fields))


def _token_events(rows, default_model="unknown-model"):
    model = default_model
    events = []
    for row_index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row.get("type") == "turn_context":
            model = str(payload.get("model") or model)
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        last = _numeric_usage_signature(info.get("last_token_usage"))
        if not last:
            continue
        events.append((
            row_index,
            model,
            last,
            _numeric_usage_signature(info.get("total_token_usage")),
        ))
    return tuple(events)


def _token_events_match(left, right):
    if left[1] != right[1] or left[2] != right[2]:
        return False
    left_total = left[3]
    right_total = right[3]
    if bool(left_total) != bool(right_total):
        return False
    return not left_total or left_total == right_total


def _context_model(payload, current_model, source_model):
    """Keep Codex's internal auto-review marker out of model attribution."""
    trace_model = payload.get("model") if isinstance(payload, dict) else None
    if trace_model == AUTO_REVIEW_MODEL:
        return source_model if source_model != AUTO_REVIEW_MODEL else "unknown-model"
    return str(trace_model or current_model or "unknown-model")


def _resolved_token_events(events, source_model):
    """Normalize internal auto-review event markers for lineage matching."""
    resolved_model = str(source_model or "unknown-model")
    if resolved_model == AUTO_REVIEW_MODEL:
        resolved_model = "unknown-model"
    return tuple(
        (row_index, resolved_model if model == AUTO_REVIEW_MODEL else model, last, total)
        for row_index, model, last, total in events
    )


def _token_events_for_source(rows, default_model, source_model):
    return _resolved_token_events(_token_events(rows, default_model), source_model)


def _inherited_token_prefix(child_events, parent_events):
    count = 0
    for child, parent in zip(child_events, parent_events):
        if not _token_events_match(child, parent):
            break
        count += 1
    return count


def _corrected_rows(rows, inherited_count, default_model="unknown-model"):
    rows = tuple(rows)
    events = _token_events(rows, default_model)
    inherited_count = max(0, min(int(inherited_count), len(events)))
    first_meta = next((
        index for index, row in enumerate(rows)
        if row.get("type") == "session_meta"
    ), None)
    drop = set()
    chunk_start = 0
    for row_index, _model, _last, _total in events[:inherited_count]:
        drop.update(range(chunk_start, row_index + 1))
        chunk_end = row_index
        while chunk_end + 1 < len(rows):
            trailing = rows[chunk_end + 1]
            payload = (
                trailing.get("payload")
                if isinstance(trailing.get("payload"), dict)
                else {}
            )
            if payload.get("type") != "task_complete":
                break
            chunk_end += 1
            drop.add(chunk_end)
        chunk_start = chunk_end + 1
    if first_meta is not None:
        drop.discard(first_meta)
    previous = None
    for event in events:
        row_index = event[0]
        if row_index in drop:
            continue
        if previous and event[3] and _token_events_match(previous, event):
            drop.add(row_index)
            continue
        previous = event
    return tuple(row for index, row in enumerate(rows) if index not in drop)


def codex_usage(raw):
    raw = raw or {}
    input_total, input_reported = normalize_reported_token_count(
        raw.get("input_tokens")
    )
    cached, cache_reported = normalize_reported_token_count(
        raw.get("cached_input_tokens", 0)
    )
    output_tokens, output_reported = normalize_reported_token_count(
        raw.get("output_tokens")
    )
    total_tokens, _ = normalize_reported_token_count(raw.get("total_tokens", 0))
    input_available = input_reported and cache_reported and cached <= input_total
    reasoning_tokens, reasoning_available = normalize_reported_token_count(
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


def _compact(value, limit=90):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def _catalog(dynamic_tools):
    result = []
    for item in dynamic_tools or []:
        if not isinstance(item, dict):
            item = {"name": str(item) or "?"}
        children = item.get("tools")
        rows = children if isinstance(children, list) else [item]
        parent_namespace = item.get("namespace") or item.get("name") or "unknown"
        parent_deferred = bool(item.get("deferLoading"))
        for child in rows:
            if not isinstance(child, dict):
                child = {"name": str(child)}
            name = str(child.get("name") or "?")
            namespace = str(child.get("namespace") or parent_namespace or "unknown")
            kind = "tool"
            raw_name = name
            if name.startswith("mcp__"):
                parts = name.split("__")
                namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
                kind = "mcp"
            elif namespace.startswith("mcp__"):
                parts = namespace.split("__")
                namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
                raw_name = "mcp__{}__{}".format(namespace, name)
                kind = "mcp"
            definition = {
                "description": child.get("description") or "",
                "inputSchema": child.get("inputSchema") or child.get("input_schema") or {},
            }
            result.append({
                "namespace": namespace,
                "name": raw_name,
                "kind": kind,
                "defer_loading": bool(child.get("deferLoading", parent_deferred)),
                "definition_tokens": len(json.dumps(definition, sort_keys=True)) // 4,
            })
    return result[:240]


def _catalog_counts(catalog):
    advertised = len(catalog or ())
    deferred = sum(1 for row in catalog or () if row.get("defer_loading"))
    return advertised, max(0, advertised - deferred), deferred


class CodexRuntimeAdapter:
    """Own Codex discovery, revisions, normalized parsing, and legacy projection."""

    descriptor = RuntimeDescriptor(
        "codex",
        "Codex",
        frozenset(("sessions", "models", "tools", "quota")),
        "runtime.codex",
        "runtime-codex",
        "openai",
    )

    def __init__(self, sessions_root, index_path, project_resolver=None,
                 compatibility=None, path_cache=None,
                 max_detail_turns=MAX_DETAIL_TURNS,
                 max_tool_events=MAX_TOOL_EVENTS,
                 default_model=DEFAULT_MODEL,
                 token_event_cache_limit=TOKEN_EVENT_CACHE_LIMIT):
        self.sessions_root = Path(os.path.abspath(os.path.expanduser(str(sessions_root))))
        self.index_path = Path(os.path.abspath(os.path.expanduser(str(index_path))))
        self.project_resolver = project_resolver or (lambda value: value)
        self.compatibility = dict(compatibility or {})
        self.path_cache = path_cache
        self.max_detail_turns = max(1, int(max_detail_turns))
        self.max_tool_events = max(1, int(max_tool_events))
        self.default_model = str(default_model or DEFAULT_MODEL)
        self.token_event_cache_limit = max(1, int(token_event_cache_limit))
        self._metadata_cache = {}
        self._token_event_cache = OrderedDict()
        self._index_signature = None
        self._index_rows = {}
        self._records_by_path = {}
        self._record_by_physical_id = {}

    def _paths(self):
        pattern = str(self.sessions_root / "*" / "*" / "*" / "*.jsonl")
        if self.path_cache is not None:
            return self.path_cache.paths(pattern)
        return tuple(glob.glob(pattern))

    def _read_index(self):
        signature = _file_signature(self.index_path)
        if signature == self._index_signature:
            return dict(self._index_rows)
        rows = {}
        try:
            with open(self.index_path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and value.get("id"):
                        rows[str(value["id"])] = value
        except OSError:
            rows = {}
        self._index_signature = signature
        self._index_rows = rows
        return dict(rows)

    @staticmethod
    def _physical_id_from_path(path):
        base = os.path.basename(path).rsplit(".", 1)[0]
        match = UUID_RE.search(base)
        return match.group(1) if match else base

    @classmethod
    def _id_from_path(cls, path, metadata=None):
        if metadata and metadata.get("logical_session_id"):
            return str(metadata["logical_session_id"])
        if metadata and metadata.get("session_id"):
            return str(metadata["session_id"])
        return cls._physical_id_from_path(path)

    @staticmethod
    def _parent_thread_id(payload):
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
        spawn = (
            subagent.get("thread_spawn")
            if isinstance(subagent.get("thread_spawn"), dict)
            else {}
        )
        return payload.get("parent_thread_id") or spawn.get("parent_thread_id")

    def metadata(self, path):
        path = os.path.abspath(os.path.expanduser(str(path)))
        try:
            stat = os.stat(path)
            signature = (str(stat.st_mtime_ns), str(stat.st_size))
            identity = (int(stat.st_dev), int(stat.st_ino))
            size = int(stat.st_size)
        except OSError:
            signature = ("0", "0")
            identity = (0, 0)
            size = 0
        cached = self._metadata_cache.get(path)
        if cached and cached["signature"] == signature:
            return dict(cached["metadata"])
        if (cached and cached.get("prefix_complete")
                and cached.get("identity") == identity
                and size > int(cached.get("size") or 0)):
            cached.update({"signature": signature, "size": size})
            return dict(cached["metadata"])
        metadata = {
            "session_id": None,
            "physical_trace_id": None,
            "logical_session_id": None,
            "forked_from_id": None,
            "parent_thread_id": None,
            "lineage_parent_id": None,
            "cwd": None,
            "model": None,
            "model_provider": None,
            "tools_loaded": 0,
            "tools_eager": 0,
            "tools_deferred": 0,
            "tool_catalog": [],
            "tool_namespaces": [],
        }
        prefix_complete = False
        identity_seen = False
        try:
            with open(path, encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index > 120:
                        prefix_complete = True
                        break
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    if row.get("type") == "session_meta":
                        if not identity_seen and (
                            payload.get("id") or payload.get("session_id")
                        ):
                            physical = str(
                                payload.get("id") or self._physical_id_from_path(path)
                            )
                            logical = str(payload.get("session_id") or physical)
                            forked = str(payload.get("forked_from_id") or "") or None
                            parent = str(self._parent_thread_id(payload) or "") or None
                            metadata.update({
                                "session_id": logical,
                                "physical_trace_id": physical,
                                "logical_session_id": logical,
                                "forked_from_id": forked,
                                "parent_thread_id": parent,
                                "lineage_parent_id": forked or parent,
                            })
                            identity_seen = True
                        metadata["cwd"] = payload.get("cwd") or metadata["cwd"]
                        metadata["model_provider"] = (
                            payload.get("model_provider") or metadata["model_provider"]
                        )
                        if isinstance(payload.get("dynamic_tools"), list):
                            catalog = _catalog(payload["dynamic_tools"])
                            advertised, eager, deferred = _catalog_counts(catalog)
                            metadata.update({
                                "tool_catalog": catalog,
                                "tools_loaded": advertised,
                                "tools_eager": eager,
                                "tools_deferred": deferred,
                                "tool_namespaces": sorted({
                                    item["namespace"] for item in catalog
                                }),
                            })
                    elif row.get("type") == "turn_context":
                        metadata["cwd"] = payload.get("cwd") or metadata["cwd"]
                        metadata["model"] = payload.get("model") or metadata["model"]
        except OSError:
            pass
        if not metadata["physical_trace_id"]:
            physical = self._physical_id_from_path(path)
            metadata.update({
                "session_id": physical,
                "physical_trace_id": physical,
                "logical_session_id": physical,
            })
        self._metadata_cache[path] = {
            "signature": signature,
            "identity": identity,
            "size": size,
            "prefix_complete": prefix_complete,
            "metadata": dict(metadata),
        }
        return metadata

    def _records(self):
        index = self._read_index()
        records = []
        current = set()
        for path in self._paths():
            current.add(path)
            metadata = self.metadata(path)
            session_id = self._id_from_path(path, metadata)
            physical_id = str(
                metadata.get("physical_trace_id") or self._physical_id_from_path(path)
            )
            cwd = metadata.get("cwd") or os.path.dirname(path)
            title = (
                (index.get(session_id) or {}).get("thread_name")
                or (index.get(physical_id) or {}).get("thread_name")
            )
            records.append({
                "id": session_id,
                "physical_trace_id": physical_id,
                "logical_session_id": session_id,
                "forked_from_id": metadata.get("forked_from_id"),
                "parent_thread_id": metadata.get("parent_thread_id"),
                "lineage_parent_id": metadata.get("lineage_parent_id"),
                "path": path,
                "project": self.project_resolver(cwd),
                "mtime": os.path.getmtime(path) if os.path.exists(path) else 0.0,
                "title": _compact(title) or None,
                "model": metadata.get("model"),
                "observed_model": metadata.get("model"),
                "model_provider": metadata.get("model_provider") or "openai",
                "model_resolution": "observed",
                "model_resolution_parent_id": None,
                "model_resolution_reason": "",
                "tools_loaded": metadata.get("tools_loaded") or 0,
                "tools_eager": metadata.get("tools_eager") or 0,
                "tools_deferred": metadata.get("tools_deferred") or 0,
                "tool_catalog": metadata.get("tool_catalog") or [],
                "tool_namespaces": metadata.get("tool_namespaces") or [],
            })
        for stale in set(self._metadata_cache) - current:
            self._metadata_cache.pop(stale, None)
        for stale in set(self._token_event_cache) - current:
            self._token_event_cache.pop(stale, None)
        records_by_physical_id = defaultdict(list)
        for record in records:
            records_by_physical_id[record["physical_trace_id"]].append(record)
        self._record_by_physical_id = {
            physical_id: matches[0]
            for physical_id, matches in records_by_physical_id.items()
            if len(matches) == 1
        }
        self._records_by_path = {record["path"]: record for record in records}
        for record in records:
            if record.get("observed_model") == AUTO_REVIEW_MODEL:
                if self._has_auto_review_identity_cycle(record):
                    record["model"] = "unknown-model"
                    record["model_resolution"] = "unresolved"
                    record["model_resolution_reason"] = "cyclic"
                else:
                    model, parent_id = self._resolved_auto_review_model(record)
                    record["model"] = model
                    if parent_id:
                        record["model_resolution"] = "verified_parent"
                        record["model_resolution_parent_id"] = parent_id
                        parent = self._record_by_physical_id.get(parent_id) or {}
                        record["model_provider"] = (
                            parent.get("model_provider") or record["model_provider"]
                        )
                    else:
                        record["model_resolution"] = "unresolved"
                        record["model_resolution_reason"] = "parent_unavailable"
        for record in records:
            parent_id = record.get("lineage_parent_id")
            parent = self._record_by_physical_id.get(parent_id)
            if (
                not parent
                or parent.get("path") == record.get("path")
                or self._has_lineage_cycle(record)
            ):
                record["lineage_revision"] = (
                    "unresolved", str(parent_id or ""),
                )
            else:
                record["lineage_revision"] = (
                    "resolved",
                    str(parent_id),
                    *_file_identity(parent["path"]),
                )
        return tuple(records)

    def discover(self, context):
        del context
        index_signature = _file_signature(self.index_path)
        return tuple(SessionSource(
            runtime_id="codex",
            client_id="codex",
            session_id=record["id"],
            display_label="Codex",
            project=record["project"],
            locator=SourceLocator("jsonl", record["path"]),
            activity_mtime=record["mtime"],
            revision=SourceRevision((
                *_file_signature(record["path"]),
                *index_signature,
                str(record["title"] or ""),
                *record["lineage_revision"],
            )),
            model_ref=(
                ModelRef(record["model_provider"], record["model"])
                if record.get("model") else None
            ),
            account_provider_id="openai",
        ) for record in self._records())

    def discover_legacy(self, context):
        del context
        return tuple({
            "provider": "codex",
            "label": "Codex",
            "id": record["id"],
            "physical_trace_id": record["physical_trace_id"],
            "logical_session_id": record["logical_session_id"],
            "forked_from_id": record["forked_from_id"],
            "parent_thread_id": record["parent_thread_id"],
            "lineage_parent_id": record["lineage_parent_id"],
            "lineage_revision": record["lineage_revision"],
            "session": os.path.basename(record["path"]),
            "path": record["path"],
            "project": record["project"],
            "mtime": record["mtime"],
            "title": record["title"],
            "model": record["model"],
            "observed_model": record["observed_model"],
            "model_provider": record["model_provider"],
            "model_resolution": record["model_resolution"],
            "model_resolution_parent_id": record["model_resolution_parent_id"],
            "model_resolution_reason": record["model_resolution_reason"],
            "tools_loaded": record["tools_loaded"],
            "tools_eager": record["tools_eager"],
            "tools_deferred": record["tools_deferred"],
            "tool_catalog": record["tool_catalog"],
            "tool_namespaces": record["tool_namespaces"],
        } for record in self._records())

    def current_revision(self, source):
        path = source.locator.value if isinstance(source, SessionSource) else source.get("path", "")
        path = os.path.abspath(os.path.expanduser(str(path)))
        record = next(
            (candidate for candidate in self._records() if candidate["path"] == path),
            None,
        )
        if record:
            title = record.get("title") or ""
            lineage_revision = record.get("lineage_revision") or ()
        else:
            session_id = (
                source.session_id
                if isinstance(source, SessionSource)
                else source.get("id", "")
            )
            title = (self._read_index().get(str(session_id)) or {}).get("thread_name") or ""
            lineage_revision = ()
        return SourceRevision((
            *_file_signature(path),
            *_file_signature(self.index_path),
            str(title),
            *lineage_revision,
        ))

    @staticmethod
    def _usage_evidence(value, available):
        return (EvidenceValue(value, EvidenceBasis.MEASURED)
                if available else EvidenceValue.unavailable())

    def load_rows(self, path):
        rows = []
        corrupt = 0
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
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
            return (), 0, False
        return tuple(rows), corrupt, True

    def _token_events_for_path(self, path, rows=None):
        path = os.path.abspath(os.path.expanduser(str(path)))
        signature = _file_signature(path)
        cached = self._token_event_cache.get(path)
        if cached and cached["signature"] == signature:
            self._token_event_cache.move_to_end(path)
            return cached["events"]
        if rows is None:
            rows, _corrupt, available = self.load_rows(path)
            if not available:
                return ()
        events = _token_events(rows)
        self._token_event_cache[path] = {
            "signature": signature,
            "events": events,
        }
        self._token_event_cache.move_to_end(path)
        while len(self._token_event_cache) > self.token_event_cache_limit:
            self._token_event_cache.popitem(last=False)
        return events

    def _record_for_path(self, path):
        path = os.path.abspath(os.path.expanduser(str(path)))
        record = self._records_by_path.get(path)
        if record is None:
            self._records()
            record = self._records_by_path.get(path)
        return record

    def _has_lineage_cycle(self, record):
        seen = set()
        current = record
        while current:
            physical_id = current.get("physical_trace_id")
            if physical_id in seen:
                return True
            seen.add(physical_id)
            parent_id = current.get("lineage_parent_id")
            if not parent_id:
                return False
            current = self._record_by_physical_id.get(parent_id)
        return False

    def _has_auto_review_identity_cycle(self, record):
        """Fail closed when either direct-parent or fork lineage is cyclic."""
        visiting = set()
        visited = set()

        def visit(current):
            physical_id = str((current or {}).get("physical_trace_id") or "")
            if not physical_id:
                return True
            if physical_id in visiting:
                return True
            if physical_id in visited:
                return False
            visiting.add(physical_id)
            try:
                for field in ("parent_thread_id", "forked_from_id"):
                    parent_id = current.get(field)
                    if not parent_id:
                        continue
                    parent = self._record_by_physical_id.get(parent_id)
                    if parent and visit(parent):
                        return True
            finally:
                visiting.discard(physical_id)
            visited.add(physical_id)
            return False

        return visit(record)

    def _resolved_auto_review_model(self, record):
        """Resolve only an unambiguous direct parent that reports an actual model."""
        parent_id = record.get("parent_thread_id")
        parent = self._record_by_physical_id.get(parent_id)
        if not parent or parent.get("physical_trace_id") == record.get("physical_trace_id"):
            return "unknown-model", None
        if parent.get("observed_model") == AUTO_REVIEW_MODEL:
            return "unknown-model", None
        if str(parent.get("model_provider") or "openai").strip().lower() != "openai":
            return "unknown-model", None
        parent_model = str(parent.get("model") or "")
        if parent_model in ("", "unknown", "unknown-model", AUTO_REVIEW_MODEL):
            return "unknown-model", None
        return parent_model, str(parent_id)

    @staticmethod
    def _verified_inherited_prefix(source, path):
        if not isinstance(source, dict):
            return None
        if source.get("observed_model") != AUTO_REVIEW_MODEL:
            return None
        value = source.get("_verified_inherited_prefix")
        revision = source.get("_verified_inherited_prefix_revision")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        if not isinstance(revision, (list, tuple)) or len(revision) != 2:
            return None
        if tuple(str(item) for item in revision) != _file_signature(path):
            return None
        return value

    def auto_review_evidence(self, source):
        """Return bounded, content-free proof for a live direct auto-review match."""
        if not isinstance(source, dict):
            return None
        path = os.path.abspath(os.path.expanduser(str(source.get("path") or "")))
        record = self._record_for_path(path)
        if (
            not record
            or record.get("observed_model") != AUTO_REVIEW_MODEL
            or record.get("model_resolution") != "verified_parent"
            or self._has_auto_review_identity_cycle(record)
        ):
            return None
        parent_id = record.get("model_resolution_parent_id")
        parent = self._record_by_physical_id.get(parent_id)
        if not parent or parent.get("path") == path:
            return None
        rows, _corrupt, available = self.load_rows(path)
        if not available:
            return None
        inherited_prefix = 0
        accounting_parent = self._record_by_physical_id.get(
            record.get("lineage_parent_id")
        )
        if accounting_parent and not self._has_lineage_cycle(record):
            child_events = _token_events_for_source(
                rows, self.default_model, record.get("model"),
            )
            parent_events = _resolved_token_events(
                self._token_events_for_path(accounting_parent["path"]),
                accounting_parent.get("model"),
            )
            inherited_prefix = _inherited_token_prefix(child_events, parent_events)
        return {
            "model": str(record.get("model") or ""),
            "model_provider": str(record.get("model_provider") or "openai"),
            "revision": list(_file_signature(path)),
            "parent_id": str(parent_id),
            "inherited_prefix": inherited_prefix,
        }

    def _accounting_rows(self, source, rows):
        path = (
            source.locator.value
            if isinstance(source, SessionSource)
            else source.get("path", "")
        )
        path = os.path.abspath(os.path.expanduser(str(path)))
        record = self._record_for_path(path)
        if not record or self._has_lineage_cycle(record):
            return _corrected_rows(rows, 0)
        parent_id = record.get("lineage_parent_id")
        parent = self._record_by_physical_id.get(parent_id)
        if not parent or parent.get("path") == path:
            inherited_prefix = self._verified_inherited_prefix(source, path)
            return _corrected_rows(rows, inherited_prefix or 0)
        child_events = _token_events_for_source(
            rows, self.default_model, record.get("model"),
        )
        parent_events = _resolved_token_events(
            self._token_events_for_path(parent["path"]),
            parent.get("model"),
        )
        inherited_count = _inherited_token_prefix(child_events, parent_events)
        return _corrected_rows(rows, inherited_count)

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != "codex":
            raise ValueError("source belongs to another runtime")
        rows, corrupt, available = self.load_rows(source.locator.value)
        if not available:
            return self._empty(source, detail, ("source_unavailable",))
        rows = self._accounting_rows(source, rows)

        counts = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        usage_seen = False
        input_complete = True
        output_complete = True
        tools = []
        turns = []
        task_started = None
        active_seconds = 0.0
        active_available = False
        ttft_seconds = 0.0
        ttft_available = False
        started_at = ended_at = None
        for row in rows:
            row_time = _datetime(row.get("timestamp"))
            if row_time:
                started_at = row_time if started_at is None else min(started_at, row_time)
                ended_at = row_time if ended_at is None else max(ended_at, row_time)
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            ptype = payload.get("type")
            if ptype == "task_started":
                task_started = row_time
            elif ptype in ("function_call", "custom_tool_call", "web_search_call",
                           "tool_search_call") and len(tools) < self.max_tool_events:
                name = payload.get("name") or (
                    "web.search" if ptype == "web_search_call" else ptype.replace("_call", "")
                )
                tools.append(ToolEvent(str(name), "tool"))
            elif ptype == "token_count":
                raw = ((payload.get("info") or {}).get("last_token_usage") or {})
                if not raw:
                    continue
                usage_seen = True
                usage = codex_usage(raw)
                input_complete = input_complete and usage["input_available"]
                output_complete = output_complete and usage["output_available"]
                counts["input"] += usage["input_tokens"]
                counts["output"] += usage["output_tokens"]
                counts["cache_read"] += usage["cache_read_input_tokens"]
                counts["cache_write"] += usage["cache_creation_input_tokens"]
                if len(turns) < self.max_detail_turns:
                    turns.append(TurnSummary(
                        len(turns) + 1, task_started, row_time,
                        self._usage_evidence(
                            usage["output_tokens"], usage["output_available"]
                        ),
                    ))
            elif ptype == "task_complete":
                duration_ms = payload.get("duration_ms")
                ttft_ms = payload.get("time_to_first_token_ms")
                if not isinstance(duration_ms, bool) and isinstance(duration_ms, (int, float)):
                    duration = float(duration_ms)
                    if math.isfinite(duration) and duration >= 0:
                        active_seconds += duration / 1000.0
                        active_available = True
                if not isinstance(ttft_ms, bool) and isinstance(ttft_ms, (int, float)):
                    duration = float(ttft_ms)
                    if math.isfinite(duration) and duration >= 0:
                        ttft_seconds += duration / 1000.0
                        ttft_available = True
                task_started = None

        warning_codes = []
        if corrupt:
            warning_codes.append("corrupt_rows")
        input_available = usage_seen and input_complete
        output_available = usage_seen and output_complete
        if not input_available or not output_available:
            warning_codes.append("usage_unavailable")
        if len(turns) >= self.max_detail_turns or len(tools) >= self.max_tool_events:
            warning_codes.append("history_truncated")
        warnings = tuple(ParseWarning(code, {
            "corrupt_rows": "Malformed Codex rows were ignored.",
            "usage_unavailable": "Codex token evidence was unavailable.",
            "history_truncated": "Detailed Codex history was bounded.",
        }[code]) for code in warning_codes)
        return NormalizedSession(
            source=source,
            started_at=started_at,
            ended_at=ended_at,
            usage=UsageEvidence(
                self._usage_evidence(counts["input"], input_available),
                self._usage_evidence(counts["output"], output_available),
                self._usage_evidence(counts["cache_read"], input_available),
                self._usage_evidence(counts["cache_write"], input_available),
                EvidenceValue.unavailable(),
            ),
            timing=TimingEvidence(
                self._usage_evidence(active_seconds, active_available),
                EvidenceValue.unavailable(),
                self._usage_evidence(ttft_seconds, ttft_available),
            ),
            tools=tuple(tools),
            turns=tuple(turns) if detail is DetailLevel.FULL else (),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=warnings,
            detail=detail,
        )

    def _empty(self, source, detail, warning_codes):
        return NormalizedSession(
            source, None, None, UsageEvidence.unavailable(), TimingEvidence.unavailable(),
            (), (), None, self.descriptor.capabilities,
            tuple(ParseWarning(code, "Codex evidence was unavailable.")
                  for code in warning_codes), detail,
        )

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        CHARS_PER_TOKEN = compat["chars_per_token"]
        analysis_block = compat["analysis_block"]
        build_insights = compat["build_insights"]
        build_state = compat["build_state"]
        catalog_counts = compat["catalog_counts"]
        codex_approval_policy_label = compat["codex_approval_policy_label"]
        codex_fallback_user_text = compat["codex_fallback_user_text"]
        codex_live_performance_summary = compat["codex_live_performance_summary"]
        codex_performance_samples = compat["codex_performance_samples"]
        codex_wait_samples = compat["codex_wait_samples"]
        compact_text = compat["compact_text"]
        cost_of = compat["cost_of"]
        execution_timing = compat["execution_timing"]
        metric_availability = compat["metric_availability"]
        home_shorten = compat["home_shorten"]
        new_codex_pending = compat["new_codex_pending"]
        normalize_dynamic_tools = compat["normalize_dynamic_tools"]
        observable_output_chars = compat["observable_output_chars"]
        parse_iso = compat["parse_iso"]
        performance_summary = compat["performance_summary"]
        price_for = compat["price_for"]
        skill_names_from_value = compat["skill_names_from_value"]
        text_from_content = compat["text_from_content"]
        tool_identity = compat["tool_identity"]
        tool_result_is_error = compat["tool_result_is_error"]
        tool_summary = compat["tool_summary"]
        trace_event = compat["trace_event"]
        usage_tokens = compat["usage_tokens"]
        user_prompt_preview = compat["user_prompt_preview"]
        path = source["path"]
        objs, _corrupt, _available = self.load_rows(path)
        if not objs:
            return None
        objs = self._accounting_rows(source, objs)
    
        model = source.get("model") or "unknown-model"
        meta_cwd = source.get("project")
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace = [], [], []
        pending = new_codex_pending()
        call_map = {}
        first_ts = last_ts = None
        biggest = None
        completed = 0
        approx_cost = True
        price_complete = True
        input_complete = True
        output_complete = True
        task_start_ts = None
        context_window = None
        tools_loaded = int(source.get("tools_loaded") or 0)
        tools_eager = int(source.get("tools_eager") or 0)
        tools_deferred = int(source.get("tools_deferred") or 0)
        tool_catalog = list(source.get("tool_catalog") or [])
        tool_namespaces = list(source.get("tool_namespaces") or [])
    
        for obj in objs:
            ts = parse_iso(obj.get("timestamp", ""))
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            ptype = payload.get("type")
            otype = obj.get("type")
    
            if otype == "session_meta":
                meta_cwd = home_shorten(payload.get("cwd") or meta_cwd)
                dynamic_tools = payload.get("dynamic_tools")
                if isinstance(dynamic_tools, list):
                    tool_catalog = normalize_dynamic_tools(dynamic_tools)
                    counts = catalog_counts(tool_catalog)
                    tools_loaded = counts["advertised"]
                    tools_eager = counts["eager"]
                    tools_deferred = counts["deferred"]
                    tool_namespaces = sorted(set(t["namespace"] for t in tool_catalog))
                continue
            if otype == "turn_context":
                model = _context_model(payload, model, source.get("model"))
                meta_cwd = home_shorten(payload.get("cwd") or meta_cwd)
                detail = " · ".join(x for x in [
                    model,
                    payload.get("effort"),
                    codex_approval_policy_label(payload.get("approval_policy")),
                ] if x)
                pending["trace"].append(trace_event(
                    ts, "context", "Run context", detail, severity="neutral",
                    model=model, tools_loaded=tools_loaded or None,
                    tool_namespaces=tool_namespaces[:6],
                    native_type="turn_context",
                ))
                continue
    
            if ptype == "task_started":
                task_start_ts = ts or task_start_ts
                pending["start_ts"] = pending["start_ts"] or ts
                context_window = payload.get("model_context_window") or context_window
                pending["context_window"] = context_window
                pending["trace"].append(trace_event(ts, "start", "Execution started",
                                                    payload.get("collaboration_mode_kind", ""), severity="start",
                                                    model=model, context_window=context_window,
                                                    tools_loaded=tools_loaded or None, trace_id=payload.get("trace_id"),
                                                    turn_id=payload.get("turn_id"),
                                                    native_type="event_msg",
                                                    native_subtype="task_started"))
                continue
    
            if ptype == "user_message":
                txt = compact_text(payload.get("message") or "", 100)
                if txt:
                    pending["user_inputs"].append(compact_text(payload.get("message") or "", 220))
                    pending["trace"].append(trace_event(ts, "user", "User message", txt,
                                                        severity="start", model=model,
                                                        native_type="event_msg",
                                                        native_subtype="user_message"))
                continue
    
            if ptype == "agent_message":
                txt = compact_text(payload.get("message") or "", 120)
                if txt:
                    pending["trace"].append(trace_event(ts, "message", "Agent update", txt,
                                                        severity="neutral", model=model,
                                                        phase=payload.get("phase"),
                                                        native_type="event_msg",
                                                        native_subtype="agent_message"))
                continue
    
            if ptype == "context_compacted":
                pending["trace"].append(trace_event(ts, "context", "Context compacted", "",
                                                    severity="warn", model=model,
                                                    native_type="event_msg"))
                continue
    
            if ptype == "thread_goal_updated":
                goal = payload.get("goal") or {}
                txt = compact_text(goal.get("objective") if isinstance(goal, dict) else str(goal), 120)
                pending["trace"].append(trace_event(ts, "goal", "Goal updated", txt,
                                                    severity="neutral", model=model,
                                                    native_type="event_msg"))
                continue
    
            if ptype == "reasoning":
                pending["has_reasoning"] = True
                summary = payload.get("summary")
                pending["trace"].append(trace_event(ts, "reasoning", "Reasoning",
                                                    compact_text(str(summary or "encrypted reasoning"), 90),
                                                    severity="reasoning", model=model,
                                                    native_type="event_msg",
                                                    native_subtype="reasoning"))
                continue
    
            if ptype == "message":
                role = payload.get("role")
                content = payload.get("content")
                if role == "assistant":
                    txt = compact_text(text_from_content(content), 84)
                    pending["trace"].append(trace_event(
                        ts, "message", "Assistant message", txt, model=model,
                        native_type="response_item", native_subtype="agent_message",
                    ))
                elif role == "user":
                    user_text = codex_fallback_user_text(payload)
                    txt = compact_text(user_text or "", 84)
                    if txt:
                        pending["fallback_user_inputs"].append(compact_text(user_text, 220))
                        pending["trace"].append(trace_event(
                            ts, "user", "User message", txt, severity="start", model=model,
                            native_type="response_item", native_subtype="user_message",
                        ))
                continue
    
            if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
                name = payload.get("name") or ("web.search" if ptype == "web_search_call" else ptype.replace("_call", ""))
                call_id = payload.get("call_id") or payload.get("id") or f"call-{len(call_map) + 1}"
                ident = tool_identity(name)
                arguments = payload.get("arguments") or payload.get("input")
                tool = {
                    **ident,
                    "id": payload.get("id"),
                    "call_id": call_id,
                    "args_chars": len(str(arguments or "")),
                    "output_chars": 0,
                    "output_tokens": 0,
                    "error": False,
                    "skills": skill_names_from_value(arguments, name),
                }
                pending["calls"][call_id] = tool
                call_map[call_id] = tool
                pending["trace"].append(trace_event(ts, "tool_call", ident["display"], ident["namespace"],
                                                    tool=name, severity="tool", model=model,
                                                    args_chars=tool["args_chars"], tool_kind=ident["kind"],
                                                    native_type="response_item",
                                                    native_subtype=(ptype if ptype in (
                                                        "function_call", "custom_tool_call", "web_search_call",
                                                    ) else "tool_call")))
                continue
    
            if ptype in ("function_call_output", "custom_tool_call_output", "web_search_end", "tool_search_output", "patch_apply_end"):
                call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
                tool = pending["calls"].get(call_id) or call_map.get(call_id)
                if tool is None:
                    ident = tool_identity(payload.get("name") or ptype)
                    tool = {**ident, "id": payload.get("id"), "call_id": call_id,
                            "args_chars": 0, "output_chars": 0, "output_tokens": 0, "error": False,
                            "skills": []}
                    pending["calls"][call_id] = tool
                    call_map[call_id] = tool
                output = payload.get("output") if "output" in payload else payload
                out_chars = observable_output_chars(output)
                tool["output_chars"] += out_chars
                tool["output_tokens"] = tool["output_chars"] // CHARS_PER_TOKEN
                tool["error"] = bool(tool.get("error") or tool_result_is_error(output, payload.get("status") == "failed"))
                pending["trace"].append(trace_event(ts, "tool_result", tool["display"],
                                                    f"~{tool['output_tokens']:,} returned tokens",
                                                    tool=tool["name"], tokens=tool["output_tokens"],
                                                    severity="warn" if tool.get("error") else "retrieval", model=model,
                                                    output_chars=tool["output_chars"],
                                                    retrieval_tokens=tool["output_tokens"], error=tool.get("error"),
                                                    native_type="response_item",
                                                    native_subtype=(ptype if ptype in (
                                                        "function_call_output", "custom_tool_call_output", "web_search_end",
                                                    ) else "tool_result")))
                continue
    
            if ptype == "task_complete":
                completed += 1
                duration = payload.get("duration_ms") or payload.get("time_to_first_token_ms")
                if executions:
                    executions[-1]["duration_ms"] = duration
                    trace.append(trace_event(ts, "complete", "Execution complete",
                                             f"{duration}ms" if duration else "",
                                             executions[-1]["idx"], severity="good",
                                             model=model, duration_ms=duration,
                                             turn_id=payload.get("turn_id"),
                                             native_type="event_msg",
                                             native_subtype="task_complete"))
                else:
                    pending["trace"].append(trace_event(ts, "complete", "Execution complete", "",
                                                        severity="good", model=model,
                                                        duration_ms=duration,
                                                        turn_id=payload.get("turn_id"),
                                                        native_type="event_msg",
                                                        native_subtype="task_complete"))
                continue
    
            if ptype == "token_count":
                info = payload.get("info") or {}
                raw = (info.get("last_token_usage") or {})
                if not raw:
                    continue
                context_window = info.get("model_context_window") or context_window or pending.get("context_window")
                usage = codex_usage(raw)
                input_complete = input_complete and usage["input_available"]
                output_complete = output_complete and usage["output_available"]
                idx = len(series) + 1
                _, missing_price = price_for(model, "codex", at=ts)
                cost_available = (
                    not missing_price
                    and usage["input_available"]
                    and usage["output_available"]
                )
                c = cost_of(usage, model, "codex", at=ts) if cost_available else {
                    "input": 0.0, "cache_write": 0.0,
                    "cache_read": 0.0, "output": 0.0,
                }
                approx_cost = approx_cost or not cost_available
                price_complete = price_complete and cost_available
                tc = sum(c.values())
                for key in cost:
                    cost[key] += c[key]
                in_tok = usage["input_tokens"] + usage["cache_read_input_tokens"]
                out_tok = usage["output_tokens"]
                reasoning = min(out_tok, usage.get("reasoning_output_tokens", 0))
                total = usage_tokens(usage)
                context_pct = (in_tok / context_window) if context_window else None
                fresh_input_tokens = usage["input_tokens"]
                cache_read_tokens = usage["cache_read_input_tokens"]
                cache_write_tokens = usage["cache_creation_input_tokens"]
                cache_tokens = cache_read_tokens + cache_write_tokens
                tot["input"] += usage["input_tokens"]
                tot["cache_read"] += usage["cache_read_input_tokens"]
                tot["cache_write"] += usage["cache_creation_input_tokens"]
                tot["output"] += out_tok
                model_tok[model] += total
                model_cost[model] += tc
                first_ts = ts if first_ts is None else min(first_ts, ts or first_ts)
                last_ts = ts if ts else last_ts
    
                tools = [dict(t) for t in pending["calls"].values()]
                user_input = user_prompt_preview(
                    pending.get("user_inputs") or pending.get("fallback_user_inputs") or []
                )
                observed_tools_loaded = tools_loaded or len(set(t.get("name") for t in call_map.values() if t.get("name")))
                for ev in pending["trace"]:
                    ev["execution"] = idx if ev.get("execution") is None else ev["execution"]
                    trace.append(ev)
                trace.append(trace_event(
                    ts, "usage", "Token count",
                    f"{out_tok:,} out / {in_tok:,} in",
                    idx, tokens=total, cost=tc, severity="usage",
                    model=model, input_tokens=in_tok, output_tokens=out_tok,
                    cache_tokens=cache_tokens,
                    context_tokens=in_tok, context_window=context_window,
                    context_pct=context_pct, tool_count=len(tools),
                    fresh_input_tokens=fresh_input_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    reasoning_tokens=reasoning, tools_loaded=observed_tools_loaded or None,
                    native_type="event_msg", native_subtype="token_count",
                ))
    
                series.append({
                    "i": idx,
                    "in": in_tok,
                    "out": out_tok,
                    "cost": round(tc, 4),
                    "fresh_input": fresh_input_tokens,
                    "cache": cache_tokens,
                    "cache_read": cache_read_tokens,
                    "cache_write": cache_write_tokens,
                    "think": bool(reasoning or pending["has_reasoning"]),
                    "tools": len(tools),
                    "side": False,
                    "reasoning": reasoning,
                    "context_pct": context_pct,
                    "user_message": user_input,
                    "user_input": user_input,
                })
                executions.append({
                    "id": f"{source['id']}:{idx}",
                    "idx": idx,
                    "ts": ts or 0,
                    "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
                    "model": model,
                    "tokens": {"input": in_tok, "output": out_tok, "reasoning": reasoning,
                               "retrieval": sum(t["output_tokens"] for t in tools),
                               "fresh_input": fresh_input_tokens, "cache": cache_tokens,
                               "cache_read": cache_read_tokens, "cache_write": cache_write_tokens,
                               "total": total},
                    "cost": round(tc, 6),
                    "cost_breakdown": {k: round(v, 6) for k, v in c.items()},
                    "tools": tools,
                    "tool_count": len(tools),
                    "reasoning_tokens": reasoning,
                    "context_tokens": in_tok,
                    "context_window": context_window,
                    "context_pct": context_pct,
                    "duration_ms": None,
                    "summary": f"Execution {idx}: {out_tok:,} out / {in_tok:,} in",
                    "user_message": user_input,
                    "user_input": user_input,
                })
                if biggest is None or tc > biggest["cost"]:
                    biggest = {"cost": tc, "idx": idx}
                pending = new_codex_pending()
                task_start_ts = None
    
        tool_data = tool_summary(executions)
        total_tokens = sum(tot.values())
        total_cost = sum(cost.values())
        if not first_ts:
            first_ts = min((parse_iso(o.get("timestamp", "")) for o in objs if parse_iso(o.get("timestamp", ""))), default=None)
        if not last_ts:
            last_ts = max((parse_iso(o.get("timestamp", "")) for o in objs if parse_iso(o.get("timestamp", ""))), default=None)
        elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
        idle = (time.time() - last_ts) if last_ts else 1e9
        reasoning_tokens = sum(e["reasoning_tokens"] for e in executions)
        output_tokens = max(0, tot["output"] - reasoning_tokens)
        coord_execs = [e for e in executions if any(t["namespace"] in ("orchestration", "workspace-agents") or "agent" in t["name"] for t in e["tools"])]
        coord_cost = sum(e["cost"] for e in coord_execs)
        semantic = {
            "reasoning": reasoning_tokens,
            "output": output_tokens,
            "retrieval": tool_data["total_output_tokens"],
            "coordination": sum(e["tokens"]["output"] for e in coord_execs),
        }
        primary_model = max(model_tok, key=model_tok.get) if model_tok else model
        think_cost = sum(
            float((execution.get("cost_breakdown") or {}).get("output") or 0)
            * int(execution.get("reasoning_tokens") or 0)
            / max(1, int((execution.get("tokens") or {}).get("output") or 0))
            for execution in executions
        )
        analyses = analysis_block(tot, total_cost, reasoning_tokens, sum(1 for e in executions if e["reasoning_tokens"]),
                                  think_cost, model_tok, model_cost, tool_data, coord_cost,
                                  len(coord_execs), completed or len(executions))
        cache_in = tot["cache_read"] + tot["cache_write"]
        cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
        insights = build_insights(tot, cost, total_cost, cache_ratio, biggest, len(series), analyses,
                                  "codex", primary_model, True, executions)
    
        source = dict(source)
        source["project"] = meta_cwd or source.get("project")
        source["tools_loaded"] = tools_loaded
        source["tools_eager"] = tools_eager
        source["tools_deferred"] = tools_deferred
        source["tool_catalog"] = tool_catalog
        source["tool_namespaces"] = tool_namespaces
        wait_samples = codex_wait_samples(objs, source.get("model"))
        state = build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                            analyses, insights, first_ts, last_ts, idle, biggest, len(coord_execs), True,
                            primary_model, "estimated with public OpenAI API rates", execution_timing("codex", objs),
                            wait_samples, availability=metric_availability(
                                "codex", cost=price_complete,
                                tokens=input_complete and output_complete,
                                input_tokens=input_complete,
                                output_tokens=output_complete,
                                cache=input_complete,
                            ))
        state["throughput"] = performance_summary(codex_performance_samples(objs, source.get("model")), tot["output"])
        state["live_throughput"] = codex_live_performance_summary(objs)
        return state

    def summarize_legacy(self, source, objs=None):
        compat = self._require_compatibility()
        if objs is None:
            objs, _corrupt, _available = self.load_rows(source.get("path") or "")
        objs = self._accounting_rows(source, tuple(objs or ()))
        CURRENT_SESSION_CONTEXT_SAMPLES = compat["context_sample_limit"]
        add_model_daily = compat["add_model_daily"]
        add_model_summary = compat["add_model_summary"]
        analyze_language_signals = compat["analyze_language_signals"]
        attach_language_signals = compat["attach_language_signals"]
        codex_live_performance_summary = compat["codex_live_performance_summary"]
        codex_performance_samples = compat["codex_performance_samples"]
        codex_tool_call_evidence = compat["codex_tool_call_evidence"]
        codex_wait_samples = compat["codex_wait_samples"]
        compact_text = compat["compact_text"]
        cost_of = compat["cost_of"]
        execution_timing = compat["execution_timing"]
        metric_availability = compat["metric_availability"]
        parse_iso = compat["parse_iso"]
        price_for = compat["price_for"]
        summarize_tool_evidence = compat["summarize_tool_evidence"]
        summary_row = compat["summary_row"]
        usage_tokens = compat["usage_tokens"]
        model = source.get("model") or "unknown-model"
        reasoning_effort = ""
        cost = 0.0
        tokens = 0
        turns = 0
        first_ts = last_ts = None
        models = set()
        model_cost, model_tok = defaultdict(float), defaultdict(int)
        model_stats = {}
        model_daily = {}
        input_tokens = output_tokens = 0
        day_cost = defaultdict(float)
        approx = True
        price_complete = True
        context_window = 0
        latest_context = 0
        context_samples = []
        terminal = False
        input_complete = True
        output_complete = True
    
        for obj in objs:
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if obj.get("type") == "turn_context":
                model = _context_model(payload, model, source.get("model"))
                effort = payload.get("effort")
                if isinstance(effort, (str, int, float)) and str(effort).strip():
                    reasoning_effort = compact_text(str(effort).strip().lower(), 20)
            if payload.get("type") == "task_started":
                context_window = int(payload.get("model_context_window") or context_window or 0)
                terminal = False
            elif payload.get("type") == "task_complete":
                terminal = True
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            raw = (info.get("last_token_usage") or {})
            if not raw:
                continue
            context_window = int(info.get("model_context_window") or context_window or 0)
            usage = codex_usage(raw)
            input_complete = input_complete and usage["input_available"]
            output_complete = output_complete and usage["output_available"]
            latest_context = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
            context_samples.append(latest_context)
            ts = parse_iso(obj.get("timestamp", ""))
            _, missing_price = price_for(model, "codex", at=ts)
            cost_available = (
                not missing_price
                and usage["input_available"]
                and usage["output_available"]
            )
            c = sum(cost_of(usage, model, "codex", at=ts).values()) \
                if cost_available else 0.0
            price_complete = price_complete and cost_available
            toks = usage_tokens(usage)
            turns += 1
            cost += c
            tokens += toks
            models.add(model)
            model_cost[model] += c
            model_tok[model] += toks
            input_count, output_count = add_model_summary(
                model_stats, model, usage, c,
                cost_available=cost_available,
            )
            add_model_daily(
                model_daily, model, usage, c, ts,
                cost_available=cost_available,
            )
            input_tokens += input_count
            output_tokens += output_count
            if ts:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                day_cost[day] += c

        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = metric_availability(
                "codex",
                cost=int(stats.get("cost_covered_executions") or 0) > 0,
                tokens=(stats.get("input_evidence") is True
                        or stats.get("output_evidence") is True),
                input_tokens=stats.get("input_evidence") is True,
                output_tokens=stats.get("output_evidence") is True,
                cache=stats.get("input_evidence") is True,
            )

        title = source.get("title")
        if not title:
            for obj in objs:
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if payload.get("type") == "user_message":
                    title = compact_text(payload.get("message") or "", 60)
                    break
        performance = codex_performance_samples(objs, source.get("model"))
        wait_samples = codex_wait_samples(objs, source.get("model"))
        row = summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                          execution_timing("codex", objs), input_tokens, output_tokens, model_stats,
                          list(model_daily.values()), performance, wait_samples,
                          availability=metric_availability(
                              "codex", cost=price_complete,
                              tokens=input_complete and output_complete,
                              input_tokens=input_complete,
                              output_tokens=output_complete,
                              cache=input_complete,
                          ))
        row["primary_model"] = model
        row["reasoning_effort"] = reasoning_effort
        row["context"] = {
            "latest": latest_context,
            "window": context_window or None,
            "latest_pct": (latest_context / context_window) if context_window else None,
            "estimated": False,
        }
        row["_context_samples"] = context_samples[-CURRENT_SESSION_CONTEXT_SAMPLES:]
        row["terminal"] = terminal
        row["live_throughput"] = codex_live_performance_summary(objs)
        signal_rollups, signal_events = analyze_language_signals(
            "codex", objs, default_model=source.get("model") or "unknown-model"
        )
        attach_language_signals(row, signal_rollups, signal_events)
        row["_tool_evidence"] = summarize_tool_evidence(codex_tool_call_evidence(objs), source.get("tool_catalog") or [])
        return row

    def deletion_plan(self, source):
        if isinstance(source, SessionSource) and source.locator.kind == "jsonl":
            return DeletionPlan(
                DeletionDisposition.TRASH,
                "Move this Codex session trace to Trash.",
                (source.locator,),
            )
        return DeletionPlan.deny("Codex deletion requires one owned session trace.")


class CodexRuntimeAdapterProxy:
    descriptor = CodexRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Codex adapter")
        return adapter

    def discover(self, context):
        return self._adapter().discover(context)

    def discover_legacy(self, context):
        return self._adapter().discover_legacy(context)

    def current_revision(self, source):
        return self._adapter().current_revision(source)

    def load(self, source, detail):
        return self._adapter().load(source, detail)

    def summarize_legacy(self, source, objs=None):
        return self._adapter().summarize_legacy(source, objs)

    def auto_review_evidence(self, source):
        return self._adapter().auto_review_evidence(source)

    def deletion_plan(self, source):
        return self._adapter().deletion_plan(source)
