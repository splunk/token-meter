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

Except for the `reporter-end-to-end` route and the two narrow standing exceptions
below, every Slack message or GitHub comment is a separate external action. Preview
the exact text and obtain explicit approval for that message immediately before
posting. An approval for investigation, code, or a previous message does not cover a
later reply.

## Reporter end-to-end delivery

When Pratik explicitly asks to start the Token Meter team flow or manage a linked
reporter request end to end, select the `reporter-end-to-end` route. That request is
standing approval for the complete bounded delivery sequence: commit only the scoped
files so the tester and reviewer can gate the exact commit; after those gates pass,
push its dedicated `codex/` branch, open a linked pull request, post the detailed
testing handoff on the linked GitHub issue, and post the concise Slack follow-up in the
existing thread. Do not pause for another approval between those steps. Recheck and
read back every external write.

Route every contributor- or user-facing draft through the communication manager before
an authorized coordinator or operator performs the external write. The communication
manager owns wording, evidence mapping, channel fit, privacy, duplication checks, and
approval-scope checks; it does not post the message itself.

The communication manager works twice in this route. Before pull-request creation it
drafts the title and body from the gated commit. After the GitHub operator creates and
reads back the pull request, it uses the real URL to draft the linked-issue handoff and
Slack follow-up; the GitHub operator posts the issue handoff and the coordinator posts
the Slack paragraph.

The two public responses have distinct positive contracts:

- The GitHub handoff contains the evidence-backed summary plus any clone, checkout,
  install, health-check, restoration, workload-measurement, and procedural next steps
  that help the reporter test the exact head.
- The Slack follow-up is one short paragraph: thank the reporter, link the pull
  request, briefly ask them to try it, and sign it as Pratik's agent. Slack contains
  no code, commands, clone or install instructions, procedural next steps, test
  matrix, or long technical summary.

This standing approval applies only to the linked reporter issue and its existing
Slack thread, only after the exact head has the required tester/reviewer evidence and
no open finding. It does not authorize merging, releasing, closing, labeling, or
requesting hosted review. If delivery is blocked, do not claim a fix or create an
unverified pull request.

Two additional narrow standing exceptions remain. When the user explicitly
authorizes checking out, reviewing, or managing pull requests, follow the
one-status-reply-per-PR rule in `token-meter-github-ops`. When Pratik's agent asked a
contributor to test a pull request and that contributor later reports results, reply
on every existing discussion thread containing those results: thank them, say the
team will follow up without inventing a diagnosis or deadline, and sign the message
as Pratik's agent. Apply the same one-short-paragraph Slack contract. These exceptions
authorize only the described replies; other external actions remain gated.

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
