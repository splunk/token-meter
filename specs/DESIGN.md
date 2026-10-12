---
name: Token Meter
description: A local-first measurement dashboard with one shared Spectrum Instrument Field across every top-level route.
colors:
  ink: "#07090c"
  cool-panel: "#111820"
  cool-panel-raised: "#17212b"
  paper: "#f6f8fb"
  muted: "#a8b3c1"
  faint: "#8693a6"
  electric-cyan: "#00bceb"
  blue-signal: "#1ba0e1"
  sky-signal: "#7fdbf2"
  positive: "#66d990"
  warning: "#ffb457"
  danger: "#ff6f6f"
  violet-signal: "#c7a7ff"
  orange-signal: "#ffb457"
  surface-deep: "#05070a"
  surface-inset: "#0d1117"
  cool-panel-high: "#202b36"
  on-accent: "#062430"
typography:
  spectrum-display:
    fontFamily: "Tektur Local, Avenir Next Condensed, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "clamp(36px, 4.2vw, 54px)"
    fontWeight: 600
    lineHeight: 0.98
    letterSpacing: "-0.035em"
  sessions-title:
    fontFamily: "Tektur Local, Avenir Next Condensed, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  data:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Menlo, monospace"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.015em"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 800
    lineHeight: 1.5
    letterSpacing: "0.04em"
rounded:
  hairline: "2px"
  tight: "4px"
  compact: "6px"
  standard: "8px"
  large: "12px"
  pill: "999px"
spacing:
  2xs: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  action-button:
    backgroundColor: "{colors.electric-cyan}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.standard}"
    padding: "6px 10px"
  text-field:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    typography: "{typography.body}"
    rounded: "{rounded.standard}"
    padding: "8px 10px"
    height: "38px"
  dashboard-card:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "16px"
  segmented-active:
    backgroundColor: "{colors.electric-cyan}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.compact}"
    padding: "8px 12px"
  session-instrument-card:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "18px 20px 16px"
  session-status-instrument:
    backgroundColor: "{colors.cool-panel-raised}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "18px"
---

# Design System: Token Meter

This document owns visual language and interaction design. For runtime,
platform, data-flow, packaging, and privacy boundaries, see
[ARCHITECTURE.md](ARCHITECTURE.md).

The browser dashboard is designed for desktop and laptop viewports of 1024 CSS
pixels and wider. Phone, tablet, and sub-1024-pixel layouts are outside the
supported product and design target. Existing small-screen behavior is graceful
degradation only and does not create a mobile design or QA requirement.

## Overview

**Creative North Star: "Spectrum Instrument Field"**

Token Meter is a dense, local operator dashboard. Its incumbent world is cool and technical: near-black layers, off-white type, cyan measurement signals, compact controls, and restrained depth. The system favors legible evidence, clear state, and reusable dashboard primitives over decorative storytelling.

That system now spans the whole dashboard. Every top-level route uses the same layered ink canvas, spectrum header, cyan active controls, cool-gradient cards, compact fields, and edge-light vocabulary. The atmosphere is one shared page field drawn from the cyan, blue, sky, violet, and orange palette, while Sessions keeps the unique data-bearing context horizon and live-run instrument cards. Stronger color is concentrated in the canvas, navigation, route headers, and selected controls; dense evidence surfaces use the quieter shared surface token.

**Key Characteristics:**

- Dense, factual dark-mode operation with tabular numeric readouts.
- The official white Splunk wordmark leads the dashboard header; the native menu stays unbranded and the exact Splunk chevron is reserved for the favicon and optional compact menu-bar status item.
- Cyan, blue, and sky are shared Token Meter signals, expressed through reusable page, header, card, control, and active-state gradients.
- Every route shares one header geometry, atmosphere, and component language; only task-specific data composition varies.
- Sessions uses a data-bearing context horizon rather than ornamental dials.
- Provider colors remain small identity signals inside the shared system.
- Motion is brief, stateful, and removed when reduced motion is requested.

## Colors

