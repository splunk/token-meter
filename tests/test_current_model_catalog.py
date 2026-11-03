import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import meter
from token_meter.models.catalog import GPT_56_SOL_PRICE_UPDATE_AT


class CurrentModelCatalogTests(unittest.TestCase):
    def test_fast_gpt_rates_are_editable_builtin_rows_and_variant_quotes(self):
        expected = {
            "gpt-6-astra": (20.0, 100.0, 25.0, 2.0),
            "gpt-6.1-sol": (4.0, 20.0, 5.0, 0.2),
            "gpt-6-luna": (0.2, 1.0, 0.25, 0.02),
        }
        rows = {
            row["model"]: row for row in meter.model_pricing_settings()["models"]
            if row["provider"] == "codex"
        }
        for model, rates in expected.items():
            with self.subTest(model=model):
                prices = dict(zip(meter.MODEL_PRICE_FIELDS, rates))
                for variant in ("fast", "priority"):
                    actual, unavailable = meter.price_for(model, "codex", variant)
                    self.assertFalse(unavailable)
                    self.assertEqual(actual, prices)
                self.assertEqual(meter.price_for(model + "-fast", "codex")[0], prices)
                self.assertEqual(rows[model + "-fast"]["prices"], prices)
                self.assertTrue(rows[model + "-fast"]["builtin"])

    def test_fast_gpt_context_costs_and_cache_savings_use_supplied_rates(self):
        expected = {
            "gpt-6-astra": (20.0, 100.0, 25.0, 2.0),
            "gpt-6.1-sol": (4.0, 20.0, 5.0, 0.2),
            "gpt-6-luna": (0.2, 1.0, 0.25, 0.02),
        }
        for model, (input_rate, output_rate, write_rate, read_rate) in expected.items():
            for context, multiplier, output_multiplier in ((272_000, 1, 1), (272_001, 2, 1.5)):
                for native_model in (model, "openai/" + model, model + "-2026-10-05"):
                    with self.subTest(model=native_model, context=context):
                        usage = {"input_tokens": context - 2_000,
                                 "cache_read_input_tokens": 1_000,
                                 "cache_creation_input_tokens": 1_000,
                                 "output_tokens": 1_000_000}
                        cost = meter.cost_of(usage, native_model, "codex", "fast")
                        self.assertAlmostEqual(cost["input"], (context - 2_000) * input_rate * multiplier / 1_000_000)
                        self.assertAlmostEqual(cost["cache_read"], read_rate * multiplier / 1_000)
                        self.assertAlmostEqual(cost["cache_write"], write_rate * multiplier / 1_000)
                        self.assertEqual(cost["output"], output_rate * output_multiplier)
                        savings = meter.cache_savings({}, "codex", native_model, [{
                            "model": native_model, "pricing_variant": "fast",
                            "tokens": {"fresh_input": context - 2_000,
                                       "cache_read": 1_000, "cache_write": 1_000},
                        }])
                        self.assertAlmostEqual(savings, (input_rate - read_rate) * multiplier / 1_000)

    def test_unpriced_fast_models_and_unknown_tiers_are_unavailable(self):
        for model, variant in (("gpt-6-sol", "fast"), ("gpt-6.1-sol", "flex"),
                               ("gpt-6.1-sol", "unsupported")):
            with self.subTest(model=model, variant=variant):
                prices, unavailable = meter.price_for(model, "codex", variant)
                self.assertTrue(unavailable)
                self.assertEqual(prices, meter.ZERO_PRICE)

    def test_editing_fast_prices_does_not_change_standard_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            override = {"input": 6.0, "output": 30.0, "cache_write": 7.5, "cache_read": 0.3}
            result = meter.set_model_price(
                "codex", "gpt-6.1-sol-fast", override,
                path=path, apply_to_all_history=True,
            )
            self.assertTrue(result["ok"])
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", path):
                fast, unavailable = meter.price_for("gpt-6.1-sol", "codex", "fast")
                self.assertFalse(unavailable)
                self.assertEqual(fast, override)
                standard, unavailable = meter.price_for("gpt-6.1-sol", "codex")
                self.assertFalse(unavailable)
                self.assertEqual(standard["input"], 2.0)
                self.assertEqual(standard["cache_read"], 0.1)
                cost = meter.cost_of({"input_tokens": 272_001, "output_tokens": 1_000_000},
                                     "gpt-6.1-sol", "codex", "fast")
                self.assertAlmostEqual(cost["input"], 3.264012)
                self.assertEqual(cost["output"], 45.0)
                row = next(row for row in meter.model_pricing_settings()["models"]
                           if row["provider"] == "codex" and row["model"] == "gpt-6.1-sol-fast")
                self.assertTrue(row["overridden"])
                self.assertEqual(row["prices"], override)

    def test_supplied_gpt_6_rates_are_builtin_settings_rows(self):
        expected = {
            "gpt-6-astra": (10.0, 50.0, 12.5, 1.0),
            "gpt-6.1-sol": (2.0, 10.0, 2.5, 0.1),
            "gpt-6-luna": (0.1, 0.5, 0.125, 0.01),
        }
        rows = {
            row["model"]: row for row in meter.model_pricing_settings()["models"]
            if row["provider"] == "codex"
        }
        for model, rates in expected.items():
            with self.subTest(model=model):
                prices = dict(zip(meter.MODEL_PRICE_FIELDS, rates))
                actual, unavailable = meter.price_for(model, "codex")
                self.assertFalse(unavailable)
                self.assertEqual(actual, prices)
                self.assertEqual(rows[model]["prices"], prices)
                self.assertTrue(rows[model]["builtin"])
                self.assertEqual(rows[model]["source"], "built-in")

    def test_supplied_gpt_6_rates_price_every_component_at_context_boundary(self):
        rates = {
            "gpt-6-astra": (10.0, 50.0, 12.5, 1.0),
            "gpt-6.1-sol": (2.0, 10.0, 2.5, 0.1),
            "gpt-6-luna": (0.1, 0.5, 0.125, 0.01),
        }
        # Cached input and cache writes both count toward input context length.
        for model, (input_rate, output_rate, write_rate, read_rate) in rates.items():
            for input_tokens, input_multiplier, output_multiplier in (
                (271_999, 1.0, 1.0), (272_000, 1.0, 1.0), (272_001, 2.0, 1.5),
            ):
                with self.subTest(model=model, context=input_tokens):
                    usage = {
                        "input_tokens": input_tokens - 2_000,
                        "cache_creation_input_tokens": 1_000,
                        "cache_read_input_tokens": 1_000,
                        "output_tokens": 1_000_000,
                    }
                    actual = meter.cost_of(usage, model, "codex")
                    expected = {
                        "input": (input_tokens - 2_000) * input_rate * input_multiplier / 1_000_000,
                        "cache_write": write_rate * input_multiplier / 1_000,
                        "cache_read": read_rate * input_multiplier / 1_000,
                        "output": output_rate * output_multiplier,
                    }
                    for component, cost in expected.items():
                        self.assertAlmostEqual(actual[component], cost, places=6)

    def test_gpt_6_1_sol_aliases_keep_the_same_long_context_rates(self):
        usage = {"input_tokens": 272_001, "output_tokens": 1_000_000}
        for model in ("gpt-6.1-sol", "openai/gpt-6.1-sol", "gpt-6.1-sol-2026-10-05"):
            with self.subTest(model=model):
                price, unavailable = meter.price_for(model, "codex")
                self.assertFalse(unavailable)
                self.assertEqual(price["cache_read"], 0.1)
                cost = meter.cost_of(usage, model, "codex")
                self.assertAlmostEqual(cost["input"], 1.088004, places=6)
                self.assertEqual(cost["output"], 15.0)
        for provider in ("claude", "cursor", "opencode"):
            with self.subTest(provider=provider):
                price, unavailable = meter.price_for("gpt-6.1-sol", provider)
                self.assertTrue(unavailable)
                self.assertEqual(price, meter.ZERO_PRICE)

    def test_gpt_6_1_sol_alias_cache_savings_uses_the_context_rate(self):
        for fresh_input, expected in ((271_000, 0.0019), (271_001, 0.0038)):
            for model in ("gpt-6.1-sol", "openai/gpt-6.1-sol", "gpt-6.1-sol-2026-10-05"):
                with self.subTest(model=model, fresh_input=fresh_input):
                    execution = {
                        "model": model,
                        "tokens": {"fresh_input": fresh_input, "cache_read": 1_000},
                    }
                    actual = meter.cache_savings(
                        {"cache_read": 1_000}, "codex", model, [execution],
                    )
                    self.assertAlmostEqual(actual, expected, places=6)

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
