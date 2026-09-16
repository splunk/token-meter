import json
import datetime
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


class FakeProcess:
    """Minimal owned child used to observe cancellation at the adapter boundary."""

    def __init__(self):
        self.terminated = 0
        self.killed = 0

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


class BlockingStdin:
    def __init__(self):
        self.write_started = threading.Event()
        self.release = threading.Event()

    def write(self, value):
        self.write_started.set()
        self.release.wait(2)
        return len(value)

    def close(self):
        return None


class EmptyStdout:
    def read1(self, _size):
        return b""

    def close(self):
        return None


class ControllablePopen:
    def __init__(self, stdin):
        self.stdin = stdin
        self.stdout = EmptyStdout()
        self.returncode = None
        self.terminated = threading.Event()
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated.set()
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed += 1
        self.returncode = -9


class GoalContractTests(unittest.TestCase):
    def test_normalizes_only_allowlisted_goal_fields(self):
        from token_meter.coach.contracts import normalize_goal

        goal = normalize_goal({
            "metric": "cost_per_execution",
            "target_percent": 25,
            "window_days": 14,
            "runtime": "codex",
            "review_weekday": 2,
            "weekly_enabled": True,
            "natural_language": "SENTINEL-PRIVATE",
            "direction": "up",
        })

        self.assertEqual(goal, {
            "metric": "cost_per_execution",
            "direction": "down",
            "target_percent": 25,
            "window_days": 14,
            "runtime": "codex",
            "review_weekday": 2,
            "weekly_enabled": True,
            "label": "Lower cost per execution",
            "unit": "USD/execution",
        })

    def test_rejects_goal_values_outside_the_contract(self):
        from token_meter.coach.contracts import normalize_goal

        valid = {
            "metric": "cost_per_execution",
            "target_percent": 25,
            "window_days": 14,
            "runtime": "codex",
            "review_weekday": 2,
            "weekly_enabled": True,
        }
        cases = (
            {**valid, "metric": "quality"},
            {**valid, "target_percent": 4},
            {**valid, "target_percent": 81},
            {**valid, "target_percent": True},
            {**valid, "window_days": 8},
            {**valid, "runtime": "private-runtime"},
            {**valid, "review_weekday": -1},
            {**valid, "review_weekday": 7},
            {**valid, "weekly_enabled": "yes"},
        )

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_goal(value)


class GoalEvidenceTests(unittest.TestCase):
    def test_cost_per_execution_preserves_partial_coverage(self):
        from token_meter.coach.contracts import metric_snapshot

        snapshot = metric_snapshot(
            "cost_per_execution",
            execution_stats={
                "as_of": 1000,
                "totals": {"cost_usd": 3.0, "execution_count": 4},
                "coverage": {
                    "cost_usd": {"covered": 3, "unavailable": 1},
                    "execution_count": {"covered": 4, "unavailable": 0},
                },
            },
        )

        self.assertEqual(snapshot, {
            "metric": "cost_per_execution",
            "value": 1.0,
            "unit": "USD/execution",
            "coverage": "partial",
            "covered": 3,
            "unavailable": 1,
            "as_of": 1000,
        })

    def test_ratio_is_unavailable_when_its_denominator_is_zero(self):
        from token_meter.coach.contracts import metric_snapshot

        snapshot = metric_snapshot(
            "retry_rate",
            execution_stats={
                "as_of": 1000,
                "totals": {"retries": 0, "attempts": 0},
                "coverage": {
                    "retries": {"covered": 2, "unavailable": 0},
                    "attempts": {"covered": 2, "unavailable": 0},
                },
            },
        )

        self.assertEqual(snapshot, {
            "metric": "retry_rate",
            "value": None,
            "unit": "%",
            "coverage": "unavailable",
            "covered": 0,
            "unavailable": 0,
            "as_of": 1000,
        })

    def test_output_per_dollar_requires_complete_paired_coverage(self):
        from token_meter.coach.contracts import metric_snapshot

        snapshot = metric_snapshot(
            "output_per_dollar",
            execution_stats={
                "as_of": 1000,
                "totals": {"output_tokens": 2_000, "cost_usd": 2.0},
                "coverage": {
                    "output_tokens": {"covered": 2, "unavailable": 0},
                    "cost_usd": {"covered": 1, "unavailable": 1},
                },
            },
        )

        self.assertEqual(snapshot["coverage"], "unavailable")
        self.assertIsNone(snapshot["value"])
        self.assertEqual(snapshot["unavailable"], 1)

    def test_context_peak_remains_a_token_count(self):
        from token_meter.coach.contracts import metric_snapshot

        snapshot = metric_snapshot(
            "context_peak",
            execution_stats={
                "as_of": 1000,
                "totals": {"context_peak": 80_000},
                "coverage": {
                    "context_peak": {"covered": 2, "unavailable": 0},
                },
            },
        )

        self.assertEqual(snapshot["value"], 80_000)
        self.assertEqual(snapshot["unit"], "tokens")

    def test_progress_uses_metric_direction_and_clamps_to_target(self):
        from token_meter.coach.contracts import goal_progress

        lower = goal_progress(
            {"metric": "cost_per_execution", "direction": "down", "target_percent": 20},
            {"value": 10.0, "coverage": "complete"},
            {"value": 7.0, "coverage": "complete"},
        )
        higher = goal_progress(
            {"metric": "output_per_dollar", "direction": "up", "target_percent": 25},
            {"value": 100.0, "coverage": "complete"},
            {"value": 112.5, "coverage": "partial"},
        )

        self.assertEqual(lower, {
            "available": True,
            "progress_percent": 100.0,
            "change_percent": -30.0,
            "coverage": "complete",
        })
        self.assertEqual(higher, {
            "available": True,
            "progress_percent": 50.0,
            "change_percent": 12.5,
            "coverage": "partial",
        })


    def test_progress_is_unavailable_without_a_positive_baseline(self):
        from token_meter.coach.contracts import goal_progress

        for baseline in (
            {"value": None, "coverage": "unavailable"},
            {"value": 0, "coverage": "complete"},
        ):
            with self.subTest(baseline=baseline):
                self.assertEqual(
                    goal_progress(
                        {"metric": "wait_per_execution", "direction": "down", "target_percent": 10},
                        baseline,
                        {"value": 5, "coverage": "complete"},
                    ),
                    {
                        "available": False,
                        "progress_percent": None,
                        "change_percent": None,
                        "coverage": "unavailable",
                    },
                )


