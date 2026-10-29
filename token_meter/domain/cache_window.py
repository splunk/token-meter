"""Content-free prompt-cache warm window for one session's latest request."""

import math
import re

from token_meter.models.catalog import DOCUMENTED_CACHE_WINDOWS
from token_meter.models.discounts import PRICE_LISTS, REQUEST_CHANNELS, RULE_CHANNELS

BASIS_RECORDED = "recorded"
BASIS_DOCUMENTED = "documented"
MAX_WINDOW_S = 24 * 3600
_SOURCES = frozenset(row["source"] for row in DOCUMENTED_CACHE_WINDOWS)
_MATCHERS = tuple(
    (re.compile(row["match"], re.IGNORECASE), row) for row in DOCUMENTED_CACHE_WINDOWS
)


RATE_MODIFIERS = ("speed", "inference_geo", "service_tier")


def _modifier(value):
    if value is None:
        return None
    # A non-string or oversized value stays unpriceable rather than becoming a standard rate.
    return str(value)[:32] if isinstance(value, str) else "unrecognized"


def _discount_price_list(value):
    return value if value in PRICE_LISTS else None


def _discount_channel(value):
    return value if value in REQUEST_CHANNELS or value in RULE_CHANNELS else "unknown"


def _count(value):
    if isinstance(value, bool):
        return 0
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


class CacheRequestTracker:
    """Keep only the facts the countdown needs while a runtime folds its requests."""

    def __init__(self, pricing_provider=None, cache_reported=True,
                 discount_price_list=None, discount_channel=None):
        self.pricing_provider = pricing_provider
        # The (price list, channel) the runtime discounts this session's requests under,
        # so the cold-cache estimate is discounted exactly like the real cost.
        self.discount_price_list = discount_price_list
        self.discount_channel = discount_channel
        self.cache_reported = bool(cache_reported)
        self.latest = None
        self.ttl_s = None
        self._ttl_ts = None
        self._ttl_model = None
        self.requests = 0
        self.cache_observed = False
        self.writes_observed = False

    def observe(self, ts, model, usage, start_ts=None, pricing_provider=None,
                pricing_variant=None, discount_price_list=None, discount_channel=None):
        try:
            ts = float(ts or 0)
        except (TypeError, ValueError):
            return
        if not math.isfinite(ts) or ts <= 0:
            return
        usage = usage if isinstance(usage, dict) else {}
        self.requests += 1
        read = _count(usage.get("cache_read_input_tokens"))
        write = _count(usage.get("cache_creation_input_tokens"))
        write_5m = _count(usage.get("cache_creation_5m_input_tokens"))
        write_1h = _count(usage.get("cache_creation_1h_input_tokens"))
        if read or write:
            self.cache_observed = True
        if write:
            self.writes_observed = True
        if (write_5m or write_1h) and (self._ttl_ts is None or ts >= self._ttl_ts):
            # A shorter breakpoint after a 1-hour one expires first.
            self.ttl_s = 300 if write_5m else 3600
            self._ttl_ts = ts
            self._ttl_model = str(model or "")
        if self.latest is not None and ts < self.latest["ts"]:
            return
        try:
            start = float(start_ts or 0)
        except (TypeError, ValueError):
            start = 0.0
        if not math.isfinite(start) or start <= 0 or start > ts:
            start = ts
        self.latest = {
            "ts": ts,
            "anchor_ts": start,
            "model": str(model or ""),
            "pricing_provider": pricing_provider or self.pricing_provider,
            "prefix_tokens": (
                _count(usage.get("input_tokens")) + read + write
                if self.cache_reported else None
            ),
            # The latest request's rate modifiers, kept raw so the pricing engine prices or rejects
            # them exactly as it does for the real cost.
            "pricing_variant": str(pricing_variant or "")[:32],
            # An unrecognized override falls back to the session's own target.
            "discount_price_list": (_discount_price_list(discount_price_list)
                                    or _discount_price_list(self.discount_price_list)),
            "discount_channel": (
                discount_channel if _discount_channel(discount_channel) == discount_channel
                else _discount_channel(self.discount_channel)),
            **{key: _modifier(usage.get(key)) for key in RATE_MODIFIERS},
        }

    def facts(self):
        if self.latest is None:
            return None
        # A recorded TTL describes the cache of the model that wrote it, not a later model's.
        same_model = self._ttl_model == self.latest["model"]
        return {
            **self.latest,
            "ttl_s": self.ttl_s if same_model else None,
            "requests": self.requests,
            "cache_reported": self.cache_reported,
            "cache_observed": self.cache_observed,
            "writes_observed": self.writes_observed,
        }


