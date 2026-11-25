---
name: Token Meter
description: A local-first measurement dashboard with one shared Spectrum Instrument Field across every top-level route, in ten selectable themes (Classic default, Nebula, Monolith, Voltage, Phosphor, Omarchy, Jade Horse, Ferrous, Afterglow, Frontier).
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

Ten dark themes share every component, layout, and token name; only token values differ. The choice is a browser display preference (`localStorage` `tm_theme`), applied before first paint by the `theme-boot` script in `<head>`, and changed from Settings → Appearance (`#settings-appearance`) or the ⌘K palette (`Theme · …`). Unknown, missing, or retired values (including the former `random`) fall back to Classic. The picker is a grid of compact cards: five columns, four at the 1180px icon-rail breakpoint, so each two-line description fits. Appearance is the first Settings section. Other tabs follow a change through the `storage` event.

| Theme | Character | Signal → mid → hi | Aura | Flare | Corners | Texture | Header ring | Glow |
|---|---|---|---|---|---|---|---|---|
| **Classic** (default) | The original cyan instrument field | `#00bceb` → `#1ba0e1` → `#7fdbf2` | `#c7a7ff` violet | `#6d7bff` indigo | 8/12px | 44px grid | cyan, ice, indigo, violet arc, 20s | .8 |
| **Nebula** | Ultraviolet glass with ion-cyan and mint highlights | `#9d8bff` → `#c77dff` → `#7ef3ff` | `#ff7ad9` magenta | `#64ffda` aqua-mint | 10/14px | sparse star dust | full iridescent ring, 16s | 1 |
| **Monolith** | Graphite and platinum with one vermilion tip | `#e4e7ee` → `#a7aebd` → `#fff` | `#a9b8ff` periwinkle | `#ff6a3d` vermilion | 4/6px | 6px dot matrix | single radar sweep with a vermilion tip, 7s | .3 |
| **Voltage** | Volt yellow on carbon with cyan and magenta sparks | `#f5e63d` → `#ffd23f` → `#fff27d` | `#2ee6ff` cyan | `#ff3df2` magenta | 2/3px | CRT scanlines | three hard CMY arcs, 5s | .85 |
| **Phosphor** | Terminal green with falling-code rain | `#3dfc8f` → `#19d97a` → `#b8ffd2` | `#c6ff4d` lime | `#4dd2ff` ice blue | 4/6px | 14px column rules | green comet with an ice head, 9s | .9 |
| **Omarchy** | Tokyo Night neon: deep indigo with blue, violet, and sky signals | `#7aa2f7` → `#7dcfff` → `#bb9af7` | `#ff9e64` amber | `#f7768e` rose | 10/14px | 34px indigo grid | blue-violet-sky arc, 40s | .9 |
| **Jade Horse** | Omarchy Jade Horse: deep pine with celadon jade, sky, and pale green-white text | `#8bcbb1` → `#86c7c5` → `#a7dfc5` | `#96b7d0` sky | `#c5cc91` pale celadon-gold | 10/14px | 36px pine grid | jade-sky arc, 44s | .85 |
| **Ferrous** | Oxidized red rust over warm iron, with molten amber and coolant-teal sparks | `#ff5a36` → `#e8481f` → `#ffa27a` | `#ffb36b` amber | `#5ee6d8` coolant teal | 4/6px | 10px diagonal scratches | rust, amber, and teal arc, 28s | 1 |
| **Afterglow** | Plum dusk with a pink-to-amber sunset horizon | `#ff5fa2` → `#ff8a5c` → `#ffc775` | `#8f7bff` violet | `#3fe3ff` cyan | 12/18px | 28px horizon lines | sunset arc, 32s | 1 |
| **Frontier** | Dot-matrix instrument: warm near-black, value blue, cost orange | `#438ce9` → `#5798ec` → `#8fbcf3` | `#a8a69c` warm gray | `#f08a55` light orange | 6/10px | none; an orange dither rain in the header | header field only | 0 |

Phosphor's signal sits close to the semantic good green; good states keep their own token and label. Omarchy's amber aura stays decorative; warning states keep the fixed `--orange`.

### Frontier

Frontier, taken from the talk deck, is the one field theme (`--motion:field`, `--figures:dots`): graphics are dots on a grid, and the only ambient motion is the header's ordered-dither rain, the same shader the other themes run, in the cost orange. Blue (`--signal` `#438ce9`, `--c-output`) means value; orange (`--c-cost` `#e8622c`) means cost and attention, on a warm near-black canvas with warm off-white text. Frontier's `--aura` is a warm gray, so status chrome that other themes tint with the aura (the recent state, arrival marks, tags, average lines) stays neutral; orange appears only through `--c-cost` and the tokens that point at it (`--kicker`, `--callout`, `--nav-mark`, `--seg-on-shadow`, `--scope-on-shadow`) and in the header rain. The deck blue `#3987e5` stays a fill (`--output`) because it is only 4.33:1 on `--panel3`.

