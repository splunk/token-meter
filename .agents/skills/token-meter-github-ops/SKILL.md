---
name: token-meter-github-ops
description: Use when Token Meter GitHub issues, pull requests, review requests, checks, comments, merges, or closure need to be managed.
---

# Token Meter GitHub Operations

## Purpose

Maintain issue and pull-request state without confusing repository readiness with
permission to act. Default to read-only inspection.

## Read path

- Read the complete issue or pull request, comments, reviews, checks, base, current
  head, and merge state.
- Compare the pull-request head with the handoff's tested and reviewed head.
- Confirm required tester and project-reviewer results satisfy
  `.agents/workflow/review-policy.yaml`.
- Carry verified hosted review findings into the shared findings ledger. A hosted
  review is advisory and cannot satisfy the project gate.

## Write gate

Immediately before a comment, edit, label change, review request, push, merge, close,
or other external mutation:

1. Identify the exact operation and target.
2. Recheck the current head, checks, findings, evidence freshness, and merge state.
3. Confirm the task envelope contains explicit approval for that specific operation.
4. For a user-visible write, require a current communication-manager result with the
   exact draft, evidence mapping, destination, approval coverage, duplicate check, and
   sensitive-data check. Preview the draft and confirm it still matches current state.
5. Perform only the approved operation without materially rewriting the draft, then
   read back its text and resulting state.

Approval for one comment does not cover another. Approval to implement, commit, push,
request review, or merge does not imply any other action. If the head changes, return
the work to verification and review.

## Reporter end-to-end delivery

An envelope on the `reporter-end-to-end` route carries standing approval for the
successful delivery sequence defined in `.agents/workflow/routing.yaml`. The
coordinator commits only the scoped change before the gates so the tester and reviewer
can bind their evidence to that exact commit. After confirming the frozen commit has
every required tester/reviewer result and no open finding, the operator may push the
dedicated branch and create the linked pull request without requesting approval again.
After reading back the pull request and receiving the communication manager's linked
drafts, the operator may post the detailed testing handoff on the linked issue. Read
back the remote head, pull request, and issue comment after writing them.

The communication manager first drafts the pull-request title and body from the gated
commit. After the operator creates and reads back the pull request, the communication
manager drafts a separate detailed linked-issue handoff using the real pull-request
URL. That handoff summarizes verified behavior, distinguishes pushed from merged or
released, and includes useful clone, checkout, install, health-check, restoration,
measurement, and procedural next steps. The communication manager also drafts the
coordinator's Slack reply as one short paragraph with thanks, the pull-request link, a
brief request to try it, and the Pratik's agent signature; Slack receives no code,
commands, clone/install steps, procedural next steps, test matrix, or long technical
summary.

This route does not authorize merge, release, close, label changes, or hosted review
requests. Those actions still require separate explicit approval.

## Review requests

Request Codex or Claude hosted review only when separately approved. Record the
requested service and head. Verify every returned claim before routing a fix; do not
apply suggestions merely because an automated reviewer sounds confident.

## Contributor status replies

When the user explicitly authorizes checking out, reviewing, or managing pull
requests, that request includes standing approval for one contributor-facing
status reply on each inspected PR. Before posting, inspect the current head and
existing discussion and preview the exact text. Do not duplicate an equivalent
same-head status reply.

Thank the contributor, state the evidence-backed merge status or next step, say
the team will follow up soon, and identify the message as from Pratik's agent.
This standing approval covers only those status replies. A merge, close, push,
review request, or unrelated external action still requires explicit authority
in the current task.

## Operator result

Return the repository and pull request, inspected head, gate evidence, exact approved
action, resulting state, links, remaining findings, and recommended next state. Omit
credentials, private paths, traces, prompts, responses, reasoning, and tool contents
from public text.
