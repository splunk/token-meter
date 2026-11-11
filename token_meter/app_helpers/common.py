"""Shared leaf helpers extracted from the composition module."""

import calendar
import contextlib
import datetime
import functools
import hashlib
import json
import math
import os
import queue
import random
import re
import secrets
import socketserver
import stat
import statistics
import time
from http.server import ThreadingHTTPServer

from token_meter import dashboard_page
from token_meter.contracts import EvidenceBasis, ModelRef, PriceQuery, PriceQuote
from token_meter.domain.usage import make_usage_provenance as _domain_make_usage_provenance, metric_available as _domain_metric_available, usage_io_token_counts as _domain_usage_io_token_counts, usage_provenance as _domain_usage_provenance, usage_token_total_counts as _domain_usage_token_total_counts
from token_meter.domain.aggregates import add_model_daily as _domain_add_model_daily, add_model_summary as _domain_add_model_summary, current_session_summaries as _domain_current_session_summaries, daily_summaries as _domain_daily_summaries, metric_coverage as _domain_metric_coverage, monthly_summaries as _domain_monthly_summaries, spend_log_summaries as _domain_spend_log_summaries, spend_projection as _domain_spend_projection
from token_meter.domain.compare import trace_key as _compare_trace_key
from token_meter.domain.insights import enrich_insights as _domain_enrich_insights, execution_low_yield as _domain_execution_low_yield, insight as _domain_insight, insight_sort_key as _domain_insight_sort_key, is_operational_warning as _domain_is_operational_warning, low_yield_should_warn as _domain_low_yield_should_warn
from token_meter.domain.timing import merge_execution_intervals as _domain_merge_execution_intervals, performance_summary as _domain_performance_summary, wait_time_summary as _domain_wait_time_summary
from token_meter.domain.tools import optional_capability_summary as _domain_optional_capability_summary, summarize_tool_evidence as _domain_summarize_tool_evidence, tool_identity as _domain_tool_identity, tool_summary as _domain_tool_summary
from token_meter.services import work_insights as _work
from token_meter.domain import work_evidence as _work_evidence
from token_meter.domain.work import TAG_ORDER as _domain_work_tags
from token_meter.models.catalog import ANTHROPIC_PRICE as CLAUDE_PRICE, BUILTIN_MODEL_PRICE_HISTORY as _CANONICAL_BUILTIN_MODEL_PRICE_HISTORY, CURSOR_PRICE, DEFAULT_MODELS as _DEFAULT_MODELS, GOOGLE_PRICE, OPENAI_PRICE, canonical_model_provider
from token_meter.models.pricing import matching_price as _catalog_matching_price
from token_meter.models import discounts as _discounts
from token_meter.quotas import anthropic as anthropic_quotas
from token_meter.quotas.common import quota_provider
from token_meter.quotas import cursor as cursor_quotas
from token_meter.quotas import openai as openai_quotas
from token_meter.runtimes.opencode import context_tokens as _native_opencode_context_tokens, decode_json as _native_opencode_json, display_cost as _native_opencode_display_cost, distribute_cost as _native_opencode_distribute, int_value as _native_opencode_int, reported_cost as _native_opencode_reported_cost, usage_counts as _native_opencode_usage
from token_meter.runtimes.agy import default_brain_dir as _default_agy_brain_dir
from token_meter.runtimes.path_cache import BoundedPathCache


MAX_PROJECT_SKILL_ROOTS = 64


CLAUDE_ROOT_CONFIG = os.path.expanduser("~/.claude.json")


CLAUDE_CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")


_opencode_db_override = os.path.expanduser(os.environ.get("OPENCODE_DB", ""))


def hermes_state_db_path(environ=None):
    environ = os.environ if environ is None else environ
    state_db = str(environ.get("HERMES_STATE_DB") or "").strip()
    home = str(environ.get("HERMES_HOME") or "").strip() or "~/.hermes"
    return os.path.abspath(os.path.expanduser(state_db or os.path.join(home, "state.db")))


def agy_brain_dir_path(environ=None):
    return _default_agy_brain_dir(os.path.expanduser("~"), environ)


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


TOKEN_METER_MCP_EVIDENCE_CACHE = os.path.expanduser(
    os.environ.get(
        "TOKEN_METER_MCP_EVIDENCE_CACHE",
        "~/.token-meter/mcp-evidence-cache.json",
    )
)


MAX_CUSTOM_MODEL_PRICES = 100


MAX_MODEL_PRICE_PERIODS = 256


MAX_MODEL_PRICE_CHANGES = 256


MODEL_PRICING_MTIME_TTL_S = 0.25


MAX_SESSION_MODEL_IDENTITIES = 2_048


HERMES_MODEL_IDENTITY_LABEL = "Bedrock application profile"


BUDGET_PROVIDERS = ("claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes", "agy")


# Runtimes whose character-count cost estimates run far below real cost (#91):
# budgets still show them, but budget notifications are measured without them.
BUDGET_ALERT_EXCLUDED_ESTIMATE_PROVIDERS = ("agy",)


