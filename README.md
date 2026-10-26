<p align="center">
  <img src="images/token-meter-header.png" alt="Splunk by Cisco · Token Meter — local-first observability for AI coding agents" width="900">
</p>

<p align="center">
  <a href="https://www.splunk.com/en_us/blog/artificial-intelligence/token-meter-a-live-cost-meter-for-your-coding-agents.html">📝 Launch blog</a>
  · <a href="https://splunk.github.io/token-meter/">🌐 Website</a>
  · <a href="https://www.google.com/search?q=site%3Asplunk.com+tokenomics">📚 Learn Tokenomics</a>
</p>

Token Meter is an open-source, local-first usage and cost dashboard for AI
coding agents. It shows token usage, estimated cost, context pressure, time, and
tool activity for **Claude, Codex, Cursor, OpenCode, Kiro, and Pi** in one place.

- **Local only.** Python standard library, no API keys, no telemetry.
- **Honest numbers.** Estimates are labelled; missing evidence stays unavailable, never zero.

## Things your bill won't tell you

- **6% of tokens were 55% of the dollars.** On Claude Opus 5.5, cache writes cost 25 times as much as cache reads.
- **83,907 tokens of tool definitions** were loaded into prompts and never called.
- **Subagent runs fell from $6.04 to $0.96 each** in a month, by giving them short, narrowly scoped tasks.
- **Cutting xhigh reasoning from 70% to 22% of spend** helped lift output per dollar 73% in three months.

These numbers come from one engineer's laptop, estimated at public list prices. Yours will differ.

