# Token Meter Website Design

## Objective

Build a GitHub Pages site that feels authored for Token Meter: a local signal
instrument for coding-agent work. The page should be visually memorable before
it is explained, immediately installable, and precise about estimates, coverage,
and the local trust boundary.

## Reference principles

The design takes principles—not layouts or components—from three references:

- `brik.space`: make the working interface part of the opening proposition;
  use one decisive headline and a clear interactive object.
- `dithr.app`: let procedural texture carry identity at architectural scale;
  dither is the image, not background garnish.
- `superpowered.design`: prefer flat editorial hierarchy, whitespace, and
  direct collections over stacked glass cards and generic SaaS bento grids.

No reference typography, branding, asset, copy, or composition is reproduced.

## Creative direction: Signal Print

Token Meter turns traces into bounded operating evidence. The website expresses
that transformation as **Signal Print**: dense agent activity becomes an ordered,
legible field.

- **Palette:** carbon black and warm bone form the base. Electric cyan is the
  measured signal; a restrained acid-lime edge marks live or actionable state.
- **Type:** large condensed display text provides the poster-like voice. System
  sans and monospace carry explanation, commands, labels, and evidence.
- **Dither:** three large procedural fields use ordered Bayer dots. The hero is
  a flowing meter plume, product proof uses a scanning band, and privacy uses a
  complete boundary ring with a restrained inner echo. It has no directional
  gap that can read as a letterform. Fields are pointer-reactive, bounded,
  off-screen paused, and static under reduced motion.
- **Structure:** flat sections with strong rules and deliberate negative space.
  Avoid generic glassmorphism, fake browser chrome, orbit diagrams, and bento
  cards. Real product screenshots are the proof.
- **Motion:** slow field drift, a restrained evidence ticker, and short tab
  transitions. Motion never carries required meaning.

## Information architecture

1. **Hero — Optimise your coding agents.** One dominant statement, one concise
   explanation, a large dither plume, and the complete platform-tabbed install
   dock. The statement uses two controlled lines—a clean lead and one bone
   emphasis slab—so it remains readable instead of wrapping into an oversized
   block. Installation is the hero interaction.
2. **Product proof — Every run leaves a signal.** A flat real-screenshot viewer
   switches Sessions, Spend, and Tools; the native companion is rebuilt as
   semantic HTML/CSS rather than presented as a raster screenshot.
3. **Signal ledger — What becomes legible.** Four editorial rows explain live
   runs, historical patterns, model comparison, and execution evidence without
   placing each idea in a generic card.
4. **Efficiency field — Evidence before verdicts.** A dedicated, flat ledger
   explains output per covered dollar, reasoning ratio, context load, and output
   per token-covered execution. Directional cues stay next to coverage and
   comparability qualifications; there is no universal efficiency score.
5. **Privacy — A closed local loop.** A bright, high-contrast section explains
   supported local reads, bounded projections, and the absence of a hosted Token
   Meter data plane.
6. **Final action.** Return directly to the single install dock and documentation.

The lowercase `splunk>` wordmark appears as a restrained authorship signature in
the header, efficiency field, and footer. It is rendered as local HTML/CSS type,
not fetched as a hosted brand asset, so the Pages artifact remains self-contained.

## Installation contract

The hero owns the only installation surface. macOS, Linux, and Windows beta tabs
switch commands and applicable requirements in the same frame. One copy action
copies the selected command and announces success. Every `#install` link returns
to this dock.

## Responsive behavior

- **1440px:** poster-scale hero with copy on the left and the dither plume
  occupying the right half; the headline stays within two deliberate lines and
  the install dock spans the lower hero.
- **1024px:** preserve the asymmetric hero and full screenshot proof without
  shrinking commands or labels below legibility.
- **768px:** stack headline, dither, and install dock; keep tabs on one row where
  possible.
- **390px:** use a single column, wrap platform controls safely, contain long
  commands in an internal scroller, and never create page-level overflow.

## Accessibility, performance, and trust

- One `h1`, logical headings, semantic landmarks, a skip link, descriptive image
  alternatives, keyboard tabs, visible focus, and an `aria-live` copy status.
- `prefers-reduced-motion` stops animation and renders stable final fields.
- Canvas uses a bounded cell grid and device-pixel-ratio cap; observers pause
  fields when off-screen or when the document is hidden.
- No hosted fonts, runtime libraries, analytics, trackers, cookies, or forms.
- Cost and selected token values remain labeled estimates. Unavailable evidence
  is never presented as zero.
- The self-contained `/docs` directory is published directly from `main` with
  `.nojekyll`; it contains only the site and three approved dashboard images.

## Native companion on the web

The macOS companion is a web-native recreation, not a screenshot or a functional
simulator. Its status-bar title, recent-session hierarchy, current action, cost,
tokens, cache, context meter, last execution, and verdict mirror the real surface.
System typography, restrained desktop material, separators, compact spacing, and
tabular values make it read as native while remaining responsive HTML/CSS. The
composition has an accessible summary and no raster dependency.

## Acceptance criteria

- The first screen is recognizably Token Meter without relying on a dashboard
  screenshot or explanatory paragraph.
- Dither is visibly structural in the hero, product proof, and privacy section.
- The page contains no bento grid, fake illustrative dashboard, or orbit diagram.
- Installation remains complete, early, platform-specific, and keyboard usable.
- Real screenshots support rather than define the visual identity.
- The native companion is rendered from semantic web elements; no menu-bar image
  ships in the Pages artifact.
- Efficiency has a first-class section covering all four maintained signals,
  with coverage, comparability, and estimate qualifications adjacent to them.
- Splunk authorship is visible in the header, efficiency signature, and footer
  without adding a hosted dependency.
- Wide, laptop, tablet, and phone views have no page-level overflow or clipped
  primary content.
- The site remains a dependency-free, self-contained `main:/docs` Pages source.
