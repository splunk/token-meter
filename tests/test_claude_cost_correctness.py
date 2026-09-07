import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import (
    DeletionDisposition,
    DetailLevel,
    DiscoveryContext,
    EvidenceBasis,
    EvidenceValue,
    ModelRef,
    NormalizedSession,
    PriceQuote,
    SessionSource,
    SourceLocator,
    SourceRevision,
    TimingEvidence,
    UsageEvidence,
)
from token_meter.models.catalog import ANTHROPIC_PRICE
from token_meter.mcp.projections import standardized_trace_projection
from token_meter.mcp.schema import METRICS
from token_meter.projections import projection_bundle
from token_meter.runtimes.claude import ClaudeRuntimeAdapter, _normalized_usage


def claude_usage(
    *, input_tokens=0, output_tokens=0, cache_read=0,
    cache_write_5m=0, cache_write_1h=0, **extra
):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write_5m + cache_write_1h,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_write_5m,
            "ephemeral_1h_input_tokens": cache_write_1h,
        },
        **extra,
    }


def assistant(message_id, usage, timestamp="2026-09-07T00:00:00.000Z"):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "model": "claude-opus-4-8",
            "usage": usage,
            "content": [{"type": "text", "text": "private response"}],
            "stop_reason": "end_turn",
        },
    }


