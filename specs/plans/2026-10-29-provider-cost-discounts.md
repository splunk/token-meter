# Provider cost discounts (contracted rates)

Status: proposed plan, not started. Route: **high-risk** (pricing-semantics,
persistence, new external write surface) → 1 independent tester + 2 project
reviewers with distinct lenses (pricing correctness; security/API).

## Problem

Token Meter prices Claude, Codex, and Cursor usage from public list ("rack")
rates. Organizations with negotiated contracts (reporter: 62% off OpenAI) see
figures far above what they pay. They want a per-model-provider discount
percentage, valid over a date range, settable from the dashboard **and** by an
external service, with costs recalculated whenever discounts change.

## Findings that shape the design

1. **Codex traces carry no dollar figure.** The 20 newest local rollouts contain
   only token counts and `rate_limits.credits` (`balance: null`); the adapter
   never reads a cost key and always prices from the OpenAI catalog
   (`runtimes/codex.py:1492-1506`, `1726-1742`). The account rep's "pre-processed
   final value" refers to OpenAI's own console, not local evidence. A discount is
   therefore valid for Codex, and no double-discount exists today.
2. **One costing choke point.** `cost_of(u, model, provider, variant, at)`
   (`app.py:4037`) prices every catalog request for Claude, Codex, Cursor, and
   Hermes-fallback, per event, with a timestamp. It resolves the model provider
   through `_resolved_price_quote` (`app.py:3950`). Separate USD paths:
   cache savings (`app.py:5953` per-execution `at`, `app.py:5966` `at=None`) and
   Kiro's own `_cost` (`runtimes/kiro.py:620, 717`).
3. **Reported costs bypass the catalog.** OpenCode (`reported_cost`), Pi
   (`usage.cost`), and Hermes (`actual_cost_usd`/`estimated_cost_usd`) use the
   runtime's own figure. Whether that figure is already contracted is unknowable.
4. **Costs are only stored pre-aggregated** (session `cost`, `model_cost`,
   `day_cost`, execution rows). Sessions mix providers and range boundaries can
   fall mid-day, so a discount **must be applied at costing time per event**, not
   by scaling stored totals.
5. **Recalculation today is HTTP-driven only.** `/settings/model-pricing`
   (`app.py:11938-11963`) clears `_summary_cache`, resets `_xsess`, recomputes,
   and refreshes cross-session state. A direct edit of `settings.json` reloads
   price tables (mtime) but leaves cached summaries stale. No USD is persisted
   anywhere else (work-insights, git-delivery, and matched-pace stores hold no
   cost), so in-memory invalidation is sufficient.
6. **No endpoint supports external callers.** The action token is per-process
   (`secrets.token_urlsafe`, `app.py:504`), rotates on restart, and is only
   discoverable by scraping `/state` or `/updates/status`. There is no Host-header
   check (DNS-rebinding exposure for any token-bearing GET).
7. **Model-provider identity.** `MODEL_PROVIDER_IDS = (anthropic, openai, cursor,
   opencode)`. Cursor prices gpt/claude models under provider `cursor`
   (`app.py:3954`). OpenCode falls back to the upstream vendor's catalog when it
   has no reported cost. Bedrock Claude resolves under `anthropic`.

## Decisions (recommended; confirm the starred ones)

- **D1 Scope key = model provider of the quote actually used**: `anthropic`,
  `openai`, `cursor`, `opencode`. Cursor-hosted GPT gets the *Cursor* discount;
  an OpenCode message priced from the OpenAI catalog gets the *OpenAI* discount.
  Display labels: Anthropic (Claude), OpenAI (Codex), Cursor, OpenCode.
