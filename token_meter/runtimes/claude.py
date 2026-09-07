"""Native adapter for Claude Code and Claude Desktop JSONL evidence."""

import glob
import hashlib
import json
import math
import os
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
from token_meter.domain.usage import normalize_reported_token_count


DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_DETAIL_TURNS = 2_000
MAX_TOOL_EVENTS = 2_000
ACTIVITY_TAIL_BYTES = 1024 * 1024
ACTIVITY_CACHE_LIMIT = 512


def _file_signature(path):
    try:
        stat = os.stat(path)
        return (str(stat.st_mtime_ns), str(stat.st_size))
    except OSError:
        return ("0", "0")


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _timestamp(value):
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _datetime(value):
    seconds = _timestamp(value) if isinstance(value, str) else float(value or 0)
    return datetime.fromtimestamp(seconds).astimezone() if seconds else None


def _safe_int(value):
    normalized, _ = normalize_reported_token_count(value)
    return normalized


def _normalized_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    input_tokens, input_reported = normalize_reported_token_count(
        usage.get("input_tokens")
    )
    cache_read, cache_read_reported = normalize_reported_token_count(
        usage.get("cache_read_input_tokens", 0)
    )
    cache_write, cache_write_reported = normalize_reported_token_count(
        usage.get("cache_creation_input_tokens", 0)
    )
    cache_creation = usage.get("cache_creation")
    cache_write_5m = 0
    cache_write_1h = 0
    cache_write_unspecified = 0
    cache_duration_valid = True
    cache_duration_complete = cache_write == 0
    if isinstance(cache_creation, dict):
        cache_write_5m, cache_write_5m_reported = normalize_reported_token_count(
            cache_creation.get("ephemeral_5m_input_tokens")
        )
        cache_write_1h, cache_write_1h_reported = normalize_reported_token_count(
            cache_creation.get("ephemeral_1h_input_tokens")
        )
        cache_duration_valid = (
            cache_write_5m_reported
            and cache_write_1h_reported
            and cache_write_5m + cache_write_1h == cache_write
        )
        for name, value in cache_creation.items():
            if name in (
                "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens",
            ):
                continue
            count, reported = normalize_reported_token_count(value)
            if not reported or count:
                cache_duration_valid = False
        cache_duration_complete = cache_duration_valid
    elif cache_creation is None:
        cache_write_unspecified = cache_write
    else:
        cache_duration_valid = False

    speed = usage.get("speed")
    speed_valid = speed in (None, "", "standard", "fast")
    service_tier = usage.get("service_tier")
    service_tier_valid = service_tier in (None, "", "standard")
    inference_geo = usage.get("inference_geo")
    inference_geo_valid = inference_geo in (None, "", "global", "us")
    server_tool_use = usage.get("server_tool_use")
    server_tool_valid = server_tool_use is None or isinstance(server_tool_use, dict)
    web_search_requests = 0
    web_fetch_requests = 0
    if isinstance(server_tool_use, dict):
        for name, value in server_tool_use.items():
            count, reported = normalize_reported_token_count(value)
            if not reported or (name not in (
                "web_search_requests", "web_fetch_requests",
            ) and count):
                server_tool_valid = False
            if name == "web_search_requests":
                web_search_requests = count
            elif name == "web_fetch_requests":
                web_fetch_requests = count

    billing_available = (
        cache_duration_valid
        and speed_valid
        and service_tier_valid
        and inference_geo_valid
        and server_tool_valid
    )
    output_tokens, output_reported = normalize_reported_token_count(
        usage.get("output_tokens")
    )
    reasoning_value = (
        usage.get("thinking_tokens")
        if "thinking_tokens" in usage
        else usage.get("reasoning_output_tokens")
    )
    reasoning_tokens, reasoning_reported = normalize_reported_token_count(
        reasoning_value
    )
    input_available = (
        input_reported and cache_read_reported and cache_write_reported
    )
    reasoning_available = (
        output_reported
        and reasoning_reported
        and reasoning_tokens <= output_tokens
    )
    return {
        **usage,
        "input_tokens": input_tokens if input_available else 0,
        "cache_read_input_tokens": cache_read if input_available else 0,
        "cache_creation_input_tokens": cache_write if input_available else 0,
        "cache_creation_5m_input_tokens": (
            cache_write_5m if input_available and cache_duration_valid else 0
        ),
        "cache_creation_1h_input_tokens": (
            cache_write_1h if input_available and cache_duration_valid else 0
        ),
        "cache_creation_unspecified_input_tokens": (
            cache_write_unspecified if input_available and cache_duration_valid else 0
        ),
        "output_tokens": output_tokens if output_reported else 0,
        "input_available": input_available,
        "output_available": output_reported,
        "billing_available": billing_available,
        "billing_complete": billing_available and cache_duration_complete,
        "cache_duration_available": input_available and cache_duration_valid,
        "web_search_requests": web_search_requests if server_tool_valid else 0,
        "web_fetch_requests": web_fetch_requests if server_tool_valid else 0,
        "reasoning_output_tokens": reasoning_tokens if reasoning_available else 0,
        "reasoning_available": reasoning_available,
    }


def _has_thinking_block(content):
    return any(
        isinstance(block, dict)
        and block.get("type") in ("thinking", "redacted_thinking")
        for block in (content or ())
    )


def _with_thinking_observation(usage, content):
    """Keep structural thinking evidence without inventing a token split."""
    return {**usage, "thinking_observed": _has_thinking_block(content)}


def _cost_coverage_complete(usage, priced):
    """Ignore unpriced records that contain no billable usage."""
    if not usage["input_available"] or not usage["output_available"]:
        return False
    if usage.get("billing_available") is False:
        return False
    if priced and usage.get("billing_complete") is not False:
        return True
    return not any(
        usage.get(field, 0)
        for field in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        )
    )


def _compact(value, limit=90):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