# Runtimes whose session storage Token Meter must never move or delete.
READ_ONLY_SESSION_PROVIDERS = ("opencode", "hermes", "agy")


GIT_DELIVERY_INTERVAL_S = 5 * 60


UPDATE_FETCH_TIMEOUT_S = 45


# Comfortably past the installer's default 600-second readiness wait.
UPDATE_STALE_AFTER_S = 30 * 60


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


SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,179}$")


DATA_URL_RE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")


BASE64_FIELD_RE = re.compile(r'("(?:data|image_url)"\s*:\s*")([A-Za-z0-9+/=]{512,})(")')


_XSESS_TTL = 15.0


_XSESS_LIVE_REFRESH_S = _XSESS_TTL


_CURRENT_SESSION_REBUILD_S = 1.5


_SOURCE_MEMBERSHIP_PROBE_S = 2.0


_SOURCE_MEMBERSHIP_FALLBACK_S = 4.0


_SOURCE_DISCOVERY_REFRESH_S = 10.0


CURRENT_SESSION_MAX_AGE_S = 30 * 60


CURRENT_SESSION_WORKING_S = 90


CURRENT_SESSION_LIMIT = 200


CURRENT_SESSION_CONTEXT_SAMPLES = 32


SESSION_STATE_CACHE_LIMIT = 32


MODEL_PROJECT_OPTION_LIMIT = 500


OTHER_LOCAL_SESSIONS_PROJECT = "__other_local_sessions__"


_matched_pace_cache = {"signature": None, "data": None}


# One entry per model-runtime pair for the global (all-session) scope, so a
# changed model only rebuilds the pairs it participates in. Reused across
# restarts via TOKEN_METER_MATCHED_PACE_CACHE. Loaded, read, mutated, and saved
# only by the thread holding the single-flight build flag below; it is never
# touched by project-scoped builds.
_matched_pace_pair_cache = {}


_matched_pace_build_state = {"building": False}


# Bump whenever the matching algorithm, thresholds, result fields, or pair-key
# format change, so stale persisted comparisons are discarded on load.
MATCHED_PACE_CACHE_SCHEMA = 2


_SKILL_CATALOG_TTL_S = 60.0


_skill_catalog_cache = {"rows": None, "at": 0.0}


_session_state_cache = {}


_session_state_build_locks = {}


_recursive_path_cache = BoundedPathCache(ttl_seconds=4.0, max_entries=64)


_model_pricing_cache = {
    "path": None, "mtime_ns": None, "mtime_checked_at": 0.0,
    "histories": {}, "effective": {}, "quotes": {},
}


_quota_cache = {}


_quota_inflight = set()


AGENT_ACCESS_SERVER = "tokenmeter"


AGENT_CURRENT_MAX_AGE_S = 6 * 60 * 60


_ISO_UTC_SUFFIX_RE = re.compile(r"(?:[Zz]|[+-]00:?00)$")


def parse_iso(ts):
    """Parse a trace timestamp; anything that is not a string is undated (None).

    The guard runs before the cache, so a list or dict value (unhashable) can
    never raise and stop a build.
    """
    if not isinstance(ts, str):
        return None
    return _parse_iso_text(ts)


@functools.lru_cache(maxsize=65536)
def _parse_iso_text(ts):
    # Logs are UTC (trailing Z). calendar.timegm treats the struct as UTC, so
    # idle/elapsed line up with time.time(). A whole-second stamp ending in Z
    # (or +00:00) is the same instant; other forms keep their existing result.
    text = ts.split(".")[0]
    if len(text) > 19:
        text = _ISO_UTC_SUFFIX_RE.sub("", text)
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


# The cached string parser stays observable through the public name.
parse_iso.cache_clear = _parse_iso_text.cache_clear


parse_iso.cache_info = _parse_iso_text.cache_info


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


OBSOLETE_SETTINGS_KEYS = ("language_signal_terms", "frustration_terms")


def sweep_orphaned_atomic_writes(path):
    """Remove temp files a killed writer of ``path`` left behind (its process is gone)."""
    # Signal 0 probes liveness only on POSIX; on Windows ``os.kill`` delivers it.
    if os.name != "posix":
        return 0
    directory = os.path.dirname(path)
    prefix = f".{os.path.basename(path)}.token-meter-"
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not name.startswith(prefix):
            continue
        pid_text, _separator, suffix = name[len(prefix):].partition("-")
        if not (pid_text.isascii() and pid_text.isdecimal()) or not suffix:
            continue
        pid = int(pid_text)
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
            continue
        except ProcessLookupError:
            pass
        except (PermissionError, OSError, OverflowError):
            continue
        candidate = os.path.join(directory, name)
        try:
            if os.path.islink(candidate) or not os.path.isfile(candidate):
                continue
            os.unlink(candidate)
            removed += 1
        except OSError:
            continue
    return removed


