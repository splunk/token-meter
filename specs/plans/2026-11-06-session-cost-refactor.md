# Session-cost refactor: analysis and plan

Historical point-in-time analysis (2026-11-06). Plan only; nothing implemented.

## Method

- Token Meter MCP `stats` grouped by `session_id`, 2026-10-01 → 2026-10-31,
  sorted by `cost_usd`, top 100. Dollar figures below come from Token Meter.
- To attribute those sessions to projects and tools, a local script read the
  matching Claude Code transcripts for **counts and sizes only**. No content
  was printed. Transcript-derived figures double-count history copied into
  forked or resumed sessions, so treat them as **shares**, not dollars.
- "Carry" = tool-result characters × the turns that result stayed in context.
  It approximates how much each result was paid for again as cache reads.

## Findings

### Where the money went

| Fact | Value |
|---|---|
| October spend, all sessions | $1,194 |
| Top 100 sessions | $1,010 (85%) |
| …of which token-meter repo sessions | 68 sessions, **$751** |
| Opus 5.5 share of the month | $1,050 |
| Top-100 tokens: cache read / cache write / output / uncached input | 1.25 B / 122 M / 6.4 M / 0.8 M |
| Estimated cost split (Opus rates) | cache writes ≈ 45–50%, cache reads ≈ 35–40%, output ≈ 10% |

Output is a small share. Almost all the cost is context: re-reading a large
context every turn, and rebuilding it when the cache expires.

### Context size is the multiplier

| Context per model turn | p50 | p90 | max |
|---|---|---|---|
| Main sessions | ~300 k | ~705 k | ~998 k |
| Subagents | ~54 k | ~226 k | ~688 k |

- Starting context before any work: main ≈ 60 k tokens, subagent ≈ 15 k.
  `specs/AGENTS.md` (21.7 KB, ≈ 5.4 k tokens) is part of that in every main
  session and every subagent.
- About 620 cache rebuilds above 50 k tokens happened in main sessions. 469 of
  them came after waiting on a user reply (median gap 19 min). Each rebuilt
  about 400 k tokens. The codebase can't shorten the user's reply time, but a
  smaller working context makes every rebuild cheaper.

### What fills the context: navigating huge files

Share of carried tool-result volume across the 68 token-meter sessions (560
transcripts, 492 of them subagents):

| Source | Calls | Carry share |
|---|---|---|
| `grep`/`rg` via Bash | 8,459 | **31%** |
| `sed -n` range reads | 1,569 | **15%** |
| Reading `/tmp` scratch/persisted outputs | 995 | 8.5% |
| Ad-hoc `python3` probes | 2,091 | 8.4% |
| `git diff` | 1,877 | 3.6% |
| Targeted `unittest` runs | 2,055 | 3.0% |
| `Read` of `work_insights.py` | 133 | 3.0% |

The grep/sed traffic is aimed at a few files:

| File | Size | grep/sed calls | Output |
|---|---|---|---|
| `page.html` | 1.26 MB (≈ 315 k tokens, 13.2 k lines, 755 JS functions, 791 KB script, 333 KB CSS) | 4,350 | 9.5 MB |
| `token_meter/app.py` | 631 KB (15 k lines, 643 top-level defs) | 2,472 | 5.5 MB |
| `services/work_insights.py` | 106 KB | 1,063 | 2.2 MB |
| `tests/test_meter.py` | 1.43 MB (26 k lines, 1,034 tests, 97 classes) | 674 | 1.5 MB |

`page.html` is larger than the context window, so agents can't read it whole.
They search it over and over, and every search result then stays in context.
`app.py` and `test_meter.py` work the same way. The monolithic files are the
main codebase-side cost driver.

### Workflow signals (not code, but they multiply cost)

- Subagent turns were about 98% Opus (20,148 Opus vs 404 Sonnet), even though
  the tier policy sends the tester and extra reviewers to `sonnet`. This matches
  the known alias fallback on this machine. The `*-local` agents fix it only
  if they are actually spawned.
- 1,101 full-suite invocations across these sessions, against a policy of at
  most once per head.

## Plan (ranked by expected impact)

Success metric: median main-session context per turn drops from ~300 k toward
≤ 150 k, and grep/sed calls per session halve. Measure both with Token Meter
on the next ~20 comparable sessions after each phase. Cost scales roughly
with context × turns, so halving resting context should cut token-meter
session cost by an estimated 30–45%. That number is a hypothesis to confirm
in Phase 1, not a promise.

### Phase 0: guardrails first (small)

1. Add a source-size ratchet to `tests/contracts/test_architecture_guards.py`:
   a per-file byte baseline that can only shrink, plus a ceiling for new files
   (e.g. 80 KB). This stops the monolith from regrowing during the split.
