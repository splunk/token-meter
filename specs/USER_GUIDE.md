# Token Meter User Guide

This guide covers installation, daily use, updates, evidence semantics, and
troubleshooting. For a product overview, start with the
[README](../README.md). For the canonical security boundary, see
[SECURITY.md](SECURITY.md).

## Requirements

- Python 3.8 or newer.
- At least one supported runtime if you want trace data.
- macOS: Swift toolchain, normally from Xcode Command Line Tools.
- Linux: `systemd --user`, GTK 3, PyGObject, and Ayatana AppIndicator. GNOME
  generally needs an AppIndicator/KStatusNotifierItem extension.
- Windows extension (beta): Windows PowerShell and WinGet from Microsoft App
  Installer. The bootstrap installs missing Git and native Windows Python 3.8
  or newer.
- `curl` for macOS and Linux lifecycle helpers.

The browser dashboard uses only the Python standard library. A machine with no
supported evidence still starts normally and shows an empty state.

## Install and Run

### macOS or Linux

```bash
git clone https://github.com/splunk/token-meter.git
./token-meter/scripts/install
```

The installer selects the current operating system, stages a stable per-user
runtime outside the clone, starts the local server and native companion, waits
for readiness, and configures automatic startup.

### Windows

> **Beta:** The Windows extension is still in beta.

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command '$p=Join-Path $env:TEMP "token-meter-bootstrap.ps1"; try { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/splunk/token-meter/main/scripts/bootstrap-windows.ps1" -OutFile $p; & $p } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }'
```

The bootstrap reuses compatible Git and Python installations. If either is
missing, it installs exact packages through WinGet and verifies the executable
before continuing. WinGet is part of Microsoft's App Installer. Token Meter
requests no administrator access.

The managed source and runtime are stored under `%LOCALAPPDATA%\Token Meter`.
The installer starts the hidden server and WinForms tray and creates only the
current user's login startup entry. The process-scoped bypass does not change
the machine or user execution policy, and the downloaded bootstrap file is
removed when the command finishes.

For a local rerun from an existing checkout, use
`.\scripts\install-windows.cmd`. Pass `-NoOpenDashboard` to the downloaded
script invocation when an unattended bootstrap should not open the dashboard.

### Run without installing

For development or troubleshooting:

```bash
cd token-meter
./scripts/start-token-meter
```

To run only the web server:

```bash
python3 meter.py
```

Open [http://127.0.0.1:8722](http://127.0.0.1:8722), start a normal agent run,
and select it from **Sessions**.

## Daily Workflows

### Sessions

**Current sessions** shows recently active runs. **All sessions** searches
history by project, application, or activity window. Selecting a session opens
a durable local URL such as `/sessions/<id>#summary`.

Within a session, **Run** shows usage, timing, tool activity, and the session
budget cap in one view.

Session deletion is available only where the runtime and platform expose a
safe, recoverable target.

### Spend

Spend supports Today, 7-day, 30-day, This month, and custom calendar ranges.
Use its daily bars, platform split, projects, runtimes, and highest-cost logs to
understand where usage accumulated. Partial and locally estimated costs remain
explicitly labeled. Session economics shows the share attributable to the top
10% of cost-covered sessions, P10-to-P90 shapes for spend, processed input,
executions, and active time, plus an openable outlier map. Cost uses the selected
period; input, executions, and active time are full-session totals for sessions
that contributed spend during that period.

### Models

Models keeps identical model names separate across applications. Filter by
project, model runtime, or history. Workload-matched pace is withheld when
sample count, coverage, token authority, or workload similarity is
insufficient.

### Tools and skills

Tools shows observed calls, returned-text estimates, failures, repeats, catalog
exposure, skill-pack activation, and review candidates. Incomplete evidence,
built-in packs, default tools, and read-only runtimes are not treated as safe
disable recommendations.

### Git

