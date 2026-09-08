---
name: token-meter-intake
description: Use when a Token Meter Slack or GitHub request needs contextual intake, clarification, triage, acknowledgment, or closure.
---

# Token Meter Intake

## Purpose

Turn feedback into a truthful human response and an actionable work envelope. Intake
does not grant implementation or external-write authority.

## Context first

- Open a Slack permalink through the Slack integration so attachments and the complete
  thread are included; do not reconstruct channel and timestamp values by hand.
- Read the linked GitHub issue or pull request in full, including comments and cited
  evidence. If a link is unavailable or ambiguous, ask for the correct source rather
  than implementing the paraphrase.
- Separate observed behavior, reporter interpretation, and open product decisions.
- Inspect current repository and dirty-tree ownership before promising a change.

## Human response

Draft a short acknowledgment in the thread's tone. Say what is understood, what is
being checked, and what input is still needed. Do not invent a diagnosis, deadline,
or completion claim.

Every Slack message or GitHub comment is a separate external action. Preview the exact
text and obtain explicit approval for that message immediately before posting. An
approval for investigation, code, or a previous message does not cover a later reply.

## Classification

Produce:

- Requested behavior, acceptance criteria, scope, and non-goals.
- Task class from `.agents/workflow/routing.yaml`.
- Risk categories from `.agents/workflow/review-policy.yaml`.
- Whether the low-risk fast path is explicitly eligible, with every required
  condition accounted for and one allowed change kind selected; uncertainty means it
  is not eligible.
- Evidence already available and evidence still needed.
- Recommended route and actions that still require approval.

Questions and clarifications normally remain with the coordinator. Diagnosis-only,
implementation, verification, review, and GitHub operations move through their
declared roles only when needed.

## Close the loop

Before drafting a completion response, verify the current workflow state and evidence.
State what changed, what was verified, and what remains unverified. Keep the update
truthful when work is blocked, local-only, uncommitted, unpushed, or not deployed.

Never include credentials, raw traces, prompts, responses, reasoning, tool contents,
account data, or private local paths in a public or shared response.