class CoachSchedulerTests(unittest.TestCase):
    def test_wake_signals_do_not_bypass_the_post_scan_floor(self):
        # Break caught: dashboard polling wakes the scheduler and causes a
        # second scan before the one-minute post-scan interval has elapsed.
        import token_meter.app as app

        clock = [0.0]
        scans = []

        class StopScheduler(BaseException):
            pass

        class Service:
            def review_if_due(self):
                scans.append(clock[0])
                if len(scans) == 2:
                    raise StopScheduler()

        class Wake:
            def __init__(self):
                self.waits = 0

            def clear(self):
                return None

            def wait(self, seconds):
                self.waits += 1
                if self.waits == 2:
                    clock[0] += seconds
                return True

        wake = Wake()
        with mock.patch.object(app, "coach_service", return_value=Service()), mock.patch.object(
            app, "_coach_wake", wake
        ), mock.patch.object(app.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(StopScheduler):
                app.coach_scheduler()

        self.assertEqual(scans, [0.0, 60.0])


class CoachPersistenceTests(unittest.TestCase):
    def test_app_settings_write_is_atomic_idempotent_and_preserves_other_keys(self):
        import meter

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"budgets": {"total": 10}}))
            state = {
                "schema_version": 1, "goal": None, "current": None,
                "refreshed_at": None, "weekly": {},
            }

            first = meter.set_coach_settings(state, path=str(path))
            before = path.stat().st_mtime_ns
            second = meter.set_coach_settings(state, path=str(path))
            after = path.stat().st_mtime_ns
            stored = json.loads(path.read_text())

        self.assertEqual(first, {"ok": True, "changed": True, "coach": state})
        self.assertEqual(second, {"ok": True, "changed": False, "coach": state})
        self.assertEqual(stored["budgets"], {"total": 10})
        self.assertEqual(stored["coach"], state)
        self.assertEqual(after, before)

    def test_coach_and_budget_writes_preserve_both_settings(self):
        import meter

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{}")
            state = {
                "schema_version": 1, "goal": None, "current": None,
                "refreshed_at": None, "weekly": {},
            }
            budget = meter.budget_settings(path=str(path))
            coach_write_started = threading.Event()
            release_coach_write = threading.Event()
            real_atomic_write = meter.atomic_write_text

            def blocking_atomic_write(write_path, text):
                if '"coach"' in text and '"budgets"' not in text:
                    coach_write_started.set()
                    self.assertTrue(release_coach_write.wait(2))
                real_atomic_write(write_path, text)

            with mock.patch.object(meter, "atomic_write_text", blocking_atomic_write):
                coach_thread = threading.Thread(
                    target=meter.set_coach_settings,
                    kwargs={"value": state, "path": str(path)},
                )
                budget_thread = threading.Thread(
                    target=meter.set_budget_settings,
                    kwargs={"values": budget, "path": str(path)},
                )
                coach_thread.start()
                self.assertTrue(coach_write_started.wait(2))
                budget_thread.start()
                release_coach_write.set()
                coach_thread.join(2)
                budget_thread.join(2)

            self.assertFalse(coach_thread.is_alive())
            self.assertFalse(budget_thread.is_alive())
            stored = json.loads(path.read_text())

        self.assertEqual(stored["coach"], state)
        self.assertEqual(stored["budgets"], budget)

    def setUp(self):
        self.saved = {}
        self.writes = []
        self.calls = []

    def stats(self, **arguments):
        self.calls.append(arguments)
        return {
            "ok": True,
            "as_of": 1_000,
            "totals": {
                "cost_usd": 4.0,
                "execution_count": 2,
                "output_tokens": 2_000,
                "wait_seconds": 30.0,
                "retries": 1,
                "attempts": 5,
                "context_peak": 0.6,
            },
            "coverage": {
                name: {"covered": 2, "unavailable": 0}
                for name in (
                    "cost_usd", "execution_count", "output_tokens",
                    "wait_seconds", "retries", "attempts", "context_peak",
                )
            },
        }

    def service(self):
        from token_meter.coach.service import CoachService

        def write(value):
            changed = value != self.saved
            self.saved = value
            self.writes.append(value)
            return {"ok": True, "changed": changed}

        return CoachService(
            read_store=lambda: self.saved,
            write_store=write,
            stats=self.stats,
            now=lambda: 1_800_000_000.0,
        )

    def test_activation_is_fast_and_persists_no_source_text(self):
        service = self.service()

        result = service.save_goal({
            "metric": "cost_per_execution",
            "target_percent": 20,
            "window_days": 7,
            "runtime": "codex",
            "review_weekday": 0,
            "weekly_enabled": True,
            "natural_language": "SENTINEL-PRIVATE",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(self.saved, {
            "schema_version": 1,
            "goal": {
                "metric": "cost_per_execution",
                "direction": "down",
                "target_percent": 20,
                "window_days": 7,
                "runtime": "codex",
                "review_weekday": 0,
                "weekly_enabled": True,
                "label": "Lower cost per execution",
                "unit": "USD/execution",
                "created_at": 1_800_000_000.0,
                "baseline": {
                    "metric": "cost_per_execution",
                    "value": None,
                    "unit": "USD/execution",
                    "coverage": "unavailable",
                    "covered": 0,
                    "unavailable": 0,
                    "as_of": None,
                },
            },
            "current": None,
            "refreshed_at": None,
            "weekly": {},
        })
        self.assertNotIn("SENTINEL-PRIVATE", str(self.saved))
        self.assertEqual(self.calls, [])

    def test_background_refresh_hydrates_baseline_and_current_once(self):
        service = self.service()
        service.save_goal({
            "metric": "cost_per_execution", "target_percent": 20,
            "window_days": 7, "runtime": "codex", "review_weekday": 0,
            "weekly_enabled": False,
        })

        current = service.refresh_evidence()
        state = service.state()

        self.assertEqual(current["value"], 2.0)
        self.assertEqual(state["goal"]["baseline"], current)
        self.assertEqual(state["current"], current)
        self.assertEqual(state["progress"]["progress_percent"], 0.0)
        self.assertEqual(self.calls[0]["runtime"], "codex")
        self.assertEqual(self.calls[0]["metrics"], ("cost_usd", "execution_count"))
        self.assertIn("start", self.calls[0])
        self.assertIn("end", self.calls[0])
        self.assertEqual(len(self.calls), 1)

    def test_weekly_toggle_during_refresh_preserves_progress_and_toggle(self):
        from token_meter.coach.service import CoachService

        stats_started = threading.Event()
        release_stats = threading.Event()
        stale_toggle_ready = threading.Event()
        release_toggle = threading.Event()
        refresh_written = threading.Event()
        base_stats = self.stats

        def stats(**arguments):
            stats_started.set()
            self.assertTrue(release_stats.wait(2))
            return base_stats(**arguments)

        def write(value):
            goal = value.get("goal") or {}
            if (
                goal.get("weekly_enabled") is False
                and value.get("current") is None
                and (self.saved.get("goal") or {}).get("weekly_enabled") is True
            ):
                stale_toggle_ready.set()
                self.assertTrue(release_toggle.wait(2))
            self.saved = value
            if value.get("current") is not None:
                refresh_written.set()
            return {"ok": True, "changed": True}

        service = CoachService(
            read_store=lambda: self.saved,
            write_store=write,
            stats=stats,
            now=lambda: 1_800_000_000.0,
        )
        service.save_goal({
            "metric": "cost_per_execution", "target_percent": 20,
            "window_days": 7, "runtime": "codex", "review_weekday": 0,
            "weekly_enabled": True,
        })

        refresh_thread = threading.Thread(target=service.refresh_evidence)
        toggle_thread = threading.Thread(
            target=service.set_weekly_enabled, args=(False,),
        )
        refresh_thread.start()
        self.assertTrue(stats_started.wait(2))
        toggle_thread.start()
        self.assertTrue(stale_toggle_ready.wait(2))
        release_stats.set()
        refresh_written.wait(0.2)
        release_toggle.set()
        refresh_thread.join(2)
        toggle_thread.join(2)

        self.assertFalse(refresh_thread.is_alive())
        self.assertFalse(toggle_thread.is_alive())
        self.assertFalse(self.saved["goal"]["weekly_enabled"])
        self.assertEqual(self.saved["current"]["value"], 2.0)
        self.assertEqual(self.saved["goal"]["baseline"]["value"], 2.0)

    def test_refresh_does_not_apply_an_old_snapshot_to_a_replaced_goal(self):
        from token_meter.coach.service import CoachService

        stats_started = threading.Event()
        release_stats = threading.Event()
        base_stats = self.stats

        def stats(**arguments):
            stats_started.set()
            self.assertTrue(release_stats.wait(2))
            return base_stats(**arguments)

        service = CoachService(
            read_store=lambda: self.saved,
            write_store=lambda value: (
                setattr(self, "saved", value)
                or {"ok": True, "changed": True}
            ),
            stats=stats,
            # Both goals intentionally share a timestamp.
            now=lambda: 1_800_000_000.0,
        )
        service.save_goal({
            "metric": "cost_per_execution", "target_percent": 20,
            "window_days": 7, "runtime": "codex", "review_weekday": 0,
            "weekly_enabled": False,
        })
        refresh_thread = threading.Thread(target=service.refresh_evidence)
        refresh_thread.start()
        self.assertTrue(stats_started.wait(2))

        service.save_goal({
            "metric": "cost_per_execution", "target_percent": 20,
            "window_days": 7, "runtime": "codex", "review_weekday": 0,
            "weekly_enabled": True,
        })
        release_stats.set()
        refresh_thread.join(2)

        self.assertFalse(refresh_thread.is_alive())
        self.assertTrue(self.saved["goal"]["weekly_enabled"])
        self.assertGreater(self.saved["goal"]["created_at"], 1_800_000_000.0)
        self.assertIsNone(self.saved["current"])
        self.assertIsNone(self.saved["refreshed_at"])

    def test_state_never_blocks_on_evidence_collection(self):
        service = self.service()
        service.save_goal({
            "metric": "retry_rate", "target_percent": 10,
            "window_days": 14, "runtime": "all", "review_weekday": 4,
            "weekly_enabled": True,
        })

        state = service.state()

        self.assertIsNone(state["current"])
        self.assertFalse(state["progress"]["available"])
        self.assertEqual(self.calls, [])

    def test_reactivating_the_same_contract_is_idempotent(self):
        service = self.service()
        value = {
            "metric": "retry_rate", "target_percent": 10,
            "window_days": 14, "runtime": "all", "review_weekday": 4,
            "weekly_enabled": False,
        }

        first = service.save_goal(value)
        saved = self.saved
        second = service.save_goal(value)

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(self.saved, saved)
        self.assertEqual(len(self.calls), 0)

    def test_state_recovers_from_malformed_store_and_never_echoes_unknown_fields(self):
        self.saved = {
            "schema_version": 99,
            "goal": {"metric": "quality", "prompt": "SENTINEL-PRIVATE"},
            "weekly": {"message": "SENTINEL-PRIVATE"},
        }

        state = self.service().state()

        self.assertIsNone(state["goal"])
        self.assertIsNone(state["progress"])
        self.assertEqual(state["weekly"], {})
        self.assertNotIn("SENTINEL-PRIVATE", str(state))

    def test_persisted_snapshot_units_are_allowlisted_not_free_text(self):
        service = self.service()
        self.saved = {
            "schema_version": 1,
            "goal": {
                "metric": "cost_per_execution", "target_percent": 20,
                "window_days": 7, "runtime": "codex", "review_weekday": 0,
                "weekly_enabled": True, "created_at": 1_000,
                "baseline": {
                    "metric": "cost_per_execution", "value": 2.0,
                    "unit": "SENTINEL-PRIVATE", "coverage": "complete",
                    "covered": 2, "unavailable": 0, "as_of": 1_000,
                },
            },
            "current": None, "refreshed_at": None, "weekly": {},
        }

        state = service.state()

        self.assertIsNone(state["goal"])
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(state))

    def test_weekly_toggle_and_clear_preserve_no_message_content(self):
        service = self.service()
        service.save_goal({
            "metric": "wait_per_execution", "target_percent": 15,
            "window_days": 30, "runtime": "all", "review_weekday": 6,
            "weekly_enabled": True,
        })

        paused = service.set_weekly_enabled(False)
        cleared = service.clear_goal()

        self.assertFalse(paused["goal"]["weekly_enabled"])
        self.assertTrue(cleared["ok"])
        self.assertIsNone(cleared["goal"])
        self.assertEqual(self.saved, {
            "schema_version": 1, "goal": None, "current": None,
            "refreshed_at": None, "weekly": {},
        })

    def test_agent_projection_is_content_free_and_focus_bounded(self):
        service = self.service()
        service.save_goal({
            "metric": "context_peak", "target_percent": 20,
            "window_days": 7, "runtime": "all", "review_weekday": 1,
            "weekly_enabled": False, "prompt": "SENTINEL-PRIVATE",
        })

        projection = service.agent_projection("progress")

        self.assertEqual(projection["data_scope"], "coach_goal_progress")
        self.assertEqual(projection["goal"]["metric"], "context_peak")
        self.assertIn("current", projection)
        self.assertIn("progress", projection)
        self.assertNotIn("agent", projection)
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(projection))
        with self.assertRaises(ValueError):
            service.agent_projection("messages")


