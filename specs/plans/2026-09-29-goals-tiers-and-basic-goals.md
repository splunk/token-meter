# Model tiers and basic Goals: implementation plan

Historical plan. The September 29 implementation decision removed the Monthly AI spend goal from the Goals catalog because Settings already owns the recurring monthly budget. It added Daily spend ceiling and Selected model spend limit, retained Model Mix, and renamed the objective type Spend limit for business objective. Cache reuse remains a diagnostic, not a user goal.

Status: Model Mix by tier is authorized and being implemented. The two basic
spend goals remain proposed. The existing Goals worktree is uncommitted.

## Outcome

Make Model Mix a workload-aware goal for one agent: the user chooses a date
range and a target share of **Frontier**, **Mid-range**, and **Efficient** model
executions. Show **Unclassified** separately. Add simple agent-scoped spend
goals to Create new goal, while keeping the recurring machine-wide monthly
budget controls in Settings.

## Goals shown in the catalog

| Goal | User chooses | Deterministic measure | Completion rule |
| --- | --- | --- | --- |
| AI spend for a business objective (existing) | Objective, agents, lifetime cap, linked whole sessions, manual or date end | Sum of linked session cost and cost coverage | Manual delivery and link confirmation plus full cost coverage and spend strictly under cap; a date end alone does not assert delivery. |
| Spend within a limit (new) | One agent, USD cap, day/week/two-week/month preset or custom dates | Sum of that agent's recorded daily cost in the inclusive range | At end, observed cost is strictly under cap and all relevant sessions have complete cost evidence. An observed amount at or above cap is a breach even with incomplete evidence. |
| Session spend ceiling (new) | One agent, USD ceiling per session, time range | Highest whole-session cost among sessions whose last activity is in range; number over ceiling | At end, at least one eligible session exists, all eligible sessions are complete and cost-covered, and each costs strictly less than the ceiling. A session with known cost at or above the ceiling is a breach. |
| Model Mix by tier (new creation mode) | One agent, time range, workload note, Frontier/Mid-range/Efficient target percentages, tolerance in percentage points | Each tier's share of all observed model executions in the range; show Unclassified share | At end, at least one execution exists, no active or unclassified executions remain, and each measured share is within the chosen tolerance of its target. |
| Monthly AI spend goal (existing Settings control) | Recurring per-agent monthly allocation in Settings | Existing calendar-month spend and coverage | Link from the Goals catalog to Settings; do not create a second allocation record or overwrite it from a one-time goal. |

The workload note is user-authored planning context, such as “mostly routine
edits with one complex design task.” It does not affect scoring and must not
be inferred from prompts or used to judge whether a model was suitable.
Spend controls indicate cost discipline, not quality or business value.

## Model-tier definitions and evidence

- **Frontier:** models positioned by their provider for demanding reasoning
  or long-running complex work.
- **Mid-range:** models positioned for a speed/capability or cost/capability
  balance across everyday work.
- **Efficient:** smaller or explicitly cost-sensitive models for well-scoped
  work.
- **Unclassified:** unknown IDs, opaque router/Auto names without an observed
  underlying model, and any ambiguous cross-provider alias. It is evidence
  coverage, not a fourth target tier.

Use an explicit, reviewed mapping keyed by underlying model provider and
canonical model ID, with bounded aliases. Do not infer a tier from token price,
the agent name, or a substring such as `pro`. Initial mapping candidates:
Anthropic Fable/Opus -> Frontier, Sonnet -> Mid-range, Haiku -> Efficient;
OpenAI Sol -> Frontier, Terra -> Mid-range, Luna -> Efficient. These are product
classifications inferred from provider positioning, not provider-certified
universal tiers. Confirm every exact ID and mapping against current official
model documentation when implementing. Cursor, OpenCode, and Kiro can expose
models from different providers, so resolve the underlying identity if present;
otherwise leave the execution Unclassified. Do not silently assign an Auto
router's executions to a tier.

Keep the tier mapping separate from the pricing table. Version the mapping so
a catalog update cannot silently rewrite an already finished goal's result.
Store the mapping version on new tier goals. For an observed model not in the
catalog, the UI may let the user assign a tier explicitly for that goal; display
that assignment as user-entered and preserve it with the goal. This mapping
does not change the model's global price or identity.

