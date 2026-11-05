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
  (`AGY_HOME`, `AGY_BRAIN_DIR`, or their `ANTIGRAVITY_` forms).

Server: `http://127.0.0.1:8722` (loopback only). Read-only endpoints:
`/health`, `/menubar`, `/updates/status`, `/agent-access/status`, and
`/work-insights/status`. Only `/health` is safe to print whole: the others include
project names, session titles, local paths, shell commands, or action tokens. Read them
only through the helper in section 3.

Code map, when a source checkout is available: read `specs/ARCHITECTURE.md` first.
Runtime parsing is in `token_meter/runtimes/`, costs in `token_meter/domain/usage.py`
and `token_meter/models/`, host paths in `token_meter/platforms/`, the HTTP server in
`token_meter/app.py` and `token_meter/web/`, the dashboard in `page.html`, the MCP in
`token_meter_mcp.py` and `token_meter/mcp/`, the companion in `menubar/`.

## 3. Gather evidence

Work from the symptom, one check at a time, and say what each result rules in or out.
Use only the commands below; each prints a fixed, content-free shape. On Windows use
`py` for `python3`, `curl.exe` for `curl`, and PowerShell for the service checks.

First save this helper with your file-writing tool as `tm_debug.py` in the system temp
folder (`$TMPDIR` or `/tmp`; `%TEMP%` on Windows), and delete it when you finish:

```python
# tm_debug.py: content-free Token Meter evidence. Prints only allowlisted shapes.
import json, os, re, sys, time, urllib.error, urllib.request
BASE = "http://127.0.0.1:8722"
WITHHELD = re.compile(r"token|title|project|command|path|cwd|url|name|email", re.I)

def leaf(value):
    if isinstance(value, list): return all(leaf(v) for v in value)
    return value is None or isinstance(value, (bool, int, float, str))

def safe(value):
    if isinstance(value, list): return [safe(v) for v in value]
    if isinstance(value, str) and (re.search(r"[\\/@]", value) or len(value) > 120):
        return "<withheld>"
    return value

def walk(value, parts):
    if not parts: return value
    if parts[0] == "*":
        return [walk(v, parts[1:]) for v in value] if isinstance(value, list) else []
    return walk(value.get(parts[0]) if isinstance(value, dict) else None, parts[1:])

def field(endpoint, *keys):
    body = json.load(urllib.request.urlopen(BASE + endpoint, timeout=10))
    for key in keys:
        value = walk(body, key.split("."))
        if WITHHELD.search(key): shown = "<withheld>"
        elif not leaf(value): shown = "<object: name a leaf field>"
        else: shown = json.dumps(safe(value))
        print(key, shown)

def status(endpoint):
    try: print(endpoint, urllib.request.urlopen(BASE + endpoint, timeout=10).status)
    except urllib.error.HTTPError as error: print(endpoint, error.code)
    except OSError as error: print(endpoint, "unreachable", type(error).__name__)

def tree(root):
    root = os.path.expanduser(os.path.expandvars(root))
    if not os.path.exists(root): return print("missing")
    files = [root] if os.path.isfile(root) else [
        os.path.join(d, f) for d, _, names in os.walk(root, onerror=lambda e: None) for f in names]
    stats = [os.stat(f) for f in files if os.path.isfile(f)]
    newest = max((s.st_mtime for s in stats), default=None)
    print("files", len(stats), "bytes", sum(s.st_size for s in stats), "newest",
          time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else "none")

FRAME = re.compile(r'File "([^"]+)", line (\d+), in ([A-Za-z_]\w*)')
EXC = re.compile(r"^\s*((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Warning|Exit|Interrupt))\b(?::\s*(\[Errno \d+\]))?")

def log(source):
    text = sys.stdin.read() if source == "-" else open(source, errors="replace").read()
    for line in text.splitlines()[-400:]:
        frame = FRAME.search(line)
        if "Traceback (most recent call last)" in line: print("Traceback")
        elif frame:
            path = frame.group(1).replace("\\", "/")
            known = ("/token_meter/" in path or path.endswith(("/meter.py", "/token_meter_mcp.py"))
                     or (re.search(r"/(lib/python[\d.]*|Lib)/", path) and "site-packages" not in path))
            print("  frame", (os.path.basename(path) + ":" + frame.group(2) + " in " + frame.group(3)) if known else "<other>")
        elif EXC.search(line):
            match = EXC.search(line)
            print(" ", match.group(1), match.group(2) or "")

commands = {"field": field, "status": status, "tree": tree, "log": log}
commands[sys.argv[1]](*sys.argv[2:])
```

Run it as `python3 <temp>/tm_debug.py <command> ...`:

- `field <endpoint> <keys>...`: named fields only. Keys are dotted, `*` walks a list
  (quote such keys in the shell). Keys naming tokens, titles, projects, commands, paths,
  or names print `<withheld>`; strings with a path or `@` are withheld too.
- `status <endpoint>`: the HTTP status code, or `unreachable`.
- `tree <root>`: file count, total bytes, and newest modification time for a trace
  root, a folder or a single database file, or `missing`. It never prints names.
