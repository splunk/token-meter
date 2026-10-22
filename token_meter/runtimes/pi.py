"""Native read-only adapter for Pi coding agent JSONL session evidence."""

import glob
import hashlib
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
from token_meter.domain.redaction import (
    has_account_at,
    has_secret_token as _has_secret_token,
    is_host_like as _is_host_like,
)
from token_meter.domain.timing import merge_execution_intervals, performance_summary
from token_meter.domain.usage import distribute_reported_cost_counts, format_usd


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000
MAX_SOURCES = 2_000
MAX_TURNS = 2_000
MAX_TOOLS = 2_000
MAX_SUBAGENT_RUNS = 500
SUBAGENT_TOOL_NAME = "subagent"
# A queued request is compacted to a short preview, so only the leading slice
# of a typed request is ever needed. Cap it before compaction: collapsing
# whitespace can only shorten a slice, never lengthen it, so anything past the
# cap cannot reach the preview. Matches the OpenCode adapter's bound.
WORK_REQUEST_TEXT_CAP = 8 * 1024
_MODEL_FILE_EXTENSIONS = (
    ".gguf", ".safetensors", ".ckpt", ".onnx", ".pt", ".pth", ".bin",
    ".rsa", ".pem", ".key", ".crt", ".cer", ".pub",
)
_MODEL_PATH_ROOT_SEGMENTS = frozenset((
    "users", "home", "etc", "var", "tmp", "opt", "usr", "private",
    "library", "applications", "documents", "desktop", "downloads",
    "localhost",
))


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


def _work_request_text(content, limit=WORK_REQUEST_TEXT_CAP):
    """Return a Pi user turn's typed request for Work insights, or "".

    Pi's stored user turns can carry credentials, account data, and host
    shapes, so text bound for the Work classifier passes the same gate Pi
    applies to every value it surfaces. All three predicates are the shared
    ones the OpenCode adapter runs, so the two adapters drop the same
    requests. A request that trips a check is dropped whole; it is never
    partially scrubbed. The cap above bounds what is read before the adapter
    compacts it to a short preview.
    """
    if isinstance(content, str):
        pieces = (content,)
    elif isinstance(content, list):
        pieces = tuple(
            item.get("text")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    else:
        return ""
    text = " ".join(piece for piece in pieces if piece).strip()
    if not text:
        return ""
    if _has_secret_token(text) or _is_host_like(text) or has_account_at(text):
        return ""
    return text[:limit]


def _safe_agent_role(value, limit=64):
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
        or _has_secret_token(text)
    ):
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", text):
        return text
    return ""


def _public_agent_id(*parts):
    """Return a stable public agent ID without exposing session identity."""
    material = "\0".join(str(part or "") for part in parts)
    if not material.strip("\0"):
        return ""
    digest = hashlib.sha256(
        b"token-meter:pi-agent:v1\0" + material.encode("utf-8", "replace")
    ).hexdigest()
    return "pi-agent-" + digest


_PUBLIC_MODEL_REGISTRY_PREFIXES = frozenset(("hf.co", "huggingface.co"))


def _model_id_is_path_like(text):
    """Reject strings shaped like a filesystem or host path, not a model."""
    segments = text.split("/")
    # Only a leading public registry (hf.co/org/model) may be a host name.
    registry_prefix = (
        len(segments) > 1
        and segments[0].lower() in _PUBLIC_MODEL_REGISTRY_PREFIXES
    )
    if len(segments) > 1 and not registry_prefix:
        first = segments[0].lower()
        if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?", first):
            # Host-shaped namespace such as example.com/secret.
            return True
        if first.split(":", 1)[0] in _MODEL_PATH_ROOT_SEGMENTS:
            return True
    for position, segment in enumerate(segments):
        if not segment or segment.startswith("."):
            return True
        if _is_host_like(segment) and not (registry_prefix and position == 0):
            # A host, IP, or host:port in any segment, e.g.
            # ollama/10.0.0.5:11434 or provider/host.example.com:8443/m.
            return True
        if segment.lower().endswith(_MODEL_FILE_EXTENSIONS):
            return True
    return False