The palette is one cool Token Meter system. Cyan-to-sky is the common action and measurement range; violet and orange add restrained atmosphere without changing semantic meaning.

### Primary

- **Electric Cyan:** The primary action, focus, chart, active-state, and Sessions context signal throughout Token Meter.

### Secondary

- **Blue Signal:** The middle of active-control and data gradients, bridging electric cyan to sky light without introducing a second palette.
- **Sky Signal:** The luminous readout and gradient endpoint used in shared headers, active controls, and the Sessions context horizon.
- **Violet Signal:** A restrained atmospheric accent used inside spectrum gradients, never as a dominant action color.

### Tertiary

- **Positive, Warning, and Danger:** Semantic state colors retain their meaning across routes. Warning orange can join a spectrum gradient only where it does not masquerade as a warning state.

### Neutral

- **Ink, Cool Panel, and Cool Panel Raised:** The global page and surface stack.
- **Paper, Muted, and Faint:** The global text hierarchy from primary reading to metadata.

### Named Rules

**The Shared Spectrum Rule.** Page atmosphere, headers, cards, controls, active states, and focus treatments come from the shared spectrum tokens. Routes must not fork these primitives or add per-route atmosphere variables.

**The Color Dosage Rule.** Cyan, sky, violet, and restrained orange may own large atmospheric regions, but working cards stay quieter so selected controls, evidence, and semantic states remain the first things a user reads.

**The Evidence Color Rule.** Provider and semantic colors identify source or state in small, explicit signals. Atmospheric violet or orange never overrides warning, danger, or provider identity.

## Typography

**Display Font:** Tektur Local (with Avenir Next Condensed and system sans fallbacks)

**Body Font:** System sans (with Segoe UI, Inter, and system-ui fallbacks)

**Label/Mono Font:** System sans for labels; UI monospace (with SF Mono and Menlo fallbacks) for measured values

**Character:** Body copy stays compact and native to macOS, while numbers use stable tabular forms. Tektur gives every top-level route a precise retro-future display voice without displacing the system type inside working content.

### Hierarchy

- **Spectrum Display** (610, fluid 36–54px, 0.98): The route title or selected Sessions task name in the shared spectrum field header.
- **Sessions Title** (610, 22px, 1.2): Task names on session instruments; long titles clamp to two lines.
- **Body** (400, 14px, 1.5): Global interface copy, explanations, and supporting evidence.
- **Data** (720, 18px, 1.15): Cost, context, speed, and related numeric readouts; use tabular numerals.
- **Label** (780, 11px, 0.04em): Compact measurement labels and status metadata, usually uppercase only when the label is functional.

### Named Rules

**The Task Leads Rule.** Session detail replaces a generic page title with the actual task name; provider, project, model, and controls remain subordinate.

**The Measured Numbers Rule.** Cost, context, speed, times, and identifiers use the mono stack and tabular numerals so values align while updating.

## Layout

The global dashboard pairs a centered content field of up to 1320px with a sticky 184px left navigation rail. The outer shell expands to preserve the content field rather than subtracting the rail from it. Its default rhythm is built from 8px, 12px, 16px, and 24px intervals. Each top-level route opens with the same responsive spectrum field header; route-specific controls can occupy its trailing edge.

Sessions opens with one layered spectrum field header, then a two-column instrument grid with a 12px gap. On detail, the working chart and decision instrument form an asymmetric two-column layout that tightens at the 1024-pixel laptop target. Supporting KPIs form an attached ledger with three columns at that laptop target.

The rail keeps frequent monitoring routes, including Efficiency, near the top and anchors Learn and Settings as a separated bottom group. The brand and navigation remain the rail's only persistent content; configuration and connection-status controls do not live there. At laptop widths the rail contracts to icon-only navigation while retaining accessible names and shortcut hints. The supported desktop shell does not convert into mobile top navigation.

### Named Rules

**The Orientation Rule.** Overview and detail share the same layered header, task naming, cyan context signal, and instrument grammar so opening a run feels like focusing the same instrument rather than entering a different product.

## Elevation & Depth

