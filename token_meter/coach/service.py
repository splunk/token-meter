"""Application service for structured goals and evidence-bounded progress."""

import datetime
import threading

from .codex import MCP_TOOLS
from .contracts import (
    GOAL_METRICS,
    RECOMMENDATIONS,
    goal_progress,
    metric_snapshot,
    normalize_goal,
)


_EXECUTION_METRICS = {
    "cost_per_execution": ("cost_usd", "execution_count"),
    "output_per_dollar": ("output_tokens", "cost_usd"),
    "wait_per_execution": ("wait_seconds", "execution_count"),
    "retry_rate": ("retries", "attempts"),
    "tool_tokens_per_execution": ("execution_count",),
    "context_peak": ("context_peak",),
}
_SNAPSHOT_KEYS = {
    "metric", "value", "unit", "coverage", "covered", "unavailable", "as_of",
}
_GOAL_CORE_KEYS = {
    "metric", "direction", "target_percent", "window_days", "runtime",
    "review_weekday", "weekly_enabled", "label", "unit",
}
_WEEKLY_ERROR_CODES = {
    "agent_failed", "agent_unavailable", "auth_required", "busy",
    "cancelled", "cli_missing", "invalid_output", "mcp_evidence_required",
    "mcp_unavailable", "output_too_large", "timeout",
}
EVIDENCE_REFRESH_SECONDS = 15 * 60
_ACTIVITY_TOOLS = frozenset(MCP_TOOLS)
_MAX_ACTIVITY_READS = 99


def _activity_evidence_progress(activity):
    """Project only an allowlisted tool name and a bounded completed-read count."""
    tool = activity.get("tool") if isinstance(activity, dict) else None
    reads = activity.get("reads") if isinstance(activity, dict) else None
    if isinstance(reads, bool) or not isinstance(reads, int) or reads < 0:
        reads = 0
    return {
        "tool": tool if isinstance(tool, str) and tool in _ACTIVITY_TOOLS else None,
        "reads": min(reads, _MAX_ACTIVITY_READS),
    }


def _finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _goal_identity(goal):
    if not isinstance(goal, dict):
        return None
    return tuple(goal.get(key) for key in sorted(_GOAL_CORE_KEYS)) + (
        goal.get("created_at"),
    )


def _measurement_identity(goal):
    """Identify a goal revision without treating its weekly toggle as evidence."""
    if not isinstance(goal, dict):
        return None
    return tuple(
        goal.get(key)
        for key in sorted(_GOAL_CORE_KEYS - {"weekly_enabled"})
    ) + (goal.get("created_at"),)


def _sanitize_snapshot(value, expected_metric=None):
    if not isinstance(value, dict):
        return None
    metric = str(value.get("metric") or "")
    if metric not in GOAL_METRICS or value.get("unit") != GOAL_METRICS[metric]["unit"]:
        return None
    if expected_metric and metric != expected_metric:
        return None
    coverage = str(value.get("coverage") or "")
    if coverage not in {"complete", "partial", "unavailable"}:
        return None
    number = _finite_number(value.get("value"))
    if coverage != "unavailable" and number is None:
        return None
    covered = value.get("covered")
    unavailable = value.get("unavailable")
    if (
        isinstance(covered, bool) or not isinstance(covered, int) or covered < 0
        or isinstance(unavailable, bool) or not isinstance(unavailable, int)
        or unavailable < 0
    ):
        return None
    as_of = _finite_number(value.get("as_of"))
    result = {
        key: value.get(key) for key in _SNAPSHOT_KEYS
    }
    result["value"] = number
    result["as_of"] = as_of
    return result