[Read: Five levers that move your coding-agent bill](https://splunk.github.io/token-meter/token-optimization.html)

## Quick Start

### macOS or Linux

```bash
git clone https://github.com/splunk/token-meter.git
./token-meter/scripts/install
```

This starts the local server and native companion, and enables automatic
startup. Add `--backend-only` to skip the menu-bar/tray companion.

### Windows

> **Beta:** the Windows extension is still in beta.

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command '$p=Join-Path $env:TEMP "token-meter-bootstrap.ps1"; try { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/splunk/token-meter/main/scripts/bootstrap-windows.ps1" -OutFile $p; & $p } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }'
```

The bootstrap uses WinGet (App Installer) to add any missing Git and Python,
with no administrator access. From a checkout, run `.\scripts\install-windows.cmd`.
Add `-BackendOnly` (`& $p -BackendOnly`) to skip the notification-area companion.

The notification-area companion includes a floating usage panel. Drag it to
move it, or hide it from its context menu; its position and visibility persist
across installs and updates. Running `run-tray.ps1` directly backgrounds it; `-Inline` is the
installer-managed launch mode.

Uninstall:
`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Token Meter\runtime\scripts\uninstall-windows.ps1"`

### Open the dashboard

Go to [http://127.0.0.1:8722](http://127.0.0.1:8722), start an agent run, and
pick it under **Sessions**. Requirements, updates, uninstall, and
troubleshooting are in the [User guide](specs/USER_GUIDE.md).

## Platforms

| Platform | Status | Companion |
| --- | --- | --- |
| macOS | Supported | Menu bar |
| Linux | Supported | AppIndicator tray |
| Windows | Beta | Notification area |

Runtimes: Claude Code and Desktop (Agent/Cowork), Codex CLI and desktop, Cursor
Agent/Composer, OpenCode, Kiro, and Pi. Only sessions stored locally are visible.

OpenCode's current storage (the `session_v2` tables OpenCode adopted in October
2026) and older pre-V2 databases are both supported: Token Meter reads the V2
store when present and falls back to the legacy tables otherwise. OpenCode
databases are only ever opened read-only. OpenCode-reported cost stays
authoritative. When no OpenCode price is configured, cache savings for models
served by a direct Anthropic, OpenAI, or Cursor provider are estimated from that
vendor's built-in catalog; OpenCode and OpenCode Go aggregator models stay
unpriced instead of borrowing a vendor rate.

## Features

### Follow a session

Live cost, tokens, context pressure, output pace, tool calls, session budgets,
and child-agent activity on one page.

<p align="center">
  <img src="images/dashboard.png" alt="Token Meter session detail with live cost, token, context, and execution metrics" width="900">
</p>

### Understand spend

Compare any period across platforms, projects, runtimes, and sessions to find
the runs worth inspecting.

<p align="center">
  <img src="images/spend.png" alt="Token Meter Spend page with selected-period totals, stacked daily runtime costs, highest-cost logs, and platform split" width="900">
</p>

### Check efficiency

| Signal | Meaning | Better |
| --- | --- | --- |
| **Output / $** | Output tokens per dollar | Higher |
| **Reasoning ratio** | Reasoning share of output | Lower or stable |
| **Context load** | Input tokens per output token | Lower |
| **Cache hit ratio** | Cache-read share of input | Higher |

<p align="center">
  <img src="images/efficiency.png" alt="Token Meter Efficiency page with output per dollar, reasoning ratio, context load, and cache hit ratio" width="900">
</p>

### Inspect tools and skills

Spot high-output, failing, repeated, or unused tools and skill packs.

<p align="center">
  <img src="images/tool-analytics.png" alt="Token Meter capability evidence and skill-pack review" width="900">
</p>

### Git

Pairs local pushes with spend to show code changed per project and day, using
local `git` only. A mechanical signal, not a productivity score.

<p align="center">
  <img src="images/git.png" alt="Token Meter Git page showing pushed lines, spend per 1K lines, push yield, coverage, and daily code changes" width="900">
</p>

### Subagents

Track spend, cost per run, and volume for each named child-agent role, and
drill into individual runs from **Sessions → Subagents**.

### Goals

Set a target for Spend, Output / $, Cost per 1K pushed lines, Context load,
Reasoning ratio, Frontier share, or Tokens used from **Efficiency → Goals**.
Choose an app and one or more models, a period, and a target based on your previous usage.
Inspect goal charts by hovering or using arrow keys. Delivery contributors show
repository spend and lines pushed; coverage explains which repositories count.
Hold your current level, repeat calendar goals, or duplicate a previous goal.
Progress also shows on Spend, Git, and the menu bar or tray.
See [goal measurements](specs/GOALS.md).

### Budgets, settings, and MCP

Set monthly and per-session budgets, edit model pricing, and connect Codex or
Claude to the local MCP: eight read-only evidence tools (`check`, `usage`,
`budget`, `capabilities`, `sessions`, `trace`, `stats`, `schema`) plus two
session-budget setters that require `confirm: true`. Traces are allowlisted
structure and numbers, not raw trace content. See the
[User guide](specs/USER_GUIDE.md#ask-from-codex-or-claude) for details.

<p align="center">
  <img src="images/mcp.png" alt="Token Meter Settings view for local agent connections" width="900">
</p>

### Menu bar and tray

See the current run without opening the dashboard. Updates are checked every
10 minutes and installed automatically by default.

<p align="center">
  <img src="images/menu-bar-widget.png" alt="Token Meter macOS menu bar companion" width="420">
</p>

## Privacy

- The dashboard binds to `127.0.0.1`. Do not expose it publicly.
- Traces, prompts, responses, paths, and analytics never leave your machine.
- The MCP returns derived numbers only. Agents you connect may send them to their model provider.
- Costs are estimates. Codex uses public API rates, which can differ from subscription billing.

Full details: [User guide](specs/USER_GUIDE.md#data-and-evidence) ·
[Security policy](specs/SECURITY.md).

## Documentation

| Document | Covers |
| --- | --- |
| [User guide](specs/USER_GUIDE.md) | Setup, daily use, MCP, updates, troubleshooting |
| [Cost estimates and budgets](specs/COST_AND_BUDGETS.md) | How reliable costs are, converting credits into agent budgets |
| [Security](specs/SECURITY.md) | Privacy boundaries, vulnerability reporting |
| [Architecture](specs/ARCHITECTURE.md) | Components, data flow, extension contracts |
| [Contributing](specs/CONTRIBUTING.md) | Issues, pull requests, validation |
| [Product](specs/PRODUCT.md) · [Design](specs/DESIGN.md) | Product and experience decisions |

## License

MIT. See [LICENSE](LICENSE).

Token Meter is an open-source project from Splunk by Cisco.
