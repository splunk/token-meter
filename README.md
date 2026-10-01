<p align="center">
  <img src="images/token-meter-header.png" alt="Token Meter — local-first observability for AI coding agents" width="900">
</p>

<p align="center">
  <a href="https://www.splunk.com/en_us/blog/artificial-intelligence/token-meter-a-live-cost-meter-for-your-coding-agents.html">📝 Launch blog</a>
  · <a href="https://splunk.github.io/token-meter/">🌐 Website</a>
  · <a href="https://www.google.com/search?q=site%3Asplunk.com+tokenomics">📚 Learn Tokenomics</a>
</p>

Token Meter is an open-source, local-first usage and cost dashboard for AI
coding agents. It turns session evidence from Claude, Codex, Cursor, OpenCode,
Kiro, and Pi into one view of token usage, estimated cost, context pressure,
time, tools, and execution—so you can decide whether to continue, intervene,
compare, or investigate a run.

Python standard library only. No API keys for trace analysis. No Token Meter
analytics or telemetry leaves your machine.

## Quick Start

### macOS or Linux

```bash
git clone https://github.com/splunk/token-meter.git
./token-meter/scripts/install
```

The installer stages a stable per-user runtime, starts the local server and
native companion, and configures automatic startup.

For a browser-dashboard-only installation without the macOS menu-bar or Linux
tray companion, use `./token-meter/scripts/install --backend-only`. This mode
does not require the Swift toolchain or Linux GTK/AppIndicator packages, and it
is preserved by automatic updates.

### Windows

> **Beta:** The Windows extension is still in beta.

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command '$p=Join-Path $env:TEMP "token-meter-bootstrap.ps1"; try { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/splunk/token-meter/main/scripts/bootstrap-windows.ps1" -OutFile $p; & $p } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }'
```

The bootstrap uses WinGet from Microsoft App Installer to install missing Git and Python.
It then stages the beta extension without administrator access. From an
existing checkout, rerun `.\scripts\install-windows.cmd`.

For a browser-dashboard-only installation without the Windows notification-area companion,
add `-BackendOnly` to the downloaded bootstrap invocation
(`& $p -BackendOnly`) or run `.\scripts\install-windows.cmd -BackendOnly` from
an existing checkout. Automatic updates preserve this mode.

### Open Token Meter

Open [http://127.0.0.1:8722](http://127.0.0.1:8722), start a normal agent run,
and choose it from **Sessions**.

For requirements, development startup, updates, uninstall commands, and
troubleshooting, see the [User guide](specs/USER_GUIDE.md).

**Windows beta uninstall:**
`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Token Meter\runtime\scripts\uninstall-windows.ps1"`

## What You Can Do

| Goal | Token Meter helps you |
| --- | --- |
| **Understand a live run** | Follow estimated cost, tokens, context pressure, wait, output pace, tool calls, execution evidence, session-budget alerts, and supported Claude or Codex child-agent activity. |
| **Review history and spend** | Find expensive or slow work across sessions, projects, runtimes, platforms, calendar ranges, and child-agent cohorts. |
| **Compare models and execution** | Compare input, output, pace, wait, and workload shape without presenting weak matches as meaningful results. |
| **Investigate tools and skills** | Find high-output, failing, repeated, unobserved, or deferred capabilities while keeping incomplete evidence explicit. |
| **Manage usage** | Check provider-reported limits, allocate a monthly budget, receive threshold notifications, and let Codex or Claude query bounded evidence through the local MCP. |

## Coverage

**Runtimes:** Claude Code and Desktop Agent/Cowork, Codex CLI and desktop,
Cursor Agent/Composer, OpenCode, Kiro, and Pi.

| Platform | Status | Experience |
| --- | --- | --- |
| **macOS** | Supported | Browser dashboard and native menu-bar companion |
| **Linux** | Supported | Browser dashboard and AppIndicator tray companion |
| **Windows** | **Beta** | Browser dashboard and notification-area extension |

Evidence varies by runtime and client version. Missing values remain
unavailable instead of appearing as a misleading zero.

Token Meter works when the agent keeps session evidence on your machine in a
supported local store. Sessions that exist only in a cloud-hosted service may
not be available to Token Meter.

### Pi coding-agent sessions

Pi support reads local session JSONL files and shows only content-free usage
evidence. When present, Token Meter shows recorded input, output, cache, local
cost, and tool-call evidence. Wait time
is inferred from user-to-assistant timestamps, not measured output speed.
Context pressure, output speed, cache savings, and semantic token classification
remain unavailable. Pi cost is the estimate persisted in its local session;
Token Meter does not infer a model or price from provider resource identifiers.
Pi `subagent` child runs are counted in the owning session's totals and listed
with their provider-reported agent name, model, duration, and activity. Only the
structural result fields are read; child prompts, tasks, messages, and error
output stay unread, and an unreported child figure remains unavailable rather
than zero.

