# Goals

Goals live in Efficiency at `#efficiency-goals`; `#goals` redirects there.
Spend and Git show current goals for their metric, and the macOS menu bar and
Linux tray show up to three. All measurement is deterministic and local; nothing here is an AI
judgment. Implementation: `token_meter/services/goals.py`.

## One goal form

| Step | Choices |
|---|---|
| App and Model | All apps and All models by default. App selects the agent runtime, using the same labels as the rest of the dashboard. The checkbox picker supports up to 32 runtime-scoped models, filtered before ranking by recent spend, including observed models without prices. Search reaches models beyond the 15-choice shortlist; selected choices stay checked across searches. All apps permits a selection across runtimes. Changing App removes incompatible selections with an inline notice; selecting models does not change App. Empty selection means All models. Cost per 1K pushed lines fixes both to All; Frontier share fixes Model to All. |
| Metric | One of the seven below. |
| Period | Today, This week (Monday to Sunday), This month, Next 7/30/90 days, or Custom dates up to 366 days, on the local calendar. Calendar weekly/monthly goals can repeat. Today and fixed-span shortcuts store explicit custom dates; editing displays those saved dates without shifting the deadline. |
| Target | A prefilled option relative to the previous period, or Custom (At most or At least plus a number). Nothing is preselected, except that Output / $ only allows At least and Cost per 1K pushed lines only allows At most, since the other direction would reward the wrong outcome. |

Targets start from a current value, taken from the first source with evidence:
the previous calendar week or month (or the preceding span of equal length for
custom dates), then the last 90 days, then this period so far. The form and cards
name the source, and each option shows the change, such as "$2.69 → $2.42 / 1K
lines". Values are recomputed with current prices whenever goals are shown, so a
price change does not look like an efficiency gain. Spend and Tokens are scaled to the goal's
period length, so a so far value is a projection. Without any of the three, only
Custom is offered. For spend, the form explains the three spend controls
(session caps, monthly budgets, goals) and names any monthly budget that already
covers the same scope.

Hold current level is available for every metric with a usable baseline,
including a measured zero. It means At least for Output / $ and At most for the
other metrics; the option spells out the direction and value. Custom retains
the supported alternative directions. Absent evidence does not become a zero
baseline. Token presets round to the nearest whole token, with halves rounded
up; the preview and saved target use that same number.

## Metrics

Every metric sums numerators and denominators across the whole period. Daily
ratios are never averaged, and missing evidence never becomes zero.

