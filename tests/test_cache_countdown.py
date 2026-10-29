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


if __name__ == "__main__":
    unittest.main()
