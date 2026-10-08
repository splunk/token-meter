# Build business-objective Goals from linked agent sessions

Historical: superseded on 2026-10-08 by the single metric goal design in [Goals](../GOALS.md). Business objectives, Model Mix, and the daily and selected model limits were removed.

This ExecPlan is a living product design and implementation plan. Keep
`Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` current as work proceeds. Product implementation
was authorized on the `FINOPS-7931-goals-development-phase-1` branch; external
delivery remains separate.

## Purpose / Big Picture

Let a user create a business objective such as “Develop feature X for less
than $50 in AI spend,” select the AI agents involved, and link the sessions
that contributed to it. The user can link past sessions from the Goals page
or tag a current session with the objective through explicit Token Meter
session metadata. Token Meter totals the linked sessions' cost, shows the
remaining allocation and evidence coverage, and keeps the objective visible
until the user records that the work is complete.

The objective budget is a **lifetime cap for selected work**, separate from
the machine-wide monthly allocations in Settings. The first Goals experience
is an objective with a spend cap, not a generic “priority delivery” score.
A project is useful for finding
candidate sessions, but project membership alone does not assign spend to a
business objective.

For the example goal, the user supplies the feature name, a $50 cap, and one
or more agents. A time range appears only if the goal ends on a selected date.
Token Meter measures the cost of the explicitly linked sessions. The user marks
the goal complete
and that the relevant AI sessions have been linked. Token Meter cannot infer
either fact from traces, Git activity, tokens, or spend.

## Progress

- [x] (2026-09-28) Reviewed the September 11–16 Slack DM, current `main`
  architecture and budget implementation, and the separate Tok PR worktree.
- [x] (2026-09-28) Drafted an initial deterministic Goals page plan.
- [x] (2026-09-28) Explored agent-scoped skill, prompt, usage, and priority
  goal types against Token Meter's available metric evidence.
- [x] (2026-09-28) Reframed Goals around user-created business objectives,
  explicit session association or tagging, and a lifetime spend cap after the
  user's feature-under-$50 example.
- [x] (2026-09-28) Narrowed the skill goal to observed use of an existing
  skill; deferred automatic skill-creation claims after inspecting the
  inventory and trace evidence.
- [x] (2026-09-28) Removed both skill creation and skill use from the
  proposed Goals catalog at the user's direction.
- [x] (2026-09-28) Confirmed whole-session attribution for a session
  assigned to a business objective.
- [ ] Review the objective record, session tagging semantics, and first-page
  flow with the product owner.
- [x] (2026-09-28) Created ignored `specs/plans/active.md` with base commit
  and implementation decisions.
- [x] (2026-09-28) Implemented the first browser Goals workflow: objective
  records and links, session assignment, monthly spend view, and Model Mix.
- [x] (2026-09-28) Added a visible goal catalog with definitions,
  measurement status, and entry points to the standalone goals and Settings
  allocation. Increased page spacing and restored
  a Goals return link from the separate Performance page.
- [x] (2026-09-29) Removed Token Allowance and Model Evaluation from the
  available goals, forms, scoring, and review actions. Renamed the monthly
  budget presentation to Monthly AI spend goal.
- [x] (2026-09-29) Added custom start/end dates and Day, Week, Bi-weekly,
  and Month shortcuts to the goal form. Existing objectives without a start
  date retain their lifetime spend behavior.
- [x] (2026-09-28) Reorganized Goals around a Current goals table, Previous
  goals table, and Create new goal view. Current goals is the default; goals
  marked complete or set to end after a date appear under Previous. Goal
  details remain expandable, and session linking lives in the objective form.
- [x] (2026-09-29) Removed the two unscored catalog concepts and the redundant
  priority and definition-of-done fields. Manual objectives no longer show or
  require a time range; date-ending objectives show presets and dates. The form
  surfaces missing required information before saving.
- [ ] User testing and review of the first implementation.
- [ ] Add Tok's read-only interpretation only after Goals measurements are
  trusted.

## Surprises & Discoveries

- Current `main` has no Goals or Coach page. The separate Tok PR's rolling
  goal sheet is reference material, not the base for this page.
- Session rows already carry a provider, an ID, often a project, and cost and
  token evidence. `daily_summaries()` can show session-level usage; the
  `project_model_stats()` endpoint can filter an exact discovered project.
  Neither contains a business-priority association.
- Project labels are imperfect attribution. One repository may contain work
  for several objectives. Explicit session selection and reassignment give
  the user control over what counts.
