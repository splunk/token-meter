---
name: token-meter-debug
description: Use when the user runs /token-meter-debug, or reports that Token Meter is broken or wrong - the dashboard or menu bar will not load, sessions or a runtime are missing, costs look wrong, the MCP or skills do not connect, updates or Work insights are stuck - and wants it diagnosed, written up as a shareable GitHub issue, or fixed in a pull request.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Debug Token Meter

Diagnose a Token Meter problem on this computer, then finish with the one outcome the
user picks. Gather evidence yourself, ask the user only what the evidence cannot tell
you, and keep everything you show or share free of private data.

## 1. Ask what they want

Start by asking two things in one message, unless the request already answers them:

1. **The problem**, in their words: what they did, what they expected, what happened,
   and when it started (after an update, a new runtime, a reboot).
2. **The outcome**:
   - **Diagnose**: find the cause and explain it, with a fix or workaround to try.
   - **Shareable issue**: a sanitized GitHub issue draft for `splunk/token-meter`
     that a maintainer can act on without asking for more.
   - **Fix and pull request**: reproduce in a source checkout, fix it with a test,
     and prepare a pull request.

If they are unsure, default to **Diagnose**; offer the other two at the end. If the
problem is a security vulnerability, stop and point them to the project's
`specs/SECURITY.md` (private report). Never draft a public issue for it.

## 2. Where Token Meter lives

| Item | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Installed runtime | `~/Library/Application Support/Token Meter/runtime` | `${XDG_DATA_HOME:-~/.local/share}/token-meter/runtime` | `%LOCALAPPDATA%\Token Meter\runtime` |
| Server logs | `runtime/meter.log`, `meter.err.log` | `journalctl --user -u token-meter-server` | `runtime\meter.log`, `meter.err.log` |
| Companion | LaunchAgent `com.token-meter.menubar`, `menubar.err.log` | `token-meter-tray.service` | `tray.log`, `tray.err.log` |
| Server service | LaunchAgent `com.token-meter.server` | `token-meter-server.service` | Run key at sign-in |

`TOKEN_METER_INSTALL_ROOT` overrides the runtime folder. In the runtime folder:
`INSTALLED_REVISION` (installed commit), `INSTALL_MODE` (`full`, or `backend-only` when
the native companion was not built), and `SOURCE_CHECKOUT` (the git checkout updates
come from).

State lives in `~/.token-meter/`: `settings.json`, `update-status.json`,
`notifications.json`, `git-delivery.sqlite3`, `work-insights.sqlite3`, caches, and
`api-token`. Never read or print `api-token`.

Trace roots Token Meter reads (read-only): `~/.claude/projects`, Claude desktop
`claude-code-sessions` under `Claude`/`Claude-3p` app data, `~/.codex/sessions`,
`~/.cursor/projects` plus Cursor's `state.vscdb` and logs, OpenCode's `opencode.db`
(`OPENCODE_DB`), `~/.kiro/sessions`, `~/.pi/agent`, and `~/.hermes/state.db`.

Server: `http://127.0.0.1:8722` (loopback only). Read-only endpoints:

- `/health`: readiness, source counts per client, `runtime_adapter_failures`,
  `pricing_revision`.
- `/menubar`: the compact companion payload.
- `/updates/status`: update state, current and latest revision, message.
- `/agent-access/status`: MCP connection and skill package state per client.
- `/work-insights/status`: classifier queue, setup, and model state.

Some of these responses include an action token, local paths, or shell commands.
Summarize the fields you need; never paste the raw body into an issue.

Code map, when a source checkout is available: read `specs/ARCHITECTURE.md` first.
Runtime parsing is in `token_meter/runtimes/`, costs in `token_meter/domain/usage.py`
and `token_meter/models/`, host paths in `token_meter/platforms/`, the HTTP server in
`token_meter/app.py` and `token_meter/web/`, the dashboard in `page.html`, the MCP in
`token_meter_mcp.py` and `token_meter/mcp/`, the companion in `menubar/`.

