# Dashboard Design System Refactor

Status: approved for implementation on branch `codex/design-system-refactor`
(2026-10-06). Visual language stays the one documented in
[DESIGN.md](DESIGN.md). This change moves the CSS behind that language onto
tokens and adds guards so it stays there.

## Problem

`page.html` holds a 272 KB inline stylesheet. It was built by appending
versioned blocks (`shared-spectrum-system-v1`, `subagent-investigation-v3`, and
others) on top of a base layer. Survey on `43a05fe`:

| Signal | Count |
|---|---|
| Rule blocks / selectors repeated | 2,917 / 427 |
| Longest single source line | 8,725 characters (dozens of rules) |
| Distinct `font-size` values | 56, including 7.5, 8, 8.5, 8.8, 9 px |
| Distinct `font-weight` values | 24 (500–860 in 10-unit steps) |
| Distinct `border-radius` values | 23 |
| Hex / rgba literals in CSS | 119 / 721 (341 distinct) |
| Hex literals in JavaScript palettes | ~60, spread over 8 constants |
| `body.spectrumApp` override rules | 60 |

Readability:

- 69 rules render `--faint` text at 7.5–10.5 px.
- `--faint` (`#7d8ba0`) is 4.71:1 on `--panel2` and 4.16:1 on `--panel3`,
  below WCAG AA (4.5:1) for small text.
- SVG chart labels use 9–10 px.

Token names are misleading: `--orange`, `--orange2`, and `--orange3` are cyan,
blue, and sky. Two `:root` blocks plus a `body.spectrumApp` block redefine the
same roles (`--line`, `--panel`, and the `--spectrum-*` copies of them).

## Goals

1. Keep one token source at the top of the stylesheet: primitives, then
   semantic roles, then scales.
2. Move every color, type size, weight, radius, and focus ring onto those
   tokens. Spacing snaps to one documented scale.
3. Raise the readability floor: text at least 11 px, with every text token at
   4.5:1 or better on every surface token.
4. Add a design-system contract test so drift fails CI instead of
   accumulating.
5. Put one top-level rule per line so diffs and reviews work per rule.
6. Make DESIGN.md describe the token layer that contributors actually edit.

## Non-goals

- No split into separate CSS or JS files. ARCHITECTURE.md defines a
  single-file dashboard, and the runtime manifest depends on it.
- No layout redesign, route change, or copy change.
- No native menu-bar or marketing-site change.
- No change to provider or semantic color meaning.
- No blind removal of the `body.spectrumApp` layer or merging of repeated
  selectors. Both change cascade order, and the risk outweighs the benefit
  without a full state-coverage harness. Both are listed as follow-ups.

## Approach (chosen)

The stylesheet stays in place and is transformed by a deterministic,
reviewable conversion script. Visual change is limited to the deliberate
readability scale.

Alternatives considered:

- **Extract `page.css`.** Better file ergonomics, but it changes the HTTP
  surface, runtime manifest, installer parity, and the single-file
  architecture contract. Deferred.
- **Rewrite components as new primitives.** Highest payoff, but it touches
  every route's markup and JavaScript, and the regression surface is too large
  for one change. The token layer added here is its prerequisite.

### Token architecture

All tokens live in one `:root` block that opens the stylesheet.

1. **Primitives**: RGB channel triples for the palette hues. Translucent uses
   become `rgb(var(--cyan-rgb) / .12)`, which keeps every existing alpha value
   exact while giving each hue one source of truth.
   - `--white-rgb`, `--black-rgb`
   - `--ink-rgb`, `--panel-rgb`, `--panel2-rgb`
   - `--cyan-rgb`, `--blue-rgb`, `--sky-rgb`, `--violet-rgb`, `--orange-rgb`
   - `--green-rgb`, `--red-rgb`, `--purple-rgb`, `--pink-rgb`, `--mint-rgb`
2. **Semantic roles**: the existing short names, kept because they already
   describe a purpose. Renaming them would only add churn.
   - Surfaces: `--bg`, `--bg2`, `--panel`, `--panel2`, `--panel3`
   - Lines: `--line`, `--line2`, `--line3`
   - Text: `--fg`, `--dim`, `--faint`
   - States: `--accent`, `--good`, `--warn`, `--bad`
   - Data series: `--c-*` and `--chart-*`
   - The misleading accent names are renamed: `--orange`, `--orange2`, and
     `--orange3` become `--cyan`, `--blue`, and `--sky`.
   - The `--spectrum-*` names stay as the spectrum component tokens.
     Duplicates that copy a semantic role (`--spectrum-ink`,
     `--spectrum-panel`, `--spectrum-panel-raised`, and the like) are
     removed, with references pointing at the role.