- Settings already has a machine-wide monthly budget and per-agent monthly
  allocations. An objective's $50 cap spans its selected sessions and may
  cross months, so it is a different measure. Both must keep separate names
  and totals.
- Skill observations are incomplete across agents, so this plan does not
  offer a skill goal.
- An in-prompt marker would require reading private prompt content into the
  Goals workflow and would behave differently across agents. The initial
  “tag within a session” is an explicit Token Meter metadata action on that
  session, not text embedded in a prompt.

## Decision Log

- Decision: use a dedicated `#goals` page as requested in the Slack DM.
  Tok remains a later interpretation layer. Date/Author: 2026-09-28 / Codex
  proposal.
- Decision: replace “priority delivery” as a default goal type with a
  **business objective + linked-session spend cap**. Rationale: Token Meter
  can measure associated AI cost, while delivery requires user acceptance.
  Date/Author: 2026-09-28 / Codex proposal following user direction.
- Decision: the user chooses one or more agents for an objective. Show a
  combined objective total and a per-agent breakdown; keep model identities
  scoped to their agent. One-agent objectives remain the simple default.
  Date/Author: 2026-09-28 / Codex proposal.
- Decision: allow manual selection of historical sessions and an explicit
  “Assign to objective” action on a current session. A tag assigns the
  **whole session** in the first release. One session has at most one primary
  objective for spend accounting, and reassignment is reversible. Assignment
  history remains a proposed follow-up.
  Date/Author: 2026-09-28 / whole-session rule confirmed by user.
- Decision: no automatic project-to-objective assignment in the first
  release. Project filters help the user locate sessions. Rationale: a
  project may serve several objectives and automatic mapping could overstate
  or misattribute cost. Date/Author: 2026-09-28 / Codex proposal.
- Decision: the objective's success check includes user-recorded completion
  and an attestation that relevant AI sessions were linked. This is clearly
  labeled user-reported; Token Meter computes only the linked spend and
  coverage. Date/Author: 2026-09-28 / Codex proposal.
- Decision: keep the complete machine-wide monthly budget controls in
  Settings. Objective caps are independent lifetime targets and never edit
  Settings allocations. Date/Author: 2026-09-28 / Codex proposal.
- Decision: implement the concrete objective-cost flow before building a
  common goal scoring framework. Date/Author: 2026-09-28 / Codex proposal.
- Decision: remove skill creation and skill usage from the Goals catalog for
  now. Rationale: creation lacks authorship/date evidence, and observed
  activation is too incomplete and ambiguous to make a dependable goal
  across agents. Date/Author: 2026-09-28 / user direction.
- Decision: keep token allowance only as an optional resource limit attached
  to a business objective. The user selects an agent and input or output
  tokens; show cache components separately and do not treat a lower token
  count as evidence of a better result. Date/Author: 2026-09-28 / user
  approval of recommendation.
- Decision: remove usage cadence from the Goals catalog. Counting days with
  AI activity rewards use without showing that the activity was needed or
  helped deliver an outcome. Date/Author: 2026-09-28 / user direction.
- Decision: reframe model exploration as an agent-scoped model evaluation
  goal. The user selects an observed baseline, one or more candidate models,
  a deadline, and a minimum number of completed single-model trials per
  candidate, optionally scoped to an objective or task type. A searchable
  picker separates models observed with that agent from other models known
  to Token Meter; known models do not imply current agent/account access.
  Token Meter detects model use from later sessions, shows model-level usage
  and cost, and counts user reviews of result usefulness. Mixed-model
  sessions remain visible but their whole-session outcome is not attributed
  to one model. Completion means the trials and reviews happened, not that
  an automatic winner was declared. Date/Author: 2026-09-28 / user approval
  of proposed flow.
- Decision: remove cost per covered execution from the Goals catalog for
  now. A lower cost per model turn does not establish a better outcome and
  can be changed by splitting work into more turns; retain it as an
  Efficiency diagnostic. Date/Author: 2026-09-28 / user direction.
- Decision: include **Model Mix** as a goal type. Scope it to one agent and
  a time window, with targets for either the number of meaningfully used
  models or selected models' shares of that agent's overall usage. Use
  agent-scoped model evidence and disclose incomplete model attribution.
  Date/Author: 2026-09-28 / user direction.

## Outcomes & Retrospective