# A model id's "@" introduces a version, a tag, or a leading namespace, never
# an account: providers emit ``claude-opus-4@20250514``, ``model@v2.1.0``,
# ``model@latest``, and ``@cf/meta/llama-3.1-8b``. The shared request-text
# matcher cannot decide this path, because a bare word after "@" is a benign
# tag or package scope in prose (``npm i -g @scope/pkg@latest``) but an
# account shape in a model id (``user@host``). So the model-id path keeps an
# explicit allowlist: after any "@" that is not a leading namespace, only an
# 8-digit date, a version with at most three numeric components, or the tags
# ``latest``/``default`` may follow.
_MODEL_ID_AT_SUFFIX = re.compile(
    r"(?:\d{8}|v?\d+(?:\.\d+){0,2}|latest|default)", re.IGNORECASE,
)


def _model_id_has_account_at(text):
    """Reject a model id whose "@" names an account instead of a version."""
    for position, char in enumerate(text):
        if char != "@":
            continue
        rest = text[position + 1:]
        if position == 0:
            # Leading namespace such as Cloudflare's @cf/ or @hf/.
            if not re.match(r"[a-z0-9-]+/", rest, re.IGNORECASE):
                return True
            continue
        if not _MODEL_ID_AT_SUFFIX.fullmatch(rest) or _is_host_like(rest):
            return True
    return False


def _subagent_model_id(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    lowered = text.lower()
    if (
        _model_id_is_path_like(text)
        or not text or len(text) > 120
        or any(char.isspace() for char in text)
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
        or "://" in text
        or text.startswith(("/", "\\", "~", "./", "../"))
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or not re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9._/@:+-]*", text)
        or lowered.startswith(("bearer ", "sk-", "-----begin "))
        or any(marker in lowered for marker in (
            "api_key=", "api-key=", "secret=", "token=",
        ))
        or _has_secret_token(text)
        or _is_host_like(text)
        or _model_id_has_account_at(text)
    ):
        return ""
    return text