class ClaudeCostCalculationTests(unittest.TestCase):
    def test_five_minute_and_one_hour_cache_writes_use_distinct_rates(self):
        cost = meter.cost_of(
            claude_usage(
                cache_write_5m=1_000_000,
                cache_write_1h=1_000_000,
            ),
            "claude-opus-4-8",
            "claude",
        )

        self.assertEqual(cost["cache_write"], 16.25)
        self.assertEqual(sum(cost.values()), 16.25)

    def test_one_hour_opus_cache_write_golden_cost_is_ten_dollars(self):
        cost = meter.cost_of(
            claude_usage(cache_write_1h=1_000_000),
            "claude-opus-4-8",
            "claude",
        )

        self.assertEqual(cost["cache_write"], 10.0)

    def test_fast_mode_us_geography_and_web_search_are_all_applied(self):
        cost = meter.cost_of(
            claude_usage(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read=1_000_000,
                cache_write_5m=1_000_000,
                cache_write_1h=1_000_000,
                speed="fast",
                service_tier="standard",
                inference_geo="us",
                server_tool_use={
                    "web_search_requests": 2,
                    "web_fetch_requests": 3,
                },
            ),
            "claude-opus-4-8",
            "claude",
        )

        expected = {
            "input": 11.0,
            "cache_write": 35.75,
            "cache_read": 1.1,
            "output": 55.0,
            "server_tools": 0.02,
        }
        for component, value in expected.items():
            self.assertAlmostEqual(cost[component], value)

    def test_model_specific_geography_and_fast_mode_rules_fail_closed(self):
        explicit_global = claude_usage(
            input_tokens=1_000_000,
            inference_geo="global",
        )
        opus_46_fast = claude_usage(input_tokens=1_000_000, speed="fast")
        unsupported_geo = claude_usage(
            input_tokens=1_000_000,
            inference_geo="us",
        )
        unsupported_fast = claude_usage(
            input_tokens=1_000_000,
            speed="fast",
        )

        self.assertTrue(_normalized_usage(explicit_global)["billing_available"])
        self.assertEqual(
            meter.cost_of(
                explicit_global, "claude-opus-4-8", "claude",
            )["input"],
            5.0,
        )
        self.assertEqual(
            meter.cost_of(
                opus_46_fast, "claude-opus-4-6", "claude",
            )["input"],
            5.0,
        )
        self.assertEqual(
            sum(meter.cost_of(
                unsupported_geo, "claude-haiku-4-5", "claude",
            ).values()),
            0.0,
        )
        self.assertEqual(
            sum(meter.cost_of(
                unsupported_fast, "claude-opus-4-7", "claude",
            ).values()),
            0.0,
        )

    def test_unsupported_model_billing_dimension_is_unavailable_in_summary(self):
        source = {
            "provider": "claude", "client": "claude_code",
            "label": "Claude Code", "id": "unsupported-geo",
            "session": "unsupported-geo.jsonl", "path": "/private/not-read",
            "project": "private", "mtime": 1, "title": None, "model": None,
        }
        record = assistant(
            "unsupported-geo",
            claude_usage(input_tokens=1_000_000, inference_geo="us"),
        )
        record["message"]["model"] = "claude-haiku-4-5"

        summary = meter.claude_summary(source, [record])

        self.assertEqual(summary["cost"], 0)
        self.assertFalse(summary["availability"]["cost"])
        self.assertTrue(summary["cost_approx"])

    def test_dashboard_cost_breakdown_displays_server_tool_charge(self):
        dashboard = Path(meter.__file__).with_name("page.html").read_text()

        self.assertIn("cost.server_tools", dashboard)
        self.assertIn("$0.010 / request", dashboard)

    def test_inconsistent_cache_detail_and_unknown_dimensions_are_unpriceable(self):
        inconsistent = claude_usage(cache_write_5m=10, cache_write_1h=5)
        inconsistent["cache_creation_input_tokens"] = 99
        unsupported = claude_usage(speed="turbo")

        self.assertFalse(_normalized_usage(inconsistent)["billing_available"])
        self.assertFalse(_normalized_usage(unsupported)["billing_available"])
        self.assertEqual(sum(meter.cost_of(
            inconsistent, "claude-opus-4-8", "claude",
        ).values()), 0.0)
        self.assertEqual(sum(meter.cost_of(
            unsupported, "claude-opus-4-8", "claude",
        ).values()), 0.0)

    def test_aggregate_only_cache_write_remains_a_labeled_compatibility_estimate(self):
        normalized = _normalized_usage({
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
        })
        cost = meter.cost_of(normalized, "claude-opus-4-8", "claude")

        self.assertTrue(normalized["billing_available"])
        self.assertFalse(normalized["billing_complete"])
        self.assertEqual(normalized["cache_creation_unspecified_input_tokens"], 1_000_000)
        self.assertEqual(cost["cache_write"], 6.25)

    def test_effective_input_rate_drives_one_hour_cache_write(self):
        quote = PriceQuote(
            ModelRef("anthropic", "claude-opus-4-8"),
            4.0, 20.0, 0.4, 5.0, EvidenceBasis.ESTIMATED,
            "claude-opus-4-8",
        )
        with mock.patch.object(
            meter, "_resolved_price_quote", return_value=(quote, False),
        ):
            cost = meter.cost_of(
                claude_usage(cache_write_1h=1_000_000),
                "claude-opus-4-8",
                "claude",
            )

        self.assertEqual(cost["cache_write"], 8.0)

    def test_incomplete_and_invalid_billing_evidence_fail_closed_in_summary(self):
        source = {
            "provider": "claude", "client": "claude_code",
            "label": "Claude Code", "id": "cost-coverage",
            "session": "cost-coverage.jsonl", "path": "/private/not-read",
            "project": "private", "mtime": 1, "title": None, "model": None,
        }
        aggregate_only = assistant("aggregate", {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
        })
        inconsistent_usage = claude_usage(cache_write_5m=10, cache_write_1h=5)
        inconsistent_usage["cache_creation_input_tokens"] = 99

        compatibility = meter.claude_summary(source, [aggregate_only])
        invalid = meter.claude_summary(
            source, [assistant("invalid", inconsistent_usage)],
        )

        self.assertGreater(compatibility["cost"], 0)
        self.assertFalse(compatibility["availability"]["cost"])
        self.assertTrue(compatibility["cost_approx"])
        self.assertEqual(invalid["cost"], 0)
        self.assertFalse(invalid["availability"]["cost"])
        self.assertTrue(invalid["cost_approx"])

    def test_catalog_contains_current_published_claude_rows(self):
        expected = {
            "claude-mythos-5": (10.0, 50.0, 12.5, 1.0),
            "claude-opus-4-7": (5.0, 25.0, 6.25, 0.5),
            "claude-opus-4-6": (5.0, 25.0, 6.25, 0.5),
            "claude-opus-4-5": (5.0, 25.0, 6.25, 0.5),
            "claude-sonnet-4-5": (3.0, 15.0, 3.75, 0.3),
            "claude-haiku-3-5": (0.8, 4.0, 1.0, 0.08),
        }
        for model, rates in expected.items():
            with self.subTest(model=model):
                price = ANTHROPIC_PRICE[model]
                self.assertEqual(
                    (price["input"], price["output"],
                     price["cache_write"], price["cache_read"]),
                    rates,
                )


class ClaudeGroupedDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / "projects"
        self.main = self.projects / "-work" / "parent.jsonl"
        self.main.parent.mkdir(parents=True)
        self.main.write_text(json.dumps(assistant(
            "shared", claude_usage(input_tokens=10, output_tokens=1),
        )) + "\n")
        self.nested = (
            self.main.with_suffix("") / "subagents" / "agent-one.jsonl"
        )
        self.nested.parent.mkdir(parents=True)
        self.nested.write_text("".join((
            json.dumps(assistant(
                "shared", claude_usage(input_tokens=10, output_tokens=1),
            )) + "\n",
            json.dumps(assistant(
                "unique", claude_usage(input_tokens=20, output_tokens=2),
                "2026-09-07T00:00:01.000Z",
            )) + "\n",
        )))
        self.adapter = ClaudeRuntimeAdapter(self.projects)

    def tearDown(self):
        self.temp.cleanup()

    def test_nested_subagent_usage_is_grouped_and_deduplicated(self):
        legacy = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root)),
        )
        native = self.adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )

        self.assertEqual(len(legacy), 1)
        self.assertEqual(set(legacy[0]["_trace_paths"]), {
            str(self.main), str(self.nested),
        })
        self.assertEqual(native[0].locator.kind, "claude-jsonl-group")
        self.assertEqual(
            self.adapter.deletion_plan(native[0]).disposition,
            DeletionDisposition.DENY,
        )
        loaded = self.adapter.load(native[0], DetailLevel.FULL)
        self.assertEqual(loaded.usage.input_tokens.value, 30)
        self.assertEqual(loaded.usage.output_tokens.value, 3)
        self.assertEqual(len(loaded.turns), 2)

    def test_copied_and_symlinked_transcripts_merge_by_logical_message_id(self):
        copied = self.main.parent / "copied.jsonl"
        alias = self.main.parent / "alias.jsonl"
        copied.write_text(self.main.read_text())
        alias.symlink_to(self.main)

        sources = self.adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )

        self.assertEqual(len(sources), 1)
        loaded = self.adapter.load(sources[0], DetailLevel.SUMMARY)
        self.assertEqual(loaded.usage.input_tokens.value, 30)
        self.assertEqual(loaded.usage.output_tokens.value, 3)

    def test_distinct_idless_rows_remain_distinct_across_grouped_traces(self):
        self.main.write_text(json.dumps(assistant(
            None, claude_usage(input_tokens=10, output_tokens=1),
        )) + "\n")
        self.nested.write_text(json.dumps(assistant(
            None,
            claude_usage(input_tokens=20, output_tokens=2),
            "2026-09-07T00:00:01.000Z",
        )) + "\n")

        source = self.adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )[0]
        loaded = self.adapter.load(source, DetailLevel.FULL)

        self.assertEqual(loaded.usage.input_tokens.value, 30)
        self.assertEqual(loaded.usage.output_tokens.value, 3)
        self.assertEqual(len(loaded.turns), 2)

    def test_group_revision_changes_when_nested_trace_changes(self):
        source = self.adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )[0]
        before = self.adapter.current_revision(source)
        self.nested.write_text(self.nested.read_text() + "{}\n")
        os.utime(self.nested, None)

        after = self.adapter.current_revision(source)

        self.assertNotEqual(before, after)

    def test_unsupported_billing_dimension_keeps_measured_duration_evidence(self):
        self.main.write_text(json.dumps(assistant(
            "duration-with-unknown-price",
            claude_usage(cache_write_5m=10, cache_write_1h=20, speed="turbo"),
        )) + "\n")
        self.nested.unlink()
        source = self.adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )[0]

        loaded = self.adapter.load(source, DetailLevel.SUMMARY)

        self.assertEqual(loaded.usage.cache_write_5m_tokens.value, 10)
        self.assertEqual(
            loaded.usage.cache_write_5m_tokens.basis, EvidenceBasis.MEASURED,
        )
        self.assertEqual(loaded.usage.cache_write_1h_tokens.value, 20)
        self.assertEqual(
            loaded.usage.cache_write_1h_tokens.basis, EvidenceBasis.MEASURED,
        )

    def test_local_agent_session_collects_sibling_and_nested_transcripts(self):
        desktop = self.root / "Claude-3p"
        metadata = (
            desktop / "local-agent-mode-sessions" / "account" / "org" /
            "local_desktop.json"
        )
        owned = metadata.with_suffix("") / ".claude" / "projects" / "session"
        main = owned / "cli-main.jsonl"
        sibling = owned / "cli-sibling.jsonl"
        nested = owned / "cli-main" / "subagents" / "agent-two.jsonl"
        nested.parent.mkdir(parents=True)
        main.write_text(json.dumps(assistant(
            "main", claude_usage(input_tokens=1, output_tokens=1),
        )) + "\n")
        sibling.write_text(json.dumps(assistant(
            "sibling", claude_usage(input_tokens=2, output_tokens=2),
        )) + "\n")
        nested.write_text(json.dumps(assistant(
            "nested", claude_usage(input_tokens=3, output_tokens=3),
        )) + "\n")
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps({
            "sessionId": "desktop",
            "cliSessionId": "cli-main",
            "title": "Local agent",
            "lastActivityAt": 1,
        }))
        adapter = ClaudeRuntimeAdapter(self.root / "empty", [desktop])

        source = adapter.discover_legacy(
            DiscoveryContext(home=str(self.root)),
        )[0]
        native = adapter.discover(
            DiscoveryContext(home=str(self.root)),
        )[0]

        self.assertEqual(set(source["_trace_paths"]), {
            str(main), str(sibling), str(nested),
        })
        self.assertEqual(adapter.load(
            native, DetailLevel.SUMMARY,
        ).usage.input_tokens.value, 6)

    def test_duration_counts_flow_through_legacy_aggregates_mcp_and_menubar(self):
        self.main.write_text(json.dumps(assistant(
            "duration",
            claude_usage(
                input_tokens=1,
                output_tokens=2,
                cache_write_5m=10,
                cache_write_1h=20,
            ),
        )) + "\n")
        self.nested.unlink()
        self.adapter.compatibility = meter._claude_compatibility()
        source = self.adapter.discover_legacy(
            DiscoveryContext(home=str(self.root)),
        )[0]

        state = self.adapter.recompute_legacy(source)
        summary = self.adapter.summarize_legacy(source)
        model = meter.aggregate_model_stats([summary])["models"][0]
        daily = meter.daily_summaries([summary])[0]
        monthly = meter.monthly_summaries([summary])[0]
        trace = standardized_trace_projection(
            source, state, ("session", "executions"), None, (),
        )
        with mock.patch.object(
            meter, "cached_session_sources", return_value=([source], True),
        ), mock.patch.object(
            meter, "cached_session_state", return_value=state,
        ), mock.patch.object(meter, "STATE", state), mock.patch.object(
            meter, "_xsess", {"data": {}},
        ):
            menubar = meter.menubar_state(source["id"])

        self.assertEqual(state["tokens"]["cache_write_5m"], 10)
        self.assertEqual(state["tokens"]["cache_write_1h"], 20)
        self.assertEqual(state["total_tokens"], 33)
        self.assertEqual(state["executions"][0]["tokens"]["cache_write_1h"], 20)
        self.assertEqual(summary["model_stats"][0]["cache_write_5m_tokens"], 10)
        self.assertEqual(summary["_model_daily"][0]["cache_write_1h_tokens"], 20)
        self.assertEqual(model["cache_write_5m_tokens"], 10)
        self.assertEqual(model["daily"][0]["cache_write_1h_tokens"], 20)
        self.assertEqual(daily["cache_write_5m_tokens"], 10)
        self.assertEqual(monthly["cache_write_1h_tokens"], 20)
        self.assertEqual(menubar["cache"]["write_5m"], 10)
        self.assertEqual(menubar["cache"]["write_1h"], 20)
        self.assertEqual(trace["executions"][0]["tokens"]["cache_write_1h"], 20)
        self.assertIn("cache_write_5m_tokens", METRICS)
        self.assertIn("cache_write_1h_tokens", METRICS)


