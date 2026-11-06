import unittest
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
import meter
from token_meter.contracts import DiscoveryContext
from tests.meter_support import LocalApiHttpBase, _REAL_SCHEDULE, _REAL_REFRESH
from tests.opencode_test_support import OpenCodeCase


class CostDiscountTests(unittest.TestCase):
    """Contracted discounts apply per request to list-price estimates only."""

    T0 = 1_788_000_000  # 2026-08-29T14:40:00Z

    def setUp(self):
        meter._reset_cost_discount_cache()
        self.addCleanup(meter._reset_cost_discount_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    @staticmethod
    def rule(price_list, channel, percent, start=None, end=None, rule_id=None):
        return {"id": rule_id or "d_" + (price_list + channel + str(percent)).replace(".", "")[:30].lower().ljust(8, "0"),
                "price_list": price_list, "channel": channel, "percent": percent,
                "start": start, "end": end}

    def settings(self, rules=(), runtime_channels=None, **other):
        path = self.tmp / "settings-{}.json".format(len(list(self.tmp.iterdir())))
        payload = dict(other)
        if rules is not None:
            payload["cost_discounts"] = {"version": 1, "rules": list(rules),
                                         **({"runtime_channels": runtime_channels} if runtime_channels else {})}
        path.write_text(json.dumps(payload))
        meter._reset_cost_discount_cache()
        return path

    def patched(self, path):
        return mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path))

    CODEX_USAGE = {"input_tokens": 1_000_000, "output_tokens": 100_000,
                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    # ---- persistence -------------------------------------------------

    def test_absent_key_means_list_price_and_reading_never_seeds(self):
        path = self.settings(rules=None, budgets={"monthly_total": 5})
        before = path.read_text()
        with self.patched(path):
            listed = meter.cost_of(self.CODEX_USAGE, "gpt-5.4", "codex", at=self.T0)
            view = meter.cost_discount_settings()
        self.assertEqual(listed.discount_usd, 0.0)
        self.assertEqual(view["rules"], [])
        self.assertEqual(view["runtime_channels"], {"claude_code": "auto", "claude_desktop": "auto"})
        self.assertEqual(path.read_text(), before)
        self.assertNotIn("cost_discounts", meter.OBSOLETE_SETTINGS_KEYS)

    def test_ops_persist_atomically_and_preserve_other_settings(self):
        path = self.settings(rules=None, budgets={"monthly_total": 5})
        original = meter.atomic_write_text
        with mock.patch.object(meter, "atomic_write_text", side_effect=original) as write:
            result = meter.apply_cost_discount_ops([{"op": "upsert", "rule": {
                "external_id": "sync:a", "price_list": "anthropic", "channel": "direct",
                "percent": 30, "start": "2026-09-01T00:00:00-07:00", "end": None,
                "label": "Team plan"}}], path=str(path), now=self.T0)
        saved = json.loads(path.read_text())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["changed"])
        self.assertEqual(write.call_count, 1)
        self.assertEqual(saved["budgets"], {"monthly_total": 5})
        stored = saved["cost_discounts"]["rules"][0]
        self.assertRegex(stored["id"], r"^d_[a-z0-9]{8,40}$")
        self.assertEqual((stored["start"], stored["set_by"], stored["source"]), (1788246000, "Settings", "dashboard"))
        self.assertEqual(result["revision"], meter.cost_discount_settings(str(path))["revision"])
        self.assertEqual(result["rules"][0]["start_iso"], "2026-09-01T07:00:00Z")

    def test_invalid_stored_rule_is_ignored_without_rewrite(self):
        path = self.settings([self.rule("openai", "any", 50), {"id": "broken", "percent": 500}])
        before, mtime = path.read_text(), path.stat().st_mtime_ns
        with self.patched(path):
            view = meter.cost_discount_settings()
            cost = meter.cost_of(self.CODEX_USAGE, "gpt-5.4", "codex", at=self.T0)
        self.assertEqual(view["invalid_rules"], 1)
        self.assertEqual(len(view["rules"]), 1)
        self.assertAlmostEqual(cost.discount_usd, cost.list_usd * 0.5)
        self.assertEqual((path.read_text(), path.stat().st_mtime_ns), (before, mtime))

    def test_upsert_delete_replace_scope_and_runtime_channels(self):
        path = str(self.settings(rules=None))
        upsert = {"op": "upsert", "rule": {"external_id": "sync:a", "price_list": "anthropic",
                                           "channel": "direct", "percent": 30}}
        first = meter.apply_cost_discount_ops([upsert], path=path, now=1)
        original = meter.atomic_write_text
        with mock.patch.object(meter, "atomic_write_text", side_effect=original) as write:
            again = meter.apply_cost_discount_ops([upsert], source="api", set_by="sync", path=path, now=2)
        self.assertEqual((again["changed"], again["revision"], write.call_count), (False, first["revision"], 0))
        rule_id = first["rules"][0]["id"]
        edited = meter.apply_cost_discount_ops([{"op": "upsert", "rule": {
            "external_id": "sync:a", "percent": 25}}], source="api", set_by="contract-sync", path=path, now=3)
        self.assertEqual((edited["rules"][0]["id"], edited["rules"][0]["percent"]), (rule_id, 25))
        self.assertEqual(edited["rules"][0]["set_by"], "contract-sync")
        scoped = meter.apply_cost_discount_ops([{"op": "replace_scope", "price_list": "openai", "channel": "any",
                                                 "rules": [{"external_id": "sync:o", "percent": 62}]}], path=path)
        self.assertEqual(sorted(r["price_list"] for r in scoped["rules"]), ["anthropic", "openai"])
        cleared = meter.apply_cost_discount_ops([{"op": "replace_scope", "price_list": "openai",
                                                  "channel": "any", "rules": []}], path=path)
        self.assertEqual([r["id"] for r in cleared["rules"]], [rule_id])
        missing = meter.apply_cost_discount_ops([{"op": "delete", "external_id": "nope"}], path=path)
        self.assertEqual((missing["ok"], missing["changed"]), (True, False))
        channels = meter.apply_cost_discount_ops([{"op": "set_runtime_channels", "claude_code": "bedrock"}], path=path)
        self.assertTrue(channels["changed"])
        self.assertEqual(meter.cost_discount_settings(path)["runtime_channels"]["claude_code"], "bedrock")
        deleted = meter.apply_cost_discount_ops([{"op": "delete", "id": rule_id}], path=path)
        self.assertEqual(deleted["rules"], [])

    def test_batches_are_all_or_nothing_and_errors_are_plain(self):
        path = str(self.settings([self.rule("anthropic", "direct", 30, start=100)]))
        before = Path(path).read_text()
        overlap = meter.apply_cost_discount_ops([
            {"op": "upsert", "rule": {"price_list": "openai", "percent": 10}},
            {"op": "upsert", "rule": {"price_list": "anthropic", "channel": "direct", "percent": 20, "start": 50}},
        ], path=path)
        self.assertFalse(overlap["ok"])
        self.assertEqual(overlap["error"], "Anthropic billed by the provider already has 30% for overlapping dates.")
        for ops in ([{"op": "rename"}], [{"op": "delete", "id": "x"}] * 65,
                    [{"op": "upsert", "rule": {"price_list": "openai", "percent": 5, "start": "2026-09-01"}}],
                    [{"op": "upsert", "rule": {"price_list": "cursor", "channel": "direct", "percent": 5}}]):
            self.assertFalse(meter.apply_cost_discount_ops(ops, path=path)["ok"])
        self.assertEqual(Path(path).read_text(), before)

    def test_stale_expected_revision_is_a_conflict(self):
        path = str(self.settings(rules=None))
        stale = meter.cost_discount_settings(path)["revision"]
        meter.apply_cost_discount_ops([{"op": "upsert", "rule": {"price_list": "openai", "percent": 5}}], path=path)
        result = meter.apply_cost_discount_ops(
            [{"op": "upsert", "rule": {"price_list": "openai", "channel": "azure", "percent": 9}}],
            expected_revision=stale, path=path)
        self.assertEqual((result["ok"], result["conflict"]), (False, True))
        self.assertEqual(result["revision"], meter.cost_discount_settings(path)["revision"])

    def test_concurrent_settings_writers_keep_each_others_keys(self):
        path = str(self.settings(rules=None))
        original_load = meter.load_json

        def slow_load(*args, **kwargs):
            value = original_load(*args, **kwargs)
            time.sleep(0.003)
            return value

        errors = []

        def discounts():
            for index in range(15):
                result = meter.apply_cost_discount_ops([{"op": "upsert", "rule": {
                    "external_id": "e{}".format(index), "price_list": "openai", "channel": "any",
                    "percent": 5, "start": index * 10, "end": index * 10 + 10}}], path=path)
                errors.extend([result] if not result["ok"] else [])

        def other_features():
            for index in range(15):
                meter.set_update_settings({"enabled": index % 2 == 0}, path=path)
                meter.set_model_price("openai", "gpt-custom-{}".format(index),
                                      {"input": 1, "output": 2, "cache_write": 0, "cache_read": 0}, path=path)

        with mock.patch.object(meter, "load_json", side_effect=slow_load):
            threads = [threading.Thread(target=discounts), threading.Thread(target=other_features)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        saved = json.loads(Path(path).read_text())
        self.assertEqual(errors, [])
        self.assertEqual(len(saved["cost_discounts"]["rules"]), 15)
        self.assertEqual(len(saved["model_pricing"]["codex"]), 15)
        self.assertIn("updates", saved)

    # ---- application -------------------------------------------------

    def codex_rows(self, stamps):
        rows = [{"type": "turn_context", "timestamp": stamps[0], "payload": {"model": "gpt-5.4"}}]
        for stamp in stamps:
            rows.append({"timestamp": stamp, "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 100_000, "output_tokens": 10_000, "total_tokens": 110_000}}}})
        return rows

    def codex_summary(self, path, stamps):
        source = {"id": "s", "path": "/tmp/s.jsonl", "provider": "codex", "client": "codex",
                  "label": "Codex", "project": "/repo", "mtime": 1, "title": "t", "model": "gpt-5.4"}
        with self.patched(path):
            return meter.codex_summary(source, self.codex_rows(stamps))

    def test_codex_discount_applies_only_inside_its_range(self):
        stamps = ["2026-09-01T10:00:00.000Z", "2026-09-02T10:00:00.000Z", "2026-09-03T10:00:00.000Z"]
        start = meter.parse_iso("2026-09-02T00:00:00")
        end = meter.parse_iso("2026-09-03T00:00:00")
        listed = self.codex_summary(self.settings(rules=None), stamps)
        discounted = self.codex_summary(self.settings([self.rule("openai", "direct", 50, start, end)]), stamps)
        each = listed["cost"] / 3
        self.assertAlmostEqual(discounted["cost"], each * 2.5)
        self.assertAlmostEqual(discounted["discount_usd"], each * 0.5)
        self.assertAlmostEqual(discounted["cost"] + discounted["discount_usd"], listed["cost"])
        self.assertAlmostEqual(discounted["model_stats"][0]["discount_usd"], each * 0.5)
        self.assertAlmostEqual(sum(discounted["_day_discount"].values()), each * 0.5)
        self.assertAlmostEqual(sum(discounted["_day_cost"].values()), discounted["cost"])
        azure = dict(listed)
        self.assertEqual(azure["discount_usd"], 0.0)

    def test_mid_day_boundary_splits_one_days_cost(self):
        stamps = ["2026-09-02T10:00:00.000Z", "2026-09-02T14:00:00.000Z"]
        start = meter.parse_iso("2026-09-02T12:00:00")
        listed = self.codex_summary(self.settings(rules=None), stamps)
        row = self.codex_summary(self.settings([self.rule("openai", "any", 40, start)]), stamps)
        (day, cost), = row["_day_cost"].items()
        self.assertAlmostEqual(cost, listed["_day_cost"][day] * 0.8)
        self.assertAlmostEqual(row["_day_discount"][day], listed["_day_cost"][day] * 0.2)

    def test_codex_on_azure_uses_azure_then_any_rule(self):
        path = self.settings([self.rule("openai", "direct", 50), self.rule("openai", "azure", 20)])
        source = {"id": "s", "path": "/tmp/s.jsonl", "provider": "codex", "client": "codex", "label": "Codex",
                  "project": "/repo", "mtime": 1, "title": "t", "model": "gpt-5.4", "model_provider": "azure"}
        with self.patched(path):
            row = meter.codex_summary(source, self.codex_rows(["2026-09-02T10:00:00.000Z"]))
        self.assertAlmostEqual(row["discount_usd"] / (row["cost"] + row["discount_usd"]), 0.2)

    def test_cursor_hosted_gpt_ignores_openai_rule(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
        with self.patched(self.settings([self.rule("openai", "any", 60)])):
            openai_only = meter.cost_of(usage, "gpt-5.6-sol", "cursor", at=self.T0)
        with self.patched(self.settings([self.rule("cursor", "any", 10)])):
            cursor_rule = meter.cost_of(usage, "gpt-5.6-sol", "cursor", at=self.T0)
        self.assertGreater(openai_only.list_usd, 0)
        self.assertEqual(openai_only.discount_usd, 0.0)
        self.assertAlmostEqual(cursor_rule.discount_usd, cursor_rule.list_usd * 0.1)

    def test_kiro_takes_only_kiro_rules(self):
        from token_meter.runtimes.kiro import KiroRuntimeAdapter
        adapter = KiroRuntimeAdapter(self.tmp, quote_resolver=meter.price_quote,
                                     compatibility=meter._kiro_compatibility())
        turn = {"input_tokens": 100_000, "output_tokens": 10_000, "end": self.T0, "model": "claude-sonnet-4-6"}
        source = {"model": "claude-sonnet-4-6"}
        with self.patched(self.settings([self.rule("anthropic", "any", 30)])):
            _model, anthropic_only, available = adapter._legacy_turn(source, turn)
        with self.patched(self.settings([self.rule("kiro", "any", 20)])):
            _model, kiro_rule, _available = adapter._legacy_turn(source, turn)
        self.assertTrue(available)
        self.assertEqual(anthropic_only.discount_usd, 0.0)
        self.assertAlmostEqual(kiro_rule.discount_usd, kiro_rule.list_usd * 0.2)
        self.assertAlmostEqual(sum(kiro_rule.values()), sum(anthropic_only.values()) * 0.8)

    def pi_summary(self, path, provider, child=None):
        root = self.tmp / "pi-{}".format(len(list(self.tmp.iterdir())))
        directory = root / "sessions" / "--repo--"
        directory.mkdir(parents=True)
        cost = {"input": 0.001, "output": 0.002, "cacheRead": 0.0001, "cacheWrite": 0.0002}
        assistant = {"role": "assistant", "model": "claude-test", "provider": provider,
                     "content": [{"type": "toolCall", "id": "call", "name": "subagent" if child else "read",
                                  "arguments": {"agent": "probe"}}],
                     "usage": {"input": 100, "output": 20, "cacheRead": 10, "cacheWrite": 5, "cost": cost}}
        rows = [
            {"type": "session", "version": 3, "id": "pi", "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"},
            {"type": "model_change", "timestamp": "2026-09-04T10:00:01Z", "provider": provider, "modelId": "claude-test"},
            {"type": "message", "timestamp": "2026-09-04T10:00:02Z", "message": {"role": "user", "content": []}},
            {"type": "message", "timestamp": "2026-09-04T10:00:05Z", "message": assistant},
        ]
        if child:
            rows.append({"type": "message", "timestamp": "2026-09-04T10:00:09Z", "message": {
                "role": "toolResult", "toolCallId": "call", "toolName": "subagent",
                "details": {"mode": "single", "results": [{
                    "agent": "probe", "exitCode": 0, "stopReason": "stop", "model": child,
                    "usage": {"input": 1000, "output": 100, "cacheRead": 0, "cacheWrite": 0,
                              "cost": 0.25, "turns": 2}}]}}})
        (directory / "pi.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with self.patched(path), mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                mock.patch.object(meter, "_pi_native_adapters", {}), \
                mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
            return meter.pi_summary(meter.pi_session_sources()[0])

    def test_pi_on_bedrock_uses_bedrock_then_any_never_direct(self):
        own = 0.0033
        direct_only = self.pi_summary(self.settings([self.rule("anthropic", "direct", 90)]), "amazon-bedrock")
        both = self.pi_summary(self.settings([self.rule("anthropic", "bedrock", 40),
                                              self.rule("anthropic", "any", 10)]), "amazon-bedrock")
        any_only = self.pi_summary(self.settings([self.rule("anthropic", "any", 10)]), "amazon-bedrock")
        self.assertAlmostEqual(direct_only["cost"], own)
        self.assertEqual(direct_only["discount_usd"], 0.0)
        self.assertAlmostEqual(both["cost"], own * 0.6)
        self.assertAlmostEqual(any_only["cost"], own * 0.9)
        self.assertAlmostEqual(any_only["cost"] + any_only["discount_usd"], own)

    def test_pi_subagent_cost_is_discounted_once(self):
        listed = self.pi_summary(self.settings(rules=None), "anthropic", child="anthropic/claude-sonnet-4-6")
        row = self.pi_summary(self.settings([self.rule("anthropic", "any", 50)]), "anthropic",
                              child="anthropic/claude-sonnet-4-6")
        self.assertAlmostEqual(listed["cost"], 0.2533)
        self.assertAlmostEqual(row["cost"], 0.2533 * 0.5)
        self.assertAlmostEqual(row["discount_usd"], 0.2533 * 0.5)
        records = {record["kind"]: record for record in row["_agent_records"]}
        self.assertAlmostEqual(records["root"]["cost"] + records["spawned"]["cost"], row["cost"])
        gateway = self.pi_summary(self.settings([self.rule("anthropic", "any", 50)]), "anthropic",
                                  child="opencode-go/deepseek-v4.1-flash")
        self.assertAlmostEqual(gateway["cost"], 0.0033 * 0.5 + 0.25)

    def test_opencode_vendor_messages_take_vendor_rules_and_gateway_takes_opencode(self):
        helper = OpenCodeCase()
        conn = helper._build_db(self.tmp)
        base = 1_788_000_000_000
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(
            helper._session_row("ses_mix", "/repo", "Mixed", "claude-sonnet-4-6", 1.11,
                                300, 30, 0, 0, 0, base + 10_000, provider_id="anthropic").values()))
        for index, (model, provider, cost) in enumerate((
                ("claude-sonnet-4-6", "anthropic", 1.0), ("gpt-5.4", "openai", 0.1),
                ("kimi-k2", "opencode", 0.01), ("free-model", "openai", 0.0))):
            created = base + index * 1000
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)", helper._message(
                "m{}".format(index), "ses_mix", {
                    "role": "assistant", "modelID": model, "providerID": provider, "cost": cost,
                    "tokens": {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                    "time": {"created": created, "completed": created + 500}}, created))
        conn.commit()
        conn.close()
        path = self.settings([self.rule("anthropic", "direct", 10), self.rule("openai", "any", 50),
                              self.rule("opencode", "any", 20)])
        source = {"provider": "opencode", "id": "ses_mix", "model": "claude-sonnet-4-6", "label": "OpenCode",
                  "runtime": "OpenCode", "session": "ses_mix", "path": "opencode:ses_mix",
                  "project": "/repo", "title": "Mixed", "mtime": 1.0}
        with self.patched(path), mock.patch.object(meter, "OPENCODE_DB", str(self.tmp / "opencode.db")), \
                mock.patch.object(meter, "_opencode_native_adapters", {}):
            row = meter.opencode_summary(source)
        costs = {stats["model"]: stats for stats in row["model_stats"]}
        self.assertAlmostEqual(costs["claude-sonnet-4-6"]["cost"], 0.9)
        self.assertAlmostEqual(costs["gpt-5.4"]["cost"], 0.05)
        self.assertAlmostEqual(costs["kimi-k2"]["cost"], 0.008)
        self.assertEqual(costs["free-model"]["cost"], 0.0)
        self.assertAlmostEqual(row["discount_usd"], 0.1 + 0.05 + 0.002)
        self.assertAlmostEqual(row["cost"] + row["discount_usd"], 1.11)

    def opencode_long_session(self, schema):
        """A 10-message OpenCode session, 0.1 each, in a V1 or V2 store."""
        from tests.runtimes.test_opencode_adapter import V2_SCHEMA
        db = self.tmp / "opencode-{}.db".format(schema)
        con = sqlite3.connect(db)
        base = 1_788_000_000_000
        model = json.dumps({"id": "gpt-5.4", "providerID": "openai"})
        if schema == "v2":
            con.executescript(V2_SCHEMA)
            con.execute(
                "INSERT INTO session_v2 (id, project_id, slug, directory, title, version, cost, "
                "tokens_input, tokens_output, agent, model, time_created, time_updated) "
                "VALUES ('ses','p','ses','/repo','Long','2',1.0,1000,100,'build',?,?,?)",
                (model, base, base + 20_000))
        else:
            OpenCodeCase._build_db(None, self.tmp)
            con.close()
            (self.tmp / "opencode.db").rename(db)
            con = sqlite3.connect(db)
            con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(
                OpenCodeCase._session_row(None, "ses", "/repo", "Long", "gpt-5.4", 1.0, 1000, 100, 0, 0, 0,
                                           base + 20_000, provider_id="openai").values()))
        for index in range(10):
            created = base + index * 1000
            data = {"role": "assistant", "modelID": "gpt-5.4", "providerID": "openai", "cost": 0.1,
                    "tokens": {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                    "time": {"created": created, "completed": created + 500}}
            if schema == "v2":
                data = dict(data, model={"id": "gpt-5.4", "providerID": "openai"})
                con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                            ("m{}".format(index), "ses", "assistant", index, created, created, json.dumps(data)))
            else:
                con.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                            ("m{}".format(index), "ses", created, created, json.dumps(data)))
        con.commit()
        con.close()
        return db

    def test_opencode_session_discount_covers_messages_beyond_the_load_cap(self):
        from token_meter.runtimes import opencode as opencode_runtime
        source = {"provider": "opencode", "id": "ses", "model": "gpt-5.4", "model_provider": "openai",
                  "label": "OpenCode", "runtime": "OpenCode", "session": "ses", "path": "opencode:ses",
                  "project": "/repo", "title": "Long", "mtime": 1.0}
        path = self.settings([self.rule("openai", "any", 50)])
        for schema in ("v1", "v2"):
            with self.subTest(schema=schema):
                db = self.opencode_long_session(schema)
                adapter = opencode_runtime.OpenCodeRuntimeAdapter(
                    db, None, compatibility=meter._opencode_compatibility(), detail_message_limit=3)
                with self.patched(path), mock.patch.object(opencode_runtime, "SUMMARY_MESSAGE_LIMIT", 4):
                    row = adapter.summarize_legacy(dict(source))
                    state = adapter.recompute_legacy(dict(source))
                self.assertEqual(row["turns"], 4)
                self.assertAlmostEqual(row["discount_usd"], 0.5)
                self.assertAlmostEqual(row["cost"], 0.5)
                self.assertAlmostEqual(row["cost"] + row["discount_usd"], 1.0)
                self.assertEqual(len(state["executions"]), 3)
                self.assertAlmostEqual(state["total_cost"], 0.5)
        with self.patched(self.settings(rules=None)):
            listed = opencode_runtime.OpenCodeRuntimeAdapter(
                db, None, compatibility=meter._opencode_compatibility()).summarize_legacy(dict(source))
        self.assertAlmostEqual(listed["cost"], 1.0)
        self.assertEqual(listed["discount_usd"], 0.0)

    def hermes_cost(self, path, status, column):
        db = self.tmp / "hermes-{}.db".format(len(list(self.tmp.iterdir())))
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, billing_provider TEXT,
            started_at REAL, ended_at REAL, last_activity_at REAL, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
            estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT,
            billing_mode TEXT, tool_call_count INTEGER, api_call_count INTEGER, cwd TEXT, title TEXT)""")
        con.execute("INSERT INTO sessions VALUES ('h','claude-test','anthropic',?,?,?,100,20,10,5,3,"
                    "?,?,?,'recorded',NULL,2,1,'/p','t')",
                    (self.T0, self.T0 + 10, self.T0 + 10,
                     2.0 if column == "estimated" else None, 2.0 if column == "actual" else None, status))
        con.commit()
        con.close()
        from token_meter.contracts import DetailLevel, DiscoveryContext
        from token_meter.runtimes.hermes import HermesRuntimeAdapter
        with self.patched(path):
            adapter = HermesRuntimeAdapter(db, compatibility={
                "price_for": meter.price_for, "cost_of": meter.cost_of, "discount_amount": meter.discount_amount})
            source = adapter.discover(DiscoveryContext(home="/home/test"))[0]
            return adapter.load(source, DetailLevel.SUMMARY).usage.cost_usd.value

    def test_hermes_billed_cost_is_untouched_and_estimate_is_discounted(self):
        path = self.settings([self.rule("anthropic", "any", 25)])
        self.assertAlmostEqual(self.hermes_cost(path, "actual", "actual"), 2.0)
        self.assertAlmostEqual(self.hermes_cost(path, "estimated", "estimated"), 1.5)

    def claude_rows(self, request_id):
        row = {"type": "assistant", "timestamp": "2026-09-02T10:00:00.000Z", "message": {
            "id": "msg-1", "model": "claude-sonnet-4-6", "content": [], "stop_reason": "end_turn",
            "usage": {"input_tokens": 100_000, "output_tokens": 10_000,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
        if request_id:
            row["requestId"] = request_id
        return [row]

    def claude_fraction(self, path, request_id, client="claude_code"):
        source = {"id": "c", "path": "/tmp/c.jsonl", "provider": "claude", "client": client, "label": "Claude",
                  "project": "/repo", "mtime": 1, "title": "t", "model": None}
        with self.patched(path):
            row = meter.claude_summary(source, self.claude_rows(request_id))
        self.assertGreater(row["cost"], 0)
        return round(row["discount_usd"] / (row["cost"] + row["discount_usd"]), 6)

    def test_claude_channel_follows_request_id_shape_or_declaration(self):
        uuid = "0b7c0a8e-3f7d-4c55-9b1e-6c2f6d1c9a10"
        rules = [self.rule("anthropic", "direct", 30), self.rule("anthropic", "bedrock", 15)]
        path = self.settings(rules)
        self.assertEqual(self.claude_fraction(path, "req_011abc"), 0.30)
        self.assertEqual(self.claude_fraction(path, uuid), 0.15)
        self.assertEqual(self.claude_fraction(path, None), 0.0)
        any_path = self.settings([*rules, self.rule("anthropic", "any", 5)])
        self.assertEqual(self.claude_fraction(any_path, None), 0.05)
        declared = self.settings(rules, runtime_channels={"claude_code": "bedrock"})
        self.assertEqual(self.claude_fraction(declared, "req_011abc"), 0.15)
        self.assertEqual(self.claude_fraction(declared, "req_011abc", client="claude_desktop"), 0.30)

    def test_cache_savings_are_discounted_by_the_same_rate(self):
        execution = {"tokens": {"cache_read": 1_000_000, "fresh_input": 0, "cache_write": 0, "output": 0},
                     "model": "gpt-5.4", "ts": self.T0, "cost": 0.6, "discount_usd": 0.4}
        with self.patched(self.settings(rules=None)):
            listed = meter.cache_savings({}, "codex", "gpt-5.4", [dict(execution, discount_usd=0.0)])
            plain_now = meter.cache_savings({"cache_read": 1_000_000}, "codex", "gpt-5.4")
        with self.patched(self.settings([self.rule("openai", "any", 40)])):
            discounted = meter.cache_savings({}, "codex", "gpt-5.4", [execution])
            now = meter.cache_savings({"cache_read": 1_000_000}, "codex", "gpt-5.4")
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(discounted, listed * 0.6)
        self.assertAlmostEqual(now, plain_now * 0.6)

    def test_discount_stacks_on_price_override_and_after_modifiers(self):
        path = self.settings(rules=None)
        meter.set_model_price("openai", "gpt-custom-1",
                              {"input": 2, "output": 8, "cache_write": 0, "cache_read": 0.2}, path=str(path),
                              apply_to_all_history=True)
        rules = {"version": 1, "rules": [self.rule("openai", "any", 50), self.rule("anthropic", "any", 25)]}
        data = json.loads(path.read_text())
        data["cost_discounts"] = rules
        path.write_text(json.dumps(data))
        meter._reset_cost_discount_cache()
        with self.patched(path):
            override = meter.cost_of(self.CODEX_USAGE, "gpt-custom-1", "codex", at=self.T0)
            usage = {"input_tokens": 100_000, "output_tokens": 10_000, "speed": "fast", "inference_geo": "us",
                     "server_tool_use": {"web_search_requests": 3}}
            fast = meter.cost_of(usage, "claude-opus-5-5", "claude", at=self.T0)
            plain = meter.cost_of({"input_tokens": 100_000, "output_tokens": 10_000},
                                  "claude-opus-5-5", "claude", at=self.T0)
        self.assertAlmostEqual(sum(override.values()), (2 + 0.8) / 2)
        self.assertGreater(fast.list_usd, plain.list_usd * 2)
        self.assertAlmostEqual(sum(fast.values()), fast.list_usd * 0.75)
        self.assertAlmostEqual(sum(fast.values()) + fast.discount_usd, fast.list_usd)
        self.assertAlmostEqual(fast["server_tools"], 0.03 * 0.75)

    def test_unavailable_and_free_stay_as_they_are(self):
        with self.patched(self.settings([self.rule("openai", "any", 50)])):
            unpriced = meter.cost_of(self.CODEX_USAGE, "no-such-model", "codex", at=self.T0)
            free = meter.cost_of({"input_tokens": 0, "output_tokens": 0}, "gpt-5.4", "codex", at=self.T0)
            self.assertEqual(meter.discount_amount(None, "openai", "direct", self.T0), (None, 0.0))
            self.assertEqual(meter.discount_amount(0.0, "openai", "direct", self.T0), (0.0, 0.0))
            row = self.codex_summary(Path(meter.TOKEN_METER_SETTINGS), ["2026-09-02T10:00:00.000Z"])
        self.assertEqual((sum(unpriced.values()), unpriced.discount_usd), (0.0, 0.0))
        self.assertEqual((sum(free.values()), free.discount_usd), (0.0, 0.0))
        self.assertTrue(row["availability"]["cost"])

    def test_cross_session_aggregates_carry_discount_beside_cost(self):
        rows = [{
            "id": "a", "provider": "codex", "runtime": "Codex", "cost": 6.0, "discount_usd": 4.0, "turns": 1,
            "availability": {"cost": True}, "_model_cost": {"gpt": 6.0}, "_model_discount": {"gpt": 4.0},
            "_model_tok": {"gpt": 10}, "_day_cost": {"2026-09-01": 2.0, "2026-09-02": 4.0},
            "_day_discount": {"2026-09-02": 4.0}, "mtime": 1,
        }, {
            "id": "b", "provider": "claude", "runtime": "Claude", "cost": 1.0, "discount_usd": 0.0, "turns": 1,
            "availability": {"cost": True}, "_model_cost": {"opus": 1.0}, "_model_tok": {"opus": 5},
            "_day_cost": {"2026-09-02": 1.0}, "mtime": 2,
        }]
        aggregate = meter._domain_aggregate_cross_session_rows(rows)
        daily = {row["day"]: row for row in meter.daily_summaries(rows, limit=None)}
        months = meter.monthly_summaries(rows)
        spend = meter.spend_projection(list(daily.values()))
        self.assertEqual((aggregate["total_cost"], aggregate["discount_usd"]), (7.0, 4.0))
        self.assertEqual({item["model"]: item.get("discount_usd") for item in aggregate["model_mix"]},
                         {"gpt": 4.0, "opus": None})
        self.assertEqual(daily["2026-09-02"]["discount_usd"], 4.0)
        self.assertNotIn("discount_usd", daily["2026-09-01"])
        self.assertEqual({p["provider"]: p.get("discount_usd") for p in daily["2026-09-02"]["providers"]},
                         {"codex": 4.0, "claude": None})
        listed = meter._domain_aggregate_cross_session_rows([rows[1]])
        self.assertNotIn("discount_usd", listed)
        self.assertEqual((months[0]["cost"], months[0]["discount_usd"]), (7.0, 4.0))
        self.assertEqual(sum(day.get("discount_usd", 0.0) for day in spend), 4.0)

    def test_pricing_recalculation_is_single_flight_and_repeats_when_stale(self):
        calls = []
        release = threading.Event()

        def runner():
            calls.append(1)
            release.wait(5)
            meter._pricing_state["cache_revision"] = meter.pricing_fingerprint()

        with mock.patch.dict(meter._pricing_recalc, {"running": False, "pending": False}), \
                mock.patch.dict(meter._pricing_state, {"cache_revision": None}):
            self.assertEqual(meter.schedule_pricing_recalculation(runner), "scheduled")
            self.assertEqual(meter.schedule_pricing_recalculation(runner), "scheduled")
            release.set()
            meter._pricing_recalc["thread"].join(5)
            self.assertFalse(meter.pricing_recalculation_running())
        self.assertEqual(len(calls), 2)

    def test_invalidate_cost_caches_drops_cached_dollars(self):
        meter._summary_cache["/x"] = {"signature": 1, "row": {}}
        meter._xsess["data"], meter._xsess["at"] = {"total_cost": 1}, time.time()
        path = self.settings([self.rule("openai", "any", 5)])
        with self.patched(path), mock.patch.dict(meter._pricing_state, {"cache_revision": "p_old"}):
            meter._load_cost_discounts()
            meter._cost_discount_cache["state"] = dict(meter._cost_discount_cache["state"], revision="stale")
            meter.invalidate_cost_caches()
            self.assertNotIn("/x", meter._summary_cache)
            self.assertIsNone(meter._xsess["data"])
            self.assertNotEqual(meter._cost_discount_cache["state"]["revision"], "stale")
            self.assertEqual(meter._pricing_state["cache_revision"], meter.pricing_fingerprint())


class CostDiscountHttpTests(LocalApiHttpBase):
    """The local discount API: Host/Origin gate, tokens, limits, and recalculation."""

    UPSERT = {"ops": [{"op": "upsert", "rule": {"external_id": "sync:o", "price_list": "openai",
                                                "channel": "any", "percent": 62}}]}

    def test_bearer_and_action_tokens_are_accepted_and_others_refused(self):
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts")[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts", bearer=False, action=True)[0], 200)
        for bearer in (False, "x" * 43):
            status, payload, _ = self.request("GET", "/api/v1/cost-discounts", bearer=bearer)
            self.assertEqual((status, payload), (401, {"ok": False, "error": "Unauthorized."}))
        status, payload, _ = self.request("POST", "/api/v1/cost-discounts", self.UPSERT, bearer=False)
        self.assertEqual(status, 401)

    def test_host_and_origin_must_be_loopback(self):
        for host in ("evil.example:{}".format(self.port), "127.0.0.1:1", "127.0.0.1"):
            status, payload, _ = self.request("GET", "/api/v1/cost-discounts", host=host)
            self.assertEqual((status, payload), (403, {"ok": False, "error": "Forbidden."}))
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts",
                                      host="localhost:{}".format(self.port))[0], 200)
        status, _, _ = self.request("POST", "/api/v1/cost-discounts", self.UPSERT, origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertFalse(self.settings.exists())

    def test_limits_and_validation_errors(self):
        status, payload, _ = self.request("POST", "/api/v1/cost-discounts", raw=b"{" + b" " * 9000 + b"}")
        self.assertEqual((status, payload["error"]), (413, "Request too large."))
        self.assertEqual(self.request("POST", "/api/v1/cost-discounts", raw=b"{nope")[0], 400)
        status, payload, _ = self.request("POST", "/api/v1/cost-discounts", {"ops": [{"op": "upsert", "rule": {
            "price_list": "openai", "percent": 100}}]})
        self.assertEqual((status, payload["error"]),
                         (400, "Discount percent must be more than 0 and less than 100."))
        self.scheduled.assert_not_called()

    def test_write_records_client_conflicts_and_idempotent_repeat(self):
        status, payload, _ = self.request("POST", "/api/v1/cost-discounts", self.UPSERT, client="contract-sync")
        self.assertEqual(status, 200, payload)
        self.assertEqual((payload["changed"], payload["recalculation"]), (True, "scheduled"))
        self.assertEqual(payload["rules"][0]["set_by"], "contract-sync")
        self.assertEqual(payload["pricing_revision"], meter.pricing_fingerprint())
        self.assertEqual(self.scheduled.call_count, 1)
        status, again, _ = self.request("POST", "/api/v1/cost-discounts", self.UPSERT, client="<bad>")
        self.assertEqual((status, again["changed"], again["recalculation"]), (200, False, "complete"))
        self.assertEqual(self.scheduled.call_count, 1)
        status, stale, _ = self.request("POST", "/api/v1/cost-discounts", {
            "expected_revision": "r_0000000000000000", **self.UPSERT})
        self.assertEqual((status, stale["revision"]), (409, payload["revision"]))
        status, other, _ = self.request("POST", "/api/v1/cost-discounts", {"ops": [{"op": "upsert", "rule": {
            "price_list": "anthropic", "percent": 5}}]}, client="<bad>")
        self.assertEqual(other["rules"][0]["set_by"], "Another tool")

    def test_resolve_endpoint(self):
        self.request("POST", "/api/v1/cost-discounts", self.UPSERT)
        status, payload, _ = self.request(
            "GET", "/api/v1/cost-discounts/resolve?price_list=openai&channel=azure&at=2026-10-01T12:00:00Z")
        self.assertEqual((status, payload["percent"]), (200, 62))
        self.assertRegex(payload["rule_id"], r"^d_")
        status, none, _ = self.request("GET", "/api/v1/cost-discounts/resolve?price_list=cursor&channel=any&at=1")
        self.assertEqual((status, none["percent"], none["rule_id"]), (200, None, None))
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts/resolve?price_list=google&channel=any")[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts/resolve?price_list=openai&channel=x")[0], 400)
        self.assertEqual(self.request(
            "GET", "/api/v1/cost-discounts/resolve?price_list=openai&channel=any&at=2026-10-01")[0], 400)

    def test_rotate_replaces_token_and_old_token_stops_working(self):
        old = self.token
        status, payload, text = self.request("POST", "/api/v1/api-token/rotate", raw=b"")
        self.assertEqual((status, payload), (200, {"ok": True}))
        new = self.token_path.read_text().strip()
        self.assertNotEqual(new, old)
        self.assertNotIn(new, text)
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts", bearer=old)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/cost-discounts", bearer=new)[0], 200)
        self.assertEqual(self.request("POST", "/api/v1/api-token/rotate", raw=b"", bearer=False)[0], 401)

    def test_token_file_is_private_and_reused_across_restarts(self):
        self.assertEqual(self.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(meter.ensure_api_token(), self.token)
        os.chmod(self.token_path, 0o644)
        self.assertEqual(meter.ensure_api_token(), self.token)
        self.assertEqual(self.token_path.stat().st_mode & 0o777, 0o600)

    def test_token_never_appears_in_dashboard_or_health_payloads(self):
        with mock.patch.dict(meter._xsess, {"data": {"pricing_revision": "p_old"}, "at": time.time()}), \
                mock.patch.object(meter, "provider_quota_snapshots", return_value={}):
            for path in ("/state", "/health", "/menubar", "/api/v1/cost-discounts"):
                with self.subTest(path=path):
                    _status, _payload, text = self.request("GET", path)
                    self.assertNotIn(self.token, text)
        self.assertNotIn(self.token, json.dumps(meter.health_state()[0]))

    def test_dashboard_alias_needs_the_action_token(self):
        status, payload, _ = self.request("POST", "/settings/cost-discounts", self.UPSERT)
        self.assertEqual(status, 403)
        status, payload, _ = self.request("POST", "/settings/cost-discounts", self.UPSERT,
                                          bearer=False, action=True)
        self.assertEqual(status, 200, payload)
        self.assertEqual((payload["rules"][0]["source"], payload["rules"][0]["set_by"]), ("dashboard", "Settings"))

    def test_health_reports_new_pricing_revision_once_recalculation_completes(self):
        self.request("POST", "/api/v1/cost-discounts", self.UPSERT)
        expected = meter.pricing_fingerprint()
        self.assertEqual(meter.health_state()[0]["pricing_revision"], "p_old")
        with mock.patch.object(meter, "schedule_pricing_recalculation", _REAL_SCHEDULE), \
                mock.patch.object(meter, "all_session_sources", return_value=[]), \
                mock.patch.object(meter, "newest_source", return_value=None), \
                mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}), \
                mock.patch.dict(meter._pricing_state, {"cache_revision": None}):
            meter.schedule_pricing_recalculation()
            meter._pricing_recalc["thread"].join(30)
            self.assertFalse(meter.pricing_recalculation_running())
            # Once the rebuilt snapshot is the one served, health reports its revision.
            meter.STATE["xsession"] = meter._xsess["data"]
            self.assertEqual(meter.health_state()[0]["pricing_revision"], expected)

    def test_watcher_reprices_after_a_direct_settings_edit(self):
        self.settings.write_text(json.dumps({"updates": {"enabled": True}}))
        meter._reset_cost_discount_cache()
        with mock.patch.dict(meter._pricing_state, {"cache_revision": meter.pricing_fingerprint()}):
            self.assertFalse(meter.pricing_watch_tick())
            self.settings.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": [{
                "id": "d_handedit1", "price_list": "openai", "channel": "any", "percent": 10}]}}))
            meter._reset_cost_discount_cache()
            self.assertTrue(meter.pricing_watch_tick())
        self.scheduled.assert_called_once_with()


class BudgetApiHttpTests(LocalApiHttpBase):
    """Local tools can read and partially update monthly budgets; session caps stay private."""

    def setUp(self):
        super().setUp()
        self.settings.write_text(json.dumps({"budgets": {
            "allocations": {"claude": 100, "codex": 50},
            "session_budgets": {"private-session-id": 3},
        }, "updates": {"enabled": False}}))
        self.refresh = mock.Mock(return_value={})
        patcher = mock.patch.object(meter, "refresh_cross_session_state", self.refresh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self):
        return json.loads(self.settings.read_text())

    def test_gate_matches_the_discount_api(self):
        self.assertEqual(self.request("GET", "/api/v1/budgets")[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/budgets", bearer=False, action=True)[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/budgets", bearer=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/budgets", bearer="x" * 43)[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/budgets", host="evil.example:{}".format(self.port))[0], 403)
        self.assertEqual(self.request("POST", "/api/v1/budgets", {"thresholds": [50]},
                                      origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("POST", "/api/v1/budgets", raw=b"{" + b" " * 9000 + b"}")[0], 413)
        self.assertEqual(self.request("POST", "/api/v1/budgets", raw=b"{nope")[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/budgets", {"thresholds": [50]},
                                      content_type="text/plain")[0], 415)
        self.assertEqual(self.saved()["budgets"]["allocations"], {"claude": 100, "codex": 50})

    def test_get_shape_never_includes_session_budgets(self):
        status, payload, text = self.request("GET", "/api/v1/budgets")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"ok", "revision", "budgets", "runtimes", "limits", "month"})
        self.assertRegex(payload["revision"], r"^b_[0-9a-f]{16}$")
        self.assertEqual(set(payload["budgets"]), {"currency", "allocations", "monthly_total", "thresholds",
                                                   "native_notifications", "default_session_budget"})
        self.assertEqual(set(payload["budgets"]["allocations"]), set(meter.BUDGET_PROVIDERS))
        self.assertEqual(payload["budgets"]["monthly_total"], 150)
        self.assertEqual(payload["runtimes"], list(meter.BUDGET_PROVIDERS))
        self.assertEqual(payload["limits"], {"max_monthly_budget": meter.MAX_MONTHLY_BUDGET,
                                             "min_session_budget": meter.MIN_SESSION_BUDGET})
        self.assertNotIn("private-session-id", text)
        self.assertNotIn("session_budgets", text)

    def test_month_reports_spend_and_list_spend_from_served_state(self):
        cross = {"budget": {"month": "2026-10", "spend": 70.0, "partial": True},
                 "monthly": [{"month": "2026-10", "cost": 70.0, "discount_usd": 30.0}]}
        with mock.patch.object(meter, "STATE", {"ok": True, "xsession": cross}):
            payload = self.request("GET", "/api/v1/budgets")[1]
        self.assertEqual(payload["month"], {"month": "2026-10", "spent": 70.0, "list_spent": 100.0,
                                            "coverage_partial": True})

    def test_partial_allocation_merge_keeps_other_runtimes_and_session_caps(self):
        status, payload, _ = self.request("POST", "/api/v1/budgets", {"allocations": {"cursor": 25}},
                                          client="budget-sync")
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["budgets"]["allocations"]["claude"], 100)
        self.assertEqual(payload["budgets"]["allocations"]["cursor"], 25)
        self.assertEqual(payload["budgets"]["monthly_total"], 175)
        saved = self.saved()
        self.assertEqual(saved["budgets"]["session_budgets"], {"private-session-id": 3.0})
        self.assertEqual(saved["updates"], {"enabled": False})
        self.refresh.assert_called_once_with()
        self.assertEqual(self.request("GET", "/api/v1/budgets")[1]["revision"], payload["revision"])

    def test_validation_messages(self):
        cases = (
            ({"allocations": {"claude": -1}}, "Claude allocation must be between 0 and 100,000,000."),
            ({"allocations": {"claude": 200_000_000}}, "Claude allocation must be between 0 and 100,000,000."),
            ({"allocations": {"gemini": 5}}, "Unknown runtime: gemini"),
            ({"thresholds": [90, 80]}, "Use 1 to 10 unique budget thresholds in increasing order."),
            ({"thresholds": [0]}, "Budget thresholds must be whole percentages from 1 to 100."),
            ({"session_budgets": {"x": 1}}, "Unknown field: session_budgets"),
            ({"monthly_total": 5}, "Unknown field: monthly_total"),
            ({"native_notifications": "yes"}, "Budget notifications must be on or off."),
        )
        before = self.settings.read_text()
        for body, message in cases:
            with self.subTest(body=body):
                status, payload, _ = self.request("POST", "/api/v1/budgets", body)
                self.assertEqual((status, payload["error"]), (400, message))
        self.assertEqual(self.settings.read_text(), before)
        self.refresh.assert_not_called()

    def test_stale_revision_conflicts_and_repeat_is_a_no_op(self):
        stale = self.request("GET", "/api/v1/budgets")[1]["revision"]
        first = self.request("POST", "/api/v1/budgets", {"thresholds": [50, 100]})[1]
        status, conflict, _ = self.request("POST", "/api/v1/budgets", {
            "expected_revision": stale, "thresholds": [60]})
        self.assertEqual((status, conflict["revision"]), (409, first["revision"]))
        mtime = self.settings.stat().st_mtime_ns
        status, again, _ = self.request("POST", "/api/v1/budgets", {
            "expected_revision": first["revision"], "thresholds": [50, 100]})
        self.assertEqual((status, again["changed"], again["revision"]), (200, False, first["revision"]))
        self.assertEqual(self.settings.stat().st_mtime_ns, mtime)
        self.assertEqual(self.refresh.call_count, 1)

    def test_budget_status_after_post_reflects_the_new_total(self):
        with mock.patch.object(meter, "refresh_cross_session_state", _REAL_REFRESH), \
                mock.patch.object(meter, "all_session_sources", return_value=[]), \
                mock.patch.dict(meter._xsess, {"data": None, "at": 0.0}):
            self.request("POST", "/api/v1/budgets", {"allocations": {"claude": 400}})
            budget = meter._xsess["data"]["budget"]
            self.assertEqual(budget["budget"], 450)
            self.assertTrue(budget["configured"])
            month = self.request("GET", "/api/v1/budgets")[1]["month"]
        self.assertEqual(month["spent"], 0.0)


if __name__ == "__main__":
    unittest.main()
