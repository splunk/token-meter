import calendar
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter


def source(provider, model=None):
    return {
        "id": "session", "path": "/tmp/session.jsonl", "provider": provider,
        "client": provider, "label": provider.title(), "project": "/repo",
        "mtime": 1, "title": "Cache countdown", "model": model,
    }


def claude_record(ts, message_id, usage, model="claude-sonnet-4-6"):
    return {
        "type": "assistant", "timestamp": ts,
        "message": {"id": message_id, "model": model, "content": [],
                    "usage": usage, "stop_reason": "end_turn"},
    }


class SummaryCacheWindowTests(unittest.TestCase):
    def test_claude_summary_records_ttl_and_anchors_on_the_latest_request(self):
        objs = [
            claude_record("2026-07-02T00:00:00.000Z", "m1", {
                "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 4000},
            }),
            claude_record("2026-07-02T00:03:00.000Z", "m2", {
                "input_tokens": 20, "output_tokens": 5,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            }),
        ]
        row = meter.claude_summary(source("claude"), objs)
        window = row["_cache_window"]
        self.assertEqual(window["basis"], "recorded")
        self.assertEqual(window["window_s"], 3600)
        self.assertEqual(window["anchor_ts"], calendar.timegm((2026, 7, 2, 0, 3, 0)))
        self.assertEqual(window["prefix_tokens"], 4020)
        self.assertGreater(window["cold_cost_delta"], 0)
        self.assertNotIn("_cache_window", {k for k in row if not k.startswith("_")})

    def test_codex_summary_uses_the_documented_openai_window(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 5000, "cached_input_tokens": 4000,
                    "output_tokens": 40, "total_tokens": 5040,
                }},
            }},
        ]
        window = meter.codex_summary(source("codex", "gpt-5.6"), objs)["_cache_window"]
        self.assertEqual(window["basis"], "documented")
        self.assertEqual(window["source"], "openai")
        self.assertEqual(window["window_s"], 1800)
        self.assertIsNone(window["max_window_s"])
        self.assertEqual(window["prefix_tokens"], 5000)

    def test_session_without_requests_has_no_window(self):
        self.assertIsNone(meter.claude_summary(source("claude"), [])["_cache_window"])

    def test_claude_anchor_is_the_request_start_before_the_reply(self):
        objs = [
            {"type": "user", "timestamp": "2026-07-02T00:00:00.000Z",
             "message": {"role": "user", "content": "go"}},
            claude_record("2026-07-02T00:00:40.000Z", "m1", {
                "input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0,
                "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 0},
            }),
        ]
        window = meter.claude_summary(source("claude"), objs)["_cache_window"]
        self.assertEqual(window["anchor_ts"], calendar.timegm((2026, 7, 2, 0, 0, 0)))

    def test_claude_subagent_requests_never_drive_the_main_window(self):
        from token_meter.runtimes import claude as claude_runtime
        self.assertTrue(claude_runtime._is_subagent_owner("/p/session/subagents/agent-1.jsonl"))
        self.assertFalse(claude_runtime._is_subagent_owner("/p/session.jsonl"))
        self.assertFalse(claude_runtime._is_subagent_owner(""))