- **Chrome.** The route header is frameless (`--head-surface:none`, transparent border, no shadow) with flat `--line2` hairlines (`--foil`). The rail is flat `--bg` with a `--line` border, and selected rail, scope, and segmented controls are flat `--panel3` with `--fg` text; the current rail item adds a 6px orange dot (`--nav-mark`, which is `--c-cost`), and a selected segmented button or Sessions scope tab adds a 2px inset orange underline (`--seg-on-shadow`, `--scope-on-shadow`; 4.7:1 on `--panel3`), so selection never rests on the 1.1:1 surface step alone. Cost readouts (the session detail cost and `.meter`) use `--cost-ink`, which is `--fg`, so a cost reads white as it does on the cards. Nothing uses glass (`--glass*:none`). `--atmos` and `--glow` are 0, so atmosphere, glows, and blurred shadows drop out, including the shade layers of `--shadow`, `--shadow-soft`, `--spectrum-shadow`, and `--spectrum-depth-focus`; `--shadow-tip` keeps tooltips separated. Status-dot glows and pulses, and the command palette and MCP dialog scrim blurs, scale with `--atmos`. `.tbtn` is a flat white 5% fill (`--button-surface`).
- **Type.** Titles use the system sans, bold, unstretched and less tightly tracked (`--font-title`, `--fw-title`, `--title-condense`, `--title-tighten`), so Tektur is never rendered or fetched. Labels, table heads, and kickers are mono uppercase (`--font-label`, `--fw-label`); readout numbers are sans tabular bold (`--font-readout`, `--fw-readout`).
- **Kickers.** `--kicker` is orange, but only the Learn slide kicker (`.deckKicker`) shows it; the Git period label, Work suggestion eyebrows, and concept-frame labels keep their own `--faint`.
- **Header rain.** `--page-texture` is none; the only decorative dots are the header's dither rain: the shared WebGL field with `--fx-front-rgb` set to the cost orange. A field theme mounts no pulsing border (the header is frameless), and `html.ambientPaused` holds every CSS ambient loop, so the clock steps only the field. It pauses like every theme's shaders: reduced motion, low power, a hidden or background window. A still theme (`--motion:still`, none ship today) instead paints a static `ditherDots` grain (`--c-cost-rgb` at .45) that builds in once over 900ms.
- **Cost heat.** Agent-activity and subagent child-row costs show relative cost by bar length only, in `--c-cost`; other themes keep the hue ramp.
- **Dot fills.** Data fills take round dots (`--data-mask`, `--scale-mask`) over dimmer dotted tracks (`--track-texture`, dots at .16). Views follow four mask rules: put `--data-mask` only on a content-free leaf fill (no text, outline, shadow, hatch, or marker on it or its descendants); keep estimated, partial, and incomplete marks solid with their hatch or outline, so a dot always means measured evidence; mask only fills at least 8px on the short side; and give vertical fills and their tracks a bottom-center dot origin (`mask-position:50% 100%`, `background-position:50% 100%`). A nonzero `.levelBar` keeps at least one dot (`--fill-min`). Under `prefers-contrast:more`, data masks, the readout-scale mask, track textures, and the Frontier picker preview's dots drop out, so every data fill is solid.

Provider identity, semantic states (good, warn, bad), cache-read/cache-write series, and Models families never change with the theme. Theme switches cross-fade through the View Transitions API when motion is allowed, then re-read chart tokens (`readChartTokens()`, which bumps `themeEpoch` for view render guards), redraw visible charts, re-read the theme's `--motion` and `--figures` (`themeMotionStill`, `themeFigures`), and update the header shaders, which read their colors from the host element's theme tokens.

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

**The Signature Rule.** These treatments carry the futuristic identity and stay shared: the iridescent `--spectrum-foil` hairline on the rail and route headers; the rotating conic `--ring` around each route header (`.spectrumPageHeadFrame` pseudo-elements, animated through the registered `--ring-angle` property at `--ring-speed`, with a blurred glow copy scaled by `--glow`); the `--spectrum-glow` halo plus one-shot ion sheen on the active navigation item; and the shimmering signal-hi gradient route title with its `--title-glow`. Themes vary them only through those tokens. Ambient motion (atmosphere drift, foil drift, ring, title shimmer, sheen) stops under `prefers-reduced-motion`. Looping ambient motion steps on one shared 20 fps clock (`dashboard/js/ambient-clock.js`); add any new looping animation to its `AMBIENT_ANIMATIONS` instead of letting it run at the display rate. Themes with `--motion:still` run no ambient motion and mount no WebGL: `html.ambientPaused` holds every ambient animation, the clock never starts, and the header shows a static neutral grain (`canvas.stillField`: `ditherDots()` with Bayer 4×4 thresholds, 10px cells) drawn once and redrawn only when the theme or the header size changes. Themes with `--motion:field` (Frontier) hold the CSS loops the same way but keep the clock for the header's dither field alone, with no pulsing border.

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

