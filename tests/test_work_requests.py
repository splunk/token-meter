"""Per-request Work labels: cost slices, action evidence, per-request questions, and request aggregation."""

import json
import tempfile
import unittest
from unittest import mock

import meter
from token_meter.domain import work as domain
from token_meter.domain import work_evidence as E
from token_meter.services import work_insights as W
from tests.test_work_insights import (AREA_Q, COMPLEXITY_Q, WORK_TYPE_Q, FakeClient, drain, letter_for,
                                      make_service, model_response)

AREAS = [{"name": "Frontend & UI", "description": "d"}, {"name": "Backend & APIs", "description": "d"},
         {"name": "Docs & writing", "description": "d"}]


class EvidenceTests(unittest.TestCase):
    def test_tool_kinds_cover_claude_codex_and_cursor_names(self):
        for name, kind in (("Edit", "edit"), ("Write", "edit"), ("apply_patch", "edit"), ("Apply patch", "edit"),
                           ("edit_file_v2", "edit"), ("Read", "read"), ("Bash", "shell"),
                           ("exec_command", "shell"), ("Shell", "shell"), ("Grep", "search"),
                           ("WebFetch", "web"), ("Agent", "agent"), ("TaskCreate", "plan"),
                           ("mcp__x__y", "mcp"), ("AskUserQuestion", "other")):
            self.assertEqual(E.tool_kind(name), kind, name)

    def test_command_kinds_keep_categories_not_text(self):
        self.assertEqual(E.command_kinds("cd x && PYTHONPATH=. python3 -m unittest discover -s tests | tail"),
                         ["test", "inspect"])
        self.assertEqual(E.command_kinds("git commit -m 'secret words' && git push"), ["git", "git"])
        self.assertEqual(E.command_kinds("launchctl kickstart -k gui/501/x"), ["service"])
        self.assertEqual(E.command_kinds("docker compose up -d"), ["infra"])
        self.assertEqual(E.command_kinds("git status --short"), ["git_read"])

    def test_file_kinds(self):
        for path, kind in (("page.html", "frontend"), ("token_meter/app.py", "backend"),
                           ("tests/test_meter.py", "tests"), ("specs/WORK_INSIGHTS.md", "docs"),
                           ("AGENTS.md", "tooling"), ("scripts/install", "tooling"),
                           (".github/workflows/ci.yml", "infra"), ("src/components/Button.ts", "frontend"),
                           ("notebooks/x.ipynb", "data"), ("Dockerfile", "infra"), ("config.yaml", "config")):
            self.assertEqual(E.file_kind(path), kind, path)

    def test_events_belong_to_the_latest_request_started_before_them(self):
        actions = [E.tool_action("Edit", {"file_path": "/repo/page.html"}, 120),
                   E.tool_action("Bash", {"command": "pytest -q"}, 130),
                   E.tool_action("Edit", {"file_path": "/repo/app.py"}, 260)]
        # Resumed traces can list requests out of order; ownership goes by time.
        slices, edited = E.request_slices([100, 250, 200], [(50, 0.5, "m1", ""), (150, 1.0, "m1", ""),
                                                            (210, 2.0, "m2", "high"), (300, 4.0, "m2", "")],
                                          actions)
        self.assertEqual([s["cost"] for s in slices], [1.5, 4.0, 2.0])
        self.assertEqual([s["model"] for s in slices], ["m1", "m2", "m2"])
        self.assertEqual(slices[2]["effort"], "high")
        self.assertEqual(slices[0]["actions"], {"edit": 1, "shell": 1, "cmd_test": 1})
        self.assertEqual(slices[0]["files"], {"frontend": 1})
        self.assertEqual(list(edited[1]), ["/repo/app.py"])
        self.assertNotIn("/repo", json.dumps(slices))

    def test_requests_without_timestamps_own_nothing_and_all_missing_falls_back(self):
        slices, _edited = E.request_slices([100, 0, 200], [(150, 1.0, "m", ""), (250, 2.0, "m", "")])
        self.assertEqual([s["cost"] for s in slices], [1.0, 0.0, 2.0])
        self.assertEqual(E.request_slices([0, 0], [(1, 1.0, "m", "")]), (None, None))

    def test_evidence_names_changed_files_only(self):
        slices, edited = E.request_slices(
            [100, 200, 300], [],
            [E.tool_action("Edit", {"file_path": f"/r/f{i}.py"}, 110) for i in range(8)]
            + [E.tool_action("Bash", {"command": "git status"}, 120)]
            + [E.tool_action("Bash", {"command": "git commit -m x && git push"}, 210)])
        self.assertEqual(E.evidence_text(slices[0], edited[0]),
                         "Files the agent changed: f0.py, f1.py, f2.py, f3.py, f4.py, f5.py and 2 more.")
        self.assertEqual(E.evidence_text(slices[1], edited[1]),
                         "The agent changed no files; it committed, pushed, or opened pull requests.")
        self.assertEqual(E.evidence_text(slices[2], edited[2]), "The agent changed no files.")

    def test_code_mode_scripts_yield_their_tool_calls(self):
        actions = E.code_actions('const r = await tools.exec_command({cmd: "npm test"});\n'
                                 'await tools.apply_patch("*** Begin Patch\\n*** Update File: src/app/page.tsx\\n@@");',
                                 ts=5)
        self.assertEqual([(a["kind"], a["commands"], a["paths"]) for a in actions],
                         [("shell", ["test"], []), ("edit", [], ["src/app/page.tsx"])])


