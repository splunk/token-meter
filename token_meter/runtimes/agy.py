"""Native read-only adapter for Antigravity (AGY) JSONL session evidence."""

import glob
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from token_meter.contracts import (
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
from token_meter.domain.timing import merge_execution_intervals, performance_summary
from token_meter.domain.usage import cost_breakdown_values, format_usd
from token_meter.models.pricing import long_context_multipliers


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000
MAX_SOURCES = 2_000
MAX_TURNS = 2_000
MAX_TOOLS = 2_000
CHARS_PER_TOKEN = 4

_SETTINGS_BLOCK_RE = re.compile(
    r"<USER_SETTINGS_CHANGE>([\s\S]*?)</USER_SETTINGS_CHANGE>",
    re.IGNORECASE,
)
_MODEL_SELECTION_RE = re.compile(
    r"changed setting `Model Selection` from [^\n`]*?to\s+([^\n`<]+?)(?:\.(?:\s|$)|\s*$)",
    re.IGNORECASE,
)
_QUALIFIER_SUFFIX_RE = re.compile(r"\s*\(([^)]+)\)\s*$")
# Model ids read from a model-selection setting must look like a released model name.
_SELECTED_MODEL_RE = re.compile(
    r"(?:gemini-\d+(?:\.\d+)?|gpt-\d+(?:\.\d+)?|gpt-oss-\d+b"
    r"|claude-(?:opus|sonnet|haiku|fable|mythos)-\d+(?:-\d+)?"
    r"|claude-\d+(?:-\d+)?-(?:opus|sonnet|haiku)|o\d)"
    r"(?:-(?:pro|flash|lite|preview|mini|nano|codex|max|argon|sol|terra|luna|thinking|exp))*"
)
_SUMMARY_TABLE = "conversation_summaries"
_SUMMARY_COLUMNS = frozenset(("conversation_id", "workspace_uris", "last_modified_time"))
_SUBAGENT_URI_RE = re.compile(
    r"brain/([A-Za-z0-9_-]+)/\.system_generated/logs/transcript\.jsonl",
    re.IGNORECASE,
)


def default_brain_dir(home=None, environment=None):
    """Return Antigravity's local brain directory without a platform dependency."""
    environment = os.environ if environment is None else dict(environment)
    override = (
        environment.get("AGY_BRAIN_DIR")
        or environment.get("ANTIGRAVITY_BRAIN_DIR")
        or ""
    ).strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    agy_home = (
        environment.get("AGY_HOME")
        or environment.get("ANTIGRAVITY_HOME")
        or ""
    ).strip()
    if agy_home:
        return os.path.abspath(os.path.expanduser(os.path.join(agy_home, "brain")))
    base_home = os.path.expanduser(str(home or "~"))
    return os.path.abspath(os.path.join(base_home, ".gemini", "antigravity", "brain"))


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


def _token_estimate(characters):
    return int(math.ceil(max(0, int(characters or 0)) / CHARS_PER_TOKEN))


def agy_agent_id(session_id):
    """Return a stable public agent ID without exposing raw session identity."""
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(
        b"token-meter:agy-agent:v1\0" + raw.encode("utf-8", "replace")
    ).hexdigest()
    return "agy-agent-" + digest[:24]


def _safe_agent_name(value, *, nickname=False):
    """Keep a short subagent type or name, never prose or credential-shaped text."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text or len(text) > 64:
        return ""
    # Lowercase letter words only: digits and mixed case are how credentials look.
    if re.fullmatch(r"[A-Za-z][a-z]{0,31}(?:[_-][a-z]{1,31}){0,3}", text):
        return text
    if nickname and re.fullmatch(r"[A-Z][a-z]{1,23}(?: [A-Z][a-z]{1,23}){1,2}", text):
        return text
    return ""


def _normalize_model(value):
    raw = str(value or "").strip()
    if not raw:
        return "unknown-model"
    # Strip trailing UI effort/mode qualifiers such as "(High)" or "(Thinking)".
    cleaned = _QUALIFIER_SUFFIX_RE.sub("", raw).strip().lower()
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", cleaned).strip("-")
    if cleaned.startswith("claude-"):
        cleaned = re.sub(r"(?<=\d)\.(?=\d)", "-", cleaned)
    if not cleaned or len(cleaned) > 96:
        return "unknown-model"
    return cleaned


def _extract_model_and_effort(text):
    """Extract normalized model ID and optional effort from a settings change block."""
    if not isinstance(text, str) or "<USER_SETTINGS_CHANGE>" not in text:
        return "", ""
    blocks = _SETTINGS_BLOCK_RE.findall(text)
    if not blocks:
        return "", ""
    matches = _MODEL_SELECTION_RE.findall("\n".join(blocks))
    if not matches:
        return "", ""
    raw_label = matches[-1].strip().rstrip(".")
    effort = ""
    qualifier_match = _QUALIFIER_SUFFIX_RE.search(raw_label)
    if qualifier_match:
        qual = qualifier_match.group(1).strip().lower()
        if qual in ("low", "medium", "high", "thinking"):
            effort = qual
    model = _normalize_model(raw_label)
    if not _SELECTED_MODEL_RE.fullmatch(model):
        # A selection was made but is not a recognizable model name: the
        # model is unknown from here on, not the previously selected one.
        return "unknown-model", ""
    return model, effort


def model_ref_for(provider, model):
    """Resolve AGY's model identity to its canonical model provider."""
    provider = str(provider or "").strip().lower()
    model_id = _normalize_model(model)
    if provider in ("anthropic", "openai", "google", "amazon"):
        model_provider = provider
    elif model_id.startswith("claude-"):
        model_provider = "anthropic"
    elif model_id.startswith(("gpt-", "o1", "o3", "o4")):
        model_provider = "openai"
    elif model_id.startswith("gemini-"):
        model_provider = "google"
    elif model_id.startswith(("amazon.", "amazon-", "nova-")):
        model_provider = "amazon"
    else:
        model_provider = "unknown-model-provider"
    return ModelRef(model_provider, model_id)


def _normalize_tool_name(value):
    value = str(value or "tool").strip()
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").lower()[:64]
    return value or "tool"


def _tool_category(name):
    name = str(name or "").lower()
    if any(part in name for part in ("command", "shell", "bash", "terminal", "exec", "manage_task")):
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


def _is_internal_agy_path(path):
    normalized = str(path or "").replace("\\", "/")
    if not (normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)):
        return True
    lowered = normalized.lower()
    return (
        "/.gemini/antigravity" in lowered
        or lowered.startswith(("/tmp/", "/private/tmp/", "/var/folders/"))
    )