def builtin_model_price_tables():
    return {
        "claude": CLAUDE_PRICE,
        "codex": OPENAI_PRICE,
        "cursor": CURSOR_PRICE,
        "opencode": {},
        "google": GOOGLE_PRICE,
    }


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


def _has_scheduled_builtin_prices(provider, model):
    """Return whether a bundled model's price changes on a scheduled date."""
    history = _CANONICAL_BUILTIN_MODEL_PRICE_HISTORY.get(canonical_model_provider(provider), {})
    return model in history


COST_DISCOUNTS_SETTINGS_KEY = "cost_discounts"


COST_DISCOUNT_READ_ONLY_ERRORS = {
    "newer_version": "Discounts were saved by a newer Token Meter.",
    "unsupported_format": "Discounts in settings.json can't be changed by this version.",
}


MAX_COST_DISCOUNT_OPS = 64


_COST_DISCOUNT_SERVER_FIELDS = frozenset((
    "source", "set_by", "updated_at", "start_iso", "end_iso", "updated_at_iso",
))


_COST_DISCOUNT_RULE_FIELDS = frozenset((
    "id", "external_id", "price_list", "channel", "percent", "start", "end", "label",
))


_cost_discount_cache = {"path": None, "mtime_ns": None, "checked_at": 0.0, "state": None}


def _reset_cost_discount_cache():
    _cost_discount_cache.update(path=None, mtime_ns=None, checked_at=0.0, state=None)


def discount_of(value):
    """Return the discount carried by a priced figure, or 0 for a plain one."""
    return float(getattr(value, "discount_usd", 0.0) or 0.0)


def _public_cost_discount_rule(rule):
    public = dict(rule)
    public["start_iso"] = _discounts.iso_timestamp(rule.get("start"))
    public["end_iso"] = _discounts.iso_timestamp(rule.get("end"))
    public["updated_at_iso"] = _discounts.iso_timestamp(rule.get("updated_at"))
    return public


def _new_cost_discount_id(taken):
    while True:
        rule_id = "d_" + secrets.token_hex(6)
        if rule_id not in taken:
            return rule_id


def _discount_rule_content(rule):
    return {field: rule.get(field) for field in _discounts.CONTENT_FIELDS}


def _submitted_discount_fields(raw):
    if not isinstance(raw, dict):
        raise ValueError("Each discount must be an object.")
    unknown = sorted(set(raw) - _COST_DISCOUNT_RULE_FIELDS - _COST_DISCOUNT_SERVER_FIELDS, key=str)
    if unknown:
        raise ValueError("Unknown field: {}".format(str(unknown[0])[:40]))
    return {key: value for key, value in raw.items() if key in _COST_DISCOUNT_RULE_FIELDS}


def _merge_discount_rule(raw, existing, taken_ids, now, source, set_by, scope=None):
    """Merge one submitted rule over the rule it matches; unchanged keeps the original."""
    submitted = _submitted_discount_fields(raw)
    merged = {
        field: existing[field] for field in _discounts.CONTENT_FIELDS
        if existing and field in existing
    }
    for field in _discounts.CONTENT_FIELDS:
        if field in submitted:
            merged[field] = submitted[field]
    if scope is not None:
        for field, value in zip(("price_list", "channel"), scope):
            supplied = submitted.get(field)
            if supplied is not None and str(supplied).strip().lower() != value:
                raise ValueError("A replaced schedule may only contain its own discounts.")
            merged[field] = value
    merged["id"] = existing["id"] if existing else _new_cost_discount_id(taken_ids)
    candidate = _discounts.normalize_rule({
        **merged, "source": source, "set_by": set_by, "updated_at": now,
    })
    if existing and _discount_rule_content(candidate) == _discount_rule_content(
            _discounts.normalize_rule(existing)):
        return existing
    return candidate


def _matched_discount(rules, raw):
    """Return the index of the rule a submission names, or ``None`` to create one."""
    if not isinstance(raw, dict):
        raise ValueError("Each discount must be an object.")
    rule_id = raw.get("id")
    if rule_id not in (None, ""):
        for position, rule in enumerate(rules):
            if rule["id"] == rule_id:
                return position
        raise ValueError("No discount has that id.")
    external_id = raw.get("external_id")
    if external_id not in (None, ""):
        for position, rule in enumerate(rules):
            if rule.get("external_id") == external_id:
                return position
    return None


def _discount_scope(op):
    price_list = str(op.get("price_list") or "").strip().lower()
    if price_list not in _discounts.PRICE_LISTS:
        raise ValueError("Choose Anthropic, OpenAI, Cursor, Kiro, or OpenCode.")
    channel = op.get("channel")
    channel = _discounts.ANY_CHANNEL if channel is None else str(channel).strip().lower()
    if channel not in _discounts.RULE_CHANNELS:
        raise ValueError("Choose who bills you: the provider, AWS Bedrock, Google Vertex, Microsoft Azure, or anyone.")
    if price_list in _discounts.FIXED_CHANNEL_PRICE_LISTS and channel != _discounts.ANY_CHANNEL:
        raise ValueError("{} discounts apply however it is billed.".format(
            _discounts.PRICE_LIST_LABELS[price_list]))
    return price_list, channel


