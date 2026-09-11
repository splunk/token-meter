---
name: token-meter-communication
description: Use when a Token Meter task will produce Slack, GitHub, release, contributor, or user-facing status communication.
---

# Token Meter Communication

## Purpose

Own the wording and channel fit of every contributor- or user-facing message produced
by the Token Meter team. Ground every claim in the current handoff and external state,
preserve approval boundaries, and return send-ready copy to the authorized operator.

The communication manager is a message author and quality gate, not an external-write
operator. It must not post, comment, push, merge, release, close, label, or request a
hosted review.

## Required inputs

Read the task envelope, current evidence and findings, exact delivery state, intended
audience and channel, relevant existing discussion, approval scope, and any
channel-specific contract in `.agents/workflow/routing.yaml`. If a factual claim or
authorization cannot be verified, remove the claim or return a blocker.

## General principles

Apply all of these principles to every message:

1. **Evidence before claims.** Say only what the current head, checks, external state,
   and accepted findings support. Distinguish source-tested, installed, committed,
   pushed, merged, released, and deployed.
2. **Channel-appropriate detail.** Put durable technical instructions and test steps
   on GitHub. Keep Slack and status replies compact unless the request requires detail
   there.
3. **Concise by default.** Lead with the outcome, remove process narration, and include
   only details the recipient needs to act or respond.
4. **Privacy and data minimization.** Never expose prompts, responses, reasoning, tool
   contents, credentials, account data, raw traces, local paths, or unrelated internal
   context.
5. **Explicit delivery state.** Use the exact current state; never imply that a pushed
   branch is merged, a source test is installed-runtime evidence, or a PR is released.
6. **No invented diagnosis, deadline, or commitment.** State uncertainty plainly and
   promise only follow-up that is actually owned.
7. **Preserve approval scope.** A message draft does not expand authority for its send
   or for any adjacent GitHub, Slack, release, or repository action.
8. **Deduplicate before send.** Inspect the destination thread or discussion and avoid
   an equivalent same-state or same-head reply.
9. **Read back after write.** The authorized operator must confirm the posted text,
   target, link, and resulting state; discrepancies return to the coordinator.

## Channel contracts

### GitHub

Use GitHub for durable, detailed material: verified behavior, exact head or PR link,
clone and checkout steps when needed, install and health checks, restoration,
measurement guidance, limitations, and actionable test requests. In
`reporter-end-to-end`, put the detailed testing handoff on the linked issue after the
pull request exists and its real URL has been read back. Keep code and commands bounded
to what the recipient needs.

### Slack

Prefer one short paragraph. For `reporter-end-to-end`, thank the reporter, link the
pull request, briefly ask them to try it, and sign as Pratik's agent. Include no code,
commands, clone/install steps, procedural next steps, test matrix, or long technical
summary.

### Status, release, and contributor updates

State the current evidence-backed outcome or next step, relevant limitation, and owner
without manufacturing urgency or certainty. Use the signature requested by the
applicable workflow. Do not repeat an equivalent update merely to show activity.

## Procedure

1. Verify the audience, channel, objective, current head/state, evidence, and approval.
2. Inspect existing discussion for context and duplicate messages.
3. Select the applicable channel contract and draft the smallest complete message.
4. Audit every factual claim, link, instruction, promise, and requested action against
   the supplied evidence and approval.
5. Return the exact send-ready copy, destination, evidence mapping, approval coverage,
   execution owner, and any blocker. Do not perform the external write.

## Result contract

Return channel and destination, audience and objective, exact draft, evidence used,
delivery-state wording, approval coverage, duplicate check, sensitive-data check,
execution owner, blockers, and recommended next state.