- **D2 (revised 2026-10-29) Discount every list-price *estimate*, whoever computed
  it, and never a *billed* amount.** "Reported by the runtime" does not mean
  "billed":
  - **Pi** computes `usage.cost` client-side from its own list-price catalog. On
    local Bedrock traces (`provider: amazon-bedrock`, `api:
    bedrock-converse-stream`), the cost-per-token ratios are exactly $5 / $25 /
    $0.50 / $6.25 per 1M (input / output / cache read / cache write), which is
    Anthropic's public Opus list price. Bedrock Converse returns no price. The
    adapter already labels these figures "Pi estimate" (`pi.py:1112`, basis
    ESTIMATED). **They are discounted.**
  - **OpenCode** computes the message `cost` from its models catalog, also at list
    price, when it calls a vendor provider directly (anthropic, openai,
    amazon-bedrock, …). **That is discounted** under the upstream vendor's rule.
    The exception is OpenCode's own gateway providers (`opencode`, `opencode-go`):
    those charges are invoiced by the aggregator at its own rates, so they are
    keyed to the `opencode` provider and get only an `opencode` rule (local data:
    11 `opencode` messages, all $0).
  - **Hermes** separates `actual_cost_usd` (status actual/reported/final, basis
    MEASURED, so billed and never discounted) from `estimated_cost_usd` (basis
    ESTIMATED, so discounted).
  - Rule: a cost is discountable iff its basis is ESTIMATED **and** it is a
    list-price figure. Adapters tag each priced figure with `price_basis:
    "list" | "billed"`, plus the upstream model provider and billing route. A
    future runtime that reports a true billed cost tags it `billed` and is excluded
    automatically.
- **D2b Rule scope = whose price list × who bills you (billing channel).** The
  same Claude model has different contracts depending on who invoices it: Anthropic
  direct or subscription, AWS Bedrock, Google Vertex, Cursor, Kiro/AWS, or OpenCode
  Zen.
  - A rule targets one **price list** (`anthropic`, `openai`, `cursor`, `opencode`)
    and one **channel** (`direct`, `bedrock`, `vertex`, `azure`, or `any`).
  - Lookup: the exact-channel rule wins, then the `any` rule, otherwise list price.
  - **Cursor and Kiro price lists are only ever touched by their own rules.** An
    Anthropic or OpenAI rule never applies to a Cursor-hosted or Kiro-hosted
    model, even with channel `any`, because those vendors set the price you pay.
  - The channel is a closed enum. ARN account numbers are never stored or projected.
- **D2c Channel detection per request**, most specific evidence first:

  | Runtime | Channel evidence | Basis |
  |---|---|---|
  | Pi | `provider` (`amazon-bedrock`, `anthropic`, `openai`, `google-vertex`, …) | explicit |
  | OpenCode | message `providerID` (`amazon-bedrock`, `anthropic`, `openai`, `opencode`, `opencode-go`, …) | explicit |
  | Hermes | `billing_provider` | explicit |
  | Codex | session_meta `model_provider` (`openai`, `azure`, …); local: 200/200 `openai` | explicit |
  | Claude Code / Desktop | model id is plain `claude-opus-5-5` on Bedrock too (14k local requests), so the model id carries no channel. The request-id shape does: `req_…` means Anthropic first-party, and a UUID means a non-Anthropic gateway (all 43k local Bedrock requests are UUIDs) | **inferred**, needs a direct-API sample to confirm `req_` |
  | Cursor, Kiro | always their own channel | fixed |

  Add a per-runtime **channel declaration** for Claude Code and Claude Desktop:
  `auto` (default, uses request-id inference), `direct`, `bedrock`, or `vertex`.
  It's for users whose inference is wrong, or who switch channels.
- **D3 ★ Discount stacks on the effective price**, whether that's built-in or a
  user override. The Settings UI warns when a provider with an active discount
  also has model overrides ("overrides are discounted too").
- **D4 Interval semantics:** half-open `[start, end)` in Unix seconds; `null` start
  = since forever, `null` end = open-ended. Past, current, and future ranges are
  all allowed (unlike prices, a future-dated contract is legitimate).
- **D5 ★ No overlap:** ranges for one provider may not overlap; a write that would
  overlap is rejected with a clear error. This gives deterministic results and
  keeps an external sync honest. (Alternative: latest-written wins; rejected
  because it hides mistakes.)
- **D6 Percent:** finite, `0 < percent < 100`, stored to 2 decimals. 0 means
  "delete the rule". 100 is rejected so a discount never fabricates a measured-
  looking zero.