## 3. Gather evidence

Work from the symptom, one check at a time, and say what each result rules in or out.

- **Always**: OS and version, `python3 --version`, `INSTALLED_REVISION`,
  `INSTALL_MODE`, and `curl -fsS http://127.0.0.1:8722/health`.
- **Dashboard or server down**: is port 8722 answering; service state
  (`launchctl print gui/$(id -u)/com.token-meter.server`,
  `systemctl --user status token-meter-server`); the last 50 lines of the error log.
- **Menu bar or tray missing**: `INSTALL_MODE` (backend-only means no Swift toolchain at
  install); companion service state and error log; `/menubar` responds.
- **Sessions or a runtime missing**: the client's count in `/health` `source_clients`;
  `runtime_adapter_failures`; whether that runtime's trace root exists, how many files
  it holds, and their newest modification time.
- **Cost looks wrong**: the `tokenmeter` MCP, if connected: `sessions` to find the run,
  `trace` for its standardized usage, `stats` for model totals and `coverage`. Note the
  runtime, model, token split, and whether executions are unpriced. Ask which number
  they expected and where it came from (a provider bill, another tool).
- **Git page empty**: a server started by a LaunchAgent may be blocked by macOS privacy
  controls from reading repositories in Documents, Desktop, or Downloads. Ask where the
  repositories live.
- **MCP or skills**: `/agent-access/status` for the client, and whether a new agent
  session was started after connecting.
- **Updates**: `/updates/status` state, message, ahead, behind, and dirty.
- **Work insights**: `/work-insights/status` state, reason, setup state, and pending
  count.

Ask the user a question when the next step depends on something only they know
(which session, what they expected, what changed, whether a retry fixes it). Ask one
or two at a time, never a long questionnaire.

Then state a diagnosis: the observed failure, the likely cause, how confident you are,
and the evidence for each. Keep the observed failure separate from the inferred cause.

## 4. Finish with the chosen outcome

**Diagnose**: the diagnosis, then the smallest fix or workaround to try, and how to
confirm it worked. Reinstall with the runtime's `scripts/install` (or the platform
installer) only after the user agrees. Offer the issue or pull request next.

**Shareable issue**: draft it in this shape, then show it to the user:

1. Title: the symptom in under 80 characters.
2. Summary: one or two sentences.
3. Environment: OS and version, Python version, installed revision, install mode,
   affected runtimes.
4. Steps to reproduce, expected, and actual behavior.
5. Evidence: `/health` fields, error class names and log lines with paths replaced by
   `<home>` or `<path>`, counts, and file sizes.
6. Diagnosis so far: observed failure, suspected cause, confidence, what was ruled out.

Ask the user to review the exact text. Create it only after they say yes, with
`gh issue create --repo splunk/token-meter`, then report the issue URL. If `gh` is not
signed in, give them the draft and the new-issue link instead.

**Fix and pull request**: use a source checkout (`SOURCE_CHECKOUT`, the user's clone, or
clone `https://github.com/splunk/token-meter` after asking where). Read
`specs/AGENTS.md` and `specs/CONTRIBUTING.md` and follow them. Create a focused branch,
reproduce the bug with a failing test, make the smallest fix, run the tests, and
reinstall from the checkout to confirm the live behavior. Show the diff and the test
results, then ask before each of commit, push, and pull request creation. Fork first if
they lack write access.

## Rules

- Diagnosis is read-only. Never edit, move, or delete trace files, databases, settings,
  or the installed runtime. Change code only in a source checkout, then reinstall.
- Inspect trace files by existence, size, count, and modification time. Never open or
  print their contents.
- Never include prompts, responses, reasoning, tool arguments or results, session
  titles, project or repository names, file paths, session ids, account details,
  credentials, or action tokens in anything you show or share. Replace paths with
  `<home>` or `<path>`.
- Label costs as estimates. Unavailable evidence is unknown, never zero.
- Never use `sudo` or disable operating-system security controls.
- Post nothing to GitHub or anywhere else without the user's yes on the exact text.
