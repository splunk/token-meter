import unittest
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
from unittest import mock

import meter
from token_meter.contracts import ModelRef, PriceQuery
from token_meter.models.catalog import (
    GEMINI_4_ARGON_PRICE_UPDATE_AT,
    settings_provider_for_model_provider,
)


class ModelPricingCacheTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = dict(meter._model_pricing_cache)
        meter._model_pricing_cache.update({
            "path": None,
            "mtime_ns": None,
            "mtime_checked_at": 0.0,
            "histories": {},
            "effective": {},
            "quotes": {},
        })

    def tearDown(self):
        meter._model_pricing_cache.clear()
        meter._model_pricing_cache.update(self.original_cache)

    def test_settings_stat_is_bounded_while_external_changes_refresh_after_ttl(self):
        with mock.patch.object(
            meter.time, "monotonic", side_effect=(10.0, 10.1, 10.3)
        ), mock.patch.object(
            meter, "_model_pricing_mtime_ns", side_effect=(1, 2)
        ) as stat_mtime, mock.patch.object(
            meter, "load_json", side_effect=({}, {})
        ) as load_settings:
            meter._load_model_price_histories("/tmp/settings.json")
            meter._load_model_price_histories("/tmp/settings.json")
            meter._load_model_price_histories("/tmp/settings.json")

        self.assertEqual(stat_mtime.call_count, 2)
        self.assertEqual(load_settings.call_count, 2)
        self.assertEqual(meter._model_pricing_cache["mtime_ns"], 2)

    def test_canonical_model_provider_query_uses_legacy_settings_history(self):
        prices = {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            result = meter.set_model_price(
                "claude", "claude-sonnet-5", prices,
                path=path, effective_from=100,
            )
            quote = meter.price_quote(
                PriceQuery(
                    ModelRef("anthropic", "claude-sonnet-5"),
                    datetime.fromtimestamp(150, timezone.utc),
                ),
                path=path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(quote.input_per_million, 4.0)
        self.assertEqual(quote.output_per_million, 20.0)


    def test_google_prices_are_listed_and_editable_in_settings(self):
        prices = {"input": 3, "output": 11, "cache_write": 0, "cache_read": 0.3}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            before = meter.model_pricing_settings(path)
            result = meter.set_model_price(
                "google", "gemini-2.5-pro", prices,
                path=path, effective_from=100,
            )
            quote = meter.price_quote(
                PriceQuery(
                    ModelRef("google", "gemini-2.5-pro"),
                    datetime.fromtimestamp(150, timezone.utc),
                ),
                path=path,
            )
            stored = json.loads(Path(path).read_text())

        self.assertEqual(settings_provider_for_model_provider("google"), "google")
        google_rows = [row for row in before["models"] if row["provider"] == "google"]
        self.assertIn("gemini-2.5-pro", {row["model"] for row in google_rows})
        self.assertEqual({row["provider_label"] for row in google_rows}, {"Google Gemini"})
        self.assertTrue(result["ok"])
        self.assertIn("gemini-2.5-pro", stored["model_pricing"]["google"])
        self.assertEqual(quote.input_per_million, 3.0)
        self.assertEqual(quote.output_per_million, 11.0)
        page = (Path(meter.__file__).resolve().parent / "page.html").read_text()
        self.assertIn("<option value=google>Google Gemini</option>", page)

    def test_settings_show_the_builtin_price_in_effect_now(self):
        argon_after = {"input": 4, "output": 20, "cache_write": 0, "cache_read": 0.2}

        def argon_row(path):
            rows = meter.model_pricing_settings(path)["models"]
            return next(
                row for row in rows
                if row["provider"] == "google" and row["model"] == "gemini-4-argon"
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(
                meter.time, "time", return_value=GEMINI_4_ARGON_PRICE_UPDATE_AT - 1
            ):
                before = argon_row(path)
            with mock.patch.object(
                meter.time, "time", return_value=GEMINI_4_ARGON_PRICE_UPDATE_AT
            ):
                after = argon_row(path)
                result = meter.set_model_price(
                    "google", "gemini-4-argon", argon_after,
                    path=path, apply_to_all_history=True,
                )
                saved = argon_row(path)
            before_cutover = meter.price_quote(
                PriceQuery(
                    ModelRef("google", "gemini-4-argon"),
                    datetime.fromtimestamp(GEMINI_4_ARGON_PRICE_UPDATE_AT - 86_400, timezone.utc),
                ),
                path=path,
            )

        self.assertEqual((before["prices"]["input"], before["prices"]["output"]), (2.0, 10.0))
        self.assertEqual((after["prices"]["input"], after["prices"]["output"]), (4.0, 20.0))
        self.assertTrue(result["ok"])
        # A scheduled built-in has no single price, so an all-history choice is kept.
        self.assertTrue(saved["overridden"])
        self.assertEqual(
            (before_cutover.input_per_million, before_cutover.output_per_million), (4.0, 20.0),
        )


if __name__ == "__main__":
    unittest.main()