Git compares locally observed successful pushes with covered AI spend.
The installer seeds readable remote-tracking reflogs immediately. Token Meter
then rechecks accessible repositories every five minutes and matches authored
changes to each repository's effective local Git email. Code pushed is text
additions plus deletions; binary changes are excluded. Spend per 1K lines uses
only projects with both Git and covered-cost evidence. Push yield reports pushed
lines per 1K covered output tokens, and Deleted share reports the deleted portion
of pushed lines. Daily chart inspection keeps exact added, deleted, covered
spend, daily Spend / 1K, and trailing seven-day Spend / 1K values. The plotted
cost-intensity line uses trailing seven-day totals. A dashed guide marks the
median day once at least four days qualify, and a marker flags each day with
covered spend and no pushed lines. Selecting a day in either chart opens one
shared day inspector with previous and next controls. The coverage readout leads
with the share of available spend represented by comparable repositories; open
it to see comparable, spend-only, Git-only, and unavailable project counts. The
Projects table can be filtered by those same evidence states while keeping its
visible columns focused on spend, pushed code, and Spend / 1K.

**Delivery economics** interprets that evidence in three parts. Signals neutrally
rank the period's observations: cost-intensity and push-yield direction, the costliest day
relative to the typical day, days with covered spend and no pushed lines, deleted
share, spend concentration, and remaining coverage gaps. Daily shape gives the
median and high day for Spend / 1K, lines per push day, and push yield. Five or
more qualifying days show the middle half; smaller samples show the observed
range. A hollow marker means the high day sits beyond the rail scale, and the
exact value stays in the numbers column. Cost by pushed lines plots each day on
log axes with a diagonal at the period's average Spend / 1K, so points above the
diagonal cost more per line than the period average; selecting a point focuses
that day in the daily chart. Days below 50 comparable pushed lines stay visible
as hollow context points, while ratio distributions and ranked outliers exclude
them. Signals with a specific day, project, or coverage gap link to that evidence.
Ratios describe only projects with comparable evidence; projects outside that
coverage may change the result. Every reading here is a statistic, not a quality
judgment.

No remote request is made and GitHub CLI is not required. A macOS-protected
location can remain partial after the installer seed rather than triggering a
folder permission prompt. The local ledger stores hashed repository and object
keys, observation time, and numeric line totals. It does not store paths,
remotes, branches, identities, messages, file names, or diff content. Missing
or expired reflog evidence is shown as partial or unavailable rather than zero.
Git evidence history is bounded to the latest 12 months. Clear the ledger under
**Settings → Git evidence history** to remove its totals and begin a new baseline;
older reflog entries are not imported again.

### Settings

Settings contains the default session budget, monthly budget allocations and
thresholds, effective-dated model pricing, Git evidence history, software updates,
menu-bar preferences, language signals, and local agent connections. The default session
budget applies whenever that browser has no saved cap for a session; changing it
does not replace existing per-session caps. Model pricing shows the review date
and provider sources for bundled rates. Select the models to change, edit their
prices, choose **From now**, **From date**, or **All history**, and save them
together. Unselected models are not changed.

## Native Companions

The macOS menu bar and Linux AppIndicator tray are supported. The Windows
notification-area extension is beta. All three read the compact local
`/menubar` payload; they do not parse traces or read provider credentials
directly.

The macOS companion provides **Run**, **Claude**, **Codex**, and **Cursor**
scopes. Run shows the current or pinned session. Provider scopes show only
quota windows reported by that provider. Missing means unavailable, not 0%.
Menu-bar title fields and quota notifications are configurable in Settings.

## Ask From Codex or Claude

Open **Settings → Agent connections** to connect the read-only local MCP entry
named `tokenmeter`. Start a new agent session after connecting.

The bounded tools are:

- `mcp__tokenmeter__check` for the caller-matched current run and optional
  execution drill-down;
- `mcp__tokenmeter__usage` for aggregate spend, model, tool, or change review;
- `mcp__tokenmeter__capabilities` for named user-installed skill-pack evidence;
- `mcp__tokenmeter__sessions` for content-free session selection by runtime,
  client, model, state, and time;
- `mcp__tokenmeter__trace` for standardized evidence or sanitized native
  structure from one selected session;
