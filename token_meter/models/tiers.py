"""Reviewed model tiers for Goals; independent of price and runtime identity."""

from .pricing import matching_price


TIER_IDS = ("frontier", "mid_range", "efficient")

# Product classifications based on provider positioning, reviewed 2026-10-08.
# Match only these provider-scoped IDs and their bounded aliases/snapshots.
# https://platform.claude.com/docs/en/models/overview
# https://developers.openai.com/api/docs/models
TIER_MODELS = {
    "anthropic": {
        "claude-mythos-5-1": "frontier",
        "claude-mythos-5": "frontier",
        "claude-fable-5-1": "frontier",
        "claude-fable-5": "frontier",
        "claude-opus-5-5": "frontier",
        "claude-opus-5": "frontier",
        "claude-opus-4-8": "frontier",
        "claude-opus-4-7": "frontier",
        "claude-opus-4-6": "frontier",
        "claude-opus-4-5": "frontier",
        "claude-sonnet-5-5": "mid_range",
        "claude-sonnet-5": "mid_range",
        "claude-sonnet-4-6": "mid_range",
        "claude-sonnet-4-5": "mid_range",
        "claude-haiku-4-5": "efficient",
        "claude-haiku-3-5": "efficient",
    },
    "openai": {
        "gpt-6-astra": "frontier",
        "gpt-6.1-sol": "mid_range",
        "gpt-6-sol": "mid_range",
        "gpt-6-luna": "efficient",
        "gpt-5.6": "frontier",
        "gpt-5.6-sol": "frontier",
        "gpt-5.6-terra": "mid_range",
        "gpt-5.6-luna": "efficient",
        "gpt-5.5": "frontier",
        "gpt-5.4": "frontier",
        "gpt-5.4-mini": "efficient",
        "gpt-5.3-codex": "frontier",
    },
}


def model_tier(model):
    """Classify an explicitly recognized underlying model, or return None."""
    found = []
    for provider, rules in TIER_MODELS.items():
        _rule, tier = matching_price(str(model or ""), rules, provider)
        if tier:
            found.append(tier)
    return found[0] if len(found) == 1 else None