class CodexCoachTests(unittest.TestCase):
    def make_workspace(self, root):
        skill = Path(root) / ".agents" / "skills" / "token-meter-coach"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: token-meter-coach\ndescription: Test coach\n---\nUse MCP.\n"
        )
        return str(root)

    def valid_request(self, mode="chat"):
        request = {
            "mode": mode,
            "message": "Where is my biggest efficiency opportunity?",
            "history": [{"role": "user", "content": "Help me spend less."}],
            "page": {"route": "efficiency", "session_id": "session-123"},
            "goal": None,
        }
        if mode == "weekly":
            request["message"] = "Run the weekly review."
            request["goal"] = {
                "metric": "cost_per_execution", "target_percent": 20,
                "window_days": 7, "runtime": "codex", "review_weekday": 0,
                "weekly_enabled": True,
            }
        return request

    def completed_result(self):
        return {
            "returncode": 0,
            "events": "",
            "stderr": "",
            "result": json.dumps({
                "message": "A bounded response.", "evidence": [],
                "action": None, "goal_draft": None,
            }),
        }

    def test_default_runner_cancels_exact_child_while_stdin_write_is_blocked(self):
        # Break caught: synchronous stdin delivery holds the owner thread so a
        # cancellation cannot reach its registered child before the write unblocks.
        from token_meter.coach.codex import _default_runner

        stdin = BlockingStdin()
        process = ControllablePopen(stdin)
        cancel_event = threading.Event()
        registered = threading.Event()
        result_path = tempfile.NamedTemporaryFile(delete=False)
        result_path.close()
        completed = []
        try:
            with mock.patch("token_meter.coach.codex.subprocess.Popen", return_value=process):
                thread = threading.Thread(
                    target=lambda: completed.append(_default_runner(
                        ["codex"], "x" * 100_000, cwd=".", env={}, timeout=5,
                        result_path=result_path.name,
                        on_process=lambda _process: registered.set(),
                        cancel_event=cancel_event,
                    )),
                )
                thread.start()
                self.assertTrue(registered.wait(1))
                self.assertTrue(stdin.write_started.wait(1))
                cancel_event.set()
                self.assertTrue(process.terminated.wait(0.5))
                thread.join(0.5)
                self.assertFalse(thread.is_alive())
        finally:
            stdin.release.set()
            if 'thread' in locals():
                thread.join(2)
            os.unlink(result_path.name)

        self.assertEqual(completed[0]["returncode"], -15)

    def test_default_runner_drains_large_stderr_without_backpressure(self):
        # Break caught: stderr pipe backpressure prevents an otherwise successful
        # child from exiting before the configured deadline.
        from token_meter.coach.codex import _default_runner

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            script = (
                "import pathlib,sys;"
                "sys.stderr.write('x'*131072);sys.stderr.flush();"
                "pathlib.Path(sys.argv[1]).write_text('{}')"
            )
            completed = _default_runner(
                [sys.executable, "-c", script, str(result_path)], "{}",
                cwd=tmp, env=os.environ.copy(), timeout=2, result_path=str(result_path),
            )

        self.assertEqual(completed["returncode"], 0)
        self.assertEqual(completed["result"], "{}")
        self.assertLessEqual(len(completed["stderr"]), 8_192)

    def test_default_runner_returns_after_direct_child_exits_with_inherited_stdout(self):
        # Break caught: a descendant retaining stdout holds the reader open and
        # makes the runner wait beyond its direct child's lifetime.
        from token_meter.coach.codex import _default_runner

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.txt"
            script = (
                "import pathlib,subprocess,sys;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(2)'],"
                "stdout=sys.stdout,stderr=sys.stderr);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
            )
            started = time.monotonic()
            try:
                completed = _default_runner(
                    [sys.executable, "-c", script, str(result_path)], "{}",
                    cwd=tmp, env=os.environ.copy(), timeout=5, result_path=str(result_path),
                )
            finally:
                if result_path.exists():
                    try:
                        os.kill(int(result_path.read_text()), 15)
                    except (OSError, ValueError):
                        pass

        self.assertEqual(completed["returncode"], 0)
        self.assertLess(time.monotonic() - started, 0.8)

    def test_default_runner_discards_continuous_descendant_stdout_after_direct_exit(self):
        # Break caught: a descendant continually filling inherited stdout keeps
        # the queue nonempty forever after the direct child has exited.
        from token_meter.coach.codex import _default_runner

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "descendant.pid"
            script = (
                "import pathlib,subprocess,sys;"
                "child=subprocess.Popen([sys.executable,'-c',"
                "\"import sys\\nwhile True: sys.stdout.write('{\\\"type\\\":\\\"event\\\"}\\\\n'*256);sys.stdout.flush()\"],"
                "stdout=sys.stdout,stderr=sys.stderr);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
            )
            completed = []
            started = time.monotonic()
            with mock.patch("token_meter.coach.codex.MAX_EVENTS_BYTES", 1_000_000_000):
                thread = threading.Thread(
                    target=lambda: completed.append(_default_runner(
                        [sys.executable, "-c", script, str(result_path)], "{}",
                        cwd=tmp, env=os.environ.copy(), timeout=5, result_path=str(result_path),
                        on_event=lambda _event: time.sleep(0.01),
                    )),
                )
                thread.start()
                deadline = time.monotonic() + 1
                while not result_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                try:
                    self.assertTrue(result_path.exists())
                    thread.join(0.7)
                    self.assertFalse(thread.is_alive())
                finally:
                    if result_path.exists():
                        try:
                            os.kill(int(result_path.read_text()), 15)
                        except (OSError, ValueError):
                            pass
                    thread.join(2)

        self.assertEqual(completed[0]["returncode"], 0)
        self.assertLess(time.monotonic() - started, 0.8)

    def test_default_runner_rejects_oversized_fragmented_jsonl_before_decoding(self):
        # Break caught: a newline-free or fragmented stdout stream evades the
        # byte cap because only decoded complete rows are counted.
        from token_meter.coach.codex import _default_runner, CoachRunError, MAX_EVENTS_BYTES

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            script = "import sys;sys.stdout.buffer.write(b'x'*{})".format(
                MAX_EVENTS_BYTES + 1,
            )
            with self.assertRaisesRegex(CoachRunError, "output_too_large"):
                _default_runner(
                    [sys.executable, "-c", script], "{}", cwd=tmp,
                    env=os.environ.copy(), timeout=2, result_path=str(result_path),
                )

    def test_runner_failure_clears_activity_and_exact_process_ownership(self):
        # Break caught: an exception path leaves stale lifecycle state or a
        # process reference that a later cancellation could target.
        from token_meter.coach.codex import CodexCoach, CoachRunError

        process = FakeProcess()

        def runner(*_args, **options):
            options["on_process"](process)
            raise OSError("unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            with self.assertRaisesRegex(CoachRunError, "agent_unavailable"):
                coach.run(self.valid_request())

        self.assertNotIn("activity", coach.status())
        self.assertEqual(coach.cancel(), {"ok": True, "changed": False})
        self.assertEqual(process.terminated, 0)

    def test_cancel_a_does_not_target_registered_run_b_after_a_finishes(self):
        # Break caught: cancellation reads a later active-process slot after its
        # captured run finishes, terminating or killing B instead of A.
        from token_meter.coach.codex import CodexCoach, CoachRunError

        release_a = threading.Event()
        a_registered = threading.Event()
        a_finished = threading.Event()
        b_registered = threading.Event()
        allow_cancel_return = threading.Event()
        release_b = threading.Event()
        first = FakeProcess()
        second = FakeProcess()
        first_terminate = first.terminate

        def terminate_first():
            first_terminate()
            release_a.set()
            b_registered.wait(2)
            allow_cancel_return.wait(2)

        first.terminate = terminate_first
        a_result = []
        b_result = []

        def runner_a(*_args, **options):
            options["on_process"](first)
            a_registered.set()
            self.assertTrue(release_a.wait(2))
            return self.completed_result()

        def runner_b(*_args, **options):
            options["on_process"](second)
            b_registered.set()
            self.assertTrue(release_b.wait(2))
            return self.completed_result()

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"),
                runner=runner_a, path_is_executable=lambda _path: True,
            )
            def run_a():
                try:
                    coach.run(self.valid_request())
                except CoachRunError as error:
                    a_result.append(error.code)
                finally:
                    a_finished.set()
            thread_a = threading.Thread(target=run_a)
            thread_a.start()
            self.assertTrue(a_registered.wait(2))

            cancel_result = []
            cancel_thread = threading.Thread(
                target=lambda: cancel_result.append(coach.cancel()),
            )
            cancel_thread.start()
            self.assertTrue(a_finished.wait(2))

            coach._runner = runner_b
            thread_b = threading.Thread(target=lambda: b_result.append(coach.run(self.valid_request())))
            thread_b.start()
            self.assertTrue(b_registered.wait(2))
            self.assertEqual(second.terminated, 0)
            self.assertEqual(second.killed, 0)
            self.assertEqual(coach.status()["activity"]["stage"], "opening_codex")

            allow_cancel_return.set()
            cancel_thread.join(2)
            self.assertFalse(cancel_thread.is_alive())
            self.assertEqual(cancel_result, [{"ok": True, "changed": True}])
            self.assertEqual(second.terminated, 0)
            self.assertEqual(second.killed, 0)

            release_b.set()
            thread_a.join(2)
            thread_b.join(2)

        self.assertFalse(thread_a.is_alive())
        self.assertFalse(thread_b.is_alive())
        self.assertEqual(a_result, ["cancelled"])
        self.assertEqual(b_result, [{
            "message": "A bounded response.", "evidence": [],
            "action": None, "goal_draft": None,
        }])
        self.assertNotIn("activity", coach.status())

    def test_default_runner_maps_fragmented_complete_mcp_rows_to_lifecycle_stages(self):
        # Break caught: fragmented JSONL advances activity before a row is
        # complete, or fails to advance after the completed allowlisted row.
        from token_meter.coach.codex import CodexCoach

        partial_ready = threading.Event()
        release_start = threading.Event()
        release_completion = threading.Event()
        release_finish = threading.Event()
        test_case = self
        start = json.dumps({
            "type": "item.started",
            "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "usage"},
        }).encode("utf-8")
        completion = json.dumps({
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "usage"},
        }).encode("utf-8") + b"\n"

        class FragmentedStdout:
            def __init__(self, result_path):
                self.result_path = result_path
                self.index = 0

            def read1(self, _size):
                self.index += 1
                if self.index == 1:
                    partial_ready.set()
                    return start[:20]
                if self.index == 2:
                    test_case.assertTrue(release_start.wait(2))
                    return start[20:] + b"\n"
                if self.index == 3:
                    test_case.assertTrue(release_completion.wait(2))
                    return completion
                if self.index == 4:
                    test_case.assertTrue(release_finish.wait(2))
                Path(self.result_path).write_text(json.dumps({
                    "message": "A bounded response.", "evidence": [],
                    "action": None, "goal_draft": None,
                }))
                process.returncode = 0
                return b""

            def close(self):
                return None

        class FragmentedProcess:
            def __init__(self, result_path):
                self.stdin = mock.Mock()
                self.stdout = FragmentedStdout(result_path)
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        def wait_for_stage(coach, stage):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if coach.status().get("activity", {}).get("stage") == stage:
                    return
                time.sleep(0.01)
            self.fail("stage {} was not observed".format(stage))

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp) / "source")
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=workspace, path_is_executable=lambda _path: True,
            )
            process = None
            def popen(command, **_kwargs):
                nonlocal process
                result_path = command[command.index("--output-last-message") + 1]
                process = FragmentedProcess(result_path)
                return process
            with mock.patch("token_meter.coach.codex.subprocess.Popen", popen):
                result = []
                thread = threading.Thread(target=lambda: result.append(coach.run(self.valid_request())))
                thread.start()
                self.assertTrue(partial_ready.wait(2))
                wait_for_stage(coach, "opening_codex")
                release_start.set()
                wait_for_stage(coach, "reading_token_meter")
                release_completion.set()
                wait_for_stage(coach, "checking_evidence")
                release_finish.set()
                thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["message"], "A bounded response.")
        self.assertNotIn("activity", coach.status())

    def test_status_projects_only_content_free_live_activity(self):
        # Break caught: a run does not publish a bounded live lifecycle state,
        # or leaves that state behind after it ends.
        from token_meter.coach.codex import CodexCoach

        runner_started = threading.Event()
        release = threading.Event()

        def runner(command, prompt, **options):
            options["on_process"](FakeProcess())
            options["on_event"]({"type": "thread.started"})
            runner_started.set()
            self.assertTrue(release.wait(2))
            return self.completed_result()

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            thread = threading.Thread(target=lambda: coach.run(self.valid_request()))
            thread.start()
            self.assertTrue(runner_started.wait(2))
            status = coach.status()
            self.assertEqual(status["activity"]["stage"], "opening_codex")
            self.assertIsInstance(status["activity"]["started_at"], (int, float))
            self.assertTrue(status["activity"]["cancellable"])
            self.assertNotIn("prompt", status["activity"])
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertNotIn("activity", coach.status())

    def test_status_maps_only_allowlisted_events_without_retaining_agent_content(self):
        # Break caught: JSONL content leaks from the adapter, or allowlisted MCP
        # lifecycle events do not advance the visible fixed stage.
        from token_meter.coach.codex import CodexCoach

        event_sent = threading.Event()
        release = threading.Event()

        def runner(command, prompt, **options):
            options["on_process"](FakeProcess())
            options["on_event"]({
                "type": "item.started",
                "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "usage"},
            })
            options["on_event"]({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "SENTINEL-PRIVATE"},
            })
            options["on_event"]({
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "usage"},
            })
            event_sent.set()
            self.assertTrue(release.wait(2))
            return self.completed_result()

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            thread = threading.Thread(target=lambda: coach.run(self.valid_request()))
            thread.start()
            self.assertTrue(event_sent.wait(2))
            activity = coach.status()["activity"]
            self.assertEqual(activity["stage"], "checking_evidence")
            self.assertEqual(
                set(activity),
                {"stage", "started_at", "cancellable", "tool", "reads"},
            )
            self.assertEqual(activity["tool"], "usage")
            self.assertEqual(activity["reads"], 1)
            self.assertNotIn("SENTINEL-PRIVATE", json.dumps(activity))
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_cancel_owns_only_the_active_registered_process_and_maps_to_cancelled(self):
        # Break caught: cancellation is unavailable, targets a process by a
        # broader identity, or reports a generic subprocess failure.
        from token_meter.coach.codex import CodexCoach, CoachRunError

        runner_started = threading.Event()
        result = []
        process = FakeProcess()

        def runner(command, prompt, **options):
            options["on_process"](process)
            runner_started.set()
            self.assertTrue(options["cancel_event"].wait(2))
            return {"returncode": 1, "events": "", "stderr": "", "result": ""}

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            self.assertEqual(coach.cancel(), {"ok": True, "changed": False})
            def run():
                try:
                    coach.run(self.valid_request())
                except CoachRunError as error:
                    result.append(error.code)
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(runner_started.wait(2))
            self.assertEqual(coach.cancel(), {"ok": True, "changed": True})
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(process.terminated, 1)
            self.assertEqual(result, ["cancelled"])
            self.assertEqual(coach.cancel(), {"ok": True, "changed": False})
            self.assertNotIn("activity", coach.status())

    def test_cancel_does_not_convert_a_completed_direct_child_to_cancelled(self):
        # Break caught: cancellation wins a completion race after the owned
        # direct child has exited, turning its completed result into cancelled.
        from token_meter.coach.codex import CodexCoach

        class CompletedProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.returncode = None

            def poll(self):
                return self.returncode

        process = CompletedProcess()
        cancel_results = []
        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"),
                path_is_executable=lambda _path: True,
            )

            def runner(*_args, **options):
                options["on_process"](process)
                process.returncode = 0
                cancel_results.append(coach.cancel())
                return self.completed_result()

            coach._runner = runner
            result = coach.run(self.valid_request())

        self.assertEqual(cancel_results, [{"ok": True, "changed": False}])
        self.assertEqual(process.terminated, 0)
        self.assertEqual(result["message"], "A bounded response.")

    def test_cancel_before_child_registration_stops_that_run_without_launching_a_child(self):
        # Break caught: an early cancellation is ignored until a process exists,
        # allowing the same run to launch a child after the user stopped it.
        from token_meter.coach.codex import CodexCoach, CoachRunError

        environment_started = threading.Event()
        release_environment = threading.Event()
        result = []
        launched = []

        def isolated(environment, temporary):
            environment_started.set()
            self.assertTrue(release_environment.wait(2))
            return dict(environment)

        def runner(*_args, **_kwargs):
            launched.append(True)
            return self.completed_result()

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            def run():
                try:
                    coach.run(self.valid_request())
                except CoachRunError as error:
                    result.append(error.code)
            with mock.patch("token_meter.coach.codex._isolated_environment", isolated):
                thread = threading.Thread(target=run)
                thread.start()
                self.assertTrue(environment_started.wait(2))
                self.assertEqual(coach.cancel(), {"ok": True, "changed": True})
                release_environment.set()
                thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["cancelled"])
        self.assertEqual(launched, [])
        self.assertNotIn("activity", coach.status())

    def test_builds_isolated_ephemeral_read_only_codex_run_with_required_mcp(self):
        from token_meter.coach.codex import CodexCoach

        seen = {}

        def runner(command, prompt, **options):
            seen.update(command=command, prompt=prompt, **options)
            seen["auth_target"] = (
                Path(options["env"]["CODEX_HOME"]) / "auth.json"
            ).resolve()
            self.assertTrue(Path(options["cwd"], ".agents", "skills", "token-meter-coach", "SKILL.md").is_file())
            return {
                "returncode": 0,
                "events": json.dumps({
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "stats"},
                }) + "\n",
                "result": json.dumps({
                    "message": "Cost per execution is the clearest opportunity.",
                    "evidence": [{
                        "label": "Covered cost / execution", "value": "$2.00",
                        "source": "Token Meter MCP",
                    }],
                    "action": {"kind": "reduce_context", "subject": "claude-opus-5"},
                    "goal_draft": None,
                }),
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            codex_home.mkdir()
            auth_path = codex_home / "auth.json"
            auth_path.write_text('{"test":true}')
            workspace = self.make_workspace(Path(tmp) / "source")
            coach = CodexCoach(
                codex_path=lambda: "/opt/bin/codex",
                mcp_command="/opt/bin/token-meter-mcp",
                mcp_args=(),
                workspace_source=workspace,
                runner=runner,
                path_is_executable=lambda path: path == "/opt/bin/codex",
                environment=lambda _path: {
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(Path(tmp) / "home"),
                    "CODEX_HOME": str(codex_home),
                },
            )
            result = coach.run(self.valid_request())

        command = seen["command"]
        for value in (
            "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--json", "--output-schema",
            "--output-last-message", "-C", "read-only",
        ):
            self.assertIn(value, command)
        joined = " ".join(command)
        self.assertIn("--model gpt-5.6-luna", joined)
        self.assertIn('model_reasoning_effort="low"', joined)
        self.assertIn('model_verbosity="low"', joined)
        self.assertIn("mcp_servers.tokenmeter.required=true", joined)
        self.assertIn("mcp_servers.tokenmeter.enabled_tools", joined)
        self.assertIn("/opt/bin/token-meter-mcp", joined)
        for override in (
            'features.shell_tool=false',
            'features.unified_exec=false',
            'features.apps=false',
            'features.browser_use=false',
            'features.computer_use=false',
            'features.image_generation=false',
            'features.multi_agent=false',
            'features.plugins=false',
            'features.remote_plugin=false',
            'tools.view_image=false',
            'web_search="disabled"',
        ):
            self.assertIn(override, joined)
        isolated_home = Path(seen["env"]["CODEX_HOME"])
        self.assertNotEqual(isolated_home, codex_home)
        self.assertEqual(seen["auth_target"], auth_path.resolve())
        self.assertFalse((isolated_home / "skills").exists())
        self.assertIn("$token-meter-coach", seen["prompt"])
        self.assertIn('"route":"efficiency"', seen["prompt"])
        self.assertNotIn("SENTINEL-PRIVATE", seen["prompt"])
        self.assertFalse(os.path.exists(seen["cwd"]))
        self.assertFalse(isolated_home.exists())
        self.assertEqual(result["action"], {"kind": "reduce_context", "subject": "claude-opus-5"})

    def test_coach_bridge_values_stay_in_child_environment_not_command_or_prompt(self):
        from token_meter.coach.codex import CodexCoach

        seen = {}
        def runner(command, prompt, **options):
            seen.update(command=command, prompt=prompt, **options)
            return {"returncode": 0, "events": "", "stderr": "", "result": json.dumps({
                "message": "No evidence needed.", "evidence": [],
                "action": None, "goal_draft": None,
            })}

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path="/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
                child_environment=lambda: {
                    "TOKEN_METER_COACH_EVIDENCE_URL": "http://127.0.0.1:8722/coach/evidence",
                    "TOKEN_METER_COACH_ACTION_TOKEN": "SENTINEL-BRIDGE",
                    "TOKEN_METER_COACH_EVIDENCE_TOKEN": "SENTINEL-INTERNAL",
                },
            )
            coach.run(self.valid_request())

        self.assertEqual(seen["env"]["TOKEN_METER_COACH_ACTION_TOKEN"], "SENTINEL-BRIDGE")
        self.assertEqual(seen["env"]["TOKEN_METER_COACH_EVIDENCE_TOKEN"], "SENTINEL-INTERNAL")
        for secret in ("SENTINEL-BRIDGE", "SENTINEL-INTERNAL"):
            self.assertNotIn(secret, " ".join(seen["command"]))
            self.assertNotIn(secret, seen["prompt"])

    def test_coach_process_environment_drops_unrelated_credentials(self):
        import meter

        source = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "CODEX_HOME": "/Users/test/.codex",
            "TMPDIR": "/tmp/test",
            "LANG": "en_US.UTF-8",
            "OPENAI_API_KEY": "SENTINEL-OPENAI",
            "AWS_SECRET_ACCESS_KEY": "SENTINEL-AWS",
            "GITHUB_TOKEN": "SENTINEL-GITHUB",
            "SLACK_TOKEN": "SENTINEL-SLACK",
        }
        with mock.patch.dict(meter.os.environ, source, clear=True):
            env = meter.coach_client_environment(
                "/Users/test/.nvm/versions/node/v24/bin/codex",
            )

        self.assertEqual(env["HOME"], "/Users/test")
        self.assertEqual(env["CODEX_HOME"], "/Users/test/.codex")
        self.assertEqual(
            env["PATH"].split(os.pathsep)[0],
            "/Users/test/.nvm/versions/node/v24/bin",
        )
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("SLACK_TOKEN", env)

    def test_isolated_codex_home_falls_back_to_hard_links_not_user_skills(self):
        from token_meter.coach.codex import _isolated_environment

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source-home"
            source.mkdir()
            auth = source / "auth.json"
            auth.write_text('{"test":true}')
            (source / "skills").mkdir()
            isolated_root = Path(tmp) / "run"
            isolated_root.mkdir()
            with mock.patch(
                "token_meter.coach.codex.os.symlink", side_effect=OSError,
            ):
                env = _isolated_environment(
                    {"HOME": str(Path(tmp) / "home"), "CODEX_HOME": str(source)},
                    str(isolated_root),
                )

            isolated = Path(env["CODEX_HOME"])
            self.assertNotEqual(isolated, source)
            self.assertTrue((isolated / "auth.json").samefile(auth))
            self.assertFalse((isolated / "skills").exists())

    def test_application_composes_the_bundled_workspace_from_implementation_path(self):
        import meter

        previous = meter._COACH_SERVICE
        meter._COACH_SERVICE = None
        try:
            workspace = Path(meter.coach_service()._executor._workspace_source)
        finally:
            meter._COACH_SERVICE = previous

        self.assertEqual(
            workspace,
            Path(meter._SOURCE_ROOT, "token_meter", "coach", "workspace"),
        )
        self.assertTrue(
            (workspace / ".agents" / "skills" / "token-meter-coach" / "SKILL.md").is_file()
        )

    def test_process_wide_coach_service_initializes_once_under_concurrency(self):
        import meter

        previous = meter._COACH_SERVICE
        meter._COACH_SERVICE = None
        first_factory_entered = threading.Event()
        release_first_factory = threading.Event()
        second_factory_entered = threading.Event()
        created = []
        results = []

        def fake_service(**_kwargs):
            instance = object()
            created.append(instance)
            if len(created) == 1:
                first_factory_entered.set()
                self.assertTrue(release_first_factory.wait(2))
            else:
                second_factory_entered.set()
            return instance

        try:
            with mock.patch.object(meter, "CoachService", side_effect=fake_service):
                first = threading.Thread(
                    target=lambda: results.append(meter.coach_service()),
                )
                second = threading.Thread(
                    target=lambda: results.append(meter.coach_service()),
                )
                first.start()
                self.assertTrue(first_factory_entered.wait(2))
                second.start()
                second_factory_entered.wait(0.2)
                release_first_factory.set()
                first.join(2)
                second.join(2)
        finally:
            meter._COACH_SERVICE = previous

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(created), 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])

    def test_windows_mcp_batch_launcher_is_wrapped_by_the_command_processor(self):
        import meter

        command, arguments = meter.coach_mcp_invocation(
            r"C:\Token Meter\scripts\run-token-meter-mcp.cmd",
            platform_name="nt",
            environment={"COMSPEC": r"C:\Windows\System32\cmd.exe"},
        )

        self.assertEqual(command, r"C:\Windows\System32\cmd.exe")
        self.assertEqual(
            arguments,
            ("/d", "/s", "/c", r"C:\Token Meter\scripts\run-token-meter-mcp.cmd"),
        )

    def test_goal_draft_is_normalized_and_unknown_agent_fields_are_rejected(self):
        from token_meter.coach.codex import CodexCoach, CoachRunError

        def runner(*_args, **_kwargs):
            return {
                "returncode": 0,
                "events": "",
                "stderr": "",
                "result": json.dumps({
                    "message": "I drafted a measurable goal.",
                    "evidence": [],
                    "action": None,
                    "goal_draft": {
                        "metric": "retry_rate", "target_percent": 15,
                        "window_days": 14, "runtime": "all", "review_weekday": 4,
                        "weekly_enabled": True, "prompt": "SENTINEL-PRIVATE",
                    },
                    "reasoning": "SENTINEL-PRIVATE",
                }),
            }

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path=lambda: "/bin/codex",
                mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"),
                runner=runner,
                path_is_executable=lambda _path: True,
            )
            with self.assertRaises(CoachRunError) as raised:
                coach.run(self.valid_request())

        self.assertEqual(raised.exception.code, "invalid_output")

    def test_chat_card_suppresses_the_action_for_goal_drafts(self):
        # Break caught: an action competes with the single optional goal draft
        # in Tok's compact card.
        from token_meter.coach.codex import CodexCoach

        def runner(*_args, **_kwargs):
            return {
                "returncode": 0,
                "events": json.dumps({
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "server": "tokenmeter", "tool": "goal"},
                }) + "\n",
                "stderr": "",
                "result": json.dumps({
                    "message": "I drafted a measurable goal.",
                    "evidence": [{
                        "label": "Retry rate", "value": "20%",
                        "source": "Token Meter MCP",
                    }],
                    "action": {"kind": "reduce_context", "subject": "claude-opus-5"},
                    "goal_draft": {
                        "metric": "retry_rate", "target_percent": 15,
                        "window_days": 14, "runtime": "all", "review_weekday": 4,
                        "weekly_enabled": True,
                    },
                }),
            }

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path=lambda: "/bin/codex", mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"), runner=runner,
                path_is_executable=lambda _path: True,
            )
            result = coach.run(self.valid_request())

        self.assertIsNone(result["action"])
        self.assertEqual(result["goal_draft"]["metric"], "retry_rate")

    def test_chat_card_rejects_more_than_three_evidence_rows(self):
        # Break caught: a fourth evidence row escapes the compact card limit.
        from token_meter.coach.codex import _sanitize_chat, CoachRunError

        with self.assertRaises(CoachRunError) as raised:
            _sanitize_chat({
                "message": "Answer",
                "evidence": [{
                    "label": "Retry rate", "value": "20%",
                    "source": "Token Meter MCP",
                }] * 4,
                "action": None,
                "goal_draft": None,
            })

        self.assertEqual(raised.exception.code, "invalid_output")

    def test_answer_allows_worked_example_length_but_caps_it(self):
        # Break caught: the message cap drifts between the schema and the
        # sanitizer, or is too tight to hold a recommendation with a worked
        # example. The answer may fill MAX_ANSWER_CHARS and no more.
        from token_meter.coach.codex import (
            CHAT_SCHEMA, MAX_ANSWER_CHARS, _sanitize_chat, CoachRunError,
        )

        self.assertEqual(MAX_ANSWER_CHARS, 1_200)
        self.assertEqual(
            CHAT_SCHEMA["properties"]["message"]["maxLength"], MAX_ANSWER_CHARS,
        )

        at_limit = _sanitize_chat({
            "message": "x" * MAX_ANSWER_CHARS,
            "evidence": [],
            "action": None,
            "goal_draft": None,
        })
        self.assertEqual(len(at_limit["message"]), MAX_ANSWER_CHARS)

        with self.assertRaises(CoachRunError) as raised:
            _sanitize_chat({
                "message": "x" * (MAX_ANSWER_CHARS + 1),
                "evidence": [],
                "action": None,
                "goal_draft": None,
            })
        self.assertEqual(raised.exception.code, "invalid_output")

    def test_weekly_requires_observed_token_meter_mcp_use(self):
        from token_meter.coach.codex import CodexCoach, CoachRunError

        def runner(*_args, **_kwargs):
            return {
                "returncode": 0,
                "events": '{"type":"turn.completed"}\n',
                "stderr": "",
                "result": json.dumps({
                    "recommendation": "keep_course",
                    "evidence": [],
                }),
            }

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path=lambda: "/bin/codex",
                mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"),
                runner=runner,
                path_is_executable=lambda _path: True,
            )
            with self.assertRaises(CoachRunError) as raised:
                coach.run(self.valid_request("weekly"))

        self.assertEqual(raised.exception.code, "mcp_evidence_required")

    def test_agent_text_cannot_spoof_observed_mcp_use(self):
        from token_meter.coach.codex import CodexCoach, CoachRunError

        def runner(*_args, **_kwargs):
            return {
                "returncode": 0,
                "events": json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "I used the tokenmeter MCP server.",
                    },
                }) + "\n",
                "stderr": "",
                "result": json.dumps({
                    "recommendation": "keep_course",
                    "evidence": [],
                }),
            }

        with tempfile.TemporaryDirectory() as tmp:
            coach = CodexCoach(
                codex_path=lambda: "/bin/codex",
                mcp_command="/bin/token-meter-mcp",
                workspace_source=self.make_workspace(Path(tmp) / "source"),
                runner=runner,
                path_is_executable=lambda _path: True,
            )
            with self.assertRaises(CoachRunError) as raised:
                coach.run(self.valid_request("weekly"))

        self.assertEqual(raised.exception.code, "mcp_evidence_required")

    def test_maps_missing_cli_timeout_and_malformed_output_to_stable_codes(self):
        from token_meter.coach.codex import CodexCoach, CoachRunError

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp) / "source")
            missing = CodexCoach(
                codex_path=lambda: None,
                mcp_command="/bin/token-meter-mcp",
                workspace_source=workspace,
                path_is_executable=lambda _path: False,
            )
            self.assertEqual(missing.status()["status"], "cli_missing")
            with self.assertRaises(CoachRunError) as raised:
                missing.run(self.valid_request())
            self.assertEqual(raised.exception.code, "cli_missing")

            for runner, code in (
                (lambda *_a, **_k: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired("codex", 90)
                ), "timeout"),
                (lambda *_a, **_k: {
                    "returncode": 0, "events": "", "stderr": "",
                    "result": "not-json",
                }, "invalid_output"),
                (lambda *_a, **_k: {
                    "returncode": 3, "events": "", "stderr": "login required",
                    "result": "",
                }, "auth_required"),
            ):
                with self.subTest(code=code):
                    coach = CodexCoach(
                        codex_path=lambda: "/bin/codex",
                        mcp_command="/bin/token-meter-mcp",
                        workspace_source=workspace,
                        runner=runner,
                        path_is_executable=lambda _path: True,
                    )
                    with self.assertRaises(CoachRunError) as raised:
                        coach.run(self.valid_request())
                    self.assertEqual(raised.exception.code, code)


