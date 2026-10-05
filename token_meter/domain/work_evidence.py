"""Per-request cost slices and action evidence for Work insights.

A request owns the assistant events and tool calls from its timestamp until the user's next request.
Outputs that persist (``request_slices``) are content-free: costs, model names, effort, and counts of
derived categories. ``evidence_text`` names edited files by basename for the local classifier prompt only.
"""

import bisect
import collections
import json
import os
import re

MAX_NAMED_FILES = 6
MAX_EVIDENCE_CHARS = 420
MAX_COMMAND_CHARS = 2_000
MAX_PATCH_PATHS = 40

TOOL_KINDS = {
    "edit": ("edit", "multiedit", "write", "notebookedit", "apply_patch", "applypatch", "patch", "edit_file",
             "search_replace", "write_file", "create_file", "delete_file", "str_replace_editor",
             "str_replace_based_edit_tool", "replace", "insert"),
    "read": ("read", "notebookread", "read_file", "view_image", "view", "open_file", "readfile"),
    "search": ("glob", "grep", "ls", "list", "list_dir", "codebase_search", "grep_search", "file_search",
               "find", "search", "tool_search"),
    "shell": ("bash", "shell", "exec_command", "shell_command", "local_shell", "run_terminal_cmd", "terminal",
              "run_command", "execute_command", "write_stdin", "run_terminal_command"),
    "web": ("websearch", "webfetch", "web_search", "web.search", "web_fetch", "fetch", "browser"),
    "agent": ("task", "agent", "spawn_agent", "subagent", "send_message"),
    "plan": ("todowrite", "update_plan", "todo_write", "todoread", "todo", "exitplanmode", "enterplanmode",
             "taskcreate", "taskupdate"),
}
_KIND_OF = {name: kind for kind, names in TOOL_KINDS.items() for name in names}

COMMAND_KINDS = ("test", "check", "build", "git", "git_read", "deps", "infra", "service", "data", "http", "run",
                 "inspect")
_TEST_RE = re.compile(
    r"^(pytest|py\.test|jest|vitest|mocha|rspec|phpunit|tox|nox|ctest|playwright\s+test|cypress\s+run"
    r"|go\s+test|cargo\s+(test|nextest)|swift\s+test|mvn\s+(-\S+\s+)*test|gradle\w*\s+test|dotnet\s+test"
    r"|(npm|pnpm|yarn|bun)\s+(run\s+)?test|make\s+(test|check)|xcodebuild\b.*\btest"
    r"|python3?\s+(-\S+\s+)*-m\s+(pytest|unittest)|python3?\s+\S*test\S*\.py)\b")
_CHECK_RE = re.compile(
    r"^(eslint|ruff|flake8|pylint|mypy|pyright|tsc|prettier|black|isort|swiftlint|golangci-lint|shellcheck"
    r"|cargo\s+(clippy|fmt|check)|(npm|pnpm|yarn|bun)\s+(run\s+)?(lint|typecheck|format|check)|git\s+diff\s+--check"
    r"|bash\s+-n|node\s+--check|python3?\s+(-\S+\s+)*-m\s+(py_compile|compileall))\b")
_BUILD_RE = re.compile(
    r"^(make|cmake|ninja|bazel|cargo\s+build|go\s+build|swiftc|swift\s+build|xcodebuild|gradle\w*\s+(build|assemble)"
    r"|mvn\s+(-\S+\s+)*(package|install|compile)|(npm|pnpm|yarn|bun)\s+(run\s+)?build|vite\s+build|webpack|esbuild"
    r"|dotnet\s+build)\b")
_GIT_WRITE_RE = re.compile(
    r"^(git\s+(commit|push|merge|rebase|tag|cherry-pick|revert|reset|stash|checkout\s+-b|switch\s+-c|worktree\s+add|pull|fetch)"
    r"|gh\s+(pr|release|issue|repo|api))\b")
_GIT_READ_RE = re.compile(r"^git\s+(status|diff|log|show|blame|branch|rev-parse|ls-files|remote|config)\b")
_DEPS_RE = re.compile(
    r"^((pip3?|uv\s+pip|pipx)\s+install|uv\s+(add|sync)|poetry\s+(add|install)|(npm|pnpm|bun)\s+(install|i|ci|add)\b"
    r"|yarn(\s+(add|install))?$|yarn\s+add|brew\s+(install|upgrade)|cargo\s+add|go\s+(get|mod)|gem\s+install"
    r"|bundle\s+install|conda\s+install|apt(-get)?\s+install)")