The dashboard uses tonal layering plus one shared surface recipe (`--spectrum-card`) for cards, menus, and floating affordances. Shared cards gain atmosphere through one radial-and-linear gradient recipe. A fine cool edge light and neutral offset shadow separate a surface from the canvas; focus and hover strengthen the same depth recipe without moving live-polled geometry. Every top-level header uses the same dark linear field with a strong spectrum border, while the Sessions context gauge alone carries a cool ambient glow because its horizon encodes live pressure.

### Shadow Vocabulary

- **Dashboard Surface:** A fine cool top highlight over a 16px by 38px neutral shadow, plus a restrained low-offset blue depth note.
- **Dashboard Focus:** The same surface grows to a 20px by 46px neutral shadow and a stronger edge light; it does not translate or resize.
- **Dashboard Soft:** A fine top highlight over a 10px by 28px shadow for compact incumbent surfaces.
- **Session Hover:** A 0 18px 38px black shadow used only while a session instrument is hovered.
- **Spectrum Gauge Glow:** A 0 24px 58px cool shadow tied to the context-pressure horizon.

### Named Rules

**The Operational Depth Rule.** Shared depth comes from soft shadows and restrained gradients. Do not add ornamental layers that look measurable but carry no evidence.

## Shapes

The Token Meter system uses compact 8px corners for cards, controls, fields, and tooltips, with 6px inner controls and pills reserved for statuses and filters. Sessions keeps these incumbent radii. Its distinctive shape is the large circular context horizon inside the detail status instrument, not a repeated card dial or calibration motif.

**The Data-Bearing Geometry Rule.** Circular geometry is reserved for the live context horizon or other evidence-backed visualizations; session cards remain rounded operational surfaces without a decorative dial.

## Components

### Buttons

- **Shape:** Actions use compact 8px containers with 6px inner controls.
- **Primary:** Actions use a subtle cyan-tinted dark fill with 6px by 10px padding. Selected top-level and segmented controls share one cyan-to-blue-to-sky gradient with dark ink text.
- **Hover / Focus:** Cyan controls strengthen their cyan border or fill. Keyboard focus remains explicit and at least 2px on session instruments.
- **Danger:** Preserve the semantic danger treatment rather than converting destructive actions to cyan.

### Chips

- **Style:** Global provider badges use compact 6px corners and the provider's identity color for text, border, and low-opacity fill. Status pills use the shared `.chip` component (with `.tone-warn` for attention); pills are reserved for compact status and filtering affordances.
- **State:** Inside Sessions, provider colors are identifiers only; there is no provider-colored left rail.

### Cards / Containers

- **Corner Style:** Global cards and Sessions instrument plates use compact 8px corners.
- **Background:** Shared cards use cool near-black gradients with restrained cyan and violet radial light. Sessions cards tune the same recipe for live-run emphasis.
- **Shadow Strategy:** Both global surfaces and Sessions cards use the dashboard shadow vocabulary; Sessions hover strengthens the lift.
- **Border:** One-pixel low-contrast borders globally; Sessions uses a low-opacity cyan border without a provider rail.
- **Internal Padding:** Global panels commonly use 16px. Session cards use 18px 20px 16px throughout the supported desktop range.

### Inputs / Fields

- **Style:** Compact 38px fields use a translucent cool-panel fill, 8px corners, and a one-pixel neutral border.
- **Focus:** Shift the border to cyan and add a restrained three-pixel cyan focus halo.
- **Disabled:** Reduce opacity while retaining the text and border structure; unavailable evidence must not resemble a measured zero.

### Navigation

Global top-level navigation uses a compact left rail and preserves the product order. Sessions, Spend, Models, Tools, and Efficiency form the primary monitoring group; Learn and Settings form a separated secondary group anchored to the bottom. Sessions owns prominent Current sessions/All sessions tabs directly below its header: Current is the recent-active card field, while All contains searchable history, filters, a compact selection summary, and an expandable model breakdown. The internal tabs use the same icon, active gradient, hover, and focus treatment as the primary navigation, but remain visibly scoped beneath the Sessions heading. The rail contains no connection-status ornament or configuration controls. The command palette remains keyboard-first through `⌘K`, and browser budget-alert delivery belongs in Settings beside the monthly alert controls. The rail keeps labels at wide desktop widths and contracts to icons at laptop widths; destinations remain available through the persistent rail.

