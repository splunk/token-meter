import unittest
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
from unittest import mock

import meter
from token_meter.contracts import ModelRef, PriceQuery
from token_meter.models.catalog import GPT_56_SOL_PRICE_UPDATE_AT


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


    def _quote(self, path, provider, model, at):
        return meter.price_quote(
            PriceQuery(ModelRef(provider, model), datetime.fromtimestamp(at, timezone.utc)),
            path=path,
        )

    def test_all_history_save_of_a_scheduled_model_keeps_the_chosen_price(self):
        current = dict(meter.OPENAI_PRICE["gpt-5.6"])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            result = meter.set_model_price(
                "codex", "gpt-5.6", current, path=path, apply_to_all_history=True,
            )
            stored = json.loads(Path(path).read_text())
            before = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT - 86_400)

        self.assertTrue(result["ok"])
        self.assertIn("gpt-5.6", stored["model_pricing"]["codex"])
        # The user chose this price for all history, so it replaces the earlier scheduled period.
        self.assertEqual((before.input_per_million, before.output_per_million), (4.0, 20.0))

    def test_dated_save_of_the_builtin_price_keeps_following_the_schedule(self):
        earlier = {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            result = meter.set_model_price(
                "codex", "gpt-5.6", earlier, path=path,
                effective_from=GPT_56_SOL_PRICE_UPDATE_AT - 10 * 86_400,
            )
            before = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT - 86_400)
            after = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT + 86_400)

        self.assertTrue(result["ok"])
        self.assertEqual((before.input_per_million, before.output_per_million), (5.0, 30.0))
        self.assertEqual((after.input_per_million, after.output_per_million), (4.0, 20.0))

    def test_equal_save_of_an_unscheduled_model_still_means_builtin(self):
        builtin = dict(meter.CLAUDE_PRICE["claude-sonnet-5"])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            dated = meter.set_model_price(
                "claude", "claude-sonnet-5", builtin, path=path, effective_from=100,
            )
            all_history = meter.set_model_price(
                "claude", "claude-sonnet-5", builtin, path=path, apply_to_all_history=True,
            )
            stored = json.loads(Path(path).read_text()) if Path(path).exists() else {}

        self.assertTrue(dated["ok"])
        self.assertTrue(all_history["ok"])
        self.assertNotIn("claude-sonnet-5", (stored.get("model_pricing") or {}).get("claude", {}))


    def _settings_row(self, path, provider, model):
        return next(
            row for row in meter.model_pricing_settings(path)["models"]
            if row["provider"] == provider and row["model"] == model
        )

    def test_settings_show_the_builtin_price_in_effect_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter.time, "time", return_value=GPT_56_SOL_PRICE_UPDATE_AT - 1):
                before = self._settings_row(path, "codex", "gpt-5.6")
            with mock.patch.object(meter.time, "time", return_value=GPT_56_SOL_PRICE_UPDATE_AT):
                after = self._settings_row(path, "codex", "gpt-5.6")

        self.assertEqual((before["prices"]["input"], before["prices"]["output"]), (5.0, 30.0))
        self.assertEqual((after["prices"]["input"], after["prices"]["output"]), (4.0, 20.0))
        self.assertEqual((before["source"], after["source"]), ("built-in", "built-in"))

    def test_saving_the_shown_price_before_a_scheduled_change_keeps_the_change(self):
        shown = {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter.time, "time", return_value=GPT_56_SOL_PRICE_UPDATE_AT - 86_400):
                result = meter.set_model_price("codex", "gpt-5.6", shown, path=path)
            after = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT + 86_400)

        self.assertTrue(result["ok"])
        self.assertEqual((after.input_per_million, after.output_per_million), (4.0, 20.0))

    def test_dated_save_of_the_current_price_before_the_change_is_kept(self):
        current = dict(meter.OPENAI_PRICE["gpt-5.6"])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            result = meter.set_model_price(
                "codex", "gpt-5.6", current, path=path,
                effective_from=GPT_56_SOL_PRICE_UPDATE_AT - 10 * 86_400,
            )
            before = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT - 86_400)
            row = self._settings_row(path, "codex", "gpt-5.6")

        self.assertTrue(result["ok"])
        self.assertEqual((before.input_per_million, before.output_per_million), (4.0, 20.0))
        self.assertTrue(row["overridden"])

    def test_dated_builtin_save_replaces_an_existing_override(self):
        custom = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
        earlier = {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            meter.set_model_price("codex", "gpt-5.6", custom, path=path, apply_to_all_history=True)
            meter.set_model_price(
                "codex", "gpt-5.6", earlier, path=path,
                effective_from=GPT_56_SOL_PRICE_UPDATE_AT - 10 * 86_400,
            )
            quotes = [
                self._quote(path, "openai", "gpt-5.6", at).input_per_million
                for at in (
                    GPT_56_SOL_PRICE_UPDATE_AT - 20 * 86_400,
                    GPT_56_SOL_PRICE_UPDATE_AT - 86_400,
                    GPT_56_SOL_PRICE_UPDATE_AT + 86_400,
                )
            ]

        self.assertEqual(quotes, [3.0, 5.0, 4.0])

    def test_all_history_override_on_cursor_and_restore_and_idempotent_resave(self):
        current = dict(meter.CURSOR_PRICE["gpt-5.6-sol"])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            first = meter.set_model_price(
                "cursor", "gpt-5.6-sol", current, path=path, apply_to_all_history=True,
            )
            again = meter.set_model_price(
                "cursor", "gpt-5.6-sol", current, path=path, apply_to_all_history=True,
            )
            kept = self._settings_row(path, "cursor", "gpt-5.6-sol")
            before = self._quote(path, "cursor", "gpt-5.6-sol", GPT_56_SOL_PRICE_UPDATE_AT - 86_400)
            restored = meter.set_model_price(
                "cursor", "gpt-5.6-sol", remove=True, path=path, apply_to_all_history=True,
            )
            reset = self._settings_row(path, "cursor", "gpt-5.6-sol")

        self.assertTrue(first["changed"])
        self.assertFalse(again["changed"])
        self.assertEqual((kept["source"], kept["overridden"]), ("override", True))
        self.assertEqual(before.input_per_million, current["input"])
        self.assertTrue(restored["ok"])
        self.assertEqual(reset["source"], "built-in")


    def test_all_history_save_of_the_shown_earlier_price_is_a_flat_price(self):
        shown = {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.json")
            with mock.patch.object(meter.time, "time", return_value=GPT_56_SOL_PRICE_UPDATE_AT - 86_400):
                result = meter.set_model_price(
                    "codex", "gpt-5.6", shown, path=path, apply_to_all_history=True,
                )
            after = self._quote(path, "openai", "gpt-5.6", GPT_56_SOL_PRICE_UPDATE_AT + 86_400)

        # "All history" replaces the timeline with one price, so the schedule no longer applies.
        self.assertTrue(result["changed"])
        self.assertEqual((after.input_per_million, after.output_per_million), (5.0, 30.0))


if __name__ == "__main__":
    unittest.main()