## First Five Minutes

1. Open **Sessions → Current sessions** and select an active run.
2. Under **Run**, check cost, context pressure, Output/$, and Reasoning ratio.
   For a supported Claude or Codex run, use **Agent activity** to inspect its
   child hierarchy, covered estimated cost, and explained attention signals.
   Add a session budget if the run needs an attention limit.
3. After more sessions accumulate, use **Spend**, **Models**, **Subagents**, **Tools**,
   **Efficiency**, and **Git** to review longer-term patterns.

## Product Tour

### Follow a session

Run keeps usage, execution, tool, budget, and supported child-agent evidence
together on one focused session page. **Agent activity** shows a bounded local
hierarchy for Claude and Codex when their traces establish one. Relative cost
color and explicit retry evidence help identify runs worth inspecting; neither
is a diagnosis or changes an agent. The dedicated **Subagents** page compares
named roles over time. **Sessions → Subagents** filters child-agent runs by
project, application, model, completion state, evidence signal, and time. Provider-reported roles such as
`token_meter_reviewer` are shown as the primary identity when available;
provider nicknames remain a fallback. A stale nonterminal trace is labeled
**Incomplete**, independently of any attention signal. OpenCode child runs are
counted in totals from the start, because an OpenCode parent's cost excludes its
children; they are not listed as own rows by default and instead appear in a
**Subagents** subsection inside each parent session card, with a **Subagents**
filter to list them instead. A live child run counts toward its parent's current
session and session cap rather than appearing as a separate session. The default **Roles**
view gives every named role its own spend, cost-per-run, or run-volume trend,
compares equal periods when coverage permits, and links each role cohort to its
runs under Sessions, with a matching-run model breakdown. Covered spend and cost per covered run remain visible when some runs lack
cost; coverage is shown, and incomplete periods have no cost-change claim.
**Work time** sums completed prompt-to-response durations, including
reasoning and tool use while excluding gaps between prompts; missing timing
evidence remains unavailable. Browser Back returns from that drill-down to the
same Roles filters.

<p align="center">
  <img src="images/dashboard.png" alt="Token Meter session detail with live cost, token, context, and execution metrics" width="900">
</p>

### Understand spend

Compare Today, 7-day, 30-day, This month, or a custom period across platforms,
projects, runtimes, and sessions. Spend concentration, percentile session
shapes, and a clickable cost-or-input versus active-time map expose which runs
deserve inspection.

<p align="center">
  <img src="images/spend.png" alt="Token Meter Spend page with selected-period totals, stacked daily runtime costs, highest-cost logs, and platform split" width="900">
</p>

### Inspect tools and skills

Review observed calls, output estimates, failures, repeats, catalog exposure,
skill-pack activation, and bounded review candidates.

<p align="center">
  <img src="images/tool-analytics.png" alt="Token Meter capability evidence and skill-pack review" width="900">
</p>

### Check token efficiency

Use **Efficiency** to compare four signals over comparable, covered work:

- **Output / $**: reported output tokens per covered dollar. Higher is better
  over time because more output is reaching the response for the spend.
- **Reasoning ratio**: reported reasoning tokens as a share of output. Lower or
  stable is usually better for comparable work, while difficult work may need
  more reasoning.
- **Context load**: processed input tokens per output token. Lower is better
  because less context is carried into each response.
- **Cache hit ratio**: cache-read tokens as a share of cache-covered input.
  Higher is usually better because more context is served from the prompt cache.

Each headline includes a daily trend, and partial coverage or unavailable
evidence stays labelled beside the numbers.

<p align="center">
  <img src="images/efficiency.png" alt="Token Meter Efficiency page with output per dollar, reasoning ratio, context load, and cache hit ratio" width="900">
</p>

### Work

