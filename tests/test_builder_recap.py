import unittest
import datetime
import http.client
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest import mock
import meter
from token_meter.services import git_delivery as git_delivery_service_module
from tests.meter_support import _TrackingSqliteConnection


class GitDeliveryLedgerConnectionTests(unittest.TestCase):
    """The ledger must close every sqlite connection it opens."""

    def test_ledger_operations_close_every_connection(self):
        from token_meter.services.git_delivery import GitDeliveryLedger

        created = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            tracker = _TrackingSqliteConnection(real_connect(*args, **kwargs))
            created.append(tracker)
            return tracker

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ledger.sqlite3")
            with mock.patch.object(
                    git_delivery_service_module.sqlite3, "connect",
                    side_effect=tracking_connect):
                ledger = GitDeliveryLedger(path)
                ledger.record("repo-key", "object-key", 1, 2, 3)
                ledger.mark_seen("repo-key", "object-key")
                self.assertTrue(ledger.has_seen("repo-key", "object-key"))
                ledger.rows()
                ledger.daily_rows(["repo-key"], "2026-09-01", "2026-09-30")
                ledger.map_project("project-key", "repo-key")
                ledger.repo_key_for_project("project-key")
                ledger.set_repository_coverage("repo-key", 12, 1)
                ledger.repository_coverage("repo-key")
                ledger.set_last_checked(123)
                ledger.last_checked()
                ledger.baseline_at()
                ledger.coalesce_repository("repo-key", "canonical-key")
                ledger.clear(123)
        self.assertTrue(created, "ledger operations should open connections")
        leaked = [connection for connection in created if not connection.closed]
        self.assertEqual(
            [], leaked,
            f"{len(leaked)} of {len(created)} ledger connections were never closed",
        )