**Display Font:** Tektur Local (with Avenir Next Condensed and system sans fallbacks), through `--font-title`; Frontier titles use the system sans

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

**The Measured Numbers Rule.** Cost, context, speed, times, and identifiers use tabular numerals so values align while updating. Readout numbers (the `.metric` value, including values that also carry `.mono`, and hero numbers as views adopt it) use `--font-readout` at `--fw-readout`: the heavy mono stack, or bold sans in Frontier. Identifiers and inline values keep the mono stack.

**The Visible-Cents Cost Rule.** Every dollar cost on every page shows cents: `$22.35`, never `$22.345`. Amounts below `$1,000` keep exactly two decimals so small spend stays legible (`$0.04`, not `$0.0`); amounts at or above `$1,000` drop to one decimal to protect column width (`$1,240.5`). Thousands group with commas. A nonzero cost below half a cent shows `<$0.01`, so a real cost never reads as a measured `$0.00`. Round half-up on the shortest decimal form. Format through `money()` in script and `format_usd()` in Python; never call `toFixed()` on a cost. Whole-dollar budget figures from `wholeMoney()` stay whole.

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
- **Turn on (`.tbtn.go`):** One solid green button, larger than other actions (10px by 18px, `--fs-14`, `--fw-heavy`, `--good` fill, `--on-accent-deep` text), for the single step that switches on a feature that is off, such as Turn on Work insights. It is shown only while the feature is off; every other action keeps the signal color.

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

Spend keeps calendar totals and the trend first, then moves from
source evidence to interpretation in one route. Highest-cost logs and platform
split establish where cost accumulated. Session economics follows with a
top-decile concentration bar, P10-to-P90 distributions, and a log-scale
active-time map whose points open the underlying session. Selected-period cost
must remain distinct from full-session input, execution, and active-time
evidence. Coverage counts, local estimates, and unavailable timing stay visible;
the surface describes unusual shape and concentration without claiming
productivity or model quality.

The Spend trend groups the selected range by Day, Week (Monday start), or
calendar Month through a `.seg` control. Grouping is automatic: Day up to 35
days, Week up to 186, Month beyond; choosing one overrides it until the range
changes. A grouping is offered only when the range holds at least two buckets,
one of them complete (neither clipped nor in progress). Buckets are clipped to the range, and a clipped or
in-progress bucket draws faded with a dashed outline and says so in its tooltip.
Each bar stacks its agents in the shared `--provider-*` colors at 82% opacity
(full on hover or focus), with a hairline between segments, square corners, and
a fixed maximum thickness per grouping (24, 44, and 64 px), always at most 64%
of its column (52% beyond 45 bars). Estimated and partial-coverage segments keep their hatch and
ring. The legend names each agent shown. The y axis uses four equal divisions
on a round step, with gridlines from one `.spendGrid` layer on those ticks.
Hover or focus dims sibling bars. The peak value always shows, and every value
shows when there are 14 bars or fewer. The average line uses every calendar day
for Day and full buckets only for Week and Month. CSS places it at
`--spend-average` of the bar track, so drawing the chart never reads layout,
and a live poll that repeats the same days, range, grouping, projects, chart
size, and theme skips the Spend pass entirely.

In a dot theme (Frontier, `--figures:dots`) Spend follows the dot rules:

- **Trend.** A measured segment at least 8px on its short side is dotted from
  the bottom center (`data-dots`, added only in dot themes). Estimated and
  partial-coverage segments, buckets that are clipped or still in progress, and
  thinner segments stay solid, so a dot always means measured evidence. Each
  column draws a dotted track behind its bar (`.spendDay::before` inside the
  isolated `.spendPlot`), and runtime legend chips take the same mask. The
  stack itself is never masked, so its incomplete-bucket outline survives.
- **Monthly budget.** The bar becomes the talk deck's dial, drawn inside the
  same `role=progressbar` wrapper: a half ring of ordered-dither dots
  (`ditherDots()`), value blue (`--c-output`) up to the first alert threshold
  and cost orange (`--c-cost`) from there to 100%, each zone denser toward its
  end; 21 rim ticks with every fifth larger; an `--fg` needle at spent ÷ budget,
  held at 100% with a cap dot when over budget; and a hollow rim dot at the
  projected month once a projection exists. The spend figure sits under the
  hub. With no budget set or no spend evidence there is no dial, and the bar
  and its evidence text stay. The dial redraws only when spend (to a tenth of a
  percent), budget, thresholds, projection, or the theme change.
- **Concentration.** With measured spend and at least 10 cost-covered
  sessions, the bar becomes the deck's pair of 10×10 waffles, Sessions and
  Spend, one dot per percent: the top decile in `--c-cost`, the rest in
  `--faint`, and a local estimate drawn as rings. The exact share stays in the
  big number, the evidence sentence, and each waffle's label.
