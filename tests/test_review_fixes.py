import unittest
import datetime
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter
from tests.dashboard_page import DASHBOARD_PAGE
from token_meter.runtimes import hermes as hermes_runtime
from tests.meter_support import LocalApiHttpBase, DiscountPricingFixture
from tests.opencode_test_support import OpenCodeCase


class ReviewFixHttpTests(LocalApiHttpBase):
    """Fixes from the independent security and pricing review (HTTP surface)."""

    RULE = {"op": "upsert", "rule": {"price_list": "openai", "channel": "any", "percent": 10}}

    def test_s1_out_of_range_dates_are_rejected_by_the_api_and_resolve(self):
        for start in (1e20, "9999-12-31T23:59:59-23:59"):
            status, payload, _ = self.request("POST", "/api/v1/cost-discounts", {"ops": [
                {"op": "upsert", "rule": {"price_list": "openai", "percent": 5, "start": start}}]})
            self.assertEqual((status, payload["error"]), (400, "Start date must be between 1970 and 2100."))
        self.assertEqual(self.request(
            "GET", "/api/v1/cost-discounts/resolve?price_list=openai&channel=any&at=100000000000000000000")[0], 400)
        self.assertEqual(self.request(
            "GET", "/api/v1/cost-discounts/resolve?price_list=openai&channel=any&at=9999-12-31T23:59:59-23:59")[0], 400)

    def test_s1_hand_edited_huge_date_is_ignored_not_fatal(self):
        self.settings.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": [
            {"id": "d_huge0001", "price_list": "openai", "channel": "any", "percent": 5, "start": 1e20},
            {"id": "d_fine0001", "price_list": "anthropic", "channel": "any", "percent": 5,
             "start": "9999-12-31T23:59:59-23:59"},
        ]}}))
        meter._reset_cost_discount_cache()
        view = meter.cost_discount_settings()
        self.assertEqual((view["rules"], view["invalid_rules"]), ([], 2))
        with mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}):
            self.assertEqual(meter.cross_session(sources=[])["cost_discounts"]["invalid_rules"], 2)
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts")[0], 200)

    def test_s2_invalid_stored_budgets_are_refused_not_reset(self):
        stored = {"budgets": {"allocations": {"claude": -5}, "session_budgets": {"sid": 3}}}
        self.settings.write_text(json.dumps(stored))
        status, payload, _ = self.request("POST", "/api/v1/budgets", {"allocations": {"codex": 5}})
        self.assertEqual((status, payload), (409, {"ok": False,
                         "error": "Stored budgets are invalid; fix them in Token Meter first."}))
        self.assertEqual(json.loads(self.settings.read_text()), stored)

    def test_s3_deeply_nested_json_is_invalid_json(self):
        # Whether the parser recurses out depends on the Python version, so force that path.
        routes = (("/api/v1/cost-discounts", {}), ("/api/v1/budgets", {}),
                  ("/settings/budgets", {"bearer": False, "action": True}))
        real_loads = json.loads

        def loads(text, *args, **kwargs):
            if text in (b"[[1]]", "[[1]]"):
                raise RecursionError
            return real_loads(text, *args, **kwargs)

        with mock.patch.object(meter.json, "loads", side_effect=loads):
            for path, kwargs in routes:
                with self.subTest(path=path, parser="recursion"):
                    status, payload, _ = self.request("POST", path, raw=b"[[1]]", **kwargs)
                    self.assertEqual((status, payload), (400, {"ok": False, "error": "Invalid JSON."}))
        body = b"[" * 4000 + b"]" * 4000
        for path, kwargs in routes:
            with self.subTest(path=path, parser="real"):
                status, payload, _ = self.request("POST", path, raw=body, **kwargs)
                self.assertEqual(status, 400)
                self.assertIn(payload["error"], ("Invalid JSON.", "Send a JSON object with ops.",
                                                 "Send a JSON object.", "Budget settings must be an object."))

    def test_s4_every_route_requires_the_loopback_host(self):
        foreign = "rebind.example:{}".format(self.port)
        with mock.patch.object(meter, "provider_quota_snapshots", return_value={}):
            for path in ("/state", "/health", "/menubar", "/session?id=x", "/"):
                with self.subTest(path=path):
                    status, payload, _ = self.request("GET", path, host=foreign, bearer=False)
                    self.assertEqual((status, payload), (403, {"ok": False, "error": "Forbidden."}))
            self.assertEqual(self.request("GET", "/state", bearer=False)[0], 200)
            self.assertEqual(self.request("GET", "/health", bearer=False,
                                          host="localhost:{}".format(self.port))[0] in (200, 503), True)
        status, _, _ = self.request("POST", "/settings/budgets", {"allocations": {}}, host=foreign,
                                    bearer=False, action=True)
        self.assertEqual(status, 403)
        status, _, _ = self.request("HEAD", "/", host=foreign, bearer=False)
        self.assertEqual(status, 403)

    def test_s5_unknown_top_level_and_op_fields_are_rejected(self):
        cases = (
            ({"expected_revison": "r_x", "ops": [self.RULE]}, "Unknown field: expected_revison"),
            ({"ops": [{**self.RULE, "extra": 1}]}, "Unknown field: extra"),
            ({"ops": [{"op": "upsert", "rule": {"price_list": "openai", "pct": 5}}]}, "Unknown field: pct"),
            ({"ops": [{"op": "delete", "id": "d_aaaaaaaa", "price_list": "openai"}]}, "Unknown field: price_list"),
            ({"ops": [{"op": "replace_scope", "price_list": "openai", "channel": "any", "rules": [],
                       "force": True}]}, "Unknown field: force"),
            ({"ops": [{"op": "set_runtime_channels", "claude_cli": "bedrock"}]}, "Unknown field: claude_cli"),
        )
        for body, message in cases:
            with self.subTest(body=body):
                status, payload, _ = self.request("POST", "/api/v1/cost-discounts", body)
                self.assertEqual((status, payload["error"]), (400, message))
        self.assertFalse(self.settings.exists())

    def test_s7_newer_file_version_is_read_only(self):
        stored = {"cost_discounts": {"version": 2, "rules": [
            {"id": "d_newer001", "price_list": "openai", "channel": "any", "percent": 20}]}}
        self.settings.write_text(json.dumps(stored))
        meter._reset_cost_discount_cache()
        view = meter.cost_discount_settings()
        self.assertTrue(view["read_only"])
        self.assertEqual(len(view["rules"]), 1)
        status, payload, _ = self.request("POST", "/api/v1/cost-discounts", {"ops": [self.RULE]})
        self.assertEqual((status, payload), (409, {"ok": False, "error": "Discounts were saved by a newer Token Meter."}))
        self.assertEqual(json.loads(self.settings.read_text()), stored)


