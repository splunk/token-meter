"""Native read-only adapter for Cursor Composer evidence.

Cursor stores session metadata and conversation bubbles in a shared SQLite
database while retaining one transcript file per top-level session.  The
adapter treats both as read-only evidence and never exposes message content or
tool payloads through normalized contracts.
"""

import contextlib
import glob
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
from token_meter.domain.timing import merge_execution_intervals as _merge_execution_intervals
from token_meter.models.catalog import CURSOR_EFFORT_SUFFIX_RE


CHARS_PER_TOKEN = 4
MAX_BUBBLES = 1_000

CURSOR_TOOL_IDENTITIES = {
    "read_file_v2": ("Read", "files"),
    "ripgrep_raw_search": ("Grep", "search"),
    "glob_file_search": ("Glob", "search"),
    "run_terminal_command_v2": ("Shell", "shell"),
    "edit_file_v2": ("Edit", "files"),
    "apply_patch": ("Apply patch", "files"),
    "todo_write": ("Todo", "planning"),
    "web_search": ("Web search", "web"),
    "web_fetch": ("Web fetch", "web"),
    "delete_file": ("Delete", "files"),
    "await": ("Await", "orchestration"),
    "task_v2": ("Subagent", "orchestration"),
    "update_current_step": ("Update step", "planning"),
    "ask_question": ("Ask question", "interaction"),
    "switch_mode": ("Switch mode", "interaction"),
    "set_active_branch": ("Set branch", "workspace"),
    "search_conversations": ("Search chats", "search"),
    "get_mcp_tools": ("MCP tool lookup", "tool_search"),
}
CURSOR_TOOL_ALIASES = {
    "read": "read_file_v2", "readfile": "read_file_v2", "read_file": "read_file_v2",
    "grep": "ripgrep_raw_search", "rg": "ripgrep_raw_search",
    "glob": "glob_file_search", "shell": "run_terminal_command_v2",
    "edit": "edit_file_v2", "applypatch": "apply_patch",
    "todowrite": "todo_write", "websearch": "web_search", "webfetch": "web_fetch",
    "delete": "delete_file", "deletefile": "delete_file",
    "task": "task_v2",
}
CURSOR_MCP_TOOL_RE = re.compile(r"^mcp-([A-Za-z0-9_.-]+)-([A-Za-z0-9_.]+)$")


def cursor_tool_identity(name):
    """Return a content-free display identity for one Cursor tool name."""
    raw = str(name or "?")
    mcp = CURSOR_MCP_TOOL_RE.fullmatch(raw)
    if mcp:
        return {"name": raw, "display": mcp.group(2), "namespace": mcp.group(1),
                "kind": "mcp", "mcp_server": mcp.group(1)}
    alias = re.sub(r"[^a-z0-9_]", "", raw.lower())
    canonical = CURSOR_TOOL_ALIASES.get(alias, raw)
    display, namespace = CURSOR_TOOL_IDENTITIES.get(canonical, (canonical, "cursor"))
    return {"name": canonical, "display": display, "namespace": namespace, "kind": "tool"}
CURSOR_TRACE_SPANS = frozenset((
    "client.ttft",
    "agent.request.attempt",
    "rpc.run",
    "ComposerChatService.submitChatMaybeAbortCurrent",
))
CURSOR_TRACE_FIELD_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')
CURSOR_AGENT_ROLE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def _agent_role(value):
    """Keep short configured subagent type names, never arbitrary prose."""
    return value if isinstance(value, str) and CURSOR_AGENT_ROLE_RE.fullmatch(value) else ""


def cursor_agent_id(composer_id):
    """Return a stable public agent ID without exposing the Composer ID."""
    composer_id = str(composer_id or "")
    if not composer_id:
        return ""
    digest = hashlib.sha256(
        b"token-meter:cursor-agent:v1\0" + composer_id.encode("utf-8", "replace")
    ).hexdigest()
    return "cursor-agent-" + digest


def _subagent_info(header):
    info = header.get("subagentInfo") if isinstance(header, dict) else None
    return info if isinstance(info, dict) else {}


def _fork_prefix_length(header):
    """Return how many leading bubbles a forked subagent copied from its parent."""
    info = _subagent_info(header)
    if not info.get("forkedFromComposerId"):
        return 0
    return _safe_int(info.get("conversationLengthAtSpawn"))


def _text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict):
                pieces.append(block.get("text") or block.get("content") or "")
        return " ".join(piece for piece in pieces if isinstance(piece, str))
    return ""


def _json_object(value, default=None):
    """Decode one SQLite JSON value without surfacing raw parse failures."""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return default
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value) if value else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return decoded if isinstance(decoded, dict) else default


def _safe_int(value):
    try:
        result = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def _safe_float(value):
    try:
        result = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


def _timestamp(value):
    """Return seconds for Cursor millisecond/second timestamps."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0
    number = _safe_float(value)
    return number / 1000.0 if number > 10_000_000_000 else number


def _datetime(value):
    seconds = _timestamp(value)
    return datetime.fromtimestamp(seconds).astimezone() if seconds else None


def _workspace_path(*values):
    for value in values:
        if not isinstance(value, dict):
            continue
        workspace = value.get("workspaceIdentifier")
        if isinstance(workspace, dict):
            uri = workspace.get("uri")
            if isinstance(uri, dict):
                path = uri.get("fsPath") or uri.get("path")
                if isinstance(path, str) and path.strip():
                    return path.strip()
        repos = value.get("trackedGitRepos")
        if isinstance(repos, list):
            for repo in repos:
                path = repo.get("repoPath") if isinstance(repo, dict) else None
                if isinstance(path, str) and path.strip():
                    return path.strip()
    return ""


def cursor_model_name(composer, header=None):
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    if isinstance(config, dict) and config.get("modelName"):
        name = str(config["modelName"])
        for row in config.get("selectedModels") or []:
            model_id = str(row.get("modelId") or "") if isinstance(row, dict) else ""
            if (model_id and name.startswith(model_id + "-") and
                    CURSOR_EFFORT_SUFFIX_RE.fullmatch(name[len(model_id):])):
                return model_id
        return name
    for value in (composer, header):
        if isinstance(value, dict) and value.get("model"):
            return str(value["model"])
    return "unknown"


def _compact(value, limit=90):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def _tool_name(value):
    name = str(value or "").strip()
    aliases = {
        "read_file": "read",
        "readfile": "read",
        "write_file": "write",
        "edit_file": "edit",
        "run_terminal_command": "terminal",
        "terminal_command": "terminal",
        "codebase_search": "search",
        "grep_search": "search",
    }
    return aliases.get(name.lower(), name or "unknown")


def _text_chars(value):
    """Count observable text without retaining or returning it."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(
            _text_chars(item.get("text"))
            for item in value
            if isinstance(item, dict) and item.get("type") in ("text", "thinking")
        )
    return 0

