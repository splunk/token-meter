"""Bounded Codex CLI adapter for the Token Meter Coach."""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .contracts import GOAL_METRICS, GOAL_RUNTIMES, RECOMMENDATIONS, normalize_goal


MAX_MESSAGE_CHARS = 2_000
MAX_ANSWER_CHARS = 1_200
MAX_HISTORY_TURNS = 6
MAX_EVENTS_BYTES = 262_144
MAX_RESULT_BYTES = 32_768
COACH_TIMEOUT_SECONDS = 240
ROUTES = {
    "sessions", "sessions-all", "spend", "models", "efficiency", "git",
    "learn", "capabilities", "settings", "settings-budgets", "settings-updates",
}
MCP_TOOLS = [
    "check", "usage", "sessions", "stats", "schema", "goal", "capabilities",
]
# Each action names a control the user can actually operate in Token Meter. The
# browser generates every label and route from these codes, so agent text never
# becomes a control.
ACTION_KINDS = [
    "review_skill_packs",
    "review_flagged_tool",
    "narrow_tool_output",
    "compare_models",
    "reduce_context",
    "reduce_reasoning",
    "reduce_retries",
    "set_monthly_budget",
    "review_costly_sessions",
    "inspect_current_run",
]
DISABLED_CODEX_FEATURES = [
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "goals",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "plugins",
    "recommended_plugins",
    "remote_plugin",
    "shell_tool",
    "skill_mcp_dependency_install",
    "tool_suggest",
    "unified_exec",
    "view_image",
    "workspace_dependencies",
]


def _evidence_schema():
    return {
        "type": "array", "maxItems": 3,
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "maxLength": 120},
                "value": {"type": "string", "maxLength": 80},
                "source": {"type": "string", "enum": ["Token Meter MCP"]},
            },
            "required": ["label", "value", "source"],
            "additionalProperties": False,
        },
    }


CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1, "maxLength": MAX_ANSWER_CHARS},
        "evidence": _evidence_schema(),
        "action": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(ACTION_KINDS)},
                        "subject": {
                            "anyOf": [
                                {"type": "null"},
                                {"type": "string", "maxLength": 60},
                            ],
                        },
                    },
                    "required": ["kind", "subject"],
                    "additionalProperties": False,
                },
            ],
        },
        "goal_draft": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string", "enum": sorted(GOAL_METRICS)},
                        "target_percent": {"type": "integer", "minimum": 5, "maximum": 80},
                        "window_days": {"type": "integer", "enum": [7, 14, 30]},
                        "runtime": {"type": "string", "enum": sorted(GOAL_RUNTIMES)},
                        "review_weekday": {"type": "integer", "minimum": 0, "maximum": 6},
                        "weekly_enabled": {"type": "boolean"},
                    },
                    "required": [
                        "metric", "target_percent", "window_days", "runtime",
                        "review_weekday", "weekly_enabled",
                    ],
                    "additionalProperties": False,
                },
            ],
        },
    },
    "required": [
        "message", "evidence", "action", "goal_draft",
    ],
    "additionalProperties": False,
}

WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": sorted(RECOMMENDATIONS)},
        "evidence": _evidence_schema(),
    },
    "required": ["recommendation", "evidence"],
    "additionalProperties": False,
}


class CoachRunError(RuntimeError):
    def __init__(self, code):
        super().__init__(str(code))
        self.code = str(code)


def _bounded_text(value, maximum, required=False, error_code="invalid_request"):
    if not isinstance(value, str):
        raise CoachRunError(error_code)
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise CoachRunError(error_code)
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise CoachRunError(error_code)
    return value


def _normalize_page(value):
    value = value if isinstance(value, dict) else {}
    route = str(value.get("route") or "sessions").strip().lower()
    if route not in ROUTES:
        raise CoachRunError("invalid_request")
    result = {"route": route}
    session_id = str(value.get("session_id") or "").strip()
    if session_id:
        if len(session_id) > 240 or any(
            not (char.isalnum() or char in "-_:.") for char in session_id
        ):
            raise CoachRunError("invalid_request")
        result["session_id"] = session_id
    return result