3. **Scales**:
   - Type sizes: `--fs-11` `--fs-12` `--fs-13` `--fs-14` `--fs-16` `--fs-18`
     `--fs-20` `--fs-22` `--fs-24` `--fs-28` `--fs-36` `--fs-44` `--fs-56`,
     plus five fluid roles that replace ten one-off `clamp()` values:
     `--fs-hero-fluid`, and `--fs-readout-xl`, `-lg`, `-md`, `-sm`.
     Consolidating them moves a few readouts by up to about 4px at 1024 px,
     for example the budget hero value shrinks from 47px to 43px.
   - Weights: `--fw-medium` (500), `--fw-semibold` (600), `--fw-bold`
     (700), `--fw-heavy` (800). No rule set 400, so regular stays the
     inherited default rather than an unused token.
   - Radii: `--radius-2xs` (2), `--radius-xs` (4), `--radius-sm` (6),
     `--radius-md` (8), `--radius-lg` (12), `--radius-pill`,
     `--radius-round`. Its two uses moved to `--radius-md`, and the old
     `--radius` was removed.
   - Focus: `--focus-ring` (the repeated `0 0 0 3px` cyan halo).
   - Spacing scale (enforced, not var-wrapped): 0, 1, 2, 3, 4, 6, 8, 10, 12,
     14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48, 64 px. Off-scale values snap
     to the nearest step, with ties rounding down, so odd values from 5 to 19
     px each lose 1 px. Rounding down offsets the larger text from the
     readability floor. Spacing
     stays as literal pixel values because wrapping about 1,500 compact
     declarations in `var()` would hurt readability more than it helps. The
     contract test enforces the scale instead.

Size mapping, which is deliberate and readability-raising:

- 7.5–11 → 11
- 11.5–12 → 12
- 12.5–13 → 13
- 14 → 14
- 15–16 → 16
- 16.5–18 → 18
- 19–20 → 20
- 21–22 → 22
- 23–25 → 24
- 27–31 → 28
- 34–40 → 36
- 42–46 → 44
- 52–66 → 56

Weight mapping:

- ≤450 → regular
- 451–560 → medium
- 561–690 → semibold
- 691–760 → bold
- 761 and above → heavy

Radius mapping:

- 1–2 → 2xs
- 3–5 → xs
- 6–7 → sm
- 8–9 → md
- 10–13 → lg

### Readability changes

- Text floor of 11 px across CSS and SVG chart text. Where the new size
  overflows, the fix is local: tighten letter-spacing, allow wrapping, or
  widen a column. Never shrink back below 11.
- `--faint` brightens until it is at least 4.5:1 on `--panel3`, the lightest
  surface it sits on.
- Uppercase labels at 11 px keep letter-spacing of at most `.06em`.

### JavaScript palettes

Provider, comparison, and model color arrays move into one `THEME` object
next to `CHART`. Color values stay the same; chart text sizes use the new
floor. Swatches are data colors, so they stay hex but live in one place. The
contract test allows hex in JavaScript only inside that object and `CHART`.

### Guard: `tests/contracts/test_design_system.py`

- Hex colors appear only inside the token `:root` block.
- Every `font-size` is a `var(--fs-*)` token. Every `font-weight` is a
  `var(--fw-*)` token (or `inherit`). Every `border-radius` is a token,
  `0`, `inherit`, or a token-composed shorthand.
- Every px value in `gap`, `padding`, and `margin` is on the spacing scale.
- Raw `rgba(r,g,b,a)` literals are counted against a ratchet baseline that
  may only decrease.
- Every `var(--x)` used in CSS or JavaScript resolves to a declared token or
  to a documented runtime-set custom property. Every declared token is used.
- Every text token is at least 4.5:1 on every surface token, computed in the
  test.
- Font tokens are at least 11 px. SVG `font-size=` attributes in templates are
  at least 11.
- The stylesheet keeps one top-level rule per line.

### Formatting

