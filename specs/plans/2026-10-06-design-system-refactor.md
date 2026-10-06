# Dashboard Design System Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Move the `page.html` stylesheet onto one token layer with enforced
scales, raise text readability, and guard both with a contract test.

**Architecture:** Each phase is a deterministic transform of the inline
`<style>` block. A browser computed-style snapshot (`scripts/style-snapshot.js`)
is taken before and after each phase:

- Pure-token phases must produce zero diff.
- Scale phases may diff only in the properties they target.

`tests/contracts/test_design_system.py` turns each phase's invariant into a permanent
guard.

**Tech Stack:** Python 3 standard library (`unittest`, `re`), inline CSS and
JavaScript in `page.html`, and the Claude Browser preview for computed styles.

**Spec:** [specs/2026-10-06-design-system-refactor-design.md](../2026-10-06-design-system-refactor-design.md)

## Global Constraints

- `page.html` stays single-file. No new served assets, no runtime-manifest
  change.
- `meter.py` and `token_meter_mcp.py` stay standard-library only. Tests use
  only `unittest` and the standard library.
- Text at least 11 px. Every text token is at least 4.5:1 on every surface
  token.
- Spacing scale (px): 0 1 2 3 4 6 8 10 12 14 16 18 20 24 28 32 36 40 44 48 64.
- Supported widths are wide desktop (1440 px) and 1024 px laptop. Sub-1024
  media rules are graceful degradation only.
- Preserve every route, hash, label, and provider or semantic color meaning.
- Never stage `specs/plans/active.md`.

---

### Task 1: Style snapshot harness

**Files:**

- Create: `scripts/style-snapshot.js` (browser-eval IIFE; no dependencies)

**Interfaces:**

- Produces: `window.__tmStyleSnapshot()`. It returns
  `{route: {signature: {prop: value}}}` for a fixed property list. The
  signature is `tag.classes` joined up the ancestor chain (depth 4), with
  numeric content stripped.

- [ ] Write a script that visits each hash route (`#sessions`,
  `#sessions-all`, `#sessions-compare`, `#sessions-subagents`, `#spend`,
  `#models`, `#subagents`, `#efficiency`, `#git`, `#learn`, `#tools`,
  `#settings`). For each route it waits two animation frames, then for every
  visible element records `color`, `background-color`, `background-image`,
  `border-*-color`, `border-radius`, `box-shadow`, `font-size`,
  `font-weight`, `line-height`, `letter-spacing`, `padding`, `margin`, and
  `gap`. It also lists elements where `scrollWidth > clientWidth + 1` and
  `overflow` is visible.
- [ ] Take the baseline at 1440 and 1024 px. Save it to
  `/private/tmp/tm-style-base-{w}.json`.

### Task 2: One top-level rule per line

**Files:**

- Modify: `page.html` (style block only)
- Test: `tests/contracts/test_design_system.py`

- [ ] Write the failing test
  `test_stylesheet_has_one_top_level_rule_per_line`. It parses the style block
  with a depth counter and asserts that no line closes more than one top-level
  block.
- [ ] Run `python3 -m unittest tests.contracts.test_design_system -v`. Expect FAIL.
- [ ] Apply the depth-aware splitter. Split only where depth returns to 0, so
  `@media{...}` stays whole and declarations are untouched.
- [ ] Run the full suite. All 79 CSS pins must still match.
- [ ] Snapshot diff: zero.
- [ ] Commit: `refactor: format dashboard stylesheet one rule per line`.

### Task 3: Consolidated color tokens

**Files:**

- Modify: `page.html`
- Test: `tests/contracts/test_design_system.py`

- [ ] Add these failing tests:
  - `test_hex_colors_only_in_token_root`
  - `test_all_css_vars_resolve`
  - `test_no_unused_tokens`
  - `test_rgba_literal_ratchet`
- [ ] Merge the base `:root`, the spectrum `:root`, and the `body.spectrumApp`
  custom properties into one leading `:root`. Final spectrum values win, which
  matches what currently renders inside `<body>`.
- [ ] Add RGB channel primitives. Rewrite every
  `rgba(r,g,b,a)`, `rgb(r,g,b)`, and hex whose channels match a primitive to
  `rgb(var(--x-rgb) / a)` or the semantic token.
