import datetime
import unittest

from token_meter.domain import branches as B

DAY = 86_400
TODAY = datetime.date(2026, 11, 11)
NOW = datetime.datetime(2026, 11, 11, 12).timestamp()
WINDOW = (datetime.date(2026, 11, 5), TODAY)
PREVIOUS = (datetime.date(2026, 10, 29), datetime.date(2026, 11, 4))


def ts(day, hour=10):
    return datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(hour)).timestamp()


def session(branch, day, cost, *, project="repo · a1", repo="R", provider="Claude Code",
            started=None, last=None, cost_available=True):
    row = {"project": project, "repo_key": repo, "provider": provider,
           "day_cost": {day: cost}, "cost_available": cost_available,
           "started_at": started if started is not None else ts(day),
           "last_activity_at": last if last is not None else ts(day, 11)}
    if branch is not None:
        row["branch"] = branch
    return row


def key_for(repo, name):
    return "{}:{}".format(repo, name)


def pushed(first_day, commits=1, lines=(10, 2), pushes=1, complete=True):
    return {"pushes": pushes, "first_push": ts(first_day, 12), "last_push": ts(first_day, 12),
            "commits": commits, "added": lines[0], "deleted": lines[1], "complete": complete}


def rollup(sessions, evidence=None, defaults=None, measured=("R",), previous=PREVIOUS):
    return B.build_branch_rollup(
        sessions, evidence or {}, defaults or {}, key_for, set(measured),
        WINDOW, previous, NOW,
    )


class NormalizationTests(unittest.TestCase):
    def test_strips_drops_head_empty_and_control_characters_and_caps(self):
        self.assertEqual(B.normalize_branch("  feat/x \n"), "feat/x")
        for value in ("", "   ", "HEAD", " HEAD ", "a\x00b", "a\tb", "a\x7fb", None, 7):
            self.assertEqual(B.normalize_branch(value), "", repr(value))
        self.assertEqual(B.normalize_branch("x" * 300), "x" * 200)

    def test_dominant_branch_majority_then_latest(self):
        self.assertEqual(B.dominant_branch(["a", "b", "a"]), "a")
        self.assertEqual(B.dominant_branch(["a", "b"]), "b")
        self.assertEqual(B.dominant_branch(["HEAD", "", None]), "")


