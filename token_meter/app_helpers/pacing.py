"""Pace and timing helpers."""

import hashlib
import math


# Features are precomputed once per sample with exactly the conversions the
# per-pair arithmetic used, so every distance is bit-identical to computing it
# from the sample dictionaries directly.
_PACE_TOOLS, _PACE_OUTPUT, _PACE_PEAK, _PACE_INPUT, _PACE_MODEL = range(5)


_PACE_LOG_PEAK, _PACE_LOG_INPUT, _PACE_LOG_MODEL = range(9, 12)


# Prefilter slack: |log2(a) - log2(b)| and |log(a / b, 2)| differ only by
# rounding, so a pair this far outside the output gate can never pass it.
_PACE_PREFILTER_SLACK = 1e-9


def _pace_features(sample):
    tools = int(sample.get("tool_calls") or 0)
    output = max(1.0, float(sample.get("output_tokens") or 0))
    peak = max(1.0, float(sample.get("peak_input_tokens") or sample.get("input_tokens") or 0))
    input_value = max(1.0, float(sample.get("input_tokens") or 0))
    model = max(1.0, float(sample.get("model_calls") or 1))
    input_tokens = max(1, int(sample.get("input_tokens") or 0))
    return (
        tools,
        output,
        peak,
        input_value,
        model,
        min(1.0, float(sample.get("cache_read_tokens") or 0) / input_tokens),
        float(sample.get("ts") or 0),
        max(1.0, float(tools or 0)),
        math.log2(output),
        math.log2(peak),
        math.log2(input_value),
        math.log2(model),
    )


def _pace_samples_signature(samples, fields):
    """Return a stable digest of the fields that affect a pace comparison."""
    digest = hashlib.sha256()
    for sample in samples or ():
        digest.update(b"\0sample\0")
        digest.update(
            repr(tuple(sample.get(field) for field in fields)).encode(
                "utf-8", errors="replace",
            )
        )
    return digest.hexdigest()