- **Thin tracks.** Log, platform, runtime-budget, and fallback bars keep solid
  fills; only their tracks take `--track-texture`.

Other themes never receive the dial, the waffles, or the `data-dots`
attributes, so their markup and computed styles are unchanged.

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

A live poll that repeats the same stats, range, filters, sort, chart sizes, and
theme skips the Efficiency render, so the four sparklines and the model table
are rewritten only when their evidence changes. Goal lists are rewritten only
when their markup changes, which keeps an open goal menu and focus in place
across the 30-second goal reload.

In Frontier the hero and support values use the readout face (`--font-readout`)
and the eyebrows the label face; the headline's glow disc drops out with
`--atmos`. The area under each sparkline is dot-masked from the bottom center
and lifted from .08 to .32 opacity (atmosphere off) so its dots read; the line,
its dots, gap bridges, and the inspection marker stay solid. Goal values use the
readout face, goal driver bars keep solid fills over a dotted track, and the
goal trend, a line without an area, is unchanged.

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
Branches sits before the project table: a four-tile strip (cost to ship, unshipped
spend, rework after first push, hard to finish), a caption for the main and
no-branch spend it does not count, and a searchable, sortable branch table that
shows five rows and then scrolls inside its frame under a sticky header.
The sortable project table is the final drill-down. Visible helper copy is
limited to the product boundary; detailed metric definitions live in accessible
field tooltips. Git does not attribute pushed work to a session or model and
does not claim code quality or developer productivity.

In Frontier the overview labels take the mono label tokens and its values the
readout tokens; the coverage bar keeps a solid fill (it is thin) on a dotted
track and stays a linear evidence bar. Daily added and deleted bars take
`--data-mask` from a bottom-center origin only where a bar is at least 8px on
both sides (the script marks those rects `data-dots`); thinner bars and short
segments stay solid, and the legend chips are dotted only when the chart drew
dotted bars. Work-type segments stay solid because each carries a separating
stroke, and their unlabeled hatch is one `<pattern>` in the chart markup; each
draw replaces only the `#d-type-layer` bars. Decorative washes scale with
`--atmos`. Deltas stay neutral, except branch metrics that improve downward.
Git reloads every 60 seconds but repaints only when the payload or the query
changes (a theme change already redraws the charts), and the day buttons'
pointer, focus, and click handlers are delegated once to the hit grid.

### Session Instrument Card

Each card is one selectable, reorderable instrument plate. Provider/runtime and activity status sit at the top without a colored left rail; the task name is the primary identity, with the readable name of the model in use now on its own row beneath it (`.currentSessionModel`, `--dim` `--fs-12`, plus a `--faint` `+N more` when the session used other models); cost, context, and speed share an aligned three-readout ledger. Context history is rendered as a small signal bar horizon. Hover lifts the plate by 3px and focus uses a visible signal outline. Cards use layered operational gradients but no decorative dial.

In Frontier the ledger and today-strip values use `--font-readout` (system sans, bold) and the labels above them (Cost, Output / $, Context in use, Speed, and the today-strip labels) use `--font-label` (mono uppercase); the cost hero is sans bold in every theme, which is Frontier's readout. The card, today strip, empty state, and header status lose their washes and glows through `--atmos`, and their borders use `--edge-rgb`. The context spark keeps solid bars in every theme, because its bars are thinner than a dot. Card markup carries no time-derived text: each card's age and its ring are filled in by one 1 Hz updater (`tickCacheRings()`), so a poll that changes nothing rebuilds no card.

The one permitted ring is the data-bearing prompt-cache countdown, `.cacheRing`, in the card's top row before the status. It is 40px: 30 dots on one 16.5px radius around a clear center (each a zero-length, round-capped dash: lit and "may have expired" dots 3px, spent dots 2px), with the time left inside in `--fs-11` semibold mono (`4:12` under ten minutes, `12m` above). Starting at 12 o'clock, dots switch off clockwise as the cache window passes. Lit dots take their tone from the time left, not the percentage: `--good` above 2 minutes, `--warn` from 2 minutes, and `--bad` from 30 seconds with a soft pulse that reduced motion removes and still themes keep paused. Spent dots are `--line2` and the face shows `—`. For a documented range, the full circle is the long end. Faded `--warn` dots (45% opacity) then cover "may have expired", and the face shows `?`. Sessions without a recorded or documented window get no ring. The hover text says whether the window was recorded or documented, and labels the cost of going cold as an estimate. The native menu-bar row and the optional title timer draw the same dots: 30 in the row ring, with the same sizes and tones, and 12 in the title timer's monochrome template version.

### Spectrum Status Instrument

The detail-side instrument pairs live state and estimated cost with context pressure: the cost readout, a context-pressure progress bar with its exact percentage, the start and last-activity times, and the run's efficiency readings. The bar is the only context gauge; there is no circular field, calibration ticks, or ornamental labels that imply unsupported precision.

