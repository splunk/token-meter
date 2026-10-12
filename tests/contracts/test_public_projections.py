import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from token_meter.contracts import (
    DetailLevel,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    ParseWarning,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    ToolEvent,
    UsageEvidence,
)
from token_meter.projections import (
    agent_group_projection,
    agent_usage_projection,
    projection_bundle,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "normalized"


def evidence(pair):
    value, basis = pair
    return EvidenceValue(value, EvidenceBasis(basis))


def normalized(row):
    return NormalizedSession(
        source=SessionSource(
            runtime_id=row["runtime_id"],
            client_id=row["client_id"],
            session_id=row["session_id"],
            display_label=row["label"],
            project="/private/sentinel/project",
            locator=SourceLocator("file", "/private/sentinel/session.jsonl"),
            activity_mtime=1004.0,
            revision=SourceRevision(("private-revision",)),
            model_ref=ModelRef(row["model_provider_id"], row["model_id"]),
            account_provider_id=row["account_provider_id"],
        ),
        started_at=datetime.fromtimestamp(1000, timezone.utc),
        ended_at=datetime.fromtimestamp(1004, timezone.utc),
        usage=UsageEvidence(
            input_tokens=evidence(row["input"]),
            output_tokens=evidence(row["output"]),
            cache_read_tokens=evidence(row["cache_read"]),
            cache_write_tokens=evidence(row["cache_write"]),
            cost_usd=evidence(row["cost"]),
        ),
        timing=TimingEvidence(
            evidence(row.get("active", [None, "unavailable"])),
            evidence(row.get("wait", [None, "unavailable"])),
            evidence(row.get("ttft", [None, "unavailable"])),
        ),
        tools=tuple(ToolEvent(*tool) for tool in row.get("tools", [])),
        turns=(),
        pricing_basis=None,
        capabilities=frozenset(),
        warnings=tuple(ParseWarning(*warning) for warning in row.get("warnings", [])),
        detail=DetailLevel.FULL,
    )


class PublicProjectionTests(unittest.TestCase):
    def test_agent_group_projection_is_browser_only_and_strictly_allowlisted(self):
        private = {
            "root_session_id": "root",
            "selected_agent_id": "child",
            "coverage": {
                "relationships": "complete", "tokens": "complete",
                "cost": "estimated", "private": "no",
            },
            "totals": {
                "agents": 2, "tokens": 30, "known_tokens": 30,
                "tokens_available": True, "cost": 1.5,
                "known_cost": 1.5, "cost_available": True,
                "private": "/trace/path",
            },
            "child_totals": {
                "agents": 1, "tokens": 20, "known_tokens": 20,
                "tokens_available": True, "cost": 1.0,
                "known_cost": 1.0, "cost_available": True,
                "private": "/child/private",
            },
            "agents": [{
                "id": "child", "parent_id": "root", "runtime": "codex",
                "kind": "spawned", "depth": 1, "tokens": 20,
                "tokens_available": True, "cost": 1.0,
                "cost_available": True, "agent_path": "/private/agent.md",
                "prompt": "secret prompt", "tool_input": "secret tool input",
            }],
            "attention": [{
                "agent_id": "child", "level": "needs_attention",
                "reasons": [{
                    "code": "retry_pressure", "explanation": "3 retries",
                    "private": "secret",
                }],
            }],
            "hidden_agent_count": "invalid-provider-count",
            "_all_agents": [{"path": "/private/trace.jsonl"}],
            "_session_ids": {"private": "child"},
        }

        projected = agent_group_projection(private)
        encoded = json.dumps(projected, sort_keys=True)

        self.assertEqual(projected["selected_agent_id"], "child")
        self.assertEqual(projected["agents"][0]["cost"], 1.0)
        self.assertEqual(projected["child_totals"]["cost"], 1.0)
        self.assertEqual(projected["hidden_agent_count"], 0)
        for forbidden in (
            "/private", "secret prompt", "secret tool input",
            "/child/private", "_all_agents", "_session_ids", "agent_path",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_agent_usage_projection_keeps_anonymous_cohorts_only(self):
        projected = agent_usage_projection({
            "totals": {
                "agents": 1, "tokens": None, "known_tokens": 10,
                "tokens_available": False, "cost": None,
                "known_cost": 0.5, "cost_available": False,
                "parent_sessions": 1, "median_cost": None,
                "p95_cost": None, "output_per_dollar": None,
                "attention_agents": 1, "agent_spend_share": 0.5,
                "group_cost_coverage": "partial",
                "group_cost_covered_sessions": 0,
            },
            "models": [{
                "id": "model::codex", "runtime": "codex", "model": "model",
                "agents": 1, "tokens": None, "known_tokens": 10,
                "tokens_available": False, "cost": None,
                "known_cost": 0.5, "cost_available": False,
                "session_id": "private-session", "path": "/private/trace",
            }],
            "roles": [],
            "scopes": [{
                "window": "7d", "runtime": "codex",
                "totals": {
                    "agents": 1, "tokens": 10, "known_tokens": 10,
                    "tokens_available": True, "cost": 0.5,
                    "known_cost": 0.5, "cost_available": True,
                    "parent_sessions": 1, "median_cost": 0.5,
                    "p95_cost": 0.5, "output_per_dollar": 20,
                    "attention_agents": 1, "agent_spend_share": 0.5,
                    "group_cost_coverage": "estimated",
                },
                "models": [], "roles": [], "runtimes": [], "depths": [],
                "kinds": [], "private": "/scope/private",
            }],
        })

        self.assertIsNone(projected["totals"]["cost"])
        self.assertEqual(projected["totals"]["known_cost"], 0.5)
        self.assertEqual(projected["totals"]["parent_sessions"], 1)
        self.assertEqual(
            projected["totals"]["group_cost_covered_sessions"], 0,
        )
        self.assertEqual(projected["models"][0]["id"], "model::codex")
        self.assertEqual(projected["scopes"][0]["window"], "7d")
        self.assertEqual(projected["scopes"][0]["runtime"], "codex")
        self.assertEqual(projected["scopes"][0]["totals"]["median_cost"], 0.5)
        self.assertNotIn("private-session", json.dumps(projected))
        self.assertNotIn("/private/trace", json.dumps(projected))
        self.assertNotIn("/scope/private", json.dumps(projected))

    def test_agent_usage_projection_reports_unresolved_children(self):
        projected = agent_usage_projection({
            "totals": {
                "agents": 2, "unresolved_agents": 3,
                "unresolved_known_cost": 1.1804,
                "unresolved_private": "/private/path",
            },
        })

        self.assertEqual(projected["totals"]["unresolved_agents"], 3)
        self.assertAlmostEqual(
            projected["totals"]["unresolved_known_cost"], 1.1804,
        )
        self.assertNotIn("unresolved_private", projected["totals"])
        self.assertNotIn("/private/path", json.dumps(projected))

    def test_agent_usage_projection_allowlists_bounded_project_scopes(self):
        scopes = [{
            "window": "7d",
            "runtime": "codex",
            "project": "~/Documents/github/token-meter",
            "totals": {"agents": 1},
            "private": "/private/trace",
        }]
        scopes.extend({
            "window": "all", "runtime": "", "project": f"project-{index}",
            "totals": {"agents": 0}, "private": f"secret-{index}",
        } for index in range(768))

        projected = agent_usage_projection({"scopes": scopes})
        encoded = json.dumps(projected, sort_keys=True)

        self.assertEqual(len(projected["scopes"]), 768)
        self.assertEqual(projected["scope_count"], 769)
        self.assertTrue(projected["scope_truncated"])
        self.assertEqual(
            projected["scopes"][0]["project"],
            "~/Documents/github/token-meter",
        )
        self.assertNotIn("/private/trace", encoded)
        self.assertNotIn("secret-0", encoded)

    def test_agent_usage_projection_allowlists_and_bounds_inventory(self):
        inventory = [{
            "id": f"agent-{index}",
            "root_session_id": "root-session",
            "project": "~/Documents/github/token-meter",
            "runtime": "codex", "client": "Codex", "kind": "spawned",
            "depth": 1, "label": "Heisenberg",
            "role": "token_meter_reviewer", "model": "gpt-5.6-terra",
            "activity_state": "incomplete", "last_activity_at": 100,
            "tokens": 200, "tokens_available": True,
            "cost": 0.5, "cost_available": True, "work_time_s": 30,
            "elapsed_s": 7200,
            "executions": 1, "attempts": 1, "retries": 3,
            "failed_attempts": 0, "tool_calls": 0,
            "attention": [{
                "code": "retry_pressure", "explanation": "3 retries",
                "private": "secret reason detail",
            }],
            "path": "/private/trace", "prompt": "secret prompt",
        } for index in range(1002)]

        projected = agent_usage_projection({
            "inventory": inventory,
            "inventory_count": 1002,
            "inventory_truncated": False,
        })
        encoded = json.dumps(projected, sort_keys=True)

        self.assertEqual(len(projected["inventory"]), 1000)
        self.assertEqual(projected["inventory_count"], 1002)
        self.assertTrue(projected["inventory_truncated"])
        self.assertEqual(
            projected["inventory"][0]["role"], "token_meter_reviewer",
        )
        self.assertEqual(projected["inventory"][0]["work_time_s"], 30)
        self.assertNotIn("elapsed_s", projected["inventory"][0])
        self.assertEqual(projected["inventory"][0]["attention"], [{
            "code": "retry_pressure", "explanation": "3 retries",
        }])
        for forbidden in (
            "/private/trace", "secret prompt", "secret reason detail", "path",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_agent_usage_projection_allowlists_role_economics(self):
        role = {
            "id": "reviewer::codex::spawned",
            "runtime": "codex", "kind": "spawned", "role": "reviewer",
            "agents": 2, "cost": 3.0, "known_cost": 3.0,
            "cost_available": True, "cost_covered_agents": 2,
            "tokens": 300, "known_tokens": 300,
            "tokens_available": True, "token_covered_agents": 2,
            "median_cost": 1.5, "p95_cost": 2.0,
            "complete_agents": 1, "incomplete_agents": 1,
            "working_agents": 0, "attention_agents": 1,
            "prompt": "secret role prompt", "path": "/private/role",
        }
        projected = agent_usage_projection({
            "scopes": [{
                "window": "7d", "runtime": "codex", "project": "/repo",
                "roles": [role],
                "comparison": {"roles": [{**role, "cost": 4.0,
                    "known_cost": 4.0}], "private": "comparison secret"},
            }],
            "role_days": [{
                **role, "day": "2026-09-24", "project": "/repo",
                "private": "daily secret", "tool_input": "do not project",
            }],
            "role_day_count": 1,
            "role_days_truncated": False,
        })
        encoded = json.dumps(projected, sort_keys=True)

        comparison = projected["scopes"][0]["comparison"]["roles"][0]
        daily = projected["role_days"][0]
        self.assertEqual(comparison["known_cost"], 4.0)
        self.assertEqual(comparison["incomplete_agents"], 1)
        self.assertEqual(daily["day"], "2026-09-24")
        self.assertEqual(daily["project"], "/repo")
        self.assertEqual(daily["attention_agents"], 1)
        self.assertEqual(projected["role_day_count"], 1)
        self.assertFalse(projected["role_days_truncated"])
        for forbidden in (
            "secret role prompt", "/private/role", "comparison secret",
            "daily secret", "do not project", "prompt", "tool_input",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_each_current_runtime_projects_without_private_source_data(self):
        fixture = json.loads((FIXTURES / "current-runtimes.json").read_text())
        for row in fixture["sessions"]:
            with self.subTest(runtime=row["runtime_id"]):
                bundle = projection_bundle(normalized(row), runtime_catalog={})
                encoded = json.dumps(bundle, sort_keys=True)
                self.assertEqual(bundle["session"]["provider"], row["runtime_id"])
                self.assertEqual(
                    bundle["session"]["model_provider"], row["model_provider_id"]
                )
                for sentinel in fixture["forbidden_sentinels"]:
                    self.assertNotIn(sentinel, encoded)

    def test_session_state_model_menubar_and_mcp_match_golden(self):
        row = {
            "runtime_id": "kiro", "client_id": "kiro",
            "session_id": "kiro-fixture", "label": "Kiro",
            "model_provider_id": "anthropic", "model_id": "claude-fixture-model",
            "account_provider_id": None,
            "input": [12, "measured"], "output": [8, "measured"],
            "cache_read": [None, "unavailable"], "cache_write": [0, "measured"],
            "cost": [0.25, "estimated"],
            "active": [3.0, "measured"], "wait": [None, "unavailable"],
            "ttft": [0.0, "measured"],
            "tools": [["read", "filesystem"], ["exec", "shell"]],
            "warnings": [["partial", "Some evidence is unavailable."]],
        }
        catalog = {
            "kiro": {"label": "Kiro", "symbol": "runtime.generic",
                     "color": "runtime-neutral", "capabilities": ["sessions"]},
            "unknown-runtime": {"label": "Unknown Runtime", "symbol": "runtime.generic",
                                "color": "runtime-neutral", "capabilities": ["sessions"]},
        }
        expected = json.loads((FIXTURES / "projection-golden.json").read_text())

        result = projection_bundle(normalized(row), runtime_catalog=catalog)

        self.assertEqual(result, expected)

    def test_unavailable_is_omitted_from_mcp_but_measured_zero_is_preserved(self):
        fixture = json.loads((FIXTURES / "current-runtimes.json").read_text())
        opencode = next(row for row in fixture["sessions"] if row["runtime_id"] == "opencode")

        result = projection_bundle(normalized(opencode), runtime_catalog={})

        self.assertEqual(result["mcp"]["usage"]["input_tokens"], 0)
        self.assertNotIn("cost_usd", result["mcp"]["usage"])
        self.assertTrue(result["mcp"]["availability"]["input_tokens"])
        self.assertFalse(result["mcp"]["availability"]["cost"])


if __name__ == "__main__":
    unittest.main()