- **D7 Events with no timestamp** are evaluated at "now" (matches current price
  semantics for `at=None`). Hermes session-granularity `at` is accepted as is.
- **D8 Labels:** whenever any discount touched a figure, the estimate label
  becomes "Discounted estimate" (Spend note, current-day note, MCP description);
  otherwise it's unchanged. Costs stay `basis = estimated`.
- **D9 ★ Show list-price context:** carry `discount_usd` (amount removed) beside
  `cost` on session, day, and model aggregates, so Spend can show "after
  discounts · list $X". Budgets, caps, goals, Compare, Work, and Git use the
  discounted `cost` (what you pay).
- **D10 ★ External API auth:** add a **stable local API token** stored at
  `~/.token-meter/api-token` (0600, created on first start, rotatable). It's
  accepted only on the new discount endpoints via `Authorization: Bearer`, plus
  the existing per-process action token for the dashboard. Add a **Host-header
  allowlist** (`127.0.0.1:8722`, `localhost:8722`) to the new endpoints. The
  server stays loopback-bound: a remote service must run on the machine or reach
  it through its own local agent. Remote exposure is out of scope.

## Use cases and how a discount applies

The example rules are A = "Anthropic · direct · 30% from 2026-09-01, open
end" (a discounted Claude team plan) and O = "OpenAI · any · 62%".

| # | Scenario | Price list used | Channel | Rule that applies | Result |
|---|---|---|---|---|---|
| 1 | Claude Code / Desktop on a Claude subscription or Anthropic API key | Anthropic list | direct (`req_` ids) | A | −30% from 2026-09-01; list before |
| 2 | Claude Code / Desktop on Bedrock | Anthropic list | bedrock (UUID ids) | none yet; a future "Anthropic · bedrock" rule | list price until a Bedrock rule exists |
| 3 | Claude via Google Vertex | Anthropic list | vertex | "Anthropic · vertex" rule only | list unless a Vertex rule exists |
| 4 | Pi / OpenCode / Hermes calling Bedrock (Claude, Nova, Llama…) | runtime's own list estimate | bedrock (explicit) | "‹vendor› · bedrock", then "‹vendor› · any" | discounted only by a Bedrock or any rule; A does not apply |
| 5 | Pi / OpenCode / Hermes with an Anthropic or OpenAI API key | runtime's list estimate | direct | A or O | discounted like Claude Code / Codex |
| 6 | Codex on ChatGPT plan or OpenAI API | OpenAI list | direct (`model_provider: openai`) | O | −62% |
| 7 | Codex on Azure OpenAI | OpenAI list | azure | "OpenAI · azure" or O (any) | O applies because it's `any`; set O to `direct` to exclude Azure |
| 8 | Cursor, any model (Claude, GPT, Composer…) | Cursor's own rates | cursor | "Cursor" rule only | A and O never apply; Cursor sets the price |
| 9 | Cursor with your own API key (BYOK) | Cursor's rates | not detectable | Cursor rule | known gap: treated as Cursor-billed |
| 10 | Kiro (AWS-billed credits) | Anthropic list, used as a proxy | kiro | "Kiro" rule only | A never applies |
| 11 | OpenCode Zen / Go gateway | OpenCode-reported charge | opencode | "OpenCode" rule only | aggregator price; vendor rules never apply |
| 12 | Hermes `actual_cost_usd` (billed, e.g. via a router) | billed amount | — | none | never discounted: already what you paid |
| 13 | Usage outside a rule's dates | — | — | — | list price; boundaries are per request, so a session crossing a start date is split |
| 14 | A start date in the past (retroactive) | — | — | — | history, past months' budgets, goals and Compare reprice. Budget notifications already sent are not retracted |
| 15 | A model with a manual price override | the override | — | the channel rule | stacks: the discount applies on top of the override, and Settings warns |
| 16 | Fast mode, long context, US-geo, 1h cache, web search | list + modifiers | — | the channel rule | discount applies after the modifiers |
| 17 | Cache-savings figure | list | — | same rule | discounted by the same rate |
| 18 | Subagent / child runs | per-request | per child | per child request | discounted once; parent totals reconcile |
| 19 | An unpriced model or missing usage | — | — | — | stays unavailable; a discount never invents a cost |
| 20 | Free or zero-cost usage | — | — | — | stays $0 |
| 21 | Different % per model inside one provider (e.g. Opus vs Sonnet) | — | — | — | **not in v1** (provider-level only) |
| 22 | Subscription with a flat seat fee | — | — | — | figures stay "API-equivalent"; a discount makes them "discounted API-equivalent", not your invoice |