In Frontier the cost and KPI values use the readout tokens and their labels the label tokens. The context bar and the session budget slider keep solid fills (they are thinner than a dot) over dotted tracks (`--track-texture`), and the instrument's top accent bar and washes drop out through `--atmos`. Beside it, the execution chart's measured bars take `--data-mask` when they are at least 8px wide and tall (estimated, unavailable, and thinner bars stay solid), and the token split paints each segment's color on a masked `::before`, dotted only at 8px or wider, under a solid chip that keeps its number readable. Above the chart, the calls field draws one dot per model call (reusing the `.waffle` look): row by row at an 8px pitch that shrinks, to no less than 3px, to stay about 120px tall; dot area follows the input the call carried; `--c-cost` marks a call that re-wrote the prompt cache and `--c-output` the rest; a call without token evidence is a `--faint` ring and estimated counts draw rings, never a zero dot. Its caption reads "Each dot is one model call. Size shows the context it carried. Orange calls re-wrote the cache." Without cache evidence (for example Cursor, `availability.cache=false`) every call takes the neutral `--dim` tone, the caption drops its last sentence, and the label reads "N model calls, cache evidence unavailable". In the per-execution chart modes, hovering a dot shows the chart's tip for that call. Session aggregates and every other theme render no calls field. In every theme, the 85% context line draws in `--bad`, the color its legend names.

## Do's and Don'ts

### Do:

- **Do** change shared spectrum tokens and primitives when a treatment should move across every route.
- **Do** lead Sessions with the task name and the three decision readouts: cost, context, and speed.
- **Do** use layered radial, linear, and conic gradients with restrained violet and orange accents across the dashboard.
- **Do** preserve semantic and provider colors as small, factual signals.
- **Do** design wide desktop and 1024-pixel laptop layouts without page-level overflow, clipped controls, or hidden navigation.
- **Do** disable decorative motion under `prefers-reduced-motion` and retain visible keyboard focus.

### Don't:

- **Don't** fork page headers, cards, controls, or active states into route-specific copies.
- **Don't** turn Sessions into interchangeable dark cards with generic headings.
- **Don't** add a provider-colored left rail, Japanese notation, calibration ticks, a card dial, or a route-specific dot field to Sessions. Two dot figures are the exceptions because every dot is evidence: the prompt-cache `.cacheRing`, where every dot encodes remaining cache time, and Frontier's calls field on session detail, where every dot is one model call.
- **Don't** let atmospheric violet or orange replace semantic danger, warning, live-state, or provider identity colors.
- **Don't** add hosted imagery, decorative assets, or typography that weakens Token Meter's local-only, evidence-first operation.

## Tokens and Contract

Every value above is a token in the single `:root` block that opens the
dashboard stylesheet (`dashboard/styles/`). Rules reference tokens for color, type, weight,
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
   Appearance picker previews. The theme blocks are `dashboard/styles/themes.css`;
   the page, rail, card, control, table, and palette rules follow in `layout.css`.
