import datetime
import hashlib
import http.client
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.services import git_delivery

_DASHBOARD_HELPERS = (
    r"^const f=n=>.*$",
    r"^const compactNumber=n=>.*$",
    r"^const money=n=>.*$",
    r"^const countWord=\(n,one,many=one\+'s'\)=>.*$",
)


def _extract_block(page, opening):
    """Return the balanced-brace source of a dashboard declaration."""
    start = page.index(opening)
    depth = 0
    for index in range(start, len(page)):
        if page[index] in "{[":
            depth += 1
        elif page[index] in "}]":
            depth -= 1
            if depth == 0:
                return page[start:index + 1]
    raise AssertionError("Unbalanced dashboard declaration: {}".format(opening))


def delivery_js(page, names, blocks=(), consts=()):
    """Assemble real dashboard source for a bounded Node behavior check."""
    parts = []
    for pattern in tuple(_DASHBOARD_HELPERS) + tuple(consts):
        match = re.search(pattern, page, re.MULTILINE)
        if match is None:
            raise AssertionError("Missing dashboard helper: {}".format(pattern))
        parts.append(match.group(0))
    for block in blocks:
        parts.append(_extract_block(page, block))
    for name in names:
        parts.append(_extract_block(page, "function {}(".format(name)))
    return "\n".join(parts)


def local_timestamp(day, hour=12):
    value = datetime.datetime.combine(
        datetime.date.fromisoformat(day), datetime.time(hour, 0),
    )
    return int(value.timestamp())


class GitDeliveryLedgerTests(unittest.TestCase):
    def test_ledger_replaces_incompatible_unreleased_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TABLE delivery_observations "
                    "(repo_key TEXT, oid TEXT, pushed_at INTEGER)"
                )
                connection.execute(
                    "INSERT INTO delivery_observations VALUES ('old', 'raw-oid', 1)"
                )

            ledger = meter.GitDeliveryLedger(str(path), "test-salt")
            inserted = ledger.record(
                "repo-key", "object-key", local_timestamp("2026-09-03"), 4, 2,
            )

            self.assertTrue(inserted)
            self.assertEqual(len(ledger.rows()), 1)
            self.assertEqual(ledger.rows()[0]["object_key"], "object-key")

    def test_ledger_persists_only_hashed_keys_and_numeric_delivery_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.sqlite3"
            ledger = meter.GitDeliveryLedger(str(path), "test-salt")

            inserted = ledger.record(
                "repo-key", "object-key", local_timestamp("2026-09-03"), 12, 4,
            )

            self.assertTrue(inserted)
            self.assertEqual(ledger.rows(), [{
                "repo_key": "repo-key",
                "object_key": "object-key",
                "observed_at": local_timestamp("2026-09-03"),
                "day": "2026-09-03",
                "added": 12,
                "deleted": 4,
            }])
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(delivery_observations)"
                    )
                }
            self.assertEqual(columns, {
                "repo_key", "object_key", "observed_at", "day", "added", "deleted",
            })
            self.assertTrue({"path", "remote", "branch", "email", "message"}.isdisjoint(columns))

    def test_ledger_rejects_invalid_line_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = meter.GitDeliveryLedger(
                str(Path(tmp) / "delivery.sqlite3"), "test-salt",
            )

            for invalid in (-1, 1.2, float("inf"), True):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    ledger.record("repo-key", "object-key", 1, invalid, 0)

    def test_clear_removes_evidence_and_records_a_history_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = meter.GitDeliveryLedger(
                str(Path(tmp) / "delivery.sqlite3"), "test-salt",
            )
            ledger.record("repo-key", "object-key", 100, 4, 1)

            ledger.clear(200)

            self.assertEqual(ledger.rows(), [])
            self.assertEqual(ledger.baseline_at(), 200)

    def test_coalesce_preserves_canonical_conflicts_and_moves_legacy_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = meter.GitDeliveryLedger(
                str(Path(tmp) / "delivery.sqlite3"), "test-salt",
            )
            ledger.record("legacy", "shared", 100, 8, 2)
            ledger.record("legacy", "legacy-only", 100, 6, 1)
            ledger.record("canonical", "shared", 100, 3, 4)
            ledger.map_project("legacy-project", "legacy")
            ledger.set_repository_coverage("legacy", True, 100, partial=True)
            ledger.set_repository_coverage("canonical", False, 200, partial=False)

            ledger.coalesce_repository("legacy", "canonical")

            self.assertEqual(ledger.rows(), [
                {"repo_key": "canonical", "object_key": "legacy-only", "observed_at": 100,
                 "day": "1970-01-01", "added": 6, "deleted": 1},
                {"repo_key": "canonical", "object_key": "shared", "observed_at": 100,
                 "day": "1970-01-01", "added": 3, "deleted": 4},
            ])
            self.assertEqual(ledger.repo_key_for_project("legacy-project"), "canonical")
            self.assertTrue(ledger.has_seen("canonical", "legacy-only"))
            self.assertTrue(ledger.has_seen("canonical", "shared"))
            self.assertEqual(ledger.repository_coverage("canonical"), {
                "measured": True, "partial": True, "checked_at": 200,
            })


class GitDeliveryScannerTests(unittest.TestCase):
    def test_subprocess_runner_supplies_a_system_path_for_launch_agents(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "token_meter.services.git_delivery.subprocess.run",
            return_value=completed,
        ) as run:
            meter.GitDeliveryService._subprocess_runner(
                ["git", "--version"], timeout=1,
            )

        self.assertEqual(run.call_args.kwargs["env"]["PATH"], os.defpath)

    def test_scan_limits_generator_candidates_without_losing_limit_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            service._scan_candidate = mock.Mock(
                side_effect=lambda _candidate, _checked_at, remaining: (
                    0, 0, "ready", True, False, remaining,
                )
            )
            candidates = (
                {"root": f"/repo-{index}", "project": f"repo-{index}"}
                for index in range(git_delivery.MAX_REPOSITORIES + 1)
            )

            result = service.scan(candidates)

        self.assertEqual(
            service._scan_candidate.call_count, git_delivery.MAX_REPOSITORIES,
        )
        self.assertEqual(
            result["coverage"]["repositories"], git_delivery.MAX_REPOSITORIES,
        )
        self.assertIn("repository_limit", result["coverage"]["codes"])

    def test_actual_git_push_is_observed_locally_without_network_or_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Alice"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "config", "user.email", "alice@example.com",
            ], check=True)
            (repo / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "remote", "add", "origin", str(remote),
            ], check=True)
            subprocess.run([
                "git", "-C", str(repo), "push", "-qu", "origin", "main",
            ], check=True)
            service = meter.GitDeliveryService(
                str(root / "delivery.sqlite3"), now=lambda: local_timestamp("2026-09-04"),
                salt="test-salt",
            )

            result = service.scan([{"root": str(repo), "project": "repo · a1b2c3"}])
            repeated = service.scan([{"root": str(repo), "project": "repo · a1b2c3"}])

            self.assertEqual(result["new_added"], 3)
            self.assertEqual(result["new_deleted"], 0)
            self.assertEqual(result["new_changed_lines"], 3)
            self.assertEqual(repeated["new_changed_lines"], 0)
            self.assertEqual(result["coverage"]["measured"], 1)
            self.assertNotIn("gh", json.dumps(result))
            self.assertNotIn(str(repo), json.dumps(result))

    def test_linked_worktree_preserves_the_main_worktree_ledger_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            linked = root / "linked-worktree"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Alice"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "config", "user.email", "alice@example.com",
            ], check=True)
            (repo / "README").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "worktree", "add", "-q", "-b", "linked", str(linked),
            ], check=True)
            service = meter.GitDeliveryService(
                str(root / "delivery.sqlite3"), now=lambda: local_timestamp("2026-09-04"),
                salt="test-salt",
            )

            service.scan([
                {"root": str(repo), "project": "repo · 111111"},
                {"root": str(linked), "project": "linked-worktree · 222222"},
            ])

            main_top = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], text=True,
            ).strip()
            main_key = service.ledger.repo_key_for_project(service._hash(str(repo)))
            linked_key = service.ledger.repo_key_for_project(service._hash(str(linked)))
            self.assertEqual(main_key, service._hash(main_top))
            self.assertEqual(linked_key, main_key)

    def test_separate_git_dir_preserves_the_worktree_ledger_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            metadata = root / "metadata" / ".git"
            metadata.parent.mkdir()
            subprocess.run([
                "git", "init", "-q", "--separate-git-dir", str(metadata), str(repo),
            ], check=True)
            subprocess.run([
                "git", "-C", str(repo), "config", "user.email", "alice@example.com",
            ], check=True)
            service = meter.GitDeliveryService(
                str(root / "delivery.sqlite3"), now=lambda: local_timestamp("2026-09-04"),
                salt="test-salt",
            )

            service.scan([{"root": str(repo), "project": "repo · 111111"}])

            top_level = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], text=True,
            ).strip()
            repo_key = service.ledger.repo_key_for_project(service._hash(str(repo)))
            self.assertEqual(repo_key, service._hash(top_level))

    def test_linked_worktree_coalesces_legacy_evidence_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            linked = root / "linked-worktree"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Alice"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "config", "user.email", "alice@example.com",
            ], check=True)
            (repo / "README").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
            subprocess.run([
                "git", "-C", str(repo), "worktree", "add", "-q", "-b", "linked", str(linked),
            ], check=True)
            service = meter.GitDeliveryService(
                str(root / "delivery.sqlite3"), now=lambda: local_timestamp("2026-09-04"),
                salt="test-salt",
            )
            linked_top = subprocess.check_output(
                ["git", "-C", str(linked), "rev-parse", "--show-toplevel"], text=True,
            ).strip()
            main_top = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], text=True,
            ).strip()
            legacy_key = service._hash(linked_top)
            canonical_key = service._hash(main_top)
            object_key = service._hash("seed-object")
            label = "linked-worktree · 111111"
            service.ledger.record(legacy_key, object_key, local_timestamp("2026-09-03"), 6, 2)
            service.ledger.map_project(service._hash(str(linked)), legacy_key)
            service.ledger.set_repository_coverage(
                legacy_key, True, local_timestamp("2026-09-03"),
            )

            service.scan([{"root": str(linked), "project": label}])
            service.ledger.set_repository_coverage(
                canonical_key, True, local_timestamp("2026-09-04"),
            )
            payload = service.query(
                label, "7", [], [label], [{"root": str(linked), "project": label}],
            )

            self.assertEqual(service.ledger.rows(), [{
                "repo_key": canonical_key, "object_key": object_key,
                "observed_at": local_timestamp("2026-09-03"), "day": "2026-09-03",
                "added": 6, "deleted": 2,
            }])
            self.assertFalse(service.ledger.record(
                canonical_key, object_key, local_timestamp("2026-09-03"), 60, 20,
            ))
            self.assertEqual(payload["overall"]["changed_lines"], 8)

    def test_scan_uses_only_local_read_only_git_commands_and_matching_identity(self):
        first_oid = "a" * 40
        second_oid = "b" * 40
        calls = []

        def runner(argv, **_kwargs):
            calls.append(tuple(argv))
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("rev-parse", "--git-common-dir"):
                return {"returncode": 0, "stdout": ".git\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "Alice@Example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {"returncode": 0, "stdout": (
                    f"{second_oid}\x00update by push\x00origin/main@{{1788445800}}\n"
                    f"{first_oid}\x00update by push\x00origin/main@{{1788359400}}\n"
                )}
            if args[0] == "rev-list":
                tip = args[-1] if not args[-1].startswith("^") else args[-2]
                return {"returncode": 0, "stdout": f"{tip}\n"}
            if args[0:3] == ("show", "-s", "--format=%ae%x00%P"):
                email = "alice@example.com" if args[-1] == second_oid else "bob@example.com"
                return {"returncode": 0, "stdout": f"{email}\x00{'1' * 40}\n"}
            if args[0:3] == ("show", "--numstat", "--format="):
                return {"returncode": 0, "stdout": "10\t4\tsrc/app.py\n-\t-\tasset.png\n"}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])

        self.assertEqual(result["new_changed_lines"], 14)
        flattened = {word for call in calls for word in call}
        self.assertTrue({"fetch", "pull", "push", "checkout", "switch", "reset", "prune"}.isdisjoint(flattened))
        self.assertNotIn("ls-remote", flattened)
        self.assertNotIn("Alice@Example.com", json.dumps(result))
        self.assertNotIn(second_oid, json.dumps(result))

    def test_missing_repository_or_identity_is_partial_not_measured_zero(self):
        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("rev-parse", "--git-common-dir"):
                return {"returncode": 0, "stdout": ".git\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 1, "stdout": ""}
            if args == ("config", "--global", "--get", "user.email"):
                return {"returncode": 1, "stdout": ""}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])

        self.assertEqual(result["coverage"]["measured"], 0)
        self.assertEqual(result["coverage"]["partial"], 1)
        self.assertIn("identity_unavailable", result["coverage"]["codes"])

    def test_empty_push_reflog_is_partial_not_measured_zero(self):
        oid = "a" * 40

        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("rev-parse", "--git-common-dir"):
                return {"returncode": 0, "stdout": ".git\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "alice@example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {
                    "returncode": 0,
                    "stdout": f"{oid}\x00fetch\x00origin/main@{{100}}\n",
                }
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: 200, salt="test-salt",
            )
            result = service.scan([{"root": "/repo", "project": "repo · a1b2c3"}])
            payload = service.query(
                "repo · a1b2c3", "7", [], ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(result["coverage"]["measured"], 0)
        self.assertEqual(result["coverage"]["partial"], 1)
        self.assertIn("no_push_history", result["coverage"]["codes"])
        self.assertFalse(payload["overall"]["availability"]["code_pushed"])

    def test_clear_baselines_old_reflogs_and_only_later_pushes_reappear(self):
        old_oid = "a" * 40
        new_oid = "b" * 40
        clock = [150]
        reflog = [
            f"{old_oid}\x00update by push\x00origin/main@{{100}}",
        ]

        def runner(argv, **_kwargs):
            args = tuple(argv[argv.index("-C") + 2:])
            if args == ("rev-parse", "--show-toplevel"):
                return {"returncode": 0, "stdout": "/repo\n"}
            if args == ("rev-parse", "--git-common-dir"):
                return {"returncode": 0, "stdout": ".git\n"}
            if args == ("config", "--get", "user.email"):
                return {"returncode": 0, "stdout": "alice@example.com\n"}
            if args[0] == "for-each-ref":
                return {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            if args[0:2] == ("reflog", "show"):
                return {"returncode": 0, "stdout": "\n".join(reflog) + "\n"}
            if args[0] == "rev-list":
                return {"returncode": 0, "stdout": args[-2 if args[-1].startswith("^") else -1] + "\n"}
            if args[0:3] == ("show", "-s", "--format=%ae%x00%P"):
                return {"returncode": 0, "stdout": "alice@example.com\x00" + "1" * 40 + "\n"}
            if args[0:3] == ("show", "--numstat", "--format="):
                return {"returncode": 0, "stdout": "4\t1\tapp.py\n"}
            raise AssertionError(f"Unexpected Git command: {argv}")

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                now=lambda: clock[0], salt="test-salt",
            )
            candidates = [{"root": "/repo", "project": "repo · a1b2c3"}]
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 5)

            clock[0] = 200
            service.clear()
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 0)
            self.assertEqual(service.ledger.rows(), [])

            reflog.insert(0, f"{new_oid}\x00update by push\x00origin/main@{{300}}")
            clock[0] = 350
            self.assertEqual(service.scan(candidates)["new_changed_lines"], 5)
            self.assertEqual(len(service.ledger.rows()), 1)