The brand lockup pairs Splunk's unmodified white corporate wordmark with the Token Meter product name. At the laptop icon-only rail width, the wordmark remains visible and the product-name text hides.

### Spend Evidence Field

Spend keeps calendar totals and the daily runtime stack first, then moves from
source evidence to interpretation in one route. Highest-cost logs and platform
split establish where cost accumulated. Session economics follows with a
top-decile concentration bar, P10-to-P90 distributions, and a log-scale
active-time map whose points open the underlying session. Selected-period cost
must remain distinct from full-session input, execution, and active-time
evidence. Coverage counts, local estimates, and unavailable timing stay visible;
the surface describes unusual shape and concentration without claiming
productivity or model quality.

### Efficiency Field

Efficiency leads with two large readings: output per covered dollar and the
reasoning share of reasoning-covered output. Each reading includes a compact
daily trend. Context load and output per token-covered execution are the only
supporting diagnostics. One range control governs both the overall readings and
a runtime-scoped model table. Rows default to spend descending; sortable column
headers allow direct comparison without changing that materiality-first default.
Coverage and local estimate labels stay adjacent to each number, and the
surface never assigns a universal score, threshold, or quality verdict. Claude
thinking activity remains visible when observed, while the ratio stays
unavailable unless the trace reports a separate thinking-token count.

The selected Run surface repeats only Output/$ and Reasoning ratio in a compact
card above the session budget. It keeps the same coverage and thinking-fallback
semantics; daily charts and per-model comparison stay on the main Efficiency
page.

### Git Field

Git uses three major surfaces: period overview, daily evidence, and project
detail. The overview is one compact scorecard rather than a collection of hero
cards. It keeps the period comparison, pushed lines, spend per 1K lines, and
spend coverage in one aligned reading plane. Push days, measured
repositories, and comparable projects sit in a quiet evidence strip beneath it.
A linear evidence bar carries actual coverage and replaces decorative radial
geometry. Partial coverage stays visibly partial and never becomes a positive or
negative status score.

The daily chart follows the overview and pairs pushed text lines with trailing
seven-day cost intensity. It stands on its own without a secondary metric rail.
The sortable project table is the final drill-down. Visible helper copy is
limited to the product boundary; detailed metric definitions live in accessible
field tooltips. Git does not attribute pushed work to a session or model and
does not claim code quality or developer productivity.

### Session Instrument Card

Each card is one selectable, reorderable instrument plate. Provider/runtime and activity status sit at the top without a colored left rail; the task name is the primary identity; cost, context, and speed share an aligned three-readout ledger. Context history is rendered as a small cyan bar horizon. Hover lifts the plate by 3px and focus uses a visible cyan outline. Cards use layered operational gradients but no decorative dial.

### Spectrum Status Instrument

The detail-side instrument pairs live state and estimated cost with context pressure. A large circular field fills to the current context percentage, and the exact percentage is repeated in a standard progress bar. The horizon line is data-bearing; there are no calibration ticks or ornamental labels that imply unsupported precision.

## Do's and Don'ts

### Do:

- **Do** change shared spectrum tokens and primitives when a treatment should move across every route.
- **Do** lead Sessions with the task name and the three decision readouts: cost, context, and speed.
- **Do** use layered radial, linear, and conic gradients with restrained violet and orange accents across the dashboard.
- **Do** preserve semantic and provider colors as small, factual signals.
- **Do** verify wide desktop and 1024-pixel laptop layouts without page-level overflow, clipped controls, or hidden navigation.
- **Do** disable decorative motion under `prefers-reduced-motion` and retain visible keyboard focus.

### Don't:

- **Don't** fork page headers, cards, controls, or active states into route-specific copies.
- **Don't** turn Sessions into interchangeable dark cards with generic headings.
- **Don't** add a provider-colored left rail, Japanese notation, calibration ticks, a card dial, or a route-specific dot field to Sessions.
- **Don't** let atmospheric violet or orange replace semantic danger, warning, live-state, or provider identity colors.
- **Don't** add hosted imagery, decorative assets, or typography that weakens Token Meter's local-only, evidence-first operation.

## Tokens and Contract

Every value above is a token in the single `:root` block that opens the
`page.html` stylesheet. Rules reference tokens for color, type, weight,
radius, and focus. Spacing uses literal values from the documented scale.
A ratcheted count of legacy translucent literals may only shrink.
`tests/contracts/test_design_system.py` enforces this contract, so drift fails CI.

### Token layers

1. **Palette.** Each hue has a hex token and, when it is used translucently,
   an rgb-channel twin: `--cyan:#00bceb` with `--cyan-rgb:0 188 235`. Write a
   tint as `rgb(var(--cyan-rgb)/.12)`. Never retype the channels. `--tint-rgb`
   (white) and `--shade-rgb` (black) carry highlights and shadows.
2. **Roles.** Surfaces run `--bg-deep → --bg → --bg2 → --panel → --panel2 →
   --panel3`. Lines are `--line`, `--line2`, and `--line3`. Text is `--fg`,
   `--dim`, `--faint`, and `--on-accent`. States are `--accent`, `--good`,
   `--warn`, and `--bad`. Data series are `--c-*` and `--chart-*`.
   Provider identity is one `--provider-*` color per runtime (see below).
3. **Scales.**
   - Families: `--font-sans`, `--font-mono`, `--font-display`.
   - Sizes: `--fs-11` through `--fs-56`, plus five fluid readout roles
     (`--fs-hero-fluid` and `--fs-readout-xl`, `-lg`, `-md`, `-sm`).
   - Weights: `--fw-medium`, `--fw-semibold`, `--fw-bold`, `--fw-heavy`.
     Regular is the inherited default.
   - Radii: `--radius-2xs`, `-xs`, `-sm`, `-md`, `-lg`, `-pill`, `-round`.
   - Focus: `--focus-ring`.
4. **Spectrum components.** `--spectrum-*` tokens hold the shared page,
   card, control, active, edge-light, and depth recipes.

Spacing is not wrapped in `var()`, which keeps compact declarations
readable. Every `gap`, `padding`, and `margin` pixel value must come from the
scale 0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48,
or 64.

Charts that stretch with `preserveAspectRatio=none` size their viewBox to
the element (`svg.clientWidth`/`clientHeight`) and redraw on resize, so
text and markers are never distorted.

JavaScript chart series live in one `THEME` constant at the top of the
script. The tested model palette stays inside its `model-color-logic` block.
Charts read stylesheet tokens through `CHART` and `cssVar()`, or pass
`var(--token)` into style attributes.

### Provider identity

Each runtime has exactly one identity color, and every surface uses it:
session cards, Spend charts and legends, badges, budget rows, and every
Models chart and table swatch. In the script, `providerColor(runtimeId)` is
the single lookup; runtimes without an identity color use
`--provider-other`.

| Runtime | Token | Color |
|---|---|---|
| Claude | `--provider-claude` | `#e3825c` rust (Claude terracotta, lightened to stay readable on dark panels) |
| Codex | `--provider-codex` | `#6f9cff` blue |
| Cursor | `--provider-cursor` | `#3cc6c0` teal |
| OpenCode | `--provider-opencode` | `#ff8fb8` pink |
| Kiro | `--provider-kiro` | `#e3cc5c` gold |
| Pi, Hermes, other, or unknown | `--provider-other` | `#8b96a3` slate |