_INFRA_RE = re.compile(
    r"^(docker|docker-compose|podman|kubectl|helm|terraform|tofu|pulumi|aws|gcloud|az|fly|flyctl|vercel|netlify"
    r"|heroku|ansible\S*|serverless|sam|cdk|wrangler)\b")
_SERVICE_RE = re.compile(r"^(launchctl|systemctl|brew\s+services|service|ssh|scp|rsync|nginx|caddy|pm2|supervisorctl)\b")
_DATA_RE = re.compile(r"^(sqlite3|psql|mysql|duckdb|bq|snowsql|dbt|jupyter|spark-submit|clickhouse-client)\b")
_HTTP_RE = re.compile(r"^(curl|wget|http|httpie|xh)\b")
_INSPECT_RE = re.compile(r"^(ls|cat|head|tail|less|grep|rg|ag|find|fd|sed|awk|wc|tree|file|stat|du|df|which|pwd|echo"
                         r"|printf|jq|diff|cmp|open|plutil|defaults\s+read|lsof|ps|top|env|date)\b")
_RUN_RE = re.compile(r"^(python3?|node|deno|bun|ruby|go\s+run|cargo\s+run|swift|java|\./\S+|bash|sh|zsh|npx|uvx)\b")
_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")

FILE_KINDS = ("frontend", "backend", "data", "infra", "tooling", "docs", "tests", "config", "media")
_EXT_KIND = {
    **dict.fromkeys((".html", ".htm", ".css", ".scss", ".sass", ".less", ".jsx", ".tsx", ".vue", ".svelte",
                     ".astro", ".swift", ".xib", ".storyboard", ".kt", ".dart"), "frontend"),
    **dict.fromkeys((".py", ".go", ".rb", ".java", ".rs", ".php", ".cs", ".scala", ".ex", ".exs", ".c", ".cc",
                     ".cpp", ".h", ".hpp", ".m", ".mm", ".proto", ".graphql"), "backend"),
    **dict.fromkeys((".ipynb", ".sql", ".csv", ".parquet", ".jsonl", ".tsv", ".pkl", ".h5", ".onnx", ".safetensors",
                     ".r", ".rmd"), "data"),
    **dict.fromkeys((".tf", ".hcl", ".tfvars", ".dockerfile", ".plist", ".service", ".nomad"), "infra"),
    **dict.fromkeys((".sh", ".bash", ".zsh", ".fish", ".ps1", ".mk", ".just"), "tooling"),
    **dict.fromkeys((".md", ".mdx", ".rst", ".txt", ".adoc", ".tex", ".docx", ".pdf"), "docs"),
    **dict.fromkeys((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".lock", ".xml"), "config"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".mp4", ".mov", ".pptx", ".key"),
                    "media"),
}
_FRONTEND_DIR_RE = re.compile(r"(^|/)(components?|pages?|views?|ui|frontend|client|web|public|static|styles?|app/)"
                              r"|\.(component|page|view)\.")
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|[._-](test|spec)\.[a-z]+$")
_INFRA_PATH_RE = re.compile(r"(^|/)(\.github/workflows|\.circleci|k8s|kubernetes|helm|terraform|infra|deploy|ansible)"
                            r"(/|$)|(^|/)(dockerfile|docker-compose[^/]*|compose\.ya?ml|\.gitlab-ci\.yml|procfile)$",
                            re.I)
_TOOLING_PATH_RE = re.compile(r"(^|/)(scripts?|bin|tools|\.claude|\.agents|\.codex|\.cursor|skills|hooks)/"
                              r"|(^|/)(agents|claude|skill|makefile|justfile|package\.json|pyproject\.toml)(\.md)?$",
                              re.I)
_DATA_PATH_RE = re.compile(r"(^|/)(data|datasets?|notebooks?|ml|models?|training|etl|pipelines?|dbt|analytics|eval\w*)/",
                           re.I)
_AGENT_INSTRUCTION_FILES = frozenset(("agents.md", "claude.md", "skill.md", "gemini.md"))
_PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def tool_kind(name):
    """Coarse kind of a tool call from its name: edit, read, search, shell, web, agent, plan, mcp, or other."""
    text = str(name or "").strip().lower()
    if text.startswith("mcp__") or text.startswith("mcp:") or text.startswith("mcp."):
        return "mcp"
    text = text.rsplit(".", 1)[-1] if text.startswith("functions.") else text
    text = re.sub(r"_v\d+$", "", text)
    return _KIND_OF.get(text, _KIND_OF.get(re.sub(r"[\s-]+", "_", text), "other"))


