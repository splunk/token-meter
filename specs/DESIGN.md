---
name: Token Meter
description: A local-first measurement dashboard with one shared Spectrum Instrument Field across every top-level route, in five selectable themes (Classic default, Nebula, Monolith, Voltage, Phosphor) plus a daily random option.
colors:
  ink: "#07090c"
  cool-panel: "#111820"
  cool-panel-raised: "#17212b"
  paper: "#f6f8fb"
  muted: "#a8b3c1"
  faint: "#8693a6"
  signal: "#00bceb"
  signal-mid: "#1ba0e1"
  signal-hi: "#7fdbf2"
  positive: "#66d990"
  warning: "#ffb457"
  danger: "#ff6f6f"
  aura: "#c7a7ff"
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
    backgroundColor: "{colors.signal}"
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
    backgroundColor: "{colors.signal}"
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

Token Meter is a dense, local operator dashboard. Its world is a futuristic instrument: deep layered void, luminous signal gradients, glass navigation, a faint page texture, compact controls, and restrained glow. The system favors legible evidence, clear state, and reusable dashboard primitives over decorative storytelling.

That system now spans the whole dashboard. Every top-level route uses the same layered ink canvas, spectrum header, signal-gradient active controls, cool-gradient cards, compact fields, and edge-light vocabulary. The atmosphere is one shared page field drawn from the active theme's signal, signal-mid, signal-hi, and aura tokens, while Sessions keeps the unique data-bearing context horizon and live-run instrument cards. Stronger color is concentrated in the canvas, navigation, route headers, and selected controls; dense evidence surfaces use the quieter shared surface token.

**Key Characteristics:**

- Dense, factual dark-mode operation with tabular numeric readouts.
- The official white Splunk wordmark leads the dashboard header; the native menu stays unbranded and the exact Splunk chevron is reserved for the favicon and optional compact menu-bar status item.
- Signal, signal-mid, and signal-hi are shared Token Meter signals, expressed through reusable page, header, card, control, and active-state gradients. Their hues come from the selected theme.
- Every route shares one header geometry, atmosphere, and component language; only task-specific data composition varies.
- Sessions uses a data-bearing context horizon rather than ornamental dials.
- Provider colors remain small identity signals inside the shared system.
- Motion is brief, stateful, and removed when reduced motion is requested.

## Themes

Five dark themes share every component, layout, and token name; only token values differ. The choice is a browser display preference (`localStorage` `tm_theme`), applied before first paint by the `theme-boot` script in `<head>`, and changed from Settings → Appearance (`#settings-appearance`) or the ⌘K palette (`Theme · …`). Unknown or missing values fall back to Classic. Appearance is the first Settings section. Other tabs follow a change through the `storage` event.

| Theme | Character | Signal → mid → hi | Aura | Flare | Corners | Texture | Header ring | Glow |
|---|---|---|---|---|---|---|---|---|
| **Classic** (default) | The original cyan instrument field | `#00bceb` → `#1ba0e1` → `#7fdbf2` | `#c7a7ff` violet | `#6d7bff` indigo | 8/12px | 44px grid | cyan, ice, indigo, violet arc, 20s | .8 |
| **Nebula** | Ultraviolet glass with ion-cyan and mint highlights | `#9d8bff` → `#c77dff` → `#7ef3ff` | `#ff7ad9` magenta | `#64ffda` aqua-mint | 10/14px | sparse star dust | full iridescent ring, 16s | 1 |
| **Monolith** | Graphite and platinum with one vermilion tip | `#e4e7ee` → `#a7aebd` → `#fff` | `#a9b8ff` periwinkle | `#ff6a3d` vermilion | 4/6px | 6px dot matrix | single radar sweep with a vermilion tip, 7s | .3 |
| **Voltage** | Volt yellow on carbon with cyan and magenta sparks | `#f5e63d` → `#ffd23f` → `#fff27d` | `#2ee6ff` cyan | `#ff3df2` magenta | 2/3px | CRT scanlines | three hard CMY arcs, 5s | .85 |
| **Phosphor** | Terminal green with falling-code rain | `#3dfc8f` → `#19d97a` → `#b8ffd2` | `#c6ff4d` lime | `#4dd2ff` ice blue | 4/6px | 14px column rules | green comet with an ice head, 9s | .9 |

