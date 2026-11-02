"""Compact, content-free per-session evidence for MCP listing and statistics.

The detailed-state cache is deliberately small, so a full-history ``stats``
query would evict its own entries and re-parse every trace. This cache keeps
only the fields the MCP projections read: a few summary fields for session
listing and the projected stat rows. Entries are keyed by a digest of the
session path and validated against the same signatures as the summary and
detailed-state caches, so a changed trace or price reprices on the next call.

When persistence is enabled, the cache file holds hashed keys, runtime and
model identifiers, session ids, allowlisted tool names, UTC days, and numbers.
It never holds paths, titles, projects, prompts, responses, or tool content.
"""

import hashlib
import json
import math
import os
import threading

from token_meter.mcp.projections import safe_identity
from token_meter.mcp.service import (
    STAT_ROW_DIMENSIONS,
    STAT_ROW_METRICS,
    session_stat_rows,
)


SCHEMA_VERSION = 1
MAX_SESSIONS = 20_000
# Larger sessions and anything past the file budget stay memory-only.
MAX_PERSISTED_ROWS = 20_000
MAX_PERSISTED_BYTES = 48 * 1024 * 1024
MAX_INTEGER = 2 ** 63
UNSUPPORTED_IDENTITY = "<unsupported>"
# Cloud resource names (AWS ARNs, Vertex and Azure paths) can carry account or project ids.
RESOURCE_MARKERS = (
    "arn:", "projects/", "subscriptions/", "deployments/", "://", "/users/", "/home/",
)
LISTING_FIELDS = (
    "terminal", "primary_model", "model_provider", "tokens", "input_tokens",
    "output_tokens", "cost", "discount_usd", "duration_s",
)


def _digest(value):
    return hashlib.sha256(repr(value).encode("utf-8", "replace")).hexdigest()[:32]


def _number(value):
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -MAX_INTEGER <= value <= MAX_INTEGER
    return isinstance(value, float) and math.isfinite(value)


def _scalar(value):
    return isinstance(value, (bool, str)) or _number(value)


def _strings(entry):
    listing = entry.get("listing") or {}
    for value in (*listing.values(), *(listing.get("models") or ())):
        if isinstance(value, str):
            yield value
    for row in (entry.get("stats") or {}).get("dimensions") or ():
        for value in row:
            if isinstance(value, str):
                yield value


def _persistable_string(value):
    if value == UNSUPPORTED_IDENTITY:
        return True
    lowered = value.lower()
    return (
        safe_identity(value, 240) == value
        and not lowered.startswith(("/", "~"))
        and not any(marker in lowered for marker in RESOURCE_MARKERS)
    )


def _persistable(entry):
    """Only allowlisted identifiers reach disk; an ARN can carry an account id."""
    stats = entry.get("stats") or {}
    if sum(len(stats.get(grain) or ()) for grain in STAT_ROW_METRICS) > MAX_PERSISTED_ROWS:
        return False
    return all(_persistable_string(value) for value in _strings(entry))


def _identity_or_marker(value, maximum):
    """Keep what the projection would show; a rejected truthy value becomes the marker."""
    if value and isinstance(value, str) and not safe_identity(value, maximum):
        return UNSUPPORTED_IDENTITY
    return value


def listing_fields(summary):
    """Return the subset of a session summary that ``session_projection`` reads."""
    summary = summary if isinstance(summary, dict) else {}
    fields = {
        name: summary[name]
        for name in LISTING_FIELDS
        if name in summary and _scalar(summary[name])
    }
    # A truthy value the projection rejects still blocks its fallback. The marker
    # keeps that outcome, never passes the identity allowlist, and holds no text.
    if summary.get("primary_model") and not _scalar(summary["primary_model"]):
        fields["primary_model"] = UNSUPPORTED_IDENTITY
    elif "primary_model" in fields:
        fields["primary_model"] = _identity_or_marker(fields["primary_model"], 160)
    if "model_provider" in fields:
        fields["model_provider"] = _identity_or_marker(fields["model_provider"], 120)
    models = summary.get("models")
    if isinstance(models, list):
        # Only "exactly one model" matters to the projection.
        fields["models"] = [
            _identity_or_marker(item, 160) if _scalar(item) else None
            for item in models[:2]
        ]
    availability = summary.get("availability")
    if isinstance(availability, dict):
        fields["availability"] = {
            str(key): bool(value) for key, value in availability.items()
        }
    elif availability:
        fields["availability"] = {"unsupported": False}
    return fields


def _valid_listing(value):
    if not isinstance(value, dict):
        return False
    for name, item in value.items():
        if name == "models":
            if not isinstance(item, list) or not all(_scalar(m) for m in item):
                return False
        elif name == "availability":
            if not isinstance(item, dict) or not all(
                isinstance(flag, bool) for flag in item.values()
            ):
                return False
        elif name not in LISTING_FIELDS or not _scalar(item):
            return False
    return True