class BranchRowTests(unittest.TestCase):
    def test_rows_join_by_salted_key_and_exclude_trunk_and_no_branch(self):
        sessions = [
            session("feat/a", "2026-11-09", 4.0),
            session("feat/a", "2026-11-10", 2.0, provider="Codex"),
            session("main", "2026-11-10", 5.0),
            session("release", "2026-11-10", 1.0),
            session(None, "2026-11-10", 3.0),
        ]
        result = rollup(sessions, {("R", "R:feat/a"): pushed("2026-11-10", commits=3, lines=(30, 6))},
                        defaults={"R": "R:release"})
        self.assertEqual([row["branch"] for row in result["rows"]], ["feat/a"])
        row = result["rows"][0]
        self.assertEqual(row["spend"], 6.0)
        self.assertEqual(row["sessions"], 2)
        self.assertEqual(row["apps"], ["Claude Code", "Codex"])
        self.assertEqual(row["days_active"], 2)
        self.assertEqual((row["pushes"], row["commits"], row["added"], row["deleted"]), (1, 3, 30, 6))
        self.assertEqual(row["status"], "pushed")
        self.assertEqual(row["spend_per_commit"], 2.0)
        self.assertEqual(result["trunk"], {"spend": 6.0, "sessions": 2, "share": 6.0 / 15.0})
        self.assertEqual(result["no_branch"], {"spend": 3.0, "sessions": 1, "share": 3.0 / 15.0})

    def test_status_reflects_push_activity_and_missing_git_evidence(self):
        sessions = [
            session("feat/stale", "2026-11-01", 1.0),
            session("feat/stale", "2026-11-03", 1.0),
            session("feat/live", "2026-11-10", 1.0),
            session("feat/norepo", "2026-11-10", 1.0, repo=""),
            session("feat/unmeasured", "2026-11-10", 1.0, repo="U"),
        ]
        rows = {row["branch"]: row for row in rollup(sessions)["rows"]}
        self.assertEqual(rows["feat/live"]["status"], "in_progress")
        self.assertEqual(rows["feat/norepo"]["status"], "no_git_evidence")
        self.assertEqual(rows["feat/unmeasured"]["status"], "no_git_evidence")
        self.assertIsNone(rows["feat/unmeasured"]["pushes"])
        self.assertIsNone(rows["feat/unmeasured"]["spend_per_commit"])
        self.assertFalse(rows["feat/unmeasured"]["availability"]["git"])
        self.assertNotIn("feat/stale", rows)  # no activity in the selected range

    def test_row_spend_is_the_branch_total_and_every_ratio_uses_it(self):
        child = session("feat/a", "2026-11-09", 1.0)
        child["child"] = True
        sessions = [
            session("feat/a", "2026-10-20", 7.0),  # before the range, still part of the branch
            session("feat/a", "2026-11-09", 2.0),
            child,
            session("feat/b", "2026-10-20", 9.0),  # no activity in range: not listed
        ]
        result = rollup(sessions, {("R", "R:feat/a"): pushed("2026-11-09", commits=4)})
        self.assertEqual([row["branch"] for row in result["rows"]], ["feat/a"])
        row = result["rows"][0]
        self.assertEqual(row["spend"], 10.0)
        self.assertEqual(row["total_spend"], row["spend"])
        self.assertEqual(row["spend_per_commit"], row["spend"] / row["commits"])
        self.assertEqual(row["sessions"], 2, "child runs add spend, not sessions")
        self.assertEqual(row["days_active"], 2)
        self.assertEqual(result["metrics"]["cost_to_ship"]["value"], row["spend"])

    def test_same_branch_name_in_two_repositories_stays_separate(self):
        sessions = [
            session("fix/x", "2026-11-10", 2.0, project="one · a1", repo="R"),
            session("fix/x", "2026-11-10", 5.0, project="two · b2", repo="S"),
        ]
        result = rollup(sessions, {("R", "R:fix/x"): pushed("2026-11-10", commits=2)}, measured=("R", "S"))
        rows = {row["project"]: row for row in result["rows"]}
        self.assertEqual(set(rows), {"one · a1", "two · b2"})
        self.assertEqual((rows["one · a1"]["spend"], rows["one · a1"]["sessions"], rows["one · a1"]["commits"]), (2.0, 1, 2))
        self.assertEqual((rows["two · b2"]["spend"], rows["two · b2"]["sessions"]), (5.0, 1))
        self.assertEqual(rows["one · a1"]["status"], "pushed")
        self.assertIn(rows["two · b2"]["status"], ("in_progress", "not_pushed"))
        self.assertEqual(rows["two · b2"]["commits"], 0)

    def test_unavailable_cost_is_none_not_zero(self):
        result = rollup([session("feat/x", "2026-11-10", 0.0, cost_available=False)])
        self.assertIsNone(result["rows"][0]["spend"])
        self.assertFalse(result["rows"][0]["availability"]["cost"])

    def test_rows_are_bounded_and_sorted_by_spend(self):
        sessions = [session("feat/{}".format(i), "2026-11-10", float(i)) for i in range(B.MAX_BRANCH_ROWS + 5)]
        result = rollup(sessions)
        self.assertEqual(len(result["rows"]), B.MAX_BRANCH_ROWS)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_rows"], B.MAX_BRANCH_ROWS + 5)
        self.assertEqual(result["rows"][0]["branch"], "feat/{}".format(B.MAX_BRANCH_ROWS + 4))


