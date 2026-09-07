"""Read-only adapter for Hermes Agent's aggregate SQLite session evidence.

Hermes stores raw messages and tool payloads separately.  This adapter never
queries those tables: it reads only the bounded aggregate columns from
``sessions`` that Hermes already maintains for each session.
"""

import math
import os
import sqlite3
import time
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
    UsageEvidence,
)
from token_meter.domain.usage import distribute_reported_cost_counts


MAX_SOURCES = 2_000
_SESSION_COLUMNS = frozenset((
    "id", "model", "billing_provider", "started_at", "ended_at",
    "last_activity_at", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd",
    "actual_cost_usd", "cost_status", "cost_source", "tool_call_count",
    "api_call_count", "billing_mode",
))
_MODEL_USAGE_COLUMNS = frozenset((
    "session_id", "model", "billing_provider", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "actual_cost_usd",
    "estimated_cost_usd", "cost_status", "cost_source", "billing_mode", "api_call_count",
))
_MODEL_PROVIDERS = frozenset((
    "anthropic", "openai", "amazon", "google", "xai", "mistral",
    "deepseek", "groq", "together", "fireworks",
))
_BILLING_MODES = frozenset((
    "official_docs_snapshot", "official_models_api", "subscription_included", "unknown",
))
_SAFE_READS = {"sessions": _SESSION_COLUMNS, "session_model_usage": _MODEL_USAGE_COLUMNS}


def _restricted_authorizer(action, arg1, arg2, database, trigger):
    del database, trigger
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in _SAFE_READS and arg2 in _SAFE_READS[arg1] else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if (
            str(arg1).lower() == "table_info" and str(arg2).lower() in _SAFE_READS
        ) else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _integer(value):
    value = _number(value)
    if value is None or value != int(value):
        return None
    return int(value)


def _timestamp(value):
    value = _number(value)
    if value is None:
        return 0.0
    value = value / 1000.0 if value > 10_000_000_000 else value
    try:
        datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        return 0.0
    return value


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _model_ref(provider, model):
    """Preserve only an ordinary public model identifier from aggregate data."""
    raw_model = str(model or "").strip()
    lower_model = raw_model.lower()
    if (not raw_model or len(raw_model) > 160 or "://" in lower_model
            or raw_model.startswith(("/", "~", ".")) or "/" in raw_model or "\\" in raw_model
            or "<" in raw_model or ">" in raw_model
            or lower_model.startswith(("arn:", "sk-", "key-", "account:"))):
        raw_model = "unknown-model"
    provider = str(provider or "").strip().lower()
    if provider not in _MODEL_PROVIDERS:
        if lower_model.startswith("claude-"):
            provider = "anthropic"
        elif lower_model.startswith(("gpt-", "o1", "o3", "o4")):
            provider = "openai"
        else:
            provider = "unknown-model-provider"
    return ModelRef(provider, raw_model.lower())


def _account_provider(value):
    value = str(value or "").strip().lower()
    return {"bedrock": "amazon", "amazon-bedrock": "amazon", "aws-bedrock": "amazon"}.get(value, value if value in _MODEL_PROVIDERS | frozenset(("openrouter", "azure", "vertex")) else None)


def _billing_mode(value):
    value = str(value or "").strip().lower()
    return value if value in _BILLING_MODES else "unknown-billing-mode"


