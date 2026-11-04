---
name: token-meter-usage-review
description: Review recent agent usage with the Token Meter MCP and produce three evidence-backed lessons. Use when the user asks to review their agent usage, spend, or efficiency, asks where their tokens or money went, or wants lessons from recent sessions.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Token Meter usage review

Use the `tokenmeter` MCP tools to review the last 14 days of agent usage, then write a
short report with three actionable lessons. If the `tokenmeter` tools are unavailable,
say so and stop; do not estimate usage from memory.

## Gather evidence

- Call the tools one at a time. The first heavy query can take a while to build its
  index; if a call times out, wait a minute and retry once.
- Start with `usage` (focus `spend`, `models`, `tools`, and `changes`; window `14d`) for
  the headline numbers.
- Use `stats` for breakdowns. It accepts at most 8 metrics per call, and tool metrics
  (`tool_calls`, `tool_result_tokens`) must be queried separately from execution or
  session metrics. Useful groupings: `model`, `client`, `day`, `session_id`, `tool_name`.
- Use `sessions` (scope `all`) for per-session cost and context, and `capabilities`
  (scope `all`) for installed skill packs with no observed use.
- Read the `coverage` of every metric. Unavailable evidence is not a zero; never report it
  as one.

## Find the lessons

Rank three lessons by estimated cost impact. Good places to look:

- How spend splits across cache writes, cache reads, output, and uncached input
  (rebuild it from token counts and the model's rates).
- Sessions whose context peak is a large share of the window, and whether context ever
  came back down.
- Which tools return the most result tokens, and the average per call.
- Spend by model, and whether routine work runs on the most expensive model.
- Whether a few sessions carry most of the spend.

For each lesson give:

1. **Evidence**: specific numbers, with the metric and scope they came from.
2. **Possible explanation**: labeled as a hypothesis whenever the data cannot prove it.
3. **One change to try**: concrete enough to start today.
4. **How to check it helped**: the exact Token Meter query to rerun in a week, and the
   threshold that would count as success.

## Rules

- Label every dollar figure as an estimate, and say when a total is partial because some
  pricing evidence is missing.
- If two totals disagree (for example `usage` and `stats` over different scopes), show
  both and say which one each breakdown uses.
- Include a **Missing evidence** section: what Token Meter could not see (retries, wait
  time, idle gaps, tool arguments, and so on) and which lesson each gap weakens.
- Never include prompts, session titles, project names, file paths, or session ids in
  the report.
- Keep it to one page: a few headline numbers, the three lessons, then missing evidence.
  Prefer simple bar charts to raw tables. When the user wants a file, write a single
  self-contained HTML page with no external assets.
