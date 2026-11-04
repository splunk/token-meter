"""Read-only adapter for Grok (xAI Grok Build) local session evidence.

Grok keeps one directory per session under ``~/.grok/sessions/<urlencoded-cwd>/<id>/``.
Measured token counts are not in the transcript. They are in
``~/.grok/logs/unified.jsonl`` on ``shell.turn.inference_done`` rows:
``prompt_tokens``, ``cached_prompt_tokens``, ``completion_tokens``, and
``reasoning_tokens``. This adapter reads those numbers plus session metadata
and payload-free tool names. It never projects prompts, responses, reasoning
text, tool arguments, tool results, or credentials.

``usage.json`` and ``signals.json`` are real siblings of ``summary.json``.
``usage.json`` repeats the log's token totals and adds ``costUsdTicks``
(10_000_000_000 ticks per USD). Those ticks are Grok's recorded figure, not
the public list rate this adapter estimates, so they are not the billed cost.
Cache-write tokens in that file are not a billed dimension of the log either.
"""

import json
import math
import os
import re
import threading
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
from token_meter.domain.redaction import has_account_at, has_secret_token, is_host_like
from token_meter.domain.timing import merge_execution_intervals, performance_summary
from token_meter.domain.usage import cost_breakdown_values
from token_meter.models.pricing import quote_for as catalog_quote_for


MAX_SOURCES = 2_000
MAX_CALLS = 10_000
MAX_TOOLS = 2_000
MAX_SUMMARY_BYTES = 256 * 1024
# Chat history is scanned from the start so an early model switch is not missed.
# Past this size the scan is incomplete and the session cost stays unavailable.
MAX_CHAT_BYTES = 64 * 1024 * 1024
MAX_EVENTS_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_TIMESTAMP_SECONDS = 32_503_680_000 - 86_400
MAX_TOKEN_COUNT = 10 ** 12
# Published xAI text models bill every token at 2× once the prompt reaches this size.
LONG_CONTEXT_TOKENS = 200_000
CONTEXT_WINDOWS = {
    "grok-4.7": 500_000,
    "grok-4.6": 500_000,
    "grok-4.5": 500_000,
    "grok-4.3": 1_000_000,
    "grok-4.20-0309-reasoning": 1_000_000,
    "grok-4.20-0309-non-reasoning": 1_000_000,
    "grok-4.20-multi-agent-0309": 1_000_000,
    "grok-build-0.1": 256_000,
}
_SESSION_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{7,79}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,159}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_EFFORTS = frozenset(("none", "minimal", "low", "medium", "high", "xhigh"))
_INFERENCE_MSG = "shell.turn.inference_done"
PRICING_NOTE = (
    "Measured Grok token counts from the local inference log, estimated at "
    "xAI public API rates. Cache writes are not in that log. A prompt of "
    "200,000 tokens or more uses the published long-context rates."
)


