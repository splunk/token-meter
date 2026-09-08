# Contributing

Thanks for helping improve Token Meter.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing component boundaries.
The generated root `AGENTS.md` and `CLAUDE.md` files let supported coding agents
discover the maintained instructions in [AGENTS.md](AGENTS.md) at startup.

## Before you start

- **New feature:** [Open a GitHub issue](https://github.com/splunk/token-meter/issues/new)
  before writing code or creating a pull request. Describe the problem,
  proposed behavior, expected UI impact, and any privacy or local-only
  considerations. Wait until the scope is agreed before implementing it.
- **Bug fix or documentation improvement:** You may open a pull request
  directly. A separate issue is optional.
- **Security issue:** Do not open a public issue or pull request containing
  exploit details or sensitive data. Follow [SECURITY.md](SECURITY.md).

## The easiest contribution path

1. Fork the repository, or clone it directly if you have write access.
2. Create a focused branch for one change.
3. Reproduce the problem before editing when fixing a bug.
4. Make the smallest change that solves the problem and add or update tests.
5. Run the relevant checks below, then open a pull request.

```bash
git clone <your-fork-url>
cd token-meter
git switch -c fix/short-description
```

## Use a coding agent

Start the agent in the repository and give it a narrow bug report or an approved
feature issue. The primary agent coordinates implementation, independent
testing, and risk-based review. Review the resulting diff, handoff, and
validation evidence before asking the agent to push or open a pull request.

The canonical project skills and role contracts live under `.agents/skills/`
and `.agents/roles/`. Codex adapters under `.codex/` and Claude adapters under
`.claude/` are generated so there is only one maintained skill body. After
cloning, or whenever the manifest, skills, roles, or workflow contracts change,
run:

```bash
./scripts/setup-agent-tools
./scripts/check-agent-tools
```

The setup command deterministically creates repository discovery files and
prints host-specific guidance if the pinned external Superpowers skills are not
installed. The check command is read-only and fails on generated-file drift,
invalid contracts, or a missing/wrong external version. On Windows, use
`py scripts/agent_tools.py setup` and `py scripts/agent_tools.py check`.

OpenCode and Pi read the canonical `.agents/skills/` tree directly. A host may
need to be restarted after its project agent directory is created for the first
time. The agent toolchain is optional for contributors who prefer to work
without an AI coding agent.

## Local setup

Token Meter has no Python package installation step. Its Python server uses only
the standard library.

For the full macOS or Linux desktop experience, run the installer from your
checkout:

```bash
./scripts/install
```

This starts the local server and menu bar or desktop tray companion. Open
[http://localhost:8722](http://localhost:8722) and check its health with:

```bash
curl http://127.0.0.1:8722/health
```

On Windows, use the per-user wrapper from a checkout:

```powershell
.\scripts\install-windows.cmd
```

The wrapper applies process-scoped execution-policy bypass and delegates to the
PowerShell installer. It installs missing Git and Python through WinGet, then
stages the same manifest-owned Python server under
`%LOCALAPPDATA%\Token Meter\runtime`, starts the WinForms tray companion, and
registers only the current user's login startup entry. For a first-install
smoke test, use the one-command bootstrap documented in `README.md`. For parser
or installer debugging, invoke `scripts\install-windows.ps1` directly with
`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File`.

Rerun `./scripts/install` after source changes when you want to test the staged
runtime. For dashboard-only development, when port 8722 is free, run:

```bash
python3 meter.py
```

The macOS menu bar companion requires the Swift toolchain. The Linux tray requires
GTK 3, PyGObject, and Ayatana AppIndicator. Token Meter works best
when the machine already has Claude, Codex, Cursor, OpenCode, or Kiro session evidence. If it does
not, the app should still start; create a normal agent session and reload the
dashboard to see live data.

## Where to make changes

The authoritative ownership and data-flow explanation is
[ARCHITECTURE.md](ARCHITECTURE.md). This table is only a quick change locator.

| Path | Purpose |
| --- | --- |
| `meter.py` | Small executable and import-compatibility facade |
| `token_meter/runtimes/` | Runtime discovery and evidence adapters |
| `token_meter/domain/` | Runtime-neutral usage, timing, tools, insights, and aggregates |
| `token_meter/models/` | Provider-scoped model catalog, pricing, and histories |
| `token_meter/quotas/` | Read-only account-provider quota adapters |
| `token_meter/platforms/` | Host paths, processes, updates, launchers, and trash policy |
| `token_meter/services/` | Application orchestration and public client services |
| `token_meter/telemetry/` | Pure privacy projection and OTel-shaped mapping only |
| `page.html` | Browser dashboard |
| `menubar/TokenMeterMenuBar.swift` | Native macOS menu bar companion |
| `menubar/token_meter_tray.py` | Native Linux AppIndicator companion |
| `scripts/run-tray.ps1` | Native Windows NotifyIcon companion |
| `token_meter_mcp.py` | Read-only local MCP integration |
| `tests/` | Python tests |
| `scripts/` | Installation and runtime helpers |
| `runtime-manifest.txt` | Shared staged-runtime inventory for every installer |

## Project guardrails

- Keep `meter.py` and `token_meter_mcp.py` dependency-free and on the Python
  standard library.
- Keep the dashboard local-only. Do not add external telemetry or hosted assets.
- Keep provider usage requests narrow, bounded, sanitized, and read-only. Never
  log, persist, expose, or send credentials or raw provider responses elsewhere.
- Keep access to Cursor transcripts, shared state, and request traces read-only.
  Preserve the existing `est` labels and unavailable states for values that are
  not authoritative.
- Do not commit local traces, prompts, responses, customer information,
  credentials, generated logs, `.DS_Store`, `__pycache__/`, or `.build/`.
- Preserve existing behavior outside the approved issue or reported bug.
- Keep runtime, model provider, account provider, and operating system as
  separate identities. Shared domain and transport code must not branch on a
  runtime name.

See [SECURITY.md](SECURITY.md) for the full security and privacy model.

## Extension recipes

### Adding a runtime

1. Implement `RuntimeAdapter` in `token_meter/runtimes/<runtime>.py`. Keep raw
   locators inside `SourceLocator`, return content-free `NormalizedSession`
   values, and make deletion targets explicit and bounded.
2. Add one `RuntimeDescriptor` and one registry entry in `token_meter/app.py`.
   Model identity belongs in `ModelRef`; do not use the runtime id as the price
   provider unless they are genuinely the same service.
3. Add sanitized discovery, revision, partial/corrupt, unavailable-versus-zero,
   normalized-load, and compatibility tests under `tests/runtimes/` and
   `tests/fixtures/`.
4. Run the architecture guards. The dashboard and native clients must pick up
   the runtime from `runtime_catalog`; do not add runtime-name switches.

```bash
python3 -m unittest tests.contracts.test_runtime_registry tests.contracts.test_runtime_catalog -v
python3 -m unittest discover -s tests/runtimes -v
```

### Adding a model

Add ordinary provider/model rules to `token_meter/models/catalog.py`. Add a
new pricing resolver only when matching semantics differ. Preserve exact
effective-time boundaries, longest-prefix behavior, and explicit unavailable
results; never fall through to another provider's table.

```bash
python3 -m unittest tests.contracts.test_model_pricing tests.integration.test_model_pricing_compat -v
```

### Adding a quota adapter

Implement the bounded read-only request and parser in
`token_meter/quotas/<provider>.py`, then register it by canonical
`account_provider_id` in `token_meter/app.py`. Runtime support must continue
when the quota capability is missing or fails. Public errors may contain only
stable codes and sanitized messages.

```bash
python3 -m unittest tests.contracts.test_quota_registry tests.contracts.test_quota_privacy -v
```

### Adding a platform

Implement `PlatformServices` under `token_meter/platforms/` and register the
host selector in `registry.py`. Keep installer, startup/service ownership,
native tray, update helper, and uninstall logic platform-native. All installers
must consume `runtime-manifest.txt` and run its parity validator before they
change services. Portable contracts are necessary, but install, automatic
startup, endpoint ownership, tray interaction, parity, and uninstall must also
be proven on the target operating system.

```bash
python3 -m unittest tests.contracts.test_platform_services tests.contracts.test_runtime_manifest -v
python3 -m unittest tests.contracts.test_windows_packaging -v
```

### Adding a telemetry mapping

Extend the allowlist in `token_meter/telemetry/privacy.py` first, then map only
that immutable aggregate in `otel_mapping.py`. Add adversarial privacy and
no-I/O tests. The repository intentionally contains no telemetry SDK, exporter,
collector address, or background export setting; adding one requires a separate
privacy and dependency decision.

```bash
python3 -m unittest tests.contracts.test_telemetry_mapping -v
```

## Validation

Run these checks from the repository root before opening a pull request:

```bash
PYTHONPYCACHEPREFIX=/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print | LC_ALL=C sort)
python3 -m unittest discover -s tests -v
bash -n scripts/install scripts/install-linux scripts/install-launch-agent scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/uninstall-systemd-user scripts/update scripts/update-linux
node -e "const fs=require('fs'); const html=fs.readFileSync('page.html','utf8'); const m=html.match(/<script>([\\s\\S]*)<\\/script>/); new Function(m[1]); console.log('js ok')"
git diff --check
```

For menu bar changes, also run:

```bash
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
```

On Windows, parse every PowerShell artifact, exercise the CMD wrapper, and run
the real NotifyIcon smoke:

```powershell
Get-ChildItem .\scripts\*.ps1 | ForEach-Object {
  [void][scriptblock]::Create([IO.File]::ReadAllText($_.FullName))
}
.\scripts\install-windows.cmd
powershell.exe -NoLogo -NoProfile -File .\scripts\run-tray.ps1 -SmokeTest
```

For visible dashboard or menu bar changes, test the behavior in the running app
and include a screenshot. If you cannot run a check—for example, because the
Swift toolchain is unavailable—say so in the pull request.

## Pull request checklist

Keep the pull request focused and include:

- The problem and why the change is needed.
- What changed.
- The feature-issue link, when applicable.
- The validation commands run and their results.
- Screenshots for visible dashboard or menu bar changes.
- Any limitations, skipped checks, or follow-up work.

Before submitting, review the diff for sensitive local data and unrelated
changes.