## Data model

`settings.json` gains one key, written through the existing atomic
`write_settings_json` path:

```json
"cost_discounts": {
  "version": 1,
  "rules": [
    {
      "id": "d_7f3a…",            // server-assigned, [a-z0-9_]{8,40}; or caller-supplied external_id
      "price_list": "openai",    // anthropic | openai | cursor | kiro | opencode
      "channel": "direct",       // any | direct | bedrock | vertex | azure; cursor/kiro/opencode lists ignore it
      "percent": 62.0,
      "start": 1767225600,       // or null
      "end": null,               // or null
      "label": "Claude team plan",// optional, ≤60 chars, printable, no newlines
      "source": "api",           // "dashboard" | "api"
      "external_id": "contract-123", // optional caller key for idempotent upserts, ≤64 [A-Za-z0-9._:-]
      "updated_at": 1793000000
    }
  ],
  "runtime_channels": {"claude": "auto", "claude_desktop": "auto"}  // auto | direct | bedrock | vertex
}
```

Overlap is checked per (price_list, channel). Bounds: ≤64 rules per provider and ≤256 total. Invalid stored rules are dropped
on load (matching model-price tolerance), never fatal. A missing key means no
discounts, so no migration is needed. `revision` = sha256 of the canonical JSON of
`rules`, used for optimistic concurrency and change detection.

## Backend design

1. **New pure module `token_meter/models/discounts.py`** (stdlib only):
   `normalize_rule`, `normalize_rules` (sort, overlap check), `discount_at(rules,
   provider, ts) -> (percent, rule_id) | None` using per-provider sorted intervals
   with `bisect`, and `revision(rules)`. No I/O.
2. **`app.py` loader** `_load_cost_discounts(path)`, sharing the mtime/TTL cache
   style of `_load_model_price_histories`, plus `cost_discount_settings()` for
   display and `set_cost_discounts(ops, expected_revision=None, source=...)`.
3. **Apply in `cost_of`:** after the breakdown is computed, look up
   `discount_at(quote.model.provider_id, at)` and scale all four components by
   `(1 - p/100)`. Record the removed amount on the result. Then do the same for:
   - cache savings at `app.py:5953` and `5966`;
   - Kiro's `_cost` and `quote_resolver` split (`kiro.py:620-727`);
   - the Hermes catalog fallback (the reported path is untouched).

   Runtime-computed list-price paths go through the same helper before the
   token-weighted split, keyed by upstream provider and route:
   - Pi `usage.cost` (`pi.py:308-335`, including subagent nested usage);
   - OpenCode vendor-provider `reported_cost` (`opencode.py:331`, `978`, `1423`);
   - Hermes `estimated_cost_usd` (`hermes.py:509`).

   Hermes `actual_cost_usd` (MEASURED) and OpenCode gateway charges are
   not discounted except by an explicit `opencode` rule. Add a single helper
   `apply_cost_discount(amount_or_breakdown, model_provider, route, at,
   price_basis)` so no adapter computes discounts itself. Adapters only supply the
   three tags.
4. **Thread `discount_usd`** through `summary_row` (`app.py:6121`), the model×day
   rebuild (`app.py:8736-8791`), and cross-session totals. Add it to the explicit
   projection allowlists (`projections.py:15-46`) only where Spend and MCP need it.