6. **Still and dot-matrix tokens.** These theme tokens switch a theme between
   the ambient look and the still dot-matrix look. Every existing theme carries
   the neutral value, which reproduces its look exactly; Frontier sets the rest.
   The root adds `--title-shimmer` (the route-title gradient), `--dot-mask` (a
   4×4 tile with one round dot; color comes from the masked fill), and
   `--dot-track` (a dotted track texture).

   | Token | Controls | Neutral | Frontier |
   |---|---|---|---|
   | `--motion` | `still` stops ambient motion and swaps the header shaders for a still field (read by script) | `ambient` | `still` |
   | `--figures` | `dots` lets views draw their dot-matrix figures; read by script with `--motion` as `themeFigures` | `bars` | `dots` |
   | `--atmos` | Multiplies the fixed atmosphere alphas: rail radial light, button highlight, scale-mark glow | `1` | `0` |
   | `--edge-rgb`, `--edge-hi-rgb` | Spectrum borders, edge hover, chart grid, chart tip head | signal, signal-hi channels | warm greys |
   | `--kicker` | `.kicker` text | `var(--signal-hi)` | `var(--aura)` |
   | `--title-fill` | Route title paint | `var(--title-shimmer)` | flat `--fg` |
   | `--font-readout` | Readout numbers | `var(--font-mono)` | `var(--font-sans)` |
   | `--font-label` | `.label`, `.metric` labels, `.tbl th` | `var(--font-sans)` | `var(--font-mono)` |
   | `--data-mask` | Mask on data fills (`.levelBar` fill) | `none` | `--dot-mask`, 4px |
   | `--scale-mask` | Mask on the `.readoutScale` fill | `--dither-50`, 4px | `--dot-mask`, 4px |
   | `--track-texture` | Texture over bar and scale tracks | `none` | `var(--dot-track)` |
   | `--callout`, `--callout-glow` | `.frameCallout` fill and ring | active gradient, spectrum glow | orange, flat ring |
   | `--head-surface`, `--head-border`, `--head-shadow` | Route header surface, border, shadow | `--spectrum-head`, strong spectrum border, depth focus | none, transparent, none |
   | `--foil` | Rail and route-header hairlines | `--spectrum-foil` | flat `--line2` |
   | `--rail-surface`, `--rail-surface-narrow`, `--rail-border` | Rail surface (desktop, 760px) and border | `--spectrum-rail`, `--spectrum-rail-narrow`, spectrum border | `--bg`, `--bg`, `--line` |
   | `--glass`, `--glass-palette`, `--glass-tray` | `backdrop-filter` of the rail, command palette, compare tray | today's blurs | none |
   | `--nav-on`, `--nav-on-fg`, `--nav-on-shadow`, `--nav-on-hover` | Selected rail and Sessions scope tab; hover on a selected control | active gradient, `--on-accent`, spectrum glow, active hover | `--panel3`, `--fg`, none, `--panel3` |
   | `--nav-mark` | 6px dot on the current rail item | transparent | `--aura` |
   | `--seg-on`, `--seg-on-fg` | Selected `.seg` button | active gradient, `--on-accent` | `--panel3`, `--fg` |
   | `--seg-on-shadow`, `--scope-on-shadow` | Selected `.seg` button and Sessions scope tab shadow | `--nav-on-shadow`, today's scope glow | 2px inset `--c-cost` underline |
   | `--cost-ink` | Session detail cost and `.meter` readouts | `var(--signal-hi)` | `var(--fg)` |
   | `--button-surface`, `--button-surface-hover` | `.tbtn` fill | `--spectrum-button`, `--spectrum-button-hover` | white 5%, 9% |
   | `--font-title`, `--fw-title`, `--title-condense`, `--title-tighten` | Display titles: family, weight, condensing (multiplies today's stretch), tracking (multiplies today's em) | Tektur, semibold, 1, 1 | sans, bold, 0, .45 |
   | `--fw-label`, `--fw-readout` | Label and readout weights | heavy, heavy | semibold, bold |
   | `--fill-min` | Minimum width of a masked data fill (`.levelBar`) | 2px | 4px, one dot |

   `--atmos` also scales the blurred layers (offset, blur, and alpha) of the shared shadows, button and field
   shadows, hover washes, the active-tab sheen, and the header frame blur. The root adds `--spectrum-head`,
   `--spectrum-rail`, `--spectrum-rail-narrow`, `--spectrum-button`, and `--spectrum-button-hover`, today's
   ambient surfaces.

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
| Grok | `--provider-grok` | `#c084fc` violet |
| Pi, Hermes, Antigravity, other, or unknown | `--provider-other` | `#8b96a3` slate |

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
| Trend delta | `.trendDelta` inside `.valueWithDelta`, rendered by `renderTrendDelta()` | Rise or fall to the right of a number: a 20px mono arrow and percent over a 12px `vs prior …` label. `data-tone` is `improving` (good) or `degrading` (bad), or neutral when empty. It wraps beneath the number only when the column is too narrow. The compact Sessions today strip, whose values are 18px, steps a measured delta down to a 14px arrow and percent over an 11px label; neutral no-baseline text keeps the 12px style | `<div class=valueWithDelta><div class=v>…</div><div class=trendDelta><strong>↑ 8.5%</strong><span>vs prior 30 days</span></div></div>` |
| Reference scale | `.readoutScale`, rendered by `readoutScaleHtml()` | Where one value sits against a typical value: an 8px track, a dithered fill and marker up to the value, and a tick at the typical value. The scale runs from 0 to at least twice the typical value. `data-tone=high` (1.5× typical or more) switches the tone to `--warn`; pass `toned:false` for counts where more is not a concern | `<span class=readoutScale role=img aria-label="…" style="--at:62%;--typ:40%"><i class=readoutScaleFill></i><i class=readoutScaleTypical></i><i class=readoutScaleMark></i></span>` |
| Reading caption | `.readoutCaption`, text from `typicalCaption()` | One plain-language line under a number, prefixed with a signal `›`, such as `2.1× your typical day of $2.30` | `<span class=readoutCaption>…</span>` |
| Gap bridge | `.chartGapBridge`, runs from `chartGapRuns()`, markup from `chartGapBridge()` | A thin `--faint` dotted line joining the last day with data to the next day with data across days that have none. Ratio and speed charts (Efficiency sparklines, Models speed and trend lines) draw solid lines only through consecutive days with data, a dot for a day with no neighbours, and nothing before the first or after the last day with data. A missing day is never plotted as zero, and its hover reads `No data` | `<line class=chartGapBridge x1="…" y1="…" x2="…" y2="…"/>` |
| Concept frame | `.conceptFrame`, rendered by `conceptFrameHtml()` | One explained frame in the welcome tour and on Learn: spectrum card surface, a `.conceptFrameHead` uppercase label row with one `Example` `.chip` at its right edge, a `.conceptFrameStage` (`role=img` with a plain-language `aria-label`) that holds a real dashboard component filled with sample values, and a `<figcaption>` legend. `[data-callout]` inside the stage anchors positioned markers | `<figure class=conceptFrame><div class=conceptFrameHead><span>Session card</span><span class=chip>Example</span></div><div class=conceptFrameStage role=img aria-label="…">…</div><figcaption><ol class=frameLegend role=list>…</ol></figcaption></figure>` |
| Frame callout | `.frameCallout`, rendered by `calloutHtml()` | A 24px numbered marker on `--callout` (the active gradient; orange in Frontier) with a dark ring, so it reads on any component. Inline by default, beside the value it explains; `data-at=tr`, `tl`, `t`, `r`, or `l` pins it to an edge of the nearest `[data-callout]`. It is a `<mark>` (no product rule styles that tag) and is `aria-hidden`; the legend carries the words | `<mark class=frameCallout aria-hidden=true>1</mark>` |
| Frame legend | `.frameLegend` | The numbered explanation under a frame: an ordered list whose items repeat the marker, then one line of at most 12 words | `<ol class=frameLegend role=list><li><mark class=frameCallout aria-hidden=true>1</mark><span>…</span></li></ol>` |
| Level bar | `.levelBar`, rendered by `levelBarHtml()` | A pill track (with `--track-texture`) filled to `--fill` with the active gradient, masked by `--data-mask`; `data-tone=warn` switches to `--warn`; an optional `<b>` tick at `--mark` (for example, a projection). Fills only from real values, never decoratively | `<span class=levelBar role=img aria-label="…" style="--fill:62%"><i></i></span>` |
| Kicker | `.kicker` | A mono uppercase `--fs-11` semibold label tracked `.06em` in `--kicker`. The Git period label, Work suggestion eyebrows, deck slide kickers, and concept-frame labels carry it and keep their own size, weight, tracking, and color where those differ, so only the deck kicker takes `--kicker` | `<span class=kicker>Spend</span>` |
| Waffle | `.waffle`, rendered by `waffleHtml(parts,{cells,cols,unit,label})` from `waffleCounts(values,cells)` | A share-of-total dot figure: `cells` dots (default 100) in `cols` columns of 8px cells, filled row by row, one `<path>` per part in its token or `providerColor()`, and a required visible caption, `Each dot is {unit}.` (`--fs-12`, `--faint`). Counts use largest remainders, and every part above 0 keeps one dot, taken from the largest part. `unknown:true` parts draw hollow `--line2` rings and `estimate:true` parts rings in their color; both count. The label gives each part's exact share (one decimal, `<1%` below 1%). A missing part, a missing unit, or a zero total draws nothing, never a misleading grid | `<figure class=waffle><svg class=waffleGrid role=img aria-label="Output 62.0%, Cost 38.0%" …><path class=waffleDot style="color:var(--c-output)" d="…"/></svg><figcaption class=waffleUnit>Each dot is 1% of spend.</figcaption></figure>` |
| Dither dots | `ditherDots({w,h,cell,r,dens})` | The talk deck's ordered-dither field as a pure function: dot centers for a w×h box in `cell` px cells where `dens(u,v,x,y)` beats the Bayer 4×4 threshold. The still-theme header grain uses it; views reuse it for Frontier-only figures | Script only |
| Branch chip | `.branchChip`, rendered by `branchChipHtml()` | The Git branch a session ran on: a 12px branch icon, the raw branch name in `--font-mono`, and an issue key such as `ABC-12` in `--accent` when the name has one. A pill that hugs its content and truncates the name with an ellipsis. As a `<button>` it opens Sessions › By branch with that branch focused and expanded; main focuses the On main total and shows main rows. Inside another button (a current-session card) render it non-interactive and place the button as a sibling control | `<button class=branchChip type=button data-branch-chip="feat/x"><svg class=branchChipIcon>…</svg><span class=branchChipName>feat/x</span></button>` |