def _uri_to_path(uri):
    raw = str(uri or "").strip()
    if not raw.startswith("file://"):
        return ""
    parsed = urlparse(raw)
    path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        return "//" + parsed.netloc + path
    if re.match(r"^/[A-Za-z]:/", path):
        return path[1:]
    return path


def _workspace_from_uris(uris):
    """Return the first explicit Antigravity workspace URI as a path, without I/O."""
    if not isinstance(uris, list):
        return ""
    for uri in uris[:8]:
        path = _uri_to_path(uri)
        if path and not _is_internal_agy_path(path):
            return os.path.normpath(path)
    return ""


def _summary_authorizer(action, arg1, arg2, database, trigger):
    del database, trigger
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return (
            sqlite3.SQLITE_OK
            if arg1 == _SUMMARY_TABLE and arg2 in _SUMMARY_COLUMNS
            else sqlite3.SQLITE_DENY
        )
    return sqlite3.SQLITE_DENY


def _wait_sample(turn, usage, tool_count):
    duration = max(0.0, turn["end"] - turn["start"])
    return {
        "provider": "agy",
        "model": turn["model"],
        "day": time.strftime("%Y-%m-%d", time.localtime(turn["end"])),
        "ts": turn["end"],
        "start_ts": turn["start"],
        "duration_s": duration,
        "generation_s": duration,
        "ttft_s": 0.0,
        "tool_calls": tool_count,
        "model_calls": 1,
        "output_tokens": usage["output_tokens"],
        "input_tokens": (
            usage["input_tokens"]
            + usage["cache_read_input_tokens"]
            + usage["cache_creation_input_tokens"]
        ),
        "uncached_input_tokens": usage["input_tokens"],
        "cache_read_tokens": usage["cache_read_input_tokens"],
        "cache_write_tokens": usage["cache_creation_input_tokens"],
        "peak_input_tokens": turn["context_tokens"],
        "context_tokens": turn["context_tokens"],
        "timing_basis": "inferred",
    }


