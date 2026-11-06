"""Value normalization helpers used by the composition module."""

import json
import math
import re

from token_meter.domain.insights import normalize_insights as _domain_normalize_insights
from token_meter.domain import notifications as _notifications
from token_meter.models.catalog import MODEL_PRICE_FIELDS, normalize_model_id
from token_meter.runtimes.codex import AUTO_REVIEW_MODEL, CODEX_BUILTIN_NAMESPACES
from token_meter.app_helpers.common import (
    BUDGET_PROVIDERS,
    CHARS_PER_TOKEN,
    MAX_MODEL_PRICE_PERIODS,
    tool_identity,
)


MODEL_PRICE_PROVIDERS = ("claude", "codex", "cursor", "opencode", "google")


MAX_MODEL_PRICE = 1_000_000.0


SESSION_MODEL_IDENTITY_KEY_RE = re.compile(r"^[a-f0-9]{64}$")


SESSION_CLAUDE_MODEL_ID_RE = re.compile(
    r"^claude-(?:haiku|sonnet|opus)-[a-z0-9][a-z0-9._:-]{0,139}$",
    re.IGNORECASE,
)


DEFAULT_RUNTIME_BUDGET = 0.0


DEFAULT_BUDGET_THRESHOLDS = (80, 90, 100)


DEFAULT_SESSION_BUDGET = 10.0


MIN_SESSION_BUDGET = 0.5


MAX_MONTHLY_BUDGET = 100_000_000.0


MAX_SESSION_BUDGETS = 500


UPDATE_CHECK_INTERVAL_S = 10 * 60


def normalize_model_price_provider(provider):
    provider = str(provider or "").strip().lower()
    if provider == "openai":
        provider = "codex"
    if provider not in MODEL_PRICE_PROVIDERS:
        raise ValueError("Provider must be Claude, Codex / OpenAI, Cursor, OpenCode, or Google Gemini.")
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


def normalize_session_budget_value(value, label="Session budget"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    value = float(value)
    if (not math.isfinite(value)
            or value < MIN_SESSION_BUDGET
            or value > MAX_MONTHLY_BUDGET):
        raise ValueError(
            f"{label} must be between "
            f"{MIN_SESSION_BUDGET:g} and {MAX_MONTHLY_BUDGET:,.0f}."
        )
    return value


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
    default_session_budget = normalize_session_budget_value(
        default_session_budget, "Default session budget",
    )

    raw_session_budgets = values.get("session_budgets") or {}
    if not isinstance(raw_session_budgets, dict):
        raise ValueError("Session budgets must be an object.")
    if len(raw_session_budgets) > MAX_SESSION_BUDGETS:
        raise ValueError(f"Token Meter supports at most {MAX_SESSION_BUDGETS} saved session budgets.")
    session_budgets = {}
    for raw_session_id, raw_budget in raw_session_budgets.items():
        if not isinstance(raw_session_id, str):
            raise ValueError("Session budget IDs must be strings.")
        session_id = raw_session_id.strip()
        if not session_id or len(session_id) > 240:
            raise ValueError("Session budget IDs must be 1 to 240 characters.")
        session_budgets[session_id] = normalize_session_budget_value(raw_budget)

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
        "session_budgets": session_budgets,
        "monthly_total": total,
        "allocations": allocations,
        "thresholds": thresholds,
        "native_notifications": native_notifications,
    }


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


MENUBAR_CACHE_TIMER_MODES = ("always", "final2", "off")


DEFAULT_MENUBAR_CACHE_TIMER = "always"


def normalize_menubar_settings(values):
    """Validate the machine-wide menu-bar preferences shared by both surfaces."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("Menu bar settings must be an object.")
    mode = values.get("cache_timer", DEFAULT_MENUBAR_CACHE_TIMER)
    if mode not in MENUBAR_CACHE_TIMER_MODES:
        raise ValueError("Cache timer must be Always, Last 2 min, or Off.")
    return {"cache_timer": mode}


def normalize_notification_settings(values):
    """Validate the machine-wide quota alert preferences shared by every companion."""
    if not isinstance(values, dict):
        raise ValueError("Notification settings must be an object.")
    quota_alerts = values.get("quota_alerts")
    if not isinstance(quota_alerts, bool):
        raise ValueError("Quota notifications must be on or off.")
    threshold = values.get("quota_threshold")
    if isinstance(threshold, bool) or threshold not in _notifications.USER_QUOTA_THRESHOLDS:
        raise ValueError("Quota alert threshold must be 80, 90, or 95 percent.")
    return {"quota_alerts": quota_alerts, "quota_threshold": int(threshold)}


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
            host_provided = False
            if name.startswith("mcp__"):
                ident = tool_identity(name)
                namespace = ident["namespace"]
                kind = "mcp"
            elif str(namespace).startswith("mcp__"):
                parts = str(namespace).split("__")
                namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
                raw_identity = f"mcp__{namespace}__{name}"
                kind = "mcp"
            elif isinstance(children, list) and namespace not in CODEX_BUILTIN_NAMESPACES:
                raw_identity = f"mcp__{namespace}__{name}"
                kind = "mcp"
                host_provided = True
            else:
                kind = "tool"
            definition = {
                "description": child.get("description") or "",
                "inputSchema": child.get("inputSchema") or child.get("input_schema") or {},
            }
            entry = {
                "namespace": namespace,
                "name": raw_identity,
                "kind": kind,
                "defer_loading": bool(child.get("deferLoading", parent_deferred)),
                "definition_tokens": len(json.dumps(definition, sort_keys=True)) // CHARS_PER_TOKEN,
            }
            if host_provided:
                entry["host_provided"] = True
            out.append(entry)
    return out[:240]


def normalize_insights(rows, limit=12):
    return _domain_normalize_insights(rows, limit)
