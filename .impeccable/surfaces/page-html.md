---
version: 1
slug: "page-html"
primary_target: "page.html"
related_targets: []
---

# Shared dashboard spectrum system

- Mode: Operate.
- Scope: all seven top-level routes in `page.html`, including Sessions overview/detail and its Run/Activity/Tools/Insights/Alerts panels.
- Audience: a developer monitoring and reviewing local Claude, Codex, and Cursor evidence.
- Primary job: move from live Sessions through Logs, Spend, Models, Tools, Learn, and Settings without relearning the visual language.
- Required interactions: top-level navigation, route controls, filters, tables, cards, Sessions selection and reordering, deep links, settings anchors, tooltips, and narrow-width navigation.
- Constraints: preserve local-only data handling, existing routes and payloads, explicit estimate/unavailable labels, accessibility, and dashboard density. No hosted assets or imagery.
- Direction: Spectrum Instrument Field. Lead the persistent navigation with the official white Splunk wordmark paired with the Token Meter product name, then keep Neo-Mirai's optimistic geometry and technical display type in Token Meter's ink, cyan, blue, sky, violet, and orange palette. Shared CSS tokens (the stylesheet's opening `:root`; see specs/DESIGN.md → Tokens and Contract) and primitives own page atmosphere, headers, cards, buttons, fields, active controls, focus, and tables. Existing provider and semantic colors remain factual signals.
- Shared composition: every route begins with the same framed spectrum header and uses the same cool-gradient surface grammar. Route-specific controls may sit at the trailing edge. Atmosphere is shared; routes do not add per-route atmosphere variables.
- Sessions composition: a disciplined two-column session grid follows the shared header. Each card reads like a labeled instrument plate with a strong task name and three aligned readouts. Detail keeps the actual task name in the same header, the main chart as the working field, and the context-pressure status instrument at the right.
- Spend composition: calendar totals and daily runtime evidence lead. Highest-cost logs and platform split establish source evidence in aligned 360px frames with internal overflow before session economics adds top-decile concentration, P10-to-P90 session shapes, and an openable cost-or-input versus active-time field. Period cost and full-session measures remain explicitly distinct.
- Provider and Models color strategy: each runtime has one identity color (`--provider-*`: rust Claude, blue Codex, teal Cursor, pink OpenCode, gold Kiro, slate for Pi, Hermes, and other runtimes) used on every surface. Models runtime families start from that color and add nearby shades; Claude 3P keeps its own violet family. Output-volume bars stay neutral so they read as context rather than a competing category system.
- Memorable moment: context pressure appears as a rising cyan horizon—small blue bar constellations on the overview and a large cool-spectrum glow in detail—so the same risk signal is recognizable at both scales.
- Risk: repeated header atmosphere could become heavy or reduce information density. Keep it shallow, responsive, and subordinate to route controls and evidence.