**Readable numbers.** A block leads with one main number. When the page
already has history to compare against, it adds a `.readoutScale` and a
`.readoutCaption`, and the remaining numbers sit in smaller secondary rows.
Current-session cards follow this: cost leads, compared with your typical
session, and output per dollar, context, and speed follow in a smaller row.
Baselines come from `dailyBaseline()`, which reads up to 30 prior active days.
A typical session is the median of each of those days' covered cost per
session, so one day of many scripted sessions cannot swamp it. Today is never part of its own baseline. Partial or unavailable cost
shows no comparison at all; it never falls back to zero. The scale fill uses
`--scale-mask` over a solid tone: `--dither-50`, a Bayer 4×4 mask with 2px
cells, or round dots in Frontier.

Every per-metric period comparison on the dashboard uses `.trendDelta` to the
right of its value, never a boxed badge. A summary sentence, such as the Git
scorecard title, may restate the headline comparison. Efficiency passes an improving direction so
the delta is colored. Git passes none for pushed-code volume and ratios,
because pushed-code evidence is never scored as better or worse. The four
branch delivery metrics on Git › Branches (cost to ship, unshipped spend,
rework after first push, and the hard-to-finish spend share) pass
`improving: down`, because each has one healthy direction.

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

Frontier changes only tokens here: the slide kicker is the orange `.kicker`
with the label weight, callout pins are orange through `--callout`, the Learn
deck's sticky footer blurs through `--glass` (none in Frontier), and the deck's
primary buttons use the selected-control tokens (`--nav-on`, `--nav-on-fg`,
`--nav-on-shadow`): the active gradient and glow elsewhere, flat `--panel3` with
`--fg` text in Frontier. Because frames are pure, each visual container builds
its frame once per slide and theme (`themeEpoch`) instead of on every poll.