def documented_window(model):
    """Return the provider-documented window row for a model id, if any."""
    model = str(model or "")
    for pattern, row in _MATCHERS:
        if pattern.search(model):
            return row
    return None


def build_cache_window(facts, cold_cost_delta=None):
    """Resolve the window for a session's latest request; None when unknown.

    `cold_cost_delta(facts, window_s)` returns the estimated extra cost of the
    next prompt after expiry, or None when pricing or cache evidence is missing.
    """
    if not isinstance(facts, dict) or not facts.get("anchor_ts"):
        return None
    # A trace that reports cache use but shows none after several requests is evidence of no cache.
    if facts.get("cache_reported") and not facts.get("cache_observed") \
            and int(facts.get("requests") or 0) >= 2:
        return None
    documented = documented_window(facts.get("model"))
    ttl_s = facts.get("ttl_s")
    if ttl_s in (300, 3600):
        basis, window_s, max_window_s = BASIS_RECORDED, int(ttl_s), int(ttl_s)
        source = "anthropic"
    elif documented is not None:
        basis = BASIS_DOCUMENTED
        window_s = int(documented["min_s"])
        max_window_s = documented.get("max_s")
        source = documented["source"]
    else:
        return None
    delta = None
    if cold_cost_delta is not None and facts.get("cache_observed"):
        try:
            delta = cold_cost_delta(facts, window_s)
        except (TypeError, ValueError, ArithmeticError):
            delta = None
    return cache_window_projection({
        "basis": basis,
        "source": source,
        "anchor_ts": facts.get("anchor_ts"),
        "window_s": window_s,
        "max_window_s": max_window_s,
        "prefix_tokens": facts.get("prefix_tokens") if facts.get("cache_observed") else None,
        "cold_cost_delta": delta,
        "cold_kind": "write" if facts.get("writes_observed") else "input",
        "cache_reported": bool(facts.get("cache_reported")),
        "cache_observed": bool(facts.get("cache_observed")),
    })


def cache_window_projection(value):
    """Return the bounded public shape, or None when the value is not usable."""
    if not isinstance(value, dict):
        return None
    basis = value.get("basis")
    if basis not in (BASIS_RECORDED, BASIS_DOCUMENTED):
        return None
    try:
        anchor = float(value.get("anchor_ts"))
        window_s = int(value.get("window_s"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(anchor) or anchor <= 0 or not 0 < window_s <= MAX_WINDOW_S:
        return None
    max_window_s = value.get("max_window_s")
    if max_window_s is not None:
        try:
            max_window_s = int(max_window_s)
        except (TypeError, ValueError):
            max_window_s = None
        if max_window_s is not None and not window_s <= max_window_s <= MAX_WINDOW_S:
            max_window_s = None
    prefix = value.get("prefix_tokens")
    prefix = _count(prefix) if prefix is not None else None
    delta = value.get("cold_cost_delta")
    if isinstance(delta, bool) or not isinstance(delta, (int, float)) \
            or not math.isfinite(delta) or delta < 0:
        delta = None
    source = value.get("source")
    return {
        "basis": basis,
        "source": source if source in _SOURCES else None,
        "anchor_ts": round(anchor, 3),
        "window_s": window_s,
        "max_window_s": max_window_s,
        "prefix_tokens": prefix,
        "cold_cost_delta": round(float(delta), 6) if delta is not None else None,
        "cold_kind": "write" if value.get("cold_kind") == "write" else "input",
        "cache_reported": value.get("cache_reported") is True,
        "cache_observed": value.get("cache_observed") is True,
    }
