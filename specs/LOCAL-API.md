# Local API for Tools

Token Meter exposes a small, versioned HTTP API so that other tools on the same
computer can manage **discounts** and **monthly budgets**. Examples are a
company contract-sync agent, a provisioning script, or a device-management job.
Everything here can also be done by hand on the Spend page. For how discounts
are applied to costs, see
[Cost estimates and budgets](COST_AND_BUDGETS.md#discounts-show-what-you-actually-pay).

The API is local-only. It never reads prompts, responses, traces, or session
content, and it cannot change anything except discounts, Claude app billing
channels, and budgets.

## Connection and authentication

| Item | Value |
|---|---|
| Base URL | `http://127.0.0.1:8722` (Token Meter binds to loopback only) |
| Host header | must be `127.0.0.1:8722` or `localhost:8722`, otherwise 403 |
| Origin header | if present, must be a loopback origin, otherwise 403 |
| Token | `~/.token-meter/api-token`: created on first server start with mode `0600`, reused across restarts, updates, and reinstalls |
| Auth header | `Authorization: Bearer <token>` (the dashboard's per-process action token is also accepted) |
| Client name | optional `X-Token-Meter-Client: <name>` (≤40 chars `[A-Za-z0-9._ -]`); shown as "Set by" on discounts; anything else shows "Another tool" |
| Body | `Content-Type: application/json`, 1–8,192 bytes |

The token never appears in any GET payload, `/state`, `/health`, `/menubar`, MCP
output, or log. `POST /api/v1/api-token/rotate` (or **Issue a new token** on the
Spend page) replaces it immediately; callers should re-read the file after a 401.
A tool must run as the same user, because the file is readable only by that user.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/cost-discounts` | Discounts, revision, active-now summary, Claude app channels, allowed values, invalid stored rules |
| `POST` | `/api/v1/cost-discounts` | Atomic batch of discount operations |
| `GET` | `/api/v1/cost-discounts/resolve` | Which discount applies to a price list and channel at a time |
| `GET` | `/api/v1/budgets` | Monthly budgets, revision, this month's spend |
| `POST` | `/api/v1/budgets` | Partial budget update |
| `POST` | `/api/v1/api-token/rotate` | Replace the API token |
| `GET` | `/health` | Readiness, including `pricing_revision` and `pricing_recalculating` |

## Discounts

### Rule object

| Field | Type | Rules |
|---|---|---|
| `id` | string | Server-assigned (`d_…`); read-only |
| `external_id` | string, optional | Caller's stable key, ≤64 `[A-Za-z0-9._:-]`, unique; use a tool prefix such as `sync:` |
| `price_list` | enum | `anthropic`, `openai`, `cursor`, `kiro`, `opencode` |
| `channel` | enum | `direct`, `bedrock`, `vertex`, `azure`, `any`. Must be `any` for `cursor`, `kiro`, `opencode` |
| `percent` | number | `0 < percent < 100`, two decimals |
| `start`, `end` | ISO-8601 with an offset or `Z`, Unix seconds, or `null` | `null` = open; the start is included and the end is not; `start < end`; times without an offset are rejected |
| `label` | string, optional | ≤60 printable characters |
| `source`, `set_by`, `updated_at` | — | Server-set |

Ranges for the same (`price_list`, `channel`) may not overlap; touching ranges
are allowed. Limits: 64 rules per (price list, channel), 256 in total.

### Operations

`POST /api/v1/cost-discounts` with `{"expected_revision"?: "r_…", "ops": [...]}`,
at most 64 ops, all applied or none:

| Op | Body | Effect |
|---|---|---|
| `upsert` | `{"op":"upsert","rule":{…}}` | Match by `id` or `external_id` and replace it, or create a new rule when only `external_id` is given. An unknown `id` is rejected |
| `delete` | `{"op":"delete","id":"…"}` or `"external_id"` | Remove; a missing rule is a no-op |
| `replace_scope` | `{"op":"replace_scope","price_list":"…","channel":"…","rules":[…]}` | Make the caller own that one schedule. Other price lists and channels are untouched; every rule in that one scope that is not listed is removed, including ones added on the Spend page |
| `set_runtime_channels` | `{"op":"set_runtime_channels","claude_code":"auto"|"direct"|"bedrock"|"vertex","claude_desktop":…}` | Override Claude app billing detection |

Response: `{"ok":true,"changed":bool,"revision":"r_…","rules":[…],"recalculation":"scheduled"|"complete","pricing_revision":"p_…"}`.
An unchanged write returns `changed:false` and does no work. A changed write
reprices history in the background. The previous figures keep serving until
`/health` reports the returned `pricing_revision`.

`GET /api/v1/cost-discounts/resolve?price_list=anthropic&channel=bedrock&at=2026-11-01T12:00:00Z`
returns `{ok, price_list, channel, at, percent, rule_id}`, with `percent` and
`rule_id` `null` when nothing applies.

## Budgets

`GET /api/v1/budgets` returns:

```json
{"ok": true, "revision": "b_…",
 "budgets": {"currency": "USD", "allocations": {"claude": 300, "codex": 200, "cursor": 0,
             "opencode": 0, "kiro": 0, "pi": 0, "hermes": 0},
             "monthly_total": 500, "thresholds": [80, 90, 100],
             "native_notifications": true, "default_session_budget": 10},
 "runtimes": ["claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes"],
 "limits": {"max_monthly_budget": …, "min_session_budget": …},
 "month": {"month": "2026-10", "spent": 212.4, "list_spent": 351.9, "coverage_partial": false}}
```

`month` is `null` until the first cross-session build exists. `spent` is the
discounted figure budgets are measured against; `list_spent` is the same
spending at list price.

`POST /api/v1/budgets` takes `{"expected_revision"?, "allocations"?, "thresholds"?, "native_notifications"?, "default_session_budget"?}`.
`allocations` is merged per runtime, so runtimes you leave out keep their value.
Unknown fields or runtimes are rejected. Per-session budgets set in the dashboard
are never read or changed through this API. The response is
`{"ok":true,"changed":bool,"revision":"b_…","budgets":{…}}`.

## Errors

| Status | When |
|---|---|
| 400 | Invalid JSON, unknown field, or a validation error (`error` is a plain-English message) |
| 401 | Missing or wrong token |
| 403 | Host not allowed, or non-loopback Origin |
| 409 | `expected_revision` is stale; the body includes the current `revision` |
| 413 | Body over 8,192 bytes |
| 415 | Content type is not JSON |

Error bodies never include local paths or exception text.

## Example

```bash
TOKEN=$(cat ~/.token-meter/api-token)
curl -fsS -X POST http://127.0.0.1:8722/api/v1/cost-discounts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "X-Token-Meter-Client: contract-sync" \
  -d '{"ops":[{"op":"replace_scope","price_list":"openai","channel":"any",
       "rules":[{"external_id":"sync:openai-ea","percent":62,"start":null,"end":null,
                 "label":"Enterprise agreement"}]}]}'
```

## Integration guidance

- Read the token file on every run, and never copy it elsewhere.
- Send `X-Token-Meter-Client` so people can see where a discount came from.
- Use `replace_scope` only for a scope the tool owns, with prefixed `external_id`s.
  Other price lists and channels are untouched, but `replace_scope` removes every
  rule in its one scope, including ones a person added on the Spend page. Use
  `upsert` and `delete` to change single rules in a shared scope.
- Unknown fields, at the top level or in an operation, are rejected with 400
  `Unknown field: <name>`. Rule dates must fall between 1970 and 2100.
- A file written by a newer Token Meter (`version` above 1) is read but never
  rewritten; writes return 409 `Discounts were saved by a newer Token Meter.`
- Send `expected_revision` from a fresh `GET`. On 409, re-read and retry once.
- Treat connection refused as "Token Meter not running" and retry later.