def _normalize_request(value):
    if not isinstance(value, dict) or set(value) - {"mode", "message", "history", "page", "goal"}:
        raise CoachRunError("invalid_request")
    mode = str(value.get("mode") or "chat").strip().lower()
    if mode not in {"chat", "weekly"}:
        raise CoachRunError("invalid_request")
    history = value.get("history") or []
    if not isinstance(history, list) or len(history) > MAX_HISTORY_TURNS:
        raise CoachRunError("invalid_request")
    normalized_history = []
    for row in history:
        if (
            not isinstance(row, dict) or set(row) != {"role", "content"}
            or row.get("role") not in {"user", "assistant"}
        ):
            raise CoachRunError("invalid_request")
        normalized_history.append({
            "role": row["role"],
            "content": _bounded_text(row.get("content"), MAX_MESSAGE_CHARS, True),
        })
    goal = value.get("goal")
    if goal is not None:
        try:
            goal = normalize_goal(goal)
        except ValueError as error:
            raise CoachRunError("invalid_request") from error
    if mode == "weekly" and goal is None:
        raise CoachRunError("invalid_request")
    return {
        "mode": mode,
        "message": _bounded_text(value.get("message"), MAX_MESSAGE_CHARS, True),
        "history": normalized_history,
        "page": _normalize_page(value.get("page")),
        "goal": goal,
    }


def _output_text(value, maximum, required=False):
    return _bounded_text(value, maximum, required, "invalid_output")


def _evidence(value):
    if not isinstance(value, list) or len(value) > 3:
        raise CoachRunError("invalid_output")
    rows = []
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != {"label", "value", "source"}
            or row.get("source") != "Token Meter MCP"
        ):
            raise CoachRunError("invalid_output")
        rows.append({
            "label": _output_text(row.get("label"), 120, True),
            "value": _output_text(row.get("value"), 80, True),
            "source": "Token Meter MCP",
        })
    return rows


def _sanitize_chat(value):
    if not isinstance(value, dict) or set(value) != {
        "message", "evidence", "action", "goal_draft",
    }:
        raise CoachRunError("invalid_output")
    action = value.get("action")
    if action is not None:
        if (
            not isinstance(action, dict)
            or set(action) != {"kind", "subject"}
            or action.get("kind") not in ACTION_KINDS
        ):
            raise CoachRunError("invalid_output")
        subject = action.get("subject")
        action = {
            "kind": action["kind"],
            "subject": (
                _output_text(subject, 60, False) or None
                if subject is not None else None
            ),
        }
    draft = value.get("goal_draft")
    if draft is not None:
        if not isinstance(draft, dict) or set(draft) != {
            "metric", "target_percent", "window_days", "runtime",
            "review_weekday", "weekly_enabled",
        }:
            raise CoachRunError("invalid_output")
        try:
            draft = normalize_goal(draft)
        except ValueError as error:
            raise CoachRunError("invalid_output") from error
        action = None
    return {
        "message": _output_text(value.get("message"), MAX_ANSWER_CHARS, True),
        "evidence": _evidence(value.get("evidence")),
        "action": action,
        "goal_draft": draft,
    }


def _sanitize_weekly(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"recommendation", "evidence"}
        or value.get("recommendation") not in RECOMMENDATIONS
    ):
        raise CoachRunError("invalid_output")
    return {
        "recommendation": value["recommendation"],
        "evidence": _evidence(value.get("evidence")),
    }


def _mcp_observed(events):
    if isinstance(events, bool):
        return events
    for line in str(events or "").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = row.get("item") if isinstance(row, dict) else None
        if (
            row.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "mcp_tool_call"
            and item.get("server") == "tokenmeter"
            and item.get("tool") in MCP_TOOLS
        ):
            return True
    return False


def _event_is_token_meter_mcp(item):
    return (
        isinstance(item, dict)
        and item.get("type") == "mcp_tool_call"
        and item.get("server") == "tokenmeter"
        and item.get("tool") in MCP_TOOLS
    )