class ColdCostDeltaTests(unittest.TestCase):
    def test_claude_delta_is_write_minus_read_at_the_engine_rates(self):
        facts = {"model": "claude-sonnet-4-6", "pricing_provider": "claude",
                 "prefix_tokens": 1_000_000, "writes_observed": True, "anchor_ts": None}
        price, missing = meter.price_for("claude-sonnet-4-6", "claude")
        self.assertFalse(missing)
        five = meter.cache_cold_cost_delta(facts, 300)
        hour = meter.cache_cold_cost_delta(facts, 3600)
        self.assertAlmostEqual(five, price["cache_write"] - price["cache_read"], places=6)
        self.assertAlmostEqual(hour, price["input"] * 2 - price["cache_read"], places=6)

    def test_openai_delta_without_writes_is_input_minus_cached_input(self):
        facts = {"model": "gpt-5.5", "pricing_provider": "codex",
                 "prefix_tokens": 1_000_000, "writes_observed": False, "anchor_ts": None}
        price, missing = meter.price_for("gpt-5.5", "codex")
        self.assertFalse(missing)
        self.assertAlmostEqual(
            meter.cache_cold_cost_delta(facts, 300),
            price["input"] - price["cache_read"], places=6,
        )

    def test_claude_fast_and_us_routing_premiums_carry_into_the_cold_cost(self):
        base = {"model": "claude-opus-5-5", "pricing_provider": "claude",
                "prefix_tokens": 1_000_000, "writes_observed": True, "anchor_ts": None}
        standard = meter.cache_cold_cost_delta(base, 300)
        self.assertGreater(standard, 0)
        self.assertAlmostEqual(
            meter.cache_cold_cost_delta({**base, "speed": "fast"}, 300), standard * 2, places=6)
        self.assertAlmostEqual(
            meter.cache_cold_cost_delta({**base, "inference_geo": "us"}, 300), standard * 1.1, places=6)

    def test_modifiers_the_engine_cannot_price_give_no_cold_cost(self):
        base = {"model": "claude-opus-4-6", "pricing_provider": "claude",
                "prefix_tokens": 100_000, "writes_observed": True, "anchor_ts": None}
        self.assertIsNotNone(meter.cache_cold_cost_delta(base, 300))
        for modifier in ({"service_tier": "priority"}, {"speed": "turbo"},
                         {"inference_geo": "eu"}, {"speed": "unrecognized"}):
            with self.subTest(modifier=modifier):
                self.assertIsNone(meter.cache_cold_cost_delta({**base, **modifier}, 300))

    def test_codex_priority_tier_prices_at_the_fast_rate(self):
        # 100k tokens stays under GPT's long-context threshold, so only the tier changes the rate.
        base = {"model": "gpt-6.1-sol", "pricing_provider": "codex",
                "prefix_tokens": 100_000, "writes_observed": False, "anchor_ts": None}
        fast_price, missing = meter.price_for("gpt-6.1-sol", "codex", "fast")
        self.assertFalse(missing)
        self.assertAlmostEqual(
            meter.cache_cold_cost_delta({**base, "pricing_variant": "fast"}, 1800),
            (fast_price["input"] - fast_price["cache_read"]) / 10, places=6,
        )
        self.assertNotAlmostEqual(
            meter.cache_cold_cost_delta(base, 1800),
            meter.cache_cold_cost_delta({**base, "pricing_variant": "fast"}, 1800), places=6,
        )
        # A tier the engine cannot price is unavailable, never a guessed standard-rate figure.
        self.assertIsNone(meter.cache_cold_cost_delta({**base, "pricing_variant": "unsupported"}, 1800))

    def test_codex_summary_records_the_service_tier_of_the_latest_request(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z",
             "payload": {"model": "gpt-6.1-sol"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {
                "type": "token_count", "service_tier": "priority", "info": {"last_token_usage": {
                    "input_tokens": 5000, "cached_input_tokens": 4000,
                    "output_tokens": 40, "total_tokens": 5040,
                }},
            }},
        ]
        priority = meter.codex_summary(source("codex", "gpt-6.1-sol"), objs)["_cache_window"]
        objs[1]["payload"].pop("service_tier")
        standard = meter.codex_summary(source("codex", "gpt-6.1-sol"), objs)["_cache_window"]
        self.assertGreater(priority["cold_cost_delta"], standard["cold_cost_delta"])

    def test_unknown_price_or_prefix_is_none_not_zero(self):
        base = {"pricing_provider": "codex", "writes_observed": False, "anchor_ts": None}
        self.assertIsNone(meter.cache_cold_cost_delta(
            {**base, "model": "not-a-real-model-xyz", "prefix_tokens": 1000}, 300))
        self.assertIsNone(meter.cache_cold_cost_delta(
            {**base, "model": "gpt-5.5", "prefix_tokens": 0}, 300))
        self.assertIsNone(meter.cache_cold_cost_delta(
            {**base, "model": "gemini-3-pro", "pricing_provider": None, "prefix_tokens": 1000}, 300))


