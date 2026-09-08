---
name: token-meter-development
description: Use when a Token Meter issue or approved feature needs diagnosis or implementation in the repository.
---

# Token Meter Development

## Purpose

Diagnose unexplained behavior or implement an approved Token Meter change as the
single writer for the assigned worktree. Return evidence; do not certify readiness.

## Select the mode

### Diagnosis-only

Use when the envelope does not authorize implementation. Reproduce the symptom when
practical, trace the actual execution and data path, distinguish observed facts from
inference, and identify the smallest credible cause.

**REQUIRED SUB-SKILL:** Use `superpowers:systematic-debugging` for failures or
unexpected behavior.

Return cause, evidence, uncertainty, affected surfaces, options, and the exact next
approval needed. Diagnosis-only mode prohibits tracked-file edits.

### Standard implementation

Use for standard or high-risk work only with explicit implementation authority and
approved acceptance criteria.

1. Read `specs/AGENTS.md`, the relevant implementation and tests, the task envelope,
   and current dirty-tree inventory.
2. Confirm this role owns tracked-file writes. Another writer must hand off before
   editing the same worktree.
3. For a product or technical design decision, complete the approved design gate.
4. **REQUIRED SUB-SKILL:** Use `superpowers:test-driven-development`. Write a focused
   regression test, observe the expected failure, implement the smallest complete
   change, and observe the focused pass.
5. Run relevant self-checks without representing them as independent verification.
6. Reinspect callers, public projections, platform behavior, packaging, documentation,
   and installed-runtime effects implicated by the diff.

### Low-risk fast path

Use the low-risk fast path only when the coordinator names it explicitly and every
eligibility condition in `.agents/workflow/review-policy.yaml` remains true. The
envelope must name one allowed change kind and evidence for every eligibility
condition. This is a separate mode: follow the common input and ownership checks in
steps 1-2 above, then run only the focused checks named in the envelope and
`git diff --check`. Do not inherit the standard design, TDD, consumer-scan,
independent tester/reviewer, full-suite, browser, native, installation, or live-runtime
steps unless the focused acceptance criteria require them. Record what was not run.

Escalate to the standard route before continuing if eligibility is uncertain, scope
expands, an unexpected failure appears, a cross-surface consumer is implicated, or
acceptance depends on runtime behavior. Do not stretch the fast path to finish work
that no longer qualifies.

Do not modify or discard unrelated changes. Do not patch only the staged runtime.
Change source first; installation and live checks belong to the verification route.

## Developer result

Return:

- Status and current head commit or explicit uncommitted state.
- Files changed, owner, and reason for each.
- Reproduction and red/green test evidence.
- Commands run with outcomes.
- Claimed behavior and affected risk categories.
- Unverified behavior, blockers, and recommended next state.

Implementation approval does not authorize commit, push, Slack or GitHub writes,
review requests, merge, deployment, or self-certification. The coordinator routes the
result to independent testing and review.