def _valid_stat_rows(value):
    if not isinstance(value, dict):
        return False
    context = value.get("context")
    dimensions = value.get("dimensions")
    if not (isinstance(context, list) and len(context) == 2
            and all(_number(item) for item in context)):
        return False
    if not isinstance(dimensions, list) or not all(
        isinstance(row, list) and len(row) == len(STAT_ROW_DIMENSIONS)
        and all(item is None or isinstance(item, str) for item in row)
        for row in dimensions
    ):
        return False
    for grain, names in STAT_ROW_METRICS.items():
        rows = value.get(grain)
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not (isinstance(row, list) and len(row) == 2 + len(names)):
                return False
            index = row[1]
            if (isinstance(index, bool) or not isinstance(index, int)
                    or not 0 <= index < len(dimensions)):
                return False
            if not all(_number(item) for item in (row[0], *row[2:])):
                return False
    return True


class MCPEvidenceCache:
    """Signature-validated listing fields and stat rows for discovered sessions."""

    def __init__(self, *, summary, state, summary_signature, state_signature,
                 path=None, write_text=None, persist=lambda: False,
                 code_version=lambda: ""):
        self._summary = summary
        self._state = state
        self._summary_signature = summary_signature
        self._state_signature = state_signature
        self._path = path
        self._write_text = write_text
        self._persist = persist
        self._code_version = code_version
        self._entries = {}
        self._lock = threading.Lock()
        self._loaded = False
        self._dirty = False

    @staticmethod
    def _key(source):
        return _digest(("path", str(source.get("path") or source.get("id") or "")))

    def _entry(self, source):
        self._load()
        return self._entries.get(self._key(source)) or {}

    def _store(self, source, **fields):
        with self._lock:
            key = self._key(source)
            entry = dict(self._entries.get(key) or {})
            entry.update(fields)
            self._entries[key] = entry
            self._dirty = True

    def listing(self, source):
        """Return listing summary fields, computing the summary only when stale."""
        if not source:
            return {}
        signature = _digest(self._summary_signature(source))
        entry = self._entry(source)
        if entry.get("listing_signature") == signature:
            return entry["listing"]
        fields = listing_fields(self._summary(source))
        self._store(source, listing_signature=signature, listing=fields)
        return fields

    def stat_rows(self, source):
        """Return compact stat rows, parsing the trace only when its evidence changed."""
        if not source:
            return None
        signature = _digest(self._state_signature(source))
        entry = self._entry(source)
        if entry.get("stats_signature") == signature:
            return entry["stats"]
        state = self._state(source)
        if not state:
            return None
        rows = session_stat_rows(source, state)
        self._store(source, stats_signature=signature, stats=rows)
        return rows

    def retain(self, sources):
        """Drop entries for sessions that are no longer discovered."""
        live = {self._key(source) for source in sources or () if source}
        self._load()
        with self._lock:
            for key in [key for key in self._entries if key not in live]:
                del self._entries[key]
                self._dirty = True

    def _load(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self._path or not self._persist():
                return
            try:
                if os.path.getsize(self._path) > 2 * MAX_PERSISTED_BYTES:
                    return
                with open(self._path, encoding="utf-8") as handle:
                    stored = json.load(handle)
            except (OSError, ValueError, RecursionError):
                # Missing, unreadable, or malformed files are a cold cache.
                return
            if not isinstance(stored, dict) or stored.get("schema") != SCHEMA_VERSION:
                return
            # Rows computed by other parser or pricing code are not reused.
            if stored.get("code") != self._code_version():
                return
            entries = stored.get("sessions")
            if not isinstance(entries, dict):
                return
            for key, entry in entries.items():
                if len(self._entries) >= MAX_SESSIONS:
                    break
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                try:
                    kept = self._validated(entry)
                except (OverflowError, TypeError, ValueError, RecursionError):
                    kept = None
                if kept:
                    self._entries[key] = kept

    @staticmethod
    def _validated(entry):
        kept = {}
        if (isinstance(entry.get("listing_signature"), str)
                and _valid_listing(entry.get("listing"))):
            kept["listing_signature"] = entry["listing_signature"]
            kept["listing"] = entry["listing"]
        if (isinstance(entry.get("stats_signature"), str)
                and _valid_stat_rows(entry.get("stats"))):
            kept["stats_signature"] = entry["stats_signature"]
            kept["stats"] = entry["stats"]
        return kept if kept and _persistable(kept) else None

    def flush(self):
        """Persist changed entries atomically when persistence is enabled."""
        if not self._path or not self._write_text or not self._persist():
            return False
        with self._lock:
            if not self._dirty:
                return False
            entries = list(self._entries.items())[-MAX_SESSIONS:]
            self._dirty = False
        parts = []
        size = 0
        for key, entry in entries:
            if not _persistable(entry):
                continue
            part = json.dumps(key) + ":" + json.dumps(entry, separators=(",", ":"))
            if size + len(part) > MAX_PERSISTED_BYTES:
                continue
            parts.append(part)
            size += len(part) + 1
        text = '{"schema":%d,"code":%s,"sessions":{%s}}' % (
            SCHEMA_VERSION, json.dumps(self._code_version()), ",".join(parts),
        )
        try:
            self._write_text(self._path, text)
        except OSError:
            with self._lock:
                self._dirty = True
            return False
        return True
