"""Git commit work types: subject and detail reads, prefix-free labeling evidence, and the type chart."""

import datetime
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.services import git_delivery
from tests.dashboard_page import DASHBOARD_PAGE
from tests.test_git_delivery import _ref_runner, delivery_js, local_timestamp


class FakeCommitClassifier:
    def __init__(self, version=git_delivery.COMMIT_LABEL_VERSION, since_day="0000-00-00", room=1000):
        self.version, self.since_day, self.room = version, since_day, room
        self.subjects = []
        self.details = []

    def commit_label_request(self):
        return {"version": self.version, "since_day": self.since_day}

    def commit_status(self, repo_key, object_key):
        if any((repo, obj) == (repo_key, object_key) for repo, obj, _subject in self.subjects):
            return "queued"
        return "take" if len(self.subjects) < self.room else "later"

    def observe_commit(self, repo_key, object_key, subject, observed_at, detail="", files=""):
        self.details.append((detail, files))
        if len(self.subjects) >= self.room:
            return False
        self.subjects.append((repo_key, object_key, subject))
        return True


def _subject_runner(subjects, **kwargs):
    base = _ref_runner(**kwargs)
    reads = kwargs.get("git_calls")

    def runner(argv, **options):
        args = tuple(argv[argv.index("-C") + 2:])
        if args[0:3] == ("show", "-s", "--format=%s%x00%b%x00"):
            if reads is not None:
                reads.append("subject-only")
            return {"returncode": 0, "stdout": subjects.get(args[-1], "Add a chart") + "\x00Explain the change.\n\x00\n"}
        if args[0:3] == ("show", "--numstat", "--format=%s%x00%b%x00"):
            if reads is not None:
                reads.append("subject")
            return {"returncode": 0, "stdout": subjects.get(args[-1], "Add a chart")
                    + "\x00Explain the change.\n\x00\n2\t1\tsrc/app.py\n"}
        return base(argv, **options)

    return runner


