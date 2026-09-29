# Goals page UX alignment plan

Status: implemented in the working tree; user validation pending.

Follow-up: The goal table now opens a full-width Goals detail view when a goal is selected. Model Mix mini-bars were removed from the table after user feedback that they were cramped. Charts in the detail view provide hover and keyboard-focus data tips, and a Back to goals action returns to the originating list.

## Outcome

Make Goals feel like the other Token Meter routes while keeping the goal table at the top. Users should see the target, observed progress, and evidence quality at a glance. The creation form appears only after a goal type is selected.

## Current gaps

- The page uses the shared Spectrum header, but Goals defines its own small table text, buttons, fields, and status badges. Use the component and typography hierarchy in `specs/DESIGN.md`: body copy around 14px, compact 11px labels, tabular monospace for measured values, shared card spacing, cyan focus treatments, and semantic status colors.
- `Create new goal` appears both as a header button and as a tab. Keep one primary `Create goal` button in the workspace header. Keep `Current goals` and `Previous goals` as the two list tabs.
- The goal catalog's Availability column repeats `Available` on every row. Keep a concise table of goal type, purpose and measure, and a `Choose` action.
- The create form is always present below the catalog and repeats the goal-type selector. This makes the page longer and makes selection feel optional. Render the catalog alone until the user chooses a type.
- The current table gives target and progress as text only. Expanded rows contain evidence details but no chart. Add a compact progress indicator in the table and an evidence-aware chart in the expanded row.
- Raw status labels such as `awaiting evidence` and `in progress` need user-facing wording that distinguishes spend, evidence, and delivery confirmation.

## Page and creation flow

1. Open Goals on `Current goals`, with the table as the first working surface. Put one `Create goal` action at the upper right of the workspace header. `Previous goals` remains a separate list tab.
2. Selecting `Create goal` displays only the goal-type catalog. The form has no initial footprint, hidden required fields, or empty placeholder card.
3. The user chooses one of the four types: `Spend limit for business objective`, `Daily spend ceiling`, `Selected model spend limit`, or `Model mix`. Replace or collapse the catalog into a compact selected-type summary. Reveal the relevant form directly beneath it and move focus to the form heading.
4. Show a `Change goal type` action beside the selected-type summary. It returns to the catalog. Keep the goal-type selector out of the form; the chosen type determines its fields. Preserve an unsaved draft in memory during a temporary type change, while keeping persisted goal records unchanged.
5. Group fields by decision: name and scope, target, date or finish rule, then objective sessions only for the business-objective type. Use shared field and button treatments. Put the primary `Save goal` action at the end of the form, with `Cancel` as a secondary action.
6. After creating a goal, clear the draft and return to the catalog in `Create goal`, as requested for business-objective creation. After editing, return to the originating Current or Previous list and expose the updated row. `Edit goal` opens the saved type's form directly; goal type stays fixed. `Add or remove sessions` opens the objective form at its session section.

## Goal visualizations

Keep the exact measurement, target, status, and evidence note as text alongside every visual. Put a small progress mark in the table's Progress cell and the full visual inside the expanded row. Use the chart palette and interaction patterns already present on Spend, Efficiency, Git, and the Settings monthly-budget target line.

| Goal type | Expanded visual | Target rule |
| --- | --- | --- |
| Spend limit for business objective | Horizontal observed-spend bar with linked-session contribution details below. | Labeled vertical marker at the total lifetime cap; observed spend at or above the marker breaches the strict limit. Do not invent daily attribution for whole-session links. |
| Daily spend ceiling | Daily cost bars for the selected agent, with date labels and day inspection. | Labeled horizontal line at the per-day ceiling. A bar touching the line breaches the limit. Missing days are gaps unless measured as zero. |
| Selected model spend limit | Cumulative dated spend line for the exact agent and model. | Labeled horizontal line at the total period cap. Missing cost coverage interrupts the complete-looking line and is named in the evidence note. |
| Model mix | Three aligned 0–100% tier bars, with observed share, execution count, and Unclassified evidence. | One target marker and an allowed-difference band per tier. Distribution does not claim task fit or outcome quality. |

The existing Goals projection returns at most 100 recent daily values for Daily spend ceiling and only an aggregate for Selected model spend limit. Add bounded, date-indexed chart data before rendering those charts. For longer periods, use an explicitly labeled chart window with navigation; keep goal status calculated from the full selected period. Do not present an unlabeled truncated series or compare a total-period target with noncumulative daily values. Return only sanitized aggregate values, dates, and evidence flags; keep trace content and paths out of chart payloads.

## Copy and accessibility

- Use `Tracking`, `Limit reached`, `Cost data incomplete`, `Waiting for completion confirmation`, and `Goal met` where those descriptions match the actual computed state. A date ending an objective does not confirm delivery.
- Explain strict limits beside the target: reaching the amount counts as a breach. Distinguish reported cost from local estimates and incomplete cost coverage.
- Label target lines directly and retain numeric values in text. Use line style or pattern plus words for states so color is not the only cue. Provide chart summaries and keyboard-accessible day or tier inspection.
- Preserve visible focus, field-level errors, and readable action menus at wide desktop and 1024px laptop widths. The form and goal table should remain usable with long titles and many goals.

## Implementation sequence and acceptance

1. Align Goals shell, table, catalog, buttons, fields, type, and status copy with shared design tokens. Keep the two-list-tab structure and one create action.
2. Make goal creation selection-first. The form is absent until a type is chosen; edit opens the correct form directly; save and cancel follow the flows above.
3. Add bounded chart projections for daily and selected-model spend, then render compact row progress and expanded visuals for all four types.
4. Review source and rendered states for no goals, each goal type, missing and estimated cost, exact-limit breaches, long periods, many rows, keyboard focus, wide desktop, and 1024px laptop. The user performs implementation tests and browser checks under the current project preference.

Acceptance: a user can identify the goal type, target, actual progress, evidence quality, and next action without opening an unrelated form. Every visual target uses the same metric and time scope as its actual series. No chart turns unavailable evidence into zero or a confirmed outcome.