def _terminate_child(process):
    """Stop one known child without searching for a process by name or PID."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
            process.wait()


def _default_runner(command, prompt, *, cwd, env, timeout, result_path,
                    on_process=None, on_event=None, cancel_event=None):
    on_process = on_process or (lambda _process: None)
    on_event = on_event or (lambda _event: None)
    cancel_event = cancel_event or threading.Event()
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            cwd=cwd, env=env,
        )
        on_process(process)

        chunks = queue.Queue(maxsize=16)
        reader_done = threading.Event()
        reader_stop = threading.Event()

        def write_stdin():
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def read_stdout():
            try:
                while True:
                    chunk = process.stdout.read1(4_096)
                    if not chunk:
                        return
                    while not reader_stop.is_set():
                        try:
                            chunks.put(chunk, timeout=0.05)
                            break
                        except queue.Full:
                            continue
            finally:
                try:
                    process.stdout.close()
                except OSError:
                    pass
                reader_done.set()

        writer = threading.Thread(target=write_stdin, daemon=True)
        reader = threading.Thread(target=read_stdout, daemon=True)
        writer.start()
        reader.start()
        deadline = time.monotonic() + timeout
        post_exit_deadline = None
        event_bytes = 0
        pending = b""
        mcp_observed = False
        failure = None
        try:
            while True:
                running = process.poll() is None
                if not running and post_exit_deadline is None:
                    post_exit_deadline = time.monotonic() + 0.2
                if not running and (
                    reader_done.is_set() or time.monotonic() >= post_exit_deadline
                ):
                    break
                if cancel_event.is_set() and running:
                    _terminate_child(process)
                elif time.monotonic() >= deadline and running:
                    _terminate_child(process)
                    failure = subprocess.TimeoutExpired(command, timeout)
                try:
                    chunk = chunks.get(timeout=0.05)
                except queue.Empty:
                    continue
                event_bytes += len(chunk)
                if event_bytes > MAX_EVENTS_BYTES:
                    _terminate_child(process)
                    failure = CoachRunError("output_too_large")
                    continue
                pending += chunk
                while b"\n" in pending:
                    if post_exit_deadline is None and process.poll() is not None:
                        post_exit_deadline = time.monotonic() + 0.2
                    if (
                        post_exit_deadline is not None
                        and time.monotonic() >= post_exit_deadline
                    ):
                        pending = b""
                        break
                    line, pending = pending.split(b"\n", 1)
                    try:
                        row = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    item = row.get("item")
                    if row.get("type") == "item.completed" and _event_is_token_meter_mcp(item):
                        mcp_observed = True
                    on_event(row)
        finally:
            reader_stop.set()
            # A descendant can retain stdout after the direct child has exited.
            # Do not kill that descendant or wait indefinitely for its inherited
            # descriptor; only the direct child is owned by this runner.
            reader.join(0.2)
        if failure:
            raise failure
        stderr.seek(0, os.SEEK_END)
        error_size = stderr.tell()
        stderr.seek(0)
        error_text = stderr.read(min(error_size, 8_192)).decode("utf-8", "replace")
    try:
        result_size = os.path.getsize(result_path)
    except OSError:
        result_size = 0
    if result_size > MAX_RESULT_BYTES:
        raise CoachRunError("output_too_large")
    try:
        result = Path(result_path).read_text(encoding="utf-8") if result_size else ""
    except OSError:
        result = ""
    return {
        "returncode": process.returncode,
        "events": mcp_observed,
        "stderr": error_text,
        "result": result,
    }


def _isolated_environment(environment, temporary):
    """Keep saved Codex auth while excluding the user's skills and config."""
    environment = dict(environment or {})
    source_home = environment.get("CODEX_HOME")
    if not source_home:
        source_home = os.path.join(
            environment.get("HOME") or os.path.expanduser("~"), ".codex",
        )
    isolated_home = os.path.join(temporary, "codex-home")
    os.mkdir(isolated_home, 0o700)
    credential_paths = []
    for name in ("auth.json", ".credentials.json"):
        source = os.path.join(source_home, name)
        if os.path.isfile(source):
            credential_paths.append((source, os.path.join(isolated_home, name)))
    def link_credentials(linker):
        try:
            for source, destination in credential_paths:
                linker(source, destination)
            return True
        except OSError:
            for _source, destination in credential_paths:
                try:
                    os.unlink(destination)
                except OSError:
                    pass
            return False

    if not link_credentials(os.symlink):
        # Windows can forbid unprivileged symlinks. A same-volume hard link
        # retains authentication without copying credential content. If it is
        # unavailable too, fail closed with an empty ephemeral Codex home.
        link_credentials(os.link)
    environment["CODEX_HOME"] = isolated_home
    return environment