class AttachTests(unittest.TestCase):
    def test_requests_get_cost_and_evidence_only_while_enabled(self):
        turns = [{"ts": 100.0, "text": "build the chart", "model": "m"},
                 {"ts": 200.0, "text": "now write the docs", "model": "m"}]
        events = [(150.0, 1.0, "m", ""), (250.0, 3.0, "m", "")]
        actions = [E.tool_action("Write", {"file_path": "/secret/dir/README.md"}, 260)]
        enabled = W.normalize_settings({"enabled": True})
        with mock.patch.object(meter, "work_insights_settings", return_value=enabled):
            row = meter.attach_work_requests({}, [dict(t) for t in turns], events, actions)
            captured = meter._WORK_TURNS.turns
            meter._WORK_TURNS.turns = None
        self.assertEqual([r["cost"] for r in row["_work_requests"]], [1.0, 3.0])
        self.assertEqual(captured[1]["evidence"], "Files the agent changed: README.md.")
        self.assertEqual(captured[1]["cost"], 3.0)
        self.assertNotIn("secret", json.dumps(row))
        with mock.patch.object(meter, "work_insights_settings", return_value=W.normalize_settings({})):
            row = meter.attach_work_requests({}, [dict(t) for t in turns], events, actions)
        self.assertEqual(row["_work_requests"], [])
        self.assertEqual(len(row["_work_turn_days"]), 2)


class RequestQuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_every_substantive_request_is_labeled_and_short_replies_only_get_pushback(self):
        service, _values, _clock = make_service(self.tmp.name)
        session = [
            {"ts": 1_799_999_000.0, "text": "add a dark mode toggle", "model": "m", "context": "",
             "evidence": "Files the agent changed: page.html.", "cost": 1.0},
            {"ts": 1_799_999_100.0, "text": "yes", "model": "m", "context": "Shall I continue?", "cost": 0.2},
            {"ts": 1_799_999_200.0, "text": "now update the README with the new option", "model": "m",
             "context": "Done.", "evidence": "Files the agent changed: README.md.", "cost": 3.0},
        ]
        service.observe("s1", session)
        by_key = {item.turn_key: item for item in service.queue}
        self.assertEqual(by_key[service._turn_key("s1", 0)].questions, W.REQUEST_QUESTIONS)
        self.assertEqual(by_key[service._turn_key("s1", 1)].questions, W.TURN_QUESTIONS)
        self.assertEqual(by_key[service._turn_key("s1", 2)].questions, W.REQUEST_QUESTIONS + W.TURN_QUESTIONS)
        third = by_key[service._turn_key("s1", 2)]
        request_state, turn_state = third.state
        self.assertIn("Earlier request in this session:\nadd a dark mode toggle", request_state)
        self.assertIn("User's latest message:\nnow update the README", request_state)
        self.assertTrue(request_state.endswith("Files the agent changed: README.md."))
        self.assertNotIn("Files the agent changed", turn_state)
        # The most expensive request of the session goes first.
        self.assertEqual(service._next_item().turn_key, third.turn_key)

    def test_session_requests_returns_labels_per_turn_and_pushback_for_follow_ups(self):
        service, _values, _clock = make_service(self.tmp.name)

        def responder(prompt):
            # The state is a JSON string, so its line breaks appear as a backslash and "n".
            readme = "README" in prompt.split("message:\\n")[-1].split("\\n")[0]
            if WORK_TYPE_Q in prompt:
                return model_response(letter_for(prompt, "docs" if readme else "feature"))
            if AREA_Q in prompt:
                return model_response(letter_for(prompt, "Docs & writing" if readme else "Frontend & UI"))
            if COMPLEXITY_Q in prompt:
                return model_response(letter_for(prompt, "1"))
            return model_response(letter_for(prompt, "no"))
        FakeClient.responder = responder
        session = [{"ts": 1_799_999_000.0 + i * 100, "text": t, "model": "m", "context": "ok" if i else ""}
                   for i, t in enumerate(["add a dark mode toggle", "yes", "now update the README please"])]
        service.observe("s1", session)
        drain(service)
        labels = service.session_requests("s1", 3)
        self.assertEqual(labels[0]["work_type"], "feature")
        self.assertEqual(labels[0]["area"], "Frontend & UI")
        self.assertNotIn("pushback", labels[0])
        self.assertEqual(labels[1], {"pushback": False})
        self.assertEqual((labels[2]["work_type"], labels[2]["area"], labels[2]["pushback"]),
                         ("docs", "Docs & writing", False))
        # The session-level entry still comes from the opener.
        self.assertEqual(service.snapshot()[service.session_key("s1")]["area"], "Frontend & UI")


def row(row_id, days, costs=None, cost=None, model="gpt-5.6", runtime="Codex"):
    out = {"id": row_id, "project": "alpha", "runtime": runtime, "provider": "codex",
           "cost": cost if cost is not None else sum(costs or ()), "start": f"{days[0]} 10:00",
           "_day_cost": {}, "model_stats": [{"model": model, "cost": 1.0, "tokens": 100}],
           "_work_turn_days": list(days)}
    for day, value in zip(days, costs or [0] * len(days)):
        out["_day_cost"][day] = out["_day_cost"].get(day, 0) + value
    if costs is not None:
        out["_work_requests"] = [{"cost": c, "model": model, "effort": "", "actions": {}, "files": {}} for c in costs]
    return out