class GitCommitWorkTypeTests(unittest.TestCase):
    def ledger(self, tmp):
        return meter.GitDeliveryLedger(str(Path(tmp) / "delivery.sqlite3"), "test-salt")

    def test_labels_persist_only_type_confidence_version_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.ledger(tmp)
            ledger.record("repo", "obj", local_timestamp("2026-09-03"), 10, 2)
            self.assertTrue(ledger.record_commit_label("repo", "obj", "fix", 0.91, git_delivery.COMMIT_LABEL_VERSION, "digest", 5))
            self.assertFalse(ledger.record_commit_label("repo", "missing", "fix", 0.9, git_delivery.COMMIT_LABEL_VERSION, "digest", 5))
            with self.assertRaises(ValueError):
                ledger.record_commit_label("repo", "obj", "chores", 0.9, git_delivery.COMMIT_LABEL_VERSION, "digest", 5)
            with sqlite3.connect(Path(tmp) / "delivery.sqlite3") as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(delivery_commit_labels)")}
            self.assertEqual(columns, {"repo_key", "object_key", "work_type", "confidence", "version",
                                       "model", "labeled_at"})

    def test_daily_type_rows_split_by_label_and_treat_other_versions_as_unlabeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.ledger(tmp)
            day = local_timestamp("2026-09-03")
            for name, lines in (("a", 10), ("b", 6), ("c", 4), ("d", 1)):
                ledger.record("repo", name, day, lines, 0)
            ledger.record_commit_label("repo", "a", "feature", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            ledger.record_commit_label("repo", "b", "feature", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            ledger.record_commit_label("repo", "c", "fix", 0.9, "c0", "m", 1)  # stale question version

            rows = ledger.daily_type_rows(["repo"], "2026-09-03", "2026-09-03", git_delivery.COMMIT_LABEL_VERSION)
            self.assertEqual(sorted((row["work_type"], row["commits"], row["lines"]) for row in rows),
                             [("", 2, 5), ("feature", 2, 16)])
            self.assertEqual(ledger.unlabeled_keys("repo", git_delivery.COMMIT_LABEL_VERSION, "2026-09-01", 10), {"c", "d"})
            ledger.clear(10)
            self.assertEqual(ledger.daily_type_rows(["repo"], "2026-09-03", "2026-09-03", git_delivery.COMMIT_LABEL_VERSION), [])

    def test_scan_hands_new_commit_subjects_to_the_classifier_and_never_stores_them(self):
        tip = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                runner=_subject_runner({tip: "Fix the secret-sauce overflow"},
                                       tracked=("refs/remotes/origin/main",),
                                       pushes={"refs/remotes/origin/main": tip}),
                now=lambda: 200, salt="test-salt",
            )
            classifier = FakeCommitClassifier()
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])

            self.assertEqual([subject for _repo, _obj, subject in classifier.subjects],
                             ["Fix the secret-sauce overflow"])
            repo_key, object_key, _subject = classifier.subjects[0]
            self.assertTrue(service.ledger.record_commit_label(repo_key, object_key, "fix", 0.8, git_delivery.COMMIT_LABEL_VERSION, "m", 1))
            stored = b"".join(path.read_bytes() for path in Path(tmp).iterdir())
            self.assertNotIn(b"secret-sauce", stored)

    def test_scan_reads_no_subjects_without_an_enabled_classifier(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                runner=_subject_runner({}, tracked=("refs/remotes/origin/main",),
                                       pushes={"refs/remotes/origin/main": "a" * 40}, git_calls=calls),
                now=lambda: 200, salt="test-salt",
            )
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            classifier = FakeCommitClassifier()
            classifier.commit_label_request = lambda: None
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertNotIn("subject", calls)

    def test_scan_backfills_subjects_of_already_counted_unlabeled_commits(self):
        tip = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            runner = _subject_runner({tip: "Add the weekly chart"}, tracked=("refs/remotes/origin/main",),
                                     pushes={"refs/remotes/origin/main": tip})
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                               now=lambda: 200, salt="test-salt")
            service.scan([{"root": "/repo", "project": "repo · a1"}])  # counted before labeling existed
            classifier = FakeCommitClassifier()
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual([subject for *_keys, subject in classifier.subjects], ["Add the weekly chart"])
            repo_key, object_key, _subject = classifier.subjects[0]
            service.ledger.record_commit_label(repo_key, object_key, "feature", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            classifier.subjects.clear()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual(classifier.subjects, [])  # labeled commits are not offered again

    def test_commits_no_push_introduces_are_not_walked_for_again(self):
        tip = "a" * 40
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            runner = _subject_runner({}, tracked=("refs/remotes/origin/main",),
                                     pushes={"refs/remotes/origin/main": tip}, git_calls=calls)
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                               now=lambda: 200, salt="test-salt")
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            repo_key = service.ledger.rows()[0]["repo_key"]
            # Counted from a branch whose reflog is gone (deleted after merge): no push introduces it now.
            service.ledger.record(repo_key, "orphan", 150, 3, 0)
            classifier = FakeCommitClassifier()
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])  # offers the tip; the orphan is missed
            for repo, obj, _subject in classifier.subjects:
                service.ledger.record_commit_label(repo, obj, "feature", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            calls.clear()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertNotIn("subject", calls)
            self.assertNotIn("rev-list", calls)
            service.forget_commit_subjects()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertIn("rev-list", calls)  # a label delete offers it again

    def service_with(self, tmp, calls, commits=None, failing=()):
        tip = "a" * 40
        runner = _subject_runner({}, tracked=("refs/remotes/origin/main",),
                                 pushes={"refs/remotes/origin/main": tip}, git_calls=calls,
                                 commits=commits, failing=failing)
        return meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                        now=lambda: 200, salt="test-salt")

    def test_subject_is_not_read_for_a_commit_whose_attempts_are_used_up(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service_with(tmp, calls)
            classifier = FakeCommitClassifier()
            classifier.commit_status = lambda repo_key, object_key: "never"
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertNotIn("subject", calls)
        self.assertEqual(classifier.subjects, [])

    def test_commits_that_did_not_fit_a_full_queue_are_offered_once_it_drains(self):
        calls = []
        tip = "a" * 40
        chain = ("c" * 40, "d" * 40, tip)
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service_with(tmp, calls, commits={tip: chain})
            classifier = FakeCommitClassifier(room=1)
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual(len(classifier.subjects), 1)
            classifier.room = 1000  # the worker drained the queue
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual(len({obj for _repo, obj, _subject in classifier.subjects}), 3)

    def test_a_still_queued_commit_is_not_skipped_and_is_offered_again_after_it_fails(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service_with(tmp, calls)
            classifier = FakeCommitClassifier()
            service.commit_classifier = classifier
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            service.scan([{"root": "/repo", "project": "repo · a1"}])  # still queued: left alone
            self.assertEqual(len(classifier.subjects), 1)
            classifier.subjects.clear()  # the model call failed and the item was dropped
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual(len(classifier.subjects), 1)

    def test_a_walk_truncated_by_a_size_cap_still_lets_unfound_commits_be_skipped(self):
        calls = []
        tip = "a" * 40
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(git_delivery, "MAX_COMMITS_PER_PUSH", 2):
            service = self.service_with(tmp, calls, commits={tip: ("c" * 40, "d" * 40, tip)})
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            repo_key = service.ledger.rows()[0]["repo_key"]
            service.ledger.record(repo_key, "orphan", 150, 3, 0)  # its branch was deleted after merge
            service.commit_classifier = classifier = FakeCommitClassifier()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertIn("orphan", service._subject_skips.get(repo_key, set()))

    def test_a_failed_backfill_walk_does_not_skip_and_is_retried(self):
        tip = "a" * 40
        fail_walks = [False]
        base = _subject_runner({}, tracked=("refs/remotes/origin/main",),
                               pushes={"refs/remotes/origin/main": tip})

        def runner(argv, **options):
            if tuple(argv[argv.index("-C") + 2:])[0] == "rev-list" and fail_walks[0]:
                return {"returncode": 128, "stdout": ""}
            return base(argv, **options)

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                               now=lambda: 200, salt="test-salt")
            service.scan([{"root": "/repo", "project": "repo · a1"}])  # counted before labeling existed
            service.commit_classifier = classifier = FakeCommitClassifier()
            fail_walks[0] = True
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual(classifier.subjects, [])
            self.assertEqual(service._subject_skips, {})
            fail_walks[0] = False
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual(len(classifier.subjects), 1)

    def test_a_failed_subject_read_is_retried_by_a_later_scan(self):
        tip = "a" * 40
        failures = [True, True]  # the full read and its message-only fallback
        base = _subject_runner({}, tracked=("refs/remotes/origin/main",),
                               pushes={"refs/remotes/origin/main": tip})

        def runner(argv, **options):
            if tuple(argv[argv.index("-C") + 2:])[0] == "show" and "--format=%s%x00%b%x00" in argv and failures:
                failures.pop()
                return {"returncode": 128, "stdout": ""}
            return base(argv, **options)

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                               now=lambda: 200, salt="test-salt")
            service.commit_classifier = classifier = FakeCommitClassifier()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual(classifier.subjects, [])
            self.assertEqual(service._subject_skips, {})
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual(len(classifier.subjects), 1)

    def test_a_transient_reflog_failure_does_not_skip_unfound_commits(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service_with(tmp, calls)
            service.scan([{"root": "/repo", "project": "repo · a1"}])  # counted before labeling existed
            failing = self.service_with(tmp, calls, failing=("refs/remotes/origin/main",))
            failing.commit_classifier = classifier = FakeCommitClassifier()
            failing.scan([{"root": "/repo", "project": "repo · a1"}])
            self.assertEqual(classifier.subjects, [])
            failing._runner = service._runner  # the reflog reads again
            failing.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual(len(classifier.subjects), 1)

    def test_query_reports_daily_work_types_with_unlabeled_commits_explicit(self):
        tip = "a" * 40
        today = datetime.date.fromtimestamp(local_timestamp("2026-09-04")).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                runner=_subject_runner({}, tracked=("refs/remotes/origin/main",),
                                       pushes={"refs/remotes/origin/main": tip}),
                now=lambda: local_timestamp("2026-09-04"), salt="test-salt",
            )
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            repo_key = service.ledger.rows()[0]["repo_key"]
            service.ledger.record("repo-other", "x", local_timestamp("2026-09-04"), 1, 1)
            # The scanned push is dated by the fake reflog long ago; these two land in the window.
            service.ledger.record(repo_key, "first", local_timestamp("2026-09-04"), 2, 1)
            service.ledger.record(repo_key, "second", local_timestamp("2026-09-04"), 7, 0)
            service.ledger.record_commit_label(repo_key, "second", "docs", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            payload = service.query("", "7", [], ["repo · a1"], [{"root": "/repo", "project": "repo · a1"}])

        work = payload["work_types"]
        self.assertEqual(work["commits"], 2)
        self.assertEqual(work["labeled_commits"], 1)
        self.assertFalse(work["classifier_enabled"])
        self.assertTrue(work["estimate"])
        self.assertEqual(work["totals"], {"unlabeled": {"commits": 1, "lines": 3},
                                          "docs": {"commits": 1, "lines": 7}})
        self.assertEqual(len(work["days"]), 7)
        self.assertEqual(work["days"][-1]["day"], today)
        self.assertEqual(work["days"][-1]["types"]["docs"], {"commits": 1, "lines": 7})


class GitWorkTypeChartContractTests(unittest.TestCase):
    page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")

    def test_work_type_card_sits_after_the_daily_chart_and_resizes_with_it(self):
        git_page = self.page.split("id=view-git", 1)[1].split("id=view-learn", 1)[0]
        self.assertLess(git_page.index("id=d-daily-chart"), git_page.index("id=d-type-chart"))
        self.assertLess(git_page.index("id=d-type-chart"), git_page.index("id=d-insight-title"))
        self.assertIn(">Work pushed by type<", git_page)
        self.assertIn("data-delivery-type-metric=commits", git_page)
        self.assertIn("data-delivery-type-metric=lines", git_page)
        self.assertIn("git:['d-daily-chart','d-type-chart']", self.page)
        self.assertIn("drawDeliveryTypeChart(payload?.work_types)", self.page)
        colors = re.search(r"^const DELIVERY_TYPE_COLORS=\{[^\n]*\};$", self.page, re.MULTILINE).group(0)
        self.assertNotRegex(colors, r"#[0-9a-fA-F]{3,8}|rgba?\(")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_chart_stacks_each_day_and_keeps_unlabeled_commits_explicit(self):
        preamble = """
const nodes={};
const node=()=>({innerHTML:'',textContent:'',hidden:false,clientWidth:1040,clientHeight:270,style:{},attrs:{},
 setAttribute(name,value){this.attrs[name]=value;},getBoundingClientRect(){return{left:0,width:1040};}});
['d-type-chart','d-type-tip','d-type-legend','d-type-note','d-type-hover'].forEach(id=>nodes[id]=node());
const $=id=>nodes[id];
const document={querySelectorAll:()=>[]};
const esc=value=>String(value);
const deliveryPercent=value=>`${Math.round(value)}%`;
const deliveryDateLabel=day=>day;
let deliveryTypeMetric='commits';
"""
        driver = """
const work={available:true,classifier_enabled:false,commits:4,labeled_commits:3,
 totals:{fix:{commits:2,lines:30},feature:{commits:1,lines:5},unlabeled:{commits:1,lines:9}},
 days:[{day:'Sep 3',types:{fix:{commits:2,lines:30},unlabeled:{commits:1,lines:9}}},{day:'Sep 4',types:{feature:{commits:1,lines:5}}}]};
drawDeliveryTypeChart(work);
const svg=nodes['d-type-chart'].innerHTML;
const first={segments:(svg.match(/class="typeSeg/g)||[]).length,unlabeled:(svg.match(/typeSeg unlabeled/g)||[]).length,
 legend:nodes['d-type-legend'].innerHTML,note:nodes['d-type-note'].textContent};
deliveryTypeMetric='lines';drawDeliveryTypeChart(work);
const lines=nodes['d-type-chart'].attrs['aria-label'];
const svgNode=nodes['d-type-chart'],keys=[];
svgNode.onfocus();keys.push(nodes['d-type-tip'].innerHTML);
svgNode.onkeydown({key:'ArrowLeft',preventDefault(){}});keys.push(nodes['d-type-tip'].innerHTML);
svgNode.onkeydown({key:'End',preventDefault(){}});keys.push(nodes['d-type-tip'].innerHTML);
const hatched=nodes['d-type-chart'].innerHTML.includes('fill:url(#d-type-hatch)')&&nodes['d-type-chart'].innerHTML.includes('<pattern id=d-type-hatch');
const axes=[1,3,5,9,54,1234].map(deliveryTypeAxisMax);
drawDeliveryTypeChart(undefined);
const loading=nodes['d-type-note'].textContent;
drawDeliveryTypeChart({available:false,days:[],totals:{}});
console.log(JSON.stringify({first,lines,keys,hatched,axes,loading,empty:nodes['d-type-chart'].innerHTML,emptyNote:nodes['d-type-note'].textContent}));
"""
        script = preamble + delivery_js(
            self.page, ["deliveryTypeValue", "deliveryTypeNote", "deliveryTypeAxisMax", "drawDeliveryTypeChart"],
            consts=(r"^const DELIVERY_CHART_INSET=\{[^\n]*\};$", r"^const DELIVERY_TYPE_ORDER=\[[^\n]*\];$",
                    r"^const DELIVERY_TYPE_LABELS=\{[^\n]*\};$", r"^const DELIVERY_TYPE_COLORS=\{[^\n]*\};$"),
        ) + driver
        result = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                           check=True).stdout)

        self.assertEqual(result["first"]["segments"], 3)
        self.assertEqual(result["first"]["unlabeled"], 1)
        self.assertIn("Fixes <b>50%</b>", result["first"]["legend"])
        self.assertIn("Not labeled yet <b>25%</b>", result["first"]["legend"])
        self.assertEqual(result["first"]["note"],
                         "3 of 4 commits labeled · turn on Work insights in Settings to label the rest locally")
        self.assertEqual(result["lines"], "Pushed lines per day by type: Features 5, Fixes 30, Not labeled yet 9")
        self.assertIn("Sep 4", result["keys"][0])  # focus opens on the latest day
        self.assertIn("Sep 3", result["keys"][1])  # arrows move between days
        self.assertIn("Sep 4", result["keys"][2])
        self.assertTrue(result["hatched"])
        self.assertEqual(result["axes"], [2, 4, 6, 10, 56, 1600])  # even tops: every gridline is whole
        self.assertEqual(result["loading"], "Reading labels\u2026")
        self.assertIn("No pushed commits in this period", result["empty"])
        self.assertEqual(result["emptyNote"], "No pushed commits in this period.")


