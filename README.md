<p align="center">
  <img src="images/token-meter-header.png" alt="Splunk by Cisco · Token Meter — local-first observability for AI coding agents" width="900">
</p>

<p align="center">
  <a href="https://www.splunk.com/en_us/blog/artificial-intelligence/token-meter-a-live-cost-meter-for-your-coding-agents.html">📝 Launch blog</a>
  · <a href="https://splunk.github.io/token-meter/">🌐 Website</a>
  · <a href="https://www.google.com/search?q=site%3Asplunk.com+tokenomics">📚 Learn Tokenomics</a>
</p>

Token Meter is an open-source, local-first usage and cost insights dashboard for AI
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

The bootstrap uses WinGet (App Installer) to add any missing Git and Python, with no
administrator access. From a checkout, run `.\scripts\install-windows.cmd`. Add
`-BackendOnly` to skip the notification-area companion.

The companion's floating usage panel can be dragged or hidden; its position
persists across installs and updates. `-Inline` is the installer-managed launch mode.

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
Agent/Composer, OpenCode (current `session_v2` and legacy storage), Kiro, and
Pi. Only sessions stored locally are visible, and agent databases are opened
read-only.

## A glance now, a deep dive later

The menu bar (macOS), tray (Linux), or notification area (Windows) shows live
cost, speed, context, and today's spend while you work. Follow the latest
session or pin one, open budgets, goals, and provider limits, and get nudges as
notifications that name only the app and model. The server decides when an
alert fires, so each one appears once even with several companions running.
Updates install automatically by default.

<p align="center">
  <img src="images/menu-bar.jpg" alt="Token Meter menu bar companion: live title, one-click dashboard, follow latest session, budgets and goals submenus, and nudge notifications" width="900">
</p>

## Product tour