_COST_DISCOUNT_OP_FIELDS = {
    "delete": frozenset(("op", "id", "external_id")),
    "replace_scope": frozenset(("op", "price_list", "channel", "rules")),
    "set_runtime_channels": frozenset(("op", "runtime_channels", *_discounts.RUNTIME_CHANNEL_KEYS)),
}


def _reject_unknown_op_fields(op, kind):
    allowed = _COST_DISCOUNT_OP_FIELDS.get(kind)
    if kind == "upsert" and "rule" in op:
        allowed = frozenset(("op", "rule"))
    if allowed is None:
        return
    unknown = sorted((key for key in op if key not in allowed), key=str)
    if unknown:
        raise ValueError("Unknown field: {}".format(str(unknown[0])[:40]))


def _op_removes_stored_entry(op, raw):
    """Whether a delete or replace_scope names a preserved invalid stored entry."""
    if not isinstance(op, dict) or not isinstance(raw, dict):
        return False
    if op.get("op") == "delete":
        return any(op.get(key) not in (None, "") and op.get(key) == raw.get(key)
                   for key in ("id", "external_id"))
    if op.get("op") == "replace_scope":
        channel = raw.get("channel")
        return (str(raw.get("price_list") or "").strip().lower() == str(op.get("price_list") or "").strip().lower()
                and str(channel if channel is not None else "any").strip().lower()
                == str(op.get("channel") if op.get("channel") is not None else "any").strip().lower())
    return False


def _apply_cost_discount_op(rules, channels, op, now, source, set_by):
    if not isinstance(op, dict):
        raise ValueError("Each discount change must be an object.")
    kind = op.get("op")
    _reject_unknown_op_fields(op, kind)
    taken = {rule["id"] for rule in rules}
    if kind == "upsert":
        raw = op.get("rule") if "rule" in op else {
            key: value for key, value in op.items() if key != "op"
        }
        position = _matched_discount(rules, raw)
        existing = rules[position] if position is not None else None
        merged = _merge_discount_rule(raw, existing, taken, now, source, set_by)
        if position is None:
            rules.append(merged)
        else:
            rules[position] = merged
        return
    if kind == "delete":
        rule_id, external_id = op.get("id"), op.get("external_id")
        if rule_id in (None, "") and external_id in (None, ""):
            raise ValueError("Delete needs an id or external id.")
        rules[:] = [
            rule for rule in rules
            if not ((rule_id not in (None, "") and rule["id"] == rule_id)
                    or (external_id not in (None, "") and rule.get("external_id") == external_id))
        ]
        return
    if kind == "replace_scope":
        scope = _discount_scope(op)
        submitted = op.get("rules")
        if not isinstance(submitted, list) or len(submitted) > _discounts.MAX_RULES_PER_SCOPE:
            raise ValueError("A schedule needs a list of at most {} discounts.".format(
                _discounts.MAX_RULES_PER_SCOPE))
        in_scope = [rule for rule in rules if (rule["price_list"], rule["channel"]) == scope]
        outside = [rule for rule in rules if (rule["price_list"], rule["channel"]) != scope]
        replacement = []
        for raw in submitted:
            position = _matched_discount(in_scope, raw)
            existing = in_scope[position] if position is not None else None
            if existing is not None and any(rule is existing for rule in replacement):
                raise ValueError("A schedule lists the same discount twice.")
            replacement.append(_merge_discount_rule(
                raw, existing, taken | {rule["id"] for rule in replacement},
                now, source, set_by, scope=scope,
            ))
        rules[:] = outside + replacement
        return
    if kind == "set_runtime_channels":
        values = op.get("runtime_channels") if "runtime_channels" in op else {
            key: value for key, value in op.items() if key != "op"
        }
        if not isinstance(values, dict) or not values:
            raise ValueError("Choose how each Claude app is billed.")
        updated = _discounts.normalize_runtime_channels(values, strict=True)
        for key in values:
            channels[key] = updated[key]
        return
    raise ValueError("Discount changes must be upsert, delete, replace_scope, or set_runtime_channels.")


_pricing_state = {"cache_revision": None, "fingerprint_key": None, "fingerprint": None}


_pricing_recalc = {"running": False, "pending": False, "thread": None}