A depth-aware formatter splits the stylesheet at top-level rule boundaries.
Each rule or `@media` block becomes one line, and the declarations inside it
are untouched, so existing test pins keep matching. Section comments get a
consistent `/* == Section == */` banner.

## Verification

1. **Computed-style diff harness.** A script run through the browser preview
   on every top-level route and Sessions sub-tab, at 1440 and 1024 px. It
   records the computed styles of each unique element signature before and
   after each phase:
   - Pure-token phases (format, color channels, token consolidation) must
     show zero diff.
   - Scale phases must diff only in the intended properties.
2. Overflow scan on every route at 1024 and 1440 px: no element whose
   `scrollWidth` exceeds `clientWidth` gains overflow, and the page has no
   horizontal scroll.
3. The full unit suite, embedded-JS parse, `py_compile`, and
   `git diff --check`.
4. `./scripts/install`, then `/health`, `/menubar`, and a staged-runtime
   parity check.
5. One independent tester and two project reviewers
   (requirements-and-correctness, regressions-and-maintainability) on the
   final head.

## Deliberate visual deltas

- Text from 7.5 to 10.5 px rises to 11 px, including SVG chart labels.
- `--faint` brightens from `#7d8ba0` to `#8693a6`.
- Weights round to the nearest 100 within the mapping above.
- Odd spacing drops by 1 px.
- These near-identical surface hexes merge, each within 5/255 per channel:
  - `#111922` and `#101820` → `--panel`
  - the `#0d1218` family → `--bg2`
  - `#151e27` and `#141d26` → `--panel2`
  - `#c2a5ff` → `--violet`
- A few small text-color merges, each within 8/255:
  - `#e6f9ff` → `--sky-pale`
  - `#ffd6d8` → `--bad-text-strong`
- Larger differences keep their own tokens: `--bg-lift`, `--soft`, and
  `--on-accent-deep`.
- The rail product name drops from 15px to 14px, which fits the full "Token
  Meter" in the 184px rail at the heavy weight.
- `.compareTabCount` becomes a true pill, matching its 18px height.
- SVG charts that used `preserveAspectRatio=none` with a fixed viewBox
  (session detail, Models, Git daily, Spend scatter, and Efficiency
  sparklines) now size their viewBox to the element. A ResizeObserver
  redraws them when their box changes. For Models and Efficiency that
  re-renders the whole view, which is idempotent and debounced. The Git hit
  grid moves from percentage to pixel insets. Text and markers are no longer
  stretched, and labels render at a true 11px or more.
- The `.agentDiscoveryClose` `font` shorthand was invalid in the base, so the
  button rendered the browser's default font. It now gets its intended
  700/18 px.

## Follow-ups (not in this change)

- Bring `performance.html` onto the same token root. This change only raised
  its `--faint`, applied the 11 px floor, and capped tracking at `.06em`.
- Lower the ratchets. 98 CSS and 10 script translucent literals remain,
  mostly one-off dark surface tints.
- Regenerate `.impeccable/design.json` with Impeccable's `document` command.
  Its component preview snippets predate this branch.

- Fold `body.spectrumApp` overrides into base rules, one route at a time,
  behind the style-diff harness.
- Merge the 427 repeated selectors.
- Consider extracting `page.css` once the token layer is stable.
- Pin `pbakaus/impeccable` as an external skill in
  `agent-toolchain.lock.yaml` so `audit` and `document` are available to
  every host.

## Phase 2: components and provider identity (2026-10-07)

Approved by the owner as an addition to the same pull request. The scope is
shared components, plus unifying provider colors together with the Models
colors.

### Provider identity

- One `--provider-*` token per runtime replaces three separate palettes: the
  session-card harness, Spend, and badge colors. Budget rows and the Models
  family anchors also use it.
- The palette is picked for at least 4.8:1 contrast on the lightest panel,
  provider-to-provider ΔE ≥ 30, and ΔE ≥ 24 from the semantic colors.
- Kiro moves to gold, to separate it from Codex blue and Claude-3P violet.
  Gemini has no runtime, so it has no token.
- Surfaces resolve color from the runtime id with `providerColor(id)`
  instead of the backend's color class, which reported Kiro, Pi, and Hermes
  as neutral. Kiro cards and badges are now gold. Pi and Hermes use slate
  everywhere, including budget rows, which used to fall back to cyan.
