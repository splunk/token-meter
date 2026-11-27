---
name: token-meter-interview
description: Use at the start of every new Token Meter feature, bug fix, or design change, before writing any code, to interview the user and turn a rough request into a confirmed spec. Skip only when the user says "just do it".
---

# Requirements interview

Turn a rough request into an unambiguous, confirmed spec before any code changes.

## When to run

- Run first for every new feature, bug fix, or design change in this repository.
- Skip when the user says "just do it" (or plainly tells you to skip the interview).
- Skip for work that changes no behavior: answering a question, reviewing a pull
  request, investigating without fixing, or continuing work whose spec the user
  already confirmed in this conversation.
- For a Slack or GitHub reporter request, `token-meter-intake` owns clarification
  with the reporter. Use this skill only for the open product decisions that remain
  for the user.
- This interview replaces generic brainstorming for Token Meter work; do not run both.

## Before the first question

Read the relevant code, tests, `specs/ARCHITECTURE.md`, and any linked issue first.
Never ask something the repository already answers. Note what you learned so the
questions can be specific ("The Spend page already has X; should this extend it or
sit beside it?").

## Rules

- Ask ONE question per message, short and specific. The user often answers by voice,
  so keep each question easy to answer out loud.
- Offer 2-3 concrete options and mark your recommended default, so "yes" or "the
  first one" is a complete answer. Use the question tool with buttons when available.
- Stop as soon as you are confident. Usually 3-8 questions; one is fine for a
  well-described bug. Skip anything trivial or covered by a repository rule.
- Do not ask about phone, tablet, or sub-1024-pixel layouts; they are outside the
  supported product target.

## Areas to cover (only where relevant)

1. Goal: the problem this solves and for whom.
2. Scope: what is in, and what is explicitly out.
3. For a bug: exact steps, expected versus actual, which runtime (Claude, Codex,
   Cursor, OpenCode, Kiro, Pi), and whether it shows in the dashboard, menu bar,
   MCP, or all of them. Ask for a screenshot when it is visual.
4. UI and design: which page or section owns it, and the empty, loading, error, and
   unavailable-evidence states. Ask for a screenshot or reference when visual. Build
   from `specs/DESIGN.md` components before proposing anything new.
5. Data and trust: whether it needs any trace content (it must not, outside Work
   insights), how estimates are labeled, and how missing evidence shows (never as a
   measured zero).
6. Edge cases and failure behavior.
7. Impact: which existing pages, routes, settings, menu-bar or MCP behavior must
   keep working.
8. Done criteria: how both of us will know it is finished, including what will be
   verified (tests, browser check, installed runtime, live menu bar).

## Then

1. Write a short spec: goal, scope, out of scope, design notes, edge cases, done
   criteria. Keep it to what was decided; no padding.
2. Show it and ask "Anything missing?" Start building only after the user confirms.
3. Save it:
   - Feature or design change: `specs/YYYY-MM-DD-<short-name>-design.md`, matching
     the existing design records.
   - Bug fix: put the spec in the goal section of `specs/plans/active.md` (ignored
     local state) instead of a new committed document.
4. Continue with the normal route in `.agents/workflow/routing.yaml`, using the done
   criteria as the acceptance criteria for testers and reviewers.