class GitDeliveryAggregationTests(unittest.TestCase):
    def test_query_exposes_efficiency_drivers_rolling_intensity_and_spend_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            ready_key = service._hash("/ready")
            service.ledger.map_project(service._hash("/ready"), ready_key)
            service.ledger.set_repository_coverage(
                ready_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                ready_key, service._hash("current-large"),
                local_timestamp("2026-08-30"), 80, 20,
            )
            service.ledger.record(
                ready_key, service._hash("current-small"),
                local_timestamp("2026-09-03"), 1, 0,
            )
            service.ledger.record(
                ready_key, service._hash("previous"),
                local_timestamp("2026-08-27"), 40, 10,
            )
            candidates = [
                {"root": "/ready", "project": "ready · a1b2c3"},
                {"root": "/unchecked", "project": "unchecked · d4e5f6"},
            ]
            spend_rows = [
                {
                    "project": "ready · a1b2c3", "day": "2026-08-30",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 1000,
                    "output_available": True,
                    "reasoning_tokens": 200,
                    "reasoning_output_tokens": 1000,
                    "reasoning_available": True,
                },
                {
                    "project": "ready · a1b2c3", "day": "2026-09-03",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 1000,
                    "output_available": True,
                    "reasoning_tokens": 100,
                    "reasoning_output_tokens": 1000,
                    "reasoning_available": True,
                },
                {
                    "project": "ready · a1b2c3", "day": "2026-08-27",
                    "covered_cost": 10.0, "cost_available": True,
                    "efficiency_covered_cost": 10.0,
                    "covered_output_tokens": 500,
                    "output_available": True,
                    "reasoning_tokens": 50,
                    "reasoning_output_tokens": 500,
                    "reasoning_available": True,
                },
                {
                    "project": "unchecked · d4e5f6", "day": "2026-09-03",
                    "covered_cost": 80.0, "cost_available": True,
                    "efficiency_covered_cost": 80.0,
                    "covered_output_tokens": 8000,
                    "output_available": True,
                    "reasoning_tokens": 800,
                    "reasoning_output_tokens": 8000,
                    "reasoning_available": True,
                },
            ]

            payload = service.query(
                "", "7", spend_rows,
                ["ready · a1b2c3", "unchecked · d4e5f6"], candidates,
            )

        drivers = payload["overall"]["efficiency"]
        self.assertEqual(drivers["output_per_dollar"], 100.0)
        self.assertEqual(drivers["delivery_yield"], 50.5)
        self.assertEqual(drivers["reasoning_ratio"], 0.15)
        self.assertTrue(drivers["availability"]["output_per_dollar"])
        self.assertTrue(drivers["availability"]["delivery_yield"])
        self.assertTrue(drivers["availability"]["reasoning_ratio"])
        self.assertEqual(payload["coverage"]["covered_spend"], 20.0)
        self.assertEqual(payload["coverage"]["available_spend"], 100.0)
        self.assertEqual(payload["coverage"]["spend_coverage"], 0.2)
        self.assertEqual(payload["comparison"]["output_per_dollar_pct"], 100.0)
        self.assertEqual(payload["comparison"]["delivery_yield_pct"], -49.5)
        self.assertEqual(payload["comparison"]["reasoning_ratio_pp"], 5.0)
        small_day = next(row for row in payload["days"] if row["day"] == "2026-09-03")
        self.assertEqual(small_day["spend_per_1k"], 10000.0)
        self.assertAlmostEqual(small_day["rolling_spend_per_1k"], 20000 / 101)
        self.assertEqual(small_day["efficiency"]["output_per_dollar"], 100.0)
        self.assertEqual(small_day["efficiency"]["delivery_yield"], 1.0)
        self.assertEqual(small_day["efficiency"]["reasoning_ratio"], 0.1)

    def test_covered_spend_ratio_remains_available_with_explicit_partial_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(
                repo_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                repo_key, service._hash("change"),
                local_timestamp("2026-09-03"), 7, 3,
            )

            payload = service.query(
                "repo · a1b2c3", "7",
                [
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 5.0, "cost_available": True},
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 0.0, "cost_available": False},
                ],
                ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(payload["overall"]["spend_per_1k"], 500.0)
        self.assertTrue(payload["overall"]["availability"]["cost"])
        self.assertTrue(payload["overall"]["availability"]["partial"])

    def test_query_returns_comparable_current_and_previous_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(repo_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(repo_key, service._hash("current"), local_timestamp("2026-09-03"), 80, 20)
            service.ledger.record(repo_key, service._hash("previous"), local_timestamp("2026-08-27"), 40, 10)
            payload = service.query(
                "repo · a1b2c3", "7",
                [
                    {"project": "repo · a1b2c3", "day": "2026-09-03", "covered_cost": 10.0, "cost_available": True},
                    {"project": "repo · a1b2c3", "day": "2026-08-27", "covered_cost": 10.0, "cost_available": True},
                ],
                ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertEqual(payload["overall"]["changed_lines"], 100)
        self.assertEqual(payload["overall"]["covered_cost"], 10.0)
        self.assertEqual(payload["overall"]["spend_per_1k"], 100.0)
        self.assertEqual(payload["previous"]["changed_lines"], 50)
        self.assertEqual(payload["comparison"]["code_pushed_pct"], 100.0)
        self.assertEqual(payload["comparison"]["spend_per_1k_pct"], -50.0)
        self.assertEqual(len(payload["days"]), 7)

    def test_query_keeps_unmeasured_code_unavailable_and_sorts_projects_by_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            ready_key = service._hash("/ready")
            service.ledger.map_project(service._hash("/ready"), ready_key)
            service.ledger.set_repository_coverage(ready_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(ready_key, service._hash("ready-change"), local_timestamp("2026-09-03"), 10, 2)
            candidates = [
                {"root": "/ready", "project": "ready · a1b2c3"},
                {"root": "/unchecked", "project": "unchecked · d4e5f6"},
            ]
            payload = service.query(
                "", "7",
                [
                    {"project": "ready · a1b2c3", "day": "2026-09-03", "covered_cost": 2.0, "cost_available": True},
                    {"project": "unchecked · d4e5f6", "day": "2026-09-03", "covered_cost": 8.0, "cost_available": True},
                ],
                ["ready · a1b2c3", "unchecked · d4e5f6"], candidates,
            )

        self.assertEqual(payload["overall"]["changed_lines"], 12)
        self.assertEqual(payload["overall"]["covered_cost"], 2.0)
        self.assertEqual(payload["coverage"]["selected_repositories"], 2)
        self.assertEqual(payload["coverage"]["comparable_repositories"], 1)
        self.assertEqual(payload["project_rows"][0]["project"], "unchecked · d4e5f6")
        self.assertFalse(payload["project_rows"][0]["availability"]["code_pushed"])
        self.assertIsNone(payload["project_rows"][0]["spend_per_1k"])
        self.assertNotIn("/unchecked", json.dumps(payload))

    def test_code_pushed_remains_available_when_covered_spend_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(repo_key, True, local_timestamp("2026-09-04"))
            service.ledger.record(repo_key, service._hash("change"), local_timestamp("2026-09-03"), 7, 3)

            payload = service.query(
                "repo · a1b2c3", "7", [], ["repo · a1b2c3"],
                [{"root": "/repo", "project": "repo · a1b2c3"}],
            )

        self.assertTrue(payload["overall"]["availability"]["code_pushed"])
        self.assertEqual(payload["overall"]["changed_lines"], 10)
        self.assertFalse(payload["overall"]["availability"]["cost"])
        self.assertIsNone(payload["overall"]["spend_per_1k"])

    def test_all_history_query_reads_only_the_bounded_twelve_month_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            repo_key = service._hash("/repo")
            service.ledger.map_project(service._hash("/repo"), repo_key)
            service.ledger.set_repository_coverage(
                repo_key, True, local_timestamp("2026-09-04"),
            )
            service.ledger.record(
                repo_key, service._hash("current"),
                local_timestamp("2026-09-03"), 7, 3,
            )
            service.ledger.record(
                repo_key, service._hash("ancient"),
                local_timestamp("2020-01-01"), 1000, 1000,
            )

            with mock.patch.object(
                service.ledger, "daily_rows", wraps=service.ledger.daily_rows,
            ) as daily_rows:
                payload = service.query(
                    "repo · a1b2c3", "all", [], ["repo · a1b2c3"],
                    [{"root": "/repo", "project": "repo · a1b2c3"}],
                )

        self.assertEqual(payload["overall"]["changed_lines"], 10)
        self.assertEqual(len(payload["days"]), git_delivery.MAX_QUERY_DAYS)
        self.assertEqual(daily_rows.call_args.args[1:], ("2025-09-04", "2026-09-04"))


class GitDeliveryApplicationTests(unittest.TestCase):
    def test_install_bootstrap_scans_while_the_invoking_app_has_repository_access(self):
        sources = [{"project": "/Users/alice/Code/private-project"}]
        service = mock.Mock()
        service.scan.return_value = {
            "ok": True, "new_changed_lines": 12, "coverage": {"measured": 1},
        }
        service.project_suffix.return_value = "a1b2c3"
        service.repository_key.return_value = "opaque-repository-key"
        with mock.patch.object(meter, "all_session_sources", return_value=sources), mock.patch.object(
            meter, "git_delivery_service", return_value=service,
        ), mock.patch.object(meter.os.path, "isdir", return_value=True), mock.patch.object(
            meter, "publish_git_delivery_candidates",
        ) as publish_candidates:
            result = meter.bootstrap_git_delivery()

        self.assertEqual(result["new_changed_lines"], 12)
        service.scan.assert_called_once()
        self.assertEqual(
            service.scan.call_args.args[0][0]["root"],
            "/Users/alice/Code/private-project",
        )
        publish_candidates.assert_not_called()

    def test_each_installer_bootstraps_delivery_before_server_start(self):
        contracts = (
            ("install", '"$INSTALL_ROOT/scripts/install-launch-agent" server-only'),
            ("install-linux", '"$INSTALL_ROOT/scripts/install-systemd-user" server-only'),
            ("install-windows.ps1", "& $StartScript -ReadinessTimeoutSeconds"),
        )
        for script, start_marker in contracts:
            with self.subTest(script=script):
                installer = Path(meter._SOURCE_ROOT, "scripts", script).read_text(
                    encoding="utf-8",
                )
                self.assertLess(installer.index("bootstrap_git_delivery"), installer.index(start_marker))

    def test_candidates_skip_missing_non_git_and_linked_worktrees_before_the_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"), salt="test-salt",
            )
            root = Path(tmp) / "private-project"
            linked = Path(tmp) / "linked-worktree"
            missing = Path(tmp) / "removed-project"
            non_repository = Path(tmp) / "notes"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Alice"], check=True)
            subprocess.run([
                "git", "-C", str(root), "config", "user.email", "alice@example.com",
            ], check=True)
            (root / "README").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
            subprocess.run([
                "git", "-C", str(root), "worktree", "add", "-q", "-b", "linked", str(linked),
            ], check=True)
            non_repository.mkdir()
            with mock.patch.object(meter, "git_delivery_service", return_value=service):
                candidates = meter.git_delivery_candidates([
                    {"project": str(linked)},
                    {"project": str(missing)},
                    {"project": str(non_repository)},
                    {"project": str(root)},
                ])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["root"], str(linked))
        self.assertRegex(candidates[0]["project"], r"^linked-worktree · [0-9a-f]{6}$")
        self.assertNotIn(str(Path(tmp)), candidates[0]["project"])
        self.assertTrue(candidates[0]["project"].endswith(service.project_suffix(linked)))
        self.assertFalse(candidates[0]["project"].endswith(
            hashlib.sha256(str(linked).encode("utf-8")).hexdigest()[:6]
        ))
        self.assertEqual(candidates[0]["aliases"][0]["root"], str(root))
        labels, canonical_by_source, _repo_by_label = service._project_repo_mapping(
            [candidates[0]["project"]], candidates,
        )
        self.assertEqual(labels, [candidates[0]["project"]])
        self.assertEqual(
            canonical_by_source[candidates[0]["aliases"][0]["project"]],
            candidates[0]["project"],
        )

    def test_git_delivery_state_hides_the_unscanned_overflow_project(self):
        candidates = [
            {"root": "/repo-{}".format(index), "project": "repo-{}".format(index)}
            for index in range(git_delivery.MAX_REPOSITORIES + 1)
        ]
        service = mock.Mock()
        service.query.return_value = {"ok": True}
        inventory = {
            "ready": True, "sources": (), "count": 0, "clients": {}, "updated_at": 1,
            "revision": 1, "git_delivery_candidates": tuple(candidates),
            "git_delivery_candidates_revision": 1,
        }
        with mock.patch.dict(meter._xsess, {"data": {}, "internal_rows": []}), mock.patch.object(
            meter, "_SOURCE_INVENTORY", inventory,
        ), mock.patch.object(meter, "git_delivery_service", return_value=service):
            meter.git_delivery_state()

        self.assertEqual(
            service.query.call_args.args[3],
            sorted("repo-{}".format(index) for index in range(git_delivery.MAX_REPOSITORIES)),
        )
        self.assertEqual(
            service.query.call_args.args[4], tuple(candidates[:git_delivery.MAX_REPOSITORIES]),
        )

    def test_git_delivery_state_reuses_scan_owned_candidates_without_discovery(self):
        candidates = [{"root": "/repo", "project": "repo · 111111", "repo_key": "opaque"}]
        service = mock.Mock()
        service.query.return_value = {"ok": True}
        inventory = {
            "ready": True, "sources": (), "count": 0, "clients": {}, "updated_at": 1,
            "revision": 1, "git_delivery_candidates": tuple(candidates),
            "git_delivery_candidates_revision": 1,
        }
        with mock.patch.dict(meter._xsess, {"data": {}, "internal_rows": []}), mock.patch.object(
            meter, "_SOURCE_INVENTORY", inventory,
        ), mock.patch.object(meter, "git_delivery_candidates", return_value=[]) as discover, mock.patch.object(
            meter, "git_delivery_service", return_value=service,
        ):
            meter.git_delivery_state()
            meter.git_delivery_state()

        discover.assert_not_called()
        self.assertEqual(service.query.call_args.args[3], ["repo · 111111"])

    def test_git_delivery_state_uses_an_empty_snapshot_before_the_first_scan(self):
        service = mock.Mock()
        service.query.return_value = {"ok": True}
        inventory = {
            "ready": True, "sources": ({"project": "/repo"},), "count": 1,
            "clients": {}, "updated_at": 1, "revision": 1,
            "git_delivery_candidates": (), "git_delivery_candidates_revision": None,
        }
        with mock.patch.dict(meter._xsess, {"data": {}, "internal_rows": []}), mock.patch.object(
            meter, "_SOURCE_INVENTORY", inventory,
        ), mock.patch.object(meter, "git_delivery_candidates", return_value=[]) as discover, mock.patch.object(
            meter, "git_delivery_service", return_value=service,
        ):
            meter.git_delivery_state()

        discover.assert_not_called()
        self.assertEqual(service.query.call_args.args[3:], ([], ()))

    def test_git_delivery_watcher_publishes_the_scanned_candidate_snapshot(self):
        candidates = [{"root": "/repo", "project": "repo · 111111", "repo_key": "opaque"}]
        inventory = {
            "ready": True, "sources": ({"project": "/repo"},), "count": 1,
            "clients": {}, "updated_at": 1, "revision": 4,
            "git_delivery_source_signature": "snapshot",
            "git_delivery_candidates": (), "git_delivery_candidates_revision": None,
        }
        service = mock.Mock()
        with mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), mock.patch.object(
            meter, "git_delivery_candidates", return_value=candidates,
        ) as discover, mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter, "publish_git_delivery_candidates",
        ) as publish_candidates, mock.patch.object(
            meter.time, "monotonic", side_effect=[0.0, 0.0, 0.0],
        ), mock.patch.object(meter.time, "sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                meter.git_delivery_watcher()

        discover.assert_called_once_with(inventory["sources"])
        service.scan.assert_called_once_with(candidates)
        publish_candidates.assert_called_once_with(candidates, 4, "snapshot")

    def test_source_inventory_mtime_refresh_keeps_git_candidates_until_project_roots_change(self):
        candidates = ({"root": "/repo", "project": "repo · 111111", "repo_key": "opaque"},)
        initial = {
            "ready": True, "sources": (), "count": 0, "clients": {}, "updated_at": 0,
            "revision": 0, "git_delivery_candidates": (), "git_delivery_candidates_revision": None,
        }
        wake = mock.Mock()
        with mock.patch.object(meter, "_SOURCE_INVENTORY", initial), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ):
            meter.publish_source_inventory([{"project": "/repo", "mtime": 1}])
            meter.publish_git_delivery_candidates(candidates)
            wake.reset_mock()
            preserved = meter.publish_source_inventory([{"project": "/repo", "mtime": 2}])
            wake.assert_not_called()
            service = mock.Mock()
            service.query.return_value = {"ok": True}
            with mock.patch.dict(meter._xsess, {"data": {}, "internal_rows": []}), mock.patch.object(
                meter, "git_delivery_candidates", return_value=(),
            ) as discover, mock.patch.object(meter, "git_delivery_service", return_value=service):
                meter.git_delivery_state()
            discover.assert_not_called()
            self.assertEqual(service.query.call_args.args[3], ["repo · 111111"])
            changed = meter.publish_source_inventory([{"project": "/other", "mtime": 2}])

        self.assertEqual(preserved["git_delivery_candidates"], candidates)
        self.assertEqual(
            preserved["git_delivery_candidates_revision"], preserved["revision"],
        )
        self.assertEqual(changed["git_delivery_candidates"], ())
        self.assertIsNone(changed["git_delivery_candidates_revision"])
        self.assertGreater(changed["revision"], preserved["revision"])
        wake.set.assert_called_once_with()

    def test_candidates_keep_later_aliases_for_an_eligible_repository_after_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = root / "primary"
            alias = root / "primary-alias"
            others = [root / "repo-{}".format(index) for index in range(
                git_delivery.MAX_REPOSITORIES,
            )]
            for path in [primary, alias, *others]:
                path.mkdir()
            keys = {str(primary): "primary-key", str(alias): "primary-key"}
            keys.update({str(path): "repo-key-{}".format(index) for index, path in enumerate(others)})
            service = mock.Mock()
            service.repository_key.side_effect = lambda path: keys[path]
            service.project_suffix.side_effect = lambda path: "{:06x}".format(
                abs(hash(str(path))) % 0x1000000,
            )
            alias_label = "primary-alias · {:06x}".format(
                abs(hash(str(alias))) % 0x1000000,
            )
            sources = ([{"project": str(primary)}]
                       + [{"project": str(path)} for path in others]
                       + [{"project": str(alias)}])
            with mock.patch.object(meter, "git_delivery_service", return_value=service):
                candidates = meter.git_delivery_candidates(sources)

        self.assertEqual(len(candidates), git_delivery.MAX_REPOSITORIES + 1)
        self.assertEqual(candidates[0]["root"], str(primary))
        self.assertEqual(candidates[0].get("aliases"), [{
            "root": str(alias),
            "project": alias_label,
        }])

    def test_candidates_deduplicate_exact_roots_before_repository_resolution(self):
        service = mock.Mock()
        service.repository_key.side_effect = ["shared", "shared"]
        service.project_suffix.return_value = "111111"
        sources = [
            {"project": "/repo/main"},
            {"project": "/repo/main"},
            {"project": "/repo/linked"},
        ]
        with mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter.os.path, "isdir", return_value=True,
        ):
            candidates = meter.git_delivery_candidates(sources)

        self.assertEqual(service.repository_key.call_args_list, [
            mock.call("/repo/main"), mock.call("/repo/linked"),
        ])
        self.assertEqual(candidates[0]["root"], "/repo/main")
        self.assertEqual(candidates[0]["aliases"][0]["root"], "/repo/linked")

    def test_watcher_floors_repeated_wakes_after_a_prompt_ready_scan(self):
        service = mock.Mock()
        service.scan.side_effect = [None, RuntimeError("stop watcher")]
        wake = mock.Mock()
        first_sources = ({"project": "/repo/first"},)
        latest_sources = ({"project": "/repo/latest"},)
        inventory = {
            "ready": True, "sources": first_sources, "revision": 1,
            "git_delivery_source_signature": "first",
        }

        def release_floor(seconds):
            self.assertEqual(seconds, meter.GIT_DELIVERY_INTERVAL_S)
            inventory.update({"sources": latest_sources, "revision": 2,
                              "git_delivery_source_signature": "latest"})

        with mock.patch.object(meter, "_SOURCE_INVENTORY", inventory), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ), mock.patch.object(
            meter, "git_delivery_candidates", side_effect=[
                [{"root": "/repo/first", "project": "first", "repo_key": "first"}],
                [{"root": "/repo/latest", "project": "latest", "repo_key": "latest"}],
            ],
        ) as candidates, mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter, "publish_git_delivery_candidates",
        ), mock.patch.object(
            meter.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 300.0],
        ), mock.patch.object(meter.time, "sleep", side_effect=release_floor) as sleep:
            with self.assertRaisesRegex(RuntimeError, "stop watcher"):
                meter.git_delivery_watcher()

        self.assertEqual(candidates.call_args_list, [
            mock.call(first_sources), mock.call(latest_sources),
        ])
        sleep.assert_called_once_with(meter.GIT_DELIVERY_INTERVAL_S)
        wake.wait.assert_not_called()

    def test_ordered_project_roots_invalidate_candidates_when_the_cap_can_change(self):
        roots = ["/repo-{}".format(index) for index in range(git_delivery.MAX_REPOSITORIES + 1)]
        sources = [{"project": root} for root in roots]
        service = mock.Mock()
        service.repository_key.side_effect = lambda root: "key-{}".format(root)
        service.project_suffix.return_value = "111111"
        initial = {
            "ready": True, "sources": (), "count": 0, "clients": {}, "updated_at": 0,
            "revision": 0, "git_delivery_source_signature": "", "git_delivery_candidates": (),
            "git_delivery_candidates_revision": None,
        }
        wake = mock.Mock()
        with mock.patch.object(meter, "_SOURCE_INVENTORY", initial), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ), mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter.os.path, "isdir", return_value=True,
        ):
            first_candidates = meter.git_delivery_candidates(sources)
            meter.publish_source_inventory(sources)
            meter.publish_git_delivery_candidates(first_candidates)
            wake.reset_mock()
            reversed_sources = list(reversed(sources))
            reversed_candidates = meter.git_delivery_candidates(reversed_sources)
            changed = meter.publish_source_inventory(reversed_sources)

        self.assertEqual(first_candidates[0]["root"], roots[0])
        self.assertEqual(reversed_candidates[0]["root"], roots[-1])
        self.assertEqual(changed["git_delivery_candidates"], ())
        wake.set.assert_called_once_with()

    def test_stale_watcher_publish_cannot_clobber_newer_inventory(self):
        initial = {
            "ready": True, "sources": (), "count": 0, "clients": {}, "updated_at": 0,
            "revision": 1, "git_delivery_source_signature": "first", "git_delivery_candidates": (),
            "git_delivery_candidates_revision": None,
        }
        wake = mock.Mock()
        candidates = [{"root": "/repo", "project": "repo", "repo_key": "opaque"}]
        with mock.patch.object(meter, "_SOURCE_INVENTORY", initial), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ):
            meter.publish_source_inventory([{"project": "/other"}])
            published = meter.publish_git_delivery_candidates(candidates, 1, "first")

        self.assertFalse(published)
        self.assertEqual(meter._SOURCE_INVENTORY["git_delivery_candidates"], ())

    def test_clear_uses_service_baseline_before_waking_the_watcher(self):
        service = mock.Mock()
        wake = mock.Mock()
        with mock.patch.object(meter, "git_delivery_service", return_value=service), mock.patch.object(
            meter, "_git_delivery_wake", wake,
        ):
            result = meter.clear_git_delivery_activity(confirm=True)

        self.assertEqual(result, {"ok": True})
        service.clear.assert_called_once_with()
        wake.set.assert_called_once_with()

    def test_spend_rows_keep_uncovered_cost_explicit(self):
        rows = meter.delivery_spend_rows([
            {"project": "/repo", "availability": {"cost": True},
             "_day_cost": {"2026-09-03": 12.5}, "_model_daily": []},
            {"project": "/repo", "availability": {"cost": False},
             "_day_cost": {"2026-09-03": 0}, "_model_daily": []},
        ])

        label = meter.delivery_project_label("/repo")
        self.assertEqual(rows, [
            {"project": label, "day": "2026-09-03", "covered_cost": 12.5, "cost_available": True},
            {"project": label, "day": "2026-09-03", "covered_cost": 0.0, "cost_available": False},
        ])

    def test_spend_rows_project_daily_efficiency_evidence_without_model_identity(self):
        rows = meter.delivery_spend_rows([{
            "project": "/repo",
            "availability": {"cost": True},
            "_day_cost": {"2026-09-03": 12.5},
            "_model_daily": [
                {
                    "day": "2026-09-03", "executions": 3,
                    "cost_covered_executions": 2,
                    "cost_covered_cost": 10.0,
                    "cost_covered_output_tokens": 4000,
                    "reasoning_tokens": 1000,
                    "reasoning_output_tokens": 4000,
                    "reasoning_executions": 2,
                },
                {
                    "day": "2026-09-03", "executions": 1,
                    "cost_covered_executions": 0,
                    "reasoning_unavailable_executions": 1,
                },
            ],
        }])

        self.assertEqual(rows, [{
            "project": meter.delivery_project_label("/repo"),
            "day": "2026-09-03",
            "covered_cost": 12.5,
            "cost_available": True,
            "efficiency_covered_cost": 10.0,
            "covered_output_tokens": 4000,
            "output_available": True,
            "output_partial": True,
            "reasoning_tokens": 1000,
            "reasoning_output_tokens": 4000,
            "reasoning_available": True,
            "reasoning_partial": True,
        }])