def _arguments(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return value
            return parsed if isinstance(parsed, dict) else value
    return value


def _command_text(arguments):
    if isinstance(arguments, str):
        return arguments[:MAX_COMMAND_CHARS]
    if not isinstance(arguments, dict):
        return ""
    command = arguments.get("command", arguments.get("cmd", arguments.get("commandLine")))
    if isinstance(command, list):
        parts = [str(part) for part in command]
        if len(parts) >= 3 and parts[0] in ("bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh") \
                and parts[1] in ("-lc", "-c", "-l"):
            return parts[-1][:MAX_COMMAND_CHARS]
        return " ".join(parts)[:MAX_COMMAND_CHARS]
    return str(command or "")[:MAX_COMMAND_CHARS]


def command_kinds(command):
    """Kinds of the commands in a shell line (test, git, build, deps, infra, ...); no command text is kept."""
    found = []
    for segment in re.split(r"&&|\|\||;|\n|\|", str(command or "")[:MAX_COMMAND_CHARS]):
        text = _ENV_PREFIX_RE.sub("", segment.strip())
        text = re.sub(r"^(sudo|time|exec|command|nohup|caffeinate)\s+", "", text)
        if not text or text.startswith(("cd ", "export ", "#", "set ", "source ", ". ")) or text in ("cd",):
            continue
        for kind, pattern in (("test", _TEST_RE), ("check", _CHECK_RE), ("git", _GIT_WRITE_RE),
                              ("git_read", _GIT_READ_RE), ("deps", _DEPS_RE), ("infra", _INFRA_RE), ("service", _SERVICE_RE),
                              ("build", _BUILD_RE), ("data", _DATA_RE), ("http", _HTTP_RE),
                              ("inspect", _INSPECT_RE), ("run", _RUN_RE)):
            if pattern.search(text):
                found.append(kind)
                break
    return found


def _paths(name, arguments):
    if isinstance(arguments, str):
        if tool_kind(name) == "edit":
            return _PATCH_PATH_RE.findall(arguments)[:MAX_PATCH_PATHS]
        return []
    if not isinstance(arguments, dict):
        return []
    paths = []
    for key in ("file_path", "path", "notebook_path", "target_file", "filePath", "file"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    for key in ("input", "patch"):
        value = arguments.get(key)
        if isinstance(value, str) and "*** " in value:
            paths.extend(_PATCH_PATH_RE.findall(value)[:MAX_PATCH_PATHS])
    edits = arguments.get("edits")
    if isinstance(edits, list):
        for edit in edits[:MAX_PATCH_PATHS]:
            if isinstance(edit, dict) and isinstance(edit.get("file_path") or edit.get("path"), str):
                paths.append(edit.get("file_path") or edit.get("path"))
    return paths


def file_kind(path):
    """Coarse category of a file from its path: frontend, backend, data, infra, tooling, docs, tests, config, media."""
    text = str(path or "").replace("\\", "/").strip().lower()
    if not text:
        return ""
    base = text.rsplit("/", 1)[-1]
    ext = os.path.splitext(base)[1]
    if base in _AGENT_INSTRUCTION_FILES:
        return "tooling"
    if _EXT_KIND.get(ext) == "docs":
        return "docs"
    if _TEST_PATH_RE.search(text):
        return "tests"
    if _INFRA_PATH_RE.search(text):
        return "infra"
    if _TOOLING_PATH_RE.search(text):
        return "tooling"
    if base == "dockerfile":
        return "infra"
    kind = _EXT_KIND.get(ext, "")
    if ext in (".js", ".ts", ".mjs", ".cjs"):
        kind = "frontend" if _FRONTEND_DIR_RE.search(text) else "backend"
    if kind in ("backend", "config", "") and _DATA_PATH_RE.search(text):
        kind = "data"
    return kind


def tool_action(name, arguments, ts=0.0):
    """One tool call as (ts, kind, paths, command kinds); paths stay in memory for the prompt only."""
    arguments = _arguments(arguments)
    kind = tool_kind(name)
    paths = _paths(name, arguments) if kind in ("edit", "read") else []
    commands = command_kinds(_command_text(arguments)) if kind == "shell" else []
    if kind == "edit" and not paths and isinstance(arguments, dict):
        paths = _PATCH_PATH_RE.findall(str(arguments.get("input") or ""))[:MAX_PATCH_PATHS]
    return {"ts": float(ts or 0), "kind": kind, "paths": paths, "commands": commands}


def _owner(ts, timeline):
    """Index of the request that owns an event at ``ts``: the latest request started at or before it.

    ``timeline`` is (start, index) sorted by start; events before every request belong to the earliest.
    """
    position = bisect.bisect_right(timeline, (ts, float("inf"))) - 1
    return timeline[max(0, position)][1]


def request_slices(turn_ts, events, actions=()):
    """Split events (ts, cost, model, effort) and actions over requests that start at ``turn_ts``.

    Returns one content-free dict per request: cost, dominant model and effort, and action counts; plus,
    separately, the per-request edited paths for the classifier prompt. Resumed or compacted traces can
    list requests out of order, so ownership goes by time, not position; a request without a timestamp
    owns nothing. Returns ``(None, None)`` when no request has a timestamp, so callers fall back to
    spreading each day's cost.
    """
    starts = [float(ts or 0) for ts in turn_ts]
    timeline = sorted((start, index) for index, start in enumerate(starts) if start > 0)
    if not timeline:
        return None, None
    cost = [0.0] * len(starts)
    model_cost = [collections.Counter() for _ in starts]
    effort_cost = [collections.Counter() for _ in starts]
    for ts, value, model, effort in events:
        index = _owner(float(ts or 0), timeline)
        amount = float(value or 0)
        cost[index] += amount
        if model:
            model_cost[index][str(model)] += amount or 1e-9
        if effort:
            effort_cost[index][str(effort).lower()] += amount or 1e-9
    counts = [collections.Counter() for _ in starts]
    edited = [collections.Counter() for _ in starts]
    for action in actions:
        index = _owner(action["ts"], timeline)
        counts[index][action["kind"]] += 1
        for command in action["commands"]:
            counts[index]["cmd_" + command] += 1
        if action["kind"] == "edit":
            for path in action["paths"]:
                edited[index][path] += 1
    slices = []
    for index in range(len(starts)):
        files = collections.Counter(file_kind(path) for path in edited[index])
        files.pop("", None)
        slices.append({
            "cost": round(cost[index], 6),
            "model": model_cost[index].most_common(1)[0][0] if model_cost[index] else "",
            "effort": effort_cost[index].most_common(1)[0][0] if effort_cost[index] else "",
            "actions": dict(counts[index]),
            "files": dict(files),
        })
    return slices, edited


def evidence_text(slice_, edited_paths=None):
    """One line on what the agent changed for a request, for the local classifier only.

    Only the files changed: in a local evaluation the agent's routine steps (git status, linters, test
    runs, URL checks) appeared on nearly every request and pulled labels toward tooling and DevOps, while
    the changed files told areas apart (area accuracy 79% to 85%).
    """
    if slice_ is None:
        return ""
    actions = slice_.get("actions") or {}
    edited = edited_paths or collections.Counter()
    if edited:
        names = [os.path.basename(str(path).rstrip("/")) or str(path) for path, _n in edited.most_common(MAX_NAMED_FILES)]
        names = list(dict.fromkeys(name[:60] for name in names))
        more = len(edited) - len(names)
        text = f"Files the agent changed: {', '.join(names)}{f' and {more} more' if more > 0 else ''}."
    elif actions.get("edit"):
        text = "The agent changed files."
    elif actions.get("cmd_git"):
        text = "The agent changed no files; it committed, pushed, or opened pull requests."
    else:
        text = "The agent changed no files."
    return text[:MAX_EVIDENCE_CHARS]


_CODE_CALL_RE = re.compile(r"tools\.([A-Za-z_][\w]*)\s*\(")
_CODE_STRING_RE = re.compile(r"\"((?:[^\"\\\n]|\\.){1,2000})\"|'((?:[^'\\\n]|\\.){1,2000})'|`((?:[^`\\]|\\.){1,2000})`")
_CODE_TOOL_NAMES = {"exec_command": "shell", "write_stdin": "", "apply_patch": "edit", "web__run": "web",
                    "view_image": "read", "spawn_agent": "agent", "update_plan": "plan"}


def code_actions(code, ts=0.0):
    """Tool calls inside a code-mode script (``tools.exec_command({cmd: ...})`` and friends)."""
    text = str(code or "")[:50_000]
    actions = []
    for match in _CODE_CALL_RE.finditer(text):
        name = match.group(1)
        kind = _CODE_TOOL_NAMES.get(name, "mcp" if name.startswith("mcp__") else tool_kind(name))
        if kind:
            actions.append({"ts": float(ts or 0), "kind": kind, "paths": [], "commands": []})
    if not actions:
        return []
    shell = next((a for a in actions if a["kind"] == "shell"), None)
    if shell is not None:
        for match in _CODE_STRING_RE.finditer(text):
            literal = next(group for group in match.groups() if group is not None)
            shell["commands"].extend(command_kinds(literal.replace("\\n", "\n")))
    edit = next((a for a in actions if a["kind"] == "edit"), None)
    if edit is not None:
        edit["paths"] = _PATCH_PATH_RE.findall(text.replace("\\n", "\n"))[:MAX_PATCH_PATHS]
    return actions