_API_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _write_api_token(path, token):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".{}.token-meter-{}".format(
        os.path.basename(path), secrets.token_hex(8)))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(token + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


BUDGET_API_FIELDS = ("allocations", "thresholds", "native_notifications", "default_session_budget")


def _public_budget_settings(settings):
    """Budget settings for the local API; session ids never leave the server."""
    return {
        "currency": settings["currency"],
        "allocations": {provider: settings["allocations"][provider] for provider in BUDGET_PROVIDERS},
        "monthly_total": settings["monthly_total"],
        "thresholds": list(settings["thresholds"]),
        "native_notifications": settings["native_notifications"],
        "default_session_budget": settings["default_session_budget"],
    }


def budget_settings_revision(settings):
    canonical = json.dumps(_public_budget_settings(settings), sort_keys=True, separators=(",", ":"))
    return "b_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


INVALID_STORED_BUDGETS_ERROR = "Stored budgets are invalid; fix them in Token Meter first."


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


def _verified_identity_matches_source(verified, source):
    if not verified:
        return False
    return (
        tuple(verified.get("revision") or ())
        == _session_model_identity_revision(source)
    )


def _apply_verified_session_identity(source, verified):
    source["model"] = verified["model"]
    source["model_provider"] = verified["model_provider"]
    source["_verified_inherited_prefix"] = verified["inherited_prefix"]
    source["_verified_inherited_prefix_revision"] = list(verified["revision"])


_settings_file_locks = {}


_goal_evidence_cache = {"key": None, "value": None}


_goal_cache = {"signature": None, "built_at": 0.0,
               "native": {"ready": False, "items": [], "additional_count": 0}}


GOAL_NATIVE_REFRESH_S = 60.0


NOTIFICATIONS_SCHEMA_VERSION = 1


NOTIFICATION_EVALUATE_INTERVAL_S = 2.0


_notification_runtime = {"evaluated_at": None, "quota_established": False, "hints_reseed": True}


_PUBLIC_EVENT_FIELDS = ("id", "kind", "severity", "title", "body", "link", "created_at", "claimed", "claimed_by")


WORK_PAUSE_CHOICES = ("1h", "tomorrow", "indefinite", "resume")


WORK_INSIGHTS_UNSUPPORTED_ERROR = "Work insights are available on macOS only."


def _pause_until(choice, now=None):
    now = time.time() if now is None else now
    if choice == "resume":
        return None
    if choice == "indefinite":
        return "indefinite"
    if choice == "1h":
        return now + 3600
    local = datetime.datetime.fromtimestamp(now)
    tomorrow = (local + datetime.timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def _safe_update_revision(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else ""


def _safe_update_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
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


def catalog_counts(catalog):
    advertised = len(catalog or [])
    deferred = sum(1 for row in catalog or [] if row.get("defer_loading"))
    return {"advertised": advertised, "eager": max(0, advertised - deferred), "deferred": deferred}


_claude_native_adapters = {}


_cursor_native_adapters = {}


_kiro_native_adapters = {}


_agy_native_adapters = {}


_opencode_adapter_key = {"configured": None, "key": None}


def _opencode_json(value, default=None):
    """Decode one OpenCode SQLite JSON text value without failing the session."""
    return _native_opencode_json(value, default)


def reset_cursor_metadata_cache():
    for adapter in _cursor_native_adapters.values():
        adapter.reset_metadata_cache()


def git_delivery_project_roots(sources):
    """Return ordered unique normalized roots relevant to Git candidate selection."""
    roots = []
    seen = set()
    for source in sources or ():
        raw_project = source.get("project") if isinstance(source, dict) else ""
        if not isinstance(raw_project, str):
            continue
        if project_filter_key(raw_project) == OTHER_LOCAL_SESSIONS_PROJECT:
            continue
        root = os.path.abspath(os.path.expanduser(raw_project))
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return tuple(roots)


def git_delivery_source_signature(roots):
    """Return a private ordered-root membership signature for Git candidates."""
    return hashlib.sha256(
        "\0".join(roots).encode("utf-8", "replace"),
    ).hexdigest()


def _matching_price(model, table):
    _rule, price = _catalog_matching_price(model, table)
    return price


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


# OpenCode message rows carry the upstream provider that served the model.
# Only direct vendor identities may reuse that vendor's public catalog; the
# OpenCode and OpenCode Go aggregators keep their own (possibly negotiated)
# rates and never borrow a vendor price.
OPENCODE_UPSTREAM_MODEL_PROVIDERS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "cursor": "cursor",
}


_CLAUDE_FAST_PREMIUM_RULES = frozenset((
    "claude-fable-5-2", "claude-mythos-5-2",
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
))


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


def add_model_summary(stats, model, usage, cost, cost_available=None, discount=None):
    """Accumulate one request; ``discount`` defaults to the one ``cost`` carries."""
    return _domain_add_model_summary(
        stats, model, usage, cost, cost_available=cost_available,
        discount=discount_of(cost) if discount is None else discount,
    )


def add_model_daily(stats, model, usage, cost, ts, cost_available=None, discount=None):
    """Accumulate exact trace-reported model I/O into local calendar days."""
    return _domain_add_model_daily(
        stats, model, usage, cost, ts, cost_available=cost_available,
        discount=discount_of(cost) if discount is None else discount,
    )


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


def wait_time_summary(samples):
    return _domain_wait_time_summary(samples)


def performance_summary(samples, total_output_tokens=0):
    return _domain_performance_summary(samples, total_output_tokens)


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


USER_TURN_CONTEXT_CHARS = 600


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


_WORK_TURNS_HANDOFF_KEY = "_work_turns_handoff"


def pi_work_actions(turns):
    """Tool calls in a Pi session as Work evidence, kept content-free.

    Pi's adapter keeps only a normalized tool name and kind, never the call's
    arguments, so each action carries the kind and its turn's timestamp and no
    edited paths or command kinds.
    """
    actions = []
    for turn in turns or ():
        ts = float(turn.get("end") or turn.get("start") or 0)
        for tool in turn.get("tools") or ():
            actions.append(_work_evidence.tool_action(tool.get("name"), None, ts))
    return actions


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


def tool_summary(executions):
    return _domain_tool_summary(executions)


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


def enable_mcp_parallel_index():
    """Let the MCP fill cold summaries and stat rows on the load-aware worker pool."""
    global _parallel_index_enabled
    _parallel_index_enabled = True


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
            "context_window": None, "user_inputs": [], "fallback_user_inputs": []}


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


def summarize_tool_evidence(calls, catalog=None):
    return _domain_summarize_tool_evidence(calls, catalog)


def _cache_pricing_provider(facts):
    provider = facts.get("pricing_provider")
    if provider in ("claude", "codex", "cursor"):
        return provider
    model = str(facts.get("model") or "").lower()
    if "claude" in model:
        return "claude"
    if re.match(r"(?:openai/)?(?:gpt-(?!oss)|o\d)", model):
        return "codex"
    return None


def _is_child_agent_row(row):
    """A row is a child run only when every agent record names a parent."""
    records = [r for r in row.get("_agent_records") or () if isinstance(r, dict)]
    return bool(records) and all(r.get("parent_id") for r in records)


_index_worker_pressure = {"event": None, "yielding": False}


def _system_load_average():
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def _source_size(source):
    try:
        return os.path.getsize(source.get("path") or "")
    except (OSError, TypeError, ValueError):
        return 0


_shared_index_pool = {"owner": None, "pools": {}}


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


def folded_child_root_id(source):
    """Return the root session id an additive child source folds into, if any.

    Only OpenCode child sessions with a resolved root carry `agent_root_id`;
    every other source returns an empty string and keeps its own identity.
    """
    if not isinstance(source, dict) or source.get("provider") != "opencode":
        return ""
    return str(source.get("agent_root_id") or "")


def fold_child_source(selected, sources):
    """Map a folded child source to its discovered root source, else keep it."""
    root_id = folded_child_root_id(selected)
    if not root_id:
        return selected
    for source in sources or ():
        if (
            source.get("provider") == selected.get("provider")
            and str(source.get("id") or "") == root_id
        ):
            return source
    return selected


def newest_current_source(sources):
    """Pick the live source, keeping a child run under its root session."""
    rows = list(sources or ())
    if not rows:
        return None
    newest = max(rows, key=lambda source: source.get("mtime") or 0)
    return fold_child_source(newest, rows)


_CONFIGURED_CAPABILITY_TTL_S = 60.0


_configured_capability_cache = {}


def optional_capability_summary(control_groups):
    return _domain_optional_capability_summary(control_groups)


@functools.lru_cache(maxsize=16384)
def _trace_path_key(path):
    return _compare_trace_key({"path": path})


def _browser_session_row(row):
    """Swap a trace path for the opaque compare key the dashboard selects by."""
    if not isinstance(row, dict) or not row.get("path"):
        return row
    public = {key: value for key, value in row.items() if key != "path"}
    public["key"] = _trace_path_key(str(row["path"]))
    return public


# Cross-session snapshots are replaced wholesale, never mutated, so one
# encoding per snapshot object serves every poll until the next rebuild.
# /state serves the published snapshot and /session the latest rebuild; they
# differ while idle, so both stay encoded. The nonce keeps a page that
# outlived a server restart from matching a fresh revision.
_dashboard_cross_cache = {"serial": 0, "entries": []}


_dashboard_cross_nonce = os.urandom(4).hex()


_DASHBOARD_CROSS_SLOTS = 2


_ABSENT = object()


CLAUDE_DESKTOP_CLIENT = "claude_desktop"


AGENT_ACCESS_CLIENTS = ("codex", "claude")


def _same_path(left, right):
    try:
        resolve = lambda value: os.path.normcase(os.path.realpath(os.path.expanduser(str(value or ""))))
        return resolve(left) == resolve(right)
    except (OSError, ValueError):
        return False


def _config_stamp(path):
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


CLAUDE_APP_RUNNING_ERROR = "Quit the Claude app first. It rewrites its config while open and would drop the change."


CLAUDE_APP_UNREADABLE_ERROR = "Token Meter could not read the Claude app's MCP config, so it left the file unchanged."


AGENT_SKILL_LABELS = {"codex": "Codex", "claude": "Claude Code"}


def _verified_agent_access_change(client, enabled, repairing, status_getter, restart_hint):
    after = status_getter(client)
    verified = bool(after.get("connected")) if enabled else not bool(after.get("configured"))
    if not verified:
        return {"ok": False, "error": "The connection command completed, but the saved configuration could not be verified."}
    label = after.get("label") or client
    return {
        "ok": True, "changed": True, "repaired": repairing, "client": after,
        "restart_required": True,
        "message": (f"{label} connection repaired. {restart_hint}"
                    if repairing else
                    f"{label} {'connected' if enabled else 'disconnected'}. {restart_hint}"),
    }


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


MATCHED_PACE_MIN_PAIRS = 20


MATCHED_PACE_MIN_COVERAGE = 0.30


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
    # Bit-for-bit the draws of ``statistics.median(rng.choice(values) ...)``:
    # ``choice`` is rejection sampling over ``getrandbits(n.bit_length())``,
    # inlined here because the call layers dominated the build time.
    getrandbits = rng.getrandbits
    count = len(values)
    bits = count.bit_length()
    middle = count // 2
    odd = count % 2
    medians = []
    for _ in range(repetitions):
        drawn = []
        for _ in range(count):
            index = getrandbits(bits)
            while index >= count:
                index = getrandbits(bits)
            drawn.append(values[index])
        drawn.sort()
        medians.append(drawn[middle] if odd else (drawn[middle - 1] + drawn[middle]) / 2)
    return _percentile(medians, 0.025), _percentile(medians, 0.975)


MATCHED_PACE_WINDOW_KEYS = (
    "today", "yesterday", "7", "30", "90", "month", "last_month", "all",
)


_MATCHED_PACE_INT_FIELDS = ("a_samples", "b_samples", "matched_pairs")


_MATCHED_PACE_FLOAT_FIELDS = ("coverage", "pace_ratio", "ci_low", "ci_high")


_MATCHED_PACE_COMPARISON_KEYS = frozenset(
    ("a_id", "b_id", "available", "reason")
    + _MATCHED_PACE_INT_FIELDS + _MATCHED_PACE_FLOAT_FIELDS
)


# Stored numbers outside these bounds are corrupt; the pair is recomputed.
_MATCHED_PACE_MAX_COUNT = 10 ** 9


_MATCHED_PACE_MAX_RATIO = 1e15


_MATCHED_PACE_MAX_REASON = 200


# Every reason string matched_pace_comparison can produce.
_MATCHED_PACE_REASON_RE = re.compile(
    r"|needs \d{1,9} timed turns per runtime"
    r"|only \d{1,9} comparable turns; needs \d{1,9}"
    r"|only \d{1,9}% of the smaller history overlaps"
)


def _valid_matched_pace_comparison(value, a_id, b_id):
    """Return a rebuilt comparison with exactly the builder keys, else None.

    Never raises: magnitude checks run before any float conversion, so a huge
    JSON integer cannot overflow. Unknown keys reject the comparison so stored
    content can never reach API output beyond the builder's own fields.
    """
    if not isinstance(value, dict) or set(value) != _MATCHED_PACE_COMPARISON_KEYS:
        return None
    if value["a_id"] != a_id or value["b_id"] != b_id:
        return None
    for field in _MATCHED_PACE_INT_FIELDS:
        number = value[field]
        if (isinstance(number, bool) or not isinstance(number, int)
                or not 0 <= number <= _MATCHED_PACE_MAX_COUNT):
            return None
    for field in _MATCHED_PACE_FLOAT_FIELDS:
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return None
        # Chained comparison is exact for ints of any size and rejects NaN.
        if not 0 <= number <= _MATCHED_PACE_MAX_RATIO:
            return None
        if isinstance(number, float) and not math.isfinite(number):
            return None
    if not 0 <= value["coverage"] <= 1:
        return None
    if not isinstance(value["available"], bool):
        return None
    reason = value["reason"]
    if (not isinstance(reason, str) or len(reason) > _MATCHED_PACE_MAX_REASON
            or not _MATCHED_PACE_REASON_RE.fullmatch(reason)):
        return None
    return {key: value[key] for key in (
        "a_id", "b_id", "a_samples", "b_samples", "matched_pairs", "coverage",
        "pace_ratio", "ci_low", "ci_high", "available", "reason",
    )}


def _valid_matched_pace_entry(entry):
    """Return the (key, cached) pair for a usable stored entry, else None."""
    if not isinstance(entry, dict):
        return None
    a_id, b_id = entry.get("a_id"), entry.get("b_id")
    signature, windows = entry.get("signature"), entry.get("windows")
    if not (isinstance(a_id, str) and isinstance(b_id, str)
            and isinstance(signature, str) and isinstance(windows, dict)):
        return None
    if set(windows) != set(MATCHED_PACE_WINDOW_KEYS):
        return None
    rebuilt = {}
    for window in MATCHED_PACE_WINDOW_KEYS:
        comparison = _valid_matched_pace_comparison(windows[window], a_id, b_id)
        if comparison is None:
            return None
        rebuilt[window] = comparison
    return (a_id, b_id), {"signature": signature, "windows": rebuilt}


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


_work_source_keys = {"sources": None, "salt": None, "keys": {}}


WORK_DRILL_ENUMS = {
    "work_type": set(_work.WORK_TYPES) | {"unclear"},
    "complexity": {"routine", "everyday", "complex"},
    "tier": {"light", "standard", "premium"},
    "effort": {"default", "low", "medium", "high", "xhigh", "max", "ultra"},
    "outcome": {"single_shot", "accepted", "recovered", "ended_on_pushback", "unclear", "pending"},
    "tag": set(_domain_work_tags),
}


def _work_bucket_valid(value, grain):
    if grain == "day":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value))