def sanitize_store(value):
    """Return only the persisted Coach schema; malformed state becomes empty."""
    empty = {
        "schema_version": 1, "goal": None, "current": None,
        "refreshed_at": None, "weekly": {},
    }
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return empty
    raw_goal = value.get("goal")
    if raw_goal is None:
        goal = None
    elif isinstance(raw_goal, dict):
        try:
            goal = normalize_goal(raw_goal)
        except ValueError:
            return empty
        created_at = _finite_number(raw_goal.get("created_at"))
        baseline = _sanitize_snapshot(
            raw_goal.get("baseline"), expected_metric=goal["metric"],
        )
        if created_at is None or baseline is None:
            return empty
        goal.update({"created_at": created_at, "baseline": baseline})
    else:
        return empty
    current = _sanitize_snapshot(
        value.get("current"), expected_metric=goal.get("metric") if goal else None,
    )
    refreshed_at = _finite_number(value.get("refreshed_at"))
    if not goal or current is None or refreshed_at is None:
        current = None
        refreshed_at = None
    raw_weekly = value.get("weekly")
    raw_weekly = raw_weekly if isinstance(raw_weekly, dict) else {}
    weekly = {}
    week_key = str(raw_weekly.get("week_key") or "")
    recommendation = str(raw_weekly.get("recommendation") or "")
    completed_at = _finite_number(raw_weekly.get("completed_at"))
    snapshot = _sanitize_snapshot(
        raw_weekly.get("snapshot"),
        expected_metric=goal.get("metric") if goal else None,
    )
    if (
        goal and len(week_key) == 8 and week_key[:4].isdigit()
        and week_key[4:6] == "-W" and week_key[6:].isdigit()
        and recommendation in RECOMMENDATIONS
        and completed_at is not None and snapshot is not None
    ):
        weekly.update({
            "week_key": week_key,
            "completed_at": completed_at,
            "recommendation": recommendation,
            "snapshot": snapshot,
        })
    attempted_at = _finite_number(raw_weekly.get("last_attempt_at"))
    last_error = str(raw_weekly.get("last_error") or "")
    if attempted_at is not None:
        weekly["last_attempt_at"] = attempted_at
    if last_error in _WEEKLY_ERROR_CODES:
        weekly["last_error"] = last_error
    return {
        "schema_version": 1, "goal": goal, "current": current,
        "refreshed_at": refreshed_at, "weekly": weekly,
    }


