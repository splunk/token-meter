"""Native read-only adapter for OpenCode's SQLite evidence store."""

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

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
    TurnSummary,
    UsageEvidence,
)
from token_meter.domain.timing import merge_execution_intervals
from token_meter.domain.usage import (
    distribute_reported_cost_counts,
    normalize_reported_token_count,
)


DETAIL_MESSAGE_LIMIT = 200
SUMMARY_MESSAGE_LIMIT = 500
# Bound for walking session.parent_id chains. Real nesting is shallow; the bound
# keeps a malformed or cyclic chain from costing more than a few lookups.
MAX_SESSION_ANCESTRY = 32


def _compact_text(value, limit=90):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def _json_object(value, default=None):
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return default
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return decoded if isinstance(decoded, dict) else default


def _milliseconds(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _seconds(value):
    return _milliseconds(value) / 1000.0


def _datetime_from_ms(value):
    milliseconds = _milliseconds(value)
    return datetime.fromtimestamp(milliseconds / 1000.0).astimezone() if milliseconds else None


def _measured_number(value, cast=int, *, combine=0):
    values = value if isinstance(value, tuple) else (value,)
    if any(item is None or isinstance(item, bool) for item in values):
        return EvidenceValue.unavailable()
    try:
        result = sum(cast(item) for item in values) + combine
    except (TypeError, ValueError, OverflowError):
        return EvidenceValue.unavailable()
    if isinstance(result, float) and (not math.isfinite(result) or result < 0):
        return EvidenceValue.unavailable()
    if result < 0:
        return EvidenceValue.unavailable()
    return EvidenceValue(result, EvidenceBasis.MEASURED)


def decode_json(value, default=None):
    return _json_object(value, default)


def int_value(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return default


def usage_counts(data):
    tokens = data.get("tokens") if isinstance(data, dict) else {}
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    input_tokens, input_available = normalize_reported_token_count(
        tokens.get("input")
    )
    output_tokens, output_available = normalize_reported_token_count(
        tokens.get("output")
    )
    reasoning_tokens, reasoning_available = normalize_reported_token_count(
        tokens.get("reasoning")
    )
    cache_read_tokens, _ = normalize_reported_token_count(cache.get("read"))
    cache_write_tokens, _ = normalize_reported_token_count(cache.get("write"))
    return {
        "input_tokens": input_tokens,
        "input_available": input_available,
        "output_tokens": output_tokens,
        "output_available": output_available,
        "reasoning_tokens": reasoning_tokens,
        "reasoning_available": reasoning_available,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_write_tokens,
    }


def context_tokens(usage):
    return sum(int((usage or {}).get(key) or 0) for key in (
        "input_tokens", "output_tokens", "reasoning_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    ))


def reported_cost(data):
    value = data.get("cost") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, False
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0, False
    return (value, True) if math.isfinite(value) and value >= 0 else (0.0, False)


def distribute_cost(message_cost, usage):
    usage = usage or {}
    return distribute_reported_cost_counts(
        message_cost,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        reasoning_tokens=usage.get("reasoning_tokens", 0),
    )


def display_cost(breakdown):
    return {
        "input": float((breakdown or {}).get("input") or 0),
        "cache_write": float((breakdown or {}).get("cache_write") or 0),
        "cache_read": float((breakdown or {}).get("cache_read") or 0),
        "output": float((breakdown or {}).get("output") or 0)
                  + float((breakdown or {}).get("reasoning") or 0),
    }


def safe_agent_role(value, limit=64):
    """Keep a short provider-reported agent name, never arbitrary prose."""
    if not isinstance(value, str):
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    text = " ".join(value.split())
    lowered = text.lower()
    if not text or len(text) > limit:
        return ""
    if (
        text.startswith(("/", "\\", "~", "./", "../"))
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or "://" in text
        or lowered.startswith(("bearer ", "sk-", "-----begin "))
        or any(marker in lowered for marker in (
            "api_key=", "api-key=", "secret=", "token=",
        ))
    ):
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", text):
        return text
    return ""


def opaque_agent_id(session_id):
    """Return a stable public agent ID without exposing the private session id."""
    physical = str(session_id or "")
    if not physical:
        return ""
    digest = hashlib.sha256(
        b"token-meter:opencode-agent:v1\0" + physical.encode("utf-8", "replace")
    ).hexdigest()
    return "opencode-agent-" + digest


def session_lineage(session_id, ancestry, max_depth=MAX_SESSION_ANCESTRY):
    """Resolve one session's family from `{id: (parent_id, directory, archived)}`.

    Returns the resolved root id (empty for a root session or when the chain
    cannot reach a true root), the nesting depth, the root ancestor's directory,
    and whether the session or any ancestor is archived. A missing parent, a
    cycle, or a chain longer than `max_depth` leaves the family unresolved.
    """
    own = ancestry.get(session_id) or ("", "", False)
    parent, directory, archived = own
    depth = 0
    seen = {session_id}
    top_directory = directory
    resolved = not parent
    current = parent
    while current:
        depth += 1
        if current in seen or depth > max_depth or current not in ancestry:
            break
        seen.add(current)
        next_parent, next_directory, next_archived = ancestry[current]
        if next_archived:
            archived = True
            break
        top_directory = next_directory or top_directory
        if not next_parent:
            resolved = True
            break
        current = next_parent
    root_id = current if parent and resolved and not archived else ""
    return {
        "root_id": root_id,
        "depth": depth,
        "directory": top_directory,
        "archived": bool(archived),
    }


class OpenCodeRuntimeAdapter:
    """Own OpenCode discovery, revision, safe loading, and legacy projection."""

    descriptor = RuntimeDescriptor(
        "opencode",
        "OpenCode",
        frozenset(("sessions", "models", "tools")),
        "runtime.opencode",
        "runtime-opencode",
        None,
    )

    def __init__(self, database_path, models_path=None, project_resolver=None,
                 compatibility=None, detail_message_limit=DETAIL_MESSAGE_LIMIT,
                 context_sample_limit=32):
        self.database_path = Path(os.path.abspath(os.path.expanduser(str(database_path))))
        self.models_path = (
            Path(os.path.abspath(os.path.expanduser(str(models_path))))
            if models_path else None
        )
        self._models_signature = None
        self._models = {}
        self._source_signature = None
        self._source_records = ()
        self._project_resolver = project_resolver or (lambda value: value)
        self.compatibility = dict(compatibility or {})
        self.detail_message_limit = max(1, int(detail_message_limit))
        self.context_sample_limit = max(1, int(context_sample_limit))

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    @staticmethod
    def _file_signature(path):
        try:
            stat = os.stat(path)
            return (str(stat.st_mtime_ns), str(stat.st_size))
        except OSError:
            return ("0", "0")

    def connection(self):
        """Open the live WAL-aware database with writes disabled."""
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 1000")
        return connection

    def _database_signature(self):
        return (
            self._file_signature(self.database_path),
            self._file_signature(str(self.database_path) + "-wal"),
        )

    def _load_models(self):
        if self.models_path is None:
            return {}
        signature = self._file_signature(self.models_path)
        if signature == self._models_signature:
            return self._models
        try:
            raw = json.loads(self.models_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raw = {}
        catalog = {}
        if isinstance(raw, dict):
            for provider_key, provider_info in raw.items():
                if not isinstance(provider_info, dict):
                    continue
                provider_id = str(provider_info.get("id") or provider_key or "")
                for model_id, model_info in (provider_info.get("models") or {}).items():
                    limit = (model_info or {}).get("limit") or {}
                    try:
                        context = int(limit.get("context") or 0)
                    except (TypeError, ValueError):
                        context = 0
                    if context:
                        catalog[(provider_id, str(model_id))] = context
        self._models_signature = signature
        self._models = catalog
        return catalog

    def model_context_window(self, model_id, provider_id=""):
        catalog = self._load_models()
        key = (str(provider_id or ""), str(model_id or ""))
        if key[0] and key in catalog:
            return catalog[key]
        matches = {window for (provider, model), window in catalog.items() if model == key[1]}
        return next(iter(matches)) if len(matches) == 1 else None

    def _query_source_records(self):
        try:
            with contextlib.closing(self.connection()) as connection:
                # Lightweight ancestry for every session, archived or not, so a
                # descendant of an archived session can be excluded with it.
                ancestry_rows = connection.execute(
                    "SELECT id, parent_id, COALESCE(directory,''), "
                    "time_archived IS NOT NULL FROM session"
                ).fetchall()
                rows = connection.execute(
                    "SELECT s.id, s.directory, COALESCE(s.title,''), "
                    "COALESCE(s.agent,''), COALESCE(s.model,''), "
                    "s.parent_id, "
                    "s.time_created, s.time_updated, "
                    "MAX(COALESCE(s.time_updated,0), "
                    "COALESCE((SELECT MAX(m.time_updated) FROM message m "
                    "WHERE m.session_id=s.id),0), "
                    "COALESCE((SELECT MAX(p.time_updated) FROM part p "
                    "WHERE p.session_id=s.id),0)) "
                    "FROM session s "
                    "WHERE s.time_archived IS NULL "
                    "ORDER BY s.time_updated DESC"
                ).fetchall()
        except (OSError, sqlite3.Error):
            return None
        ancestry = {
            str(sid): (str(parent or ""), str(directory or ""), bool(archived))
            for sid, parent, directory, archived in ancestry_rows
        }
        lineages = {}
        for row in rows:
            lineages[str(row[0])] = session_lineage(str(row[0]), ancestry)
        # A session archived as part of an archived family is excluded together
        # with every descendant, matching OpenCode's own archive semantics.
        kept = {sid for sid, lineage in lineages.items() if not lineage["archived"]}
        parents_with_children = {
            ancestry[sid][0] for sid in kept if ancestry.get(sid, ("",))[0]
        }
        records = []
        for (
            sid, directory, title, agent, raw_model, parent_id,
            created, updated, revision,
        ) in rows:
            sid = str(sid)
            if sid not in kept:
                continue
            lineage = lineages[sid]
            model = _json_object(raw_model, {}) or {}
            title = str(title or "")
            if title.startswith("New session") or not title.strip():
                title = ""
            parent = str(parent_id or "")
            records.append({
                "id": sid,
                "directory": str(directory or ""),
                "title": _compact_text(title) or None,
                "agent": str(agent or ""),
                "model": str(model.get("id") or "unknown"),
                "model_provider": str(model.get("providerID") or ""),
                "parent_id": parent,
                "root_id": lineage["root_id"],
                "depth": lineage["depth"],
                "project_directory": lineage["directory"] or str(directory or ""),
                "is_child": bool(parent),
                "has_children": sid in parents_with_children,
                "created": _seconds(created),
                "updated": _seconds(updated),
                "revision": _seconds(revision),
            })
        return tuple(records)

    def _records(self):
        signature = self._database_signature()
        if signature == self._source_signature:
            return self._source_records
        records = self._query_source_records()
        if records is None:
            return self._source_records if self._source_signature is not None else ()
        self._source_signature = signature
        self._source_records = records
        return records

    def discover(self, context):
        sources = []
        for record in self._records():
            model_ref = ModelRef(
                record["model_provider"] or "unknown-model-provider",
                record["model"],
            )
            sources.append(SessionSource(
                runtime_id=self.descriptor.runtime_id,
                client_id=self.descriptor.runtime_id,
                session_id=record["id"],
                display_label=self.descriptor.label,
                project=self._project_for_record(record),
                locator=SourceLocator("sqlite-session", record["id"]),
                activity_mtime=record["updated"],
                revision=SourceRevision((str(record["revision"]), str(record["title"] or ""))),
                model_ref=model_ref,
            ))
        return tuple(sources)

    def _project_for_record(self, record):
        """Resolve a session's project, scoping children to their root ancestor."""
        directory = record.get("project_directory") or record.get("directory")
        return (
            self._project_resolver(directory)
            or ("" if record.get("is_child") else record.get("agent"))
            or "No project"
        )

    def discover_legacy(self, context):
        return tuple({
            "provider": self.descriptor.runtime_id,
            "client": self.descriptor.runtime_id,
            "label": self.descriptor.label,
            "runtime": self.descriptor.label,
            "id": record["id"],
            "session": record["id"],
            "path": "{}:{}".format(self.descriptor.runtime_id, record["id"]),
            "project": self._project_for_record(record),
            "mtime": record["updated"],
            "signature_mtime": record["revision"],
            "title": record["title"],
            "model": record["model"],
            "model_provider": record["model_provider"],
            "agent": record["agent"],
            "agent_parent_id": record["parent_id"],
            "agent_root_id": record["root_id"],
            "agent_role": safe_agent_role(record["agent"]),
            "agent_depth": int(record["depth"]),
            "agent_has_children": bool(record["has_children"]),
            "tools_loaded": 0,
        } for record in self._records())

    def current_revision(self, source):
        session_id = source.session_id if isinstance(source, SessionSource) else str(source.get("id") or "")
        try:
            with contextlib.closing(self.connection()) as connection:
                row = connection.execute(
                    "SELECT MAX(COALESCE(s.time_updated,0), "
                    "COALESCE((SELECT MAX(m.time_updated) FROM message m WHERE m.session_id=s.id),0), "
                    "COALESCE((SELECT MAX(p.time_updated) FROM part p WHERE p.session_id=s.id),0)) "
                    "FROM session s WHERE s.id=?",
                    (session_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            return SourceRevision(("unavailable",))
        revision = _seconds(row[0]) if row and row[0] is not None else 0.0
        return SourceRevision((str(revision),))

    def _empty_session(self, source, detail, warning_code):
        return NormalizedSession(
            source=source,
            started_at=None,
            ended_at=None,
            usage=UsageEvidence.unavailable(),
            timing=TimingEvidence.unavailable(),
            tools=(),
            turns=(),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=(ParseWarning(warning_code, "Runtime evidence was unavailable."),),
            detail=detail,
        )

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != self.descriptor.runtime_id:
            raise ValueError("source belongs to another runtime")
        try:
            with contextlib.closing(self.connection()) as connection:
                row = connection.execute(
                    "SELECT tokens_input, tokens_output, tokens_reasoning, "
                    "tokens_cache_read, tokens_cache_write, cost, "
                    "time_created, time_updated FROM session WHERE id=?",
                    (source.session_id,),
                ).fetchone()
                if not row:
                    return self._empty_session(source, detail, "session_missing")
                message_rows = connection.execute(
                    "SELECT json_extract(data,'$.role'), "
                    "json_extract(data,'$.time.created'), "
                    "json_extract(data,'$.time.completed'), "
                    "json_extract(data,'$.tokens.output'), "
                    "json_extract(data,'$.tokens.reasoning') "
                    "FROM message WHERE session_id=? "
                    "ORDER BY time_created ASC, id ASC LIMIT ?",
                    (source.session_id, DETAIL_MESSAGE_LIMIT + 1),
                ).fetchall()
                tool_rows = connection.execute(
                    "SELECT DISTINCT json_extract(data,'$.tool') "
                    "FROM part WHERE session_id=? AND json_extract(data,'$.type')='tool'",
                    (source.session_id,),
                ).fetchall()
        except (OSError, sqlite3.Error):
            return self._empty_session(source, detail, "source_unavailable")

        s_input, s_output, reasoning, cache_read, cache_write, cost, created, updated = row
        intervals = []
        turns = []
        for role, started_ms, ended_ms, output, turn_reasoning in message_rows[:DETAIL_MESSAGE_LIMIT]:
            if role != "assistant":
                continue
            started_at = _datetime_from_ms(started_ms)
            ended_at = _datetime_from_ms(ended_ms)
            if started_ms and ended_ms and _milliseconds(ended_ms) >= _milliseconds(started_ms):
                intervals.append((_seconds(started_ms), _seconds(ended_ms)))
            turns.append(TurnSummary(
                index=len(turns) + 1,
                started_at=started_at,
                ended_at=ended_at,
                output_tokens=_measured_number((output, turn_reasoning)),
            ))
        active_seconds = merge_execution_intervals(intervals)
        timing = TimingEvidence(
            active_seconds=(
                EvidenceValue(active_seconds, EvidenceBasis.MEASURED)
                if intervals else EvidenceValue.unavailable()
            ),
            wait_seconds=EvidenceValue.unavailable(),
            ttft_seconds=EvidenceValue.unavailable(),
        )
        warnings = ()
        if len(message_rows) > DETAIL_MESSAGE_LIMIT:
            warnings = (ParseWarning(
                "history_truncated",
                "Detailed history was limited to {} messages.".format(DETAIL_MESSAGE_LIMIT),
            ),)
        tools = tuple(
            ToolEvent(str(name), "tool") for (name,) in tool_rows if name
        )
        return NormalizedSession(
            source=source,
            started_at=_datetime_from_ms(created),
            ended_at=_datetime_from_ms(updated),
            usage=UsageEvidence(
                input_tokens=_measured_number(s_input),
                output_tokens=_measured_number((s_output, reasoning)),
                cache_read_tokens=_measured_number(cache_read),
                cache_write_tokens=_measured_number(cache_write),
                cost_usd=_measured_number(cost, float),
            ),
            timing=timing,
            tools=tools,
            turns=tuple(turns) if detail is DetailLevel.FULL else (),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=warnings,
            detail=detail,
        )

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        CHARS_PER_TOKEN = compat["chars_per_token"]
        analysis_block = compat["analysis_block"]
        argument_fingerprint = compat["argument_fingerprint"]
        build_insights = compat["build_insights"]
        build_state = compat["build_state"]
        compact_text = compat["compact_text"]
        duration_label = compat["duration_label"]
        metric_availability = compat["metric_availability"]
        metric_available = compat["metric_available"]
        model_context_window = compat["model_context_window"]
        observable_output_chars = compat["observable_output_chars"]
        performance_summary = compat["performance_summary"]
        skill_names_from_value = compat["skill_names_from_value"]
        tool_identity = compat["tool_identity"]
        tool_summary = compat["tool_summary"]
        trace_event = compat["trace_event"]
        user_prompt_preview = compat["user_prompt_preview"]
        """Build usage from OpenCode's authoritative per-message tokens and cost.
    
        The session row provides cumulative aggregates that are always accurate.
        Per-message queries are limited to the most recent turns so live watcher
        recompute stays fast even while the owning process writes the WAL DB.
        """
        sid = str(source.get("id") or "")
        try:
            with contextlib.closing(self.connection()) as conn:
                session_row = conn.execute(
                    "SELECT tokens_input, tokens_output, tokens_reasoning, "
                    "tokens_cache_read, tokens_cache_write, cost, time_created, time_updated, model "
                    "FROM session WHERE id = ?", (sid,)
                ).fetchone()
                if not session_row:
                    return None
                s_input, s_output, s_reasoning, s_cr, s_cw, s_cost, created, updated, session_model_raw = session_row
                message_rows = conn.execute(
                    "SELECT id, data FROM message WHERE session_id=? "
                    "ORDER BY time_created DESC, id DESC LIMIT ?",
                    (sid, self.detail_message_limit + 1)).fetchall()
                if not message_rows:
                    message_rows = []
                history_truncated = len(message_rows) > self.detail_message_limit
                message_rows = message_rows[:self.detail_message_limit]
                mid_list = [row[0] for row in message_rows]
                if mid_list:
                    placeholders = ",".join("?" for _ in mid_list)
                    part_rows = conn.execute(
                        f"SELECT message_id, data FROM part WHERE message_id IN ({placeholders}) "
                        "ORDER BY time_created ASC",
                        mid_list).fetchall()
                else:
                    part_rows = []
        except (OSError, sqlite3.Error):
            return None
    
        message_rows.reverse()
    
        session_model = decode_json(session_model_raw, {}) or {}
        model_id = str(session_model.get("id") or source.get("model") or "")
        model_provider = str(session_model.get("providerID") or source.get("model_provider") or "")
        context_window = model_context_window(model_id, model_provider)
    
        parts_by_message = defaultdict(list)
        text_parts_by_message = defaultdict(list)
        for message_id, data_raw in part_rows:
            data = decode_json(data_raw, None)
            if not isinstance(data, dict):
                continue
            part_type = data.get("type")
            if part_type in ("tool", "reasoning"):
                parts_by_message[str(message_id)].append(data)
            elif part_type == "text" and isinstance(data.get("text"), str):
                text_parts_by_message[str(message_id)].append(data["text"])
    
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        series, executions, trace = [], [], []
        wait_samples, performance_samples, active_intervals = [], [], []
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        think_tokens = 0
        think_turns = 0
        think_cost = 0.0
        routine_out = 0
        completed = 0
        first_ts = last_ts = 0.0
        biggest = None
        duration_ms = 0
        cost_observed = (
            not isinstance(s_cost, bool) and isinstance(s_cost, (int, float))
            and math.isfinite(float(s_cost)) and float(s_cost) >= 0
        )
    
        last_user_ts = 0.0
        pending_user_prompts = []
    
        for message_id, data_raw in message_rows:
            data = decode_json(data_raw, None)
            if not isinstance(data, dict):
                continue
            role = data.get("role")
            if role == "user":
                time_obj = data.get("time")
                created_ms = int_value(time_obj.get("created")) if isinstance(time_obj, dict) else 0
                if created_ms:
                    last_user_ts = created_ms / 1000.0
                user_texts = text_parts_by_message.get(str(message_id), [])
                if not user_texts and isinstance(data.get("text"), str):
                    user_texts = [data["text"]]
                user_input = user_prompt_preview(user_texts)
                if user_input:
                    pending_user_prompts.append({"text": user_input, "ts": last_user_ts})
                continue
            if role != "assistant":
                continue
            usage = usage_counts(data)
            input_evidence = usage["input_available"]
            output_evidence = usage["output_available"]
            token_evidence = input_evidence or output_evidence
            in_tok = usage["input_tokens"]
            cache_write = usage["cache_creation_input_tokens"]
            cache_read = usage["cache_read_input_tokens"]
            out_tok = usage["output_tokens"]
            reasoning_tok = usage["reasoning_tokens"]
            total_tok = context_tokens(usage)
            msg_cost, msg_cost_available = reported_cost(data)
            cost_observed = cost_observed or msg_cost_available
            if total_tok <= 0 and not msg_cost_available and not token_evidence:
                continue
            idx = len(series) + 1
            model = str(data.get("modelID") or source.get("model") or "unknown")
            provider_id = str(data.get("providerID") or model_provider)
            message_context_window = model_context_window(model, provider_id) or context_window
            user_input = user_prompt_preview([
                prompt.get("text") or "" for prompt in pending_user_prompts
            ])
            time_obj = data.get("time")
            created = int_value(time_obj.get("created")) if isinstance(time_obj, dict) else 0
            completed_ms = int_value(time_obj.get("completed")) if isinstance(time_obj, dict) else 0
            ts = (created / 1000.0) if created else (last_ts or 0.0)
            end_ts = (completed_ms / 1000.0) if completed_ms else ts
            duration_ms = (completed_ms - created) if (created and completed_ms) else 0
            cache_tok = cache_write + cache_read
            if last_user_ts > 0 and end_ts > last_user_ts:
                wait_s = end_ts - last_user_ts
                if wait_s > 0:
                    tool_parts_count = sum(1 for p in parts_by_message.get(str(message_id), [])
                                           if p.get("type") == "tool")
                    wait_samples.append({
                        "provider": "opencode", "model": model,
                        "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
                        "ts": end_ts, "start_ts": last_user_ts, "duration_s": wait_s,
                        "tool_calls": tool_parts_count, "output_tokens": out_tok,
                        "context_tokens": total_tok, "model_calls": 1,
                        "timing_basis": "message timestamps",
                        "ttft_s": 0.0, "attempts": 1, "failed_attempts": 0, "retries": 0,
                    })
                last_user_ts = 0.0
            c = distribute_cost(msg_cost, usage)
            display_c = display_cost(c)
            tc = msg_cost
            fresh_tok = in_tok
            model_tok[model] += total_tok
            model_cost[model] += tc
            tot["input"] += in_tok
            tot["cache_write"] += cache_write
            tot["cache_read"] += cache_read
            tot["output"] += out_tok
            for key in cost:
                cost[key] += display_c[key]
            think_now = reasoning_tok > 0
            if think_now:
                think_tokens += reasoning_tok
                think_turns += 1
                think_cost += c["reasoning"]
            routine_out += out_tok
            if data.get("finish") in ("turn_complete", "stop"):
                completed += 1
    
            tools = []
            part_reasoning_ms = 0.0
            for part in parts_by_message.get(str(message_id), []):
                ptype = part.get("type")
                if ptype == "tool":
                    name = str(part.get("tool") or "?")
                    ident = tool_identity(name)
                    call_id = str(part.get("callID") or "")
                    state = part.get("state") or {}
                    status = str(state.get("status") or "").lower()
                    error = bool(state.get("error") not in (None, "", False)) or status in ("error", "failed")
                    args = state.get("input")
                    result_value = state.get("output")
                    result_present = result_value not in (None, "", [], {})
                    output_chars = observable_output_chars(result_value) if result_present else 0
                    tools.append({
                        **ident, "id": call_id, "call_id": call_id,
                        "args_chars": len(json.dumps(args or "")) if args is not None else 0,
                        "args_fingerprint": argument_fingerprint(args),
                        "output_chars": output_chars,
                        "output_tokens": output_chars // CHARS_PER_TOKEN,
                        "result_available": result_present,
                        "error": error,
                        "skills": skill_names_from_value(args, name),
                    })
                elif ptype == "reasoning":
                    rtime = part.get("time") or {}
                    rstart = int_value(rtime.get("start"))
                    rend = int_value(rtime.get("end"))
                    if rend and rstart:
                        part_reasoning_ms += max(0, rend - rstart)
    
            if first_ts:
                first_ts = min(first_ts, ts) if ts else first_ts
            else:
                first_ts = ts
            if end_ts:
                last_ts = max(last_ts, end_ts)
    
            execution_cost_available = msg_cost_available
            execution_availability = metric_availability(
                "opencode", cost=execution_cost_available, tokens=token_evidence,
                input_tokens=input_evidence, output_tokens=output_evidence,
                throughput=bool(duration_ms and out_tok),
                context=bool(message_context_window), timing=bool(duration_ms), tool_results=True,
            )
            for prompt in pending_user_prompts:
                trace.append(trace_event(
                    prompt.get("ts") or ts, "user", "User message",
                    compact_text(prompt.get("text") or "", 84), idx,
                    severity="start", model=model,
                    native_type="message", native_subtype="user_message",
                ))
            for tool in tools:
                trace.append(trace_event(
                    ts, "tool_call", tool["display"], tool["namespace"], idx,
                    tool=tool["name"], severity="tool", model=model,
                    args_chars=tool["args_chars"], tool_kind=tool["kind"],
                    native_type="part", native_subtype="tool_call",
                ))
                if tool["result_available"] or tool["error"]:
                    trace.append(trace_event(
                        ts, "tool_result", tool["display"],
                        f"~{tool['output_tokens']:,} returned tokens" if tool["result_available"] else "Tool error",
                        idx, tool=tool["name"], tokens=tool["output_tokens"],
                        severity="warn" if tool["error"] else "retrieval", model=model,
                        output_chars=tool["output_chars"],
                        retrieval_tokens=tool["output_tokens"], error=tool["error"],
                        native_type="part", native_subtype="tool_result",
                    ))
            if part_reasoning_ms or think_now:
                trace.append(trace_event(
                    ts, "reasoning", "Reasoning",
                    duration_label(part_reasoning_ms / 1000.0) if part_reasoning_ms
                    else f"{reasoning_tok:,} reasoning tokens",
                    idx, tokens=reasoning_tok, severity="reasoning", model=model,
                    duration_ms=part_reasoning_ms or None, output_tokens=reasoning_tok,
                    native_type="part", native_subtype="reasoning",
                ))
            trace.append(trace_event(
                ts, "message", "Assistant turn",
                f"{out_tok + reasoning_tok:,} out / {in_tok + cache_tok:,} in", idx,
                tokens=total_tok, cost=tc, severity="usage", model=model,
                input_tokens=in_tok + cache_tok, output_tokens=out_tok,
                cache_tokens=cache_tok, context_tokens=total_tok,
                fresh_input_tokens=fresh_tok, cache_read_tokens=cache_read,
                cache_write_tokens=cache_write, tool_count=len(tools),
                reasoning_tokens=reasoning_tok,
                native_type="message", native_subtype="agent_message",
            ))
    
            active_s = duration_ms / 1000.0 if duration_ms else 0.0
            if duration_ms:
                active_intervals.append((ts, end_ts))
            if duration_ms and out_tok:
                performance_samples.append({
                    "provider": "opencode", "model": model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
                    "ts": end_ts or ts, "input_tokens": in_tok + cache_tok,
                    "output_tokens": out_tok + reasoning_tok, "peak_input_tokens": total_tok,
                    "uncached_input_tokens": in_tok, "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write, "model_calls": 1,
                    "duration_s": active_s, "generation_s": active_s, "ttft_s": 0.0,
                    "tool_calls": len(tools), "timing_basis": "open-code response time",
                })
    
            ctx_tokens = total_tok
            ctx_pct = ctx_tokens / message_context_window if message_context_window else None
            series.append({
                "i": idx, "in": ctx_tokens, "out": out_tok,
                "cost": round(tc, 4), "fresh_input": fresh_tok,
                "cache": cache_tok, "cache_read": cache_read, "cache_write": cache_write,
                "think": think_now, "tools": len(tools), "side": False,
                "reasoning": reasoning_tok,
                "context_pct": ctx_pct,
                "user_message": user_input, "user_input": user_input,
            })
            executions.append({
                "id": message_id, "idx": idx, "ts": ts or 0,
                "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
                "model": model,
                "tokens": {"input": in_tok + cache_tok, "output": out_tok,
                           "reasoning": reasoning_tok,
                           "retrieval": sum(t["output_tokens"] for t in tools),
                           "fresh_input": fresh_tok, "cache": cache_tok,
                           "cache_read": cache_read, "cache_write": cache_write,
                           "total": total_tok},
                "cost": round(tc, 6),
                "cost_breakdown": {k: round(v, 6) for k, v in c.items()},
                "tools": tools, "tool_count": len(tools),
                "reasoning_tokens": reasoning_tok,
                "context_tokens": ctx_tokens, "context_window": message_context_window,
                "context_pct": ctx_pct, "duration_ms": duration_ms or None,
                "summary": f"Execution {idx}: {len(tools)} tools · ${tc:.3f} OpenCode-reported"
                           if execution_cost_available else f"Execution {idx}: {len(tools)} tools · cost unavailable",
                "user_message": user_input, "user_input": user_input,
                "availability": execution_availability,
            })
            pending_user_prompts = []
            if biggest is None or tc > biggest["cost"]:
                biggest = {"cost": tc, "idx": idx}
    
        # Session row carries the authoritative cumulative totals. Override
        # the per-message accumulation (which covers only the last 200 turns).
        tot = {
            "input": int(s_input or 0), "cache_write": int(s_cw or 0),
            "cache_read": int(s_cr or 0), "output": int(s_output or 0),
        }
        s_cost_val = float(s_cost or 0.0)
        total_cost = s_cost_val
        total_tokens = sum(tot.values()) + int(s_reasoning or 0)
        full_cost_breakdown = distribute_cost(total_cost, {
            "input_tokens": tot["input"], "cache_creation_input_tokens": tot["cache_write"],
            "cache_read_input_tokens": tot["cache_read"], "output_tokens": tot["output"],
            "reasoning_tokens": int(s_reasoning or 0),
        })
        cost = display_cost(full_cost_breakdown)
        tool_data = tool_summary(executions)
        model_names = [execution.get("model") or "unknown" for execution in executions]
        primary_model = max(set(model_names), key=model_names.count) if model_names else source.get("model") or "unknown"
        reasoning_total = sum(int(execution.get("reasoning_tokens") or 0) for execution in executions)
        semantic = {
            "reasoning": think_tokens,
            "output": max(0, routine_out),
            "retrieval": tool_data["total_output_tokens"],
            "coordination": 0,
        }
        analyses = analysis_block(
            tot, total_cost, think_tokens, think_turns, think_cost,
            model_tok, model_cost, tool_data, 0.0, 0, completed,
        )
        idle = (time.time() - last_ts) if last_ts else 1e9
        cache_in = tot["cache_read"] + tot["cache_write"]
        cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
        insights = build_insights(tot, cost, total_cost, cache_ratio, biggest, len(series),
                                  analyses, "opencode", primary_model, False, executions)
        source = dict(source)
        context_latest = int(executions[-1].get("context_tokens") or 0) if executions else 0
        context_window = (
            int(executions[-1].get("context_window") or 0) or context_window
            if executions else context_window
        )
        source.update({
            "token_estimate": False,
            "estimate_basis": "",
            "pricing_variant": "",
            "context_window": context_window,
            "context_latest": context_latest,
        })
        input_available = bool(executions and all(metric_available(e, "input_tokens") for e in executions))
        output_available = bool(executions and all(metric_available(e, "output_tokens") for e in executions))
        cost_available = cost_observed
        active_s = merge_execution_intervals(active_intervals)
        throughput = performance_summary(performance_samples, tot["output"] + int(s_reasoning or 0))
        availability = metric_availability(
            "opencode", cost=cost_available, tokens=bool(input_available or output_available),
            input_tokens=input_available, output_tokens=output_available,
            throughput=bool(throughput.get("available")),
            context=bool(context_window and executions), timing=bool(active_s),
            tool_results=any(tool.get("result_available") for e in executions for tool in e.get("tools") or []),
        )
        state = build_state(
            source, tot, cost, total_tokens, total_cost, series, executions, trace,
            semantic, analyses, insights, first_ts, last_ts, idle, biggest, 0, False,
            primary_model, "OpenCode-reported total; category split estimated from token weights",
            {"duration_s": active_s, "available": bool(active_s),
            "reported_executions": sum(bool(e.get("duration_ms")) for e in executions),
            "observed_executions": 0, "execution_count": len(executions),
            "basis": "OpenCode message timestamps" if active_s else "unavailable"},
            wait_samples, availability=availability,
        )
        state["throughput"] = throughput
        state["execution_history_truncated"] = history_truncated
        state["execution_history_limit"] = self.detail_message_limit
        return state
    
    
    def summarize_legacy(self, source, connection=None):
        compat = self._require_compatibility()
        metric_availability = compat["metric_availability"]
        model_context_window = compat["model_context_window"]
        summarize_tool_evidence = compat["summarize_tool_evidence"]
        summary_row = compat["summary_row"]
        tool_identity = compat["tool_identity"]
        """Build exact OpenCode day/model attribution without loading part text."""
        sid = str(source.get("id") or "")
        conn = connection if isinstance(connection, sqlite3.Connection) else None
        owns_connection = conn is None
        try:
            if conn is None:
                conn = self.connection()
            row = conn.execute(
                "SELECT tokens_input, tokens_output, tokens_reasoning, "
                "tokens_cache_read, tokens_cache_write, cost, model, "
                "COALESCE(time_created,0), COALESCE(time_updated,0) "
                "FROM session WHERE id = ?", (sid,)
            ).fetchone()
            te = []
            message_rows = []
            if row:
                te = conn.execute(
                    "SELECT DISTINCT json_extract(data,'$.tool') as name "
                    "FROM part WHERE session_id=? AND json_extract(data,'$.type')='tool'",
                    (sid,)
                ).fetchall()
                message_rows = conn.execute(
                    "SELECT data, time_created FROM message WHERE session_id=? "
                    "AND json_extract(data,'$.role') IN ('user','assistant') "
                    "ORDER BY time_created DESC, id DESC LIMIT ?",
                    (sid, SUMMARY_MESSAGE_LIMIT)
                ).fetchall()
                message_rows.reverse()
        except (OSError, sqlite3.Error):
            row = None
            te = []
            message_rows = []
        finally:
            if owns_connection and conn is not None:
                conn.close()
        if not row:
            availability = metric_availability("opencode")
            return summary_row(
                source, source.get("title"), 0.0, 0, 0, set(), None, None,
                {}, {}, {}, False, availability=availability,
            )
        s_input, s_output, s_reasoning, s_cr, s_cw, s_cost, s_model_raw, created, updated = row
        s_cost_val = float(s_cost or 0.0)
        inp = int(s_input or 0)
        out = int(s_output or 0)
        reasoning = int(s_reasoning or 0)
        cre = int(s_cr or 0)
        cw = int(s_cw or 0)
        session_model = decode_json(s_model_raw, {}) or {}
        model = str(session_model.get("id") or source.get("model") or "unknown")
        model_provider = str(session_model.get("providerID") or source.get("model_provider") or "")
        last_ts = (updated / 1000.0) if updated else None
        first_ts = (created / 1000.0) if created else None
        context_window = model_context_window(model, model_provider)
    
        # Lightweight tool evidence: only distinct tool names (not full args/chars).
        tool_evidence = []
        for (name,) in (te or []):
            if name:
                evidence = tool_identity(str(name))
                evidence["output_tokens"] = 0
                evidence["error"] = False
                evidence["ts"] = (updated / 1000.0) if updated else 0
                evidence["calls"] = 1
                evidence["args_fingerprint"] = ""
                evidence["skills"] = []
                tool_evidence.append(evidence)
    
        tokens = inp + out + reasoning + cre + cw
        models = set()
        model_cost = defaultdict(float)
        model_tok = defaultdict(int)
        model_stats = {}
        model_daily = {}
        day_cost = defaultdict(float)
        performance_samples = []
        wait_samples = []
        context_samples = []
        latest_context = 0
        turns = 0
        active_intervals = []
        last_user_ms = 0
    
        # Message metadata is sufficient for exact executions, calendar-day usage,
        # model attribution, context, and response timing. Part text remains unread.
        for data_raw, row_created in message_rows:
            data = decode_json(data_raw, None)
            if not isinstance(data, dict):
                continue
            role = data.get("role")
            time_obj = data.get("time") if isinstance(data.get("time"), dict) else {}
            created_ms = int_value(time_obj.get("created")) or int_value(row_created)
            completed_ms = int_value(time_obj.get("completed"))
            if role == "user":
                last_user_ms = created_ms
                continue
            if role != "assistant":
                continue
    
            turns += 1
            usage = usage_counts(data)
            input_evidence = usage["input_available"]
            output_evidence = usage["output_available"]
            msg_tokens = context_tokens(usage)
            msg_cost, msg_cost_available = reported_cost(data)
            msg_model = str(data.get("modelID") or model)
            msg_provider = str(data.get("providerID") or model_provider)
            msg_window = model_context_window(msg_model, msg_provider) or context_window
            end_ms = completed_ms or created_ms
            ts = end_ms / 1000.0 if end_ms else (last_ts or 0)
            duration_s = max(0.0, (completed_ms - created_ms) / 1000.0) \
                if created_ms and completed_ms else 0.0
            input_tokens = (usage["input_tokens"] + usage["cache_read_input_tokens"]
                            + usage["cache_creation_input_tokens"])
            output_tokens = usage["output_tokens"] + usage["reasoning_tokens"]
    
            models.add(msg_model)
            model_cost[msg_model] += msg_cost
            model_tok[msg_model] += msg_tokens
            stats = model_stats.setdefault(msg_model, {
                "cost": 0.0, "tokens": 0, "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "reasoning_tokens": 0,
                "reasoning_output_tokens": 0, "reasoning_executions": 0,
                "reasoning_unavailable_executions": 0,
                "token_covered_executions": 0,
                "io_covered_executions": 0,
                "cost_covered_executions": 0,
                "cost_covered_output_tokens": 0,
                "cost_covered_cost": 0.0,
                "cache_covered_input_tokens": 0, "executions": 0,
                "cost_evidence": False, "input_evidence": False,
                "output_evidence": False,
            })
            stats["cost"] += msg_cost
            stats["tokens"] += msg_tokens
            stats["input_tokens"] += input_tokens
            stats["output_tokens"] += output_tokens
            stats["cache_read_tokens"] += usage["cache_read_input_tokens"]
            stats["cache_write_tokens"] += usage["cache_creation_input_tokens"]
            if output_evidence:
                stats["token_covered_executions"] += 1
            if input_evidence and output_evidence:
                stats["io_covered_executions"] += 1
            if input_evidence:
                stats["cache_covered_input_tokens"] += input_tokens
            if msg_cost_available and output_evidence:
                stats["cost_covered_executions"] += 1
                stats["cost_covered_output_tokens"] += output_tokens
                stats["cost_covered_cost"] += msg_cost
            if usage["reasoning_available"] and output_evidence:
                stats["reasoning_tokens"] += min(
                    output_tokens, usage["reasoning_tokens"]
                )
                stats["reasoning_output_tokens"] += output_tokens
                stats["reasoning_executions"] += 1
            else:
                stats["reasoning_unavailable_executions"] += 1
            stats["executions"] += 1
            stats["cost_evidence"] = stats["cost_evidence"] or msg_cost_available
            stats["input_evidence"] = stats["input_evidence"] or input_evidence
            stats["output_evidence"] = stats["output_evidence"] or output_evidence
    
            if ts:
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                if msg_cost_available:
                    day_cost[day] += msg_cost
                daily = model_daily.setdefault((msg_model, day), {
                    "model": msg_model, "day": day, "cost": 0.0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "reasoning_tokens": 0, "reasoning_output_tokens": 0,
                    "reasoning_executions": 0,
                    "reasoning_unavailable_executions": 0,
                    "token_covered_executions": 0,
                    "io_covered_executions": 0,
                    "cost_covered_executions": 0,
                    "cost_covered_output_tokens": 0,
                    "cost_covered_cost": 0.0,
                    "cache_covered_input_tokens": 0, "executions": 0,
                    "cost_evidence": False, "input_evidence": False,
                    "output_evidence": False,
                })
                daily["cost"] += msg_cost
                daily["input_tokens"] += input_tokens
                daily["output_tokens"] += output_tokens
                daily["cache_read_tokens"] += usage["cache_read_input_tokens"]
                daily["cache_write_tokens"] += usage["cache_creation_input_tokens"]
                if output_evidence:
                    daily["token_covered_executions"] += 1
                if input_evidence and output_evidence:
                    daily["io_covered_executions"] += 1
                if input_evidence:
                    daily["cache_covered_input_tokens"] += input_tokens
                if msg_cost_available and output_evidence:
                    daily["cost_covered_executions"] += 1
                    daily["cost_covered_output_tokens"] += output_tokens
                    daily["cost_covered_cost"] += msg_cost
                if usage["reasoning_available"] and output_evidence:
                    daily["reasoning_tokens"] += min(
                        output_tokens, usage["reasoning_tokens"]
                    )
                    daily["reasoning_output_tokens"] += output_tokens
                    daily["reasoning_executions"] += 1
                else:
                    daily["reasoning_unavailable_executions"] += 1
                daily["executions"] += 1
                daily["cost_evidence"] = daily["cost_evidence"] or msg_cost_available
                daily["input_evidence"] = daily["input_evidence"] or input_evidence
                daily["output_evidence"] = daily["output_evidence"] or output_evidence
    
            if msg_tokens or isinstance(data.get("tokens"), dict):
                context_samples.append(msg_tokens)
                latest_context = msg_tokens
                context_window = msg_window or context_window
            if duration_s:
                active_intervals.append((created_ms / 1000.0, completed_ms / 1000.0))
            if duration_s and output_tokens:
                performance_samples.append({
                    "provider": "opencode", "model": msg_model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
                    "ts": ts, "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "peak_input_tokens": msg_tokens,
                    "uncached_input_tokens": usage["input_tokens"],
                    "cache_read_tokens": usage["cache_read_input_tokens"],
                    "cache_write_tokens": usage["cache_creation_input_tokens"],
                    "model_calls": 1, "duration_s": duration_s,
                    "generation_s": duration_s, "ttft_s": 0.0,
                    "tool_calls": 0, "timing_basis": "OpenCode message timestamps",
                })
            if last_user_ms and end_ms > last_user_ms:
                wait_s = (end_ms - last_user_ms) / 1000.0
                wait_samples.append({
                    "provider": "opencode", "model": msg_model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(end_ms / 1000.0)),
                    "ts": end_ms / 1000.0, "start_ts": last_user_ms / 1000.0,
                    "duration_s": wait_s, "tool_calls": 0,
                    "output_tokens": output_tokens, "context_tokens": msg_tokens,
                    "model_calls": 1, "timing_basis": "OpenCode message timestamps",
                    "ttft_s": 0.0, "attempts": 1, "failed_attempts": 0, "retries": 0,
                })
                last_user_ms = 0
    
        if not models:
            models.add(model)
        for stats in model_stats.values():
            cost_evidence = stats.pop("cost_evidence")
            input_evidence = stats.pop("input_evidence")
            output_evidence = stats.pop("output_evidence")
            stats["availability"] = metric_availability(
                "opencode", cost=cost_evidence,
                tokens=input_evidence or output_evidence,
                input_tokens=input_evidence, output_tokens=output_evidence,
                cache=input_evidence,
            )
        model_daily_rows = []
        for daily in model_daily.values():
            cost_evidence = daily.pop("cost_evidence")
            input_evidence = daily.pop("input_evidence")
            output_evidence = daily.pop("output_evidence")
            daily["availability"] = metric_availability(
                "opencode", cost=cost_evidence,
                tokens=input_evidence or output_evidence,
                input_tokens=input_evidence, output_tokens=output_evidence,
                cache=input_evidence,
            )
            model_daily_rows.append(daily)
    
        active_s = merge_execution_intervals(active_intervals)
        active = {"duration_s": active_s, "available": bool(active_s),
                  "basis": "OpenCode message timestamps" if active_s else "unavailable"}
        session_cost_available = (
            not isinstance(s_cost, bool) and isinstance(s_cost, (int, float))
            and math.isfinite(float(s_cost)) and float(s_cost) >= 0
        )
        session_token_evidence = all(value is not None for value in (
            s_input, s_output, s_reasoning, s_cr, s_cw,
        ))
        availability = metric_availability(
            "opencode", cost=session_cost_available, tokens=session_token_evidence,
            input_tokens=session_token_evidence, output_tokens=session_token_evidence,
            throughput=bool(performance_samples), cache=session_token_evidence,
            context=bool(context_window and context_samples), timing=bool(active_s),
        )
        row = summary_row(
            source, source.get("title"), s_cost_val, tokens, turns, models,
            first_ts, last_ts, model_cost, model_tok, day_cost, False, active,
            inp + cre + cw, out + reasoning, model_stats, model_daily_rows, performance_samples,
            wait_samples, availability,
        )
        row["primary_model"] = max(
            model_stats,
            key=lambda name: (model_stats[name]["executions"], model_stats[name]["tokens"]),
        ) if model_stats else model
        row["context"] = {
            "window": context_window,
            "latest": latest_context or None,
            "latest_pct": (latest_context / context_window)
                          if (context_window and latest_context) else None,
            "estimated": False,
        }
        row["_context_samples"] = context_samples[-self.context_sample_limit:]
        row["terminal"] = False
        row["_tool_evidence"] = summarize_tool_evidence(tool_evidence)
        # Mark a child session so the browser can keep it out of the default
        # session list while still counting its spend. Only the resolved root
        # session id is published: it is itself a discovered, listed session, so
        # the raw (possibly unlisted) parent reference never leaves the server.
        if source.get("agent_parent_id"):
            row["is_child_session"] = True
            root_id = str(source.get("agent_root_id") or "")
            if root_id:
                row["root_session_id"] = root_id
            row["child_depth"] = max(1, int(source.get("agent_depth") or 1))
            row["child_agent_role"] = safe_agent_role(
                source.get("agent_role")
            ) or None
        agent_records = self._agent_record_for(
            source, row,
            cost_value=s_cost_val, cost_available=session_cost_available,
            tokens_available=session_token_evidence,
            input_tokens=inp, output_tokens=out, reasoning=reasoning,
            cache_read=cre, cache_write=cw, total_tokens=tokens,
            first_ts=first_ts, last_ts=last_ts, work_time_s=active_s,
        )
        if agent_records:
            row["_agent_records"] = agent_records
        return row

    def _agent_record_for(
        self, source, row, *, cost_value, cost_available, tokens_available,
        input_tokens, output_tokens, reasoning, cache_read, cache_write,
        total_tokens, first_ts, last_ts, work_time_s,
    ):
        """Describe a child session as one spawned agent record.

        A root session contributes no record. A child contributes a single
        `spawned` record whose parent is the opaque digest of the parent
        session id, so no private session identity is projected. A reported cost
        of zero is a measured free-tier price, not missing evidence, so
        availability is decided by column validity rather than by magnitude.
        """
        parent_id = str(source.get("agent_parent_id") or "")
        is_child = bool(parent_id)
        # A group needs its root member. A parent session emits a `root` record
        # when it actually has children; a child emits one `spawned` record.
        if not is_child and not source.get("agent_has_children"):
            return []
        agent_id = opaque_agent_id(source.get("id"))
        if not agent_id:
            return []
        parent_agent_id = opaque_agent_id(parent_id) if is_child else ""
        if is_child and not parent_agent_id:
            return []
        model = str(row.get("primary_model") or source.get("model") or "unknown")
        last_activity = last_ts or (float(source.get("mtime") or 0) or None)
        now = time.time()
        # OpenCode summaries never report a terminal state, so a child that is
        # not recently active is a stale nonterminal trace. Claiming "complete"
        # would assert a finish we have no evidence for.
        activity_state = (
            "working" if last_activity and now - last_activity <= 90
            else "incomplete"
        )
        session_id = str(source.get("id") or "")
        role = source.get("agent_role") if is_child else None
        return [{
            "id": agent_id,
            "parent_id": parent_agent_id or None,
            # The public session id is the established OpenCode session route,
            # so it stays navigable. The private parent session id is not, and
            # the group layer falls back to the owning session id for it.
            "session_id": session_id or None,
            "runtime": "opencode",
            "client": "OpenCode",
            "kind": "spawned" if is_child else "root",
            "depth": max(1, int(source.get("agent_depth") or 1)) if is_child else 0,
            "label": _compact_text(source.get("title"), 80),
            "role": role or None,
            "model": model,
            "activity_state": activity_state,
            "started_at": first_ts,
            "ended_at": last_ts,
            "last_activity_at": last_activity,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cache_read_tokens": int(cache_read or 0),
            "cache_write_tokens": int(cache_write or 0),
            "reasoning_tokens": int(reasoning or 0),
            "tokens": int(total_tokens or 0),
            "tokens_available": bool(tokens_available),
            "cost": round(float(cost_value or 0.0), 6) if cost_available else None,
            "cost_available": bool(cost_available),
            "executions": int(row.get("turns") or 0),
            "work_time_s": round(float(work_time_s), 3) if work_time_s else None,
        }]


    def deletion_plan(self, source):
        return DeletionPlan.deny("Sessions share one read-only database.")


class OpenCodeRuntimeAdapterProxy:
    """Keep a registry entry stable while tests/configuration change DB paths."""

    descriptor = OpenCodeRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid runtime adapter")
        return adapter

    def discover(self, context):
        return self._adapter().discover(context)

    def discover_legacy(self, context):
        return self._adapter().discover_legacy(context)

    def current_revision(self, source):
        return self._adapter().current_revision(source)

    def load(self, source, detail):
        return self._adapter().load(source, detail)

    def summarize_legacy(self, source, connection=None):
        return self._adapter().summarize_legacy(source, connection)

    def deletion_plan(self, source):
        return self._adapter().deletion_plan(source)