class BuilderRecapDomainTests(unittest.TestCase):
    def test_antigravity_is_a_public_recap_runtime(self):
        from token_meter.domain.builder_recap import _safe_runtime

        self.assertEqual(_safe_runtime("Antigravity"), "Antigravity")
        self.assertEqual(_safe_runtime("Antigravity private"), "")
        self.assertEqual(_safe_runtime("Grok"), "Grok")

    TODAY = datetime.date(2026, 9, 13)

    def row(self, last, daily=(), **values):
        return {
            "last": last,
            "runtime": "Codex",
            "duration_s": 0,
            "duration_available": False,
            "output_tokens": 0,
            "availability": {},
            "_model_daily": list(daily),
            "_performance_samples": [],
            "_tool_evidence": {},
            **values,
        }

    def recap(self, rows, days=7, git_days=(), previous_git_lines=None,
              previous_git_commits=None):
        from token_meter.domain.builder_recap import build_builder_recap
        return build_builder_recap(
            rows, None if git_days is None else list(git_days), days,
            today=self.TODAY, generated_at=1_757_718_400,
            previous_git_lines=previous_git_lines,
            previous_git_commits=previous_git_commits,
        )

    def supporting(self, result, stat_id):
        return next(stat for stat in result["supporting"] if stat["id"] == stat_id)

    def spotlight(self, result, stat_id):
        return next(stat for stat in result["spotlights"] if stat["id"] == stat_id)

    def test_windows_are_inclusive_equal_and_local_calendar_based(self):
        result = self.recap([], days=7)
        self.assertEqual(result["current"], {
            "start_day": "2026-09-07", "end_day": "2026-09-13",
        })
        self.assertEqual(result["previous"], {
            "start_day": "2026-08-31", "end_day": "2026-09-06",
        })
        self.assertEqual(len(result["activity_days"]), 7)

    def test_invalid_range_is_rejected_before_aggregation(self):
        with self.assertRaisesRegex(ValueError, "7, 30, or 90"):
            self.recap([], days=14)

    def test_30_and_90_day_windows_remain_equal_length_and_inclusive(self):
        recap_30 = self.recap([], days=30)
        recap_90 = self.recap([], days=90)
        self.assertEqual(recap_30["current"], {
            "start_day": "2026-08-15", "end_day": "2026-09-13",
        })
        self.assertEqual(recap_30["previous"], {
            "start_day": "2026-07-16", "end_day": "2026-08-14",
        })
        self.assertEqual(recap_90["current"], {
            "start_day": "2026-06-16", "end_day": "2026-09-13",
        })
        self.assertEqual(recap_90["previous"], {
            "start_day": "2026-03-18", "end_day": "2026-06-15",
        })

    def test_core_aggregates_use_day_level_paired_coverage(self):
        paired = lambda day, output, cost: {
            "day": day, "model": "gpt-5", "output_tokens": output,
            "cost_covered_output_tokens": output, "cost_covered_cost": cost,
        }
        mixed = {
            "day": "2026-09-13", "model": "gpt-5", "output_tokens": 900,
            "cost_covered_output_tokens": 0, "cost_covered_cost": 9,
        }
        result = self.recap([
            self.row("2026-09-13T18:00:00", [
                paired("2026-09-13", 200, 1), paired("2026-08-31", 100, 1),
            ]),
            self.row("2026-09-12T18:00:00", [
                paired("2026-09-12", 100, 0.5), paired("2026-09-10", 100, 0.5),
            ]),
            self.row("2026-09-01T18:00:00", [mixed]),
        ])

        self.assertEqual(result["activity_days"][-1], {
            "day": "2026-09-13", "recorded_session": True,
        })
        self.assertEqual(self.spotlight(result, "build_streak")["current"], 2)
        self.assertEqual(self.spotlight(result, "efficiency_change")["current"], 200.0)
        self.assertEqual(self.spotlight(result, "efficiency_change")["previous"], 100.0)
        self.assertEqual(self.spotlight(result, "efficiency_change")["delta_pct"], 100.0)

    def test_covered_spend_sums_paired_cost_and_stays_unavailable_when_absent(self):
        paired = lambda day, output, cost: {
            "day": day, "model": "gpt-5", "output_tokens": output,
            "cost_covered_output_tokens": output, "cost_covered_cost": cost,
        }
        result = self.recap([
            self.row("2026-09-13T18:00:00", [
                paired("2026-09-13", 200, 1.25), paired("2026-09-12", 100, 0.75),
                paired("2026-08-31", 100, 2.5),
            ]),
        ])

        spend = self.supporting(result, "covered_spend")
        self.assertEqual(spend["family"], "cost")
        self.assertTrue(spend["available"])
        self.assertAlmostEqual(spend["current"], 2.0)
        self.assertAlmostEqual(spend["previous"], 2.5)
        self.assertEqual(spend["unit"], "USD")
        self.assertEqual(spend["basis"], "paired cost-covered output and cost")
        self.assertEqual(spend["sample_count"], 2)

        # Output with no paired cost must not become a measured zero.
        uncovered = self.recap([
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": "gpt-5", "output_tokens": 900,
                "cost_covered_output_tokens": 0, "cost_covered_cost": 0,
            }]),
        ])
        self.assertEqual(
            [stat["id"] for stat in uncovered["supporting"]
             if stat["id"] == "covered_spend"],
            [],
        )

    def test_every_supporting_stat_id_has_a_deterministic_sort_position(self):
        paired = lambda day, output, cost: {
            "day": day, "model": "gpt-5", "output_tokens": output,
            "cost_covered_output_tokens": output, "cost_covered_cost": cost,
        }
        result = self.recap([
            self.row("2026-09-13T18:00:00", [
                paired("2026-09-13", 200, 1.25), paired("2026-08-31", 100, 2.5),
            ], duration_s=120, duration_available=True, _performance_samples=[
                {"day": "2026-09-13", "output_tokens": 400, "generation_s": 4},
                {"day": "2026-08-31", "output_tokens": 300, "generation_s": 5},
            ], _tool_evidence={"tools": [{"daily": [
                {"day": "2026-09-13", "calls": 12},
            ]}]}),
        ], git_days=[
            {"day": "2026-09-13", "available": True, "active": True,
             "changed_lines": 400},
        ])

        ordered = [stat["id"] for stat in result["supporting"]]
        self.assertEqual(ordered, sorted(set(ordered), key=ordered.index))
        self.assertIn("covered_spend", ordered)

    def test_efficiency_comparison_is_unavailable_with_prior_zero_cost(self):
        result = self.recap([
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": "gpt-5", "output_tokens": 100,
                "cost_covered_output_tokens": 100, "cost_covered_cost": 1,
            }, {
                "day": "2026-08-31", "model": "gpt-5", "output_tokens": 100,
                "cost_covered_output_tokens": 100, "cost_covered_cost": 0,
            }]),
        ])

        efficiency = self.spotlight(result, "efficiency_change")
        self.assertFalse(efficiency["available"])
        self.assertIsNone(efficiency["delta_pct"])

    def test_malformed_non_finite_evidence_is_ignored(self):
        result = self.recap([self.row("not-a-date", [{
            "day": "2026-09-13", "model": "gpt-5", "output_tokens": 100,
            "cost_covered_output_tokens": float("nan"),
            "cost_covered_cost": float("inf"),
        }], mtime=float("inf"), _performance_samples=[{
            "day": "2026-09-13", "output_tokens": float("inf"),
            "duration_s": float("nan"),
        }], _tool_evidence={"tools": [{"daily": [{
            "day": "2026-09-13", "calls": float("inf"),
        }]}]})])

        self.assertEqual(result["activity_days"][-1], {
            "day": "2026-09-13", "recorded_session": True,
        })
        self.assertFalse(self.spotlight(result, "efficiency_change")["available"])
        self.assertNotIn("output_pace", [stat["id"] for stat in result["supporting"]])
        self.assertEqual(result["coverage"]["tools"]["eligible"], 0)

    def test_float_conversion_overflow_is_ignored_as_unavailable_evidence(self):
        class OverflowingFloat:
            def __float__(self):
                raise OverflowError("malformed numeric evidence")

        overflow = OverflowingFloat()
        result = self.recap([
            self.row("not-a-date", [], mtime=overflow),
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": "gpt-5",
                "cost_covered_output_tokens": overflow,
                "cost_covered_cost": overflow,
            }]),
        ])

        self.assertEqual(result["activity_days"][-1], {
            "day": "2026-09-13", "recorded_session": True,
        })
        self.assertFalse(self.spotlight(result, "efficiency_change")["available"])

    def test_git_coverage_requires_explicit_available_evidence_and_counts_only_active_days(self):
        unavailable = self.recap([], git_days=None)
        no_measured_rows = self.recap([], git_days=())
        measured_inactive = self.recap([], git_days=[
            {"day": f"2026-09-{day:02d}", "available": True, "active": False}
            for day in range(7, 14)
        ] + [{"day": "2026-09-12", "available": False, "active": True}])

        self.assertFalse(unavailable["coverage"]["git"]["available"])
        self.assertFalse(unavailable["coverage"]["git"]["projection_available"])
        self.assertFalse(no_measured_rows["coverage"]["git"]["available"])
        self.assertTrue(no_measured_rows["coverage"]["git"]["projection_available"])
        self.assertEqual(no_measured_rows["coverage"]["git"]["eligible"], 0)
        self.assertFalse(self.spotlight(no_measured_rows, "lines_pushed")["available"])
        self.assertNotIn("delivery_active_days", [
            stat["id"] for stat in no_measured_rows["supporting"]
        ])
        self.assertTrue(measured_inactive["coverage"]["git"]["available"])
        self.assertTrue(measured_inactive["coverage"]["git"]["projection_available"])
        self.assertEqual(measured_inactive["coverage"]["git"]["eligible"], 7)
        self.assertEqual(
            self.supporting(measured_inactive, "delivery_active_days")["current"], 0
        )
        delivery = self.supporting(measured_inactive, "delivery_active_days")
        self.assertIsNone(delivery["previous"])
        self.assertIsNone(delivery["delta"])

    def test_lines_pushed_sums_only_measured_nonnegative_integer_change_volume(self):
        result = self.recap([], previous_git_lines=120_000, git_days=[
            {"day": "2026-09-13", "available": True, "active": True,
             "changed_lines": 184_392},
            {"day": "2026-09-12", "available": True, "active": True,
             "changed_lines": True},
            {"day": "2026-09-11", "available": True, "active": True,
             "changed_lines": -1},
            {"day": "2026-09-10", "available": True, "active": True,
             "changed_lines": 1.5},
            {"day": "2026-09-09", "available": True, "active": True,
             "changed_lines": float("inf")},
            {"day": "2026-09-08", "available": True, "active": True,
             "changed_lines": "999999"},
            {"day": "2026-09-08", "available": True, "active": True,
             "changed_lines": 10 ** 1000},
            {"day": "2026-09-07", "available": False, "active": True,
             "changed_lines": 999_999},
        ])

        lines = self.spotlight(result, "lines_pushed")
        self.assertTrue(lines["available"])
        self.assertEqual(lines["current"], 184_392)
        self.assertEqual(lines["previous"], 120_000)
        self.assertEqual(lines["delta"], 64_392)
        self.assertAlmostEqual(lines["delta_pct"], 53.66, places=2)
        self.assertEqual(lines["unit"], "lines")
        self.assertEqual(
            lines["basis"],
            "added plus deleted text lines from successful local pushes",
        )
        self.assertEqual(lines["sample_count"], 1)

    def test_commits_pushed_sums_only_measured_nonnegative_integer_commit_counts(self):
        result = self.recap([], previous_git_commits=94, git_days=[
            {"day": "2026-09-13", "available": True, "active": True,
             "changed_lines": 900, "commits": 7},
            {"day": "2026-09-12", "available": True, "active": True,
             "changed_lines": 400, "commits": 5},
            {"day": "2026-09-11", "available": True, "active": True,
             "changed_lines": 300, "commits": True},
            {"day": "2026-09-10", "available": True, "active": True,
             "changed_lines": 200, "commits": -1},
            {"day": "2026-09-09", "available": True, "active": True,
             "changed_lines": 100, "commits": 2.5},
            {"day": "2026-09-08", "available": True, "active": True,
             "changed_lines": 100, "commits": "12"},
            {"day": "2026-09-07", "available": False, "active": True,
             "changed_lines": 100, "commits": 999},
        ])

        commits = self.spotlight(result, "commits_pushed")
        self.assertTrue(commits["available"])
        self.assertEqual(commits["current"], 12)
        self.assertEqual(commits["previous"], 94)
        self.assertEqual(commits["delta"], -82)
        self.assertEqual(commits["unit"], "commits")
        self.assertEqual(
            commits["basis"],
            "own non-merge commits introduced by successful local pushes",
        )
        self.assertEqual(commits["sample_count"], 2)

    def test_commits_pushed_is_unavailable_without_measured_commit_evidence(self):
        missing_projection = self.recap([], git_days=None)
        no_commit_field = self.recap([], git_days=[
            {"day": "2026-09-13", "available": True, "active": True,
             "changed_lines": 120},
        ])

        for result in (missing_projection, no_commit_field):
            commits = self.spotlight(result, "commits_pushed")
            self.assertFalse(commits["available"])
            self.assertIsNone(commits["current"])
            self.assertEqual(commits["sample_count"], 0)
        self.assertTrue(
            self.spotlight(no_commit_field, "lines_pushed")["available"]
        )

    def test_public_labels_reject_paths_urls_controls_and_credentialish_values(self):
        sentinels = ("/private/repo", "https://secret.example", "sk-live-secret",
                     "api_key=secret", "builder@example.com", "<private>", "bad\x00label",
                     "Untrusted runtime narrative", "Untrusted model narrative",
                     "Please upload your credentials")
        result = self.recap([
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": sentinels[0], "executions": 9,
            }, {
                "day": "2026-09-12", "model": "gpt-5.2-codex", "executions": 3,
            }], runtime=sentinels[1], _tool_evidence={"tools": [
                {"name": "read_file", "display": sentinels[2], "daily": [{
                    "day": "2026-09-13", "calls": 9,
                }]},
                {"name": "read_file", "display": sentinels[9], "daily": [{
                    "day": "2026-09-12", "calls": 3,
                }]},
            ]}),
            self.row("2026-09-13T17:00:00", [{
                "day": "2026-09-13", "model": sentinels[3], "executions": 9,
            }], runtime=sentinels[4]),
            self.row("2026-09-13T16:00:00", [{
                "day": "2026-09-13", "model": sentinels[5], "executions": 9,
            }], runtime=sentinels[6]),
            self.row("2026-09-11T16:00:00", [{
                "day": "2026-09-11", "model": "gpt-5.2-codex", "executions": 3,
            }], runtime="Codex"),
            self.row("2026-09-10T16:00:00", [{
                "day": "2026-09-10", "model": sentinels[8], "executions": 3,
            }], runtime=sentinels[7]),
        ])

        payload = json.dumps(result, sort_keys=True)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, payload)
        self.assertIn("gpt-5.2-codex", payload)
        self.assertIn("Read file", payload)

    def test_public_model_projection_rejects_project_like_identifiers(self):
        private_models = (
            "customer-acme-project", "project-alpha", "repo-token-meter",
            "workspace-client-a", "internal-app", "my-secret-build",
            "gpt-5.6-customer-acme-project", "claude-project-alpha",
            "gpt5customer", "qwen3customer", "llama3project",
            "gemma3tenant", "starcoder2internal", "gpt5acme",
        )
        daily = [{
            "day": "2026-09-13", "model": model, "executions": 9,
        } for model in private_models]
        daily.append({
            "day": "2026-09-13", "model": "gpt-5.6-sol", "executions": 3,
        })

        result = self.recap([
            self.row("2026-09-13T18:00:00", daily, runtime="Codex"),
        ])

        payload = json.dumps(result, sort_keys=True)
        for model in private_models:
            self.assertNotIn(model, payload)
        self.assertIn("gpt-5.6-sol", payload)

    def test_public_model_projection_preserves_versioned_family_labels(self):
        public_models = (
            "qwen3-coder", "starcoder2", "llama3.3", "gemma3",
            "deepseek-v3", "o3", "claude-sonnet-4-6", "gpt-5.6-sol",
        )
        result = self.recap([
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": model, "executions": 1,
            } for model in public_models], runtime="Codex"),
        ])

        self.assertEqual(
            {row["model"] for row in result["stack"]["models"]},
            set(public_models),
        )

    def test_output_pace_models_and_dated_tools_are_aggregated_by_period(self):
        result = self.recap([
            self.row("2026-09-13T18:00:00", [{
                "day": "2026-09-13", "model": "gpt-5.5",
            }], runtime="Codex", _performance_samples=[{
                "day": "2026-09-13", "output_tokens": 300, "duration_s": 30,
            }, {
                "day": "2026-08-31", "output_tokens": 100, "duration_s": 20,
            }], _tool_evidence={"tools": [{"name": "read_file", "display": "Read file", "daily": [{
                "day": "2026-09-13", "calls": 4,
            }, {"day": "2026-08-31", "calls": 2}]}]}),
            self.row("2026-09-12T18:00:00", [{
                "day": "2026-09-12", "model": "gpt-5.5",
            }], runtime="Claude"),
        ])

        pace = self.supporting(result, "output_pace")
        self.assertEqual(pace["current"], 10.0)
        self.assertEqual(pace["previous"], 5.0)
        self.assertEqual(pace["delta_pct"], 100.0)
        self.assertEqual(result["stack"]["models"], [
            {"runtime": "Claude", "model": "gpt-5.5"},
            {"runtime": "Codex", "model": "gpt-5.5"},
        ])
        self.assertEqual(result["coverage"]["tools"]["eligible"], 1)

    def test_usage_rankings_are_capped_scoped_and_private(self):
        rows = []
        fixtures = [
            ("Codex", "gpt-5.3-codex", (4, 3, 2, 1)),
            ("Claude", "gpt-5.5", (3, 2, 1)),
            ("Cursor", "cursor-small", (2, 2)),
            ("OpenCode", "gpt-5.5", (3,)),
        ]
        serial = 0
        for runtime, model, executions in fixtures:
            for count in executions:
                serial += 1
                day = 13 - ((serial - 1) % 7)
                rows.append(self.row(
                    f"2026-09-{day:02d}T12:00:00",
                    [{"day": f"2026-09-{day:02d}", "model": model,
                      "executions": count}],
                    id=f"private-session-{serial}", runtime=runtime,
                ))

        result = self.recap(rows)

        self.assertEqual(result["usage"]["agents"], [
            {"label": "Codex", "count": 4, "share": 40.0},
            {"label": "Claude", "count": 3, "share": 30.0},
            {"label": "Cursor", "count": 2, "share": 20.0},
        ])
        self.assertEqual(result["usage"]["models"], [
            {"label": "gpt-5.3-codex", "runtime": "Codex",
             "count": 10, "share": 43.47826086956522},
            {"label": "gpt-5.5", "runtime": "Claude",
             "count": 6, "share": 26.08695652173913},
            {"label": "cursor-small", "runtime": "Cursor",
             "count": 4, "share": 17.391304347826086},
        ])
        serialized = json.dumps(result["usage"], sort_keys=True)
        self.assertNotIn("private-session", serialized)
        self.assertNotIn("OpenCode", serialized)

    def test_spotlight_eligibility_uses_safe_period_evidence(self):
        def daily(day, model, executions=1, output=0, cost=0):
            return {"day": day, "model": model, "executions": executions,
                    "output_tokens": output, "cost_covered_output_tokens": output,
                    "cost_covered_cost": cost}

        result = self.recap([
            self.row("2026-09-13T18:00:00", [
                daily("2026-09-13", "gpt-5", 5, 900, 3),
                daily("2026-09-12", "gpt-5", 2, 100, 1),
            ], id="session-secret", runtime="Codex", duration_s=50,
                wall_duration_s=9_999, duration_available=True,
                _performance_samples=[
                    {"day": "2026-09-13", "model": "gpt-5", "output_tokens": 600, "generation_s": 30},
                    {"day": "2026-09-12", "model": "gpt-5", "output_tokens": 500, "generation_s": 20},
                    {"day": "2026-09-11", "model": "gpt-5", "output_tokens": 400, "generation_s": 10},
                ], _tool_evidence={"tools": [
                    {"name": "read_file", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 8}]},
                    {"name": "Token Meter diagnostics", "daily": [{"day": "2026-09-13", "calls": 99}]},
                ]}),
            self.row("2026-09-13T17:00:00", [daily("2026-09-13", "claude", 5, 600, 3)],
                id="codex-second", runtime="Codex", duration_s=10, duration_available=True),
            self.row("2026-09-12T17:00:00", [daily("2026-09-12", "claude", 5, 600, 3)],
                id="claude-first", runtime="Claude", duration_s=20, duration_available=True),
            self.row("2026-09-11T17:00:00", [daily("2026-09-11", "unknown-model", 9)],
                id="claude-second", runtime="unknown", duration_s=30, duration_available=False,
                wall_duration_s=10_000),
            self.row("2026-09-10T17:00:00", [daily("2026-09-10", "unknown", 9)],
                runtime="unknown", id="unknown-session"),
        ])

        self.assertEqual({row["id"] for row in result["spotlights"]}, {
            "efficiency_record", "efficiency_change", "marathon_session",
            "speed_champion", "build_streak", "daily_driver", "go_to_model",
            "lines_pushed", "commits_pushed", "biggest_build", "tool_mvp",
            "stack_explorer",
        })
        self.assertEqual(self.spotlight(result, "marathon_session")["current"], 50)
        self.assertEqual(self.spotlight(result, "speed_champion")["current"], 25.0)
        self.assertEqual(self.spotlight(result, "speed_champion")["leaders"], [
            {"runtime": "Codex", "model": "gpt-5"},
        ])
        self.assertEqual(self.spotlight(result, "daily_driver")["leaders"], [
            {"runtime": "Codex"},
        ])
        self.assertEqual(self.spotlight(result, "go_to_model")["leaders"], [
            {"runtime": "Codex", "model": "gpt-5"},
        ])
        biggest = self.spotlight(result, "biggest_build")
        self.assertEqual(biggest["current"], 1_000)
        self.assertEqual(biggest["leaders"], [{"runtime": "Codex"}])
        self.assertEqual(self.spotlight(result, "tool_mvp")["leaders"], [
            {"tool": "Read file"},
        ])
        self.assertEqual(self.spotlight(result, "stack_explorer")["current"], 5)

    def test_record_ties_and_auto_selection_follow_the_contract(self):
        def paired(day, output, cost):
            return {"day": day, "model": "gpt-5", "executions": 1,
                    "cost_covered_output_tokens": output, "cost_covered_cost": cost}

        record_rows = [self.row("2026-09-13T12:00:00", [paired("2026-09-13", 400, 1)])]
        for day in ("2026-09-06", "2026-08-30", "2026-08-23"):
            record_rows[0]["_model_daily"].append(paired(day, 100, 1))
        record_result = self.recap(record_rows)
        self.assertTrue(self.spotlight(record_result, "efficiency_record")["available"])
        self.assertEqual(record_result["spotlight_default"], "efficiency_change")

        tied = self.recap([
            self.row("2026-09-13T12:00:00", id="a", runtime="Codex"),
            self.row("2026-09-12T12:00:00", id="b", runtime="Claude"),
        ])
        self.assertEqual(self.spotlight(tied, "daily_driver")["leaders"], [
            {"runtime": "Claude"}, {"runtime": "Codex"},
        ])
        three_way = self.recap([
            self.row("2026-09-13T12:00:00", id="a", runtime="Codex"),
            self.row("2026-09-12T12:00:00", id="b", runtime="Claude"),
            self.row("2026-09-11T12:00:00", id="c", runtime="Cursor"),
        ])
        self.assertFalse(self.spotlight(three_way, "daily_driver")["available"])

        tied_record = self.recap([self.row("2026-09-13T12:00:00", [
            paired("2026-09-13", 100, 1), paired("2026-09-06", 100, 1),
            paired("2026-08-30", 50, 1), paired("2026-08-23", 50, 1),
        ])])
        self.assertFalse(self.spotlight(tied_record, "efficiency_record")["available"])

        streak_result = self.recap([
            self.row("2026-09-13T12:00:00", id="a"),
            self.row("2026-09-12T12:00:00", id="b"),
        ])
        self.assertEqual(streak_result["spotlight_default"], "build_streak")
        self.assertIsNone(self.recap([])["spotlight_default"])

    def test_public_recap_is_a_recursive_privacy_allowlist(self):
        def recursive_keys(value, path=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield path + (key,)
                    yield from recursive_keys(item, path + (key,))
            elif isinstance(value, list):
                for item in value:
                    yield from recursive_keys(item, path + ("*",))

        result = self.recap([self.row("2026-09-13T12:00:00", [{
            "day": "2026-09-13", "model": "gpt-5", "executions": 1,
            "cost_covered_output_tokens": 100, "cost_covered_cost": 1,
            "path": "/private/repo", "project": "SECRET_PROMPT",
        }], id="session-secret", path="/private/repo", project="SECRET_PROMPT",
            title="commit subject", session_name="session-secret",
            desktop_session_id="session-secret", args_fingerprint="tool result secret",
            raw_error="tool result secret", _tool_evidence={"tools": [{
                "name": "read_file", "id": "tool-secret", "args_fingerprint": "tool result secret",
                "daily": [{"day": "2026-09-13", "calls": 1, "raw_error": "tool result secret"}],
            }]} )])
        forbidden = {"path", "project", "title", "session_name", "desktop_session_id",
                     "args_fingerprint", "raw_error"}
        for path in recursive_keys(result):
            self.assertNotIn(path[-1], forbidden)
            if path[-1] == "id":
                self.assertIn(path[:-1], {("spotlights", "*"), ("supporting", "*"),
                                          ("headlines", "*")})
        payload = json.dumps(result, sort_keys=True)
        for sentinel in ("SECRET_PROMPT", "/private/repo", "session-secret",
                         "commit subject", "tool result secret"):
            self.assertNotIn(sentinel, payload)

    def test_tool_mvp_excludes_token_meter_mcp_and_projects_safe_display(self):
        result = self.recap([self.row("2026-09-13T12:00:00", [], _tool_evidence={
            "tools": [{
                "name": "mcp__tokenmeter__diagnose", "display": "diagnose",
                "namespace": "tokenmeter", "kind": "mcp",
                "daily": [{"day": "2026-09-13", "calls": 99}],
            }, {
                "name": "read_file_v2", "display": "Read file", "namespace": "filesystem",
                "kind": "tool", "daily": [{"day": "2026-09-13", "calls": 3}],
            }, {
                "name": "secret", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 99}],
            }, {
                "name": "password", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 99}],
            }, {
                "name": "authorization", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 99}],
            }, {
                "name": "access_token", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 99}],
            }, {
                "name": "mcp__safe__access_token", "display": "Read file", "daily": [{"day": "2026-09-13", "calls": 99}],
            }],
        })])

        tool = self.spotlight(result, "tool_mvp")
        self.assertTrue(tool["available"])
        self.assertEqual(tool["leaders"], [{"tool": "Read file"}])
        self.assertEqual(result["coverage"]["tools"]["eligible"], 1)
        payload = json.dumps(result, sort_keys=True)
        for sentinel in ("mcp__tokenmeter__diagnose", "read_file_v2", "Secret", "Password",
                         "Authorization", "Access token"):
            self.assertNotIn(sentinel, payload)

    def test_tool_mvp_projects_unknown_provider_identifiers_to_fixed_generic_labels(self):
        private_names = (
            "mcp__repo__private-project-alpha",
            "mcp__workspace__customer_acme",
            "git_private_repo",
        )
        result = self.recap([self.row("2026-09-13T12:00:00", [], _tool_evidence={
            "tools": [
                {"name": "read_file_v2", "display": "untrusted display", "daily": [
                    {"day": "2026-09-13", "calls": 5},
                ]},
                *[{"name": name, "display": "untrusted display", "daily": [
                    {"day": "2026-09-13", "calls": 1},
                ]} for name in private_names],
            ],
        })])

        self.assertEqual(self.spotlight(result, "tool_mvp")["leaders"], [{"tool": "Read file"}])
        self.assertEqual(self.spotlight(result, "tool_mvp")["current"], 5)
        self.assertEqual(result["coverage"]["tools"]["eligible"], 3)
        payload = json.dumps(result, sort_keys=True)
        for private_name in private_names:
            self.assertNotIn(private_name, payload)
        for fragment in ("private-project-alpha", "customer_acme", "private repo"):
            self.assertNotIn(fragment, payload)

    def headline(self, result, headline_id):
        return next((item for item in result["headlines"] if item["id"] == headline_id), None)

    def test_headlines_are_absent_without_evidence(self):
        result = self.recap([], days=30)
        self.assertEqual(result["headlines"], [])
        # One session and one day prove no identity, streak, week, or cache claim.
        single = self.recap([self.row("2026-09-13T12:00:00", [{
            "day": "2026-09-13", "model": "gpt-5", "executions": 1,
            "input_tokens": 100, "cache_read_tokens": 0,
            "cost_covered_output_tokens": 100, "cost_covered_cost": 1,
        }], id="only")], days=30)
        self.assertEqual(single["headlines"], [])

    def test_identity_headline_requires_sixty_percent_of_sessions(self):
        rows = [self.row(f"2026-09-{13 - index:02d}T12:00:00", id=f"claude-{index}",
                         runtime="Claude Code") for index in range(6)]
        rows += [self.row("2026-09-01T12:00:00", id=f"codex-{index}") for index in range(4)]
        at_threshold = self.headline(self.recap(rows, days=30), "identity")
        self.assertEqual(at_threshold, {
            "id": "identity", "title": "A Claude person", "value": "60%",
            "detail": "of sessions on Claude Code",
        })
        below = self.recap(rows + [self.row("2026-09-02T12:00:00", id="codex-x")], days=30)
        self.assertIsNone(self.headline(below, "identity"))
        vowel = self.headline(self.recap([
            self.row("2026-09-13T12:00:00", id=f"oc-{index}", runtime="OpenCode")
            for index in range(3)
        ], days=7), "identity")
        self.assertEqual((vowel["title"], vowel["value"]), ("An OpenCode person", "100%"))
        # Rounding never overstates a share.
        rows = [self.row("2026-09-13T12:00:00", id=f"c-{index}", runtime="Claude Code")
                for index in range(2)] + [self.row("2026-09-13T12:00:00", id="x")]
        self.assertEqual(self.headline(self.recap(rows), "identity")["value"], "66%")

    def test_streak_headline_reports_the_longest_run_of_three_or_more_days(self):
        two = self.recap([self.row("2026-09-13T12:00:00", id="a"),
                          self.row("2026-09-12T12:00:00", id="b")])
        self.assertIsNone(self.headline(two, "streak"))
        four = self.recap([self.row(f"2026-09-{day:02d}T12:00:00", id=str(day))
                           for day in (13, 12, 11, 10, 8)])
        self.assertEqual(self.headline(four, "streak"), {
            "id": "streak", "title": "Longest streak", "value": "4 days",
            "detail": "consecutive active days",
        })

    def test_best_week_headline_compares_comparable_weeks_and_says_estimate(self):
        def day(value, output, cost):
            return {"day": value, "model": "gpt-5", "executions": 1,
                    "cost_covered_output_tokens": output, "cost_covered_cost": cost}
        rows = [self.row("2026-09-13T12:00:00", [
            day("2026-09-13", 3000, 1), day("2026-09-05", 1000, 1),
            day("2026-08-29", 1000, 1),
        ], id="weeks")]
        best = self.headline(self.recap(rows, days=30), "best_week")
        self.assertEqual(best["title"], "Best week")
        self.assertEqual(best["value"], "3.0×")
        self.assertIn("estimate", best["detail"])
        # A 7-day period has one week, so there is nothing to compare against.
        self.assertIsNone(self.headline(self.recap(rows, days=7), "best_week"))
        flat = [self.row("2026-09-13T12:00:00", [
            day("2026-09-13", 1000, 1), day("2026-09-05", 1000, 1),
        ], id="flat")]
        self.assertIsNone(self.headline(self.recap(flat, days=30), "best_week"))

    def test_cache_share_headline_uses_only_cache_covered_input(self):
        rows = [self.row("2026-09-13T12:00:00", [{
            "day": "2026-09-13", "model": "gpt-5", "executions": 2,
            "input_tokens": 1000, "cache_read_tokens": 875,
        }, {
            "day": "2026-09-13", "model": "gpt-5-mini", "executions": 1,
            "input_tokens": 5000, "cache_read_tokens": 0,
            "availability": {"cache": False},
        }], id="cached")]
        self.assertEqual(self.headline(self.recap(rows), "cache_share"), {
            "id": "cache_share", "title": "Cache-powered", "value": "87%",
            "detail": "of input tokens read from cache",
        })
        uncovered = [self.row("2026-09-13T12:00:00", [{
            "day": "2026-09-13", "model": "gpt-5", "executions": 1,
            "input_tokens": 1000, "cache_read_tokens": 900,
        }], id="none", availability={"cache": False})]
        self.assertIsNone(self.headline(self.recap(uncovered), "cache_share"))

    def test_headlines_are_bounded_ordered_and_content_free(self):
        rows = [self.row(f"2026-09-{13 - index:02d}T12:00:00", [{
            "day": f"2026-09-{13 - index:02d}", "model": "claude-sonnet-4-5", "executions": 1,
            "input_tokens": 1000, "cache_read_tokens": 900,
            "cost_covered_output_tokens": 1000 * (index + 1), "cost_covered_cost": 1,
        }], id=f"secret-session-{index}", runtime="Claude Code",
            project="/private/repo", title="SECRET_PROMPT") for index in range(12)]
        result = self.recap(rows, days=30)
        self.assertEqual([item["id"] for item in result["headlines"]],
                         ["identity", "best_week", "streak", "cache_share"])
        self.assertLessEqual(len(result["headlines"]), 4)
        for item in result["headlines"]:
            self.assertEqual(sorted(item), ["detail", "id", "title", "value"])
            self.assertTrue(all(isinstance(item[key], str) and len(item[key]) <= 48
                                for key in item))
        payload = json.dumps(result["headlines"])
        for sentinel in ("secret-session", "/private/repo", "SECRET_PROMPT", "percentile"):
            self.assertNotIn(sentinel, payload)


