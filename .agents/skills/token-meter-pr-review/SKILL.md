---
name: token-meter-pr-review
description: Use when asked to review, check, or assess a Token Meter pull request (often a contributor PR by URL or number) for standards, tests, safety, privacy, data exposure, pricing, or merge readiness.
---

# Token Meter PR Review

## Overview

The coordinator orchestrates an independent, gated review of one pull request head.
It does not fix the PR. The contributor's description, validation, and limitations
are claims to verify, not evidence. This skill sequences the existing
`token-meter-verification` and `token-meter-review` skills. Their checklists still
apply in full.

## 1. Intake (exact target)

```bash
gh pr view N --repo splunk/token-meter --json title,body,author,headRefOid,baseRefName,files,comments,reviews,statusCheckRollup,mergeable
gh pr diff N --repo splunk/token-meter --name-only
```

Record the following:
- the head SHA;
- the merge base with `origin/main`;
- the linked issue and any requirements agreed there (`gh issue view`, including comments);
- the claimed validation;
- each stated limitation.

Requirements agreed on the issue are the acceptance criteria.

## 2. Isolate

```bash
git fetch origin main "pull/N/head:pr-N"
git worktree add /private/tmp/tm-prN pr-N
```

- Never check out the PR in the user's worktree, which may contain unrelated changes.
- Never run `./scripts/install` from a PR head: it replaces the user's installed
  runtime. Live checks use an isolated server on a spare port with fixture data,
  never the real `~/.claude`, `~/.codex`, provider homes, settings, or port 8722.

## 3. Classify risk and size the gate

Read `.agents/workflow/review-policy.yaml`. If the diff touches any of the
following, the change is high risk:
- privacy-boundary
- pricing-semantics
- provider-parsing
- deletion
- persistence
- installer-or-staged-runtime
- native-menu
- cross-platform
- shared-helper

A high-risk change needs one tester plus two reviewers with distinct lenses.
Other changes need one tester and one reviewer. New runtimes are always high risk.

## 4. Dispatch in parallel (one message)

Dispatch background agents with self-contained prompts. Each prompt contains:
- the worktree, base SHA, and head SHA;
- the issue requirements;
- the PR's claims and limitations, framed as "verify or refute";
- the instruction to stay read-only, not post to GitHub, and not install;
- the required result: findings first, each with severity, file:line at head,
  a failure scenario, and evidence, followed by the verified claims and the
  unverified items.

| Agent | Lens | Must cover |
|---|---|---|
| `token-meter-tester` | test-quality-and-real-evidence | Full suite and the claimed test count, `py_compile`, embedded JS parse, the design-system contract, `git diff --check`, `runtime-manifest.txt` coverage of new files, and an isolated live server with fixture payload greps (prompt text, a fake `sk-test-` secret, `/private/tmp` paths, raw exceptions), plus refused or allowed deletion |
| `token-meter-reviewer` | privacy-and-data-boundaries | Read-only provider inputs; SQLite opened read-only, authorizer scope, and fallback paths; symlinks and traversal outside roots; bounded reads and ReDoS; absolute paths, URIs, prompt text, or free-form names leaking into HTTP, `/menubar`, MCP, or `projections.py`; every delete path (route, trash helper, UI, MCP, bulk); env-var overrides; no network or telemetry; stdlib only |
| `token-meter-reviewer` | requirements-and-correctness + regressions-and-consumers | Issue requirements met; estimates labeled `est` on every surface; unavailable evidence never shown as 0; unknown models unpriced, not $0; price tables cite their source, effective dates and long-context tiers are correct, and other providers' pricing and stored overrides are unchanged; models scoped by runtime; registry, filters, budgets, and `--provider-*` color; test assertions not loosened; revision and cache invalidation; top-level navigation order preserved |

Add a reviewer when the diff warrants another lens:
- `platform-and-native-behavior` for Swift, tray, Windows, or Linux changes;
- `runtime-and-packaging-parity` for installer or manifest changes.

## 5. Consolidate and verify

While the agents run, do not repeat their work. When they return:
1. Reproduce every blocking or important finding yourself, with a command or
   quoted lines at the head SHA. Downgrade or drop any finding you cannot
   reproduce.
2. Deduplicate overlapping findings across agents.
3. Go through each stated limitation. Accept it, or upgrade it to a finding when
   it hides a real risk, for example "estimates are about 10x low" when those
   estimates feed budgets.
4. Treat a claim in the PR body that the evidence contradicts as a finding.

## 6. Report to the user

1. **Verdict:** merge-ready, changes requested, or blocked, bound to the head SHA.
2. **Findings:** a table with severity, area, file:line, issue, and suggested fix,
   most severe first.
3. **Security and data exposure:** an explicit answer, even when there is none.
4. **Limitations:** a disposition for each stated limitation.
5. **Verified:** the checks that passed, with exact results.
6. **Not verified:** for example target-host Windows or Linux behavior, a live
   browser at 1440 and 1024 px, or the native menu.

## 7. Contributor reply (when authorized)

A request to review or manage a PR is standing approval for one status comment.
Before posting it:
- the `token-meter-communication-manager` drafts it;
- check that no equivalent comment exists for the same head;
- thank the contributor, give the evidence-backed status and the concrete
  requested changes, and sign it "— Pratik's agent".

After posting, read the comment back. Approving, requesting changes as a formal
review, merging, closing, labeling, and pushing each need separate approval.

## 8. Freshness and cleanup

- Any new push to the PR invalidates every result. Re-run the gate at the new head.
- When done, remove the worktree with `git worktree remove /private/tmp/tm-prN`
  and delete the `pr-N` ref.

## Common mistakes

| Mistake | Fix |
|---|---|
| Trusting "N tests OK" from the PR body | The tester reruns the suite at the head SHA |
| Reviewing in the user's worktree | Use a separate `/private/tmp` worktree |
| Installing the PR to "check live" | Use an isolated server and fixtures, never `./scripts/install` |
| Relaying agent findings unverified | Reproduce blocking and important findings before reporting |
| Missing a delete path | Enumerate the route, trash helper, UI, MCP, and bulk paths |
| Treating limitations as out of scope | Disposition each one; some are findings |
