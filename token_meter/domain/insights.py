"""Runtime-neutral insight construction, ordering, and action thresholds."""

from token_meter.domain.usage import format_usd


CONTEXT_SOFT_PCT = 0.65
CONTEXT_WATCH_PCT = 0.70
CONTEXT_INTERVENE_PCT = 0.85
LOW_YIELD_RATIO = 0.005
LOW_YIELD_COST = 0.05
LOW_YIELD_CONTEXT_PCT = 0.25
LOW_YIELD_INPUT_TOKENS = 60000

INSIGHT_CATEGORY_ORDER = {
    "Context": 0,
    "Yield": 1,
    "Spend": 2,
    "Tools": 3,
    "Cache": 4,
    "Reasoning": 5,
    "Flow": 6,
    "Pricing": 7,
}
INSIGHT_KIND_SCORE = {"warn": 0, "good": 1, "neutral": 2}


def insight(key, kind, category, title, text, detail="", action="", priority=50):
    row = {
        "key": key,
        "kind": kind,
        "category": category,
        "title": title,
        "text": text,
        "priority": priority,
    }
    if detail:
        row["detail"] = detail
    if action:
        row["action"] = action
    return row


def insight_sort_key(row):
    return (
        INSIGHT_KIND_SCORE.get(row.get("kind"), 2),
        row.get("priority", 50),
        INSIGHT_CATEGORY_ORDER.get(row.get("category"), 99),
        row.get("key") or "",
    )


def normalize_insights(rows, limit=12):
    normalized = []
    seen = set()
    for row in rows or []:
        key = row.get("key") or row.get("text") or ""
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return sorted(normalized, key=insight_sort_key)[:limit]


def execution_low_yield(execution):
    tokens = execution.get("tokens") or {}
    input_tokens = int(tokens.get("input") or execution.get("context_tokens") or 0)
    output_tokens = int(tokens.get("output") or 0)
    cost = float(execution.get("cost") or 0)
    if input_tokens <= 0:
        return False
    return (output_tokens / input_tokens) < LOW_YIELD_RATIO and cost > LOW_YIELD_COST


def low_yield_should_warn(executions, context_pct=0):
    if not executions:
        return False
    latest = executions[-1]
    if not execution_low_yield(latest):
        return False
    latest_tokens = latest.get("tokens") or {}
    latest_input = int(latest_tokens.get("input") or latest.get("context_tokens") or 0)
    consecutive = 0
    for execution in reversed(executions):
        if not execution_low_yield(execution):
            break
        consecutive += 1
    return (
        (context_pct or 0) >= LOW_YIELD_CONTEXT_PCT
        or latest_input >= LOW_YIELD_INPUT_TOKENS
        or consecutive >= 2
    )


def is_operational_warning(row):
    key = row.get("key") or ""
    return key == "context-high" or (
        key == "low-yield-latest" and row.get("kind") == "warn"
    )


def enrich_insights(insights, executions, tool_data, context_window,
                    context_latest, context_peak, provider=None):
    out = list(insights or [])
    latest_pct = 0.0
    if context_window:
        latest_pct = context_latest / context_window
        peak_pct = context_peak / context_window
        if latest_pct >= CONTEXT_INTERVENE_PCT:
            out.insert(0, insight(
                "context-high", "warn", "Context", "Compact now",
                "Context is {:.0f}% of the model window.".format(latest_pct * 100),
                detail="The next execution is close to the model limit and will replay a large prompt.",
                action="Summarize, compact, or narrow tool output before continuing.",
                priority=0,
            ))
        elif latest_pct >= CONTEXT_WATCH_PCT:
            out.append(insight(
                "context-watch", "warn", "Context", "Prepare to compact",
                "Context is {:.0f}% of the model window.".format(latest_pct * 100),
                detail="The run is entering the range where summary quality and tool selectivity start to matter.",
                action="Prepare a summary before the context reaches 85%.",
                priority=8,
            ))
        elif peak_pct > CONTEXT_SOFT_PCT:
            out.append(insight(
                "context-peak", "neutral", "Context", "Context peak",
                "Context peaked at {:.0f}% of the model window.".format(peak_pct * 100),
                detail="This is historical pressure, not necessarily the latest state.",
                priority=55,
            ))
    loaded = tool_data.get("advertised") or tool_data.get("loaded") or 0
    unique_used = tool_data.get("unique_used") or 0
    if loaded and tool_data.get("loaded_known"):
        ratio = unique_used / loaded
        kind = "neutral" if ratio >= 0.25 else "warn"
        out.append(insight(
            "tools-loaded", kind, "Tools", "Tool surface",
            "Runtime reported {} tools; {} were used in this log.".format(
                loaded, unique_used
            ),
            detail="{} eager and {} deferred; catalog coverage is {}.".format(
                tool_data.get("eager", 0),
                tool_data.get("deferred", 0),
                tool_data.get("catalog_coverage", "reported"),
            ),
            action="Review rarely used tools or keep them deferred." if kind == "warn" else "",
            priority=34 if kind == "warn" else 68,
        ))
    if executions:
        latest = executions[-1]
        if low_yield_should_warn(executions, latest_pct if context_window else 0):
            out.append(insight(
                "low-yield-latest", "warn", "Yield", "Low-yield execution",
                "Latest execution replayed large context for a small output.",
                detail="The run is paying to replay a large prompt without producing much new work.",
                action="Summarize or restart with a tighter request.",
                priority=6,
            ))
        elif execution_low_yield(latest):
            out.append(insight(
                "low-yield-latest", "neutral", "Yield", "Low-yield execution",
                "Latest execution produced little output from its input.",
                detail="This is notable, but not yet actionable under the current thresholds.",
                priority=60,
            ))
    return normalize_insights(out)