- `log <file>` or `log -` (stdin): only `Traceback`, Token Meter and standard-library
  frames (file name, line, function), and exception class names with an errno. Every
  message, path, and other frame is dropped.

Checks by symptom:

- **Always**: OS and version, `python3 --version`, `INSTALLED_REVISION`,
  `INSTALL_MODE`, and `curl -fsS http://127.0.0.1:8722/health` (readiness, source counts
  per client, `runtime_adapter_failures`, `pricing_revision`).
- **Dashboard or server down**: `status /health`; service state:
  - macOS: `launchctl print gui/$(id -u)/com.token-meter.server | grep -E 'state|last exit'`
  - Linux: `systemctl --user show -p ActiveState,SubState,Result,ExecMainStatus token-meter-server`
  - Windows: `[bool](Get-Process -Id (Get-Content "$env:LOCALAPPDATA\Token Meter\runtime\meter.pid") -ErrorAction SilentlyContinue)`,
    and whether the sign-in entry exists (report only true or false):
    `[bool](Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Token Meter' -ErrorAction SilentlyContinue)`

  Then `log <runtime>/meter.err.log` (Linux:
  `journalctl --user -u token-meter-server --no-pager -n 400 -o cat | python3 <temp>/tm_debug.py log -`).
- **Menu bar or tray missing**: `INSTALL_MODE` (backend-only means no Swift toolchain at
  install); companion state the same way (`com.token-meter.menubar`,
  `token-meter-tray`, `tray.pid`); `log` on `menubar.err.log` or `tray.err.log` (Linux:
  the `token-meter-tray` journal); `status /menubar`.
- **Sessions or a runtime missing**: that client's count in `/health`
  `source_clients`; `runtime_adapter_failures`; `tree` on its trace root.
- **Cost looks wrong**: the `tokenmeter` MCP, if connected: `sessions` to find the run,
  `trace` for its standardized usage, `stats` for model totals and `coverage`. Note the
  runtime, model, token split, and whether executions are unpriced. Ask which number
  they expected and where it came from (a provider bill, another tool).
- **Git page empty**: on macOS, a server started by a LaunchAgent may be blocked by
  privacy controls from reading repositories in Documents, Desktop, or Downloads. Ask
  where the repositories live.
- **MCP or skills**: `field /agent-access/status 'clients.*.id' 'clients.*.detected'
  'clients.*.connected' 'clients.*.skills.state' 'clients.*.skills.installed'`; and
  whether a new agent session was started after connecting.
- **Updates**: `field /updates/status state message ahead behind dirty`.
- **Work insights**: `field /work-insights/status status.state status.reason
  status.pending status.setup.state`.

Ask the user a question when the next step depends on something only they know
(which session, what they expected, what changed, whether a retry fixes it). Ask one
or two at a time, never a long questionnaire.

Then state a diagnosis: the observed failure, the likely cause, how confident you are,
and the evidence for each. Keep the observed failure separate from the inferred cause.

## 4. Finish with the chosen outcome

**Diagnose**: the diagnosis, then the smallest fix or workaround to try, and how to
confirm it worked. When a newer version is available, the dashboard's **Settings →
Software updates** card can install it. Otherwise, after the user agrees, reinstall by
running the installer inside the folder named in `SOURCE_CHECKOUT`: `scripts/install`
on macOS, `scripts/install-linux` on Linux, `scripts\install-windows.cmd` on Windows.
Never run the installer copy inside the runtime folder: it re-points updates at the
runtime itself. Offer the issue or pull request next.

**Shareable issue**: draft it in this shape, then show it to the user:

1. Title: the symptom in under 80 characters.
2. Summary: one or two sentences.
3. Environment: OS and version, Python version, installed revision, install mode,
   affected runtimes.
4. Steps to reproduce, expected, and actual behavior.
5. Evidence: `/health` fields and the helper's output (fields, status codes, tree
   counts, log frames and exception classes).
6. Diagnosis so far: observed failure, suspected cause, confidence, what was ruled out.

Ask the user to review the exact text. Create it only after they say yes, with
`gh issue create --repo splunk/token-meter`, then report the issue URL. If `gh` is not
signed in, give them the draft and https://github.com/splunk/token-meter/issues/new.

**Fix and pull request**: work in the user's own clone, or clone
`https://github.com/splunk/token-meter` (or their fork) into a folder they choose. Never
branch or edit inside `SOURCE_CHECKOUT` when it is Token Meter's managed clone (a
`source` folder beside the runtime): updates stop following a changed or non-main
checkout. Read `specs/AGENTS.md` and `specs/CONTRIBUTING.md` and follow them. Create a
focused branch, reproduce the bug with a failing test built only from synthetic data,
and make the smallest fix; use the MCP `trace` tool when you need a session's sanitized
structure. Run the tests. To confirm the live behavior, ask before reinstalling from the
branch: it replaces the running app with unreleased code and makes updates follow that
checkout. Before that reinstall, read `SOURCE_CHECKOUT` and keep its value (do not
show it); afterwards, restore by running the installer inside that saved folder. Show
the diff and the test results, then ask before each of commit, push, and pull request
creation. Fork first if they lack write access.

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
