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
only through the helper in section 3, which allows a fixed set of fields.

Code map, when a source checkout is available: read `specs/ARCHITECTURE.md` first.
Runtime parsing is in `token_meter/runtimes/`, costs in `token_meter/domain/usage.py`
and `token_meter/models/`, host paths in `token_meter/platforms/`, the HTTP server in
`token_meter/app.py` and `token_meter/web/`, the dashboard in `dashboard/` (one file
per view; `dashboard/page.manifest` lists the order), the MCP in
`token_meter_mcp.py` and `token_meter/mcp/`, the companion in `menubar/`.

## 3. Gather evidence

Work from the symptom, one check at a time, and say what each result rules in or out.
Use only the commands below; each prints a fixed, content-free shape. On Windows use
`py` for `python3`, `curl.exe` for `curl`, and PowerShell for the service checks.

First make a private temp folder (`d=$(mktemp -d)`; on Windows
`$d = New-Item -ItemType Directory (Join-Path $env:TEMP ([guid]::NewGuid()))`), save
this helper there as `tm_debug.py` with your file-writing tool, and run it with
`python3 -I "$d/tm_debug.py" <command> ...` (`-I` keeps other files in the folder from
being imported). Shell state may not carry between commands, so reuse the folder's
literal path in later commands, and keep it out of anything you share. On Windows use `py -I`, else `python -I`, else the interpreter named in
the runtime's `PYTHON_EXECUTABLE` file (use it, do not show it). Delete the folder when
you finish.