def _work_label_since(settings):
    """First day the classifier labels, from the history setting; empty when it labels all history."""
    days = settings.get("backfill_days") or 0
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat() if days else ""


def metric_coverage(rows, metric):
    return _domain_metric_coverage(rows, metric)


def _builder_recap_line_count(value, maximum=(1 << 53) - 1):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= maximum else None
    if isinstance(value, float):
        if (math.isfinite(value) and value.is_integer()
                and 0 <= value <= maximum):
            return int(value)
    return None


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


def _physical_source_path(value):
    value = str(value or "")
    if not value or not os.path.isabs(os.path.expanduser(value)):
        return ""
    return os.path.abspath(os.path.expanduser(value))


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


AGENT_RESULT_MAX_CHARS = 8000


AGENT_CHECK_FOCUS = {"continue", "cost", "context", "tools", "next_phase"}


AGENT_USAGE_FOCUS = {"spend", "models", "tools", "changes"}


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


# Runtimes whose every priced record is timestamped no later than the newest
# file mtime the source revision tracks, so an older source cannot reach a window.
_ACTIVITY_BOUNDED_PROVIDERS = frozenset(("claude", "codex"))


def _window_start_ts(day_keys):
    earliest = datetime.date.fromisoformat(min(day_keys))
    # One spare day covers DST and local/UTC day-boundary differences.
    return datetime.datetime.combine(earliest, datetime.time()).timestamp() - 86400.0