- `mcp__tokenmeter__stats` for selected metrics grouped by stable dimensions;
- `mcp__tokenmeter__schema` for query fields, units, limits, and evidence
  semantics.

A typical experiment uses `sessions` to choose comparable runs, `trace` to
retrieve per-execution tokens, cost, timing, semantic event types, tool
identities, context, and coverage, then `stats` to group the same standardized
evidence by runtime, model, day, or session ID. Use `schema` first when building
a reusable harness rather than assuming a provider-specific field exists.

`sessions`, `trace`, and `stats` are cursor-paginated. When
`page.next_cursor` is present, repeat the identical query with that cursor. A
trace changed during pagination returns `stale_cursor`; restart that query to
avoid mixing revisions. A response is capped at 65,536 serialized bytes.

Each metric reports evidence coverage and retains whether values were measured,
estimated, inferred, or unavailable. `native_structure` is diagnostic only: it
is not raw trace content, and provider payloads are never recursively returned.

`check` current detail is runtime/project matched. Explicit trace queries use
an opaque selected session ID and remain content-free. Prompts, responses,
reasoning text, tool arguments/results, credentials, environment values,
settings, project names, session titles, and trace paths are excluded. Results
returned to an explicitly connected agent may be processed by that client's
model provider under its own terms.

## Software Updates

**Check for updates every 10 minutes** is enabled by default.
**Automatically install available updates** is off by default. They are
separate controls: turning off automatic installation keeps checks running,
while turning off checks also turns off automatic installation. The interval
is fixed at 10 minutes while the server is active. Checks fetch revision
metadata without modifying the checkout.

Automatic installation is limited to a managed checkout that is on `main`,
tracks the official `https://github.com/splunk/token-meter.git` origin, is
clean and non-diverged, and is behind upstream. A safe update fast-forwards
the checkout, reruns the installer, and returns after the local server
restarts. Other branches, dirty checkouts, untrusted origins, and diverged
history remain untouched and report that the update needs attention.

Normal automatic updates do not require the dashboard. When automatic
installation is off and a safe update is available, the native menu shows
**New update available** as an install action. During installation it shows
**Updating Token Meter...**; a blocked or failed update shows **Update needs
attention** and opens the Software updates settings. **Check now** remains
available in Settings. After correcting the reported condition, retry the
available update from the native menu or dashboard; an explicitly retried
failed revision is allowed to reinstall.

## Automatic Startup and Uninstall

macOS installs separate per-user LaunchAgents for the server and menu bar:

```bash
"$HOME/Library/Application Support/Token Meter/runtime/scripts/uninstall-launch-agent"
```

Linux installs separate server and tray `systemd --user` services:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/token-meter/runtime/scripts/uninstall-systemd-user"
```

The Windows extension (beta) installs one owned current-user startup entry:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Token Meter\runtime\scripts\uninstall-windows.ps1"
```

These helpers stop and remove Token Meter lifecycle entries. They do not
require `sudo` or security-control changes.

## Data and Evidence

Token Meter reads local runtime stores: JSONL traces for Claude, Codex, Cursor,
Kiro, and Pi; read-only SQLite enrichment for Cursor, OpenCode,
and Hermes Agent; and
runtime-owned metadata needed to join a visible session to its trace.

Discovery and parsing are runtime adapters. Operating-system paths are platform
services. Optional enrichment falls back to the authoritative base trace when
its source is missing, locked, corrupt, or unsupported. Opening a historical
session without new trace activity does not make it current. See
[ARCHITECTURE.md](ARCHITECTURE.md) for path precedence, data flow, cache
invalidation, and extension contracts.

### Hermes Agent sessions

Token Meter reads Hermes' local aggregate session database in read-only mode.
It deliberately excludes message and tool-payload tables, so it can show
recorded aggregate tokens and qualifying cost without importing conversation
content. Hermes does not provide per-call daily cost evidence, so a session
total is not assigned to a calendar day. `HERMES_STATE_DB` takes precedence;
otherwise Token Meter uses `HERMES_HOME/state.db`, then the default Hermes
state location. Hermes support reads SQLite aggregate evidence, not JSONL.