```python
# tm_debug.py: content-free Token Meter evidence. Prints only allowlisted shapes.
import builtins, json, os, re, stat, sys, sysconfig, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8722"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open
FIELDS = {
    "/health": {"ok", "state_ready", "inventory_ready", "page_ready", "sources",
                "pricing_recalculating", "port"},
    "/updates/status": {"enabled", "auto_install", "state", "message", "checking",
                        "updating", "available", "can_update", "dirty", "ahead", "behind",
                        "current_revision", "latest_revision"},
    "/agent-access/status": {"ok", "launcher_ready", "clients.*.id", "clients.*.detected",
                             "clients.*.available", "clients.*.configured",
                             "clients.*.connected", "clients.*.conflict",
                             "clients.*.skills.state", "clients.*.skills.installed",
                             "clients.*.skills.total"},
    "/work-insights/status": {"ok", "status.state", "status.reason", "status.pending",
                              "status.queued", "status.labels", "status.setup.state",
                              "settings.enabled"},
}
ENDPOINT = re.compile(r"^/[a-z][a-z/-]{0,40}$")
def walk(value, parts):
    if not parts: return value
    if parts[0] == "*":
        return [walk(v, parts[1:]) for v in value] if isinstance(value, list) else []
    return walk(value.get(parts[0]) if isinstance(value, dict) else None, parts[1:])

def safe(value):
    if isinstance(value, list): return [safe(v) for v in value]
    if isinstance(value, dict): return "<object>"
    if isinstance(value, str) and (re.search(r"[\\/@]", value) or len(value) > 120):
        return "<withheld>"
    return value

def field(endpoint, *keys):
    if endpoint not in FIELDS: return print(endpoint, "<endpoint not allowed>")
    body = json.load(OPEN(BASE + endpoint, timeout=10))
    for key in keys:
        allowed = key in FIELDS[endpoint]
        print(key, json.dumps(safe(walk(body, key.split(".")))) if allowed else "<not allowed>")

def status(endpoint):
    if not ENDPOINT.match(endpoint): return print("<endpoint not allowed>")
    try: print(endpoint, OPEN(BASE + endpoint, timeout=10).status)
    except urllib.error.HTTPError as error: print(endpoint, error.code)
    except OSError as error: print(endpoint, "unreachable", type(error).__name__)

def tree(root):
    root = os.path.expanduser(os.path.expandvars(root))
    try: top = os.stat(root)
    except FileNotFoundError: return print("missing")
    except OSError: return print("unreadable")
    errors, files = [], [root]
    if not (stat.S_ISDIR(top.st_mode) or stat.S_ISREG(top.st_mode)): return print("unreadable")
    if stat.S_ISDIR(top.st_mode):
        try: os.listdir(root)
        except OSError: return print("unreadable")
        files = [os.path.join(d, f) for d, _, names in os.walk(root, onerror=errors.append)
                 for f in names]
    count = size = 0
    newest = None
    for path in files:
        try: info = os.stat(path)
        except OSError: errors.append(path); continue
        if stat.S_ISREG(info.st_mode) and not os.access(path, os.R_OK): errors.append(path)
        elif stat.S_ISREG(info.st_mode):
            count, size = count + 1, size + info.st_size
            newest = max(newest or 0, info.st_mtime)
    print("files", count, "bytes", size, "newest",
          time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else "none",
          "unreadable", len(errors))

FRAME = re.compile(r'File "([^"]+)", line (\d+), in (<\w{1,40}>|[A-Za-z_]\w{0,60})$')
EXC = re.compile(r"^\s*((?:[A-Za-z_]\w{0,60}\.){0,6})"
                 r"([A-Za-z_]\w{0,60}(?:Error|Exception|Warning|Exit|Interrupt))"
                 r"(?=:|\s*$)(?::\s*(\[(?:Errno|WinError) -?\d{1,10}\]))?")

def runtime_roots():
    roots = [os.environ.get("TOKEN_METER_INSTALL_ROOT", ""),
             "~/Library/Application Support/Token Meter/runtime",
             os.path.join(os.environ.get("XDG_DATA_HOME") or "~/.local/share", "token-meter", "runtime"),
             os.path.join(os.environ.get("LOCALAPPDATA", "~"), "Token Meter", "runtime")]
    return [os.path.expanduser(r) for r in roots if r]

ROOTS = {"runtime": runtime_roots(), "stdlib": [sysconfig.get_paths()["stdlib"]]}
SOURCES = {}

def real_source(kind, relative):
    # Text of a real installed Token Meter or standard-library file, else None.
    if (kind, relative) not in SOURCES:
        SOURCES[(kind, relative)] = None
        for root in ROOTS[kind]:
            path = os.path.join(root, *relative.split("/"))
            try:
                if not stat.S_ISREG(os.stat(path).st_mode): continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    SOURCES[(kind, relative)] = fh.read(); break
            except OSError: pass
    return SOURCES[(kind, relative)]

def under_root(path):
    for root in ROOTS["runtime"]:
        prefix = root.replace("\\", "/").rstrip("/") + "/"
        if path.startswith(prefix): return path[len(prefix):]
    match = re.search(r"/runtime/(.+)$", path)
    return match.group(1) if match else ""

def frame_name(path, function):
    path = path.replace("\\", "/")
    own = re.fullmatch(r"(token_meter/[\w/]{1,120}\.py|meter\.py|token_meter_mcp\.py)", under_root(path))
    std = re.search(r"/(?:lib/python3(?:\.\d+t?)?|Lib)/([\w/]{1,120}\.py)$", path)
    kind, relative = ("runtime", own.group(1)) if own else ("stdlib", std.group(1)) if std else (None, None)
    text = real_source(kind, relative) if kind and "site-packages" not in path else None
    if text is None: return None
    if not function.startswith("<") and not re.search(r"\bdef %s\b" % re.escape(function), text): return None
    return os.path.basename(path)

# Standard-library exceptions defined in C, so no Python file declares them.
C_EXCEPTIONS = {
    "sqlite3": {"Error", "Warning", "DatabaseError", "OperationalError", "IntegrityError",
                "ProgrammingError", "InterfaceError", "DataError", "NotSupportedError",
                "InternalError"},
    "ssl": {"SSLError", "SSLZeroReturnError", "SSLWantReadError", "SSLWantWriteError",
            "SSLSyscallError", "SSLEOFError", "SSLCertVerificationError"},
    "socket": {"gaierror", "herror"},
    "zlib": {"error"}, "binascii": {"Error"}, "select": {"error"},
}

def exception_name(prefix, name):
    if not prefix: return name if isinstance(getattr(builtins, name, None), type) else "<other exception>"
    if name in C_EXCEPTIONS.get(prefix, ()): return name
    relative = prefix.replace(".", "/")
    kind = "runtime" if prefix.split(".")[0] == "token_meter" else "stdlib"
    declared = re.compile(r"\bclass %s\b|^\s*from\s+[\w.]+\s+import\s+[^\n]*\b%s\b"
                          % (re.escape(name), re.escape(name)), re.M)
    for candidate in (relative + ".py", relative + "/__init__.py"):
        text = real_source(kind, candidate)
        if text and declared.search(text): return name
    return "<other exception>"

def log(source):
    try:
        if source == "-": data = sys.stdin.buffer.read()[-2000000:]
        else:
            if not stat.S_ISREG(os.stat(source).st_mode): return print("unreadable")
            with open(source, "rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 2000000))
                data = handle.read()
    except FileNotFoundError: return print("missing")
    except OSError: return print("unreadable")
    printed = False
    for line in data.decode("utf-8", "replace").splitlines()[-400:]:
        line = line[:500].rstrip()
        frame, exc = FRAME.search(line), EXC.search(line)
        if "Traceback (most recent call last)" in line: print("Traceback")
        elif frame:
            name = frame_name(frame.group(1), frame.group(3))
            print("  frame", f"{name}:{frame.group(2)} in {frame.group(3)}" if name else "<other>")
        elif exc: print(" ", exception_name(exc.group(1).rstrip("."), exc.group(2)), exc.group(3) or "")
        else: continue
        printed = True
    if not printed: print("no traceback found")

def main(argv):
    commands = {"field": field, "status": status, "tree": tree, "log": log}
    try: commands[argv[0]](*argv[1:])
    except Exception as error: print("error", type(error).__name__)

if __name__ == "__main__":
    main(sys.argv[1:])
```

Commands:

- `field <endpoint> <keys>...`: only the fields this helper allows for `/health`,
  `/updates/status`, `/agent-access/status`, and `/work-insights/status`; anything else
  prints `<not allowed>`. Keys are dotted, `*` walks a list (quote such keys in the
  shell). Strings with a path or `@` print `<withheld>`.