class CursorRuntimeAdapter:
    """Own Cursor discovery, source revisions, and content-free loading."""

    descriptor = RuntimeDescriptor(
        "cursor",
        "Cursor",
        frozenset(("sessions", "models", "tools", "quota")),
        "runtime.cursor",
        "runtime-cursor",
        "cursor",
    )

    def __init__(self, projects_root, database_path, request_logs=None,
                 project_resolver=None, compatibility=None,
                 max_bubbles=MAX_BUBBLES, path_cache=None,
                 project_decoder=None):
        self.projects_root = Path(os.path.abspath(os.path.expanduser(str(projects_root))))
        self.database_path = Path(os.path.abspath(os.path.expanduser(str(database_path))))
        self.request_logs = (
            Path(os.path.abspath(os.path.expanduser(str(request_logs))))
            if request_logs else None
        )
        self.project_resolver = project_resolver or (lambda value: value)
        self.project_decoder = project_decoder or (lambda value: value.replace("-", "/"))
        self.compatibility = dict(compatibility or {})
        self.max_bubbles = max(1, int(max_bubbles))
        self.path_cache = path_cache
        self._metadata_signature = None
        self._metadata_rows = {}
        self._request_cache = {}

    @staticmethod
    def _file_signature(path):
        try:
            stat = os.stat(path)
            return (str(stat.st_mtime_ns), str(stat.st_size))
        except OSError:
            return ("0", "0")

    def _database_signature(self):
        return (
            self._file_signature(self.database_path),
            self._file_signature(str(self.database_path) + "-wal"),
        )

    def connection(self):
        """Open Cursor's live WAL-aware database with writes disabled."""
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.05)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 3000")
        return connection

    def _query_metadata(self):
        try:
            with contextlib.closing(self.connection()) as connection:
                headers = connection.execute(
                    "SELECT composerId, workspaceId, createdAt, lastUpdatedAt, "
                    "isArchived, isSubagent, checkpointAt, value FROM composerHeaders"
                ).fetchall()
                composers = {
                    str(key).split(":", 1)[1]: _json_object(value, {})
                    for key, value in connection.execute(
                        "SELECT key, value FROM cursorDiskKV "
                        "WHERE key LIKE 'composerData:%'"
                    ).fetchall()
                    if str(key).startswith("composerData:")
                }
        except (OSError, sqlite3.Error):
            return {}

        result = {}
        for (composer_id, workspace_id, created_at, updated_at, archived,
             subagent, checkpoint_at, raw_header) in headers:
            composer_id = str(composer_id)
            header = _json_object(raw_header, {})
            composer = composers.get(composer_id, {})
            info = _subagent_info(header)
            result[composer_id] = {
                "parent_id": str(info.get("parentComposerId") or ""),
                "role": _agent_role(info.get("subagentTypeName")),
                "workspace_id": str(workspace_id or ""),
                "created_at": _safe_int(
                    created_at or header.get("createdAt") or composer.get("createdAt")
                ),
                "updated_at": _safe_int(
                    updated_at or header.get("lastUpdatedAt") or
                    composer.get("lastUpdatedAt")
                ),
                "checkpoint_at": _safe_int(checkpoint_at),
                "archived": bool(archived),
                "is_subagent": bool(
                    subagent or header.get("isBestOfNSubcomposer") or
                    composer.get("isBestOfNSubcomposer")
                ),
                "title": _compact(header.get("name") or composer.get("name")),
                "project": _workspace_path(header, composer),
                "model": cursor_model_name(composer, header),
            }
        return result

    def metadata_index(self):
        signature = self._database_signature()
        if signature != self._metadata_signature:
            rows = self._query_metadata()
            self._metadata_rows = rows
            self._metadata_signature = signature
        return {key: dict(value) for key, value in self._metadata_rows.items()}

    def reset_metadata_cache(self):
        self._metadata_signature = None
        self._metadata_rows = {}

    def _transcript_paths(self):
        transcripts = self.projects_root / "*" / "agent-transcripts" / "*"
        paths = []
        for pattern in (transcripts / "*.jsonl", transcripts / "subagents" / "*.jsonl"):
            pattern = str(pattern)
            paths.extend(
                self.path_cache.paths(pattern) if self.path_cache is not None
                else glob.glob(pattern)
            )
        return paths

    def _legacy_records(self):
        metadata = self.metadata_index()
        by_id = {}
        request_revisions = self.request_revision_index()
        for path in self._transcript_paths():
            session_id = os.path.basename(path).rsplit(".", 1)[0]
            container = os.path.dirname(path)
            nested = os.path.basename(container) == "subagents"
            if nested:
                container = os.path.dirname(container)
            elif os.path.basename(container) != session_id:
                continue
            row = metadata.get(session_id) or {}
            spawned = nested or bool(row.get("is_subagent"))
            parent_id = row.get("parent_id") or (
                os.path.basename(container) if nested else ""
            )
            project_dir = os.path.basename(os.path.dirname(os.path.dirname(container)))
            project = row.get("project") or self.project_decoder(project_dir)
            trace_mtime = self._mtime(path)
            metadata_mtime = max(
                _safe_float(row.get("updated_at")) / 1000.0,
                _safe_float(row.get("checkpoint_at")) / 1000.0,
            )
            activity_mtime = max(trace_mtime, metadata_mtime)
            candidate = {
                "id": session_id,
                "path": path,
                "project": self.project_resolver(project),
                "mtime": activity_mtime,
                "signature_mtime": activity_mtime,
                "trace_mtime": trace_mtime,
                "request_revision": request_revisions.get(session_id, ""),
                "title": row.get("title") or None,
                "model": row.get("model") or "unknown",
                "agent_id": cursor_agent_id(session_id),
                "agent_kind": "spawned" if spawned else "root",
                "agent_parent_id": (
                    cursor_agent_id(parent_id)
                    if spawned and parent_id and parent_id != session_id else None
                ),
                "agent_role": row.get("role") or "",
            }
            previous = by_id.get(session_id)
            if not previous or (trace_mtime, path) > (
                    previous["trace_mtime"], previous["path"]):
                by_id[session_id] = candidate
        return tuple(by_id.values())

    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _request_trace_paths(self):
        if self.request_logs is None:
            return ()
        pattern = str(self.request_logs / "**" / "cursor.requestTraces.log")
        if self.path_cache is not None:
            return self.path_cache.paths(pattern, recursive=True)
        return tuple(glob.glob(pattern, recursive=True))

    def enrichment_mtime(self):
        values = [
            self._mtime(self.database_path),
            self._mtime(str(self.database_path) + "-wal"),
        ]
        values.extend(self._mtime(path) for path in self._request_trace_paths())
        return max(values, default=0.0)

    def _request_file(self, path):
        signature = self._file_signature(path)
        cached = self._request_cache.get(path)
        if cached and cached["signature"] == signature:
            return cached["rows"]
        rows = []
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if " span_completed " not in line:
                        continue
                    fields = {
                        key: value[1:-1]
                        if value.startswith('"') and value.endswith('"') else value
                        for key, value in CURSOR_TRACE_FIELD_RE.findall(line)
                    }
                    name = fields.get("name")
                    composer_id = fields.get("composerId")
                    if name not in CURSOR_TRACE_SPANS or not composer_id:
                        continue
                    duration_ms = _safe_float(fields.get("durationMs"))
                    end_ts = _timestamp(line.split(" ", 1)[0])
                    if not end_ts or duration_ms > 24 * 60 * 60 * 1000:
                        continue
                    rows.append({
                        "composer_id": composer_id,
                        "request_id": fields.get("requestId") or "",
                        "trace_id": fields.get("traceId") or "",
                        "name": name,
                        "end_ts": float(end_ts),
                        "start_ts": float(end_ts) - duration_ms / 1000.0,
                        "duration_s": duration_ms / 1000.0,
                        "error": str(fields.get("error") or "").lower() == "true",
                    })
        except OSError:
            rows = []
        self._request_cache[path] = {"signature": signature, "rows": rows}
        return rows

    def request_spans(self, session_id):
        return sorted(
            (row for row in self._request_rows()
             if row.get("composer_id") == session_id),
            key=lambda row: (row["end_ts"], row["name"]),
        )

    def _request_rows(self):
        paths = self._request_trace_paths()
        current = set(paths)
        for path in tuple(self._request_cache):
            if path not in current:
                self._request_cache.pop(path, None)
        return tuple(row for path in paths for row in self._request_file(path))

    def request_revision_index(self):
        """Return stable request-trace revisions scoped to Composer sessions."""
        rows_by_session = defaultdict(list)
        for row in self._request_rows():
            session_id = str(row.get("composer_id") or "")
            if not session_id:
                continue
            rows_by_session[session_id].append((
                str(row.get("request_id") or ""),
                str(row.get("trace_id") or ""),
                str(row.get("name") or ""),
                float(row.get("start_ts") or 0),
                float(row.get("end_ts") or 0),
                float(row.get("duration_s") or 0),
                bool(row.get("error")),
            ))
        return {
            session_id: hashlib.sha256(
                repr(sorted(rows)).encode("utf-8", errors="replace")
            ).hexdigest()
            for session_id, rows in rows_by_session.items()
        }

    def discover(self, context):
        del context
        return tuple(SessionSource(
            runtime_id=self.descriptor.runtime_id,
            client_id=self.descriptor.runtime_id,
            session_id=record["id"],
            display_label=self.descriptor.label,
            project=record["project"],
            locator=SourceLocator("transcript", record["path"]),
            activity_mtime=record["mtime"],
            revision=SourceRevision((
                str(record["signature_mtime"]),
                str(record["trace_mtime"]),
                str(record["request_revision"]),
            )),
            model_ref=ModelRef("cursor", record["model"]),
            account_provider_id=self.descriptor.account_provider_id,
        ) for record in self._legacy_records())

    def discover_legacy(self, context):
        del context
        return tuple({
            "provider": "cursor",
            "client": "cursor",
            "label": "Cursor",
            "runtime": "Cursor",
            "id": record["id"],
            "session": os.path.basename(record["path"]),
            "path": record["path"],
            "project": record["project"],
            "mtime": record["mtime"],
            "signature_mtime": record["signature_mtime"],
            "trace_mtime": record["trace_mtime"],
            "request_revision": record["request_revision"],
            "title": record["title"],
            "model": record["model"],
            "agent_id": record["agent_id"],
            "agent_kind": record["agent_kind"],
            "agent_parent_id": record["agent_parent_id"],
            "agent_role": record["agent_role"],
        } for record in self._legacy_records())

    def _session_revision(self, session_id):
        try:
            with contextlib.closing(self.connection()) as connection:
                row = connection.execute(
                    "SELECT lastUpdatedAt, checkpointAt FROM composerHeaders "
                    "WHERE composerId=?",
                    (session_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            row = None
        return tuple(str(_safe_int(value)) for value in row) if row else ("0", "0")

    def current_revision(self, source):
        if isinstance(source, SessionSource):
            session_id = source.session_id
            path = source.locator.value if source.locator.kind == "transcript" else ""
        else:
            session_id = str(source.get("id") or "")
            path = str(source.get("path") or "")
        trace = self._file_signature(path)
        updated, checkpoint = self._session_revision(session_id)
        request_revision = self.request_revision_index().get(session_id, "")
        return SourceRevision((
            trace[0], trace[1], updated, checkpoint, request_revision,
        ))

    def snapshot(self, session_id):
        """Read one ordered conversation and discard malformed bubble values."""
        try:
            with contextlib.closing(self.connection()) as connection:
                row = connection.execute(
                    "SELECT workspaceId, createdAt, lastUpdatedAt, isArchived, "
                    "isSubagent, checkpointAt, value FROM composerHeaders "
                    "WHERE composerId=?",
                    (session_id,),
                ).fetchone()
                if not row:
                    return None
                composer_row = connection.execute(
                    "SELECT value FROM cursorDiskKV WHERE key=?",
                    ("composerData:" + session_id,),
                ).fetchone()
                header = _json_object(row[6], {})
                composer = _json_object(composer_row[0], {}) if composer_row else {}
                ordered = composer.get("fullConversationHeadersOnly") or []
                if not isinstance(ordered, list):
                    ordered = []
                inherited = self._verified_fork_prefix(connection, header, ordered)
                if inherited:
                    ordered = ordered[inherited:]
                bubble_ids = [
                    str(item.get("bubbleId"))
                    for item in ordered[:self.max_bubbles + 1]
                    if isinstance(item, dict) and item.get("bubbleId")
                ]
                values = {}
                for offset in range(0, len(bubble_ids), 400):
                    chunk = bubble_ids[offset:offset + 400]
                    keys = ["bubbleId:{}:{}".format(session_id, value) for value in chunk]
                    placeholders = ",".join("?" for _ in keys)
                    values.update(connection.execute(
                        "SELECT key, value FROM cursorDiskKV WHERE key IN ({})".format(
                            placeholders
                        ),
                        keys,
                    ).fetchall())
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None
        bubbles = []
        for bubble_id in bubble_ids[:self.max_bubbles]:
            value = values.get("bubbleId:{}:{}".format(session_id, bubble_id))
            bubble = _json_object(value, None)
            if bubble:
                bubbles.append(bubble)
        return {
            "available": True,
            "header": header,
            "composer": composer,
            "bubbles": bubbles,
            "created_at": _safe_int(row[1]),
            "updated_at": _safe_int(row[2]),
            "truncated": len(bubble_ids) > self.max_bubbles,
            "checkpoint_at": _safe_int(row[5]),
            "is_subagent": bool(row[4]),
        }

    @staticmethod
    def _verified_fork_prefix(connection, header, ordered):
        """Count leading bubbles proven to be copies of the fork parent's history."""
        limit = min(_fork_prefix_length(header), len(ordered))
        if limit <= 0:
            return 0
        parent_id = str(_subagent_info(header).get("forkedFromComposerId") or "")
        parent_row = connection.execute(
            "SELECT value FROM cursorDiskKV WHERE key=?", ("composerData:" + parent_id,),
        ).fetchone()
        parent = _json_object(parent_row[0], {}) if parent_row else {}
        parent_ordered = parent.get("fullConversationHeadersOnly") or []
        if not isinstance(parent_ordered, list):
            return 0
        count = 0
        for own, inherited in zip(ordered[:limit], parent_ordered):
            own_id = own.get("bubbleId") if isinstance(own, dict) else None
            parent_bubble = inherited.get("bubbleId") if isinstance(inherited, dict) else None
            if not own_id or own_id != parent_bubble:
                break
            count += 1
        return count

    @staticmethod
    def _empty_session(source, detail, warning):
        return NormalizedSession(
            source=source,
            started_at=None,
            ended_at=None,
            usage=UsageEvidence.unavailable(),
            timing=TimingEvidence.unavailable(),
            tools=(),
            turns=(),
            pricing_basis=None,
            capabilities=CursorRuntimeAdapter.descriptor.capabilities,
            warnings=(ParseWarning(warning, "Cursor evidence was unavailable."),),
            detail=detail,
        )

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != self.descriptor.runtime_id:
            raise ValueError("source belongs to another runtime")
        snapshot = self.snapshot(source.session_id)
        if snapshot is None:
            return self._empty_session(source, detail, "source_unavailable")

        composer = snapshot["composer"]
        bubbles = snapshot["bubbles"]
        raw_input_tokens = composer.get("contextTokensUsed")
        input_available = (
            not isinstance(raw_input_tokens, bool) and
            isinstance(raw_input_tokens, (int, float)) and
            math.isfinite(float(raw_input_tokens)) and raw_input_tokens >= 0
        )
        input_tokens = _safe_int(raw_input_tokens)
        output_chars = 0
        output_available = False
        tools = []
        turns = []
        assistant_started = []
        user_started = []
        for bubble in bubbles:
            role = bubble.get("type")
            created_at = _datetime(bubble.get("createdAt"))
            if role in (1, "user"):
                if created_at:
                    user_started.append(created_at)
                continue
            if role not in (2, "assistant"):
                continue
            output_available = True
            if created_at:
                assistant_started.append(created_at)
            chars = _text_chars(bubble.get("text")) + _text_chars(bubble.get("content"))
            output_chars += chars
            tool_data = bubble.get("toolFormerData")
            if isinstance(tool_data, dict) and tool_data.get("name"):
                tools.append(ToolEvent(_tool_name(tool_data.get("name")), "tool"))
            for block in bubble.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools.append(ToolEvent(_tool_name(block.get("name")), "tool"))
            turns.append(TurnSummary(
                index=len(turns) + 1,
                started_at=created_at,
                ended_at=created_at,
                output_tokens=EvidenceValue(
                    max(0, chars // CHARS_PER_TOKEN), EvidenceBasis.ESTIMATED
                ),
            ))

        output_tokens = max(0, output_chars // CHARS_PER_TOKEN)
        started_at = _datetime(snapshot["created_at"])
        ended_at = _datetime(snapshot["updated_at"])
        active = None
        if started_at and ended_at and ended_at >= started_at:
            active = (ended_at - started_at).total_seconds()
        wait = None
        if user_started and assistant_started:
            wait = max(0.0, (assistant_started[0] - user_started[0]).total_seconds())
        warnings = ()
        if snapshot["truncated"]:
            warnings = (ParseWarning(
                "history_truncated",
                "Detailed history was limited to {} bubbles.".format(self.max_bubbles),
            ),)
        elif not input_available and not output_available:
            warnings = (ParseWarning(
                "usage_unavailable",
                "Cursor token evidence was unavailable for this session.",
            ),)
        return NormalizedSession(
            source=source,
            started_at=started_at,
            ended_at=ended_at,
            usage=UsageEvidence(
                input_tokens=(
                    EvidenceValue(input_tokens, EvidenceBasis.ESTIMATED)
                    if input_available else EvidenceValue.unavailable()
                ),
                output_tokens=(
                    EvidenceValue(output_tokens, EvidenceBasis.ESTIMATED)
                    if output_available else EvidenceValue.unavailable()
                ),
                cache_read_tokens=EvidenceValue.unavailable(),
                cache_write_tokens=EvidenceValue.unavailable(),
                cost_usd=EvidenceValue.unavailable(),
            ),
            timing=TimingEvidence(
                active_seconds=(
                    EvidenceValue(active, EvidenceBasis.INFERRED)
                    if active is not None else EvidenceValue.unavailable()
                ),
                wait_seconds=(
                    EvidenceValue(wait, EvidenceBasis.INFERRED)
                    if wait is not None else EvidenceValue.unavailable()
                ),
                ttft_seconds=EvidenceValue.unavailable(),
            ),
            tools=tuple(tools),
            turns=tuple(turns) if detail is DetailLevel.FULL else (),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=warnings,
            detail=detail,
        )

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    def snapshot_legacy(self, session_id):
        snapshot = self.snapshot(session_id)
        return snapshot or {
            "available": False, "header": {}, "composer": {}, "bubbles": []
        }

    def recompute_legacy(self, source):
        """Build explicitly estimated usage from Cursor's locally persisted evidence."""
        compat = self._require_compatibility()
        ZERO_PRICE = compat["zero_price"]
        analysis_block = compat["analysis_block"]
        argument_fingerprint = compat["argument_fingerprint"]
        build_state = compat["build_state"]
        compact_text = compat["compact_text"]
        cost_of = compat["cost_of"]
        cursor_context_estimates = compat["cursor_context_estimates"]
        cursor_enriched_groups = compat["cursor_enriched_groups"]
        cursor_model_call_count = compat["cursor_model_call_count"]
        cursor_price_variant = compat["cursor_price_variant"]
        cursor_pricing_note = compat["cursor_pricing_note"]
        cursor_prompt_breakdown = compat["cursor_prompt_breakdown"]
        cursor_timestamp = compat["cursor_timestamp"]
        cursor_tool_identity = compat["cursor_tool_identity"]
        cursor_transcript_groups = compat["cursor_transcript_groups"]
        cursor_turn_timing = compat["cursor_turn_timing"]
        cursor_visible_output = compat["cursor_visible_output"]
        duration_label = compat["duration_label"]
        load = compat["load"]
        local_tm = compat["local_tm"]
        metric_availability = compat["metric_availability"]
        metric_available = compat["metric_available"]
        observable_output_chars = compat["observable_output_chars"]
        performance_summary = compat["performance_summary"]
        price_for = compat["price_for"]
        request_spans = compat["request_spans"]
        skill_names_from_value = compat["skill_names_from_value"]
        snapshot = compat["snapshot"]
        tool_result_is_error = compat["tool_result_is_error"]
        tool_summary = compat["tool_summary"]
        trace_event = compat["trace_event"]
        transcript = load(source.get("path"))
        snapshot_data = snapshot(source.get("id"))
        enriched = bool(snapshot_data.get("available") and snapshot_data.get("bubbles"))
        groups = (cursor_enriched_groups(snapshot_data) if enriched else
                  cursor_transcript_groups(transcript, source))
        if not groups:
            return None
        spans = request_spans(source.get("id")) if enriched else []
        composer = snapshot_data.get("composer") or {}
        latest_context = int(composer.get("contextTokensUsed") or 0)
        latest_window = int(composer.get("contextTokenLimit") or 0)
        context_rows = cursor_context_estimates(groups, latest_context, latest_window)
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace = [], [], []
        wait_samples, performance_samples, active_intervals = [], [], []
        first_ts = last_ts = 0.0
        claimed_request_ids = frozenset(
            str(group.get("request_id")) for group in groups if group.get("request_id")
        )

        for position, group in enumerate(groups):
            idx = position + 1
            start_ts = float(group.get("start_ts") or 0)
            next_start = float(groups[position + 1].get("start_ts") or 0) if position + 1 < len(groups) else 0
            bubbles = group.get("bubbles") or []
            bubble_ts = [cursor_timestamp(bubble.get("createdAt")) for bubble in bubbles
                         if isinstance(bubble, dict) and bubble.get("createdAt")]
            terminal_ts = max(bubble_ts, default=start_ts)
            turn_duration_ms = group.get("turn_duration_ms") or max(
                (bubble.get("turnDurationMs") or 0 for bubble in bubbles if isinstance(bubble, dict)),
                default=0,
            )
            timing = cursor_turn_timing(
                spans, start_ts, next_start, terminal_ts, turn_duration_ms,
                request_id=str(group.get("request_id") or ""),
                claimed_request_ids=claimed_request_ids,
            )
            end_ts = timing.get("end_ts") or terminal_ts or start_ts
            active_intervals.extend(timing.get("active_intervals") or [])
            model = str(group.get("model") or source.get("model") or "unknown")
            user_text = compact_text(str(group.get("user_text") or ""), 220)
            context_row = context_rows[position]
            context_tokens = int(context_row.get("tokens") or 0)
            context_window = int(context_row.get("window") or 0)
            visible = cursor_visible_output(bubbles)
            assistant_tokens = int(visible["assistant_tokens"])
            reasoning_tokens = int(visible["reasoning_tokens"])
            output_tokens = assistant_tokens + reasoning_tokens
            model_calls = cursor_model_call_count(bubbles)
            variant = cursor_price_variant(composer, model)
            pricing_at = end_ts or start_ts
            price, _ = price_for(model, "cursor", variant, at=pricing_at)
            pricing_supported = any(float(value or 0) > 0 for value in price.values())
            usage = {"input_tokens": context_tokens, "output_tokens": output_tokens}
            cost_available = bool(context_tokens and pricing_supported)
            cost_breakdown = (cost_of(usage, model, "cursor", variant, at=pricing_at)
                              if cost_available else dict(ZERO_PRICE))
            execution_cost = sum(cost_breakdown.values())
            tools = []
            reasoning_ms = 0.0
            assistant_text = ""
            if user_text:
                trace.append(trace_event(start_ts, "user", "User message", compact_text(user_text, 84),
                                         idx, severity="start", model=model,
                                         native_type="message",
                                         native_subtype="user_message"))
    
            for bubble in bubbles:
                if not isinstance(bubble, dict):
                    continue
                ts = cursor_timestamp(bubble.get("createdAt")) or terminal_ts
                thinking = bubble.get("thinking")
                try:
                    thinking_ms = float(bubble.get("thinkingDurationMs") or 0)
                except (TypeError, ValueError):
                    thinking_ms = 0.0
                if thinking is not None or thinking_ms > 0:
                    reasoning_ms += max(0.0, thinking_ms)
                    trace.append(trace_event(
                        ts, "reasoning", "Reasoning",
                        duration_label(thinking_ms / 1000.0) if thinking_ms else "Trace-visible reasoning",
                        idx, severity="reasoning", model=model, duration_ms=thinking_ms or None,
                        native_type="message", native_subtype="reasoning",
                    ))
                tool_data = bubble.get("toolFormerData")
                if isinstance(tool_data, dict) and tool_data.get("name"):
                    ident = cursor_tool_identity(tool_data.get("name"))
                    arguments = tool_data.get("params") or tool_data.get("rawArgs") or {}
                    result_value = tool_data.get("result")
                    if result_value in (None, "", [], {}):
                        result_value = tool_data.get("additionalData")
                    result_present = result_value not in (None, "", [], {})
                    output_chars = observable_output_chars(result_value) if result_present else 0
                    status = str(tool_data.get("status") or "").lower()
                    errored = bool(status in ("error", "failed", "failure") or
                                   tool_data.get("error") not in (None, "", False) or
                                   tool_result_is_error(result_value, False))
                    tool = {
                        **ident,
                        "id": tool_data.get("toolCallId") or bubble.get("bubbleId"),
                        "call_id": tool_data.get("toolCallId") or bubble.get("bubbleId"),
                        "args_chars": len(str(arguments or "")),
                        "args_fingerprint": argument_fingerprint(arguments),
                        "output_chars": output_chars,
                        "output_tokens": output_chars // CHARS_PER_TOKEN,
                        "result_available": result_present,
                        "error": errored,
                        "skills": skill_names_from_value(arguments, tool_data.get("name")),
                    }
                    tools.append(tool)
                    trace.append(trace_event(
                        ts, "tool_call", ident["display"], ident["namespace"], idx,
                        tool=ident["name"], severity="tool", model=model,
                        args_chars=tool["args_chars"], tool_kind=ident["kind"],
                        native_type="message", native_subtype="tool_call",
                    ))
                    if result_present or errored:
                        trace.append(trace_event(
                            ts, "tool_result", ident["display"],
                            f"~{tool['output_tokens']:,} returned tokens" if result_present else "Tool error",
                            idx, tool=ident["name"], tokens=tool["output_tokens"],
                            severity="warn" if errored else "retrieval", model=model,
                            output_chars=output_chars, retrieval_tokens=tool["output_tokens"], error=errored,
                            native_type="message", native_subtype="tool_result",
                        ))
                text = bubble.get("text")
                if isinstance(text, str) and text.strip():
                    assistant_text = compact_text(text, 84)
                    trace.append(trace_event(ts, "message", "Assistant message", assistant_text,
                                             idx, model=model, native_type="message",
                                             native_subtype="agent_message"))
                for block in bubble.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and str(block.get("text") or "").strip():
                        assistant_text = compact_text(str(block.get("text")), 84)
                        trace.append(trace_event(ts, "message", "Assistant message", assistant_text,
                                                 idx, model=model, native_type="message",
                                                 native_subtype="agent_message"))
                    elif block.get("type") == "tool_use":
                        ident = cursor_tool_identity(block.get("name"))
                        arguments = block.get("input") or {}
                        tool = {
                            **ident, "id": block.get("id"), "call_id": block.get("id"),
                            "args_chars": len(str(arguments or "")),
                            "args_fingerprint": argument_fingerprint(arguments),
                            "output_chars": 0, "output_tokens": 0,
                            "result_available": False, "error": False,
                            "skills": skill_names_from_value(arguments, block.get("name")),
                        }
                        tools.append(tool)
                        trace.append(trace_event(ts, "tool_call", ident["display"], ident["namespace"],
                                                 idx, tool=ident["name"], severity="tool", model=model,
                                                 args_chars=tool["args_chars"], tool_kind="tool",
                                                 native_type="message",
                                                 native_subtype="tool_call"))
    
            if timing.get("retries"):
                trace.append(trace_event(
                    end_ts, "retry", "Request retried",
                    f"{timing['attempts']} attempts · {timing['failed_attempts']} failed",
                    idx, severity="warn", model=model, attempts=timing["attempts"],
                    failed_attempts=timing["failed_attempts"], retries=timing["retries"],
                    native_type="request", status="failed",
                ))
            trace.append(trace_event(
                end_ts, "complete", "Execution complete",
                duration_label(timing.get("wait_s") or 0) if timing.get("wait_s") else "",
                idx, severity="good" if group.get("completed") else "neutral", model=model,
                duration_ms=(timing.get("wait_s") or 0) * 1000 or None,
                cost=execution_cost if cost_available else None,
                native_type="request",
                status="completed" if group.get("completed") else "unknown",
            ))
    
            retrieval = sum(int(tool.get("output_tokens") or 0) for tool in tools)
            context_pct = context_tokens / context_window if context_window else None
            output_available = bool(bubbles)
            token_available = bool(context_tokens or output_available)
            execution_availability = metric_availability(
                "cursor", cost=cost_available, tokens=token_available,
                input_tokens=bool(context_tokens), output_tokens=output_available,
                throughput=bool(output_tokens and timing.get("active_s")),
                context=bool(context_window), timing=bool(timing.get("wait_s")),
                tool_results=any(tool.get("result_available") for tool in tools),
            )
            series.append({
                "i": idx, "in": context_tokens, "out": output_tokens, "cost": execution_cost,
                "fresh_input": context_tokens, "cache": 0, "cache_read": 0, "cache_write": 0,
                "think": bool(reasoning_tokens or reasoning_ms), "tools": len(tools), "side": False,
                "reasoning": reasoning_tokens, "reasoning_ms": reasoning_ms,
                "context_pct": context_pct, "context_tokens": context_tokens,
                "user_message": user_text, "user_input": user_text,
                "availability": execution_availability,
            })
            execution = {
                "id": f"{source['id']}:{idx}", "idx": idx, "ts": end_ts or start_ts,
                "time": local_tm(end_ts or start_ts), "model": model,
                "tokens": {"input": context_tokens, "output": output_tokens,
                           "reasoning": reasoning_tokens, "retrieval": retrieval,
                           "fresh_input": context_tokens, "cache": 0, "cache_read": 0,
                           "cache_write": 0, "total": context_tokens + output_tokens},
                "cost": execution_cost, "cost_breakdown": cost_breakdown, "tools": tools,
                "tool_count": len(tools), "model_calls": model_calls,
                "reasoning_tokens": reasoning_tokens, "reasoning_duration_ms": reasoning_ms,
                "context_tokens": context_tokens, "context_window": context_window,
                "context_interpolated": bool(context_row.get("interpolated")),
                "context_pct": context_pct, "duration_ms": (timing.get("active_s") or 0) * 1000 or None,
                "wait_duration_ms": (timing.get("wait_s") or 0) * 1000 or None,
                "ttft_ms": (timing.get("ttft_s") or 0) * 1000 or None,
                "attempts": timing.get("attempts") or 0,
                "failed_attempts": timing.get("failed_attempts") or 0,
                "retries": timing.get("retries") or 0,
                "timing_basis": timing.get("timing_basis"), "pricing_variant": variant,
                "summary": (f"Execution {idx}: {len(tools)} tools · ${execution_cost:.3f} local estimate"
                            if cost_available else f"Execution {idx}: {len(tools)} tools · cost unavailable"),
                "user_message": user_text, "user_input": user_text,
                "availability": execution_availability,
                "completed": bool(group.get("completed")),
            }
            executions.append(execution)
            tot["input"] += context_tokens
            tot["output"] += output_tokens
            if cost_available:
                for key in cost:
                    cost[key] += float(cost_breakdown.get(key) or 0)
            model_tok[model] += context_tokens + output_tokens
            model_cost[model] += execution_cost
            if timing.get("wait_s"):
                wait_samples.append({
                    "provider": "cursor", "model": model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
                    "ts": end_ts, "start_ts": start_ts, "duration_s": timing["wait_s"],
                    "tool_calls": len(tools), "output_tokens": output_tokens,
                    "context_tokens": context_tokens, "model_calls": model_calls,
                    "timing_basis": timing.get("timing_basis") or "observed",
                    "ttft_s": timing.get("ttft_s") or 0,
                    "attempts": timing.get("attempts") or 0,
                    "failed_attempts": timing.get("failed_attempts") or 0,
                    "retries": timing.get("retries") or 0,
                })
            if output_tokens and timing.get("active_s"):
                performance_samples.append({
                    "provider": "cursor", "model": model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
                    "ts": end_ts, "input_tokens": context_tokens, "output_tokens": output_tokens,
                    "peak_input_tokens": context_tokens, "uncached_input_tokens": context_tokens,
                    "cache_read_tokens": 0, "cache_write_tokens": 0, "model_calls": model_calls,
                    "duration_s": timing["active_s"], "generation_s": timing["active_s"],
                    "ttft_s": timing.get("ttft_s") or 0, "tool_calls": len(tools),
                    "timing_basis": timing.get("timing_basis") or "observed",
                })
            if start_ts:
                first_ts = min(first_ts or start_ts, start_ts)
            if end_ts:
                last_ts = max(last_ts, end_ts)
    
        total_tokens = sum(tot.values())
        total_cost = sum(cost.values())
        tool_data = tool_summary(executions)
        model_names = [execution.get("model") or "unknown" for execution in executions]
        primary_model = max(set(model_names), key=model_names.count) if model_names else source.get("model") or "unknown"
        reasoning_total = sum(int(execution.get("reasoning_tokens") or 0) for execution in executions)
        reasoning_cost = sum(
            float((execution.get("cost_breakdown") or {}).get("output") or 0)
            * int(execution.get("reasoning_tokens") or 0)
            / max(1, int((execution.get("tokens") or {}).get("output") or 0))
            for execution in executions
        )
        analyses = analysis_block(
            tot, total_cost, reasoning_total,
            sum(bool(execution.get("reasoning_tokens") or execution.get("reasoning_duration_ms"))
                for execution in executions),
            reasoning_cost, model_tok, model_cost, tool_data, 0.0, 0,
            sum(bool(group.get("completed")) for group in groups),
        )
        active_s = _merge_execution_intervals(active_intervals)
        if not active_s:
            active_s = sum(float(execution.get("duration_ms") or 0) / 1000.0 for execution in executions)
        source = dict(source)
        primary_variant = cursor_price_variant(composer, primary_model)
        primary_price, _ = price_for(primary_model, "cursor", primary_variant)
        pricing_supported = any(float(value or 0) > 0 for value in primary_price.values())
        pricing_note = cursor_pricing_note(primary_model, primary_variant, pricing_supported)
        source.update({
            "context_latest": latest_context or (executions[-1].get("context_tokens") if executions else 0),
            "context_window": latest_window or max((e.get("context_window") or 0 for e in executions), default=0),
            "context_breakdown": cursor_prompt_breakdown(composer),
            "token_estimate": True,
            "estimate_basis": "context_proxy_and_visible_output",
            "pricing_variant": primary_variant,
        })
        input_available = bool(executions and all(metric_available(e, "input_tokens") for e in executions))
        output_available = bool(executions and all(metric_available(e, "output_tokens") for e in executions))
        cost_available = bool(executions and all(metric_available(e, "cost") for e in executions))
        throughput = performance_summary(performance_samples, tot["output"])
        availability = metric_availability(
            "cursor", cost=cost_available, tokens=bool(input_available or output_available),
            input_tokens=input_available, output_tokens=output_available,
            throughput=bool(throughput.get("available")),
            context=bool(source["context_window"]), timing=bool(active_s or wait_samples),
            tool_results=any(tool.get("result_available") for e in executions for tool in e.get("tools") or []),
        )
        biggest = max(
            ({"cost": execution.get("cost") or 0, "idx": execution.get("idx")} for execution in executions),
            key=lambda row: row["cost"], default=None,
        )
        state = build_state(
            source, tot, cost, total_tokens, total_cost, series, executions, trace,
            {"reasoning": reasoning_total, "output": max(0, tot["output"] - reasoning_total),
             "retrieval": tool_data["total_output_tokens"], "coordination": 0},
            analyses, [], first_ts, last_ts,
            (time.time() - last_ts) if last_ts else 1e9, biggest, 0, cost_available,
            primary_model, pricing_note,
            {"duration_s": active_s, "available": bool(active_s),
             "reported_executions": sum(bool(e.get("duration_ms")) for e in executions),
             "observed_executions": 0, "execution_count": len(executions),
             "basis": "request trace" if spans else ("turn duration" if active_s else "unavailable")},
            wait_samples, availability=availability,
        )
        state["throughput"] = throughput
        state["token_estimate"] = True
        state["estimation"] = {
            "basis": "one context snapshot per execution plus trace-visible model text",
            "input": "context proxy",
            "output": "visible text estimate",
            "excluded": ["cache accounting", "hidden reasoning", "repeated internal model-call input"],
        }
        state["context"]["breakdown"] = source.get("context_breakdown") or []
        state["context"]["estimated"] = True
        state["cursor_enrichment"] = {
            "database": enriched,
            "request_traces": bool(spans),
            "transcript_fallback": not enriched,
        }
        return state

    def summarize_legacy(self, source, objs=None):
        """Build a cross-session Cursor row from the same local-estimate contract."""
        compat = self._require_compatibility()
        CURRENT_SESSION_CONTEXT_SAMPLES = compat["context_sample_limit"]
        metric_availability = compat["metric_availability"]
        recompute = compat["recompute"]
        summarize_tool_evidence = compat["summarize_tool_evidence"]
        summary_row = compat["summary_row"]
        usage_provenance = compat["usage_provenance"]
        state = recompute(source)
        if not state:
            availability = metric_availability("cursor")
            return summary_row(
                source, source.get("title"), 0.0, 0, 0, set(), None, None,
                {}, {}, {}, False, availability=availability,
            )
        executions = state.get("executions") or []
        availability = state.get("availability") or metric_availability("cursor")
        model_stats = {}
        model_daily = {}
        model_cost = defaultdict(float)
        model_tok = defaultdict(int)
        day_cost = defaultdict(float)
        performance_samples = []
        wait_samples = []

        def merge_execution_availability(target, evidence):
            target_availability = target.setdefault("availability", {})
            for metric, value in evidence.items():
                target_availability[metric] = (
                    target_availability.get(metric) is True or value is True
                )

        for execution in executions:
            model = execution.get("model") or "unknown"
            token_data = execution.get("tokens") or {}
            execution_availability = execution.get("availability") or availability
            input_available = execution_availability.get("input_tokens") is True
            output_available = execution_availability.get("output_tokens") is True
            input_tokens = int(token_data.get("input") or 0) if input_available else 0
            output_tokens = int(token_data.get("output") or 0) if output_available else 0
            reasoning_tokens = max(0, min(
                output_tokens, int(execution.get("reasoning_tokens") or 0)
            ))
            execution_cost = float(execution.get("cost") or 0)
            stats = model_stats.setdefault(model, {
                "cost": 0.0, "tokens": 0, "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "reasoning_tokens": 0,
                "reasoning_output_tokens": 0, "reasoning_executions": 0,
                "reasoning_unavailable_executions": 0,
                "token_covered_executions": 0,
                "io_covered_executions": 0,
                "io_covered_input_tokens": 0, "io_covered_output_tokens": 0,
                "cost_covered_executions": 0,
                "cost_covered_output_tokens": 0, "executions": 0,
                "cost_covered_cost": 0.0,
                "availability": {},
            })
            merge_execution_availability(stats, execution_availability)
            stats["cost"] += execution_cost
            stats["tokens"] += input_tokens + output_tokens
            stats["input_tokens"] += input_tokens
            stats["output_tokens"] += output_tokens
            if output_available:
                stats["reasoning_tokens"] += reasoning_tokens
                stats["reasoning_output_tokens"] += output_tokens
                stats["reasoning_executions"] += 1
                stats["token_covered_executions"] += 1
            else:
                stats["reasoning_unavailable_executions"] += 1
            if input_available and output_available:
                stats["io_covered_executions"] += 1
                stats["io_covered_input_tokens"] += input_tokens
                stats["io_covered_output_tokens"] += output_tokens
            if execution_availability.get("cost") is True and output_available:
                stats["cost_covered_executions"] += 1
                stats["cost_covered_output_tokens"] += output_tokens
                stats["cost_covered_cost"] += execution_cost
            stats["executions"] += 1
            model_cost[model] += execution_cost
            model_tok[model] += input_tokens + output_tokens
            ts = float(execution.get("ts") or 0)
            if ts:
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                day_cost[day] += execution_cost
                daily = model_daily.setdefault((model, day), {
                    "model": model, "day": day, "cost": 0.0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "reasoning_tokens": 0, "reasoning_output_tokens": 0,
                    "reasoning_executions": 0,
                    "reasoning_unavailable_executions": 0,
                    "token_covered_executions": 0,
                    "io_covered_executions": 0,
                    "io_covered_input_tokens": 0, "io_covered_output_tokens": 0,
                    "cost_covered_executions": 0,
                    "cost_covered_output_tokens": 0, "executions": 0,
                    "cost_covered_cost": 0.0,
                    "availability": {},
                })
                merge_execution_availability(daily, execution_availability)
                daily["cost"] += execution_cost
                daily["input_tokens"] += input_tokens
                daily["output_tokens"] += output_tokens
                if output_available:
                    daily["reasoning_tokens"] += reasoning_tokens
                    daily["reasoning_output_tokens"] += output_tokens
                    daily["reasoning_executions"] += 1
                    daily["token_covered_executions"] += 1
                else:
                    daily["reasoning_unavailable_executions"] += 1
                if input_available and output_available:
                    daily["io_covered_executions"] += 1
                    daily["io_covered_input_tokens"] += input_tokens
                    daily["io_covered_output_tokens"] += output_tokens
                if execution_availability.get("cost") is True and output_available:
                    daily["cost_covered_executions"] += 1
                    daily["cost_covered_output_tokens"] += output_tokens
                    daily["cost_covered_cost"] += execution_cost
                daily["executions"] += 1
            duration_s = float(execution.get("duration_ms") or 0) / 1000.0
            if output_tokens and duration_s:
                performance_samples.append({
                    "provider": "cursor", "model": model,
                    "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
                    "ts": ts, "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "peak_input_tokens": int(execution.get("context_tokens") or input_tokens),
                    "uncached_input_tokens": input_tokens, "cache_read_tokens": 0,
                    "cache_write_tokens": 0, "model_calls": int(execution.get("model_calls") or 0),
                    "duration_s": duration_s, "generation_s": duration_s,
                    "ttft_s": float(execution.get("ttft_ms") or 0) / 1000.0,
                    "tool_calls": int(execution.get("tool_count") or 0),
                    "timing_basis": execution.get("timing_basis") or "observed",
                })
                attempts = int(execution.get("attempts") or 0)
                if attempts > 0:
                    performance_samples[-1].update({
                        "attempts": attempts,
                        "retries": int(execution.get("retries") or 0),
                        "failed_attempts": int(execution.get("failed_attempts") or 0),
                    })
        for sample in (state.get("wait_time") or {}).get("samples") or []:
            ts = float(sample.get("ts") or 0)
            wait_samples.append({
                **sample,
                "provider": "cursor",
                "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            })
        first_ts = float((state.get("timing") or {}).get("start_ts") or 0) or None
        last_ts = float((state.get("timing") or {}).get("end_ts") or 0) or None
        active = {
            "duration_s": float((state.get("timing") or {}).get("duration_s") or 0),
            "available": bool((state.get("timing") or {}).get("duration_available")),
            "basis": (state.get("timing") or {}).get("duration_basis") or "unavailable",
        }
        row = summary_row(
            source, source.get("title"), float(state.get("total_cost") or 0),
            int(state.get("total_tokens") or 0), len(executions), set(model_stats),
            first_ts, last_ts, model_cost, model_tok, day_cost, bool(state.get("cost_approx")), active,
            int((state.get("tokens") or {}).get("input") or 0),
            int((state.get("tokens") or {}).get("output") or 0),
            model_stats, list(model_daily.values()), performance_samples, wait_samples,
            availability,
        )
        row["token_estimate"] = bool(state.get("token_estimate"))
        row["provenance"] = usage_provenance([row])
        row["usage_basis"] = row["provenance"]["usage_basis"]
        calls = []
        for execution in executions:
            for tool in execution.get("tools") or []:
                calls.append({**tool, "ts": execution.get("ts") or 0})
        row["_tool_evidence"] = summarize_tool_evidence(calls)
        row["context"] = state.get("context") or {}
        row["_context_samples"] = [
            int(execution.get("context_tokens") or
                (execution.get("tokens") or {}).get("input") or 0)
            for execution in executions[-CURRENT_SESSION_CONTEXT_SAMPLES:]
            if int(execution.get("context_tokens") or
                   (execution.get("tokens") or {}).get("input") or 0) > 0
        ]
        row["primary_model"] = state.get("primary_model") or source.get("model") or "unknown"
        row["terminal"] = False
        row["tool_calls"] = int((state.get("tools") or {}).get("total_calls") or 0)
        row["tool_errors"] = int((state.get("tools") or {}).get("total_errors") or 0)
        row["_agent_records"] = [self._agent_record(source, state, row)]
        return row

    @staticmethod
    def _agent_record(source, state, row):
        executions = state.get("executions") or []
        availability = state.get("availability") or {}
        tokens = state.get("tokens") or {}
        timing = state.get("timing") or {}
        start_ts = float(timing.get("start_ts") or 0) or None
        last_ts = float(timing.get("end_ts") or 0) or None
        terminal = bool(executions and executions[-1].get("completed"))
        last_activity = last_ts or float(source.get("mtime") or 0) or None
        activity_state = (
            "complete" if terminal
            else "working" if last_activity and time.time() - last_activity <= 90
            else "incomplete"
        )
        spawned = source.get("agent_kind") == "spawned"
        return {
            "id": source.get("agent_id") or cursor_agent_id(source.get("id")),
            "parent_id": source.get("agent_parent_id") if spawned else None,
            "session_id": source.get("id"),
            "runtime": "cursor",
            "client": "Cursor",
            "kind": "spawned" if spawned else "root",
            "depth": None,
            "label": "",
            "role": source.get("agent_role") or None,
            "model": row.get("primary_model") or source.get("model") or "unknown",
            "started_at": start_ts,
            "ended_at": last_ts if terminal else None,
            "last_activity_at": last_activity,
            "activity_state": activity_state,
            "input_tokens": int(tokens.get("input") or 0),
            "output_tokens": int(tokens.get("output") or 0),
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": sum(
                int(execution.get("reasoning_tokens") or 0) for execution in executions
            ),
            "tokens": int(state.get("total_tokens") or 0),
            "tokens_available": availability.get("tokens") is True,
            "cost": float(state.get("total_cost") or 0),
            "cost_available": availability.get("cost") is True,
            "executions": len(executions),
            "attempts": sum(int(execution.get("attempts") or 0) for execution in executions),
            "retries": sum(int(execution.get("retries") or 0) for execution in executions),
            "failed_attempts": sum(
                int(execution.get("failed_attempts") or 0) for execution in executions
            ),
            "tool_calls": int(row.get("tool_calls") or 0),
            "work_time_s": (
                float(timing.get("duration_s") or 0)
                if timing.get("duration_available") else None
            ),
        }

    def deletion_plan(self, source):
        if isinstance(source, SessionSource) and source.locator.kind == "transcript":
            return DeletionPlan(
                DeletionDisposition.TRASH,
                "Move the session transcript to Trash; the shared database stays read-only.",
                (source.locator,),
            )
        return DeletionPlan.deny("Cursor's shared database stays read-only.")


class CursorRuntimeAdapterProxy:
    """Keep one registry entry while runtime paths can change in tests/config."""

    descriptor = CursorRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Cursor adapter")
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

    def deletion_plan(self, source):
        return self._adapter().deletion_plan(source)
