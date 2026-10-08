# Goals

Goals live in Efficiency at `#efficiency-goals`; `#goals` redirects there.
Spend and Git show current goals for their metric, and the macOS menu bar and
Linux tray show up to three. All measurement is deterministic and local; nothing here is an AI
judgment. Implementation: `token_meter/services/goals.py`.

## One goal form

| Step | Choices |
|---|---|
| Scope | All agents, one agent, or one of your main models: the runtime scoped models with spend in the last 60 days, highest first, up to 15 plus any a saved goal uses. Cost per 1K pushed lines is measured across all projects only, because a push cannot be attributed to an agent or model. Frontier share is set for all agents or one agent, since one model is always 0% or 100%. |
| Metric | One of the six below. |
| Period | This week (Monday to Sunday), This month, or custom dates up to 366 days, on the local calendar. Weekly and monthly goals can repeat. |
| Target | A prefilled option relative to the previous period, or Custom (At most or At least plus a number). Nothing is preselected, except that Output / $ only allows At least and Cost per 1K pushed lines only allows At most, since the other direction would reward the wrong outcome. |

Targets start from a current value, taken from the first source with evidence:
the previous calendar week or month (or the preceding span of equal length for
custom dates), then the last 90 days, then this period so far. The form and cards
name the source, and each option shows the change, such as "$2.69 → $2.42 / 1K
lines". Values are recomputed with current prices whenever goals are shown, so a
price change does not look like an efficiency gain. Spend is scaled to the goal's
period length, so a so far value is a projection. Without any of the three, only
Custom is offered. For spend, the form explains the three spend controls
(session caps, monthly budgets, goals) and names any monthly budget that already
covers the same scope.

## Metrics

Every metric sums numerators and denominators across the whole period. Daily
ratios are never averaged, and missing evidence never becomes zero.

| Metric | Definition | Options |
|---|---|---|
| Spend | Dated observed cost in scope. | Cut 10%, Cut 20%, Hold current level |
| Output / $ | Cost covered output tokens / paired covered cost, as on Efficiency. Targets are At least. | Improve 10%, 15%, 25% |
| Cost per 1K pushed lines | 1,000 × covered cost / added plus deleted pushed lines, over the Git repositories your sessions ran in that have both pushes and cost (the same number the Git page shows). The form and card name those repositories and the share of all agent spend they cover; sessions started outside a repository folder do not count. Requires 50 lines. Delivery evidence, not a quality score. Targets are At most. | Cut 10%, Cut 20%, Hold current level |
| Context load | Input / output tokens over executions with both counts. | Lower 10%, Lower 20%, Raise 10% |
| Reasoning ratio | Reported reasoning tokens / reasoning covered output, as a percentage. Sessions that never report a reasoning split (for example Claude, which only shows thinking) are left out and counted, so they cannot hold a goal at No result. | Lower 5 points, Lower 10 points, Raise 5 points |
| Frontier share | Spend on frontier tier models / all spend in scope, as a percentage. Tiers come from `token_meter/models/tiers.py`; unrecognized models stay in the denominator and are shown separately. | Lower 5 points, Lower 10 points, Raise 5 points |

## Status

| Status | Rule |
|---|---|
| On track | Open period; projected spend, or the current ratio, is on the target side. |
| At risk | Open period; that value is on the wrong side. |
| Met | Ended period with complete evidence and the target achieved. |
| Missed | Ended period with complete evidence and the target not achieved. Known spend above an At most spend target is Missed immediately. |
| No result | No data yet, incomplete evidence, running sessions at period end, fewer than 50 pushed lines, stale Git history, or unrecognized model spend that could flip a Frontier share result. |

Goals in the first fifth of their period are marked Early. Ending a goal
manually moves it to Previous goals without a verdict. Confidence is shown as
Complete data, Partial data, or Estimated; Estimated follows the Spend page rule
(estimated token counts), not the local pricing every session uses.

## Card details

Each card shows a progress bar (with a projected marker for open spend goals), a
value to date trend line against the target, the previous period, and a Watch
line: the guardrail metric that would expose an unhelpful win. Spend goals watch
Output / $; every other metric watches Spend. A repeating goal rolls into the
current week or month and lists up to six earlier periods with their result;
editing it keeps that history.

## Detail view and coaching

Selecting a goal opens a full width view at `#efficiency-goals/<id>` with Back
and Edit. It shows the current value, target, projection, and reference value;
the value to date for each day against the target; what drove it (spend share by
model, reasoning effort, and agent, plus cache hits; repositories for Cost per
1K pushed lines); earlier periods for repeating goals; and the five sessions that
moved the number most, linked to their session pages. Contributors explain a
goal; they never define its scope.

Coaching is deterministic (`token_meter/services/goal_coaching.py`) and reads
only these measurements. Each tip states its evidence and one lever the user
controls:

| Rule | Fires when | Lever | Estimate |
|---|---|---|---|
| Model mix | Frontier models are 30% or more of the spend | A cheaper model the user already uses, for routine work | Half of the top frontier model's spend moved to that model, at the user's own cost per 1M output tokens |
| Reasoning effort | High effort or above is 30% or more of spend with a reported effort | Medium effort for routine work | Half of that model's high effort spend at the user's own medium cost per execution |
| Concentration | The top three sessions are 40% or more, among more than three | Split long tasks, start fresh, `/compact` | None |
| Cache | Under 50% of processed input came from cache | Keep related work in one continuous session | None |
| Coverage | Under 50% of agent spend ran in repositories with pushes | Start sessions inside the repository folder | None |
| Repositories | One repository costs at least twice another per 1K lines | Compare how work differs there | None |

Estimates need at least 20 executions on both sides, cover the period so far,
are labeled upper bounds that assume the cheaper option could do the work, and
can overlap, so the view says not to add them together. When no rule fires the
view says that no single lever stands out.

## Starters

Cut Codex spend 10%, Improve Claude Output / $, and Cut cost per 1K pushed
lines 10% use This month and last month as the baseline. **Add** saves the starter in
one click with an Undo notice; **Customize** opens it in the form. When the named
agent has no usable baseline, the starter switches to the highest spend agent
that has one; otherwise it shows why it is unavailable.

## Records and privacy

Goals are stored under `goals.items` in local settings through the locked
atomic settings write; at most 50 are kept. Records from the earlier prototype
(business objectives, Model Mix, daily and model limits) are counted and
dropped on the next save; reading never fails because of them. Monthly budgets
remain in Settings; a monthly spend goal only shows the matching allocation as
a reference. Model goals store an opaque key for the runtime and model, never the model
string, and labels hide Bedrock ARNs. The menu bar payload contains goal IDs,
metric and runtime IDs, model labels, numbers, statuses, and dates, but no titles. The watcher
rebuilds it at most once a minute while sessions are live, from already
published evidence; native polls never trigger a rebuild. Goals are not
exposed through MCP.