class CurrentSessionAndMenubarTests(unittest.TestCase):
    def row(self, now, window):
        return {"id": "s1", "provider": "claude", "label": "Claude", "mtime": now - 5,
                "last_activity_ts": now - 5, "_cache_window": window}

    def test_current_session_cards_project_the_window(self):
        now = 1_800_000_000.0
        window = {"basis": "recorded", "source": "anthropic", "anchor_ts": now - 30,
                  "window_s": 300, "max_window_s": 300, "prefix_tokens": 100,
                  "cold_cost_delta": 0.5, "cache_reported": True, "cache_observed": True,
                  "unexpected": "dropped"}
        cards = meter.current_session_summaries([self.row(now, window)], now=now)
        self.assertEqual(cards[0]["cache_window"]["window_s"], 300)
        self.assertNotIn("unexpected", cards[0]["cache_window"])
        cards = meter.current_session_summaries([self.row(now, None)], now=now)
        self.assertIsNone(cards[0]["cache_window"])

    def test_menubar_window_matches_the_selected_session_only(self):
        current = [{"id": "a", "cache_window": {"basis": "documented", "anchor_ts": 10, "window_s": 1800}}]
        self.assertEqual(meter.menubar_cache_window(current, "a")["window_s"], 1800)
        self.assertIsNone(meter.menubar_cache_window(current, "b"))
        self.assertIsNone(meter.menubar_cache_window(current, ""))
        self.assertIsNone(meter.menubar_cache_window(None, "a"))
        self.assertEqual(meter.menubar_cache_window(current, "child", root_id="a")["window_s"], 1800)

    def test_folded_family_keeps_the_parent_window_over_a_newer_child(self):
        from token_meter.domain.aggregates import fold_child_session_rows
        parent = {"id": "p", "provider": "opencode", "mtime": 10,
                  "_cache_window": {"basis": "documented", "anchor_ts": 100, "window_s": 300}}
        child = {"id": "c", "provider": "opencode", "mtime": 20, "is_child_session": True,
                 "root_session_id": "p", "agent_root_id": "p",
                 "_cache_window": {"basis": "documented", "anchor_ts": 200, "window_s": 300}}
        folded = fold_child_session_rows([parent, child])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["_cache_window"]["anchor_ts"], 100)
        # A parent with no window never borrows a child's timer.
        folded = fold_child_session_rows([dict(parent, _cache_window=None), child])
        self.assertIsNone(folded[0]["_cache_window"])

    def test_claude_inline_sidechain_rows_never_drive_the_main_window(self):
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 4000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 0}}
        main = claude_record("2026-07-02T00:00:10.000Z", "m1", usage)
        side = dict(claude_record("2026-07-02T00:02:00.000Z", "s1", usage), isSidechain=True)
        side_user = {"type": "user", "isSidechain": True, "timestamp": "2026-07-02T00:01:50.000Z",
                     "message": {"role": "user", "content": "sub"}}
        window = meter.claude_summary(source("claude"), [main, side_user, side])["_cache_window"]
        self.assertEqual(window["anchor_ts"], calendar.timegm((2026, 7, 2, 0, 0, 10)))


class MenubarSettingsTests(unittest.TestCase):
    def test_default_is_always_and_invalid_stored_values_migrate_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(meter.menubar_settings(str(path)), {"cache_timer": "always"})
            path.write_text(json.dumps({"menubar": {"cache_timer": "loud"}, "other": 1}))
            self.assertEqual(meter.menubar_settings(str(path)), {"cache_timer": "always"})
            path.write_text(json.dumps({"menubar": "nonsense"}))
            self.assertEqual(meter.menubar_settings(str(path)), {"cache_timer": "always"})

    def test_writes_validate_preserve_other_settings_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"updates": {"enabled": True, "auto_install": False}}))
            first = meter.set_menubar_settings({"cache_timer": "final2"}, str(path))
            self.assertEqual(first, {"ok": True, "changed": True, "menubar": {"cache_timer": "final2"}})
            stat = path.stat()
            second = meter.set_menubar_settings({"cache_timer": "final2"}, str(path))
            self.assertFalse(second["changed"])
            self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
            stored = json.loads(path.read_text())
            self.assertEqual(stored["updates"], {"enabled": True, "auto_install": False})
            self.assertEqual(meter.menubar_settings(str(path)), {"cache_timer": "final2"})
            for bad in ({"cache_timer": "sometimes"}, {"cache_timer": 1}, ["off"], "off"):
                with self.subTest(bad=bad):
                    self.assertFalse(meter.set_menubar_settings(bad, str(path))["ok"])
            self.assertEqual(meter.menubar_settings(str(path)), {"cache_timer": "final2"})

    def test_writes_hold_the_shared_settings_lock_and_hide_os_error_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            real_lock = meter._budget_settings_lock
            held = []

            def spy(lock_path):
                held.append(lock_path)
                return real_lock(lock_path)

            with mock.patch.object(meter, "_budget_settings_lock", spy):
                self.assertTrue(meter.set_menubar_settings({"cache_timer": "off"}, path)["ok"])
            self.assertEqual(held, [path])
            with mock.patch.object(meter, "write_settings_json",
                                   side_effect=OSError(f"denied: {path}")):
                result = meter.set_menubar_settings({"cache_timer": "always"}, path)
            self.assertEqual(result, {"ok": False, "error": "Token Meter could not save settings."})

    def test_dashboard_state_carries_the_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            meter.set_menubar_settings({"cache_timer": "off"}, path)
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", path):
                payload = meter.dashboard_state_payload({"ok": True})
            self.assertEqual(payload["menubar_settings"], {"cache_timer": "off"})

    def test_post_route_is_allowlisted_and_action_token_guarded(self):
        text = (Path(__file__).resolve().parents[1] / "token_meter" / "app.py").read_text()
        post = text[text.index("    def do_POST(self):"):]
        allowlist = post[:post.index("self.send_error(404)")]
        self.assertIn('"/settings/menubar"', allowlist)
        guard = post.index("X-Token-Meter-Action")
        self.assertLess(guard, post.index('if req_path == "/settings/menubar":'))