The hues were chosen to stay at least 4.5:1 against the lightest panel
(lowest: slate at 4.79:1), at least ΔE 30 apart from each other, and at
least ΔE 24 from the semantic accent, good, warn, and bad colors. Provider
badges use an 8% tint, so badge text stays at 4.5:1 or better over its own
fill. These colors always appear with a text label. The Models runtime
families (`MODEL_COLORS`) start with the provider color and add four nearby
shades for individual models. Each family stays in its own hue band, so any
two shades from different families are at least ΔE 20 apart; the contract
test checks this. Every Models chart (trend, share, speed, spend
bars, table swatches) picks from these families. The trend chart uses
`modelColor()`, which gives each model a stable shade by its position among
that runtime's models. The ranked top-five charts use `modelRankedColors()`.
That assigns shades in rank order within each runtime. Each family has five
shades, so the visible top five never share a color. Only the
residual "Other" bucket stays neutral. Claude-3P keeps its own violet
family because it distinguishes third-party Claude models inside the Claude
runtime. A contract test keeps each family anchored on its provider token.

### Components

Shared building blocks live at the top of the stylesheet, right after the
base primitives. Each component's selectors are wrapped in `:where()`, which
gives them zero specificity. A screen adds the component class and then
overrides only what is genuinely different, usually size and spacing.
Components own identity (color, type, tracking, surface), not layout.

| Component | Class | Owns | Markup |
|---|---|---|---|
| Metric tile | `.metric` | Uppercase label, monospace heavy value, secondary note | `<span>`/`.label`, then `<strong>`/`<b>`/`.v`, then `<small>`/`.subline`/`.sm` |
| Empty state | `.emptyState` | Centered grid, secondary 12px text, title and paragraph styles | Text, or `<strong>`/`<h3>` plus `<p>` |
| Chart tooltip | `.chartTip` | Floating surface: cyan edge, radius, dark gradient, shadow, no pointer events | Any content |
| Status pill | `.chip`, plus `.tone-warn` | Pill shape, border, fill, secondary text; warn tone for attention | Short text |
| Trend delta | `.trendDelta` inside `.valueWithDelta`, rendered by `renderTrendDelta()` | Rise or fall to the right of a number: a 20px mono arrow and percent over a 12px `vs prior …` label. `data-tone` is `improving` (good) or `degrading` (bad), or neutral when empty. It wraps beneath the number only when the column is too narrow | `<div class=valueWithDelta><div class=v>…</div><div class=trendDelta><strong>↑ 8.5%</strong><span>vs prior 30 days</span></div></div>` |

Every per-metric period comparison on the dashboard uses `.trendDelta` to the
right of its value, never a boxed badge. A summary sentence, such as the Git
scorecard title, may restate the headline comparison. Efficiency passes an improving direction so
the delta is colored. Git always passes none, because pushed-code evidence is
never scored as better or worse.

`.chip` is the original base pill and keeps normal specificity, so its
`.tone-warn` modifier reliably beats single-class screen rules. The other
three components are zero-specificity.

To add one: write its rules as `:where(.name …)`, add the name to
`ZERO_SPECIFICITY_COMPONENTS` in the contract test, use it in markup, and
describe it here. For script-rendered components, add one shared render helper
(like `renderTrendDelta()`) instead of per-screen copies.

### Readability floor

- No text below 11px, including SVG chart labels.
- `--fg`, `--dim`, `--faint`, and `--chart-label` each meet at least 4.5:1 on
  every surface token. The test computes these ratios.
- Uppercase labels track at most `.06em`.

### Contract checks

The test fails when any of these slips:

- A hex color outside `:root` (CSS), or outside `THEME` and the model palette
  (JavaScript).
- A font size, weight, radius, or font family that is not a token, including
  `em` or `%` sizes and a `font` shorthand that does not use tokens.
- A named color or `hsl()` color, positive letter-spacing above `.06em`, or
  letter-spacing in px.
- A hex color or px font size in a markup `style` attribute, or an inline
  script `font-size` below 11px.
- A scaled SVG chart that sizes its text without `svgTextPx`.
- Spacing off the scale.
- An undeclared `var()` reference, a `:root` token nothing uses, or a
  `THEME.compare` color that drifts from its stylesheet token.