def build_cost_insights(totals, costs, total_cost, cache_ratio, biggest,
                        execution_count, analyses, model, cost_approx,
                        cache_saved=0.0):
    """Build cost/cache/reasoning/coordination insights from normalized facts."""

    out = []
    if total_cost <= 0:
        return out
    labels = {
        "input": "uncached input",
        "cache_write": "cache writes",
        "cache_read": "cached input",
        "output": "output",
    }
    top = max(costs, key=costs.get)
    top_share = costs[top] / total_cost if total_cost else 0
    top_kind = "warn" if top_share >= 0.75 and costs[top] >= 0.25 else "neutral"
    out.append(insight(
        "top:{}".format(top), top_kind, "Spend", "Spend driver",
        "{} is {:.0f}% of spend ({}).".format(
            labels[top], top_share * 100, format_usd(costs[top])
        ),
        detail="This points to the part of the run that is actually moving cost.",
        action=(
            "Reduce this bucket first if you need to lower spend."
            if top_kind == "warn" else ""
        ),
        priority=22 if top_kind == "warn" else 46,
    ))
    fresh = int(totals.get("input", 0) or 0)
    read = int(totals.get("cache_read", 0) or 0)
    write = int(totals.get("cache_write", 0) or 0)
    cached = read + write
    input_total = fresh + cached
    cached_share = cached / input_total if input_total else 0
    if cache_saved is not None and cache_saved > 0.01:
        out.append(insight(
            "cache-saved", "good", "Cache", "Cache leverage",
            "Caching saved ~{}.".format(format_usd(cache_saved)),
            detail=(
                "Cache read hit ratio is {:.0f}% across {:,} cached input tokens."
                .format(cache_ratio * 100, cached)
            ),
            priority=28,
        ))
    elif input_total >= 50000 and cached_share < 0.15:
        out.append(insight(
            "cache-low", "warn", "Cache", "Low cache leverage",
            "Only {:.0f}% of input was cached.".format(cached_share * 100),
            detail="{:,} tokens were billed as fresh input in this log.".format(fresh),
            action="Reuse a live thread or trim large repeated context before the next request.",
            priority=30,
        ))
    reasoning = analyses["reasoning"]
    reasoning_share = reasoning["share"]
    if reasoning_share > 0.6 and reasoning["think_turns"]:
        out.append(insight(
            "reasoning-high", "warn", "Reasoning", "Reasoning load",
            "{:.0f}% of output came from reasoning turns.".format(
                reasoning_share * 100
            ),
            detail="{:,} reasoning tokens across {} executions.".format(
                reasoning["tokens"], reasoning["think_turns"]
            ),
            action="Split exploratory work from implementation, or ask for a narrower next step.",
            priority=26,
        ))
    elif reasoning_share > 0.25 and reasoning["think_turns"]:
        out.append(insight(
            "reasoning-mix", "neutral", "Reasoning", "Reasoning mix",
            "{:.0f}% of output was reasoning.".format(reasoning_share * 100),
            detail="This is expected for complex code work, but it is worth watching on long runs.",
            priority=72,
        ))
    coordination = analyses["coordination"]
    if coordination["share"] > 0.30:
        out.append(insight(
            "coordination-high", "warn", "Flow", "Coordination tax",
            "Coordination tax is {:.0f}% of spend.".format(
                coordination["share"] * 100
            ),
            detail="{} coordination executions cost {}.".format(
                coordination["turns"], format_usd(coordination["cost"])
            ),
            action="Use fewer subagents or collapse exploration into one pass.",
            priority=32,
        ))
    elif coordination["share"] > 0.10 and coordination["turns"]:
        out.append(insight(
            "coordination-mix", "neutral", "Flow", "Coordination mix",
            "Coordination used {:.0f}% of spend.".format(
                coordination["share"] * 100
            ),
            detail="{} coordination executions were detected.".format(
                coordination["turns"]
            ),
            priority=74,
        ))
    if cost_approx:
        out.append(insight(
            "cost-approx", "neutral", "Pricing", "Pricing basis",
            "Cost uses {} public API rates.".format(model),
            detail="Subscription billing, discounts, and non-public pricing can differ.",
            priority=90,
        ))
    if biggest and biggest["cost"] > 0:
        biggest_share = biggest["cost"] / total_cost if total_cost else 0
        kind = (
            "warn"
            if biggest["cost"] >= 0.50
            or (execution_count > 1 and biggest_share >= 0.55)
            else "neutral"
        )
        out.append(insight(
            "biggest", kind, "Spend", "Largest execution",
            "Priciest execution was {} (#{} of {}).".format(
                format_usd(biggest["cost"]), biggest["idx"], execution_count
            ),
            detail="It accounts for {:.0f}% of this log's spend.".format(
                biggest_share * 100
            ),
            action=(
                "Inspect that execution before continuing if it was unexpected."
                if kind == "warn" else ""
            ),
            priority=24 if kind == "warn" else 70,
        ))
    return normalize_insights(out)