The first implementation is present in source. It has not been tested or
installed; the user handles testing. It does not yet freeze a numeric
completion snapshot or show assignment history. Model Mix uses
existing per-session model evidence.

## Objective and session-link contract

The user creates an objective with a short title, USD spend cap, and at least
one selected agent. A start and end date are required only for goals set to end
on a date. The goal's unit is dollars of AI cost associated with linked
sessions. If the user says “under $50,” the strict comparison is `< $50.00`;
“at most $50” would use `<= $50.00`. Compare unrounded values and round only
for display. The UI should state the chosen comparison plainly.

Each link references a stable opaque session key and one objective ID. The
user can add or remove a completed session from search results and can set an
active session's objective from its Session detail. Assignment from the
Current sessions card remains a proposed follow-up.
Both actions write the same explicit `session_key -> objective_id` association.
An unassigned session contributes no spend to any objective. The Goals page
shows the assigned sessions, and each assigned Session view shows its
objective; the user can remove or reassign the association at any time.
The active-session tag is Token Meter metadata. It does not inspect prompt
text, depend on a filename or project path, or change the agent's prompt.
The tag applies to the **entire session**, including usage before it was set.
If a session contains work for multiple objectives, the user must choose its
primary objective or leave it unassigned. Do not apportion its cost by an
arbitrary percentage. A future segment-level tag needs reliable per-execution
timestamps and cost for every supported agent before it can allocate only
part of a session.

An objective can span Claude, Codex, Cursor, OpenCode, Kiro, Pi, and Hermes
sessions. Only linked sessions from the objective's selected agents count.
Adding a link from another agent requires explicitly adding that agent to the
objective first. A project filter suggests likely sessions but does not link
them automatically. Unlinked sessions are outside the objective total; the
page must say so. Reassignment removes a session from the old objective
before adding it to the new one, preventing double-counting.

Persist only bounded goal fields, optional user-authored session-purpose labels,
opaque session links, timestamps, and structured completion events through the
existing atomic JSON-write path.
Do not persist or expose prompts, responses, reasoning, tool content, raw
traces, credentials, account data, or local paths. Do not include objective
titles, purpose labels, or session identities in MCP or native projections by
default. Goal records remain free of trace-derived content; prompt-aware Coach
analysis stays confined to loopback `GET /coach`.

## Measurement and status rules

Let `L` be the set of unique sessions linked to the objective, filtered to
its selected agents. Let `K(s)` be the known reported or estimated USD cost
portion for session `s`; the session is cost-covered only when all of its
cost-relevant usage is accounted for. Then:

```
observed_spend      = sum(K(s) for s in L)
cost_coverage       = cost-covered linked sessions / all linked sessions
budget_utilization  = observed_spend / objective_cap
remaining_observed  = max(0, objective_cap - observed_spend)
agent_spend(p)       = sum(K(s) for s in L where agent(s) = p)
```

Show the combined amount, per-agent amounts, session count, coverage,
reported/estimated provenance, and the cap. `observed_spend` is a lower
bound when any linked session lacks complete cost evidence. If that lower
bound reaches or exceeds a strict “under $50” cap, the budget is already
breached. An observed amount below the cap cannot establish success while
coverage is partial. A zero-link objective is unmeasured, not $0 of proven
feature cost. An active linked session shows provisional spend; finalize the
objective only when all linked sessions are complete.

The page has separate **completion** and **cost** states. Manual completion is
user-confirmed; reaching a selected end date ends the goal without asserting
completion. Cost is `provisional`, `within_cap`, `cap_breached`, or
`insufficient_evidence`. The overall goal is `met` only when the user marks
the work complete, confirms the relevant AI sessions were linked, every linked
session is complete with full cost coverage, and the final cost satisfies the
cap. Spend over the cap is conclusively
`cap_breached` even if the feature is not done. Do not show “percent of
feature complete” based on budget use; show **budget utilized** instead.

Changing the cap, selected end date, or selected agents creates
a new goal revision. Link and completion actions use idempotent IDs and keep
the time they were recorded. After completion, freeze a content-free numeric
snapshot of the linked-session total and coverage so later trace deletion or
repricing does not silently rewrite the result; any explicit re-evaluation
or change to linked sessions creates a new revision with its own provenance.
Removing a linked trace
before completion yields unavailable evidence rather than a measured zero.

## User flow

1. **Create objective:** enter “Develop feature X,” “under $50 total AI spend,”
   the agents that may contribute, and how the goal should finish. Date-ending
   goals also need start and end dates.