- [ ] Rename `--orange`, `--orange2`, and `--orange3` to `--cyan`, `--blue`,
  and `--sky` across CSS and JavaScript. Repoint duplicate `--spectrum-*` roles
  and delete unused tokens.
- [ ] Remaining one-off rgba values set the ratchet baseline.
- [ ] Snapshot diff: zero. The full suite passes after updating any pins that
  contained a renamed token.
- [ ] Commit: `refactor: centralize dashboard colors as tokens`.

### Task 4: Type, weight, radius, and focus scales with the readability floor

**Files:**

- Modify: `page.html`
- Test: `tests/contracts/test_design_system.py`

- [ ] Add these failing tests:
  - `test_font_sizes_use_scale_tokens`
  - `test_font_weights_use_scale_tokens`
  - `test_radii_use_scale_tokens`
  - `test_type_floor_is_eleven_px`
  - `test_text_tokens_meet_contrast_on_every_surface`
- [ ] Declare the `--fs-*`, `--fw-*`, `--radius-*`, and `--focus-ring`
  tokens. Rewrite declarations using the spec's mapping tables.
- [ ] Brighten `--faint` to the lowest lightness that reaches 4.5:1 on
  `--panel3`.
- [ ] Snapshot diff: only `font-size`, `font-weight`, `border-radius`,
  `line-height`, and `color` on faint text. No new overflow entries. Any new
  overflow is fixed locally in Task 7.
- [ ] Commit: `feat: apply dashboard type scale and readability floor`.

### Task 5: Spacing scale

- [ ] Add the failing test `test_spacing_values_on_scale`. It covers `gap`,
  `row-gap`, `column-gap`, `padding*`, and `margin*` px values, and ignores
  `calc()`, negatives, and custom-property values.
- [ ] Snap odd px values to the nearest scale step. On ties, round toward the
  larger step for padding and the smaller step for gap.
- [ ] Snapshot diff: spacing properties only, each at most 1 px (or 2 px for
  values above 32). No new overflow.
- [ ] Commit: `refactor: snap dashboard spacing to the shared scale`.

### Task 6: JavaScript theme palette and SVG text floor

- [ ] Add the failing tests `test_js_hex_only_in_theme_and_chart` and
  `test_svg_text_meets_floor`.
- [ ] Introduce one `THEME` constant before `CHART`. Move the palettes into
  it:
  - `runtimeHarness` colors
  - `COMPARE_COLORS`
  - `runtimeColors`
  - `SPEND_RUNTIME_COLORS`
  - model families
  - `MODEL_OUTPUT_BAR_*`
  - `MODEL_MIX_COLORS` and `MODEL_OTHER_COLOR`
  - `MODEL_SPEED_EXTRA_COLORS`
  - the `#07090c` chart point strokes, which become `CHART.ink`
- [ ] Keep each original identifier as a reference into `THEME` so call sites
  are unchanged. Raise SVG `font-size="9|10"` to 11.
- [ ] Parse the embedded JavaScript. Visually check every chart route.
- [ ] Commit: `refactor: centralize dashboard chart palettes`.

### Task 7: Overflow and readability fixes

- [ ] Browse every route at 1440 and 1024 px. Resolve each new overflow
  entry, clipped header, or wrapped table label with a local layout fix. Never
  reduce text below 11 px.
- [ ] Commit: `fix: fit dashboard tables and ledgers to the readability floor`.

### Task 8: Documentation

- [ ] Update `specs/DESIGN.md`:
  - Frontmatter colors and typography match the tokens.
  - New "Tokens and Contract" section: token layers, scales, contract test,
    snapshot harness, and how to add a token.
  - Raised readability floor.
- [ ] Update `specs/CONTRIBUTING.md` with the recipe for styling a new
  surface.
- [ ] Update the `specs/ARCHITECTURE.md` presentation row if it names CSS
  structure.
- [ ] Commit: `docs: document dashboard design tokens and contract`.

### Task 9: Gates

- [ ] Run the full suite, embedded-JS parse, `py_compile`, and
  `git diff --check`.
- [ ] Run `./scripts/install`, then check `/health`, `/menubar`, and
  staged-runtime parity.
- [ ] Get one independent tester and two reviewers on the final head. Fix
  findings, and rerun any gates they invalidate.
