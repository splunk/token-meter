---
name: token-meter-verification
description: Use when Token Meter changes or claims need independent source, test, runtime, native, browser, privacy, or platform verification.
---

# Token Meter Verification

## Purpose

Independently verify the task envelope's acceptance criteria against one exact head.
Do not modify tracked files or repair failures while acting as tester.

## Build the verification matrix

Read `specs/AGENTS.md`, the task envelope, developer result, current diff, and
`.agents/workflow/review-policy.yaml`. Select checks from the surfaces the diff can
affect:

| Surface | Evidence |
|---|---|
| Python and contracts | Focused tests, full suite when proportionate, and `py_compile` |
| Dashboard | Embedded-JavaScript parse plus live wide-desktop and 1024-pixel-laptop interaction checks |
| Dashboard styling | `tests.contracts.test_design_system`, a `scripts/style-snapshot.js` base-versus-head diff at 1440 and 1024 px (changes limited to the claimed deltas, no new overflow or clipping), and rendered HTML and SVG text of 11px or more |
| Native menu | Swift compile, deterministic smoke output, and live menu behavior |
| Packaging | Installer syntax, manifest coverage, installed-runtime revision, and source/runtime parity |
| HTTP behavior | `/health`, relevant endpoint payloads, cache/privacy boundaries, and failure states |
| Cross-platform | Target-host parse, launch, interaction, update, and uninstall evidence |

Run focused checks first, then the broader checks required by risk. Synthetic fixture
passes do not replace real upstream, installed-runtime, or visible behavior where the
acceptance criteria depend on them.

## Evidence rules

- Record the command or interaction, outcome, and inspected head.
- Use `passed`, `failed`, `blocked`, or `not-run`; never turn missing evidence into a
  pass or measured zero.
- Separate source checks from live installed behavior.
- Confirm source/runtime parity after installation rather than assuming the checkout
  is what the user sees.
- Preserve exact target-host limitations. macOS evidence does not prove Windows or
  Linux behavior.
- Stop and report a failure; return the change to the developer instead of fixing it.

## Tester result

Return acceptance-criteria coverage, commands and live checks, counts where useful,
the exact head, failures, not-run checks, unverified behavior, blockers, and the
recommended next state. A later tracked code or test change invalidates the result.
