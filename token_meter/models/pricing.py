"""Pure effective-dated price resolution over the model catalog."""

import re
import time
from datetime import timezone

from token_meter.contracts import EvidenceBasis, PriceQuote

from .catalog import (
    BUILTIN_MODEL_PRICE_HISTORY,
    BUILTIN_PRICE_TABLES,
    CURSOR_VARIANT_MODEL_IDS,
    MODEL_PRICE_FIELDS,
    MODEL_PROVIDER_IDS,
)


def observed_timestamp(observed_at):
    if observed_at is None:
        return None
    if isinstance(observed_at, bool):
        return None
    if isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool):
        return float(observed_at)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.timestamp()


def builtin_price_table(provider_id, observed_at=None):
    """Return a copy of bundled prices effective at one observation time."""

    if provider_id not in MODEL_PROVIDER_IDS:
        return {}
    table = {
        model: dict(prices)
        for model, prices in BUILTIN_PRICE_TABLES[provider_id].items()
    }
    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        return table
    for model, periods in BUILTIN_MODEL_PRICE_HISTORY.get(provider_id, {}).items():
        selected = None
        for effective_from, prices in periods:
            if effective_from is None or timestamp >= effective_from:
                selected = prices
        if selected is not None:
            table[model] = dict(selected)
    return table


def price_period_key(provider_id, observed_at=None, histories=None):
    """Return the bounded cache bucket containing one observation time."""

    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        return "current"
    cutovers = {
        effective_from
        for periods in BUILTIN_MODEL_PRICE_HISTORY.get(provider_id, {}).values()
        for effective_from, _prices in periods
        if effective_from is not None
    }
    for periods in (histories or {}).values():
        cutovers.update(
            revision["effective_from"]
            for revision in periods
            if revision["effective_from"] is not None
        )
    return max((value for value in cutovers if value <= timestamp), default=0)


def revision_at(periods, observed_at=None, now=None):
    timestamp = observed_timestamp(observed_at)
    if timestamp is None:
        timestamp = time.time() if now is None else now
    selected = None
    for revision in periods or ():
        effective_from = revision["effective_from"]
        if effective_from is None or effective_from <= timestamp:
            selected = revision
        else:
            break
    return selected


def effective_price_table(provider_id, observed_at=None, histories=None, now=None):
    """Apply validated user history to one canonical provider's bundled table."""

    table = builtin_price_table(provider_id, observed_at)
    if provider_id not in MODEL_PROVIDER_IDS:
        return table
    for model, periods in (histories or {}).items():
        revision = revision_at(periods, observed_at, now=now)
        if not revision or revision.get("use_builtin") is True:
            continue
        if revision.get("inactive") is True:
            table.pop(model, None)
        else:
            table[model] = dict(revision["prices"])
    return table


def _model_alias_candidates(model_id, provider_id=None):
    """Return bounded provider-scoped aliases for one native model id."""

    compact = str(model_id or "").strip().replace(" ", "-").lower()
    candidates = [compact]
    provider = str(provider_id or "").strip().lower()
    if not provider:
        return candidates
    native_ids = [compact]
    arn = re.fullmatch(
        r"arn:aws(?:-[a-z0-9-]+)?:bedrock:[a-z0-9-]+:[0-9]{12}:"
        r"inference-profile/(.+)",
        compact,
    )
    if arn:
        native_ids.append(arn.group(1))
    for native_id in native_ids:
        prefixes = [provider + separator for separator in (".", "/", ":")]
        prefixes.extend(
            region + "." + provider + "."
            for region in ("us", "eu", "apac", "global")
        )
        for prefix in prefixes:
            if not native_id.startswith(prefix):
                continue
            alias = native_id[len(prefix):]
            if alias and alias not in candidates:
                candidates.append(alias)
    return candidates


def model_alias_matches(model_id, provider_id, canonical_id):
    """Return whether one native id is an exact bounded alias of a catalog id."""

    canonical = str(canonical_id or "").strip().replace(" ", "-").lower()
    return canonical in _model_alias_candidates(model_id, provider_id)


def matching_price(model_id, table, provider_id=None):
    """Return the longest provider-scoped catalog rule for one model id."""

    if model_id in table:
        return model_id, table[model_id]
    for candidate in _model_alias_candidates(model_id, provider_id):
        for rule in sorted(table, key=len, reverse=True):
            compact_rule = str(rule).replace(" ", "-").lower()
            suffix = candidate[len(compact_rule):]
            if (candidate.startswith(compact_rule) and
                    (not suffix or re.fullmatch(
                        r"(?:(?:[._:/@+-](?:v[0-9]+(?::[0-9]+)?|[0-9]{8}))|"
                        r"(?:[._:/@+-][0-9]{4}-[0-9]{2}-[0-9]{2}))+",
                        suffix,
                    ))):
                return rule, table[rule]
    return None, None


def quote_for(query, effective_table=None):
    """Return an exact provider-scoped quote; never search another provider."""

    provider_id = query.model.provider_id
    if provider_id not in MODEL_PROVIDER_IDS:
        return PriceQuote.unavailable(query.model)
    table = (
        builtin_price_table(provider_id, query.observed_at)
        if effective_table is None else effective_table
    )
    model_id = query.model.model_id
    if provider_id == "cursor":
        for cursor_model_id in CURSOR_VARIANT_MODEL_IDS:
            if model_alias_matches(model_id, provider_id, cursor_model_id):
                model_id = "{}-{}".format(
                    cursor_model_id, query.model.variant or "",
                )
                break
    matched_rule, prices = matching_price(model_id, table, provider_id)
    if prices is None:
        return PriceQuote.unavailable(query.model)
    if any(field not in prices for field in MODEL_PRICE_FIELDS):
        return PriceQuote.unavailable(query.model)
    return PriceQuote(
        model=query.model,
        input_per_million=float(prices["input"]),
        output_per_million=float(prices["output"]),
        cache_read_per_million=float(prices["cache_read"]),
        cache_write_per_million=float(prices["cache_write"]),
        basis=EvidenceBasis.ESTIMATED,
        matched_rule=matched_rule,
    )