**Randomize daily** (`tm_theme=random`, last in the picker and ⌘K) resolves to one theme per local calendar day in the `theme-boot` script (`window.tokenMeterTheme.resolve`), so it applies before first paint, never repeats the previous day's theme, and an open page re-checks every minute to roll over at midnight. Phosphor's signal sits close to the semantic good green; good states keep their own token and label.

Provider identity, semantic states (good, warn, bad), cache-read/cache-write series, and Models families never change with the theme. Theme switches cross-fade through the View Transitions API when motion is allowed, then re-read chart tokens (`readChartTokens()`), redraw visible charts, and update the header shaders, which read their colors from the host element's theme tokens.

## Colors

The palette is one Token Meter system per theme. Signal-to-signal-hi is the common action and measurement range; aura adds restrained atmosphere without changing semantic meaning.

### Primary

- **Signal:** The primary action, focus, chart, active-state, and Sessions context signal throughout Token Meter.

### Secondary

- **Signal mid:** The middle of active-control and data gradients.
- **Signal hi:** The luminous readout and gradient endpoint used in shared headers, active controls, route titles, and the Sessions context horizon.
- **Aura:** A restrained atmospheric accent used inside spectrum gradients and the foil hairline, never as a dominant action color.
- **Flare:** Each theme's third, contrasting accent (`--flare`). Decorative only: header ring, foil hairline, one page-atmosphere glow, and the theme swatch. Never a data series, state, or provider color.

### Tertiary

- **Positive, Warning, and Danger:** Semantic state colors retain their meaning across routes. Warning orange can join a spectrum gradient only where it does not masquerade as a warning state.

### Neutral

- **Ink, Cool Panel, and Cool Panel Raised:** The global page and surface stack.
- **Paper, Muted, and Faint:** The global text hierarchy from primary reading to metadata.

### Named Rules

**The Shared Spectrum Rule.** Page atmosphere, headers, cards, controls, active states, and focus treatments come from the shared spectrum tokens. Routes must not fork these primitives or add per-route atmosphere variables. A theme changes token values, never rules.

**The Signature Rule.** These treatments carry the futuristic identity and stay shared: the iridescent `--spectrum-foil` hairline on the rail and route headers; the rotating conic `--ring` around each route header (`.spectrumPageHeadFrame` pseudo-elements, animated through the registered `--ring-angle` property at `--ring-speed`, with a blurred glow copy scaled by `--glow`); the `--spectrum-glow` halo plus one-shot ion sheen on the active navigation item; and the shimmering signal-hi gradient route title with its `--title-glow`. Themes vary them only through those tokens. Ambient motion (atmosphere drift, foil drift, ring, title shimmer, sheen) stops under `prefers-reduced-motion`.

**The Purposeful Motion Rule.** Motion explains a change or confirms an action, following the open-slide `review-animations` standards. Tokens: `--ease-out` (entering, pressing), `--ease-in-out` (on-screen movement), `--dur-press` 160ms, `--dur-enter` 320ms, `--dur-data` 480ms. The shared behaviors are:
- **Readouts count to their new value.** When a `.metric` value, a Session KPI, the budget spend, or the Efficiency headline changes by at least 1%, it counts from the old value to the new one, at most once per readout every 10s, so the size of a real change is visible without live ticks animating constantly. Only same-format numbers (optional `$`, `<`, `≈`, or `~` prefix) on persistent elements count. A placeholder such as `—` switches instantly, so unavailable evidence never shows a counted-up zero. Readouts inside `aria-live`, hidden readouts, and rebuilt markup update instantly.
- **Charts reveal left to right** (a `clip-path` sweep) once when the active route changes, never on poll, resize, or same-route redraws.
- **Views stagger in** (45ms steps, at most 6) the first time each route opens in a page session.
- **Theme picks grow from the pointer.** Choosing a theme with the pointer reveals it as a circle from the pressed option. Keyboard and ⌘K picks keep the plain crossfade.
- **Presses scale** to `.97` (rail items `.98`) on `:active`.
- **Cards stay still under the pointer.** No glow follows the cursor, because a moving highlight behind text makes it harder to read. Card hover border and shadow ease over 160ms.

