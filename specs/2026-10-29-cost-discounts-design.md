# Discounts — Design

Status: **historical design record** (2026-10-29). The shipped behaviour is documented in
[Cost estimates and budgets](COST_AND_BUDGETS.md#discounts-show-what-you-actually-pay)
and the [Local API](LOCAL-API.md); where this proposal differs, those win.

## 1. Summary

Token Meter prices usage at public list ("rack") rates. Many individuals and
organizations pay less: enterprise agreements, committed-spend discounts, cloud
marketplace contracts, and discounted team plans. For them, every cost Token Meter
shows is overstated. One reporter, for example, has 62% off OpenAI usage.

This feature adds **discounts** that work for any Token Meter user. A discount is a
percentage off list price for one **price list** (Anthropic, OpenAI, Cursor, Kiro,
OpenCode) and one **billing channel** (the provider directly, AWS Bedrock, Google
Vertex, Microsoft Azure, or anyone). It is valid over a date range whose start and
end can each be left open.

There are two equal ways to set a discount:

- **Manually**, on the Spend page next to monthly budgets. This needs no setup and is the path most
  users take.
- **By another tool on the same computer**, through a local HTTP API. Examples are
  a company's contract or billing sync service, a script, or an MDM job.

Every cost Token Meter shows is recalculated whenever a discount changes, however
it was changed.

Key guarantees:

- **The default is 0%.** With no rules, every figure is exactly what Token Meter
  shows today.
- **Once set, a discount persists.** Updates, reinstalls, and uninstalls never
  reset it (see §8).
- **Discounts touch only list-price estimates**, never amounts a provider
  actually billed.
- **Discounts are applied per request at the request's own time**, so date
  boundaries are exact even mid-session.
- **Costs are always labelled.** A figure stays an *estimate*; once discounted it
  reads "Discounted estimate", and the list price stays visible beside it.
- **Manual and tool-set discounts are the same object.** Settings shows who set
  each one ("Settings" or the tool's name), and either side can edit or remove it.

## 2. Background findings

These checks were run against the current code and local traces (key names and
numeric ratios only; no content was read).

| Question | Finding | Consequence |
|---|---|---|
| Does Codex report a final (already discounted) cost? | No. Rollouts carry only token counts and an empty credit balance, and Token Meter prices every Codex request from the OpenAI catalog. The "pre-processed final value" the account rep mentioned is in OpenAI's own console. | A discount is required; there is no double-discount risk. |
| Is a runtime-"reported" cost a billed amount? | Not usually. Pi on Bedrock records costs whose per-token ratio is exactly Anthropic's public list price ($5 / $25 / $0.50 / $6.25 per 1M), and Bedrock returns no price. OpenCode computes vendor-call costs from its catalog. Hermes separates `actual_cost_usd` (billed) from `estimated_cost_usd`. | Discount every list-price estimate, whichever tool computed it; never discount a billed amount. |
| Can we tell Bedrock from direct Anthropic? | Pi, OpenCode, Hermes, and Codex record the provider explicitly. Claude Code / Desktop record a plain model id (`claude-opus-5-5`) on Bedrock too, but request ids differ: Anthropic first-party ids look like `req_…`, while Bedrock ids are UUIDs (all 43k local Bedrock requests). | The channel can be inferred, with a manual per-runtime override. The `req_` assumption must be confirmed against one direct-API trace before shipping auto-detection. |
| Where are costs calculated? | One per-request function (`cost_of`), plus cache savings, Kiro's estimator, and the runtime-estimate paths (Pi, OpenCode, Hermes). Stored totals are pre-aggregated per session, day, and model. | The discount must be applied when each request is priced, not by scaling totals. |
| Does an external edit recalculate today? | No. Only the dashboard's pricing save clears caches. | Add one invalidation path, plus a settings fingerprint check so direct edits and other writers are picked up too. |

## 3. Concepts

| Term | Meaning | Values |
|---|---|---|
| **Price list** | Whose public rates Token Meter used to estimate the request | `anthropic`, `openai`, `cursor`, `kiro`, `opencode` |
| **Billing channel** | Who invoices you for that request | `direct`, `bedrock`, `vertex`, `azure`, `any` (rule-side only) |
| **Rule** | price list + channel + percent + date range | e.g. Anthropic · direct · 30% · from 2026-09-01 |
| **Price basis** | Whether a figure is a list-price estimate or a billed amount | `list` (discountable), `billed` (never discounted) |

Lookup for each request:

1. Skip if the figure is `billed` or unavailable.
2. Find the rule for (price list, exact channel) whose range contains the
   request time.
3. Otherwise find the rule for (price list, `any`) whose range contains it.
4. Otherwise apply no discount (0%).

**Cursor, Kiro, and OpenCode price lists can only be matched by their own
rules.** An Anthropic or OpenAI rule never applies to a model hosted by Cursor or
Kiro, even with channel `any`, because those vendors set the price you pay.

Date ranges are half-open, `[start, end)`. `start: null` means "since the
beginning", `end: null` means "no end". Past, present, and future ranges are all
allowed. Ranges for the same (price list, channel) may not overlap; ranges that
only touch (one ends exactly when the next starts) are fine.

## 4. Use cases

The examples use **A** = "Anthropic · direct · 30% from 2026-09-01, no end"
(a discounted Claude team plan) and **O** = "OpenAI · any · 62%" (an enterprise
agreement). The scenarios apply to any user; the rules are examples.

| # | Scenario | Price list | Channel (how we know) | Rule that applies | Result |
|---|---|---|---|---|---|
| 1 | Claude Code / Desktop on a Claude subscription or Anthropic API key | Anthropic | direct (`req_` request ids) | A | −30% from 2026-09-01; list price before |
| 2 | Claude Code / Desktop on AWS Bedrock | Anthropic | bedrock (UUID request ids, or manual override) | "Anthropic · bedrock" if one exists | list price; **A does not apply** |
| 3 | Claude via Google Vertex | Anthropic | vertex | "Anthropic · vertex" | list price unless a Vertex rule exists |
| 4 | Pi / OpenCode / Hermes calling Bedrock (Claude, Nova, Llama, …) | the vendor's list | bedrock (explicit provider field) | "‹vendor› · bedrock", then "‹vendor› · any" | A does not apply |
| 5 | Pi / OpenCode / Hermes with an Anthropic or OpenAI key | the vendor's list | direct (explicit) | A or O | discounted the same as Claude Code / Codex |
| 6 | Codex on a ChatGPT plan or OpenAI API | OpenAI | direct (`model_provider: openai`) | O | −62% |
| 7 | Codex on Azure OpenAI | OpenAI | azure (`model_provider: azure`) | "OpenAI · azure", then O | O applies because it is `any`; scope O to `direct` to exclude Azure |
| 8 | Cursor, any model (Claude, GPT, Composer, …) | Cursor | cursor (fixed) | Cursor rule only | A and O never apply; Cursor's prices decide |
| 9 | Cursor with your own API key (BYOK) | Cursor | not detectable | Cursor rule | **known gap:** treated as Cursor-billed |
| 10 | Kiro (AWS-billed credits) | Kiro (estimated from Anthropic list) | kiro (fixed) | Kiro rule only | A never applies |
| 11 | OpenCode Zen / Go gateway | OpenCode | opencode (fixed) | OpenCode rule only | vendor rules never apply |
| 12 | Hermes `actual_cost_usd` (billed, e.g. through a router) | — | — | none | never discounted; already what you paid |
| 13 | Usage before a rule's start or after its end | — | — | — | list price. Each request uses its own time, so a session crossing the start date is split |
| 14 | A rule added today with a start date in the past | — | — | — | history, past months' budgets, goals, and Compare all reprice. Budget notifications already sent are not retracted |
| 15 | A model with a manual price override in Settings | the override | — | the matching rule | the discount stacks on the override; Settings shows a warning |
| 16 | Fast mode, long context, US-geo, 1-hour cache, web search | list + modifier | — | the matching rule | the discount applies after modifiers |
| 17 | "Cache saved you $X" figure | list | — | same rule | discounted by the same rate |
| 18 | Subagent / child runs | per request | per child | per child request | discounted once; parent totals reconcile |
| 19 | An unpriced model or missing usage | — | — | — | stays "unavailable"; a discount never invents a cost |
| 20 | Free / $0 usage | — | — | — | stays $0 |
| 21 | Different % per model inside one provider (Opus vs Sonnet) | — | — | — | **not in v1**; provider-level only |
| 22 | Flat per-seat subscription | — | — | — | figures stay "API-equivalent"; once discounted they are "discounted API-equivalent", not your invoice |

### Typical setups

| Who | What they set | Where |
|---|---|---|
| An individual on a discounted plan | One rule, e.g. "Anthropic · the provider · 20% · from the plan's start date" | Settings |
| A team with an enterprise agreement for one vendor | One rule per vendor, with the contract term as the dates; a renewal at a new rate is a new rule starting the day the old one ends | Settings, or the team's own sync tool |
| A company that buys Claude through AWS Bedrock *and* directly | Separate "Anthropic · AWS Bedrock" and "Anthropic · the provider" rules, because the contracts differ | Settings or a tool |
| A company with central contract data (for example, a contract-sync tool on the same computer) | The tool owns those rules via `replace_scope`; employees can see them and who set them | Local API |
| Someone with no discount | Nothing; costs stay at list price exactly as today | — |

## 5. What changes on each surface

| Surface | Change |
|---|---|
| Spend, Sessions, Models, Daily, Compare, Work, Git | Show the discounted cost. Spend's total note reads "After discounts · list $X". |
| Monthly budgets, session caps, goals | Measure against the discounted cost (what you pay). |
| Menu bar | Shows the discounted figure with the existing "est" suffix. No native code change in v1. |
| MCP | `cost_usd` is the discounted figure, plus a new `discount_usd`. The description says it reflects configured contract discounts. Discounts stay read-only over MCP in v1. |
| Spend → Monthly budget | **Discounts live with budgets on the Spend page** (`#spend-discounts`). (1) A notice inside the Monthly budget card, which everyone sees: with no discounts it reads "Spend uses public list prices. Pay a negotiated or company rate? Add a discount so spend and budgets match your bill" with **Add a discount** and **Not now**; with discounts it reads "Spend includes your discounts" with a chip per active discount and **Manage discounts**. (2) A **Discounts** card directly under "Set budgets", which opens and closes with it, containing the table (models from, billed by, %, from, until, note, set by), an add row, and two collapsed sections: "How Claude apps are billed" and "Let other tools set discounts". "Not now" hides the notice only while there are no discounts; the card stays reachable from Set budgets, the ⌘K command "Spend · Discounts", and `#spend-discounts`. |
| `/health` | Adds `pricing_revision` so callers can confirm a change has been applied. |

## 6. Local API for other tools

The API is a general, documented interface for any tool. Nothing in it is specific
to one company or service. The server listens only on `127.0.0.1:8722`, so a tool
must run on the same computer, or reach it through its own agent on that computer.
Remote exposure is out of scope.

Each caller identifies itself with a short client name, so Settings can show who
set each discount:

- send the header `X-Token-Meter-Client: contract-sync` (≤40 chars,
  `[A-Za-z0-9._ -]`);
- a missing name shows as "Another tool";
- the name is a display label only, never an authorization.

### 6.1 Authentication

- **API token.** A stable token is stored at `~/.token-meter/api-token`
  (permissions `0600`). It's created on first start, reused across restarts and
  updates, and rotated with `POST /api/v1/api-token/rotate`. Send it as
  `Authorization: Bearer <token>`.
- The dashboard keeps using its per-process action token
  (`X-Token-Meter-Action`). Both are accepted on the discount endpoints only.
- **Host check:** the `Host` header must be `127.0.0.1:8722` or `localhost:8722`
  (blocks DNS rebinding). A browser `Origin`, if present, must be loopback.
- The token never appears in any GET payload, log, MCP output, or menu-bar payload.

### 6.2 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/cost-discounts` | All rules, current revision, the rate active now per price list and channel, and the allowed enums |
| `POST` | `/api/v1/cost-discounts` | Atomic batch of `upsert` / `delete` / `replace_scope` operations |
| `GET` | `/api/v1/cost-discounts/resolve?price_list=anthropic&channel=bedrock&at=2026-10-01T12:00:00Z` | Dry-run: which rule (if any) applies at a time |
| `POST` | `/api/v1/api-token/rotate` | Issue a new API token; the old one stops working immediately |

### 6.3 Rule object

```json
{
  "id": "d_3k9x2m1q",
  "external_id": "sync:anthropic-team-plan",
  "price_list": "anthropic",
  "channel": "direct",
  "percent": 30,
  "start": "2026-09-01T00:00:00-07:00",
  "end": null,
  "label": "Claude team plan",
  "source": "api",
  "set_by": "contract-sync",
  "updated_at": "2026-10-29T18:04:11Z"
}
```

| Field | Type | Rules |
|---|---|---|
| `id` | string | Server-assigned, read-only |
| `external_id` | string, optional | Caller key for idempotent upserts. ≤64 chars `[A-Za-z0-9._:-]`, unique |
| `price_list` | enum | `anthropic`, `openai`, `cursor`, `kiro`, `opencode` |
| `channel` | enum | `direct`, `bedrock`, `vertex`, `azure`, `any`. Must be `any` for `cursor`, `kiro`, `opencode` |
| `percent` | number | `0 < percent < 100`, stored to 2 decimals. To remove a rule, delete it |
| `start`, `end` | string or number or null | ISO-8601 **with offset**, or Unix seconds. `null` = open. `start < end` when both are set. Naive dates are rejected |
| `label` | string, optional | ≤60 printable characters, shown in Settings |
| `source` | enum | Set by the server: `api` or `dashboard` |
| `set_by` | string | Set by the server: "Settings", the caller's `X-Token-Meter-Client` name, or "Another tool" |
| `updated_at` | string | Set by the server |

Limits: 64 rules per (price list, channel), 256 total, 64 operations per request,
and an 8 KB request body.

### 6.4 Writing

`POST /api/v1/cost-discounts`

```json
{
  "expected_revision": "r_5f2c…",
  "ops": [
    {"op": "upsert", "rule": {"external_id": "sync:anthropic-team-plan",
      "price_list": "anthropic", "channel": "direct", "percent": 30,
      "start": "2026-09-01T00:00:00-07:00", "end": null,
      "label": "Claude team plan"}},
    {"op": "delete", "external_id": "sync:old-openai"},
    {"op": "replace_scope", "price_list": "openai", "channel": "any",
      "rules": [{"external_id": "sync:openai-ea", "percent": 62,
                 "start": null, "end": null, "label": "OpenAI EA"}]}
  ]
}
```

- **Atomic.** All operations apply, or none do.
- **`upsert`** matches on `id` or `external_id`. Sending the same body twice
  causes no change and no recalculation (`changed: false`).
- **`delete`** by `id` or `external_id`. Deleting a missing rule is a no-op, not
  an error.
- **`replace_scope`** makes the caller the owner of one (price list, channel)
  schedule. Other price lists and channels are untouched; every rule in that one
  scope that is not listed is removed, including ones added on the Spend page.
- **`expected_revision`** (optional) gives optimistic concurrency: if someone else
  changed discounts since the caller last read them, the write is rejected with 409.

Response `200`:

```json
{
  "ok": true,
  "changed": true,
  "revision": "r_8a17…",
  "rules": [ … ],
  "recalculation": "complete"
}
```

`recalculation` is `complete` when costs were repriced before responding, or
`scheduled` when a large history is repriced in the background. In that case, poll
`GET /health` until `pricing_revision` equals `revision`.

### 6.5 Errors

| Status | When | Body |
|---|---|---|
| 400 | Validation error (bad enum, percent, date, overlap) | `{"ok": false, "error": "Anthropic · direct already has a discount from 2026-09-01; ranges may not overlap."}` |
| 401 | Missing or wrong token | `{"ok": false, "error": "Unauthorized."}` |
| 403 | Bad `Host` or non-loopback `Origin` | `{"ok": false, "error": "Forbidden."}` |
| 409 | `expected_revision` is stale | `{"ok": false, "error": "Discounts changed; re-read and retry.", "revision": "r_…"}` |
| 413 | Body over 8 KB | `{"ok": false, "error": "Request too large."}` |

Errors never include local paths or raw exception text.

### 6.6 Examples

```bash
TOKEN=$(cat ~/.token-meter/api-token)
```

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8722/api/v1/cost-discounts
```

```bash
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "X-Token-Meter-Client: contract-sync" -H "Content-Type: application/json" \
  -d '{"ops":[{"op":"upsert","rule":{"external_id":"sync:anthropic-team-plan","price_list":"anthropic","channel":"direct","percent":30,"start":"2026-09-01T00:00:00-07:00","end":null,"label":"Claude team plan"}}]}' \
  http://127.0.0.1:8722/api/v1/cost-discounts
```

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8722/api/v1/cost-discounts/resolve?price_list=anthropic&channel=bedrock&at=2026-10-01T12:00:00Z"
```

### 6.7 Recommended sync loop for tools

1. Read `GET /api/v1/cost-discounts` and keep `revision`.
2. Build the desired schedule for each scope the tool owns. Send one `replace_scope`
   per scope with `expected_revision`.
3. On 409, re-read and retry once. On 401, re-read the token file (it may have
   been rotated).
4. If `recalculation` is `scheduled`, poll `/health` until `pricing_revision`
   matches.
5. Use stable `external_id`s with a tool-specific prefix (for example `sync:`) and
   only `replace_scope` scopes the tool owns. `replace_scope` removes every rule in
   its one scope, including ones a person added on the Spend page; other scopes
   are untouched.
6. Send `X-Token-Meter-Client` on every request so people can see where a
   discount came from.

## 7. Recalculation

Whenever discounts change — through the API, the dashboard, or a direct edit of
`settings.json` — Token Meter:

1. reloads and validates the rules;
2. clears cached session summaries and cross-session state;
3. reprices every request from its trace at its own timestamp;
4. rebuilds Spend, Models, Daily, budgets, goals, and the menu-bar payload;
5. publishes the new `pricing_revision` on `/health`.

No dollar figure is persisted anywhere else (the Work, Git, and pace caches store
no USD), so nothing else can go stale. An unchanged write skips recalculation.

## 8. Defaults and persistence

| Situation | Behaviour |
|---|---|
| Fresh install, no settings file | No rules, so 0% everywhere; aggregate totals are unchanged, and session, model, and execution rows add `discount_usd: 0` |
| Discount set, then update or `./scripts/install` re-run | Kept. Settings live in `~/.token-meter/settings.json`, outside the staged runtime; no installer, updater, or uninstaller reads, writes, or deletes it |
| Uninstall, then reinstall | Kept; the uninstaller leaves `~/.token-meter/` in place |
| Another setting saved at the same moment | Kept. All settings writers will share one file lock and one read-modify-write path. Today, separate features use separate locks, so two simultaneous saves could otherwise drop each other's keys |
| Settings file edited by hand with an invalid rule | That rule is ignored for pricing and counted as invalid on the Discounts card; valid rules still apply. Reading never rewrites the file. On the next save, a malformed object entry (bad type, enum, date, or percent) is kept verbatim, while non-object, duplicate, clashing, and over-limit entries are discarded. A file whose `version` is anything other than 1 is read-only: it is read for what this build understands, and every write returns 409 |
| Older Token Meter reads a newer file | Unknown keys are preserved on write (read-modify-write) |
| Token Meter never writes a default | The `cost_discounts` key appears only after the first save. Absence means 0% |

Tests will pin these: an installer and updater source check that the settings
file is never referenced; a reinstall test that the rules survive; a concurrent
write test across two settings features; and a test that the loader never seeds
defaults.

## 9. Privacy and security

- Nothing leaves the machine. The API is loopback-only and Host-checked.
- Stored rules contain only enums, numbers, dates, and a short label. No account
  ids, ARNs, or credentials are stored. The Bedrock account number is never stored
  or projected.
- The API token file is `0600` and excluded from every payload and log.
- Channel inference reads only the *shape* of a request id (`req_` prefix vs
  UUID), never its value.

## 10. Out of scope (v1)

- Per-model discounts.
- Tiered or volume pricing.
- Currency conversion.
- Remote / non-loopback access.
- MCP writes.
- Discounting billed amounts.
- Detecting Cursor BYOK.
- Menu-bar copy changes.

## 11. Open questions

1. Confirm the `req_` request-id shape on a direct Anthropic API or subscription
   trace before enabling Claude channel auto-detection. Until confirmed, Claude
   Code and Desktop default to the manual channel setting.
2. Should tools also be able to set the Claude Code and Desktop channel
   declaration over the API?
3. Per-model discounts (use case 21): when should we add them?
