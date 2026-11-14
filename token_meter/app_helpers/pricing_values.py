"""Pricing value helpers."""

import datetime
import math

from token_meter.models.catalog import GPT_56_LONG_CONTEXT_TOKENS, GPT_56_PRICE_UPDATE_AT
from token_meter.models import discounts as _discounts
from token_meter.app_helpers.common import _cached_price_query, parse_iso


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


COST_DISCOUNTS_VERSION = 1


def _cost_discount_state_from_raw(raw):
    """Validate a stored ``cost_discounts`` value, keeping every valid rule."""
    malformed = []
    if isinstance(raw, dict):
        rules, invalid = _discounts.normalize_rules_lenient(raw.get("rules"), malformed)
        channels = _discounts.normalize_runtime_channels(raw.get("runtime_channels"))
    else:
        rules, invalid = [], (0 if raw is None else 1)
        channels = _discounts.normalize_runtime_channels(None)
    # Only this build's exact format (version 1, or no version, with a list of rules)
    # is ever rewritten; anything else is read for what this build understands.
    shape_ok = raw is None or (
        isinstance(raw, dict)
        and ("version" not in raw
             or (type(raw["version"]) is int and raw["version"] == COST_DISCOUNTS_VERSION))
        and isinstance(raw.get("rules", []), list)
    )
    version = raw.get("version") if isinstance(raw, dict) else None
    newer = (isinstance(version, (int, float)) and not isinstance(version, bool)
             and version > COST_DISCOUNTS_VERSION)
    return {
        "read_only": not shape_ok,
        "read_only_reason": None if shape_ok else ("newer_version" if newer else "unsupported_format"),
        # Entries invalid on their own are kept verbatim on the next write, still ignored for pricing.
        "dropped": tuple(malformed),
        "rules": tuple(rules),
        "runtime_channels": channels,
        "invalid": invalid,
        "index": _discounts.build_index(rules),
        "revision": _discounts.revision(rules, channels),
    }


def _price_query_from_compat(model_provider, model, variant=None, at=None):
    """Construct a bounded cached request for the compatibility facade."""
    model_id = str(model or "").strip() or "unknown-model"
    if len(model_id) > 160:
        model_id = "unknown-model"
    variant = str(variant).strip() if variant is not None else None
    return _cached_price_query(
        model_provider, model_id, variant or None, _price_timestamp(at)
    )


def _price_multipliers(u, model, provider, at=None):
    """Return current published long-context multipliers without repricing history."""
    timestamp = _price_timestamp(at)
    if timestamp is not None and timestamp < GPT_56_PRICE_UPDATE_AT:
        return 1.0, 1.0
    compact = str(model or "").replace(" ", "-").lower()
    # Codex bills credits and subscriptions, which OpenAI documents without the API's
    # long-context surcharge; Cursor passes API pricing through.
    if (provider != "cursor" or
            not compact.startswith(("gpt-5.6", "gpt-6-", "gpt-6.1-"))):
        return 1.0, 1.0
    input_tokens = (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0)
    )
    if input_tokens > GPT_56_LONG_CONTEXT_TOKENS:
        return 2.0, 1.5
    return 1.0, 1.0