2. **Associate work:** search recent sessions, filter by agent or project,
   and select the relevant ones in a searchable multi-select picker. Each
   result leads with a one-line task description: use the existing session
   display title when available, mark whether it is an agent/custom title or
   a first-prompt excerpt, and allow the user to enter an optional short
   purpose label. A title is displayed from the existing local Sessions
   inventory and is never copied automatically into the goal record; a
   purpose label is saved only when the user explicitly enters it. If no
   useful title or label exists, show a readable fallback made from agent,
   safe project label, and local date/time, rather than a bare session ID.
   Beneath the description show agent, project, local date/time, model,
   active/completed state, cost, and cost-evidence status. Offer an “Open
   session” link for disambiguation. The picker
   lists only sessions from the objective's selected agents and supports
   adding several at once. The objective displays a separate list of linked
   sessions with remove/reassign actions. From an active session, choose
   “Assign to objective” in a compact objective selector. Show an objective
   chip on the session so attribution is visible and reversible.
3. **Monitor:** the objective card shows `$38.40 / $50.00`, `76.8% budget
   utilized`, agent breakdown, linked-session count, missing-cost count, and
   “Feature completion not confirmed.” These example figures are illustrative.
4. **Finish:** the user marks a manual goal complete and confirms the selected
   AI sessions cover the relevant work, or lets a date-ending goal end. Token
   Meter computes the budget verdict from complete session evidence.

This is an attribution tool based on user-selected sessions. It does not
claim to detect every unlinked AI interaction that may have helped the
feature, and it does not prove business value from a low token bill.

## Related goal types after the objective-cost flow

The Goals page may later show separate agent-scoped capability or resource
goals. Those tied to an objective use its linked sessions; the existing
monthly agent allocation remains machine-wide in Settings. Their
calculations remain distinct from feature acceptance:

| Goal type | Deterministic measure | Additional evidence needed |
|---|---|---|
| Monthly AI spend goal | All agent cost in a local calendar month divided by its Settings allocation; show the existing allocation on Goals without creating a second budget | Cost may be unavailable when a trace lacks usable input/output counts, supported model pricing or billing evidence, or recorded cost. Unknown is not $0: known cost at or above the cap proves breach, while known cost below the cap with incomplete coverage cannot prove adherence. Codex API-equivalent cost is an estimate, not subscription billing. |
| Model Mix | For one selected agent and time window, count distinct observed models with meaningful usage and compute each selected model's share of covered model executions. Support either a minimum number of models above a per-model share floor or an allocation target across selected models. | Require agent-scoped model identity, a stable denominator of all that agent's covered executions, and coverage disclosure. A selected-model combined share alone can be satisfied by one model, so multi-model goals need per-model floors. Distribution by itself does not prove that model choices fit the tasks; show cost and user usefulness reviews as context. |

Retry rate is offered only for an agent with complete attempt evidence.
Expensive-session ceilings need a complete month-level session-cost
aggregation. None of these proxies replaces the objective's user-confirmed
acceptance or spend cap.

For Model Mix, use covered model executions as the default usage
unit, scoped to the selected agent and time window. Display sessions,
output tokens, and estimated cost as additional context. In count mode,
`qualified_models = count(models with execution_share >= per_model_floor)`.
In allocation mode, each selected model has an explicit minimum or target
share; the sum of shares is based on all known model executions for that
agent, not only the selected subset. Unknown model identity makes the
result provisional. Do not label distribution alone as responsible or
automatically declare a model choice better; task-fit reviews would require
separate evidence and are outside this goal.

## Context and Orientation

`page.html` owns the browser UI and routing. `token_meter/app.py` composes
the local HTTP server, cached cross-session state, settings, and protected
writes. `token_meter/domain/aggregates.py::daily_summaries()` exposes
session-level daily usage and coverage; `monthly_summaries()` supplies the
separate Settings budget. `project_model_stats()` can help filter candidate
sessions, but it does not establish objective membership. Agent models are
always scoped by runtime. `token_meter_mcp.py` exposes bounded local tools.

The prior Tok PR is https://github.com/splunk/token-meter/pull/47. Its
contracts and unavailable-evidence behavior are references. Its chat-based
goal sheet and rolling windows do not define this objective flow. Reinspect
the current branch and architecture before implementation; this plan does
not assume the PR was merged.

## Plan of Work

### Milestone 1: live objective and linked-session cap