class CoachSkillContractTests(unittest.TestCase):
    def test_runtime_manifest_packages_the_bundled_coach_skill(self):
        import meter
        from token_meter.packaging import load_manifest, manifest_source_files

        root = Path(meter._SOURCE_ROOT)
        files = set(manifest_source_files(root, load_manifest(root / "runtime-manifest.txt")))

        self.assertIn(
            "token_meter/coach/workspace/.agents/skills/token-meter-coach/SKILL.md",
            files,
        )
        self.assertIn(
            "token_meter/coach/workspace/.agents/skills/token-meter-coach/agents/openai.yaml",
            files,
        )

    def test_skill_defines_the_goal_weekday_encoding(self):
        import meter

        skill = Path(
            meter._SOURCE_ROOT,
            "token_meter", "coach", "workspace", ".agents", "skills",
            "token-meter-coach", "SKILL.md",
        ).read_text(encoding="utf-8")

        self.assertIn(
            "Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, "
            "Saturday=5, Sunday=6",
            " ".join(skill.split()),
        )
        self.assertIn(
            "Set `weekly_enabled` to `true` only when the user explicitly asks",
            skill,
        )

    def test_skill_reuses_the_bounded_goal_projection_for_weekly_reviews(self):
        import meter

        skill = Path(
            meter._SOURCE_ROOT,
            "token_meter", "coach", "workspace", ".agents", "skills",
            "token-meter-coach", "SKILL.md",
        ).read_text(encoding="utf-8")

        self.assertIn("call `goal` with `focus=progress` first", skill)

    def test_bundled_agent_defines_tok_without_weakening_evidence_rules(self):
        import meter

        skill_root = Path(
            meter._SOURCE_ROOT,
            "token_meter", "coach", "workspace", ".agents", "skills",
            "token-meter-coach",
        )
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        agent = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        normalized_skill = " ".join(skill.split())

        for marker in (
            "# Tok — Master of tokens",
            "Every answer must name one of those things.",
            "Why the evidence supports it, including the material caveat.",
            "What to compare afterwards so the user can tell whether it worked.",
            "Never shame the user, score their productivity, or call usage wasteful.",
            "Use only the `tokenmeter` MCP tools for factual claims about usage.",
            "An answer that only describes where usage is concentrated is a failed answer",
            "Never restrict your analysis to the page the user is looking at.",
            "It is not a filter.",
            "price several levers before you recommend one",
            "`flagged_tools` returns the ones it flagged with the reason, a `user_can_disable` flag, and an `actionable` flag",
            "Prefer a flagged tool where `actionable` is true",
            "A flagged tool where `actionable` is false is a runtime built-in the user cannot disable, narrow, or reconfigure",
            "A flagged tool with `actionable` false is never a valid recommendation.",
            "`reduce_reasoning` when the subject is reasoning effort on a reasoning-capable model",
            "query `reasoning_tokens` alongside `output_tokens` by `model`",
            "lowering the reasoning effort or thinking budget is a real lever",
            "Cite the measured share, name the configuration change in plain words",
            "Treat `reasoning_tokens` as unavailable, not zero",
            "prefer a lever that is trending up over one that is already stable",
            "Comparing two models by average cost per execution is **not** defensible on its own",
            "say roughly how large it is in dollars or tokens over the window you measured",
            "For a cost question, compare the levers in dollars, not raw token counts.",
            "the largest input-token volume is not the largest cost",
            "Total input tokens are also not the context carried per execution",
            "A concrete worked example of how to apply the change",
            "never with the user's actual prompts, session titles, project names, or file paths",
            "For a narrow question that asks for a single number or one fact, answer in one or two sentences",
            "When a lever has a real worked example, this is the shape",
            "`action.subject` may contain only a name a Token Meter MCP response gave you.",
            "Use `review_skill_packs` only when the subject is a skill pack.",
            "Use `review_flagged_tool` when the subject is a tool or MCP server",
            "Treat `null`, zero covered rows, and incomplete coverage as unavailable",
        ):
            self.assertIn(" ".join(marker.split()), normalized_skill)
        self.assertIn('display_name: "Tok — Master of tokens"', agent)
        self.assertIn("Use $token-meter-coach", agent)


