import unittest

from token_meter.domain.cache_window import (
    CacheRequestTracker,
    build_cache_window,
    cache_window_projection,
    documented_window,
)


def claude_usage(read=0, write_5m=0, write_1h=0, fresh=10):
    return {
        "input_tokens": fresh,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write_5m + write_1h,
        "cache_creation_5m_input_tokens": write_5m,
        "cache_creation_1h_input_tokens": write_1h,
    }


class DocumentedWindowTests(unittest.TestCase):
    def test_model_families_resolve_to_documented_rows(self):
        cases = {
            "claude-opus-5-5": ("anthropic", 300, 300),
            "us.anthropic.claude-sonnet-5-5": ("anthropic", 300, 300),
            "claude-4.5-sonnet": ("anthropic", 300, 300),
            "gpt-5.6-sol": ("openai-ttl", 1800, None),
            "gpt-6.1-terra": ("openai-ttl", 1800, None),
            "gpt-5.5": ("openai-extended", 300, 1800),
            "gpt-5.1-codex-max": ("openai-extended", 300, 1800),
            "gpt-5": ("openai-extended", 300, 1800),
            "gpt-4.1": ("openai-extended", 300, 1800),
            "gpt-4o": ("openai", 300, 600),
            "o3": ("openai", 300, 600),
        }
        for model, (family, low, high) in cases.items():
            with self.subTest(model=model):
                row = documented_window(model)
                self.assertEqual((row["family"], row["min_s"], row["max_s"]), (family, low, high))

    def test_unknown_families_have_no_documented_window(self):
        for model in ("gemini-3-pro", "composer-1", "auto", "", "opencode-local", None,
                      "gpt-oss-120b", "gpt-oss:20b"):
            with self.subTest(model=model):
                self.assertIsNone(documented_window(model))


class TrackerTests(unittest.TestCase):
    def test_latest_request_wins_and_recorded_ttl_comes_from_newest_split(self):
        tracker = CacheRequestTracker("claude")
        tracker.observe(100, "claude-opus-5-5", claude_usage(write_1h=5000))
        tracker.observe(200, "claude-opus-5-5", claude_usage(read=5000, write_5m=300), start_ts=190)
        tracker.observe(150, "claude-opus-5-5", claude_usage(read=1))
        facts = tracker.facts()
        self.assertEqual(facts["anchor_ts"], 190)
        self.assertEqual(facts["ttl_s"], 300)
        self.assertEqual(facts["prefix_tokens"], 10 + 5000 + 300)
        self.assertTrue(facts["cache_observed"])
        self.assertTrue(facts["writes_observed"])

    def test_read_only_latest_keeps_the_ttl_it_was_written_with(self):
        tracker = CacheRequestTracker("claude")
        tracker.observe(100, "claude-opus-5-5", claude_usage(write_1h=5000))
        tracker.observe(200, "claude-opus-5-5", claude_usage(read=5000))
        self.assertEqual(tracker.facts()["ttl_s"], 3600)

    def test_invalid_timestamps_are_ignored_and_bad_start_falls_back(self):
        tracker = CacheRequestTracker()
        for ts in (None, 0, -5, "x", float("nan")):
            tracker.observe(ts, "gpt-5.5", {})
        self.assertIsNone(tracker.facts())
        tracker.observe(50, "gpt-5.5", {}, start_ts=90)
        self.assertEqual(tracker.facts()["anchor_ts"], 50)

    def test_recorded_ttl_does_not_carry_over_to_a_different_model(self):
        tracker = CacheRequestTracker()
        tracker.observe(100, "claude-opus-5-5", claude_usage(write_1h=5000))
        tracker.observe(200, "gpt-5.5", {"input_tokens": 50, "cache_read_input_tokens": 10})
        window = build_cache_window(tracker.facts(), None)
        self.assertEqual((window["basis"], window["source"]), ("documented", "openai"))

    def test_reported_cache_that_never_appears_means_no_window(self):
        tracker = CacheRequestTracker("claude")
        tracker.observe(100, "claude-opus-5-5", claude_usage())
        self.assertIsNotNone(build_cache_window(tracker.facts(), None))
        tracker.observe(200, "claude-opus-5-5", claude_usage())
        self.assertIsNone(build_cache_window(tracker.facts(), None))

    def test_runtime_without_cache_reporting_has_no_prefix(self):
        tracker = CacheRequestTracker("cursor", cache_reported=False)
        tracker.observe(50, "claude-4.5-sonnet", {})
        facts = tracker.facts()
        self.assertIsNone(facts["prefix_tokens"])
        self.assertFalse(facts["cache_observed"])