class ReviewFixPricingTests(DiscountPricingFixture):
    """Fixes from the independent pricing review."""

    def test_p1_hermes_mixed_vendor_rows_are_kept_and_discounted_by_their_own_vendor(self):
        from tests.runtimes.test_hermes_adapter import HermesRuntimeAdapterTests
        db_path = HermesRuntimeAdapterTests._create_store(None, self.tmp, estimated_cost=3.0)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("hermes-session", "claude-test", "anthropic", 50, 10, 5, 2, 1, 2.0, None, "estimated", "recorded", 1),
            ("hermes-session", "gpt-5.4", "openai", 50, 10, 5, 3, 1, 1.0, None, "estimated", "recorded", 1),
        ])
        con.commit()
        con.close()
        self.rules(("anthropic", "any", 25, None))
        with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                mock.patch.object(meter, "_hermes_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            summary = meter.hermes_summary(meter.hermes_session_sources()[0])
        self.assertAlmostEqual(summary["cost"], 2.5)
        self.assertAlmostEqual(summary["discount_usd"], 0.5)
        self.assertEqual(len(summary["model_stats"]), 2)

    def test_p2_summary_cache_reprices_when_rules_change_on_disk(self):
        trace = self.tmp / "s.jsonl"
        trace.write_text("\n".join(json.dumps(row) for row in (
            {"type": "turn_context", "timestamp": "2026-09-02T10:00:00.000Z", "payload": {"model": "gpt-5.4"}},
            {"timestamp": "2026-09-02T10:00:04.000Z", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 100_000, "output_tokens": 10_000, "total_tokens": 110_000}}}},
        )) + "\n")
        source = {"id": "s", "path": str(trace), "provider": "codex", "client": "codex", "label": "Codex",
                  "project": "/repo", "mtime": 1.0, "title": "t", "model": "gpt-5.4"}
        with mock.patch.dict(meter._summary_cache, {}, clear=True):
            listed = meter.session_summary(source)["cost"]
            time.sleep(0.3)
            self.rules(("openai", "any", 50, None))
            meter._cost_discount_cache["checked_at"] = 0.0
            repriced = meter.session_summary(source)["cost"]
            state_key = meter.session_state_signature(source)
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(repriced, listed * 0.5)
        self.assertEqual(state_key[-1], meter.pricing_fingerprint())

    def test_p4_cache_savings_without_executions_use_the_session_channel_and_time(self):
        tot = {"cache_read": 1_000_000}
        listed = meter.cache_savings(tot, "codex", "gpt-5.4", at=self.T0)
        self.rules(("openai", "azure", 50, self.T0 - 10))
        self.assertAlmostEqual(meter.cache_savings(tot, "codex", "gpt-5.4", channel="azure", at=self.T0), listed * 0.5)
        self.assertAlmostEqual(meter.cache_savings(tot, "codex", "gpt-5.4", model_provider="azure", at=self.T0),
                               listed * 0.5)
        self.assertAlmostEqual(meter.cache_savings(tot, "codex", "gpt-5.4", channel="azure", at=self.T0 - 20), listed)
        self.assertAlmostEqual(meter.cache_savings(tot, "codex", "gpt-5.4", channel="direct", at=self.T0), listed)

    def test_p6_kiro_session_cost_is_priced_per_turn_across_a_rule_boundary(self):
        from token_meter.runtimes.kiro import KiroRuntimeAdapter, model_ref_for
        adapter = KiroRuntimeAdapter(self.tmp, quote_resolver=meter.price_quote,
                                     compatibility=meter._kiro_compatibility())
        model = model_ref_for("claude-sonnet-4-6")
        turns = [{"input_tokens": 100_000, "output_tokens": 10_000, "end": self.T0 - 100},
                 {"input_tokens": 100_000, "output_tokens": 10_000, "end": self.T0 + 100}]
        listed = adapter._session_cost(model, turns)
        self.rules(("kiro", "any", 50, self.T0))
        self.assertAlmostEqual(adapter._session_cost(model, turns), listed * 0.75)
        source = (Path(meter.__file__).resolve().parent / "token_meter" / "runtimes" / "kiro.py").read_text()
        self.assertIn("cost = self._session_cost(source.model_ref, turns)", source)

    def test_p7_missing_timestamps_never_take_todays_rate(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        self.rules(("openai", "any", 50, 100))
        self.assertEqual(meter.cost_of(usage, "gpt-5.4", "codex", at=None).discount_usd, 0.0)
        self.assertEqual(meter.discount_amount(1.0, "openai", "direct", None), (1.0, 0.0))
        self.assertGreater(meter.cost_of(usage, "gpt-5.4", "codex", at=self.T0).discount_usd, 0)
        self.rules(("openai", "any", 50, None))
        self.assertAlmostEqual(meter.discount_amount(1.0, "openai", "direct", None)[0], 0.5)

    def test_p3_vertex_claude_sessions_take_the_vertex_rule(self):
        self.rules(("anthropic", "direct", 30, None), ("anthropic", "vertex", 10, None))
        row = {"type": "assistant", "timestamp": "2026-09-02T10:00:00.000Z", "requestId": "req_vrtx_011abc",
               "message": {"id": "m", "model": "claude-sonnet-4-6", "content": [], "stop_reason": "end_turn",
                           "usage": {"input_tokens": 100_000, "output_tokens": 10_000,
                                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
        source = {"id": "c", "path": "/tmp/c.jsonl", "provider": "claude", "client": "claude_code",
                  "label": "Claude", "project": "/repo", "mtime": 1, "title": "t", "model": None}
        summary = meter.claude_summary(source, [row])
        self.assertAlmostEqual(summary["discount_usd"] / (summary["cost"] + summary["discount_usd"]), 0.10)


class ReviewRound2HttpTests(LocalApiHttpBase):
    """Round-two review fixes on the HTTP surface."""

    RULE = {"op": "upsert", "rule": {"price_list": "openai", "channel": "any", "percent": 10}}

    def test_r1_state_carries_fresh_discounts_while_repricing_serves_the_old_snapshot(self):
        stale = {"pricing_revision": "p_old", "cost_discounts": {"revision": "r_old", "rules": []}}
        with mock.patch.object(meter, "STATE", {"ok": True, "xsession": stale}), \
                mock.patch.dict(meter._pricing_recalc, {"running": True, "pending": False}):
            written = self.request("POST", "/api/v1/cost-discounts", {"ops": [self.RULE]})[1]
            state = self.request("GET", "/state", bearer=False)[1]
        self.assertEqual(state["xsession"]["cost_discounts"]["revision"], "r_old")
        self.assertEqual(state["cost_discounts"]["revision"], written["revision"])
        self.assertEqual([rule["percent"] for rule in state["cost_discounts"]["rules"]], [10])

    def test_r3_any_version_but_integer_one_is_read_only(self):
        for version in ("2", 2.0, "1", True, None):
            with self.subTest(version=version):
                stored = {"cost_discounts": {"version": version, "rules": []}}
                self.settings.write_text(json.dumps(stored))
                meter._reset_cost_discount_cache()
                self.assertTrue(meter.cost_discount_settings()["read_only"])
                self.assertEqual(self.request("POST", "/api/v1/cost-discounts", {"ops": [self.RULE]})[0], 409)
                self.assertEqual(json.loads(self.settings.read_text()), stored)
        self.settings.write_text(json.dumps({"cost_discounts": {"rules": []}}))
        meter._reset_cost_discount_cache()
        self.assertFalse(meter.cost_discount_settings()["read_only"])

    def test_r3_invalid_stored_rules_survive_a_write_verbatim(self):
        broken = {"id": "broken", "percent": 500, "note": "hand edit"}
        self.settings.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": [
            {"id": "d_keep0001", "price_list": "anthropic", "channel": "any", "percent": 5}, broken]}}))
        meter._reset_cost_discount_cache()
        self.assertEqual(self.request("POST", "/api/v1/cost-discounts", {"ops": [self.RULE]})[0], 200)
        rules = json.loads(self.settings.read_text())["cost_discounts"]["rules"]
        self.assertIn(broken, rules)
        self.assertEqual(len(rules), 3)
        meter._reset_cost_discount_cache()
        view = meter.cost_discount_settings()
        self.assertEqual((len(view["rules"]), view["invalid_rules"]), (2, 1))

    def test_r4_budgets_get_refuses_invalid_stored_budgets(self):
        self.settings.write_text(json.dumps({"budgets": {"allocations": {"claude": -5}}}))
        status, payload, _ = self.request("GET", "/api/v1/budgets")
        self.assertEqual((status, payload), (409, {"ok": False,
                         "error": "Stored budgets are invalid; fix them in Token Meter first."}))


