"""Bounded built-in model catalog, provider aliases, and dated price data."""

import re


MODEL_PRICE_FIELDS = ("input", "output", "cache_write", "cache_read")
MODEL_PROVIDER_IDS = ("anthropic", "openai", "cursor", "opencode")
LEGACY_PROVIDER_TO_MODEL_PROVIDER = {
    "claude": "anthropic",
    "codex": "openai",
    "cursor": "cursor",
    "opencode": "opencode",
}
MODEL_PROVIDER_TO_SETTINGS_PROVIDER = {
    model_provider: legacy_provider
    for legacy_provider, model_provider in LEGACY_PROVIDER_TO_MODEL_PROVIDER.items()
}
MODEL_PRICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,159}$")
BUILTIN_PRICE_REVIEWED_ON = "2026-09-07"
BUILTIN_PRICE_SOURCES = (
    {
        "provider": "anthropic",
        "label": "Anthropic API pricing",
        "url": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    {
        "provider": "openai",
        "label": "OpenAI model pricing",
        "url": "https://developers.openai.com/api/docs/models/compare",
    },
    {
        "provider": "cursor",
        "label": "Cursor Models & Pricing",
        "url": "https://cursor.com/docs/models-and-pricing",
    },
)


ANTHROPIC_PRICE = {
    "claude-mythos-5": {
        "input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 1.0,
    },
    "claude-opus-5": {
        "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50,
    },
    "claude-opus-4-8": {
        "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50,
    },
    "claude-opus-4-7": {
        "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50,
    },
    "claude-opus-4-6": {
        "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50,
    },
    "claude-opus-4-5": {
        "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50,
    },
    "claude-fable-5": {
        "input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 1.0,
    },
    "claude-fable-5-1": {
        "input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 0.25,
    },
    "claude-mythos-5-1": {
        "input": 10.0, "output": 50.0, "cache_write": 12.50, "cache_read": 0.25,
    },
    # Current published standard pricing is $2 input / $10 output per 1M tokens.
    "claude-sonnet-5": {
        "input": 2.0, "output": 10.0, "cache_write": 2.50, "cache_read": 0.20,
    },
    "claude-sonnet-4-6": {
        "input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-sonnet-4-5": {
        "input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10,
    },
    # Retired from the first-party API; retained for historical and cloud traces.
    "claude-haiku-3-5": {
        "input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08,
    },
}

# Public OpenAI API pricing, per 1M tokens. Codex subscription accounting can
# differ by plan, so the UI labels OpenAI/Codex costs as API-rate estimates.
OPENAI_PRICE = {
    # GPT-6 Astra pricing from the official OpenAI model catalog.
    "gpt-6-astra": {
        "input": 10.0, "output": 50.0, "cache_write": 0.0, "cache_read": 1.0,
    },
    # The unsuffixed alias uses Sol. Its August 2026 promotion is preserved below.
    "gpt-5.6": {
        "input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.40,
    },
    "gpt-5.6-sol": {
        "input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.40,
    },
    "gpt-5.6-terra": {
        "input": 2.0, "output": 12.0, "cache_write": 2.50, "cache_read": 0.20,
    },
    "gpt-5.6-luna": {
        "input": 0.20, "output": 1.20, "cache_write": 0.25, "cache_read": 0.02,
    },
    "gpt-5.5": {
        "input": 5.0, "output": 30.0, "cache_write": 0.0, "cache_read": 0.50,
    },
    "gpt-5.4": {
        "input": 2.50, "output": 15.0, "cache_write": 0.0, "cache_read": 0.25,
    },
    "gpt-5.4-mini": {
        "input": 0.75, "output": 4.50, "cache_write": 0.0, "cache_read": 0.075,
    },
    "gpt-5.3-codex": {
        "input": 1.75, "output": 14.0, "cache_write": 0.0, "cache_read": 0.175,
    },
}

CURSOR_PRICE = {
    "composer-2.5-standard": {
        "input": 0.50, "output": 2.50, "cache_write": 0.0, "cache_read": 0.20,
    },
    "composer-2.5-fast": {
        "input": 3.0, "output": 15.0, "cache_write": 0.0, "cache_read": 0.50,
    },
    "grok-4.6-standard": {
        "input": 2.0, "output": 6.0, "cache_write": 0.0, "cache_read": 0.50,
    },
    "grok-4.6-fast": {
        "input": 4.0, "output": 12.0, "cache_write": 0.0, "cache_read": 1.0,
    },
    "grok-4.5-standard": {
        "input": 2.0, "output": 6.0, "cache_write": 0.0, "cache_read": 0.50,
    },
    "grok-4.5-fast": {
        "input": 4.0, "output": 18.0, "cache_write": 0.0, "cache_read": 1.0,
    },
}
CURSOR_VARIANT_MODEL_IDS = ("composer-2.5", "grok-4.6", "grok-4.5")

GPT_56_PRICE_UPDATE_AT = 1_785_456_000  # 2026-07-31T00:00:00Z
GPT_56_SOL_PRICE_UPDATE_AT = 1_787_270_400  # 2026-08-21T00:00:00Z
GPT_56_LONG_CONTEXT_TOKENS = 272_000
_GPT_56_PRE_UPDATE_PRICE = {
    "input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50,
}
BUILTIN_MODEL_PRICE_HISTORY = {
    "openai": {
        "gpt-5.6": (
            (None, _GPT_56_PRE_UPDATE_PRICE),
            (GPT_56_SOL_PRICE_UPDATE_AT, OPENAI_PRICE["gpt-5.6"]),
        ),
        "gpt-5.6-sol": (
            (None, _GPT_56_PRE_UPDATE_PRICE),
            (GPT_56_SOL_PRICE_UPDATE_AT, OPENAI_PRICE["gpt-5.6-sol"]),
        ),
        "gpt-5.6-terra": (
            (None, _GPT_56_PRE_UPDATE_PRICE),
            (GPT_56_PRICE_UPDATE_AT, OPENAI_PRICE["gpt-5.6-terra"]),
        ),
        "gpt-5.6-luna": (
            (None, _GPT_56_PRE_UPDATE_PRICE),
            (GPT_56_PRICE_UPDATE_AT, OPENAI_PRICE["gpt-5.6-luna"]),
        ),
    },
}

BUILTIN_PRICE_TABLES = {
    "anthropic": ANTHROPIC_PRICE,
    "openai": OPENAI_PRICE,
    "cursor": CURSOR_PRICE,
    "opencode": {},
}
DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.5",
}
ZERO_PRICE = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}
def canonical_model_provider(provider_id):
    """Map a legacy runtime/settings provider to its explicit model provider."""

    provider_id = str(provider_id or "").strip().lower()
    if provider_id in MODEL_PROVIDER_IDS:
        return provider_id
    return LEGACY_PROVIDER_TO_MODEL_PROVIDER.get(provider_id)


def settings_provider_for_model_provider(provider_id):
    """Return the existing persisted settings namespace for a model provider."""

    provider_id = str(provider_id or "").strip().lower()
    return MODEL_PROVIDER_TO_SETTINGS_PROVIDER.get(provider_id)


def normalize_model_id(model_id):
    model_id = str(model_id or "").strip().lower()
    if not MODEL_PRICE_ID_RE.fullmatch(model_id):
        raise ValueError(
            "Model id must be 1–160 characters using letters, numbers, dots, dashes, "
            "underscores, colons, @, +, or /."
        )
    return model_id
