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
problem is a security vulnerability, stop: no public issue. Point them to the private
reporting guidance at
https://github.com/splunk/token-meter/blob/main/specs/SECURITY.md.

## 2. Where Token Meter lives

| Item | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Installed runtime | `~/Library/Application Support/Token Meter/runtime` | `${XDG_DATA_HOME:-~/.local/share}/token-meter/runtime` | `%LOCALAPPDATA%\Token Meter\runtime` |
| Server | LaunchAgent `com.token-meter.server` | `token-meter-server.service` | `Token Meter` value in the user's `Run` key, `meter.pid` |
| Server logs | `meter.log`, `meter.err.log` in the runtime | `journalctl --user -u token-meter-server` | `meter.log`, `meter.err.log` in the runtime |
| Companion | LaunchAgent `com.token-meter.menubar`, `menubar.err.log` | `token-meter-tray.service` (journal) | `tray.pid`, `tray.log`, `tray.err.log` |

`TOKEN_METER_INSTALL_ROOT` overrides the runtime folder. The runtime folder is staged
output, not a checkout. In it: `INSTALLED_REVISION` (installed commit), `INSTALL_MODE`
(`full`, or `backend-only` when the native companion was not built), and
`SOURCE_CHECKOUT` (the git checkout that installs and updates come from).

State lives in `~/.token-meter/`: `settings.json`, `update-status.json`,
`notifications.json`, `git-delivery.sqlite3`, `work-insights.sqlite3`, caches, and
`api-token`. Never read or print `api-token`.

Trace roots Token Meter reads (read-only). `<appdata>` is `~/Library/Application Support`
on macOS, `${XDG_CONFIG_HOME:-~/.config}` on Linux, and `%APPDATA%` on Windows:

- Claude Code: `~/.claude/projects`. Claude desktop: `claude-code-sessions` and
  `local-agent-mode-sessions` under `<appdata>/Claude` and `<appdata>/Claude-3p`.
- Codex: `~/.codex/sessions`. Cursor: `~/.cursor/projects`, plus
  `<appdata>/Cursor/User/globalStorage/state.vscdb` and `<appdata>/Cursor/logs`.
- OpenCode: `opencode.db` under `${XDG_DATA_HOME:-~/.local/share}/opencode`
  (`%LOCALAPPDATA%\opencode` on Windows); `OPENCODE_DB` overrides it.
- Kiro: `~/.kiro/sessions` (`KIRO_SESSIONS`) and the IDE store
  `Kiro/User/globalStorage/kiro.kiroagent` under `<appdata>` (`%LOCALAPPDATA%` on
  Windows; `KIRO_AGENT_STORAGE`).
- Pi: `~/.pi/agent` (`PI_CODING_AGENT_DIR`). Hermes: `~/.hermes/state.db`
  (`HERMES_HOME`, `HERMES_STATE_DB`). Antigravity: `~/.gemini/antigravity/brain`
  (`AGY_HOME`, `AGY_BRAIN_DIR`).

Server: `http://127.0.0.1:8722` (loopback only). Read-only endpoints:
`/health`, `/menubar`, `/updates/status`, `/agent-access/status`, and
`/work-insights/status`. Only `/health` is safe to print whole: the others include
project names, session titles, local paths, shell commands, or action tokens. Read them
with the filter in section 3.

Code map, when a source checkout is available: read `specs/ARCHITECTURE.md` first.
Runtime parsing is in `token_meter/runtimes/`, costs in `token_meter/domain/usage.py`
and `token_meter/models/`, host paths in `token_meter/platforms/`, the HTTP server in
`token_meter/app.py` and `token_meter/web/`, the dashboard in `page.html`, the MCP in
`token_meter_mcp.py` and `token_meter/mcp/`, the companion in `menubar/`.

## 3. Gather evidence

Work from the symptom, one check at a time, and say what each result rules in or out.
Use only the content-free forms below. On Windows use `py` for `python3` and `curl.exe`
for `curl`, and pipe a here-string into `py -` in place of a heredoc.

Print chosen fields of an endpoint, never the whole body (dotted keys; `*` walks a
list, so quote such keys in the shell):

```bash
python3 - /updates/status state message ahead behind dirty current_revision latest_revision <<'PY'
import json, sys, urllib.request
body = json.load(urllib.request.urlopen("http://127.0.0.1:8722" + sys.argv[1], timeout=10))
def pick(value, keys):
    if not keys: return value
    if keys[0] == "*": return [pick(v, keys[1:]) for v in value or []]
    return pick(value.get(keys[0]) if isinstance(value, dict) else None, keys[1:])
for key in sys.argv[2:]:
    print(key, json.dumps(pick(body, key.split(".")))[:200])
PY
```

Show error lines from a log with every path, and the rest of its line, replaced (a
`.py` file keeps its name):

```bash
python3 - "<runtime>/meter.err.log" <<'PY'
import re, sys
lines = open(sys.argv[1], errors="replace").read().splitlines()[-300:]
path = re.compile(r'"([^"]*[\\/][^"]*)"|((?:[A-Za-z]:)?[\\/][^"\',()]*)')
def cut(m):
    name = re.split(r"[\\/]", m.group(1) or m.group(2))[-1]
    return "<path>/" + name if name.endswith(".py") else "<path>"
for line in lines:
    if re.search(r"Error|Exception|Traceback|line \d+", line):
        print(path.sub(cut, line)[:240])
PY
```