class BuildWindowTests(unittest.TestCase):
    def test_recorded_window_uses_trace_ttl_and_prices_the_cold_delta(self):
        tracker = CacheRequestTracker("claude")
        tracker.observe(1000, "claude-opus-5-5", claude_usage(read=9000, write_1h=1000))
        seen = {}

        def delta(facts, window_s):
            seen["window"] = window_s
            return 0.42

        window = build_cache_window(tracker.facts(), delta)
        self.assertEqual(window["basis"], "recorded")
        self.assertEqual(window["window_s"], 3600)
        self.assertEqual(window["max_window_s"], 3600)
        self.assertEqual(window["cold_cost_delta"], 0.42)
        self.assertEqual(window["cold_kind"], "write")
        self.assertEqual(seen["window"], 3600)

    def test_documented_window_for_codex_without_ttl_split(self):
        tracker = CacheRequestTracker("codex")
        tracker.observe(1000, "gpt-5.6-sol", {"input_tokens": 50, "cache_read_input_tokens": 4000})
        window = build_cache_window(tracker.facts(), lambda facts, window_s: 0.1)
        self.assertEqual(window["basis"], "documented")
        self.assertEqual(window["source"], "openai")
        self.assertEqual(window["window_s"], 1800)
        self.assertIsNone(window["max_window_s"])
        self.assertEqual(window["cold_kind"], "input")

    def test_no_cache_evidence_never_prices_a_delta(self):
        tracker = CacheRequestTracker("cursor", cache_reported=False)
        tracker.observe(1000, "claude-4.5-sonnet", {})
        window = build_cache_window(tracker.facts(), lambda facts, window_s: 9.0)
        self.assertEqual(window["basis"], "documented")
        self.assertIsNone(window["cold_cost_delta"])
        self.assertIsNone(window["prefix_tokens"])
        self.assertFalse(window["cache_observed"])

    def test_unknown_model_without_recorded_ttl_has_no_window(self):
        tracker = CacheRequestTracker()
        tracker.observe(1000, "gemini-3-pro", {"cache_read_input_tokens": 10})
        self.assertIsNone(build_cache_window(tracker.facts(), None))
        self.assertIsNone(build_cache_window(None, None))

    def test_pricing_failure_becomes_unknown_not_zero(self):
        tracker = CacheRequestTracker("claude")
        tracker.observe(1000, "claude-opus-5-5", claude_usage(read=10))

        def boom(facts, window_s):
            raise ValueError("no price")

        self.assertIsNone(build_cache_window(tracker.facts(), boom)["cold_cost_delta"])
        self.assertIsNone(build_cache_window(tracker.facts(), lambda f, w: None)["cold_cost_delta"])


class ProjectionTests(unittest.TestCase):
    def test_projection_rejects_unusable_values_and_bounds_fields(self):
        self.assertIsNone(cache_window_projection(None))
        self.assertIsNone(cache_window_projection({"basis": "guessed", "anchor_ts": 1, "window_s": 300}))
        self.assertIsNone(cache_window_projection({"basis": "recorded", "anchor_ts": 1, "window_s": 0}))
        self.assertIsNone(cache_window_projection({"basis": "recorded", "anchor_ts": -1, "window_s": 300}))
        value = cache_window_projection({
            "basis": "documented", "source": "/Users/me/secret", "anchor_ts": 5,
            "window_s": 300, "max_window_s": 100, "prefix_tokens": -4,
            "cold_cost_delta": float("inf"), "extra": "dropped",
        })
        self.assertEqual(set(value), {
            "basis", "source", "anchor_ts", "window_s", "max_window_s",
            "prefix_tokens", "cold_cost_delta", "cold_kind", "cache_reported", "cache_observed",
        })
        self.assertEqual(value["cold_kind"], "input")
        self.assertIsNone(value["source"])
        self.assertIsNone(value["max_window_s"])
        self.assertEqual(value["prefix_tokens"], 0)
        self.assertIsNone(value["cold_cost_delta"])


if __name__ == "__main__":
    unittest.main()