- `status <endpoint>`: the HTTP status code, or `unreachable`.
- `tree <root>`: file count, total bytes, newest modification time, and how many entries
  could not be read, for a folder or a single database file; or `missing` or
  `unreadable`. Unreadable (including files it cannot open) means unknown, not zero. It never prints names.
- `log <file>` or `log -` (stdin): only `Traceback`, Token Meter and standard-library
  frames (file name, line, function) whose file really exists in the installed runtime or
  this Python's standard library and defines that function, and exception classes that
  are built in, declared or imported in such a file, or a known C-level standard-library
  exception (such as `sqlite3.OperationalError`), with an errno; anything else shows as
  `<other>` or `<other exception>`. It prints `missing`, `unreadable`, or `no traceback found` when
  there is nothing to show.

Any failure prints only `error <ExceptionClass>`.

Checks by symptom:

- **Always**: OS and version, `python3 --version` (Windows: `py --version`), the
  contents of `INSTALLED_REVISION` and `INSTALL_MODE` (Windows:
  `Get-Content -ErrorAction SilentlyContinue` on each), and
  `curl -fsS http://127.0.0.1:8722/health` (readiness, source counts per client,
  `runtime_adapter_failures`, `pricing_revision`).
- **Dashboard or server down**: `status /health`; service state:
  - macOS: `launchctl print gui/$(id -u)/com.token-meter.server | grep -E 'state|last exit'`
  - Linux: `systemctl --user show -p ActiveState,SubState,Result,ExecMainStatus token-meter-server`
  - Windows: `$p = Get-Content -ErrorAction SilentlyContinue "$env:LOCALAPPDATA\Token Meter\runtime\meter.pid"; [bool]($p -and ([string](Get-CimInstance Win32_Process -Filter "ProcessId = $p" -ErrorAction SilentlyContinue).CommandLine -like '*meter.py*'))`
    (for the tray, `tray.pid` and `'*run-tray.ps1*'`), and whether the sign-in entry
    exists (report only true or false):
    `[bool](Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Token Meter' -ErrorAction SilentlyContinue)`

  Then `log <runtime>/meter.err.log` (Linux:
  `journalctl --user -u token-meter-server --no-pager -n 400 -o cat | python3 -I "$d/tm_debug.py" log -`).
- **Menu bar or tray missing**: `INSTALL_MODE` (backend-only means no Swift toolchain at
  install); companion state the same way (`com.token-meter.menubar`,
  `token-meter-tray`, `tray.pid`); `log` on `menubar.err.log` or `tray.err.log` (Linux:
  the `token-meter-tray` journal); `status /menubar`. Never read `/menubar` itself: it
  carries session titles and ids.
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
confirm it worked. Token Meter installs available updates on its own, or from the menu
bar's **New update available**; **Settings → Software updates** shows the state and has
**Check now**. To reinstall the current version, after the user agrees, run the
installer inside the folder named in `SOURCE_CHECKOUT`: `scripts/install` on macOS,
`scripts/install-linux` on Linux, `scripts\install-windows.cmd` on Windows. Never run
the installer copy inside the runtime folder: it re-points updates at the runtime
itself. Offer the issue or pull request next.

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
branch or edit inside Token Meter's managed clone: a folder named `source` or
`source-...` beside the runtime, or the `TOKEN_METER_SOURCE_ROOT` path. Updates stop
following a changed or non-main checkout. Read `specs/AGENTS.md` and
`specs/CONTRIBUTING.md` and follow them. Create a focused branch, reproduce the bug with
a failing test built only from synthetic data, and make the smallest fix; use the MCP
`trace` tool when you need a session's sanitized structure. Run the tests.

To confirm the live behavior, ask before reinstalling from the branch: it replaces the
running app with unreleased code and makes updates follow that checkout. Before it,
note, without showing them, the value of `SOURCE_CHECKOUT`, the `INSTALL_MODE`, and any
`TOKEN_METER_INSTALL_ROOT` or `TOKEN_METER_SOURCE_ROOT` setting. Run both the branch
install and the restore with those settings: `--backend-only` (Windows `-BackendOnly`)
when the mode was `backend-only`, and the same `TOKEN_METER_` variables. Install only
from a branch with no upstream; with an upstream, the installer moves Token Meter's managed
clone to the branch. If it has one, run `git branch --unset-upstream` and tell the user
(`git push -u` sets it again).

Afterwards, restore from a clean `main` checkout: the saved `SOURCE_CHECKOUT` when it is
not the clone you worked in, is on `main`, and `git -C <saved> status --porcelain`
prints nothing; otherwise a fresh clone of `https://github.com/splunk/token-meter` in
a new folder, and run its installer with the same settings. Then check that `INSTALLED_REVISION` equals that checkout's
`git rev-parse --short HEAD` exactly, with no `+local`. Never reset, clean, or discard
changes in any checkout.

Show the diff and the test results, then ask before each of commit, push, and pull
request creation. Fork first if they lack write access.

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
