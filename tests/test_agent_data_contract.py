import unittest
import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
import meter
from tests.dashboard_page import DASHBOARD_PAGE
from tests import test_builder_recap  # module import: its test classes are not collected twice


class AgentDataContractTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "safe-session", "provider": "codex", "client": "codex", "label": "Codex",
            "path": "/private/logs/secret.jsonl", "session": "secret.jsonl",
            "project": "/Users/test/work/repository", "mtime": meter.time.time(),
        }
        self.state = {
            "provider": "codex", "source": self.source, "session": "secret.jsonl",
            "project": self.source["project"], "total_cost": 1.25, "cost_approx": True,
            "total_tokens": 120000, "turns": 4, "last_turn_cost": 0.21,
            "context": {"latest": 40000, "window": 200000, "latest_pct": 0.2},
            "tools": {"total_output_tokens": 9000, "flagged_tokens": 1000, "by_name": []},
            "executions": [{"idx": 4, "cost": 0.21, "tokens": {
                "input": 40000, "output": 900, "retrieval": 1000,
            }, "context_pct": 0.2}],
            "trace": [
                {"execution": 4, "kind": "user", "label": "User message", "detail": "private prompt"},
                {"execution": 4, "kind": "tool_call", "label": "search", "detail": "private args"},
                {"execution": 4, "kind": "tool_result", "label": "search", "detail": "private result", "tokens": 1000},
            ],
            "insights": [], "ended": False,
        }

    def test_check_matches_runtime_and_project_and_omits_content(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(execution=4, caller={
                "runtime": "codex", "project": "/Users/test/work/repository/subdir",
            })
        encoded = json.dumps(result)
        self.assertTrue(result["ok"])
        self.assertNotIn("project", result["selected_session"])
        self.assertNotIn("repository", encoded)
        self.assertEqual(result["data_scope"], "matched_current_run")
        self.assertNotIn("private prompt", encoded)
        self.assertNotIn("private args", encoded)
        self.assertNotIn("private result", encoded)
        self.assertNotIn("/private/logs", encoded)
        self.assertLessEqual(len(result["evidence"]), 3)
        self.assertLessEqual(len(result["execution"]["activity"]), 5)
        selected_tools = next(row for row in result["evidence"] if row["label"] == "Selected execution tool results")
        self.assertEqual(selected_tools["value"], 1000)

    def test_default_check_does_not_present_run_wide_tool_tokens_as_latest(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })
        latest_tools = next(row for row in result["evidence"] if row["label"] == "Latest execution tool results")
        self.assertEqual(latest_tools["value"], 1000)
        self.assertNotEqual(latest_tools["value"], self.state["tools"]["total_output_tokens"])

    def test_cost_check_answers_the_cost_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="cost", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("$1.25", result["answer"])
        self.assertIn("cost", result["recommended_action"].lower())

    def test_context_check_answers_the_context_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="context", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("20%", result["answer"])
        self.assertIn("context", result["recommended_action"].lower())

    def test_check_dashboard_url_targets_the_single_run_surface(self):
        state = {**self.state, "context": {"latest": 180000, "window": 200000,
                                           "latest_pct": 0.9}}
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=state):
            result = meter.agent_check(caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertTrue(result["dashboard_url"].endswith("#summary"))

    def test_tools_check_answers_the_tool_volume_question(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="tools", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("9,000", result["answer"])
        self.assertIn("tool", result["recommended_action"].lower())

    def test_next_phase_check_summarizes_all_readiness_signals(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]), \
                mock.patch.object(meter, "recompute", return_value=self.state):
            result = meter.agent_check(focus="next_phase", caller={
                "runtime": "codex", "project": "/Users/test/work/repository",
            })

        self.assertIn("20%", result["answer"])
        self.assertIn("$1.25", result["answer"])
        self.assertIn("9,000", result["answer"])
        self.assertIn("next phase", result["recommended_action"].lower())

    def test_missing_context_percentage_is_not_described_as_zero(self):
        state = {"context": {"latest_pct": None}, "last_turn_cost": 0,
                 "insights": [], "executions": [], "ended": False}
        recommendation = meter.menubar_recommendation(state)
        verdict = meter.menubar_verdict(state, recommendation)
        self.assertIn("not reported", verdict["detail"])
        self.assertNotIn("Context is 0%", verdict["detail"])

    def test_dashboard_omits_removed_session_tab_payloads(self):
        state = {
            "source": {"id": "session", "provider": "codex"},
            "trace": [{"kind": "usage"}],
            "insights": [{"kind": "warn"}],
            "tools": {
                "total_calls": 7,
                "total_output_tokens": 1000,
                "unique_used": 1,
                "by_namespace": [{"namespace": "shell"}],
                "by_name": [{"name": "exec"}],
                "by_execution": [{"execution": 2}],
            },
        }
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            projected = meter.dashboard_state_payload(state)

        self.assertNotIn("trace", projected)
        self.assertNotIn("insights", projected)
        self.assertEqual(projected["tools"]["total_calls"], 7)
        self.assertEqual(projected["tools"]["total_output_tokens"], 1000)
        self.assertEqual(projected["tools"]["unique_used"], 1)
        for removed in ("by_namespace", "by_name", "by_execution"):
            self.assertNotIn(removed, projected["tools"])
        self.assertEqual(state["trace"], [{"kind": "usage"}])

    def test_dashboard_payload_replaces_trace_paths_with_opaque_keys(self):
        from token_meter.domain.compare import trace_key
        current = "/Users/private-user/.codex/sessions/rollout-1.jsonl"
        other = "/Users/private-user/.claude/projects/p/two.jsonl"
        state = {
            "source": {"id": "one", "path": current, "project": "~/p"},
            "xsession": {
                "sessions": [
                    {"id": "one", "path": current, "project": "~/p"},
                    {"id": "two", "path": other, "project": "~/p"},
                ],
                "current_sessions": [{"id": "one", "project": "~/p"}],
                "capabilities": {
                    "items": [],
                    "claude_desktop": {
                        "local_agent_sessions": 1,
                        "roots": ["~/Library/Application Support/Claude"],
                    },
                },
            },
        }
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            projected = meter.dashboard_state_payload(state)

        encoded = json.dumps(projected)
        self.assertNotIn("private-user", encoded)
        self.assertNotIn("path", projected["source"])
        self.assertEqual(projected["source"]["key"], trace_key({"path": current}))
        self.assertEqual(projected["source"]["project"], "~/p")
        rows = projected["xsession"]["sessions"]
        self.assertEqual([row["key"] for row in rows],
                         [trace_key({"path": current}), trace_key({"path": other})])
        self.assertTrue(all("path" not in row for row in rows))
        self.assertEqual([row["project"] for row in rows], ["~/p", "~/p"])
        self.assertEqual(projected["xsession"]["current_sessions"], [{"id": "one", "project": "~/p"}])
        desktop = projected["xsession"]["capabilities"]["claude_desktop"]
        self.assertNotIn("roots", desktop)
        self.assertEqual(desktop["local_agent_sessions"], 1)
        self.assertEqual(state["source"]["path"], current)
        self.assertEqual(state["xsession"]["sessions"][0]["path"], current)
        self.assertIn("roots", state["xsession"]["capabilities"]["claude_desktop"])

    def test_dashboard_reprojects_agent_data_at_the_browser_boundary(self):
        state = {
            "source": {"id": "root", "provider": "codex"},
            "agent_group": {
                "root_session_id": "root", "selected_agent_id": "root",
                "coverage": {}, "totals": {"agents": 2},
                "agents": [{
                    "id": "root", "kind": "root", "depth": 0,
                    "agent_path": "/private/agent.md", "prompt": "secret",
                }],
                "_all_agents": [{"path": "/private/trace.jsonl"}],
            },
            "xsession": {
                "agent_usage": {
                    "totals": {"agents": 1},
                    "models": [{
                        "id": "model::codex", "runtime": "codex",
                        "model": "model", "agents": 1,
                        "session_id": "private-session",
                    }],
                },
            },
        }
        registry = mock.Mock()
        registry.descriptors = ()

        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            projected = meter.dashboard_state_payload(state)

        encoded = json.dumps(projected)
        self.assertEqual(projected["agent_group"]["root_session_id"], "root")
        self.assertEqual(
            projected["xsession"]["agent_usage"]["models"][0]["id"],
            "model::codex",
        )
        for forbidden in (
            "/private", "secret", "_all_agents", "agent_path",
            "private-session", "session_id\": \"private-session",
        ):
            self.assertNotIn(forbidden, encoded)

    def _live_state_with_cross(self, cross):
        return {
            "ok": True,
            "source": {"id": "root", "provider": "codex"},
            "trace": [{"kind": "usage", "text": "PRIVATE-TRACE"}],
            "insights": [{"text": "PRIVATE-INSIGHT"}],
            "recommendation": "PRIVATE-RECOMMENDATION",
            "verdict": "PRIVATE-VERDICT",
            "tools": {
                "total_calls": 3,
                "by_name": [{"name": "PRIVATE-TOOL"}],
                "by_execution": [{"execution": 1}],
            },
            "agent_group": {
                "root_session_id": "root", "selected_agent_id": "root",
                "coverage": {}, "totals": {"agents": 1},
                "agents": [{"id": "root", "kind": "root", "depth": 0,
                            "agent_path": "/private/agent.md", "prompt": "PRIVATE-PROMPT"}],
                "_all_agents": [{"path": "/private/trace.jsonl"}],
            },
            "total_cost": 1.5,
            "xsession": cross,
        }

    def test_dashboard_state_body_replaces_trace_paths_on_cached_and_held_polls(self):
        from token_meter.domain.compare import trace_key
        current = "/Users/private-user/.codex/sessions/rollout-1.jsonl"
        other = "C:\\Users\\private-user\\.claude\\projects\\p\\two.jsonl"
        cross = {
            "generated_at": 100,
            "sessions": [
                {"id": "one", "path": current, "project": "~/p"},
                {"id": "two", "path": other, "project": "~/p"},
            ],
        }
        state = {"source": {"id": "one", "path": current}, "xsession": cross}
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            first = meter.dashboard_state_body(state)
            full = json.loads(first)
            again = json.loads(meter.dashboard_state_body(state))
            held = json.loads(meter.dashboard_state_body(state, full["xsession_revision"]))

        self.assertNotIn("private-user", first)
        self.assertEqual(full["source"]["key"], trace_key({"path": current}))
        self.assertEqual([row["key"] for row in full["xsession"]["sessions"]],
                         [trace_key({"path": current}), trace_key({"path": other})])
        self.assertEqual(again["xsession"], full["xsession"])
        self.assertNotIn("xsession", held)
        self.assertEqual(held["source"], full["source"])
        self.assertEqual(cross["sessions"][0]["path"], current)

    def test_dashboard_state_body_matches_projected_payload(self):
        cross = {
            "generated_at": 100,
            "tool_waste": {"secret": True},
            "agent_usage": {
                "totals": {"agents": 1},
                "models": [{"id": "model::codex", "runtime": "codex", "model": "model",
                            "agents": 1, "session_id": "private-session"}],
            },
            "daily": [{"day": "2026-10-23", "cost": 2.0}],
        }
        state = self._live_state_with_cross(cross)
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            expected = meter.dashboard_state_payload(state)
            body = json.loads(meter.dashboard_state_body(state))

        revision = body.pop("xsession_revision")
        self.assertTrue(revision)
        # Discounts are attached fresh from settings, outside the cached snapshot.
        self.assertEqual(set(body.pop("cost_discounts")), set(meter.cost_discount_settings()))
        self.assertEqual(body, expected)
        self.assertNotIn("tool_waste", body["xsession"])
        encoded = json.dumps(body)
        for forbidden in ("PRIVATE", "/private", "_all_agents", "agent_path",
                          "private-session", "by_name", "by_execution"):
            self.assertNotIn(forbidden, encoded)

    def test_dashboard_state_body_omits_unchanged_cross_session_snapshot(self):
        cross = {"generated_at": 100, "daily": [{"day": "2026-10-23"}]}
        state = self._live_state_with_cross(cross)
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            first = json.loads(meter.dashboard_state_body(state))
            revision = first["xsession_revision"]
            state["total_cost"] = 2.5
            repeat = json.loads(meter.dashboard_state_body(state, revision))
            stale = json.loads(meter.dashboard_state_body(state, "older"))

        self.assertNotIn("xsession", repeat)
        self.assertTrue(repeat["xsession_unchanged"])
        self.assertEqual(repeat["xsession_revision"], revision)
        self.assertEqual(repeat["total_cost"], 2.5)
        self.assertEqual(stale["xsession"], first["xsession"])
        self.assertNotIn("xsession_unchanged", stale)

    def test_dashboard_state_body_encodes_each_cross_snapshot_once(self):
        cross = {"generated_at": 100, "daily": [{"day": "2026-10-23"}]}
        rebuilt = {"generated_at": 100, "daily": [{"day": "2026-10-24"}]}
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            with mock.patch.object(
                    meter, "_dashboard_public_cross",
                    wraps=meter._dashboard_public_cross) as project:
                first = json.loads(meter.dashboard_state_body(
                    self._live_state_with_cross(cross)))
                again = json.loads(meter.dashboard_state_body(
                    self._live_state_with_cross(cross)))
                self.assertEqual(project.call_count, 1)
                changed = json.loads(meter.dashboard_state_body(
                    self._live_state_with_cross(rebuilt),
                    first["xsession_revision"],
                ))
                self.assertEqual(project.call_count, 2)

        self.assertEqual(first["xsession_revision"], again["xsession_revision"])
        self.assertNotEqual(changed["xsession_revision"], first["xsession_revision"])
        self.assertEqual(changed["xsession"]["daily"], [{"day": "2026-10-24"}])

    def test_dashboard_state_body_keeps_published_and_rebuilt_snapshots_encoded(self):
        published = {"generated_at": 100, "daily": [{"day": "a"}]}
        rebuilt = {"generated_at": 115, "daily": [{"day": "b"}]}
        registry = mock.Mock()
        registry.descriptors = ()
        previous_state, saved_cache = meter.STATE, dict(meter._xsess)
        try:
            meter.STATE = self._live_state_with_cross(published)
            meter._xsess["data"] = rebuilt
            with mock.patch.object(meter, "runtime_registry", return_value=registry), \
                    mock.patch.object(meter, "_dashboard_public_cross",
                                      wraps=meter._dashboard_public_cross) as project:
                revisions = [
                    json.loads(meter.dashboard_state_body(
                        self._live_state_with_cross(cross)))["xsession_revision"]
                    for cross in (published, rebuilt, published, rebuilt)
                ]
                self.assertEqual(project.call_count, 2)
                self.assertEqual(revisions[0], revisions[2])
                self.assertEqual(revisions[1], revisions[3])
                self.assertNotEqual(revisions[0], revisions[1])

                replacement = {"generated_at": 130, "daily": [{"day": "c"}]}
                meter.STATE = self._live_state_with_cross(replacement)
                meter._xsess["data"] = replacement
                meter.dashboard_state_body(meter.STATE)
                cached = [entry[0] for entry in meter._dashboard_cross_cache["entries"]]
                self.assertEqual(len(cached), 1)
                self.assertIs(cached[0], replacement)
        finally:
            meter.STATE = previous_state
            meter._xsess.clear()
            meter._xsess.update(saved_cache)

    def test_state_route_omits_cross_session_snapshot_the_client_holds(self):
        cross = {"generated_at": 100, "daily": [{"day": "2026-10-23"}]}
        previous = meter.STATE
        try:
            meter.STATE = self._live_state_with_cross(cross)
            request = test_builder_recap.BuilderRecapEndpointTests.request
            full, status, content_type = request(self, "/state")
            self.assertEqual(status, 200)
            self.assertEqual(content_type, "application/json")
            revision = full["xsession_revision"]
            self.assertEqual(full["xsession"]["daily"], cross["daily"])
            held, _, _ = request(self, "/state?xsession=" + revision)
            stale, _, _ = request(self, "/state?xsession=older")
        finally:
            meter.STATE = previous
        self.assertNotIn("xsession", held)
        self.assertTrue(held["xsession_unchanged"])
        self.assertEqual(held["xsession_revision"], revision)
        self.assertEqual(held["total_cost"], full["total_cost"])
        self.assertEqual(stale["xsession"], full["xsession"])
        for body in (full, held, stale):
            self.assertNotIn("PRIVATE", json.dumps(body))

    def test_session_route_omits_cross_session_snapshot_the_client_holds(self):
        cross = {"generated_at": 100, "current_sessions": [{"id": "session-1"}]}
        source = {"id": "session-1", "path": "/logs/exact.jsonl"}
        saved_cache = dict(meter._xsess)

        def get(path):
            handler = object.__new__(meter.H)
            handler.path = path
            handler.headers = {"Host": "127.0.0.1:8722"}
            sent = []
            handler._send = lambda body, *_args, **_kwargs: sent.append(json.loads(body))
            state = {"source": {"id": "session-1"}, "timing": {}}
            with mock.patch.object(meter, "find_session", return_value=source), \
                    mock.patch.object(meter, "cached_session_state", return_value=state), \
                    mock.patch.object(meter, "selected_session_stats", return_value={}):
                handler.do_GET()
            return sent[0]

        try:
            meter._xsess["data"] = cross
            full = get("/session?id=session-1&live=1")
            held = get("/session?id=session-1&live=1&xsession=" + full["xsession_revision"])
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertEqual(full["xsession"]["current_sessions"], cross["current_sessions"])
        self.assertFalse(full["ended"])
        self.assertNotIn("xsession", held)
        self.assertTrue(held["xsession_unchanged"])
        self.assertFalse(held["ended"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for live polling verification")
    def test_live_polling_reuses_only_the_snapshot_matching_its_revision(self):
        page_path = Path(DASHBOARD_PAGE)
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start-(page.slice(start-6,start)==='async '?6:0),i+1);}}throw Error(`unclosed ${{name}}`);}}
eval(extract('adoptXsession'));eval(extract('fetchLiveJson'));
const urls=[];let replies=[];
global.fetch=async url=>{{urls.push(url);const r=replies.shift();return {{ok:r.status===undefined,status:r.status||200,json:async()=>r}};}};
(async()=>{{
 const holder={{snapshot:null,revision:''}};
 replies=[{{xsession_revision:'r1',xsession:{{n:1}}}}];
 const first=await fetchLiveJson('/state',holder,'State');
 replies=[{{xsession_revision:'r1',xsession_unchanged:true}}];
 const reused=await fetchLiveJson('/session?id=a&live=1',holder,'Session');
 holder.revision='r1';holder.snapshot={{n:1}};
 replies=[{{xsession_revision:'r2',xsession_unchanged:true}},{{xsession_revision:'r2',xsession:{{n:2}}}}];
 const mismatch=await fetchLiveJson('/state',holder,'State');
 replies=[{{status:503}}];
 let error='';try{{await fetchLiveJson('/state',holder,'State');}}catch(e){{error=e.message;}}
 console.log(JSON.stringify({{urls,first:first.xsession,reused:reused.xsession,sameObject:reused.xsession===first.xsession,
  mismatch:mismatch.xsession,held:holder.revision,error}}));
}})().catch(e=>{{console.error(e);process.exit(1);}});
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "urls": [
                "/state?xsession=",
                "/session?id=a&live=1&xsession=r1",
                "/state?xsession=r1",
                "/state?xsession=",
                "/state?xsession=r2",
            ],
            "first": {"n": 1},
            "reused": {"n": 1},
            "sameObject": True,
            "mismatch": {"n": 2},
            "held": "r2",
            "error": "State request failed (503).",
        })

    def test_dashboard_state_body_without_cross_session_has_no_revision(self):
        registry = mock.Mock()
        registry.descriptors = ()
        with mock.patch.object(meter, "runtime_registry", return_value=registry):
            body = json.loads(meter.dashboard_state_body(
                {"ok": False, "loading": True, "source": {}}, "stale",
            ))
        self.assertNotIn("xsession", body)
        self.assertNotIn("xsession_unchanged", body)
        self.assertEqual(body["xsession_revision"], "")

    def test_publish_skips_serialization_without_subscribers(self):
        state = {"ok": True, "source": {"id": "s"}}
        previous = meter.STATE
        try:
            with mock.patch.object(meter.json, "dumps") as dumps:
                meter.publish(state)
            dumps.assert_not_called()
            self.assertIs(meter.STATE, state)
            q_ = meter.queue.Queue(maxsize=2)
            with meter.subscribers_lock:
                meter.subscribers.append(q_)
            try:
                meter.publish(state)
            finally:
                with meter.subscribers_lock:
                    meter.subscribers.remove(q_)
            self.assertEqual(q_.get_nowait(), "data: " + json.dumps(state) + "\n\n")
        finally:
            meter.STATE = previous

    def test_check_refuses_to_fall_back_across_projects(self):
        with mock.patch.object(meter, "all_session_sources", return_value=[self.source]):
            result = meter.agent_check(caller={"runtime": "codex", "project": "/another/repository"})
        self.assertFalse(result["ok"])
        self.assertIn("did not fall back", result["caveat"])

    def test_claude_trace_cwd_preserves_hyphenated_project_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(json.dumps({
                "type": "user", "cwd": "/Users/test/Documents/github/token-meter",
            }) + "\n")
            cwd = meter.claude_trace_cwd(str(path))
        self.assertEqual(cwd, "/Users/test/Documents/github/token-meter")

    def test_current_run_resolution_prefers_exact_project_over_newer_parent(self):
        now = meter.time.time()
        exact = {**self.source, "id": "exact", "project": "/Users/test/work/repository", "mtime": now - 10}
        parent = {**self.source, "id": "parent", "project": "/Users/test/work", "mtime": now}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[parent, exact],
        )
        self.assertEqual(selected["id"], "exact")
        self.assertEqual(resolution, "matched")

    def test_current_run_resolution_rejects_stale_implicit_match(self):
        stale = {**self.source, "mtime": meter.time.time() - meter.AGENT_CURRENT_MAX_AGE_S - 1}
        selected, resolution = meter.resolve_agent_source(
            caller={"runtime": "codex", "project": "/Users/test/work/repository"},
            sources=[stale],
        )
        self.assertIsNone(selected)
        self.assertIn("No recent Codex run", resolution)

    def test_usage_is_aggregate_only_and_ranked_categories_are_bounded(self):
        cross = {
            "daily": [{"day": time_day, "cost": 2.0, "sessions": 2,
                       "providers": [{"provider": "codex", "cost": 2.0}],
                       "tool_tokens": 1000, "flagged_tokens": 300}
                      for time_day in ("2026-07-01", "2026-06-30")],
            "sessions": [{"cost_approx": True, "title": "private title", "project": "/private/repo"}],
            "model_mix": [{"model": f"model-{idx}", "cost": idx, "tokens": idx * 100}
                          for idx in range(8)],
            "tool_waste": {"by_name": [
                {"name": f"tool-{idx}", "namespace": "safe", "output_tokens": idx * 100, "calls": idx}
                for idx in range(8)
            ]},
        }
        with mock.patch.dict(meter._xsess, {
                "data": cross, "at": meter.time.time(), "internal_rows": (),
        }):
            result = meter.agent_usage(window="7d", focus="models")
        encoded = json.dumps(result)
        self.assertEqual(result["data_scope"], "anonymous_aggregate_history")
        self.assertLessEqual(len(result["categories"]), 5)
        self.assertTrue(result["dashboard_url"].endswith("/#spend"))
        self.assertNotIn("private title", encoded)
        self.assertNotIn("/private/repo", encoded)

    def test_usage_model_categories_are_scoped_to_calendar_window(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)
        outside_seven_days = today - datetime.timedelta(days=7)
        daily = [
            {"day": today.isoformat(), "cost": 2.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 2.0}]},
            {"day": recent.isoformat(), "cost": 3.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 3.0}]},
            {"day": outside_seven_days.isoformat(), "cost": 5.0, "sessions": 1,
             "providers": [{"provider": "codex", "cost": 5.0}]},
        ]
        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_model_daily": [
                {"model": "model-current", "day": today.isoformat(), "cost": 2.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
                {"model": "model-week", "day": recent.isoformat(), "cost": 3.0,
                 "input_tokens": 200, "output_tokens": 30, "executions": 1},
                {"model": "model-fortnight", "day": outside_seven_days.isoformat(), "cost": 5.0,
                 "input_tokens": 300, "output_tokens": 50, "executions": 1},
            ],
        }
        cross = {
            "daily": daily, "sessions": [internal],
            "model_mix": [{"model": "all-time", "cost": 999.0, "tokens": 999999}],
            "tool_waste": {"by_name": []},
        }

        with mock.patch.dict(meter._xsess, {
                "data": cross, "at": meter.time.time(), "internal_rows": (internal,),
        }):
            today_result = meter.agent_usage(window="today", focus="models")
            week_result = meter.agent_usage(window="7d", focus="models")
            fortnight_result = meter.agent_usage(window="14d", focus="models")

        self.assertEqual([row["model"] for row in today_result["categories"]], ["model-current"])
        self.assertEqual(
            {row["model"] for row in week_result["categories"]},
            {"model-current", "model-week"},
        )
        self.assertEqual(
            {row["model"] for row in fortnight_result["categories"]},
            {"model-current", "model-week", "model-fortnight"},
        )
        self.assertEqual([today_result["days_observed"], week_result["days_observed"],
                          fortnight_result["days_observed"]], [1, 2, 3])

    def test_usage_tool_categories_and_evidence_share_the_selected_window(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)
        outside_seven_days = today - datetime.timedelta(days=7)

        def midday(day):
            return int(time.mktime((*day.timetuple()[:3], 12, 0, 0, 0, 0, -1)))

        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_tool_evidence": meter.summarize_tool_evidence([
                {**meter.tool_identity("search"), "output_tokens": 120,
                 "ts": midday(today), "args_fingerprint": "today", "error": False},
                {**meter.tool_identity("fetch"), "output_tokens": 80,
                 "ts": midday(recent), "args_fingerprint": "recent", "error": False},
                {**meter.tool_identity("search"), "output_tokens": 500,
                 "ts": midday(outside_seven_days), "args_fingerprint": "older", "error": False},
            ]),
        }
        cross = {
            "daily": [
                {"day": today.isoformat(), "cost": 2.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 2.0}]},
                {"day": recent.isoformat(), "cost": 3.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 3.0}]},
                {"day": outside_seven_days.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
            ],
            "sessions": [internal], "model_mix": [],
            "tool_waste": {"by_name": [
                {"name": "search", "namespace": "unknown", "output_tokens": 620, "calls": 2},
                {"name": "fetch", "namespace": "unknown", "output_tokens": 80, "calls": 1},
            ]},
        }

        with mock.patch.dict(meter._xsess, {
                "data": cross, "at": meter.time.time(), "internal_rows": (internal,),
        }):
            today_result = meter.agent_usage(window="today", focus="tools")
            week_result = meter.agent_usage(window="7d", focus="tools")
            fortnight_result = meter.agent_usage(window="14d", focus="tools")

        def tool_evidence(result):
            return next(row["value"] for row in result["evidence"]
                        if row["label"] == "Trace-observed tool results")

        self.assertEqual(today_result["categories"], [{
            "name": "search", "namespace": "search", "returned_tokens": 120, "calls": 1,
        }])
        self.assertEqual(tool_evidence(today_result), 120)
        self.assertEqual(tool_evidence(week_result), 200)
        self.assertEqual(tool_evidence(fortnight_result), 700)
        self.assertEqual(
            {row["name"]: row["returned_tokens"] for row in week_result["categories"]},
            {"search": 120, "fetch": 80},
        )

    def test_usage_headline_and_action_answer_the_requested_focus(self):
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=1)

        def midday(day):
            return int(time.mktime((*day.timetuple()[:3], 12, 0, 0, 0, 0, -1)))

        internal = {
            "id": "one", "provider": "codex", "runtime": "Codex",
            "availability": {"cost": True, "tokens": True},
            "_model_daily": [
                {"model": "model-top", "day": today.isoformat(), "cost": 5.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
                {"model": "model-top", "day": recent.isoformat(), "cost": 5.0,
                 "input_tokens": 100, "output_tokens": 20, "executions": 1},
            ],
            "_tool_evidence": meter.summarize_tool_evidence([
                {**meter.tool_identity("search"), "output_tokens": 9000,
                 "ts": midday(today), "args_fingerprint": "today", "error": False},
            ]),
        }
        cross = {
            "daily": [
                {"day": today.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
                {"day": recent.isoformat(), "cost": 5.0, "sessions": 1,
                 "providers": [{"provider": "codex", "cost": 5.0}]},
            ],
            "sessions": [internal], "model_mix": [], "tool_waste": {"by_name": []},
        }

        with mock.patch.dict(meter._xsess, {
                "data": cross, "at": meter.time.time(), "internal_rows": (internal,),
        }):
            spend = meter.agent_usage(window="7d", focus="spend")
            models = meter.agent_usage(window="7d", focus="models")
            tools = meter.agent_usage(window="7d", focus="tools")

        self.assertIn("$10.00", spend["answer"])
        self.assertIn("spend", spend["recommended_action"].lower())
        self.assertIn("model-top", models["answer"])
        self.assertIn("model", models["recommended_action"].lower())
        self.assertIn("9,000", tools["answer"])
        self.assertIn("tool", tools["recommended_action"].lower())
        self.assertEqual(len({spend["answer"], models["answer"], tools["answer"]}), 3)

    def _windowed_usage_fixture(self):
        today = datetime.date.today()
        now = meter.time.time()

        def midday(days_ago):
            day = today - datetime.timedelta(days=days_ago)
            return day, time.mktime((*day.timetuple()[:3], 12, 0, 0, 0, 0, -1))

        def source(session_id, provider, days_ago, **extra):
            _day, ts = midday(days_ago)
            return {"id": session_id, "provider": provider, "path": f"/missing/{session_id}.jsonl",
                    "mtime": min(ts, now), "signature_mtime": min(ts, now), **extra}

        sources = [
            source("recent-claude", "claude", 0),
            source("old-claude", "claude", 30),
            source("older-codex", "codex", 40, cost_approx=True),
            source("old-cursor", "cursor", 50),
            source("week-codex", "codex", 6),
        ]
        calls = []

        def summary(row, opencode_conn=None):
            calls.append(row["id"])
            day, ts = midday(int((today - datetime.date.fromtimestamp(row["mtime"])).days))
            key = day.isoformat()
            return {
                "id": row["id"], "provider": row["provider"], "runtime": row["provider"],
                "mtime": row["mtime"], "turns": 1, "cost_approx": bool(row.get("cost_approx")),
                "availability": {"cost": True, "tokens": True},
                "_day_cost": {key: 1.0}, "_model_daily": [{
                    "model": "m-" + row["id"], "day": key, "cost": 1.0,
                    "input_tokens": 10, "output_tokens": 2, "executions": 1,
                }],
            }

        return sources, calls, summary

    def _windowed_usage(self, sources, summary, **patches):
        with mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}), \
                mock.patch.object(meter, "all_session_sources", return_value=list(sources)), \
                mock.patch.object(meter, "session_summary", side_effect=summary), \
                mock.patch.object(meter, "cross_session", side_effect=AssertionError), \
                contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(meter, name, value))
            return {
                (window, focus): meter.agent_usage(window=window, focus=focus)
                for window in ("today", "7d", "14d")
                for focus in ("spend", "models", "tools", "changes")
            }

    def test_windowed_usage_skips_bounded_sessions_outside_the_window(self):
        sources, calls, summary = self._windowed_usage_fixture()
        with mock.patch.object(meter, "AGENT_USAGE_RECENT_SESSIONS", 2):
            windowed = self._windowed_usage(sources, summary)
        self.assertNotIn("old-claude", calls)
        self.assertNotIn("older-codex", calls)
        self.assertIn("old-cursor", calls)
        self.assertEqual(set(calls), {"recent-claude", "week-codex", "old-cursor"})

        calls.clear()
        with mock.patch.object(meter, "AGENT_USAGE_RECENT_SESSIONS", 2):
            unfiltered = self._windowed_usage(
                sources, summary, source_activity_bound=lambda _source: None,
            )
        self.assertEqual(set(calls), {source["id"] for source in sources})
        for key, result in windowed.items():
            with self.subTest(key=key):
                expected = dict(unfiltered[key])
                actual = dict(result)
                expected.pop("as_of")
                actual.pop("as_of")
                self.assertEqual(actual, expected)
        self.assertEqual(
            [row["model"] for row in windowed[("7d", "models")]["categories"]],
            ["m-recent-claude", "m-week-codex"],
        )
        self.assertEqual(windowed[("7d", "models")]["approximate_fields"], [])

    def test_windowed_usage_settles_the_recent_session_list(self):
        sources, calls, summary = self._windowed_usage_fixture()
        with mock.patch.object(meter, "AGENT_USAGE_RECENT_SESSIONS", 3):
            windowed = self._windowed_usage(sources, summary)
        # Three recent rows need the newest out-of-window bounded session, and only it.
        self.assertIn("old-claude", calls)
        self.assertNotIn("older-codex", calls)
        self.assertEqual(windowed[("7d", "models")]["approximate_fields"], [])

        calls.clear()
        with mock.patch.object(meter, "AGENT_USAGE_RECENT_SESSIONS", 4):
            windowed = self._windowed_usage(sources, summary)
        self.assertIn("older-codex", calls)
        self.assertEqual(windowed[("7d", "models")]["approximate_fields"], ["cost"])

    def test_usage_serves_the_previous_snapshot_while_history_reprices(self):
        today = datetime.date.today().isoformat()
        cross = {"daily": [{"day": today, "cost": 4.0, "sessions": 1,
                            "providers": [{"provider": "codex", "cost": 4.0}]}],
                 "sessions": []}
        with mock.patch.dict(meter._xsess, {"data": cross, "at": 0.0, "internal_rows": ()}), \
                mock.patch.object(meter, "pricing_recalculation_running", return_value=True), \
                mock.patch.object(meter, "all_session_sources", side_effect=AssertionError):
            result = meter.agent_usage(window="today", focus="spend")
        self.assertIn("$4.00", result["answer"])

    def test_activity_bound_uses_the_newest_tracked_time_and_only_bounded_runtimes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "trace.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            os.utime(path, (2_000.0, 2_000.0))
            self.assertEqual(meter.source_activity_bound(
                {"provider": "claude", "path": path, "mtime": 1_000.0, "signature_mtime": 1_500.0},
            ), 2_000.0)
            self.assertEqual(meter.source_activity_bound(
                {"provider": "codex", "path": path, "mtime": 3_000.0},
            ), 3_000.0)
        for source in (
            {"provider": "cursor", "mtime": 1.0},
            {"provider": "claude", "mtime": float("nan")},
            {"provider": "codex", "mtime": "bad"},
            {"provider": "claude"},
            None,
        ):
            with self.subTest(source=source):
                self.assertIsNone(meter.source_activity_bound(source))

    def test_capability_result_names_only_requested_evidence(self):
        cross = {"capabilities": {
            "summary": {"optional": {
                "enabled": 2, "unused": 1,
                "review_candidates": ["skill_pack:Codex:docs@personal"],
            }},
            "control_groups": [
                {"id": "skill_pack:Codex:docs@personal", "name": "docs@personal",
                 "control_type": "skill_pack", "runtime": "Codex", "mutable": True,
                 "reviewable": True, "used": False, "enabled": True, "activations": 0,
                 "environment": {"TOKEN": "secret"}},
                {"name": "tokenmeter", "control_type": "skill_pack", "runtime": "Codex",
                 "used": False, "enabled": True},
            ],
        }}
        with mock.patch.object(meter, "agent_capability_evidence",
                               return_value=(cross["capabilities"], {"complete": True})):
            result = meter.agent_capabilities(scope="all", limit=5)
        encoded = json.dumps(result)
        self.assertEqual([row["name"] for row in result["candidates"]], ["docs@personal"])
        self.assertNotIn("TOKEN", encoded)
        self.assertNotIn("secret", encoded)

    def test_all_scope_capabilities_returns_only_counted_review_candidates(self):
        candidate_id = "skill_pack:Codex:z-review"
        cross = {"capabilities": {
            "summary": {"optional": {
                "enabled": 1, "unused": 1, "review_candidates": [candidate_id],
            }},
            "control_groups": [
                {"id": "skill_pack:Codex:a-disabled", "name": "a-disabled",
                 "control_type": "skill_pack", "runtime": "Codex", "mutable": True,
                 "reviewable": True, "used": False, "enabled": False},
                {"id": candidate_id, "name": "z-review", "control_type": "skill_pack",
                 "runtime": "Codex", "mutable": True, "reviewable": True,
                 "used": False, "enabled": True},
            ],
        }}

        with mock.patch.object(meter, "agent_capability_evidence",
                               return_value=(cross["capabilities"], {"complete": True})):
            result = meter.agent_capabilities(scope="all", limit=5)

        self.assertEqual([row["name"] for row in result["candidates"]], ["z-review"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates_returned"], 1)

    @contextlib.contextmanager
    def cold_session_history(self, count, parse_s, discovery_s=0.0, discovery_error=None):
        sources = [
            {"provider": "codex", "path": f"/synthetic/{index}.jsonl", "id": str(index),
             "mtime": float(index)}
            for index in range(count)
        ]
        parsed = []
        scanned_rows = []

        def discover():
            time.sleep(discovery_s)
            if discovery_error is not None:
                raise discovery_error
            return sources

        def summarize(source, opencode_conn=None):
            if source["path"] in meter._summary_cache:
                return meter._summary_cache[source["path"]]["row"]
            time.sleep(parse_s)
            parsed.append(source["path"])
            row = {"turns": 1, "path": source["path"], "mtime": source["mtime"]}
            meter._summary_cache[source["path"]] = {"signature": source["path"], "row": row}
            return row

        def waste(rows):
            scanned_rows[:] = rows
            return {}

        inventory = {
            "summary": {"optional": {"enabled": 1, "review_candidates": ["skill_pack:Codex:docs"]}},
            "control_groups": [{"id": "skill_pack:Codex:docs", "name": "docs",
                                "control_type": "skill_pack", "runtime": "Codex",
                                "used": False, "enabled": True}],
        }
        with mock.patch.dict(meter._xsess, {"data": None}), \
                mock.patch.dict(meter._history_scan, {"progress": None, "last_complete": None}), \
                mock.patch.dict(meter._summary_cache, {}, clear=True), \
                mock.patch.object(meter, "all_session_sources", side_effect=discover), \
                mock.patch.object(meter, "canonical_aggregation_sources", side_effect=list), \
                mock.patch.object(meter, "session_summary", side_effect=summarize), \
                mock.patch.object(meter, "global_tool_waste", side_effect=waste), \
                mock.patch.object(meter, "capability_inventory", return_value=inventory), \
                mock.patch.object(meter, "cross_session", side_effect=AssertionError("full aggregate")):
            try:
                yield parsed, scanned_rows
            finally:
                # The scan thread must not outlive these patches.
                progress = meter._history_scan["progress"]
                if progress is not None:
                    self.assertTrue(progress["done"].wait(10))

    def wait_for_history_scan(self):
        self.assertTrue(meter._history_scan["progress"]["done"].wait(10))

    def test_cold_capabilities_answers_inside_the_client_timeout_with_partial_coverage(self):
        self.assertLess(meter.AGENT_HISTORY_BUDGET_S, 60)
        with self.cold_session_history(count=40, parse_s=0.1) as (parsed, scanned_rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 1.0):
            started = time.monotonic()
            result = meter.agent_capabilities(scope="all", limit=5)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0 + 1.0)
            coverage = result["coverage"]
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["sessions_total"], 40)
            self.assertGreater(coverage["sessions_scanned"], 0)
            self.assertLess(coverage["sessions_scanned"], 40)
            self.assertEqual(result["assessment"], "Partial review")
            self.assertIn("unscanned session", result["caveat"])
            self.assertIn(f"{coverage['sessions_scanned']} of 40", result["answer"])
            self.assertEqual(result["evidence"][-1]["label"], "Sessions scanned")
            # Newest sources are scanned first.
            self.assertEqual(
                sorted(row["mtime"] for row in scanned_rows),
                [float(index) for index in range(40 - len(scanned_rows), 40)],
            )

            self.wait_for_history_scan()
            again = meter.agent_capabilities(scope="all", limit=5)

        self.assertEqual(again["coverage"],
                         {"complete": True, "sessions_scanned": 40, "sessions_total": 40})
        self.assertEqual(again["assessment"], "Review available")
        self.assertNotIn("unscanned session", again["caveat"])
        self.assertEqual(len(again["evidence"]), 2)
        # The background scan parsed each session once.
        self.assertEqual(sorted(parsed), sorted(set(parsed)))
        self.assertEqual(len(parsed), 40)

    def test_capabilities_answers_while_discovery_outlasts_the_budget(self):
        with self.cold_session_history(count=5, parse_s=0, discovery_s=2.0) as (parsed, _rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 0.05):
            started = time.monotonic()
            result = meter.agent_capabilities(scope="all", limit=5)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.5)
            self.assertEqual(parsed, [])
            self.assertFalse(result["ok"])
            self.assertEqual(result["candidates"], [])
            self.assertNotIn("candidate_count", result)
            self.assertEqual(result["evidence"],
                             [{"label": "Sessions scanned", "value": "0; discovery still running"}])
            self.assertEqual(result["coverage"],
                             {"complete": False, "sessions_scanned": 0, "sessions_total": None})

    def test_cold_current_scope_answers_before_discovery_finishes(self):
        with self.cold_session_history(count=3, parse_s=0, discovery_s=2.0) as (parsed, _rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 0.05), \
                mock.patch.object(meter, "recompute", side_effect=AssertionError("no run yet")):
            started = time.monotonic()
            result = meter.agent_capabilities(
                scope="current", limit=5, caller={"runtime": "codex", "project": "/synthetic"},
            )
            elapsed = time.monotonic() - started
            discoveries = meter.all_session_sources.call_count

        self.assertLess(elapsed, 1.5)
        # Run resolution waits for the scan's discovery instead of starting its own.
        self.assertEqual(discoveries, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["assessment"], "History scan incomplete")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["evidence"],
                         [{"label": "Sessions scanned", "value": "0; discovery still running"}])
        self.assertNotIn("0 of 0", json.dumps(result))

    def test_current_scope_resolves_the_run_from_the_scan_discovery(self):
        current = {"provider": "codex", "path": "/synthetic/2.jsonl", "id": "2"}
        summary = {"enabled": 1, "groups": [
            {"id": "skill_pack:Codex:docs", "name": "docs", "control_type": "skill_pack",
             "runtime": "Codex", "current_used": False},
        ]}
        resolve = mock.Mock(return_value=(current, ""))
        with self.cold_session_history(count=40, parse_s=0.1), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 1.0), \
                mock.patch.object(meter, "resolve_agent_source", resolve), \
                mock.patch.object(meter, "recompute", return_value={"trace": []}), \
                mock.patch.object(meter, "agent_session_summary", return_value={"id": "2"}), \
                mock.patch.object(meter, "session_optional_capabilities", return_value=summary):
            result = meter.agent_capabilities(scope="current", limit=5)
            discoveries = meter.all_session_sources.call_count

        self.assertEqual(discoveries, 1)
        self.assertEqual(len(resolve.call_args.kwargs["sources"]), 40)
        self.assertTrue(result["ok"])
        self.assertEqual(result["assessment"], "Partial review")
        scanned = result["coverage"]["sessions_scanned"]
        self.assertGreater(scanned, 0)
        self.assertEqual(result["evidence"][-1]["value"], f"{scanned} of 40")
        self.assertEqual([row["name"] for row in result["candidates"]], ["docs"])

    def test_a_scan_thread_that_cannot_start_is_retried(self):
        with self.cold_session_history(count=3, parse_s=0):
            with mock.patch.object(meter.threading.Thread, "start",
                                   side_effect=RuntimeError("can't start new thread")):
                with self.assertRaises(RuntimeError):
                    meter.agent_capabilities(scope="all", limit=5)
            stuck = meter._history_scan["progress"]
            self.assertTrue(stuck["done"].is_set())
            result = meter.agent_capabilities(scope="all", limit=5)
            self.assertIsNot(meter._history_scan["progress"], stuck)

        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"],
                         {"complete": True, "sessions_scanned": 3, "sessions_total": 3})

    def test_cold_git_stats_withholds_totals_until_the_history_scan_completes(self):
        project = mock.Mock(return_value={"ok": True, "projected": True})
        spend_rows = []
        with self.cold_session_history(count=40, parse_s=0.1) as (_parsed, _rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 1.0), \
                mock.patch.object(meter, "cached_git_delivery_candidates", return_value=()), \
                mock.patch.object(meter, "git_delivery_ledger_candidates", return_value=()), \
                mock.patch.object(meter, "delivery_spend_rows",
                                  side_effect=lambda rows: spend_rows.append(
                                      [row["mtime"] for row in rows]) or []), \
                mock.patch.object(meter, "delivery_model_spend_rows", return_value=[]), \
                mock.patch.object(meter, "git_delivery_service") as service, \
                mock.patch.object(meter._mcp_delivery, "git_stats_projection", project):
            started = time.monotonic()
            cold = meter.agent_git_stats(range="30")
            elapsed = time.monotonic() - started
            self.wait_for_history_scan()
            warm = meter.agent_git_stats(range="30")

        self.assertLess(elapsed, 1.0 + 1.0)
        self.assertTrue(cold["ok"])
        self.assertFalse(cold["available"])
        self.assertEqual(cold["reason"], "history_scan_incomplete")
        self.assertFalse(cold["history"]["complete"])
        self.assertEqual(cold["history"]["sessions_total"], 40)
        self.assertNotIn("current", cold)
        self.assertEqual(warm, {"ok": True, "projected": True})
        # Totals are computed only from the complete scan, in discovery order like cross_session.
        self.assertEqual(spend_rows, [[float(index) for index in range(40)]])
        service.return_value.query.assert_called_once()

    def test_cold_work_stats_withholds_totals_until_the_history_scan_completes(self):
        settings = {"enabled": True, "paused_until": 0, "areas": [], "backfill_days": 0}
        service = mock.Mock()
        service.status.return_value = {"labels": 2, "pending": 0}
        build = mock.Mock(return_value=({"insights": True}, None))
        with self.cold_session_history(count=3, parse_s=0, discovery_s=2.0) as (_parsed, _rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 0.05), \
                mock.patch.object(meter, "work_insights_supported", return_value=True), \
                mock.patch.object(meter, "work_insights_settings", return_value=settings), \
                mock.patch.object(meter, "work_insights_service", return_value=service), \
                mock.patch.object(meter, "_work_insights_aggregate", build), \
                mock.patch.object(meter._mcp_delivery, "work_stats_projection",
                                  return_value={"ok": True, "available": True}):
            started = time.monotonic()
            cold = meter.agent_work_stats(period="30d", view="suggestions")
            elapsed = time.monotonic() - started
            self.wait_for_history_scan()
            warm = meter.agent_work_stats(period="30d", view="suggestions")

        self.assertLess(elapsed, 1.5)
        self.assertFalse(cold["available"])
        self.assertEqual(cold["reason"], "history_scan_incomplete")
        self.assertEqual(cold["history"],
                         {"complete": False, "sessions_scanned": 0, "sessions_total": None})
        self.assertIn("withheld instead of undercounted", cold["caveat"])
        self.assertTrue(warm["available"])
        build.assert_called_once()
        self.assertEqual(len(build.call_args.kwargs["source_rows"]), 3)

    def test_history_scan_fills_newest_chunks_on_the_index_pool(self):
        chunks = []
        prefetch = mock.Mock(side_effect=lambda sources: chunks.append(
            [int(source["id"]) for source in sources]))
        with self.cold_session_history(count=40, parse_s=0), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_FIRST", 16), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MIN", 16), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MAX", 16), \
                mock.patch.object(meter, "prefetch_session_summaries", prefetch):
            result = meter.agent_capabilities(scope="all", limit=5)

        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(chunks, [list(range(39, 23, -1)), list(range(23, 7, -1)), list(range(7, -1, -1))])

    def test_history_scan_shrinks_chunks_that_overrun_the_time_target(self):
        sizes = []

        def slow_fill(sources):
            sizes.append(len(sources))
            time.sleep(0.05)
            return len(sources)

        with self.cold_session_history(count=40, parse_s=0), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_FIRST", 16), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MIN", 4), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MAX", 64), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_TARGET_S", 0.01), \
                mock.patch.object(meter, "prefetch_session_summaries", side_effect=slow_fill):
            result = meter.agent_capabilities(scope="all", limit=5)

        self.assertTrue(result["coverage"]["complete"])
        # 16 sessions parsed in ~0.05 s is ~320/s, so a 0.01 s target is 3, clamped to 4.
        self.assertEqual(sizes, [16] + [4] * 6)

    def test_history_scan_never_grows_chunks_from_cached_or_tiny_fills(self):
        sizes = []
        returns = iter([0, 4, 40] + [0] * 20)

        def fill(sources):
            sizes.append(len(sources))
            return next(returns)

        with self.cold_session_history(count=60, parse_s=0), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_FIRST", 8), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MIN", 4), \
                mock.patch.object(meter, "HISTORY_SCAN_CHUNK_MAX", 64), \
                mock.patch.object(meter, "prefetch_session_summaries", side_effect=fill):
            result = meter.agent_capabilities(scope="all", limit=5)

        self.assertTrue(result["coverage"]["complete"])
        # A cached (0) or sub-pool (4) fill keeps the size; a fast real fill at most doubles it.
        self.assertEqual(sizes[:4], [8, 8, 8, 16])

    def test_a_refresh_serves_the_last_complete_scan_while_it_runs(self):
        with self.cold_session_history(count=5, parse_s=0) as (_parsed, _rows), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 5), \
                mock.patch.object(meter, "HISTORY_REFRESH_WAIT_S", 0.05):
            first = meter.agent_capabilities(scope="all", limit=5)
            self.assertTrue(first["coverage"]["complete"])
            self.expire_history_scan()
            discovered = threading.Event()
            release = threading.Event()

            def slow_discovery():
                discovered.set()
                release.wait(5)
                return [{"provider": "codex", "path": f"/synthetic/{index}.jsonl", "id": str(index),
                         "mtime": float(index)} for index in range(5)]

            with mock.patch.object(meter, "all_session_sources", side_effect=slow_discovery):
                started = time.monotonic()
                during = meter.agent_capabilities(scope="all", limit=5)
                elapsed = time.monotonic() - started
                release.set()
                self.wait_for_history_scan()

        self.assertTrue(discovered.is_set())
        self.assertLess(elapsed, 1.5)
        self.assertEqual(during["coverage"], {
            "complete": True, "sessions_scanned": 5, "sessions_total": 5, "refreshing": True,
        })
        self.assertEqual(during["assessment"], "Review available")

    def expire_history_scan(self):
        meter._history_scan["progress"]["finished_at"] -= meter._XSESS_TTL + 1

    def test_a_current_scope_refresh_resolves_the_run_from_the_last_complete_scan(self):
        summary = {"enabled": 1, "groups": [
            {"id": "skill_pack:Codex:docs", "name": "docs", "control_type": "skill_pack",
             "runtime": "Codex", "current_used": False},
        ]}
        resolve = mock.Mock(return_value=({"provider": "codex", "path": "/synthetic/4.jsonl", "id": "4"}, ""))
        release = threading.Event()

        def slow_discovery():
            release.wait(5)
            return []

        with self.cold_session_history(count=5, parse_s=0), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 5), \
                mock.patch.object(meter, "HISTORY_REFRESH_WAIT_S", 0.05), \
                mock.patch.object(meter, "resolve_agent_source", resolve), \
                mock.patch.object(meter, "recompute", return_value={"trace": []}), \
                mock.patch.object(meter, "agent_session_summary", return_value={"id": "4"}), \
                mock.patch.object(meter, "session_optional_capabilities", return_value=summary):
            meter.agent_capabilities(scope="current", limit=5)
            self.expire_history_scan()
            with mock.patch.object(meter, "all_session_sources", side_effect=slow_discovery):
                started = time.monotonic()
                during = meter.agent_capabilities(scope="current", limit=5)
                elapsed = time.monotonic() - started
                release.set()
                self.wait_for_history_scan()

        self.assertLess(elapsed, 1.5)
        self.assertTrue(during["ok"])
        self.assertEqual(len(resolve.call_args.kwargs["sources"]), 5)
        self.assertTrue(during["coverage"]["refreshing"])

    @contextlib.contextmanager
    def warm_current_scope(self, previous_age_s=0.0):
        """A process whose last complete scan finished and has expired; yields the resolve mock."""
        summary = {"enabled": 1, "groups": [
            {"id": "skill_pack:Codex:docs", "name": "docs", "control_type": "skill_pack",
             "runtime": "Codex", "current_used": False},
        ]}
        resolve = mock.Mock(return_value=({"provider": "codex", "path": "/synthetic/4.jsonl", "id": "4"}, ""))
        with self.cold_session_history(count=5, parse_s=0), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 1.0), \
                mock.patch.object(meter, "HISTORY_REFRESH_WAIT_S", 0.05), \
                mock.patch.object(meter, "resolve_agent_source", resolve), \
                mock.patch.object(meter, "recompute", return_value={"trace": []}), \
                mock.patch.object(meter, "agent_session_summary", return_value={"id": "4"}), \
                mock.patch.object(meter, "session_optional_capabilities", return_value=summary), \
                contextlib.redirect_stderr(io.StringIO()):
            meter.agent_capabilities(scope="current", limit=5)
            self.wait_for_history_scan()
            previous = meter._history_scan["last_complete"]
            self.assertIs(previous, meter._history_scan["progress"])
            self.expire_history_scan()
            previous["discovered_at"] -= previous_age_s
            resolve.reset_mock()
            yield resolve

    @staticmethod
    def fresh_sources(count):
        return [{"provider": "codex", "path": f"/fresh/{index}.jsonl", "id": f"f{index}",
                 "mtime": float(100 + index)} for index in range(count)]

    def test_a_current_scope_refresh_that_fails_discovery_reuses_a_recent_scan_once(self):
        with self.warm_current_scope() as resolve:
            failing = mock.Mock(side_effect=OSError("gone"))
            with mock.patch.object(meter, "all_session_sources", failing):
                result = meter.agent_capabilities(scope="current", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(len(resolve.call_args.kwargs["sources"]), 5)
        self.assertTrue(result["coverage"]["refreshing"])
        # One call starts one refresh scan, even when that scan fails.
        self.assertEqual(failing.call_count, 1)

    def test_a_current_scope_refresh_with_old_sources_waits_like_a_cold_call(self):
        with self.warm_current_scope(previous_age_s=meter.HISTORY_SOURCE_REUSE_S + 1) as resolve:
            fresh = self.fresh_sources(3)

            def discovery():
                time.sleep(0.3)  # Longer than the refresh cap, shorter than the call budget.
                return fresh

            with mock.patch.object(meter, "all_session_sources", side_effect=discovery):
                result = meter.agent_capabilities(scope="current", limit=5)
                self.wait_for_history_scan()

        self.assertTrue(result["ok"])
        self.assertEqual(resolve.call_args.kwargs["sources"], tuple(fresh))

    def test_a_current_scope_refresh_never_resolves_from_old_sources(self):
        release = threading.Event()
        with self.warm_current_scope(previous_age_s=meter.HISTORY_SOURCE_REUSE_S + 1) as resolve:
            with mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 0.1), \
                    mock.patch.object(meter, "all_session_sources", side_effect=lambda: release.wait(5) and []):
                result = meter.agent_capabilities(scope="current", limit=5)
                release.set()
                self.wait_for_history_scan()

        self.assertFalse(result["ok"])
        self.assertEqual(result["assessment"], "History scan incomplete")
        resolve.assert_not_called()

    def test_a_current_scope_refresh_with_old_sources_reports_failed_discovery(self):
        with self.warm_current_scope(previous_age_s=meter.HISTORY_SOURCE_REUSE_S + 1) as resolve:
            with mock.patch.object(meter, "all_session_sources", side_effect=OSError("gone")), \
                    self.assertRaisesRegex(RuntimeError, "could not scan session history"):
                meter.agent_capabilities(scope="current", limit=5)
        resolve.assert_not_called()

    def test_a_refresh_that_fails_after_discovery_resolves_from_its_own_sources(self):
        with self.warm_current_scope() as resolve:
            fresh = self.fresh_sources(3)
            summarize = meter.session_summary.side_effect

            def failing_summary(source, opencode_conn=None):
                if source["path"].startswith("/fresh/"):
                    raise ValueError("unreadable")
                return summarize(source, opencode_conn)

            with mock.patch.object(meter, "all_session_sources", return_value=fresh), \
                    mock.patch.object(meter, "session_summary", side_effect=failing_summary):
                result = meter.agent_capabilities(scope="current", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(resolve.call_args.kwargs["sources"], tuple(fresh))
        self.assertTrue(result["coverage"]["refreshing"])

    def test_a_failed_refresh_falls_back_to_the_last_complete_scan(self):
        with self.cold_session_history(count=5, parse_s=0), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 5), \
                contextlib.redirect_stderr(io.StringIO()):
            meter.agent_capabilities(scope="all", limit=5)
            self.expire_history_scan()
            with mock.patch.object(meter, "all_session_sources", side_effect=OSError("gone")):
                result = meter.agent_capabilities(scope="all", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], {
            "complete": True, "sessions_scanned": 5, "sessions_total": 5, "refreshing": True,
        })

    def test_capabilities_reports_a_failed_history_scan_and_retries_it(self):
        stderr = io.StringIO()
        with self.cold_session_history(count=3, parse_s=0, discovery_error=OSError("/secret/path boom")), \
                mock.patch.object(meter, "AGENT_HISTORY_BUDGET_S", 5), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaisesRegex(RuntimeError, "could not scan session history"):
                meter.agent_capabilities(scope="all", limit=5)
            failed = meter._history_scan["progress"]
            with self.assertRaises(RuntimeError):
                meter.agent_capabilities(scope="all", limit=5)
            self.assertIsNot(meter._history_scan["progress"], failed)

        self.assertIn("history scan: internal error at OSError at", stderr.getvalue())
        self.assertNotIn("secret", stderr.getvalue())
        self.assertNotIn("boom", stderr.getvalue())

    def test_agent_result_has_a_hard_serialized_bound(self):
        result = meter.bounded_agent_result({
            "answer": "a" * 10000, "evidence": [{"label": "x", "value": "y" * 10000}] * 10,
            "recommended_action": "z" * 10000, "caveat": "c" * 10000,
        })
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