class ReviewRound2PricingTests(DiscountPricingFixture):
    """Round-two review fixes on pricing."""

    def codex_source(self, stamps):
        trace = self.tmp / "codex-{}.jsonl".format(len(list(self.tmp.iterdir())))
        rows = [{"type": "turn_context", "timestamp": stamps[0], "payload": {"model": "gpt-5.4"}}]
        rows += [{"timestamp": stamp, "payload": {"type": "token_count", "info": {"last_token_usage": {
            "input_tokens": 100_000, "output_tokens": 10_000, "total_tokens": 110_000}}}} for stamp in stamps]
        trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return {"id": trace.stem, "session": trace.stem, "path": str(trace), "provider": "codex",
                "client": "codex", "label": "Codex", "project": "/repo", "mtime": 1.0, "title": "t",
                "model": "gpt-5.4"}

    def test_r2_hermes_partial_model_costs_keep_list_equal_cost_plus_discount(self):
        from tests.runtimes.test_hermes_adapter import HermesRuntimeAdapterTests
        db_path = HermesRuntimeAdapterTests._create_store(None, self.tmp, estimated_cost=3.0)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("hermes-session", "claude-test", "anthropic", 50, 10, 5, 2, 1, 2.0, None, "estimated", "recorded", 1),
            ("hermes-session", "gpt-5.4", "openai", 50, 10, 5, 3, 1, None, None, "unknown", "none", 1),
        ])
        con.commit()
        con.close()
        self.rules(("anthropic", "any", 25, None))
        with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                mock.patch.object(meter, "_hermes_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            summary = meter.hermes_summary(meter.hermes_session_sources()[0])
        self.assertAlmostEqual(summary["cost"] + summary["discount_usd"], 3.0)
        self.assertAlmostEqual(summary["discount_usd"], 0.5)
        self.assertAlmostEqual(summary["cost"], 2.5)
        # Two model rows plus the unattributed list-price share (round three, Q4).
        self.assertEqual(len(summary["model_stats"]), 3)

    def test_r7_opencode_prices_discounts_and_dates_a_message_with_one_time(self):
        helper = OpenCodeCase()
        conn = helper._build_db(self.tmp)
        row_created = 1_788_000_000_000
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(
            helper._session_row("ses_t", "/repo", "Timed", "gpt-5.4", 1.0, 100, 10, 0, 0, 0,
                                row_created + 86_400_000 * 3, provider_id="openai").values()))
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", helper._message("m0", "ses_t", {
            "role": "assistant", "modelID": "gpt-5.4", "providerID": "openai", "cost": 1.0,
            "tokens": {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}},
            row_created))
        conn.commit()
        conn.close()
        # Only the row's own creation time dates this message; the rule starts just before it.
        self.rules(("openai", "any", 50, row_created / 1000 - 10))
        source = {"provider": "opencode", "id": "ses_t", "model": "gpt-5.4", "label": "OpenCode",
                  "runtime": "OpenCode", "session": "ses_t", "path": "opencode:ses_t",
                  "project": "/repo", "title": "Timed", "mtime": 1.0}
        with mock.patch.object(meter, "OPENCODE_DB", str(self.tmp / "opencode.db")), \
                mock.patch.object(meter, "_opencode_native_adapters", {}):
            row = meter.opencode_summary(source)
            state = meter.recompute_opencode(source)
        day = time.strftime("%Y-%m-%d", time.localtime(row_created / 1000))
        self.assertAlmostEqual(row["discount_usd"], 0.5)
        self.assertAlmostEqual(row["model_stats"][0]["discount_usd"], 0.5)
        self.assertAlmostEqual(row["_day_cost"][day], 0.5)
        self.assertAlmostEqual(state["total_cost"], 0.5)
        self.assertAlmostEqual(state["executions"][0]["discount_usd"], 0.5)

    def test_r7_kiro_prices_discounts_and_dates_a_start_only_turn_alike(self):
        from token_meter.runtimes.kiro import KiroRuntimeAdapter
        adapter = KiroRuntimeAdapter(self.tmp, quote_resolver=meter.price_quote,
                                     compatibility=meter._kiro_compatibility())
        turn = {"index": 1, "input_tokens": 100_000, "output_tokens": 10_000, "start": self.T0 + 100,
                "end": 0, "tools": (), "model": "claude-sonnet-4-6"}
        self.rules(("kiro", "any", 50, self.T0))
        source = {"id": "k", "path": str(self.tmp / "k.jsonl"), "provider": "kiro", "label": "Kiro",
                  "project": "/repo", "mtime": 1.0, "title": "t", "model": "claude-sonnet-4-6"}
        with mock.patch.object(adapter, "_legacy_rows", return_value=(turn,)):
            row = adapter.summarize_legacy(source)
        day = time.strftime("%Y-%m-%d", time.localtime(self.T0 + 100))
        self.assertGreater(row["discount_usd"], 0)
        self.assertAlmostEqual(row["discount_usd"], row["cost"])
        self.assertAlmostEqual(row["_day_cost"][day], row["cost"])

    def test_r8_whole_second_utc_stamps_parse_and_take_dated_rules(self):
        self.assertEqual(meter.parse_iso("2026-10-29T12:00:00Z"), meter.parse_iso("2026-10-29T12:00:00.000Z"))
        self.assertEqual(meter.parse_iso("2026-10-29T12:00:00+00:00"), 1793275200)
        self.assertIsNone(meter.parse_iso("not a time"))
        for value in (None, 1790000000, 1790000000.5, True, ""):
            with self.subTest(value=value):
                self.assertIsNone(meter.parse_iso(value))
        numeric = self.codex_rows_for("2026-10-29T12:00:00Z")
        numeric[0]["timestamp"] = 1790000000
        meter.codex_summary(self.codex_source(["2026-10-29T12:00:00Z"]), numeric)
        self.rules(("openai", "any", 50, meter.parse_iso("2026-10-01T00:00:00")))
        for stamp in ("2026-10-29T12:00:00Z", "2026-10-29T12:00:00.250Z"):
            with self.subTest(stamp=stamp):
                row = meter.codex_summary(self.codex_source([stamp]), self.codex_rows_for(stamp))
                self.assertAlmostEqual(row["discount_usd"], row["cost"])

    def codex_rows_for(self, stamp):
        return [{"type": "turn_context", "timestamp": stamp, "payload": {"model": "gpt-5.4"}},
                {"timestamp": stamp, "payload": {"type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100_000, "output_tokens": 10_000, "total_tokens": 110_000}}}}]

    def test_r9_build_state_cache_fallback_uses_the_claude_session_channel(self):
        tot = {"input": 0, "cache_write": 0, "cache_read": 1_000_000, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
        listed = meter.cache_block(tot, cost, [], "claude", "claude-sonnet-4-6", at=self.T0)["saved"]
        self.rules(("anthropic", "bedrock", 40, None))
        saved = meter.cache_block(tot, cost, [], "claude", "claude-sonnet-4-6",
                                  channel="bedrock", at=self.T0)["saved"]
        self.assertAlmostEqual(saved, listed * 0.6)
        captured = []
        real_build_state = meter.build_state

        def capture(source, *args, **kwargs):
            captured.append(dict(source))
            return real_build_state(source, *args, **kwargs)
        trace = self.tmp / "claude.jsonl"
        trace.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-09-02T10:00:00.000Z",
            "requestId": "0b7c0a8e-3f7d-4c55-9b1e-6c2f6d1c9a10",
            "message": {"id": "m", "model": "claude-sonnet-4-6", "content": [], "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 1000}}}) + "\n")
        with mock.patch.object(meter, "build_state", side_effect=capture):
            adapter = meter.ClaudeRuntimeAdapter(str(self.tmp), (), compatibility=meter._claude_compatibility())
            adapter.recompute_legacy({"id": "c", "session": "c", "path": str(trace), "provider": "claude",
                                      "client": "claude_code",
                                      "label": "Claude", "project": "/repo", "mtime": 1.0, "title": "t"})
        self.assertEqual(captured[0]["discount_channel"], "bedrock")

    def test_r9_kiro_load_prices_each_turn_at_its_own_time(self):
        from token_meter.contracts import DetailLevel
        from token_meter.runtimes.kiro import KiroRuntimeAdapter
        adapter = KiroRuntimeAdapter(self.tmp, quote_resolver=meter.price_quote,
                                     compatibility=meter._kiro_compatibility())
        path = self.tmp / "messages.jsonl"
        path.write_text("")
        source = adapter._source_from_legacy({"id": "k", "path": str(path), "model": "claude-sonnet-4-6",
                                              "mtime": 1.0, "label": "Kiro", "project": "/repo"})
        turns = tuple({"index": index + 1, "input_tokens": 100_000, "output_tokens": 10_000,
                       "start": stamp - 5, "end": stamp, "tools": ()}
                      for index, stamp in enumerate((self.T0 - 100, self.T0 + 100)))
        parsed = {"turns": turns, "corrupt": 0, "available": True, "truncated": False, "recognized": True}
        with mock.patch.object(adapter, "_parsed", return_value=parsed):
            listed = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value
            self.rules(("kiro", "any", 50, self.T0))
            discounted = adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(discounted, listed * 0.75)

    def test_r9_detail_state_is_repriced_when_rules_change(self):
        source = self.codex_source(["2026-09-02T10:00:00.000Z"])
        with mock.patch.dict(meter._session_state_cache, {}, clear=True):
            listed = meter.cached_session_state(source)["total_cost"]
            time.sleep(0.3)
            self.rules(("openai", "any", 50, None))
            meter._cost_discount_cache["checked_at"] = 0.0
            repriced = meter.cached_session_state(source)["total_cost"]
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(repriced, listed * 0.5)

    def test_r9_budget_status_session_cap_and_spend_goal_use_the_discounted_cost(self):
        from token_meter.services import goals as goal_service
        source = self.codex_source(["2026-09-02T10:00:00.000Z"])
        self.rules(("openai", "any", 50, None))
        row = meter.codex_summary(source, self.codex_rows_for("2026-09-02T10:00:00.000Z"))
        self.assertGreater(row["discount_usd"], 0)
        when = datetime.datetime(2026, 9, 15, 12).astimezone()
        status = meter.monthly_budget_status(meter.monthly_summaries([row]),
                                             {"allocations": {"codex": 100}}, now=when)
        self.assertAlmostEqual(status["spend"], row["cost"])
        state = meter.recompute(source)
        with mock.patch.dict(meter._xsess, {"sessions": []}):
            cap = meter.session_budget_snapshot(source, state, {"default_session_budget": 10})
        self.assertAlmostEqual(cap["spend_usd"], round(row["cost"], 4))
        goal = goal_service.measure("spend", {"kind": "all", "key": ""}, "2026-09-01", "2026-09-30",
                                    [row], meter.metric_available)
        self.assertAlmostEqual(goal["value"], row["cost"])


class ReviewRound3Tests(DiscountPricingFixture):
    """Round-three review fixes."""

    def store(self, rules, **extra):
        self.path.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": rules}, **extra}))
        meter._reset_cost_discount_cache()

    CLASH = [
        {"id": "d_keep0001", "price_list": "anthropic", "channel": "direct", "percent": 30},
        {"id": "d_clash001", "price_list": "anthropic", "channel": "direct", "percent": 20, "start": 100},
    ]

    def saved_rules(self):
        return json.loads(self.path.read_text())["cost_discounts"]["rules"]

    def test_q1_clashing_entry_never_resurrects_after_delete(self):
        self.store(self.CLASH)
        self.assertEqual(meter.cost_discount_settings()["invalid_rules"], 1)
        result = meter.apply_cost_discount_ops([{"op": "delete", "id": "d_keep0001"}])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.saved_rules(), [])
        meter._reset_cost_discount_cache()
        view = meter.cost_discount_settings()
        self.assertEqual((view["rules"], view["invalid_rules"]), ([], 0))
        self.assertEqual(view["revision"], result["revision"])

    def test_q1_replace_scope_clears_the_scope_without_resurrecting(self):
        self.store(self.CLASH)
        result = meter.apply_cost_discount_ops(
            [{"op": "replace_scope", "price_list": "anthropic", "channel": "direct", "rules": []}])
        self.assertEqual(self.saved_rules(), [])
        meter._reset_cost_discount_cache()
        self.assertEqual(meter.cost_discount_settings()["rules"], [])
        self.assertEqual(meter.cost_discount_settings()["revision"], result["revision"])

    def test_q1_write_revision_equals_the_next_read_with_preserved_invalid_entries(self):
        malformed = {"id": "d_bad00001", "price_list": "anthropic", "channel": "any", "percent": 500}
        self.store([self.CLASH[0], malformed, self.CLASH[1]])
        result = meter.apply_cost_discount_ops([{"op": "upsert", "rule": {"price_list": "openai", "percent": 5}}])
        self.assertIn(malformed, self.saved_rules())
        self.assertNotIn(self.CLASH[1], self.saved_rules())
        meter._reset_cost_discount_cache()
        view = meter.cost_discount_settings()
        self.assertEqual(view["revision"], result["revision"])
        self.assertEqual(view["invalid_rules"], 1)

    def test_q1_deleting_or_replacing_an_invalid_entry_clears_the_count(self):
        malformed = {"id": "d_bad00001", "price_list": "anthropic", "channel": "any", "percent": 500}
        self.store([malformed])
        result = meter.apply_cost_discount_ops([{"op": "delete", "id": "d_bad00001"}])
        self.assertTrue(result["changed"])
        self.assertEqual(self.saved_rules(), [])
        meter._reset_cost_discount_cache()
        self.assertEqual(meter.cost_discount_settings()["invalid_rules"], 0)
        self.store([malformed])
        meter.apply_cost_discount_ops([{"op": "replace_scope", "price_list": "anthropic", "channel": "any", "rules": []}])
        self.assertEqual(self.saved_rules(), [])

    def test_q5_non_object_or_non_list_shapes_are_read_only(self):
        for stored in ({"cost_discounts": "30% off"}, {"cost_discounts": {"version": 1, "rules": "x"}},
                       {"cost_discounts": ["rule"]}):
            with self.subTest(stored=stored):
                self.path.write_text(json.dumps(stored))
                meter._reset_cost_discount_cache()
                self.assertTrue(meter.cost_discount_settings()["read_only"])
                result = meter.apply_cost_discount_ops([{"op": "upsert", "rule": {"price_list": "openai", "percent": 5}}])
                self.assertTrue(result.get("newer_version"), result)
                self.assertEqual(json.loads(self.path.read_text()), stored)
                body, status = meter.cost_discount_write({"ops": [{"op": "delete", "id": "d_x0000000"}]}, "api", "x")
                self.assertEqual(status, 409)

    def hermes(self, session_status, rows):
        from tests.runtimes.test_hermes_adapter import HermesRuntimeAdapterTests
        root = self.tmp / "hermes-{}".format(len(list(self.tmp.iterdir())))
        root.mkdir()
        db_path = HermesRuntimeAdapterTests._create_store(None, root, estimated_cost=3.0)
        con = sqlite3.connect(db_path)
        if session_status == "actual":
            con.execute("UPDATE sessions SET actual_cost_usd=3.0, estimated_cost_usd=NULL, cost_status='actual'")
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        self.rules(("anthropic", "any", 25, None))
        with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                mock.patch.object(meter, "_hermes_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            return meter.hermes_summary(meter.hermes_session_sources()[0])

    ROWS = [
        ("hermes-session", "claude-test", "anthropic", 50, 10, 5, 2, 1, 2.0, None, "estimated", "recorded", 1),
        ("hermes-session", "gpt-5.4", "openai", 50, 10, 5, 3, 1, 1.0, None, "estimated", "recorded", 1),
    ]

    def test_q2_billed_hermes_session_is_never_discounted(self):
        for rows in (self.ROWS, [self.ROWS[0], (*self.ROWS[1][:8], None, None, "unknown", "none", 1)]):
            with self.subTest(rows=len([row for row in rows if row[8] is not None])):
                summary = self.hermes("actual", rows)
                self.assertAlmostEqual(summary["cost"], 3.0)
                self.assertEqual(summary["discount_usd"], 0.0)
                self.assertEqual(sum(row["discount_usd"] for row in summary["model_stats"]), 0.0)
                self.assertAlmostEqual(sum(summary["_model_cost"].values()), 3.0)

    def test_q4_unpriced_share_is_a_model_row_and_inconsistent_rows_fall_back(self):
        partial = self.hermes("estimated", [self.ROWS[0], (*self.ROWS[1][:8], None, None, "unknown", "none", 1)])
        self.assertAlmostEqual(partial["cost"], 2.5)
        self.assertAlmostEqual(sum(partial["_model_cost"].values()), partial["cost"])
        self.assertAlmostEqual(sum(row["cost"] for row in partial["model_stats"]), partial["cost"])
        self.assertIn(hermes_runtime.UNATTRIBUTED_MODEL_KEY, partial["_model_cost"])
        over = self.hermes("estimated", [(*self.ROWS[0][:8], 4.0, None, "estimated", "recorded", 1),
                                         (*self.ROWS[1][:8], None, None, "unknown", "none", 1)])
        self.assertAlmostEqual(over["cost"] + over["discount_usd"], 3.0)
        self.assertAlmostEqual(over["cost"], 2.25)
        self.assertAlmostEqual(sum(over["_model_cost"].values()), over["cost"])

    def test_q6_opencode_summary_leaves_a_timeless_message_undated(self):
        helper = OpenCodeCase()
        conn = helper._build_db(self.tmp)
        timed = 1_788_000_000_000
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(
            helper._session_row("ses_u", "/repo", "Undated", "gpt-5.4", 2.0, 200, 20, 0, 0, 0,
                                timed + 86_400_000 * 5, provider_id="openai").values()))
        message = {"role": "assistant", "modelID": "gpt-5.4", "providerID": "openai", "cost": 1.0,
                   "tokens": {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}}
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                     ("m0", "ses_u", timed, timed, json.dumps({**message, "time": {"created": timed}})))
        conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", ("m1", "ses_u", None, None, json.dumps(message)))
        conn.commit()
        conn.close()
        source = {"provider": "opencode", "id": "ses_u", "model": "gpt-5.4", "label": "OpenCode",
                  "runtime": "OpenCode", "session": "ses_u", "path": "opencode:ses_u",
                  "project": "/repo", "title": "Undated", "mtime": 1.0}
        with mock.patch.object(meter, "OPENCODE_DB", str(self.tmp / "opencode.db")), \
                mock.patch.object(meter, "_opencode_native_adapters", {}):
            row = meter.opencode_summary(source)
        self.assertEqual(set(row["_day_cost"]), {time.strftime("%Y-%m-%d", time.localtime(timed / 1000))})

    def test_q6_build_state_passes_the_session_channel_and_last_time_to_the_cache_block(self):
        tot = {"input": 0, "cache_write": 0, "cache_read": 1_000_000, "output": 0}
        cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}

        def saved(channel, last_ts):
            source = {"id": "c", "session": "c", "path": "/tmp/c.jsonl", "provider": "claude",
                      "label": "Claude", "project": "/repo", "discount_channel": channel}
            state = meter.build_state(
                source, tot, cost, 1_000_000, 0.0, [], [], [],
                {"reasoning": 0, "output": 0, "retrieval": 0, "coordination": 0}, {}, [],
                last_ts - 10, last_ts, 0, None, 0, False, "claude-sonnet-4-6", "")
            return state["cache"]["saved"]
        listed = saved("bedrock", self.T0 + 10)
        self.rules(("anthropic", "bedrock", 40, self.T0))
        self.assertAlmostEqual(saved("bedrock", self.T0 + 10), listed * 0.6)
        self.assertAlmostEqual(saved("direct", self.T0 + 10), listed)
        self.assertAlmostEqual(saved("bedrock", self.T0 - 100), listed)


class ReviewRound4Tests(DiscountPricingFixture):
    """Round-four minor fixes."""

    hermes = ReviewRound3Tests.hermes
    ROWS = ReviewRound3Tests.ROWS
    UNPRICED = ("hermes-session", "gpt-5.4", "openai", 50, 10, 5, 3, 1, None, None, "unknown", "none", 1)

    def test_1_list_and_dict_timestamps_never_fail_a_build(self):
        codex = self.tmp / "codex.jsonl"
        codex.write_text("\n".join(json.dumps(row) for row in (
            {"type": "turn_context", "timestamp": ["2026"], "payload": {"model": "gpt-5.4"}},
            {"timestamp": {"at": 1}, "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}}},
            {"timestamp": "2026-09-02T10:00:00Z", "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}}},
        )) + "\n")
        claude = self.tmp / "claude.jsonl"
        claude.write_text("\n".join(json.dumps({
            "type": "assistant", "timestamp": stamp, "message": {
                "id": "m{}".format(index), "model": "claude-sonnet-4-6", "content": [], "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0}}})
            for index, stamp in enumerate((["x"], {"y": 1}, "2026-09-02T10:00:00Z"))) + "\n")
        sources = [
            {"id": "cx", "session": "cx", "path": str(codex), "provider": "codex", "client": "codex",
             "label": "Codex", "project": "/repo", "mtime": 1.0, "title": "t", "model": "gpt-5.4"},
            {"id": "cl", "session": "cl", "path": str(claude), "provider": "claude", "client": "claude_code",
             "label": "Claude", "project": "/repo", "mtime": 1.0, "title": "t"},
        ]
        self.assertIsNone(meter.parse_iso(["2026"]))
        self.assertIsNone(meter.parse_iso({"at": 1}))
        with mock.patch.dict(meter._summary_cache, {}, clear=True), \
                mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}):
            rows = [meter.session_summary(source) for source in sources]
            cross = meter.cross_session(sources=sources)
        self.assertEqual([row["turns"] for row in rows], [2, 3])
        self.assertEqual(cross["total_sessions"], 2)

    def test_2_billed_session_with_an_oversized_estimate_row_falls_back(self):
        summary = self.hermes("actual", [(*self.ROWS[0][:8], 4.0, None, "estimated", "recorded", 1), self.UNPRICED])
        self.assertAlmostEqual(summary["cost"], 3.0)
        self.assertEqual(summary["discount_usd"], 0.0)
        self.assertAlmostEqual(sum(row["cost"] for row in summary["model_stats"]), 3.0)
        aggregate = meter._domain_aggregate_cross_session_rows([summary])
        self.assertAlmostEqual(aggregate["total_cost"], summary["cost"])

    def test_3_leftover_cost_is_a_keyed_row_that_the_models_page_keeps(self):
        for rows in ([self.ROWS[0], self.UNPRICED],
                     [self.ROWS[0], self.UNPRICED,
                      ("hermes-session", "gpt-5.4-mini", "openai", 0, 0, 0, 0, 0, None, None, "unknown", "none", 1)]):
            with self.subTest(unpriced=len(rows) - 1):
                summary = self.hermes("estimated", rows)
                gpt = [row for row in summary["model_stats"] if row["model"].endswith(":gpt-5.4")]
                # The unpriced row stays uncovered (see test_mixed_model_cost_keeps_unknown_model_uncovered).
                self.assertEqual(gpt[0]["cost"], 0.0)
                self.assertFalse(gpt[0]["availability"]["cost"])
                self.assertAlmostEqual(summary["_model_cost"][hermes_runtime.UNATTRIBUTED_MODEL_KEY], 1.0)
                self.assertNotIn("unknown-model", summary["_model_cost"])
                self.assertAlmostEqual(sum(summary["_model_cost"].values()), summary["cost"])
                models = meter.aggregate_model_stats([summary])["models"]
                self.assertAlmostEqual(sum(float(row.get("cost") or 0) for row in models), summary["cost"])
                self.assertTrue(any(row["model"] == hermes_runtime.UNATTRIBUTED_MODEL_KEY for row in models))

    def test_4_one_tolerance_for_reconciliation_and_leftover(self):
        self.assertEqual(hermes_runtime.COST_TOLERANCE, 1e-9)
        from tests.runtimes.test_hermes_adapter import HermesRuntimeAdapterTests
        root = self.tmp / "tiny"
        root.mkdir()
        db_path = HermesRuntimeAdapterTests._create_store(None, root, estimated_cost=2.0000000005)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [self.ROWS[0], self.UNPRICED])
        con.commit()
        con.close()
        with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                mock.patch.object(meter, "_hermes_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            summary = meter.hermes_summary(meter.hermes_session_sources()[0])
        gpt = [row for row in summary["model_stats"] if row["model"].endswith(":gpt-5.4")]
        self.assertEqual(gpt[0]["cost"], 0.0)
        self.assertFalse(gpt[0]["availability"]["cost"])

    def hermes_session(self, estimated_cost, rows):
        from tests.runtimes.test_hermes_adapter import HermesRuntimeAdapterTests
        root = self.tmp / "hermes-{}".format(len(list(self.tmp.iterdir())))
        root.mkdir()
        db_path = HermesRuntimeAdapterTests._create_store(None, root, estimated_cost=estimated_cost)
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, api_call_count INTEGER)")
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        with mock.patch.object(meter, "HERMES_STATE_DB", str(db_path)), \
                mock.patch.object(meter, "_hermes_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            return meter.hermes_summary(meter.hermes_session_sources()[0])

    def test_leftover_row_never_collides_with_a_fully_unknown_usage_row(self):
        unknown = ("hermes-session", None, None, 50, 10, 5, 3, 1, None, None, "unknown", "none", 1)
        summary = self.hermes_session(3.0, [self.ROWS[0], unknown])
        real_key = "unknown-model-provider:unknown-billing-route:unknown-model"
        self.assertNotEqual(hermes_runtime.UNATTRIBUTED_MODEL_KEY, real_key)
        real = [row for row in summary["model_stats"] if row["model"] == real_key]
        self.assertEqual(len(real), 1)
        self.assertEqual(real[0]["cost"], 0.0)
        self.assertFalse(real[0]["availability"]["cost"])
        self.assertNotIn(real_key, summary["_model_cost"])
        self.assertAlmostEqual(summary["_model_cost"][hermes_runtime.UNATTRIBUTED_MODEL_KEY], 1.0)
        models = meter.aggregate_model_stats([summary])["models"]
        self.assertTrue(any(row["model"] == hermes_runtime.UNATTRIBUTED_MODEL_KEY and row["cost"] > 0
                            for row in models))

    def test_leftover_row_threshold_is_the_shared_cost_tolerance(self):
        # A leftover below 1e-9 is rounding, not cost; above it, it is a row. Fails at 1e-12.
        below = self.hermes_session(2.0 + 5e-10, [self.ROWS[0], self.UNPRICED])
        above = self.hermes_session(2.0 + 5e-9, [self.ROWS[0], self.UNPRICED])
        self.assertNotIn(hermes_runtime.UNATTRIBUTED_MODEL_KEY, below["_model_cost"])
        self.assertIn(hermes_runtime.UNATTRIBUTED_MODEL_KEY, above["_model_cost"])

    def test_5_non_object_entries_are_dropped_and_messages_name_the_reason(self):
        malformed = {"id": "d_bad00001", "price_list": "anthropic", "percent": 500}
        self.path.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": ["junk", 5, None, malformed]}}))
        meter._reset_cost_discount_cache()
        self.assertEqual(meter.cost_discount_settings()["invalid_rules"], 4)
        meter.apply_cost_discount_ops([{"op": "upsert", "rule": {"price_list": "openai", "percent": 5}}])
        stored = json.loads(self.path.read_text())["cost_discounts"]["rules"]
        self.assertEqual([entry for entry in stored if entry.get("percent") == 500], [malformed])
        self.assertEqual(len(stored), 2)
        for version, message in (("2", "Discounts in settings.json can't be changed by this version."),
                                 (2, "Discounts were saved by a newer Token Meter.")):
            with self.subTest(version=version):
                self.path.write_text(json.dumps({"cost_discounts": {"version": version, "rules": []}}))
                meter._reset_cost_discount_cache()
                body, status = meter.cost_discount_write({"ops": [{"op": "delete", "id": "d_x0000000"}]}, "api", "x")
                self.assertEqual((status, body["error"]), (409, message))
        self.path.write_text(json.dumps({"cost_discounts": "off"}))
        meter._reset_cost_discount_cache()
        self.assertEqual(meter.cost_discount_settings()["read_only_reason"], "unsupported_format")


class ReviewRound4PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(meter.__file__).resolve().parent
        cls.page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")
        cls.design = (root / "specs" / "2026-10-29-cost-discounts-design.md").read_text(encoding="utf-8")

    def test_5_card_names_the_read_only_reason(self):
        self.assertIn("discountSettings?.read_only_reason==='newer_version'", self.page)
        self.assertIn("\"Discounts in settings.json can't be changed by this version.\"", self.page)

    def test_6_clearing_the_add_row_resumes_live_renders(self):
        self.assertIn("$('discount-form').addEventListener('input',()=>{discountFormDirty=discountAddRowHasInput();});",
                      self.page)
        start = self.page.index("function discountAddRowHasInput(){")
        end = self.page.index("$('discount-form').addEventListener", start)
        script = ("const fields={'d-pct':{value:''},'d-start':{value:''},'d-end':{value:''},'d-note':{value:'  '}};"
                  "const $=id=>fields[id];" + self.page[start:end].replace("const DISCOUNT_ADD_FIELDS", "var DISCOUNT_ADD_FIELDS") +
                  "const empty=discountAddRowHasInput();fields['d-pct'].value='30';const typed=discountAddRowHasInput();"
                  "fields['d-pct'].value='';console.log([empty,typed,discountAddRowHasInput()].join(','))")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "false,true,false")

    def test_7_design_doc_states_what_a_save_keeps(self):
        self.assertIn("a malformed object entry (bad type, enum, date, or percent) is kept verbatim, while "
                      "non-object, duplicate, clashing, and over-limit entries are discarded", self.design)


class ReviewRound3PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(DASHBOARD_PAGE)).read_text(encoding="utf-8")

    def test_q3_first_render_happens_even_when_the_percent_field_has_focus(self):
        self.assertIn("if(xs?.cost_discounts&&!discountEditInProgress())renderDiscounts(xs.cost_discounts);", self.page)
        opener = self.page[self.page.index("function openDiscounts(){"):]
        self.assertLess(opener.index("renderDiscounts(settings)"), opener.index("$('d-pct').focus("))
        self.assertIn("else if(!discountEditInProgress())renderDiscounts(state.cost_discounts);", self.page)
        start = self.page.index("function discountEditInProgress(){")
        logic = self.page[start:self.page.index("}", start) + 1]
        script = ("let discountFormDirty=false,discountSettings={rules:[]};const $=()=>({});"
                  "const viewInteractionActive=()=>true;" + logic +
                  "const first=discountEditInProgress();discountFormDirty=true;const typed=discountEditInProgress();"
                  "discountSettings=null;console.log([first,typed,discountEditInProgress()].join(','))")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "false,true,false")


class ReviewRound2PageAndDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(meter.__file__).resolve().parent
        cls.page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")
        cls.docs = {name: (root / "specs" / name).read_text(encoding="utf-8")
                    for name in ("ARCHITECTURE.md", "LOCAL-API.md", "2026-10-29-cost-discounts-design.md")}

    def test_r1_editor_prefers_fresh_discounts_and_rereads_after_a_conflict(self):
        self.assertIn(" if(state.xsession)state.xsession.cost_discounts=state.cost_discounts;", self.page)
        self.assertEqual(self.page.count("adoptFreshDiscounts(state);LATEST=state;"), 2)
        self.assertIn("if(response.status===409)await reloadDiscounts();", self.page)
        self.assertIn("fetch('/api/v1/cost-discounts',{headers:{'X-Token-Meter-Action':token},cache:'no-store'})",
                      self.page)

    def test_r5_status_region_focus_after_remove_and_read_only_selects(self):
        self.assertIn("<div class=discountCue id=discount-cue role=status hidden></div>", self.page)
        self.assertNotIn("discountCueText role=status", self.page)
        self.assertIn("(next||$('discount-title')).focus({preventScroll:true});", self.page)
        self.assertIn("select.disabled=readOnly;", self.page)

    def test_r6_docs_describe_the_server_wide_host_check_and_vertex_ids(self):
        self.assertIn("This applies to every Token Meter route", self.docs["LOCAL-API.md"])
        self.assertIn("custom `TOKEN_METER_URL`", self.docs["LOCAL-API.md"])
        self.assertIn("Every HTTP route, including the dashboard page", self.docs["ARCHITECTURE.md"])
        self.assertIn("`req_vrtx_…` means `vertex`", self.docs["ARCHITECTURE.md"])
        self.assertNotIn("the file is never rewritten on read |", self.docs["2026-10-29-cost-discounts-design.md"])


class ReviewFixPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(DASHBOARD_PAGE)).read_text(encoding="utf-8")

    def test_s6_live_refresh_keeps_the_notice_and_card_stable_and_accessible(self):
        # Round two moved role=status back to the stable container (see R5).
        self.assertIn("<div class=discountCue id=discount-cue role=status hidden></div>", self.page)
        self.assertIn("if(signature===discountCueSignature)return;", self.page)
        self.assertIn("if(signature!==discountCardSignature){", self.page)
        self.assertIn("restoreDiscountFocus(focus);", self.page)
        self.assertIn('aria-label="${esc(`Remove ${discountRuleName(rule)}`)}"', self.page)
        self.assertIn("return `${(DISCOUNT_LISTS[rule.price_list]||[rule.price_list])[0]} ${rule.percent}% discount`;",
                      self.page)
        self.assertIn('id=discount-invalid hidden', self.page)
        self.assertIn("saved ${invalid===1?'discount is':'discounts are'} invalid and ignored", self.page)

    def test_s6_remove_label_names_the_discount(self):
        start = self.page.index("function discountRuleName(rule){")
        script = ("const DISCOUNT_LISTS={openai:['OpenAI','codex']};"
                  + self.page[start:self.page.index("function renderDiscounts", start)]
                  + "console.log(`Remove ${discountRuleName({price_list:'openai',percent:62})}`)")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "Remove OpenAI 62% discount")