class CommitWorkLabelClearTests(unittest.TestCase):
    def test_commit_labels_are_deleted_even_when_the_work_ledger_delete_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=_ref_runner(),
                                               now=lambda: 200, salt="test-salt")
            service.ledger.record("repo", "obj", local_timestamp("2026-09-03"), 3, 1)
            service.ledger.record_commit_label("repo", "obj", "docs", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            work = mock.Mock()
            work.clear.side_effect = meter._work.LabelDeleteError("busy")
            with mock.patch.object(meter, "git_delivery_service", return_value=service), \
                 mock.patch.object(meter, "work_insights_service_if_started", return_value=work):
                with self.assertRaises(meter._work.LabelDeleteError):
                    meter.clear_work_insights()
            rows = service.ledger.daily_type_rows(["repo"], "2026-09-03", "2026-09-03", git_delivery.COMMIT_LABEL_VERSION)
        self.assertEqual([(row["work_type"], row["commits"]) for row in rows], [("", 1)])

    def test_a_git_ledger_failure_is_reported_not_swallowed(self):
        service = mock.Mock()
        service.ledger.clear_commit_labels.side_effect = sqlite3.OperationalError("locked")
        with mock.patch.object(meter, "git_delivery_service", return_value=service), \
             mock.patch.object(meter, "work_insights_service_if_started", return_value=mock.Mock()):
            with self.assertRaises(sqlite3.Error):
                meter.clear_work_insights()

    def test_deleting_work_labels_also_deletes_commit_work_type_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=_ref_runner(),
                                               now=lambda: 200, salt="test-salt")
            service.ledger.record("repo", "obj", local_timestamp("2026-09-03"), 3, 1)
            service.ledger.record_commit_label("repo", "obj", "docs", 0.9, git_delivery.COMMIT_LABEL_VERSION, "m", 1)
            with mock.patch.object(meter, "git_delivery_service", return_value=service), \
                 mock.patch.object(meter, "work_insights_service_if_started", return_value=None), \
                 mock.patch.object(meter, "TOKEN_METER_WORK_INSIGHTS_DB", str(Path(tmp) / "work.sqlite3")):
                meter.clear_work_insights()
            rows = service.ledger.daily_type_rows(["repo"], "2026-09-03", "2026-09-03", git_delivery.COMMIT_LABEL_VERSION)
        self.assertEqual([(row["work_type"], row["commits"]) for row in rows], [("", 1)])


