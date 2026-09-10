#!/usr/bin/env python3
"""
Token Meter application composition and legacy compatibility surface.

Tails local agent logs, parses each execution as it lands, and serves a
localhost dashboard with Sessions and aggregate history views. Stdlib only. Trace analysis
stays local; the optional menu-bar quota view makes read-only account-usage
requests to the signed-in Claude, Codex, and Cursor provider services. OpenCode
usage is read from its local database and does not require a provider request.

  python3 meter.py     ->  http://localhost:8722

Claude correctness note: one API response (message.id) can be split across
several JSONL lines, one per content block, and each line repeats the same usage
block. Claude parsing dedupes by message.id so costs are not double-counted.
Codex uses token_count events instead; those are already one usage slice.
"""
import calendar
import copy
import contextlib
import datetime
import functools
import glob
import hashlib
import html
import json
import math
import os
import queue
import random
import re
import secrets
import selectors
import shlex
import shutil
import sqlite3
import stat
import subprocess
import statistics
import time
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

IMPLEMENTATION_FILE = os.path.abspath(__file__)
_SOURCE_ROOT = os.path.dirname(os.path.dirname(IMPLEMENTATION_FILE))

from token_meter.compat import envelope_to_legacy_source, legacy_source_to_envelope
from token_meter.contracts import (
    AdapterFailure,
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    ModelRef,
    PriceQuery,
    PriceQuote,
)
from token_meter.domain.usage import (
    cache_metrics as _domain_cache_metrics,
    cache_savings_for_rate as _domain_cache_savings_for_rate,
    cost_breakdown_values as _domain_cost_breakdown_values,
    make_usage_provenance as _domain_make_usage_provenance,
    metric_available as _domain_metric_available,
    normalize_reported_token_count as _domain_normalize_reported_token_count,
    usage_io_token_counts as _domain_usage_io_token_counts,
    usage_provenance as _domain_usage_provenance,
    usage_token_total_counts as _domain_usage_token_total_counts,
)
from token_meter.domain.aggregates import (
    add_model_daily as _domain_add_model_daily,
    add_model_summary as _domain_add_model_summary,
    aggregate_cross_session_rows as _domain_aggregate_cross_session_rows,
    aggregate_model_stats as _domain_aggregate_model_stats,
    current_session_summaries as _domain_current_session_summaries,
    daily_summaries as _domain_daily_summaries,
    global_tool_waste as _domain_global_tool_waste,
    metric_coverage as _domain_metric_coverage,
    monthly_summaries as _domain_monthly_summaries,
    rollup_language_signal_events as _domain_rollup_language_signal_events,
    spend_log_summaries as _domain_spend_log_summaries,
    spend_projection as _domain_spend_projection,
)
from token_meter.domain.insights import (
    build_cost_insights as _domain_build_cost_insights,
    enrich_insights as _domain_enrich_insights,
    execution_low_yield as _domain_execution_low_yield,
    insight as _domain_insight,
    insight_sort_key as _domain_insight_sort_key,
    is_operational_warning as _domain_is_operational_warning,
    low_yield_should_warn as _domain_low_yield_should_warn,
    normalize_insights as _domain_normalize_insights,
)
from token_meter.domain.timing import (
    merge_execution_intervals as _domain_merge_execution_intervals,
    performance_summary as _domain_performance_summary,
    wait_time_summary as _domain_wait_time_summary,
)
from token_meter.domain.tools import (
    capability_control_groups as _domain_capability_control_groups,
    optional_capability_summary as _domain_optional_capability_summary,
    summarize_tool_evidence as _domain_summarize_tool_evidence,
    tool_identity as _domain_tool_identity,
    tool_summary as _domain_tool_summary,
)
from token_meter.services.git_delivery import GitDeliveryLedger, GitDeliveryService
from token_meter.models.catalog import (
    ANTHROPIC_PRICE as CLAUDE_PRICE,
    BUILTIN_MODEL_PRICE_HISTORY as _CANONICAL_BUILTIN_MODEL_PRICE_HISTORY,
    BUILTIN_PRICE_REVIEWED_ON,
    BUILTIN_PRICE_SOURCES,
    CURSOR_VARIANT_MODEL_IDS,
    CURSOR_PRICE,
    DEFAULT_MODELS as _DEFAULT_MODELS,
    GPT_56_LONG_CONTEXT_TOKENS,
    GPT_56_PRICE_UPDATE_AT,
    MODEL_PRICE_FIELDS,
    OPENAI_PRICE,
    ZERO_PRICE,
    canonical_model_provider,
    normalize_model_id,
    settings_provider_for_model_provider,
)
from token_meter.models.pricing import (
    builtin_price_table as _catalog_builtin_price_table,
    effective_price_table as _catalog_effective_price_table,
    matching_price as _catalog_matching_price,
    model_alias_matches as _catalog_model_alias_matches,
    price_period_key as _catalog_price_period_key,
    quote_for as _catalog_quote_for,
    revision_at as _catalog_revision_at,
)
from token_meter.platforms.base import ProcessPurpose
from token_meter.platforms.registry import platform_services
from token_meter.quotas.base import CallableQuotaAdapter, QuotaUnavailable
from token_meter.quotas import anthropic as anthropic_quotas
from token_meter.quotas.common import (
    quota_compact_duration,
    quota_coverage_note,
    quota_http_json,
    quota_number,
    quota_pace,
    quota_provider,
    quota_slug,
    quota_timestamp,
    quota_window,
)
from token_meter.quotas import cursor as cursor_quotas
from token_meter.quotas import openai as openai_quotas
from token_meter.quotas import xai as xai_quotas
from token_meter.quotas.registry import QuotaRegistry
from token_meter.runtimes.cursor import (
    CursorRuntimeAdapter,
    CursorRuntimeAdapterProxy,
)
from token_meter.runtimes.codex import (
    AUTO_REVIEW_MODEL,
    CodexRuntimeAdapter,
    CodexRuntimeAdapterProxy,
)
from token_meter.runtimes.claude import (
    ClaudeRuntimeAdapter,
    ClaudeRuntimeAdapterProxy,
    _normalized_usage as _normalized_claude_usage,
)
from token_meter.runtimes.opencode import (
    OpenCodeRuntimeAdapter,
    OpenCodeRuntimeAdapterProxy,
    context_tokens as _native_opencode_context_tokens,
    decode_json as _native_opencode_json,
    display_cost as _native_opencode_display_cost,
    distribute_cost as _native_opencode_distribute,
    int_value as _native_opencode_int,
    reported_cost as _native_opencode_reported_cost,
    usage_counts as _native_opencode_usage,
)
from token_meter.runtimes.kiro import (
    KiroRuntimeAdapter,
    KiroRuntimeAdapterProxy,
    default_agent_storage_root as _default_kiro_agent_storage_root,
)
from token_meter.runtimes.pi import (
    PiRuntimeAdapter,
    PiRuntimeAdapterProxy,
)
from token_meter.runtimes.hermes import (
    HermesRuntimeAdapter,
    HermesRuntimeAdapterProxy,
)
from token_meter.runtimes.grok import (
    GrokRuntimeAdapter,
    GrokRuntimeAdapterProxy,
)
from token_meter.runtimes.path_cache import BoundedPathCache
from token_meter.runtimes.registry import RuntimeRegistry
from token_meter.mcp.service import MCPQueryService
from token_meter.services.agent_api import AgentAPIService
from token_meter.services.application import Application
from token_meter.services.budgets import BudgetService
from token_meter.services.capabilities import CapabilityService
from token_meter.services.deletion import DeletionService
from token_meter.services.menubar import MenubarService
from token_meter.services.sessions import SessionService
from token_meter.services.settings import SettingsService
from token_meter.services.runtime_catalog import (
    menubar_runtime_catalog,
    runtime_catalog as _runtime_catalog,
)
from token_meter.services.updates import UpdateService
from token_meter.web.server import serve_local

_PLATFORM_SERVICES = platform_services()
_PLATFORM_PATHS = _PLATFORM_SERVICES.resolve_paths()
IS_LINUX = _PLATFORM_SERVICES.platform_id == "linux"
XDG_CONFIG_HOME = _PLATFORM_PATHS.config_home
XDG_DATA_HOME = _PLATFORM_PATHS.data_home
XDG_CACHE_HOME = _PLATFORM_PATHS.cache_home

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CLAUDE_DESKTOP_DATA_ROOTS = list(_PLATFORM_PATHS.claude_desktop_data_roots)
CLAUDE_DESKTOP_SESSIONS = os.path.join(CLAUDE_DESKTOP_DATA_ROOTS[0], "claude-code-sessions")
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
CLAUDE_ROOT_CONFIG = os.path.expanduser("~/.claude.json")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
CODEX_INDEX = os.path.expanduser("~/.codex/session_index.jsonl")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
CODEX_AUTH = os.path.expanduser("~/.codex/auth.json")
CLAUDE_CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
CURSOR_PROJECTS = os.path.expanduser("~/.cursor/projects")
CURSOR_STATE_DB = _PLATFORM_PATHS.cursor_state_db
CURSOR_REQUEST_LOGS = _PLATFORM_PATHS.cursor_request_logs
# OpenCode keeps every session, message, and message part in one SQLite
# database (WAL-mode, held open while OpenCode runs). Its relative database
# override is rooted below the OpenCode XDG data directory, matching OpenCode.
OPENCODE_DATA_ROOT = _PLATFORM_PATHS.opencode_data_root
OPENCODE_CACHE_ROOT = _PLATFORM_PATHS.opencode_cache_root
_opencode_db_override = os.path.expanduser(os.environ.get("OPENCODE_DB", ""))
OPENCODE_DB = (
    _opencode_db_override
    if os.path.isabs(_opencode_db_override)
    else os.path.join(OPENCODE_DATA_ROOT, _opencode_db_override or "opencode.db")
)
OPENCODE_MODELS_PATH = os.path.expanduser(
    os.environ.get("OPENCODE_MODELS_PATH", os.path.join(OPENCODE_CACHE_ROOT, "models.json"))
)
KIRO_SESSIONS = os.path.abspath(os.path.expanduser(
    os.environ.get("KIRO_SESSIONS", "~/.kiro/sessions")
))
KIRO_AGENT_STORAGE = _default_kiro_agent_storage_root(
    os.path.expanduser("~"), os.environ
)
PI_AGENT_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")
))
GROK_HOME = os.path.abspath(os.path.expanduser(
    os.environ.get("GROK_HOME", "~/.grok")
))
GROK_AUTH = os.path.join(GROK_HOME, "auth.json")


def hermes_state_db_path(environ=None):
    environ = os.environ if environ is None else environ
    state_db = str(environ.get("HERMES_STATE_DB") or "").strip()
    home = str(environ.get("HERMES_HOME") or "").strip() or "~/.hermes"
    return os.path.abspath(os.path.expanduser(state_db or os.path.join(home, "state.db")))


HERMES_STATE_DB = hermes_state_db_path()
TOKEN_METER_SETTINGS = os.path.expanduser(
    os.environ.get("TOKEN_METER_SETTINGS", "~/.token-meter/settings.json")
)
TOKEN_METER_SESSION_MODEL_IDENTITIES = os.path.expanduser(
    os.environ.get(
        "TOKEN_METER_SESSION_MODEL_IDENTITIES",
        "~/.token-meter/session-model-identities.json",
    )
)
TOKEN_METER_UPDATE_STATUS = os.path.expanduser(
    os.environ.get("TOKEN_METER_UPDATE_STATUS", "~/.token-meter/update-status.json")
)
TOKEN_METER_GIT_DELIVERY_DB = os.path.expanduser(
    os.environ.get("TOKEN_METER_GIT_DELIVERY_DB", "~/.token-meter/git-delivery.sqlite3")
)
PORT = 8722

DEFAULT_FRUSTRATION_TERMS = [
    "fuck", "fck", "fucked", "fucking", "shit", "shitty", "bullshit",
    "idiot", "stupid", "useless", "crap", "damn", "wtf",
]
DEFAULT_POSITIVE_TERMS = [
    "thank you", "thanks", "perfect", "great",
    "exactly what i needed", "works now", "love it",
]
MAX_FRUSTRATION_TERMS = 64
MAX_FRUSTRATION_TERM_LENGTH = 40
MODEL_PRICE_PROVIDERS = ("claude", "codex", "cursor", "opencode")
MAX_CUSTOM_MODEL_PRICES = 100
MAX_MODEL_PRICE_PERIODS = 256
MAX_MODEL_PRICE_CHANGES = 256
MAX_MODEL_PRICE = 1_000_000.0
MODEL_PRICING_MTIME_TTL_S = 0.25
MAX_SESSION_MODEL_IDENTITIES = 2_048
SESSION_MODEL_IDENTITY_KEY_RE = re.compile(r"^[a-f0-9]{64}$")
SESSION_CLAUDE_MODEL_ID_RE = re.compile(
    r"^claude-(?:haiku|sonnet|opus)-[a-z0-9][a-z0-9._:-]{0,139}$",
    re.IGNORECASE,
)
HERMES_MODEL_IDENTITY_LABEL = "Bedrock application profile"
BUDGET_PROVIDERS = ("claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes", "grok")
DEFAULT_RUNTIME_BUDGET = 0.0
DEFAULT_BUDGET_THRESHOLDS = (80, 90, 100)
DEFAULT_SESSION_BUDGET = 10.0
MIN_SESSION_BUDGET = 0.5
MAX_MONTHLY_BUDGET = 100_000_000.0
OPENCODE_DETAIL_MESSAGE_LIMIT = 200
UPDATE_CHECK_INTERVAL_S = 10 * 60
GIT_DELIVERY_INTERVAL_S = 5 * 60
UPDATE_FETCH_TIMEOUT_S = 45
UPDATE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,199}$")

# Compatibility re-exports retain the existing runtime-oriented names while
# their authoritative data now lives in the model-provider catalog.
BUILTIN_MODEL_PRICE_HISTORY = {
    "codex": _CANONICAL_BUILTIN_MODEL_PRICE_HISTORY["openai"],
}
DEFAULT_CLAUDE_MODEL = _DEFAULT_MODELS["anthropic"]
DEFAULT_OPENAI_MODEL = _DEFAULT_MODELS["openai"]
CHARS_PER_TOKEN = 4
TRACE_LIMIT = 220
EXEC_LIMIT = 180
MENUBAR_CONTEXT_SOFT_PCT = 0.65
MENUBAR_CONTEXT_WATCH_PCT = 0.70
MENUBAR_CONTEXT_INTERVENE_PCT = 0.85
MENUBAR_COST_SPIKE = 0.50
QUOTA_REFRESH_S = 60.0
QUOTA_STALE_S = 10 * 60.0
QUOTA_HTTP_TIMEOUT_S = 8.0
QUOTA_PROCESS_TIMEOUT_S = 8.0
LOW_YIELD_RATIO = 0.005
LOW_YIELD_COST = 0.05
LOW_YIELD_CONTEXT_PCT = 0.25
LOW_YIELD_INPUT_TOKENS = 60000
TOOL_OVERSIZED_TOKENS = 8000
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.@:/-]{1,180}$")
SKILL_PATH_RE = re.compile(r"(?:^|[/\\])([^/\\\s'\"]+)[/\\]SKILL\.md(?:\b|$)", re.IGNORECASE)
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,179}$")
DATA_URL_RE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")
BASE64_FIELD_RE = re.compile(r'("(?:data|image_url)"\s*:\s*")([A-Za-z0-9+/=]{512,})(")')

subscribers, subscribers_lock = [], threading.Lock()
STATE = {}
_SOURCE_INVENTORY = {
    "ready": False,
    "sources": (),
    "count": None,
    "clients": {},
    "updated_at": None,
}
_git_delivery_service_instance = None
_git_delivery_service_lock = threading.Lock()
_git_delivery_wake = threading.Event()
_xsess = {
    "data": None, "at": 0.0, "sessions": [],
    "internal_rows": (), "project_model_stats": {},
}
_XSESS_TTL = 15.0
_XSESS_LIVE_REFRESH_S = _XSESS_TTL
_CURRENT_SESSION_REBUILD_S = 1.5
_SOURCE_MEMBERSHIP_PROBE_S = 2.0
_SOURCE_MEMBERSHIP_FALLBACK_S = 4.0
_SOURCE_DISCOVERY_REFRESH_S = 10.0
CURRENT_SESSION_MAX_AGE_S = 30 * 60
CURRENT_SESSION_WORKING_S = 90
CURRENT_SESSION_LIMIT = 8
CURRENT_SESSION_CONTEXT_SAMPLES = 32
SESSION_STATE_CACHE_LIMIT = 32
MODEL_PROJECT_OPTION_LIMIT = 500
OTHER_LOCAL_SESSIONS_PROJECT = "__other_local_sessions__"
_summary_cache = {}
_summary_cache_lock = threading.Lock()
_matched_pace_cache = {"signature": None, "data": None}
_matched_pace_cache_lock = threading.Lock()
_SKILL_CATALOG_TTL_S = 60.0
_skill_catalog_cache = {"rows": None, "at": 0.0}
_skill_catalog_cache_lock = threading.Lock()
_session_state_cache = {}
_session_state_cache_lock = threading.Lock()
_recursive_path_cache = BoundedPathCache(ttl_seconds=4.0, max_entries=64)
_RUNTIME_DISCOVERY_FAILURES = ()
_RUNTIME_LOAD_FAILURE = None
_model_pricing_cache = {
    "path": None, "mtime_ns": None, "mtime_checked_at": 0.0,
    "histories": {}, "effective": {}, "quotes": {},
}
_session_model_identity_lock = threading.RLock()
_quota_cache = {}
_quota_inflight = set()
_quota_lock = threading.Lock()
_QUOTA_REGISTRY = None
_quota_registry_lock = threading.Lock()
_update_operation_lock = threading.Lock()
_update_wake = threading.Event()
_ACTION_TOKEN = secrets.token_urlsafe(24)
AGENT_ACCESS_SERVER = "tokenmeter"
AGENT_CURRENT_MAX_AGE_S = 6 * 60 * 60


@functools.lru_cache(maxsize=65536)
def parse_iso(ts):
    # Logs are UTC (trailing Z). calendar.timegm treats the struct as UTC, so
    # idle/elapsed line up with time.time().
    try:
        return calendar.timegm(time.strptime((ts or "").split(".")[0], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def local_dt(ts):
    return time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime(ts)).lower() if ts else ""


def local_tm(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""


def duration_label(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _merge_execution_intervals(intervals):
    """Return wall-active seconds after collapsing overlapping execution windows."""
    return _domain_merge_execution_intervals(intervals)


def _claude_user_prompt(obj):
    if obj.get("type") != "user":
        return False
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
        for block in content
    )


def _is_user_input_tool(name):
    """Return whether a tool explicitly pauses the agent for human input."""
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return normalized in ("askuserquestion", "requestuserinput")


def _track_claude_user_pause(group, block, ts):
    if not group or not isinstance(block, dict) or block.get("type") != "tool_use":
        return
    if not _is_user_input_tool(block.get("name")):
        return
    tool_id = block.get("id")
    if tool_id:
        group.setdefault("user_pause_starts", {}).setdefault(tool_id, float(ts or 0))


def _close_claude_user_pauses(group, message, ts):
    if not group or not isinstance(message, dict):
        return
    starts = group.setdefault("user_pause_starts", {})
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        started = starts.pop(block.get("tool_use_id"), None)
        if started is not None and float(ts or 0) >= started:
            group["user_pause_s"] = float(group.get("user_pause_s") or 0) + float(ts or 0) - started


def _claude_effective_duration(group, duration_s, timing_basis):
    """Remove trace-visible human-response pauses from observed wall time."""
    duration_s = float(duration_s or 0)
    paused_s = float((group or {}).get("user_pause_s") or 0)
    excluded_s = min(duration_s, paused_s) if timing_basis == "observed" else 0.0
    return max(0.0, duration_s - excluded_s), excluded_s


def execution_timing(provider, objs):
    """Build trace-backed active execution time, excluding idle gaps."""
    intervals = []
    reported = observed = 0
    open_start = open_last = None

    for obj in objs:
        ts = parse_iso(obj.get("timestamp", ""))

        if provider == "claude":
            if _claude_user_prompt(obj):
                if open_start and open_last and open_last > open_start:
                    intervals.append((open_start, open_last))
                    observed += 1
                open_start = ts or open_start
                open_last = ts or open_last
                continue
            if obj.get("type") != "system" or obj.get("subtype") != "turn_duration":
                if open_start and ts and obj.get("type") == "assistant":
                    open_last = ts if open_last is None else max(open_last, ts)
                continue
            duration_ms = obj.get("durationMs")
            try:
                duration_ms = float(duration_ms or 0)
            except (TypeError, ValueError):
                duration_ms = 0
            if ts and duration_ms > 0:
                intervals.append((ts - duration_ms / 1000.0, ts))
                reported += 1
            elif open_start and ts and ts > open_start:
                intervals.append((open_start, ts))
                observed += 1
            open_start = open_last = None
            continue

        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        if ptype == "task_started":
            if open_start and open_last and open_last > open_start:
                intervals.append((open_start, open_last))
                observed += 1
            open_start = ts or open_start
            open_last = ts or open_last
            continue
        if ptype != "task_complete":
            if open_start and ts:
                open_last = ts if open_last is None else max(open_last, ts)
            continue
        duration_ms = payload.get("duration_ms")
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if ts and duration_ms > 0:
            intervals.append((ts - duration_ms / 1000.0, ts))
            reported += 1
        elif open_start and ts and ts > open_start:
            intervals.append((open_start, ts))
            observed += 1
        open_start = open_last = None

    if open_start and open_last and open_last > open_start:
        intervals.append((open_start, open_last))
        observed += 1

    duration_s = _merge_execution_intervals(intervals)
    if reported and observed:
        basis = "reported + observed"
    elif reported:
        basis = "reported"
    elif observed:
        basis = "observed"
    else:
        basis = "unavailable"
    row = {
        "duration_s": duration_s,
        "available": duration_s > 0,
        "reported_executions": reported,
        "observed_executions": observed,
        "execution_count": reported + observed,
        "basis": basis,
    }
    return row


def load(path):
    out = []
    if not path:
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {} if default is None else default


def atomic_write_text(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.token-meter-{os.getpid()}")
    mode = None
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def normalize_language_signal_terms(values, group="language signal"):
    """Normalize one user-editable lexical group while preserving display order."""
    if isinstance(values, str):
        values = re.split(r"[,\n]", values)
    if not isinstance(values, list):
        raise ValueError(f"{group.title()} terms must be a list or comma-separated text.")
    normalized = []
    seen = set()
    for value in values:
        term = " ".join(str(value or "").strip().lower().split())
        if not term:
            continue
        if len(term) > MAX_FRUSTRATION_TERM_LENGTH:
            raise ValueError(
                f"Each {group} term must be {MAX_FRUSTRATION_TERM_LENGTH} characters or fewer."
            )
        if any(ord(char) < 32 for char in term):
            raise ValueError(f"{group.title()} terms cannot contain control characters.")
        if term not in seen:
            normalized.append(term)
            seen.add(term)
    if len(normalized) > MAX_FRUSTRATION_TERMS:
        raise ValueError(f"Use at most {MAX_FRUSTRATION_TERMS} {group} terms.")
    return normalized


def normalize_frustration_terms(values):
    """Backward-compatible Friction-group normalizer."""
    return normalize_language_signal_terms(values, "friction")


def language_signal_settings(path=None):
    path = path or TOKEN_METER_SETTINGS
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}

    raw = settings.get("language_signal_terms")
    defaults = {
        "positive": list(DEFAULT_POSITIVE_TERMS),
        "friction": list(DEFAULT_FRUSTRATION_TERMS),
    }
    groups = {}
    for group in ("positive", "friction"):
        values = raw.get(group) if isinstance(raw, dict) and group in raw else None
        if values is None and group == "friction" and "frustration_terms" in settings:
            values = settings.get("frustration_terms")
        try:
            groups[group] = (
                normalize_language_signal_terms(values, group)
                if values is not None else list(defaults[group])
            )
        except ValueError:
            groups[group] = list(defaults[group])
    return {
        **groups,
        "defaults": defaults,
        "max_terms": MAX_FRUSTRATION_TERMS,
        "method": (
            "case-insensitive whole-phrase match; quoted or discussed phrases can match; "
            "not sentiment analysis"
        ),
    }


def set_language_signal_terms(values, path=None):
    """Persist both machine-wide lexical signal groups atomically."""
    path = path or TOKEN_METER_SETTINGS
    if not isinstance(values, dict):
        return {"ok": False, "error": "Language signal terms must be an object."}
    current = language_signal_settings(path)
    try:
        groups = {
            group: normalize_language_signal_terms(
                values.get(group, current[group]), group
            )
            for group in ("positive", "friction")
        }
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    changed = (
        settings.get("language_signal_terms") != groups
        or "frustration_terms" in settings
    )
    settings["language_signal_terms"] = groups
    settings.pop("frustration_terms", None)
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return {"ok": False, "error": f"Token Meter could not save settings: {error}"}
    return {
        "ok": True,
        "changed": changed,
        **groups,
        "defaults": {
            "positive": list(DEFAULT_POSITIVE_TERMS),
            "friction": list(DEFAULT_FRUSTRATION_TERMS),
        },
        "max_terms": MAX_FRUSTRATION_TERMS,
    }


def frustration_settings(path=None):
    """Return the Friction group through the legacy settings contract."""
    settings = language_signal_settings(path)
    return {
        "terms": list(settings["friction"]),
        "defaults": list(settings["defaults"]["friction"]),
        "max_terms": settings["max_terms"],
    }


def set_frustration_terms(values, path=None):
    """Persist the Friction group through the legacy settings contract."""
    result = set_language_signal_terms({"friction": values}, path)
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "changed": result.get("changed", False),
        "terms": list(result["friction"]),
        "defaults": list(result["defaults"]["friction"]),
        "max_terms": result["max_terms"],
    }


def normalize_model_price_provider(provider):
    provider = str(provider or "").strip().lower()
    if provider == "openai":
        provider = "codex"
    if provider not in MODEL_PRICE_PROVIDERS:
        raise ValueError("Provider must be Claude, Codex / OpenAI, Cursor, or OpenCode.")
    return provider


def normalize_model_price_id(model):
    return normalize_model_id(model)


def normalize_model_price(prices):
    if not isinstance(prices, dict):
        raise ValueError("Model prices must be an object.")
    normalized = {}
    for field in MODEL_PRICE_FIELDS:
        value = prices.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field.replace('_', ' ').title()} price must be a number.")
        value = float(value)
        if not math.isfinite(value) or value < 0 or value > MAX_MODEL_PRICE:
            raise ValueError(
                f"{field.replace('_', ' ').title()} price must be between 0 and "
                f"{MAX_MODEL_PRICE:,.0f}."
            )
        normalized[field] = value
    return normalized


def builtin_model_price_tables():
    return {
        "claude": CLAUDE_PRICE,
        "codex": OPENAI_PRICE,
        "cursor": CURSOR_PRICE,
        "opencode": {},
    }


def _price_timestamp(value):
    """Normalize an event timestamp; ``None`` intentionally means current pricing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        return parse_iso(value.strip())
    return None


def _price_datetime(value):
    timestamp = _price_timestamp(value)
    if timestamp is None:
        return None
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)


def _model_price_period_key(provider, at=None, histories=None):
    """Return the bounded cache bucket containing one event timestamp."""
    model_provider = canonical_model_provider(provider)
    return _catalog_price_period_key(
        model_provider,
        _price_timestamp(at),
        (histories or {}).get(provider, {}),
    )


def builtin_model_price_table(provider, at=None):
    """Return built-in prices effective for one usage-event timestamp."""
    return _catalog_builtin_price_table(
        canonical_model_provider(provider), _price_timestamp(at)
    )


def _model_pricing_mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _normalize_model_price_revision(raw, builtin):
    if not isinstance(raw, dict):
        raise ValueError("Model price period must be an object.")
    effective_from = raw.get("effective_from")
    if effective_from is not None:
        if isinstance(effective_from, bool) or not isinstance(effective_from, (int, float)):
            raise ValueError("Model price effective time must be a Unix timestamp.")
        effective_from = float(effective_from)
        if not math.isfinite(effective_from) or effective_from < 0:
            raise ValueError("Model price effective time must be a Unix timestamp.")
    actions = sum(("prices" in raw, raw.get("use_builtin") is True,
                   raw.get("inactive") is True))
    if actions != 1:
        raise ValueError("Model price period must contain exactly one action.")
    revision = {"effective_from": effective_from}
    if "prices" in raw:
        revision["prices"] = normalize_model_price(raw["prices"])
    elif raw.get("use_builtin") is True:
        if not builtin:
            raise ValueError("Only bundled models can resume built-in pricing.")
        revision["use_builtin"] = True
    else:
        if builtin:
            raise ValueError("Bundled models cannot be retired.")
        revision["inactive"] = True
    return revision


def _normalize_model_price_history(raw, builtin):
    """Normalize a legacy timeless price or a bounded revision timeline."""
    if isinstance(raw, dict) and all(field in raw for field in MODEL_PRICE_FIELDS):
        return [{"effective_from": None, "prices": normalize_model_price(raw)}]
    if not isinstance(raw, list) or len(raw) > MAX_MODEL_PRICE_PERIODS:
        raise ValueError("Model price history is invalid or too long.")
    by_effective = {}
    for item in raw:
        revision = _normalize_model_price_revision(item, builtin)
        by_effective[revision["effective_from"]] = revision
    return sorted(
        by_effective.values(),
        key=lambda revision: (-1 if revision["effective_from"] is None
                              else revision["effective_from"]),
    )


def _load_model_price_histories(path=None):
    """Load validated price timelines with an mtime cache for hot cost paths."""
    path = path or TOKEN_METER_SETTINGS
    checked_at = time.monotonic()
    if (
        _model_pricing_cache["path"] == path
        and _model_pricing_cache.get("histories") is not None
        and checked_at - _model_pricing_cache.get("mtime_checked_at", 0.0)
        < MODEL_PRICING_MTIME_TTL_S
    ):
        return _model_pricing_cache["histories"]
    mtime_ns = _model_pricing_mtime_ns(path)
    if (_model_pricing_cache["path"] == path
            and _model_pricing_cache["mtime_ns"] == mtime_ns):
        _model_pricing_cache["mtime_checked_at"] = checked_at
        return _model_pricing_cache["histories"]

    settings = load_json(path, {})
    raw = settings.get("model_pricing") if isinstance(settings, dict) else {}
    histories = {provider: {} for provider in MODEL_PRICE_PROVIDERS}
    custom_models = set()
    if isinstance(raw, dict):
        for raw_provider, rows in raw.items():
            try:
                provider = normalize_model_price_provider(raw_provider)
            except ValueError:
                continue
            if not isinstance(rows, dict):
                continue
            for raw_model, prices in rows.items():
                try:
                    model = normalize_model_price_id(raw_model)
                    builtin = model in builtin_model_price_tables()[provider]
                    periods = _normalize_model_price_history(prices, builtin)
                    if periods:
                        custom_key = (provider, model)
                        if (not builtin and custom_key not in custom_models
                                and len(custom_models) >= MAX_CUSTOM_MODEL_PRICES):
                            continue
                        histories[provider][model] = periods
                        if not builtin:
                            custom_models.add(custom_key)
                except ValueError:
                    continue
    _model_pricing_cache.update({
        "path": path,
        "mtime_ns": mtime_ns,
        "mtime_checked_at": checked_at,
        "histories": histories,
        "effective": {},
        "quotes": {},
    })
    return histories


def _model_price_revision_at(periods, at=None):
    return _catalog_revision_at(
        periods, _price_timestamp(at), now=time.time()
    )


def _same_model_price_action(left, right):
    if not left or not right:
        return False
    for key in ("prices", "use_builtin", "inactive"):
        if key in left or key in right:
            return left.get(key) == right.get(key)
    return False


def _last_model_price(periods):
    for revision in reversed(periods or ()):
        if "prices" in revision:
            return revision["prices"]
    return None


def effective_model_price_table(provider, path=None, at=None):
    provider = normalize_model_price_provider(provider)
    histories = _load_model_price_histories(path)
    cache_key = (provider, _model_price_period_key(provider, at, histories))
    cached = _model_pricing_cache["effective"].get(cache_key)
    if cached is not None:
        return cached
    table = _catalog_effective_price_table(
        canonical_model_provider(provider),
        _price_timestamp(at),
        histories[provider],
        now=time.time(),
    )
    _model_pricing_cache["effective"][cache_key] = table
    return table


def model_pricing_settings(path=None):
    """Return current prices and sanitized timeline metadata for Settings."""
    path = path or TOKEN_METER_SETTINGS
    histories = _load_model_price_histories(path)
    rows = []
    labels = {"claude": "Claude", "codex": "Codex / OpenAI", "cursor": "Cursor",
               "opencode": "OpenCode"}
    for provider in MODEL_PRICE_PROVIDERS:
        builtins = builtin_model_price_tables()[provider]
        effective = effective_model_price_table(provider, path)
        for model in sorted(set(builtins) | set(histories[provider])):
            builtin = model in builtins
            periods = histories[provider].get(model) or []
            revision = _model_price_revision_at(periods)
            overridden = bool(revision and "prices" in revision and builtin)
            active = builtin or bool(revision and "prices" in revision)
            prices = effective.get(model) or _last_model_price(periods) or ZERO_PRICE
            if not builtin and not active:
                source = "retired"
            elif builtin and not overridden:
                source = "built-in"
            else:
                source = "override" if builtin else "custom"
            rows.append({
                "provider": provider,
                "provider_label": labels[provider],
                "model": model,
                "prices": dict(prices),
                "builtin": builtin,
                "overridden": overridden,
                "custom": not builtin,
                "active": active,
                "source": source,
                "effective_from": revision.get("effective_from") if revision else None,
                "periods": len(periods),
            })
    return {
        "models": rows,
        "currency": "USD",
        "unit": "per 1M tokens",
        "reviewed_on": BUILTIN_PRICE_REVIEWED_ON,
        "sources": [dict(source) for source in BUILTIN_PRICE_SOURCES],
        "overrides": sum(
            1 for row in rows if row["builtin"] and row["overridden"]
        ),
        "custom_models": sum(1 for row in rows if row["custom"] and row["active"]),
        "retired_custom_models": sum(
            1 for row in rows if row["custom"] and not row["active"]
        ),
        "max_custom_models": MAX_CUSTOM_MODEL_PRICES,
    }


def _normalize_model_price_scope(apply_to_all_history, effective_from, now):
    if not isinstance(apply_to_all_history, bool):
        raise ValueError("Apply to all history must be true or false.")
    if effective_from is not None:
        if isinstance(effective_from, bool) or not isinstance(effective_from, (int, float)):
            raise ValueError("Model price effective time must be a Unix timestamp.")
        effective_from = float(effective_from)
        if not math.isfinite(effective_from) or effective_from < 0:
            raise ValueError("Model price effective time must be a Unix timestamp.")
        if effective_from > now + 300:
            raise ValueError("Model price effective time cannot be in the future.")
    if apply_to_all_history and effective_from is not None:
        raise ValueError("Choose either an effective time or all history, not both.")
    return (
        None if apply_to_all_history else (
            effective_from if effective_from is not None else now
        ),
        "all_history" if apply_to_all_history else (
            "selected_time" if effective_from is not None else "now"
        ),
    )


def _normalize_model_price_change(change):
    if not isinstance(change, dict):
        raise ValueError("Each model price change must be an object.")
    provider = normalize_model_price_provider(change.get("provider"))
    model = normalize_model_price_id(change.get("model"))
    remove = change.get("remove", False)
    if not isinstance(remove, bool):
        raise ValueError("Remove must be true or false.")
    if remove and "prices" in change:
        raise ValueError("A model price change cannot set and remove prices together.")
    prices = None if remove else normalize_model_price(change.get("prices"))
    return {
        "provider": provider,
        "model": model,
        "prices": prices,
        "remove": remove,
    }


def _apply_model_price_change(
        histories, change, apply_to_all_history, applied_from):
    provider = change["provider"]
    model = change["model"]
    normalized = change["prices"]
    remove = change["remove"]
    builtin = builtin_model_price_tables()[provider].get(model)
    periods = histories[provider].get(model) or []
    if apply_to_all_history and (remove or (builtin is not None and builtin == normalized)):
        return histories[provider].pop(model, None) is not None
    if apply_to_all_history:
        replacement = [{"effective_from": None, "prices": normalized}]
        changed = periods != replacement
        histories[provider][model] = replacement
        return changed

    if remove:
        action = ({"use_builtin": True} if builtin is not None else {"inactive": True})
    elif builtin is not None and builtin == normalized:
        action = {"use_builtin": True}
    else:
        action = {"prices": normalized}
    current = _model_price_revision_at(periods, applied_from)
    if current is None:
        current = ({"use_builtin": True} if builtin is not None else {"inactive": True})
    if _same_model_price_action(current, action):
        return False
    replacement = {"effective_from": applied_from, **action}
    by_effective = {item["effective_from"]: item for item in periods}
    by_effective[applied_from] = replacement
    updated = sorted(
        by_effective.values(),
        key=lambda revision: (-1 if revision["effective_from"] is None
                              else revision["effective_from"]),
    )
    if len(updated) > MAX_MODEL_PRICE_PERIODS:
        raise ValueError(
            f"Use at most {MAX_MODEL_PRICE_PERIODS} saved periods per model."
        )
    histories[provider][model] = updated
    return True


def set_model_prices(changes, path=None, apply_to_all_history=False,
                     effective_from=None):
    """Validate and persist a bounded set of model price changes atomically."""
    path = path or TOKEN_METER_SETTINGS
    try:
        if (not isinstance(changes, list) or not changes
                or len(changes) > MAX_MODEL_PRICE_CHANGES):
            raise ValueError(
                f"Provide 1–{MAX_MODEL_PRICE_CHANGES} model price changes."
            )
        applied_from, effective_scope = _normalize_model_price_scope(
            apply_to_all_history, effective_from, time.time()
        )
        normalized_changes = [_normalize_model_price_change(change) for change in changes]
        keys = [(change["provider"], change["model"]) for change in normalized_changes]
        if len(keys) != len(set(keys)):
            raise ValueError("A model price batch cannot contain duplicate models.")
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    histories = copy.deepcopy(_load_model_price_histories(path))
    results = []
    try:
        for change in normalized_changes:
            changed = _apply_model_price_change(
                histories, change, apply_to_all_history, applied_from,
            )
            results.append({
                "provider": change["provider"],
                "model": change["model"],
                "removed": change["remove"],
                "changed": changed,
            })
        custom_count = sum(
            1
            for item_provider, rows in histories.items()
            for item_model in rows
            if item_model not in builtin_model_price_tables()[item_provider]
        )
        if custom_count > MAX_CUSTOM_MODEL_PRICES:
            raise ValueError(
                "Remove an archived custom model before adding another; "
                f"the limit is {MAX_CUSTOM_MODEL_PRICES}."
            )
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    stored = {
        item_provider: {
            item_model: rows[item_model]
            for item_model in sorted(rows)
        }
        for item_provider, rows in histories.items()
        if rows
    }
    if stored:
        settings["model_pricing"] = stored
    else:
        settings.pop("model_pricing", None)
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        return {"ok": False, "error": "Token Meter could not save settings."}

    _model_pricing_cache.update({
        "path": None, "mtime_ns": None, "mtime_checked_at": 0.0,
        "histories": {}, "effective": {}, "quotes": {},
    })
    return {
        "ok": True,
        "changed": any(result["changed"] for result in results),
        "changes": results,
        "apply_to_all_history": apply_to_all_history,
        "effective_from": applied_from,
        "effective_scope": effective_scope,
        "model_pricing": model_pricing_settings(path),
    }


def set_model_price(provider, model, prices=None, remove=False, path=None,
                    apply_to_all_history=False, effective_from=None):
    """Compatibility wrapper for one model price change."""
    change = {
        "provider": provider,
        "model": model,
        "remove": remove,
    }
    if not remove:
        change["prices"] = prices
    result = set_model_prices([change], path=path, apply_to_all_history=apply_to_all_history,
        effective_from=effective_from)
    if not result.get("ok"):
        return result
    change = result["changes"][0]
    result.update({
        "provider": change["provider"],
        "model": change["model"],
        "removed": change["removed"],
    })
    return result


def _opaque_session_identity_key(value, namespace):
    value = str(value or "").strip()
    if not value or len(value) > 512:
        return ""
    return hashlib.sha256(
        (str(namespace) + "\0" + value).encode("utf-8")
    ).hexdigest()


def session_model_identity_key(source):
    """Return an opaque, physical-trace-scoped key without retaining its ID."""
    if not isinstance(source, dict):
        return ""
    if source.get("provider") == "codex":
        return _opaque_session_identity_key(
            source.get("physical_trace_id"), "token-meter-codex-session-v1",
        )
    if source.get("provider") == "hermes":
        return _opaque_session_identity_key(
            source.get("id"), "token-meter-hermes-session-v1",
        )
    return ""


def _session_model_identity_parent_key(parent_id):
    return _opaque_session_identity_key(
        parent_id, "token-meter-codex-parent-v1",
    )


def _session_model_identity_revision(source):
    signature = file_signature(str((source or {}).get("path") or ""))
    if not signature:
        return None
    return tuple(str(value) for value in signature)


def _normalize_session_model_identity_revision(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    normalized = tuple(str(item) for item in value)
    if any(not re.fullmatch(r"[0-9]{1,24}", item) for item in normalized):
        return None
    return normalized


def _normalize_session_model_identity_model(provider, model):
    raw_provider = str(provider or "").strip().lower()
    if raw_provider in ("hermes", "anthropic", "claude"):
        model = normalize_model_price_id(model)
        if (
            model == "unknown-model"
            or not SESSION_CLAUDE_MODEL_ID_RE.fullmatch(model)
        ):
            raise ValueError(
                "Choose an exact public Claude model ID, such as claude-sonnet-5."
            )
        return "anthropic", model
    provider = normalize_model_price_provider(raw_provider)
    if provider != "codex":
        raise ValueError("Codex session assignments require Codex / OpenAI models.")
    model = normalize_model_price_id(model)
    if model in ("unknown-model", AUTO_REVIEW_MODEL):
        raise ValueError("Choose an actual Codex / OpenAI model ID.")
    return "openai", model


def _normalize_session_model_identity_verified(value):
    if not isinstance(value, dict):
        return None
    try:
        provider, model = _normalize_session_model_identity_model(
            value.get("model_provider"), value.get("model"),
        )
    except ValueError:
        return None
    if provider != "openai":
        return None
    revision = _normalize_session_model_identity_revision(value.get("revision"))
    parent_key = str(value.get("parent_key") or "")
    inherited_prefix = value.get("inherited_prefix")
    if (
        not revision
        or not SESSION_MODEL_IDENTITY_KEY_RE.fullmatch(parent_key)
        or isinstance(inherited_prefix, bool)
        or not isinstance(inherited_prefix, int)
        or inherited_prefix < 0
        or inherited_prefix > 1_000_000
    ):
        return None
    return {
        "model_provider": provider,
        "model": model,
        "revision": list(revision),
        "parent_key": parent_key,
        "inherited_prefix": inherited_prefix,
    }


def _normalize_session_model_identity_user(value):
    if not isinstance(value, dict):
        return None
    try:
        provider, model = _normalize_session_model_identity_model(
            value.get("model_provider"), value.get("model"),
        )
    except ValueError:
        return None
    return {"model_provider": provider, "model": model}


def _load_session_model_identity_store(path=None):
    path = path or TOKEN_METER_SESSION_MODEL_IDENTITIES
    raw = load_json(path, {})
    rows = raw.get("sessions") if isinstance(raw, dict) else {}
    sessions = {}
    if isinstance(rows, dict):
        for key, value in list(rows.items())[:MAX_SESSION_MODEL_IDENTITIES]:
            key = str(key or "")
            if not SESSION_MODEL_IDENTITY_KEY_RE.fullmatch(key) or not isinstance(value, dict):
                continue
            entry = {}
            verified = _normalize_session_model_identity_verified(value.get("verified"))
            user = _normalize_session_model_identity_user(value.get("user"))
            if verified:
                entry["verified"] = verified
            if user:
                entry["user"] = user
            if entry:
                sessions[key] = entry
    return {"version": 1, "sessions": sessions}


def _write_session_model_identity_store(store, path=None):
    path = path or TOKEN_METER_SESSION_MODEL_IDENTITIES
    sessions = (store or {}).get("sessions") or {}
    serialized = {
        "version": 1,
        "sessions": {key: sessions[key] for key in sorted(sessions)},
    }
    atomic_write_text(path, json.dumps(serialized, indent=2, ensure_ascii=False) + "\n")


def codex_auto_review_evidence(source):
    """Ask the Codex adapter for non-content evidence from a live direct parent."""
    if (
        not isinstance(source, dict)
        or source.get("provider") != "codex"
        or source.get("observed_model") != AUTO_REVIEW_MODEL
    ):
        return None
    try:
        evidence = _codex_native_adapter().auto_review_evidence(source)
    except (OSError, TypeError, ValueError):
        return None
    return evidence if isinstance(evidence, dict) else None


def _verified_session_model_identity(source, evidence):
    if not isinstance(evidence, dict):
        return None
    try:
        provider, model = _normalize_session_model_identity_model(
            evidence.get("model_provider"), evidence.get("model"),
        )
    except ValueError:
        return None
    revision = _normalize_session_model_identity_revision(evidence.get("revision"))
    source_revision = _session_model_identity_revision(source)
    parent_key = _session_model_identity_parent_key(evidence.get("parent_id"))
    inherited_prefix = evidence.get("inherited_prefix")
    if (
        source.get("observed_model") != AUTO_REVIEW_MODEL
        or str(source.get("model") or "") != model
        or not revision
        or revision != source_revision
        or not parent_key
        or isinstance(inherited_prefix, bool)
        or not isinstance(inherited_prefix, int)
        or inherited_prefix < 0
        or inherited_prefix > 1_000_000
    ):
        return None
    return {
        "model_provider": provider,
        "model": model,
        "revision": list(revision),
        "parent_key": parent_key,
        "inherited_prefix": inherited_prefix,
    }


def _verified_identity_matches_source(verified, source):
    if not verified:
        return False
    return (
        tuple(verified.get("revision") or ())
        == _session_model_identity_revision(source)
    )


def _safe_hermes_model_identity_hint(value):
    if not isinstance(value, dict):
        value = {}
    label = (
        HERMES_MODEL_IDENTITY_LABEL
        if value.get("label") == HERMES_MODEL_IDENTITY_LABEL else ""
    )
    candidates = []
    for item in value.get("candidates") or ():
        candidate = str(item or "").strip().lower()
        if not SESSION_CLAUDE_MODEL_ID_RE.fullmatch(candidate):
            continue
        try:
            prices, missing = price_for(candidate, "claude")
        except (TypeError, ValueError, OverflowError):
            continue
        if not missing and any(float(value or 0) > 0 for value in prices.values()) \
                and candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) >= 8:
            break
    return label, candidates


def _public_session_model_identity(key, source, assignable=False, *,
                                   kind="auto_review", runtime="codex",
                                   hint=None, assigned_model=None):
    result = {
        "key": key,
        "kind": kind,
        "source": source,
        "assignable": bool(assignable),
    }
    if kind == "missing_model" and runtime == "hermes":
        result["runtime"] = "hermes"
        label, candidates = _safe_hermes_model_identity_hint(hint)
        result["assignable"] = bool(assignable and label and candidates)
        if label:
            result["observed_label"] = label
        result["candidates"] = candidates
        if assigned_model and SESSION_CLAUDE_MODEL_ID_RE.fullmatch(
            str(assigned_model)
        ):
            result["assigned_model"] = str(assigned_model).lower()
    if source == "unresolved":
        result["reason"] = "underlying_model_unavailable"
    return result


def public_session_model_identity(value):
    if not isinstance(value, dict):
        return None
    key = str(value.get("key") or "")
    source = str(value.get("source") or "")
    kind = str(value.get("kind") or "")
    allowed_sources = (
        {"verified_parent", "verified_history", "user_assigned", "unresolved"}
        if kind == "auto_review" else
        {"user_assigned", "unresolved"} if kind == "missing_model" else set()
    )
    if not SESSION_MODEL_IDENTITY_KEY_RE.fullmatch(key) or source not in allowed_sources:
        return None
    result = {
        "key": key,
        "kind": kind,
        "source": source,
        "assignable": bool(value.get("assignable")),
    }
    if kind == "missing_model":
        if value.get("runtime") != "hermes":
            return None
        result["runtime"] = "hermes"
        label, candidates = _safe_hermes_model_identity_hint({
            "label": value.get("observed_label"),
            "candidates": value.get("candidates"),
        })
        if label:
            result["observed_label"] = label
        result["candidates"] = candidates
        result["assignable"] = bool(
            result["assignable"] and label and candidates
        )
        assigned_model = str(value.get("assigned_model") or "").strip().lower()
        if source == "user_assigned" and SESSION_CLAUDE_MODEL_ID_RE.fullmatch(
            assigned_model
        ):
            result["assigned_model"] = assigned_model
    if source == "unresolved":
        result["reason"] = "underlying_model_unavailable"
    return result


def _apply_verified_session_identity(source, verified):
    source["model"] = verified["model"]
    source["model_provider"] = verified["model_provider"]
    source["_verified_inherited_prefix"] = verified["inherited_prefix"]
    source["_verified_inherited_prefix_revision"] = list(verified["revision"])


def enrich_codex_model_identities(sources, path=None):
    """Apply revision-bound Codex identity evidence without changing price settings."""
    source_rows = [dict(source) for source in (sources or ()) if isinstance(source, dict)]
    with _session_model_identity_lock:
        store = _load_session_model_identity_store(path)
        changed = False
        for source in source_rows:
            if (
                source.get("provider") != "codex"
                or source.get("observed_model") != AUTO_REVIEW_MODEL
            ):
                continue
            key = session_model_identity_key(source)
            if not key:
                source["model"] = "unknown-model"
                continue
            entry = dict(store["sessions"].get(key) or {})
            verified = _verified_session_model_identity(
                source, codex_auto_review_evidence(source),
            )
            if verified:
                if entry.get("verified") != verified:
                    entry["verified"] = verified
                    store["sessions"][key] = entry
                    changed = True
                _apply_verified_session_identity(source, verified)
                source["model_identity"] = _public_session_model_identity(
                    key, "verified_parent",
                )
                continue
            user = _normalize_session_model_identity_user(entry.get("user"))
            if user and user.get("model_provider") != "openai":
                user = None
            historical = _normalize_session_model_identity_verified(entry.get("verified"))
            historical_matches = (
                source.get("model_resolution_reason") != "cyclic"
                and _verified_identity_matches_source(historical, source)
            )
            if user:
                source["model"] = user["model"]
                source["model_provider"] = user["model_provider"]
                if historical_matches:
                    _apply_verified_session_identity(source, historical)
                    source["model"] = user["model"]
                    source["model_provider"] = user["model_provider"]
                source["model_identity"] = _public_session_model_identity(
                    key, "user_assigned", assignable=True,
                )
            elif historical_matches:
                _apply_verified_session_identity(source, historical)
                source["model_identity"] = _public_session_model_identity(
                    key, "verified_history",
                )
            else:
                source["model"] = "unknown-model"
                source["model_identity"] = _public_session_model_identity(
                    key, "unresolved", assignable=True,
                )
        if changed:
            try:
                _write_session_model_identity_store(store, path)
            except OSError:
                pass
    return source_rows


def enrich_hermes_model_identities(sources, path=None):
    """Apply one content-free user assignment to unresolved Hermes sessions."""
    source_rows = [dict(source) for source in (sources or ()) if isinstance(source, dict)]
    with _session_model_identity_lock:
        store = _load_session_model_identity_store(path)
        for source in source_rows:
            if (
                source.get("provider") != "hermes"
                or source.get("model") != "unknown-model"
            ):
                continue
            key = session_model_identity_key(source)
            if not key:
                continue
            hint = source.get("model_identity_hint")
            label, candidates = _safe_hermes_model_identity_hint(hint)
            safe_hint = {"label": label, "candidates": candidates}
            assignable = bool(label and candidates)
            user = _normalize_session_model_identity_user(
                (store["sessions"].get(key) or {}).get("user")
            )
            if (
                assignable
                and user
                and user.get("model_provider") == "anthropic"
                and user.get("model") in candidates
            ):
                source["model"] = user["model"]
                source["model_provider"] = user["model_provider"]
                source["model_identity"] = _public_session_model_identity(
                    key, "user_assigned", assignable=True,
                    kind="missing_model", runtime="hermes", hint=safe_hint,
                    assigned_model=user["model"],
                )
            else:
                source["model"] = "unknown-model"
                source["model_identity"] = _public_session_model_identity(
                    key, "unresolved", assignable=assignable,
                    kind="missing_model", runtime="hermes", hint=safe_hint,
                )
    return source_rows


def enrich_session_model_identities(sources, path=None):
    """Apply runtime-scoped saved model identity without changing pricing."""
    return enrich_hermes_model_identities(
        enrich_codex_model_identities(sources, path=path), path=path,
    )


def set_session_model_identity(session_key, model=None, provider="codex", remove=False,
                               sources=None, path=None):
    """Save or remove one explicit fallback model without touching model pricing."""
    key = str(session_key or "")
    if not SESSION_MODEL_IDENTITY_KEY_RE.fullmatch(key):
        return {"ok": False, "error": "A valid saved-session identity is required."}
    source_rows = enrich_session_model_identities(
        sources if sources is not None else all_session_sources(), path=path,
    )
    matches = [
        source for source in source_rows
        if (source.get("model_identity") or {}).get("key") == key
    ]
    if len(matches) != 1:
        return {
            "ok": False,
            "error": "This saved session is no longer uniquely available.",
        }
    source = matches[0]
    identity = source.get("model_identity") or {}
    with _session_model_identity_lock:
        store = _load_session_model_identity_store(path)
        entry = dict(store["sessions"].get(key) or {})
        if remove:
            changed = "user" in entry
            entry.pop("user", None)
            if entry:
                store["sessions"][key] = entry
            else:
                store["sessions"].pop(key, None)
        else:
            if not identity.get("assignable"):
                return {
                    "ok": False,
                    "error": "This session already has verified model evidence.",
                }
            expected_provider = (
                "hermes" if identity.get("kind") == "missing_model" else "codex"
            )
            raw_provider = str(provider or "").strip().lower()
            allowed_providers = (
                {"hermes", "anthropic", "claude"}
                if expected_provider == "hermes" else {"codex", "openai"}
            )
            if raw_provider not in allowed_providers:
                return {
                    "ok": False,
                    "error": "The selected model provider does not match this session.",
                }
            try:
                model_provider, model_id = _normalize_session_model_identity_model(
                    expected_provider, model,
                )
            except ValueError as error:
                return {"ok": False, "error": str(error)}
            if (
                expected_provider == "hermes"
                and model_id not in set(identity.get("candidates") or ())
            ):
                return {
                    "ok": False,
                    "error": "Select a public model observed for this Hermes session.",
                }
            user = {"model_provider": model_provider, "model": model_id}
            changed = entry.get("user") != user
            entry["user"] = user
            store["sessions"][key] = entry
        try:
            if changed:
                _write_session_model_identity_store(store, path)
        except OSError:
            return {"ok": False, "error": "Token Meter could not save this session model."}
    return {
        "ok": True,
        "changed": changed,
        "removed": bool(remove),
        "session_key": key,
    }


def normalize_budget_settings(values):
    """Validate one machine-wide monthly budget configuration."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("Budget settings must be an object.")
    currency = str(values.get("currency") or "USD").strip().upper()
    if currency != "USD":
        raise ValueError("Token Meter budgets currently support USD only.")

    default_session_budget = values.get(
        "default_session_budget", DEFAULT_SESSION_BUDGET,
    )
    if (isinstance(default_session_budget, bool)
            or not isinstance(default_session_budget, (int, float))):
        raise ValueError("Default session budget must be a number.")
    default_session_budget = float(default_session_budget)
    if (not math.isfinite(default_session_budget)
            or default_session_budget < MIN_SESSION_BUDGET
            or default_session_budget > MAX_MONTHLY_BUDGET):
        raise ValueError(
            "Default session budget must be between "
            f"{MIN_SESSION_BUDGET:g} and {MAX_MONTHLY_BUDGET:,.0f}."
        )

    raw_allocations = values.get("allocations") or {}
    if not isinstance(raw_allocations, dict):
        raise ValueError("Runtime allocations must be an object.")
    unknown = sorted(set(raw_allocations) - set(BUDGET_PROVIDERS))
    if unknown:
        raise ValueError("Runtime allocations must use registered runtime IDs.")
    allocations = {}
    for provider in BUDGET_PROVIDERS:
        value = raw_allocations.get(provider, DEFAULT_RUNTIME_BUDGET)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{provider.title()} allocation must be a number.")
        value = float(value)
        if not math.isfinite(value) or value < 0 or value > MAX_MONTHLY_BUDGET:
            raise ValueError(
                f"{provider.title()} allocation must be between 0 and "
                f"{MAX_MONTHLY_BUDGET:,.0f}."
            )
        allocations[provider] = value
    derived_total = sum(allocations.values())
    if derived_total > MAX_MONTHLY_BUDGET:
        raise ValueError(
            f"Combined runtime budget must not exceed {MAX_MONTHLY_BUDGET:,.0f}."
        )
    total = derived_total

    raw_thresholds = values.get("thresholds", DEFAULT_BUDGET_THRESHOLDS)
    if not isinstance(raw_thresholds, list) and not isinstance(raw_thresholds, tuple):
        raise ValueError("Budget thresholds must be a list.")
    thresholds = []
    for raw in raw_thresholds:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("Budget thresholds must be whole percentages.")
        value = int(raw)
        if value != raw or value < 1 or value > 100:
            raise ValueError("Budget thresholds must be whole percentages from 1 to 100.")
        thresholds.append(value)
    if not thresholds or len(thresholds) > 10 or thresholds != sorted(set(thresholds)):
        raise ValueError("Use 1 to 10 unique budget thresholds in increasing order.")

    native_notifications = values.get("native_notifications", True)
    if not isinstance(native_notifications, bool):
        raise ValueError("Budget notifications must be on or off.")
    return {
        "currency": "USD",
        "default_session_budget": default_session_budget,
        "monthly_total": total,
        "allocations": allocations,
        "thresholds": thresholds,
        "native_notifications": native_notifications,
    }


def budget_settings(path=None):
    """Load the durable monthly budget with defaults for missing runtimes."""
    path = path or TOKEN_METER_SETTINGS
    settings = load_json(path, {})
    raw = settings.get("budgets") if isinstance(settings, dict) else {}
    try:
        return normalize_budget_settings(raw)
    except ValueError:
        return normalize_budget_settings({})


def set_budget_settings(values, path=None):
    """Persist a validated machine-wide monthly budget atomically."""
    path = path or TOKEN_METER_SETTINGS
    try:
        normalized = normalize_budget_settings(values)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    changed = settings.get("budgets") != normalized
    settings["budgets"] = normalized
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return {"ok": False, "error": f"Token Meter could not save settings: {error}"}
    return {"ok": True, "changed": changed, "budgets": normalized}


def normalize_update_settings(values):
    """Validate the machine-wide software-update preferences."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("Update settings must be an object.")
    enabled = values.get("enabled", True)
    auto_install = values.get("auto_install", True)
    if not isinstance(enabled, bool) or not isinstance(auto_install, bool):
        raise ValueError("Update preferences must be on or off.")
    if not enabled:
        auto_install = False
    return {
        "enabled": enabled,
        "auto_install": auto_install,
        "interval_seconds": UPDATE_CHECK_INTERVAL_S,
    }


def update_settings(path=None):
    """Load the default-on update-check and installation preferences."""
    path = path or TOKEN_METER_SETTINGS
    settings = load_json(path, {})
    raw = settings.get("updates") if isinstance(settings, dict) else {}
    try:
        return normalize_update_settings(raw)
    except ValueError:
        return normalize_update_settings({})


def set_update_settings(values, path=None):
    """Persist update preferences without changing the checkout."""
    path = path or TOKEN_METER_SETTINGS
    try:
        normalized = normalize_update_settings(values)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    stored = {
        "enabled": normalized["enabled"],
        "auto_install": normalized["auto_install"],
    }
    changed = settings.get("updates") != stored
    settings["updates"] = stored
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return {"ok": False, "error": f"Token Meter could not save settings: {error}"}
    _update_wake.set()
    return {"ok": True, "changed": changed, "updates": normalized}


def _safe_update_revision(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else ""


def _safe_update_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def source_checkout_path():
    """Find the installer checkout without returning it through the HTTP API."""
    runtime_root = _SOURCE_ROOT
    candidates = []
    explicit = os.environ.get("TOKEN_METER_SOURCE_CHECKOUT")
    if explicit:
        candidates.append(explicit)
    marker = os.path.join(runtime_root, "SOURCE_CHECKOUT")
    try:
        with open(marker, encoding="utf-8") as fh:
            candidates.append(fh.read(4096).strip())
    except OSError:
        pass
    candidates.append(runtime_root)
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = os.path.realpath(os.path.abspath(os.path.expanduser(candidate)))
        if candidate in seen:
            continue
        seen.add(candidate)
        if (os.path.exists(os.path.join(candidate, ".git"))
                and os.path.isfile(os.path.join(candidate, "scripts", "install"))):
            return candidate
    return ""


def _update_status_record(path=None):
    path = path or TOKEN_METER_UPDATE_STATUS
    raw = load_json(path, {})
    return raw if isinstance(raw, dict) else {}


def _persist_update_status(values, path=None):
    """Write only the bounded fields used across update checks and restarts."""
    path = path or TOKEN_METER_UPDATE_STATUS
    previous = _update_status_record(path)
    allowed = {
        "phase", "error_code", "current_revision", "latest_revision",
        "previous_revision", "checked_at", "started_at", "installed_at",
        "failed_revision", "available", "can_update", "dirty", "ahead", "behind",
    }
    record = {
        key: values[key]
        for key in allowed
        if key in values
    }
    for key in ("installed_at", "previous_revision"):
        if key not in record and key in previous:
            record[key] = previous[key]
    try:
        atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def _update_message(state, error_code="", latest_revision=""):
    if state == "disabled":
        return "Automatic update checks are off."
    if state == "checking":
        return "Checking the configured Git upstream."
    if state == "updating":
        return "Pulling and reinstalling Token Meter."
    if state == "updated":
        return "Token Meter was updated successfully."
    if state == "available":
        return (
            f"Version {latest_revision} is ready to install."
            if latest_revision else "A new version is ready to install."
        )
    if state == "current":
        return "This installation matches its configured upstream."
    if state == "waiting":
        return "Waiting for the first update check."
    messages = {
        "source_unavailable": "The installed source checkout is unavailable.",
        "git_unavailable": "Git is unavailable, so Token Meter cannot check for updates.",
        "upstream_unavailable": "The source checkout has no usable tracking upstream.",
        "unsupported_update_branch": "Automatic updates require a main checkout tracking a remote main branch.",
        "fetch_failed": "Token Meter could not fetch the configured Git upstream.",
        "inspect_failed": "Token Meter could not compare the installed and upstream revisions.",
        "dirty_checkout": "An update exists, but the source checkout has local changes.",
        "diverged_checkout": "An update exists, but the source checkout has diverged.",
        "launch_failed": "Token Meter could not start the updater.",
        "linux_helper_unavailable": (
            "The installed Linux update helper is unavailable. "
            "Reinstall Token Meter to repair automatic updates."
        ),
        "install_failed": "The update did not install. The existing checkout needs attention.",
    }
    return messages.get(error_code, "Update status is unavailable.")


def software_update_status(settings_path=None, status_path=None):
    """Return a bounded, path-free update snapshot for the local dashboard."""
    settings = update_settings(settings_path)
    raw = _update_status_record(status_path)
    enabled = settings["enabled"]
    phase = str(raw.get("phase") or "")
    error_code = str(raw.get("error_code") or "")
    current_revision = _safe_update_revision(raw.get("current_revision"))
    latest_revision = _safe_update_revision(raw.get("latest_revision"))
    active_phases = {"starting", "fetching", "installing"}
    if not enabled:
        state = "disabled"
    elif phase in active_phases:
        state = "updating"
    elif phase == "checking":
        state = "checking"
    elif phase == "complete":
        state = "updated"
    elif phase == "available" and raw.get("can_update") is True:
        state = "available"
    elif phase == "current":
        state = "current"
    elif phase in {"available", "error", "failed"}:
        state = "attention"
    else:
        state = "waiting"
    checked_at = _safe_update_int(raw.get("checked_at"))
    return {
        "enabled": enabled,
        "auto_install": settings["auto_install"],
        "interval_seconds": UPDATE_CHECK_INTERVAL_S,
        "state": state,
        "checking": state == "checking",
        "updating": state == "updating",
        "available": bool(enabled and raw.get("available")),
        "can_update": bool(enabled and raw.get("can_update")),
        "dirty": bool(raw.get("dirty")),
        "ahead": _safe_update_int(raw.get("ahead")),
        "behind": _safe_update_int(raw.get("behind")),
        "current_revision": current_revision,
        "latest_revision": latest_revision,
        "checked_at": checked_at,
        "next_check_at": checked_at + UPDATE_CHECK_INTERVAL_S if enabled and checked_at else 0,
        "installed_at": _safe_update_int(raw.get("installed_at")),
        "message": _update_message(state, error_code, latest_revision),
        "actions": {"token": _ACTION_TOKEN, "check": True, "install": True},
    }


def _platform_subprocess_kwargs(purpose=ProcessPurpose.DEFAULT):
    options = _PLATFORM_SERVICES.process_options(purpose)
    if not options.supported:
        return {}
    kwargs = {}
    if options.close_fds:
        kwargs["close_fds"] = True
    if options.start_new_session:
        kwargs["start_new_session"] = True
    if options.creation_flags:
        kwargs["creationflags"] = options.creation_flags
    return kwargs


def _run_update_git(checkout, args, runner=None, timeout=None):
    runner = runner or subprocess.run
    result = runner(
        ["git", "-C", checkout] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout or UPDATE_FETCH_TIMEOUT_S,
        **_platform_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError("git command failed")
    return (result.stdout or "").strip()


def check_for_software_update(
        checkout=None, runner=None, now=None, settings_path=None, status_path=None):
    """Fetch and compare the tracking upstream without changing the worktree."""
    now = int(now or time.time())
    if not update_settings(settings_path)["enabled"]:
        return software_update_status(settings_path, status_path)
    if not _update_operation_lock.acquire(blocking=False):
        return software_update_status(settings_path, status_path)
    try:
        previous = _update_status_record(status_path)
        _persist_update_status({
            "phase": "checking",
            "checked_at": _safe_update_int(previous.get("checked_at")),
            "current_revision": previous.get("current_revision", ""),
            "latest_revision": previous.get("latest_revision", ""),
        }, status_path)
        checkout = checkout or source_checkout_path()
        if not checkout:
            _persist_update_status({
                "phase": "error", "error_code": "source_unavailable", "checked_at": now,
            }, status_path)
            return software_update_status(settings_path, status_path)
        if not shutil.which("git"):
            _persist_update_status({
                "phase": "error", "error_code": "git_unavailable", "checked_at": now,
            }, status_path)
            return software_update_status(settings_path, status_path)
        try:
            upstream = _run_update_git(
                checkout, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                runner,
            )
            if not UPDATE_REF_RE.fullmatch(upstream) or "/" not in upstream:
                raise ValueError("invalid upstream")
            branch = _run_update_git(
                checkout, ["rev-parse", "--abbrev-ref", "HEAD"], runner,
            )
            if branch != "main" or upstream.rsplit("/", 1)[-1] != "main":
                _persist_update_status({
                    "phase": "error", "error_code": "unsupported_update_branch",
                    "checked_at": now,
                }, status_path)
                return software_update_status(settings_path, status_path)
            remote = upstream.split("/", 1)[0]
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
            _persist_update_status({
                "phase": "error", "error_code": "upstream_unavailable", "checked_at": now,
            }, status_path)
            return software_update_status(settings_path, status_path)
        try:
            _run_update_git(
                checkout, ["fetch", "--quiet", "--prune", "--no-tags", remote],
                runner, UPDATE_FETCH_TIMEOUT_S,
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            _persist_update_status({
                "phase": "error", "error_code": "fetch_failed", "checked_at": now,
            }, status_path)
            return software_update_status(settings_path, status_path)
        try:
            current_revision = _safe_update_revision(
                _run_update_git(checkout, ["rev-parse", "HEAD"], runner)
            )
            latest_revision = _safe_update_revision(
                _run_update_git(checkout, ["rev-parse", "@{upstream}"], runner)
            )
            counts = _run_update_git(
                checkout, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                runner,
            ).split()
            if len(counts) != 2:
                raise ValueError("invalid revision counts")
            ahead, behind = (int(counts[0]), int(counts[1]))
            dirty = bool(_run_update_git(checkout, ["status", "--porcelain"], runner))
            if min(ahead, behind) < 0 or not current_revision or not latest_revision:
                raise ValueError("invalid revision state")
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
            _persist_update_status({
                "phase": "error", "error_code": "inspect_failed", "checked_at": now,
            }, status_path)
            return software_update_status(settings_path, status_path)
        available = behind > 0
        can_update = bool(available and ahead == 0 and not dirty)
        error_code = (
            "diverged_checkout" if available and ahead > 0
            else ("dirty_checkout" if available and dirty else "")
        )
        _persist_update_status({
            "phase": "available" if available else "current",
            "error_code": error_code,
            "current_revision": current_revision,
            "latest_revision": latest_revision,
            "checked_at": now,
            "available": available,
            "can_update": can_update,
            "dirty": dirty,
            "ahead": ahead,
            "behind": behind,
        }, status_path)
        return software_update_status(settings_path, status_path)
    finally:
        _update_operation_lock.release()


def trigger_software_update_check(settings_path=None, status_path=None):
    """Start one non-blocking update check for a dashboard or settings action."""
    if not update_settings(settings_path)["enabled"]:
        return {"ok": False, "error": "Enable automatic update checks first."}
    if str(_update_status_record(status_path).get("phase") or "") in {
            "starting", "fetching", "installing"}:
        return {"ok": False, "error": "A Token Meter update is already in progress."}
    if _update_operation_lock.locked():
        return {"ok": True, "status": software_update_status(settings_path, status_path)}
    previous = _update_status_record(status_path)
    _persist_update_status({
        "phase": "checking",
        "checked_at": _safe_update_int(previous.get("checked_at")),
        "current_revision": previous.get("current_revision", ""),
        "latest_revision": previous.get("latest_revision", ""),
    }, status_path)
    threading.Thread(
        target=check_for_software_update,
        kwargs={"settings_path": settings_path, "status_path": status_path},
        daemon=True,
    ).start()
    return {"ok": True, "status": software_update_status(settings_path, status_path)}


def start_software_update(popen=None, settings_path=None, status_path=None):
    """Launch the detached fast-forward-and-reinstall helper."""
    status = software_update_status(settings_path, status_path)
    raw = _update_status_record(status_path)
    if not status["enabled"]:
        return {"ok": False, "error": "Enable automatic update checks first."}
    failed_target = _safe_update_revision(raw.get("failed_revision"))
    retry_failed = bool(
        str(raw.get("phase") or "") == "failed"
        and str(raw.get("error_code") or "") in {"install_failed", "launch_failed"}
        and failed_target
        and failed_target == _safe_update_revision(raw.get("latest_revision"))
    )
    if (not status["available"] or not status["can_update"]) and not retry_failed:
        return {"ok": False, "error": "No safely installable update is available."}
    checkout = source_checkout_path()
    target_status_path = status_path or TOKEN_METER_UPDATE_STATUS
    update_plan = _PLATFORM_SERVICES.update_plan(
        _SOURCE_ROOT, checkout or "", target_status_path,
    )
    helper_ready = bool(
        update_plan.supported
        and update_plan.command
        and os.path.isfile(update_plan.script_path)
        and (
            _PLATFORM_SERVICES.platform_id == "windows"
            or os.access(update_plan.script_path, os.X_OK)
        )
    )
    if not checkout or not helper_ready:
        return {"ok": False, "error": "The installed updater is unavailable."}
    started_at = int(time.time())
    _persist_update_status({
        "phase": "starting",
        "started_at": started_at,
        "checked_at": status["checked_at"],
        "previous_revision": status["current_revision"],
        "current_revision": status["current_revision"],
        "latest_revision": status["latest_revision"],
        "available": True,
        "can_update": False,
        "failed_revision": failed_target if retry_failed else "",
    }, status_path)
    popen = popen or subprocess.Popen
    process_options = _PLATFORM_SERVICES.process_options(ProcessPurpose.DETACHED)
    if not process_options.supported:
        _persist_update_status({
            "phase": "failed", "error_code": "launch_failed",
            "checked_at": status["checked_at"],
            "current_revision": status["current_revision"],
            "latest_revision": status["latest_revision"],
            "failed_revision": status["latest_revision"],
        }, status_path)
        return {"ok": False, "error": "Token Meter could not start the updater."}
    try:
        launch_options = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": process_options.close_fds,
        }
        if process_options.start_new_session:
            launch_options["start_new_session"] = True
        if process_options.creation_flags:
            launch_options["creationflags"] = process_options.creation_flags
        if retry_failed:
            launch_options["env"] = {
                **os.environ,
                "TOKEN_METER_UPDATE_RETRY_REVISION": failed_target,
            }
        popen(list(update_plan.command), **launch_options)
    except OSError:
        _persist_update_status({
            "phase": "failed", "error_code": "launch_failed",
            "checked_at": status["checked_at"],
            "current_revision": status["current_revision"],
            "latest_revision": status["latest_revision"],
            "failed_revision": status["latest_revision"],
        }, status_path)
        return {"ok": False, "error": "Token Meter could not start the updater."}
    return {"ok": True, "status": software_update_status(settings_path, status_path)}


def software_update_watcher():
    """Run checks every 10 minutes and install safe results when enabled."""
    while True:
        _update_wake.clear()
        settings = update_settings()
        if not settings["enabled"]:
            _update_wake.wait(60)
            continue
        previous = _update_status_record()
        phase = str(previous.get("phase") or "")
        if phase in {"starting", "fetching", "installing"}:
            _update_wake.wait(5)
            continue
        if phase in {"complete", "failed"}:
            if _update_wake.wait(UPDATE_CHECK_INTERVAL_S):
                continue
        status = check_for_software_update()
        target = _safe_update_revision(status.get("latest_revision"))
        failed_target = _safe_update_revision(previous.get("failed_revision"))
        if (settings["auto_install"] and status.get("available") is True
                and status.get("can_update") is True
                and target and target != failed_target):
            start_software_update()
        _update_wake.wait(UPDATE_CHECK_INTERVAL_S)


def toml_named_sections(path, table):
    """Read simple enabled state from named TOML sections without a TOML dependency."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    header = re.compile(rf'^\[{re.escape(table)}\.(?:"([^"]+)"|([^\.\]]+))\]\s*$', re.MULTILINE)
    matches = list(header.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        name = (match.group(1) or match.group(2) or "").strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        enabled_match = re.search(r'^\s*enabled\s*=\s*(true|false)\s*$', body, re.MULTILINE | re.IGNORECASE)
        result[name] = {
            "enabled": enabled_match is None or enabled_match.group(1).lower() == "true",
            "start": match.start(), "body_start": match.end(), "end": end,
        }
    return result


def safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def file_signature(path):
    """Return a cheap cache key that changes when a trace is replaced or appended."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def home_shorten(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path and path.startswith(home) else path


def source_runtime_label(source):
    """Return the trace runtime whose timing semantics produced this session."""
    source = source or {}
    if source.get("runtime"):
        return str(source["runtime"])
    provider = source.get("provider") or "unknown"
    if provider == "codex":
        return "Codex"
    if provider == "cursor":
        return "Cursor"
    if provider != "claude":
        return str(source.get("label") or provider)
    if source.get("client") != "claude_desktop":
        return "Claude Code"
    metadata_path = os.path.abspath(os.path.expanduser(str(source.get("metadata_path") or "")))
    third_party_root = os.path.abspath(CLAUDE_DESKTOP_DATA_ROOTS[1])
    if metadata_path == third_party_root or metadata_path.startswith(third_party_root + os.sep):
        return "Claude-3P"
    return "Claude Desktop"


def metric_availability(provider, **overrides):
    """Describe which normalized metrics are backed by the provider trace.

    Missing availability remains backward compatible elsewhere, but every new
    state carries this object so unavailable Cursor billing values cannot look
    like measured zeroes.
    """
    if provider == "cursor":
        result = {
            "cost": False,
            "tokens": False,
            "input_tokens": False,
            "output_tokens": False,
            "cache": False,
            "throughput": False,
            "context": False,
            "timing": False,
            "tool_results": False,
        }
    else:
        result = {
            "cost": True,
            "tokens": True,
            "input_tokens": True,
            "output_tokens": True,
            "cache": True,
            "throughput": True,
            "context": True,
            "timing": True,
            "tool_results": True,
        }
    result.update({key: bool(value) for key, value in overrides.items() if key in result})
    return result


def metric_available(row, metric):
    """Treat absent availability as available for legacy states and fixtures."""
    return _domain_metric_available(row, metric)


def make_usage_provenance(session_ids, estimated_ids=(), available_ids=None,
                          estimated_cost=0.0, estimated_tokens=0):
    """Describe evidence quality separately from metric coverage."""
    return _domain_make_usage_provenance(
        session_ids,
        estimated_ids,
        available_ids,
        estimated_cost,
        estimated_tokens,
    )


def usage_provenance(rows):
    """Roll up reported versus local-estimate sessions without changing coverage."""
    return _domain_usage_provenance(rows)


def decode_claude_project(name):
    user = os.environ.get("USER", "")
    prefix = "-Users-" + user
    if user and name.startswith(prefix):
        name = "~" + name[len(prefix):]
    return name.strip("-").replace("-", "/").replace("~/", "~/")


def decode_cursor_project(name):
    """Best-effort fallback for Cursor's lossy project directory encoding."""
    user = os.environ.get("USER", "")
    prefix = f"Users-{user}-" if user else ""
    if prefix and name.startswith(prefix):
        return home_shorten("/Users/" + user + "/" + name[len(prefix):].replace("-", "/"))
    return name.replace("-", "/") if name else ""


def codex_id_from_path(path, meta=None):
    if meta and meta.get("session_id"):
        return meta["session_id"]
    base = os.path.basename(path).rsplit(".", 1)[0]
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", base)
    return match.group(1) if match else base


def normalize_dynamic_tools(dynamic_tools):
    """Flatten old function arrays and newer namespace-grouped tool catalogs."""
    out = []
    for item in dynamic_tools or []:
        if not isinstance(item, dict):
            out.append({
                "namespace": "unknown", "name": str(item) or "?", "kind": "tool",
                "defer_loading": False, "definition_tokens": 0,
            })
            continue
        children = item.get("tools")
        rows = children if isinstance(children, list) else [item]
        parent_namespace = item.get("namespace") or item.get("name") or "unknown"
        parent_deferred = bool(item.get("deferLoading"))
        for child in rows:
            if not isinstance(child, dict):
                child = {"name": str(child)}
            name = child.get("name") or "?"
            namespace = child.get("namespace") or parent_namespace or "unknown"
            raw_identity = name
            if name.startswith("mcp__"):
                ident = tool_identity(name)
                namespace = ident["namespace"]
                kind = "mcp"
            elif str(namespace).startswith("mcp__"):
                parts = str(namespace).split("__")
                namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
                raw_identity = f"mcp__{namespace}__{name}"
                kind = "mcp"
            else:
                kind = "tool"
            definition = {
                "description": child.get("description") or "",
                "inputSchema": child.get("inputSchema") or child.get("input_schema") or {},
            }
            out.append({
                "namespace": namespace,
                "name": raw_identity,
                "kind": kind,
                "defer_loading": bool(child.get("deferLoading", parent_deferred)),
                "definition_tokens": len(json.dumps(definition, sort_keys=True)) // CHARS_PER_TOKEN,
            })
    return out[:240]


def catalog_counts(catalog):
    advertised = len(catalog or [])
    deferred = sum(1 for row in catalog or [] if row.get("defer_loading"))
    return {"advertised": advertised, "eager": max(0, advertised - deferred), "deferred": deferred}


def cursor_model(composer, header=None):
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    if isinstance(config, dict) and config.get("modelName"):
        return str(config["modelName"])
    for value in (composer, header):
        if isinstance(value, dict) and value.get("model"):
            return str(value["model"])
    return "unknown"


_claude_native_adapters = {}
_codex_native_adapters = {}
_cursor_native_adapters = {}
_kiro_native_adapters = {}


def _claude_compatibility():
    return {
        "chars_per_token": CHARS_PER_TOKEN,
        "context_sample_limit": CURRENT_SESSION_CONTEXT_SAMPLES,
        "default_model": DEFAULT_CLAUDE_MODEL,
        "add_model_daily": add_model_daily,
        "add_model_summary": add_model_summary,
        "analysis_block": analysis_block,
        "analyze_language_signals": analyze_language_signals,
        "attach_language_signals": attach_language_signals,
        "build_insights": build_insights,
        "build_state": build_state,
        "claude_human_text": _claude_human_text,
        "claude_performance_samples": claude_performance_samples,
        "claude_tool_call_evidence": claude_tool_call_evidence,
        "claude_tool_results": claude_tool_results,
        "claude_user_events": claude_user_events,
        "claude_wait_samples": claude_wait_samples,
        "compact_text": compact_text,
        "claude_billing_supported": claude_billing_supported,
        "cost_of": cost_of,
        "execution_timing": execution_timing,
        "metric_availability": metric_availability,
        "parse_iso": parse_iso,
        "performance_summary": performance_summary,
        "price_for": price_for,
        "skill_names_from_value": skill_names_from_value,
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "text_from_content": text_from_content,
        "tool_identity": tool_identity,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
        "usage_tokens": usage_tokens,
        "user_prompt_preview": user_prompt_preview,
    }


def _claude_adapter_for(projects_root=None, desktop_data_roots=None):
    projects = os.path.abspath(os.path.expanduser(projects_root or CLAUDE_PROJECTS))
    roots = tuple(
        os.path.abspath(os.path.expanduser(path))
        for path in (desktop_data_roots or CLAUDE_DESKTOP_DATA_ROOTS)
    )
    key = (projects, *roots)
    adapter = _claude_native_adapters.get(key)
    if adapter is None:
        adapter = ClaudeRuntimeAdapter(
            projects,
            roots,
            project_resolver=home_shorten,
            project_decoder=decode_claude_project,
            compatibility=_claude_compatibility(),
            path_cache=_recursive_path_cache,
            default_model=DEFAULT_CLAUDE_MODEL,
        )
        _claude_native_adapters[key] = adapter
        if len(_claude_native_adapters) > 8:
            oldest = next(iter(_claude_native_adapters))
            if oldest != key:
                _claude_native_adapters.pop(oldest, None)
    return adapter


def _claude_native_adapter():
    return _claude_adapter_for()


def _codex_compatibility():
    return {
        "chars_per_token": CHARS_PER_TOKEN,
        "context_sample_limit": CURRENT_SESSION_CONTEXT_SAMPLES,
        "default_model": DEFAULT_OPENAI_MODEL,
        "add_model_daily": add_model_daily,
        "add_model_summary": add_model_summary,
        "analysis_block": analysis_block,
        "analyze_language_signals": analyze_language_signals,
        "attach_language_signals": attach_language_signals,
        "build_insights": build_insights,
        "build_state": build_state,
        "catalog_counts": catalog_counts,
        "codex_approval_policy_label": codex_approval_policy_label,
        "codex_live_performance_summary": codex_live_performance_summary,
        "codex_performance_samples": codex_performance_samples,
        "codex_tool_call_evidence": codex_tool_call_evidence,
        "codex_wait_samples": codex_wait_samples,
        "compact_text": compact_text,
        "cost_of": cost_of,
        "execution_timing": execution_timing,
        "metric_availability": metric_availability,
        "home_shorten": home_shorten,
        "new_codex_pending": new_codex_pending,
        "normalize_dynamic_tools": normalize_dynamic_tools,
        "observable_output_chars": observable_output_chars,
        "parse_iso": parse_iso,
        "performance_summary": performance_summary,
        "price_for": price_for,
        "skill_names_from_value": skill_names_from_value,
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "text_from_content": text_from_content,
        "tool_identity": tool_identity,
        "tool_result_is_error": tool_result_is_error,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
        "usage_tokens": usage_tokens,
        "user_prompt_preview": user_prompt_preview,
    }


def _codex_adapter_for(sessions_root=None, index_path=None):
    key = tuple(os.path.abspath(os.path.expanduser(path)) for path in (
        sessions_root or CODEX_SESSIONS,
        index_path or CODEX_INDEX,
    ))
    adapter = _codex_native_adapters.get(key)
    if adapter is None:
        adapter = CodexRuntimeAdapter(
            *key,
            project_resolver=home_shorten,
            compatibility=_codex_compatibility(),
            path_cache=_recursive_path_cache,
            default_model=DEFAULT_OPENAI_MODEL,
        )
        _codex_native_adapters[key] = adapter
        if len(_codex_native_adapters) > 8:
            oldest = next(iter(_codex_native_adapters))
            if oldest != key:
                _codex_native_adapters.pop(oldest, None)
    return adapter


def _codex_native_adapter():
    return _codex_adapter_for()


def _cursor_compatibility():
    """Inject presentation helpers while Cursor owns evidence interpretation."""
    return {
        "zero_price": ZERO_PRICE,
        "analysis_block": analysis_block,
        "analyze_language_signal_turns": analyze_language_signal_turns,
        "argument_fingerprint": argument_fingerprint,
        "attach_language_signals": attach_language_signals,
        "build_state": build_state,
        "compact_text": compact_text,
        "context_sample_limit": CURRENT_SESSION_CONTEXT_SAMPLES,
        "cost_of": cost_of,
        "cursor_context_estimates": lambda *args: cursor_context_estimates(*args),
        "cursor_enriched_groups": lambda *args: cursor_enriched_groups(*args),
        "cursor_model_call_count": lambda *args: cursor_model_call_count(*args),
        "cursor_price_variant": lambda *args: cursor_price_variant(*args),
        "cursor_pricing_note": lambda *args: cursor_pricing_note(*args),
        "cursor_prompt_breakdown": lambda *args: cursor_prompt_breakdown(*args),
        "cursor_timestamp": lambda *args: cursor_timestamp(*args),
        "cursor_tool_identity": lambda *args: cursor_tool_identity(*args),
        "cursor_transcript_groups": lambda *args: cursor_transcript_groups(*args),
        "cursor_turn_timing": lambda *args: cursor_turn_timing(*args),
        "cursor_visible_output": lambda *args: cursor_visible_output(*args),
        "duration_label": duration_label,
        "load": load,
        "local_tm": local_tm,
        "metric_availability": metric_availability,
        "metric_available": metric_available,
        "observable_output_chars": observable_output_chars,
        "performance_summary": performance_summary,
        "price_for": price_for,
        "recompute": lambda source: recompute_cursor(source),
        "request_spans": lambda session_id: cursor_request_spans(session_id),
        "skill_names_from_value": skill_names_from_value,
        "snapshot": lambda session_id: cursor_snapshot(session_id),
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "tool_result_is_error": tool_result_is_error,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
        "usage_provenance": usage_provenance,
    }


def _cursor_adapter_for(projects_root=None, database_path=None, request_logs=None):
    key = tuple(os.path.abspath(os.path.expanduser(path)) for path in (
        projects_root or CURSOR_PROJECTS,
        database_path or CURSOR_STATE_DB,
        request_logs or CURSOR_REQUEST_LOGS,
    ))
    adapter = _cursor_native_adapters.get(key)
    if adapter is None:
        adapter = CursorRuntimeAdapter(
            *key,
            project_resolver=home_shorten,
            project_decoder=decode_cursor_project,
            compatibility=_cursor_compatibility(),
            path_cache=_recursive_path_cache,
        )
        _cursor_native_adapters[key] = adapter
        if len(_cursor_native_adapters) > 8:
            oldest = next(iter(_cursor_native_adapters))
            if oldest != key:
                _cursor_native_adapters.pop(oldest, None)
    return adapter


def _cursor_native_adapter():
    return _cursor_adapter_for()


def _kiro_compatibility():
    return {
        "add_model_daily": add_model_daily,
        "add_model_summary": add_model_summary,
        "analysis_block": analysis_block,
        "build_state": build_state,
        "metric_availability": metric_availability,
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
    }


def _kiro_adapter_for(sessions_root=None, agent_storage_root=None):
    key = tuple(os.path.abspath(os.path.expanduser(path)) for path in (
        sessions_root or KIRO_SESSIONS,
        agent_storage_root or KIRO_AGENT_STORAGE,
    ))
    adapter = _kiro_native_adapters.get(key)
    if adapter is None:
        adapter = KiroRuntimeAdapter(
            *key,
            project_resolver=home_shorten,
            quote_resolver=price_quote,
            compatibility=_kiro_compatibility(),
            path_cache=_recursive_path_cache,
        )
        _kiro_native_adapters[key] = adapter
        if len(_kiro_native_adapters) > 8:
            oldest = next(iter(_kiro_native_adapters))
            if oldest != key:
                _kiro_native_adapters.pop(oldest, None)
    return adapter


def _kiro_native_adapter():
    return _kiro_adapter_for()


_pi_native_adapters = {}


def _pi_compatibility():
    return {
        "add_model_daily": add_model_daily,
        "add_model_summary": add_model_summary,
        "analysis_block": analysis_block,
        "build_state": build_state,
        "context_sample_limit": CURRENT_SESSION_CONTEXT_SAMPLES,
        "metric_availability": metric_availability,
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "tool_identity": tool_identity,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
    }


def _pi_adapter_for(agent_dir=None):
    path = os.path.abspath(os.path.expanduser(agent_dir or PI_AGENT_DIR))
    adapter = _pi_native_adapters.get(path)
    if adapter is None:
        adapter = PiRuntimeAdapter(
            path,
            project_resolver=home_shorten,
            compatibility=_pi_compatibility(),
            path_cache=_recursive_path_cache,
        )
        _pi_native_adapters[path] = adapter
        if len(_pi_native_adapters) > 8:
            oldest = next(iter(_pi_native_adapters))
            if oldest != path:
                _pi_native_adapters.pop(oldest, None)
    return adapter


def _pi_native_adapter():
    return _pi_adapter_for()


def pi_session_sources(agent_dir=None):
    return list(_pi_adapter_for(agent_dir).discover_legacy(
        DiscoveryContext(home=os.path.expanduser("~"))
    ))


def recompute_pi(source):
    return _pi_native_adapter().recompute_legacy(source)


_hermes_native_adapters = {}


def _hermes_compatibility():
    return {
        "analysis_block": analysis_block,
        "build_state": build_state,
        "cost_of": cost_of,
        "metric_availability": metric_availability,
        "price_for": price_for,
        "summary_row": summary_row,
        "tool_summary": tool_summary,
    }


def _hermes_adapter_for(database_path=None):
    path = os.path.abspath(os.path.expanduser(database_path or HERMES_STATE_DB))
    adapter = _hermes_native_adapters.get(path)
    if adapter is None:
        adapter = HermesRuntimeAdapter(
            path, project_resolver=home_shorten, compatibility=_hermes_compatibility(),
        )
        _hermes_native_adapters[path] = adapter
        if len(_hermes_native_adapters) > 8:
            oldest = next(iter(_hermes_native_adapters))
            if oldest != path:
                _hermes_native_adapters.pop(oldest, None)
    return adapter


def _hermes_native_adapter():
    return _hermes_adapter_for()


def hermes_session_sources(database_path=None):
    return list(_hermes_adapter_for(database_path).discover_legacy(
        DiscoveryContext(home=os.path.expanduser("~"))
    ))


def recompute_hermes(source):
    return _hermes_native_adapter().recompute_legacy(source)


_grok_native_adapters = {}


def _grok_compatibility():
    return _pi_compatibility()


def _grok_adapter_for(grok_home=None):
    path = os.path.abspath(os.path.expanduser(grok_home or GROK_HOME))
    adapter = _grok_native_adapters.get(path)
    if adapter is None:
        adapter = GrokRuntimeAdapter(
            path,
            project_resolver=home_shorten,
            compatibility=_grok_compatibility(),
        )
        _grok_native_adapters[path] = adapter
        if len(_grok_native_adapters) > 8:
            oldest = next(iter(_grok_native_adapters))
            if oldest != path:
                _grok_native_adapters.pop(oldest, None)
    return adapter


def _grok_native_adapter():
    return _grok_adapter_for()


def grok_session_sources(grok_home=None):
    return list(_grok_adapter_for(grok_home).discover_legacy(
        DiscoveryContext(home=os.path.expanduser("~"))
    ))


def recompute_grok(source):
    return _grok_native_adapter().recompute_legacy(source)


def opencode_db_path():
    path = os.path.expanduser(OPENCODE_DB)
    return path if os.path.isabs(path) else os.path.join(OPENCODE_DATA_ROOT, path)


_opencode_native_adapters = {}


def _opencode_compatibility():
    return {
        "chars_per_token": CHARS_PER_TOKEN,
        "analysis_block": analysis_block,
        "analyze_language_signal_turns": analyze_language_signal_turns,
        "argument_fingerprint": argument_fingerprint,
        "attach_language_signals": attach_language_signals,
        "build_insights": build_insights,
        "build_state": build_state,
        "compact_text": compact_text,
        "duration_label": duration_label,
        "metric_availability": metric_availability,
        "metric_available": metric_available,
        "model_context_window": _opencode_model_window,
        "observable_output_chars": observable_output_chars,
        "performance_summary": performance_summary,
        "skill_names_from_value": skill_names_from_value,
        "summarize_tool_evidence": summarize_tool_evidence,
        "summary_row": summary_row,
        "tool_identity": tool_identity,
        "tool_summary": tool_summary,
        "trace_event": trace_event,
        "user_prompt_preview": user_prompt_preview,
    }


def _opencode_native_adapter():
    path = os.path.abspath(os.path.expanduser(opencode_db_path()))
    models_path = os.path.abspath(os.path.expanduser(OPENCODE_MODELS_PATH))
    key = (path, models_path)
    adapter = _opencode_native_adapters.get(key)
    if adapter is None:
        adapter = OpenCodeRuntimeAdapter(
            path,
            models_path,
            project_resolver=home_shorten,
            compatibility=_opencode_compatibility(),
            detail_message_limit=OPENCODE_DETAIL_MESSAGE_LIMIT,
            context_sample_limit=CURRENT_SESSION_CONTEXT_SAMPLES,
        )
        _opencode_native_adapters[key] = adapter
        if len(_opencode_native_adapters) > 8:
            oldest = next(iter(_opencode_native_adapters))
            if oldest != key:
                _opencode_native_adapters.pop(oldest, None)
    return adapter


def _load_opencode_models(path=None):
    if path is None:
        return dict(_opencode_native_adapter()._load_models())
    return dict(OpenCodeRuntimeAdapter(opencode_db_path(), path)._load_models())


def _opencode_model_window(model_id, provider_id=""):
    return _opencode_native_adapter().model_context_window(model_id, provider_id)


def _opencode_db_connection(path=None):
    """Open OpenCode's live database read-only while retaining WAL visibility."""
    if path is None:
        return _opencode_native_adapter().connection()
    return OpenCodeRuntimeAdapter(path, OPENCODE_MODELS_PATH).connection()


def _opencode_json(value, default=None):
    """Decode one OpenCode SQLite JSON text value without failing the session."""
    return _native_opencode_json(value, default)


def opencode_session_sources(db_path=None):
    if db_path is not None:
        adapter = OpenCodeRuntimeAdapter(
            db_path, OPENCODE_MODELS_PATH, project_resolver=home_shorten,
        )
    else:
        adapter = _opencode_native_adapter()
    return list(adapter.discover_legacy(DiscoveryContext(home=os.path.expanduser("~"))))


def kiro_session_sources(sessions_root=None, agent_storage_root=None):
    adapter = _kiro_adapter_for(sessions_root, agent_storage_root)
    return list(adapter.discover_legacy(DiscoveryContext(home=os.path.expanduser("~"))))


def claude_trace_cwd(path, max_lines=120):
    return _claude_native_adapter().trace_cwd(path, max_lines)


def claude_desktop_metadata_paths(root=None):
    return list(_claude_native_adapter().desktop_metadata_paths(root))


def claude_desktop_index(root=None):
    return _claude_native_adapter().desktop_index(root)


def claude_local_agent_sources(desktop_idx):
    return _claude_native_adapter().local_agent_sources(desktop_idx)


def claude_session_sources():
    return list(_claude_native_adapter().discover_legacy(
        DiscoveryContext(home=os.path.expanduser("~"))
    ))


def codex_meta(path):
    return _codex_native_adapter().metadata(path)


def codex_index():
    return _codex_native_adapter()._read_index()


def codex_session_sources():
    return enrich_codex_model_identities(
        _codex_native_adapter().discover_legacy(
            DiscoveryContext(home=os.path.expanduser("~"))
        )
    )


def _cursor_db_connection(path=None):
    return _cursor_adapter_for(database_path=path).connection()


def cursor_metadata_index(db_path=None):
    return _cursor_adapter_for(database_path=db_path).metadata_index()


def reset_cursor_metadata_cache():
    for adapter in _cursor_native_adapters.values():
        adapter.reset_metadata_cache()


def cursor_snapshot(composer_id, db_path=None):
    return _cursor_adapter_for(database_path=db_path).snapshot_legacy(str(composer_id))


def cursor_request_spans(composer_id, root=None):
    return _cursor_adapter_for(request_logs=root).request_spans(str(composer_id))


def cursor_enrichment_mtime(db_path=None, log_root=None):
    return _cursor_adapter_for(
        database_path=db_path, request_logs=log_root,
    ).enrichment_mtime()


def cursor_session_sources():
    return list(_cursor_native_adapter().discover_legacy(
        DiscoveryContext(home=os.path.expanduser("~"))
    ))


def all_session_sources():
    global _RUNTIME_DISCOVERY_FAILURES
    result = runtime_registry().discover_legacy_all(
        DiscoveryContext(home=os.path.expanduser("~"))
    )
    sources = enrich_session_model_identities(result.sources)
    _RUNTIME_DISCOVERY_FAILURES = result.failures
    return sources


def runtime_discovery_failures():
    return [failure.to_public_dict() for failure in _RUNTIME_DISCOVERY_FAILURES]


def runtime_adapter_failures():
    failures = list(_RUNTIME_DISCOVERY_FAILURES)
    if _RUNTIME_LOAD_FAILURE is not None:
        failures.append(_RUNTIME_LOAD_FAILURE)
    return [failure.to_public_dict() for failure in failures]


def runtime_display_label(runtime_id):
    adapter = runtime_registry().get(str(runtime_id or ""))
    return adapter.descriptor.label if adapter is not None else "Unknown Runtime"


def supported_runtime_labels():
    return [descriptor.label for descriptor in runtime_registry().descriptors]


def supported_runtime_phrase():
    labels = supported_runtime_labels()
    if len(labels) < 2:
        return labels[0] if labels else "a supported runtime"
    return "{}, or {}".format(", ".join(labels[:-1]), labels[-1])


def publish_source_inventory(sources):
    """Atomically publish a reusable discovery snapshot for lightweight endpoints."""
    global _SOURCE_INVENTORY
    source_rows = tuple(sources or ())
    live_paths = {
        str(source.get("path") or "")
        for source in source_rows
        if source.get("path")
    }
    if not _RUNTIME_DISCOVERY_FAILURES:
        with _summary_cache_lock:
            for path in tuple(_summary_cache):
                if path not in live_paths:
                    _summary_cache.pop(path, None)
    clients = defaultdict(int)
    for source in source_rows:
        clients[source.get("client") or source.get("provider") or "unknown"] += 1
    _SOURCE_INVENTORY = {
        "ready": True,
        "sources": source_rows,
        "count": len(source_rows),
        "clients": dict(clients),
        "updated_at": time.time(),
    }
    _git_delivery_wake.set()
    return _SOURCE_INVENTORY


def cached_session_sources():
    """Return the watcher-owned source snapshot without touching the filesystem."""
    inventory = _SOURCE_INVENTORY
    return list(inventory.get("sources") or ()), bool(inventory.get("ready"))


def source_from_path(path):
    if str(path or "").startswith("opencode:"):
        sid = str(path).split(":", 1)[1]
        for source in opencode_session_sources():
            if source["id"] == sid:
                return source
        return None
    for source in all_session_sources():
        if source["path"] == path:
            return source
    if path and path.startswith(os.path.expanduser("~/.codex/")):
        meta = codex_meta(path)
        sid = codex_id_from_path(path, meta)
        source = {
            "provider": "codex", "label": "Codex", "id": sid, "session": os.path.basename(path),
            "path": path, "project": home_shorten(meta.get("cwd") or os.path.dirname(path)),
            "mtime": safe_mtime(path), "title": None,
            "model": meta.get("model") or "unknown-model",
            "observed_model": meta.get("model") or "unknown-model",
            "model_provider": meta.get("model_provider") or "openai",
            "physical_trace_id": meta.get("physical_trace_id") or sid,
            "logical_session_id": meta.get("logical_session_id") or sid,
            "parent_thread_id": meta.get("parent_thread_id"),
            "lineage_parent_id": meta.get("lineage_parent_id"),
            "tools_loaded": meta.get("tools_loaded") or 0,
            "tools_eager": meta.get("tools_eager") or 0,
            "tools_deferred": meta.get("tools_deferred") or 0,
            "tool_catalog": meta.get("tool_catalog") or [],
            "tool_namespaces": meta.get("tool_namespaces") or [],
        }
        return enrich_codex_model_identities([source])[0]
    if path and path.startswith(os.path.expanduser("~/.cursor/")):
        sid = os.path.basename(path).rsplit(".", 1)[0]
        adapter = _cursor_native_adapter()
        metadata = adapter.metadata_index().get(sid) or {}
        project_dir = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
        metadata_mtime = max(
            float(metadata.get("updated_at") or 0) / 1000.0,
            float(metadata.get("checkpoint_at") or 0) / 1000.0,
        )
        activity_mtime = max(safe_mtime(path), metadata_mtime)
        return {
            "provider": "cursor", "client": "cursor", "label": "Cursor",
            "runtime": "Cursor", "id": sid, "session": os.path.basename(path),
            "path": path,
            "project": home_shorten(metadata.get("project") or decode_cursor_project(project_dir)),
            "mtime": activity_mtime,
            "signature_mtime": activity_mtime,
            "request_revision": adapter.request_revision_index().get(sid, ""),
            "trace_mtime": safe_mtime(path), "title": metadata.get("title") or None,
            "model": metadata.get("model") or "unknown",
        }
    sid = os.path.basename(path).rsplit(".", 1)[0]
    trace_cwd = claude_trace_cwd(path)
    return {
        "provider": "claude", "client": "claude_code", "label": "Claude Code", "id": sid,
        "session": os.path.basename(path),
        "path": path,
        "project": home_shorten(trace_cwd) or decode_claude_project(os.path.basename(os.path.dirname(path))),
        "mtime": safe_mtime(path), "title": None,
    }


def newest_source():
    sources = all_session_sources()
    return max(sources, key=lambda s: s["mtime"]) if sources else None


def find_session(sid, sources=None):
    source_pool = sources if sources is not None else all_session_sources()
    physical_matches = []
    logical_matches = []
    for source in source_pool:
        stem = os.path.basename(source["path"]).rsplit(".", 1)[0]
        if sid in (source["session"], stem):
            physical_matches.append(source)
        elif sid in (source["id"], source.get("desktop_session_id")):
            logical_matches.append(source)
    matches = physical_matches or logical_matches
    if len(matches) < 2:
        return matches[0] if matches else None

    def selection_rank(source):
        try:
            summary = session_summary(source)
        except Exception:
            summary = {}
        active = (summary.get("terminal") is False)
        try:
            activity = max(
                float(source.get("mtime") or 0),
                float(source.get("signature_mtime") or 0),
            )
        except (TypeError, ValueError, OverflowError):
            activity = 0.0
        return active, activity, str(source.get("path") or "")

    return max(matches, key=selection_rank)


def trash_session_log(session_id, sources=None, trash_dir=None, mover=None):
    """Move one exact, currently discovered session log to Trash."""
    session_id = str(session_id or "").strip()
    if not session_id or len(session_id) > 240:
        return {"ok": False, "error": "A valid session ID is required.", "error_code": "invalid_id"}
    source_pool = list(sources) if sources is not None else all_session_sources()
    source = find_session(session_id, sources=source_pool)
    if not source or str(source.get("id") or "") != session_id:
        return {"ok": False, "error": "Session is not in the discovered log inventory.",
                "error_code": "not_found"}
    duplicate_paths = {
        str(path) for path in (source.get("_duplicate_paths") or ()) if path
    }
    if len(duplicate_paths) > 1:
        return {
            "ok": False,
            "error": "This logical session has multiple trace files and cannot be deleted safely.",
            "error_code": "ambiguous_id",
        }
    path = str(source.get("path") or "")
    if not path.endswith(".jsonl") or not os.path.isfile(path):
        return {"ok": False, "error": "The discovered session log is not available.",
                "error_code": "not_found"}

    trash_plan = _PLATFORM_SERVICES.trash_plan(
        path,
        override=trash_dir,
        command_available=bool(trash_dir is None and mover is None and shutil.which("gio")),
    )
    if not trash_plan.supported:
        return {
            "ok": False,
            "error": "Moving session logs to Trash is unavailable on this platform.",
            "error_code": trash_plan.error_code or "trash_unsupported",
        }
    try:
        if trash_plan.strategy == "command":
            subprocess.run(
                list(trash_plan.command), check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            destination = os.path.basename(path)
        else:
            trash_dir = trash_plan.destination_root
            mover = mover or shutil.move
            os.makedirs(trash_dir, exist_ok=True)
            base = f"Token Meter - {os.path.basename(path)}"
            stem, ext = os.path.splitext(base)
            destination = os.path.join(trash_dir, base)
            suffix = 2
            while os.path.exists(destination):
                destination = os.path.join(trash_dir, f"{stem} {suffix}{ext}")
                suffix += 1
            mover(path, destination)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": "Token Meter could not move the session log to Trash.",
                "error_code": "trash_failed"}

    _summary_cache.pop(path, None)
    with _session_state_cache_lock:
        _session_state_cache.pop(path, None)
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True,
        "changed": True,
        "session_id": session_id,
        "title": source.get("title") or "(untitled log)",
        "project": source.get("project") or "",
        "provider": source.get("provider") or "",
        "trash_name": os.path.basename(destination),
        "message": "Session log moved to Trash.",
    }


def _matching_price(model, table):
    _rule, price = _catalog_matching_price(model, table)
    return price


def cursor_model_parameters(composer, model=None):
    """Return persisted parameters for the selected Cursor model."""
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    selected = config.get("selectedModels") if isinstance(config, dict) else []
    for row in selected or []:
        if not isinstance(row, dict):
            continue
        params = {
            str(item.get("id")): item.get("value")
            for item in (row.get("parameters") or []) if isinstance(item, dict) and item.get("id")
        }
        if not model or str(row.get("modelId") or "") == str(model):
            return params
    return {}


def cursor_price_variant(composer, model):
    for cursor_model_id in CURSOR_VARIANT_MODEL_IDS:
        if not _catalog_model_alias_matches(model, "cursor", cursor_model_id):
            continue
        fast = cursor_model_parameters(composer, cursor_model_id).get("fast")
        if str(fast).lower() == "true":
            return "fast"
        if str(fast).lower() == "false":
            return "standard"
    return ""


@functools.lru_cache(maxsize=2048)
def _cached_price_query(model_provider, model_id, variant, timestamp):
    observed_at = (
        None if timestamp is None else
        datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    )
    return PriceQuery(
        ModelRef(model_provider, model_id, variant),
        observed_at,
    )


def _price_query_from_compat(model_provider, model, variant=None, at=None):
    """Construct a bounded cached request for the compatibility facade."""
    model_id = str(model or "").strip() or "unknown-model"
    if len(model_id) > 160:
        model_id = "unknown-model"
    variant = str(variant).strip() if variant is not None else None
    return _cached_price_query(
        model_provider, model_id, variant or None, _price_timestamp(at)
    )


def price_quote(query, path=None):
    """Resolve one exact model-provider query without cross-provider fallback."""
    settings_provider = settings_provider_for_model_provider(query.model.provider_id)
    if settings_provider is None:
        return _catalog_quote_for(query)
    table = effective_model_price_table(
        settings_provider, path=path, at=query.observed_at
    )
    cache_key = (
        id(table), query.model.provider_id, query.model.model_id, query.model.variant,
    )
    quote = _model_pricing_cache["quotes"].get(cache_key)
    if quote is not None:
        return quote
    quote = _catalog_quote_for(query, effective_table=table)
    quotes = _model_pricing_cache["quotes"]
    if len(quotes) >= 2048:
        quotes.clear()
    quotes[cache_key] = quote
    return quote


def _resolved_price_quote(model, provider="claude", variant=None, at=None):
    """Resolve compatibility estimates without borrowing another model's price."""

    if provider == "cursor":
        quote = price_quote(_price_query_from_compat("cursor", model, variant, at))
        return quote, True
    if provider not in ("claude", "codex", "opencode"):
        return _compat_price_quote(
            str(provider or "unknown-provider"), str(model or "unknown-model"),
            str(variant or ""), 0.0, 0.0, 0.0, 0.0,
        ), True
    if provider == "opencode":
        quote = price_quote(_price_query_from_compat("opencode", model, variant, at))
        if quote.available:
            return quote, False
        return quote, True
    model_provider = canonical_model_provider(provider)
    quote = price_quote(_price_query_from_compat(model_provider, model, variant, at))
    if quote.available:
        return quote, False
    return quote, True


def price_for(model, provider="claude", variant=None, at=None):
    """Return the configured price effective for one usage event."""

    quote, approximate = _resolved_price_quote(model, provider, variant, at)
    return (quote.to_legacy_price() or dict(ZERO_PRICE)), approximate


def _price_multipliers(u, model, provider, at=None):
    """Return current published long-context multipliers without repricing history."""
    timestamp = _price_timestamp(at)
    if timestamp is not None and timestamp < GPT_56_PRICE_UPDATE_AT:
        return 1.0, 1.0
    compact = str(model or "").replace(" ", "-").lower()
    if provider not in ("codex", "cursor") or not compact.startswith("gpt-5.6"):
        return 1.0, 1.0
    input_tokens = (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0)
    )
    if input_tokens > GPT_56_LONG_CONTEXT_TOKENS:
        return 2.0, 1.5
    return 1.0, 1.0


_CLAUDE_FAST_PREMIUM_RULES = frozenset((
    "claude-opus-5", "claude-opus-4-8",
))
_CLAUDE_FAST_STANDARD_RULES = frozenset(("claude-opus-4-6",))
_CLAUDE_INFERENCE_GEO_RULES = frozenset((
    "claude-mythos-5", "claude-mythos-5-1",
    "claude-fable-5", "claude-fable-5-1",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6",
))


def _claude_quote_supports_dimensions(usage, quote):
    if not quote.available or not usage.get("billing_available"):
        return False
    matched_rule = quote.matched_rule
    if (usage.get("speed") == "fast" and
            matched_rule not in (
                _CLAUDE_FAST_PREMIUM_RULES | _CLAUDE_FAST_STANDARD_RULES
            )):
        return False
    if (usage.get("inference_geo") in ("global", "us") and
            matched_rule not in _CLAUDE_INFERENCE_GEO_RULES):
        return False
    return True


def claude_billing_supported(u, model, variant=None, at=None):
    usage = (
        u if isinstance(u, dict) and "cache_duration_available" in u
        else _normalized_claude_usage(u)
    )
    quote, _ = _resolved_price_quote(model, "claude", variant, at=at)
    return _claude_quote_supports_dimensions(usage, quote)


def cost_of(u, model, provider="claude", variant=None, at=None):
    quote, _ = _resolved_price_quote(model, provider, variant, at=at)
    if provider == "claude":
        usage = _normalized_claude_usage(u)
        empty = {
            "input": 0.0,
            "cache_write": 0.0,
            "cache_read": 0.0,
            "output": 0.0,
            "server_tools": 0.0,
        }
        if not _claude_quote_supports_dimensions(usage, quote):
            return empty

        input_rate = quote.input_per_million
        output_rate = quote.output_per_million
        cache_read_rate = quote.cache_read_per_million
        cache_write_5m_rate = quote.cache_write_per_million
        if usage.get("speed") == "fast":
            if quote.matched_rule in _CLAUDE_FAST_PREMIUM_RULES:
                input_rate = 10.0
                output_rate = 50.0
                cache_read_rate = 1.0
                cache_write_5m_rate = 12.5

        token_multiplier = 1.1 if usage.get("inference_geo") == "us" else 1.0
        cache_write_5m = (
            usage.get("cache_creation_5m_input_tokens", 0)
            + usage.get("cache_creation_unspecified_input_tokens", 0)
        )
        cache_write_1h = usage.get("cache_creation_1h_input_tokens", 0)
        return {
            "input": (
                usage.get("input_tokens", 0)
                * input_rate
                * token_multiplier
                / 1_000_000
            ),
            "cache_write": (
                (
                    cache_write_5m * cache_write_5m_rate
                    + cache_write_1h * input_rate * 2.0
                )
                * token_multiplier
                / 1_000_000
            ),
            "cache_read": (
                usage.get("cache_read_input_tokens", 0)
                * cache_read_rate
                * token_multiplier
                / 1_000_000
            ),
            "output": (
                usage.get("output_tokens", 0)
                * output_rate
                * token_multiplier
                / 1_000_000
            ),
            "server_tools": usage.get("web_search_requests", 0) * 0.01,
        }
    input_multiplier, output_multiplier = _price_multipliers(u, model, provider, at)
    return _domain_cost_breakdown_values(
        u.get("input_tokens", 0),
        u.get("output_tokens", 0),
        u.get("cache_read_input_tokens", 0),
        u.get("cache_creation_input_tokens", 0),
        quote,
        input_multiplier=input_multiplier,
        output_multiplier=output_multiplier,
    )


@functools.lru_cache(maxsize=1024)
def _compat_price_quote(provider_id, model_id, variant, input_price, output_price,
                        cache_read_price, cache_write_price):
    return PriceQuote(
        model=ModelRef(provider_id, model_id, variant or None),
        input_per_million=input_price,
        output_per_million=output_price,
        cache_read_per_million=cache_read_price,
        cache_write_per_million=cache_write_price,
        basis=EvidenceBasis.ESTIMATED,
        matched_rule=None,
    )


def usage_tokens(u):
    return _domain_usage_token_total_counts(
        u.get("input_tokens", 0),
        u.get("output_tokens", 0),
        u.get("cache_read_input_tokens", 0),
        u.get("cache_creation_input_tokens", 0),
    )


def usage_io_tokens(u):
    """Return total trace-reported input, including cache, and output."""
    return _domain_usage_io_token_counts(
        u.get("input_tokens", 0),
        u.get("output_tokens", 0),
        u.get("cache_read_input_tokens", 0),
        u.get("cache_creation_input_tokens", 0),
    )


def add_model_summary(stats, model, usage, cost, cost_available=None):
    return _domain_add_model_summary(
        stats, model, usage, cost, cost_available=cost_available,
    )


def add_model_daily(stats, model, usage, cost, ts, cost_available=None):
    """Accumulate exact trace-reported model I/O into local calendar days."""
    return _domain_add_model_daily(
        stats, model, usage, cost, ts, cost_available=cost_available,
    )


def claude_performance_samples(objs):
    """Return completed Claude turn samples with attributable trace timing."""
    messages = {rec["id"]: rec for rec in iter_claude_messages(objs)}
    samples = []
    current = None

    def ensure_current(ts=0):
        nonlocal current
        if current is None:
            current = {
                "message_ids": [], "seen": set(), "start_ts": ts or 0,
                "last_ts": ts or 0, "terminal": False,
                "user_pause_starts": {}, "user_pause_s": 0.0,
            }
        return current

    def close_turn(obj=None):
        nonlocal current
        duration_ms = obj.get("durationMs") if obj else 0
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        group = current
        current = None
        if not group:
            return
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            timing_basis = "turn_duration"
        else:
            duration_s = float(group.get("last_ts") or 0) - float(group.get("start_ts") or 0)
            timing_basis = "observed"
        if duration_s <= 0:
            return
        wall_duration_s = duration_s
        duration_s, user_pause_s = _claude_effective_duration(group, duration_s, timing_basis)
        if duration_s <= 0:
            return
        records = [messages[mid] for mid in group["message_ids"] if mid in messages and not messages[mid].get("side")]
        models = {rec.get("model") or "unknown-model" for rec in records if rec.get("usage")}
        if len(models) != 1:
            return
        input_tokens = output_tokens = 0
        uncached_input_tokens = cache_read_tokens = cache_write_tokens = 0
        peak_input_tokens = 0
        tool_ids = set()
        for rec in records:
            usage = rec.get("usage") or {}
            fresh_input, fresh_available = _domain_normalize_reported_token_count(
                usage.get("input_tokens")
            )
            cache_read, cache_read_available = _domain_normalize_reported_token_count(
                usage.get("cache_read_input_tokens", 0)
            )
            cache_write, cache_write_available = _domain_normalize_reported_token_count(
                usage.get("cache_creation_input_tokens", 0)
            )
            out_count, output_available = _domain_normalize_reported_token_count(
                usage.get("output_tokens")
            )
            if not (
                fresh_available and cache_read_available
                and cache_write_available and output_available
            ):
                return
            in_count = fresh_input + cache_read + cache_write
            input_tokens += in_count
            output_tokens += out_count
            uncached_input_tokens += fresh_input
            cache_read_tokens += cache_read
            cache_write_tokens += cache_write
            peak_input_tokens = max(peak_input_tokens, in_count)
            for block in rec.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_ids.add(block.get("id") or (block.get("name"), len(tool_ids)))
        if output_tokens <= 0:
            return
        ts = (parse_iso(obj.get("timestamp", "")) if obj else 0) or group.get("last_ts") or max(
            (rec.get("last_ts") or rec.get("ts") or 0 for rec in records), default=0
        )
        samples.append({
            "provider": "claude", "model": next(iter(models)),
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "peak_input_tokens": peak_input_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "model_calls": len(records),
            "duration_s": duration_s, "generation_s": duration_s,
            "ttft_s": 0.0, "tool_calls": len(tool_ids), "timing_basis": timing_basis,
            "wall_duration_s": wall_duration_s, "user_pause_s": user_pause_s,
        })

    for obj in objs:
        otype = obj.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if otype == "user":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if claude_user_text(msg).strip():
                close_turn()
                current = {
                    "message_ids": [], "seen": set(), "start_ts": ts,
                    "last_ts": ts, "terminal": False,
                    "user_pause_starts": {}, "user_pause_s": 0.0,
                }
            elif current:
                _close_claude_user_pauses(current, msg, ts)
            continue
        if otype == "assistant":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            mid = msg.get("id") or obj.get("uuid")
            group = ensure_current(ts)
            if mid not in group["seen"]:
                group["seen"].add(mid)
                group["message_ids"].append(mid)
            group["last_ts"] = max(float(group.get("last_ts") or 0), ts)
            for block in msg.get("content") or []:
                _track_claude_user_pause(group, block, ts)
            if msg.get("stop_reason") and msg.get("stop_reason") != "tool_use":
                group["terminal"] = True
            continue
        if otype == "system" and obj.get("subtype") == "turn_duration":
            close_turn(obj)
    if current and current.get("terminal"):
        close_turn()
    return samples


def codex_performance_samples(objs, default_model=None):
    """Return completed Codex task samples with model-attributable timing."""
    model = default_model or "unknown-model"
    samples = []
    current = None

    def ensure_current(ts=0):
        nonlocal current
        if current is None:
            current = {"started_ts": ts or 0, "usage": {}, "tool_calls": 0}
        return current

    def close_task(payload, ts):
        nonlocal current
        task = current
        current = None
        if not task or len(task["usage"]) != 1:
            return
        duration_ms = payload.get("duration_ms")
        ttft_ms = payload.get("time_to_first_token_ms")
        try:
            duration_ms = float(duration_ms or 0)
            ttft_ms = float(ttft_ms or 0)
        except (TypeError, ValueError):
            return
        if duration_ms <= 0:
            return
        sample_model, counts = next(iter(task["usage"].items()))
        output_tokens = int(counts.get("output_tokens") or 0)
        if output_tokens <= 0:
            return
        duration_s = duration_ms / 1000.0
        generation_s = (duration_ms - ttft_ms) / 1000.0 if 0 < ttft_ms < duration_ms else duration_s
        samples.append({
            "provider": "codex", "model": sample_model,
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0, "input_tokens": int(counts.get("input_tokens") or 0),
            "output_tokens": output_tokens, "duration_s": duration_s,
            "peak_input_tokens": int(counts.get("peak_input_tokens") or 0),
            "uncached_input_tokens": int(counts.get("uncached_input_tokens") or 0),
            "cache_read_tokens": int(counts.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(counts.get("cache_write_tokens") or 0),
            "model_calls": int(counts.get("model_calls") or 0),
            "generation_s": generation_s, "ttft_s": max(0.0, ttft_ms / 1000.0),
            "tool_calls": int(task.get("tool_calls") or 0), "timing_basis": "task_complete",
        })

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            continue
        if ptype == "task_started":
            current = {"started_ts": ts, "usage": {}, "tool_calls": 0}
            continue
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            ensure_current(ts)["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if not raw:
                continue
            usage = codex_usage(raw)
            task = ensure_current(ts)
            row = task["usage"].setdefault(model, {
                "input_tokens": 0, "output_tokens": 0, "peak_input_tokens": 0,
                "uncached_input_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "model_calls": 0,
            })
            input_count, output_count = usage_io_tokens(usage)
            row["input_tokens"] += input_count
            row["output_tokens"] += output_count
            row["peak_input_tokens"] = max(row["peak_input_tokens"], input_count)
            row["uncached_input_tokens"] += int(usage.get("input_tokens") or 0)
            row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
            row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
            row["model_calls"] += 1
            continue
        if ptype == "task_complete":
            close_task(payload, ts)
    return samples


def codex_live_performance_summary(objs):
    """Return provisional pace from completed checkpoints in the active task.

    Final throughput remains gated on ``task_complete`` in
    ``codex_performance_samples``. This separate summary lets the native menu
    show useful progress sooner without admitting an unfinished task into
    historical/model analytics. The denominator stops at the latest completed
    token checkpoint, so the displayed value changes only when another step
    completes rather than decaying while a tool is still running.
    """
    current = None

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if ptype == "task_started":
            current = {
                "started_ts": ts,
                "latest_checkpoint_ts": 0,
                "output_tokens": 0,
                "completed_steps": 0,
            }
            continue
        if ptype == "task_complete":
            current = None
            continue
        if ptype != "token_count" or not current:
            continue
        raw = ((payload.get("info") or {}).get("last_token_usage") or {})
        if not raw:
            continue
        output_tokens = usage_io_tokens(codex_usage(raw))[1]
        if output_tokens <= 0 or not ts:
            continue
        current["output_tokens"] += output_tokens
        current["completed_steps"] += 1
        current["latest_checkpoint_ts"] = max(
            float(current.get("latest_checkpoint_ts") or 0), ts,
        )

    empty = {
        "available": False,
        "output_tps": 0,
        "basis": "unavailable",
        "completed_steps": 0,
        "measured_output_tokens": 0,
        "measured_seconds": 0,
    }
    if not current:
        return empty
    started_ts = float(current.get("started_ts") or 0)
    checkpoint_ts = float(current.get("latest_checkpoint_ts") or 0)
    measured_seconds = checkpoint_ts - started_ts
    measured_output = int(current.get("output_tokens") or 0)
    completed_steps = int(current.get("completed_steps") or 0)
    if measured_seconds <= 0 or measured_output <= 0 or completed_steps <= 0:
        return empty
    return {
        "available": True,
        "output_tps": measured_output / measured_seconds,
        "basis": "live_end_to_end",
        "completed_steps": completed_steps,
        "measured_output_tokens": measured_output,
        "measured_seconds": measured_seconds,
    }


def _wait_sample(group, end_ts, duration_s, timing_basis, provider,
                 wall_duration_s=None, user_pause_s=0.0):
    """Return one completed prompt-to-response wall-clock sample."""
    if not group or duration_s <= 0:
        return None
    models = sorted(name for name in (group.get("models") or set()) if name)
    model = models[0] if len(models) == 1 else ("mixed" if models else "unknown")
    return {
        "provider": provider,
        "model": model,
        "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
        "ts": end_ts or 0,
        "start_ts": float(group.get("start_ts") or 0),
        "duration_s": float(duration_s),
        "tool_calls": int(group.get("tool_calls") or 0),
        "output_tokens": int(group.get("output_tokens") or 0),
        "timing_basis": timing_basis,
        "wall_duration_s": float(wall_duration_s if wall_duration_s is not None else duration_s),
        "user_pause_s": float(user_pause_s or 0),
    }


def claude_wait_samples(objs):
    """Return completed Claude prompt-to-response wait samples.

    Wait time is deliberately end to end: reasoning, tool use, and model output
    all count because the user is still waiting for the turn to finish.
    """
    samples = []
    current = None

    def close_turn(end_ts=0, duration_ms=0, allow_observed=False):
        nonlocal current
        group = current
        current = None
        if not group:
            return
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            basis = "reported"
        elif allow_observed:
            end_ts = end_ts or group.get("last_ts") or 0
            duration_s = float(end_ts or 0) - float(group.get("start_ts") or 0)
            basis = "observed"
        else:
            return
        wall_duration_s = duration_s
        duration_s, user_pause_s = _claude_effective_duration(group, duration_s, basis)
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "claude", wall_duration_s, user_pause_s)
        if sample:
            samples.append(sample)

    for obj in objs:
        otype = obj.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if _claude_user_prompt(obj):
            close_turn(allow_observed=bool(current and current.get("terminal")))
            current = {
                "start_ts": ts, "last_ts": ts, "models": set(),
                "tool_ids": set(), "tool_calls": 0, "output_tokens": 0,
                "seen_usage": set(), "terminal": False,
                "user_pause_starts": {}, "user_pause_s": 0.0,
            }
            continue
        if otype == "user" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            _close_claude_user_pauses(current, msg, ts)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            continue
        if otype == "assistant" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            model = msg.get("model")
            if model:
                current["models"].add(model)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            message_id = msg.get("id") or obj.get("uuid")
            if message_id not in current["seen_usage"]:
                current["seen_usage"].add(message_id)
                output_tokens, _ = _domain_normalize_reported_token_count(
                    (msg.get("usage") or {}).get("output_tokens")
                )
                current["output_tokens"] += output_tokens
            for block in msg.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id") or (block.get("name"), len(current["tool_ids"]))
                current["tool_ids"].add(tool_id)
                _track_claude_user_pause(current, block, ts)
            current["tool_calls"] = len(current["tool_ids"])
            if msg.get("stop_reason") and msg.get("stop_reason") != "tool_use":
                current["terminal"] = True
            continue
        if otype == "system" and obj.get("subtype") == "turn_duration":
            close_turn(ts, obj.get("durationMs"), allow_observed=True)
    if current and current.get("terminal"):
        close_turn(current.get("last_ts") or 0, allow_observed=True)
    return samples


def codex_wait_samples(objs, default_model=None):
    """Return completed Codex task prompt-to-response wait samples."""
    samples = []
    model = default_model or "unknown-model"
    current = None

    def close_task(end_ts=0, duration_ms=0, allow_observed=False):
        nonlocal current
        group = current
        current = None
        if not group:
            return
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            basis = "reported"
        elif allow_observed:
            end_ts = end_ts or group.get("last_ts") or 0
            duration_s = float(end_ts or 0) - float(group.get("start_ts") or 0)
            basis = "observed"
        else:
            return
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "codex")
        if sample:
            samples.append(sample)

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            if current:
                current["models"].add(model)
            continue
        if ptype == "task_started":
            close_task(allow_observed=False)
            current = {
                "start_ts": ts, "last_ts": ts, "models": {model},
                "tool_calls": 0, "output_tokens": 0,
            }
            continue
        if not current:
            continue
        current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            current["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if raw:
                current["output_tokens"] += usage_io_tokens(codex_usage(raw))[1]
            continue
        if ptype == "task_complete":
            close_task(ts, payload.get("duration_ms"), allow_observed=True)
    return samples


def wait_time_summary(samples):
    return _domain_wait_time_summary(samples)


def performance_summary(samples, total_output_tokens=0):
    return _domain_performance_summary(samples, total_output_tokens)


def codex_usage(raw):
    raw = raw or {}
    input_total, input_reported = _domain_normalize_reported_token_count(
        raw.get("input_tokens")
    )
    cached, cache_reported = _domain_normalize_reported_token_count(
        raw.get("cached_input_tokens", 0)
    )
    output_tokens, output_reported = _domain_normalize_reported_token_count(
        raw.get("output_tokens")
    )
    total_tokens, _ = _domain_normalize_reported_token_count(
        raw.get("total_tokens", 0)
    )
    input_available = input_reported and cache_reported and cached <= input_total
    reasoning_tokens, reasoning_available = _domain_normalize_reported_token_count(
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


def tool_identity(name):
    return _domain_tool_identity(name)


def trace_event(ts, kind, label, detail="", execution=None, tool=None, tokens=0, cost=0.0, severity="neutral", **meta):
    event = {
        "ts": ts or 0,
        "time": local_tm(ts),
        "local": local_dt(ts),
        "kind": kind,
        "label": label,
        "detail": detail,
        "execution": execution,
        "tool": tool,
        "tokens": int(tokens or 0),
        "cost": round(cost or 0.0, 6),
        "severity": severity,
    }
    for key, value in meta.items():
        if value is not None:
            event[key] = value
    return event


def trim_trace(trace):
    return trace[-TRACE_LIMIT:]


def compact_text(s, limit=72):
    s = " ".join((s or "").split())
    return s[:limit - 1] + "…" if len(s) > limit else s


def text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict):
                pieces.append(block.get("text") or block.get("content") or "")
        return " ".join(p for p in pieces if isinstance(p, str))
    return ""


def claude_user_text(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in (None, "text"):
            pieces.append(block.get("text") or block.get("content") or "")
    return " ".join(p for p in pieces if isinstance(p, str))


def frustration_term_counts(text, terms):
    """Return exact configured term hits using word-safe, case-insensitive matching."""
    text = str(text or "")
    counts = {}
    for term in terms or []:
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        matches = re.findall(rf"(?<!\w){escaped}(?!\w)", text, flags=re.IGNORECASE)
        if matches:
            counts[term] = len(matches)
    return counts


def week_start(day):
    if not day:
        return ""
    try:
        value = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        return ""
    return (value - datetime.timedelta(days=value.weekday())).isoformat()


def _claude_human_text(obj):
    """Return human-authored Claude text, None for tool/meta/user-shaped records."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return None
    if obj.get("sourceToolAssistantUUID") or obj.get("toolUseResult") is not None:
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):
        text_blocks = [
            block.get("text") or block.get("content") or ""
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        if not text_blocks and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content
        ):
            return None
        text = " ".join(value for value in text_blocks if isinstance(value, str))
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    stripped = text.strip()
    meta_prefixes = (
        "<local-command-caveat>", "<local-command-stdout>",
        "<command-name>", "<system-reminder>",
    )
    if stripped.startswith(meta_prefixes):
        return None
    return text


def _dedupe_user_turns(turns, window_seconds=2.0):
    result = []
    for turn in turns:
        fingerprint = " ".join(str(turn.get("text") or "").lower().split())
        previous = result[-1] if result else None
        if previous:
            previous_fingerprint = " ".join(str(previous.get("text") or "").lower().split())
            delta = abs(float(turn.get("ts") or 0) - float(previous.get("ts") or 0))
            if fingerprint == previous_fingerprint and delta <= window_seconds:
                continue
        result.append(turn)
    return result


def claude_user_turns(objs, default_model=None):
    turns = []
    pending = []
    current_model = default_model or "unknown-model"
    for obj in objs or []:
        text = _claude_human_text(obj)
        if text is not None:
            turn = {
                "ts": parse_iso(obj.get("timestamp", "")) or 0,
                "text": text,
                "model": None,
            }
            turns.append(turn)
            pending.append(turn)
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        model = msg.get("model") or current_model
        current_model = model
        if pending:
            for turn in pending:
                turn["model"] = model
            pending = []
    for turn in pending:
        turn["model"] = current_model
    return _dedupe_user_turns(turns)


def _codex_fallback_user_text(payload):
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    text = text_from_content(payload.get("content"))
    stripped = text.strip()
    if stripped.startswith(("# AGENTS.md instructions", "<environment_context>")):
        return None
    return text


def codex_user_turns(objs, default_model=None):
    """Prefer canonical user_message events; fall back for older Codex logs."""
    current_model = default_model or "unknown-model"
    event_turns = []
    fallback_turns = []
    for obj in objs or []:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "turn_context":
            current_model = payload.get("model") or current_model
            continue
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if payload.get("type") == "user_message":
            event_turns.append({"ts": ts, "text": payload.get("message") or "", "model": current_model})
            continue
        text = _codex_fallback_user_text(payload)
        if text is not None:
            fallback_turns.append({"ts": ts, "text": text, "model": current_model})
    return _dedupe_user_turns(event_turns or fallback_turns)


def cursor_user_turns(objs, default_model=None):
    """Extract human turns from Cursor's durable transcript without wrappers."""
    turns = []
    for row in objs or []:
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        text = text_from_content(cursor_message_content(row))
        match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, flags=re.DOTALL)
        turns.append({"ts": 0, "text": match.group(1) if match else text,
                      "model": default_model or "unknown"})
    return turns


def rollup_frustration_events(events):
    return _domain_rollup_language_signal_events(events)


def user_turns_for_provider(provider, objs, default_model=None):
    if provider == "codex":
        return codex_user_turns(objs, default_model)
    if provider == "cursor":
        return cursor_user_turns(objs, default_model)
    return claude_user_turns(objs, default_model)


def language_signal_events(turns, terms, default_model=None):
    events = []
    for turn in turns or []:
        ts = turn.get("ts") or 0
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        term_counts = frustration_term_counts(turn.get("text"), terms)
        events.append({
            "ts": ts,
            "day": day,
            "week": week_start(day),
            "model": turn.get("model") or default_model or "unknown",
            "utterance": bool(term_counts),
            "matches": sum(term_counts.values()),
            "term_counts": term_counts,
        })
    return events


def analyze_language_signal_turns(turns, terms=None, default_model=None):
    configured = language_signal_settings() if terms is None else terms
    rollups = {}
    events = {}
    for group in ("positive", "friction"):
        group_terms = list(configured.get(group) or [])
        group_events = language_signal_events(turns, group_terms, default_model)
        events[group] = group_events
        rollups[group] = rollup_frustration_events(group_events)
    return rollups, events


def analyze_language_signals(provider, objs, terms=None, default_model=None):
    turns = user_turns_for_provider(provider, objs, default_model)
    return analyze_language_signal_turns(turns, terms, default_model)


def attach_language_signals(row, rollups, events):
    row["language_signals"] = rollups
    row["_language_signal_events"] = events
    row["frustration"] = rollups.get("friction") or rollup_frustration_events([])
    row["_frustration_events"] = events.get("friction") or []
    return row


def analyze_frustration(provider, objs, terms=None, default_model=None):
    """Backward-compatible Friction-only lexical analysis."""
    configured = list(frustration_settings()["terms"] if terms is None else terms)
    turns = user_turns_for_provider(provider, objs, default_model)
    events = language_signal_events(turns, configured, default_model)
    return rollup_frustration_events(events), events


def user_prompt_preview(texts, limit=220):
    seen = set()
    unique = []
    for text in texts:
        key = " ".join((text or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return compact_text(" / ".join(unique), limit)


def execution_low_yield(execution):
    return _domain_execution_low_yield(execution)


def low_yield_should_warn(executions, context_pct=0):
    return _domain_low_yield_should_warn(executions, context_pct)


def is_operational_warning(insight):
    return _domain_is_operational_warning(insight)


INSIGHT_CATEGORY_ORDER = {
    "Context": 0,
    "Yield": 1,
    "Spend": 2,
    "Tools": 3,
    "Cache": 4,
    "Reasoning": 5,
    "Flow": 6,
    "Pricing": 7,
}
INSIGHT_KIND_SCORE = {"warn": 0, "good": 1, "neutral": 2}


def insight(key, kind, category, title, text, detail="", action="", priority=50):
    return _domain_insight(key, kind, category, title, text, detail, action, priority)


def insight_sort_key(row):
    return _domain_insight_sort_key(row)


def normalize_insights(rows, limit=12):
    return _domain_normalize_insights(rows, limit)


def claude_user_events(objs):
    events = []
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        txt = compact_text(claude_user_text(msg), 220)
        if txt:
            events.append({"ts": parse_iso(obj.get("timestamp", "")) or 0, "text": txt})
    return sorted(events, key=lambda e: e["ts"])


def tool_summary(executions):
    return _domain_tool_summary(executions)


def iter_claude_messages(objs):
    return list(_claude_native_adapter().logical_messages(
        objs, timestamp_parser=parse_iso,
    ))


def tool_result_is_error(value, explicit=False):
    if explicit:
        return True
    if isinstance(value, dict):
        if value.get("is_error") is True or value.get("success") is False:
            return True
        status = str(value.get("status") or "").lower()
        if status in ("error", "failed", "failure"):
            return True
        exit_code = value.get("exit_code", value.get("exitCode"))
        if exit_code not in (None, 0, "0"):
            return True
        if value.get("error") not in (None, "", False):
            return True
        return any(tool_result_is_error(v) for v in value.values() if isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(tool_result_is_error(v) for v in value)
    text = str(value or "").strip().lower()
    return text.startswith(("error:", "failed:", "tool error", "process exited with code"))


def observable_output_chars(value):
    """Count trace-visible text while excluding embedded image/base64 bytes."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value or "")
    text = DATA_URL_RE.sub("<image-data>", text)
    text = BASE64_FIELD_RE.sub(r'\1<binary-data>\3', text)
    return len(text)


def argument_fingerprint(value):
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value or "")
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16] if text else ""


_SKILL_MEASURABLE_KEYS = frozenset({
    "allowed-tools", "tools", "tool-schemas", "mcp-servers", "mcpservers",
    "mcp", "requires-mcp", "requiresmcp",
})
_SKILL_EMPTY_CAPABILITY_VALUES = frozenset({"", "false", "null", "none", "~"})


def _skill_frontmatter_key(value):
    return str(value or "").strip().casefold().replace("_", "-")


def _skill_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(65536)
    except OSError:
        return {}
    lines = raw.splitlines()
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}
    closing = next((idx for idx, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        return {}
    fm = {}
    current_key = None
    for raw_line in lines[1:closing]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line[:1].isspace():
            if current_key and fm.get(current_key) == "":
                fm[current_key] = "<nested-value>"
            continue
        m = re.match(r"^([A-Za-z0-9_.@+-]+)\s*:\s*(.*)", raw_line.rstrip())
        if m:
            key = m.group(1).strip()
            val = re.sub(r"\s+#.*$", "", m.group(2)).strip()
            normalized = val.casefold()
            if normalized == "true":
                fm[key] = True
            elif normalized == "false":
                fm[key] = False
            else:
                fm[key] = val
            current_key = key
        else:
            current_key = None
    return fm


def _skill_capability_value_is_nonempty(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    if text.casefold() in _SKILL_EMPTY_CAPABILITY_VALUES:
        return False
    if re.fullmatch(r"\[\s*\]|\{\s*\}", text):
        return False
    return True


def _skill_measurability(path):
    """Classify whether trace evidence can observe this skill's capabilities."""
    fm = _skill_frontmatter(path)
    capability_values = [
        value for key, value in fm.items()
        if _skill_frontmatter_key(key) in _SKILL_MEASURABLE_KEYS
    ]
    if not capability_values:
        return "unknown"
    if any(_skill_capability_value_is_nonempty(value) for value in capability_values):
        return "measurable"
    return "instruction"


def _skill_has_measurable_capabilities(path):
    return _skill_measurability(path) == "measurable"


def skill_names_from_value(value, tool_name=""):
    """Infer path-based loads and explicit native Skill-tool activations."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value or "")
    names = {match.group(1) for match in SKILL_PATH_RE.finditer(text)}
    tool_leaf = str(tool_name or "").rsplit(".", 1)[-1].casefold()
    direct = value.get("skill") if tool_leaf == "skill" and isinstance(value, dict) else None
    if isinstance(direct, str):
        direct = direct.strip()
        if SKILL_NAME_RE.fullmatch(direct):
            names.add(direct.rsplit(":", 1)[-1])
    return sorted(names)


def claude_tool_results(objs):
    chars_by_id = defaultdict(int)
    ts_by_id = {}
    errors_by_id = defaultdict(bool)
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                chars_by_id[tid] += observable_output_chars(block.get("content", ""))
                ts_by_id[tid] = parse_iso(obj.get("timestamp", ""))
                errors_by_id[tid] = errors_by_id[tid] or tool_result_is_error(
                    block.get("content"), block.get("is_error") is True
                )
    return chars_by_id, ts_by_id, errors_by_id


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
}
CURSOR_TOOL_ALIASES = {
    "read": "read_file_v2", "readfile": "read_file_v2", "read_file": "read_file_v2",
    "grep": "ripgrep_raw_search", "rg": "ripgrep_raw_search",
    "glob": "glob_file_search", "shell": "run_terminal_command_v2",
    "edit": "edit_file_v2", "applypatch": "apply_patch",
    "todowrite": "todo_write", "websearch": "web_search", "webfetch": "web_fetch",
    "delete": "delete_file", "deletefile": "delete_file",
}


def cursor_tool_identity(name):
    raw = str(name or "?")
    alias = re.sub(r"[^a-z0-9_]", "", raw.lower())
    canonical = CURSOR_TOOL_ALIASES.get(alias, raw)
    display, namespace = CURSOR_TOOL_IDENTITIES.get(canonical, (canonical, "cursor"))
    return {"name": canonical, "display": display, "namespace": namespace, "kind": "tool"}


def cursor_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return float(parse_iso(value) or 0)
    return 0.0


def cursor_message_content(row):
    message = row.get("message") if isinstance(row, dict) else {}
    content = message.get("content") if isinstance(message, dict) else []
    return content if isinstance(content, list) else []


def cursor_transcript_groups(rows, source):
    """Reconstruct coarse turns when Cursor SQLite enrichment is unavailable."""
    groups = []
    current = None
    for row in rows or []:
        role = row.get("role") if isinstance(row, dict) else None
        if role == "user":
            if current:
                groups.append(current)
            user_text = text_from_content(cursor_message_content(row))
            query = re.search(r"<user_query>\s*(.*?)\s*</user_query>", user_text, flags=re.DOTALL)
            current = {
                "user_text": query.group(1) if query else user_text,
                "model": source.get("model") or "unknown", "bubbles": [],
                "completed": False,
            }
            continue
        if role == "assistant":
            if current is None:
                current = {"user_text": "", "model": source.get("model") or "unknown",
                           "bubbles": [], "completed": False}
            current["bubbles"].append({"type": 2, "content": cursor_message_content(row)})
            continue
        if row.get("type") == "turn_ended" and current:
            current["completed"] = row.get("status") in (None, "success", "completed")
            groups.append(current)
            current = None
    if current:
        groups.append(current)
    return groups


def cursor_enriched_groups(snapshot):
    groups = []
    current = None
    for bubble in snapshot.get("bubbles") or []:
        if bubble.get("type") == 1:
            if current:
                groups.append(current)
            model_info = bubble.get("modelInfo") if isinstance(bubble.get("modelInfo"), dict) else {}
            current = {
                "user_text": str(bubble.get("text") or ""),
                "model": model_info.get("modelName") or
                         cursor_model(snapshot.get("composer") or {}, snapshot.get("header") or {}),
                "request_id": bubble.get("requestId") or "",
                "start_ts": cursor_timestamp(bubble.get("createdAt")),
                "context": bubble.get("contextWindowStatusAtCreation")
                           if isinstance(bubble.get("contextWindowStatusAtCreation"), dict) else {},
                "bubbles": [], "completed": False,
            }
            continue
        if bubble.get("type") != 2:
            continue
        if current is None:
            current = {
                "user_text": "", "model": cursor_model(
                    snapshot.get("composer") or {}, snapshot.get("header") or {}
                ), "request_id": "", "start_ts": 0, "context": {}, "bubbles": [],
                "completed": False,
            }
        current["bubbles"].append(bubble)
        if bubble.get("turnDurationMs") is not None:
            current["completed"] = True
            current["turn_duration_ms"] = bubble.get("turnDurationMs")
    if current:
        groups.append(current)
    return groups


def cursor_turn_timing(spans, start_ts, next_start_ts=0, terminal_ts=0, turn_duration_ms=0):
    boundary = float(next_start_ts or (terminal_ts + 24 * 60 * 60) or float("inf"))
    matches = [
        row for row in spans or []
        if row.get("end_ts", 0) >= float(start_ts or 0) - 1
        and row.get("start_ts", 0) < boundary
        and row.get("end_ts", 0) <= boundary + 1
    ]
    submits = [row for row in matches if row.get("name") == "ComposerChatService.submitChatMaybeAbortCurrent"]
    attempts = [row for row in matches if row.get("name") == "agent.request.attempt"]
    rpc_errors = {
        row.get("request_id") for row in matches
        if row.get("name") == "rpc.run" and row.get("error") and row.get("request_id")
    }
    ttfts = [row for row in matches if row.get("name") == "client.ttft" and row.get("duration_s", 0) > 0]
    final_ts = max((row["end_ts"] for row in submits), default=float(terminal_ts or 0))
    intervals = [(row["start_ts"], row["end_ts"]) for row in attempts]
    try:
        fallback_s = max(0.0, float(turn_duration_ms or 0) / 1000.0)
    except (TypeError, ValueError):
        fallback_s = 0.0
    if not intervals and fallback_s and terminal_ts:
        intervals = [(max(0.0, float(terminal_ts) - fallback_s), float(terminal_ts))]
    active_s = _merge_execution_intervals(intervals)
    if final_ts and start_ts and final_ts >= start_ts:
        wait_s = final_ts - start_ts
    else:
        wait_s = fallback_s
        final_ts = float(terminal_ts or (start_ts + fallback_s if start_ts else 0))
    failed = sum(bool(row.get("error") or row.get("request_id") in rpc_errors) for row in attempts)
    return {
        "end_ts": final_ts,
        "wait_s": max(0.0, wait_s),
        "active_s": max(0.0, active_s),
        "active_intervals": intervals,
        "timing_basis": "request_trace" if (submits or attempts) else
                        ("turn_duration" if fallback_s else "unavailable"),
        "ttft_s": min((row["duration_s"] for row in ttfts), default=0.0),
        "attempts": len(attempts),
        "failed_attempts": failed,
        "retries": max(0, len(attempts) - 1),
    }


def cursor_prompt_breakdown(composer):
    raw = composer.get("promptTokenBreakdown") if isinstance(composer, dict) else {}
    categories = raw.get("categories") if isinstance(raw, dict) else []
    return [
        {
            "id": str(row.get("id") or "unknown"),
            "label": str(row.get("label") or row.get("id") or "Unknown"),
            "estimated_tokens": int(row.get("estimatedTokens") or 0),
        }
        for row in (categories or []) if isinstance(row, dict)
    ]


def cursor_context_estimates(groups, latest_tokens=0, latest_window=0):
    """Fill sparse Cursor context checkpoints without calling them billable input."""
    rows = []
    for group in groups or []:
        context = group.get("context") if isinstance(group.get("context"), dict) else {}
        rows.append({
            "tokens": max(0, int(context.get("tokensUsed") or 0)),
            "window": max(0, int(context.get("tokenLimit") or 0)),
            "interpolated": False,
        })
    if not rows:
        return rows
    if latest_tokens:
        rows[-1]["tokens"] = max(0, int(latest_tokens))
    if latest_window:
        rows[-1]["window"] = max(0, int(latest_window))

    known_tokens = [index for index, row in enumerate(rows) if row["tokens"] > 0]
    for index, row in enumerate(rows):
        if row["tokens"] > 0:
            continue
        previous = max((known for known in known_tokens if known < index), default=None)
        following = min((known for known in known_tokens if known > index), default=None)
        if following is not None and previous is not None:
            span = following - previous
            share = (index - previous) / span
            start = rows[previous]["tokens"]
            row["tokens"] = max(1, round(start + (rows[following]["tokens"] - start) * share))
        elif following is not None:
            row["tokens"] = max(1, round(rows[following]["tokens"] * (index + 1) / (following + 1)))
        elif previous is not None:
            row["tokens"] = rows[previous]["tokens"]
        row["interpolated"] = bool(row["tokens"])

    known_windows = [index for index, row in enumerate(rows) if row["window"] > 0]
    for index, row in enumerate(rows):
        if row["window"] > 0:
            continue
        nearest = min(known_windows, key=lambda known: abs(known - index)) if known_windows else None
        if nearest is not None:
            row["window"] = rows[nearest]["window"]
    return rows


def cursor_visible_output(bubbles):
    """Estimate only model-authored text that Cursor persisted in its bubbles."""
    assistant_chars = 0
    reasoning_chars = 0
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        seen = set()
        text = bubble.get("text")
        if isinstance(text, str) and text.strip():
            seen.add(text)
            assistant_chars += len(text)
        for block in bubble.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            value = block.get("text")
            if isinstance(value, str) and value.strip() and value not in seen:
                seen.add(value)
                assistant_chars += len(value)
        thinking = bubble.get("thinking")
        if isinstance(thinking, str) and thinking.strip() and thinking not in seen:
            reasoning_chars += len(thinking)
    return {
        "assistant_chars": assistant_chars,
        "reasoning_chars": reasoning_chars,
        "assistant_tokens": math.ceil(assistant_chars / CHARS_PER_TOKEN) if assistant_chars else 0,
        "reasoning_tokens": math.ceil(reasoning_chars / CHARS_PER_TOKEN) if reasoning_chars else 0,
    }


def cursor_model_call_count(bubbles):
    call_ids = set()
    has_model_activity = False
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        tool_data = bubble.get("toolFormerData")
        if isinstance(tool_data, dict):
            call_id = tool_data.get("modelCallId")
            if call_id:
                call_ids.add(str(call_id))
            has_model_activity = has_model_activity or bool(tool_data.get("name"))
        has_model_activity = has_model_activity or bool(str(bubble.get("text") or "").strip())
        has_model_activity = has_model_activity or any(
            isinstance(block, dict) and block.get("type") in ("text", "tool_use")
            for block in (bubble.get("content") or [])
        )
    return max(len(call_ids), int(has_model_activity))


def cursor_pricing_note(model, variant, supported):
    basis = "one context snapshot per execution plus trace-visible model text"
    if not supported:
        return f"Local Cursor token estimate ({basis}); no configured public rate for {model}."
    rate = "selected-model public API rates"
    for cursor_model_id in CURSOR_VARIANT_MODEL_IDS:
        if _catalog_model_alias_matches(model, "cursor", cursor_model_id):
            rate = f"{cursor_model_id.replace('-', ' ').title()} {variant.title()} public rates"
            break
    return f"Local Cursor estimate ({basis}), priced with {rate}; cache and hidden model work are excluded."


def recompute_cursor(source):
    return _cursor_native_adapter().recompute_legacy(source)


def _opencode_int(value, default=0):
    return _native_opencode_int(value, default)


def _opencode_usage(data):
    """Return OpenCode's five independent token components."""
    return _native_opencode_usage(data)


def _opencode_context_tokens(usage):
    return _native_opencode_context_tokens(usage)


def _opencode_reported_cost(data):
    """Return (cost, available); a reported numeric zero remains available."""
    return _native_opencode_reported_cost(data)


# OpenCode records an authoritative total cost per message from its own
# provider. No public rate table can cover arbitrary OpenCode backends, so
# Token Meter distributes that exact total across the input / cache-write /
# cache-read / output buckets using a documented token-weighted proxy that
# preserves the authoritative total. The proxy does not invent cost.
def _opencode_distribute(msg_cost, usage):
    """Split one authoritative OpenCode-reported cost into five proxy buckets.

    OpenCode records an authoritative total cost per message from its own
    provider. No bundled rate table can cover arbitrary OpenCode backends, so
    Token Meter distributes that exact total across the five independent token
    components with a documented token-weighted proxy. The total is never
    invented; only its category split is estimated.
    """
    return _native_opencode_distribute(msg_cost, usage)


def _opencode_display_cost(breakdown):
    """Fold reasoning into output for Token Meter's four-bucket UI contract."""
    return _native_opencode_display_cost(breakdown)


def recompute_opencode(source):
    return _opencode_native_adapter().recompute_legacy(source)


def recompute_kiro(source):
    return _kiro_native_adapter().recompute_legacy(source)


_RUNTIME_REGISTRY = None
_runtime_registry_lock = threading.Lock()


def runtime_registry():
    """Return the explicit ordered runtime registry used by the legacy facade."""
    global _RUNTIME_REGISTRY
    if _RUNTIME_REGISTRY is not None:
        return _RUNTIME_REGISTRY
    with _runtime_registry_lock:
        if _RUNTIME_REGISTRY is None:
            _RUNTIME_REGISTRY = RuntimeRegistry((
                ClaudeRuntimeAdapterProxy(lambda: _claude_native_adapter()),
                CodexRuntimeAdapterProxy(lambda: _codex_native_adapter()),
                CursorRuntimeAdapterProxy(lambda: _cursor_native_adapter()),
                OpenCodeRuntimeAdapterProxy(lambda: _opencode_native_adapter()),
                KiroRuntimeAdapterProxy(lambda: _kiro_native_adapter()),
                PiRuntimeAdapterProxy(lambda: _pi_native_adapter()),
                HermesRuntimeAdapterProxy(lambda: _hermes_native_adapter()),
                GrokRuntimeAdapterProxy(lambda: _grok_native_adapter()),
            ))
    return _RUNTIME_REGISTRY


def recompute(source):
    global _RUNTIME_LOAD_FAILURE
    if isinstance(source, str):
        source = source_from_path(source)
    if not source:
        return None
    runtime_id = source.get("provider")
    registry = runtime_registry()
    adapter = registry.get(runtime_id)
    if adapter is None:
        return None
    try:
        envelope = legacy_source_to_envelope(
            source,
            account_provider_id=adapter.descriptor.account_provider_id,
        )
    except (TypeError, ValueError):
        _RUNTIME_LOAD_FAILURE = AdapterFailure(
            runtime_id=str(runtime_id or "unknown"),
            operation="load",
            code="invalid_source",
        )
        return None
    result = registry.load_legacy_for(
        envelope.normalized.runtime_id,
        envelope_to_legacy_source(envelope),
        DetailLevel.FULL,
    )
    _RUNTIME_LOAD_FAILURE = result.failure
    return result.value


def source_revision_signature(source):
    """Track trace activity plus display metadata stored outside the trace."""
    if not source:
        return None
    path = str(source.get("path") or "")
    identity = public_session_model_identity(source.get("model_identity")) or {}
    return (
        float(source.get("signature_mtime") or source.get("mtime") or safe_mtime(path)),
        str(source.get("title") or ""),
        str(source.get("request_revision") or ""),
        tuple(source.get("lineage_revision") or ()),
        str(source.get("model") or ""),
        str(source.get("model_provider") or ""),
        json.dumps(identity, sort_keys=True, separators=(",", ":")),
    )


def session_state_signature(source):
    """Return a bounded detailed-state cache key for one discovered session."""
    if not source:
        return None
    path = str(source.get("path") or "")
    return (
        str(source.get("provider") or ""),
        str(source.get("id") or ""),
        path,
        source_revision_signature(source),
        file_signature(path),
    )


def cached_session_state(source):
    """Recompute detailed state only when the selected session evidence changes."""
    if not source:
        return None
    signature = session_state_signature(source)
    cache_key = str(source.get("path") or source.get("id") or "")
    with _session_state_cache_lock:
        cached = _session_state_cache.get(cache_key)
        if cached and cached.get("signature") == signature:
            return copy.deepcopy(cached.get("state"))

    state = recompute(source)
    if state is None:
        return None
    with _session_state_cache_lock:
        _session_state_cache[cache_key] = {
            "signature": signature,
            "state": copy.deepcopy(state),
            "at": time.time(),
        }
        if len(_session_state_cache) > SESSION_STATE_CACHE_LIMIT:
            oldest = sorted(
                _session_state_cache,
                key=lambda key: _session_state_cache[key].get("at") or 0,
            )[:len(_session_state_cache) - SESSION_STATE_CACHE_LIMIT]
            for key in oldest:
                _session_state_cache.pop(key, None)
    return copy.deepcopy(state)


def recompute_claude(source):
    return _claude_native_adapter().recompute_legacy(source)


def analysis_block(tot, total_cost, think_out, think_turns, think_cost, model_tok, model_cost,
                   tool_data, side_cost, side_turns, completed):
    tool_bloat = [{
        "name": row["name"],
        "calls": row["calls"],
        "tokens": row["output_tokens"],
        "chars": row["output_chars"],
        "namespace": row["namespace"],
        "kind": row["kind"],
    } for row in tool_data["by_name"][:8]]
    return {
        "reasoning": {
            "share": (think_out / tot["output"]) if tot["output"] else 0.0,
            "think_turns": think_turns,
            "tokens": think_out,
            "cost": think_cost,
        },
        "model_mix": sorted(
            [{"model": k, "tokens": model_tok[k], "cost": model_cost[k]} for k in model_tok],
            key=lambda x: -x["cost"]),
        "tool_bloat": tool_bloat,
        "coordination": {
            "share": (side_cost / total_cost) if total_cost else 0.0,
            "turns": side_turns,
            "cost": side_cost,
        },
        "cost_per_task": {
            "completed": completed,
            "per_task": (total_cost / completed) if completed else 0.0,
        },
    }


def new_codex_pending():
    return {"trace": [], "calls": {}, "has_reasoning": False, "start_ts": None,
            "context_window": None, "user_inputs": []}


def codex_approval_policy_label(value):
    """Return a compact label for legacy strings and structured Codex policies."""
    if isinstance(value, dict):
        value = next(iter(value), "")
    return str(value or "").replace("_", " ")


def recompute_codex(source):
    return _codex_native_adapter().recompute_legacy(source)


def build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                primary_model, pricing_note, active_timing=None, wait_samples=None,
                availability=None):
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    active_timing = active_timing or {}
    wait_samples = wait_samples or []
    wait_time = wait_time_summary(wait_samples)
    active_seconds = float(active_timing.get("duration_s") or 0)
    active_available = bool(active_timing.get("available") and active_seconds > 0)
    minutes = max(active_seconds / 60.0, 1e-9)
    cache_in = tot["cache_read"] + tot["cache_write"]
    cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
    cache = cache_block(
        tot, cost, executions, source["provider"], primary_model,
        savings_available=source.get("cache_savings_available") is not False,
    )
    tool_data = tool_summary(executions)
    model_identity = public_session_model_identity(source.get("model_identity"))
    context_window = (source.get("context_window") or
                      max((e.get("context_window") or 0 for e in executions), default=0) or None)
    context_peak = max(
        int(source.get("context_latest") or 0),
        max((e.get("context_tokens") or e.get("tokens", {}).get("input", 0)
             for e in executions), default=0),
    )
    context_latest = int(source.get("context_latest") or
                         (executions[-1].get("context_tokens", 0) if executions else 0))
    context_pct = (context_latest / context_window) if context_window else None
    context_peak_pct = (context_peak / context_window) if context_window else None
    tools_loaded = int(source.get("tools_loaded") or 0)
    loaded_known = bool(tools_loaded)
    if not tools_loaded:
        tools_loaded = tool_data["unique_used"]
    tool_catalog = list(source.get("tool_catalog") or [])[:240]
    counts = catalog_counts(tool_catalog)
    advertised = counts["advertised"] if loaded_known else 0
    eager = int(source.get("tools_eager") or counts["eager"] or 0)
    deferred = int(source.get("tools_deferred") or counts["deferred"] or 0)
    catalog_names = {row.get("name") for row in tool_catalog if row.get("name")}
    used_names = {row.get("name") for row in tool_data.get("by_name", []) if row.get("name")}
    catalog_coverage = "unavailable"
    if loaded_known:
        catalog_coverage = "reported" if used_names.issubset(catalog_names) else "partial"
    tool_data["loaded"] = tools_loaded
    tool_data["loaded_known"] = loaded_known
    tool_data["advertised"] = advertised
    tool_data["eager"] = eager
    tool_data["deferred"] = deferred
    tool_data["catalog_coverage"] = catalog_coverage
    tool_data["loaded_namespaces"] = list(source.get("tool_namespaces") or [])
    tool_data["catalog"] = tool_catalog[:80]
    availability = availability or metric_availability(
        source["provider"], context=bool(context_window),
        timing=bool(active_available or wait_samples),
        tool_results=True,
    )
    if availability.get("cost") is False:
        for field in ("saved", "cost", "read_cost", "write_cost"):
            cache[field] = None
    cache["available"] = bool(availability.get("cache"))
    tool_data["results_available"] = bool(availability.get("tool_results"))
    insights = enrich_insights(insights, executions, tool_data, context_window, context_latest, context_peak,
                               source["provider"])
    source_obj = {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "runtime": source_runtime_label(source),
        "label": source["label"],
        "id": source["id"],
        "desktop_session_id": source.get("desktop_session_id"),
        "path": source["path"],
        "project": source.get("project") or "",
        "pricing_note": pricing_note,
        **({"model_identity": model_identity} if model_identity else {}),
        "approximate_cost": bool(approx_cost),
        "token_estimate": bool(source.get("token_estimate")),
        "estimate_basis": source.get("estimate_basis") or "",
        "pricing_variant": source.get("pricing_variant") or "",
        "tools_loaded": tools_loaded,
        "tools_loaded_known": loaded_known,
        "tools_advertised": advertised,
        "tools_eager": eager,
        "tools_deferred": deferred,
        "tool_catalog_coverage": catalog_coverage,
        "availability": availability,
    }
    return {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "source": source_obj,
        "availability": availability,
        "session": source["session"],
        "project": source.get("project") or "",
        "tokens": tot,
        "cost": cost,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "cost_approx": bool(approx_cost),
        "primary_model": primary_model,
        "turns": len(series),
        "subagent_turns": side_turns,
        "cache_ratio": cache_ratio,
        "cache_saved": cache["saved"],
        "cache": cache,
        "burn_tok_min": total_tokens / minutes if active_available else 0,
        "burn_usd_min": total_cost / minutes if active_available else 0,
        "timing": {
            "start_ts": first_ts or 0,
            "end_ts": last_ts or 0,
            "start_local": local_dt(first_ts),
            "end_local": local_dt(last_ts),
            "duration_s": int(round(active_seconds)),
            "duration": duration_label(active_seconds) if active_available else "--",
            "duration_available": active_available,
            "duration_basis": active_timing.get("basis") or "unavailable",
            "execution_count": int(active_timing.get("execution_count") or 0),
            "reported_executions": int(active_timing.get("reported_executions") or 0),
            "observed_executions": int(active_timing.get("observed_executions") or 0),
            "wall_duration_s": int(elapsed),
            "timezone": time.tzname[time.localtime().tm_isdst > 0],
            "end_label": "Last activity",
        },
        "wait_time": {
            **wait_time,
            "samples": [
                {
                    "i": index,
                    "duration_s": row["duration_s"],
                    "model": row.get("model") or "unknown",
                    "tool_calls": int(row.get("tool_calls") or 0),
                    "model_calls": int(row.get("model_calls") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "context_tokens": int(row.get("context_tokens") or 0),
                    "timing_basis": row.get("timing_basis") or "observed",
                    "ts": row.get("ts") or 0,
                    "ttft_s": float(row.get("ttft_s") or 0),
                    "attempts": int(row.get("attempts") or 0),
                    "failed_attempts": int(row.get("failed_attempts") or 0),
                    "retries": int(row.get("retries") or 0),
                }
                for index, row in enumerate(wait_samples, 1)
            ],
        },
        "context": {
            "window": context_window,
            "latest": context_latest,
            "peak": context_peak,
            "latest_pct": context_pct,
            "peak_pct": context_peak_pct,
        },
        "elapsed_s": int(elapsed),
        "active_elapsed_s": int(round(active_seconds)),
        "idle_s": int(idle),
        "idle": idle > 90,
        "ended": False,
        "biggest_turn": biggest,
        "last_turn_cost": series[-1]["cost"] if series else 0,
        "series": series,
        "chart": {"series": series, "scale_hint": "linear"},
        "executions": executions[-EXEC_LIMIT:],
        "trace": trim_trace(sorted(trace, key=lambda e: (e.get("ts") or 0, e.get("execution") or 0))),
        "trace_truncated": len(trace) > TRACE_LIMIT,
        "tools": tool_data,
        "semantic": semantic,
        "analyses": analyses,
        "insights": insights,
        "ts": time.strftime("%H:%M:%S"),
    }


def enrich_insights(insights, executions, tool_data, context_window, context_latest,
                    context_peak, provider):
    return _domain_enrich_insights(
        insights,
        executions,
        tool_data,
        context_window,
        context_latest,
        context_peak,
        provider,
    )


def cache_savings(tot, provider, model, executions=None):
    if executions:
        saved = 0.0
        for execution in executions:
            tokens = execution.get("tokens") or {}
            cache_read = int(tokens.get("cache_read", 0) or 0)
            if not cache_read:
                continue
            execution_model = execution.get("model") or model
            variant = execution.get("pricing_variant")
            at = execution.get("ts") or None
            usage = {
                "input_tokens": int(tokens.get("fresh_input", 0) or 0),
                "cache_creation_input_tokens": int(tokens.get("cache_write", 0) or 0),
                "cache_read_input_tokens": cache_read,
                "output_tokens": int(tokens.get("output", 0) or 0),
            }
            p, _ = price_for(execution_model, provider, variant, at=at)
            input_multiplier, _ = _price_multipliers(usage, execution_model, provider, at)
            saved += _domain_cache_savings_for_rate(
                cache_read,
                p["input"],
                p["cache_read"],
                input_multiplier=input_multiplier,
            )
        return saved
    p, _ = price_for(model, provider)
    return _domain_cache_savings_for_rate(
        tot["cache_read"], p["input"], p["cache_read"]
    )


def cache_block(tot, cost, executions, provider, model, savings_available=True):
    fresh = int(tot.get("input", 0) or 0)
    read = int(tot.get("cache_read", 0) or 0)
    write = int(tot.get("cache_write", 0) or 0)
    latest_tokens = (executions[-1].get("tokens") if executions else {}) or {}
    latest_input = int(latest_tokens.get("input", 0) or 0)
    latest_cache = int(latest_tokens.get("cache", 0) or 0)
    latest_read = int(latest_tokens.get("cache_read", latest_cache) or 0)
    latest_write = int(latest_tokens.get("cache_write", 0) or 0)
    result = _domain_cache_metrics(
        fresh=fresh,
        read=read,
        write=write,
        read_cost=cost.get("cache_read", 0.0),
        write_cost=cost.get("cache_write", 0.0),
        saved=cache_savings(tot, provider, model, executions) if savings_available else 0.0,
        latest_input=latest_input,
        latest_cache=latest_cache,
        latest_read=latest_read,
        latest_write=latest_write,
    )
    result["savings_available"] = bool(savings_available)
    for target, source in (
        ("write_5m", "cache_write_5m"),
        ("write_1h", "cache_write_1h"),
        ("write_unspecified", "cache_write_unspecified"),
    ):
        if source in tot:
            result[target] = int(tot.get(source) or 0)
    for target, source in (
        ("write_5m", "cache_write_5m"),
        ("write_1h", "cache_write_1h"),
        ("write_unspecified", "cache_write_unspecified"),
    ):
        if source in latest_tokens:
            result["latest"][target] = int(latest_tokens.get(source) or 0)
    if not savings_available:
        result["saved"] = None
    return result


def build_insights(tot, cost, total_cost, cache_ratio, biggest, n_turns, an,
                   provider, model, cost_approx, executions=None):
    return _domain_build_cost_insights(
        tot,
        cost,
        total_cost,
        cache_ratio,
        biggest,
        n_turns,
        an,
        model,
        cost_approx,
        cache_saved=cache_savings(tot, provider, model, executions),
    )


def summarize_tool_evidence(calls, catalog=None):
    return _domain_summarize_tool_evidence(calls, catalog)


def claude_tool_call_evidence(objs, msgs=None):
    msgs = msgs if msgs is not None else iter_claude_messages(objs)
    result_chars, result_ts, result_errors = claude_tool_results(objs)
    calls = []
    for rec in msgs:
        for block in rec.get("content") or []:
            if block.get("type") != "tool_use":
                continue
            ident = tool_identity(block.get("name") or "?")
            tid = block.get("id")
            calls.append({
                **ident,
                "output_tokens": int(result_chars.get(tid, 0)) // CHARS_PER_TOKEN,
                "error": bool(result_errors.get(tid)),
                "ts": result_ts.get(tid) or rec.get("ts") or 0,
                "args_fingerprint": argument_fingerprint(block.get("input")),
                "skills": skill_names_from_value(block.get("input"), block.get("name")),
            })
    return calls


def codex_tool_call_evidence(objs):
    calls = {}
    order = []
    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            name = payload.get("name") or ("web.search" if ptype == "web_search_call" else ptype.replace("_call", ""))
            call_id = payload.get("call_id") or payload.get("id") or f"call-{len(order) + 1}"
            if call_id not in calls:
                arguments = payload.get("arguments") or payload.get("input")
                calls[call_id] = {
                    **tool_identity(name), "output_chars": 0, "output_tokens": 0,
                    "error": False, "ts": ts,
                    "args_fingerprint": argument_fingerprint(arguments),
                    "skills": skill_names_from_value(arguments, name),
                }
                order.append(call_id)
            continue
        if ptype not in ("function_call_output", "custom_tool_call_output", "web_search_end", "tool_search_output", "patch_apply_end"):
            continue
        call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
        if call_id not in calls:
            name = payload.get("name") or ptype
            calls[call_id] = {
                **tool_identity(name), "output_chars": 0, "output_tokens": 0,
                "error": False, "ts": ts, "args_fingerprint": "", "skills": [],
            }
            order.append(call_id)
        row = calls[call_id]
        output = payload.get("output") if "output" in payload else payload
        row["output_chars"] += observable_output_chars(output)
        row["output_tokens"] = row["output_chars"] // CHARS_PER_TOKEN
        row["error"] = bool(row.get("error") or tool_result_is_error(output, payload.get("status") == "failed"))
        row["ts"] = ts or row.get("ts") or 0
    return [calls[call_id] for call_id in order]


def claude_summary(source, objs):
    return _claude_native_adapter().summarize_legacy(source, objs)


def codex_summary(source, objs):
    return _codex_native_adapter().summarize_legacy(source, objs)


def summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                active_timing=None, input_tokens=0, output_tokens=0, model_stats=None,
                model_daily=None, performance_samples=None, wait_samples=None,
                availability=None, session_name=None):
    active_timing = active_timing or {}
    model_stats = model_stats or {}
    model_daily = model_daily or []
    performance_samples = performance_samples or []
    wait_samples = wait_samples or []
    availability = availability or metric_availability(source.get("provider"))
    model_identity = public_session_model_identity(source.get("model_identity"))
    wall_duration = (last_ts - first_ts) if (first_ts and last_ts) else 0
    row = {
        "id": source["id"],
        "path": source["path"],
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "runtime": source_runtime_label(source),
        "label": source["label"],
        "desktop_session_id": source.get("desktop_session_id"),
        "project": source.get("project") or "",
        "title": title or source.get("title") or "(untitled log)",
        "session_name": compact_text(str(session_name or source.get("title") or ""), 90),
        "reasoning_effort": compact_text(str(source.get("reasoning_effort") or ""), 20),
        "cost": cost,
        "cost_approx": bool(approx),
        "availability": availability,
        "tokens": tokens,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "turns": turns,
        "models": sorted(models),
        **({"model_identity": model_identity} if model_identity else {}),
        "model_stats": sorted([
            {
                "model": model,
                "cost": float(values.get("cost") or 0),
                "tokens": int(values.get("tokens") or 0),
                "input_tokens": int(values.get("input_tokens") or 0),
                "output_tokens": int(values.get("output_tokens") or 0),
                "token_covered_executions": int(values.get(
                    "token_covered_executions", values.get("executions") or 0
                ) or 0),
                "io_covered_executions": int(values.get(
                    "io_covered_executions", values.get("executions") or 0
                ) or 0),
                "cache_read_tokens": int(values.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(values.get("cache_write_tokens") or 0),
                **{
                    field: int(values.get(field) or 0)
                    for field in (
                        "cache_write_5m_tokens",
                        "cache_write_1h_tokens",
                        "cache_write_unspecified_tokens",
                    )
                    if field in values
                },
                "reasoning_tokens": int(values.get("reasoning_tokens") or 0),
                "reasoning_output_tokens": int(
                    values.get("reasoning_output_tokens") or 0
                ),
                "reasoning_executions": int(values.get("reasoning_executions") or 0),
                "thinking_executions": int(values.get("thinking_executions") or 0),
                "thinking_covered_executions": int(
                    values.get("thinking_covered_executions") or 0
                ),
                "reasoning_unavailable_executions": int(
                    values.get(
                        "reasoning_unavailable_executions",
                        max(
                            0,
                            int(values.get("executions") or 0)
                            - int(values.get("reasoning_executions") or 0),
                        ),
                    ) or 0
                ),
                "executions": int(values.get("executions") or 0),
                "availability": values.get("availability") or availability,
                **({"cost_covered_executions": int(
                    values.get("cost_covered_executions") or 0
                )} if "cost_covered_executions" in values else {}),
                **({"cost_covered_output_tokens": int(
                    values.get("cost_covered_output_tokens") or 0
                )} if "cost_covered_output_tokens" in values else {}),
                **({"cost_covered_cost": float(
                    values.get("cost_covered_cost") or 0
                )} if "cost_covered_cost" in values else {}),
                **({"cache_covered_input_tokens": int(
                    values.get("cache_covered_input_tokens") or 0
                )} if "cache_covered_input_tokens" in values else {}),
            }
            for model, values in model_stats.items()
        ], key=lambda row: (-row["cost"], -row["tokens"], row["model"])),
        "throughput": performance_summary(performance_samples, output_tokens),
        "wait_time": wait_time_summary(wait_samples),
        "mtime": source["mtime"],
        "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(first_ts)) if first_ts else "",
        "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "",
        "duration_s": int(round(active_timing.get("duration_s") or 0)),
        "duration_available": bool(active_timing.get("available")),
        "duration_basis": active_timing.get("basis") or "unavailable",
        "wall_duration_s": int(wall_duration),
        "_model_cost": dict(model_cost),
        "_model_tok": dict(model_tok),
        "_day_cost": dict(day_cost),
        "_model_daily": model_daily,
        "_performance_samples": performance_samples,
        "_wait_samples": wait_samples,
    }
    row["provenance"] = usage_provenance([row])
    row["usage_basis"] = row["provenance"]["usage_basis"]
    return row


def cursor_summary(source, objs=None):
    return _cursor_native_adapter().summarize_legacy(source, objs)


def opencode_summary(source, objs=None):
    return _opencode_native_adapter().summarize_legacy(source, objs)


def kiro_summary(source, objs=None):
    return _kiro_native_adapter().summarize_legacy(source, objs)


def pi_summary(source, objs=None):
    return _pi_native_adapter().summarize_legacy(source, objs)


def hermes_summary(source, objs=None):
    return _hermes_native_adapter().summarize_legacy(source, objs)


def session_summary(source, opencode_conn=None):
    signature = source_revision_signature(source)
    with _summary_cache_lock:
        cached = _summary_cache.get(source["path"])
    if cached and cached.get("signature") == signature:
        return cached["row"]
    adapter = runtime_registry().get(source.get("provider"))
    summarizer = getattr(adapter, "summarize_legacy", None)
    if summarizer is not None:
        row = summarizer(source, opencode_conn)
    else:
        row = summary_row(source, source.get("title"), 0.0, 0, 0, set(), None, None,
                          {}, {}, {}, False, availability=metric_availability("unknown"))
    with _summary_cache_lock:
        _summary_cache[source["path"]] = {"signature": signature, "row": row}
    return row


def selected_session_stats(source):
    """Project exact, bounded efficiency statistics for one selected source."""
    summary = session_summary(source)
    return {
        "model_stats": copy.deepcopy(summary.get("model_stats") or []),
        "usage_basis": summary.get("usage_basis") or "reported",
        "provenance": copy.deepcopy(summary.get("provenance") or {}),
    }


def current_session_summaries(rows, now=None, max_age_s=CURRENT_SESSION_MAX_AGE_S,
                              limit=CURRENT_SESSION_LIMIT):
    return _domain_current_session_summaries(
        rows,
        now=now,
        max_age_s=max_age_s,
        limit=limit,
        working_age_s=CURRENT_SESSION_WORKING_S,
        context_sample_limit=CURRENT_SESSION_CONTEXT_SAMPLES,
    )


def global_tool_waste(session_rows):
    return _domain_global_tool_waste(
        session_rows, runtime_resolver=source_runtime_label,
    )


def codex_mcp_states():
    return {name: bool(row.get("enabled")) for name, row in toml_named_sections(CODEX_CONFIG, "mcp_servers").items()}


def claude_mcp_states():
    states = {}
    desktop_configs = [os.path.join(root, "claude_desktop_config.json") for root in CLAUDE_DESKTOP_DATA_ROOTS]
    for path in (CLAUDE_ROOT_CONFIG, *desktop_configs):
        data = load_json(path, {})
        for name in (data.get("mcpServers") or {}) if isinstance(data, dict) else {}:
            states[name] = True
    return states


def codex_plugin_states():
    return {name: bool(row.get("enabled")) for name, row in toml_named_sections(CODEX_CONFIG, "plugins").items()}


def claude_plugin_installations():
    data = load_json(os.path.expanduser("~/.claude/plugins/installed_plugins.json"), {})
    plugins = data.get("plugins") if isinstance(data, dict) else {}
    result = {}
    for plugin_id, installs in (plugins or {}).items():
        if not isinstance(installs, list) or not installs:
            continue
        valid = [row for row in installs if isinstance(row, dict) and row.get("installPath")]
        if valid:
            result[plugin_id] = valid[-1]
    return result


def skill_identity(runtime, name, origin_id, plugin_id=""):
    """Return a stable identity that cannot collide across runtimes or origins."""
    runtime_key = re.sub(r"[^a-z0-9]+", "-", str(runtime or "unknown").lower()).strip("-") or "unknown"
    owner = str(plugin_id or origin_id or "unknown").strip()
    return f"skill:{runtime_key}:{owner}:{name}"


def _scan_discovered_skills():
    usage = {}
    rows = []

    def add(path, runtime, source, origin_id, origin="user", enabled=True, plugin_id="",
            mutable=False, control_scope="", reviewable=False, setting_path=""):
        name = os.path.basename(os.path.dirname(path))
        used = usage.get(name.lower()) or {}
        providers = {str(provider).lower() for provider in used.get("providers") or []}
        expected_provider = "codex" if runtime == "Codex" else "claude"
        if providers and expected_provider not in providers:
            used = {}
        measurement = "measurable" if used else _skill_measurability(path)
        rows.append({
            "id": skill_identity(runtime, name, origin_id, plugin_id),
            "type": "skill", "name": name, "runtime": runtime, "source": source,
            "path": home_shorten(path), "enabled": bool(enabled),
            "configuration": "Enabled" if enabled else "Disabled", "plugin_id": plugin_id,
            "mutable": bool(mutable), "control_scope": control_scope,
            "origin": origin, "origin_id": origin_id, "reviewable": bool(reviewable),
            "setting_path": home_shorten(setting_path) if setting_path else "",
            "used": bool(used), "activations": int(used.get("activations") or 0),
            "sessions_used": int(used.get("sessions_used") or 0), "last_used": used.get("last_used") or "Never",
            "measurement": measurement, "unmeasurable": measurement != "measurable",
        })

    codex_skills_root = os.path.expanduser("~/.codex/skills")
    codex_system_root = os.path.join(codex_skills_root, ".system") + os.sep
    for path in glob.glob(os.path.join(codex_system_root, "**", "SKILL.md"), recursive=True):
        add(path, "Codex", "Codex built-in", "codex:built-in", "built_in", True)
    for path in glob.glob(os.path.join(codex_skills_root, "**", "SKILL.md"), recursive=True):
        if "/plugins/" not in path and not path.startswith(codex_system_root):
            add(path, "Codex", "User installed", "codex:user", "user", True)

    codex_plugins = codex_plugin_states()
    codex_cache = os.path.expanduser("~/.codex/plugins/cache")
    for path in glob.glob(os.path.join(codex_cache, "*", "*", "*", "skills", "*", "SKILL.md")):
        rel = os.path.relpath(path, codex_cache).split(os.sep)
        if len(rel) < 6:
            continue
        market, plugin = rel[0], rel[1]
        plugin_id = f"{plugin}@{market}"
        configured = plugin_id in codex_plugins
        bundled = market in ("openai-bundled", "openai-primary-runtime", "openai-curated-remote")
        add(path, "Codex", "Codex runtime pack" if bundled else "User-installed plugin",
            f"codex:plugin:{market}", "runtime_pack" if bundled else "user_plugin",
            codex_plugins.get(plugin_id, True), plugin_id, configured,
            "plugin pack" if configured else "", configured and not bundled, CODEX_CONFIG)

    claude_settings = load_json(CLAUDE_SETTINGS, {})
    claude_enabled = claude_settings.get("enabledPlugins") if isinstance(claude_settings, dict) else {}
    for plugin_id, install in claude_plugin_installations().items():
        root = install.get("installPath") or ""
        marketplace = plugin_id.rsplit("@", 1)[-1] if "@" in plugin_id else "unknown"
        runtime_pack = marketplace in ("claude-plugins-official", "openai-codex")
        for path in glob.glob(os.path.join(root, "skills", "*", "SKILL.md")):
            add(path, "Claude", "Claude runtime pack" if runtime_pack else "User-installed plugin",
                f"claude:plugin:{marketplace}", "runtime_pack" if runtime_pack else "user_plugin",
                bool((claude_enabled or {}).get(plugin_id)), plugin_id, True, "plugin pack",
                not runtime_pack, CLAUDE_SETTINGS)

    for data_root in CLAUDE_DESKTOP_DATA_ROOTS:
        desktop_root = os.path.join(data_root, "local-agent-mode-sessions", "skills-plugin")
        for path in glob.glob(os.path.join(desktop_root, "**", "skills", "*", "SKILL.md"), recursive=True):
            add(path, "Claude Desktop", "Cowork built-in", "claude-desktop:built-in", "built_in", True)

    deduped = {}
    for row in rows:
        deduped[row["id"]] = row
    return sorted(deduped.values(), key=lambda row: (row["runtime"], row["name"], row["source"]))


def invalidate_discovered_skill_cache():
    with _skill_catalog_cache_lock:
        _skill_catalog_cache["rows"] = None
        _skill_catalog_cache["at"] = 0.0


def discovered_skills(skill_usage=None):
    """Return current usage over a short-lived cache of static skill metadata."""
    now = time.monotonic()
    with _skill_catalog_cache_lock:
        cached_rows = _skill_catalog_cache.get("rows")
        if (
            cached_rows is None
            or now - float(_skill_catalog_cache.get("at") or 0) >= _SKILL_CATALOG_TTL_S
        ):
            cached_rows = tuple(_scan_discovered_skills())
            _skill_catalog_cache["rows"] = cached_rows
            _skill_catalog_cache["at"] = now
        rows = [dict(row) for row in cached_rows]

    usage = {
        str(row.get("name") or "").lower(): row
        for row in (skill_usage or [])
    }
    for row in rows:
        used = usage.get(str(row.get("name") or "").lower()) or {}
        providers = {
            str(provider).lower() for provider in used.get("providers") or []
        }
        expected_provider = "codex" if row.get("runtime") == "Codex" else "claude"
        if providers and expected_provider not in providers:
            used = {}
        row.update({
            "used": bool(used),
            "activations": int(used.get("activations") or 0),
            "sessions_used": int(used.get("sessions_used") or 0),
            "last_used": used.get("last_used") or "Never",
            "measurement": "measurable" if used else row.get("measurement") or "unknown",
        })
        row["unmeasurable"] = row["measurement"] != "measurable"
    return rows


def capability_control_groups(mcp_items, skill_items):
    return _domain_capability_control_groups(mcp_items, skill_items)


def optional_capability_summary(control_groups):
    return _domain_optional_capability_summary(control_groups)


def capability_content_revision(*parts):
    """Return a stable revision for JSON-safe capability evidence."""
    encoded = json.dumps(
        parts, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def capability_inventory(waste=None):
    waste = waste or {}
    tool_evidence = waste.get("inventory_tools") or waste.get("by_name") or []
    tool_items = []
    for row in tool_evidence:
        if row.get("kind") == "mcp":
            continue
        advertised = int(row.get("advertised_sessions") or 0)
        eager = int(row.get("eager_sessions") or 0)
        deferred = int(row.get("deferred_sessions") or 0)
        state = "Observed only"
        if advertised:
            state = "Eager" if eager and not deferred else ("Deferred" if deferred and not eager else "Mixed")
        tool_items.append({
            "id": f"tool:{row.get('id') or row.get('name')}", "type": "tool",
            "name": row.get("display") or row.get("name"),
            "identity": row.get("name"), "runtime": row.get("runtime") or "Trace",
            "source": row.get("namespace") or "unknown", "state": state,
            "enabled": None, "configuration": "Unknown", "mutable": False,
            "used": bool(row.get("calls")),
            "calls": int(row.get("calls") or 0), "returned_tokens": int(row.get("output_tokens") or 0),
            "advertised_sessions": advertised, "eager_sessions": eager, "deferred_sessions": deferred,
            "last_used": row.get("last_used") or "Never", "recommendation": row.get("recommendation") or "keep",
        })

    codex_states, claude_states = codex_mcp_states(), claude_mcp_states()
    mcp_usage = defaultdict(lambda: {
        "calls": 0, "tokens": 0, "last_used": "Never", "used": False,
        "definition_tokens": 0, "eager_definition_tokens": 0,
        "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
    })
    for row in tool_evidence:
        if row.get("kind") != "mcp":
            continue
        name = row.get("mcp_server") or row.get("namespace") or "mcp"
        u = mcp_usage[name]
        u["calls"] += int(row.get("calls") or 0)
        u["tokens"] += int(row.get("output_tokens") or 0)
        u["used"] = u["used"] or bool(row.get("calls"))
        for key in ("definition_tokens", "eager_definition_tokens", "deferred_definition_tokens",
                    "unused_eager_definition_tokens"):
            u[key] += int(row.get(key) or 0)
        if row.get("last_ts") and row.get("last_used"):
            u["last_used"] = row["last_used"]
    all_mcp_names = set(codex_states) | set(claude_states) | set(mcp_usage)
    mcp_items = []
    for name in sorted(all_mcp_names):
        codex_on = bool(codex_states.get(name))
        claude_on = bool(claude_states.get(name))
        usage_row = mcp_usage[name]
        enabled = codex_on or claude_on
        mcp_items.append({
            "id": f"mcp:{name}", "type": "mcp", "name": name, "runtime": "Codex + Claude",
            "source": "trace/config",
            "state": "Enabled" if enabled else "Disabled", "enabled": enabled,
            "configuration": "Enabled" if enabled else "Disabled",
            "mutable": False,
            "codex_enabled": codex_on, "claude_enabled": claude_on, "used": usage_row["used"],
            "calls": usage_row["calls"], "returned_tokens": usage_row["tokens"], "last_used": usage_row["last_used"],
            "definition_tokens": usage_row["definition_tokens"],
            "eager_definition_tokens": usage_row["eager_definition_tokens"],
            "deferred_definition_tokens": usage_row["deferred_definition_tokens"],
            "unused_eager_definition_tokens": usage_row["unused_eager_definition_tokens"],
        })

    skill_items = discovered_skills(waste.get("skills") or [])
    control_groups = capability_control_groups(mcp_items, skill_items)
    observed_runtimes = waste.get("runtime_sessions")
    if observed_runtimes is not None:
        runtime_sessions = {
            "Codex": int(observed_runtimes.get("Codex") or 0),
            "Claude": int(observed_runtimes.get("Claude") or 0)
                      + int(observed_runtimes.get("Claude Code") or 0),
            "Cursor": int(observed_runtimes.get("Cursor") or 0),
        }
    else:
        # Compatibility for callers that provide a pre-runtime-count snapshot.
        provider_sessions = waste.get("provider_sessions") or {}
        runtime_sessions = {
            "Codex": int(provider_sessions.get("codex") or 0),
            "Claude": int(provider_sessions.get("claude") or 0),
            "Cursor": int(provider_sessions.get("cursor") or 0),
        }
    for row in control_groups:
        row["scanned_sessions"] = runtime_sessions.get(row.get("runtime"), 0)
    optional_summary = optional_capability_summary(control_groups)
    optional_summary["scanned_sessions"] = int(waste.get("total_sessions") or 0)
    optional_summary["scanned_sessions_by_runtime"] = runtime_sessions
    review_ids = set(optional_summary["review_candidates"])
    for row in mcp_items:
        row["control_id"] = row["id"]
        row["review_candidate"] = row["control_id"] in review_ids
    for row in skill_items:
        row["control_id"] = f"skill_pack:{row.get('runtime')}:{row.get('plugin_id')}" if row.get("plugin_id") else ""
        row["review_candidate"] = bool(row["control_id"] and row["control_id"] in review_ids)
    tool_reported = sum(1 for row in tool_items if row["advertised_sessions"])
    tool_reported_used = sum(1 for row in tool_items if row["advertised_sessions"] and row["used"])
    tools_observed = sum(1 for row in tool_items if row["used"])
    mcp_enabled = sum(1 for row in mcp_items if row["enabled"])
    mcp_enabled_used = sum(1 for row in mcp_items if row["enabled"] and row["used"])
    skills_enabled = sum(1 for row in skill_items if row["enabled"])
    skills_used = sum(1 for row in skill_items if row["enabled"] and row["used"])
    desktop_index = claude_desktop_index()
    local_agents = [row for row in desktop_index.values() if row.get("source_kind") == "agent"]
    traceable_agents = claude_local_agent_sources(desktop_index)
    latest_desktop = max((row.get("metadata_mtime") or 0 for row in local_agents), default=0)
    summary = {
        "tools": {"available": len(tool_items), "reported": tool_reported, "enabled": None,
                  "used": tool_reported_used, "observed": tools_observed,
                  "utilization": tool_reported_used / tool_reported if tool_reported else 0.0,
                  "observed_only": sum(1 for row in tool_items if not row["advertised_sessions"])},
        "mcps": {"available": len(mcp_items), "enabled": mcp_enabled, "used": mcp_enabled_used,
                 "historically_used": sum(1 for row in mcp_items if row["used"]),
                 "utilization": mcp_enabled_used / mcp_enabled if mcp_enabled else 0.0},
        "skills": {"available": len(skill_items), "enabled": skills_enabled, "used": skills_used,
                   "historically_used": sum(1 for row in skill_items if row["used"]),
                   "utilization": skills_used / skills_enabled if skills_enabled else 0.0},
        "optional": optional_summary,
        "definitions": {key: int(waste.get(key) or 0) for key in (
            "definition_tokens", "eager_definition_tokens", "deferred_definition_tokens", "unused_eager_definition_tokens"
        )},
    }
    items = tool_items + mcp_items + skill_items
    claude_desktop = {
        "local_agent_sessions": len(local_agents),
        "traceable_agent_sessions": len(traceable_agents),
        "latest_local_agent": local_dt(latest_desktop) if latest_desktop else "Never",
        "cloud_trace_available": False,
        "roots": [home_shorten(root) for root in CLAUDE_DESKTOP_DATA_ROOTS if os.path.isdir(root)],
        "note": "Scanning local Claude Desktop Agent/Cowork traces.",
    }
    review_revision = capability_content_revision(optional_summary, control_groups)
    inventory_revision = capability_content_revision(summary, items, claude_desktop)
    return {
        "summary": summary, "items": items, "control_groups": control_groups,
        "actions": capability_action_capability(), "claude_desktop": claude_desktop,
        "review_revision": review_revision,
        "inventory_revision": inventory_revision,
        "revision": capability_content_revision(review_revision, inventory_revision),
        "generated_at": int(time.time()),
    }


def capability_summary_payload(capabilities):
    """Return decision evidence without the heavy inventory rows."""
    capabilities = capabilities or {}
    payload = {key: value for key, value in capabilities.items() if key != "items"}
    payload["inventory_count"] = len(capabilities.get("items") or [])
    return payload


def dashboard_state_payload(state):
    """Bound browser polling to data used by dashboard surfaces."""
    if not isinstance(state, dict):
        return state
    payload = dict(state)
    for removed in ("trace", "insights", "recommendation", "verdict"):
        payload.pop(removed, None)
    if isinstance(state.get("tools"), dict):
        public_tools = dict(state["tools"])
        for removed in (
            "by_namespace", "by_name", "by_execution",
            "execution_rows_truncated", "execution_rows_shown",
            "execution_calls_shown", "namespaces_used",
        ):
            public_tools.pop(removed, None)
        payload["tools"] = public_tools
    payload["runtime_catalog"] = _runtime_catalog(runtime_registry().descriptors)
    cross = state.get("xsession")
    if isinstance(cross, dict):
        public_cross = dict(cross)
        public_cross.pop("tool_waste", None)
        if isinstance(cross.get("capabilities"), dict):
            public_cross["capabilities"] = capability_summary_payload(
                cross.get("capabilities")
            )
        payload["xsession"] = public_cross
    return payload


def session_optional_capabilities(state, capabilities):
    """Summarize enabled removable groups for one selected session."""
    state = state or {}
    tools = state.get("tools") or {}
    provider = state.get("provider") or (state.get("source") or {}).get("provider") or ""
    skill_activations = {row.get("name"): int(row.get("activations") or 0)
                         for row in tools.get("skills") or [] if row.get("name")}
    groups = []
    for source_group in (capabilities or {}).get("control_groups") or []:
        group = dict(source_group)
        if (not group.get("enabled") or not group.get("mutable") or
                not group.get("reviewable", True)):
            continue
        attached = ((provider == "codex" and group.get("runtime") == "Codex") or
                    (provider == "claude" and group.get("runtime") == "Claude"))
        active_members = set(group.get("members") or []) & set(skill_activations)
        current_used = bool(active_members)
        activations = sum(skill_activations[name] for name in active_members)
        if not attached:
            continue
        group.update({
            "current_used": current_used, "current_activations": activations,
            "current_eager_definition_tokens": 0,
            "current_deferred_definition_tokens": 0,
            "current_unused_eager_definition_tokens": 0,
            "overhead_measured": False,
        })
        groups.append(group)

    used = [row for row in groups if row.get("current_used")]
    unused = [row for row in groups if not row.get("current_used")]
    avoidable_tokens = sum(int(row.get("current_unused_eager_definition_tokens") or 0) for row in unused)
    return {
        "scope": "session", "enabled": len(groups), "used": len(used), "unused": len(unused),
        "utilization": len(used) / len(groups) if groups else 0.0,
        "mcp_enabled": sum(1 for row in groups if row.get("control_type") == "mcp"),
        "mcp_used": sum(1 for row in used if row.get("control_type") == "mcp"),
        "skill_packs_enabled": sum(1 for row in groups if row.get("control_type") == "skill_pack"),
        "skill_packs_used": sum(1 for row in used if row.get("control_type") == "skill_pack"),
        "avoidable_eager_definition_tokens": avoidable_tokens,
        "overhead_measured_groups": sum(1 for row in groups if row.get("overhead_measured")),
        "eager_unused_groups": sum(1 for row in unused
                                    if int(row.get("current_unused_eager_definition_tokens") or 0) > 0),
        "deferred_unused_groups": sum(1 for row in unused
                                       if int(row.get("current_deferred_definition_tokens") or 0) > 0
                                       and int(row.get("current_unused_eager_definition_tokens") or 0) == 0),
        "unmeasured_unused_groups": sum(1 for row in unused if not row.get("overhead_measured")),
        "global_review_candidates": list((((capabilities or {}).get("summary") or {}).get("optional") or {}).get("review_candidate_names") or []),
        "groups": groups,
    }


def attach_cross_session(state, cross=None):
    if not state:
        return state
    cross = cross or cross_session()
    state["xsession"] = cross
    state["optional_capabilities"] = session_optional_capabilities(state, cross.get("capabilities") or {})
    return state


def capability_action_capability():
    return {
        "token": _ACTION_TOKEN,
        "skill_pack_toggle": True,
    }


def session_action_capability():
    trash_plan = _PLATFORM_SERVICES.trash_plan("")
    return {
        "available": trash_plan.supported,
        "token": _ACTION_TOKEN,
        "recoverable": True,
        "destination": trash_plan.destination_label,
        "read_only_providers": ["opencode", "hermes", "grok"],
    }


def request_session_delete(session_id):
    """Apply the public read-only-provider boundary before any trash action."""
    source = find_session(session_id) if session_id else None
    provider = str((source or {}).get("provider") or "").strip().lower()
    read_only = {
        str(value).strip().lower()
        for value in session_action_capability().get("read_only_providers") or ()
    }
    if source and provider in read_only:
        return {
            "ok": False,
            "error": "{} sessions are read-only in Token Meter.".format(
                runtime_display_label(provider)
            ),
            "error_code": "read_only_provider",
        }
    return trash_session_log(session_id)


def agent_access_launcher():
    root = _SOURCE_ROOT
    platform_launcher = _PLATFORM_SERVICES.agent_launcher(root)
    candidates = [platform_launcher] if platform_launcher else []
    candidates.extend([
        os.path.join(root, "bin", "token-meter-mcp"),
        "/Library/Application Support/Token Meter/bin/token-meter-mcp",
    ])
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), candidates[0])


def agent_client_executable(client, which=None):
    which = which or shutil.which
    direct = which(client)
    if direct:
        return direct
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", client),
        os.path.join(home, ".volta", "bin", client),
        os.path.join(home, ".asdf", "shims", client),
        os.path.join(home, ".npm-global", "bin", client),
        os.path.join(home, "bin", client),
        os.path.join("/opt/homebrew/bin", client),
        os.path.join("/usr/local/bin", client),
    ]
    nvm = glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", client))
    candidates.extend(sorted(nvm, key=safe_mtime, reverse=True))
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def agent_client_environment(cli_path):
    """Give env-based Node wrappers their sibling runtime under a LaunchAgent."""
    env = os.environ.copy()
    current = [value for value in str(env.get("PATH") or "").split(os.pathsep) if value]
    # Keep the wrapper's directory, not the resolved script target. NVM's
    # `codex` is a symlink whose sibling `node` binary is required by env(1).
    preferred = [os.path.dirname(os.path.abspath(cli_path))] if cli_path else []
    for value in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if value not in preferred:
            preferred.append(value)
    env["PATH"] = os.pathsep.join(dict.fromkeys(preferred + current))
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def agent_access_command(client, enabled, launcher=None, cli_path=None):
    launcher = launcher or agent_access_launcher()
    client = str(client or "").strip().lower()
    if client == "codex":
        cli_path = cli_path or agent_client_executable("codex") or "codex"
        if enabled:
            return [cli_path, "mcp", "add", "--env", "TOKEN_METER_CALLER=codex",
                    AGENT_ACCESS_SERVER, "--", launcher]
        return [cli_path, "mcp", "remove", AGENT_ACCESS_SERVER]
    if client == "claude":
        cli_path = cli_path or agent_client_executable("claude") or "claude"
        if enabled:
            return [cli_path, "mcp", "add", "--transport", "stdio", "--scope", "user",
                    AGENT_ACCESS_SERVER, "--env", "TOKEN_METER_CALLER=claude", "--", launcher]
        return [cli_path, "mcp", "remove", AGENT_ACCESS_SERVER, "--scope", "user"]
    raise ValueError("Unsupported agent client.")


def agent_access_command_display(command):
    command = list(command or [])
    if command:
        command[0] = os.path.basename(command[0])
    return shlex.join(command)


def agent_cli_error_detail(completed):
    raw = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""
    return compact_text(lines[-1].replace(os.path.expanduser("~"), "~"), 220)


def _agent_access_matches(command, args, env, launcher, runtime):
    try:
        command_match = os.path.realpath(os.path.expanduser(str(command or ""))) == os.path.realpath(launcher)
    except OSError:
        command_match = False
    return bool(command_match and list(args or []) == [] and str((env or {}).get("TOKEN_METER_CALLER") or "") == runtime)


def agent_access_client_status(client, launcher=None, runner=None, which=None, claude_config_path=None):
    client = str(client or "").strip().lower()
    if client not in ("codex", "claude"):
        raise ValueError("Unsupported agent client.")
    launcher = launcher or agent_access_launcher()
    runner = runner or subprocess.run
    which = which or shutil.which
    cli_path = agent_client_executable(client, which=which)
    configured = False
    connected = False
    conflict = False
    actual_enabled = True
    command, args, env = "", [], {}
    if cli_path and client == "codex":
        try:
            completed = runner([cli_path, "mcp", "get", AGENT_ACCESS_SERVER, "--json"],
                               capture_output=True, text=True, timeout=15, check=False,
                               env=agent_client_environment(cli_path),
                               **_platform_subprocess_kwargs())
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed and completed.returncode == 0:
            try:
                row = json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                row = {}
            transport = row.get("transport") if isinstance(row, dict) else {}
            if isinstance(transport, dict) and transport.get("type") == "stdio":
                configured = True
                command = transport.get("command") or ""
                args = transport.get("args") or []
                env = transport.get("env") or {}
                actual_enabled = row.get("enabled") is not False
    elif cli_path:
        config = load_json(claude_config_path or CLAUDE_ROOT_CONFIG, {})
        servers = config.get("mcpServers") if isinstance(config, dict) else {}
        row = (servers or {}).get(AGENT_ACCESS_SERVER) if isinstance(servers, dict) else None
        if isinstance(row, dict):
            configured = True
            command = row.get("command") or ""
            args = row.get("args") or []
            env = row.get("env") or {}

    exact = configured and _agent_access_matches(command, args, env, launcher,
                                                  "codex" if client == "codex" else "claude")
    connected = bool(exact and actual_enabled)
    conflict = bool(configured and not connected)
    add_command = agent_access_command(client, True, launcher=launcher, cli_path=cli_path or client)
    remove_command = agent_access_command(client, False, launcher=launcher, cli_path=cli_path or client)
    return {
        "id": client,
        "label": "Codex" if client == "codex" else "Claude Code",
        "detected": bool(cli_path),
        "available": bool(cli_path and os.path.isfile(launcher) and os.access(launcher, os.X_OK)),
        "configured": configured,
        "connected": connected,
        "conflict": conflict,
        "status": "Connected" if connected else ("Existing entry differs from this install" if conflict else
                  ("Ready to connect" if cli_path else "Client not found")),
        "connect_command": agent_access_command_display(add_command),
        "disconnect_command": agent_access_command_display(remove_command),
        "restart_note": "Start a new agent session after changing this connection.",
    }


def agent_access_status(**kwargs):
    launcher = kwargs.pop("launcher", None) or agent_access_launcher()
    clients = [agent_access_client_status(client, launcher=launcher, **kwargs)
               for client in ("codex", "claude")]
    return {
        "ok": True,
        "server": AGENT_ACCESS_SERVER,
        "launcher_ready": bool(os.path.isfile(launcher) and os.access(launcher, os.X_OK)),
        "clients": clients,
        "any_detected": any(row["detected"] for row in clients),
        "any_connected": any(row["connected"] for row in clients),
        "access": {
            "current_run": "Detailed cost, context, execution, and safe tool labels for the matched run.",
            "history": "Aggregate spend, model, runtime, and tool categories without run or project names.",
            "capabilities": "Named user-installed skill packs only when capability review is requested.",
            "never": "Prompts, messages, reasoning, tool arguments or results, paths, credentials, and config values.",
            "processing": "Returned metrics enter the connected agent context and may be processed by its model provider.",
            "mutation": False,
        },
    }


def set_agent_access(client, enabled, repair=False, runner=None, status_getter=None):
    client = str(client or "").strip().lower()
    if client not in ("codex", "claude") or enabled not in (True, False):
        return {"ok": False, "error": "A supported client and explicit connection state are required."}
    runner = runner or subprocess.run
    status_getter = status_getter or agent_access_client_status
    before = status_getter(client)
    if not before.get("detected"):
        return {"ok": False, "error": f"{before.get('label') or client} CLI was not found."}
    if not before.get("available"):
        return {"ok": False, "error": "The Token Meter MCP launcher is not executable. Reinstall Token Meter."}
    if enabled and before.get("connected"):
        return {"ok": True, "changed": False, "client": before, "restart_required": False}
    repairing = bool(enabled and repair is True and before.get("conflict"))
    if before.get("conflict") and not repairing:
        return {"ok": False, "conflict": True,
                "error": "The existing tokenmeter MCP entry differs from this install. Confirm repair to replace only that entry."}
    if not enabled and not before.get("configured"):
        return {"ok": True, "changed": False, "client": before, "restart_required": False}
    cli_path = agent_client_executable(client)
    commands = []
    if repairing:
        commands.append(("remove the existing tokenmeter entry",
                         agent_access_command(client, False, cli_path=cli_path)))
    commands.append(("save the Token Meter connection",
                     agent_access_command(client, enabled, cli_path=cli_path)))
    for description, command in commands:
        try:
            completed = runner(command, capture_output=True, text=True, timeout=45, check=False,
                               env=agent_client_environment(cli_path),
                               **_platform_subprocess_kwargs())
        except subprocess.TimeoutExpired:
            return {"ok": False, "conflict": repairing,
                    "error": f"The agent client timed out while trying to {description}."}
        except OSError:
            return {"ok": False, "conflict": repairing,
                    "error": f"The agent client could not {description}."}
        if completed.returncode != 0:
            label = before.get("label") or client
            detail = agent_cli_error_detail(completed)
            message = f"{label} could not {description}."
            if detail:
                message = f"{message} {detail}"
            return {"ok": False, "conflict": repairing, "error": message}
    after = status_getter(client)
    verified = bool(after.get("connected")) if enabled else not bool(after.get("configured"))
    if not verified:
        return {"ok": False, "error": "The connection command completed, but the saved configuration could not be verified."}
    return {
        "ok": True, "changed": True, "repaired": repairing, "client": after,
        "restart_required": True,
        "message": (f"{after.get('label') or client} connection repaired. Start a new agent session."
                    if repairing else
                    f"{after.get('label') or client} {'connected' if enabled else 'disconnected'}. Start a new agent session."),
    }


def _toml_line_structure(line, state="normal", array_depth=0):
    """Track TOML string state so root-table scanning never enters a literal."""
    index = 0
    while index < len(line):
        char = line[index]
        if state == "triple-basic":
            if line.startswith('\"\"\"', index):
                state, index = "normal", index + 3
            else:
                index += 1
        elif state == "triple-literal":
            if line.startswith("'''", index):
                state, index = "normal", index + 3
            else:
                index += 1
        elif state == "basic":
            if char == "\\":
                index += 2
            elif char == '"':
                state, index = "normal", index + 1
            else:
                index += 1
        elif state == "literal":
            if char == "'":
                state = "normal"
            index += 1
        else:
            if char == "#":
                break
            if line.startswith('\"\"\"', index):
                state, index = "triple-basic", index + 3
            elif line.startswith("'''", index):
                state, index = "triple-literal", index + 3
            elif char == '"':
                state, index = "basic", index + 1
            elif char == "'":
                state, index = "literal", index + 1
            elif char == "[":
                array_depth, index = array_depth + 1, index + 1
            elif char == "]":
                array_depth, index = max(0, array_depth - 1), index + 1
            else:
                index += 1
    return state, array_depth


def _toml_line_state(line, state="normal"):
    return _toml_line_structure(line, state)[0]


def _toml_is_table_header(line, state, array_depth):
    if state != "normal" or array_depth:
        return False
    return bool(re.match(r"^[ \t]*(?:\[\[.*\]\]|\[.*\])[ \t]*(?:#.*)?(?:\r?\n)?$", line))


def _toml_root_prefix(text):
    offset, state, array_depth = 0, "normal", 0
    for line in text.splitlines(keepends=True):
        if _toml_is_table_header(line, state, array_depth):
            return text[:offset], text[offset:]
        state, array_depth = _toml_line_structure(line, state, array_depth)
        offset += len(line)
    return text, ""


def _toml_comment_index(value):
    index, state = 0, "normal"
    while index < len(value):
        char = value[index]
        if state == "basic":
            if char == "\\":
                index += 2
                continue
            if char == '"':
                state = "normal"
        elif state == "literal":
            if char == "'":
                state = "normal"
        elif char == "#":
            return index
        elif char == '"':
            state = "basic"
        elif char == "'":
            state = "literal"
        index += 1
    return len(value)


def _toml_root_assignment(prefix, key):
    pattern = re.compile(rf"^(?P<before>[ \t]*(?:{re.escape(key)}|\"{re.escape(key)}\")[ \t]*=[ \t]*)(?P<after>.*)$")
    offset, state = 0, "normal"
    for line in prefix.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        line_end = line[len(line_body):]
        match = pattern.match(line_body) if state == "normal" else None
        if match:
            after = match.group("after")
            comment_at = _toml_comment_index(after)
            value = after[:comment_at].rstrip()
            suffix = after[len(value):] + line_end
            return {
                "start": offset,
                "end": offset + len(line),
                "before": match.group("before"),
                "value": value,
                "suffix": suffix,
            }
        state = _toml_line_state(line, state)
        offset += len(line)
    return None


def _toml_root_scalar(text, key):
    prefix, _ = _toml_root_prefix(text)
    assignment = _toml_root_assignment(prefix, key)
    if not assignment:
        return None
    value = assignment["value"]
    if value.startswith(('\"\"\"', "'''")):
        return None
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return None


def _toml_root_scalar_text(value):
    return str(int(value)) if isinstance(value, int) else json.dumps(value, ensure_ascii=False)


def _set_toml_root_scalar(text, key, value):
    prefix, suffix = _toml_root_prefix(text)
    assignment = _toml_root_assignment(prefix, key)
    if assignment and assignment["value"].startswith(('\"\"\"', "'''")):
        raise ValueError(f"Codex {key} must be a single-line value.")
    if value is None:
        if assignment:
            prefix = prefix[:assignment["start"]] + prefix[assignment["end"]:]
        return prefix + suffix
    if assignment:
        replacement = f"{assignment['before']}{_toml_root_scalar_text(value)}{assignment['suffix']}"
        prefix = prefix[:assignment["start"]] + replacement + prefix[assignment["end"]:]
    else:
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        prefix += f"{key} = {_toml_root_scalar_text(value)}\n"
    return prefix + suffix


def set_codex_plugin_enabled(plugin_id, enabled):
    if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
        return {"ok": False, "error": "Invalid plugin id."}
    states = codex_plugin_states()
    if plugin_id not in states:
        return {"ok": False, "error": "Codex plugin is not configured."}
    try:
        with open(CODEX_CONFIG, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    quoted = re.escape(plugin_id)
    header = re.search(rf'^\[plugins\."{quoted}"\]\s*$', text, re.MULTILINE)
    if not header:
        header = re.search(rf'^\[plugins\.{quoted}\]\s*$', text, re.MULTILINE)
    if not header:
        return {"ok": False, "error": "Codex plugin section was not found."}
    next_header = re.search(r'^\[', text[header.end():], re.MULTILINE)
    end = header.end() + next_header.start() if next_header else len(text)
    body = text[header.end():end]
    value = "true" if enabled else "false"
    if re.search(r'^\s*enabled\s*=\s*(?:true|false)\s*$', body, re.MULTILINE | re.IGNORECASE):
        body = re.sub(r'(^\s*enabled\s*=\s*)(?:true|false)(\s*$)', rf'\g<1>{value}\g<2>', body,
                      count=1, flags=re.MULTILINE | re.IGNORECASE)
    else:
        body = f"\nenabled = {value}" + body
    try:
        atomic_write_text(CODEX_CONFIG, text[:header.end()] + body + text[end:])
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    verified_state = codex_plugin_states().get(plugin_id)
    if verified_state is not bool(enabled):
        return {"ok": False, "error": "Codex setting was written but could not be verified."}
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True, "plugin_id": plugin_id, "runtime": "Codex", "enabled": bool(enabled),
        "verified": True, "setting_path": home_shorten(CODEX_CONFIG), "restart_required": True,
    }


def set_claude_plugin_enabled(plugin_id, enabled):
    if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
        return {"ok": False, "error": "Invalid plugin id."}
    if plugin_id not in claude_plugin_installations():
        return {"ok": False, "error": "Claude plugin is not installed."}
    settings = load_json(CLAUDE_SETTINGS, {})
    if not isinstance(settings, dict):
        settings = {}
    enabled_plugins = settings.setdefault("enabledPlugins", {})
    enabled_plugins[plugin_id] = bool(enabled)
    try:
        atomic_write_text(CLAUDE_SETTINGS, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    verified = load_json(CLAUDE_SETTINGS, {})
    verified_state = ((verified.get("enabledPlugins") or {}).get(plugin_id)
                      if isinstance(verified, dict) else None)
    if verified_state is not bool(enabled):
        return {"ok": False, "error": "Claude setting was written but could not be verified."}
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True, "plugin_id": plugin_id, "runtime": "Claude", "enabled": bool(enabled),
        "verified": True, "setting_path": home_shorten(CLAUDE_SETTINGS), "restart_required": True,
    }


def set_skill_pack_enabled(runtime, plugin_id, enabled):
    runtime = str(runtime or "").strip().lower()
    if runtime == "codex":
        result = set_codex_plugin_enabled(plugin_id, enabled)
    elif runtime == "claude":
        result = set_claude_plugin_enabled(plugin_id, enabled)
    else:
        return {"ok": False, "error": "Only Codex and Claude plugin packs can be changed."}
    if result.get("ok"):
        invalidate_discovered_skill_cache()
    return result


def set_capability_control_enabled(control, enabled):
    if (control or {}).get("control_type") == "skill_pack":
        return set_skill_pack_enabled(control.get("runtime"), control.get("plugin_id"), enabled)
    return {"ok": False, "error": "Unsupported capability control."}


def disable_capability_controls(control_ids, capabilities=None, setter=None):
    """Disable an exact set of current review candidates with partial-failure reporting."""
    if not isinstance(control_ids, list) or not control_ids or len(control_ids) > 100:
        return {"ok": False, "error": "Select between 1 and 100 unused capability groups."}
    requested = list(dict.fromkeys(str(value or "").strip() for value in control_ids))
    if any(not value for value in requested):
        return {"ok": False, "error": "Capability control ids must be non-empty strings."}
    capabilities = capabilities or (cross_session().get("capabilities") or {})
    candidate_ids = set((((capabilities.get("summary") or {}).get("optional") or {}).get("review_candidates") or []))
    groups = {row.get("id"): row for row in capabilities.get("control_groups") or []}
    invalid = [control_id for control_id in requested if (
        control_id not in candidate_ids or control_id not in groups or
        not groups[control_id].get("enabled") or groups[control_id].get("used") or
        not groups[control_id].get("mutable") or not groups[control_id].get("reviewable", True)
    )]
    if invalid:
        return {
            "ok": False, "error": "One or more controls are no longer unused review candidates.",
            "invalid_control_ids": invalid,
        }

    setter = setter or set_capability_control_enabled
    changed, failures, results = [], [], []
    for control_id in requested:
        control = groups[control_id]
        item = setter(control, False)
        result = {
            "control_id": control_id, "name": control.get("name"),
            "control_type": control.get("control_type"), "ok": bool(item.get("ok")),
        }
        if item.get("ok"):
            changed.append(control_id)
            result["verified"] = bool(item.get("verified", control.get("control_type") == "mcp"))
        else:
            result["error"] = item.get("error") or "Capability change failed."
            failures.append(result)
        results.append(result)
    return {
        "ok": not failures, "partial": bool(changed and failures),
        "requested": len(requested), "changed": len(changed),
        "changed_control_ids": changed, "failures": failures, "results": results,
        "restart_required": bool(changed),
    }


def refresh_capability_state():
    """Rebuild and publish capabilities after a verified configuration change."""
    cross = cross_session()
    if STATE:
        publish(attach_cross_session(dict(STATE), cross))
    return cross.get("capabilities") or {}


def daily_summaries(session_rows, limit=30):
    return _domain_daily_summaries(
        session_rows, limit=limit, availability_resolver=metric_availability,
    )


def spend_projection(daily_rows):
    return _domain_spend_projection(daily_rows)


def spend_log_summaries(session_rows, start_day, end_day):
    return _domain_spend_log_summaries(
        session_rows, start_day, end_day,
        availability_resolver=metric_availability,
    )


def monthly_summaries(session_rows, limit=12):
    return _domain_monthly_summaries(session_rows, limit=limit)


def monthly_budget_status(months, settings=None, now=None):
    """Combine the current calendar-month rollup with configured budget targets."""
    settings = normalize_budget_settings(settings or {})
    now = now or datetime.datetime.now().astimezone()
    month_key = now.strftime("%Y-%m")
    current = next(
        (row for row in (months or []) if row.get("month") == month_key),
        {
            "month": month_key, "cost": 0.0, "sessions": 0, "active_days": 0,
            "observed_days": 0, "days": [], "providers": [],
            "coverage": {"cost": {
                "covered_sessions": 0, "total_sessions": 0, "complete": True,
            }},
            "provenance": make_usage_provenance((), (), ()),
            "usage_basis": "unavailable", "availability": {"cost": False},
        },
    )
    total = float(settings["monthly_total"])
    spend = float(current.get("cost") or 0)
    percent = spend / total if total else 0.0
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    elapsed_days = max(1, min(days_in_month, now.day))
    active_days = int(current.get("active_days") or 0)
    projected = (
        spend / elapsed_days * days_in_month
        if total and active_days >= 3 else None
    )
    provider_spend = {
        row.get("provider"): float(row.get("cost") or 0)
        for row in (current.get("providers") or [])
    }
    allocations = settings["allocations"]
    runtimes = []
    for provider in BUDGET_PROVIDERS:
        allocation = float(allocations.get(provider) or 0)
        runtime_spend = provider_spend.get(provider, 0.0)
        exceeded = bool(allocation and runtime_spend >= allocation)
        runtimes.append({
            "provider": provider,
            "label": runtime_display_label(provider),
            "spend": runtime_spend,
            "allocation": allocation,
            "percent": runtime_spend / allocation if allocation else None,
            "remaining": max(0.0, allocation - runtime_spend) if allocation else None,
            "exceeded": exceeded,
            "over_by": max(0.0, runtime_spend - allocation) if allocation else 0.0,
        })
    exceeded_runtimes = [
        {
            "provider": row["provider"],
            "label": row["label"],
            "spend": row["spend"],
            "allocation": row["allocation"],
            "percent": row["percent"],
            "over_by": row["over_by"],
        }
        for row in runtimes
        if row["exceeded"]
    ]
    cost_coverage = (current.get("coverage") or {}).get("cost") or {}
    partial = bool(
        cost_coverage.get("total_sessions")
        and not cost_coverage.get("complete")
    )
    estimated = bool((current.get("provenance") or {}).get("estimated_sessions"))
    crossed = [value for value in settings["thresholds"] if percent >= value / 100]
    if not total:
        state = "unconfigured"
    elif percent >= 1:
        state = "over"
    elif crossed:
        state = "warning"
    else:
        state = "on_track"
    return {
        "month": month_key,
        "configured": total > 0,
        "currency": settings["currency"],
        "budget": total,
        "spend": spend,
        "percent": percent,
        "remaining": max(0.0, total - spend) if total else None,
        "unallocated": max(0.0, total - sum(allocations.values())) if total else 0.0,
        "active_days": active_days,
        "elapsed_days": elapsed_days,
        "days_in_month": days_in_month,
        "days_remaining": max(0, days_in_month - elapsed_days),
        "projected_spend": projected,
        "projection_ready": projected is not None,
        "projection_min_active_days": 3,
        "partial": partial,
        "estimated": estimated,
        "lower_bound": partial,
        "state": state,
        "runtime_exceeded": bool(exceeded_runtimes),
        "exceeded_runtimes": exceeded_runtimes,
        "attention": state == "over" or bool(exceeded_runtimes),
        "thresholds_crossed": crossed,
        "next_threshold": next(
            (value for value in settings["thresholds"] if percent < value / 100),
            None,
        ),
        "runtimes": runtimes,
        "settings": settings,
    }


MATCHED_PACE_MIN_PAIRS = 20
MATCHED_PACE_MIN_COVERAGE = 0.30


def _pace_log_distance(left, right, max_distance):
    left = max(1.0, float(left or 0))
    right = max(1.0, float(right or 0))
    distance = abs(math.log(left / right, 2))
    return None if distance > max_distance else distance / max_distance


def pace_match_distance(left, right):
    """Return workload distance, or None when two completed turns are not comparable."""
    left_tools = int(left.get("tool_calls") or 0)
    right_tools = int(right.get("tool_calls") or 0)
    if bool(left_tools) != bool(right_tools):
        return None
    dimensions = [
        (_pace_log_distance(
            left.get("peak_input_tokens") or left.get("input_tokens"),
            right.get("peak_input_tokens") or right.get("input_tokens"), 2.0,
        ), 0.28),
        (_pace_log_distance(left.get("input_tokens"), right.get("input_tokens"), 3.0), 0.17),
        (_pace_log_distance(left.get("output_tokens"), right.get("output_tokens"), 2.0), 0.22),
        (_pace_log_distance(left.get("model_calls") or 1, right.get("model_calls") or 1, 2.0), 0.16),
    ]
    if left_tools:
        dimensions.append((_pace_log_distance(left_tools, right_tools, 2.0), 0.12))
    if any(distance is None for distance, _ in dimensions):
        return None
    left_input = max(1, int(left.get("input_tokens") or 0))
    right_input = max(1, int(right.get("input_tokens") or 0))
    left_cache = min(1.0, float(left.get("cache_read_tokens") or 0) / left_input)
    right_cache = min(1.0, float(right.get("cache_read_tokens") or 0) / right_input)
    cache_distance = abs(left_cache - right_cache)
    if cache_distance > 0.60:
        return None
    score = sum(distance * weight for distance, weight in dimensions)
    score += (cache_distance / 0.60) * 0.05
    recency_days = abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)) / 86400.0
    score += min(1.0, recency_days / 90.0) * 0.03
    return score


def _percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))]


def _bootstrap_median_interval(values, seed_key, repetitions=400):
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    seed = int(hashlib.sha256(str(seed_key).encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    medians = []
    for _ in range(repetitions):
        medians.append(statistics.median(rng.choice(values) for _ in values))
    return _percentile(medians, 0.025), _percentile(medians, 0.975)


def matched_pace_comparison(a_id, a_samples, b_id, b_samples, distance_cache=None):
    """Compare two model-runtime histories using deterministic workload matching."""
    def usable(sample):
        return (
            float(sample.get("duration_s") or 0) > 0
            and int(sample.get("input_tokens") or 0) > 0
            and int(sample.get("output_tokens") or 0) > 0
        )

    a_rows = [sample for sample in (a_samples or []) if usable(sample)]
    b_rows = [sample for sample in (b_samples or []) if usable(sample)]
    result = {
        "a_id": a_id, "b_id": b_id,
        "a_samples": len(a_rows), "b_samples": len(b_rows),
        "matched_pairs": 0, "coverage": 0.0, "pace_ratio": 0.0,
        "ci_low": 0.0, "ci_high": 0.0, "available": False,
    }
    smaller = min(len(a_rows), len(b_rows))
    if smaller < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"needs {MATCHED_PACE_MIN_PAIRS} timed turns per runtime"
        return result

    candidates = []
    for a_index, left in enumerate(a_rows):
        for b_index, right in enumerate(b_rows):
            cache_key = (id(left), id(right))
            if distance_cache is not None and cache_key in distance_cache:
                distance = distance_cache[cache_key]
            else:
                distance = pace_match_distance(left, right)
                if distance_cache is not None:
                    distance_cache[cache_key] = distance
            if distance is not None:
                candidates.append((distance, abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)),
                                   a_index, b_index))
    candidates.sort()
    used_a = set()
    used_b = set()
    ratios = []
    for _, _, a_index, b_index in candidates:
        if a_index in used_a or b_index in used_b:
            continue
        used_a.add(a_index)
        used_b.add(b_index)
        left_duration = float(a_rows[a_index].get("duration_s") or 0)
        right_duration = float(b_rows[b_index].get("duration_s") or 0)
        ratios.append(right_duration / left_duration)

    matched_pairs = len(ratios)
    coverage = matched_pairs / smaller if smaller else 0.0
    result["matched_pairs"] = matched_pairs
    result["coverage"] = coverage
    if ratios:
        result["pace_ratio"] = statistics.median(ratios)
    if matched_pairs >= MATCHED_PACE_MIN_PAIRS:
        result["ci_low"], result["ci_high"] = _bootstrap_median_interval(
            ratios, f"{a_id}|{b_id}|{matched_pairs}"
        )
    if matched_pairs < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"only {matched_pairs} comparable turns; needs {MATCHED_PACE_MIN_PAIRS}"
    elif coverage < MATCHED_PACE_MIN_COVERAGE:
        result["reason"] = f"only {round(coverage * 100)}% of the smaller history overlaps"
    else:
        result["available"] = True
        result["reason"] = ""
    return result


def matched_pace_windows(sample_groups, now_ts=None):
    """Build pairwise matched-pace comparisons for every dashboard history window."""
    today = datetime.date.fromtimestamp(float(now_ts if now_ts is not None else time.time()))
    digest = hashlib.sha256(today.isoformat().encode("utf-8"))
    signature_fields = (
        "duration_s", "ts", "day", "input_tokens", "peak_input_tokens",
        "cache_read_tokens", "output_tokens", "tool_calls", "model_calls",
    )
    for runtime_id in sorted(sample_groups):
        digest.update(b"\0runtime\0")
        digest.update(str(runtime_id).encode("utf-8", errors="replace"))
        for sample in sample_groups[runtime_id]:
            values = tuple(sample.get(field) for field in signature_fields)
            digest.update(b"\0sample\0")
            digest.update(repr(values).encode("utf-8", errors="replace"))
    signature = digest.hexdigest()

    with _matched_pace_cache_lock:
        if (
            _matched_pace_cache.get("signature") == signature
            and _matched_pace_cache.get("data") is not None
        ):
            return copy.deepcopy(_matched_pace_cache["data"])

        rules = {
            "today": ("exact", today.isoformat()),
            "yesterday": ("exact", (today - datetime.timedelta(days=1)).isoformat()),
            "7": ("since", (today - datetime.timedelta(days=6)).isoformat()),
            "30": ("since", (today - datetime.timedelta(days=29)).isoformat()),
            "90": ("since", (today - datetime.timedelta(days=89)).isoformat()),
            "all": ("all", ""),
        }
        result = {window: [] for window in rules}
        ids = sorted(sample_groups)
        windowed_samples = {}
        for runtime_id in ids:
            buckets = {window: [] for window in rules}
            for sample in sample_groups[runtime_id]:
                day = str(sample.get("day") or "")
                for window, (match, boundary) in rules.items():
                    if (
                        match == "all"
                        or (match == "exact" and day == boundary)
                        or (match == "since" and day >= boundary)
                    ):
                        buckets[window].append(sample)
            windowed_samples[runtime_id] = buckets
        for a_index, a_id in enumerate(ids):
            for b_id in ids[a_index + 1:]:
                distance_cache = {}
                for window in rules:
                    result[window].append(matched_pace_comparison(
                        a_id, windowed_samples[a_id][window],
                        b_id, windowed_samples[b_id][window],
                        distance_cache=distance_cache,
                    ))
        data = {
            "method": "nearest workload match on context, input, output, cache, model calls, tools, and recency",
            "min_pairs": MATCHED_PACE_MIN_PAIRS,
            "min_coverage": MATCHED_PACE_MIN_COVERAGE,
            "windows": result,
        }
        _matched_pace_cache["signature"] = signature
        _matched_pace_cache["data"] = data
        return copy.deepcopy(data)


def _finalize_throughput_fields(row):
    """Add weighted speed and coverage fields to a model or model/day row."""
    tool_free_samples = int(row.get("tool_free_samples") or 0)
    timed_samples = int(row.get("timed_samples") or 0)
    if tool_free_samples and row.get("tool_free_seconds", 0) > 0:
        speed_output = int(row.get("tool_free_output_tokens") or 0)
        speed_seconds = float(row.get("tool_free_seconds") or 0)
        basis = "tool_free"
        sample_count = tool_free_samples
    elif timed_samples and row.get("timed_seconds", 0) > 0:
        speed_output = int(row.get("timed_output_tokens") or 0)
        speed_seconds = float(row.get("timed_seconds") or 0)
        basis = "end_to_end"
        sample_count = timed_samples
    else:
        speed_output = 0
        speed_seconds = 0.0
        basis = "unavailable"
        sample_count = 0
    total_output = int(row.get("output_tokens") or 0)
    row["output_tps"] = (speed_output / speed_seconds) if speed_seconds > 0 else 0
    row["throughput_basis"] = basis
    row["throughput_samples"] = sample_count
    row["timing_coverage"] = (speed_output / total_output) if total_output > 0 else 0
    ttft_samples = int(row.get("ttft_samples") or 0)
    row["avg_ttft_ms"] = (float(row.get("ttft_total_s") or 0) * 1000 / ttft_samples) if ttft_samples else 0
    wait_samples = int(row.get("wait_samples") or 0)
    row["avg_wait_s"] = (float(row.get("wait_seconds") or 0) / wait_samples) if wait_samples else 0
    durations = sorted(
        float(value) for value in (row.get("wait_durations_s") or [])
        if float(value or 0) > 0
    )
    p95_index = min(len(durations) - 1, max(0, math.ceil(len(durations) * 0.95) - 1)) if durations else 0
    row["median_wait_s"] = statistics.median(durations) if durations else 0
    row["p95_wait_s"] = durations[p95_index] if durations else 0
    workload_fields = {
        "workload_peak_inputs": "median_peak_input_tokens",
        "workload_outputs": "median_workload_output_tokens",
        "workload_tool_calls": "median_tool_calls",
        "workload_model_calls": "median_model_calls",
        "workload_cache_ratios": "median_cache_ratio",
    }
    for source_key, target_key in workload_fields.items():
        values = [float(value) for value in (row.get(source_key) or []) if float(value or 0) >= 0]
        row[target_key] = statistics.median(values) if values else 0
    return row


def project_filter_key(value):
    """Keep project controls to folder-like paths; retain all other sessions together."""
    project = str(value or "").strip().replace("\\", "/")
    if (project.startswith("/") and not project.startswith("/private/")
            or project.startswith("~/") and not project.startswith((
                "~/.codex/", "~/Library/", "~/Documents/Codex/",
            ))
            or re.match(r"^[A-Za-z]:/", project)):
        return project
    return OTHER_LOCAL_SESSIONS_PROJECT


def delivery_project_label(value):
    """Return a compact project label without exposing its local path."""
    project = project_filter_key(value)
    if project == OTHER_LOCAL_SESSIONS_PROJECT:
        return ""
    root = os.path.abspath(os.path.expanduser(project))
    name = os.path.basename(root.rstrip(os.sep)) or "Project"
    suffix = git_delivery_service().project_suffix(root)
    return "{} · {}".format(name[:54], suffix)


def git_delivery_candidates(sources=None):
    """Derive bounded repository candidates from already-discovered projects."""
    candidates = []
    seen_roots = set()
    for source in list(sources or ()):
        raw_project = source.get("project") if isinstance(source, dict) else ""
        if project_filter_key(raw_project) == OTHER_LOCAL_SESSIONS_PROJECT:
            continue
        root = os.path.abspath(os.path.expanduser(raw_project))
        project = delivery_project_label(raw_project)
        if len(root) > 4096 or not project or root in seen_roots:
            continue
        candidates.append({"root": root, "project": project})
        seen_roots.add(root)
        if len(candidates) >= 50:
            break
    return candidates


def delivery_spend_rows(internal_rows):
    """Project daily cost and token totals into the content-free Git boundary."""
    rows = []
    for session in internal_rows or ():
        project = delivery_project_label(session.get("project"))
        if not project:
            continue
        availability = session.get("availability") or {}
        cost_available = availability.get("cost") is not False
        costs = session.get("_day_cost") or {}
        model_days = {}
        for daily in session.get("_model_daily") or ():
            day = daily.get("day") if isinstance(daily, dict) else ""
            if not isinstance(day, str) or len(day) != 10:
                continue
            target = model_days.setdefault(day, {
                "executions": 0,
                "cost_covered_executions": 0,
                "efficiency_covered_cost": 0.0,
                "covered_output_tokens": 0,
                "reasoning_tokens": 0,
                "reasoning_output_tokens": 0,
                "reasoning_executions": 0,
            })
            target["executions"] += max(0, int(daily.get("executions") or 0))
            target["cost_covered_executions"] += max(
                0, int(daily.get("cost_covered_executions") or 0),
            )
            try:
                efficiency_cost = float(daily.get("cost_covered_cost") or 0)
            except (TypeError, ValueError):
                efficiency_cost = 0.0
            if math.isfinite(efficiency_cost) and efficiency_cost >= 0:
                target["efficiency_covered_cost"] += efficiency_cost
            target["covered_output_tokens"] += max(
                0, int(daily.get("cost_covered_output_tokens") or 0),
            )
            target["reasoning_tokens"] += max(
                0, int(daily.get("reasoning_tokens") or 0),
            )
            target["reasoning_output_tokens"] += max(
                0, int(daily.get("reasoning_output_tokens") or 0),
            )
            target["reasoning_executions"] += max(
                0, int(daily.get("reasoning_executions") or 0),
            )
        days = list(costs)
        for day in model_days:
            if day not in days:
                days.append(day)
        for day in days:
            if not isinstance(day, str) or len(day) != 10:
                continue
            try:
                cost = float(costs.get(day) or 0)
            except (TypeError, ValueError):
                cost = 0.0
            if not math.isfinite(cost) or cost < 0:
                cost = 0.0
            row = {
                "project": project,
                "day": day,
                "covered_cost": cost if cost_available else 0.0,
                "cost_available": cost_available,
            }
            evidence = model_days.get(day)
            if evidence is not None:
                executions = evidence["executions"]
                covered_executions = min(
                    executions, evidence["cost_covered_executions"],
                )
                reasoning_executions = min(
                    executions, evidence["reasoning_executions"],
                )
                row.update({
                    "efficiency_covered_cost": evidence["efficiency_covered_cost"],
                    "covered_output_tokens": evidence["covered_output_tokens"],
                    "output_available": covered_executions > 0,
                    "output_partial": covered_executions < executions,
                    "reasoning_tokens": min(
                        evidence["reasoning_output_tokens"],
                        evidence["reasoning_tokens"],
                    ),
                    "reasoning_output_tokens": evidence["reasoning_output_tokens"],
                    "reasoning_available": (
                        reasoning_executions > 0
                        and evidence["reasoning_output_tokens"] > 0
                    ),
                    "reasoning_partial": reasoning_executions < executions,
                })
            rows.append(row)
    return rows


def git_delivery_service():
    """Return the process-local service backed by Token Meter's private ledger."""
    global _git_delivery_service_instance
    with _git_delivery_service_lock:
        if _git_delivery_service_instance is None:
            _git_delivery_service_instance = GitDeliveryService(
                TOKEN_METER_GIT_DELIVERY_DB,
            )
        return _git_delivery_service_instance


def bootstrap_git_delivery():
    """Seed readable local push history from the interactive installer context."""
    sources = all_session_sources()
    return git_delivery_service().scan(git_delivery_candidates(sources))


def git_delivery_state(project="", range_key="7"):
    """Build the bounded Git payload from watcher-owned source evidence."""
    if _xsess.get("data") is None:
        cross_session()
    internal_rows = _xsess.get("internal_rows") or ()
    candidates = git_delivery_candidates(_SOURCE_INVENTORY.get("sources") or ())
    projects = sorted({candidate["project"] for candidate in candidates})
    return git_delivery_service().query(
        project,
        range_key,
        delivery_spend_rows(internal_rows),
        projects,
        candidates,
    )


def clear_git_delivery_activity(confirm=False):
    """Clear only the Token Meter-owned hash-and-numbers Git ledger."""
    if confirm is not True:
        return {"ok": False, "error": "Explicit confirmation is required."}
    git_delivery_service().clear()
    _git_delivery_wake.set()
    return {"ok": True}


def git_delivery_watcher():
    """Inspect local successful-push reflogs every five minutes."""
    while True:
        if not _SOURCE_INVENTORY.get("ready"):
            _git_delivery_wake.wait(1.0)
            _git_delivery_wake.clear()
            continue
        candidates = git_delivery_candidates(_SOURCE_INVENTORY.get("sources") or ())
        git_delivery_service().scan(candidates)
        _git_delivery_wake.wait(GIT_DELIVERY_INTERVAL_S)
        _git_delivery_wake.clear()


def aggregate_model_stats(session_rows):
    return _domain_aggregate_model_stats(
        session_rows,
        runtime_resolver=source_runtime_label,
        throughput_finalizer=_finalize_throughput_fields,
        matched_pace=matched_pace_windows,
        project_option_limit=MODEL_PROJECT_OPTION_LIMIT,
        project_resolver=project_filter_key,
    )


def aggregate_language_signals(session_rows, terms=None):
    """Aggregate Positive and Friction lexical evidence without retaining messages."""
    settings = language_signal_settings()
    configured = {
        group: list((terms or {}).get(group, settings[group]))
        for group in ("positive", "friction")
    }
    results = {}
    for group in ("positive", "friction"):
        events = []
        for session in session_rows or []:
            runtime = session.get("runtime") or source_runtime_label(session)
            stored = session.get("_language_signal_events") or {}
            source_events = stored.get(group) if isinstance(stored, dict) else None
            if source_events is None and group == "friction":
                source_events = session.get("_frustration_events") or []
            for source_event in source_events or []:
                event = dict(source_event)
                model = event.get("model") or "unknown"
                event["runtime"] = runtime
                event["model_id"] = f"{model}::{runtime}"
                events.append(event)
        result = rollup_frustration_events(events)

        def session_rollup(session):
            stored = session.get("language_signals") or {}
            if isinstance(stored, dict) and group in stored:
                return stored.get(group) or {}
            return (session.get("frustration") or {}) if group == "friction" else {}

        result.update({
            "group": group,
            "configured_terms": configured[group],
            "default_terms": list(settings["defaults"][group]),
            "max_terms": settings["max_terms"],
            "matched_sessions": sum(
                1 for session in (session_rows or [])
                if (session_rollup(session).get("utterances") or 0) > 0
            ),
            "affected_sessions": sum(
                1 for session in (session_rows or [])
                if (session_rollup(session).get("utterances") or 0) > 0
            ),
            "sessions_with_user_turns": sum(
                1 for session in (session_rows or [])
                if (session_rollup(session).get("user_turns") or 0) > 0
            ),
            "method": settings["method"],
        })
        results[group] = result
    return {
        "positive": results["positive"],
        "friction": results["friction"],
        "configured_terms": configured,
        "default_terms": settings["defaults"],
        "max_terms": settings["max_terms"],
        "method": settings["method"],
    }


def aggregate_frustration(session_rows, terms=None):
    """Backward-compatible aggregate for the Friction language-signal group."""
    configured = None if terms is None else {"friction": terms}
    return aggregate_language_signals(session_rows, configured)["friction"]


def metric_coverage(rows, metric):
    return _domain_metric_coverage(rows, metric)


def canonical_aggregation_sources(sources):
    """Honor adapter-owned aggregation identities without generic ID deduplication."""
    source_rows = list(sources or ())
    groups = {}
    for index, source in enumerate(source_rows):
        key = source.get("_aggregation_key")
        if key:
            groups.setdefault(str(key), []).append((index, source))

    selected = {}
    for key, candidates in groups.items():
        def rank(indexed_source):
            _index, source = indexed_source
            try:
                activity = float(source.get("mtime") or 0)
            except (TypeError, ValueError, OverflowError):
                activity = 0.0
            try:
                signature = float(source.get("signature_mtime") or 0)
            except (TypeError, ValueError, OverflowError):
                signature = 0.0
            return (
                not bool(source.get("_aggregation_canonical")),
                -activity,
                -signature,
                str(source.get("path") or ""),
            )

        selected[key] = min(candidates, key=rank)[0]

    result = []
    for index, source in enumerate(source_rows):
        key = source.get("_aggregation_key")
        if not key or selected.get(str(key)) == index:
            result.append(source)
    return result


def cross_session(sources=None):
    now = time.time()
    if _xsess["data"] and (now - _xsess["at"] < _XSESS_TTL):
        return _xsess["data"]

    internal_rows = []

    source_rows = canonical_aggregation_sources(
        list(sources) if sources is not None else all_session_sources()
    )
    opencode_conn = None
    if any(source.get("provider") == "opencode" for source in source_rows):
        try:
            opencode_conn = _opencode_db_connection(opencode_db_path())
        except (OSError, sqlite3.Error):
            opencode_conn = None
    connection_manager = (
        contextlib.closing(opencode_conn) if opencode_conn is not None
        else contextlib.nullcontext(None)
    )
    with connection_manager as shared_opencode_conn:
        for source in source_rows:
            row = (
                session_summary(source, opencode_conn=shared_opencode_conn)
                if source.get("provider") == "opencode" and shared_opencode_conn is not None
                else session_summary(source)
            )
            if row["turns"] == 0:
                continue
            internal_rows.append(row)

    aggregate = _domain_aggregate_cross_session_rows(
        internal_rows, runtime_resolver=source_runtime_label,
    )
    sessions = aggregate["sessions"]
    _xsess["sessions"] = sessions
    mm = aggregate["model_mix"]
    daily_all = daily_summaries(internal_rows, limit=None)
    daily = daily_all[:30]
    spend = {"days": spend_projection(daily_all)}
    monthly = monthly_summaries(internal_rows)
    budgets = budget_settings()
    budget = monthly_budget_status(monthly, budgets)
    trend = aggregate["trend"]
    total = aggregate["total_cost"]
    model_name_cost = aggregate["model_name_cost"]
    premium = (model_name_cost.get("claude-opus-5", 0)
               + model_name_cost.get("claude-opus-4-8", 0)
               + model_name_cost.get("claude-fable-5", 0)
               + model_name_cost.get("gpt-5.5", 0))
    tool_waste = global_tool_waste(internal_rows)
    coverage = aggregate["coverage"]
    provenance = aggregate["provenance"]
    global_wait = wait_time_summary([
        sample
        for row in internal_rows
        for sample in (row.get("_wait_samples") or [])
    ])
    language_signals = aggregate_language_signals(internal_rows)
    data = {
        "generated_at": int(now),
        "sessions": sessions[:60],
        "current_sessions": current_session_summaries(internal_rows, now=now),
        "model_mix": mm,
        "trend": trend,
        "total_cost": total,
        "reported_cost": aggregate["reported_cost"],
        "estimated_cost": aggregate["estimated_cost"],
        "total_sessions": aggregate["total_sessions"],
        "total_executions": aggregate["total_executions"],
        "total_tokens": aggregate["total_tokens"],
        "coverage": coverage, "provenance": provenance,
        "usage_basis": provenance["usage_basis"],
        "availability": {
            "cost": coverage["cost"]["covered_sessions"] > 0,
            "tokens": coverage["tokens"]["covered_sessions"] > 0,
            "input_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "output_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "cache": coverage["cache"]["covered_sessions"] > 0,
            "throughput": coverage["tokens"]["covered_sessions"] > 0,
            "context": True, "timing": bool(global_wait.get("available")), "tool_results": True,
        },
        "wait_time": global_wait,
        "opus_share": (premium / total) if total else 0.0,
        "premium_share": (premium / total) if total else 0.0,
        "providers": aggregate["providers"],
        "model_stats": aggregate_model_stats(internal_rows),
        "language_signals": language_signals,
        "frustration": language_signals["friction"],
        "model_pricing": model_pricing_settings(),
        "budgets": budgets,
        "budget": budget,
        "tool_waste": tool_waste,
        "daily": daily,
        "spend": spend,
        "monthly": monthly,
        "capabilities": capability_inventory(tool_waste),
        "session_actions": session_action_capability(),
    }
    _xsess["internal_rows"] = tuple(internal_rows)
    _xsess["project_model_stats"] = {}
    _xsess["data"], _xsess["at"] = data, now
    return data


def project_model_stats(project):
    """Return aggregate-only model evidence for one exact discovered project."""
    project = str(project or "")
    if not project or len(project) > 1024:
        return {"ok": False, "error": "A valid project is required."}, 400
    cross = cross_session()
    cache = _xsess.setdefault("project_model_stats", {})
    cached = cache.get(project)
    if cached is not None:
        return {
            "ok": True,
            "generated_at": cross.get("generated_at"),
            "model_stats": cached,
        }, 200
    matching = [
        row for row in (_xsess.get("internal_rows") or ())
        if project_filter_key(row.get("project")) == project
    ]
    if not matching:
        return {"ok": False, "error": "Project was not found."}, 404
    stats = aggregate_model_stats(matching)
    stats.pop("projects", None)
    stats.pop("projects_truncated", None)
    cache[project] = stats
    return {
        "ok": True,
        "generated_at": cross.get("generated_at"),
        "model_stats": stats,
    }, 200


def log_sessions_state():
    """Return the complete lightweight log inventory outside the polled state payload."""
    cross = _xsess.get("data")
    if not cross:
        return {
            "generated_at": None,
            "sessions": [],
            "total_sessions": None,
            "loading": True,
        }
    sessions = list(_xsess.get("sessions") or cross.get("sessions") or [])
    return {
        "generated_at": cross.get("generated_at"),
        "sessions": sessions,
        "total_sessions": int(cross.get("total_sessions") or len(sessions)),
        "loading": False,
    }


def spend_logs_state(start_day, end_day):
    """Return every spend-contributing session in one inclusive date range."""
    try:
        start = datetime.date.fromisoformat(str(start_day or ""))
        end = datetime.date.fromisoformat(str(end_day or ""))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "From and to must be valid dates in YYYY-MM-DD format.",
        }, 400
    if start > end:
        return {
            "ok": False,
            "error": "From cannot be later than to.",
        }, 400
    cross = cross_session()
    start_key, end_key = start.isoformat(), end.isoformat()
    sessions = spend_log_summaries(
        _xsess.get("internal_rows") or (), start_key, end_key,
    )
    return {
        "ok": True,
        "generated_at": cross.get("generated_at"),
        "from": start_key,
        "to": end_key,
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_cost": sum(float(row.get("cost") or 0) for row in sessions),
    }, 200


def enqueue_latest(q_, data):
    """Keep a slow SSE client subscribed by replacing queued stale snapshots."""
    try:
        q_.put_nowait(data)
        return True
    except queue.Full:
        try:
            while True:
                q_.get_nowait()
        except queue.Empty:
            pass
        try:
            q_.put_nowait(data)
        except queue.Full:
            pass
        return True
    except Exception:
        return False


def publish(state):
    global STATE
    STATE = state
    data = "data: " + json.dumps(state) + "\n\n"
    with subscribers_lock:
        dead = []
        for q_ in subscribers:
            if not enqueue_latest(q_, data):
                dead.append(q_)
        for d in dead:
            subscribers.remove(d)


def source_mtime_signature(sources):
    """Track additions, removals, trace updates, and display metadata changes."""
    return tuple(sorted(
        (str(source.get("path") or ""),
         source_revision_signature(source))
        for source in (sources or [])
        if source.get("path")
    ))


def source_identity_signature(sources):
    """Track session membership independently from ordinary log updates."""
    return tuple(sorted(
        (str(source.get("provider") or ""),
         str(source.get("id") or ""),
         str(source.get("path") or ""))
        for source in (sources or [])
        if source.get("id") or source.get("path")
    ))


def source_membership_probe_due(now, last_probe):
    """Check cheap filesystem revisions every two seconds."""
    return now - last_probe >= _SOURCE_MEMBERSHIP_PROBE_S


def source_membership_fallback_due(now, last_fallback):
    """Bound path-only session discovery to a four-second worst case."""
    return now - last_fallback >= _SOURCE_MEMBERSHIP_FALLBACK_S


def source_discovery_refresh_due(inventory_ready, now, last_refresh):
    """Bound adapter-wide enumeration while keeping known-file checks fast."""
    return bool(
        not inventory_ready
        or now - last_refresh >= _SOURCE_DISCOVERY_REFRESH_S
    )


def _source_inventory_roots():
    return (
        CLAUDE_PROJECTS,
        *CLAUDE_DESKTOP_DATA_ROOTS,
        CODEX_SESSIONS,
        CURSOR_PROJECTS,
        KIRO_SESSIONS,
        KIRO_AGENT_STORAGE,
        PI_AGENT_DIR,
        os.path.join(PI_AGENT_DIR, "sessions"),
        GROK_HOME,
        os.path.join(GROK_HOME, "sessions"),
    )


def _physical_source_path(value):
    value = str(value or "")
    if not value or not os.path.isabs(os.path.expanduser(value)):
        return ""
    return os.path.abspath(os.path.expanduser(value))


def source_inventory_probe_targets(sources, roots=None, extra_files=None):
    """Build a bounded target set for cheap new/resumed-session detection."""
    roots = tuple(
        path for path in (
            _physical_source_path(value)
            for value in (_source_inventory_roots() if roots is None else roots)
        ) if path
    )
    if extra_files is None:
        database = opencode_db_path()
        extra_files = (database, database + "-wal")
    targets = set(roots)

    def add_path_and_ancestors(value):
        path = _physical_source_path(value)
        if not path:
            return
        targets.add(path)
        if os.path.isdir(path):
            try:
                with os.scandir(path) as entries:
                    targets.update(
                        entry.path for entry in entries
                        if entry.is_file(follow_symlinks=False)
                    )
            except OSError:
                pass
            parent = path
        else:
            parent = os.path.dirname(path)
        matching_roots = []
        for root in roots:
            try:
                if os.path.commonpath((path, root)) == root:
                    matching_roots.append(root)
            except ValueError:
                continue
        boundary = max(matching_roots, key=len) if matching_roots else parent
        while parent:
            targets.add(parent)
            if parent == boundary:
                break
            next_parent = os.path.dirname(parent)
            if next_parent == parent:
                break
            parent = next_parent

    for source in sources or ():
        add_path_and_ancestors(source.get("path"))
        add_path_and_ancestors(source.get("metadata_path"))
    for value in extra_files or ():
        add_path_and_ancestors(value)
    return tuple(sorted(targets))


def source_inventory_probe_signature(targets, current_path=""):
    """Stat probe targets while ignoring ordinary growth of the active trace."""
    current_path = _physical_source_path(current_path)
    current_is_dir = bool(current_path and os.path.isdir(current_path))
    signature = []
    for value in targets or ():
        path = _physical_source_path(value)
        if not path:
            continue
        is_current = path == current_path or bool(
            current_is_dir and path.startswith(current_path + os.sep)
        )
        if is_current:
            signature.append((path, "active", 0, 0))
            continue
        try:
            row = os.stat(path)
        except OSError:
            signature.append((path, "missing", 0, 0))
            continue
        is_directory = stat.S_ISDIR(row.st_mode)
        signature.append((
            path,
            "directory" if is_directory else "file",
            int(row.st_mtime_ns),
            0 if is_directory else int(row.st_size),
        ))
    return tuple(signature)


def runtime_candidate_paths():
    """Enumerate path identities only, without parsing adapter metadata."""
    paths = set()
    claude = _claude_native_adapter()
    paths.update(claude._glob(str(claude.projects_root / "*" / "*.jsonl")))
    paths.update(claude.desktop_metadata_paths())
    paths.update(_codex_native_adapter()._paths())
    paths.update(_cursor_native_adapter()._transcript_paths())
    kiro = _kiro_native_adapter()
    paths.update(kiro._glob(str(kiro.sessions_root / "*" / "*" / "messages.jsonl")))
    paths.update(kiro._glob(str(kiro.sessions_root / "cli" / "*.jsonl")))
    if kiro.agent_storage_root is not None:
        paths.update(kiro._glob(str(kiro.agent_storage_root / "*" / "*")))
    return tuple(sorted(
        path for path in (_physical_source_path(value) for value in paths)
        if path
    ))


def refresh_known_source_activity(sources, current_path):
    """Refresh the active file without rediscovering every adapter."""
    current_path = str(current_path or "")
    rows = sources or ()
    refreshed = None
    for index, source in enumerate(rows):
        path = str(source.get("path") or "")
        if (not path or path != current_path
                or source.get("provider") == "opencode"):
            continue
        trace_mtime = safe_mtime(path)
        current_mtime = float(source.get("mtime") or 0)
        if trace_mtime <= current_mtime:
            continue
        row = dict(source)
        row["mtime"] = trace_mtime
        if "trace_mtime" in row:
            row["trace_mtime"] = trace_mtime
        if "signature_mtime" in row:
            row["signature_mtime"] = max(
                trace_mtime, float(row.get("signature_mtime") or 0),
            )
        if refreshed is None:
            refreshed = list(rows)
        refreshed[index] = row
    return refreshed if refreshed is not None else sources


def cross_session_refresh_due(dirty, membership_changed, now, last_refresh):
    """Refresh membership immediately while throttling ordinary log updates."""
    return bool(
        dirty and (
            membership_changed
            or now - last_refresh >= _XSESS_LIVE_REFRESH_S
        )
    )


def current_session_refresh_due(signature, last_signature, now, last_refresh):
    """Observe revisions every watcher tick but coalesce whole-trace rebuilds."""
    if signature == last_signature:
        return False
    return bool(
        last_signature is None
        or now - last_refresh >= _CURRENT_SESSION_REBUILD_S
    )


def refresh_cross_session_state(state=None, builder=None, publisher=None):
    """Force a fresh cross-log snapshot and publish it with the live state."""
    builder = builder or cross_session
    publisher = publisher or publish
    _xsess["data"], _xsess["at"] = None, 0.0
    cross = builder()
    base = dict(state or STATE or {})
    if base:
        publisher(attach_cross_session(base, cross))
    return cross


def publish_after_session_delete():
    """Publish a fresh remaining-session state immediately after deletion."""
    sources = all_session_sources()
    next_source = max(sources, key=lambda row: row.get("mtime") or 0) if sources else None
    cross = cross_session()
    if next_source:
        state = recompute(next_source)
        if state:
            publish(attach_cross_session(state, cross))
            return next_source.get("id")
    publish({
        "ok": False,
        "message": "No {} logs found yet.".format(supported_runtime_phrase()),
        "source": {},
        "total_cost": 0,
        "total_tokens": 0,
        "turns": 0,
        "xsession": cross,
    })
    return None


def current_state():
    if STATE:
        return STATE
    inventory = _SOURCE_INVENTORY
    count = inventory.get("count")
    if inventory.get("ready") and count is not None:
        message = f"Token Meter is indexing {count:,} local session{'s' if count != 1 else ''}."
    else:
        message = "Token Meter is discovering local session history."
    return {
        "ok": False,
        "loading": True,
        "message": message,
        "source": {},
        "total_cost": 0,
        "total_tokens": 0,
        "turns": 0,
        "context": {},
        "insights": [],
    }


AGENT_RESULT_MAX_CHARS = 8000
AGENT_CHECK_FOCUS = {"continue", "cost", "context", "tools", "next_phase"}
AGENT_USAGE_WINDOWS = {"today": 1, "7d": 7, "14d": 14}
AGENT_USAGE_FOCUS = {"spend", "models", "tools", "changes"}


def agent_as_of():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def agent_project_key(value):
    value = str(value or "").strip()
    if not value or value == "No project":
        return ""
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def agent_project_name(value):
    value = str(value or "").strip().rstrip("/\\")
    if not value or value == "No project":
        return "No project"
    return compact_text(value.replace("\\", "/").rsplit("/", 1)[-1], 52)


def agent_provider(value):
    value = str(value or "").strip().lower()
    for runtime_id in sorted(runtime_registry().runtime_ids, key=len, reverse=True):
        prefixes = tuple(runtime_id + separator for separator in ("-", "_", " "))
        if value == runtime_id or value.startswith(prefixes):
            return runtime_id
    return ""


def resolve_agent_source(session_id=None, caller=None, sources=None):
    """Resolve a safe current-run target without crossing a caller's runtime/project."""
    sources = list(sources if sources is not None else all_session_sources())
    requested = str(session_id or "").strip()
    if requested:
        source = find_session(requested, sources=sources)
        if not source:
            return None, "The requested Token Meter session was not found."
        return source, "explicit"

    caller = caller or {}
    provider = agent_provider(caller.get("runtime") or caller.get("provider"))
    project = agent_project_key(caller.get("project") or caller.get("cwd"))
    candidates = [row for row in sources if not provider or row.get("provider") == provider]
    if project:
        exact_matches = []
        ancestor_matches = []
        for row in candidates:
            candidate = agent_project_key(row.get("project"))
            if not candidate:
                continue
            if candidate == project:
                exact_matches.append(row)
            elif project.startswith(candidate + os.sep):
                ancestor_matches.append((candidate, row))
        matches = exact_matches
        if not matches and ancestor_matches:
            nearest_length = max(len(candidate) for candidate, _ in ancestor_matches)
            matches = [row for candidate, row in ancestor_matches if len(candidate) == nearest_length]
        if not matches:
            runtime = runtime_display_label(provider) if provider else "agent"
            return None, f"No {runtime} run matched the caller's current project."
        candidates = matches
    if not candidates:
        return None, "No matching {} run was found.".format(supported_runtime_phrase())
    selected = max(candidates, key=lambda row: float(row.get("mtime") or 0))
    mtime = float(selected.get("mtime") or 0)
    if not mtime or time.time() - mtime > AGENT_CURRENT_MAX_AGE_S:
        runtime = runtime_display_label(provider) if provider else "agent"
        return None, f"No recent {runtime} run matched the caller's current project."
    return selected, "matched"


def agent_session_summary(source):
    return {
        "id": source.get("id"),
        "provider": source.get("provider"),
        "client": source.get("client") or source.get("provider"),
    }


def agent_dashboard_url(session_id=None, panel="summary"):
    base = f"http://127.0.0.1:{PORT}"
    if session_id:
        return f"{base}/sessions/{quote(str(session_id), safe='')}#{panel}"
    return f"{base}/#{panel}"


def compact_agent_value(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, str):
        return compact_text(value, 500)
    if isinstance(value, list):
        return [compact_agent_value(item, depth + 1) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): compact_agent_value(item, depth + 1)
                for key, item in list(value.items())[:50]}
    return value


def bounded_agent_result(result):
    """Keep a model-facing result useful even if an upstream label grows unexpectedly."""
    result = dict(result or {})
    result["evidence"] = list(result.get("evidence") or [])[:3]
    if isinstance(result.get("candidates"), list):
        result["candidates"] = result["candidates"][:5]
    result.setdefault("truncated", False)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= AGENT_RESULT_MAX_CHARS:
        return result
    result = compact_agent_value(result)
    result["truncated"] = True
    result["evidence"] = list(result.get("evidence") or [])[:2]
    if isinstance(result.get("candidates"), list):
        result["candidates"] = result["candidates"][:3]
    if isinstance(result.get("categories"), list):
        result["categories"] = result["categories"][:3]
    for key in ("answer", "caveat", "recommended_action"):
        if isinstance(result.get(key), str):
            result[key] = compact_text(result[key], 240)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > AGENT_RESULT_MAX_CHARS:
        result.pop("categories", None)
        result.pop("candidates", None)
        result.pop("execution", None)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > AGENT_RESULT_MAX_CHARS:
        result["evidence"] = []
    return result


def agent_no_session(message, panel="summary"):
    return bounded_agent_result({
        "ok": False,
        "answer": message,
        "verdict": {"key": "unavailable", "label": "Run not matched", "severity": "idle"},
        "evidence": [],
        "recommended_action": "Open Token Meter and choose the intended run, then ask again with its session id.",
        "caveat": "Token Meter did not fall back to another project because that could expose the wrong run.",
        "dashboard_url": agent_dashboard_url(panel=panel),
        "as_of": agent_as_of(),
        "data_scope": "matched_current_run",
        "approximate_fields": [],
    })


def safe_execution_trace(state, execution_idx, limit=5):
    allowed = {"tool_call", "tool_result", "usage", "complete", "context", "reasoning", "coordination", "start"}
    out = []
    for event in state.get("trace") or []:
        if event.get("execution") != execution_idx or event.get("kind") not in allowed:
            continue
        item = {"kind": event.get("kind"), "label": compact_text(event.get("label") or "Activity", 64)}
        if event.get("kind") == "tool_result" and event.get("tokens") is not None:
            item["returned_tokens"] = int(event.get("tokens") or 0)
        if event.get("cost"):
            item["cost"] = round(float(event.get("cost") or 0), 4)
        out.append(item)
    return out[:limit]


def agent_check(focus="continue", execution=None, session_id=None, caller=None):
    focus = str(focus or "continue").strip().lower()
    if focus not in AGENT_CHECK_FOCUS:
        raise ValueError(f"focus must be one of: {', '.join(sorted(AGENT_CHECK_FOCUS))}")
    if execution is not None:
        try:
            execution = int(execution)
        except (TypeError, ValueError):
            raise ValueError("execution must be a positive integer")
        if execution < 1:
            raise ValueError("execution must be a positive integer")

    source, resolution = resolve_agent_source(session_id=session_id, caller=caller)
    if not source:
        return agent_no_session(resolution)
    state = recompute(source)
    if not state:
        return agent_no_session("Token Meter found the run but could not read its metrics.")

    recommendation = menubar_recommendation(state)
    verdict = menubar_verdict(state, recommendation)
    context = state.get("context") or {}
    tools = state.get("tools") or {}
    executions = state.get("executions") or []
    last_execution = executions[-1] if executions else {}
    requested_execution = None
    if execution is not None:
        requested_execution = next((row for row in executions if int(row.get("idx") or 0) == execution), None)
        if requested_execution is None:
            raise ValueError(f"execution {execution} is not available in the retained run history")
        last_execution = requested_execution

    total_cost = round(float(state.get("total_cost") or 0), 4)
    availability = state.get("availability") or {}
    cost_available = metric_available(state, "cost")
    tokens_available = metric_available(state, "tokens")
    tool_results_available = metric_available(state, "tool_results")
    context_pct = context.get("latest_pct")
    context_text = f"{context_pct * 100:.0f}%" if context_pct is not None else "not reported"
    tool_tokens = int(tools.get("total_output_tokens") or 0)
    flagged_tokens = int(tools.get("flagged_tokens") or 0)
    selected_tool_tokens = int((last_execution.get("tokens") or {}).get("retrieval") or 0)
    selected_execution_label = "Selected execution" if requested_execution is not None else "Latest execution"
    cursor_estimate = source.get("provider") == "cursor"
    evidence_pool = {
        "cost": ({"label": "Local run cost estimate" if cursor_estimate else "Estimated run cost",
                  "value": total_cost, "unit": "USD"}
                 if cost_available else
                 {"label": "Run cost", "value": "unavailable",
                  "reason": "No supported rate or input-context evidence"}),
        "last_cost": ({"label": f"{selected_execution_label} cost" +
                                 (" estimate" if cursor_estimate else ""),
                       "value": round(float(last_execution.get("cost") or 0), 4), "unit": "USD"}
                      if cost_available else
                      {"label": f"{selected_execution_label} cost", "value": "unavailable"}),
        "context": {"label": "Current context use", "value": context_text},
        "latest_tools": ({"label": f"{selected_execution_label} tool results",
                          "value": selected_tool_tokens, "unit": "estimated tokens"}
                         if tool_results_available else
                         {"label": f"{selected_execution_label} tool results", "value": "unavailable"}),
        "run_tools": ({"label": "Run-wide trace-observed tool results", "value": tool_tokens,
                       "unit": "estimated tokens", "flagged_tokens": flagged_tokens}
                      if tool_results_available else
                      {"label": "Run-wide tool results", "value": "unavailable"}),
        "turns": {"label": "Executions", "value": int(state.get("turns") or len(executions))},
    }
    order = {
        "continue": ("context", "last_cost", "latest_tools"),
        "cost": ("cost", "last_cost", "turns"),
        "context": ("context", "turns", "cost"),
        "tools": ("run_tools", "latest_tools", "context"),
        "next_phase": ("context", "cost", "run_tools"),
    }[focus]
    evidence = [evidence_pool[key] for key in order]
    action = recommendation.get("label") or "Review the selected run"
    if recommendation.get("detail"):
        action = f"{action}: {recommendation['detail']}"
    selected = agent_session_summary(source)
    answer = verdict.get("detail") or recommendation.get("detail") or "Token Meter found no immediate intervention signal."
    latest_cost = round(float(last_execution.get("cost") or 0), 4)
    if focus == "cost":
        if cost_available:
            answer = (f"Estimated run cost is ${total_cost:.2f}; "
                      f"the {selected_execution_label.lower()} cost is ${latest_cost:.2f}.")
            action = "Review the run and latest-execution cost before approving more work."
        else:
            answer = "Run cost is unavailable because this trace lacks supported pricing or input-context evidence."
            action = "Use a cost-covered trace before making a cost-based decision."
    elif focus == "context":
        answer = f"Current context use is {context_text}."
        if context_pct is None:
            action = "Confirm the model context window before making a context-pressure decision."
        else:
            action = f"Use the {context_text} context signal when deciding whether to continue or start fresh."
    elif focus == "tools":
        if tool_results_available:
            answer = (f"This run has {tool_tokens:,} trace-observed tool-result tokens; "
                      f"the {selected_execution_label.lower()} has {selected_tool_tokens:,}.")
            if flagged_tokens:
                action = (f"Review the {flagged_tokens:,} flagged tool-result tokens and narrow oversized, "
                          "repeated, or failed results where practical.")
            else:
                action = "Keep tool calls scoped and request only the result fields needed for the next step."
        else:
            answer = "Tool-result volume is unavailable because this runtime did not expose supported result evidence."
            action = "Use a trace with tool-result evidence before making a tool-efficiency decision."
    elif focus == "next_phase":
        cost_text = f"${total_cost:.2f}" if cost_available else "unavailable"
        tools_text = f"{tool_tokens:,} tokens" if tool_results_available else "unavailable"
        answer = (f"Next-phase readiness: context is {context_text}, estimated run cost is {cost_text}, "
                  f"and trace-observed tool results total {tools_text}.")
        action = "Approve the next phase only if its expected value justifies these cost, context, and tool signals."
    result = {
        "ok": True,
        "answer": answer,
        "verdict": {
            **{key: verdict.get(key) for key in ("key", "label", "severity")},
            "detail": answer,
        },
        "evidence": evidence,
        "recommended_action": compact_text(action, 220),
        "caveat": ("Cursor input is a local one-context-snapshot-per-execution proxy; output uses trace-visible model text. Cost applies the persisted model variant's public rate. Cache, hidden reasoning, repeated internal model-call input, and authoritative dashboard billing are excluded."
                   if cursor_estimate else
                   "Costs are estimates based on public API rates." if state.get("cost_approx") else
                   "Tool-result volume is trace-observed and may not include content the client did not log."),
        "dashboard_url": agent_dashboard_url(source.get("id"), "summary"),
        "as_of": agent_as_of(),
        "data_scope": "matched_current_run",
        "approximate_fields": (["cost"] if state.get("cost_approx") and cost_available else []) +
                              (["input_tokens", "output_tokens", "output_pace"] if cursor_estimate and tokens_available else []) +
                              (["tool_result_tokens"] if cursor_estimate and tool_results_available else []),
        "availability": availability,
        "selected_session": selected,
        "selection": resolution,
    }
    if requested_execution is not None:
        result["execution"] = {
            "index": execution,
            "cost": round(float(requested_execution.get("cost") or 0), 4) if cost_available else None,
            "tokens": ({
                "input": int((requested_execution.get("tokens") or {}).get("input") or 0),
                "output": int((requested_execution.get("tokens") or {}).get("output") or 0),
                "retrieval": int((requested_execution.get("tokens") or {}).get("retrieval") or 0),
            } if tokens_available else {"available": False,
                                        "retrieval_estimate": selected_tool_tokens if tool_results_available else None}),
            "context_pct": requested_execution.get("context_pct"),
            "activity": safe_execution_trace(state, execution),
        }
        if cursor_estimate:
            result["execution"]["estimated"] = True
    return bounded_agent_result(result)


def agent_usage_day_keys(window, today=None):
    today = today or datetime.date.today()
    day_count = AGENT_USAGE_WINDOWS[window]
    return {
        (today - datetime.timedelta(days=offset)).isoformat()
        for offset in range(day_count)
    }


def agent_usage_model_categories(session_rows, day_keys, limit=5):
    categories = {}
    for session in session_rows or []:
        runtime = session.get("runtime") or source_runtime_label(session)
        for daily in session.get("_model_daily") or []:
            if daily.get("day") not in day_keys:
                continue
            model = daily.get("model") or "unknown"
            key = (model, runtime)
            row = categories.setdefault(key, {
                "model": model, "runtime": runtime, "cost": 0.0,
                "tokens": 0, "executions": 0,
            })
            row["cost"] += float(daily.get("cost") or 0)
            row["tokens"] += (int(daily.get("input_tokens") or 0)
                              + int(daily.get("output_tokens") or 0))
            row["executions"] += int(daily.get("executions") or 0)
    ranked = sorted(
        categories.values(),
        key=lambda row: (-row["cost"], -row["tokens"], row["model"], row["runtime"]),
    )[:limit]
    for row in ranked:
        row["cost"] = round(row["cost"], 4)
    return ranked


def agent_usage_tool_categories(session_rows, day_keys, limit=5):
    categories = {}
    total_tokens = 0
    flagged_tokens = 0
    for session in session_rows or []:
        evidence = session.get("_tool_evidence") or {}
        total_tokens += sum(
            int(value or 0) for day, value in (evidence.get("day_tokens") or {}).items()
            if day in day_keys
        )
        flagged_tokens += sum(
            int(value or 0) for day, value in (evidence.get("day_flagged") or {}).items()
            if day in day_keys
        )
        provider = session.get("provider") or "unknown"
        for tool in evidence.get("tools") or []:
            name = tool.get("name") or "?"
            namespace = tool.get("namespace") or "unknown"
            kind = tool.get("kind") or "tool"
            diagnostic = kind == "mcp" and (
                namespace == "tokenmeter" or str(name).startswith("mcp__tokenmeter__")
            )
            if diagnostic:
                continue
            key = (name, namespace, "" if kind == "mcp" else provider)
            row = categories.setdefault(key, {
                "name": tool.get("display") or name,
                "namespace": namespace, "returned_tokens": 0, "calls": 0,
            })
            daily_rows = tool.get("daily") or []
            if isinstance(daily_rows, dict):
                daily_rows = daily_rows.values()
            for daily in daily_rows:
                if daily.get("day") not in day_keys:
                    continue
                row["returned_tokens"] += int(daily.get("output_tokens") or 0)
                row["calls"] += int(daily.get("calls") or 0)
    ranked = sorted(
        (row for row in categories.values() if row["calls"] or row["returned_tokens"]),
        key=lambda row: (-row["returned_tokens"], -row["calls"], row["name"]),
    )[:limit]
    return ranked, total_tokens, flagged_tokens


def agent_usage(window="7d", focus="changes"):
    window = str(window or "7d").strip().lower()
    focus = str(focus or "changes").strip().lower()
    if window not in AGENT_USAGE_WINDOWS:
        raise ValueError("window must be one of: today, 7d, 14d")
    if focus not in AGENT_USAGE_FOCUS:
        raise ValueError(f"focus must be one of: {', '.join(sorted(AGENT_USAGE_FOCUS))}")
    cross = cross_session()
    days = sorted(cross.get("daily") or [], key=lambda row: row.get("day") or "", reverse=True)
    day_keys = agent_usage_day_keys(window)
    selected = [row for row in days if row.get("day") in day_keys]
    total_cost = sum(float(row.get("cost") or 0) for row in selected)
    sessions = sum(int(row.get("sessions") or 0) for row in selected)
    providers = defaultdict(float)
    for row in selected:
        for provider in row.get("providers") or []:
            if metric_available(provider, "cost"):
                providers[provider.get("provider") or "unknown"] += float(provider.get("cost") or 0)
    provider_rank = sorted(providers.items(), key=lambda item: (-item[1], item[0]))[:5]
    internal_rows = list(_xsess.get("internal_rows") or ())
    model_rank = agent_usage_model_categories(internal_rows, day_keys)
    tool_rank, tool_tokens, flagged_tokens = agent_usage_tool_categories(
        internal_rows, day_keys,
    )

    newest = selected[0] if selected else {}
    previous = selected[1] if len(selected) > 1 else {}
    newest_cost = float(newest.get("cost") or 0)
    previous_cost = float(previous.get("cost") or 0)
    delta = ((newest_cost - previous_cost) / previous_cost) if previous_cost else None
    cost_complete = all(((row.get("coverage") or {}).get("cost") or {}).get("complete", True)
                        for row in selected)
    evidence_pool = {
        "spend": {"label": f"Estimated spend ({window})" + ("" if cost_complete else " · partial coverage"),
                  "value": round(total_cost, 4), "unit": "USD", "complete": cost_complete},
        "sessions": {"label": "Daily run count summed", "value": sessions},
        "tools": {"label": "Trace-observed tool results", "value": tool_tokens, "unit": "tokens", "flagged_tokens": flagged_tokens},
        "change": {"label": "Latest day vs prior day", "value": f"{delta * 100:+.0f}%" if delta is not None else "No prior-day baseline"},
        "provider": {"label": "Largest runtime by spend", "value": provider_rank[0][0] if provider_rank else "No data",
                     "cost": round(provider_rank[0][1], 4) if provider_rank else 0},
    }
    order = {
        "spend": ("spend", "change", "provider"),
        "models": ("spend", "provider", "change"),
        "tools": ("tools", "spend", "change"),
        "changes": ("change", "spend", "tools"),
    }[focus]
    if not selected:
        answer = f"Token Meter has no aggregate usage for {window}."
        action = "Run {}, then ask again after Token Meter observes a local trace.".format(
            supported_runtime_phrase()
        )
        assessment = "No data"
    elif focus == "spend":
        answer = (f"Estimated spend among cost-covered logs is ${total_cost:.2f} across the selected {window} window"
                  + ("; one or more traces lack pricing evidence." if not cost_complete else ", with no strong change signal."))
        if delta is not None and delta >= 0.25:
            answer = (f"Estimated spend is ${total_cost:.2f} across {window}; the latest day is "
                      f"{delta * 100:.0f}% more expensive than the prior recorded day.")
            assessment = "Spend increased"
        else:
            assessment = "Spend stable"
        action = "Review the spend total, daily change, and largest runtime before approving more work."
    elif focus == "models":
        if model_rank:
            largest = model_rank[0]
            answer = (f"{largest['model']} is the largest model category in {window} at "
                      f"${float(largest.get('cost') or 0):.2f}.")
            action = "Review the largest model category and test a cheaper model only where task quality holds."
            assessment = "Model mix available"
        else:
            answer = f"Token Meter found spend in {window}, but no window-specific model category evidence."
            action = "Use a trace with model-attributed usage before making a model decision."
            assessment = "Model mix unavailable"
    elif focus == "tools":
        answer = f"Trace-observed tool results returned {tool_tokens:,} tokens in {window}."
        if tool_rank:
            answer += (f" {tool_rank[0]['name']} was the largest returned-token category at "
                       f"{int(tool_rank[0].get('returned_tokens') or 0):,} tokens.")
        if flagged_tokens and flagged_tokens / max(1, tool_tokens) >= 0.25:
            action = "Review the largest tool category and narrow repeated, failed, or oversized results."
            assessment = "Tool output needs review"
        else:
            action = "Keep tool requests scoped to the result fields needed for the task."
            assessment = "Tool output stable"
    elif delta is not None:
        answer = f"The latest day changed {delta * 100:+.0f}% from the prior recorded day."
        action = "Review the daily spend change and compare again after another recorded day."
        assessment = "Spend increased" if delta >= 0.25 else "Daily change available"
    else:
        answer = "Token Meter has no prior recorded day in this window for a change comparison."
        action = "Compare again after another recorded day."
        assessment = "No change baseline"
    approximate = any(bool(row.get("cost_approx")) for row in (cross.get("sessions") or []))
    result = {
        "ok": bool(selected),
        "answer": answer,
        "assessment": assessment,
        "evidence": [evidence_pool[key] for key in order],
        "recommended_action": action,
        "caveat": ("History is aggregate-only; run titles, project names, session ids, and paths are omitted. "
                   + ("Cursor values are local context-and-visible-output proxies, not dashboard billing. "
                      if any(row.get("provider") == "cursor" for row in (cross.get("sessions") or [])) else "")
                   + ("Spend is partial because one or more traces lack pricing evidence."
                      if not cost_complete else "")),
        "coverage": {"cost_complete": cost_complete},
        "dashboard_url": agent_dashboard_url(panel="spend"),
        "as_of": agent_as_of(),
        "data_scope": "anonymous_aggregate_history",
        "approximate_fields": ["cost"] if approximate else [],
        "window": window,
        "days_observed": len(selected),
    }
    if focus == "models":
        result["categories"] = model_rank
    elif focus == "tools":
        result["categories"] = tool_rank
    else:
        result["categories"] = [{"provider": name, "cost": round(value, 4)} for name, value in provider_rank]
    return bounded_agent_result(result)


def agent_capabilities(scope="current", limit=5, caller=None):
    scope = str(scope or "current").strip().lower()
    if scope not in ("current", "all"):
        raise ValueError("scope must be one of: current, all")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer from 1 to 5")
    if limit < 1 or limit > 5:
        raise ValueError("limit must be an integer from 1 to 5")
    cross = cross_session()
    capabilities = cross.get("capabilities") or {}
    selected = None
    if scope == "current":
        source, resolution = resolve_agent_source(caller=caller)
        if not source:
            return agent_no_session(resolution, panel="capabilities")
        state = recompute(source)
        if not state:
            return agent_no_session("Token Meter found the run but could not read its capability evidence.", panel="capabilities")
        summary = session_optional_capabilities(state, capabilities)
        groups = summary.get("groups") or []
        selected = agent_session_summary(source)
    else:
        summary = ((capabilities.get("summary") or {}).get("optional") or {})
        groups = capabilities.get("control_groups") or []
    groups = [row for row in groups if row.get("name") != "tokenmeter" and row.get("namespace") != "tokenmeter"]
    if scope == "all":
        review_candidates = set(summary.get("review_candidates") or [])
        groups = [row for row in groups if row.get("id") in review_candidates]
    else:
        groups = [row for row in groups if not row.get("current_used")]
    groups.sort(key=lambda row: (
        bool(row.get("current_used") if scope == "current" else row.get("used")),
        -int(row.get("current_unused_eager_definition_tokens") or row.get("unused_eager_definition_tokens") or 0),
        str(row.get("name") or ""),
    ))
    candidate_count = len(groups)
    candidates = []
    for row in groups[:limit]:
        used = bool(row.get("current_used") if scope == "current" else row.get("used"))
        candidate = {
            "name": compact_text(row.get("name") or "Unknown", 80),
            "type": row.get("control_type") or "capability",
            "runtime": row.get("runtime") or "",
            "used": used,
            "observed_uses": int(row.get("current_activations") or row.get("activations") or row.get("calls") or 0),
        }
        overhead = int(row.get("current_unused_eager_definition_tokens") or row.get("unused_eager_definition_tokens") or 0)
        if overhead:
            candidate["avoidable_eager_tokens"] = overhead
        candidates.append(candidate)
    enabled = int(summary.get("enabled") or 0)
    unused = candidate_count
    if unused:
        answer = f"{unused} of {enabled} removable capability groups have no observed use in this scope."
        action = "Review the named candidates in Tools & Skills; only disable a group after confirming you do not need it."
        assessment = "Review available"
    else:
        answer = "No unused removable capability group was found in this scope."
        action = "Keep the current setup and review again after more representative work."
        assessment = "No cleanup needed"
    result = {
        "ok": True,
        "answer": answer,
        "assessment": assessment,
        "evidence": [
            {"label": "Enabled removable groups", "value": enabled},
            {"label": "Groups without observed use", "value": unused},
        ],
        "candidates": candidates,
        "candidate_count": candidate_count,
        "candidates_returned": len(candidates),
        "recommended_action": action,
        "caveat": ("Capability evidence names user-installed skill packs but never returns configuration values, "
                   "environment variables, credentials, tool arguments, or tool results."
                   + (f" Returned {len(candidates)} of {candidate_count} candidates because of the requested limit."
                      if len(candidates) < candidate_count else "")),
        "dashboard_url": agent_dashboard_url(panel="capabilities"),
        "as_of": agent_as_of(),
        "data_scope": "named_capability_evidence",
        "approximate_fields": [],
        "scope": scope,
    }
    if selected:
        result["selected_session"] = selected
    return bounded_agent_result(result)


def compact_duration_ms(ms):
    try:
        seconds = max(0, int(ms) / 1000.0)
    except Exception:
        return ""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(round(seconds))}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def menubar_activity(st):
    trace = st.get("trace") or []
    preferred = {
        "tool_call", "tool_result", "message", "reasoning", "complete",
        "context", "goal", "start", "user",
    }
    event = None
    for ev in reversed(trace[-24:]):
        if ev.get("kind") in preferred:
            event = ev
            break
    if event is None and trace:
        event = trace[-1]
    if not event:
        return {
            "kind": "idle",
            "title": "Waiting for activity",
            "detail": "No trace events yet.",
            "time": "",
            "execution": None,
        }

    kind = event.get("kind") or "activity"
    label = event.get("label") or kind.replace("_", " ").title()
    detail = event.get("detail") or ""
    tool = event.get("tool")
    execution = event.get("execution")
    duration_ms = event.get("duration_ms")

    if kind == "tool_call":
        title = f"Running {label}"
    elif kind == "tool_result":
        title = f"Received {label}"
    elif kind == "reasoning":
        title = "Reasoning"
    elif kind == "message":
        title = label if label != "Agent update" else "Agent update"
    elif kind == "complete":
        title = f"Completed #{execution}" if execution else "Execution complete"
    elif kind == "context":
        title = label
    elif kind == "user":
        title = "User message"
    else:
        title = label

    bits = []
    duration = compact_duration_ms(duration_ms)
    if not duration and isinstance(detail, str) and detail.endswith("ms") and detail[:-2].isdigit():
        duration = compact_duration_ms(detail[:-2])
    if duration:
        bits.append(f"in {duration}" if kind == "complete" else duration)
    elif detail:
        bits.append(detail)
    if execution:
        if kind == "complete":
            pass
        else:
            bits.append(f"#{execution}")
    if tool and tool not in title:
        bits.append(tool)
    if event.get("cost"):
        bits.append(f"${event['cost']:.3f}")
    detail = " · ".join(bits)
    return {
        "kind": kind,
        "title": compact_text(title, 64),
        "detail": compact_text(detail, 120),
        "time": event.get("time") or "",
        "execution": execution,
        "tool": tool,
    }


def menubar_recommendation(st):
    context = st.get("context") or {}
    pct = context.get("latest_pct") or 0
    insights = st.get("insights") or []
    warn = next((i for i in insights if i.get("kind") == "warn"), None)
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    low_yield_actionable = (not st.get("token_estimate") and
                            low_yield_should_warn(st.get("executions") or [], pct))

    if st.get("ended"):
        return {
            "label": "Pinned log",
            "detail": "This is a frozen log view; return to live to follow newest activity.",
            "severity": "idle",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return {
            "label": "Compact now",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "bad",
            "target": "summary",
        }
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return {
            "label": "Review spike",
            "detail": f"Last execution cost ${last_cost:.2f}.",
            "severity": "bad",
            "target": "summary",
        }
    if low_yield_actionable:
        return {
            "label": "Summarize soon",
            "detail": "Latest execution replayed large context for low output.",
            "severity": "warn",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return {
            "label": "Summarize soon",
            "detail": f"Context is {pct * 100:.0f}%; prepare before 85%.",
            "severity": "warn",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_SOFT_PCT:
        return {
            "label": "Watch context",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "idle",
            "target": "summary",
        }
    if warn:
        return {
            "label": "Check signal",
            "detail": warn.get("text") or "A warning signal is active.",
            "severity": "warn",
            "target": "summary",
        }
    return {
        "label": "Let it run",
        "detail": "No immediate intervention needed.",
        "severity": "good",
        "target": "summary",
    }


def menubar_verdict(st, recommendation):
    context = st.get("context") or {}
    reported_pct = context.get("latest_pct")
    pct = reported_pct or 0
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    insights = st.get("insights") or []
    operational_warn = next((i for i in insights if i.get("kind") == "warn" and is_operational_warning(i)), None)

    def payload(key, detail):
        labels = {
            "healthy": ("Healthy", "TM", "good"),
            "watch": ("Watch closely", "TM !", "warn"),
            "intervene": ("Intervene now", "TM !!", "bad"),
            "idle": ("Idle", "TM idle", "idle"),
        }
        label, prefix, severity = labels[key]
        return {"key": key, "label": label, "prefix": prefix, "severity": severity, "detail": detail}

    if st.get("ended"):
        return payload("idle", "This is a frozen log view; return to live to follow newest activity.")
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return payload(
            "intervene",
            f"Context is {pct * 100:.0f}% of the model window; compact now.",
        )
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return payload(
            "intervene",
            f"Last execution cost ${last_cost:.2f}; review the spike before continuing.",
        )
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return payload(
            "watch",
            f"Context is {pct * 100:.0f}% of the model window; prepare to summarize before 85%.",
        )
    if operational_warn:
        detail = operational_warn.get("text") or recommendation.get("detail") or "An operational warning is active."
        return payload("watch", detail)

    detail = (f"Context is {pct * 100:.0f}% and no operational warning needs intervention."
              if reported_pct is not None else
              "Context percentage is not reported; no operational warning needs intervention.")
    return payload("healthy", detail)


def provider_cli_path(name):
    resolved = shutil.which(name)
    if resolved:
        return resolved
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", name),
        os.path.join(home, ".volta", "bin", name),
        os.path.join(home, ".asdf", "shims", name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    candidates.extend(sorted(
        glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", name)),
        key=safe_mtime, reverse=True,
    ))
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def _rpc_read_response(process, selector, request_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = selector.select(max(0.0, deadline - time.monotonic()))
        if not events:
            break
        line = process.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if message.get("error"):
            raise QuotaUnavailable("Codex could not read account quotas.")
        return message.get("result") or {}
    raise QuotaUnavailable("Codex quota request timed out.")


def codex_app_server_rate_limits(timeout=QUOTA_PROCESS_TIMEOUT_S):
    return openai_quotas.app_server_rate_limits(
        provider_cli_path,
        agent_client_environment,
        _rpc_read_response,
        subprocess,
        selectors,
        timeout,
    )


def _codex_limit_label(value):
    return openai_quotas.limit_label(value)


def _codex_window(provider_id, raw, kind, label, now=None):
    return openai_quotas._window(provider_id, raw, kind, label, now=now)


def parse_codex_quota(payload, source="Codex app-server", now=None):
    return openai_quotas.parse_quota(payload, source=source, now=now)


def codex_oauth_quota(now=None, opener=None):
    return openai_quotas.oauth_quota(
        CODEX_AUTH, quota_http_json, now=now, opener=opener
    )


def load_codex_quota(now=None):
    return openai_quotas.load_quota(
        codex_app_server_rate_limits, codex_oauth_quota, now=now
    )


def claude_auth_status(timeout=3.0):
    return anthropic_quotas.auth_status(
        provider_cli_path, agent_client_environment, subprocess, timeout=timeout
    )


def claude_oauth_credentials(auth_status=None, timeout=3.0):
    return anthropic_quotas.oauth_credentials(
        CLAUDE_CREDENTIALS,
        auth_status,
        subprocess,
        timeout=timeout,
        now_fn=time.time,
    )


def _claude_window(field, raw, kind, label, duration, now=None):
    return anthropic_quotas._window(field, raw, kind, label, duration, now=now)


def parse_claude_quota(payload, credentials=None, now=None):
    return anthropic_quotas.parse_quota(
        payload, credentials=credentials, now=now
    )


def load_claude_quota(now=None, opener=None):
    return anthropic_quotas.load_quota(
        claude_auth_status,
        claude_oauth_credentials,
        quota_http_json,
        now=now,
        opener=opener,
    )


def cursor_auth_session(now=None, db_path=None):
    return cursor_quotas.auth_session(
        lambda: _cursor_db_connection(db_path),
        (sqlite3.Error, OSError),
        now=now,
    )


def parse_cursor_quota(payload, now=None):
    return cursor_quotas.parse_quota(payload, now=now)


def load_cursor_quota(now=None, opener=None):
    return cursor_quotas.load_quota(
        cursor_auth_session, quota_http_json, now=now, opener=opener
    )


def grok_oauth_token(now=None):
    return xai_quotas.oauth_token(GROK_AUTH, now=now)


def parse_grok_quota(payload, now=None):
    return xai_quotas.parse_quota(payload, now=now)


def load_grok_quota(now=None, opener=None):
    return xai_quotas.load_quota(
        grok_oauth_token, quota_http_json, now=now, opener=opener
    )


def _quota_label(provider):
    return quota_registry().label_for_public(provider)


def _quota_loading_row(provider):
    return quota_provider(
        provider, _quota_label(provider), "loading", "Provider account",
        error="Loading provider quotas.",
    )


def _quota_failure_row(provider, error, now):
    safe_error = str(error) if isinstance(error, QuotaUnavailable) else "Provider quota refresh failed."
    with _quota_lock:
        previous = copy.deepcopy(_quota_cache.get(provider))
    if previous and previous.get("windows"):
        previous["status"] = "error"
        previous["error"] = compact_text(safe_error, 180)
        previous["attempted_at"] = now
        return previous
    row = quota_provider(
        provider, _quota_label(provider), "error", "Provider account", error=safe_error,
    )
    row["fetched_at"] = now
    row["attempted_at"] = now
    return row


def refresh_provider_quota(provider, loader, now=None):
    now = float(now if now is not None else time.time())
    try:
        row = loader(now=now)
        if not isinstance(row, dict):
            raise QuotaUnavailable("Provider returned an invalid quota response.")
        row = copy.deepcopy(row)
        row["fetched_at"] = now
        row["attempted_at"] = now
        row.setdefault("error", "")
    except Exception as exc:
        row = _quota_failure_row(provider, exc, now)
    with _quota_lock:
        _quota_cache[provider] = row
        _quota_inflight.discard(provider)
    return copy.deepcopy(row)


def _quota_refresh_worker(provider, loader):
    refresh_provider_quota(provider, loader)


def quota_registry():
    """Return account-provider adapters independently of runtime registration."""
    global _QUOTA_REGISTRY
    if _QUOTA_REGISTRY is not None:
        return _QUOTA_REGISTRY
    with _quota_registry_lock:
        if _QUOTA_REGISTRY is None:
            _QUOTA_REGISTRY = QuotaRegistry((
                CallableQuotaAdapter(
                    "anthropic", "claude", "Claude",
                    lambda now=None: load_claude_quota(now=now),
                ),
                CallableQuotaAdapter(
                    "openai", "codex", "Codex",
                    lambda now=None: load_codex_quota(now=now),
                ),
                CallableQuotaAdapter(
                    "cursor", "cursor", "Cursor",
                    lambda now=None: load_cursor_quota(now=now),
                ),
                CallableQuotaAdapter(
                    "xai", "grok", "Grok",
                    lambda now=None: load_grok_quota(now=now),
                ),
            ))
    return _QUOTA_REGISTRY


def provider_quota_snapshots(now=None, loaders=None, start_refresh=True):
    now = float(now if now is not None else time.time())
    loaders = loaders or quota_registry().public_loaders()
    to_start = []
    with _quota_lock:
        for provider, loader in loaders.items():
            cached = _quota_cache.get(provider)
            last_attempt = (cached or {}).get("attempted_at") or (cached or {}).get("fetched_at") or 0
            if start_refresh and provider not in _quota_inflight and now - last_attempt >= QUOTA_REFRESH_S:
                _quota_inflight.add(provider)
                to_start.append((provider, loader))
        cached_rows = {provider: copy.deepcopy(_quota_cache.get(provider)) for provider in loaders}

    for provider, loader in to_start:
        threading.Thread(
            target=_quota_refresh_worker, args=(provider, loader),
            name=f"token-meter-quota-{provider}", daemon=True,
        ).start()

    out = []
    for provider in loaders:
        row = cached_rows.get(provider) or _quota_loading_row(provider)
        fetched_at = quota_timestamp(row.get("fetched_at"))
        age = max(0.0, now - fetched_at) if fetched_at is not None else None
        row["age_seconds"] = int(age) if age is not None else None
        row["stale"] = bool(age is not None and age >= QUOTA_STALE_S)
        if row["stale"] and row.get("windows"):
            row["status"] = "stale"
        for window in row.get("windows") or []:
            window["pace"] = quota_pace(window, now=now)
        out.append(row)
    return out


def reset_provider_quota_cache():
    with _quota_lock:
        _quota_cache.clear()
        _quota_inflight.clear()


def menubar_project_name(value):
    """Return only a display-safe project leaf for the native payload."""
    project = str(value or "").rstrip("/\\")
    if not project or project == "No project":
        return ""
    return compact_text(project.replace("\\", "/").rsplit("/", 1)[-1], 52)


def menubar_session_name(source):
    session_name = compact_text(source.get("session_name") or "", 52).strip()
    if session_name and session_name.lower() not in ("untitled", "untitled session"):
        return session_name
    title = compact_text(source.get("title") or "", 52).strip()
    if title and title.lower() not in ("untitled", "untitled session"):
        return title
    project = menubar_project_name(source.get("project"))
    if project:
        return project
    return str(source.get("id") or "session")[:12]


def menubar_recent_sessions(sources, selected_id=None, limit=5, summaries=None):
    summary_names = {}
    for summary in summaries or ():
        key = (str(summary.get("provider") or ""), str(summary.get("id") or ""))
        name = compact_text(summary.get("session_name") or "", 52).strip()
        if key[1] and name and key not in summary_names:
            summary_names[key] = name

    ordered, seen = [], set()
    for source in sorted(sources or [], key=lambda row: -(row.get("mtime") or 0)):
        sid = str(source.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ordered.append(source)

    selected = next((row for row in ordered if row.get("id") == selected_id), None)
    choices = ordered[:max(0, limit)]
    if selected and selected not in choices and limit > 0:
        choices = [selected] + [row for row in ordered if row is not selected][:limit - 1]

    result = []
    for row in choices:
        display_row = dict(row)
        key = (str(row.get("provider") or ""), str(row.get("id") or ""))
        if summary_names.get(key):
            display_row["session_name"] = summary_names[key]
        result.append({
            "id": row.get("id"),
            "provider": row.get("provider"),
            "client": row.get("client") or row.get("provider"),
            "label": row.get("label"),
            "name": menubar_session_name(display_row),
            "mtime": row.get("mtime") or 0,
        })
    return result


def menubar_context_pulse(st, limit=18):
    """Return a short numeric context history for the native Run Pulse.

    The native companion needs a visual indication of whether measured context
    is rising or settling, but it must never receive trace text, paths, tool
    inputs, or other session contents. Keep only finite context percentages
    from the most recent completed executions.
    """
    values = []
    for execution in (st.get("executions") or []):
        value = execution.get("context_pct")
        if isinstance(value, bool):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(round(max(0.0, min(1.0, value)), 4))
    return values[-max(1, limit):]


def menubar_software_update(status=None):
    """Project update state into the native menu without local checkout details."""
    status = status if isinstance(status, dict) else software_update_status()
    state = str(status.get("state") or "waiting")
    allowed_states = {
        "disabled", "waiting", "checking", "current",
        "available", "updating", "updated", "attention",
    }
    if state not in allowed_states:
        state = "attention"
    actions = status.get("actions") if isinstance(status.get("actions"), dict) else {}
    action_token = str(actions.get("token") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", action_token):
        action_token = ""
    return {
        "enabled": status.get("enabled") is True,
        "auto_install": status.get("auto_install") is True,
        "state": state,
        "available": status.get("available") is True,
        "can_update": status.get("can_update") is True,
        "current_revision": _safe_update_revision(status.get("current_revision")),
        "latest_revision": _safe_update_revision(status.get("latest_revision")),
        "actions": {
            "token": action_token,
            "install": actions.get("install") is True,
        },
    }


def menubar_today_spend(cross, today=None):
    """Return a bounded current-day spend projection for the native title."""
    day = (today or datetime.date.today()).isoformat()
    daily = cross.get("daily") if isinstance(cross, dict) else []
    row = next(
        (item for item in (daily or [])
         if isinstance(item, dict) and item.get("day") == day),
        None,
    )
    availability = row.get("availability") if isinstance(row, dict) else {}
    available = bool(isinstance(availability, dict) and availability.get("cost") is True)
    provenance = row.get("provenance") if isinstance(row, dict) else {}
    estimated_cost = (
        float(provenance.get("estimated_cost") or 0)
        if isinstance(provenance, dict) else 0.0
    )
    return {
        "available": available,
        "total_cost": float(row.get("cost") or 0) if available else None,
        "estimated": estimated_cost > 0,
    }


def menubar_state(session_id=None):
    requested_id = str(session_id or "").strip()
    sources, inventory_ready = cached_session_sources()
    selected_source = (
        find_session(requested_id, sources=sources)
        if requested_id and inventory_ready else None
    )
    missing = bool(requested_id and inventory_ready and not selected_source)
    # The watcher owns the first full recompute. Calling current_state() here
    # before it publishes would make every two-second native poll independently
    # rebuild all cross-session history and starve the cold-start worker.
    published_source_id = str(((STATE or {}).get("source") or {}).get("id") or "")
    if selected_source and STATE and str(selected_source.get("id") or "") == published_source_id:
        st = STATE
    elif selected_source and STATE:
        st = cached_session_state(selected_source)
    else:
        st = STATE or {
        "ok": False,
        "message": "Token Meter is loading local session history.",
        "source": {},
        "context": {},
        "cache": {},
        "throughput": {},
        "executions": [],
        "insights": [],
        }
    if selected_source and STATE and not st:
        missing = True
        selected_source = None
        st = current_state()
    source = st.get("source") or {}
    context = st.get("context") or {}
    cache = st.get("cache") or {}
    throughput = st.get("throughput") or {}
    live_throughput = st.get("live_throughput") or {}
    activity = menubar_activity(st)
    recommendation = menubar_recommendation(st)
    verdict = menubar_verdict(st, recommendation)
    availability = st.get("availability") or metric_availability(st.get("provider"))
    selected_id = source.get("id")
    effective_selected_id = requested_id if selected_source else selected_id
    model = next((row.get("model") for row in reversed(st.get("executions") or [])
                  if row.get("model")), None) or source.get("model") or "unknown"
    project_name = menubar_project_name(st.get("project") or source.get("project"))
    cross = _xsess.get("data") or (STATE or {}).get("xsession") or {}
    budget = cross.get("budget") or monthly_budget_status([], budget_settings())
    return {
        "ok": bool(st.get("source")),
        "runtime_catalog": menubar_runtime_catalog(runtime_registry().descriptors),
        "provider": st.get("provider"),
        "availability": availability,
        "model": model,
        "source": {
            "label": source.get("label"),
            "id": source.get("id"),
            "project": project_name,
            "pricing_note": source.get("pricing_note"),
            "approximate_cost": source.get("approximate_cost"),
            "token_estimate": source.get("token_estimate"),
            "availability": source.get("availability") or availability,
        },
        "session": st.get("session"),
        "project": project_name,
        "total_cost": (
            st.get("total_cost", 0) if availability.get("cost") is not False else None
        ),
        "today_spend": menubar_today_spend(cross),
        "cost_approx": st.get("cost_approx", False),
        "total_tokens": st.get("total_tokens", 0),
        "turns": st.get("turns", 0),
        "throughput": {
            "available": bool(throughput.get("available")),
            "output_tps": throughput.get("output_tps", 0),
            "basis": throughput.get("basis") or "unavailable",
            "sample_count": throughput.get("sample_count", 0),
            "timing_coverage": throughput.get("timing_coverage", 0),
        },
        "live_throughput": {
            "available": bool(live_throughput.get("available")),
            "output_tps": live_throughput.get("output_tps", 0),
            "basis": live_throughput.get("basis") or "unavailable",
            "completed_steps": live_throughput.get("completed_steps", 0),
            "measured_output_tokens": live_throughput.get("measured_output_tokens", 0),
            "measured_seconds": live_throughput.get("measured_seconds", 0),
        },
        "cache": {
            "available": bool(availability.get("cache", True)),
            "fresh": cache.get("fresh", 0),
            "read": cache.get("read", 0),
            "write": cache.get("write", 0),
            **{
                field: int(cache.get(field) or 0)
                for field in ("write_5m", "write_1h", "write_unspecified")
                if field in cache
            },
            "total": cache.get("total", 0),
            "input_total": cache.get("input_total", 0),
            "hit_ratio": cache.get("hit_ratio", 0),
            "input_share": cache.get("input_share", 0),
            "saved": cache.get("saved", 0),
            "cost": cache.get("cost", 0),
            "latest": cache.get("latest") or {},
        },
        "context": {
            "latest": context.get("latest"),
            "window": context.get("window"),
            "latest_pct": context.get("latest_pct"),
        },
        "last_turn_cost": (
            st.get("last_turn_cost", 0)
            if availability.get("cost") is not False else None
        ),
        "idle_s": st.get("idle_s", 0),
        "ended": st.get("ended", False),
        "activity": activity,
        "recommendation": recommendation,
        "verdict": verdict,
        "insights": (st.get("insights") or [])[:4],
        "selection": {
            "requested_id": requested_id or None,
            "selected_id": effective_selected_id,
            "pinned": bool(requested_id and selected_source),
            "missing": missing,
        },
        "recent_sessions": menubar_recent_sessions(
            sources,
            selected_id=effective_selected_id,
            limit=5,
            summaries=(cross.get("current_sessions") or []) + (cross.get("sessions") or []),
        ),
        "context_pulse": menubar_context_pulse(st),
        "provider_quotas": provider_quota_snapshots(),
        "budget": budget,
        "software_update": menubar_software_update(),
        "ts": st.get("ts"),
    }


def watcher():
    cur, last_sig = None, None
    last_current_refresh = 0.0
    sources = []
    inventory_ready = False
    last_source_discovery = 0.0
    probe_targets = ()
    probe_signature = None
    candidate_paths = ()
    last_membership_probe = 0.0
    last_membership_fallback = 0.0
    last_sources_sig = None
    last_source_identities = None
    cross_dirty = False
    last_cross_refresh = 0.0
    while True:
        observation_now = time.monotonic()
        inventory_change_detected = False
        inventory_refreshed = False
        if inventory_ready and source_membership_probe_due(
                observation_now, last_membership_probe):
            next_probe_signature = source_inventory_probe_signature(
                probe_targets, current_path=(cur or {}).get("path"),
            )
            inventory_change_detected = bool(
                probe_signature is not None
                and next_probe_signature != probe_signature
            )
            probe_signature = next_probe_signature
            last_membership_probe = observation_now
        if (inventory_ready and not inventory_change_detected
                and source_membership_fallback_due(
                    observation_now, last_membership_fallback)):
            next_candidate_paths = runtime_candidate_paths()
            inventory_change_detected = next_candidate_paths != candidate_paths
            candidate_paths = next_candidate_paths
            last_membership_fallback = observation_now
        if (inventory_change_detected or source_discovery_refresh_due(
                inventory_ready, observation_now, last_source_discovery)):
            if inventory_change_detected:
                _recursive_path_cache.clear()
            sources = all_session_sources()
            inventory_refreshed = True
            inventory_ready = True
            last_source_discovery = observation_now
            candidate_paths = runtime_candidate_paths()
            probe_targets = source_inventory_probe_targets(sources)
            newest = max(
                sources, key=lambda source: source.get("mtime") or 0,
            ) if sources else None
            probe_signature = source_inventory_probe_signature(
                probe_targets, current_path=(newest or {}).get("path"),
            )
            last_membership_probe = observation_now
            last_membership_fallback = observation_now
        else:
            refreshed_sources = refresh_known_source_activity(
                sources, (cur or {}).get("path"),
            )
            inventory_refreshed = refreshed_sources is not sources
            sources = refreshed_sources
        if inventory_refreshed:
            publish_source_inventory(sources)
        nf = (
            max(sources, key=lambda source: source["mtime"])
            if inventory_refreshed and sources else cur
        )
        sources_sig = (
            source_mtime_signature(sources)
            if inventory_refreshed else last_sources_sig
        )
        source_identities = (
            source_identity_signature(sources)
            if inventory_refreshed else last_source_identities
        )
        membership_changed = bool(
            inventory_change_detected
            or source_identities != last_source_identities
        )
        if sources_sig != last_sources_sig:
            cross_dirty = True
            last_sources_sig = sources_sig
        if membership_changed:
            cross_dirty = True
            last_source_identities = source_identities
        if nf and (not cur or nf["path"] != cur["path"]):
            cur, last_sig = nf, None
            last_current_refresh = 0.0
        elif nf and cur and nf["path"] == cur["path"]:
            cur = nf
        updated_state = None
        if cur:
            sig = source_revision_signature(cur)
            if not sig or not sig[0]:
                cur = None
                time.sleep(0.5)
                continue
            current_now = time.monotonic()
            if current_session_refresh_due(
                    sig, last_sig, current_now, last_current_refresh):
                updated_state = recompute(cur)
                if updated_state:
                    cache_at = _xsess.get("at") or 0.0
                    publish(attach_cross_session(
                        updated_state,
                        cross_session(sources=sources),
                    ))
                    if (_xsess.get("at") or 0.0) > cache_at:
                        cross_dirty = False
                        last_cross_refresh = time.monotonic()
                    last_sig = sig
                    last_current_refresh = current_now
        now = time.monotonic()
        if cross_session_refresh_due(
                cross_dirty, membership_changed, now, last_cross_refresh):
            cross = refresh_cross_session_state(
                updated_state or STATE,
                builder=lambda: cross_session(sources=sources),
            )
            if not STATE:
                publish({
                    "ok": False,
                    "loading": False,
                    "message": "No readable {} logs found yet.".format(
                        supported_runtime_phrase()
                    ),
                    "source": {},
                    "total_cost": 0,
                    "total_tokens": 0,
                    "turns": 0,
                    "context": {},
                    "insights": [],
                    "xsession": cross,
                })
            cross_dirty = False
            last_cross_refresh = now
        time.sleep(0.5)


def page_candidates():
    paths = []
    explicit = os.environ.get("TOKEN_METER_PAGE")
    if explicit:
        paths.append(os.path.abspath(os.path.expanduser(explicit)))
    paths.extend([
        os.path.join(_SOURCE_ROOT, "page.html"),
        os.path.join(os.getcwd(), "page.html"),
    ])

    out, seen = [], set()
    for path in paths:
        if path not in seen:
            out.append(path)
            seen.add(path)
    return out


PAGE_CANDIDATES = page_candidates()


def page_path():
    for path in PAGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def is_dashboard_page_path(req_path):
    return req_path in ("/", "/widget") or bool(
        re.fullmatch(r"/sessions/[^/]{1,240}/?", req_path or "")
    )


_DASHBOARD_ASSETS = {
    "/assets/fonts/Tektur-Variable.ttf": ("assets/fonts/Tektur-Variable.ttf", "font/ttf"),
    "/assets/brand/logo-splunk-acc-rgb-w.png": (
        "assets/brand/logo-splunk-acc-rgb-w.png",
        "image/png",
    ),
    **{
        f"/{asset}": (asset, "text/javascript; charset=utf-8")
        for asset in (
            "assets/session-effects.js",
            "assets/vendor/paper-shaders/get-shader-color-from-string.js",
            "assets/vendor/paper-shaders/get-shader-noise-texture.js",
            "assets/vendor/paper-shaders/shader-mount.js",
            "assets/vendor/paper-shaders/shader-sizing.js",
            "assets/vendor/paper-shaders/shader-utils.js",
            "assets/vendor/paper-shaders/vertex-shader.js",
            "assets/vendor/paper-shaders/shaders/pulsing-border.js",
        )
    },
}


def dashboard_asset_path(req_path):
    """Resolve explicitly bundled dashboard assets without exposing arbitrary files."""
    spec = _DASHBOARD_ASSETS.get(req_path)
    if not spec:
        return None
    dashboard = page_path()
    if not dashboard:
        return None
    path = os.path.join(os.path.dirname(dashboard), *spec[0].split("/"))
    return path if os.path.isfile(path) else None


def dashboard_asset_content_type(req_path):
    spec = _DASHBOARD_ASSETS.get(req_path)
    return spec[1] if spec else None


def missing_page_html():
    candidates = "\n".join(
        f"<li><code>{html.escape(path)}</code></li>" for path in PAGE_CANDIDATES
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Token Meter setup error</title>
  <style>
    body {{ font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; max-width: 760px; }}
    code {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>page.html is missing</h1>
  <p>Token Meter needs the dashboard file <code>page.html</code> alongside <code>meter.py</code>, or in the directory where you start the server.</p>
  <p>Run from a full repository clone with <code>./scripts/start-token-meter</code>, or copy <code>page.html</code> from the repo into the same folder as <code>meter.py</code>.</p>
  <p>Looked in:</p>
  <ul>{candidates}</ul>
</body>
</html>"""


def health_state():
    """Return constant-time liveness/readiness from watcher-owned cached state."""
    path = page_path()
    inventory = _SOURCE_INVENTORY
    inventory_ready = bool(inventory.get("ready"))
    payload = {
        "ok": bool(path),
        "state_ready": bool(STATE),
        "inventory_ready": inventory_ready,
        "sources": inventory.get("count") if inventory_ready else None,
        "source_clients": dict(inventory.get("clients") or {}) if inventory_ready else {},
        "runtime_adapter_failures": runtime_adapter_failures(),
        "port": PORT,
        "page_ready": bool(path),
        "page_path": path,
        "page_candidates": PAGE_CANDIDATES,
    }
    return payload, 200 if path else 503


class H(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        req_path = urlparse(self.path).path
        if is_dashboard_page_path(req_path):
            path = page_path()
            body = b"" if path else missing_page_html().encode()
            self.send_response(200 if path else 503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(os.path.getsize(path) if path else len(body)))
            self.end_headers()
        elif (path := dashboard_asset_path(req_path)):
            self.send_response(200)
            self.send_header("Content-Type", dashboard_asset_content_type(req_path))
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        req_path = urlparse(self.path).path
        if req_path not in ("/capability/toggle", "/capability/disable-unused",
                            "/agent-access/toggle", "/session/delete",
                            "/settings/frustration", "/settings/language-signals",
                            "/settings/model-pricing", "/settings/session-model-identity",
                            "/settings/budgets", "/settings/updates",
                            "/git-delivery/clear", "/updates/check", "/updates/install"):
            self.send_error(404)
            return
        origin = self.headers.get("Origin") or ""
        if origin and (urlparse(origin).hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            self._send(json.dumps({"ok": False, "error": "Local dashboard origin required."}),
                       "application/json", status=403)
            return
        content_type = self.headers.get("Content-Type") or ""
        if not content_type.startswith("application/json"):
            self._send(json.dumps({"ok": False, "error": "JSON request required."}),
                       "application/json", status=415)
            return
        action_token = self.headers.get("X-Token-Meter-Action") or ""
        if not action_token or not secrets.compare_digest(action_token, _ACTION_TOKEN):
            self._send(json.dumps({"ok": False, "error": "Invalid action token."}),
                       "application/json", status=403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self._send(json.dumps({"ok": False, "error": "Invalid request size."}),
                       "application/json", status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(json.dumps({"ok": False, "error": "Invalid JSON."}),
                       "application/json", status=400)
            return
        if req_path == "/settings/language-signals":
            result = set_language_signal_terms(payload.get("terms") or payload)
            if result.get("ok"):
                _summary_cache.clear()
                cross = refresh_cross_session_state()
                result["language_signals"] = cross.get("language_signals") or {}
                result["frustration"] = cross.get("frustration") or {}
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/frustration":
            result = set_frustration_terms(payload.get("terms"))
            if result.get("ok"):
                _summary_cache.clear()
                cross = refresh_cross_session_state()
                result["frustration"] = cross.get("frustration") or {}
                result["language_signals"] = cross.get("language_signals") or {}
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/model-pricing":
            if isinstance(payload.get("changes"), list):
                result = set_model_prices(
                    payload["changes"],
                    apply_to_all_history=payload.get("apply_to_all_history") is True,
                    effective_from=payload.get("effective_from"),
                )
            else:
                result = set_model_price(
                    payload.get("provider"),
                    payload.get("model"),
                    payload.get("prices"),
                    remove=payload.get("remove") is True,
                    apply_to_all_history=payload.get("apply_to_all_history") is True,
                    effective_from=payload.get("effective_from"),
                )
            if result.get("ok"):
                _summary_cache.clear()
                _xsess["data"], _xsess["at"] = None, 0.0
                current_id = ((STATE.get("source") or {}).get("id") if STATE else "")
                source = find_session(current_id) if current_id else newest_source()
                updated = recompute(source) if source else None
                cross = refresh_cross_session_state(updated or STATE)
                result["model_pricing"] = cross.get("model_pricing") or result["model_pricing"]
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/session-model-identity":
            result = set_session_model_identity(
                payload.get("session_key"),
                model=payload.get("model"),
                provider=payload.get("provider") or "codex",
                remove=payload.get("remove") is True,
            )
            if result.get("ok"):
                with _summary_cache_lock:
                    _summary_cache.clear()
                with _session_state_cache_lock:
                    _session_state_cache.clear()
                _xsess["data"], _xsess["at"] = None, 0.0
                current_id = ((STATE.get("source") or {}).get("id") if STATE else "")
                source = find_session(current_id) if current_id else newest_source()
                updated = recompute(source) if source else None
                refresh_cross_session_state(updated or STATE)
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/budgets":
            result = set_budget_settings(payload)
            if result.get("ok"):
                cross = refresh_cross_session_state()
                result["budgets"] = cross.get("budgets") or result["budgets"]
                result["budget"] = cross.get("budget") or {}
                result["monthly"] = cross.get("monthly") or []
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/updates":
            result = set_update_settings(payload)
            if result.get("ok") and result["updates"]["enabled"]:
                check = trigger_software_update_check()
                result["status"] = check.get("status") or software_update_status()
            elif result.get("ok"):
                result["status"] = software_update_status()
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/git-delivery/clear":
            result = clear_git_delivery_activity(payload.get("confirm") is True)
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/updates/check":
            result = trigger_software_update_check()
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/updates/install":
            result = start_software_update()
            self._send(json.dumps(result), "application/json",
                       status=202 if result.get("ok") else 409)
            return
        if req_path == "/agent-access/toggle":
            result = set_agent_access(payload.get("client"), payload.get("enabled"),
                                      repair=payload.get("repair") is True)
            status = 200 if result.get("ok") else (409 if result.get("conflict") else 400)
            self._send(json.dumps(result), "application/json", status=status)
            return
        if req_path == "/session/delete":
            result = request_session_delete(payload.get("session_id"))
            if result.get("ok"):
                result["next_session_id"] = publish_after_session_delete()
            status = 200 if result.get("ok") else (404 if result.get("error_code") == "not_found" else
                     (500 if result.get("error_code") == "trash_failed" else 400))
            self._send(json.dumps(result), "application/json", status=status)
            return
        if req_path == "/capability/disable-unused":
            capabilities = cross_session().get("capabilities") or {}
            result = disable_capability_controls(payload.get("control_ids"), capabilities)
        else:
            capability_type = str(payload.get("type") or "").strip().lower()
            control_id = str(payload.get("control_id") or "").strip()
            enabled = payload.get("enabled") is True
            control = next((row for row in capability_inventory().get("control_groups") or []
                            if row.get("id") == control_id and row.get("control_type") == "skill_pack"
                            and row.get("mutable")), None)
            if not control:
                result = {"ok": False, "error": "Capability control is not in the discovered inventory."}
            elif capability_type == "skill":
                result = set_skill_pack_enabled(control.get("runtime"), control.get("plugin_id"), enabled)
            else:
                result = {"ok": False, "error": "Unsupported capability type."}
        if result.get("ok") or result.get("changed"):
            result["capabilities"] = capability_summary_payload(refresh_capability_state())
        status = 200 if result.get("ok") else (409 if result.get("partial") else
                 (503 if "not available" in result.get("error", "") else 400))
        self._send(json.dumps(result), "application/json", status=status)

    def do_GET(self):
        parsed = urlparse(self.path)
        req_path = parsed.path
        if is_dashboard_page_path(req_path):
            path = page_path()
            if path:
                self._send(open(path, encoding="utf-8").read())
            else:
                self._send(missing_page_html(), status=503)
        elif (asset_path := dashboard_asset_path(req_path)):
            with open(asset_path, "rb") as asset:
                self._send(asset.read(), dashboard_asset_content_type(req_path))
        elif req_path == "/session":
            query = parse_qs(parsed.query)
            sid = (query.get("id") or [""])[0]
            live = (query.get("live") or [""])[0] == "1"
            source = find_session(sid)
            st = cached_session_state(source) if source else None
            if st:
                st["selected_stats"] = selected_session_stats(source)
                cross = _xsess.get("data") or cross_session()
                attach_cross_session(st, cross)
                current_ids = {
                    str(row.get("id") or "")
                    for row in (cross.get("current_sessions") or [])
                }
                st["ended"] = not live or str((st.get("source") or {}).get("id") or "") not in current_ids
                st["selected_live"] = live
                if st.get("timing"):
                    st["timing"]["end_label"] = "Last activity"
            self._send(json.dumps(dashboard_state_payload(st or {})), "application/json")
        elif req_path == "/state":
            self._send(json.dumps(dashboard_state_payload(current_state())), "application/json")
        elif req_path == "/capabilities/inventory":
            capabilities = cross_session().get("capabilities") or {}
            items = capabilities.get("items") or []
            self._send(json.dumps({
                "ok": True,
                "revision": capabilities.get("revision") or "",
                "inventory_revision": capabilities.get("inventory_revision") or "",
                "generated_at": capabilities.get("generated_at"),
                "count": len(items),
                "items": items,
            }), "application/json")
        elif req_path == "/logs":
            self._send(json.dumps(log_sessions_state()), "application/json")
        elif req_path == "/spend/logs":
            query = parse_qs(parsed.query)
            payload, status = spend_logs_state(
                (query.get("from") or [""])[0],
                (query.get("to") or [""])[0],
            )
            self._send(json.dumps(payload), "application/json", status=status)
        elif req_path == "/model-stats":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            payload, status = project_model_stats(project)
            self._send(json.dumps(payload), "application/json", status=status)
        elif req_path == "/git-delivery":
            query = parse_qs(parsed.query)
            project = (query.get("project") or [""])[0]
            range_key = (query.get("range") or ["7"])[0]
            payload = git_delivery_state(project, range_key)
            status = 200 if payload.get("ok") else (
                404 if payload.get("error") == "Project was not found." else 400
            )
            self._send(json.dumps(payload), "application/json", status=status)
        elif req_path == "/agent-access/status":
            self._send(json.dumps(agent_access_status()), "application/json")
        elif req_path == "/menubar":
            sid = (parse_qs(parsed.query).get("session") or [""])[0][:240]
            self._send(json.dumps(menubar_state(sid)), "application/json")
        elif req_path == "/health":
            payload, status = health_state()
            self._send(json.dumps(payload), "application/json", status=status)
        elif req_path == "/updates/status":
            self._send(json.dumps(software_update_status()), "application/json")
        elif req_path == "/events":
            # Older dashboard builds used EventSource and can keep reconnecting
            # even after Chromium replaces the visible tab with an error page.
            # A 204 response explicitly tells EventSource clients to stop.
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        else:
            self.send_error(404)


class TokenMeterHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


_APPLICATION = None


def application():
    """Return the dependency-injected application graph behind all transports."""
    global _APPLICATION
    if _APPLICATION is None:
        settings_service = SettingsService(
            readers={
                "budgets": lambda: budget_settings(),
                "updates": lambda: update_settings(),
                "model_pricing": lambda: model_pricing_settings(),
                "language_signals": lambda: language_signal_settings(),
            },
            writers={
                "budgets": lambda value: set_budget_settings(value),
                "updates": lambda value: set_update_settings(value),
                "language_signals": lambda value: set_language_signal_terms(value),
            },
        )
        _APPLICATION = Application(
            sessions=SessionService(
                runtime_registry(),
                lambda: DiscoveryContext(home=os.path.expanduser("~")),
            ),
            settings=settings_service,
            budgets=BudgetService(
                lambda: budget_settings(),
                lambda months, settings=None, now=None: monthly_budget_status(
                    months, settings=settings, now=now,
                ),
            ),
            capabilities=CapabilityService(
                lambda waste=None: capability_inventory(waste),
                lambda control, enabled: set_capability_control_enabled(control, enabled),
            ),
            updates=UpdateService(
                lambda: software_update_status(),
                lambda: trigger_software_update_check(),
                lambda: start_software_update(),
            ),
            deletion=DeletionService(
                lambda session_id, **kwargs: trash_session_log(session_id, **kwargs)
            ),
            menubar=MenubarService(lambda session_id=None: menubar_state(session_id)),
            agent_api=AgentAPIService(
                lambda **kwargs: agent_check(**kwargs),
                lambda **kwargs: agent_usage(**kwargs),
                lambda **kwargs: agent_capabilities(**kwargs),
                MCPQueryService(
                    sources=all_session_sources,
                    find_session=lambda session_id, sources: find_session(
                        session_id, sources=sources,
                    ),
                    summary=session_summary,
                    state=cached_session_state,
                    revision=source_revision_signature,
                    project_key=agent_project_key,
                    runtime_descriptors=lambda: runtime_registry().descriptors,
                    now=time.time,
                ),
            ),
            current_state=lambda: current_state(),
            cross_session=lambda: cross_session(),
            health=lambda: health_state(),
        )
    return _APPLICATION


def main():
    """Run the local HTTP application and its background services."""
    print("Auto-following newest {} sessions. Ctrl-C to stop.".format(
        supported_runtime_phrase()
    ))
    serve_local(
        handler_class=H,
        server_class=TokenMeterHTTPServer,
        port=PORT,
        background=(watcher, software_update_watcher, git_delivery_watcher),
    )


if __name__ == "__main__":
    main()