def _source_order_mtime(source):
    try:
        return float(source.get("mtime") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


# Older discovered sources may miss the caller's run, so scope=current reuses only this fresh a scan.
HISTORY_SOURCE_REUSE_S = 60.0


_history_scan = {"progress": None, "last_complete": None}


def _history_scan_failed():
    return RuntimeError("Token Meter could not scan session history.")


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


def parse_codex_quota(payload, source="Codex app-server", now=None):
    return openai_quotas.parse_quota(payload, source=source, now=now)


def parse_claude_quota(payload, credentials=None, now=None):
    return anthropic_quotas.parse_quota(
        payload, credentials=credentials, now=now
    )


def parse_cursor_quota(payload, now=None):
    return cursor_quotas.parse_quota(payload, now=now)


def _quota_loading_row(provider):
    labels = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}
    return quota_provider(
        provider, labels[provider], "loading", "Provider account", error="Loading provider quotas.",
    )


def page_root(path):
    """Directory that holds the dashboard and its bundled assets."""
    if dashboard_page.is_manifest(path):
        return dashboard_page.dashboard_root(path)
    return os.path.dirname(path)


def page_available(path):
    """True when ``path`` can be served whole; a manifest needs every listed part.

    ``page_path()`` only returns files that exist, so a single-file page is ready
    whenever a path was found.
    """
    if dashboard_page.is_manifest(path):
        return dashboard_page.parts_present(path)
    return bool(path)


