"""Reviewed model tiers for Goals; independent of price and runtime identity."""

from .pricing import matching_price


TIER_IDS = ("frontier", "mid_range", "efficient")

# Product classifications based on provider positioning, reviewed 2026-10-08.
# Match only these provider-scoped IDs and their bounded aliases/snapshots.
# https://platform.claude.com/docs/en/models/overview
# https://developers.openai.com/api/docs/models
# Gemini rows follow Google's model descriptions, reviewed 2026-11-24:
# https://ai.google.dev/gemini-api/docs/models (redirects to AI Studio docs)
TIER_MODELS = {
    "anthropic": {
        "claude-mythos-5-2": "frontier",
        "claude-fable-5-2": "frontier",
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
        "gpt-6.2-astra": "frontier",
        "gpt-6.2-sol": "mid_range",
        "gpt-6-astra": "frontier",
        "gpt-6.1-sol": "mid_range",
        "gpt-6.1-terra": "mid_range",
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
    "google": {
        "gemini-4-carbon": "frontier",
        "gemini-4-argon": "frontier",
        "gemini-4-neon": "frontier",
        "gemini-3.1-pro": "frontier",
        "gemini-3.1-pro-preview": "frontier",
        "gemini-2.5-pro": "frontier",
        "gemini-3.8-flash": "mid_range",
        "gemini-3.6-flash": "mid_range",
        "gemini-3-flash": "mid_range",
        "gemini-3-flash-preview": "mid_range",
        "gemini-2.5-flash": "mid_range",
        "gemini-3.5-flash-lite": "efficient",
        "gemini-3.1-flash-lite": "efficient",
        "gemini-2.5-flash-lite": "efficient",
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