class ClaudeDurationProjectionTests(unittest.TestCase):
    def test_duration_counts_are_allowlisted_without_source_locator(self):
        measured = lambda value: EvidenceValue(value, EvidenceBasis.MEASURED)
        session = NormalizedSession(
            source=SessionSource(
                runtime_id="claude",
                client_id="claude_code",
                session_id="safe-id",
                display_label="Claude Code",
                project="/private/project",
                locator=SourceLocator("claude-jsonl-group", "/private/trace"),
                activity_mtime=1,
                revision=SourceRevision(("private-revision",)),
                model_ref=ModelRef("anthropic", "claude-opus-4-8"),
            ),
            started_at=None,
            ended_at=None,
            usage=UsageEvidence(
                input_tokens=measured(0),
                output_tokens=measured(0),
                cache_read_tokens=measured(0),
                cache_write_tokens=measured(15),
                cost_usd=EvidenceValue.unavailable(),
                cache_write_5m_tokens=measured(10),
                cache_write_1h_tokens=measured(5),
                cache_write_unspecified_tokens=measured(0),
            ),
            timing=TimingEvidence.unavailable(),
            tools=(),
            turns=(),
            pricing_basis=None,
            capabilities=frozenset(),
            warnings=(),
            detail=DetailLevel.SUMMARY,
        )

        bundle = projection_bundle(session, {})

        self.assertEqual(bundle["session"]["usage"]["cache_creation_5m_input_tokens"], 10)
        self.assertEqual(bundle["session"]["usage"]["cache_creation_1h_input_tokens"], 5)
        self.assertEqual(bundle["mcp"]["usage"]["cache_write_5m_tokens"], 10)
        self.assertEqual(bundle["mcp"]["usage"]["cache_write_1h_tokens"], 5)
        self.assertNotIn("/private", json.dumps(bundle))


if __name__ == "__main__":
    unittest.main()