Under `prefers-reduced-motion`, the count-up, sweep, stagger, reveal, and press scale are all removed, and values change instantly. A static film-grain layer (`--grain`, `--grain-opacity` per theme) adds material without motion.

**The Color Dosage Rule.** Signal, signal-hi, aura, and restrained orange may own large atmospheric regions, but working cards stay quieter so selected controls, evidence, and semantic states remain the first things a user reads.

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

**The One-Decimal Cost Rule.** Every dollar cost on every page shows exactly one decimal: `$22.3`, never `$22.345` or `$22.35`. Thousands group with commas (`$1,240.5`). A nonzero cost under five cents shows `<$0.1`, so a real cost never reads as a measured `$0.0`. Format through `money()` in script and `format_usd()` in Python; never call `toFixed()` on a cost. Whole-dollar budget figures from `wholeMoney()` stay whole.

## Layout

The global dashboard pairs a centered content field of up to 1320px with a sticky 184px left navigation rail. The outer shell expands to preserve the content field rather than subtracting the rail from it. Its default rhythm is built from 8px, 12px, 16px, and 24px intervals. Each top-level route opens with the same responsive spectrum field header; route-specific controls can occupy its trailing edge.

Sessions opens with one layered spectrum field header, then a two-column instrument grid with a 12px gap. On detail, the working chart and decision instrument form an asymmetric two-column layout that tightens at the 1024-pixel laptop target. Supporting KPIs form an attached ledger with three columns at that laptop target.

The rail keeps frequent monitoring routes, including Efficiency, near the top and anchors Learn and Settings as a separated bottom group. The brand and navigation remain the rail's only persistent content; configuration and connection-status controls do not live there. At laptop widths the rail contracts to icon-only navigation while retaining accessible names and hover labels. The supported desktop shell does not convert into mobile top navigation.

### Named Rules

**The Orientation Rule.** Overview and detail share the same layered header, task naming, signal-colored context signal, and instrument grammar so opening a run feels like focusing the same instrument rather than entering a different product.

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
- **Primary:** Actions use a subtle signal-tinted dark fill with 6px by 10px padding. Selected top-level and segmented controls share one signal-to-mid-to-hi gradient with dark ink text.
- **Hover / Focus:** Signal controls strengthen their signal border or fill. Keyboard focus remains explicit and at least 2px on session instruments.
- **Danger:** Preserve the semantic danger treatment rather than converting destructive actions to the signal color.

### Chips

- **Style:** Global provider badges use compact 6px corners and the provider's identity color for text, border, and low-opacity fill. Status pills use the shared `.chip` component (with `.tone-warn` for attention); pills are reserved for compact status and filtering affordances.
- **State:** Inside Sessions, provider colors are identifiers only; there is no provider-colored left rail.

### Cards / Containers

- **Corner Style:** Global cards and Sessions instrument plates use compact 8px corners.
- **Background:** Shared cards use cool near-black gradients with restrained signal-hi and aura radial light. Sessions cards tune the same recipe for live-run emphasis.
- **Shadow Strategy:** Both global surfaces and Sessions cards use the dashboard shadow vocabulary; Sessions hover strengthens the lift.
- **Border:** One-pixel low-contrast borders globally; Sessions uses a low-opacity signal border without a provider rail.
- **Internal Padding:** Global panels commonly use 16px. Session cards use 18px 20px 16px throughout the supported desktop range.