def default_sessions_dir(home=None, environment=None):
    """Return Grok's session root without a platform dependency."""
    environment = os.environ if environment is None else dict(environment)
    override = str(environment.get("GROK_SESSIONS_DIR") or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    grok_home = str(environment.get("GROK_HOME") or "").strip()
    if grok_home:
        return os.path.abspath(os.path.expanduser(os.path.join(grok_home, "sessions")))
    base = os.path.expanduser(str(home or "~"))
    return os.path.abspath(os.path.join(base, ".grok", "sessions"))


def default_log_path(home=None, environment=None):
    """Return the unified log that carries inference token counts."""
    environment = os.environ if environment is None else dict(environment)
    override = str(environment.get("GROK_LOG_PATH") or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    grok_home = str(environment.get("GROK_HOME") or "").strip()
    if grok_home:
        return os.path.abspath(os.path.expanduser(os.path.join(grok_home, "logs", "unified.jsonl")))
    base = os.path.expanduser(str(home or "~"))
    return os.path.abspath(os.path.join(base, ".grok", "logs", "unified.jsonl"))


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
        try:
            value = float(value)
        except OverflowError:
            return 0.0
        if value > 10_000_000_000:
            value /= 1000.0
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            return 0.0
    else:
        return 0.0
    return value if math.isfinite(value) and 0 < value <= MAX_TIMESTAMP_SECONDS else 0.0


def _date(seconds):
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 0 or number > MAX_TOKEN_COUNT or value != number:
        return None
    return number


def _read_json(path, limit):
    try:
        if os.path.islink(path):
            return None
        size = os.path.getsize(path)
        if size > limit:
            return None
        with open(path, encoding="utf-8") as handle:
            value = json.loads(handle.read(limit + 1))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _safe_title(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text or len(text) > 120:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    lowered = text.lower()
    if (
        "://" in text
        or text.startswith(("/", "\\", "~", "."))
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or has_secret_token(text)
        or "@" in text
    ):
        return ""
    if lowered.startswith(("bearer ", "sk-", "-----begin ")):
        return ""
    return text


def _safe_effort(value):
    text = str(value or "").strip().lower()
    return text if text in _EFFORTS else ""


def _model_id(value):
    """Keep a Grok model id, never an account, host, or secret from tool text."""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text.endswith("-build"):
        text = text[:-len("-build")]
    if not text or not _MODEL_ID_RE.fullmatch(text):
        return ""
    if "://" in text or "/" in text or "\\" in text:
        return ""
    if (
        has_secret_token(text)
        or is_host_like(text)
        or has_account_at(text)
        or text.startswith(("bearer ", "sk-", "-----begin "))
        or any(marker in text for marker in ("api_key=", "api-key=", "secret=", "token="))
    ):
        return ""
    return text


def _context_window(model_id):
    for rule, window in sorted(CONTEXT_WINDOWS.items(), key=lambda item: len(item[0]), reverse=True):
        if model_id == rule or model_id.startswith(rule + "-"):
            return window
    return None


def _tail_text(path, limit):
    """Return at most the last ``limit`` bytes, split on a newline boundary."""
    try:
        if os.path.islink(path):
            return ""
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            raw = handle.read(limit)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace")
    if size > limit:
        text = text.split("\n", 1)[-1]
    return text


class GrokRuntimeAdapter:
    """Discover Grok session directories and expose no message content."""

    descriptor = RuntimeDescriptor(
        "grok",
        "Grok",
        frozenset(("sessions", "models", "tools")),
        "runtime.generic",
        "runtime-neutral",
        None,
    )

    def __init__(self, sessions_dir=None, log_path=None, project_resolver=None,
                 quote_resolver=None, compatibility=None):
        self.sessions_dir = Path(os.path.abspath(os.path.expanduser(
            str(sessions_dir or default_sessions_dir())
        )))
        self.log_path = Path(os.path.abspath(os.path.expanduser(
            str(log_path or default_log_path())
        )))
        self.project_resolver = project_resolver or (lambda value: value)
        self.quote_resolver = quote_resolver
        self.compatibility = dict(compatibility or {})
        self._dirs = {}
        self._usage_cache = None
        self._usage_lock = threading.Lock()

    def _owned(self, path):
        path = os.path.realpath(os.path.abspath(os.path.expanduser(str(path or ""))))
        root = os.path.realpath(str(self.sessions_dir))
        try:
            return os.path.commonpath((path, root)) == root
        except ValueError:
            return False

    def _session_dirs(self):
        root = self.sessions_dir
        found = []
        try:
            projects = list(root.iterdir())
        except OSError:
            return ()
        for project in projects:
            if not project.is_dir() or project.is_symlink():
                continue
            try:
                children = list(project.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir() or child.is_symlink() or not self._owned(child):
                    continue
                if not _SESSION_ID_RE.fullmatch(child.name):
                    continue
                summary = child / "summary.json"
                chat = child / "chat_history.jsonl"
                if summary.is_file() or chat.is_file():
                    found.append(child)
        found.sort(key=lambda path: (-_mtime(path), path.name))
        return tuple(found[:MAX_SOURCES])

    def _summary(self, directory):
        return _read_json(directory / "summary.json", MAX_SUMMARY_BYTES) or {}

    def _models_in(self, directory):
        """Return assistant model ids and whether the scan covered the file.

        Only the top-level ``model_id`` on ``type: assistant`` rows counts.
        Tool results quote that field inside their text, and a regex over the
        tail would treat those quotes as a model switch.
        """
        chat = directory / "chat_history.jsonl"
        found = []
        if not chat.is_file() or chat.is_symlink():
            return (), False
        read = 0
        incomplete = False
        try:
            with chat.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    read += len(line)
                    if read > MAX_CHAT_BYTES:
                        incomplete = True
                        break
                    if '"model_id"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, RecursionError, ValueError):
                        continue
                    if not isinstance(row, dict) or row.get("type") != "assistant":
                        continue
                    model = _model_id(row.get("model_id"))
                    if model and model not in found:
                        found.append(model)
        except OSError:
            return (), True
        return tuple(found), incomplete

    def _project(self, summary, directory):
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        raw = info.get("cwd")
        if not isinstance(raw, str):
            try:
                raw = unquote(directory.parent.name)
            except Exception:
                raw = ""
        text = str(raw or "").strip()
        if (
            not text or len(text) > 512 or "\n" in text or "\x00" in text
            or not (text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", text))
        ):
            return ""
        try:
            return str(self.project_resolver(text) or "")
        except Exception:
            return ""

    def _record(self, directory):
        summary = self._summary(directory)
        if str(summary.get("session_kind") or "").strip().lower() == "claude_import":
            return None
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        session_id = directory.name
        declared = str(info.get("id") or "").strip()
        if declared and declared != session_id:
            return None
        models, models_unreliable = self._models_in(directory)
        model = models[-1] if models else _model_id(summary.get("current_model_id"))
        activity = (
            _timestamp(summary.get("last_active_at"))
            or _timestamp(summary.get("updated_at"))
            or _timestamp(summary.get("created_at"))
            or _mtime(directory)
        )
        title = _safe_title(summary.get("generated_title")) or _safe_title(summary.get("session_summary"))
        self._dirs[session_id] = directory
        record = {
            "provider": "grok",
            "client": "grok",
            "label": "Grok",
            "runtime": "Grok",
            "id": session_id,
            "session": session_id,
            "path": "grok:{}".format(session_id),
            "project": self._project(summary, directory),
            "mtime": activity,
            "signature_mtime": activity,
            "request_revision": "|".join(self._revision_for(directory).parts),
            "title": title or "Grok session",
            "model": model or "unknown-model",
            "model_provider": "xai" if model else "",
            "source_kind": "grok_session",
            "model_count": len(set(models) or (() if not model else (model,))),
            "models_unreliable": models_unreliable,
        }
        effort = _safe_effort(summary.get("reasoning_effort"))
        if effort:
            record["reasoning_effort"] = effort
        started = _timestamp(summary.get("created_at"))
        if started:
            record["started_at"] = started
        return record

    def _revision_for(self, directory):
        parts = ["grok-session"]
        for name in ("summary.json", "chat_history.jsonl", "events.jsonl"):
            parts.extend(_file_signature(directory / name))
        calls = self._usage_index()[1].get(directory.name, ())
        # The shared log's mtime would invalidate every Grok session on any
        # append. Fingerprint only this session's retained calls.
        parts.append("calls:{}".format(len(calls)))
        if calls:
            last = calls[-1]
            parts.extend((
                str(last.get("end") or 0),
                str(last.get("prompt_tokens")),
                str(last.get("completion_tokens")),
                str(last.get("reasoning_tokens")),
            ))
        return SourceRevision(tuple(parts))

    def discover_legacy(self, context):
        del context
        records = []
        for directory in self._session_dirs():
            record = self._record(directory)
            if record is not None:
                records.append(record)
        return tuple(records)

    def discover(self, context):
        result = []
        for record in self.discover_legacy(context):
            model = record.get("model") or ""
            result.append(SessionSource(
                runtime_id="grok",
                client_id="grok",
                session_id=record["id"],
                display_label="Grok",
                project=record["project"] or None,
                locator=SourceLocator("grok-session", record["id"]),
                activity_mtime=record["mtime"],
                revision=SourceRevision((record["request_revision"],)),
                model_ref=ModelRef("xai", model) if model and model != "unknown-model" else None,
                account_provider_id=None,
            ))
        return tuple(result)

    def current_revision(self, source):
        directory = self._directory_for(getattr(source, "session_id", "") or "")
        if directory is None:
            return SourceRevision(("grok-session", "0", "0"))
        return self._revision_for(directory)

    def _directory_for(self, session_id):
        session_id = str(session_id or "")
        cached = self._dirs.get(session_id)
        if cached is not None and cached.is_dir():
            return cached
        if not _SESSION_ID_RE.fullmatch(session_id):
            return None
        for directory in self._session_dirs():
            if directory.name == session_id:
                self._dirs[session_id] = directory
                return directory
        return None

    def _usage_index(self):
        """Return ``(signature, calls_by_session, truncated_ids, window_start)``."""
        signature = _file_signature(self.log_path)
        with self._usage_lock:
            if self._usage_cache and self._usage_cache[0] == signature:
                return self._usage_cache
            index, truncated_ids, window_start = self._parse_log()
            self._usage_cache = (signature, index, truncated_ids, window_start)
            return self._usage_cache

    def _session_truncated(self, session_id, record):
        """A cap applies to the sessions it actually cut, not to every session."""
        _signature, _index, truncated_ids, window_start = self._usage_index()
        if session_id in truncated_ids:
            return True
        if not window_start:
            return False
        started = _timestamp((record or {}).get("started_at"))
        if not started:
            directory = self._directory_for(session_id)
            summary = self._summary(directory) if directory is not None else {}
            started = _timestamp(summary.get("created_at"))
        # No start time, or a start before the retained tail, means earlier
        # calls may be missing. Sessions created inside the tail are complete.
        if not started:
            return True
        return started < window_start

    def _parse_log(self):
        """Index inference rows. An oversized log is read from the tail.

        Grok keeps a single ``unified.jsonl`` (no rotated sibling in the
        current layout). Reading the head of a file past the cap dropped the
        newest sessions; the tail keeps them. ``MAX_CALLS`` is per session.
        ``window_start`` is the first retained event time when the file was
        tailed, else None.

        ``completion_tokens`` in this log already includes ``reasoning_tokens``.
        Every observed row has reasoning strictly below completion, the same
        subset shape as an OpenAI completion count. The public API documents
        the two as separately billed totals; this CLI log does not. A row
        where reasoning exceeds completion contradicts that shape and is
        unavailable, rather than billed twice or silently dropped.
        """
        path = self.log_path
        index = defaultdict(list)
        truncated_ids = set()
        window_start = None
        tailed = False
        try:
            if path.is_symlink() or not path.is_file():
                return {}, set(), None
            size = path.stat().st_size
            tailed = size > MAX_LOG_BYTES
            with path.open("rb") as handle:
                if tailed:
                    handle.seek(max(0, size - MAX_LOG_BYTES))
                    handle.readline()
                for raw in handle:
                    line = raw.decode("utf-8", "replace")
                    if _INFERENCE_MSG not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, RecursionError, ValueError):
                        continue
                    if not isinstance(event, dict) or event.get("msg") != _INFERENCE_MSG:
                        continue
                    session_id = str(event.get("sid") or "")
                    if not _SESSION_ID_RE.fullmatch(session_id):
                        continue
                    ctx = event.get("ctx") if isinstance(event.get("ctx"), dict) else {}
                    prompt = _integer(ctx.get("prompt_tokens"))
                    cached = _integer(ctx.get("cached_prompt_tokens"))
                    completion = _integer(ctx.get("completion_tokens"))
                    reasoning = _integer(ctx.get("reasoning_tokens"))
                    if cached is not None and prompt is not None and cached > prompt:
                        prompt = cached = completion = reasoning = None
                    if (
                        reasoning is not None and completion is not None
                        and reasoning > completion
                    ):
                        prompt = cached = completion = reasoning = None
                    end = _timestamp(event.get("ts"))
                    if window_start is None and end:
                        window_start = end
                    calls = index[session_id]
                    if len(calls) >= MAX_CALLS:
                        truncated_ids.add(session_id)
                        continue
                    elapsed_ms = _integer(ctx.get("model_elapsed_ms")) or 0
                    calls.append({
                        "end": end,
                        "elapsed_s": elapsed_ms / 1000.0 if elapsed_ms else 0.0,
                        "ttft_s": (_integer(ctx.get("ttft_ms")) or 0) / 1000.0,
                        "prompt_tokens": prompt,
                        "cached_tokens": cached,
                        "completion_tokens": completion,
                        "reasoning_tokens": reasoning,
                        "loop_index": _integer(ctx.get("loop_index")) or 0,
                    })
        except OSError:
            return {}, set(), None
        for session_id, calls in index.items():
            calls.sort(key=lambda row: (row["end"], row["loop_index"]))
            index[session_id] = calls
        return dict(index), truncated_ids, window_start if tailed else None

    def _tools(self, directory):
        path = directory / "events.jsonl"
        text = _tail_text(path, MAX_EVENTS_BYTES) if path.is_file() and not path.is_symlink() else ""
        tools = []
        for line in text.splitlines():
            if '"tool_completed"' not in line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "tool_completed":
                continue
            name = event.get("tool_name")
            if not isinstance(name, str) or not _TOOL_NAME_RE.fullmatch(name):
                continue
            outcome = str(event.get("outcome") or "").strip().lower()
            tools.append({
                "name": name,
                "ts": _timestamp(event.get("ts")),
                "error": outcome in ("error", "failed", "failure", "denied"),
            })
            if len(tools) >= MAX_TOOLS:
                break
        return tuple(tools)

    def _quote(self, model_id, at):
        if not model_id:
            return None
        query = PriceQuery(ModelRef("xai", model_id), _date(at))
        try:
            if self.quote_resolver is not None:
                return self.quote_resolver(query)
            return catalog_quote_for(query)
        except (TypeError, ValueError):
            return None

    def _parsed(self, record):
        directory = self._directory_for(record.get("id"))
        calls = list(self._usage_index()[1].get(record.get("id"), ()))
        tools = self._tools(directory) if directory is not None else ()
        model = record.get("model") or "unknown-model"
        window = _context_window(model) if model != "unknown-model" else None
        quote = self._quote(model if model != "unknown-model" else "", None)
        multiple_models = int(record.get("model_count") or 0) > 1
        models_unreliable = bool(record.get("models_unreliable"))
        log_truncated = self._session_truncated(record.get("id"), record)
        turns = []
        tool_cursor = 0
        ordered_tools = tuple(sorted(tools, key=lambda row: (row["ts"], row["name"])))
        for call in calls:
            end = call["end"]
            start = end - call["elapsed_s"] if end and call["elapsed_s"] and end >= call["elapsed_s"] else end
            owned = []
            while tool_cursor < len(ordered_tools) and (
                not end or ordered_tools[tool_cursor]["ts"] <= end
            ):
                owned.append(ordered_tools[tool_cursor])
                tool_cursor += 1
            prompt = call["prompt_tokens"]
            cached = call["cached_tokens"]
            completion = call["completion_tokens"]
            complete = prompt is not None and cached is not None and completion is not None
            fresh = prompt - cached if complete else 0
            multiplier = 2.0 if complete and prompt >= LONG_CONTEXT_TOKENS else 1.0
            cost = None
            # Bill completion only. In this CLI log it already includes reasoning.
            if complete and quote is not None and getattr(quote, "available", False) and not multiple_models:
                cost = cost_breakdown_values(
                    fresh, completion, cached, 0, quote, multiplier, multiplier,
                )
            turns.append({
                "index": len(turns) + 1,
                "start": start or 0.0,
                "end": end or 0.0,
                "model": model,
                "fresh": fresh if complete else 0,
                "cache_read": cached if complete else 0,
                "output": completion if complete else 0,
                "reasoning": call["reasoning_tokens"],
                "prompt": prompt if complete else None,
                "token_available": complete,
                "cache_available": complete,
                "context_available": complete and window is not None,
                "cost": cost,
                "long_context": multiplier > 1,
                "tools": tuple(owned),
                "ttft_s": call["ttft_s"],
                "elapsed_s": call["elapsed_s"],
            })
        if tool_cursor < len(ordered_tools) and turns:
            turns[-1]["tools"] = tuple(turns[-1]["tools"]) + ordered_tools[tool_cursor:]
        return {
            "turns": tuple(turns),
            "window": window,
            "multiple_models": multiple_models,
            "models_unreliable": models_unreliable,
            "truncated": log_truncated,
            "quote": quote,
        }

    def _publishable(self, parsed, turn):
        """Measured totals require one model and a call whose numbers are complete."""
        return (
            turn["token_available"]
            and not parsed["multiple_models"]
            and not parsed["models_unreliable"]
            and not parsed["truncated"]
        )

    def _legacy_usage(self, turn):
        return {
            "input_tokens": turn["fresh"],
            "output_tokens": turn["output"],
            "cache_read_input_tokens": turn["cache_read"],
            "cache_creation_input_tokens": 0,
            "reasoning_tokens": turn["reasoning"] if turn["reasoning"] is not None else 0,
            "reasoning_available": turn["reasoning"] is not None,
            "input_available": turn["token_available"],
            "output_available": turn["token_available"],
        }

    def _require_compatibility(self):
        if not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        parsed = self._parsed(source)
        turns = parsed["turns"]
        if not turns:
            return None
        tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        series, executions, trace, wait_samples, intervals = [], [], [], [], []
        complete = (
            not parsed["truncated"]
            and not parsed["multiple_models"]
            and not parsed["models_unreliable"]
        )
        all_tokens = complete and all(turn["token_available"] for turn in turns)
        all_cache = complete and all(turn["cache_available"] for turn in turns)
        all_cost = complete and all(turn["cost"] is not None for turn in turns)
        for turn in turns:
            breakdown = turn["cost"] or {key: 0.0 for key in cost}
            usage = self._legacy_usage(turn)
            tools = []
            for tool in turn["tools"]:
                ident = compat["tool_identity"](tool["name"])
                tools.append({
                    **ident,
                    "id": "grok-tool-{}".format(len(tools) + 1),
                    "call_id": "grok-tool-{}".format(len(tools) + 1),
                    "args_chars": 0,
                    "output_chars": 0,
                    "output_tokens": 0,
                    "result_available": True,
                    "error": bool(tool["error"]),
                    "skills": [],
                })
            publish = self._publishable(parsed, turn)
            total = sum(usage[key] for key in (
                "input_tokens", "output_tokens", "cache_read_input_tokens",
            )) if publish else None
            execution_cost = sum(breakdown.values()) if turn["cost"] is not None else 0.0
            timing = bool(turn["start"] and turn["end"] >= turn["start"] and turn["elapsed_s"])
            availability = compat["metric_availability"](
                "grok",
                cost=turn["cost"] is not None and all_cost,
                tokens=turn["token_available"] and all_tokens,
                input_tokens=turn["token_available"] and all_tokens,
                output_tokens=turn["token_available"] and all_tokens,
                cache=turn["cache_available"] and all_cache,
                throughput=timing,
                context=turn["context_available"],
                timing=timing,
                tool_results=bool(tools),
            )
            context_tokens = turn["prompt"] or 0
            context_pct = (
                context_tokens / parsed["window"]
                if turn["context_available"] and parsed["window"] else None
            )
            series.append({
                "i": turn["index"],
                "in": usage["input_tokens"],
                "out": usage["output_tokens"],
                "cost": execution_cost if turn["cost"] is not None else 0.0,
                "fresh_input": usage["input_tokens"],
                "cache": usage["cache_read_input_tokens"],
                "cache_read": usage["cache_read_input_tokens"],
                "cache_write": 0,
                "think": bool(turn["reasoning"]),
                "tools": len(tools),
                "side": False,
                "reasoning": turn["reasoning"] or 0,
                "reasoning_ms": 0,
                "context_pct": context_pct,
                "context_tokens": context_tokens,
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
                    "input": usage["input_tokens"] if publish else None,
                    "output": usage["output_tokens"] if publish else None,
                    "reasoning": turn["reasoning"] if publish and turn["reasoning"] is not None else None,
                    "retrieval": 0 if publish else None,
                    "fresh_input": usage["input_tokens"] if publish else None,
                    "cache": usage["cache_read_input_tokens"] if publish else None,
                    "cache_read": usage["cache_read_input_tokens"] if publish else None,
                    "cache_write": 0 if publish else None,
                    "total": total,
                },
                "cost": execution_cost if turn["cost"] is not None else 0.0,
                "discount_usd": 0.0,
                "cost_breakdown": breakdown,
                "tools": tools,
                "tool_count": len(tools),
                "model_calls": 1,
                "reasoning_tokens": (turn["reasoning"] or 0) if publish else None,
                "reasoning_duration_ms": 0,
                "context_tokens": context_tokens,
                "context_window": parsed["window"] or 0,
                "context_pct": context_pct,
                "duration_ms": turn["elapsed_s"] * 1000 if turn["elapsed_s"] else None,
                "wait_duration_ms": turn["elapsed_s"] * 1000 if turn["elapsed_s"] else None,
                "summary": "Execution {}: {} tools".format(turn["index"], len(tools)),
                "user_message": "",
                "user_input": "",
                "availability": availability,
            })
            trace.append(compat["trace_event"](
                turn["end"], "complete", "Model call", "Content excluded", turn["index"],
                severity="good", model=turn["model"],
                cost=execution_cost if turn["cost"] is not None else None,
                native_type="inference", native_subtype="inference_done",
            ))
            for tool in tools:
                trace.append(compat["trace_event"](
                    turn["end"], "tool_call", tool["display"], "Payload excluded", turn["index"],
                    tool=tool["name"], severity="warn" if tool.get("error") else "tool",
                    model=turn["model"], native_type="tool_completed", native_subtype="tool_completed",
                ))
            # A session whose total is unavailable must not credit the last model.
            if all_tokens and publish:
                tot["input"] += usage["input_tokens"]
                tot["cache_read"] += usage["cache_read_input_tokens"]
                tot["output"] += usage["output_tokens"]
                model_tok[turn["model"]] += total
            if all_cost and turn["cost"] is not None:
                for key in cost:
                    cost[key] += breakdown[key]
                model_cost[turn["model"]] += execution_cost
            if timing:
                intervals.append((turn["start"], turn["end"]))
                wait_samples.append({
                    "duration_s": turn["elapsed_s"],
                    "generation_s": turn["elapsed_s"],
                    "output_tokens": usage["output_tokens"],
                    "tool_calls": len(tools),
                    "ts": turn["end"],
                    "ttft_s": turn["ttft_s"],
                })
        primary = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        total_tokens, total_cost = sum(tot.values()), sum(cost.values())
        tool_data = compat["tool_summary"](executions)
        analyses = compat["analysis_block"](
            tot, total_cost if all_cost else 0.0, 0,
            sum(1 for turn in turns if turn["reasoning"]),
            0.0, model_tok, model_cost, tool_data, 0.0, 0, len(executions),
        )
        active = merge_execution_intervals(intervals)
        throughput = performance_summary(wait_samples, tot["output"])
        source = dict(source)
        source["context_latest"] = turns[-1]["prompt"] or 0
        source["context_window"] = parsed["window"] or 0
        source["token_estimate"] = False
        source["cache_savings_available"] = False
        availability = compat["metric_availability"](
            "grok",
            cost=all_cost,
            tokens=all_tokens,
            input_tokens=all_tokens,
            output_tokens=all_tokens,
            cache=all_cache,
            throughput=throughput["available"],
            context=bool(parsed["window"]) and all(turn["context_available"] for turn in turns),
            timing=bool(intervals),
            tool_results=any(turn["tools"] for turn in turns),
        )
        note = PRICING_NOTE
        if parsed["multiple_models"]:
            note = "This Grok session used more than one model, so cost is unavailable."
        elif parsed["models_unreliable"]:
            note = "The Grok chat history was too large to scan for every model, so cost is unavailable."
        elif parsed["truncated"]:
            note = "The Grok inference log exceeded the read limit, so this session's cost is unavailable."
        elif not all_cost:
            note = "Measured Grok token counts; no configured public price for this model."
        state = compat["build_state"](
            source, tot, cost if all_cost else {key: 0.0 for key in cost},
            total_tokens if all_tokens else 0,
            total_cost if all_cost else 0.0,
            series, executions, trace,
            {"reasoning": sum(turn["reasoning"] or 0 for turn in turns), "output": tot["output"],
             "retrieval": 0, "coordination": 0},
            analyses, [],
            min((turn["start"] for turn in turns if turn["start"]), default=0),
            max((turn["end"] for turn in turns if turn["end"]), default=0),
            0, None, 0, True, primary, note,
            {
                "duration_s": active,
                "available": bool(intervals),
                "reported_executions": len(intervals),
                "observed_executions": len(intervals),
                "execution_count": len(executions),
                "basis": "measured",
            },
            wait_samples,
            availability=availability,
        )
        state["throughput"] = throughput
        state["discount_usd"] = 0.0
        state["semantic_available"] = False
        state["token_estimate"] = False
        state["pricing_note"] = note
        return state

    def summarize_legacy(self, source, unused=None):
        del unused
        compat = self._require_compatibility()
        parsed = self._parsed(source)
        turns = parsed["turns"]
        model_cost, model_tok, model_stats, model_daily = (
            defaultdict(float), defaultdict(int), {}, {}
        )
        day_cost, tool_calls, intervals, models, wait_samples, context_samples = (
            defaultdict(float), [], [], set(), [], []
        )
        total_cost = input_tokens = output_tokens = 0
        complete = (
            not parsed["truncated"]
            and not parsed["multiple_models"]
            and not parsed["models_unreliable"]
        )
        all_tokens = complete and bool(turns) and all(turn["token_available"] for turn in turns)
        all_cache = complete and bool(turns) and all(turn["cache_available"] for turn in turns)
        all_cost = complete and bool(turns) and all(turn["cost"] is not None for turn in turns)
        all_context = complete and bool(turns) and all(turn["context_available"] for turn in turns)
        for turn in turns:
            usage = self._legacy_usage(turn)
            breakdown = turn["cost"] or {
                "input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0,
            }
            value = sum(breakdown.values()) if turn["cost"] is not None else 0.0
            total = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["output_tokens"]
            publish = self._publishable(parsed, turn)
            # Token totals and per-model aggregates move together. A session
            # that is unavailable does not keep a measured total on the last model.
            if all_tokens and publish:
                input_tokens += usage["input_tokens"] + usage["cache_read_input_tokens"]
                output_tokens += usage["output_tokens"]
                model_tok[turn["model"]] += total
                compat["add_model_summary"](
                    model_stats, turn["model"], usage,
                    value if all_cost else 0.0,
                    cost_available=all_cost,
                )
                compat["add_model_daily"](
                    model_daily, turn["model"], usage,
                    value if all_cost else 0.0,
                    turn["end"], cost_available=all_cost,
                )
            if all_cost and turn["cost"] is not None:
                total_cost += value
                model_cost[turn["model"]] += value
            models.add(turn["model"])
            if all_cost and turn["end"] and turn["cost"] is not None:
                day_cost[time.strftime("%Y-%m-%d", time.localtime(turn["end"]))] += value
            if turn["elapsed_s"] and turn["end"]:
                intervals.append((turn["start"], turn["end"]))
                if turn["prompt"] is not None:
                    context_samples.append(turn["prompt"])
                wait_samples.append({
                    "duration_s": turn["elapsed_s"],
                    "generation_s": turn["elapsed_s"],
                    "output_tokens": usage["output_tokens"],
                    "tool_calls": len(turn["tools"]),
                    "ts": turn["end"],
                    "ttft_s": turn["ttft_s"],
                })
            for tool in turn["tools"]:
                ident = compat["tool_identity"](tool["name"])
                tool_calls.append({
                    **ident,
                    "output_tokens": 0,
                    "error": bool(tool["error"]),
                    "ts": turn["end"],
                    "skills": [],
                })
        throughput = performance_summary(wait_samples, output_tokens)
        availability = compat["metric_availability"](
            "grok",
            cost=all_cost,
            tokens=all_tokens,
            input_tokens=all_tokens,
            output_tokens=all_tokens,
            cache=all_cache,
            throughput=throughput["available"],
            context=all_context,
            timing=bool(intervals),
            tool_results=bool(tool_calls),
        )
        active = merge_execution_intervals(intervals)
        source = dict(source)
        source["token_estimate"] = False
        first_ts = min((turn["start"] for turn in turns if turn["start"]), default=source.get("mtime") or 0)
        last_ts = max((turn["end"] for turn in turns if turn["end"]), default=source.get("mtime") or 0)
        row = compat["summary_row"](
            source, None,
            total_cost if all_cost else 0.0,
            sum(model_tok.values()) if all_tokens else 0,
            len(turns), models, first_ts, last_ts, model_cost, model_tok, day_cost,
            True,
            {"duration_s": active, "available": bool(intervals), "basis": "measured"},
            input_tokens if all_tokens else 0,
            output_tokens if all_tokens else 0,
            model_stats, list(model_daily.values()), wait_samples, wait_samples,
            availability,
        )
        primary = (
            max(model_tok, key=model_tok.get) if model_tok
            else source.get("model") or "unknown-model"
        )
        row["primary_model"] = primary
        row["throughput"] = throughput
        latest = context_samples[-1] if context_samples else 0
        row["context"] = {
            "latest": latest,
            "window": parsed["window"],
            "latest_pct": (latest / parsed["window"]) if parsed["window"] and latest else None,
            "estimated": False,
        }
        limit = int(compat.get("context_sample_limit") or 24)
        row["_context_samples"] = context_samples[-limit:]
        row["terminal"] = False
        row["token_estimate"] = False
        row["provenance"] = compat["usage_provenance"]([row])
        row["usage_basis"] = row["provenance"]["usage_basis"]
        row["_tool_evidence"] = compat["summarize_tool_evidence"](tool_calls)
        return row

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
        directory = self._directory_for(source.session_id)
        if directory is not None:
            models, models_unreliable = self._models_in(directory)
        else:
            models, models_unreliable = (), False
        model = models[-1] if models else (
            source.model_ref.model_id if source.model_ref else "unknown-model"
        )
        record = {
            "id": source.session_id,
            "model": model or "unknown-model",
            "model_count": len(models) if models else (0 if not model or model == "unknown-model" else 1),
            "models_unreliable": models_unreliable,
            "mtime": source.activity_mtime,
        }
        parsed = self._parsed(record)
        turns = parsed["turns"]
        complete = (
            not parsed["truncated"]
            and not parsed["multiple_models"]
            and not parsed["models_unreliable"]
        )
        tokens_available = complete and bool(turns) and all(turn["token_available"] for turn in turns)
        cache_available = complete and bool(turns) and all(turn["cache_available"] for turn in turns)
        cost_available = complete and bool(turns) and all(turn["cost"] is not None for turn in turns)
        intervals = [
            (turn["start"], turn["end"]) for turn in turns
            if turn["start"] and turn["end"] >= turn["start"]
        ]
        active = merge_execution_intervals(intervals)
        warnings = []
        if parsed["multiple_models"]:
            warnings.append(ParseWarning(
                "multiple_models", "This Grok session used more than one model.",
            ))
        if parsed["models_unreliable"]:
            warnings.append(ParseWarning(
                "models_unreliable",
                "The Grok chat history was too large to scan for every model.",
            ))
        if not tokens_available:
            warnings.append(ParseWarning(
                "usage_unavailable", "Grok token evidence was unavailable.",
            ))
        if parsed["truncated"]:
            warnings.append(ParseWarning(
                "history_truncated", "The Grok inference log was bounded.",
            ))
        fresh = sum(turn["fresh"] for turn in turns)
        cached = sum(turn["cache_read"] for turn in turns)
        output = sum(turn["output"] for turn in turns)
        cost = sum(sum(turn["cost"].values()) for turn in turns if turn["cost"])
        return NormalizedSession(
            source=source,
            started_at=_date(min((turn["start"] for turn in turns if turn["start"]), default=0)),
            ended_at=_date(max((turn["end"] for turn in turns if turn["end"]), default=0)),
            usage=UsageEvidence(
                self._available(fresh, tokens_available),
                self._available(output, tokens_available),
                self._available(cached, cache_available),
                EvidenceValue.unavailable(),
                self._available(cost, cost_available, EvidenceBasis.ESTIMATED),
            ),
            timing=TimingEvidence(
                self._available(active, bool(intervals), EvidenceBasis.MEASURED),
                EvidenceValue.unavailable(),
                EvidenceValue.unavailable(),
            ),
            tools=tuple(
                ToolEvent(tool["name"], "tool", "error" if tool["error"] else "success")
                for turn in turns for tool in turn["tools"]
            )[:MAX_TOOLS],
            turns=tuple(
                TurnSummary(
                    turn["index"], _date(turn["start"]), _date(turn["end"]),
                    self._available(turn["output"], turn["token_available"]),
                )
                for turn in turns
            ) if detail is DetailLevel.FULL else (),
            pricing_basis="xai_public_rates" if cost_available else None,
            capabilities=self.descriptor.capabilities,
            warnings=tuple(warnings),
            detail=detail,
        )

    def deletion_plan(self, source):
        del source
        return DeletionPlan.deny("Grok session storage is read-only.")


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