def _subagent_stop_reason(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", text) else ""


def _subagent_arguments_role(arguments):
    """Return the only structural argument a single child call can name."""
    if not isinstance(arguments, dict):
        return ""
    return _safe_agent_role(arguments.get("agent"))


def _subagent_usage(value):
    """Normalize nested-model usage without treating a gap as a measured zero."""
    if not isinstance(value, dict):
        return None
    input_tokens = _integer(value.get("input"))
    output_tokens = _integer(value.get("output"))
    cache_read = _integer(value.get("cacheRead"))
    cache_write = _integer(value.get("cacheWrite"))
    reasoning = _integer(value.get("reasoning")) or 0
    reasoning = min(reasoning, output_tokens) if output_tokens is not None else 0
    raw_cost = value.get("cost")
    breakdown = _cost_breakdown(raw_cost)
    scalar_cost = _number(raw_cost)
    if scalar_cost is None and isinstance(raw_cost, dict):
        scalar_cost = _number(raw_cost.get("total"))
    if breakdown is not None:
        total_cost = sum(breakdown.values())
    elif scalar_cost is not None:
        # The shipped subagent tool reports a scalar cost. Allocate it across
        # the token buckets the way the provider-reported-cost helper does for
        # other scalar-cost runtimes, so the breakdown still sums to the total.
        distributed = distribute_reported_cost_counts(
            scalar_cost,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cache_read_tokens=cache_read or 0,
            cache_write_tokens=cache_write or 0,
            reasoning_tokens=reasoning,
        )
        breakdown = {
            "input": distributed["input"],
            "cache_write": distributed["cache_write"],
            "cache_read": distributed["cache_read"],
            "output": distributed["output"] + distributed["reasoning"],
        }
        total_cost = scalar_cost
    else:
        total_cost = 0.0
    input_available = input_tokens is not None
    output_available = output_tokens is not None
    cache_available = cache_read is not None and cache_write is not None
    return {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "cache_read_tokens": cache_read or 0,
        "cache_write_tokens": cache_write or 0,
        "reasoning_tokens": reasoning,
        "tokens": (
            (input_tokens or 0) + (output_tokens or 0)
            + (cache_read or 0) + (cache_write or 0)
        ),
        "input_available": input_available,
        "output_available": output_available,
        "cache_available": cache_available,
        "cost_available": breakdown is not None,
        "cost": breakdown,
        "total_cost": total_cost,
        "turns": _integer(value.get("turns")),
    }


def _subagent_result_runs(details):
    """Read only the documented structural result fields; drop child content.

    Returns the bounded runs, the number of reported result entries, and
    whether any reported entry was dropped (malformed or over the cap).
    """
    if not isinstance(details, dict):
        return (), 0, False
    results = details.get("results")
    if results is None:
        return (), 0, False
    if not isinstance(results, list):
        # A malformed results value still reports that a child ran; with no
        # top-level usage it becomes one unavailable run, not a skipped call.
        return (), 1, True
    runs = []
    dropped = False
    for index, result in enumerate(results):
        if len(runs) >= MAX_SUBAGENT_RUNS or not isinstance(result, dict):
            dropped = True
            continue
        runs.append({
            "index": index,
            "role": _safe_agent_role(result.get("agent")),
            "model": _subagent_model_id(result.get("model")),
            "usage": _subagent_usage(result.get("usage")),
            "exit_code": _integer(result.get("exitCode")),
            "stop_reason": _subagent_stop_reason(result.get("stopReason")),
        })
    return tuple(runs), len(results), dropped


def _subagent_run(evidence, *, index=0, role="", model="", usage=None,
                  activity_state="incomplete", failed=False):
    call_ts = float(evidence.get("call_ts") or 0) or None
    result_ts = float(evidence.get("result_ts") or 0) or None
    last_activity = result_ts or call_ts
    executions = 0
    if usage is not None:
        executions = usage["turns"] if usage["turns"] is not None else 1
    return {
        "call_id": evidence["call_id"],
        "index": index,
        "role": role,
        "model": model,
        "usage": usage,
        "activity_state": activity_state,
        "started_at": call_ts,
        "ended_at": result_ts if activity_state == "complete" else None,
        "last_activity_at": last_activity,
        "executions": executions,
        "failed_attempts": 1 if failed else 0,
    }


def _subagent_details_reconcile(top, runs):
    """Trust a per-child split only when it matches the authoritative total."""
    if not runs or not (
        top["input_available"] and top["output_available"]
        and top["cache_available"] and top["cost_available"]
    ):
        return False
    totals = [0, 0, 0, 0]
    cost = 0.0
    for run in runs:
        usage = run.get("usage")
        if not usage or not (
            usage["input_available"] and usage["output_available"]
            and usage["cache_available"] and usage["cost_available"]
        ):
            return False
        totals[0] += usage["input_tokens"]
        totals[1] += usage["output_tokens"]
        totals[2] += usage["cache_read_tokens"]
        totals[3] += usage["cache_write_tokens"]
        cost += usage["total_cost"]
    return totals == [
        top["input_tokens"], top["output_tokens"],
        top["cache_read_tokens"], top["cache_write_tokens"],
    ] and abs(cost - top["total_cost"]) < 1e-9


def _child_activity(run, *, failed_overall=False):
    if run["exit_code"] not in (None, 0):
        return "incomplete"
    if run["stop_reason"] in ("error", "aborted"):
        return "incomplete"
    if failed_overall and run["exit_code"] is None and not run["stop_reason"]:
        # The tool reported failure but this child carries no own verdict.
        return "incomplete"
    return "complete"


def _finalize_subagent_runs(calls):
    """Resolve collected calls into bounded observable child runs.

    Returns ``(runs, partial, truncated)``. ``partial`` is true when any call
    or child evidence was dropped, so nested totals cannot be a complete
    measurement. ``truncated`` is true when a run cap dropped observable
    child runs, so cross-session agent totals are partial as well.
    """
    runs = []
    partial = False
    truncated = False
    now = time.time()
    for evidence in calls.values():
        if len(runs) >= MAX_SUBAGENT_RUNS:
            partial = truncated = True
            break
        if not evidence["finished"]:
            call_ts = float(evidence["call_ts"] or 0)
            state = (
                "working" if call_ts and now - call_ts <= 90 else "incomplete"
            )
            runs.append(_subagent_run(
                evidence, role=evidence["role"], activity_state=state,
            ))
            continue
        details = evidence["details_runs"]
        use_details = (
            bool(details)
            and (
                evidence["usage"] is None
                or _subagent_details_reconcile(evidence["usage"], details)
            )
        )
        if use_details:
            if evidence["details_dropped"] and evidence["usage"] is None:
                # Without an authoritative total, a dropped child is missing
                # spend rather than a measured zero.
                partial = True
            if (
                len(details) >= MAX_SUBAGENT_RUNS
                and evidence["details_result_count"] > len(details)
            ):
                # The per-call result cap dropped reported children.
                truncated = True
            for run in details:
                if len(runs) >= MAX_SUBAGENT_RUNS:
                    partial = truncated = True
                    break
                state = _child_activity(
                    run, failed_overall=evidence["is_error"],
                )
                runs.append(_subagent_run(
                    evidence, index=run["index"], role=run["role"],
                    model=run["model"], usage=run["usage"],
                    activity_state=state, failed=state == "incomplete",
                ))
            continue
        if evidence["usage"] is not None:
            role = evidence["role"]
            model = ""
            if len(details) == 1:
                role = role or details[0]["role"]
                model = details[0]["model"]
            runs.append(_subagent_run(
                evidence, role=role, model=model, usage=evidence["usage"],
                activity_state=(
                    "complete" if not evidence["is_error"] else "incomplete"
                ),
                failed=evidence["is_error"],
            ))
            continue
        if evidence["details_present"] and not evidence["details_result_count"]:
            # A result with no reported results and no usage ran no child.
            continue
        runs.append(_subagent_run(
            evidence, role=evidence["role"], failed=evidence["is_error"],
        ))
    return tuple(runs), partial, truncated


def _nested_usage_totals(runs, partial=False):
    """Return additive nested spend and its explicit coverage flags."""
    totals = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "tokens": 0, "total_cost": 0.0,
        "tokens_available": not partial, "cache_available": not partial,
        "cost_available": not partial,
    }
    for run in runs or ():
        usage = run.get("usage") if isinstance(run, dict) else None
        if not usage:
            totals["tokens_available"] = False
            totals["cache_available"] = False
            totals["cost_available"] = False
            continue
        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
        totals["cache_read_tokens"] += usage["cache_read_tokens"]
        totals["cache_write_tokens"] += usage["cache_write_tokens"]
        totals["tokens"] += usage["tokens"]
        if not (usage["input_available"] and usage["output_available"]):
            totals["tokens_available"] = False
        if not usage["cache_available"]:
            totals["cache_available"] = False
        if usage["cost_available"]:
            totals["total_cost"] += usage["total_cost"]
        else:
            totals["cost_available"] = False
    return totals


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

    def _parsed(self, path, collect_work=False):
        if not self._owned_path(path):
            return {"turns": (), "corrupt": 0, "available": False,
                    "truncated": False, "subagent_runs": (),
                    "subagent_partial": False, "subagent_truncated": False,
                    "work_turns": ()}
        rows, corrupt, available, truncated = _read_jsonl(path)
        header = rows[0] if rows else {}
        if not isinstance(header, dict) or header.get("type") != "session":
            return {"turns": (), "corrupt": corrupt, "available": available,
                    "truncated": truncated, "subagent_runs": (),
                    "subagent_partial": False, "subagent_truncated": False,
                    "work_turns": ()}
        turns = []
        work_turns = []
        pending_user_ts = 0.0
        previous_ts = 0.0
        provider = model = ""
        tools_by_call_id = {}
        subagent_calls = {}
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
                if collect_work:
                    request_text = _work_request_text(message.get("content"))
                    if request_text:
                        work_turns.append({
                            "ts": pending_user_ts or 0,
                            "text": request_text,
                            "model": model_ref_for(provider, model).model_id,
                        })
            elif role == "toolresult":
                call = tools_by_call_id.get(str(message.get("toolCallId") or ""))
                if call is not None:
                    call["result_available"] = True
                    if message.get("isError") is True:
                        call["error"] = True
                evidence = subagent_calls.get(
                    str(message.get("toolCallId") or "")
                )
                if evidence is not None and not evidence["finished"]:
                    details = message.get("details")
                    evidence["finished"] = True
                    evidence["result_ts"] = ts or previous_ts
                    evidence["is_error"] = message.get("isError") is True
                    evidence["usage"] = _subagent_usage(message.get("usage"))
                    evidence["details_present"] = isinstance(details, dict)
                    (
                        evidence["details_runs"],
                        evidence["details_result_count"],
                        evidence["details_dropped"],
                    ) = _subagent_result_runs(details)
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
                    existing = subagent_calls.get(tool["id"])
                    if (
                        tool["name"] == SUBAGENT_TOOL_NAME and tool["id"]
                        and not (existing and existing["finished"])
                    ):
                        # A repeated ID never replaces finished evidence.
                        subagent_calls[tool["id"]] = {
                            "call_id": tool["id"],
                            "call_ts": ts or previous_ts,
                            "role": _subagent_arguments_role(
                                item.get("arguments")
                            ),
                            "result_ts": None,
                            "usage": None,
                            "details_present": False,
                            "details_runs": (),
                            "details_result_count": 0,
                            "details_dropped": False,
                            "finished": False,
                            "is_error": False,
                        }
                start = pending_user_ts or previous_ts or ts
                end = ts or start
                turns.append({
                    "index": len(turns) + 1, "start": start, "end": max(start, end),
                    "model": model_ref_for(provider, model).model_id,
                    "stop_reason": _subagent_stop_reason(message.get("stopReason")),
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
        subagent_runs, subagent_partial, subagent_truncated = (
            _finalize_subagent_runs(subagent_calls)
        )
        return {
            "turns": tuple(turns), "corrupt": corrupt,
            "available": available, "truncated": truncated,
            "subagent_runs": subagent_runs,
            "subagent_partial": subagent_partial,
            "subagent_truncated": subagent_truncated,
            "work_turns": tuple(work_turns),
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
        nested = _nested_usage_totals(
            parsed["subagent_runs"], parsed["subagent_partial"],
        )
        tokens_available = (
            bool(turns) and all(turn["token_available"] for turn in turns)
            and nested["tokens_available"]
        )
        cache_available = (
            bool(turns) and all(turn["cache_available"] for turn in turns)
            and nested["cache_available"]
        )
        cost_available = (
            bool(turns) and all(turn["cost"] is not None for turn in turns)
            and nested["cost_available"]
        )
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
        if parsed["truncated"] or parsed["subagent_partial"]:
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
                self._available(
                    sum(turn["input_tokens"] for turn in turns)
                    + nested["input_tokens"], tokens_available,
                ),
                self._available(
                    sum(turn["output_tokens"] for turn in turns)
                    + nested["output_tokens"], tokens_available,
                ),
                self._available(
                    sum(turn["cache_read_tokens"] for turn in turns)
                    + nested["cache_read_tokens"], cache_available,
                ),
                self._available(
                    sum(turn["cache_write_tokens"] for turn in turns)
                    + nested["cache_write_tokens"], cache_available,
                ),
                self._available(
                    sum(sum(turn["cost"].values()) for turn in turns if turn["cost"])
                    + nested["total_cost"],
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

    def _legacy_parsed(self, source, collect_work=False):
        return self._parsed(source.get("path") or "", collect_work=collect_work)

    def _legacy_usage(self, turn):
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
        runs = parsed["subagent_runs"]
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
                    "{} Pi estimate".format(format_usd(execution_cost))
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
        primary_model = max(model_tok, key=model_tok.get) if model_tok else source.get("model")
        own_output = tot["output"]
        nested = _nested_usage_totals(runs, parsed["subagent_partial"])
        for run in runs:
            usage = run.get("usage")
            if not usage:
                continue
            tot["input"] += usage["input_tokens"]
            tot["output"] += usage["output_tokens"]
            tot["cache_read"] += usage["cache_read_tokens"]
            tot["cache_write"] += usage["cache_write_tokens"]
            breakdown = usage["cost"] or {key: 0.0 for key in cost}
            for key in cost:
                cost[key] += breakdown[key]
            run_model = (
                _public_model_id(run["model"]) if run["model"]
                else "unknown-model"
            )
            model_tok[run_model] += usage["tokens"]
            model_cost[run_model] += (
                usage["total_cost"] if usage["cost_available"] else 0.0
            )
        all_tokens_available = all_tokens_available and nested["tokens_available"]
        all_cache_available = all_cache_available and nested["cache_available"]
        all_cost_available = all_cost_available and nested["cost_available"]
        total_tokens, total_cost = sum(tot.values()), sum(cost.values())
        tool_data = compat["tool_summary"](executions)
        analyses = compat["analysis_block"](
            tot, total_cost, 0, 0, 0.0, model_tok, model_cost, tool_data, 0.0, 0, len(executions),
        )
        active = merge_execution_intervals(intervals)
        source = dict(source)
        source["context_latest"] = executions[-1]["context_tokens"] if executions else 0
        throughput = performance_summary(wait_samples, own_output)
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
        # Request text is Work-only: read it (and its redaction gate) only when
        # the feature is on, so a native or off-path summary never touches it.
        work_on = compat["work_insights_enabled"]()
        parsed = self._legacy_parsed(source, collect_work=work_on)
        turns = parsed["turns"]
        runs = parsed["subagent_runs"]
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
        own_tokens_available = all_tokens_available
        own_cache_available = all_cache_available
        own_cost_available = all_cost_available
        own_input_tokens = input_tokens
        own_output_tokens = output_tokens
        own_tokens = sum(model_tok.values())
        own_cost = sum(model_cost.values())
        own_model_tok = dict(model_tok)
        own_cache_read = sum(turn["cache_read_tokens"] for turn in turns)
        own_cache_write = sum(turn["cache_write_tokens"] for turn in turns)
        own_reasoning = sum(turn["reasoning_tokens"] for turn in turns)
        own_active = merge_execution_intervals(intervals)
        own_start = min((turn["start"] for turn in turns if turn["start"]), default=0)
        own_end = max((turn["end"] for turn in turns if turn["end"]), default=0)
        primary_own_model = (
            max(own_model_tok, key=own_model_tok.get) if own_model_tok
            else source.get("model")
        )
        own_terminal = bool(turns and turns[-1]["stop_reason"] == "stop")
        nested = _nested_usage_totals(runs, parsed["subagent_partial"])
        for run in runs:
            usage = run.get("usage")
            if not usage:
                continue
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            value = usage["total_cost"] if usage["cost_available"] else 0.0
            total_cost += value
            run_model = (
                _public_model_id(run["model"]) if run["model"]
                else "unknown-model"
            )
            model_tok[run_model] += usage["tokens"]
            model_cost[run_model] += value
            models.add(run_model)
            usage_row = {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cache_read_input_tokens": usage["cache_read_tokens"],
                "cache_creation_input_tokens": usage["cache_write_tokens"],
            }
            compat["add_model_summary"](
                model_stats, run_model, usage_row, value,
                cost_available=usage["cost_available"],
            )
            run_ts = run["last_activity_at"] or 0
            compat["add_model_daily"](
                model_daily, run_model, usage_row, value, run_ts,
                cost_available=usage["cost_available"],
            )
            if run_ts:
                day_cost[time.strftime("%Y-%m-%d", time.localtime(run_ts))] += value
        all_tokens_available = all_tokens_available and nested["tokens_available"]
        all_cache_available = all_cache_available and nested["cache_available"]
        all_cost_available = all_cost_available and nested["cost_available"]
        agent_records = self._subagent_records(source, runs, own={
            "input_tokens": own_input_tokens,
            "output_tokens": own_output_tokens,
            "cache_read_tokens": own_cache_read,
            "cache_write_tokens": own_cache_write,
            "reasoning_tokens": own_reasoning,
            "tokens": own_tokens,
            "cost": own_cost,
            "cost_available": own_cost_available,
            "tokens_available": own_tokens_available,
            "model": primary_own_model,
            "executions": len(turns),
            "terminal": own_terminal,
            "started_at": own_start or None,
            "last_activity_at": own_end or None,
            "work_time_s": own_active,
        })
        # Session pace diagnostics cover parent model calls only, so the
        # coverage denominator must not grow with nested child output.
        throughput = performance_summary(wait_samples, own_output_tokens)
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
        row["primary_model"] = (
            max(own_model_tok, key=own_model_tok.get) if own_model_tok
            else source.get("model")
        )
        # summary_row recomputes throughput from the full token total, which
        # now includes child output; restore the parent-scoped value.
        row["throughput"] = throughput
        row["context"] = {
            "latest": context_samples[-1] if context_samples else 0,
            "window": None, "latest_pct": None, "estimated": False,
        }
        row["_context_samples"] = context_samples[-compat["context_sample_limit"]:]
        # Session-row liveness keeps the pre-existing nonterminal behavior so
        # sessions without subagent calls are unchanged. Agent records use the
        # recorded stop reason for their own lifecycle state.
        row["terminal"] = False
        row["_tool_evidence"] = compat["summarize_tool_evidence"](tool_calls)
        if agent_records:
            row["_agent_records"] = list(agent_records)
            # Dropped child evidence, including runs dropped by a run cap,
            # keeps the affected cross-session agent totals partial without
            # touching the inventory display limit. Rows with complete
            # evidence do not carry the key.
            if parsed["subagent_partial"] or parsed.get("subagent_truncated"):
                row["_agent_records_partial"] = True
        compact_text = compat["compact_text"]
        work_turns = [
            {"ts": turn["ts"], "text": compact_text(turn["text"], 90),
             "model": turn["model"]}
            for turn in parsed["work_turns"]
        ]
        work_events = [
            (turn["end"] or turn["start"], sum((turn["cost"] or {}).values()),
             turn["model"], "")
            for turn in turns
        ] if work_on else ()
        compat["attach_work_requests"](
            row, work_turns,
            events=work_events,
            actions=compat["pi_work_actions"](turns) if work_on else (),
        )
        return row

    def _subagent_records(self, source, runs, *, own):
        """Build one root record and one spawned record per observable run."""
        runs = tuple(runs or ())
        if not runs:
            return ()
        session_id = str(source.get("id") or "")
        root_id = _public_agent_id("root", session_id)
        if not root_id:
            return ()
        now = time.time()
        client = source.get("label") or "Pi"
        root_last = (
            own.get("last_activity_at") or float(source.get("mtime") or 0) or None
        )
        own_model = own.get("model") or "unknown-model"
        terminal = bool(own.get("terminal"))
        records = [{
            "id": root_id,
            "parent_id": None,
            "session_id": session_id or None,
            "runtime": "pi",
            "client": client,
            "kind": "root",
            "depth": 0,
            "label": "",
            "role": None,
            "model": own_model,
            "activity_state": (
                "complete" if terminal
                else "working" if root_last and now - float(root_last) <= 90
                else "incomplete"
            ),
            "started_at": own.get("started_at"),
            "ended_at": own.get("last_activity_at") if terminal else None,
            "last_activity_at": root_last,
            "input_tokens": own.get("input_tokens") or 0,
            "output_tokens": own.get("output_tokens") or 0,
            "cache_read_tokens": own.get("cache_read_tokens") or 0,
            "cache_write_tokens": own.get("cache_write_tokens") or 0,
            "reasoning_tokens": own.get("reasoning_tokens") or 0,
            "tokens": own.get("tokens") if own.get("tokens_available") else None,
            "tokens_available": bool(own.get("tokens_available")),
            "cost": own.get("cost") if own.get("cost_available") else None,
            "cost_available": bool(own.get("cost_available")),
            "executions": own.get("executions") or 0,
            "attempts": own.get("executions") or 0,
            "retries": 0,
            "failed_attempts": 0,
            "tool_calls": 0,
            "work_time_s": own.get("work_time_s"),
        }]
        for run in runs:
            usage = run.get("usage")
            cache_available = bool(usage and usage["cache_available"])
            # The child token total includes cache buckets, so a missing
            # cache report leaves that total partial rather than measured.
            tokens_available = bool(
                usage and usage["input_available"] and usage["output_available"]
                and cache_available
            )
            cost_available = bool(usage and usage["cost_available"])
            started = run.get("started_at")
            finished = run.get("last_activity_at")
            work_time = (
                max(0.0, float(finished) - float(started))
                if started and finished and finished >= started else None
            )
            records.append({
                "id": _public_agent_id(
                    "child", session_id, run["call_id"], str(run["index"]),
                ),
                "parent_id": root_id,
                "session_id": None,
                "runtime": "pi",
                "client": client,
                "kind": "spawned",
                "depth": 1,
                "label": "",
                "role": run.get("role") or None,
                "model": (
                    _public_model_id(run["model"]) if run.get("model")
                    else "unknown-model"
                ),
                "activity_state": run.get("activity_state") or "incomplete",
                "started_at": started,
                "ended_at": run.get("ended_at"),
                "last_activity_at": finished,
                "input_tokens": usage["input_tokens"] if usage else 0,
                "output_tokens": usage["output_tokens"] if usage else 0,
                "cache_read_tokens": usage["cache_read_tokens"] if usage else 0,
                "cache_write_tokens": usage["cache_write_tokens"] if usage else 0,
                "reasoning_tokens": usage["reasoning_tokens"] if usage else 0,
                "tokens": usage["tokens"] if tokens_available else None,
                "tokens_available": tokens_available,
                "cache_available": cache_available,
                "cost": usage["total_cost"] if cost_available else None,
                "cost_available": cost_available,
                "executions": run.get("executions") or 0,
                "attempts": 1 if started else 0,
                "retries": 0,
                "failed_attempts": run.get("failed_attempts") or 0,
                "tool_calls": 0,
                "work_time_s": work_time,
            })
        return tuple(records)

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