### Inputs / Fields

- **Style:** Compact 38px fields use a translucent cool-panel fill, 8px corners, and a one-pixel neutral border.
- **Focus:** Shift the border to signal-hi and add a restrained signal focus halo.
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

Each card is one selectable, reorderable instrument plate. Provider/runtime and activity status sit at the top without a colored left rail; the task name is the primary identity; cost, context, and speed share an aligned three-readout ledger. Context history is rendered as a small signal bar horizon. Hover lifts the plate by 3px and focus uses a visible signal outline. Cards use layered operational gradients but no decorative dial.

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
   an rgb-channel twin: `--signal:#00bceb` with `--signal-rgb:0 188 235`. Write a
   tint as `rgb(var(--signal-rgb)/.12)`. Surfaces have twins too, so a
   translucent panel is `rgb(var(--panel-rgb)/.9)`, never a raw `rgba()`. Never retype the channels. `--tint-rgb`
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
   card, control, active, foil, glow, edge-light, and depth recipes. They are
   built only from theme tokens, and their glow alphas scale by `--glow`.
5. **Theme blocks.** `:root,[data-theme=classic]{…}` follows the root, then one
   `[data-theme=<id>]{…}` block per theme. Every block declares the same set
   of tokens (signal family, aura family, surfaces and their rgb twins,
   `--line-rgb`, text, theme data series, `--glow`, `--page-texture`,
   `--ring`, `--ring-speed`, `--title-glow`, the header-shader channels
   `--fx-front-rgb`, `--fx-back-rgb`, `--fx-edge-a-rgb`, `--fx-edge-b-rgb`
   (read by `assets/session-effects.js` from its host element), and the corner scale `--radius-2xs` through `--radius-lg`), declares nothing else,
   and never redeclares a root token. Provider and semantic colors live only
   in `:root`. `--swatch-<id>` and `--swatch-<id>-bg` in the root feed the
   Appearance picker previews.

Spacing is not wrapped in `var()`, which keeps compact declarations
readable. Every `gap`, `padding`, and `margin` pixel value must come from the
scale 0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48,
or 64.

Charts that stretch with `preserveAspectRatio=none` size their viewBox to
the element (`svg.clientWidth`/`clientHeight`) and redraw on resize, so
text and markers are never distorted.