- More raw `rgba()` literals than the ratchet baseline, in CSS or in the
  script. The baseline only goes down.
- A text/surface pair below 4.5:1, or text below 11px.
- More than one top-level rule on a line.
- A component selector that is not wrapped in `:where()`, or a component no
  markup uses.
- A stylesheet class whose name never appears in markup or script (dead
  CSS). Two exceptions exist. Classes the script builds from data at runtime
  are allowed, and that set may grow with new backend ids. Short legacy names
  not yet proven dead are also allowed, and that set may only shrink. This is
  a name-level heuristic: a class that shares its name with any word in the
  script counts as used.
- A provider color token for a surface (`--harness-*`, `--spend-*`), or a
  Models family whose first color differs from its `--provider-*` token.
- A provider color below 4.5:1 on the lightest panel, provider badge text
  below 4.5:1 over its own tint, or a runtime with an identity color but no
  matching badge rule.

### Changing the system

- **New color:** add the hex token (and an `-rgb` twin if it is used
  translucently) to the right `:root` group, then reference it.
- **New size or step:** extend the scale and update the contract test, but
  only when an existing step genuinely cannot serve. A one-off value is a
  design question, not a token.
- **Removing a literal:** after converting a raw `rgba()` to a token, lower
  `RGBA_LITERAL_BASELINE`.
- **Regression check:** for any stylesheet-wide change, load
  `scripts/style-snapshot.js` in the dashboard and run
  `await __tmStyle.capture('base')` before the change and
  `await __tmStyle.diff('base')` after it, at 1440px and 1024px. The diff
  lists changed computed properties and new overflow per route. When a
  change adds a shared class to existing elements, pass
  `{ignoreClasses: ['metric']}` (or the new class) to `diff` so each element
  is compared with its old self.

## Native Menu Companion

The menu-bar companion is a conventional macOS menu, not a shrunken dashboard. It uses the system menu material, typography, selection behavior, separators, checkmarks, and submenus so the interaction is immediately familiar and stays readable against any desktop.

- **Direct session following:** The selected session name and follow mode lead the menu. `Follow latest` and up to five recent sessions remain visible at the first level; choosing a session pins it, marks it with a checkmark, and refreshes the same bounded `/menubar?session=…` view. Session titles truncate to keep the menu compact and retain their full identity in tooltips.
- **Two primary facts:** Cost and context are the only standing run metrics. Estimates remain labelled, missing context reads `Unavailable`, and measured context retains its token/window detail. Output speed, model, cache, and other evidence remain available in the status-item title, tooltip, or dashboard instead of competing in the menu. While a Codex response is active, completed token checkpoints may supply an explicitly `live` rolling pace; the final completed-response measurement replaces it.
- **Progressive disclosure:** Open Dashboard remains one click away. Monthly budget appears only when configured. Provider limits and secondary destinations, preferences, shortcuts, alerts, and Quit live in two native submenus, preserving all behavior without a permanent scope rail or settings chrome.
- **Honest evidence and privacy:** Unavailable provider limits do not become zero. Tooltips retain reset, pace, freshness, methodology, and coverage notes. The native payload remains bounded and display-safe—never paths, prompts, responses, credentials, account data, or raw traces.
- **Native behavior:** The menu contains no branded header, custom popover, chart, tab rail, or animated resize. The two-second poll may update the status title while the menu is open, but defers rebuilding menu items until it closes so focus and pointer targets remain stable.
- **Branding:** The menu surface carries no Splunk wordmark. Existing text, automatic, and icon-only status-item modes remain user-configurable; the compact icon is a macOS template image and the complete current state remains in the status-item tooltip.
- **Verification:** Source-contract tests protect native `NSMenu` ownership, first-level session actions, bounded recent-session count, cost/context hierarchy, compact submenus, deferred refresh, and absence of active popover chrome. Deterministic native smokes decode the live payload and verify direct following, status-title rendering, provider limits, budgets, and saved settings. These complement Swift compilation, visual inspection, server checks, and installed-runtime parity.
