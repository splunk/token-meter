# Agent Workflow Handoff

Use one handoff record per work item and worktree. The machine-readable contract is
`.agents/workflow/handoff.schema.json`; this document explains how to fill it.

## Task envelope

The coordinator sends a specialist only a bounded package containing:

- Objective, mode, acceptance criteria, scope, and non-goals.
- Assigned role, risk lenses, allowed actions, and explicit external approvals.
- Worktree, branch, base commit, expected head, and current dirty-tree ownership.
- Relevant evidence and findings, without private conversation or trace content.
- Required output and the conditions that require escalation.

The envelope grants only the actions it names. Diagnosis-only work does not authorize
implementation. Implementation approval does not authorize a commit, push, message,
review request, merge, or other external action.

An eligible low-risk fast path completed directly by the coordinator needs no
specialist transfer. Record the selected allowed change kind, eligibility decision,
focused evidence, and unverified checks in the final result; use the full handoff only
if work is delegated or escalates.

## Worker result

Every specialist returns:

- Status and current head commit.
- Files changed, if the role is the assigned writer.
- Commands and live checks run, with pass, fail, blocked, or not-run status.
- Findings with severity, evidence, owner, and disposition.
- Unverified behavior, blockers, and the exact input needed to continue.
- Recommended next workflow state.

Tester and reviewer results must identify the inspected head. Any later tracked code
or test change makes that evidence stale. The coordinator routes the new head through
verification and review again.

## Published projection

The GitHub operator may publish an approved, durable summary in a pull request. Omit
credentials, local absolute paths, raw traces, prompts, responses, reasoning, tool
contents, account data, and private machine state. A Slack or GitHub permalink may be
included when it is already authorized for that audience.

## Resume checklist

Before resuming, confirm the repository, worktree, branch, base, head, dirty-tree
inventory, evidence freshness, open findings, and approvals. If any differs, mark the
handoff stale and return it to the coordinator rather than guessing.