JavaScript chart series live in one `THEME` constant at the top of the
script; theme-dependent chart colors come from stylesheet tokens through
`CHART` and `tint(name, alpha)`, which builds a translucent color from a
`--<name>-rgb` twin. The tested model palette stays inside its `model-color-logic` block.
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
| Chart tooltip | `.chartTip` | Floating surface: signal edge, radius, dark gradient, shadow, no pointer events | Any content |
| Status pill | `.chip`, plus `.tone-warn` | Pill shape, border, fill, secondary text; warn tone for attention | Short text |
| Trend delta | `.trendDelta` inside `.valueWithDelta`, rendered by `renderTrendDelta()` | Rise or fall to the right of a number: a 20px mono arrow and percent over a 12px `vs prior …` label. `data-tone` is `improving` (good) or `degrading` (bad), or neutral when empty. It wraps beneath the number only when the column is too narrow | `<div class=valueWithDelta><div class=v>…</div><div class=trendDelta><strong>↑ 8.5%</strong><span>vs prior 30 days</span></div></div>` |
| Reference scale | `.readoutScale`, rendered by `readoutScaleHtml()` | Where one value sits against a typical value: an 8px track, a dithered fill and marker up to the value, and a tick at the typical value. The scale runs from 0 to at least twice the typical value. `data-tone=high` (1.5× typical or more) switches the tone to `--warn`; pass `toned:false` for counts where more is not a concern | `<span class=readoutScale role=img aria-label="…" style="--at:62%;--typ:40%"><i class=readoutScaleFill></i><i class=readoutScaleTypical></i><i class=readoutScaleMark></i></span>` |
| Reading caption | `.readoutCaption`, text from `typicalCaption()` | One plain-language line under a number, prefixed with a signal `›`, such as `2.1× your typical day of $2.30` | `<span class=readoutCaption>…</span>` |
| Gap bridge | `.chartGapBridge`, runs from `chartGapRuns()`, markup from `chartGapBridge()` | A thin `--faint` dotted line joining the last day with data to the next day with data across days that have none. Ratio and speed charts (Efficiency sparklines, Models speed and trend lines) draw solid lines only through consecutive days with data, a dot for a day with no neighbours, and nothing before the first or after the last day with data. A missing day is never plotted as zero, and its hover reads `No data` | `<line class=chartGapBridge x1="…" y1="…" x2="…" y2="…"/>` |
| Concept frame | `.conceptFrame`, rendered by `conceptFrameHtml()` | One explained frame in the welcome tour and on Learn: spectrum card surface, a `.conceptFrameHead` uppercase label row with one `Example` `.chip` at its right edge, a `.conceptFrameStage` (`role=img` with a plain-language `aria-label`) that holds a real dashboard component filled with sample values, and a `<figcaption>` legend. `[data-callout]` inside the stage anchors positioned markers | `<figure class=conceptFrame><div class=conceptFrameHead><span>Session card</span><span class=chip>Example</span></div><div class=conceptFrameStage role=img aria-label="…">…</div><figcaption><ol class=frameLegend role=list>…</ol></figcaption></figure>` |
| Frame callout | `.frameCallout`, rendered by `calloutHtml()` | A 24px numbered marker on the active gradient with a dark ring, so it reads on any component. Inline by default, beside the value it explains; `data-at=tr`, `tl`, `t`, `r`, or `l` pins it to an edge of the nearest `[data-callout]`. It is a `<mark>` (no product rule styles that tag) and is `aria-hidden`; the legend carries the words | `<mark class=frameCallout aria-hidden=true>1</mark>` |
| Frame legend | `.frameLegend` | The numbered explanation under a frame: an ordered list whose items repeat the marker, then one line of at most 12 words | `<ol class=frameLegend role=list><li><mark class=frameCallout aria-hidden=true>1</mark><span>…</span></li></ol>` |
| Level bar | `.levelBar`, rendered by `levelBarHtml()` | A pill track filled to `--fill` with the active gradient; `data-tone=warn` switches to `--warn`; an optional `<b>` tick at `--mark` (for example, a projection). Fills only from real values, never decoratively | `<span class=levelBar role=img aria-label="…" style="--fill:62%"><i></i></span>` |

**Readable numbers.** A block leads with one main number. When the page
already has history to compare against, it adds a `.readoutScale` and a
`.readoutCaption`, and the remaining numbers sit in smaller secondary rows.
Current-session cards follow this: cost leads, compared with your typical
session, and output per dollar, context, and speed follow in a smaller row.
Baselines come from `dailyBaseline()`, which reads up to 30 prior active days.
A typical session is the median of each of those days' covered cost per
session, so one day of many scripted sessions cannot swamp it. Today is never part of its own baseline. Partial or unavailable cost
shows no comparison at all; it never falls back to zero. The scale fill uses
`--dither-50`, a Bayer 4×4 mask with 2px cells, over a solid tone.

Every per-metric period comparison on the dashboard uses `.trendDelta` to the
right of its value, never a boxed badge. A summary sentence, such as the Git
scorecard title, may restate the headline comparison. Efficiency passes an improving direction so
the delta is colored. Git always passes none, because pushed-code evidence is
never scored as better or worse.

`.chip` is the original base pill and keeps normal specificity, so its
`.tone-warn` modifier reliably beats single-class screen rules. The other
components are zero-specificity.

