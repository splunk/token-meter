---
name: token-meter-model-suggestions
description: Suggest which model and reasoning effort to use for each kind of work, from the Token Meter Work page's labels, pushback, and cost evidence. Use when the user asks which model to use for what, whether they overpay for routine work, whether a cheaper model or lower effort would do, or what the Work page recommends.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Token Meter model suggestions

Use the `tokenmeter` MCP's `work_stats` tool to suggest which model and reasoning effort
fits each kind of work, based on the user's own labeled history. If the `tokenmeter`
tools are unavailable, say so and stop. If `work_stats` is not among them, say Token
Meter needs an update for this skill, and stop. Do not suggest models from memory or
general benchmarks.

## Gather evidence

- Call the tools one at a time; if a call times out, wait a minute and retry once.
- Start with `work_stats` view `suggestions`, period `30d` (use `90d` when the user asks
  for a longer view or 30 days has few labeled requests). This returns the Work page's own
  suggestions and per-model effort changes.
- If it returns `available: false`, report the `reason` and stop:
  - `work_insights_off`: Work insights are off. They label work locally and are turned on
    in the dashboard's Settings.
  - `classifier_not_run`: Work insights are on but have not labeled anything yet; try
    again after some sessions are labeled.
  - `label_storage_unavailable` or `unsupported`: say so plainly.
- Then, for the same period:
  - view `models`: the per-model scorecard (tasks, spend, resolved rate, pushback), the
    kind-of-work × model fit, and pushback by model;
  - view `right_sizing`: complexity × price tier and complexity × reasoning effort, with
    `possible_overspend`, `possible_false_economy`, and `possible_overthinking` flags;
  - view `summary`: coverage, spend by kind of work, and overall pushback, for context.
- Pass `runtime` only when the user asks about one app (for example `Claude Code` or
  `Codex`). Compare models only within the same runtime.

## Build the suggestions

Rank up to five suggestions by `estimated_saving_usd`, then by spend. Use these sources:

- **`switch_model`**: for one kind of work and complexity, a cheaper model resolved
  about as often for less per resolved task. Quote both cost-per-resolved figures, both
  resolved rates, and both pushback rates. When `untested_on_harder_work` is true, say
  the cheaper model was never tried on harder work of this kind, so the switch should
  stay limited to this complexity.
- **`family_upgrade`**: a newer, cheaper version in the same model family did as well.
  Quote both output prices (USD per million output tokens) and resolved rates.
- **`premium_routine`**: routine work ran on premium-tier models. Name `from_models` and
  the suggested `to_models`, which stay inside the same app.
- **`effort_routine`** and `effort_changes` with kind `lower`: high reasoning effort on
  routine work, or a lower effort level that drew about the same pushback. Effort means
  different things on different models; compare levels only within one model.
- **`light_complex`** and `effort_changes` with kind `raise`: cheap models or low effort
  on complex work drew more pushback. Suggest moving up, and say the saving is quality,
  not money.
- **`long_threads`**: long sessions cost more per request than short ones. This is a
  session-length suggestion, not a model change; include it only if it ranks.
- Use the `models` view's `fit` cells to name the lowest-pushback model per kind of work
  when the user asks "which model for what". Treat a cell whose `pushback.few_samples`
  is true, or with fewer than 3 `sessions`, as not enough evidence.

For each suggestion give:

1. **Suggestion**: the change, scoped to the kind of work, complexity, and app it came
   from.
2. **Evidence**: the numbers behind it, the view they came from, and the sample size.
3. **Risk**: what could get worse (pushback, resolved rate), and how sure the evidence
   is. Below about 20 labeled follow-ups, call it a weak signal.
4. **How to check it helped**: the `work_stats` query to rerun in two weeks and the
   pushback or cost-per-task threshold that would count as success.

## Report

- Open with one line on coverage: labeled requests out of all requests, and the period.
- Then the ranked suggestions, then a short **Which model for what** table (kind of work,
  suggested model, cost per task, pushback, tasks) built only from cells with enough
  samples.
- End with **Missing evidence**: unlabeled or pending sessions, kinds of work with too few
  samples, models without prices (no tier), and that labels and pushback come from a
  local classifier and are estimates.

## Rules

- Label every dollar figure and saving as an estimate. Savings from different suggestions
  can overlap; never add them into one total.
- Never say a model is better in general. Every claim is about this user's labeled work,
  in this period.
- Do not change any setting. The user switches models in their own client.
- Never include prompts, session titles, project names, file paths, or session ids. Work
  area names are the user's own Settings labels; use them only to describe where spend
  went.