Count a trace root without listing names: `find <root> -type f | wc -l`, and the newest
modification time with
`python3 -c "import os,sys,time;print(time.ctime(max((e.stat().st_mtime for e in os.scandir(sys.argv[1])),default=0)))" <root>`.

- **Always**: OS and version, `python3 --version`, `INSTALLED_REVISION`,
  `INSTALL_MODE`, and `curl -fsS http://127.0.0.1:8722/health` (readiness, source counts
  per client, `runtime_adapter_failures`, `pricing_revision`).
- **Dashboard or server down**: does `curl -s -o /dev/null -w '%{http_code}'
  http://127.0.0.1:8722/health` answer; service state (macOS
  `launchctl print gui/$(id -u)/com.token-meter.server | grep -E 'state|last exit'`,
  Linux `systemctl --user status --no-pager token-meter-server`, Windows
  `Get-Process -Id (Get-Content meter.pid)` in the runtime and whether the `Run` value
  exists); then the error lines from the server log (Linux:
  `journalctl --user -u token-meter-server --no-pager -n 50`, filtered the same way).
- **Menu bar or tray missing**: `INSTALL_MODE` (backend-only means no Swift toolchain at
  install); companion service state the same way (`com.token-meter.menubar`,
  `token-meter-tray`, `tray.pid`) and its error log; the status code of `/menubar`.
- **Sessions or a runtime missing**: that client's count in `/health`
  `source_clients`; `runtime_adapter_failures`; whether its trace root exists, its file
  count, and its newest modification time.
- **Cost looks wrong**: the `tokenmeter` MCP, if connected: `sessions` to find the run,
  `trace` for its standardized usage, `stats` for model totals and `coverage`. Note the
  runtime, model, token split, and whether executions are unpriced. Ask which number
  they expected and where it came from (a provider bill, another tool).
- **Git page empty**: on macOS, a server started by a LaunchAgent may be blocked by
  privacy controls from reading repositories in Documents, Desktop, or Downloads. Ask
  where the repositories live.
- **MCP or skills**: `/agent-access/status` fields `clients.*.id`,
  `clients.*.detected`, `clients.*.connected`, `clients.*.skills.state`, and
  `clients.*.skills.installed`; and whether a new agent session was started after
  connecting.
- **Updates**: `/updates/status` fields `state`, `message`, `ahead`, `behind`, `dirty`.
- **Work insights**: `/work-insights/status` fields `status.state`, `status.reason`,
  `status.pending`, and `status.setup.state`.

Ask the user a question when the next step depends on something only they know
(which session, what they expected, what changed, whether a retry fixes it). Ask one
or two at a time, never a long questionnaire.

Then state a diagnosis: the observed failure, the likely cause, how confident you are,
and the evidence for each. Keep the observed failure separate from the inferred cause.

## 4. Finish with the chosen outcome

**Diagnose**: the diagnosis, then the smallest fix or workaround to try, and how to
confirm it worked. To reinstall, offer the dashboard's **Settings → Updates**, or, after
the user agrees, run the installer inside the folder named in `SOURCE_CHECKOUT`
(`scripts/install` on macOS, `scripts/install-linux` on Linux,
`scripts/install-windows.ps1` on Windows). Never run the installer copy inside the
runtime folder: it re-points updates at the runtime itself. Offer the issue or pull
request next.

**Shareable issue**: draft it in this shape, then show it to the user:

1. Title: the symptom in under 80 characters.
2. Summary: one or two sentences.
3. Environment: OS and version, Python version, installed revision, install mode,
   affected runtimes.
4. Steps to reproduce, expected, and actual behavior.
5. Evidence: `/health` fields, filtered endpoint fields, filtered log lines, counts,
   and file sizes.
6. Diagnosis so far: observed failure, suspected cause, confidence, what was ruled out.

Ask the user to review the exact text. Create it only after they say yes, with
`gh issue create --repo splunk/token-meter`, then report the issue URL. If `gh` is not
signed in, give them the draft and https://github.com/splunk/token-meter/issues/new.

**Fix and pull request**: use a source checkout (`SOURCE_CHECKOUT`, the user's clone, or
clone `https://github.com/splunk/token-meter` after asking where). Read
`specs/AGENTS.md` and `specs/CONTRIBUTING.md` and follow them. Create a focused branch,
reproduce the bug with a failing test built only from synthetic data, and make the
smallest fix; use the MCP `trace` tool when you need a session's sanitized structure.
Run the tests. To confirm the live behavior, ask before reinstalling from the branch:
it replaces the running app with unreleased code and makes updates follow that
checkout. Afterwards, restore by reinstalling from the original `SOURCE_CHECKOUT` on
its usual branch. Show the diff and the test results, then ask before each of commit,
push, and pull request creation. Fork first if they lack write access.

## Rules

- Diagnosis is read-only. Never edit, move, or delete trace files, databases, settings,
  or the installed runtime. Change code only in a source checkout, then reinstall.
- Inspect trace files only by existence, count, size, and modification time. Never open
  them, print their contents, or list their names.
- Never include prompts, responses, reasoning, tool arguments or results, session
  titles, project or repository names, file paths, session ids, account details,
  credentials, or action tokens in anything you show or share. Replace paths with
  `<home>` or `<path>`.
- Never copy local trace, log, or settings content into a repository, issue, or pull
  request.
- Label costs as estimates. Unavailable evidence is unknown, never zero.
- Never use `sudo` or disable operating-system security controls.
- Post nothing to GitHub or anywhere else without the user's yes on the exact text.