class CommitDetailsTests(unittest.TestCase):
    def test_details_keep_the_first_body_line_and_reduce_paths_to_kind_counts(self):
        output = ("Stop the overflow\x00\nClamp the secret-widget chart width.\nMore text.\n"
                  "Co-Authored-By: Someone <a@b.c>\n\x00\n"
                  "10\t2\tdashboard/js/secret_widget.js\n3\t0\ttests/test_secret_widget.py\n-\t-\tlogo.png\n")
        subject, detail, files = git_delivery.commit_details(output)
        self.assertEqual(subject, "Stop the overflow")
        self.assertEqual(detail, "Clamp the secret-widget chart width.")
        self.assertEqual(files, "Changed files: 1 UI, 1 tests, 1 images (+13 / -2 lines).")
        self.assertNotIn("secret_widget", files)
        self.assertNotIn(".js", files)

    def test_quoted_and_renamed_paths_are_classified_by_their_new_name(self):
        output = ('S\x00\x00\n1\t0\t"caf\\303\\251/tests/test_x.py"\n2\t0\ttests/a.py => src/a.py\n'
                  '3\t1\tsrc/{old => tests}/test_b.py\n'
                  '4\t0\t"tests/caf\\303\\251.py" => "src/caf\\303\\251.py"\n'
                  '5\t0\ttests/a{1}.py => src/c.py\n'
                  '6\t0\ttests/{a => b}/x.py\n')
        # Old names look like tests where the new names are code, and the reverse: only new names count.
        self.assertEqual(git_delivery.commit_details(output)[2], "Changed files: 3 tests, 3 code (+21 / -1 lines).")

    def test_common_trailers_are_not_taken_as_the_detail_line(self):
        body = "Change-Id: I123\nReviewed-by: A <a@b.c>\nCc: B <b@c.d>\nNote: keep the old order.\n"
        self.assertEqual(git_delivery.commit_details("S\x00" + body + "\x00")[1], "Note: keep the old order.")
        for line in ("Follow-up: handle empty input", "Fixes: the crash on load"):
            self.assertEqual(git_delivery.commit_details(f"S\x00{line}\n\x00")[1], line)
        trailers = "Co-developed-by: A <a@b.c>\nFixes: #123\nRefs: 1a2b3c4d\nAssisted-by: tool\nKeep it short.\n"
        self.assertEqual(git_delivery.commit_details("S\x00" + trailers + "\x00")[1], "Keep it short.")

    def test_trailer_only_body_and_no_files_read_as_no_detail(self):
        subject, detail, files = git_delivery.commit_details("Bump version\x00Signed-off-by: A <a@b.c>\n\x00\n")
        self.assertEqual((subject, detail, files), ("Bump version", "", "Changed files: none."))
        long_subject, long_detail, _files = git_delivery.commit_details("x" * 500 + "\x00" + "y" * 500 + "\x00")
        self.assertEqual((len(long_subject), len(long_detail)), (200, 200))

    def test_a_commit_too_large_to_list_is_labeled_from_its_message_alone(self):
        tip = "a" * 40
        calls = []
        base = _subject_runner({tip: "Vendor the parser"}, tracked=("refs/remotes/origin/main",),
                               pushes={"refs/remotes/origin/main": tip}, git_calls=calls)

        def runner(argv, **options):
            if tuple(argv[argv.index("-C") + 2:])[0:2] == ("show", "--numstat") and "--format=%s%x00%b%x00" in argv:
                return {"returncode": None, "stdout": ""}  # timed out
            return base(argv, **options)

        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(str(Path(tmp) / "delivery.sqlite3"), runner=runner,
                                               now=lambda: 200, salt="test-salt")
            service.commit_classifier = classifier = FakeCommitClassifier()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
        self.assertEqual([subject for *_keys, subject in classifier.subjects], ["Vendor the parser"])
        self.assertEqual(classifier.details, [("Explain the change.", "Changed files: not listed.")])
        self.assertIn("subject-only", calls)

    def test_scan_hands_detail_and_file_kinds_but_never_paths_to_the_classifier(self):
        tip = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            service = meter.GitDeliveryService(
                str(Path(tmp) / "delivery.sqlite3"),
                runner=_subject_runner({tip: "Stop the overflow"}, tracked=("refs/remotes/origin/main",),
                                       pushes={"refs/remotes/origin/main": tip}),
                now=lambda: 200, salt="test-salt",
            )
            service.commit_classifier = classifier = FakeCommitClassifier()
            service.scan([{"root": "/repo", "project": "repo · a1"}])
            stored = b"".join(path.read_bytes() for path in Path(tmp).iterdir())
        self.assertEqual(classifier.details, [("Explain the change.", "Changed files: 1 code (+2 / -1 lines).")])
        self.assertNotIn("app.py", json.dumps(classifier.details))
        self.assertNotIn(b"Explain the change", stored)


if __name__ == "__main__":
    unittest.main()