One dashboard, one question per page. The
[talk deck](https://splunk.github.io/token-meter/talk/) walks through each page
with real numbers.

### Sessions: what did this run just cost?

Live cost, output per dollar, context in use, speed, tools, and subagent
activity for every running agent, plus per-session budgets.

<p align="center">
  <img src="docs/talk/img/live-sessions.jpg" alt="Sessions page with spend today, a getting-started guide, and live session cards" width="900">
</p>

### Compare: which run did it better?

Put up to four sessions side by side, such as the same prompt on Haiku, Sonnet,
and Opus. Token Meter picks the cheapest and fastest run and explains the gap.
It measures effort, not answer quality.

<p align="center">
  <img src="docs/talk/img/live-compare.jpg" alt="Compare sessions view with three runs of the same prompt and cost insights" width="900">
</p>

### Spend: am I on track this month?

Spend by day, platform, project, and session, with a monthly budget and a
projection based on active days. Paying a negotiated rate? Add a **discount**
(per provider, billing channel, and date range) next to the budget, and every
cost shows what you actually pay, with the list price alongside. Tools on the
same computer can set discounts and budgets through the
[local API](specs/LOCAL-API.md).

<p align="center">
  <img src="docs/talk/img/live-spend.jpg" alt="Spend page with monthly trend, budget, and projection" width="900">
</p>

### Models: which model is worth it?

Where the spend goes by model, model mix over time, and observed speed. The
same model name in two apps is tracked as two histories.

<p align="center">
  <img src="docs/talk/img/live-models.jpg" alt="Models page with spend share, model mix over time, and speed over time" width="900">
</p>

### Subagents: what do helper agents cost?

Cost per run, run time, and output per dollar for each child-agent role. Drill
into individual runs and issues from **Sessions → Subagents**.

<p align="center">
  <img src="docs/talk/img/live-subagents.jpg" alt="Subagents page with role trends and cost per run" width="900">
</p>

### Tools: what eats context?

Unused tool definitions, skills and MCP servers that never ran, and which tools
return the most output.

<p align="center">
  <img src="docs/talk/img/live-tools.jpg" alt="Tools page with unused definitions, skill usage, and tool output" width="900">
</p>

### Efficiency: am I getting more per dollar?

| Signal | Meaning | Better |
| --- | --- | --- |
| **Output / $** | Output tokens per dollar | Higher |
| **Reasoning ratio** | Reasoning share of output | Lower or stable |
| **Context load** | Input tokens per output token | Lower |
| **Cache hit ratio** | Cache-read share of input | Higher |

**Goals** (Efficiency → Goals) set a target on Spend, Tokens, Output / $,
Context load, Reasoning ratio, Frontier share, or Cost per 1K pushed lines, for
all agents, one agent, or chosen models. Work goals set a pushback ceiling and
an agent : human range for one kind of work or all kinds, and all models or
chosen ones; the range counts only while pushback
stays under its goal. Progress also shows on Spend, Git, and Work, and every
goal except Work goals shows in the menu bar.
See [goal measurements](specs/GOALS.md).

<p align="center">
  <img src="docs/talk/img/live-efficiency.jpg" alt="Efficiency page with output per dollar, context load, and per-model rows" width="900">
</p>

### Work: where did the money actually go? (macOS)

Opt in under **Settings → Work insights** and a local
[Gemma 4 E4B](https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf) model
in Ollama labels each request's area, kind of work, and complexity, and spots
pushback. Work then shows:

- **Right-sizing**: cheaper models or lower reasoning effort that would likely
  have done the same job, each with an estimated saving.
- **Work breakdown**: one sortable table by kind of work with spend, task
  outcomes (accepted, recovered after pushback, or ended on pushback), cost per
  resolved task, the agent : human turn ratio, pushback, and the best and worst
  model, filterable by model.
- **Session tags**, working hours, pushback and agent : human turns over time,
  and a sortable model scorecard with the kinds of work each model is best and
  weakest at.

<p align="center">
  <img src="docs/talk/img/live-work.jpg" alt="Work page with model suggestions, reasoning suggestions, pushback, and spend by area" width="900">
</p>

Setup runs in the background: Token Meter uses your Ollama 0.34+ or installs a
pinned, signed copy bound to `127.0.0.1`, then downloads the ~5 GB model. It
needs about 10 GB of memory, pauses on battery or low memory, and can be paused
from the page, Settings, or the menu bar. Labels are estimates.

### Git: what shipped for the spend?

Pairs local pushes with spend to show spend per 1K pushed lines and push yield
per project and day. Local `git` only; it never contacts a remote. A mechanical
signal, not a productivity score.

<p align="center">
  <img src="docs/talk/img/live-git.jpg" alt="Git page with spend per 1K pushed lines and push yield" width="900">
</p>

### Themes

Pick a look under **Settings → Appearance**. Classic stays the default.

| | | | |
| :---: | :---: | :---: | :---: |
| <img src="docs/talk/img/theme-classic.jpg" alt="Classic theme" width="200"><br>Classic | <img src="docs/talk/img/theme-nebula.jpg" alt="Nebula theme" width="200"><br>Nebula | <img src="docs/talk/img/theme-afterglow.jpg" alt="Afterglow theme" width="200"><br>Afterglow | <img src="docs/talk/img/theme-voltage.jpg" alt="Voltage theme" width="200"><br>Voltage |
| <img src="docs/talk/img/theme-phosphor.jpg" alt="Phosphor theme" width="200"><br>Phosphor | <img src="docs/talk/img/theme-ferrous.jpg" alt="Ferrous theme" width="200"><br>Ferrous | <img src="docs/talk/img/theme-monolith.jpg" alt="Monolith theme" width="200"><br>Monolith | <img src="docs/talk/img/theme-omarchy.jpg" alt="Omarchy theme" width="200"><br>Omarchy |

### Ask from Codex or Claude (MCP)

Connect Codex or Claude to the local MCP: eight read-only evidence tools
(`check`, `usage`, `budget`, `capabilities`, `sessions`, `trace`, `stats`,
`schema`) plus two session-budget setters that require `confirm: true`. It
returns allowlisted structure and numbers, not raw trace content. See the
[User guide](specs/USER_GUIDE.md#ask-from-codex-or-claude).

## Privacy

- The dashboard binds to `127.0.0.1`. Do not expose it publicly.
- Traces, prompts, responses, paths, and analytics never leave your machine.
- The MCP returns derived numbers only. Agents you connect may send them to their model provider.
- Costs are estimates. Codex uses public API rates, which can differ from subscription billing.
- Work insights, when on, send typed prompts, the end of the reply before each one, and
  changed file names only to the local Ollama; Token Meter stores labels and counts, never text.

Full details: [User guide](specs/USER_GUIDE.md#data-and-evidence) ·
[Security policy](specs/SECURITY.md).

## Documentation

| Document | Covers |
| --- | --- |
| [User guide](specs/USER_GUIDE.md) | Setup, daily use, MCP, updates, troubleshooting |
| [Cost estimates and budgets](specs/COST_AND_BUDGETS.md) | How reliable costs are, discounts, the prompt-cache countdown, converting credits into agent budgets |
| [Local API](specs/LOCAL-API.md) | Letting other local tools set discounts and budgets |
| [Security](specs/SECURITY.md) | Privacy boundaries, vulnerability reporting |
| [Architecture](specs/ARCHITECTURE.md) | Components, data flow, extension contracts |
| [Contributing](specs/CONTRIBUTING.md) | Issues, pull requests, validation |
| [Product](specs/PRODUCT.md) · [Design](specs/DESIGN.md) | Product and experience decisions |

## License

MIT. See [LICENSE](LICENSE).

Token Meter is an open-source project from Splunk by Cisco.