5. **One invalidation helper** `invalidate_cost_caches()` (clear `_summary_cache`,
   reset `_xsess`, `_model_pricing_cache`, discount cache, recompute,
   `refresh_cross_session_state`). The model-pricing endpoint is refactored to call
   it too, with no behavior change.
6. **External-write detection:** the watcher tick compares a `pricing_revision` =
   hash(model_pricing, cost_discounts) and runs the same invalidation when it
   changes. This covers hand edits and other writers, and fixes the existing
   model-pricing staleness gap as a side benefit.
7. **Recalculation cost:** full repricing is synchronous today for pricing edits.
   Measure on a large local history; if it's over ~2 s, return
   `{"recalculation": "scheduled"}` and run it single-flighted in the background
   (pattern already used for matched-pace rebuilds), with `revision` in `/health`
   so callers can poll.

## HTTP API

All endpoints are loopback-only, Host-allowlisted, JSON, and return bounded bodies
with no local paths or raw exception text.

| Method / path | Auth | Purpose |
|---|---|---|
| `GET /api/v1/cost-discounts` | API token or action token | `{revision, rules[], active_now: {provider: percent}, providers[]}` |
| `POST /api/v1/cost-discounts` | API token or action token | Batch ops (below); atomic all-or-nothing |
| `POST /settings/cost-discounts` | action token | Dashboard alias of the same handler (keeps the existing `/settings/*` convention) |

POST body (≤8 KB, the existing limit; ≤64 ops):

```json
{
  "expected_revision": "sha256…",          // optional; 409 on mismatch
  "ops": [
    {"op": "upsert", "external_id": "contract-123", "model_provider": "openai",
     "percent": 62, "start": "2026-01-01T00:00:00-08:00", "end": null, "label": "EA"},
    {"op": "delete", "id": "d_7f3a…"},
    {"op": "replace_provider", "model_provider": "anthropic", "rules": [ … ]}
  ]
}
```

- `start`/`end` accept Unix seconds or ISO-8601 with an explicit offset. A naive
  date is rejected (no silent timezone guessing).
- `upsert` matches on `id` or `external_id`, so it's idempotent. `replace_provider`
  lets a sync service own one provider's whole schedule.
- Response: `{ok, changed, revision, rules, recalculated: true|"scheduled"}`.
  Errors: 400 validation (first error message), 401 token, 403 host/origin,
  409 revision conflict, 413 size.
- An unchanged write returns `changed: false` and skips recalculation.
- `scripts/` gets no new commands. README documents curl usage and where the
  token lives.

## Dashboard (page.html)

- Spend → Monthly budget gets a **Discounts** card under Set budgets (superseded placement: see the design doc) instead of a Settings card next to Model
  pricing (no new top-level view), with route `#cost-discounts` registered in the
  settings router. Existing `#model-pricing` routes are unchanged.
- Per-provider rows show percent, start date, end date (blank = open), label, and
  source badge (Dashboard/API), with add, edit, and delete. Date pickers convert
  local midnight to Unix seconds. "Active now" is shown per provider. Overlap and
  validation errors come back from the server.
- Spend total note and current-day note say "Discounted estimate · list $X" when
  `discount_usd > 0`. Provider identity uses `providerColor()`/`--provider-*`, and
  components reuse `.chip`, `.emptyState`, and the existing table styles. No new
  colors, px sizes, or raw weights (design contract).
- Native menu bar: no Swift change in v1. It shows the discounted figure with the
  existing " est" suffix. This needs a live check; if the copy should change, that
  goes in a follow-up.

## MCP

- The `cost_usd` description becomes "Reported, or Token Meter-estimated
  (after any configured contract discount), API-equivalent cost". Add
  `discount_usd` where `cost_usd` is aggregated. MCP stays read-only for
  discounts in v1; adding write access would need the confirmed-change pattern
  and is a separate decision.

## Tests (TDD order)

1. `tests/contracts/test_cost_discounts.py` (pure module):
   - interval boundaries: start inclusive, end exclusive, null/null, open ends;
   - overlap rejection, including touching ranges (allowed);
   - percent bounds, revision stability, and bisect lookup over 64 rules.