class HermesRuntimeAdapter:
    """Discover Hermes session aggregates without opening message evidence."""

    descriptor = RuntimeDescriptor(
        "hermes", "Hermes Agent", frozenset(("sessions", "models")),
        "runtime.generic", "runtime-neutral", None,
    )

    def __init__(self, database_path, project_resolver=None, compatibility=None, authorizer=None):
        self.database_path = Path(os.path.abspath(os.path.expanduser(str(database_path))))
        self.project_resolver = project_resolver or (lambda value: "")
        self.compatibility = dict(compatibility or {})
        self.authorizer = authorizer

    def _connection(self):
        con = sqlite3.connect(
            "{}?mode=ro".format(self.database_path.resolve().as_uri()), uri=True
        )
        con.set_authorizer(self.authorizer or _restricted_authorizer)
        return con

    def _columns(self):
        try:
            with self._connection() as con:
                return frozenset(row[1] for row in con.execute("PRAGMA table_info(sessions)"))
        except (OSError, sqlite3.Error):
            return frozenset()

    def _rows(self):
        columns = self._columns()
        selected = sorted(columns & _SESSION_COLUMNS)
        if "id" not in selected:
            return ()
        try:
            with self._connection() as con:
                con.row_factory = sqlite3.Row
                order = "last_activity_at DESC" if "last_activity_at" in columns else "id ASC"
                query = "SELECT {} FROM sessions ORDER BY {} LIMIT ?".format(
                    ", ".join('"{}"'.format(column) for column in selected), order,
                )
                return tuple(dict(row) for row in con.execute(query, (MAX_SOURCES,)))
        except (OSError, sqlite3.Error):
            return ()

    def _row_for(self, session_id):
        return next((row for row in self._rows() if row.get("id") == session_id), None)

    def _model_usage_rows(self, session_id):
        """Read Hermes' aggregate per-model rows, never messages or task bodies."""
        try:
            with self._connection() as con:
                columns = frozenset(row[1] for row in con.execute("PRAGMA table_info(session_model_usage)"))
                selected = sorted(columns & _MODEL_USAGE_COLUMNS)
                if "session_id" not in selected or "model" not in selected:
                    return ()
                con.row_factory = sqlite3.Row
                query = "SELECT {} FROM session_model_usage WHERE session_id = ? LIMIT ?".format(
                    ", ".join('"{}"'.format(column) for column in selected)
                )
                return tuple(dict(row) for row in con.execute(query, (session_id, MAX_SOURCES)))
        except (OSError, sqlite3.Error):
            return ()

    def _record(self, row):
        session_id = str(row.get("id") or "").strip()
        if not session_id or len(session_id) > 120:
            return None
        model = _model_ref(row.get("billing_provider"), row.get("model"))
        activity = _timestamp(row.get("last_activity_at"))
        if not activity:
            activity = _timestamp(row.get("ended_at")) or _timestamp(row.get("started_at"))
        return {
            "provider": "hermes", "client": "hermes", "label": "Hermes Agent",
            "runtime": "Hermes Agent", "id": session_id, "session": session_id,
            # The database path remains adapter-private; legacy consumers use an opaque key.
            "path": "hermes:{}".format(session_id), "project": "",
            "mtime": activity, "signature_mtime": activity,
            "request_revision": "|".join(self._revision().parts), "title": "Hermes session",
            "model": model.model_id, "model_provider": model.provider_id,
            "account_provider": _account_provider(row.get("billing_provider")),
            "source_kind": "hermes_sqlite",
        }

    def _legacy_records(self):
        return tuple(record for record in (self._record(row) for row in self._rows()) if record)

    def discover_legacy(self, context):
        del context
        return self._legacy_records()

    def discover(self, context):
        del context
        result = []
        for record in self._legacy_records():
            result.append(SessionSource(
                runtime_id="hermes", client_id="hermes", session_id=record["id"],
                display_label="Hermes Agent", project=record["project"] or None,
                locator=SourceLocator("sqlite-session", record["id"]),
                activity_mtime=record["mtime"], revision=self._revision(),
                model_ref=_model_ref(record["model_provider"], record["model"]),
                account_provider_id=record.get("account_provider"),
            ))
        return tuple(result)

    def _revision(self):
        try:
            stat = self.database_path.stat()
            parts = ["hermes-sqlite", str(stat.st_mtime_ns), str(stat.st_size)]
            for suffix in ("-wal", "-shm"):
                try:
                    sidecar = self.database_path.with_name(self.database_path.name + suffix).stat()
                    parts.extend((str(sidecar.st_mtime_ns), str(sidecar.st_size)))
                except OSError:
                    parts.extend(("0", "0"))
            return SourceRevision(tuple(parts))
        except OSError:
            return SourceRevision(("hermes-sqlite", "0", "0"))

    def current_revision(self, source):
        del source
        return self._revision()

    @staticmethod
    def _evidence(value, basis=EvidenceBasis.MEASURED):
        return EvidenceValue(value, basis) if value is not None else EvidenceValue.unavailable()

    def _usage(self, row):
        # SQLite initializes cost columns to numeric zero even when billing has
        # not produced a cost.  Status is the evidence gate, so default zero is
        # never promoted into a measured or estimated result.
        status = str(row.get("cost_status") or "").strip().lower()
        source = str(row.get("cost_source") or "").strip().lower()
        # Subscription-included is a flat-rate entitlement, not observed
        # allocable usage cost; keep it unavailable rather than measured zero.
        qualifying_source = bool(source) and source not in ("unknown", "unavailable", "none", "default")
        actual = _number(row.get("actual_cost_usd")) if qualifying_source and status in ("actual", "reported", "final") else None
        estimated = _number(row.get("estimated_cost_usd")) if qualifying_source and status in ("estimated", "estimate", "recorded") else None
        cost = self._evidence(actual, EvidenceBasis.MEASURED)
        if actual is None:
            cost = self._evidence(estimated, EvidenceBasis.ESTIMATED)
        output = _integer(row.get("output_tokens"))
        reasoning = _integer(row.get("reasoning_tokens"))
        # Hermes output already includes reasoning; reasoning is a detail, not
        # an additional token bucket.
        return UsageEvidence(
            self._evidence(_integer(row.get("input_tokens"))),
            self._evidence(output),
            self._evidence(_integer(row.get("cache_read_tokens"))),
            self._evidence(_integer(row.get("cache_write_tokens"))),
            cost,
        )

    def _model_usage(self, session_id):
        grouped = {}
        source_rows = self._model_usage_rows(session_id)
        for row in source_rows:
            model = _model_ref(row.get("billing_provider"), row.get("model")).model_id
            provider = _model_ref(row.get("billing_provider"), row.get("model")).provider_id
            route = _account_provider(row.get("billing_provider")) or "unknown-billing-route"
            mode = _billing_mode(row.get("billing_mode"))
            values = grouped.setdefault(
                (provider, route, mode, model),
                {
                    "rows": [], "source_rows": [], "provider": provider,
                    "route": route, "mode": mode,
                },
            )
            values["rows"].append(self._usage(row))
            values["source_rows"].append(row)
        result = []
        for (provider, route, mode, model), values in grouped.items():
            rows = values["rows"]
            def total(field):
                evidence = [getattr(row, field) for row in rows]
                return sum(value.value for value in evidence) if all(value.value is not None for value in evidence) else None
            result.append({
                "model": model, "provider": provider, "route": route, "mode": mode,
                "input": total("input_tokens"), "output": total("output_tokens"),
                "cache_read": total("cache_read_tokens"), "cache_write": total("cache_write_tokens"),
                "cost": total("cost_usd"),
                "cost_basis": (
                    EvidenceBasis.UNAVAILABLE if total("cost_usd") is None
                    else EvidenceBasis.MEASURED if all(row.cost_usd.basis is EvidenceBasis.MEASURED for row in rows)
                    else EvidenceBasis.ESTIMATED
                ),
                "executions": sum(
                    _integer(row.get("api_call_count")) or 0
                    for row in values["source_rows"]
                ),
                "reasoning": sum(
                    _integer(row.get("reasoning_tokens")) or 0
                    for row in values["source_rows"]
                ),
            })
        return tuple(result)

    def load(self, source, detail):
        if isinstance(source, dict):
            return self.recompute_legacy(source)
        if not isinstance(source, SessionSource) or source.runtime_id != "hermes":
            raise ValueError("source belongs to another runtime")
        row = self._row_for(source.session_id)
        if row is None:
            raise ValueError("Hermes session is no longer available")
        warnings = ()
        if not any(_integer(row.get(field)) is not None for field in ("input_tokens", "output_tokens")):
            warnings = (ParseWarning("usage_unavailable", "Hermes token evidence was unavailable."),)
        return NormalizedSession(
            source=source,
            started_at=_date(_timestamp(row.get("started_at"))),
            ended_at=_date(_timestamp(row.get("ended_at")) or _timestamp(row.get("last_activity_at"))),
            usage=self._usage(row), timing=TimingEvidence.unavailable(), tools=(), turns=(),
            pricing_basis=None, capabilities=self.descriptor.capabilities,
            warnings=warnings, detail=detail,
        )

    def _legacy_row(self, source):
        return self._row_for(str(source.get("id") or ""))

    def _require_compatibility(self):
        if not hasattr(self, "compatibility") or not self.compatibility:
            raise RuntimeError("legacy compatibility projection is unavailable")
        return self.compatibility

    @staticmethod
    def _cost_breakdown(total_cost, input_tokens, output_tokens, cache_read, cache_write, reasoning):
        split = distribute_reported_cost_counts(
            total_cost, input_tokens, max(0, output_tokens - reasoning),
            cache_read, cache_write, reasoning,
        )
        split["output"] += split.pop("reasoning", 0.0)
        return split

    def recompute_legacy(self, source):
        compat = self._require_compatibility()
        row = self._legacy_row(source)
        if row is None:
            return None
        usage = self._usage(row)
        input_tokens = usage.input_tokens.value or 0
        output_tokens = usage.output_tokens.value or 0
        reasoning_tokens = _integer(row.get("reasoning_tokens")) or 0
        cache_read = usage.cache_read_tokens.value or 0
        cache_write = usage.cache_write_tokens.value or 0
        total = input_tokens + output_tokens + cache_read + cache_write
        cost_available = usage.cost_usd.value is not None
        total_cost = usage.cost_usd.value or 0.0
        start = _timestamp(row.get("started_at"))
        end = _timestamp(row.get("ended_at")) or _timestamp(row.get("last_activity_at"))
        model = _model_ref(row.get("billing_provider"), row.get("model")).model_id
        availability = compat["metric_availability"](
            "hermes", cost=cost_available,
            tokens=usage.input_tokens.value is not None and usage.output_tokens.value is not None,
            input_tokens=usage.input_tokens.value is not None,
            output_tokens=usage.output_tokens.value is not None,
            cache=usage.cache_read_tokens.value is not None and usage.cache_write_tokens.value is not None,
            timing=False, throughput=False, context=False, tool_results=False,
        )
        execution = {
            "id": "{}:aggregate".format(source["id"]), "idx": 1, "ts": end or start,
            "time": time.strftime("%H:%M", time.localtime(end or start or 0)), "model": model,
            "tokens": {"input": input_tokens, "output": output_tokens, "reasoning": reasoning_tokens,
                       "retrieval": 0, "fresh_input": input_tokens,
                       "cache": cache_read + cache_write, "cache_read": cache_read,
                       "cache_write": cache_write, "total": total},
            "cost": total_cost, "cost_breakdown": self._cost_breakdown(
                total_cost, input_tokens, output_tokens, cache_read, cache_write, reasoning_tokens), "tools": [], "tool_count": 0,
            "model_calls": _integer(row.get("api_call_count")) or 0, "reasoning_tokens": reasoning_tokens,
            "reasoning_duration_ms": 0, "context_tokens": 0, "context_window": 0,
            "context_pct": None, "duration_ms": None, "wait_duration_ms": None,
            "summary": "Hermes session aggregate", "user_message": "", "user_input": "",
            "availability": availability,
        }
        state_source = dict(source)
        state_source["cache_savings_available"] = False
        analyses = compat["analysis_block"](
            {"input": input_tokens, "output": output_tokens, "cache_read": cache_read, "cache_write": cache_write},
            total_cost, reasoning_tokens, 1 if reasoning_tokens > 0 else 0, 0.0,
            {model: total}, {model: total_cost},
            compat["tool_summary"]([execution]), 0.0, 0, 1,
        )
        return compat["build_state"](
            state_source,
            {"input": input_tokens, "output": output_tokens, "cache_read": cache_read, "cache_write": cache_write},
            self._cost_breakdown(total_cost, input_tokens, output_tokens, cache_read, cache_write, reasoning_tokens),
            total, total_cost, [], [execution], [],
            {"reasoning": reasoning_tokens, "output": max(0, output_tokens - reasoning_tokens), "retrieval": 0, "coordination": 0}, analyses, [],
            start, end, 0, {"cost": total_cost, "idx": 1} if cost_available else None, 0,
            usage.cost_usd.basis is EvidenceBasis.ESTIMATED, model,
            "Hermes-recorded aggregate cost; category split is token-weighted estimate.",
            {"duration_s": 0, "available": False, "basis": "unavailable"}, [], availability,
        )

    def summarize_legacy(self, source, unused=None):
        del unused
        compat = self._require_compatibility()
        row = self._legacy_row(source)
        if row is None:
            return compat["summary_row"](source, "Hermes session", 0.0, 0, 0, set(), 0, 0, {}, {}, {}, False)
        usage = self._usage(row)
        model = _model_ref(row.get("billing_provider"), row.get("model")).model_id
        model_usage = self._model_usage(str(source.get("id") or ""))
        start = _timestamp(row.get("started_at"))
        end = _timestamp(row.get("ended_at")) or _timestamp(row.get("last_activity_at"))
        total = sum(value or 0 for value in (
            usage.input_tokens.value, usage.output_tokens.value,
            usage.cache_read_tokens.value, usage.cache_write_tokens.value,
        ))
        cost = usage.cost_usd.value or 0.0
        session_total = total
        session_cost = usage.cost_usd.value
        if model_usage:
            model_total = sum(sum(detail[field] or 0 for field in ("input", "output", "cache_read", "cache_write")) for detail in model_usage)
            model_cost_total = sum(detail["cost"] for detail in model_usage) if all(detail["cost"] is not None for detail in model_usage) else None
            if model_total != session_total or (model_cost_total is not None and session_cost is not None and not math.isclose(model_cost_total, session_cost, rel_tol=0, abs_tol=1e-9)):
                model_usage = ()
        if model_usage:
            # Per-model aggregate rows are Hermes' durable attribution source.
            # Use their totals once, rather than adding session and model rows.
            total = sum(sum(detail[field] or 0 for field in ("input", "output", "cache_read", "cache_write")) for detail in model_usage)
            if all(detail["cost"] is not None for detail in model_usage):
                cost = sum(detail["cost"] for detail in model_usage)
        available = compat["metric_availability"](
            "hermes", cost=usage.cost_usd.value is not None,
            tokens=usage.input_tokens.value is not None and usage.output_tokens.value is not None,
            input_tokens=usage.input_tokens.value is not None,
            output_tokens=usage.output_tokens.value is not None,
            cache=usage.cache_read_tokens.value is not None and usage.cache_write_tokens.value is not None,
            timing=False, throughput=False, context=False, tool_results=False,
        )
        # Hermes has session-wide aggregates but no per-call/day ledger.  Do not
        # misattribute a resumed or cross-day session's whole cost to its end day.
        day_cost = {}
        base_identity_counts = {}
        for detail in model_usage:
            base = (detail["provider"], detail["route"], detail["model"])
            base_identity_counts[base] = base_identity_counts.get(base, 0) + 1

        def model_key(detail):
            base = (detail["provider"], detail["route"], detail["model"])
            if base_identity_counts.get(base, 0) > 1:
                return "{}:{}:{}:{}".format(
                    detail["provider"], detail["route"], detail["mode"], detail["model"],
                )
            return "{}:{}:{}".format(*base)
        model_cost = {model_key(detail): detail["cost"] for detail in model_usage if detail["cost"] is not None} or ({model: cost} if usage.cost_usd.value is not None else {})
        model_tokens = {model_key(detail): sum(detail[field] or 0 for field in ("input", "output", "cache_read", "cache_write")) for detail in model_usage} or {model: total}
        model_stats = {
            model_key(detail): {"cost": detail["cost"] or 0.0,
                              "tokens": model_tokens[model_key(detail)],
                              "input_tokens": detail["input"] or 0, "output_tokens": detail["output"] or 0,
                              "cache_read_tokens": detail["cache_read"] or 0,
                              "cache_write_tokens": detail["cache_write"] or 0,
                              "reasoning_tokens": detail["reasoning"], "reasoning_output_tokens": detail["reasoning"],
                              "reasoning_executions": 1 if (detail["reasoning"] or 0) > 0 else 0,
                              "executions": detail["executions"],
                              "availability": {**available, "cost": detail["cost"] is not None}}
            for detail in model_usage
        } or {model: {"cost": cost, "tokens": total, "input_tokens": usage.input_tokens.value or 0,
                      "output_tokens": usage.output_tokens.value or 0, "cache_read_tokens": usage.cache_read_tokens.value or 0,
                      "cache_write_tokens": usage.cache_write_tokens.value or 0,
                      "reasoning_tokens": _integer(row.get("reasoning_tokens")) or 0,
                      "reasoning_output_tokens": _integer(row.get("reasoning_tokens")) or 0,
                      "reasoning_executions": 1 if (_integer(row.get("reasoning_tokens")) or 0) > 0 else 0,
                      "executions": _integer(row.get("api_call_count")) or 0, "availability": available}}
        return compat["summary_row"](
            source, "Hermes session", cost, total, _integer(row.get("api_call_count")) or 0,
            set(model_tokens), start, end, model_cost, model_tokens, day_cost,
            usage.cost_usd.basis is EvidenceBasis.ESTIMATED,
            {"duration_s": 0, "available": False, "basis": "unavailable"},
            usage.input_tokens.value or 0, usage.output_tokens.value or 0,
            model_stats, [], [], [], available,
        )

    def deletion_plan(self, source):
        del source
        return DeletionPlan.deny("Hermes session storage is read-only.")


class HermesRuntimeAdapterProxy:
    descriptor = HermesRuntimeAdapter.descriptor

    def __init__(self, adapter_factory):
        self._adapter_factory = adapter_factory

    def _adapter(self):
        return self._adapter_factory()

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