**Work** shows what the spend went into. It is available on macOS only. When
you turn on **Settings → Work insights**, a local decision model
([Jet](https://huggingface.co/michaljach/jet), Apache-2.0) running in your own
Ollama labels each session's area, work type, and complexity, and flags
follow-up turns where you pushed back on the previous work. The page shows:

- **Where the spend went** by area, and **how sessions ended**: accepted,
  recovered after pushback, or ended on pushback.
- **Pushback over time** and **cost per resolved session** by kind of work.
- **Model choices**: a scorecard of your top models by spend.
- **Right-sizing**: spend split by model tier and reasoning effort, with an
  estimated saving where a cheaper tier would likely have done.

Set up the model once, with Ollama running:

```bash
./scripts/setup-work-classifier
```

Labeling is off by default, runs in the background at a gentle pace (5 labels a
minute unless you raise it), pauses on battery, and can be paused from the page,
Settings, or the menu bar. Labels are estimates; low-confidence answers show as
Unclear.

### Git

**Git** pairs successful local pushes with covered spend, so you can see code
changed by project and day. It uses local `git` evidence only—never remote
requests—and clearly marks partial or unavailable coverage. It is a mechanical
signal, not a code-quality or productivity score.

<p align="center">
  <img src="images/git.png" alt="Token Meter Git page showing pushed lines, spend per 1K lines, push yield, coverage, and daily code changes" width="900">
</p>

### Configure budgets and agent access

Manage monthly budgets, model pricing, language signals, native preferences,
and local agent connections for Codex and Claude. Software update checks
and automatic installation are separate settings; both are on by default.

<p align="center">
  <img src="images/mcp.png" alt="Token Meter Settings view for local agent connections" width="900">
</p>

The local MCP exposes eight read-only evidence tools plus two explicit session-budget setters:

| Tool | Use |
| --- | --- |
| `check` | Make a bounded decision about the caller-matched current run. |
| `usage` | Review aggregate spend, model, tool, or change evidence. |
| `budget` | Read the matched or selected run's effective cap, estimated spend, remaining amount, and threshold state. |
| `set_session_budget` | Set one matched or selected run's cap; requires `confirm: true`. |
| `set_default_session_budget` | Change the default cap for new/unoverridden runs; requires `confirm: true`. |
| `capabilities` | Review optional user-installed skill-pack evidence. |
| `sessions` | Select content-free session IDs using runtime, client, model, state, or time filters. |
| `trace` | Read a standardized trace or sanitized runtime-native structure for one session. |
| `stats` | Aggregate selected token, cost, timing, context, attempt, model-call, or tool metrics. |
| `schema` | Discover fields, dimensions, units, limits, and availability semantics. |

A comparison harness can call `sessions`, pass one returned ID to `trace`, and
then call `stats` with dimensions such as `runtime`, `model`, `day`, or
`session_id`. List responses expose `page.next_cursor`; continue by replaying
the same query with that cursor. Metrics retain measured, estimated, inferred,
and unavailable coverage, so missing evidence is not silently treated as zero.

The `native_structure` trace view is not raw trace content. It keeps only
allowlisted event types/subtypes, model and tool identities, statuses,
relationships, timestamps, and numeric evidence. It does not expose raw trace
content, prompts, responses, reasoning text, tool payloads, or trace paths.
Budget setters accept only USD caps in Token Meter's validated range. They do
not alter monthly allocations or pricing. Use `expected_current_budget_usd`
when a concurrent update must fail rather than overwrite a changed cap.

### Check without opening the dashboard

Use the macOS menu bar, Linux tray, or beta Windows extension to reach the
current or pinned run. The native clients read a compact local payload and do
not parse traces or read provider credentials directly. Token Meter checks for
updates every 10 minutes and installs safe `main` updates automatically by
default, so normal updates do not require opening the dashboard. If automatic
installation is off, the native menu shows **New update available** instead.

<p align="center">
  <img src="images/menu-bar-widget.png" alt="Token Meter macOS menu bar companion" width="420">
</p>

## Evidence and Privacy

Token Meter reads local runtime stores and binds its dashboard to `127.0.0.1`.
It does not upload traces, prompts, responses, project paths, token counts,
costs, or derived analytics. Do not expose the localhost dashboard publicly.

Costs and selected token values can be estimates. Codex cost uses public
API-equivalent rates, which can differ from subscription billing; Cursor usage
includes local proxies where authoritative values are unavailable; Pi cost is
the local estimate persisted in its session record.

Work insights, when you turn them on, read the prompts you typed plus the last
few lines of the assistant reply before each one, keep that text in memory,
and send it only to the loopback Ollama address you configure. Cloud-proxied
Ollama models are refused. Token Meter stores labels, never the text.

Subagent views use only content-free structural relationships and existing
usage evidence. They do not expose prompts, responses, reasoning, tool
contents, commands, or trace paths. Partial relationship, token, or cost
coverage stays explicitly partial or unavailable.

The optional MCP returns bounded derived evidence, not prompts, responses,
reasoning, tool contents, credentials, settings, or trace paths. A result sent
to an explicitly connected agent may be processed by that client's model
provider under its own terms. See the [User guide](specs/USER_GUIDE.md) for the
full evidence semantics and [Security policy](specs/SECURITY.md) for the
canonical boundary.

## Documentation

| Document | Use it for |
| --- | --- |
| [User guide](specs/USER_GUIDE.md) | Requirements, daily use, MCP, updates, uninstall, evidence semantics, and troubleshooting |
| [Security](specs/SECURITY.md) | Privacy and security boundaries or vulnerability reporting |
| [Architecture](specs/ARCHITECTURE.md) | Components, data flow, runtime adapters, and extension contracts |
| [Contributing](specs/CONTRIBUTING.md) | Issues, pull requests, development, and validation |
| [Product principles](specs/PRODUCT.md) and [visual design](specs/DESIGN.md) | Product and experience decisions |
| [Specifications and plans](specs/) | Maintained feature designs and implementation plans |

## License

MIT. See [LICENSE](LICENSE).