2. Save the survey script as a local tool so each phase reports the same
   numbers. Keep it under ignored local state, or make it content-free and
   commit it under `scripts/`.

### Phase 1: split `page.html` (largest win)

- Source layout: `dashboard/index.html` (shell),
  `dashboard/styles/{tokens,components,<view>}.css`, and
  `dashboard/js/{core,router,charts,<view>}.js`, one per top-level view
  (Sessions, Spend, Models, Subagents, Efficiency, Work, Git, Learn, Tools,
  Settings). Target ≤ 60 KB per file, so an agent can read the one view it is
  working on in a single call.
- The server assembles the page in memory at startup with stdlib string
  concatenation, in a fixed order. No build tool, no hosted assets, and the
  page stays local-only and dependency-free. `/health` keeps validating the
  assembled page.
- Do the move mechanically (a one-off script that cuts on existing section
  markers) and verify byte-identical assembled output against today's
  `page.html` before any cleanup. Don't have an agent hand-edit 1.26 MB.
- Update the consumers together: `runtime-manifest.txt` and parity checks,
  `scripts/install`, `scripts/install-windows.ps1`, the Linux installer, the
  design-system contract, `scripts/style-snapshot.js`, the embedded-JS parse
  command in `specs/AGENTS.md`, and the 69 `page.html` string assertions in
  `tests/test_meter.py`. Route these through one `assembled_page()` test
  helper.
- Risk: high-surface (installer, runtime parity, design contract). This needs
  the high-risk route: one tester and two reviewers.

### Phase 2: split `tests/test_meter.py`

- Move its 97 classes into `tests/<surface>/test_*.py`, mirroring the source
  modules (dashboard views, server/HTTP, installer, menubar source, parsers),
  with shared fixtures in `tests/support/`.
- Benefit: agents find tests by file name instead of grepping 1.4 MB. Targeted
  runs (`unittest tests.dashboard.test_spend`) replace full-file runs. Keep
  `scripts/run-tests` and discovery unchanged.
- Coverage-preserving: assert the same test count before and after the move.

### Phase 3: split `token_meter/app.py`

- Pull out cohesive modules along existing seams: HTTP routing/handlers,
  settings and atomic writes, budgets and caps, the menubar payload, discovery
  and rebuild orchestration, and update/lifecycle. `app.py` keeps composition
  plus compatibility re-exports (the `meter.py` facade contract is
  unchanged).
- Do the same for `services/work_insights.py` (queue/pacing vs Ollama client
  vs label ledger) and `domain/work.py`, which together draw about 3.7 MB of
  grep/sed/Read traffic.
- Update `specs/ARCHITECTURE.md`'s component map in the same change.

### Phase 4: cheaper agent defaults in the repo

- Trim `specs/AGENTS.md` toward ~8 KB. Keep the invariants (privacy, pricing,
  dashboard order, design rules) and move the long procedural flows
  (reporter-end-to-end, Slack/GitHub contracts, PR-management replies) into
  the existing on-demand skills under `.agents/skills/`. Estimated effect:
  about 3 k fewer tokens on every turn of every main session and subagent,
  roughly 5–7% of cache reads.
- Add bounded helpers that replace the ad-hoc probes, which account for about
  17% of carry (`python3` scratch and `/tmp` reads):
  - `scripts/show <file> <symbol>` prints one function or CSS block;
  - `scripts/check-page` runs JS parse + design contract with a terse summary;
  - an endpoint probe that summarizes `/health`/`/menubar` instead of dumping
    JSON.
- Make terse output the default for `scripts/run-tests`: failures plus a
  one-line summary, with `-v` opt-in. Many of these output-size controls may
  already exist.
- Workflow: confirm subagents spawn the `*-local` tiers, and enforce one full
  suite per head. These need no code change, but on today's evidence the
  savings are comparable to a code phase.

### Order and checkpoints

Phase 0 → 1 → measure → 2 → 3 → 4. Phase 4's AGENTS.md trim and helpers are
independent and could run in parallel with Phase 1. Stop and reassess after
Phase 1: if context per turn and grep counts don't move meaningfully, the
cost is behavioral (session length, idle rebuilds) rather than structural,
and Phases 2–3 should shrink.

## Not verified

- Exact Opus 5.5 unit prices weren't checked against the repo's pricing
  table. The cost split is an estimate from token mix.
- Transcript shares include forked-session history duplication.
- The 22 top-100 sessions outside this repo were not analyzed for tools.
- Some sessions show a covered execution count of exactly 180. That could be
  a bound in Token Meter's per-session accounting, worth a separate check
  before relying on per-session totals.