### Readability floor

- No text below 11px, including SVG chart labels.
- `--fg`, `--dim`, `--faint`, `--chart-label`, `--kicker`, and `--signal` (a text
  color through `--accent`) each meet at least 4.5:1 on every surface token in every theme, and `--on-accent` meets 4.5:1 on each
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

- **Direct session following:** The selected session name and follow mode lead the menu. `Follow latest` and up to five recent sessions remain visible at the first level; choosing a session pins it, marks it with a checkmark, and refreshes the same bounded `/menubar?session=…` view. Session titles truncate to keep the menu compact.
- **Run metrics live in the title:** The menu has no standing run-metric rows. `Menu bar title` chooses the status-title readouts: cost, output speed, context, model, provider limits, and today's spend (cost, speed, and today's spend by default). The title rounds money to whole dollars; today's spend keeps its `est` marker, and a missing value reads `--` (`--%` for context, `-- tok/s` for speed). Limits show the most constrained fresh window with its provider mark and drop out when none is fresh. While a Codex response is active, completed token checkpoints may supply a rolling pace; the final completed-response measurement replaces it.
- **Prompt-cache ring:** When the session has a recorded or documented cache window, a row below the header draws the ring as 30 dots on a 16.5pt radius in a 40pt square, with the time left inside in 11pt semibold monospaced digits and the headline beside it in monospaced digits. Lit and may-have-expired dots are 3pt across and spent dots 2pt; dot 0 sits at 12 o'clock and dots switch off clockwise. Lit dots are system green above 2 minutes, orange from 2 minutes, and red from 30 seconds; may-have-expired dots are orange at 45% opacity; spent dots use the quaternary label color. The optional title timer (`Cache timer in menu bar`: Always, Last 2 min, Off) draws the same state as a 12-dot template ring on a 5.6pt radius: 2.2pt lit dots, 2.2pt may-have-expired dots at 45% opacity, and 1.4pt spent dots at 22%.
- **Progressive disclosure:** The Dashboard, Spend, Tools, and Settings quick actions sit one click away below the header. Provider limits (Beta), Budgets, Goals, and Menu bar settings are native submenus, work-insights pause controls appear only while the feature is on, and Get Enterprise Tokenomics and Quit stay at the first level, preserving all behavior without a permanent scope rail or settings chrome.
- **Honest evidence and privacy:** Unavailable provider limits do not become zero: a provider without a reported window shows its error or freshness instead of a percentage, and an unset budget reads `Not set`. The cache row's hover text is the only native tooltip; it says whether the window was recorded or documented and labels the cold-cache cost as an estimate. Sessions, provider limits, and the status item have no tooltips. The native payload remains bounded and display-safe—never paths, prompts, responses, credentials, account data, or raw traces.
- **Native behavior:** The menu contains no branded header, custom popover, chart, tab rail, or animated resize. Polls run every 2 s, or every 10 s in low power, and redraw the status title only when what it shows changes. A poll never rebuilds the menu while it is closed: it marks it stale, and a stale menu is rebuilt when it next opens. A poll that arrives while it is open is applied as soon as it closes, so focus and pointer targets remain stable. The cache row's ring and text update each second only while the menu is open.
- **Branding:** The menu surface carries no Splunk wordmark. Text, automatic, and icon-only status-item modes remain user-configurable (automatic shows the icon on screens narrower than 1200 points); the title and the compact icon are macOS template images, and the complete current state is the status item's accessibility value.
- **Verification:** Source-contract tests protect native `NSMenu` ownership, first-level session actions, bounded recent-session count, the absence of standing metric rows, compact submenus, deferred and on-open rebuilds, the dot ring, and absence of active popover chrome. Native smokes decode the live payload and verify direct following, status-title rendering, provider limits, budgets, and saved settings; the deterministic smoke also checks that status marks decode once, that an unchanged title keeps its render key, and that polls on a closed menu build nothing until it opens, which rebuilds it once. These complement Swift compilation, visual inspection, server checks, and installed-runtime parity.