class MetricTests(unittest.TestCase):
    def test_cost_to_ship_is_median_spend_of_branches_first_pushed_in_period(self):
        sessions = [
            session("feat/a", "2026-11-06", 2.0),
            session("feat/b", "2026-11-07", 4.0),
            session("feat/c", "2026-11-08", 9.0),
            session("feat/old", "2026-10-30", 5.0),
        ]
        evidence = {
            ("R", "R:feat/a"): pushed("2026-11-06", lines=(10, 0)),
            ("R", "R:feat/b"): pushed("2026-11-07", lines=(20, 10)),
            ("R", "R:feat/c"): pushed("2026-11-08", lines=(100, 0)),
            ("R", "R:feat/old"): pushed("2026-10-30", lines=(8, 0)),
        }
        result = rollup(sessions, evidence)
        metric = result["metrics"]["cost_to_ship"]
        self.assertEqual((metric["value"], metric["branches"], metric["median_pushed_lines"]), (4.0, 3, 30))
        self.assertTrue(metric["available"])
        self.assertEqual(result["previous"]["cost_to_ship"]["value"], 5.0)
        self.assertEqual(metric["comparison"]["change_pct"], -20.0)

    def test_cost_to_ship_without_a_first_push_is_unavailable(self):
        metric = rollup([session("feat/a", "2026-11-10", 2.0)])["metrics"]["cost_to_ship"]
        self.assertIsNone(metric["value"])
        self.assertFalse(metric["available"])
        self.assertIsNone(metric["comparison"]["change_pct"])

    def test_unshipped_share_counts_stale_unpushed_and_reports_in_progress_separately(self):
        sessions = [
            session("feat/stale", "2026-11-05", 2.0, last=ts("2026-11-02")),
            session("feat/live", "2026-11-10", 3.0),
            session("feat/shipped", "2026-11-05", 1.0),
            session("feat/unknown", "2026-11-05", 4.0, repo="U"),
        ]
        metric = rollup(sessions, {("R", "R:feat/shipped"): pushed("2026-11-05")})["metrics"]["unshipped_share"]
        self.assertAlmostEqual(metric["value"], 2.0 / 10.0)
        self.assertEqual(metric["spend"], 2.0)
        self.assertEqual(metric["in_progress_spend"], 3.0)
        self.assertAlmostEqual(metric["in_progress_share"], 3.0 / 10.0)

    def test_unshipped_share_without_git_evidence_is_unavailable(self):
        metric = rollup([session("feat/a", "2026-11-05", 2.0)], measured=())["metrics"]["unshipped_share"]
        self.assertIsNone(metric["value"])
        self.assertFalse(metric["available"])

    def test_rework_share_is_session_level_spend_after_first_push(self):
        sessions = [
            session("feat/a", "2026-11-06", 3.0, started=ts("2026-11-06", 9)),
            session("feat/a", "2026-11-07", 1.0, started=ts("2026-11-07", 9)),
            session("feat/b", "2026-11-07", 4.0),
        ]
        metric = rollup(sessions, {("R", "R:feat/a"): pushed("2026-11-06"),
                                   ("R", "R:feat/b"): pushed("2026-11-08")})["metrics"]["rework_share"]
        self.assertAlmostEqual(metric["value"], 1.0 / 8.0)
        self.assertEqual(metric["estimate"], "session_level")
        self.assertIsNone(rollup(sessions)["metrics"]["rework_share"]["value"])

    def test_hard_branches_need_five_eligible_and_flag_three_times_median(self):
        sessions, evidence = [], {}
        for index, spend in enumerate((2.0, 2.0, 2.0, 2.0, 12.0)):
            name = "feat/{}".format(index)
            sessions.append(session(name, "2026-11-08", spend))
            evidence[("R", "R:" + name)] = pushed("2026-11-08", commits=2)
        result = rollup(sessions, evidence)
        metric = result["metrics"]["hard_branches"]
        self.assertTrue(metric["available"])
        self.assertEqual(metric["count"], 1)
        self.assertAlmostEqual(metric["spend_share"], 12.0 / 20.0)
        self.assertEqual([row["branch"] for row in result["rows"] if row["hard"]], ["feat/4"])

        fewer = rollup(sessions[:4], {key: evidence[key] for key in list(evidence)[:4]})
        self.assertFalse(fewer["metrics"]["hard_branches"]["available"])
        self.assertIsNone(fewer["metrics"]["hard_branches"]["count"])
        cheap = [dict(row, day_cost={"2026-11-08": 0.1}) for row in sessions]
        self.assertFalse(rollup(cheap, evidence)["metrics"]["hard_branches"]["available"])

    def test_all_range_has_no_previous_period(self):
        result = rollup([session("feat/a", "2026-11-10", 1.0)], previous=None)
        self.assertIsNone(result["previous"])
        self.assertIsNone(result["metrics"]["unshipped_share"]["comparison"]["change_pp"])


if __name__ == "__main__":
    unittest.main()


class BranchPayloadServiceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from token_meter.services import git_branches, git_delivery

        self.git_branches = git_branches
        self.tmp = tempfile.TemporaryDirectory()
        self.service = git_delivery.GitDeliveryService(
            str(Path(self.tmp.name) / "d.sqlite3"), runner=self.no_git, now=lambda: NOW, salt="s")
        self.candidates = ({"root": "/repo", "project": "repo · a1", "repo_key": "R"},)
        self.service.ledger.set_repository_coverage("R", True, 1)
        key = git_branches.branch_key(self.service, "R", "feat/x")
        git_branches.record_push(self.service.ledger, "R", key, "p", ts("2026-11-09", 12), True, [("o", 10, 2)])

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def no_git(argv, **_kwargs):
        raise AssertionError("branch payload must not run Git")

    def test_payload_joins_trace_branch_to_salted_ledger_key_without_exposing_keys(self):
        sessions = [session("feat/x", "2026-11-08", 3.0, repo=None), session("feat/y", "2026-11-10", 1.0, repo=None)]
        payload = self.git_branches.branch_payload(self.service, sessions, self.candidates, "7")
        rows = {row["branch"]: row for row in payload["rows"]}
        self.assertEqual((rows["feat/x"]["status"], rows["feat/x"]["commits"]), ("pushed", 1))
        self.assertEqual(rows["feat/y"]["status"], "in_progress")
        self.assertEqual(payload["metrics"]["cost_to_ship"]["value"], 3.0)
        encoded = __import__("json").dumps(payload)
        self.assertNotIn(self.git_branches.branch_key(self.service, "R", "feat/x"), encoded)
        self.assertNotIn('"R"', encoded)
        self.assertNotIn("repo_key", encoded)

    def test_project_filter_and_invalid_range(self):
        sessions = [session("feat/x", "2026-11-08", 3.0, repo=None),
                    session("feat/z", "2026-11-08", 2.0, project="other · b2", repo=None)]
        payload = self.git_branches.branch_payload(self.service, sessions, self.candidates, "7", "repo · a1")
        self.assertEqual([row["branch"] for row in payload["rows"]], ["feat/x"])
        self.assertIsNone(self.git_branches.branch_payload(self.service, sessions, self.candidates, "bogus"))