class BuilderRecapEndpointTests(unittest.TestCase):
    def setUp(self):
        self.saved_cache = dict(meter._xsess)

    def tearDown(self):
        meter._xsess.clear()
        meter._xsess.update(self.saved_cache)

    def test_builder_recap_state_reduces_git_evidence_before_public_build(self):
        expected = {"ok": True, "generated_at": 123, "privacy": {
            "content_included": False, "project_identity_included": False,
        }}
        meter._xsess["internal_rows"] = [{"id": "private"}]
        with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}), \
                mock.patch.object(meter, "git_delivery_state", return_value={
                    "ok": True,
                    "previous": {
                        "changed_lines": 8, "commits": 2,
                        "availability": {"code_pushed": True},
                        "project": "/private/previous", "subject": "secret previous",
                    },
                    "days": [{
                        "day": "2026-09-13", "changed_lines": 12, "commits": 3,
                        "availability": {"code_pushed": True},
                        "project": "/private/repo", "subject": "secret",
                    }],
                }), \
                mock.patch.object(meter, "_domain_build_builder_recap", return_value=expected) as build:
            payload, status = meter.builder_recap_state("30")

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[0], [{"id": "private"}])
        self.assertEqual(build.call_args.args[1], [{
            "day": "2026-09-13", "available": True, "active": True,
            "changed_lines": 12, "commits": 3,
        }])
        self.assertEqual(build.call_args.args[2], 30)
        self.assertEqual(build.call_args.kwargs["generated_at"], 123)
        self.assertEqual(build.call_args.kwargs["previous_git_lines"], 8)
        self.assertEqual(build.call_args.kwargs["previous_git_commits"], 2)
        self.assertNotIn("/private/repo", json.dumps(build.call_args.args[1]))
        self.assertNotIn("secret", json.dumps(build.call_args.args[1]))
        self.assertNotIn("private/previous", json.dumps(build.call_args.kwargs))

    def test_builder_recap_state_fails_closed_on_malformed_changed_line_counts(self):
        meter._xsess["internal_rows"] = []
        malformed = (True, -1, 1.5, float("inf"), "999", 10 ** 1000)
        days = [{
            "day": f"2026-09-{13 - index:02d}", "changed_lines": value,
            "commits": value, "availability": {"code_pushed": True},
        } for index, value in enumerate(malformed)]
        with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}), \
                mock.patch.object(meter, "git_delivery_state", return_value={
                    "ok": True, "days": days,
                    "previous": {
                        "changed_lines": "999", "commits": 1.5,
                        "availability": {"code_pushed": True},
                    },
                }), \
                mock.patch.object(meter, "_domain_build_builder_recap", return_value={"ok": True}) as build:
            payload, status = meter.builder_recap_state("30")

        self.assertEqual((payload, status), ({"ok": True}, 200))
        projected = build.call_args.args[1]
        self.assertEqual(len(projected), len(malformed))
        self.assertTrue(all(row["changed_lines"] is None for row in projected))
        self.assertTrue(all(row["commits"] is None for row in projected))
        self.assertIsNone(build.call_args.kwargs["previous_git_lines"])
        self.assertIsNone(build.call_args.kwargs["previous_git_commits"])

    def test_builder_recap_state_marks_git_failures_unavailable(self):
        meter._xsess["internal_rows"] = [{"id": "private"}]
        with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}), \
                mock.patch.object(meter, "git_delivery_state", return_value={
                    "ok": False, "error": "private git failure",
                }), \
                mock.patch.object(meter, "_domain_build_builder_recap", return_value={"ok": True}) as build:
            payload, status = meter.builder_recap_state("7")

        self.assertEqual((payload, status), ({"ok": True}, 200))
        self.assertIsNone(build.call_args.args[1])

    def test_builder_recap_state_rejects_invalid_ranges_without_discovery(self):
        for value in ("", "14", "30.0", "seven"):
            with self.subTest(value=value), \
                    mock.patch.object(meter, "cross_session") as cross, \
                    mock.patch.object(meter, "git_delivery_state") as git, \
                    mock.patch.object(meter, "_domain_build_builder_recap") as build:
                payload, status = meter.builder_recap_state(value)
            self.assertEqual(status, 400)
            self.assertEqual(payload, {
                "ok": False, "error": "A valid recap range is required.",
            })
            cross.assert_not_called()
            git.assert_not_called()
            build.assert_not_called()

    def request(self, path):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", path)
            response = conn.getresponse()
            payload = json.loads(response.read())
            status = response.status
            content_type = response.getheader("Content-Type")
            conn.close()
            return payload, status, content_type
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_builder_recap_http_route_defaults_validates_and_returns_json(self):
        meter._xsess["internal_rows"] = [{
            "id": "private-session", "path": "/private/trace.jsonl",
            "project": "/private/repo", "title": "private title",
            "last": "2026-09-13T12:00:00", "runtime": "Codex",
        }]
        with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}) as cross, \
                mock.patch.object(meter, "git_delivery_state", return_value={"ok": True, "days": []}) as git:
            valid, valid_status, valid_type = self.request("/builder-recap?range=7")
            default, default_status, _ = self.request("/builder-recap")
            invalid, invalid_status, _ = self.request("/builder-recap?range=14")
            repeated, repeated_status, _ = self.request("/builder-recap?range=7&range=30")
            cross.reset_mock()
            git.reset_mock()
            blanks = [
                self.request("/builder-recap?range="),
                self.request("/builder-recap?range=7&range="),
                self.request("/builder-recap?range=&range=30"),
            ]

        self.assertEqual((valid_status, valid_type), (200, "application/json"))
        self.assertEqual(valid["range_days"], 7)
        self.assertEqual(valid["privacy"], {
            "content_included": False, "project_identity_included": False,
        })
        for sentinel in ("private-session", "/private/trace.jsonl", "/private/repo", "private title"):
            self.assertNotIn(sentinel, json.dumps(valid, sort_keys=True))
        self.assertEqual((default_status, default["range_days"]), (200, 30))
        for payload, status in ((invalid, invalid_status), (repeated, repeated_status)):
            self.assertEqual(status, 400)
            self.assertEqual(payload, {
                "ok": False, "error": "A valid recap range is required.",
            })
            self.assertNotIn("private", json.dumps(payload))
        for payload, status, _ in blanks:
            self.assertEqual(status, 400)
            self.assertEqual(payload, {
                "ok": False, "error": "A valid recap range is required.",
            })
        cross.assert_not_called()
        git.assert_not_called()

        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        self.assertIn('elif req_path == "/builder-recap":', source)
        self.assertIn("builder_recap_state(range_values[0])", source)
        post_source = source[source.index("def do_POST"):source.index("def do_GET")]
        self.assertNotIn('req_path == "/builder-recap"', post_source)

    def test_builder_recap_http_payload_rejects_unsafe_runtime_model_and_tool_labels(self):
        sentinels = ("https://private.example", "/private/model", "sk-live-secret",
                     "api_key=private", "<private-label>")
        meter._xsess["internal_rows"] = [{
            "last": "2026-09-13T12:00:00", "runtime": sentinels[0],
            "_model_daily": [{"day": "2026-09-13", "model": sentinels[1], "executions": 3}],
            "_performance_samples": [{"day": "2026-09-13", "model": sentinels[2],
                                      "output_tokens": 1000, "duration_s": 10}],
            "_tool_evidence": {"tools": [{"name": "read_file", "display": sentinels[3],
                                              "daily": [{"day": "2026-09-13", "calls": 2}]},
                                             {"name": "read_file", "display": sentinels[4],
                                              "daily": [{"day": "2026-09-13", "calls": 2}]}]},
        }]
        with mock.patch.object(meter, "cross_session", return_value={"generated_at": 123}), \
                mock.patch.object(meter, "git_delivery_state", return_value={"ok": True, "days": []}):
            payload, status, _ = self.request("/builder-recap?range=7")

        self.assertEqual(status, 200)
        serialized = json.dumps(payload, sort_keys=True)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, serialized)


if __name__ == "__main__":
    unittest.main()