ROOT = Path(__file__).resolve().parents[1]


class SurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / "page.html").read_text()
        cls.swift = (ROOT / "menubar" / "TokenMeterMenuBar.swift").read_text()

    def test_session_card_renders_the_ring_before_status_and_ticks_locally(self):
        self.assertIn("${cacheRingHtml(row)}<div class=\"currentSessionStatus", self.page)
        self.assertIn("setInterval(tickCacheRings,1000);", self.page)
        self.assertIn("CACHE_RING_TICKS=30", self.page)
        self.assertIn("@media(prefers-reduced-motion:reduce){.cacheRing.bad{animation:none}", self.page)

    def test_dashboard_settings_offer_the_three_modes_through_the_action_endpoint(self):
        for mode in ("always", "final2", "off"):
            self.assertIn(f"data-cache-timer={mode}", self.page)
        self.assertIn("fetch('/settings/menubar'", self.page)
        self.assertIn("renderMenubarSettings(state?.menubar_settings)", self.page)

    def test_native_companion_reads_payload_and_writes_the_shared_setting(self):
        self.assertIn('CacheWindow.fromJSON(dict["cache_window"])', self.swift)
        self.assertIn('dict["cache_timer"]', self.swift)
        self.assertIn('NSMenuItem(title: "Cache timer in menu bar"', self.swift)
        self.assertIn('"http://127.0.0.1:8722/settings/menubar"', self.swift)
        self.assertIn("RunLoop.main.add(cacheTick, forMode: .common)", self.swift)
        self.assertIn("addHeader()\n        addCacheRow()", self.swift)