- The Models share, speed, spend-bar, and table swatches drop the five-color
  rank palette. They now use provider-anchored family shades from
  `modelRankedColors()`, assigned in rank order within each runtime. Each
  family has five shades, so the visible top five never repeat a color. The trend chart keeps `modelColor()`'s stable per-model
  shades, so a model's trend shade can differ from its ranked-chart shade.
  The hue family is always the same.
- Provider badges use an 8% tint, so badge text keeps 4.5:1 over its fill.
- Each Models family starts with its provider color and keeps its other
  shades inside the same hue band. The closest pair of shades from different
  families improves from ΔE 4 to ΔE 20, and in-family spacing stays at
  ΔE 10 or more. That matches the original families' ΔE 11.

### Components

- The new components are `.metric`, `.emptyState`, `.chartTip`, and the
  `.chip` status pill with `.tone-warn`.
- Each is written with `:where()`, so it has zero specificity and sits after
  the base primitives. Screens keep their own size and spacing. Components
  own color, type, tracking, and surface.
- Migrated:
  - 12 metric-tile families
  - 15 empty states
  - 6 tooltips
  - 4 status pills
- Not migrated:
  - interactive filter chips, which are controls
  - table-cell, SVG, and in-tooltip empty states
  - the Sessions hero empty state
  - tiles with their own child classes (`capStat`, `deliveryMetric`)
- No JavaScript markup helpers. Templates only gain the component class.
  Generating them through helpers would rewrite about 60 heavily pinned
  templates for no visual benefit.

### Dead CSS

- 301 rules were removed whose classes appear nowhere in markup or script:
  the retired Daily, Global, and waste views and the older Subagent layouts.
- Selectors containing `:is()`, `:where()`, or `:not()` were kept, because
  a dead alternative can sit beside a live one.
- A computed-style diff of every route and the session detail page, at
  1440 px and 1024 px, showed no change beyond live-data jitter.
- The contract test now rejects unreferenced classes. The allowlist holds
  only classes the script builds at runtime plus a few short legacy names,
  and it may only shrink.

### Visual deltas

- Provider colors change on every surface they appear on, including Models
  share and speed charts.
- Metric tiles:
  - Labels use `.04em` tracking. They were 0, `.035em`, or `.045em` before.
  - Values use the mono heavy number face. `.sessionEfficiencyMetric` and
    `.currentSessionMetric` values move from sans to mono.
    `.deliveryDayMetric` and `.subagentEvidenceMetric` values move from bold
    to heavy, and `.deliveryDayMetric` values from `--dim` to `--fg`.
  - Notes use `--dim`. `.previewKpi .sm` was `--faint`.
- Empty states use 12px `--dim` copy. Before, `.modelEmpty` was 14px
  `--faint`, and `.commandEmpty` and `.signalEmpty` were `--faint`. They are
  grid-centered with 4px gaps.
- Tooltips share one surface:
  - the cyan edge at .42
  - the 8px radius (Efficiency and Git used 6px with a `--line2` edge)
  - the dark gradient and one shadow
- Status pills use the base `.chip`:
  - the `--line2` border, which now applies to every chip
  - a .045 fill and the base inset highlight
  - the shared warn tone at .4 border / .08 fill

## Owner refinements (2026-10-07)

- **Claude color:** Claude becomes rust, `#e3825c`. This is Anthropic's
  terracotta, lightened so badge text keeps 4.5:1. Its Models family is
  re-anchored on it.
- **Claude-3P color:** Claude-3P moves from orchid-pink to violet, `#a46af4`.
  Its family stays within ΔE 20 of no other runtime's shades.
- **Comparisons:** Efficiency's period comparisons drop their bordered boxes.
  The new `.trendDelta` component shows each one to the right of its number
  inside `.valueWithDelta`: a 20px percent over a 12px period label. Under
  the 1180 px breakpoint, Git values step down to 24px so all three deltas
  stay beside their numbers at 1024 px.
- **Git comparisons:** Git's Pushed lines, Spend / 1K, and Push yield gain the
  same component in neutral tone, replacing the inline "129% higher" note.
- **Shared helpers:** `renderTrendDelta()`, `trendTone()`, and
  `percentTrendDelta()` are shared helpers for any future comparison.
- **Performance page:** its navigation rail gains the same number hints and
  ⌥1–9 shortcuts as the dashboard rail.
- **Agent guidance:** `specs/AGENTS.md` and the development, review, and
  verification skills now point agents at the design system before any
  styling change.