### Pi coding-agent sessions

Pi discovery reads only Pi-owned JSONL session files. Its default root is
`~/.pi/agent`; set `PI_CODING_AGENT_DIR` when Pi stores its local agent files
elsewhere. A file must have the expected Pi session header before Token Meter
will show it. The adapter does not import cloud transcripts, scan arbitrary
directories, or project message, reasoning, or tool-payload content.

Pi sessions use a generic content-free title. Recorded input, output, cache,
cost, and tool-call evidence can be shown when present. Wait is inferred from
the user-to-assistant timestamps. Context pressure, output speed, cache savings,
and semantic token classification remain unavailable because Pi's records do
not establish them. A provider resource identifier, such as an
application-profile reference, is replaced with a safe generic model label;
Token Meter does not infer or price a foundation model from it.

### Costs and estimates

Token Meter uses effective-dated provider/model price periods. Reinstalling
after a price update does not rewrite estimates for events before the price's
effective time. Manual overrides can start a new period or explicitly apply to
all history.

Codex costs use public API-equivalent rates and remain estimates because
subscription billing can differ. Cursor input, output, pace, and cost are local
proxies derived from persisted evidence and remain labeled `est`; cache and
hidden model work may be unavailable. Pi cost is the local estimate persisted
in its session record, never a Token Meter price-table lookup. Token Meter does
not display Pi application-profile identifiers, and leaves Pi context pressure,
semantic token classification, and cache savings unavailable when the trace
does not record that evidence.
Token Meter reports recorded evidence, not a pre-flight prediction.

## Privacy

Token Meter binds to `127.0.0.1`. It does not upload traces, prompts, responses,
project paths, token counts, costs, or derived analytics. Do not expose the
localhost dashboard publicly.

Claude, Codex, and Cursor quota views make bounded read-only requests using the
matching provider's existing sign-in. Credentials and raw responses are not
persisted, returned through localhost, included in logs/errors, or sent to a
different provider. Third-party Claude authentication such as Bedrock fails
closed as unavailable.

The dashboard can display local project paths, runtime/model names, capability
names, and derived metrics. See [SECURITY.md](SECURITY.md) and the
[architecture privacy invariants](ARCHITECTURE.md#privacy-and-security-invariants)
for the complete boundary.

## Troubleshooting

### Port 8722 is already in use

```bash
lsof -nP -iTCP:8722 -sTCP:LISTEN
```

Stop only the process you recognize, then rerun the installer or foreground
server. Do not start multiple Token Meter servers on the same port.

### No logs were found

Start a normal supported agent session and reload. Confirm the runtime uses its
standard local store and that Token Meter can read it. A regular Claude Desktop
cloud chat is not measurable unless Agent/Cowork produces joined local metadata
and a trace.

### Claude Desktop appears under another Claude label

Desktop attribution depends on current metadata containing a joinable session
identifier. If metadata is missing or uses an unknown schema, Token Meter keeps
the authoritative trace and uses the narrower label rather than guessing.

### Pi sessions do not appear

Run a normal Pi coding-agent session, then confirm that its local JSONL evidence
is under `~/.pi/agent` or the directory named by `PI_CODING_AGENT_DIR`. Token
Meter ignores files without a Pi session header, malformed files, symlinks, and
cloud-only conversations. It does not need an API key or a provider account to
read local Pi evidence.

### Source changes do not appear

Run `./scripts/install` from the intended checkout. Do not patch only the
staged Application Support or XDG runtime. The installer validates and stages
the manifest-owned source together.

### Notifications do not appear

Check browser/system notification permission and the relevant Settings toggle.
Unavailable or denied browser delivery still leaves in-app alert history.

### The native companion does not start

Check server readiness first:

```bash
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
```

Then rerun the platform installer. Linux users should inspect the user services
and AppIndicator dependencies; macOS users need the Swift toolchain. Windows
beta users need native Windows Python outside WSL; rerun the Windows wrapper to
install and verify it when missing.