class DiscountedColdCostTests(unittest.TestCase):
    """The cold-cache estimate is discounted exactly like the session's own requests."""

    UUID = "0b7c0a8e-3f7d-4c55-9b1e-6c2f6d1c9a10"

    def setUp(self):
        meter._reset_cost_discount_cache()
        self.addCleanup(meter._reset_cost_discount_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def rules(self, *rules):
        path = self.tmp / "settings-{}.json".format(len(list(self.tmp.iterdir())))
        path.write_text(json.dumps({"cost_discounts": {"version": 1, "rules": [
            {"id": "d_rule{:04d}".format(index), "price_list": price_list, "channel": channel,
             "percent": percent, "start": start, "end": None}
            for index, (price_list, channel, percent, start) in enumerate(
                rule if len(rule) == 4 else (*rule, None) for rule in rules)
        ]}}))
        meter._reset_cost_discount_cache()
        patcher = mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def claude_delta(self, request_id, ts="2026-07-02T00:03:00.000Z"):
        record = claude_record(ts, "m1", {
            "input_tokens": 10, "output_tokens": 5,
            "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 0},
        })
        record["requestId"] = request_id
        return meter.claude_summary(source("claude"), [record])["_cache_window"]["cold_cost_delta"]

    def facts(self, model, provider, price_list, channel, anchor_ts=1_790_000_000):
        return {"model": model, "pricing_provider": provider, "prefix_tokens": 1_000_000,
                "writes_observed": False, "anchor_ts": anchor_ts,
                "discount_price_list": price_list, "discount_channel": channel}

    def test_no_rules_leave_the_delta_unchanged(self):
        self.rules()
        listed = self.claude_delta("req_011abc")
        facts = self.facts("gpt-5.5", "codex", "openai", "direct", anchor_ts=None)
        price, _missing = meter.price_for("gpt-5.5", "codex")
        self.assertGreater(listed, 0)
        self.assertEqual(meter.cache_cold_cost_delta(facts, 300),
                         meter.cache_cold_cost_delta({**facts, "discount_price_list": None}, 300))
        self.assertAlmostEqual(meter.cache_cold_cost_delta(facts, 300),
                               price["input"] - price["cache_read"], places=6)

    def test_claude_code_direct_rule_follows_the_request_id_shape(self):
        self.rules()
        listed = self.claude_delta("req_011abc")
        self.rules(("anthropic", "direct", 30))
        self.assertAlmostEqual(self.claude_delta("req_011abc"), listed * 0.7, places=5)
        self.assertAlmostEqual(self.claude_delta(self.UUID), listed, places=5)
        self.rules(("anthropic", "bedrock", 20))
        self.assertAlmostEqual(self.claude_delta(self.UUID), listed * 0.8, places=5)

    def test_codex_openai_rule_and_dates(self):
        objs = [
            {"type": "turn_context", "timestamp": "2026-07-01T00:00:00.000Z", "payload": {"model": "gpt-5.6"}},
            {"timestamp": "2026-07-01T00:00:04.000Z", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 5000, "cached_input_tokens": 4000,
                                     "output_tokens": 40, "total_tokens": 5040}}}},
        ]
        self.rules()
        listed = meter.codex_summary(source("codex", "gpt-5.6"), objs)["_cache_window"]["cold_cost_delta"]
        self.rules(("openai", "any", 62))
        discounted = meter.codex_summary(source("codex", "gpt-5.6"), objs)["_cache_window"]["cold_cost_delta"]
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(discounted, listed * 0.38, places=5)
        self.rules(("openai", "any", 62, calendar.timegm((2026, 8, 1, 0, 0, 0))))
        later = meter.codex_summary(source("codex", "gpt-5.6"), objs)["_cache_window"]["cold_cost_delta"]
        self.assertAlmostEqual(later, listed, places=5)

    def test_each_runtime_target_only_takes_its_own_rules(self):
        self.rules(("anthropic", "any", 40), ("openai", "any", 50))
        claude = self.facts("claude-sonnet-4-6", "claude", "anthropic", "bedrock", anchor_ts=None)
        listed_claude = meter.cache_cold_cost_delta({**claude, "discount_price_list": None}, 300)
        gpt = self.facts("gpt-5.5", "codex", "cursor", "any", anchor_ts=None)
        listed_gpt = meter.cache_cold_cost_delta({**gpt, "discount_price_list": None}, 300)
        self.assertAlmostEqual(meter.cache_cold_cost_delta(claude, 300), listed_claude * 0.6, places=6)
        for price_list, model, listed, provider in (
                ("cursor", "gpt-5.5", listed_gpt, "codex"),
                ("opencode", "claude-sonnet-4-6", listed_claude, "claude"),
                ("kiro", "claude-sonnet-4-6", listed_claude, "claude")):
            with self.subTest(price_list=price_list):
                facts = self.facts(model, provider, price_list, "any", anchor_ts=None)
                self.assertAlmostEqual(meter.cache_cold_cost_delta(facts, 300), listed, places=6)
        self.rules(("cursor", "any", 25), ("kiro", "any", 10), ("opencode", "any", 5))
        self.assertAlmostEqual(meter.cache_cold_cost_delta(gpt, 300), listed_gpt * 0.75, places=6)
        self.assertAlmostEqual(meter.cache_cold_cost_delta(
            self.facts("claude-sonnet-4-6", "claude", "kiro", "any", anchor_ts=None), 300),
            listed_claude * 0.9, places=6)
        self.assertAlmostEqual(meter.cache_cold_cost_delta(
            self.facts("claude-sonnet-4-6", "claude", "opencode", "any", anchor_ts=None), 300),
            listed_claude * 0.95, places=6)

    def test_adapters_record_the_target_they_discount_with(self):
        from token_meter.domain.cache_window import CacheRequestTracker
        from token_meter.runtimes import opencode as opencode_runtime
        tracker = CacheRequestTracker(discount_price_list="kiro", discount_channel="any")
        tracker.observe(10, "m", {"cache_read_input_tokens": 1})
        tracker.observe(20, "m", {"cache_read_input_tokens": 1},
                        discount_price_list="anthropic", discount_channel="bedrock")
        self.assertEqual((tracker.facts()["discount_price_list"], tracker.facts()["discount_channel"]),
                         ("anthropic", "bedrock"))
        tracker.observe(30, "m", {}, discount_price_list="<bad>", discount_channel="nowhere")
        self.assertEqual((tracker.facts()["discount_price_list"], tracker.facts()["discount_channel"]),
                         ("kiro", "any"))
        self.assertEqual(opencode_runtime.discount_target("opencode", "claude-sonnet-4-6"), ("opencode", "any"))
        self.assertEqual(opencode_runtime.discount_target("amazon-bedrock", "claude-sonnet-4-6"),
                         ("anthropic", "bedrock"))
        root = Path(meter.__file__).resolve().parent / "token_meter" / "runtimes"
        for runtime, expected in (("cursor", 'discount_price_list="cursor", discount_channel="any"'),
                                  ("kiro", 'discount_price_list="kiro", discount_channel="any"'),
                                  ("claude", 'discount_price_list="anthropic", discount_channel=billing_channel'),
                                  ("codex", 'discount_price_list="openai", discount_channel=billing_channel'),
                                  ("opencode", "discount_price_list=target_list, discount_channel=target_channel"),
                                  ("pi", 'discount_price_list=turn.get("discount_price_list")'),
                                  ("hermes", 'channel_from_provider_name(row.get("billing_provider"))')):
            self.assertIn(expected, (root / "{}.py".format(runtime)).read_text(), runtime)

    def test_pi_bedrock_session_takes_the_bedrock_rule(self):
        root = self.tmp / "pi"
        directory = root / "sessions" / "--repo--"
        directory.mkdir(parents=True)
        rows = [
            {"type": "session", "version": 3, "id": "pi", "timestamp": "2026-09-04T10:00:00Z", "cwd": "/repo"},
            {"type": "model_change", "timestamp": "2026-09-04T10:00:01Z", "provider": "amazon-bedrock",
             "modelId": "claude-sonnet-4-6"},
            {"type": "message", "timestamp": "2026-09-04T10:00:02Z", "message": {"role": "user", "content": []}},
            {"type": "message", "timestamp": "2026-09-04T10:00:05Z", "message": {
                "role": "assistant", "model": "claude-sonnet-4-6", "provider": "amazon-bedrock", "content": [],
                "usage": {"input": 100, "output": 20, "cacheRead": 4000, "cacheWrite": 0,
                          "cost": {"input": 0.001, "output": 0.002, "cacheRead": 0.001, "cacheWrite": 0}}}},
        ]
        (directory / "pi.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")

        def delta():
            with mock.patch.object(meter, "PI_AGENT_DIR", str(root)), \
                    mock.patch.object(meter, "_pi_native_adapters", {}), \
                    mock.patch.object(meter, "_RUNTIME_REGISTRY", None):
                return meter.pi_summary(meter.pi_session_sources()[0])["_cache_window"]["cold_cost_delta"]
        self.rules()
        listed = delta()
        self.rules(("anthropic", "direct", 90))
        self.assertAlmostEqual(delta(), listed, places=5)
        self.rules(("anthropic", "bedrock", 25))
        self.assertGreater(listed, 0)
        self.assertAlmostEqual(delta(), listed * 0.75, places=5)

    def test_session_card_and_menubar_carry_the_discounted_figure(self):
        import time as clock
        now = clock.time()
        stamp = clock.strftime("%Y-%m-%dT%H:%M:%S.000Z", clock.gmtime(now - 30))
        self.rules()
        listed = self.claude_delta("req_011abc", ts=stamp)
        self.rules(("anthropic", "direct", 30))
        record = claude_record(stamp, "m1", {
            "input_tokens": 10, "output_tokens": 5,
            "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 4000, "ephemeral_1h_input_tokens": 0},
        })
        record["requestId"] = "req_011abc"
        row = meter.claude_summary({**source("claude"), "mtime": now - 30}, [record])
        card = meter.current_session_summaries([row], now=now)[0]
        self.assertAlmostEqual(card["cache_window"]["cold_cost_delta"], round(listed * 0.7, 6), places=5)
        menubar = meter.menubar_cache_window([card], card["id"])
        self.assertAlmostEqual(menubar["cold_cost_delta"], round(listed * 0.7, 6), places=5)


if __name__ == "__main__":
    unittest.main()