To add one: write its rules as `:where(.name …)`, add the name to
`ZERO_SPECIFICITY_COMPONENTS` in the contract test, use it in markup, and
describe it here. For script-rendered components, add one shared render helper
(like `renderTrendDelta()`) instead of per-screen copies.

### Welcome tour and Learn deck

The tour is one registry, `DECK_CHAPTERS` and `DECK_SLIDES`: 12 slides in four
chapters (The basics, While you work, Review and find waste, Do better). Each
slide's rail label and kicker is the Token Meter page or term it teaches. It
explains concepts for anyone, a PM, a designer, or a new engineer, at about a
grade-7 reading level. The same registry renders in two hosts, so copy lives in
one place:

- **Welcome overlay.** A full-viewport modal `<dialog class=welcome>` over the
  shared `--spectrum-page` atmosphere. A top-right round ✕ button
  (`aria-label="Close welcome"`), Esc, and **Skip tour** all dismiss it.
- **Learn deck.** `.deck.deckInline` on the Learn page, inside a bordered panel.
  Its footer sticks to the bottom of the viewport so Next stays in reach.
  **Play as full screen** opens the overlay at the current slide.

Each host is a `.deck`: a 212px chapter rail (`.deckRail`, chapter headings with
`.deckStep` slide buttons marked ✓ done, ● current, ○ upcoming; each step label
fits one line), a stage, and a footer with **Skip chapter**, the
`7 / 14 · about 2 min left` counter, and Back / Next.

A slide holds one idea and explains it with a frame. On the left: a kicker, a
display headline of at most 8 plain words, an optional one-sentence body of at
most 14 words, and at most one `Open <page>` button. On the right: a
`.conceptFrame` showing a real dashboard component (session card, companion
menu, Spend day bars and budget bar, Models spend bar, Efficiency line,
Tools review rows, Subagents cost row, Compare cards) with the same classes and
styles as the product. Frames hold simple made-up sample values chosen to make
the idea obvious, never the user's data, and each carries one `Example` chip.
One to three `.frameCallout` markers sit on the part that matters, and the
`.frameLegend` explains each in at most 12 words. Jargon is said in plain words
first, with the technical term at most once in parentheses, for example
"Re-reading (cache read)". One list slide (Get started: four habits) shows rows
instead of a frame; it carries no sample numbers and no Example chip.

Frame markup functions take no arguments, so no live value can reach them. The
deck reads two live facts, only for behavior: the platform names the companion's
place (menu bar, notification area, or tray), and a configured budget turns the
last call to action into **Review your budget**. Where a product rule is scoped
to its page, it is widened with `:is(<page scope>,.conceptFrame)` so the frame
gets the same style; frame-only fitting (height, columns, stillness) lives in
one `:where(.conceptFrame)` block at the end of the stylesheet. Tests hold every
slide to the word budgets, require the Example chip and matching marker and
legend numbers on every frame, and render frames with and without rich state to
prove they never change. At 1180px and below the frame stacks under the copy and
the action button moves beside the headline; no slide scrolls inside the
overlay at 1440×900 or 1024×768. In the overlay's last slide, Done is a
secondary button so the slide's own call to action is the only primary. Slides
enter with one short rise per change, and every motion is removed under
`prefers-reduced-motion`.

### Readability floor

- No text below 11px, including SVG chart labels.
- `--fg`, `--dim`, `--faint`, and `--chart-label` each meet at least 4.5:1 on
  every surface token in every theme, and `--on-accent` meets 4.5:1 on each
  active-gradient stop. The test computes these ratios.
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
- A text/surface pair below 4.5:1 in any theme, or text below 11px.
- A theme block that declares a different token set from the default theme,
  declares a non-token property, redeclares a root token, or themes a provider
  or semantic color; or theme ids that differ between the stylesheet, the
  `theme-boot` script, and the `THEMES` registry.
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