def page_bytes(path):
    """Assembled dashboard bytes, or None when a part is missing or unreadable."""
    try:
        return dashboard_page.read_page(path).encode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def is_dashboard_page_path(req_path):
    return req_path == "/" or bool(re.fullmatch(r"/sessions/[^/]{1,240}/?", req_path or ""))


_DASHBOARD_ASSETS = {
    "/assets/fonts/Tektur-Variable.ttf": ("assets/fonts/Tektur-Variable.ttf", "font/ttf"),
    "/assets/brand/logo-splunk-by-cisco-rgb-w.png": (
        "assets/brand/logo-splunk-by-cisco-rgb-w.png",
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


def dashboard_asset_content_type(req_path):
    spec = _DASHBOARD_ASSETS.get(req_path)
    return spec[1] if spec else None


def missing_performance_html():
    return """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Token Meter setup error</title></head>
<body><h1>performance.html is missing</h1>
<p>Reinstall Token Meter from a complete source checkout.</p></body>
</html>"""


def _page_within_runtime(page, runtime):
    """True when the served page lives under ``runtime``; installers ask this instead of reading a path."""
    if not page or not isinstance(runtime, str) or not runtime or len(runtime) > 4096:
        return False
    if not os.path.isabs(runtime):
        return False
    try:
        root = os.path.normcase(os.path.realpath(runtime))
        served = os.path.normcase(os.path.realpath(page))
        return os.path.commonpath([root, served]) == root
    except (OSError, ValueError):
        return False


class TokenMeterHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def server_bind(self):
        # http.server stores socket.getfqdn(host) as server_name, a blocking
        # reverse lookup. The bind address is loopback and no code reads the
        # FQDN, so keep the host as-is and let listen() start without DNS.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port