class AgyRuntimeAdapter:
    """Discover only Antigravity-owned JSONL transcript files and expose no message content."""

    descriptor = RuntimeDescriptor(
        "agy",
        "Antigravity",
        frozenset(("sessions", "models", "tools")),
        "runtime.generic",
        "runtime-neutral",
        None,
    )

    def __init__(self, brain_dir=None, project_resolver=None, quote_resolver=None,
                 compatibility=None, path_cache=None):
        resolved_brain = brain_dir or default_brain_dir()
        self.brain_dir = Path(os.path.abspath(os.path.expanduser(str(resolved_brain))))
        self.project_resolver = project_resolver or (lambda value: value)
        self.quote_resolver = quote_resolver
        self.compatibility = dict(compatibility or {})
        self.path_cache = path_cache
        self._metadata_cache = {}
        self._summary_cache = (None, {})
        self._descriptor_cache = {}

    def _glob(self, pattern):
        if self.path_cache is not None:
            return self.path_cache.paths(pattern)
        return tuple(glob.glob(pattern))

    def _owned_path(self, path):
        path = os.path.realpath(os.path.abspath(os.path.expanduser(str(path or ""))))
        root = os.path.realpath(str(self.brain_dir))
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    def _paths(self):
        patterns = (
            str(self.brain_dir / "*" / ".system_generated" / "logs" / "transcript.jsonl"),
            str(self.brain_dir / "*" / "transcript.jsonl"),
        )
        by_conversation = {}
        # The .system_generated layout is listed first and wins over a legacy copy.
        for pattern in patterns:
            for path in self._glob(pattern):
                if os.path.isfile(path) and self._owned_path(path) and not os.path.islink(path):
                    path = os.path.abspath(path)
                    by_conversation.setdefault(self._conversation_dir_and_id(path)[1], path)
        newest = sorted(
            by_conversation.values(), key=lambda path: (-_mtime(path), path),
        )[:MAX_SOURCES]
        return tuple(sorted(newest))

    @staticmethod
    def _conversation_dir_and_id(path):
        resolved = Path(path)
        if (
            resolved.parent.name == "logs"
            and resolved.parent.parent.name == ".system_generated"
        ):
            conv_dir = resolved.parent.parent.parent
        else:
            conv_dir = resolved.parent
        return conv_dir, conv_dir.name

    def _subagent_index(self):
        """Map child conversation IDs to their parent conversation and role metadata."""
        pattern = str(self.brain_dir / "*" / ".system_generated" / "subagents" / "*.json")
        meta_paths = [
            path for path in self._glob(pattern)
            if os.path.isfile(path) and self._owned_path(path) and not os.path.islink(path)
        ]
        meta_paths = sorted(meta_paths, key=lambda path: (-_mtime(path), path))[:MAX_SOURCES]
        children = {}
        for meta_path in meta_paths:
            signature = _file_signature(meta_path)
            cached = self._descriptor_cache.get(meta_path)
            if cached and cached[0] == signature:
                data = cached[1]
            else:
                data = _read_json(meta_path)
                self._descriptor_cache[meta_path] = (signature, data)
                if len(self._descriptor_cache) > MAX_SOURCES:
                    self._descriptor_cache.pop(next(iter(self._descriptor_cache)), None)
            if not data:
                continue
            parent_dir = Path(meta_path).parent.parent.parent
            parent_id = parent_dir.name
            child_id = str(data.get("conversationId") or Path(meta_path).stem).strip()
            descriptor = (
                data.get("subagentDescriptor")
                if isinstance(data.get("subagentDescriptor"), dict) else {}
            )
            role = _safe_agent_name(descriptor.get("typeName"))
            label = _safe_agent_name(descriptor.get("role"), nickname=True)
            workspace = _workspace_from_uris(data.get("workspaceUris"))
            if child_id:
                children[child_id] = {
                    "parent_id": parent_id,
                    "role": role or None,
                    "label": label or "",
                    "workspace": workspace,
                }
        return children

    def _summary_database_path(self):
        return self.brain_dir.parent / "conversation_summaries.db"

    def _summary_connection(self, path):
        uri = path.resolve().as_uri()
        con = sqlite3.connect(uri + "?mode=ro", uri=True)
        try:
            con.execute("SELECT 1").fetchone()
        except sqlite3.OperationalError:
            con.close()
            # Hosts that deny the read lock still allow an immutable snapshot read.
            con = sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            con.close()
            raise
        con.set_authorizer(_summary_authorizer)
        return con

    def _summary_workspaces(self):
        """Map conversation IDs to Antigravity's own recorded workspace, read-only."""
        path = self._summary_database_path()
        signature = (*_file_signature(str(path)), *_file_signature(str(path) + "-wal"))
        if signature[:2] == ("0", "0"):
            return {}
        if self._summary_cache[0] == signature:
            return self._summary_cache[1]
        workspaces = {}
        try:
            con = self._summary_connection(path)
            try:
                rows = con.execute(
                    "SELECT conversation_id, workspace_uris FROM conversation_summaries "
                    "WHERE workspace_uris IS NOT NULL "
                    "ORDER BY last_modified_time DESC LIMIT ?",
                    (MAX_SOURCES,),
                ).fetchall()
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            # Unreadable now (locked, corrupt): no projects, and retry on the next discovery.
            return {}
        for conversation_id, raw_uris in rows:
            try:
                uris = json.loads(raw_uris) if isinstance(raw_uris, str) else None
            except ValueError:
                continue
            workspace = _workspace_from_uris(uris)
            if conversation_id and workspace:
                workspaces[str(conversation_id)] = workspace
        self._summary_cache = (signature, workspaces)
        return workspaces

    def _metadata(self, path, subagent_children=None, summary_workspaces=None):
        signature = _file_signature(path)
        cached = self._metadata_cache.get(path)
        if cached and cached[0] == signature:
            base = dict(cached[1]) if cached[1] else None
        else:
            rows, corrupt, available, truncated = _read_jsonl(path)
            conv_dir, conv_id = self._conversation_dir_and_id(path)
            oversized = truncated and not available
            if (not rows and not corrupt and not oversized) or not conv_id:
                base = None
            else:
                model = provider = effort = ""
                has_planner_step = False
                has_measured_tokens = False
                pending_subagent_calls = []
                spawned_children = {}
                for row in rows:
                    row_type = row.get("type")
                    if row_type == "USER_INPUT":
                        extracted_model, extracted_effort = _extract_model_and_effort(
                            row.get("content")
                        )
                        if extracted_model:
                            model = extracted_model
                        if extracted_effort:
                            effort = extracted_effort
                    elif row_type == "PLANNER_RESPONSE":
                        has_planner_step = True
                        if (
                            _integer(row.get("input_tokens")) is not None
                            and _integer(row.get("output_tokens")) is not None
                        ):
                            has_measured_tokens = True
                        raw_model = row.get("model") or row.get("modelId")
                        if raw_model:
                            model = _normalize_model(raw_model)
                        raw_provider = row.get("provider")
                        if raw_provider:
                            provider = str(raw_provider).strip().lower()
                        raw_calls = row.get("tool_calls")
                        if isinstance(raw_calls, list):
                            for call in raw_calls:
                                if not isinstance(call, dict):
                                    continue
                                if _normalize_tool_name(call.get("name")) == "invoke_subagent":
                                    args = (
                                        call.get("args")
                                        if isinstance(call.get("args"), dict)
                                        else call.get("arguments")
                                    )
                                    if isinstance(args, dict):
                                        pending_subagent_calls.append({
                                            "role": _safe_agent_name(
                                                args.get("TypeName") or args.get("typeName")
                                            ) or None,
                                            "label": _safe_agent_name(
                                                args.get("Role") or args.get("role"), nickname=True
                                            ) or "",
                                        })
                    elif row_type == "INVOKE_SUBAGENT":
                        content = str(row.get("content") or "")
                        call_meta = (
                            pending_subagent_calls.pop(0)
                            if pending_subagent_calls else {}
                        )
                        for child_id in _SUBAGENT_URI_RE.findall(content):
                            if child_id == conv_id:
                                continue
                            spawned_children[child_id] = {
                                "parent_id": conv_id,
                                "role": call_meta.get("role"),
                                "label": call_meta.get("label") or "",
                                "workspace": "",
                            }
                if not has_planner_step and not corrupt and not oversized:
                    base = None
                else:
                    model_ref = model_ref_for(provider, model)
                    base = {
                        "provider": "agy",
                        "client": "agy",
                        "label": "Antigravity",
                        "runtime": "Antigravity",
                        "id": conv_id,
                        "session": conv_id,
                        "path": path,
                        "_spawned_children": spawned_children,
                        "mtime": _mtime(path),
                        "signature_mtime": _mtime(path),
                        "title": "Antigravity session",
                        "model": model_ref.model_id,
                        "model_provider": model_ref.provider_id,
                        "reasoning_effort": effort,
                        "token_estimate": bool(has_planner_step and not has_measured_tokens),
                        "estimate_basis": (
                            "transcript_chars (~4 chars/token) + public model rates"
                            if (has_planner_step and not has_measured_tokens) else ""
                        ),
                        "source_kind": "agy_jsonl",
                        "corrupt": corrupt,
                        "available": available,
                        "truncated": truncated,
                    }
            self._metadata_cache[path] = (signature, base)
            if len(self._metadata_cache) > MAX_SOURCES:
                self._metadata_cache.pop(next(iter(self._metadata_cache)), None)
            base = dict(base) if base else None

        if base is None:
            return None

        conv_id = base["id"]
        child_meta = (subagent_children or {}).get(conv_id)
        raw_project = (
            (summary_workspaces or {}).get(conv_id)
            or (child_meta or {}).get("workspace")
            or ""
        )
        base["project"] = self.project_resolver(raw_project) if raw_project else ""
        base["agent_id"] = agy_agent_id(conv_id)
        base["agent_kind"] = "root"
        base["agent_parent_id"] = None
        base["agent_role"] = None
        base["agent_label"] = ""
        return base

    def _legacy_records(self):
        subagent_children = self._subagent_index()
        summary_workspaces = self._summary_workspaces()
        records = [
            self._metadata(path, subagent_children, summary_workspaces)
            for path in self._paths()
        ]
        records = [record for record in records if record is not None]
        for record in records:
            for child_id, child_meta in (record.get("_spawned_children") or {}).items():
                if child_id not in subagent_children:
                    subagent_children[child_id] = dict(child_meta)
        # Link children once, after descriptor and INVOKE_SUBAGENT evidence are merged.
        for record in records:
            child_meta = subagent_children.get(record["id"])
            if child_meta and child_meta.get("parent_id"):
                record["agent_kind"] = "spawned"
                record["agent_parent_id"] = agy_agent_id(child_meta["parent_id"])
                record["agent_role"] = child_meta.get("role")
                record["agent_label"] = child_meta.get("label") or ""
        # Propagate the parent model to child sessions (supports multi-level subagent trees).
        # Projects are never inherited: a child may run in its own worktree.
        by_id = {record["id"]: record for record in records}
        for _ in range(6):
            changed = False
            for record in records:
                if record.get("agent_kind") != "spawned":
                    continue
                child_meta = subagent_children.get(record["id"]) or {}
                parent = by_id.get(child_meta.get("parent_id") or "")
                if not parent:
                    continue
                if (
                    record.get("model") == "unknown-model"
                    and parent.get("model")
                    and parent["model"] != "unknown-model"
                ):
                    record["model"] = parent["model"]
                    record["model_provider"] = parent.get("model_provider") or model_ref_for(
                        "", parent["model"]
                    ).provider_id
                    changed = True
            if not changed:
                break
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
                client_id="agy",
                session_id=record["id"],
                display_label="Antigravity",
                project=record.get("project") or None,
                locator=SourceLocator("jsonl", record["path"]),
                activity_mtime=record["mtime"],
                revision=self._revision(record["path"]),
                model_ref=model,
                account_provider_id=None,
            ))
        return tuple(result)

    def _revision(self, path):
        return SourceRevision(("agy-jsonl", *_file_signature(path)))

    def current_revision(self, source):
        path = source.locator.value if isinstance(source, SessionSource) else source.get("path", "")
        return self._revision(path)

    def _cost_breakdown(self, model_ref, input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, observed_at=0.0):
        if self.quote_resolver is None or model_ref is None:
            return None
        # An undated request is priced at today's rate, never at a scheduled future rate.
        when = datetime.fromtimestamp(observed_at or time.time(), timezone.utc)
        try:
            quote = self.quote_resolver(PriceQuery(model_ref, when))
        except Exception:
            return None
        if not getattr(quote, "available", False):
            return None
        # Long-context tiers are decided per request from that request's prompt size.
        prompt_tokens = (input_tokens or 0) + (cache_read_tokens or 0) + (cache_write_tokens or 0)
        input_multiplier, output_multiplier = long_context_multipliers(quote, prompt_tokens)
        return cost_breakdown_values(
            input_tokens or 0, output_tokens or 0, cache_read_tokens or 0, cache_write_tokens or 0,
            quote, input_multiplier, output_multiplier,
        )

    def _parsed(self, path, fallback_model="", fallback_provider=""):
        if not self._owned_path(path):
            return {
                "turns": (), "corrupt": 0, "available": False, "truncated": False,
            }
        rows, corrupt, available, truncated = _read_jsonl(path)
        if not rows:
            return {
                "turns": (), "corrupt": corrupt, "available": available,
                "truncated": truncated,
            }
        turns = []
        pending_user_ts = 0.0
        pending_input_chars = 0
        previous_ts = 0.0
        model = str(fallback_model or "")
        provider = str(fallback_provider or "")
        skill_extractor = self.compatibility.get("skill_names_from_value")
        has_measured_tokens = any(
            _integer(row.get("input_tokens")) is not None
            and _integer(row.get("output_tokens")) is not None
            for row in rows
            if row.get("type") == "PLANNER_RESPONSE"
        )

        for row in rows:
            kind = str(row.get("type") or "")
            ts = _timestamp(row.get("created_at") or row.get("timestamp"))
            status = str(row.get("status") or "").upper()

            if kind == "USER_INPUT":
                pending_user_ts = ts or previous_ts
                pending_input_chars += _safe_json_chars(row.get("content"))
                extracted_model, _ = _extract_model_and_effort(row.get("content"))
                if extracted_model:
                    model = extracted_model
                    provider = ""
                previous_ts = ts or previous_ts
                continue

            if kind != "PLANNER_RESPONSE":
                pending_input_chars += _safe_json_chars(row.get("content"))
                if turns and turns[-1].get("tools") and kind in (
                    "GENERIC", "INVOKE_SUBAGENT", "SEARCH_WEB", "VIEW_FILE", "RUN_COMMAND",
                ):
                    for tool in turns[-1]["tools"]:
                        tool["result_available"] = True
                        if status == "ERROR":
                            tool["error"] = True
                    if ts and ts > turns[-1]["end"]:
                        turns[-1]["end"] = ts
                previous_ts = ts or previous_ts
                continue

            if kind == "PLANNER_RESPONSE" and len(turns) < MAX_TURNS:
                raw_model = row.get("model") or row.get("modelId")
                if raw_model:
                    model = _normalize_model(raw_model)
                raw_provider = row.get("provider")
                if raw_provider:
                    provider = str(raw_provider).strip().lower()
                model_ref = model_ref_for(provider, model)

                input_tokens = _integer(row.get("input_tokens"))
                output_tokens = _integer(row.get("output_tokens"))
                cache_read = _integer(row.get("cache_read_tokens"))
                cache_write = _integer(row.get("cache_write_tokens"))

                token_available = input_tokens is not None and output_tokens is not None
                token_estimated = False
                # Antigravity records cache reads but not cache writes; each stays
                # unavailable on its own instead of becoming a measured zero.
                cache_read_available = cache_read is not None
                cache_write_available = cache_write is not None
                context_available = input_tokens is not None and cache_read is not None
                context_tokens = (
                    (input_tokens or 0) + (cache_read or 0) + (cache_write or 0)
                    if context_available else 0
                )
                has_thinking = bool(str(row.get("thinking") or "").strip())
                raw_tools = row.get("tool_calls")
                if (
                    has_measured_tokens
                    and not token_available
                    and not has_thinking
                    and not (isinstance(raw_tools, list) and raw_tools)
                ):
                    previous_ts = ts or previous_ts
                    continue

                if not token_available and not has_measured_tokens:
                    output_chars = (
                        _safe_json_chars(row.get("content"))
                        + _safe_json_chars(row.get("thinking"))
                        + sum(
                            _safe_json_chars(
                                item.get("args") if item.get("args") is not None else item.get("arguments")
                            )
                            for item in (raw_tools if isinstance(raw_tools, list) else ())
                            if isinstance(item, dict)
                        )
                    )
                    if pending_input_chars > 0 or output_chars > 0 or (isinstance(raw_tools, list) and raw_tools):
                        input_tokens = max(1, _token_estimate(pending_input_chars))
                        output_tokens = max(1, _token_estimate(output_chars))
                        cache_read = 0
                        cache_write = 0
                        token_available = True
                        token_estimated = True
                        cache_read_available = False
                        cache_write_available = False
                        context_available = False

                tools = []
                for tool_index, item in enumerate(raw_tools if isinstance(raw_tools, list) else (), 1):
                    if not isinstance(item, dict):
                        continue
                    raw_name = item.get("name")
                    name = _normalize_tool_name(raw_name)
                    args = item.get("args") if item.get("args") is not None else item.get("arguments")
                    skills = (
                        skill_extractor(args, raw_name)
                        if callable(skill_extractor) and isinstance(args, dict)
                        else []
                    )
                    tools.append({
                        "id": "step-{}-tool-{}".format(
                            _integer(row.get("step_index")) or len(turns) + 1, tool_index,
                        ),
                        "name": name,
                        "category": _tool_category(name),
                        "args_chars": _safe_json_chars(args),
                        "result_available": False,
                        "error": status == "ERROR",
                        "skills": skills,
                    })

                start = pending_user_ts or previous_ts or ts
                end = max(start, ts)
                cost = (
                    self._cost_breakdown(
                        model_ref,
                        input_tokens or 0,
                        output_tokens or 0,
                        cache_read or 0,
                        cache_write or 0,
                        end or start,
                    )
                    if token_available else None
                )
                turns.append({
                    "index": len(turns) + 1,
                    "start": start,
                    "end": end,
                    "model": model_ref.model_id,
                    "model_ref": model_ref,
                    "stop_reason": "error" if status == "ERROR" else ("tool_use" if tools else "stop"),
                    "input_tokens": input_tokens or 0,
                    "output_tokens": output_tokens or 0,
                    "reasoning_tokens": 0,
                    "has_thinking": has_thinking,
                    "cache_read_tokens": cache_read or 0,
                    "cache_write_tokens": cache_write or 0,
                    "token_available": token_available,
                    "token_estimated": token_estimated,
                    "cache_read_available": cache_read_available,
                    "cache_write_available": cache_write_available,
                    "cache_available": cache_read_available and cache_write_available,
                    "context_available": context_available,
                    "context_tokens": context_tokens,
                    "cost": cost,
                    "tools": tools[:MAX_TOOLS],
                })
                pending_user_ts = 0.0
                pending_input_chars = 0
            previous_ts = ts or previous_ts

        return {
            "turns": tuple(turns),
            "corrupt": corrupt,
            "available": available,
            "truncated": truncated,
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
        fallback_model = source.model_ref.model_id if source.model_ref else ""
        fallback_provider = source.model_ref.provider_id if source.model_ref else ""
        parsed = self._parsed(
            source.locator.value,
            fallback_model=fallback_model,
            fallback_provider=fallback_provider,
        )
        turns = parsed["turns"]
        tokens_available = bool(turns) and all(turn["token_available"] for turn in turns)
        any_token_estimated = bool(turns) and any(turn.get("token_estimated") for turn in turns)
        token_basis = EvidenceBasis.ESTIMATED if any_token_estimated else EvidenceBasis.MEASURED
        cache_read_available = bool(turns) and all(turn["cache_read_available"] for turn in turns)
        cache_write_available = bool(turns) and all(turn["cache_write_available"] for turn in turns)
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
            "corrupt_rows": "Malformed Antigravity rows were ignored.",
            "usage_unavailable": "Antigravity token evidence was unavailable.",
            "history_truncated": "Detailed Antigravity history was bounded.",
        }
        return NormalizedSession(
            source=source,
            started_at=_date(min((turn["start"] for turn in turns if turn["start"]), default=0)),
            ended_at=_date(max((turn["end"] for turn in turns if turn["end"]), default=0)),
            usage=UsageEvidence(
                self._available(sum(turn["input_tokens"] for turn in turns), tokens_available, token_basis),
                self._available(sum(turn["output_tokens"] for turn in turns), tokens_available, token_basis),
                self._available(sum(turn["cache_read_tokens"] for turn in turns), cache_read_available),
                self._available(sum(turn["cache_write_tokens"] for turn in turns), cache_write_available),
                self._available(
                    sum(sum(turn["cost"].values()) for turn in turns if turn["cost"]),
                    cost_available,
                    EvidenceBasis.ESTIMATED,
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
                TurnSummary(
                    turn["index"], _date(turn["start"]), _date(turn["end"]),
                    self._available(
                        turn["output_tokens"],
                        turn["token_available"],
                        EvidenceBasis.ESTIMATED if turn.get("token_estimated") else EvidenceBasis.MEASURED,
                    ),
                )
                for turn in turns
            ) if detail is DetailLevel.FULL else (),
            pricing_basis=(
                "transcript_chars + public_model_rates"
                if (cost_available and any_token_estimated)
                else "public_model_rates" if cost_available else None
            ),
            capabilities=self.descriptor.capabilities,
            warnings=tuple(ParseWarning(code, messages[code]) for code in warning_codes),
            detail=detail,
        )

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    def _legacy_parsed(self, source):
        return self._parsed(
            source.get("path") or "",
            fallback_model=source.get("model") or "",
            fallback_provider=source.get("model_provider") or "",
        )

    @staticmethod
    def _legacy_usage(turn):
        return {
            "input_tokens": turn["input_tokens"],
            "output_tokens": turn["output_tokens"],
            "cache_read_input_tokens": turn["cache_read_tokens"],
            "cache_creation_input_tokens": turn["cache_write_tokens"],
        }

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        parsed = self._legacy_parsed(source)
        turns = parsed["turns"]
        if not turns:
            return None
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace, wait_samples, intervals = [], [], [], [], []
        all_tokens_available = all(turn["token_available"] for turn in turns)
        all_cache_available = all(turn["cache_available"] for turn in turns)
        all_cost_available = all(turn["cost"] is not None for turn in turns)
        think_turns = 0

        for turn in turns:
            breakdown = turn["cost"] or {key: 0.0 for key in cost}
            cost_available = turn["cost"] is not None
            execution_cost = sum(breakdown.values())
            usage = self._legacy_usage(turn)
            if turn["has_thinking"]:
                think_turns += 1
            tools = []
            for tool in turn["tools"]:
                ident = compat["tool_identity"](tool["name"])
                tools.append({
                    **ident,
                    "id": tool["id"],
                    "call_id": tool["id"],
                    "args_chars": 0,
                    "output_chars": 0,
                    "output_tokens": 0,
                    "result_available": bool(tool.get("result_available")),
                    "error": bool(tool.get("error")),
                    "skills": list(tool.get("skills") or ()),
                })
            total = sum(usage.values())
            timing_available = bool(turn["start"] and turn["end"] >= turn["start"])
            availability = compat["metric_availability"](
                "agy",
                cost=cost_available,
                tokens=turn["token_available"],
                input_tokens=turn["token_available"],
                output_tokens=turn["token_available"],
                cache=turn["cache_available"],
                throughput=False,
                context=False,
                timing=timing_available,
                tool_results=any(tool["result_available"] for tool in tools),
            )
            duration = max(0.0, turn["end"] - turn["start"])
            series.append({
                "i": turn["index"],
                "in": usage["input_tokens"],
                "out": usage["output_tokens"],
                "cost": execution_cost,
                "fresh_input": usage["input_tokens"],
                "cache": usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"],
                "cache_read": usage["cache_read_input_tokens"],
                "cache_write": usage["cache_creation_input_tokens"],
                "think": bool(turn["has_thinking"]),
                "tools": len(tools),
                "side": False,
                "reasoning": 0,
                "reasoning_ms": 0,
                "context_pct": None,
                "context_tokens": turn["context_tokens"],
                "user_message": "",
                "user_input": "",
                "availability": availability,
            })
            executions.append({
                "id": "{}:{}".format(source["id"], turn["index"]),
                "idx": turn["index"],
                "ts": turn["end"] or turn["start"],
                "time": time.strftime("%H:%M", time.localtime(turn["end"] or turn["start"] or 0)),
                "model": turn["model"],
                "tokens": {
                    "input": usage["input_tokens"],
                    "output": usage["output_tokens"],
                    "reasoning": 0,
                    "retrieval": 0,
                    "fresh_input": usage["input_tokens"],
                    "cache": usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"],
                    "cache_read": usage["cache_read_input_tokens"],
                    "cache_write": usage["cache_creation_input_tokens"],
                    "total": total,
                },
                "cost": execution_cost,
                "cost_breakdown": breakdown,
                "tools": tools,
                "tool_count": len(tools),
                "model_calls": 1,
                "reasoning_tokens": 0,
                "reasoning_duration_ms": 0,
                "context_tokens": turn["context_tokens"],
                "context_window": 0,
                "context_pct": None,
                "duration_ms": duration * 1000 if duration else None,
                "wait_duration_ms": duration * 1000 if duration else None,
                "summary": "Execution {}: {} tools · {}".format(
                    turn["index"],
                    len(tools),
                    "{} est".format(format_usd(execution_cost))
                    if cost_available else "cost unavailable",
                ),
                "user_message": "",
                "user_input": "",
                "availability": availability,
            })
            trace.append(compat["trace_event"](
                turn["start"], "user", "User input", "Content excluded", turn["index"],
                severity="start", model=turn["model"], native_type="user", native_subtype="user_message",
            ))
            if turn["has_thinking"]:
                trace.append(compat["trace_event"](
                    turn["start"], "reasoning", "Reasoning", "Content excluded", turn["index"],
                    severity="reasoning", model=turn["model"], native_type="assistant", native_subtype="reasoning",
                ))
            for tool in tools:
                trace.append(compat["trace_event"](
                    turn["end"], "tool_call", tool["display"], "Payload excluded", turn["index"],
                    tool=tool["name"], severity="warn" if tool.get("error") else "tool",
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
            for key in cost:
                cost[key] += breakdown[key]
            model_tok[turn["model"]] += total
            model_cost[turn["model"]] += execution_cost
            if timing_available:
                intervals.append((turn["start"], turn["end"]))
                wait_samples.append(_wait_sample(turn, usage, len(tools)))

        primary_model = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        total_tokens, total_cost = sum(tot.values()), sum(cost.values())
        tool_data = compat["tool_summary"](executions)
        analyses = compat["analysis_block"](
            tot, total_cost, 0, think_turns, 0.0, model_tok, model_cost, tool_data, 0.0, 0, len(executions),
        )
        active = merge_execution_intervals(intervals)
        any_token_estimated = bool(turns) and any(turn.get("token_estimated") for turn in turns)
        source = dict(source)
        source["context_latest"] = executions[-1]["context_tokens"] if executions else 0
        source["token_estimate"] = any_token_estimated
        source["estimate_basis"] = (
            "transcript_chars (~4 chars/token) + public model rates"
            if any_token_estimated else ""
        )
        throughput = performance_summary(wait_samples, tot["output"])
        context_available = bool(turns) and all(turn["context_available"] for turn in turns)
        availability = compat["metric_availability"](
            "agy",
            cost=all_cost_available,
            tokens=all_tokens_available,
            input_tokens=all_tokens_available,
            output_tokens=all_tokens_available,
            cache=all_cache_available,
            throughput=throughput["available"],
            context=context_available,
            timing=bool(intervals),
            tool_results=any(
                tool.get("result_available")
                for execution in executions for tool in execution["tools"]
            ),
        )
        biggest = max(
            ({"cost": execution["cost"], "idx": execution["idx"]} for execution in executions),
            key=lambda row: row["cost"],
            default=None,
        ) if all_cost_available else None
        source["cache_savings_available"] = False
        if any_token_estimated:
            pricing_note = (
                "Estimated from Antigravity transcript length (~4 chars/token) at public model rates."
                if all_cost_available
                else "Estimated from Antigravity transcript length (~4 chars/token); no configured public price for this model."
            )
        else:
            pricing_note = (
                "Measured Antigravity token counts; estimated at public model rates."
                if all_cost_available
                else "Measured Antigravity token counts; no configured public price for this model."
            )
        state = compat["build_state"](
            source, tot, cost, total_tokens, total_cost, series, executions, trace,
            {"reasoning": 0, "output": tot["output"], "retrieval": 0, "coordination": 0},
            analyses, [],
            min((turn["start"] for turn in turns if turn["start"]), default=0),
            max((turn["end"] for turn in turns if turn["end"]), default=0),
            0, biggest, 0, True, primary_model,
            pricing_note,
            {
                "duration_s": active,
                "available": bool(intervals),
                "reported_executions": 0,
                "observed_executions": len(intervals),
                "execution_count": len(executions),
                "basis": "inferred",
            },
            wait_samples,
            availability=availability,
        )
        state["throughput"] = throughput
        state["semantic_available"] = False
        state["token_estimate"] = any_token_estimated
        if any_token_estimated:
            state["estimation"] = {
                "basis": "visible transcript text divided by {} chars/token".format(CHARS_PER_TOKEN),
                "input": "visible user and tool text added since the previous model response",
                "output": "visible assistant, thinking, and tool call argument character estimate",
                "excluded": [
                    "conversation history re-sent with each request",
                    "cache accounting", "system prompts", "internal context",
                ],
            }
        return state

    def summarize_legacy(self, source, unused=None):
        del unused
        compat = self._require_compatibility()
        parsed = self._legacy_parsed(source)
        turns = parsed["turns"]
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
            breakdown = turn["cost"] or {
                "input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0,
            }
            cost_available = turn["cost"] is not None
            value = sum(breakdown.values())
            total = sum(usage.values())
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            total_cost += value
            model_cost[turn["model"]] += value
            model_tok[turn["model"]] += total
            models.add(turn["model"])
            compat["add_model_summary"](
                model_stats, turn["model"], usage, value, cost_available=cost_available,
            )
            compat["add_model_daily"](
                model_daily, turn["model"], usage, value, turn["end"], cost_available=cost_available,
            )
            if turn["end"] and cost_available:
                day_cost[time.strftime("%Y-%m-%d", time.localtime(turn["end"]))] += value
            if turn["start"] and turn["end"] >= turn["start"]:
                intervals.append((turn["start"], turn["end"]))
                context_samples.append(turn["context_tokens"])
                wait_samples.append(_wait_sample(turn, usage, len(turn["tools"])))
            for tool in turn["tools"]:
                ident = compat["tool_identity"](tool["name"])
                tool_calls.append({
                    **ident,
                    "output_tokens": 0,
                    "error": bool(tool.get("error")),
                    "ts": turn["end"],
                    "skills": list(tool.get("skills") or ()),
                })

        throughput = performance_summary(wait_samples, output_tokens)
        availability = compat["metric_availability"](
            "agy",
            cost=all_cost_available,
            tokens=all_tokens_available,
            input_tokens=all_tokens_available,
            output_tokens=all_tokens_available,
            cache=all_cache_available,
            throughput=throughput["available"],
            context=all_context_available,
            timing=bool(intervals),
            tool_results=False,
        )
        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = compat["metric_availability"](
                "agy",
                cost=int(stats.get("cost_covered_executions") or 0) > 0,
                tokens=all_tokens_available,
                input_tokens=all_tokens_available,
                output_tokens=all_tokens_available,
                cache=all_cache_available,
                throughput=throughput["available"],
                context=all_context_available,
                timing=False,
                tool_results=False,
            )
        active = merge_execution_intervals(intervals)
        any_token_estimated = bool(turns) and any(turn.get("token_estimated") for turn in turns)
        source = dict(source)
        source["token_estimate"] = any_token_estimated
        source["estimate_basis"] = (
            "transcript_chars (~4 chars/token) + public model rates"
            if any_token_estimated else ""
        )
        first_ts = min((turn["start"] for turn in turns if turn["start"]), default=0)
        last_ts = max((turn["end"] for turn in turns if turn["end"]), default=0)
        row = compat["summary_row"](
            source,
            source.get("title") if source.get("agent_kind") == "spawned" else None,
            total_cost,
            sum(model_tok.values()),
            len(turns),
            models,
            first_ts,
            last_ts,
            model_cost,
            model_tok,
            day_cost,
            True,
            {"duration_s": active, "available": bool(intervals), "basis": "inferred"},
            input_tokens,
            output_tokens,
            model_stats,
            list(model_daily.values()),
            wait_samples,
            wait_samples,
            availability,
        )
        primary_model = (
            max(model_tok, key=model_tok.get) if model_tok
            else source.get("model") or "unknown-model"
        )
        row["primary_model"] = primary_model
        row["throughput"] = throughput
        row["context"] = {
            "latest": context_samples[-1] if context_samples else 0,
            "window": None,
            "latest_pct": None,
            "estimated": False,
        }
        row["_context_samples"] = context_samples[-compat["context_sample_limit"]:]
        row["terminal"] = False
        row["token_estimate"] = any_token_estimated
        row["_tool_evidence"] = compat["summarize_tool_evidence"](tool_calls)
        if turns:
            row["_agent_records"] = [self._agent_record(
                source, turns, primary_model, input_tokens, output_tokens,
                sum(turn["cache_read_tokens"] for turn in turns),
                sum(turn["cache_write_tokens"] for turn in turns),
                sum(model_tok.values()), total_cost,
                all_tokens_available, all_cost_available,
                first_ts, last_ts, active, len(tool_calls),
            )]
        return row

    @staticmethod
    def _agent_record(source, turns, model, input_tokens, output_tokens,
                      cache_read_tokens, cache_write_tokens, total_tokens,
                      total_cost, tokens_available, cost_available,
                      first_ts, last_ts, active_s, tool_call_count):
        session_id = str(source.get("id") or "")
        agent_id = source.get("agent_id") or agy_agent_id(session_id)
        spawned = source.get("agent_kind") == "spawned"
        terminal = bool(turns and turns[-1].get("stop_reason") == "stop")
        last_activity = last_ts or float(source.get("mtime") or 0) or None
        now = time.time()
        activity_state = (
            "complete" if terminal
            else "working" if last_activity and now - float(last_activity) <= 90
            else "incomplete"
        )
        failed = sum(1 for turn in turns if turn.get("stop_reason") == "error")
        return {
            "id": agent_id,
            "parent_id": source.get("agent_parent_id") if spawned else None,
            "session_id": session_id or None,
            "runtime": "agy",
            "client": source.get("label") or "Antigravity",
            "kind": "spawned" if spawned else "root",
            "depth": 1 if spawned else 0,
            "label": source.get("agent_label") or "",
            "role": source.get("agent_role") or None,
            "model": model or "unknown-model",
            "activity_state": activity_state,
            "started_at": first_ts or None,
            "ended_at": last_ts if terminal else None,
            "last_activity_at": last_activity,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "reasoning_tokens": 0,
            "tokens": total_tokens if tokens_available else None,
            "tokens_available": bool(tokens_available),
            "cost": total_cost if cost_available else None,
            "cost_available": bool(cost_available),
            "executions": len(turns),
            "attempts": len(turns),
            "retries": 0,
            "failed_attempts": failed,
            "tool_calls": tool_call_count,
            "work_time_s": active_s if active_s > 0 else None,
        }

    def deletion_plan(self, source):
        del source
        return DeletionPlan.deny("Antigravity session storage is read-only.")


class AgyRuntimeAdapterProxy:
    descriptor = AgyRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Antigravity adapter")
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