class CostDiscountPageTests(unittest.TestCase):
    """The Spend page carries the approved Discounts notice and card."""

    @classmethod
    def setUpClass(cls):
        cls.page = (Path(DASHBOARD_PAGE)).read_text(encoding="utf-8")
        if "id=spend-discounts" not in cls.page:
            cls.page = (Path(DASHBOARD_PAGE)).read_text(encoding="utf-8")

    def section(self, start, end):
        begin = self.page.index(start)
        return self.page[begin:self.page.index(end, begin)]

    def test_route_and_palette_entry_keep_existing_budget_routes(self):
        self.assertIn("h==='spend-budgets'||h==='spend-discounts'||h==='settings-budgets'||h==='budgets'", self.page)
        self.assertIn("if(h==='spend-discounts')openDiscounts();", self.page)
        self.assertIn("{id:'spend-discounts',label:'Spend · Discounts',", self.page)
        self.assertIn("route:'spend-discounts'", self.page)
        self.assertIn("{id:'spend-budgets',label:'Spend · Monthly budget'", self.page)

    def test_notice_sits_in_the_budget_card_with_the_approved_copy(self):
        lead = self.section('<section class="card budgetLead">', "<div class=budgetLeadBody>")
        self.assertIn("id=discount-cue", lead)
        for copy in ("Spend uses public list prices.",
                     "Pay a negotiated or company rate? Add a discount so spend and budgets match your bill.",
                     ">Add a discount</button>", ">Not now</button>", "Spend includes your discounts",
                     "Costs before discounts are shown beside each total.", ">Manage discounts</button>",
                     "tm_discount_cue_dismissed"):
            self.assertIn(copy, self.page)
        self.assertNotIn("mock", self.section("const DISCOUNT_LISTS=", "$('discount-rotate').onclick").lower())

    def test_card_follows_the_budget_form_without_nesting_a_form(self):
        editor = self.section("<div class=spendBudgetEditor>", "<div class=spendKpis>")
        self.assertLess(editor.index("</form>"), editor.index("id=spend-discounts"))
        card = editor[editor.index("id=spend-discounts"):]
        self.assertNotIn("<form", card)
        self.assertIn('id=discount-form role=group aria-label="Add a discount"', card)
        for element in ("discount-tabs", "discount-rows", "discount-badge", "discount-status", "d-list",
                        "d-channel", "d-pct", "d-start", "d-end", "d-note", "d-add", "d-claude-code",
                        "d-claude-desktop", "discount-rotate"):
            self.assertIn("id={}".format(element), card)
        for copy in ("How Claude apps are billed", "Let other tools set discounts", "/api/v1/cost-discounts",
                     "~/.token-meter/api-token", "Issue a new token", "Detect automatically"):
            self.assertIn(copy, card)

    def test_writes_use_the_dashboard_alias_and_recalculation_polling(self):
        script = self.section("const DISCOUNT_LISTS=", "$('discount-rotate').onclick")
        self.assertIn("fetch('/settings/cost-discounts',{method:'POST'", script)
        self.assertIn("'X-Token-Meter-Action':token", script)
        self.assertIn("Discount saved. Costs are being recalculated.", script)
        self.assertIn("All costs were recalculated.", script)
        self.assertIn("health.pricing_revision===target", script)
        self.assertIn("op:'set_runtime_channels'", script)
        self.assertIn("/api/v1/api-token/rotate", self.page)
        self.assertIn("$('spend-discounts').hidden=!open;", self.page)

    def test_totals_show_list_price_beside_discounted_spend(self):
        self.assertIn("Estimate after discounts · list ${money(total+periodDiscount)}", self.page)
        self.assertIn("After discounts · list ${money(Number(today.cost||0)+Number(today.discount_usd||0))}",
                      self.page)

    def test_card_cannot_widen_the_spend_page_at_laptop_width(self):
        # The shared pricing table's 1030px minimum widened the whole budget editor at 1024px.
        self.assertIn(".discountCard,.spendBudgetEditor{min-width:0}", self.page)
        self.assertIn(".discountCard .modelPriceTable{min-width:760px}", self.page)
        self.assertIn(".discountAdd{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}", self.page)
        # The Add discount button sizes to its label instead of filling a grid column.
        self.assertIn(".discountAdd .tbtn{justify-self:start;align-self:end;padding:6px 12px}", self.page)

    def test_local_dates_are_sent_with_the_browser_offset(self):
        script = self.section("function discountLocalMidnightIso", "function discountSay")
        result = subprocess.run(["node", "-e", script + "console.log(discountLocalMidnightIso('2026-09-01'))"],
                                capture_output=True, text=True, check=True, env={**os.environ, "TZ": "America/Los_Angeles"})
        self.assertEqual(result.stdout.strip(), "2026-09-01T00:00:00-07:00")

    def test_xsession_carries_discount_settings_but_never_the_api_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "api-token"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(Path(tmp) / "settings.json")), \
                    mock.patch.object(meter, "TOKEN_METER_API_TOKEN", str(token_path)), \
                    mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}):
                meter._reset_cost_discount_cache()
                token = meter.ensure_api_token()
                cross = meter.cross_session(sources=[])
            meter._reset_cost_discount_cache()
        self.assertEqual(cross["cost_discounts"]["rules"], [])
        self.assertIn("runtime_channels", cross["cost_discounts"])
        self.assertNotIn(token, json.dumps(cross))


if __name__ == "__main__":
    unittest.main()