class RequestAggregationTests(unittest.TestCase):
    PRICES = {"gpt-5.6": 10.0, "cheap": 1.0, "mid": 4.0}

    def build(self, rows, requests, **kwargs):
        return domain.build_work_insights(
            rows, {}, lambda ident: ident.split("\0")[0], AREAS, lambda m, p: self.PRICES.get(m),
            today="2026-09-30", requests_for=lambda ident, n: requests.get(ident.split("\0")[0], [{}] * n), **kwargs)

    def test_a_long_session_splits_its_spend_across_the_areas_it_touched(self):
        rows = [row("long", ["2026-09-10"] * 4, [1.0, 30.0, 0.5, 8.5])]
        requests = {"long": [{"work_type": "plan", "area": "Backend & APIs"},
                             {"work_type": "feature", "area": "Frontend & UI", "pushback": False},
                             {"pushback": False},  # a short "yes" inherits the previous request's labels
                             {"work_type": "docs", "area": "Docs & writing", "pushback": True}]}
        out = self.build(rows, requests)
        spend = out["allocation"][-1]["spend"]
        self.assertEqual(spend, {"Backend & APIs": 1.0, "Frontend & UI": 30.5, "Docs & writing": 8.5})
        self.assertEqual(out["allocation"][-1]["sessions"], {"Frontend & UI": 1})
        economics = {e["work_type"]: e for e in out["economics"]}
        self.assertEqual(economics["feature"]["spend"], 30.5)
        # The feature task ended when the docs request pushed back on it.
        self.assertEqual(economics["feature"]["resolved_tasks"], 0)
        self.assertEqual(economics["feature"]["judged_tasks"], 1)
        self.assertEqual(economics["plan"]["resolved_tasks"], 1)
        self.assertEqual(out["coverage"]["requests"], 4)
        self.assertEqual(out["coverage"]["labeled_requests"], 3)

    def test_without_a_runtime_split_each_day_cost_spreads_over_that_days_requests(self):
        r = row("s", ["2026-09-10", "2026-09-10", "2026-09-11"])
        r["_day_cost"] = {"2026-09-10": 4.0, "2026-09-11": 2.0, "2026-09-12": 1.0}
        r["cost"] = 7.0
        requests = {"s": [{"work_type": "feature", "area": "Frontend & UI"}, {"work_type": "docs",
                                                                              "area": "Docs & writing"}, {}]}
        out = self.build([r], requests)
        totals = collections_sum(out)
        self.assertEqual(totals, {"Frontend & UI": 2.0, "Docs & writing": 5.0})

    def test_right_sizing_uses_each_requests_complexity_and_model(self):
        r = row("s", ["2026-09-10"] * 3, [5.0, 1.0, 2.0])
        r["_work_requests"][2]["model"] = "cheap"
        requests = {"s": [{"work_type": "feature", "area": "Frontend & UI", "complexity": "complex"},
                          {"work_type": "docs", "area": "Docs & writing", "complexity": "routine", "pushback": False},
                          {"complexity": "routine", "work_type": "docs", "area": "Docs & writing", "pushback": False}]}
        out = self.build([r], requests)
        cells = {(c["complexity"], c["tier"]): c for c in out["right_sizing"]["cells"]}
        self.assertEqual((cells[("routine", "premium")]["spend"], cells[("routine", "light")]["spend"],
                          cells[("complex", "premium")]["spend"]), (1.0, 2.0, 5.0))
        self.assertEqual(cells[("complex", "premium")]["rework"]["rate"], 0.0)

    def test_drill_down_returns_sessions_with_the_matching_spend(self):
        rows = [row("long", ["2026-09-10"] * 2, [10.0, 2.0]), row("doc", ["2026-09-11"], [3.0])]
        requests = {"long": [{"work_type": "feature", "area": "Frontend & UI"},
                             {"work_type": "docs", "area": "Docs & writing"}],
                    "doc": [{"work_type": "docs", "area": "Docs & writing"}]}
        out = domain.find_sessions(rows, {}, lambda ident: ident.split("\0")[0], AREAS,
                                   lambda m, p: self.PRICES.get(m), {"area": "Docs & writing"}, today="2026-09-30",
                                   requests_for=lambda ident, n: requests[ident.split("\0")[0]])
        self.assertEqual([(s["id"], s["match_cost"], s["cost"]) for s in out["sessions"]],
                         [("doc", 3.0, 3.0), ("long", 2.0, 12.0)])
        self.assertEqual(out["spend"], 5.0)

    def test_live_hints_follow_the_latest_request(self):
        r = row("live", ["2026-09-30"] * 2, [1.0, 1.0])
        r["session"] = "live"
        requests = {"live": [{"complexity": "complex"}, {"complexity": "routine"}]}
        hints = domain.live_session_hints([r, row("other", ["2026-09-30"], [1.0], model="mid")],
                                          [{"session": "live"}], {}, lambda ident: ident.split("\0")[0],
                                          lambda m, p: self.PRICES.get(m),
                                          requests_for=lambda ident, n: requests.get(ident.split("\0")[0], [{}] * n))
        self.assertEqual([h["kind"] for h in hints["live"]], ["premium_routine"])


def collections_sum(out):
    totals = {}
    for bucket in out["allocation"]:
        for area, value in bucket["spend"].items():
            totals[area] = round(totals.get(area, 0) + value, 6)
    return totals


