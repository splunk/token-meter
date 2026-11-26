# Frontier: groundwork for a design overhaul

Status: point-in-time record, 2026-11-26. Part 1 shipped with the branch
`feat/frontier-finish-groundwork`; Part 2 is a proposal for the overhaul and
has not been started. Check the current stylesheet before acting on any count
below.

## Goal

Make a later, larger redesign of the Frontier look cheap and safe: one place
to change each decision, contract tests that catch drift, and a regression
harness that can see the dot-matrix treatment. No theme's appearance changes
in Part 1.

## What made an overhaul expensive (before Part 1)

- **Style tokens copied into every theme.** Each of the ten theme blocks
  declared the same 44 style tokens (chrome, type roles, motion, figures).
  The nine ambient themes carried identical values, so 396 declarations were
  copies, and any new style token had to be added to ten blocks.
- **Spec drift.** The DESIGN.md token table said Frontier was `--motion:still`
  with `--kicker` and `--nav-mark` on `--aura`; the stylesheet ships
  `field` and `--c-cost`. Nothing tested the table.
- **Dot geometry scattered.** The 4px dot grid was typed in seven places, and
  eight rules hand-paired `--data-mask` with `mask-position:50% 100%` for
  vertical fills.
- **A blind regression harness.** `scripts/style-snapshot.js` read no mask,
  `min-width`, or `backdrop-filter` values, so it could not see dot fills,
  one-dot minimums, or glass. It switched themes through the View Transition
  and read styles mid-transition, so a tab that was not painting reported the
  previous theme's colors.

## Part 1: shipped (no visual change)

- **Palette plus finish.** `themes.css` keeps one palette block per theme
  (63 tokens) and adds two shared finish blocks: ambient `:where(:root)` (the
  default) and dot-matrix `:where([data-theme=frontier])`. A redesign of the
  Frontier look is now edits to one block; a second theme can take the
  dot-matrix finish by joining its `:where()` list.
- **`--dot-pitch`** (root, 4px) drives every dot mask, the dotted track, the
  picker preview, and the one-dot minimums. **`--data-mask-up`** (finish) is
  the bottom-center dot mask; the eight hand-paired rules use it.
- **Contract.** Finishes are complete and disjoint from palettes; each theme
  takes at most one finish; the DESIGN.md Finish tokens table must match the
  finishes (single-value cells are compared, every finish token must be
  documented); no rule pairs `--data-mask` with a hand-written bottom origin.
- **Harness.** Captures masks, `min-width`, background position and size,
  `backdrop-filter`, and `font-stretch`; reads styles with transitions and
  animations off; `{theme: 'frontier'}` captures one theme without touching
  the saved preference.

Evidence for "no visual change": every theme's resolved tokens are identical
before and after (static parse, and 2,420 computed values in the browser
across ten themes), apart from the two new tokens; the snapshot diff at
1440px found zero changed elements on all 12 routes in Frontier and Classic.
The only computed delta is invisible: on the eight vertical-fill rules in
ambient themes, `mask-position` is now `0% 0%` instead of `50% 100%` on an
element whose mask image is `none`.

## Part 2: proposed for the overhaul

Ordered by how much each unblocks the redesign.

1. **Name Frontier's roles.** Frontier's palette borrows Classic names for
   different meanings: `--aura` is a warm gray, `--signal-pale` is the
   off-white text color, `--signal-deep-rgb` is a surface, and `--flare`
   (`#f08a55`) is a second orange beside the cost orange `--c-cost`
   (`#e8622c`). `var(--aura…)` appears 35 times outside the theme blocks.
   Introduce semantic roles the overhaul can reason about (`--value`,
   `--cost`, `--attention`, `--neutral-mark`) defined in each palette from
   today's values, migrate rules to them, then retire the borrowed meanings.
2. **Move figure choices out of views.** Nine view call sites branch on
   `themeFigures` (budgets, capabilities, Git, Models, session detail cost
   heat, Spend, Work insights), and the 8px "large enough to dot"
   threshold is retyped in charts, Git, Models, and Spend. Add one
   `dotFill(width, height, measured)` helper whose threshold derives from
   `--dot-pitch`, and a small registry of finish-only figures, so a new
   finish can add or drop figures without touching each view.
3. **One theme registry.** Theme ids live in the boot script, `THEMES` in
   `theme-navigation.js`, the `--swatch-*` root pairs, the palette blocks,
   `performance.html`, the README, and the talk deck. Generate the boot list
   and picker from one source, or at least make the contract the only list
   to edit.
4. **Decide the still path.** `--motion:still` (a static `ditherDots` header
   grain) ships in no theme since Frontier moved to `field`. Keep it as a
   finish option with a test-only theme, or remove it and its header code.
5. **Native parity.** The menu bar draws the dot cache ring in every theme
   and has no notion of the dashboard theme. Decide whether the overhaul's
   look applies natively, and if so pass a finish id through `/menubar`.
6. **Screenshot regression.** The computed-style harness cannot see canvas or
   WebGL output (the header dither field) and needs real data to render dot
   fills. Pair it with per-route screenshots in Frontier on a fixture data
   set before the overhaul starts.
7. **Contrast headroom.** The deck blue `#3987e5` stays a fill because it is
   4.33:1 on `--panel3`; text uses `--signal` `#438ce9`. A redesign that
   wants one blue must lift the surfaces or the blue; the contrast contract
   (`kicker` and `signal` on every surface) will enforce it.