class GitDeliveryHttpContractTests(unittest.TestCase):
    def request(self, method, path, body=None, headers=None):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        server.timeout = 1
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()
            worker.join(timeout=2)
            server.server_close()

    def test_get_delivery_returns_only_bounded_safe_projection(self):
        payload = {
            "ok": True,
            "projects": ["private-project · acfdbe"],
            "days": [],
            "overall": {},
            "previous": {},
            "comparison": {},
            "project_rows": [],
            "coverage": {},
        }
        with mock.patch.object(meter, "git_delivery_state", return_value=payload):
            status, body = self.request("GET", "/git-delivery?range=7")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"], ["private-project · acfdbe"])
        self.assertNotIn("/Users/alice", body)

    def test_clear_requires_action_token_and_explicit_confirmation(self):
        body = json.dumps({"confirm": True})
        status, response = self.request(
            "POST", "/git-delivery/clear", body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )

        self.assertEqual(status, 403)
        self.assertIn("Invalid action token", response)


class GitDashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter._SOURCE_ROOT, "page.html").read_text(encoding="utf-8")

    def test_git_is_a_dedicated_answer_first_page(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for marker in (
            "Pushed code &times; covered spend.", "Pushed lines", "Spend / 1K",
            "Spend coverage", "Code pushed by day",
            "id=d-daily-chart", "id=d-project-table", "data-delivery-sort",
            ">Projects<",
            "id=d-coverage-bar", "id=d-coverage-percent", "id=d-active-days",
            "Local Git evidence &middot; Text changes only &middot; Not a quality score.",
        ):
            self.assertIn(marker, git_page)
        self.assertIn("rolling_spend_per_1k", self.page)
        self.assertIn(
            "#view-git .spectrumPageActions .modelControls{grid-template-columns:repeat(2",
            self.page,
        )
        self.assertNotIn("commit", git_page.lower())
        self.assertNotIn("radial-gradient", git_page)
        self.assertIn("Last 12 months", git_page)
        for repeated_copy in (
            "Review pushed code, cost intensity, and project coverage.",
            "Period-over-period signal from locally observed successful pushes.",
            "Pushed text lines by day, paired with trailing seven-day cost intensity.",
            "Projects with period activity",
            "Text additions plus deletions observed after successful named-remote pushes.",
        ):
            self.assertNotIn(repeated_copy, git_page)

    def test_git_uses_one_compact_overview_then_trend_and_projects(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for marker in (
            'class="card deliveryOverview"',
            'class=deliveryMetricGrid',
            'class=deliveryCoverageBar',
            'class=deliveryEvidenceStrip',
            '.deliveryOverview{',
            '.deliveryCoverageBar{',
            '.deliveryOverviewHead{min-height:68px',
            '.deliveryChartWrap{position:relative;height:270px',
        ):
            self.assertIn(marker, self.page)
        for removed_structure in (
            'class=deliveryBrief', 'class="card deliveryOutcome"',
            'class="card deliveryEvidence"', 'class=deliveryDrivers',
            'class=deliveryDriverGrid', 'deliveryCoverageRing',
        ):
            self.assertNotIn(removed_structure, git_page)
        self.assertEqual(git_page.count('class="card deliveryOverview"'), 1)
        self.assertLess(git_page.index('class="card deliveryOverview"'), git_page.index('class="card deliveryVisual"'))
        self.assertLess(git_page.index('class="card deliveryVisual"'), git_page.index('class="card deliveryProjects"'))

    def test_git_omits_efficiency_driver_presentation(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

        for removed_marker in (
            "Efficiency drivers", "class=deliveryDriverStrip",
            "id=d-driver-summary", "id=d-output-dollar", "id=d-delivery-yield",
            "id=d-reasoning-ratio", "class=deliveryDriverValue",
        ):
            self.assertNotIn(removed_marker, git_page)
        for removed_code in (
            ".deliveryDriverStrip{", "function deliveryDriverChange",
            "function deliveryDriverSummary", "$('d-driver-summary')",
        ):
            self.assertNotIn(removed_code, self.page)
        for removed_label in (
            "<span>Output / $</span>", "<span>Push yield</span>",
            "<span>Reasoning</span>",
        ):
            self.assertNotIn(removed_label, git_page)

    def test_git_projects_become_sortable_cards_at_narrow_width(self):
        for marker in (
            ".deliveryMobileSort{display:none}",
            "@media(max-width:700px){.deliveryMobileSort{display:grid",
            ".deliveryTableWrap thead{display:none}",
            ".deliveryTableWrap tbody{display:grid",
            "id=d-mobile-sort",
            'data-label="Covered spend"',
            "$('d-mobile-sort').addEventListener('change'",
        ):
            self.assertIn(marker, self.page)

    def test_git_overview_exposes_bounded_coverage_and_activity_evidence(self):
        for marker in (
            "coverageBar.style.setProperty('--delivery-coverage'",
            "coverageBar.setAttribute('aria-valuenow'",
            "coverageBar.classList.toggle('partial'",
            "$('d-coverage-percent').textContent",
            "$('d-active-days').textContent",
            "$('d-period-label').textContent",
            "days.filter(row=>(Number(row?.changed_lines)||0)>0).length",
            "No Git-backed projects discovered",
        ):
            self.assertIn(marker, self.page)

    def test_git_coverage_never_exposes_unavailable_as_zero(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        coverage_bar = git_page.split("id=d-coverage-bar", 1)[1].split("><i", 1)[0]
        load_delivery = self.page.split("async function loadDelivery()", 1)[1].split(
            "$('d-project').addEventListener", 1,
        )[0]

        self.assertNotIn("aria-valuenow=0", coverage_bar)
        self.assertIn('aria-valuetext="Spend coverage unavailable"', coverage_bar)
        self.assertIn("coverageBar.removeAttribute('aria-valuenow')", load_delivery)
        self.assertIn(
            "coverageBar.setAttribute('aria-valuetext','Spend coverage unavailable')",
            load_delivery,
        )
        self.assertIn("$('d-coverage-percent').textContent='--'", load_delivery)

    def test_git_chart_inspector_opens_for_data_and_closes_outside(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        self.assertIn("function selectDeliveryDay", self.page)
        self.assertIn("function dismissGitChartInspector", self.page)
        self.assertIn("y2=${y} />", self.page)
        self.assertIn("rx=2 />", self.page)
        self.assertIn("addEventListener('pointerenter'", self.page)
        self.assertIn("addEventListener('focus'", self.page)
        self.assertIn("addEventListener('click'", self.page)
        self.assertIn(
            "if(!target?.closest('#d-chart-wrap')&&!target?.closest('#d-map')"
            "&&!target?.closest('#d-day-inspector'))"
            "dismissGitChartInspector()",
            self.page,
        )
        self.assertIn(
            "if(event.key==='Escape'){dismissGitChartInspector();hideDeliveryMapTip();}",
            self.page,
        )
        self.assertIn("deliverySelectedDay=''", self.page)
        self.assertIn("button.setAttribute('aria-pressed','false')", self.page)
        self.assertNotIn("d-daily-chart').addEventListener('pointerleave'", self.page)
        self.assertNotIn("<span>Output / $</span>", git_page)
        self.assertNotIn("<span>Push yield</span>", git_page)
        self.assertNotIn("<span>Reasoning</span>", git_page)

    def test_git_chart_hover_tip_hides_when_pointer_leaves_plot(self):
        hide_tip = "function hideDeliveryChartTip()" + self.page.split(
            "function hideDeliveryChartTip()", 1,
        )[1].split("function selectDeliveryDay", 1)[0]
        binding = re.search(
            r"\$\('d-chart-hits'\)\.addEventListener\('pointerleave',event=>"
            r"\{[^\n]+\}\);",
            self.page,
        ).group(0)
        driver = f"""
const listeners={{}};
const tip={{hidden:false}},inspector={{hidden:false}};
const nodes={{
 'd-chart-tip':tip,
 'd-day-inspector':inspector,
 'd-chart-hits':{{addEventListener(name,handler){{listeners[name]=handler;}}}},
}};
const $=id=>nodes[id];
let deliverySelectedDay='2026-09-01';
{hide_tip}
{binding}
const state=()=>({{tipHidden:tip.hidden,inspectorHidden:inspector.hidden,
 selectedDay:deliverySelectedDay}});
listeners.pointerleave({{pointerType:'mouse'}});const mouse=state();
tip.hidden=false;listeners.pointerleave({{pointerType:'pen'}});const pen=state();
tip.hidden=false;listeners.pointerleave({{pointerType:'touch'}});const touch=state();
console.log(JSON.stringify({{mouse,pen,touch}}));
"""
        result = subprocess.run(
            ["node", "-e", driver], capture_output=True, text=True, check=True,
        )

        self.assertEqual(
            json.loads(result.stdout),
            {
                "mouse": {
                    "tipHidden": True,
                    "inspectorHidden": False,
                    "selectedDay": "2026-09-01",
                },
                "pen": {
                    "tipHidden": True,
                    "inspectorHidden": False,
                    "selectedDay": "2026-09-01",
                },
                "touch": {
                    "tipHidden": False,
                    "inspectorHidden": False,
                    "selectedDay": "2026-09-01",
                },
            },
        )
        self.assertEqual(
            self.page.count(
                "$('d-chart-hits').addEventListener('pointerleave',event=>"
            ),
            1,
        )

    def test_git_is_the_canonical_name_and_delivery_hash_is_compatible(self):
        for marker in (
            "id=tab-git data-label=Git aria-label=Git",
            "title=\"Git · Shortcut: Option+5\"",
            "<span class=tabLabel>Git</span>",
            "<div class=view id=view-git>",
            "<h1>Git</h1>",
            "{id:'git',label:'Git'",
            "route:'git',directKey:'Digit5'",
            "if(h==='delivery')setHashRoute('git',{replace:true,apply:false})",
            "if(h==='git'||h==='delivery')",
            "showTab('git')",
            "openTopLevelRoute('git')",
            "Git evidence history",
        ):
            self.assertIn(marker, self.page)
        for removed in (
            "id=tab-delivery", "data-label=Delivery", "aria-label=Delivery",
            "<span class=tabLabel>Delivery</span>", "<h1>Delivery</h1>",
            "label:'Delivery'", "route:'delivery',directKey:'Digit5'",
        ):
            self.assertNotIn(removed, self.page)

        docs = {
            "README.md": Path(meter._SOURCE_ROOT, "README.md").read_text(encoding="utf-8"),
            "specs/USER_GUIDE.md": Path(meter._SOURCE_ROOT, "specs/USER_GUIDE.md").read_text(encoding="utf-8"),
            "specs/ARCHITECTURE.md": Path(meter._SOURCE_ROOT, "specs/ARCHITECTURE.md").read_text(encoding="utf-8"),
            "specs/SECURITY.md": Path(meter._SOURCE_ROOT, "specs/SECURITY.md").read_text(encoding="utf-8"),
            "specs/AGENTS.md": Path(meter._SOURCE_ROOT, "specs/AGENTS.md").read_text(encoding="utf-8"),
        }
        self.assertIn("### Git", docs["README.md"])
        self.assertIn("### Git", docs["specs/USER_GUIDE.md"])
        expected_order = (
            "Sessions → Spend → Models → Efficiency → Git → Learn → Tools → Settings"
        )
        self.assertIn(expected_order, " ".join(docs["specs/ARCHITECTURE.md"].split()))
        self.assertIn(expected_order, " ".join(docs["specs/AGENTS.md"].split()))
        self.assertIn("The Git page is a local-only Git reader", docs["specs/SECURITY.md"])

class GitDeliveryEconomicsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(meter._SOURCE_ROOT, "page.html").read_text(encoding="utf-8")

    def git_view(self):
        return self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]

    def run_js(self, names, driver, blocks=(), consts=()):
        script = delivery_js(self.page, names, blocks, consts) + driver
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_overview_adds_push_yield_and_deleted_share_evidence(self):
        git_page = self.git_view()

        for marker in (
            "id=d-push-yield", "id=d-push-yield-note", ">Push yield<",
            "id=d-rework", ">Deleted share<",
        ):
            self.assertIn(marker, git_page)
        self.assertIn(
            ".deliveryMetricGrid{display:grid;grid-template-columns:minmax(0,1.04fr)",
            self.page,
        )
        self.assertIn("$('d-push-yield').textContent=yieldAvailable", self.page)
        self.assertIn("Waiting for output-token coverage.", self.page)

    def test_delivery_economics_section_follows_the_daily_chart(self):
        git_page = self.git_view()

        for marker in (
            "class=deliveryInsightSection", ">Delivery economics<", ">Signals<",
            ">Daily shape<", ">Cost by pushed lines<", "id=d-signals",
            "id=d-shape", "id=d-map", "id=d-map-svg", "id=d-insight-range",
            "id=d-map-coverage",
        ):
            self.assertIn(marker, git_page)
        self.assertLess(
            git_page.index('class="card deliveryVisual"'),
            git_page.index("class=deliveryInsightSection"),
        )
        self.assertLess(
            git_page.index("class=deliveryInsightSection"),
            git_page.index('class="card deliveryProjects"'),
        )
        self.assertEqual(git_page.count("class=deliveryInsightSection"), 1)
        self.assertIn(
            'aria-description="Conversion, typical days, and outliers. '
            'Statistics only · no quality judgment."',
            git_page,
        )

    def test_git_evidence_explorer_and_linked_day_inspector_are_present(self):
        git_page = self.git_view()

        for marker in (
            "aria-controls=d-evidence-panel", "id=d-evidence-panel hidden",
            "id=d-evidence-spend", "id=d-evidence-freshness",
            "data-delivery-evidence-filter=comparable",
            "id=d-day-inspector hidden", "id=d-day-prev", "id=d-day-next",
            "id=d-day-close", "id=d-evidence-filter",
            ">Evidence<", "Below 50 pushed lines",
        ):
            self.assertIn(marker, git_page)

        self.assertIn(
            ".deliveryMapHit[aria-pressed=true] .deliveryMapPoint", self.page,
        )
        self.assertIn(".deliveryMapPoint.lowVolume", self.page)
        self.assertIn(".deliveryChartTip{display:none!important}", self.page)

    def test_signals_card_keeps_a_fixed_scrollable_frame(self):
        card_rule = re.search(
            r"\.deliverySignalCard\{([^}]*)\}", self.page,
        )
        list_rule = re.search(
            r"\.deliverySignalList\{([^}]*)\}", self.page,
        )

        self.assertIsNotNone(card_rule)
        self.assertIsNotNone(list_rule)
        for declaration in (
            "display:flex", "height:360px", "min-height:0",
            "flex-direction:column",
        ):
            self.assertIn(declaration, card_rule.group(1))
        for declaration in (
            "flex:1", "min-height:0", "overflow-y:auto",
            "overscroll-behavior:contain", "scrollbar-gutter:stable",
        ):
            self.assertIn(declaration, list_rule.group(1))

    def test_projects_table_keeps_only_the_primary_delivery_measures(self):
        git_page = self.git_view()
        table = git_page.split("id=d-project-table", 1)[1].split(
            "</table>", 1,
        )[0]

        for label in ("Project", "Covered spend", "Code pushed", "Spend / 1K"):
            self.assertIn(label, table)
        for removed in (
            ">Evidence<", "Vs previous period",
            "data-delivery-sort=comparison", "colspan=6",
        ):
            self.assertNotIn(removed, table)
        self.assertIn("colspan=4", table)
        self.assertIn("id=d-evidence-filter", git_page)
        renderer = self.page.split("function renderDeliveryTable", 1)[1].split(
            "function deliveryDateLabel", 1,
        )[0]
        self.assertNotIn("data-label=\"Evidence\"", renderer)
        self.assertNotIn("data-label=\"Change\"", renderer)
        self.assertNotIn("deliveryEvidenceBadge", self.page)
        self.assertNotIn("if(key==='comparison')", self.page)
        self.assertNotIn("'spend_per_1k','comparison'", self.page)

    def test_git_compact_project_selects_do_not_clip_their_text(self):
        self.assertIn(
            ".deliveryEvidenceFilter select,.deliveryMobileSort select"
            "{padding-top:5px;padding-bottom:5px}",
            self.page,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_project_evidence_statuses_and_filters_keep_unavailable_distinct(self):
        driver = """
const rows=[
 {project:'both',changed_lines:100,covered_cost:5,availability:{code_pushed:true,cost:true}},
 {project:'spend',changed_lines:0,covered_cost:5,availability:{code_pushed:false,cost:true}},
 {project:'git',changed_lines:100,covered_cost:0,availability:{code_pushed:true,cost:false}},
 {project:'none',changed_lines:0,covered_cost:0,availability:{code_pushed:false,cost:false}},
];
console.log(JSON.stringify({
 statuses:rows.map(row=>deliveryProjectEvidence(row)),
 comparable:deliveryFilteredProjectRows(rows,'comparable').map(row=>row.project),
 spend:deliveryFilteredProjectRows(rows,'spend_only').map(row=>row.project),
 git:deliveryFilteredProjectRows(rows,'git_only').map(row=>row.project),
 unavailable:deliveryFilteredProjectRows(rows,'unavailable').map(row=>row.project),
 all:deliveryFilteredProjectRows(rows,'all').map(row=>row.project),
}));
"""
        payload = self.run_js(
            ["deliveryProjectEvidence", "deliveryFilteredProjectRows"], driver,
            consts=(r"^const DELIVERY_EVIDENCE_FILTERS=\[.*\];$",),
        )

        self.assertEqual(
            [status["key"] for status in payload["statuses"]],
            ["comparable", "spend_only", "git_only", "unavailable"],
        )
        self.assertEqual(
            [status["label"] for status in payload["statuses"]],
            ["Comparable", "Spend only", "Git only", "No period evidence"],
        )
        self.assertEqual(payload["comparable"], ["both"])
        self.assertEqual(payload["spend"], ["spend"])
        self.assertEqual(payload["git"], ["git"])
        self.assertEqual(payload["unavailable"], ["none"])
        self.assertEqual(payload["all"], ["both", "spend", "git", "none"])

    def test_daily_chart_explains_one_pushed_code_measure(self):
        git_page = self.git_view()

        self.assertIn("Code pushed by day", git_page)
        self.assertIn(
            'aria-description="Added + deleted text lines from successful pushes. '
            'Select a day for spend details."',
            git_page,
        )
        self.assertNotIn(
            "<p>Added + deleted text lines from successful pushes. "
            "Select a day for spend details.</p>",
            git_page,
        )
        self.assertIn('aria-label="Daily pushed text lines"', git_page)
        for removed_marker in (
            "7-day Spend / 1K", ">Typical day<", ">Spend, no push<",
            "id=d-legend-typical", "id=d-legend-quiet",
        ):
            self.assertNotIn(removed_marker, git_page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_git_evidence_ratio_help_resets_when_refresh_becomes_unavailable(self):
        helper = _extract_block(
            self.page, "function setDeliveryEvidenceRatio(text,detail)",
        )
        driver = """
const ratio={textContent:'',dataset:{},description:'',setAttribute(name,value){
 if(name==='aria-description')this.description=value;
}};
const $=id=>ratio;
""" + helper + """
setDeliveryEvidenceRatio('1 / 20 comparable','1 of 20 projects can compare ratios.');
const loaded={text:ratio.textContent,tip:ratio.dataset.tip,description:ratio.description};
setDeliveryEvidenceRatio(
 'Git evidence unavailable.',
 'Git evidence unavailable. Ratios require overlapping spend and Git evidence.'
);
console.log(JSON.stringify({loaded,unavailable:{
 text:ratio.textContent,tip:ratio.dataset.tip,description:ratio.description
}}));
"""
        result = subprocess.run(
            ["node", "-e", driver], capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "loaded": {
                "text": "1 / 20 comparable",
                "tip": "1 of 20 projects can compare ratios.",
                "description": "1 of 20 projects can compare ratios.",
            },
            "unavailable": {
                "text": "Git evidence unavailable.",
                "tip": "Git evidence unavailable. Ratios require overlapping spend and Git evidence.",
                "description": "Git evidence unavailable. Ratios require overlapping spend and Git evidence.",
            },
        })
        self.assertIn("setDeliveryEvidenceRatio(selected?", self.page)
        self.assertIn("setDeliveryEvidenceRatio('Git evidence unavailable.'", self.page)
        self.assertNotIn(
            "$('d-evidence-ratio').textContent='Git evidence unavailable.'",
            self.page,
        )

    def test_project_rows_encode_spend_share_and_line_composition(self):
        for marker in (
            "class=deliverySpendShare", "class=deliveryComposition",
            ".deliverySpendShare i{display:block;width:calc(var(--share,0)*1%)",
            "of covered spend in this period",
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_ratio_formatting_never_truncates_a_rounded_whole_number(self):
        payload = self.run_js(
            ["deliveryRatio"],
            "\nconsole.log(JSON.stringify({"
            "low:deliveryRatio(7.1224),trailing:deliveryRatio(7.10),"
            "whole:deliveryRatio(7),rounded:deliveryRatio(99.996),"
            "tens:deliveryRatio(25.077),large:deliveryRatio(1500),"
            "missing:deliveryRatio(null)}));",
        )

        self.assertEqual(payload, {
            "low": "7.12", "trailing": "7.1", "whole": "7", "rounded": "100",
            "tens": "25.1", "large": "1.5K", "missing": "--",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_daily_series_drop_quiet_days_and_low_volume_ratio_days(self):
        driver = """
const payload={days:[
 {day:'2026-01-01',changed_lines:0,comparable_changed_lines:0,covered_cost:4,spend_per_1k:null,
  availability:{code_pushed:true,cost:true,spend_per_1k:false},
  efficiency:{delivery_yield:null,availability:{delivery_yield:false}}},
 {day:'2026-01-02',changed_lines:10,comparable_changed_lines:10,covered_cost:9,spend_per_1k:900,
  availability:{code_pushed:true,cost:true,spend_per_1k:true},
  efficiency:{delivery_yield:.4,availability:{delivery_yield:true}}},
 {day:'2026-01-03',changed_lines:200,comparable_changed_lines:200,covered_cost:4,spend_per_1k:20,
  availability:{code_pushed:true,cost:true,spend_per_1k:true},
  efficiency:{delivery_yield:8,availability:{delivery_yield:true}}},
 {day:'2026-01-04',changed_lines:600,comparable_changed_lines:600,covered_cost:6,spend_per_1k:10,
  availability:{code_pushed:true,cost:true,spend_per_1k:true},
  efficiency:{delivery_yield:12,availability:{delivery_yield:true}}},
]};
console.log(JSON.stringify({
 intensity:deliveryDaySeries(payload,'spend_per_1k'),
 lines:deliveryDaySeries(payload,'changed_lines'),
 yield:deliveryDaySeries(payload,'delivery_yield'),
 empty:deliveryDistribution([]),
 spread:deliveryDistribution([10,20,30,120]),
}));
"""
        payload = self.run_js(
            [
                "deliveryQuantile", "deliveryDistribution", "deliveryRatioDay",
                "deliveryIntensityDays", "deliveryDaySeries",
            ],
            driver,
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual(payload["intensity"], [20, 10])
        self.assertEqual(payload["lines"], [10, 200, 600])
        self.assertEqual(payload["yield"], [8, 12])
        self.assertIsNone(payload["empty"])
        self.assertEqual(payload["spread"], {
            "count": 4, "min": 10, "max": 120,
            "p25": 17.5, "median": 25, "p75": 52.5,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_map_points_require_both_pushed_lines_and_covered_spend(self):
        driver = """
const payload={days:[
 {day:'2026-01-01',comparable_changed_lines:0,covered_cost:5,availability:{cost:true}},
 {day:'2026-01-02',comparable_changed_lines:400,covered_cost:0,availability:{cost:true}},
 {day:'2026-01-03',comparable_changed_lines:400,covered_cost:8,availability:{cost:false}},
 {day:'2026-01-04',comparable_changed_lines:49,covered_cost:10,availability:{cost:true}},
 {day:'2026-01-05',comparable_changed_lines:500,covered_cost:10,availability:{cost:true}},
]};
console.log(JSON.stringify(deliveryMapPoints(payload)));
"""
        payload = self.run_js(
            ["deliveryMapPoints"], driver,
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual(payload, [
            {"day": "2026-01-04", "lines": 49, "cost": 10,
             "intensity": 10000 / 49, "lowVolume": True},
            {"day": "2026-01-05", "lines": 500, "cost": 10,
             "intensity": 20, "lowVolume": False},
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_signal_actions_link_peak_day_project_and_coverage_explorer(self):
        driver = """
const day=(iso,intensity)=>({day:iso,changed_lines:200,comparable_changed_lines:200,
 covered_cost:intensity*.2,spend_per_1k:intensity,
 availability:{code_pushed:true,cost:true,spend_per_1k:true},
 efficiency:{availability:{delivery_yield:false}}});
const payload={
 overall:{added:800,deleted:200,changed_lines:1000,availability:{code_pushed:true},
  efficiency:{availability:{delivery_yield:false}}},
 comparison:{available:false},coverage:{selected_repositories:4,comparable_repositories:2},
 days:[day('2026-01-01',10),day('2026-01-02',20),day('2026-01-03',30),day('2026-01-04',120)],
 project_rows:[
  {project:'alpha · aaaaaa',covered_cost:90,availability:{cost:true}},
  {project:'beta · bbbbbb',covered_cost:10,availability:{cost:true}},
 ],
};
console.log(JSON.stringify(deliverySignals(payload).filter(row=>row.action).map(row=>({
 key:row.key,action:row.action,value:row.value,
}))));
"""
        payload = self.run_js(
            [
                "deliveryPercent", "deliveryRatio", "deliveryMultiple",
                "deliveryQuantile", "deliveryDistribution", "deliveryRatioDay",
                "deliveryIntensityDays", "deliveryDateLabel",
                "deliveryProjectName", "deliverySignals",
            ],
            driver,
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual(payload, [
            {"key": "peak_day", "action": "day", "value": "2026-01-04"},
            {"key": "concentration", "action": "project", "value": "alpha · aaaaaa"},
            {"key": "coverage", "action": "coverage", "value": ""},
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_peak_day_signal_selects_after_scrolling_the_chart(self):
        driver = """
let handler=null,selected='',deliveryIgnorePointerSelection=false;
const button={dataset:{deliverySignalAction:'day',deliverySignalValue:'2026-01-04'},
 addEventListener:(name,callback)=>{if(name==='click')handler=callback;}};
const list={innerHTML:'',querySelectorAll:()=>[button]};
const chart={scrollIntoView:()=>{selected='2026-01-01';}};
const $=id=>id==='d-signals'?list:id==='d-chart-wrap'?chart:null;
const esc=value=>String(value);
const deliverySignals=()=>[{kind:'warn',title:'Peak day',text:'Open day',
 action:'day',value:'2026-01-04'}];
const selectDeliveryDay=day=>{selected=day;};
const selectDeliveryProject=()=>{};
const setDeliveryEvidencePanel=()=>{};
renderDeliverySignals({});
handler({stopPropagation(){}});
const afterSignal=selected;
selectDeliveryDayFromPointer('2026-01-01');
const afterIncidentalHover=selected;
deliveryIgnorePointerSelection=false;
selectDeliveryDayFromPointer('2026-01-02');
console.log(JSON.stringify({afterSignal,afterIncidentalHover,afterPointerMove:selected}));
"""
        payload = self.run_js(
            ["selectDeliveryDayFromPointer", "renderDeliverySignals"], driver,
        )

        self.assertEqual(payload, {
            "afterSignal": "2026-01-04",
            "afterIncidentalHover": "2026-01-04",
            "afterPointerMove": "2026-01-02",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_signals_rank_trend_yield_outlier_and_evidence_caveats(self):
        driver = """
const day=(iso,lines,cost,intensity)=>({day:iso,changed_lines:lines,
 comparable_changed_lines:lines,covered_cost:cost,spend_per_1k:intensity,
 availability:{code_pushed:true,cost:true,spend_per_1k:intensity!==null},
 efficiency:{delivery_yield:null,availability:{delivery_yield:false}}});
const payload={
 overall:{added:800,deleted:200,changed_lines:1000,covered_cost:50,spend_per_1k:50,
  availability:{cost:true,code_pushed:true,spend_per_1k:true},
  efficiency:{delivery_yield:6.5,availability:{delivery_yield:true}}},
 comparison:{available:true,code_pushed_pct:120,spend_per_1k_pct:-25,delivery_yield_pct:30},
 coverage:{selected_repositories:4,comparable_repositories:2},
 days:[day('2026-01-01',200,2,10),day('2026-01-02',200,4,20),
  day('2026-01-03',200,6,30),day('2026-01-04',200,24,120),
  day('2026-01-05',10,9,900),day('2026-01-06',0,7,null)],
 project_rows:[],
};
console.log(JSON.stringify(deliverySignals(payload).map(row=>(
 {key:row.key,kind:row.kind,title:row.title,text:row.text}))));
"""
        payload = self.run_js(
            [
                "deliveryPercent", "deliveryRatio", "deliveryMultiple",
                "deliveryQuantile", "deliveryDistribution", "deliveryRatioDay",
                "deliveryIntensityDays", "deliveryDateLabel",
                "deliveryProjectName", "deliverySignals",
            ],
            driver,
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual([row["key"] for row in payload], [
            "intensity_trend", "push_yield", "peak_day", "quiet_days",
            "coverage",
        ])
        self.assertEqual([row["kind"] for row in payload], [
            "neutral", "neutral", "warn", "neutral", "neutral",
        ])
        self.assertEqual(payload[0]["title"], "Cost intensity fell 25%")
        self.assertEqual(
            payload[1]["title"], "6.5 pushed lines per 1K output tokens",
        )
        self.assertIn("4.8× the typical day per 1K lines", payload[2]["title"])
        self.assertEqual(
            payload[3]["title"],
            "1 of 6 days had covered spend and no pushed lines",
        )
        self.assertEqual(
            payload[4]["title"],
            "2 of 4 projects compare cost with Git evidence",
        )
        self.assertEqual(
            payload[4]["text"],
            "Ratios use only projects with comparable evidence; projects outside "
            "that coverage may change the result.",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_signals_report_coverage_and_concentration_without_a_trend(self):
        driver = """
const payload={
 overall:{added:0,deleted:0,changed_lines:0,covered_cost:0,
  availability:{cost:false,code_pushed:false,spend_per_1k:false},
  efficiency:{availability:{delivery_yield:false}}},
 comparison:{available:false},
 coverage:{selected_repositories:5,comparable_repositories:1},
 days:[],
 project_rows:[
  {project:'alpha · aaaaaa',covered_cost:90,availability:{cost:true}},
  {project:'beta · bbbbbb',covered_cost:10,availability:{cost:true}},
 ],
};
        console.log(JSON.stringify(deliverySignals(payload).map(row=>(
         {key:row.key,kind:row.kind,title:row.title,text:row.text}))));
"""
        payload = self.run_js(
            [
                "deliveryPercent", "deliveryRatio", "deliveryMultiple",
                "deliveryQuantile", "deliveryDistribution", "deliveryRatioDay",
                "deliveryIntensityDays", "deliveryDateLabel",
                "deliveryProjectName", "deliverySignals",
            ],
            driver,
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual([row["key"] for row in payload], [
            "concentration", "coverage",
        ])
        self.assertEqual(
            payload[0]["title"], "90% of covered spend sits in alpha",
        )
        self.assertEqual(
            payload[1]["title"],
            "1 of 5 projects compare cost with Git evidence",
        )
        self.assertEqual(
            payload[1]["text"],
            "Ratios use only projects with comparable evidence; projects outside "
            "that coverage may change the result.",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_git_trends_describe_direction_without_good_or_bad_tones(self):
        driver = """
const esc=value=>String(value);
console.log(JSON.stringify({
 comparison:deliveryComparisonText({available:true,code_pushed_pct:12,spend_per_1k_pct:-8}),
 higher:deliveryTrendText(12,true),
 lower:deliveryTrendText(-8,false),
}));
"""
        payload = self.run_js(
            ["deliveryPercent", "deliveryComparisonText", "deliveryTrendText"],
            driver,
        )

        self.assertNotIn("class=good", payload["comparison"])
        self.assertNotIn("deliveryTrendBad", payload["comparison"])
        self.assertEqual(payload["higher"]["tone"], "deliveryTrendNeutral")
        self.assertEqual(payload["lower"]["tone"], "deliveryTrendNeutral")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_map_pointer_selection_survives_the_bubbled_document_click(self):
        preamble = """
const handlers={};
class Element{
 constructor(areas=[]){this.areas=areas;}
 closest(selector){return this.areas.includes(selector)?this:null;}
}
const buttons=[{dataset:{deliveryDay:'2026-01-01'},pressed:'false',
 setAttribute(name,value){if(name==='aria-pressed')this.pressed=value;},scrollIntoView(){}}];
const mapButtons=[{dataset:{deliveryMapDay:'2026-01-01'},pressed:'false',
 setAttribute(name,value){if(name==='aria-pressed')this.pressed=value;}}];
const nodes={
 'd-chart-tip':{hidden:true,innerHTML:'',style:{}},
 'd-chart-hits':{querySelectorAll(selector){
  return selector.includes('[aria-pressed=true]')?buttons.filter(button=>button.pressed==='true'):buttons;
 }},
 'd-map-svg':{querySelectorAll(selector){
  return selector.includes('[aria-pressed=true]')?mapButtons.filter(button=>button.pressed==='true'):mapButtons;
 }},
 'd-day-inspector':{hidden:true},
 'd-day-title':{textContent:''},'d-day-note':{textContent:''},
 'd-day-metrics':{innerHTML:''},'d-day-prev':{disabled:false},'d-day-next':{disabled:false},
};
const $=id=>nodes[id];
const document={activeElement:null,addEventListener:(name,handler)=>{handlers[name]=handler;}};
const esc=value=>String(value);
const hideDeliveryMapTip=()=>{};
let deliverySelectedDay='';
const deliveryPayload={days:[{day:'2026-01-01',added:80,deleted:20,covered_cost:2,
 changed_lines:100,comparable_changed_lines:100,spend_per_1k:20,rolling_spend_per_1k:20,
 availability:{cost:true,spend_per_1k:true,rolling_spend_per_1k:true}}]};
"""
        click_start = self.page.index("document.addEventListener('click',event=>{")
        click_end = self.page.index("\n});", click_start) + len("\n});")
        driver = """
selectDeliveryDay('2026-01-01');
handlers.click({target:new Element(['#d-map'])});
const afterMap={selected:deliverySelectedDay,pressed:buttons[0].pressed,
 mapPressed:mapButtons[0].pressed,hidden:nodes['d-chart-tip'].hidden,
 inspectorHidden:nodes['d-day-inspector'].hidden,dayTitle:nodes['d-day-title'].textContent};
handlers.click({target:new Element([])});
console.log(JSON.stringify({afterMap,afterOutside:{selected:deliverySelectedDay,
 pressed:buttons[0].pressed,mapPressed:mapButtons[0].pressed,
 hidden:nodes['d-chart-tip'].hidden,inspectorHidden:nodes['d-day-inspector'].hidden}}));
"""
        script = preamble + delivery_js(
            self.page, [
                "deliveryDateLabel", "renderDeliveryDayInspector",
                "dismissGitChartInspector", "selectDeliveryDay",
            ],
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        ) + "\n" + self.page[click_start:click_end] + driver
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )

        self.assertEqual(json.loads(result.stdout), {
            "afterMap": {
                "selected": "2026-01-01", "pressed": "true",
                "mapPressed": "true", "hidden": False,
                "inspectorHidden": False, "dayTitle": "Jan 1",
            },
            "afterOutside": {
                "selected": "", "pressed": "false", "mapPressed": "false",
                "hidden": True, "inspectorHidden": True,
            },
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_shape_uses_observed_range_until_five_days_qualify(self):
        driver = """
console.log(JSON.stringify({
 sparse:deliveryShapeRange(deliveryDistribution([10,20,120])),
 stable:deliveryShapeRange(deliveryDistribution([10,20,30,40,50])),
}));
"""
        payload = self.run_js(
            ["deliveryQuantile", "deliveryDistribution", "deliveryShapeRange"],
            driver,
        )

        self.assertEqual(payload["sparse"], {
            "label": "Observed range", "low": 10, "high": 120,
        })
        self.assertEqual(payload["stable"], {
            "label": "Middle half", "low": 20, "high": 40,
        })

    def test_narrow_git_charts_use_scrollable_legible_viewports(self):
        git_page = self.git_view()

        for marker in (
            "class=deliveryChartViewport", "class=deliveryMapViewport",
            "class=deliveryMobileScrollHint",
        ):
            self.assertIn(marker, git_page)
        for marker in (
            ".deliveryChartViewport,.deliveryMapViewport{",
            ".deliveryChartWrap,.deliveryMapWrap{min-width:720px}",
            ".deliveryMobileScrollHint{display:none}",
            ".deliveryShapeHeader{display:none}",
            'data-label="${esc(range.label)}"',
        ):
            self.assertIn(marker, self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_daily_chart_draws_only_pushed_line_bars(self):
        preamble = """
const nodes={};
const stub=()=>({innerHTML:'',hidden:false,style:{setProperty(){}},
 querySelectorAll:()=>[],classList:{toggle(){},add(){}},setAttribute(){},textContent:''});
const $=id=>(nodes[id]=nodes[id]||stub());
const esc=value=>String(value);
let deliverySelectedDay='';
const deliveryPayload={days:[]};
"""
        driver = """
const day=(iso,lines,cost,intensity)=>({day:iso,added:Math.round(lines*.8),
 deleted:Math.round(lines*.2),changed_lines:lines,comparable_changed_lines:lines,
 covered_cost:cost,spend_per_1k:intensity,rolling_spend_per_1k:intensity,
 availability:{code_pushed:true,cost:true,spend_per_1k:intensity!==null,
  rolling_spend_per_1k:intensity!==null},
 efficiency:{delivery_yield:null,availability:{delivery_yield:false}}});
const earned=[day('2026-01-01',200,2,10),day('2026-01-02',200,4,20),
 day('2026-01-03',200,6,30),day('2026-01-04',200,24,120),day('2026-01-05',0,7,null)];
drawDeliveryChart(earned);
const full=nodes['d-daily-chart'].innerHTML;
const report={
 added:(full.match(/class=added /g)||[]).length,
 deleted:(full.match(/class=deleted /g)||[]).length,
 costLine:/class=costLine /.test(full),
 costArea:/class=costArea /.test(full),
 costDot:/class=costDot /.test(full),
 typical:/class=typical /.test(full),
 quiet:/class=quiet /.test(full),
 spendAxis:full.includes('7-DAY SPEND / 1K LINES'),
 hitTop:nodes['d-chart-hits'].style.top,
};
console.log(JSON.stringify(report));
"""
        script = preamble + delivery_js(
            self.page,
            [
                "deliveryDateLabel",
                "dismissGitChartInspector", "selectDeliveryDay", "drawDeliveryChart",
            ],
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        ) + driver
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        )

        self.assertEqual(json.loads(result.stdout), {
            "added": 4,
            "deleted": 4,
            "costLine": False,
            "costArea": False,
            "costDot": False,
            "typical": False,
            "quiet": False,
            "spendAxis": False,
            "hitTop": "12.258064516129032%",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_shape_rows_expose_each_measure_with_its_own_day_count(self):
        driver = """
const payload={days:[
 {day:'2026-01-01',changed_lines:200,comparable_changed_lines:200,covered_cost:4,spend_per_1k:20,
  availability:{code_pushed:true,cost:true,spend_per_1k:true},
  efficiency:{delivery_yield:8,availability:{delivery_yield:true}}},
 {day:'2026-01-02',changed_lines:600,comparable_changed_lines:600,covered_cost:6,spend_per_1k:10,
  availability:{code_pushed:true,cost:true,spend_per_1k:true},
  efficiency:{delivery_yield:null,availability:{delivery_yield:false}}},
]};
console.log(JSON.stringify(deliveryShapeRows(payload).map(row=>({
 key:row.key,label:row.label,count:row.distribution?row.distribution.count:null,
 median:row.distribution?row.format(row.distribution.median):null}))));
"""
        payload = self.run_js(
            [
                "deliveryRatio", "deliveryQuantile", "deliveryDistribution",
                "deliveryRatioDay", "deliveryIntensityDays", "deliveryDaySeries",
                "deliveryShapeRows",
            ],
            driver,
            blocks=("const DELIVERY_SHAPE_METRICS=[",),
            consts=(r"^const DELIVERY_MIN_RATIO_LINES=\d+;$",),
        )

        self.assertEqual(payload, [
            {"key": "spend_per_1k", "label": "Spend / 1K", "count": 2,
             "median": "$15.00"},
            {"key": "changed_lines", "label": "Lines / push day", "count": 2,
             "median": "400"},
            {"key": "delivery_yield", "label": "Push yield", "count": 1,
             "median": "8"},
        ])


if __name__ == "__main__":
    unittest.main()