class CleanTextTests(unittest.TestCase):
    def test_ambient_wrappers_uploads_and_interrupts(self):
        self.assertEqual(W.prepare_text('<in-app-browser-context source="x"> tabs </in-app-browser-context>'
                                        '  ## My request: yes this is your call'), "yes this is your call")
        self.assertEqual(W.prepare_text("<uploaded_files><file><file_path>/a/TAX FORM.pdf</file_path></file>"
                                        "</uploaded_files> review this please"),
                         "[uploaded: TAX FORM.pdf] review this please")
        self.assertEqual(W.prepare_text("[Request interrupted by user]"), "")


if __name__ == "__main__":
    unittest.main()


class ReviewFixTests(unittest.TestCase):
    PRICES = RequestAggregationTests.PRICES
    build = RequestAggregationTests.build

    def test_request_costs_add_up_to_the_session_cost(self):
        r = row("s", ["2026-09-10", "2026-09-10"], [0.0, 4.0], cost=10.0)
        out = self.build([r], {"s": [{"work_type": "feature", "area": "Frontend & UI"},
                                     {"work_type": "docs", "area": "Docs & writing"}]})
        self.assertEqual(collections_sum(out), {"Docs & writing": 10.0})

    def test_spend_stays_on_the_day_it_happened(self):
        r = row("late", ["2026-09-30"], [5.0])
        r["_work_requests"][0]["days"] = {"2026-09-30": 1.0, "2026-10-01": 4.0}
        requests = {"late": [{"work_type": "feature", "area": "Frontend & UI"}]}
        out = domain.build_work_insights([r], {}, lambda ident: ident.split("\0")[0], AREAS,
                                         lambda m, p: self.PRICES.get(m), today="2026-10-05", months=3,
                                         requests_for=lambda ident, n: requests[ident.split("\0")[0]])
        by_month = {b["month"]: b["spend"] for b in out["allocation"]}
        self.assertEqual((by_month["2026-09"], by_month["2026-10"]), ({"Frontend & UI": 1.0}, {"Frontend & UI": 4.0}))
        # Without a runtime split, each day's cost stays on its own day too.
        plain = row("plain", ["2026-09-30"])
        plain["_day_cost"], plain["cost"] = {"2026-09-30": 1.0, "2026-10-02": 9.0}, 10.0
        out = domain.build_work_insights([plain], {"plain": {"area": "Frontend & UI"}}, lambda ident: ident.split("\0")[0],
                                         AREAS, lambda m, p: self.PRICES.get(m), today="2026-10-05", months=3)
        by_month = {b["month"]: b["spend_total"] for b in out["allocation"]}
        self.assertEqual((by_month["2026-09"], by_month["2026-10"]), (1.0, 9.0))
        # A session with no readable requests keeps its daily split as well.
        silent = row("silent", ["2026-09-30"])
        silent["_work_turn_days"], silent["_day_cost"], silent["cost"] = [], {"2026-09-30": 2.0, "2026-10-01": 3.0}, 5.0
        out = domain.build_work_insights([silent], {}, lambda ident: ident.split("\0")[0], AREAS,
                                         lambda m, p: self.PRICES.get(m), today="2026-10-05", months=3)
        by_month = {b["month"]: b["spend"] for b in out["allocation"]}
        self.assertEqual((by_month["2026-09"], by_month["2026-10"]), ({"No request text": 2.0}, {"No request text": 3.0}))

    def test_one_session_cannot_carry_a_model_suggestion(self):
        # 24 alternating tasks in one session per model: enough tasks, too few sessions.
        def long_session(key, model, cost):
            days = ["2026-09-10"] * 48
            r = row(key, days, [cost] * 48, model=model)
            labels = [{"work_type": "debug" if i % 4 < 2 else "docs", "area": "Backend & APIs",
                       "complexity": "everyday", "pushback": False} for i in range(48)]
            labels[0].pop("pushback")
            return r, labels
        a, la = long_session("a", "gpt-5.6", 1.0)
        b, lb = long_session("b", "cheap", 0.1)
        out = self.build([a, b], {"a": la, "b": lb})
        self.assertFalse([r for r in out["recommendations"] if r["kind"] in ("switch_model", "family_upgrade")])
        self.assertFalse(any(c["best"] for c in out["model_fit"]["cells"]))

    def test_economics_drill_counts_what_the_row_counts(self):
        r = row("cross", ["2026-08-31", "2026-09-01"], [3.0, 2.0])
        requests = {"cross": [{"work_type": "feature", "area": "Frontend & UI"},
                              {"work_type": "feature", "area": "Frontend & UI", "pushback": False}]}
        requests_for = lambda ident, n: requests[ident.split("\0")[0]]
        key = lambda ident: ident.split("\0")[0]
        price = lambda m, p: self.PRICES.get(m)
        for months, expected in (("30d", 2.0), ("3", 5.0)):  # the task straddles the 30-day window's start
            out = domain.build_work_insights([r], {}, key, AREAS, price, today="2026-09-30", months=months,
                                             requests_for=requests_for)
            feature = next(e for e in out["economics"] if e["work_type"] == "feature")
            found = domain.find_sessions([r], {}, key, AREAS, price, {"work_type": "feature"}, today="2026-09-30",
                                         months=months, requests_for=requests_for)
            self.assertEqual((feature["spend"], found["spend"]), (expected, expected), months)

    def test_live_hints_use_the_model_of_the_labeled_request(self):
        r = row("live", ["2026-09-30"] * 2, [1.0, 1.0])
        r["session"] = "live"
        r["_work_requests"][0]["model"], r["_work_requests"][1]["model"] = "gpt-5.6", "cheap"
        requests = {"live": [{"complexity": "routine"}, {}]}  # the newest request is not labeled yet
        hints = domain.live_session_hints([r, row("o", ["2026-09-30"], [1.0], model="mid")], [{"session": "live"}],
                                          {}, lambda ident: ident.split("\0")[0], lambda m, p: self.PRICES.get(m),
                                          requests_for=lambda ident, n: requests.get(ident.split("\0")[0], [{}] * n))
        self.assertEqual([(h["kind"], h["model"]) for h in hints["live"]], [("premium_routine", "gpt-5.6")])

    def test_work_requests_stay_out_of_public_rows(self):
        from token_meter.domain.aggregates import aggregate_cross_session_rows
        r = row("s", ["2026-09-10"], [1.0])
        r.update({"session": "s", "runtime": "Codex", "provider": "codex", "title": "t", "mtime": 1})
        encoded = json.dumps(aggregate_cross_session_rows([r]), default=str)
        self.assertNotIn("_work_requests", encoded)