class CoachService:
    """Own one active goal while delegating storage and evidence retrieval."""

    def __init__(self, *, read_store, write_store, stats, executor=None, now=None,
                 local_now=None):
        self._read_store = read_store
        self._write_store = write_store
        self._stats = stats
        self._executor = executor
        self._now = now or (lambda: datetime.datetime.now().timestamp())
        self._local_now = local_now or (lambda: datetime.datetime.now().astimezone())
        self._store_lock = threading.RLock()
        self._weekly_lock = threading.Lock()
        self._evidence_lock = threading.Lock()
        self._evidence_attempt_identity = None
        self._evidence_attempt_at = None

    def _store(self):
        return sanitize_store(self._read_store())

    def _window(self, goal):
        end = datetime.datetime.fromtimestamp(
            float(self._now()), tz=datetime.timezone.utc,
        )
        start = end - datetime.timedelta(days=goal["window_days"])
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )

    def _snapshot(self, goal):
        start, end = self._window(goal)
        arguments = {
            "metrics": _EXECUTION_METRICS[goal["metric"]],
            "start": start,
            "end": end,
        }
        if goal["runtime"] != "all":
            arguments["runtime"] = goal["runtime"]
        else:
            arguments["runtime"] = None
        execution = self._stats(**arguments)
        tool = None
        if goal["metric"] == "tool_tokens_per_execution":
            tool_arguments = {
                "metrics": ("tool_result_tokens",),
                "start": start,
                "end": end,
                "runtime": arguments["runtime"],
            }
            tool = self._stats(**tool_arguments)
        return metric_snapshot(goal["metric"], execution, tool)

    def save_goal(self, value):
        normalized = normalize_goal(value)
        with self._store_lock:
            store = self._store()
            existing = store.get("goal")
            if existing and all(
                existing.get(key) == normalized.get(key)
                for key in _GOAL_CORE_KEYS
            ):
                return {"ok": True, "changed": False, "goal": existing}
            created_at = float(self._now())
            existing_created_at = _finite_number(
                existing.get("created_at") if existing else None,
            )
            if existing_created_at is not None and created_at <= existing_created_at:
                # `created_at` is also the persisted revision discriminator.
                # Keep it monotonic when the injected/system clock has not advanced.
                created_at = existing_created_at + 0.000001
            goal = {
                **normalized,
                "created_at": created_at,
                "baseline": {
                    "metric": normalized["metric"], "value": None,
                    "unit": normalized["unit"], "coverage": "unavailable",
                    "covered": 0, "unavailable": 0, "as_of": None,
                },
            }
            next_store = {
                "schema_version": 1, "goal": goal, "current": None,
                "refreshed_at": None, "weekly": {},
            }
            write = self._write_store(next_store)
            if not isinstance(write, dict) or not write.get("ok"):
                return write if isinstance(write, dict) else {
                    "ok": False, "error": "Token Meter could not save the goal.",
                }
        return {
            "ok": True,
            "changed": bool(write.get("changed", True)),
            "goal": goal,
        }

    def set_weekly_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("weekly_enabled must be a boolean")
        with self._store_lock:
            store = self._store()
            goal = store.get("goal")
            if not goal:
                raise ValueError("an active goal is required")
            if goal["weekly_enabled"] == enabled:
                return {"ok": True, "changed": False, "goal": goal}
            goal = {**goal, "weekly_enabled": enabled}
            next_store = {**store, "goal": goal}
            write = self._write_store(next_store)
            if not isinstance(write, dict) or not write.get("ok"):
                return write if isinstance(write, dict) else {
                    "ok": False, "error": "Token Meter could not save the goal.",
                }
        return {"ok": True, "changed": bool(write.get("changed", True)), "goal": goal}

    def clear_goal(self):
        with self._store_lock:
            store = self._store()
            next_store = {
                "schema_version": 1, "goal": None, "current": None,
                "refreshed_at": None, "weekly": {},
            }
            if store == next_store:
                return {"ok": True, "changed": False, "goal": None}
            write = self._write_store(next_store)
            if not isinstance(write, dict) or not write.get("ok"):
                return write if isinstance(write, dict) else {
                    "ok": False, "error": "Token Meter could not clear the goal.",
                }
        return {"ok": True, "changed": bool(write.get("changed", True)), "goal": None}

    def state(self):
        store = self._store()
        goal = store.get("goal")
        current = store.get("current") if goal else None
        progress = goal_progress(goal, goal.get("baseline"), current) if goal else None
        agent = self._executor.status() if self._executor is not None else {
            "available": False, "status": "unavailable",
        }
        agent = agent if isinstance(agent, dict) else {}
        activity = agent.get("activity")
        if isinstance(activity, dict):
            activity = {
                key: activity.get(key)
                for key in ("stage", "started_at", "cancellable")
            } | _activity_evidence_progress(agent.get("activity"))
            agent = {
                "available": bool(agent.get("available")),
                "status": str(agent.get("status") or "unavailable"),
                "activity": activity,
            }
        else:
            agent = {
                "available": bool(agent.get("available")),
                "status": str(agent.get("status") or "unavailable"),
            }
        return {
            "ok": True,
            "agent": agent,
            "goal": goal,
            "current": current,
            "progress": progress,
            "weekly": store.get("weekly") or {},
            "next_review_at": self._next_review_at(goal, store.get("weekly") or {}),
        }

    def cancel(self):
        cancel = getattr(self._executor, "cancel", None)
        if not callable(cancel):
            return {"ok": True, "changed": False}
        try:
            result = cancel()
        except Exception:
            return {"ok": True, "changed": False}
        if not isinstance(result, dict) or result.get("ok") is not True:
            return {"ok": True, "changed": False}
        return {"ok": True, "changed": bool(result.get("changed"))}

    def refresh_evidence(self, force=False):
        """Refresh numeric progress off the request path and hydrate a new baseline."""
        store = self._store()
        goal = store.get("goal")
        if not goal:
            return None
        current = store.get("current")
        refreshed_at = _finite_number(store.get("refreshed_at"))
        now = float(self._now())
        if (
            not force and current is not None and refreshed_at is not None
            and max(0.0, now - refreshed_at) < EVIDENCE_REFRESH_SECONDS
        ):
            return current
        if not self._evidence_lock.acquire(blocking=False):
            return current
        try:
            identity = _measurement_identity(goal)
            if (
                not force
                and self._evidence_attempt_identity == identity
                and self._evidence_attempt_at is not None
                and max(0.0, now - self._evidence_attempt_at)
                < EVIDENCE_REFRESH_SECONDS
            ):
                return current
            self._evidence_attempt_identity = identity
            self._evidence_attempt_at = now
            snapshot = self._snapshot(goal)
            with self._store_lock:
                latest = self._store()
                latest_goal = latest.get("goal")
                if (
                    _measurement_identity(latest_goal)
                    != _measurement_identity(goal)
                ):
                    return latest.get("current")
                baseline = latest_goal.get("baseline") or {}
                if baseline.get("as_of") is None:
                    latest_goal = {**latest_goal, "baseline": snapshot}
                next_store = {
                    **latest, "goal": latest_goal, "current": snapshot,
                    "refreshed_at": float(self._now()),
                }
                write = self._write_store(next_store)
                if not isinstance(write, dict) or not write.get("ok"):
                    return latest.get("current")
                return snapshot
        finally:
            self._evidence_lock.release()

    def agent_projection(self, focus="progress"):
        focus = str(focus or "progress").strip().lower()
        if focus not in {"active", "progress", "weekly"}:
            raise ValueError("focus must be active, progress, or weekly")
        store = self._store()
        goal = store.get("goal")
        result = {
            "ok": True,
            "as_of": float(self._now()),
            "data_scope": "coach_goal_{}".format(focus),
            "goal": goal,
        }
        if focus == "progress":
            current = store.get("current") if goal else None
            result.update({
                "current": current,
                "progress": (
                    goal_progress(goal, goal.get("baseline"), current)
                    if goal else None
                ),
            })
        elif focus == "weekly":
            result["weekly"] = store.get("weekly") or {}
            result["next_review_at"] = self._next_review_at(
                goal, store.get("weekly") or {},
            )
        return result

    def _week_key(self, value=None):
        value = value or self._local_now()
        year, week, _weekday = value.isocalendar()
        return "{:04d}-W{:02d}".format(year, week)

    def _next_review_at(self, goal, weekly):
        if not goal or not goal.get("weekly_enabled"):
            return None
        now = self._local_now()
        delta = int(goal["review_weekday"]) - now.weekday()
        attempted_at = _finite_number(weekly.get("last_attempt_at"))
        attempted_this_week = False
        if attempted_at is not None:
            attempted = datetime.datetime.fromtimestamp(
                attempted_at, tz=now.tzinfo,
            )
            attempted_this_week = self._week_key(attempted) == self._week_key(now)
        if (
            delta < 0 or weekly.get("week_key") == self._week_key(now)
            or attempted_this_week
        ):
            delta += 7
        due_date = (now + datetime.timedelta(days=delta)).date()
        due = datetime.datetime.combine(
            due_date, datetime.time.min, tzinfo=now.tzinfo,
        )
        return due.timestamp()

    def review_if_due(self):
        store = self._store()
        goal = store.get("goal")
        if not goal:
            return {"ok": True, "ran": False, "reason": "no_goal"}
        if not goal.get("weekly_enabled"):
            result = {"ok": True, "ran": False, "reason": "disabled"}
        else:
            now = self._local_now()
            weekly = store.get("weekly") or {}
            if weekly.get("week_key") == self._week_key(now):
                result = {
                    "ok": True, "ran": False, "reason": "already_completed",
                }
            else:
                attempted_at = _finite_number(weekly.get("last_attempt_at"))
                attempted_this_week = False
                if attempted_at is not None:
                    attempted = datetime.datetime.fromtimestamp(
                        attempted_at, tz=now.tzinfo,
                    )
                    attempted_this_week = (
                        self._week_key(attempted) == self._week_key(now)
                    )
                if attempted_this_week:
                    result = {
                        "ok": True, "ran": False,
                        "reason": "already_attempted",
                    }
                elif now.weekday() < goal["review_weekday"]:
                    result = {"ok": True, "ran": False, "reason": "not_due"}
                else:
                    # The weekly runner records even evidence failures as this
                    # week's bounded attempt, preventing minute-by-minute retry.
                    return self.run_weekly(manual=False)

        # Progress stays fresh even when no weekly review is due, but evidence
        # availability does not change the scheduler result in these states.
        try:
            self.refresh_evidence()
        except Exception:
            pass
        return result

    def ask(self, payload):
        if (
            not isinstance(payload, dict)
            or set(payload) - {"message", "history", "page"}
            or self._executor is None
        ):
            return {"ok": False, "error_code": "invalid_request"}
        goal = self._store().get("goal")
        request = {
            "mode": "chat",
            "message": payload.get("message"),
            "history": payload.get("history") or [],
            "page": payload.get("page") or {},
            "goal": normalize_goal(goal) if goal else None,
        }
        try:
            reply = self._executor.run(request)
        except Exception as error:
            code = str(getattr(error, "code", "agent_failed"))
            if code not in _WEEKLY_ERROR_CODES and code != "invalid_request":
                code = "agent_failed"
            return {"ok": False, "error_code": code}
        return {"ok": True, "reply": reply}

    def run_weekly(self, manual=False):
        store = self._store()
        goal = store.get("goal")
        if not goal:
            return {"ok": False, "ran": False, "error_code": "no_goal"}
        if not manual and not goal.get("weekly_enabled"):
            return {"ok": True, "ran": False, "reason": "disabled"}
        if self._executor is None:
            return {"ok": False, "ran": False, "error_code": "agent_unavailable"}
        if not self._weekly_lock.acquire(blocking=False):
            return {"ok": False, "ran": False, "error_code": "busy"}
        attempted_at = float(self._now())
        goal_identity = _goal_identity(goal)

        def record_failure(code):
            with self._store_lock:
                latest = self._store()
                if _goal_identity(latest.get("goal")) != goal_identity:
                    return
                weekly = dict(latest.get("weekly") or {})
                weekly.update({
                    "last_attempt_at": attempted_at,
                    "last_error": code,
                })
                self._write_store({**latest, "weekly": weekly})

        try:
            try:
                snapshot = self.refresh_evidence(force=manual)
            except Exception:
                snapshot = None
            if snapshot is None:
                record_failure("mcp_unavailable")
                return {"ok": False, "ran": False, "error_code": "mcp_unavailable"}
            try:
                result = self._executor.run({
                    "mode": "weekly",
                    "message": "Review progress on the active Token Meter goal for this week.",
                    "history": [],
                    "page": {"route": "efficiency"},
                    "goal": goal,
                })
            except Exception as error:
                code = str(getattr(error, "code", "agent_failed"))
                if code not in _WEEKLY_ERROR_CODES:
                    code = "agent_failed"
                record_failure(code)
                return {"ok": False, "ran": False, "error_code": code}
            recommendation = str((result or {}).get("recommendation") or "")
            if recommendation not in RECOMMENDATIONS:
                record_failure("invalid_output")
                return {"ok": False, "ran": False, "error_code": "invalid_output"}
            with self._store_lock:
                latest = self._store()
                if _goal_identity(latest.get("goal")) != goal_identity:
                    return {
                        "ok": False, "ran": False,
                        "error_code": "goal_changed",
                    }
                completed_at = float(self._now())
                weekly = {
                    "week_key": self._week_key(),
                    "completed_at": completed_at,
                    "last_attempt_at": attempted_at,
                    "recommendation": recommendation,
                    "snapshot": snapshot,
                }
                write = self._write_store({**latest, "weekly": weekly})
                if not isinstance(write, dict) or not write.get("ok"):
                    return {
                        "ok": False, "ran": False,
                        "error_code": "save_failed",
                    }
            return {
                "ok": True,
                "ran": True,
                "recommendation": recommendation,
                "snapshot": snapshot,
            }
        finally:
            self._weekly_lock.release()