| Metric | Definition | Options |
|---|---|---|
| Spend | Dated observed cost in scope. | Cut 10%, Cut 20%, Hold current level |
| Tokens used | Dated normalized processed input plus output. Input includes cache once; reasoning is already included in output. Counts available tokens even when prices are missing; incomplete I/O coverage is disclosed. Usage volume, not a quality or productivity score. | Cut 10%, Cut 20%, Hold current level, or Custom At most/At least |
| Output / $ | Cost covered output tokens / paired covered cost, as on Efficiency. Targets are At least. | Improve 10%, 15%, 25%, Hold current level |
| Cost per 1K pushed lines | 1,000 × covered cost / added plus deleted pushed lines, over the Git repositories your sessions ran in that have both measured Git coverage and cost (the same calculation as the Git page for matching dates). The form and card name those repositories and the share of all agent spend they cover; sessions started outside a repository folder do not count. Requires 50 lines. Delivery evidence, not a quality score. Targets are At most. | Cut 10%, Cut 20%, Hold current level |
| Context load | Input / output tokens over executions with both counts. | Lower 10%, Lower 20%, Raise 10%, Hold current level |
| Reasoning ratio | Reported reasoning tokens / reasoning covered output, as a percentage. Sessions that never report a reasoning split (for example Claude, which only shows thinking) are left out and counted, so they cannot hold a goal at No result. | Lower 5 points, Lower 10 points, Raise 5 points, Hold current level |
| Frontier share | Spend on frontier tier models / all spend in scope, as a percentage. Tiers come from `token_meter/models/tiers.py`; unrecognized models stay in the denominator and are shown separately. | Lower 5 points, Lower 10 points, Raise 5 points, Hold current level |
| Pushback (Work) | For one kind of work or all kinds (every request, matching the Work page's All work row), for all models or chosen models: labeled follow-ups where the next message pushed back / all labeled follow-ups, as a percentage. Needs Work insights; labels are local-model estimates, and a result needs 20 labeled follow-ups. Targets are At most. | Lower 5 points, Lower 10 points, Hold current level |
| Agent : human (Work) | For one kind of work or all kinds, for all models or chosen models: model calls / typed requests, over requests whose app splits model calls by request. The target is a range (between a low and a high value). It counts only while pushback stays under its Pushback goal: the latest one not ended with the same kind of work and models whose dates overlap. Needs 20 human turns. | Around current level (±20%), Raise 10–40%, Lower 10–40%, or a Custom range |

Custom token targets use whole counts through 9,007,199,254,740,991. Enter
`1000000000` for one billion; a live K/M/B preview and exact count explain the
input. At most tracks a usage cap; At least tracks a usage milestone. Cards,
detail, and existing native Goals summaries use compact token formatting.

## Status

| Status | Rule |
|---|---|
| On track | Open period; projected cumulative spend/tokens, or the current ratio, is on the target side. |
| At risk | Open period; that value is on the wrong side. |
| Met | Ended period with complete evidence and the target achieved. |
| Missed | Ended period with complete evidence and the target not achieved. Known cumulative spend/tokens above an At most target is Missed immediately. |
| No result | No data yet, incomplete evidence, running sessions at period end, fewer than 50 pushed lines, stale Git history, unrecognized model spend that could flip a Frontier share result, or an Agent : human range with no Pushback goal for the same kind of work and models (or too few labeled follow-ups to judge that pushback). |

An Agent : human range is At risk (open) or Missed (ended) when it is outside its range ("Below the range",
"Above the range") or when pushback for its kind of work is above its Pushback goal.

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

Previous goals is available beside New goal, with Current/Previous controls in
the same workspace. Expired goals show their evidence-aware verdict; manually
ended goals show Ended. Duplicate opens an unsaved new form with the copied
absolute target selected as Custom. Calendar goals use the current week/month;
custom goals copy their duration starting today. Cancel writes nothing, and
Save assigns a new ID without changing the source record. Values/history are
recomputed from current evidence, pricing, and goal settings rather than stored
as immutable completion snapshots.

## Detail view and coaching

Selecting a goal opens a full width view at `#efficiency-goals/<id>` with Back
and Edit. It shows the current value, target, projection, and reference value;
the cumulative value to date for each day against the target, with hover and keyboard inspection (arrow keys, Home, End); what drove it (spend share by
model, reasoning effort, and agent, plus cache hits; repositories for Cost per
1K pushed lines); earlier periods for repeating goals; and the five sessions that
moved the number most, linked to their session pages. Delivery goals instead show
repository contributors ranked by covered spend, with Spend, Lines pushed, Cost
per 1K pushed lines, and Spend share. Lines pushed counts additions plus deletions;
all included repositories contribute to the table totals, beyond the five-row preview. Contributors explain a
goal; they never define its scope.

Delivery detail shows the top eight repositories by spend and identifies how
many additional comparable repositories contribute to the measurement. All projects
includes every eligible repository discovered from local agent activity, up to the
existing 50-repository discovery limit; it does not inventory every repository on
disk. The included/discovered count, covered spend share, and known global scan
limits explain coverage. Distinct repositories retain their safe identifying suffixes. Its
Repository evidence disclosure uses Git's Comparable, Spend only, Git only,
and No period evidence categories for the exact goal window, including stale
and partial evidence. Membership does not depend on meeting the goal target.
Measured zero lines differ from unavailable history. Open Git uses the existing
Git route and its own history selection; it does not transfer custom goal dates.
Charts preserve missing evidence gaps and show No data rather than interpolating
unavailable values. The tooltip includes the date, to-date value, and target.

Multi-model goals sum each selected model's evidence once before calculating a
ratio. Same-name models in different runtimes stay distinct; unrelated models in
a mixed session are excluded. Existing single-model goals remain readable. Saved
selections whose local evidence disappears remain saved and show No result,
without silently broadening the selection. Native summaries identify N models.

Goals reuses the watcher-published repository candidates and ledger, without
triggering scans or fresh repository discovery from a Goals request.

Token detail ranks models/apps and sessions by their known tokens, including
unpriced usage, and names that basis explicitly. Existing cost-based coaching
inputs retain cost-based shares. Tokens currently has no additional coaching
rules; Spend remains its guardrail.

Coaching is deterministic (`token_meter/services/goal_coaching.py`) and reads
only these measurements. Each tip states its evidence and one lever the user
controls:

| Rule | Fires when | Lever | Estimate |
|---|---|---|---|
| Model mix | Frontier models are 30% or more of the spend | A cheaper model the user already uses, for routine work | Half of the top frontier model's spend moved to that model, at the user's own cost per 1M output tokens |
| Reasoning effort | High effort or above is 30% or more of spend with a reported effort | Medium effort for routine work | Half of that model's high effort spend at the user's own medium cost per execution |
| Concentration | The top three sessions are 40% or more, among more than three | Split long tasks, start fresh, `/compact` | None |
| Cache | Under 50% of processed input came from cache | Keep related work in one continuous session | None |
| Coverage | Under 50% of agent spend has repository attribution and measured Git coverage | Start sessions inside the repository folder | None |
| Repositories | One repository costs at least twice another per 1K lines | Compare how work differs there | None |

Estimates need at least 20 executions on both sides, cover the period so far,
are labeled upper bounds that assume the cheaper option could do the work, and
can overlap, so the view says not to add them together. When no rule fires the
view says that no single lever stands out.

## Pages and default goals

Each goal shows only on its page: spend and token goals on Spend, cost per 1K
pushed lines on Git, Pushback and Agent : human on Work, and the ratio metrics on Efficiency. Work goals have no
session drivers or contributors in their detail view, and no default goal. Each page's New goal
offers only that page's metrics.

There are no suggestions. Instead, the dashboard sends one action-token
`seed` action; it adds monthly, repeating, all-apps defaults that hold last
month's level: Spend at most last month, cost per 1K lines at most last month,
and Output / $ at least and Context load at most last month. Each default is
added at most once and recorded in `goals.seeded`, so a deleted default never
returns. A page that already has any goal is marked seeded without adding one,
and a default without a usable baseline waits until one exists. A seed that
changes nothing does not write settings.

## Records and privacy

Goals are stored under `goals.items` (with `goals.seeded`) in local settings through the locked
atomic settings write; at most 50 are kept. Records from the earlier prototype
(business objectives, Model Mix, daily and model limits) are counted and
dropped on the next save; reading never fails because of them. Monthly budgets
remain in Settings; a monthly spend goal only shows the matching allocation as
a reference. Model goals store an opaque key for the runtime and model; multi-model scopes
store up to 32 sorted, deduplicated opaque keys. Neither stores model strings, and labels hide Bedrock ARNs. The menu bar payload contains goal IDs,
metric and runtime IDs, model labels, numbers, statuses, and dates, but no titles. The watcher
rebuilds it at most once a minute while sessions are live, from already
published evidence; native polls never trigger a rebuild. Goals are not
exposed through MCP.