For selected agent `a` and inclusive local dates `[start,end]`, sum the
per-model daily `executions` already available on session `_model_daily` rows:

```
N = sum(executions for agent a in [start,end])
N_tier = sum(executions classified as tier)
share_tier = 100 * N_tier / N                         (N > 0)
unclassified_share = 100 * N_unclassified / N
deviation_tier = abs(share_tier - target_tier)
met = end reached AND N > 0 AND no active executions AND
      N_unclassified == 0 AND all deviations <= tolerance_pp
```

Targets must each be 0–100% and sum to 100%. Use a visible default tolerance
of 5 percentage points, editable from 0 to 20. Display each target, actual,
deviation, execution count, mapping source, and unclassified count. The
denominator includes unclassified executions so coverage never inflates a
known tier's share. If daily execution evidence is missing for a runtime,
report incomplete evidence instead of moving whole-session executions to the
session's last day. A goal remains in progress until the selected window ends;
early target attainment is provisional.

## Implementation slices

1. **Measure and classify tiers.** In `token_meter/services/goals.py`, add
   `tier_shares` as a Model Mix mode and project its metrics from `_model_daily`.
   Add a small reviewed model-tier catalog under `token_meter/models/` and
   reuse the existing model ID normalization where it is safe. Keep legacy
   `minimum_models` and `target_shares` goals readable and editable, but make
   tier shares the default for new Model Mix goals. Existing records require
   no migration or automatic reclassification.
2. **Create the tier UI.** In `page.html`, replace the new-goal model-name
   picker with three percentage inputs, a tolerance input, and a workload
   planning note. Explain the tiers and show observed models under each tier
   for the selected agent, including an Unclassified group. Retain the
   agent-specific date presets. The current/previous table should show target
   versus actual mix and coverage without saying that distribution proves
   responsible model choice.
3. **Add Spend within a limit.** Add a new validated goal type and the form
   entry point. Use the existing per-day cost evidence and local calendar
   dates rather than a session's last date. Show estimated/reported cost and
   cost-coverage counts. Cap comparisons use unrounded values; round only for
   display. Keep this one-time goal separate from Settings' recurring monthly
   allocation.
4. **Add Session spend ceiling.** Add its validated type and form. Count each
   eligible whole session once, with last activity inside the selected range.
   Show the maximum, session count, and over-ceiling sessions. Missing cost is
   unknown, never zero. Active sessions make final success provisional.
5. **Integrate and document.** Put the new basic goals in the Goals catalog and
   Goal type selector with one-sentence definitions, required fields, inline
   errors, and Current/Previous behavior. Update `README.md` and the Goals
   section of `specs/ARCHITECTURE.md`. Keep native and MCP projections free
   of goal titles, notes, and session identities.

## Acceptance and user-run verification

- Creating or editing each goal validates agent, target, and date inputs;
  saving returns to a cleared Create new goal form.
- The same model ID in two agents is counted only within the selected agent.
  A mixed-model session contributes each day's model executions to the right
  tier; an opaque Auto model is shown as Unclassified.
- A session spanning the date boundary contributes daily spend and executions
  only on days in range; the session ceiling still uses its complete cost and
  last-activity eligibility.
- Missing cost or execution evidence cannot produce a false success; known
  cost above a cap produces a breach. Empty windows show awaiting evidence.
- A tier mapping update does not change a completed goal without an explicit
  goal revision. Legacy Model Mix goals remain legible.
- At wide desktop and 1024px laptop widths, the catalog, form, table, and
  action menu remain usable. The user runs implementation tests, browser
  checks, and installed-runtime verification before accepting the change.

## Primary sources for the tier review

- Anthropic current model lineup and positioning:
  https://platform.claude.com/docs/en/models/overview
- Anthropic model IDs and aliases:
  https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions
- OpenAI current model selection guidance:
  https://platform.openai.com/docs/models
- Cursor model-selection behavior, including Auto:
  https://docs.cursor.com/models/
- Kiro model-selection behavior, including Auto:
  https://kiro.dev/docs/cli/chat/model-selection/
