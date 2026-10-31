# Cost Estimates and Agent Budgets

## Are these numbers what we actually pay?

No. Token Meter shows **estimates**, not invoices.

Token Meter reads the session logs that coding agents (Claude Code, Codex,
Cursor, and others) write on your machine. For each request it takes the
recorded token counts (input, output, cache writes, cache reads) and multiplies
them by the **public list price** for that model. It never connects to a billing
system, so it cannot see contract rates, enterprise discounts, committed-spend
agreements, gateway or Bedrock pricing, or seat-based subscriptions on its own.

Read the cost as "what this local usage would cost at public rates", unless you
add your negotiated rates as [discounts](#discounts-show-what-you-actually-pay).
Then it reads as "what this usage costs at your rates".

## Why it differs from other tools

- **Scope.** Token Meter sees only local sessions on the machine it runs on.
  Admin consoles and gateway dashboards include every device, web and cloud
  sessions, CI, and other users.
- **Usage outside local coding sessions.** Claude.ai web and desktop chats, API
  calls from your own scripts or services, cloud and CI agents, and separate MCP
  or tool-provider charges do not appear in local session logs.
- **Cache pricing.** Coding sessions resend a lot of context, and most of it is
  billed as cache reads at a fraction of the input price. Tools that price all
  input at the full rate, or omit cache tokens, report very different totals.
- **Subagents.** Child-agent runs are included in the session total. Some tools
  omit them or show them separately.
- **Billing extras.** Web search ($10 per 1,000 searches), 1-hour cache writes,
  fast mode, and US-only data residency are priced when the local logs record
  them. A request that uses a model or pricing option Token Meter does not
  recognize is left out of the dollar total rather than guessed, so the total
  can read low.
- **Time windows.** Time zones, date cutoffs, and billing delays shift totals.

## What it is reliable for

Token counts come directly from local logs, and the pricing math is checked
against published rates. Use Token Meter to compare sessions, models, and
trends on your own machine: which workflows consume the most context, whether
caching is helping, and how spend changes over time. For actual spend, use the
provider's billing or admin console.

## Discounts: show what you actually pay

If you or your company pay less than list price, add a discount and every cost
estimate uses it. The default is no discount (0%), and a saved discount is kept
through updates and reinstalls.

### Where

**Spend → Monthly budget.** A notice in the budget card reads "Spend uses public
list prices" until a discount exists; **Add a discount** opens the **Discounts**
card under Set budgets. You can also open it with ⌘K → "Spend · Discounts" or
the `#spend-discounts` link. Another tool on the same computer can manage
discounts through the [local API](LOCAL-API.md).

### What a discount is

A discount is a percentage (more than 0% and less than 100%) for one
combination of:

- **Models from**, the price list the usage was estimated from: Anthropic,
  OpenAI, Cursor, Kiro, or OpenCode.
- **Billed by**, who invoices you: the provider itself (including Claude and
  ChatGPT plans), AWS Bedrock, Google Vertex, Microsoft Azure, or Anyone.
  Cursor, Kiro, and OpenCode always use Anyone.
- **From** and **Until** dates. Leave From blank for all past usage and Until
  blank for no end. From is included; Until is not.

Two discounts for the same "models from" and "billed by" can't overlap in time.
A renewal at a new rate is a new discount that starts the day the old one ends.

### How a request is matched

Token Meter applies discounts to each request individually, at that request's
own time. A request with no recorded time is treated as dated 1 January 1970, so
only a discount with no start date applies to it; a missing time never picks up
today's rate. With no discounts, aggregate totals are unchanged; session, model,
and execution rows add `discount_usd: 0`.

1. If the cost is an amount the provider actually billed (for example a Hermes
   `actual_cost_usd`), or the cost is unavailable, it's left alone.
2. Otherwise it uses the discount for the request's price list and exact
   "billed by", if one is active then.
3. Otherwise it uses that price list's "Anyone" discount.
4. Otherwise it uses list price.

**Billed by is detected per runtime:**

- Pi, OpenCode, and Hermes record the provider.
- Codex records `model_provider`: `openai` means the provider, `azure` means
  Azure.
- Claude Code and Claude Desktop don't say. Token Meter infers it from the shape
  of the session's request IDs: `req_vrtx_…` means Google Vertex, other `req_…`
  IDs mean Anthropic, and a UUID means AWS Bedrock. A session with no request ID matches only "Anyone" discounts. If the
  inference is wrong, set it under **How Claude apps are billed**.
- Antigravity doesn't record who bills you, so its requests match only "Anyone"
  discounts.

**The price list is the one actually used:**

- Cursor and Kiro set their own prices, so an Anthropic or OpenAI discount never
  applies to models used through them.
- OpenCode's own gateways (Zen, Go) use only an OpenCode discount.
- OpenCode, Pi, and Hermes vendor calls use that vendor's discount, because their
  recorded costs are list-price estimates.
- Antigravity requests on Claude or OpenAI models use that vendor's discount.
  There is no Google price list yet, so Gemini requests stay at list price.

**Discounts are always applied last:** after fast mode, long context, US-only
residency, 1-hour cache writes, web search, and any manual model-price override.
They also apply to the "cache saved you" figure and the prompt-cache re-send
estimate (below). Subagent runs are discounted once, as part of their own
requests.

### What changes

- Every cost (Sessions, Spend, Models, Compare, Work, Git, the menu bar, and
  MCP) shows the discounted figure. When a period includes a discount, the Spend
  totals read **After discounts · list $X**. MCP adds `discount_usd`, so
  `cost_usd + discount_usd` is the list-price estimate.
- Monthly budgets, session caps, and goals are measured against the discounted
  cost, which is what you pay.
- Antigravity costs estimated from transcript length (labelled `est`) run well
  below real cost. The monthly budget still shows them, but budget alerts are
  measured without them, and the budget card says how much was left out.
  Measured Antigravity costs count toward alerts as usual.
- Adding, editing, or removing a discount reprices all history in the
  background (about 20 seconds for a large history). The previous figures stay
  on screen until the new ones are ready. A discount with a past start date
  changes past months too; budget alerts already sent are not withdrawn.

### Examples

| Setup | Discount |
|---|---|
| A discounted Claude team plan from 1 September | Anthropic · the provider · 30% · from Sep 1 |
| An OpenAI enterprise agreement, including Azure | OpenAI · Anyone · 62% |
| Claude bought both through Bedrock and directly, on different contracts | Anthropic · AWS Bedrock · N%, plus Anthropic · the provider · M% |
| No negotiated rate | Nothing; costs stay at list price |

### Limits

- One percentage per "models from" and "billed by"; there are no per-model,
  tiered, or volume rates.
- Cursor used with your own API key looks like Cursor billing.
- Models from vendors outside the five price lists (for example Google or Amazon
  Nova used through Pi or OpenCode) are not discounted. OpenAI's open-weight
  `gpt-oss` models are priced by their host, so OpenAI discounts don't apply to them.
- A Pi or OpenCode `openai-codex` provider (a ChatGPT plan) counts as billed by
  OpenAI.

## Prompt-cache countdown

Session cards and the menu bar show how long the latest request's prompt cache
stays warm, and roughly what the next request would cost extra if it arrives
after the cache expires.

- **How long the cache stays warm:** the window is the cache lifetime the trace
  recorded (5 minutes or 1 hour). If no lifetime was recorded, Token Meter uses
  the provider's documented typical window and labels it "about". It reads
  "Cache may have expired" or "Cache likely expired" once that time has passed.
- **The extra cost:** this is the list-price difference between resending the
  cached prefix as cache writes or fresh input, and reading it from cache. It uses
  the same model, speed, and routing as the latest request.
- **With discounts:** the extra cost is discounted under the same price list and
  billing channel as that session's own requests, so it never disagrees with the
  session's cost. A cache window outside a discount's dates uses list price.

## Converting credits into agent budgets

Token Meter budgets are monthly US-dollar amounts set per agent (runtime). If
your allowance is issued as credits, convert each credit pool to dollars and add
them up for that agent:

```text
agent budget ($) = Σ (credits in pool × $ per credit)
```

### Example

| Agent | Credit pool | Credits | $ per credit | Dollars |
| --- | --- | ---: | ---: | ---: |
| Claude | Team allocation | 500 | $0.04 | $20.00 |
| Claude | Project top-up | 1,000 | $0.03 | $30.00 |
| **Claude budget** | | | | **$50.00** |
| Codex | Team allocation | 2,000 | $0.01 | $20.00 |
| **Codex budget** | | | | **$20.00** |

The monthly total is the sum of the agent budgets: $70.00.

### Set it in Token Meter

1. Open the dashboard and go to **Spend → Monthly budget**.
2. Choose **Set budgets** (or **Edit budgets** if one is already saved).
3. Enter each agent's dollar amount in its **Budget** field. Leave agents you
   do not use at `0`.
4. Optionally adjust the alert thresholds (80%, 90%, and 100% by default) and
   the default session budget.
5. Choose **Save budgets**. Budgets apply machine-wide and the menu bar uses
   them for alerts.

### Keep in mind

- Budgets are compared against Token Meter's estimate, which is list price
  unless you add a [discount](#discounts-show-what-you-actually-pay). If your
  credit price reflects a negotiated rate, add it as a discount, or the estimate
  will use up the budget faster than your real credit balance does.
- A tool on the same computer can set budgets through the
  [local API](LOCAL-API.md#budgets).
- Budgets reset each calendar month. If credits cover a different period,
  divide by the number of months they cover.
- Spend outside local sessions does not count against these budgets. Check your
  provider's credit balance for the authoritative remaining amount.