Record the approved Goals route and placement in maintained project docs.
Add the objective form, agent selection, historical session picker, active
session “Assign to objective” action, visible objective chip, reversible
links, and a live combined/per-agent spend projection. Use a bounded,
searchable multi-select result list for historical sessions, not an
unbounded single dropdown; lead each result with the existing local session
title or user-authored purpose label, identify prompt-derived titles, and
fall back to agent/project/time metadata where no title exists. Show model,
cost, and coverage for each result. Do not persist source titles as goal
metadata. Add validated,
idempotent, action-token-protected writes through the existing atomic
settings path. A user can create a $50 objective, link Claude and Codex
sessions, reload, and see the same attributed spend and coverage. The page
works without Tok or the Codex CLI.

### Milestone 2: completion and final verdict

Add the definition-of-done acceptance record and all-relevant-sessions
attestation. Apply the strict cap comparison, deadline, complete-session
rule, missing-evidence states, and frozen numeric completion snapshot.
Ensure revised scope or cap cannot silently preserve an old success verdict.

### Milestone 3: independent linked capability goals

Add prompt-practice as an independent slice, using structured user check-ins
without storing prompt text. Then consider token, cadence, model-exploration,
and comparable-work unit-cost goals based
on actual use. Do not build a shared scoring framework ahead of consumers.

### Milestone 4: Tok reads trusted results

Only after the page and formulas are reviewed, expose a bounded sanitized
read-only Goals projection. Tok may explain spend, coverage, and one
reversible experiment. It does not infer feature completion or calculate a
different goal verdict.

## Concrete Steps

Work from `/Users/dowens/dev/code/token-meter`. At implementation start,
record the branch, base commit, and dirty-tree inventory in ignored
`specs/plans/active.md`; preserve unrelated worktree changes. Read
`specs/ARCHITECTURE.md` and relevant code and tests before each slice. Keep
one tracked-file writer. Follow the then-current project workflow and the
user's testing and commit preferences. Never patch the installed runtime
directly.

## Validation and Acceptance

The design is ready for implementation when the product owner agrees that
whole-session, explicit, single-objective attribution is the initial unit;
that a literal in-prompt marker is outside the Goals privacy boundary; and
that “under $50” applies to selected sessions with full evidence and a
user-confirmed outcome. A user should be able to identify what is included
and excluded before accepting an objective's result.

Milestone 1 is accepted when linked session costs are summed exactly once
across selected agents, an active session can be tagged and later reassigned,
and the page distinguishes missing cost from zero. Milestone 2 is accepted
when the goal can only be `met` after completion confirmation, full cost
coverage, and a final spend below its strict cap. Source/runtime and
independent review gates follow the project's workflow when implementation
is authorized. This design-only revision has not run tests or installation
checks.

## Idempotence and Recovery

Saving the same objective or session link twice creates one record. An
interrupted write leaves old or new valid settings, not a partial file.
Reassignment removes old attribution before applying new attribution.
Malformed objective data fails closed without changing Settings budgets.
Deleting or losing a linked trace before completion yields unavailable
evidence. If Tok integration does not land, the Goals page remains usable.

## Interfaces and Dependencies

Use local-only HTTP and the existing atomic settings/action-token mechanisms.
The browser reads a bounded content-free Goals projection and submits
validated structured actions. No hosted service, new Python dependency,
prompt capture, project-management integration, or AI judgment is required
for the objective-cap flow. A future in-agent tag command or segment-level
allocation needs its own design.

## Artifacts and Notes

Slack DM `D0C1BGFKSSD` requested a dedicated Goals page with reliable
measurements and Tok as a later interpretation layer. The local FinOps paper
“Optimizing GenAI Usage: A FinOps Perspective on Cost, Performance, and
Efficiency” informs the cost/performance/business-impact framing, not the
formulas or default targets.

Plan revision 2026-09-28: replaced the earlier “priority delivery” type and
one-agent monthly metrics-first plan after the user specified a business
objective with selected or tagged sessions and a total spend cap. The first
goal is now a user-scoped feature-cost objective. Skill goals have been
removed from this catalog. The user confirmed whole-session attribution;
the association is an explicit session-to-objective mapping. No product
implementation is in progress. The current UI saves objective details and selected
sessions together, then resets the create form. Goal status is read-only in the
table; the Actions menu contains Edit goal and End goal, plus Add or remove
sessions for business objectives. Validation is reserved for the user.
