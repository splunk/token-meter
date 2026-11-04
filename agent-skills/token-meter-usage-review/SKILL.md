---
name: token-meter-usage-review
description: Review the last 30 days of agent usage with the Token Meter MCP and produce a dense, evidence-backed report of ranked learnings. Use when the user asks to review their agent usage, spend, or efficiency, asks where their tokens or money went, or wants lessons from recent sessions.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Token Meter usage review

Use the `tokenmeter` MCP tools to review the last 30 days of agent usage (or the window
the user names), then write a dense report of ranked learnings. If the `tokenmeter` tools
are unavailable, say so and stop; do not estimate usage from memory.

## Gather evidence

- Call the tools one at a time. The first heavy query can take a while to build its
  index; if a call times out, wait a minute and retry once.
- Build every 30-day number from `stats` with `start` set to 30 days before now (ISO-8601).
  `usage` only supports `today`, `7d`, and `14d`; use it for its flagged tool tokens and
  day-over-day change, and say which window each number covers.
- `stats` accepts at most 8 metrics per call, and tool metrics (`tool_calls`,
  `tool_result_tokens`) must be queried separately from execution or session metrics.
  Run at least these:
  - by `model`: `cost_usd`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
    `cache_write_tokens`, `session_count`, `context_peak`;
  - by `client`: `cost_usd`, `session_count`, `context_peak`;
  - by `day`: `cost_usd`, `session_count`, `cache_write_tokens`, `output_tokens`;
  - by `session_id`, sorted by `cost_usd`, limit 25: `cost_usd`, `context_peak`,
    `context_latest`, `cache_write_tokens`, `output_tokens`;
  - by `tool_name`: `tool_calls`, `tool_result_tokens`;
  - once per runtime for coverage: `retries`, `failed_attempts`, `wait_seconds`,
    `model_calls`, `active_seconds`.
- To compare halves, rerun the `day` query or split `start`/`end` into the earlier and
  later 15 days.
- Use `capabilities` (scope `all`) for installed skill packs with no observed use.
- Read the `coverage` of every metric. Unavailable evidence is not a zero; never report it
  as one.

## Find the learnings

Look at each lens, compute the number, and keep what is material:

- **Cost structure**: split spend into cache writes, cache reads, output, and uncached
  input from token counts and the model's rates; compare cache writes to all new content
  (output plus tool results).
- **Concentration**: share of spend in the top 1, 5, and 10 sessions.
- **Context**: sessions peaking above 50% of the window, and whether `context_latest`
  ever came back down.
- **Tool output**: share and tokens per call by tool; outliers per session.
- **Model mix**: spend share by model and client; routine work on the most expensive model.
- **Trend**: earlier half vs. later half for spend, sessions, and cost per session.
- **Hygiene**: unused skill packs and anything else Token Meter flags.

Report up to five learnings, ranked by estimated cost impact, with fewer when the
evidence is thin. Each learning is one compact block:

- **Finding**: one sentence with the key number.
- **Evidence**: two to four numbers, each with its metric and scope.
- **Likely cause**: labeled as a hypothesis whenever the data cannot prove it.
- **Try**: one concrete change to start today.
- **Check**: the exact Token Meter query to rerun in 7 days and the success threshold.
- **Impact**: an estimated dollar range at stake over 30 days, with the assumption stated.

## Report layout

Keep it dense and scannable. No filler prose, no repeated numbers.

1. **Headline strip**: 30-day estimated spend, sessions, top model share, cache-write
   share of spend, peak context, and the trend between halves.
2. **Learnings**: the ranked blocks above.
3. **Patterns**: one small table each for spend by model and client, top sessions
   (ranked by cost, labeled only by rank), and top tools by result tokens.
4. **Missing evidence**: what Token Meter could not see (retries, wait time, idle gaps,
   tool arguments, unpriced executions, and so on) and which learning each gap weakens.

## Rules

- Label every dollar figure as an estimate, and say when a total is partial because some
  pricing evidence is missing.
- If two totals disagree (for example `usage` and `stats` over different scopes), show
  both and say which one each section uses.
- Never include prompts, session titles, project names, file paths, or session ids in
  the report.
- Prefer compact tables and simple bars to paragraphs. When the user wants a file, write
  a single self-contained HTML page with no external assets.