class ClaudeRuntimeAdapter:
    """Own Claude discovery, logical-message deduplication, and projections."""

    descriptor = RuntimeDescriptor(
        "claude",
        "Claude",
        frozenset(("sessions", "models", "tools", "quota")),
        "runtime.claude",
        "runtime-claude",
        "anthropic",
    )

    def __init__(self, projects_root, desktop_data_roots=(), project_resolver=None,
                 project_decoder=None, compatibility=None, path_cache=None,
                 default_model=DEFAULT_MODEL,
                 max_detail_turns=MAX_DETAIL_TURNS,
                 max_tool_events=MAX_TOOL_EVENTS):
        self.projects_root = Path(os.path.abspath(os.path.expanduser(str(projects_root))))
        self.desktop_data_roots = tuple(
            Path(os.path.abspath(os.path.expanduser(str(path))))
            for path in desktop_data_roots
        )
        self.project_resolver = project_resolver or (lambda value: value)
        self.project_decoder = project_decoder or (lambda value: value.strip("-").replace("-", "/"))
        self.compatibility = dict(compatibility or {})
        self.path_cache = path_cache
        self.default_model = str(default_model or DEFAULT_MODEL)
        self.max_detail_turns = max(1, int(max_detail_turns))
        self.max_tool_events = max(1, int(max_tool_events))
        self._cwd_cache = {}
        self._activity_cache = {}
        self._message_id_cache = {}

    def _glob(self, pattern, recursive=False):
        if self.path_cache is not None:
            return self.path_cache.paths(pattern, recursive=recursive)
        return tuple(glob.glob(pattern, recursive=recursive))

    def desktop_metadata_paths(self, root=None):
        if root:
            return tuple(
                path for path in self._glob(
                    os.path.join(str(root), "**", "local_*.json"),
                    recursive=True,
                )
                if "skills-plugin" not in Path(path).parts
            )
        paths = []
        for data_root in self.desktop_data_roots:
            for session_dir in ("claude-code-sessions", "local-agent-mode-sessions"):
                session_root = data_root / session_dir
                paths.extend(self._glob(str(session_root / "local_*.json")))
                for branch in self._glob(str(session_root / "*")):
                    if (os.path.basename(branch) == "skills-plugin"
                            or not os.path.isdir(branch)):
                        continue
                    paths.extend(self._glob(
                        os.path.join(branch, "**", "local_*.json"),
                        recursive=True,
                    ))
        return tuple(paths)

    def desktop_index(self, root=None):
        result = {}
        for path in self.desktop_metadata_paths(root):
            try:
                with open(path, encoding="utf-8") as handle:
                    row = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict) or not row.get("cliSessionId"):
                continue
            cli_id = str(row["cliSessionId"])
            title = _compact(row.get("title"), 90)
            if title.lower() in ("untitled", "untitled session"):
                title = ""
            source_kind = (
                "agent" if "{}local-agent-mode-sessions{}".format(os.sep, os.sep) in path
                else "project"
            )
            origin_cwd = row.get("originCwd") or ""
            selected = row.get("userSelectedFolders") or []
            selected_cwd = next(
                (folder for folder in selected
                 if isinstance(folder, str) and folder.strip()), "",
            ) if isinstance(selected, list) else ""
            raw_cwd = origin_cwd or selected_cwd or row.get("cwd") or ""
            no_project = bool(
                source_kind == "agent" and not origin_cwd and not selected_cwd and
                os.path.basename(raw_cwd) == "outputs"
            )
            candidate = {
                "client": "claude_desktop",
                "label": "Claude Desktop",
                "desktop_session_id": row.get("sessionId") or
                                      os.path.basename(path).rsplit(".", 1)[0],
                "cli_session_id": cli_id,
                "cwd": raw_cwd,
                "project": "No project" if no_project else self.project_resolver(raw_cwd),
                "source_kind": source_kind,
                "title": title or None,
                "model": row.get("model"),
                "metadata_path": path,
                "metadata_mtime": _mtime(path),
                "last_activity_ms": _safe_int(row.get("lastActivityAt")),
            }
            previous = result.get(cli_id)
            if not previous or (
                candidate["last_activity_ms"], candidate["metadata_mtime"]
            ) > (previous["last_activity_ms"], previous["metadata_mtime"]):
                result[cli_id] = candidate
        return result

    def trace_cwd(self, path, max_lines=120):
        signature = _file_signature(path)
        key = (str(path), int(max_lines))
        cached = self._cwd_cache.get(key)
        if cached and cached["signature"] == signature:
            return cached["cwd"]
        cwd = ""
        try:
            with open(path, encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= max_lines:
                        break
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    candidate = row.get("cwd") if isinstance(row, dict) else None
                    if isinstance(candidate, str) and candidate.strip():
                        cwd = candidate.strip()
                        break
        except OSError:
            pass
        self._cwd_cache[key] = {"signature": signature, "cwd": cwd}
        return cwd

    def trace_activity(self, path):
        path = str(path)
        signature = _file_signature(path)
        cached = self._activity_cache.get(path)
        if cached and cached["signature"] == signature:
            return cached["activity"]
        latest = 0.0
        try:
            size = os.path.getsize(path)
            start = max(0, size - ACTIVITY_TAIL_BYTES)
            with open(path, "rb") as handle:
                handle.seek(start)
                tail = handle.read(ACTIVITY_TAIL_BYTES)
            if start:
                _partial, separator, tail = tail.partition(b"\n")
                if not separator:
                    tail = b""
            for line in tail.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    latest = max(latest, _timestamp(row.get("timestamp")))
        except OSError:
            latest = 0.0
        self._activity_cache[path] = {
            "signature": signature,
            "activity": latest,
        }
        if len(self._activity_cache) > ACTIVITY_CACHE_LIMIT:
            self._activity_cache.pop(next(iter(self._activity_cache)))
        return latest

    def desktop_activity(self, path, desktop):
        reported = float(desktop.get("last_activity_ms") or 0)
        if reported >= 100_000_000_000:
            reported /= 1000.0
        return max(self.trace_activity(path), reported)

    def trace_group_activity(self, paths, desktop=None):
        activity = max((self.trace_activity(path) for path in paths), default=0.0)
        if desktop:
            reported = float(desktop.get("last_activity_ms") or 0)
            if reported >= 100_000_000_000:
                reported /= 1000.0
            activity = max(activity, reported)
        return activity

    def _nested_trace_paths(self, main_path):
        main_path = str(main_path)
        nested = self._glob(
            os.path.join(
                os.path.splitext(main_path)[0], "subagents", "**", "*.jsonl",
            ),
            recursive=True,
        )
        return tuple(sorted({main_path, *(str(path) for path in nested)}))

    def _local_agent_trace_paths(self, main_path):
        main_path = Path(main_path)
        owner_root = None
        for parent in main_path.parents:
            if parent.name == ".claude":
                owner_root = parent.parent
                break
        if owner_root is None:
            return (str(main_path),)
        paths = self._glob(
            str(owner_root / ".claude" / "projects" / "**" / "*.jsonl"),
            recursive=True,
        )
        return tuple(sorted({str(path) for path in paths})) or (str(main_path),)

    @staticmethod
    def _source_revision(paths, metadata_path="", title=""):
        digest = hashlib.sha256()
        for path in sorted({str(path) for path in paths if path}):
            digest.update(path.encode("utf-8", "surrogatepass"))
            digest.update(b"\0")
            for part in _file_signature(path):
                digest.update(part.encode("ascii", "replace"))
                digest.update(b"\0")
        return SourceRevision((
            "claude-trace-group-v1",
            digest.hexdigest(),
            *_file_signature(metadata_path or ""),
            str(title or ""),
        ))

    @staticmethod
    def _locator_paths(locator):
        if locator.kind != "claude-jsonl-group":
            return (locator.value,)
        try:
            paths = json.loads(locator.value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        if not isinstance(paths, list):
            return ()
        return tuple(str(path) for path in paths if isinstance(path, str) and path)

    def local_agent_sources(self, desktop_index):
        sources = []
        for desktop in desktop_index.values():
            if desktop.get("source_kind") != "agent":
                continue
            metadata_path = desktop.get("metadata_path") or ""
            session_root = metadata_path.rsplit(".json", 1)[0]
            pattern = os.path.join(
                session_root, ".claude", "projects", "*",
                "{}.jsonl".format(desktop.get("cli_session_id")),
            )
            paths = self._glob(pattern)
            if not paths:
                sibling_pattern = os.path.join(
                    os.path.dirname(metadata_path), "*", ".claude", "projects", "*",
                    "{}.jsonl".format(desktop.get("cli_session_id")),
                )
                paths = self._glob(sibling_pattern)
            for path in paths:
                trace_paths = self._local_agent_trace_paths(path)
                sources.append({
                    "provider": "claude",
                    "client": "claude_desktop",
                    "label": "Claude Desktop",
                    "id": desktop.get("cli_session_id"),
                    "desktop_session_id": desktop.get("desktop_session_id"),
                    "session": os.path.basename(path),
                    "path": path,
                    "metadata_path": metadata_path,
                    "project": desktop.get("project") or "No project",
                    "mtime": self.trace_group_activity(trace_paths, desktop),
                    "signature_mtime": max(
                        max((_mtime(item) for item in trace_paths), default=0.0),
                        float(desktop.get("metadata_mtime") or 0),
                    ),
                    "title": desktop.get("title"),
                    "model": desktop.get("model"),
                    "desktop_source_kind": "agent",
                    "_trace_paths": trace_paths,
                })
        return sources

    def _record_message_ids(self, record):
        paths = tuple(record.get("_trace_paths") or (record.get("path") or "",))
        cache_key = tuple(
            (str(path), *_file_signature(path)) for path in sorted(paths) if path
        )
        cached = self._message_id_cache.get(cache_key)
        if cached is not None:
            return cached
        rows, _corrupt, _available = self.load_rows(paths)
        message_ids = frozenset(
            str(message["id"])
            for message in self.logical_messages(rows)
            if message.get("id")
        )
        self._message_id_cache[cache_key] = message_ids
        if len(self._message_id_cache) > ACTIVITY_CACHE_LIMIT:
            self._message_id_cache.pop(next(iter(self._message_id_cache)))
        return message_ids

    def _canonical_records(self, records):
        """Merge physical records that share a session or logical message ID."""
        records = list(records)
        parents = list(range(len(records)))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left, right):
            left = find(left)
            right = find(right)
            if left != right:
                parents[right] = left

        session_owner = {}
        message_owner = {}
        for index, record in enumerate(records):
            session_id = str(record.get("id") or "")
            if session_id:
                previous = session_owner.setdefault(session_id, index)
                union(index, previous)
            for message_id in self._record_message_ids(record):
                previous = message_owner.setdefault(message_id, index)
                union(index, previous)

        groups = {}
        order = []
        for index, record in enumerate(records):
            key = find(index)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(record)

        result = []
        for group_id in order:
            candidates = groups[group_id]

            def rank(record):
                try:
                    activity = float(record.get("mtime") or 0)
                except (TypeError, ValueError, OverflowError):
                    activity = 0.0
                try:
                    signature = float(record.get("signature_mtime") or 0)
                except (TypeError, ValueError, OverflowError):
                    signature = 0.0
                return (
                    record.get("desktop_source_kind") != "agent",
                    -activity,
                    -signature,
                    str(record.get("path") or ""),
                )

            ranked = sorted(candidates, key=rank)
            canonical = dict(ranked[0])
            session_id = str(canonical.get("id") or "")
            key = (
                "claude:{}".format(session_id)
                if session_id
                else "claude-path:{}".format(canonical.get("path") or group_id)
            )
            for candidate in ranked[1:]:
                for field in (
                    "client", "label", "desktop_session_id", "metadata_path",
                    "project", "title", "model",
                ):
                    if not canonical.get(field) and candidate.get(field):
                        canonical[field] = candidate[field]
            canonical["_aggregation_key"] = key
            canonical["_aggregation_canonical"] = True
            canonical["mtime"] = max(
                float(candidate.get("mtime") or 0) for candidate in candidates
            )
            canonical["signature_mtime"] = max(
                float(candidate.get("signature_mtime") or 0)
                for candidate in candidates
            )
            physical_paths = tuple(sorted({
                str(path)
                for candidate in candidates
                for path in (
                    candidate.get("_trace_paths")
                    or ((candidate.get("path"),) if candidate.get("path") else ())
                )
                if path
            }))
            canonical["_trace_paths"] = physical_paths
            if len(physical_paths) > 1:
                canonical["_duplicate_paths"] = physical_paths
            result.append(canonical)
        return tuple(result)

    def _legacy_records(self):
        records = []
        desktop_index = self.desktop_index()
        known_paths = set()
        for path in self._glob(str(self.projects_root / "*" / "*.jsonl")):
            session_id = os.path.basename(path).rsplit(".", 1)[0]
            project_raw = os.path.basename(os.path.dirname(path))
            desktop = desktop_index.get(session_id) or {}
            trace_cwd = self.trace_cwd(path)
            client = desktop.get("client") or "claude_code"
            project = (
                desktop.get("project") or self.project_resolver(trace_cwd) or
                self.project_decoder(project_raw)
            )
            trace_paths = self._nested_trace_paths(path)
            records.append({
                "provider": "claude",
                "client": client,
                "label": desktop.get("label") or "Claude Code",
                "id": session_id,
                "desktop_session_id": desktop.get("desktop_session_id"),
                "session": os.path.basename(path),
                "path": path,
                "metadata_path": desktop.get("metadata_path"),
                "project": project,
                "mtime": (
                    self.trace_group_activity(trace_paths, desktop)
                    if client == "claude_desktop" else max(
                        (_mtime(item) for item in trace_paths), default=0.0,
                    )
                ),
                "signature_mtime": max(
                    max((_mtime(item) for item in trace_paths), default=0.0),
                    float(desktop.get("metadata_mtime") or 0),
                ),
                "title": desktop.get("title"),
                "model": desktop.get("model"),
                "_trace_paths": trace_paths,
            })
            known_paths.add(path)
        for source in self.local_agent_sources(desktop_index):
            if source["path"] not in known_paths:
                records.append(source)
                known_paths.add(source["path"])
        return self._canonical_records(records)

    def discover(self, context):
        del context
        sources = []
        for record in self._legacy_records():
            trace_paths = tuple(record.get("_trace_paths") or (record["path"],))
            locator = (
                SourceLocator(
                    "claude-jsonl-group",
                    json.dumps(trace_paths, separators=(",", ":")),
                )
                if len(trace_paths) > 1
                else SourceLocator("jsonl", trace_paths[0])
            )
            sources.append(SessionSource(
                runtime_id="claude",
                client_id=record["client"],
                session_id=record["id"],
                display_label=record["label"],
                project=record["project"],
                locator=locator,
                activity_mtime=record["mtime"],
                revision=self._source_revision(
                    trace_paths,
                    record.get("metadata_path") or "",
                    record.get("title") or "",
                ),
                model_ref=(
                    ModelRef("anthropic", record["model"])
                    if record.get("model") else None
                ),
                account_provider_id="anthropic",
            ))
        return tuple(sources)

    def discover_legacy(self, context):
        del context
        return self._legacy_records()

    def current_revision(self, source):
        paths = (
            self._locator_paths(source.locator)
            if isinstance(source, SessionSource)
            else tuple(source.get("_trace_paths") or (source.get("path", ""),))
        )
        session_id = source.session_id if isinstance(source, SessionSource) else source.get("id", "")
        desktop = self.desktop_index().get(str(session_id)) or {}
        title = desktop.get("title") or (
            "" if isinstance(source, SessionSource) else source.get("title") or ""
        )
        metadata_path = desktop.get("metadata_path") or (
            "" if isinstance(source, SessionSource) else source.get("metadata_path") or ""
        )
        return self._source_revision(paths, metadata_path, title)

    def load_rows(self, path):
        paths = (
            tuple(path)
            if isinstance(path, (tuple, list))
            else (path,)
        )
        rows = []
        corrupt = 0
        available = False
        seen_rows = set()
        for item in paths:
            try:
                with open(item, encoding="utf-8") as handle:
                    available = True
                    for line in handle:
                        if not line.strip():
                            continue
                        row_digest = hashlib.sha256(
                            line.encode("utf-8", "surrogatepass")
                        ).digest()
                        if row_digest in seen_rows:
                            continue
                        seen_rows.add(row_digest)
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
                if len(paths) == 1:
                    return (), 0, False
                else:
                    continue
        return tuple(rows), corrupt, available

    def logical_messages(self, rows, timestamp_parser=None):
        timestamp_parser = timestamp_parser or _timestamp
        by_id = {}
        order = []
        for row in rows:
            if row.get("type") != "assistant":
                continue
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            message_id = message.get("id") or row.get("uuid")
            logical_key = (
                message_id
                if message_id is not None
                else ("idless-row", len(order))
            )
            logical = by_id.get(logical_key)
            if logical is None:
                logical = {
                    "id": message_id,
                    "model": message.get("model") or "unknown-model",
                    "usage": message.get("usage") or {},
                    "stop_reason": message.get("stop_reason"),
                    "ts": timestamp_parser(row.get("timestamp")) or 0,
                    "last_ts": timestamp_parser(row.get("timestamp")) or 0,
                    "side": bool(row.get("isSidechain")),
                    "content": [],
                }
                by_id[logical_key] = logical
                order.append(logical_key)
            content = message.get("content")
            if isinstance(content, list):
                logical["content"].extend(
                    block for block in content if isinstance(block, dict)
                )
            usage = message.get("usage") or {}
            output_tokens = _safe_int(usage.get("output_tokens"))
            current_output_tokens = _safe_int(
                logical["usage"].get("output_tokens")
            )
            if output_tokens > current_output_tokens:
                logical["usage"] = usage or logical["usage"]
            elif output_tokens == current_output_tokens and usage:
                logical["usage"] = {**logical["usage"], **usage}
            if message.get("stop_reason"):
                logical["stop_reason"] = message["stop_reason"]
            logical["last_ts"] = max(
                float(logical.get("last_ts") or 0),
                timestamp_parser(row.get("timestamp")) or 0,
            )
        return tuple(by_id[logical_key] for logical_key in order)

    @staticmethod
    def _evidence(value, available):
        return (EvidenceValue(value, EvidenceBasis.MEASURED)
                if available else EvidenceValue.unavailable())

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource):
            raise TypeError("native load requires SessionSource")
        if source.runtime_id != "claude":
            raise ValueError("source belongs to another runtime")
        rows, corrupt, available = self.load_rows(
            self._locator_paths(source.locator)
        )
        if not available:
            return self._empty(source, detail, ("source_unavailable",))
        messages = self.logical_messages(rows)
        usage_seen = False
        input_complete = True
        output_complete = True
        counts = {
            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "cache_write_5m": 0, "cache_write_1h": 0,
            "cache_write_unspecified": 0,
        }
        duration_available = True
        turns = []
        tools = []
        for message in messages:
            raw_usage = message.get("usage") or {}
            if raw_usage:
                usage_seen = True
                usage = _normalized_usage(raw_usage)
                input_complete = input_complete and usage["input_available"]
                output_complete = output_complete and usage["output_available"]
                duration_available = (
                    duration_available and usage["cache_duration_available"]
                )
                counts["input"] += usage["input_tokens"]
                counts["output"] += usage["output_tokens"]
                counts["cache_read"] += usage["cache_read_input_tokens"]
                counts["cache_write"] += usage["cache_creation_input_tokens"]
                counts["cache_write_5m"] += usage["cache_creation_5m_input_tokens"]
                counts["cache_write_1h"] += usage["cache_creation_1h_input_tokens"]
                counts["cache_write_unspecified"] += usage[
                    "cache_creation_unspecified_input_tokens"
                ]
                if len(turns) < self.max_detail_turns:
                    turns.append(TurnSummary(
                        len(turns) + 1,
                        _datetime(message.get("ts")),
                        _datetime(message.get("last_ts")),
                        self._evidence(
                            usage["output_tokens"], usage["output_available"]
                        ),
                    ))
            for block in message.get("content") or ():
                if (block.get("type") == "tool_use" and block.get("name") and
                        len(tools) < self.max_tool_events):
                    tools.append(ToolEvent(str(block["name"]), "tool"))
        durations = []
        for row in rows:
            if row.get("type") != "system" or row.get("subtype") != "turn_duration":
                continue
            value = row.get("durationMs")
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                value = float(value)
                if math.isfinite(value) and value >= 0:
                    durations.append(value / 1000.0)
        timestamps = [_timestamp(row.get("timestamp")) for row in rows]
        timestamps = [value for value in timestamps if value]
        warning_codes = []
        if corrupt:
            warning_codes.append("corrupt_rows")
        input_available = usage_seen and input_complete
        output_available = usage_seen and output_complete
        if not input_available or not output_available:
            warning_codes.append("usage_unavailable")
        if len(turns) >= self.max_detail_turns or len(tools) >= self.max_tool_events:
            warning_codes.append("history_truncated")
        messages_by_code = {
            "corrupt_rows": "Malformed Claude rows were ignored.",
            "usage_unavailable": "Claude token evidence was unavailable.",
            "history_truncated": "Detailed Claude history was bounded.",
        }
        return NormalizedSession(
            source=source,
            started_at=_datetime(min(timestamps)) if timestamps else None,
            ended_at=_datetime(max(timestamps)) if timestamps else None,
            usage=UsageEvidence(
                self._evidence(counts["input"], input_available),
                self._evidence(counts["output"], output_available),
                self._evidence(counts["cache_read"], input_available),
                self._evidence(counts["cache_write"], input_available),
                EvidenceValue.unavailable(),
                cache_write_5m_tokens=self._evidence(
                    counts["cache_write_5m"], input_available and duration_available,
                ),
                cache_write_1h_tokens=self._evidence(
                    counts["cache_write_1h"], input_available and duration_available,
                ),
                cache_write_unspecified_tokens=self._evidence(
                    counts["cache_write_unspecified"],
                    input_available and duration_available,
                ),
            ),
            timing=TimingEvidence(
                self._evidence(sum(durations), bool(durations)),
                self._evidence(sum(durations), bool(durations)),
                EvidenceValue.unavailable(),
            ),
            tools=tuple(tools),
            turns=tuple(turns) if detail is DetailLevel.FULL else (),
            pricing_basis=None,
            capabilities=self.descriptor.capabilities,
            warnings=tuple(
                ParseWarning(code, messages_by_code[code]) for code in warning_codes
            ),
            detail=detail,
        )

    def _empty(self, source, detail, warning_codes):
        return NormalizedSession(
            source, None, None, UsageEvidence.unavailable(), TimingEvidence.unavailable(),
            (), (), None, self.descriptor.capabilities,
            tuple(ParseWarning(code, "Claude evidence was unavailable.")
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
        claude_performance_samples = compat["claude_performance_samples"]
        claude_tool_results = compat["claude_tool_results"]
        claude_user_events = compat["claude_user_events"]
        claude_wait_samples = compat["claude_wait_samples"]
        cost_of = compat["cost_of"]
        claude_billing_supported = compat["claude_billing_supported"]
        execution_timing = compat["execution_timing"]
        metric_availability = compat["metric_availability"]
        parse_iso = compat["parse_iso"]
        performance_summary = compat["performance_summary"]
        price_for = compat["price_for"]
        skill_names_from_value = compat["skill_names_from_value"]
        tool_identity = compat["tool_identity"]
        tool_summary = compat["tool_summary"]
        trace_event = compat["trace_event"]
        usage_tokens = compat["usage_tokens"]
        user_prompt_preview = compat["user_prompt_preview"]
        paths = source.get("_trace_paths") or (source["path"],)
        objs, _corrupt, _available = self.load_rows(paths)
        if not objs:
            return None
    
        msgs = self.logical_messages(objs, timestamp_parser=parse_iso)
        user_events = claude_user_events(objs)
        user_event_idx = 0
        pending_user_texts = []
        result_chars, result_ts, result_errors = claude_tool_results(objs)
        tool_name_by_id = {}
        for rec in msgs:
            for block in rec["content"]:
                if block.get("type") == "tool_use":
                    tool_name_by_id[block.get("id")] = block.get("name") or "?"
    
        tot = {
            "input": 0, "cache_write": 0, "cache_read": 0, "output": 0,
            "cache_write_5m": 0, "cache_write_1h": 0,
            "cache_write_unspecified": 0,
        }
        cost = {
            "input": 0.0, "cache_write": 0.0, "cache_read": 0.0,
            "output": 0.0, "server_tools": 0.0,
        }
        first_ts = last_ts = None
        biggest = None
        series, executions, trace = [], [], []
        think_turns = think_out = routine_out = think_cost = 0
        completed = 0
        model_tok, model_cost = defaultdict(int), defaultdict(float)
        side_cost = side_turns = 0
        approx_cost = False
        price_complete = True
        input_complete = True
        output_complete = True
    
        for rec in msgs:
            usage = _with_thinking_observation(
                _normalized_usage(rec["usage"]), rec["content"]
            )
            if not usage:
                continue
            input_complete = input_complete and usage["input_available"]
            output_complete = output_complete and usage["output_available"]
            idx = len(series) + 1
            model = rec["model"]
            ts = rec["ts"]
            if ts:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
                while user_event_idx < len(user_events) and user_events[user_event_idx]["ts"] <= ts:
                    pending_user_texts.append(user_events[user_event_idx]["text"])
                    user_event_idx += 1
            user_input = user_prompt_preview(pending_user_texts)
            pending_user_texts = []
    
            _, approx = price_for(model, "claude", at=ts)
            cost_available = (
                not approx
                and usage["input_available"]
                and usage["output_available"]
                and usage["billing_available"]
                and claude_billing_supported(usage, model, at=ts)
            )
            coverage_complete = _cost_coverage_complete(usage, cost_available)
            c = cost_of(usage, model, "claude", at=ts) if cost_available else {
                "input": 0.0, "cache_write": 0.0,
                "cache_read": 0.0, "output": 0.0, "server_tools": 0.0,
            }
            approx_cost = approx_cost or not coverage_complete
            price_complete = price_complete and coverage_complete
            tc = sum(c.values())
            for key in cost:
                cost[key] += c[key]
    
            in_tok = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                      + usage.get("cache_creation_input_tokens", 0))
            out_tok = usage.get("output_tokens", 0)
            total = usage_tokens(usage)
            tot["input"] += usage.get("input_tokens", 0)
            tot["cache_write"] += usage.get("cache_creation_input_tokens", 0)
            tot["cache_write_5m"] += usage.get(
                "cache_creation_5m_input_tokens", 0
            )
            tot["cache_write_1h"] += usage.get(
                "cache_creation_1h_input_tokens", 0
            )
            tot["cache_write_unspecified"] += usage.get(
                "cache_creation_unspecified_input_tokens", 0
            )
            tot["cache_read"] += usage.get("cache_read_input_tokens", 0)
            tot["output"] += out_tok
            model_tok[model] += total
            model_cost[model] += tc

            has_think = _has_thinking_block(rec["content"])
            reasoning_available = (
                usage.get("reasoning_available") is True
                and usage.get("output_available") is True
            )
            reasoning_tokens = (
                int(usage.get("reasoning_output_tokens") or 0)
                if reasoning_available else 0
            )
            tool_blocks = [b for b in rec["content"] if b.get("type") == "tool_use"]
            tools = []
            for block in tool_blocks:
                ident = tool_identity(block.get("name") or "?")
                tid = block.get("id")
                out_chars = result_chars.get(tid, 0)
                tool = {
                    **ident,
                    "id": tid,
                    "call_id": tid,
                    "args_chars": len(json.dumps(block.get("input", ""))),
                    "output_chars": out_chars,
                    "output_tokens": out_chars // CHARS_PER_TOKEN,
                    "error": bool(result_errors.get(tid)),
                    "skills": skill_names_from_value(block.get("input"), block.get("name")),
                }
                tools.append(tool)
    
            if reasoning_available:
                think_turns += 1
                think_out += reasoning_tokens
                reasoning_cost = (
                    c["output"] * reasoning_tokens / out_tok if out_tok else 0.0
                )
                think_cost += reasoning_cost
                trace.append(trace_event(ts, "reasoning", "Reasoning", f"thinking turn #{idx}", idx,
                                         tokens=reasoning_tokens, cost=reasoning_cost,
                                         severity="reasoning", model=model,
                                         output_tokens=reasoning_tokens,
                                         native_type="assistant",
                                         native_subtype="reasoning"))
            routine_out += max(0, out_tok - reasoning_tokens)
            if rec["stop_reason"] == "end_turn":
                completed += 1
            if rec["side"]:
                side_cost += tc
                side_turns += 1
                trace.append(trace_event(ts, "coordination", "Subagent turn", f"execution #{idx}", idx,
                                         tokens=out_tok, cost=tc, severity="coordination",
                                         model=model, output_tokens=out_tok,
                                         native_type="assistant",
                                         native_subtype="agent_message"))
    
            cache_tokens = usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            fresh_input_tokens = usage.get("input_tokens", 0)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)
            cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
            trace.append(trace_event(
                ts, "message", "Assistant turn",
                f"{out_tok:,} out / {in_tok:,} in",
                idx, tokens=total, cost=tc, severity="usage",
                model=model, input_tokens=in_tok, output_tokens=out_tok,
                cache_tokens=cache_tokens, context_tokens=in_tok,
                fresh_input_tokens=fresh_input_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                tool_count=len(tools), reasoning_tokens=reasoning_tokens,
                native_type="assistant", native_subtype="agent_message",
            ))
            for tool in tools:
                trace.append(trace_event(ts, "tool_call", tool["display"], tool["namespace"], idx,
                                         tool=tool["name"], severity="tool",
                                         model=model, args_chars=tool["args_chars"],
                                         native_type="assistant",
                                         native_subtype="tool_call"))
                if tool["output_tokens"]:
                    trace.append(trace_event(result_ts.get(tool["id"]) or ts, "tool_result", tool["display"],
                                             f"~{tool['output_tokens']:,} returned tokens", idx,
                                             tool=tool["name"], tokens=tool["output_tokens"],
                                             severity="warn" if tool.get("error") else "retrieval",
                                             model=model, output_chars=tool["output_chars"],
                                             retrieval_tokens=tool["output_tokens"], error=tool.get("error"),
                                             native_type="user",
                                             native_subtype="tool_result"))
    
            series.append({
                "i": idx,
                "in": in_tok,
                "out": out_tok,
                "cost": round(tc, 4),
                "fresh_input": fresh_input_tokens,
                "cache": cache_tokens,
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
                "cache_write_5m": usage.get(
                    "cache_creation_5m_input_tokens", 0
                ),
                "cache_write_1h": usage.get(
                    "cache_creation_1h_input_tokens", 0
                ),
                "cache_write_unspecified": usage.get(
                    "cache_creation_unspecified_input_tokens", 0
                ),
                "think": has_think,
                "tools": len(tools),
                "side": rec["side"],
                "reasoning": reasoning_tokens,
                "user_message": user_input,
                "user_input": user_input,
            })
            executions.append({
                "id": rec["id"],
                "idx": idx,
                "ts": ts or 0,
                "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
                "model": model,
                "tokens": {"input": in_tok, "output": out_tok, "reasoning": reasoning_tokens,
                           "retrieval": sum(t["output_tokens"] for t in tools), "fresh_input": fresh_input_tokens,
                           "cache": cache_tokens, "cache_read": cache_read_tokens, "cache_write": cache_write_tokens,
                           "cache_write_5m": usage.get(
                               "cache_creation_5m_input_tokens", 0
                           ),
                           "cache_write_1h": usage.get(
                               "cache_creation_1h_input_tokens", 0
                           ),
                           "cache_write_unspecified": usage.get(
                               "cache_creation_unspecified_input_tokens", 0
                           ),
                           "total": total},
                "cost": round(tc, 6),
                "cost_breakdown": {k: round(v, 6) for k, v in c.items()},
                "tools": tools,
                "tool_count": len(tools),
                "reasoning_tokens": reasoning_tokens,
                "context_tokens": in_tok,
                "context_window": None,
                "context_pct": None,
                "duration_ms": None,
                "summary": f"Turn {idx}: {out_tok:,} out / {in_tok:,} in",
                "user_message": user_input,
                "user_input": user_input,
            })
            if biggest is None or tc > biggest["cost"]:
                biggest = {"cost": tc, "idx": idx}
    
        tool_data = tool_summary(executions)
        retrieval_tokens = tool_data["total_output_tokens"]
        total_tokens = sum(
            tot[key] for key in ("input", "cache_write", "cache_read", "output")
        )
        total_cost = sum(cost.values())
        elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
        minutes = max(elapsed / 60.0, 1e-9)
        cache_in = tot["cache_read"] + tot["cache_write"]
        cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
        idle = (time.time() - last_ts) if last_ts else 1e9
        side_out = sum(s["out"] for s in series if s["side"])
        semantic = {
            "reasoning": think_out,
            "output": max(0, routine_out),
            "retrieval": retrieval_tokens,
            "coordination": side_out,
        }
        primary_model = max(model_tok, key=model_tok.get) if model_tok else "unknown-model"
        analyses = analysis_block(tot, total_cost, think_out, think_turns, think_cost, model_tok, model_cost,
                                  tool_data, side_cost, side_turns, completed)
        insights = build_insights(tot, cost, total_cost, cache_ratio, biggest, len(series), analyses,
                                  "claude", primary_model, approx_cost, executions)
    
        wait_samples = claude_wait_samples(objs)
        state = build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                            analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                            primary_model, "exact Claude API-rate estimate", execution_timing("claude", objs),
                            wait_samples, availability=metric_availability(
                                "claude", cost=price_complete,
                                tokens=input_complete and output_complete,
                                input_tokens=input_complete,
                                output_tokens=output_complete,
                                cache=input_complete,
                            ))
        state["throughput"] = performance_summary(claude_performance_samples(objs), tot["output"])
        return state

    def summarize_legacy(self, source, objs=None):
        compat = self._require_compatibility()
        if objs is None:
            objs, _corrupt, _available = self.load_rows(
                source.get("_trace_paths") or (source.get("path") or "",)
            )
        CURRENT_SESSION_CONTEXT_SAMPLES = compat["context_sample_limit"]
        add_model_daily = compat["add_model_daily"]
        add_model_summary = compat["add_model_summary"]
        analyze_language_signals = compat["analyze_language_signals"]
        attach_language_signals = compat["attach_language_signals"]
        claude_human_text = compat["claude_human_text"]
        claude_performance_samples = compat["claude_performance_samples"]
        claude_tool_call_evidence = compat["claude_tool_call_evidence"]
        claude_wait_samples = compat["claude_wait_samples"]
        compact_text = compat["compact_text"]
        cost_of = compat["cost_of"]
        claude_billing_supported = compat["claude_billing_supported"]
        execution_timing = compat["execution_timing"]
        metric_availability = compat["metric_availability"]
        parse_iso = compat["parse_iso"]
        price_for = compat["price_for"]
        summarize_tool_evidence = compat["summarize_tool_evidence"]
        summary_row = compat["summary_row"]
        usage_tokens = compat["usage_tokens"]
        msgs = self.logical_messages(objs, timestamp_parser=parse_iso)
        cost = 0.0
        tokens = 0
        first_ts = last_ts = None
        models = set()
        model_cost, model_tok = defaultdict(float), defaultdict(int)
        model_stats = {}
        model_daily = {}
        input_tokens = output_tokens = 0
        day_cost = defaultdict(float)
        approx = False
        price_complete = True
        latest_context = 0
        context_samples = []
        primary_model = source.get("model") or "unknown-model"
        input_complete = True
        output_complete = True
        for rec in msgs:
            usage = _with_thinking_observation(
                _normalized_usage(rec["usage"]), rec["content"]
            )
            if not usage:
                continue
            input_complete = input_complete and usage["input_available"]
            output_complete = output_complete and usage["output_available"]
            primary_model = rec["model"] or primary_model
            latest_context = (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
            )
            context_samples.append(latest_context)
            _, missing = price_for(rec["model"], "claude", at=rec["ts"])
            cost_available = (
                not missing
                and usage["input_available"]
                and usage["output_available"]
                and usage["billing_available"]
                and claude_billing_supported(usage, rec["model"], at=rec["ts"])
            )
            coverage_complete = _cost_coverage_complete(usage, cost_available)
            c = sum(cost_of(usage, rec["model"], "claude", at=rec["ts"]).values()) \
                if cost_available else 0.0
            approx = approx or not coverage_complete
            price_complete = price_complete and coverage_complete
            toks = usage_tokens(usage)
            cost += c
            tokens += toks
            models.add(rec["model"].replace("claude-", ""))
            model_cost[rec["model"]] += c
            model_tok[rec["model"]] += toks
            input_count, output_count = add_model_summary(
                model_stats, rec["model"], usage, c,
                cost_available=cost_available,
            )
            add_model_daily(
                model_daily, rec["model"], usage, c, rec["ts"],
                cost_available=cost_available,
            )
            input_tokens += input_count
            output_tokens += output_count
            if rec["ts"]:
                first_ts = rec["ts"] if first_ts is None else min(first_ts, rec["ts"])
                last_ts = rec["ts"] if last_ts is None else max(last_ts, rec["ts"])
                day = time.strftime("%Y-%m-%d", time.localtime(rec["ts"]))
                day_cost[day] += c

        for stats in (*model_stats.values(), *model_daily.values()):
            stats["availability"] = metric_availability(
                "claude",
                cost=int(stats.get("cost_covered_executions") or 0) > 0,
                tokens=(stats.get("input_evidence") is True
                        or stats.get("output_evidence") is True),
                input_tokens=stats.get("input_evidence") is True,
                output_tokens=stats.get("output_evidence") is True,
                cache=stats.get("input_evidence") is True,
            )

        custom_title = ai_title = ""
        for obj in objs:
            record_type = obj.get("type")
            if record_type == "custom-title":
                value = _compact(obj.get("customTitle"))
                if value:
                    custom_title = value
            elif record_type == "ai-title":
                value = _compact(obj.get("aiTitle"))
                if value:
                    ai_title = value
        declared_title = source.get("title") or custom_title or ai_title

        title = declared_title or None
        if not title:
            for obj in objs:
                txt = claude_human_text(obj)
                if txt and txt.strip():
                    title = compact_text(txt.strip(), 60)
                    break

        performance = claude_performance_samples(objs)
        wait_samples = claude_wait_samples(objs)
        row = summary_row(source, title, cost, tokens, len(msgs), models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                          execution_timing("claude", objs), input_tokens, output_tokens, model_stats,
                          list(model_daily.values()), performance, wait_samples,
                          availability=metric_availability(
                              "claude", cost=price_complete,
                              tokens=input_complete and output_complete,
                              input_tokens=input_complete,
                              output_tokens=output_complete,
                              cache=input_complete,
                          ),
                          session_name=declared_title)
        row["primary_model"] = primary_model
        row["context"] = {
            "latest": latest_context,
            "window": None,
            "latest_pct": None,
            "estimated": False,
        }
        row["_context_samples"] = context_samples[-CURRENT_SESSION_CONTEXT_SAMPLES:]
        row["terminal"] = bool(msgs and msgs[-1].get("stop_reason") == "end_turn")
        signal_rollups, signal_events = analyze_language_signals(
            "claude", objs, default_model=source.get("model") or "unknown-model"
        )
        attach_language_signals(row, signal_rollups, signal_events)
        row["_tool_evidence"] = summarize_tool_evidence(claude_tool_call_evidence(objs, msgs))
        return row

    def deletion_plan(self, source):
        if isinstance(source, SessionSource) and source.locator.kind == "jsonl":
            return DeletionPlan(
                DeletionDisposition.TRASH,
                "Move this Claude session trace to Trash.",
                (source.locator,),
            )
        return DeletionPlan.deny("Claude deletion requires one owned session trace.")


class ClaudeRuntimeAdapterProxy:
    descriptor = ClaudeRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        adapter = self._adapter_factory()
        if getattr(adapter, "load", None) is None or getattr(adapter, "discover", None) is None:
            raise TypeError("adapter factory returned an invalid Claude adapter")
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
