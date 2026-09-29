"""Reviewed model tiers for Goals; independent of price and runtime identity."""

import hashlib

from .pricing import matching_price


TIER_CATALOG_VERSION = 1
TIER_IDS = ("frontier", "mid_range", "efficient")

# Product classifications based on provider positioning, reviewed 2026-09-29.
# Match only these provider-scoped IDs and their bounded aliases/snapshots.
# https://platform.claude.com/docs/en/models/overview
# https://developers.openai.com/api/docs/models
TIER_MODELS_V1 = {
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
        "claude-sonnet-5": "mid_range",
        "claude-sonnet-4-6": "mid_range",
        "claude-sonnet-4-5": "mid_range",
        "claude-haiku-4-5": "efficient",
        "claude-haiku-3-5": "efficient",
    },
    "openai": {
        "gpt-6-astra": "frontier",
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


def model_tier(model, version=TIER_CATALOG_VERSION):
    """Classify an explicitly recognized underlying model, or return None."""
    if version != TIER_CATALOG_VERSION:
        return None
    found = []
    for provider, rules in TIER_MODELS_V1.items():
        _rule, tier = matching_price(str(model or ""), rules, provider)
        if tier:
            found.append(tier)
    return found[0] if len(found) == 1 else None


def model_tier_key(agent, model):
    """Opaque key for a goal-local classification; persist no model string."""
    return hashlib.sha256(f"{agent}:{model}".encode()).hexdigest()


def tier_override_allowed(model):
    """An opaque router or missing identity cannot be classified by name."""
    name = str(model or "").strip().lower()
    return bool(name and not name.startswith(("unknown", "auto", "cursor-auto", "kiro-auto")) and name not in {
        "auto", "automatic", "auto-select", "cursor-auto", "kiro-auto", "default", "router",
    })


def public_model_label(model):
    """Show a bounded model label without an account-bearing Bedrock ARN."""
    label = str(model or "")
    if label.lower().startswith("arn:aws"):
        label = label.split("/", 1)[1] if "/" in label else "Bedrock model (ARN)"
    return "".join(char for char in label if ord(char) >= 32)[:120]