class CodexCoach:
    """Run one isolated Coach turn through a user-authenticated Codex CLI."""

    def __init__(self, *, codex_path, mcp_command, workspace_source,
                 mcp_args=(), runner=None, path_is_executable=None,
                 environment=None, child_environment=None):
        self._codex_path = codex_path if callable(codex_path) else lambda: codex_path
        self._mcp_command = str(mcp_command)
        self._mcp_args = tuple(str(value) for value in (mcp_args or ()))
        self._workspace_source = str(workspace_source)
        self._runner = runner or _default_runner
        self._path_is_executable = path_is_executable or (
            lambda path: bool(path and os.path.isfile(path) and os.access(path, os.X_OK))
        )
        self._environment = environment or (lambda _path: os.environ.copy())
        self._child_environment = child_environment or (lambda: {})
        self._lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._activity = None
        self._process = None
        self._cancel_event = None
        self._next_run_id = 0

    def status(self):
        path = self._codex_path()
        available = bool(path and self._path_is_executable(path))
        with self._activity_lock:
            activity = self._activity
            if activity:
                projected = {
                    "stage": activity["stage"],
                    "started_at": activity["started_at"],
                    "cancellable": activity["cancellable"],
                    "tool": activity["tool"],
                    "reads": activity["reads"],
                }
            else:
                projected = None
        result = {"available": available, "status": "running" if projected else (
            "ready" if available else "cli_missing"
        )}
        if projected:
            result["activity"] = projected
        return result

    def cancel(self):
        with self._activity_lock:
            activity = self._activity
            process = self._process
            cancel_event = self._cancel_event
            if not activity or not activity["cancellable"]:
                return {"ok": True, "changed": False}
            if process is not None:
                try:
                    if process.poll() is not None:
                        return {"ok": True, "changed": False}
                except AttributeError:
                    # Injected test doubles without poll() represent a live
                    # registered child until their runner completes.
                    pass
            activity["cancellable"] = False
            cancel_event.set()
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        return {"ok": True, "changed": True}

    def _register_process(self, run_id, process):
        terminate = False
        with self._activity_lock:
            if self._activity and self._activity["run_id"] == run_id:
                self._process = process
                terminate = self._cancel_event.is_set()
        if terminate:
            try:
                process.terminate()
            except OSError:
                pass

    def _record_event(self, run_id, row):
        item = row.get("item") if isinstance(row, dict) else None
        if not _event_is_token_meter_mcp(item):
            return
        stage = None
        if row.get("type") == "item.started":
            stage = "reading_token_meter"
        elif row.get("type") == "item.completed":
            stage = "checking_evidence"
        if not stage:
            return
        tool = item.get("tool") if item.get("tool") in MCP_TOOLS else None
        with self._activity_lock:
            if self._activity and self._activity["run_id"] == run_id:
                self._activity["stage"] = stage
                self._activity["tool"] = tool
                if stage == "checking_evidence":
                    self._activity["reads"] += 1

    def _command(self, codex_path, workspace, schema_path, result_path):
        overrides = [
            "approval_policy=\"never\"",
            "web_search=\"disabled\"",
            "tools.view_image=false",
            "model_reasoning_effort=\"low\"",
            "model_verbosity=\"low\"",
            "mcp_servers.tokenmeter.command={}".format(json.dumps(self._mcp_command)),
            "mcp_servers.tokenmeter.args={}".format(json.dumps(list(self._mcp_args))),
            "mcp_servers.tokenmeter.env.TOKEN_METER_CALLER=\"token-meter-coach\"",
            "mcp_servers.tokenmeter.required=true",
            "mcp_servers.tokenmeter.enabled_tools={}".format(json.dumps(MCP_TOOLS)),
            "mcp_servers.tokenmeter.default_tools_approval_mode=\"approve\"",
            "mcp_servers.tokenmeter.startup_timeout_sec=15",
            "mcp_servers.tokenmeter.tool_timeout_sec=45",
        ]
        overrides.extend(
            "features.{}=false".format(feature)
            for feature in DISABLED_CODEX_FEATURES
        )
        command = [
            codex_path, "exec", "--model", "gpt-5.6-luna", "--ephemeral", "--sandbox", "read-only",
            "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--json", "--output-schema", schema_path,
            "--output-last-message", result_path, "-C", workspace,
        ]
        for override in overrides:
            command.extend(("-c", override))
        command.append("-")
        return command

    def run(self, request):
        request = _normalize_request(request)
        codex_path = self._codex_path()
        if not codex_path or not self._path_is_executable(codex_path):
            raise CoachRunError("cli_missing")
        if not self._lock.acquire(blocking=False):
            raise CoachRunError("busy")
        with self._activity_lock:
            self._next_run_id += 1
            run_id = self._next_run_id
            cancel_event = threading.Event()
            self._activity = {
                "run_id": run_id,
                "stage": "opening_codex",
                "started_at": time.time(),
                "cancellable": True,
                "tool": None,
                "reads": 0,
            }
            self._process = None
            self._cancel_event = cancel_event
        try:
            with tempfile.TemporaryDirectory(prefix="token-meter-coach-") as temporary:
                workspace = os.path.join(temporary, "workspace")
                shutil.copytree(self._workspace_source, workspace)
                schema_path = os.path.join(temporary, "schema.json")
                result_path = os.path.join(temporary, "result.json")
                schema = WEEKLY_SCHEMA if request["mode"] == "weekly" else CHAT_SCHEMA
                Path(schema_path).write_text(
                    json.dumps(schema, separators=(",", ":")), encoding="utf-8",
                )
                prompt = (
                    "Use $token-meter-coach. Follow the skill and return only the supplied schema. "
                    "The request JSON follows:\n" +
                    json.dumps(request, ensure_ascii=False, separators=(",", ":"))
                )
                try:
                    environment = _isolated_environment(
                        self._environment(codex_path), temporary,
                    )
                    bridge = self._child_environment()
                    if isinstance(bridge, dict):
                        environment.update({
                            key: value for key, value in bridge.items()
                            if key in {
                                "TOKEN_METER_COACH_EVIDENCE_URL",
                                "TOKEN_METER_COACH_ACTION_TOKEN",
                                "TOKEN_METER_COACH_EVIDENCE_TOKEN",
                            } and isinstance(value, str)
                        })
                    if cancel_event.is_set():
                        raise CoachRunError("cancelled")
                    completed = self._runner(
                        self._command(codex_path, workspace, schema_path, result_path),
                        prompt, cwd=workspace, env=environment,
                        timeout=COACH_TIMEOUT_SECONDS, result_path=result_path,
                        on_process=lambda process: self._register_process(run_id, process),
                        on_event=lambda row: self._record_event(run_id, row),
                        cancel_event=cancel_event,
                    )
                except subprocess.TimeoutExpired as error:
                    if cancel_event.is_set():
                        raise CoachRunError("cancelled") from error
                    raise CoachRunError("timeout") from error
                except CoachRunError as error:
                    if cancel_event.is_set():
                        raise CoachRunError("cancelled") from error
                    raise
                except OSError as error:
                    if cancel_event.is_set():
                        raise CoachRunError("cancelled") from error
                    raise CoachRunError("agent_unavailable") from error
                if not isinstance(completed, dict):
                    raise CoachRunError("agent_failed")
                if cancel_event.is_set():
                    raise CoachRunError("cancelled")
                if completed.get("returncode") != 0:
                    error_text = str(completed.get("stderr") or "").lower()
                    code = "auth_required" if any(
                        marker in error_text
                        for marker in ("login", "auth", "credential", "sign in")
                    ) else ("mcp_unavailable" if "mcp" in error_text else "agent_failed")
                    raise CoachRunError(code)
                result_text = completed.get("result")
                if not isinstance(result_text, str) or len(result_text.encode("utf-8")) > MAX_RESULT_BYTES:
                    raise CoachRunError("output_too_large")
                try:
                    decoded = json.loads(result_text)
                except (TypeError, json.JSONDecodeError) as error:
                    raise CoachRunError("invalid_output") from error
                sanitized = _sanitize_weekly(decoded) if request["mode"] == "weekly" else _sanitize_chat(decoded)
                if (
                    request["mode"] == "weekly" or bool(sanitized.get("evidence"))
                ) and not _mcp_observed(completed.get("events")):
                    raise CoachRunError("mcp_evidence_required")
                return sanitized
        finally:
            with self._activity_lock:
                if self._activity and self._activity["run_id"] == run_id:
                    self._activity = None
                    self._process = None
                    self._cancel_event = None
            self._lock.release()