class CoachWeeklyTests(CoachPersistenceTests):
    class Executor:
        def __init__(self, result=None, error=None):
            self.result = result or {
                "recommendation": "reduce_retries",
                "evidence": [{
                    "label": "Retry rate", "value": "20%",
                    "source": "Token Meter MCP",
                }],
            }
            self.error = error
            self.calls = []

        def status(self):
            return {"available": True, "status": "ready"}

        def run(self, request):
            self.calls.append(request)
            if self.error:
                raise self.error
            return self.result

    def service_with_executor(self, executor, local_now):
        from token_meter.coach.service import CoachService

        def write(value):
            changed = value != self.saved
            self.saved = value
            self.writes.append(value)
            return {"ok": True, "changed": changed}

        return CoachService(
            read_store=lambda: self.saved,
            write_store=write,
            stats=self.stats,
            executor=executor,
            now=lambda: local_now.timestamp(),
            local_now=lambda: local_now,
        )

    def goal_value(self, enabled=True):
        return {
            "metric": "retry_rate", "target_percent": 10,
            "window_days": 7, "runtime": "all", "review_weekday": 0,
            "weekly_enabled": enabled,
        }

    def test_cancel_delegates_idempotently_and_ask_finish_race_stays_a_noop(self):
        # Break caught: cancellation is not exposed by the service, or a late
        # cancel can target a completed interactive run.
        now = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)

        class Executor(self.Executor):
            def __init__(self):
                super().__init__(result={
                    "message": "Answer", "evidence": [], "action": None,
                    "goal_draft": None,
                })
                self.cancel_results = [
                    {"ok": True, "changed": True},
                    {"ok": True, "changed": False},
                ]

            def cancel(self):
                return self.cancel_results.pop(0)

        executor = Executor()
        service = self.service_with_executor(executor, now)
        self.assertEqual(service.cancel(), {"ok": True, "changed": True})
        self.assertTrue(service.ask({
            "message": "How am I doing?", "history": [], "page": {"route": "sessions"},
        })["ok"])
        self.assertEqual(service.cancel(), {"ok": True, "changed": False})

    def test_state_projects_only_allowlisted_agent_activity(self):
        # Break caught: service state forwards private adapter activity fields.
        now = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)

        class Executor(self.Executor):
            def status(self):
                return {
                    "available": True, "status": "running",
                    "activity": {
                        "stage": "checking_evidence", "started_at": 1.0,
                        "cancellable": True, "prompt": "SENTINEL-PRIVATE",
                        "tool": "shell", "reads": 10_000,
                    },
                }

        state = self.service_with_executor(Executor(), now).state()
        self.assertEqual(state["agent"]["activity"], {
            "stage": "checking_evidence", "started_at": 1.0, "cancellable": True,
            "tool": None, "reads": 99,
        })

    def test_state_survives_an_unhashable_activity_tool_value(self):
        # Break caught: a non-string tool raises TypeError against the allowlist
        # set and turns /coach/state into a 500.
        now = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)

        class Executor(self.Executor):
            def status(self):
                return {
                    "available": True, "status": "running",
                    "activity": {
                        "stage": "reading_token_meter", "started_at": 1.0,
                        "cancellable": True, "tool": ["usage"], "reads": None,
                    },
                }

        activity = self.service_with_executor(Executor(), now).state()["agent"]["activity"]
        self.assertIsNone(activity["tool"])
        self.assertEqual(activity["reads"], 0)

    def test_state_forwards_only_an_allowlisted_activity_tool_name(self):
        # Break caught: an arbitrary tool label reaches the browser as stage copy.
        now = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)

        class Executor(self.Executor):
            def status(self):
                return {
                    "available": True, "status": "running",
                    "activity": {
                        "stage": "reading_token_meter", "started_at": 1.0,
                        "cancellable": True, "tool": "usage", "reads": 2,
                    },
                }

        activity = self.service_with_executor(Executor(), now).state()["agent"]["activity"]
        self.assertEqual(activity["tool"], "usage")
        self.assertEqual(activity["reads"], 2)

    def test_due_scheduler_runs_once_per_local_iso_week_and_persists_no_prose(self):
        monday = datetime.datetime(
            2026, 9, 14, 9, 30, tzinfo=datetime.timezone(
                datetime.timedelta(hours=5, minutes=30)
            ),
        )
        executor = self.Executor()
        service = self.service_with_executor(executor, monday)
        service.save_goal(self.goal_value())

        first = service.review_if_due()
        second = service.review_if_due()

        self.assertTrue(first["ran"])
        self.assertFalse(second["ran"])
        self.assertEqual(second["reason"], "already_completed")
        self.assertEqual(len(executor.calls), 1)
        weekly = self.saved["weekly"]
        self.assertEqual(weekly["week_key"], "2026-W38")
        self.assertEqual(weekly["recommendation"], "reduce_retries")
        self.assertEqual(weekly["snapshot"]["metric"], "retry_rate")
        self.assertNotIn("evidence", weekly)
        self.assertNotIn("Retry rate", json.dumps(weekly))

    def test_disabled_or_not_due_scheduler_is_a_noop_but_manual_run_is_allowed(self):
        tuesday = datetime.datetime(2026, 9, 15, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor()
        service = self.service_with_executor(executor, tuesday)
        service.save_goal({**self.goal_value(enabled=False), "review_weekday": 4})

        disabled = service.review_if_due()
        manual = service.run_weekly(manual=True)

        self.assertEqual(disabled, {"ok": True, "ran": False, "reason": "disabled"})
        self.assertTrue(manual["ran"])
        self.assertEqual(len(executor.calls), 1)

    def test_failed_weekly_run_keeps_prior_success_and_records_only_error_code(self):
        from token_meter.coach.codex import CoachRunError

        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        successful = self.Executor()
        service = self.service_with_executor(successful, monday)
        service.save_goal(self.goal_value())
        service.run_weekly(manual=True)
        previous = dict(self.saved["weekly"])

        failure = self.Executor(error=CoachRunError("mcp_unavailable"))
        failed_service = self.service_with_executor(failure, monday)
        result = failed_service.run_weekly(manual=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "mcp_unavailable")
        self.assertEqual(self.saved["weekly"]["recommendation"], previous["recommendation"])
        self.assertEqual(self.saved["weekly"]["week_key"], previous["week_key"])
        self.assertEqual(self.saved["weekly"]["last_error"], "mcp_unavailable")
        self.assertNotIn("error", self.saved["weekly"])

    def test_automatic_weekly_failure_is_not_retried_until_next_week(self):
        from token_meter.coach.codex import CoachRunError

        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor(error=CoachRunError("agent_failed"))
        service = self.service_with_executor(executor, monday)
        service.save_goal(self.goal_value())

        first = service.review_if_due()
        second = service.review_if_due()

        self.assertEqual(first["error_code"], "agent_failed")
        self.assertEqual(
            second, {"ok": True, "ran": False, "reason": "already_attempted"},
        )
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            datetime.datetime.fromtimestamp(
                service.state()["next_review_at"], tz=monday.tzinfo,
            ),
            monday.replace(day=28, hour=0, minute=0),
        )

    def test_automatic_evidence_failure_is_not_retried_until_next_week(self):
        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor()
        service = self.service_with_executor(executor, monday)
        service.save_goal(self.goal_value())
        service._stats = mock.Mock(side_effect=RuntimeError("SENTINEL-PRIVATE"))

        first = service.review_if_due()
        second = service.review_if_due()

        self.assertEqual(
            first, {"ok": False, "ran": False, "error_code": "mcp_unavailable"},
        )
        self.assertEqual(
            second, {"ok": True, "ran": False, "reason": "already_attempted"},
        )
        self.assertEqual(service._stats.call_count, 1)
        self.assertEqual(executor.calls, [])
        self.assertEqual(self.saved["weekly"]["last_error"], "mcp_unavailable")
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(self.saved))

    def test_weekly_evidence_failure_records_a_bounded_attempt(self):
        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor()
        service = self.service_with_executor(executor, monday)
        service.save_goal(self.goal_value())
        service._stats = mock.Mock(side_effect=RuntimeError("SENTINEL-PRIVATE"))

        result = service.run_weekly(manual=True)

        self.assertEqual(
            result, {"ok": False, "ran": False, "error_code": "mcp_unavailable"},
        )
        self.assertEqual(self.saved["weekly"]["last_error"], "mcp_unavailable")
        self.assertIn("last_attempt_at", self.saved["weekly"])
        self.assertNotIn("SENTINEL-PRIVATE", json.dumps(self.saved))
        self.assertEqual(executor.calls, [])

    def test_manual_retry_can_recheck_evidence_after_a_bounded_failure(self):
        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor()
        service = self.service_with_executor(executor, monday)
        service.save_goal(self.goal_value())
        good_stats = self.stats
        service._stats = mock.Mock(side_effect=RuntimeError("temporary"))

        first = service.run_weekly(manual=True)
        service._stats = good_stats
        second = service.run_weekly(manual=True)

        self.assertEqual(first["error_code"], "mcp_unavailable")
        self.assertTrue(second["ran"])
        self.assertEqual(len(executor.calls), 1)

    def test_weekly_result_is_discarded_if_the_active_goal_changes_mid_run(self):
        monday = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        base_executor = self.Executor

        class ReplacingExecutor(self.Executor):
            service = None

            def run(inner_self, request):
                result = base_executor.run(inner_self, request)
                inner_self.service.save_goal({
                    "metric": "context_peak", "target_percent": 15,
                    "window_days": 14, "runtime": "codex", "review_weekday": 4,
                    "weekly_enabled": True,
                })
                return result

        executor = ReplacingExecutor()
        service = self.service_with_executor(executor, monday)
        executor.service = service
        service.save_goal(self.goal_value())

        result = service.run_weekly(manual=True)

        self.assertEqual(
            result, {"ok": False, "ran": False, "error_code": "goal_changed"},
        )
        self.assertEqual(self.saved["goal"]["metric"], "context_peak")
        self.assertEqual(self.saved["weekly"], {})

    def test_chat_uses_the_active_structured_goal_and_returns_stable_failures(self):
        from token_meter.coach.codex import CoachRunError

        now = datetime.datetime(2026, 9, 21, 8, tzinfo=datetime.timezone.utc)
        executor = self.Executor(result={
            "message": "Answer", "evidence": [],
            "action": None, "goal_draft": None,
        })
        service = self.service_with_executor(executor, now)
        service.save_goal(self.goal_value())

        answered = service.ask({
            "message": "How am I doing?", "history": [],
            "page": {"route": "efficiency"},
        })

        self.assertTrue(answered["ok"])
        self.assertEqual(executor.calls[-1]["mode"], "chat")
        self.assertEqual(executor.calls[-1]["goal"]["metric"], "retry_rate")
        self.assertNotIn("baseline", executor.calls[-1]["goal"])

        executor.error = CoachRunError("busy")
        failed = service.ask({
            "message": "Again", "history": [], "page": {"route": "sessions"},
        })
        self.assertEqual(failed, {"ok": False, "error_code": "busy"})


if __name__ == "__main__":
    unittest.main()
