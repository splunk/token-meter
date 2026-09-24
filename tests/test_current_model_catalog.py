import unittest
from datetime import datetime, timezone

import meter
from token_meter.models.catalog import GPT_56_SOL_PRICE_UPDATE_AT


class CurrentModelCatalogTests(unittest.TestCase):
    def test_models_released_on_september_22_use_published_rates_in_settings(self):
        expected = {
            ("codex", "gpt-6-sol"): {
                "input": 2.0,
                "output": 10.0,
                "cache_write": 2.5,
                "cache_read": 0.2,
            },
            ("codex", "gpt-6-luna"): {
                "input": 0.1,
                "output": 0.5,
                "cache_write": 0.125,
                "cache_read": 0.01,
            },
            ("claude", "claude-opus-5-5"): {
                "input": 4.0,
                "output": 20.0,
                "cache_write": 5.0,
                "cache_read": 0.2,
            },
        }
        settings = meter.model_pricing_settings()
        rows = {
            (row["provider"], row["model"]): row
            for row in settings["models"]
        }

        self.assertEqual(settings["reviewed_on"], "2026-09-24")
        for (provider, model), prices in expected.items():
            with self.subTest(provider=provider, model=model):
                actual, unavailable = meter.price_for(model, provider)
                self.assertFalse(unavailable)
                self.assertEqual(actual, prices)
                self.assertEqual(rows[(provider, model)]["prices"], prices)
                self.assertTrue(rows[(provider, model)]["builtin"])
                self.assertEqual(rows[(provider, model)]["source"], "built-in")

    def test_gpt_6_sol_and_luna_use_published_long_context_rates(self):
        usage = {
            "input_tokens": 272_001,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }

        expected = {
            "gpt-6-sol": {
                "input": 1.088004,
                "cache_write": 5.0,
                "cache_read": 0.4,
                "output": 15.0,
            },
            "gpt-6-luna": {
                "input": 0.0544,
                "cache_write": 0.25,
                "cache_read": 0.02,
                "output": 0.75,
            },
        }

        for model, prices in expected.items():
            with self.subTest(model=model):
                actual = meter.cost_of(usage, model, "codex")
                for component, value in prices.items():
                    self.assertAlmostEqual(actual[component], value, places=6)

    def test_cursor_grok_models_require_the_trace_visible_speed_variant(self):
        composer = {
            "modelConfig": {
                "selectedModels": [{
                    "modelId": "grok-4.6",
                    "parameters": [{"id": "fast", "value": "true"}],
                }],
            },
        }

        unselected, unavailable = meter.price_for("grok-4.6", "cursor")
        fast, fast_approximate = meter.price_for("grok-4.6", "cursor", "fast")
        standard, standard_approximate = meter.price_for(
            "grok-4.5", "cursor", "standard",
        )
        composer_fast, composer_fast_approximate = meter.price_for(
            "composer-2.5", "cursor", "fast",
        )

        self.assertEqual(unselected, meter.ZERO_PRICE)
        self.assertTrue(unavailable)
        self.assertEqual((fast["input"], fast["cache_read"], fast["output"]),
                         (4.0, 1.0, 12.0))
        self.assertTrue(fast_approximate)
        self.assertEqual((standard["input"], standard["cache_read"], standard["output"]),
                         (2.0, 0.5, 6.0))
        self.assertTrue(standard_approximate)
        self.assertEqual((composer_fast["input"], composer_fast["cache_read"],
                          composer_fast["output"]), (3.0, 0.5, 15.0))
        self.assertTrue(composer_fast_approximate)
        self.assertEqual(meter.cursor_price_variant(composer, "grok-4.6"), "fast")

    def test_codex_gpt_5_3_model_uses_its_published_api_rate(self):
        price, unavailable = meter.price_for("gpt-5.3-codex", "codex")

        self.assertFalse(unavailable)
        self.assertEqual(price, {
            "input": 1.75,
            "output": 14.0,
            "cache_write": 0.0,
            "cache_read": 0.175,
        })

    def test_gpt_5_6_sol_uses_the_current_promotional_rate(self):
        price, unavailable = meter.price_for("gpt-5.6-sol", "codex")

        self.assertFalse(unavailable)
        self.assertEqual(price, {
            "input": 4.0,
            "output": 20.0,
            "cache_write": 5.0,
            "cache_read": 0.4,
        })
        before, before_unavailable = meter.price_for(
            "gpt-5.6-sol", "codex",
            at=datetime.fromtimestamp(GPT_56_SOL_PRICE_UPDATE_AT - 1, timezone.utc),
        )
        self.assertFalse(before_unavailable)
        self.assertEqual(before, {
            "input": 5.0,
            "output": 30.0,
            "cache_write": 6.25,
            "cache_read": 0.5,
        })

    def test_cursor_third_party_model_names_do_not_borrow_provider_rates(self):
        for model_id in ("gpt-5.3-codex", "claude-opus-5"):
            with self.subTest(model_id=model_id):
                price, approximate = meter.price_for(model_id, "cursor")

                self.assertEqual(price, meter.ZERO_PRICE)
                self.assertTrue(approximate)


if __name__ == "__main__":
    unittest.main()
