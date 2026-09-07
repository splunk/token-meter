# Claude Cost Correctness Requirements

## Scope

Token Meter shall estimate Claude API-equivalent cost from locally observable
Claude Code and Claude Desktop traces. The estimate is not a provider invoice,
does not infer subscription-plan allocation, and does not infer a cloud reseller
price from a model-shaped identifier.

## Requirements

### R1: Cache-write duration

- When a Claude usage record reports both
  `cache_creation.ephemeral_5m_input_tokens` and
  `cache_creation.ephemeral_1h_input_tokens`, Token Meter shall preserve both
  measured counts and price them at 1.25 times and 2 times the effective input
  rate respectively.
- When the duration-specific counts do not sum to the reported aggregate cache
  creation count, Token Meter shall exclude that record from the cost estimate
  and mark cost coverage incomplete.
- When a historical record reports only the aggregate cache creation count,
  Token Meter shall preserve the count as duration-unspecified, use the existing
  configured cache-write rate as a compatibility estimate, and mark cost
  coverage incomplete rather than inventing a duration.

### R2: Trace-carried billing dimensions

- When `speed` is absent, null, or `standard`, Token Meter shall use standard
  model rates.
- When `speed` is `fast` for a model with published fast-mode pricing, Token
  Meter shall use the published fast-mode input, output, and cache rates.
- When `speed` is `fast` for Opus 4.6, Token Meter shall use standard rates;
  models for which the fast request is rejected shall remain unpriceable.
- When `inference_geo` is `us` on Claude 4.6 or later, Token Meter shall apply
  the published 1.1 multiplier to every token category. Explicit `global`,
  empty, or absent geography means the standard global rate. An explicit
  geography on an older model shall remain unpriceable because that API
  dimension is unsupported.
- When `server_tool_use.web_search_requests` is a valid non-negative count,
  Token Meter shall add the published per-request web-search cost. Web-fetch
  requests have no additional charge.
- When an explicit speed, service tier, geography, or nonzero server-tool
  category is unsupported, Token Meter shall exclude the record from the cost
  estimate and mark cost coverage incomplete.

### R3: Complete duplicate-safe discovery

- When a Claude Code session has nested `subagents/*.jsonl` traces, Token Meter
  shall load them as part of the parent logical session.
- When a Claude Desktop local-agent session stores multiple main or nested
  transcripts below the metadata-owned session directory, Token Meter shall
  load all of them as one logical session.
- When the same assistant `message.id` occurs in more than one physical trace,
  Token Meter shall count its usage once.
- When assistant rows have no `message.id` or row UUID, Token Meter shall
  preserve distinct rows and deduplicate only exact physical copies.
- When a logical session owns multiple physical traces, Token Meter shall keep
  deletion disabled rather than delete only part of the session.
- A change to any grouped trace shall change the source revision.

### R4: Catalog coverage

- The Anthropic catalog shall include the currently published direct-API rates
  for Mythos 5 limited, Opus 4.7, Opus 4.6, Opus 4.5, Sonnet 4.5, and Haiku 3.5.
- The Sonnet 5 catalog note shall describe its current published price without a
  stale claim that the price will revert.
- Catalog aliases shall remain provider-scoped and unknown models shall remain
  unavailable.

### R5: Safe projections

- Native and MCP projections shall expose aggregate cache writes plus the
  measured five-minute, one-hour, and duration-unspecified components when
  available.
- No projection shall expose a local path, prompt, response, reasoning, tool
  payload, credential, account datum, or raw trace.
- The dashboard cost breakdown shall display a web-search component whenever
  the total includes a server-tool request charge.

## Acceptance

A synthetic one-million-token one-hour Opus 4.8 cache write shall cost $10.00
at standard global rates. A duplicate copy of that logical message shall not
change the result. Focused, full, installed-runtime, endpoint, manifest-parity,
and supported-width browser checks must pass before handoff.