2. `tests/test_meter.py`:
   - persistence through atomic write; invalid stored rules are ignored;
   - Codex event before, inside, and after a range costs list, discounted, and list;
   - a mid-day boundary splits `day_cost` correctly;
   - a mixed-provider session discounts only the matched provider;
   - Cursor-hosted GPT uses the Cursor rule, not the OpenAI rule;
   - Pi on Bedrock gets the anthropic/bedrock rule, falling back to anthropic/any;
   - Pi subagent nested cost is discounted once (no double count);
   - OpenCode vendor-provider cost and its upstream-catalog fallback get the vendor
     rule, while OpenCode gateway (`opencode`) cost gets only an `opencode` rule;
   - Hermes actual cost is unchanged and Hermes estimated cost is discounted;
   - Claude Code Bedrock ARN / `us.anthropic.` ids resolve to route `bedrock`;
   - the ARN account id never appears in any payload;
   - cache savings are discounted;
   - Kiro is discounted;
   - a model-price override and a discount stack;
   - budgets, caps, and goals see the discounted cost;
   - `discount_usd` sums reconcile (`cost + discount_usd == list`).
3. HTTP:
   - API token accepted; action token accepted; a missing or wrong token is 401;
   - a bad Host is 403; a non-loopback Origin is 403;
   - 409 on a stale `expected_revision`;
   - idempotent `external_id` upsert;
   - `replace_provider`;
   - an unchanged write skips recompute;
   - a successful write changes `/state` totals with no restart;
   - a hand-edited `settings.json` is picked up by the watcher.
4. The API token file is created 0600, reused across restarts, and never appears
   in any GET payload or log.
5. UI contract strings, the design-system contract, the MCP schema/description
   test, and the privacy test (no token or label leaks into MCP or the menu bar).

## Validation gates

- unit + contract suite;
- `py_compile`;
- embedded JS parse;
- `git diff --check`;
- the design-system contract;
- browser checks at 1440 and 1024 px (add, edit, delete, overlap error, and Spend
  label);
- `scripts/style-snapshot.js` diff for the new card;
- `./scripts/install`;
- `/health` and `/menubar`;
- runtime parity;
- a live curl round trip against the installed server (set 62% OpenAI, confirm
  `/state` Codex spend drops by 62%, delete it, confirm the spend is restored);
- a live menu-bar check;
- a performance measurement of full repricing.

## Docs

- README: replace "estimated at public list prices" with discount-aware wording,
  and add a "Contract discounts" section with curl examples and the token location.
- specs/ARCHITECTURE.md: discount stage in Domain and Model Flow, and the invariant
  "discounts apply only to catalog estimates, per event, keyed by the quote's model
  provider". Add the new endpoint/auth row.
- runtime-manifest.txt: add `token_meter/models/discounts.py`.

## Out of scope (v1)

- Per-model or per-runtime discounts.
- Tiered or volume pricing.
- Currency conversion.
- Remote (non-loopback) access.
- MCP writes.
- Discounting billed amounts (Hermes actual, aggregator invoices) except through
  an explicit rule for that provider.
- Native menu-bar copy changes.

## Open questions for the owner

1. D2/D2b/D2c: discount every list-price estimate (Pi, OpenCode vendor calls,
   Hermes estimates) and never a billed amount. Scope each rule as price list ×
   billing channel. Cursor, Kiro, and OpenCode are reachable only by their own
   rules. Recommended: yes.
2a. Claude Code/Desktop channel: infer it from the request-id shape (`req_` =
   direct) with a per-runtime override. Needs one direct-API or subscription trace
   to confirm the `req_` assumption before shipping auto-detection.
2b. Per-model discounts inside one provider (use case 21): defer to v2?
2. D3: should discounts stack on manual model-price overrides? Recommended: yes, with a UI warning.
3. D5: reject overlapping ranges, or let the latest write win? Recommended: reject.
4. D9: show list price beside the discounted cost? Recommended: yes.
5. D10: stable file-based API token plus a Host allowlist for external callers? Or
   should the external service run as a local process that scrapes the per-process
   token? Recommended: the stable token.
