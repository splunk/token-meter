---
name: token-meter-budget-pacing
description: Check month-to-date agent spend against a monthly budget with the Token Meter MCP and project where the month will land. Use when the user asks whether they are on track, how much budget is left, how fast they are spending, or wants to set a session cap.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Token Meter budget pacing

Use the `tokenmeter` MCP tools to show how this month's estimated spend is pacing and
what is left per day. If the `tokenmeter` tools are unavailable, say so and stop; do not
estimate spend from memory.

## Gather evidence

- Call the tools one at a time; if a call times out, wait a minute and retry once.
- Month to date: `stats` with `start` set to the first day of this month (local
  midnight, ISO-8601), metrics `cost_usd` and `session_count`:
  - with no grouping, for the total;
  - grouped by `day` with `limit` 31, for the daily run rate. Day buckets are UTC, so
    the first and last bucket can shift by the user's UTC offset; say so when it
    matters;
  - grouped by `runtime` and by `model`, for what is driving spend.
- Last month, for comparison: the same total query with `start` and `end` covering the
  previous calendar month.
- Current run: `budget` with no arguments reads the cap, spend, remaining amount, and
  threshold state of the caller's matched run. If it says the run was not matched, report
  that and continue without it.
- The machine-wide monthly budget is not available through the MCP. Ask the user for it,
  or point them to the Spend page's Budgets section in the dashboard. Without it, report
  pacing and projection only.
- Read `coverage`. Unpriced executions mean the total is partial; never count them as zero.

## Compute

- Days elapsed (include today) and days left in the month.
- Daily average so far, and the average of the last 7 days.
- Projected month total at each rate: month to date plus rate times days left.
- With a monthly budget: remaining amount, allowed spend per remaining day, and the
  projected over or under amount.
- Biggest drivers: the runtime and model with the largest share, and the most expensive
  day.

## Report

Keep it to a short, dense block:

1. **Status**: on track, at risk, or over, with the one number that decides it.
2. **Numbers**: month to date, daily average, last-7-day average, both projections,
   budget left per day (when known), and last month's total.
3. **Drivers**: top runtime and model by share, and the most expensive day.
4. **One move**: a single concrete change that brings the projection under budget, with
   the daily amount it needs to save.
5. **Missing evidence**: unpriced executions, a missing monthly budget, or an unmatched
   current run.

## Changing caps

- Only set a cap when the user directly asks for it. Never suggest a change and then
  make it in the same turn.
- Before any change, restate the scope (this session, or the default for every session
  without its own cap), the current cap, and the new amount, then wait for the user's
  yes. Skip the wait only when the user's request already named both the scope and the
  amount. `confirm: true` records that yes; never set it on your own.
- `set_session_budget` changes the matched (or named) session's cap. Use
  `set_default_session_budget` only when the user says "default" or "all sessions",
  because it changes every session without its own cap.
- Pass `expected_current_budget_usd` so a concurrent change is not overwritten: for a
  session cap, the cap from a fresh `budget` read; for the default, only when that read
  says the cap comes from the default. If there is no current cap, leave it out.
- Read `budget` again afterwards and report the new cap.
- Never change the monthly budget; it is edited only on the dashboard.

## Rules

- Label every dollar figure as an estimate, and say when a total is partial.
- Never include prompts, session titles, project names, file paths, or session ids.
