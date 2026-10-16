"""Pure token, cache, and cost calculations over normalized evidence."""

import math
from dataclasses import dataclass

from token_meter.contracts import EvidenceBasis, EvidenceValue, PriceQuote, UsageEvidence


_BASIS_RANK = {
    EvidenceBasis.MEASURED: 0,
    EvidenceBasis.INFERRED: 1,
    EvidenceBasis.ESTIMATED: 2,
    EvidenceBasis.UNAVAILABLE: 3,
}
_REPORTED_COST_WEIGHTS = {
    "input": 1.0,
    "cache_write": 1.25,
    "cache_read": 0.10,
    "output": 5.0,
    "reasoning": 5.0,
}
MAX_SAFE_TOKEN_COUNT = (1 << 53) - 1


def format_usd(value):
    """Display a dollar cost with one decimal; a nonzero cost below $0.05 is `<$0.1`."""
    value = float(value or 0)
    if 0 < abs(value) < 0.05:
        return "<$0.1" if value > 0 else "-<$0.1"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.1f}"


def normalize_reported_token_count(value):
    """Return an exact, JSON-safe token count and whether it was reported."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0, False
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return 0, False
    if value < 0 or value > MAX_SAFE_TOKEN_COUNT:
        return 0, False
    return int(value), True


def _combined_basis(*values):
    return max(values, key=lambda value: _BASIS_RANK[value])


def _sum_evidence(*values):
    if any(value.basis is EvidenceBasis.UNAVAILABLE for value in values):
        return EvidenceValue.unavailable()
    return EvidenceValue(
        sum(value.value for value in values),
        _combined_basis(*(value.basis for value in values)),
    )


def _priced(tokens, rate, price_basis, multiplier):
    if tokens.basis is EvidenceBasis.UNAVAILABLE:
        return EvidenceValue.unavailable()
    value = float(tokens.value) * float(rate) * float(multiplier) / 1_000_000
    return EvidenceValue(value, _combined_basis(tokens.basis, price_basis))


@dataclass(frozen=True)
class CostBreakdown:
    input_usd: EvidenceValue
    cache_write_usd: EvidenceValue
    cache_read_usd: EvidenceValue
    output_usd: EvidenceValue

    @classmethod
    def unavailable(cls):
        missing = EvidenceValue.unavailable
        return cls(missing(), missing(), missing(), missing())

    @property
    def available(self):
        return all(value.basis is not EvidenceBasis.UNAVAILABLE for value in (
            self.input_usd,
            self.cache_write_usd,
            self.cache_read_usd,
            self.output_usd,
        ))

    @property
    def total_usd(self):
        return _sum_evidence(
            self.input_usd,
            self.cache_write_usd,
            self.cache_read_usd,
            self.output_usd,
        )

    def to_legacy_dict(self):
        return {
            "input": float(self.input_usd.value or 0.0),
            "cache_write": float(self.cache_write_usd.value or 0.0),
            "cache_read": float(self.cache_read_usd.value or 0.0),
            "output": float(self.output_usd.value or 0.0),
        }


def usage_token_total(usage):
    """Return all billed token components, preserving unavailable evidence."""

    if not isinstance(usage, UsageEvidence):
        raise TypeError("usage must be UsageEvidence")
    return _sum_evidence(
        usage.input_tokens,
        usage.cache_write_tokens,
        usage.cache_read_tokens,
        usage.output_tokens,
    )


def usage_token_total_counts(input_tokens=0, output_tokens=0,
                             cache_read_tokens=0, cache_write_tokens=0):
    """Fast compatibility projection for already-normalized token counts."""

    return input_tokens + cache_write_tokens + cache_read_tokens + output_tokens


def usage_io_tokens(usage):
    """Return total input including cache plus output as separate evidence."""

    if not isinstance(usage, UsageEvidence):
        raise TypeError("usage must be UsageEvidence")
    return (
        _sum_evidence(
            usage.input_tokens,
            usage.cache_write_tokens,
            usage.cache_read_tokens,
        ),
        usage.output_tokens,
    )


def usage_io_token_counts(input_tokens=0, output_tokens=0,
                          cache_read_tokens=0, cache_write_tokens=0):
    """Fast compatibility projection for normalized input/output counts."""

    return (
        int(input_tokens or 0)
        + int(cache_write_tokens or 0)
        + int(cache_read_tokens or 0),
        int(output_tokens or 0),
    )


def cost_breakdown(usage, quote, input_multiplier=1.0, output_multiplier=1.0):
    """Price normalized usage with one provider-scoped immutable quote."""

    if not isinstance(usage, UsageEvidence):
        raise TypeError("usage must be UsageEvidence")
    if not isinstance(quote, PriceQuote):
        raise TypeError("quote must be PriceQuote")
    if not quote.available:
        return CostBreakdown.unavailable()
    multipliers = (float(input_multiplier), float(output_multiplier))
    if any(not math.isfinite(value) or value < 0 for value in multipliers):
        raise ValueError("cost multipliers must be finite and non-negative")
    return CostBreakdown(
        input_usd=_priced(
            usage.input_tokens,
            quote.input_per_million,
            quote.basis,
            multipliers[0],
        ),
        cache_write_usd=_priced(
            usage.cache_write_tokens,
            quote.cache_write_per_million,
            quote.basis,
            multipliers[0],
        ),
        cache_read_usd=_priced(
            usage.cache_read_tokens,
            quote.cache_read_per_million,
            quote.basis,
            multipliers[0],
        ),
        output_usd=_priced(
            usage.output_tokens,
            quote.output_per_million,
            quote.basis,
            multipliers[1],
        ),
    )


def cost_breakdown_values(input_tokens, output_tokens, cache_read_tokens,
                          cache_write_tokens, quote, input_multiplier=1.0,
                          output_multiplier=1.0):
    """Price available normalized counts without compatibility allocations."""

    if not isinstance(quote, PriceQuote):
        raise TypeError("quote must be PriceQuote")
    if not quote.available:
        return {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    return {
        "input": input_tokens * quote.input_per_million * input_multiplier / 1_000_000,
        "cache_write": (
            cache_write_tokens
            * quote.cache_write_per_million
            * input_multiplier
            / 1_000_000
        ),
        "cache_read": (
            cache_read_tokens
            * quote.cache_read_per_million
            * input_multiplier
            / 1_000_000
        ),
        "output": output_tokens * quote.output_per_million * output_multiplier / 1_000_000,
    }


def cache_savings_for_rate(cache_read_tokens, input_per_million,
                           cache_read_per_million, input_multiplier=1.0):
    """Return estimated savings against pricing the same tokens as fresh input."""

    return (
        int(cache_read_tokens or 0)
        * max(0.0, float(input_per_million) - float(cache_read_per_million))
        * float(input_multiplier)
        / 1_000_000
    )


def cache_metrics(fresh, read, write, read_cost, write_cost, saved,
                  latest_input=0, latest_cache=0, latest_read=0, latest_write=0):
    """Build cache ratios from normalized aggregate and latest-turn facts."""

    fresh = int(fresh or 0)
    read = int(read or 0)
    write = int(write or 0)
    cached = read + write
    input_total = fresh + cached
    latest_input = int(latest_input or 0)
    latest_cache = int(latest_cache or 0)
    return {
        "fresh": fresh,
        "read": read,
        "write": write,
        "total": cached,
        "input_total": input_total,
        "hit_ratio": (read / cached) if cached else 0.0,
        "input_share": (cached / input_total) if input_total else 0.0,
        "saved": float(saved or 0.0),
        "cost": float(read_cost or 0.0) + float(write_cost or 0.0),
        "read_cost": float(read_cost or 0.0),
        "write_cost": float(write_cost or 0.0),
        "latest": {
            "tokens": latest_cache,
            "read": int(latest_read or 0),
            "write": int(latest_write or 0),
            "input": latest_input,
            "share": (latest_cache / latest_input) if latest_input else 0.0,
        },
    }


def metric_available(row, metric):
    """Treat absent availability as available for legacy normalized rows."""

    availability = (row or {}).get("availability")
    return not isinstance(availability, dict) or availability.get(metric) is not False


def make_usage_provenance(session_ids, estimated_ids=(), available_ids=None,
                          estimated_cost=0.0, estimated_tokens=0):
    """Describe evidence quality separately from metric coverage."""

    session_ids = set(session_ids or ())
    estimated_ids = set(estimated_ids or ()) & session_ids
    available_ids = (
        session_ids
        if available_ids is None
        else set(available_ids or ()) & session_ids
    )
    estimated_ids &= available_ids
    reported_ids = available_ids - estimated_ids
    unavailable_ids = session_ids - available_ids
    if estimated_ids and reported_ids:
        basis = "mixed"
    elif estimated_ids:
        basis = "local_estimate"
    elif reported_ids:
        basis = "reported"
    else:
        basis = "unavailable"
    return {
        "usage_basis": basis,
        "reported_sessions": len(reported_ids),
        "estimated_sessions": len(estimated_ids),
        "unavailable_sessions": len(unavailable_ids),
        "estimated_cost": float(estimated_cost or 0),
        "estimated_tokens": int(estimated_tokens or 0),
    }


def usage_provenance(rows):
    """Roll up reported and estimated normalized rows without changing coverage."""

    rows = list(rows or [])
    session_ids = set()
    estimated_ids = set()
    available_ids = set()
    estimated_cost = 0.0
    estimated_tokens = 0
    for index, row in enumerate(rows):
        row = row or {}
        session_id = row.get("id") or row.get("path") or "row-{}".format(index)
        session_ids.add(session_id)
        available = metric_available(row, "cost") or metric_available(row, "tokens")
        if available:
            available_ids.add(session_id)
        if row.get("token_estimate"):
            estimated_ids.add(session_id)
            if metric_available(row, "cost"):
                estimated_cost += float(row.get("cost") or 0)
            if metric_available(row, "tokens"):
                estimated_tokens += int(row.get("tokens") or 0)
    return make_usage_provenance(
        session_ids,
        estimated_ids,
        available_ids,
        estimated_cost,
        estimated_tokens,
    )


def distribute_reported_cost(reported_cost, usage, reasoning_tokens=None):
    """Split an authoritative total with token-weighted display proxies."""

    if not isinstance(usage, UsageEvidence):
        raise TypeError("usage must be UsageEvidence")
    values = (
        usage.input_tokens,
        usage.cache_write_tokens,
        usage.cache_read_tokens,
        usage.output_tokens,
    )
    reasoning_tokens = reasoning_tokens or EvidenceValue(0, EvidenceBasis.MEASURED)
    if any(value.basis is EvidenceBasis.UNAVAILABLE for value in values + (reasoning_tokens,)):
        raise ValueError("reported cost distribution requires available token evidence")
    return distribute_reported_cost_counts(
        reported_cost,
        input_tokens=usage.input_tokens.value,
        output_tokens=usage.output_tokens.value,
        cache_read_tokens=usage.cache_read_tokens.value,
        cache_write_tokens=usage.cache_write_tokens.value,
        reasoning_tokens=reasoning_tokens.value,
    )


def distribute_reported_cost_counts(reported_cost, input_tokens=0, output_tokens=0,
                                    cache_read_tokens=0, cache_write_tokens=0,
                                    reasoning_tokens=0):
    """Fast authoritative-cost allocation for normalized token components."""

    msg_cost = float(reported_cost or 0.0)
    buckets = {
        "input": input_tokens,
        "cache_write": cache_write_tokens,
        "cache_read": cache_read_tokens,
        "output": output_tokens,
        "reasoning": reasoning_tokens,
    }
    weights = {
        key: float(buckets.get(key) or 0) * _REPORTED_COST_WEIGHTS[key]
        for key in buckets
    }
    total_weight = sum(weights.values())
    if msg_cost <= 0 or total_weight <= 0:
        return {
            "input": 0.0,
            "cache_write": 0.0,
            "cache_read": 0.0,
            "output": msg_cost,
            "reasoning": 0.0,
        }
    per_weight = msg_cost / total_weight
    breakdown = {key: weight * per_weight for key, weight in weights.items()}
    result = {
        "input": round(breakdown["input"], 6),
        "cache_write": round(breakdown["cache_write"], 6),
        "cache_read": round(breakdown["cache_read"], 6),
        "reasoning": round(breakdown["reasoning"], 6),
    }
    result["output"] = msg_cost - sum(result.values())
    return result
