"""Deterministic coaching from goal measurements.

Rules read only the measured goal, its drivers, and its contributors. Every tip
states its evidence, one lever the user controls, and, where the user's own
history supports it, an upper bound savings estimate with its basis.
"""

from token_meter.models.tiers import TIER_MODELS
from token_meter.services.goals import HIGH_EFFORTS

FRONTIER_SHARE_TRIGGER = 30.0
HIGH_EFFORT_TRIGGER = 30.0
CONCENTRATION_TRIGGER = 40.0
CACHE_HIT_TRIGGER = 50.0
COVERAGE_TRIGGER = 50.0
MIN_EXECUTIONS = 20
MOVED_SHARE = 0.5
RUNTIME_PROVIDERS = {"claude": "anthropic", "codex": "openai"}


def _money(value):
    return f"${value:,.2f}"


def _tip(tip_id, title, evidence, action, estimate=None, basis=None):
    return {"id": tip_id, "title": title, "evidence": evidence, "action": action,
            "estimate": round(estimate, 2) if estimate and estimate > 0 else None,
            "estimate_basis": basis if estimate and estimate > 0 else None}


def _model_mix(goal, drivers, _contributors):
    models = drivers.get("by_model") or []
    frontier = [model for model in models if model["tier"] == "frontier"]
    share = sum(model["share"] for model in frontier)
    if not frontier or share < FRONTIER_SHARE_TRIGGER:
        return None
    top = frontier[0]
    alternatives = [model for model in models
                    if model["runtime"] == top["runtime"] and model["tier"] in ("mid_range", "efficient")
                    and model["executions"] >= MIN_EXECUTIONS and model["cost_per_million_output"]
                    and top["cost_per_million_output"]
                    and model["cost_per_million_output"] < top["cost_per_million_output"]]
    evidence = (f"{share:.0f}% of this spend is on frontier models; {top['model']} alone is "
                f"{_money(top['cost'])} ({top['share']:.0f}%).")
    if alternatives:
        alternative = max(alternatives, key=lambda model: model["executions"])
        moved = top["cost"] * MOVED_SHARE
        estimate = moved * (1 - alternative["cost_per_million_output"] / top["cost_per_million_output"])
        return _tip("model_mix", "Frontier models carry most of the cost", evidence,
                    f"Use {alternative['model']} for routine edits, reviews, and lookups; keep {top['model']} "
                    "for hard or ambiguous work.", estimate,
                    f"Up to this much so far this period if half of the {top['model']} spend had run on "
                    f"{alternative['model']}, using your own cost per 1M output tokens on each "
                    f"({_money(top['cost_per_million_output'])} and "
                    f"{_money(alternative['cost_per_million_output'])}). Assumes the cheaper model could do that work.")
    names = [name for name, tier in TIER_MODELS.get(RUNTIME_PROVIDERS.get(top["runtime"]), {}).items()
             if tier == "mid_range"][:2]
    return _tip("model_mix", "Frontier models carry most of the cost", evidence,
                f"Try {' or '.join(names) if names else 'a mid range model'} for routine work. Token Meter "
                "can estimate savings once you have some history on a cheaper model.")


def _effort(goal, drivers, _contributors):
    rows = drivers.get("effort_by_model") or []
    total = sum(row["cost"] for row in rows)
    high = [row for row in rows if row["effort"] in HIGH_EFFORTS]
    share = sum(row["cost"] for row in high) * 100 / total if total else 0
    if not high or share < HIGH_EFFORT_TRIGGER:
        return None
    evidence = f"{share:.0f}% of spend with a reported effort ran at high reasoning effort or above."
    action = "Use medium effort for routine edits, reviews, and lookups; save high effort for hard problems."
    best = None
    for row in sorted(high, key=lambda item: -item["cost"]):
        medium = [item for item in rows if item["key"] == row["key"] and item["effort"] in ("medium", "low")
                  and item["executions"] >= MIN_EXECUTIONS]
        executions = sum(item["executions"] for item in medium)
        if row["executions"] >= MIN_EXECUTIONS and executions:
            per_high = row["cost"] / row["executions"]
            per_medium = sum(item["cost"] for item in medium) / executions
            if per_medium < per_high:
                best = (row, per_high, per_medium)
                break
    if not best:
        return _tip("effort", "Most spend runs at high reasoning effort", evidence, action)
    row, per_high, per_medium = best
    estimate = row["cost"] * MOVED_SHARE * (1 - per_medium / per_high)
    return _tip("effort", "Most spend runs at high reasoning effort", evidence, action, estimate,
                f"Up to this much so far this period if half of the {row['model']} {row['effort']} effort spend had "
                f"run at medium, using your own average cost per execution at each ({_money(per_high)} and "
                f"{_money(per_medium)}). Assumes medium effort was enough for that work.")


def _concentration(goal, _drivers, contributors):
    items = contributors.get("items") or []
    top = items[:3]
    share = sum(item["share"] for item in top)
    if (contributors.get("sessions") or 0) <= len(top) or share < CONCENTRATION_TRIGGER:
        return None
    basis = contributors.get("basis", "cost")
    return _tip("concentration", "A few sessions drive most of it",
                f"Your top {len(top)} sessions are {share:.0f}% of this period's {basis}.",
                "Split long tasks into focused sessions and start fresh when the context grows; in Claude Code, "
                "/compact trims the history. The sessions are listed below.")


def _cache(goal, drivers, _contributors):
    hit = drivers.get("cache_hit")
    if hit is None or hit >= CACHE_HIT_TRIGGER:
        return None
    return _tip("cache", "Little context is reused from cache",
                f"Only {hit:.0f}% of processed input came from cache.",
                "Keep related work in one continuous session and avoid switching models mid session, so the "
                "provider can reuse cached context at a lower price.")


def _coverage(goal, _drivers, _contributors):
    share = (goal.get("measurement") or {}).get("counted_share")
    if share is None or share >= COVERAGE_TRIGGER:
        return None
    return _tip("coverage", "Most agent spend is outside your repositories",
                f"Only {share:.0f}% of agent spend ran in repositories with pushes, so the rest cannot count.",
                "Start agent sessions from inside the repository folder you will push from.")


def _repositories(goal, drivers, _contributors):
    repos = [repo for repo in drivers.get("by_repository") or [] if repo["value"] is not None]
    if len(repos) < 2:
        return None
    high, low = max(repos, key=lambda repo: repo["value"]), min(repos, key=lambda repo: repo["value"])
    if high["value"] < low["value"] * 2:
        return None
    return _tip("repositories", "Delivery cost varies by repository",
                f"{high['label']} costs {_money(high['value'])} per 1K lines; {low['label']} costs "
                f"{_money(low['value'])}.",
                f"Compare how you work in {high['label']}: task size, model, and effort.")


RULES = {
    "spend": (_model_mix, _effort, _concentration),
    "output_per_dollar": (_model_mix, _cache),
    "cost_per_1k_lines": (_coverage, _repositories),
    "context_load": (_concentration, _cache),
    "reasoning_ratio": (_effort,),
    "frontier_share": (_model_mix,),
}


def coach(detail):
    goal = detail["goal"]
    tips = [tip for rule in RULES.get(goal["metric"], ()) if (tip := rule(goal, detail.get("drivers") or {},
                                                                           detail.get("contributors") or {}))]
    if not tips:
        tips.append(_tip("steady", "No single lever stands out",
                         "Nothing in this period crosses a coaching threshold.",
                         "Keep your current habits and check back later in the period."))
    return tips