class GatingTests(unittest.TestCase):
    def test_runtime_adapters_skip_work_extraction_when_off(self):
        source = '''{"type":"user","timestamp":"2026-09-10T10:00:00Z","message":{"role":"user","content":"fix the chart please"}}
{"type":"assistant","timestamp":"2026-09-10T10:00:05Z","message":{"id":"m1","role":"assistant","model":"claude-sonnet-5","content":[{"type":"tool_use","id":"t1","name":"Edit","input":{"file_path":"/x/page.html"}}],"usage":{"input_tokens":10,"output_tokens":5}}}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/s.jsonl"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(source)
            src = {"provider": "claude", "path": path, "id": "s", "model": "claude-sonnet-5", "label": "Claude",
                   "session": "s", "project": "p", "title": "t", "mtime": 1}
            calls = []
            with mock.patch.object(meter, "claude_work_actions", side_effect=lambda msgs: calls.append(1) or []), \
                    mock.patch.object(meter, "work_insights_settings", return_value=W.normalize_settings({})):
                meter._claude_native_adapters.clear()
                row_off = meter.session_summary(src)
            self.assertEqual(calls, [])
            self.assertEqual(row_off.get("_work_requests"), [])


class QueueRaceTests(unittest.TestCase):
    def test_a_turn_requeued_while_running_stays_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _values, _clock = make_service(tmp)
            session = [{"ts": 1_799_999_000.0, "text": "add a dark mode toggle", "model": "m"}]
            service.observe("s1", session)
            running = service._next_item()
            service.observe("s1", session)  # a re-parse replaces the queued item while the first one runs
            service._finish_item(running)
            self.assertIn(running.turn_key, service.queued)
            self.assertEqual([item.turn_key for item in service.queue], [running.turn_key])
