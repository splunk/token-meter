import unittest
import datetime
import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock
import meter
from token_meter.contracts import DiscoveryContext
from token_meter.runtimes.codex import CodexRuntimeAdapter


class RemovedLanguageSignalTests(unittest.TestCase):
    def test_language_signal_endpoints_return_not_found(self):
        for path in ("/settings/language-signals", "/settings/frustration"):
            with self.subTest(path=path):
                handler = object.__new__(meter.H)
                handler.path = path
                errors, responses = [], []
                handler.send_error = lambda status: errors.append(status)
                handler._send = lambda *args, **kwargs: responses.append((args, kwargs))
                handler.headers = {"Host": "127.0.0.1:8722"}
                handler.do_POST()
                self.assertEqual(errors, [404])
                self.assertEqual(responses, [])

    def test_language_signal_code_is_gone_from_backend_and_parsers(self):
        for name in (
            "language_signal_settings", "set_language_signal_terms", "frustration_settings",
            "set_frustration_terms", "analyze_language_signals", "analyze_language_signal_turns",
            "attach_language_signals", "aggregate_language_signals", "aggregate_frustration",
            "DEFAULT_FRUSTRATION_TERMS", "DEFAULT_POSITIVE_TERMS",
        ):
            self.assertFalse(hasattr(meter, name), name)
        root = Path(meter.IMPLEMENTATION_FILE).parent
        for path in sorted((root / "runtimes").glob("*.py")) + [root / "domain" / "aggregates.py"]:
            source = path.read_text()
            for marker in ("language_signal", "frustration", "signal_turns"):
                self.assertNotIn(marker, source, f"{path.name}: {marker}")
        self.assertNotIn('"language_signals"', Path(meter.IMPLEMENTATION_FILE).read_text())

    def test_cross_session_payload_has_no_language_signal_fields(self):
        def row(source):
            return {
                **source, "turns": 1, "cost": 1.0, "tokens": 100,
                "input_tokens": 90, "output_tokens": 10,
                "models": ["claude-test"], "token_estimate": False,
                "availability": meter.metric_availability(
                    "claude", cost=True, tokens=True,
                    input_tokens=True, output_tokens=True,
                ),
                "_model_cost": {"claude-test": 1.0},
                "_model_tok": {"claude-test": 100},
                "_day_cost": {"2026-08-18": 1.0}, "model_stats": [],
                "_model_daily": [], "_performance_samples": [],
                "_wait_samples": [], "_tool_evidence": {},
            }
        saved_cache = dict(meter._xsess)
        try:
            meter._xsess["data"], meter._xsess["at"] = None, 0
            with mock.patch.object(meter, "session_summary", side_effect=row), \
                    mock.patch.object(meter, "capability_inventory", return_value={}):
                result = meter.cross_session(sources=[{
                    "id": "only", "path": "/tmp/only", "provider": "claude",
                    "runtime": "Claude", "label": "Claude Desktop", "project": "/repo", "mtime": 1,
                }])
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_cache)
        self.assertNotIn("language_signals", result)
        self.assertNotIn("frustration", result)
        self.assertNotIn("language_signals", json.dumps(result))

    def test_every_settings_write_drops_obsolete_language_signal_terms(self):
        obsolete = {
            "language_signal_terms": {"positive": ["great"], "friction": ["damn"]},
            "frustration_terms": ["damn"],
        }
        writers = {
            "updates": lambda path: meter.set_update_settings({"enabled": False, "auto_install": False}, path),
            "budgets": lambda path: meter.set_budget_settings({"monthly_usd": 50}, path),
            "session_budget": lambda path: meter.set_session_budget_override("session-a", 5, path=path),
            "default_session_budget": lambda path: meter.set_default_session_budget_value(12, path=path),
            "model_pricing": lambda path: meter.set_model_price(
                "claude", "claude-sonnet-5",
                {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4},
                effective_from=100, path=path,
            ),
            "work_insights": lambda path: meter.set_work_insights_settings({"pause_on_battery": False}, path),
        }
        for name, write in writers.items():
            with self.subTest(writer=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "settings.json"
                path.write_text(json.dumps({**obsolete, "unrelated": {"keep": True}}))
                with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                    result = write(str(path))
                saved = json.loads(path.read_text())
                self.assertTrue(result.get("ok"), result)
                self.assertNotIn("language_signal_terms", saved)
                self.assertNotIn("frustration_terms", saved)
                self.assertEqual(saved["unrelated"], {"keep": True})

    def test_reading_settings_never_rewrites_obsolete_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original = json.dumps({"frustration_terms": ["damn"]})
            path.write_text(original)
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                meter.update_settings(str(path))
                meter.budget_settings(str(path))
                meter.model_pricing_settings()
            self.assertEqual(path.read_text(), original)


class PricingTests(unittest.TestCase):
    def test_builtin_pricing_exposes_reviewed_primary_sources(self):
        pricing = meter.model_pricing_settings()
        self.assertEqual(pricing["reviewed_on"], "2026-11-11")
        self.assertEqual(
            [source["provider"] for source in pricing["sources"]],
            ["anthropic", "openai", "cursor", "google", "xai"],
        )
        self.assertEqual(
            [source["url"] for source in pricing["sources"]],
            [
                "https://platform.claude.com/docs/en/about-claude/pricing",
                "https://developers.openai.com/api/docs/models/compare",
                "https://cursor.com/docs/models-and-pricing",
                "https://ai.google.dev/gemini-api/docs/pricing",
                "https://docs.x.ai/docs/models",
            ],
        )

    def test_current_anthropic_builtin_prices_match_primary_source(self):
        self.assertEqual(meter.CLAUDE_PRICE["claude-fable-5"], {
            "input": 10.0, "output": 50.0,
            "cache_write": 12.5, "cache_read": 1.0,
        })
        self.assertEqual(meter.CLAUDE_PRICE["claude-opus-4-8"], {
            "input": 5.0, "output": 25.0,
            "cache_write": 6.25, "cache_read": 0.5,
        })

    def test_opus_5_uses_published_api_rates(self):
        price, approximate = meter.price_for("claude-opus-5", "claude")
        self.assertEqual(price, {
            "input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5,
        })
        self.assertFalse(approximate)
        row = next(
            item for item in meter.model_pricing_settings()["models"]
            if item["provider"] == "claude" and item["model"] == "claude-opus-5"
        )
        self.assertTrue(row["builtin"])
        self.assertFalse(row["overridden"])
        self.assertEqual(row["source"], "built-in")

    def test_sonnet_5_uses_current_standard_api_rates(self):
        price, approximate = meter.price_for("claude-sonnet-5", "claude")
        self.assertEqual(price, {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2})
        self.assertFalse(approximate)

    def test_sonnet_5_5_uses_published_rates_in_settings_and_estimates(self):
        expected = {
            "input": 2.0, "output": 10.0,
            "cache_write": 2.5, "cache_read": 0.2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                item for item in meter.model_pricing_settings(
                    path=str(Path(tmp) / "settings.json"),
                )["models"]
                if item["provider"] == "claude"
                and item["model"] == "claude-sonnet-5-5"
            ]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["prices"], expected)
        self.assertTrue(row["builtin"])
        self.assertEqual(row["source"], "built-in")
        price, unavailable = meter.price_for("claude-sonnet-5-5", "claude")
        self.assertEqual(price, expected)
        self.assertFalse(unavailable)

    def test_unknown_models_do_not_inherit_runtime_default_prices(self):
        for provider, model, default_price in (
            ("claude", "claude-future-unknown", meter.CLAUDE_PRICE[meter.DEFAULT_CLAUDE_MODEL]),
            ("codex", "gpt-future-unknown", meter.OPENAI_PRICE[meter.DEFAULT_OPENAI_MODEL]),
            ("opencode", "future-model", {
                "input": 2.0, "output": 10.0,
                "cache_write": 2.5, "cache_read": 0.2,
            }),
        ):
            with self.subTest(provider=provider, model=model):
                price, unavailable = meter.price_for(model, provider)
                self.assertEqual(price, meter.ZERO_PRICE)
                self.assertNotEqual(price, default_price)
                self.assertTrue(unavailable)

    def test_codex_path_discovery_does_not_invent_a_default_model(self):
        path = os.path.expanduser("~/.codex/sessions/model-less.jsonl")
        with (
            mock.patch.object(meter, "all_session_sources", return_value=[]),
            mock.patch.object(meter, "codex_meta", return_value={}),
            mock.patch.object(meter, "safe_mtime", return_value=0),
        ):
            source = meter.source_from_path(path)

        self.assertEqual(source["model"], "unknown-model")

    def test_gpt_5_6_uses_current_sol_api_rates(self):
        price, approximate = meter.price_for("gpt-5.6", "codex")
        expected = {
            "input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.4,
        }
        self.assertEqual(price, expected)
        self.assertFalse(approximate)
        explicit, explicit_approximate = meter.price_for("gpt-5.6-sol", "codex")
        self.assertEqual(explicit, price)
        self.assertFalse(explicit_approximate)
        for model in ("gpt-5.6", "gpt-5.6-sol"):
            with self.subTest(model=model):
                row = next(
                    item for item in meter.model_pricing_settings()["models"]
                    if item["provider"] == "codex" and item["model"] == model
                )
                self.assertEqual(row["prices"], expected)
                self.assertEqual(row["source"], "built-in")

    def test_gpt_5_6_current_tier_prices_match_official_model_catalog(self):
        expected = {
            "gpt-5.6-terra": {
                "input": 2.0, "output": 12.0, "cache_write": 2.5, "cache_read": 0.2,
            },
            "gpt-5.6-luna": {
                "input": 0.2, "output": 1.2, "cache_write": 0.25, "cache_read": 0.02,
            },
        }
        for model, prices in expected.items():
            with self.subTest(model=model):
                actual, approximate = meter.price_for(model, "codex")
                self.assertEqual(actual, prices)
                self.assertFalse(approximate)

    def test_gpt_6_astra_uses_official_api_rates_and_appears_in_settings(self):
        expected = {
            "input": 10.0, "output": 50.0,
            "cache_write": 12.5, "cache_read": 1.0,
        }
        actual, unavailable = meter.price_for("gpt-6-astra", "codex")

        self.assertEqual(actual, expected)
        self.assertFalse(unavailable)
        row = next(
            item for item in meter.model_pricing_settings()["models"]
            if item["provider"] == "codex" and item["model"] == "gpt-6-astra"
        )
        self.assertTrue(row["builtin"])
        self.assertFalse(row["overridden"])
        self.assertEqual(row["source"], "built-in")
        self.assertEqual(row["prices"], expected)

    def test_gpt_5_6_price_cutover_preserves_older_session_estimates(self):
        previous = {
            "input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.5,
        }
        for model, current_input in (("gpt-5.6-terra", 2.0), ("gpt-5.6-luna", 0.2)):
            with self.subTest(model=model):
                old, old_approximate = meter.price_for(
                    model, "codex", at=meter.GPT_56_PRICE_UPDATE_AT - 1,
                )
                current, current_approximate = meter.price_for(
                    model, "codex", at=meter.GPT_56_PRICE_UPDATE_AT,
                )
                self.assertEqual(old, previous)
                self.assertFalse(old_approximate)
                self.assertEqual(current["input"], current_input)
                self.assertFalse(current_approximate)

    def test_gpt_5_6_long_context_multiplier_starts_at_price_cutover(self):
        usage = {
            "input_tokens": meter.GPT_56_LONG_CONTEXT_TOKENS + 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 100_000,
        }
        old = meter.cost_of(
            usage, "gpt-5.6-sol", "cursor", at=meter.GPT_56_PRICE_UPDATE_AT - 1,
        )
        current = meter.cost_of(
            usage, "gpt-5.6-sol", "cursor", at=meter.GPT_56_PRICE_UPDATE_AT,
        )
        self.assertAlmostEqual(old["input"], usage["input_tokens"] * 5.0 / 1e6)
        self.assertAlmostEqual(old["output"], 3.0)
        self.assertAlmostEqual(current["input"], usage["input_tokens"] * 5.0 * 2.0 / 1e6)
        self.assertAlmostEqual(current["output"], 4.5)

    def test_codex_credit_pricing_has_no_long_context_surcharge(self):
        from token_meter.models.catalog import GPT_56_SOL_PRICE_UPDATE_AT as at
        usage = {
            "input_tokens": meter.GPT_56_LONG_CONTEXT_TOKENS - 99_999,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 100_000,
            "output_tokens": 100_000,
        }
        for model in ("gpt-5.6-sol", "gpt-6.1-sol"):
            with self.subTest(model=model):
                price, _ = meter.price_for(model, "codex", at=at)
                codex = meter.cost_of(usage, model, "codex", at=at)
                self.assertAlmostEqual(codex["input"], usage["input_tokens"] * price["input"] / 1e6)
                self.assertAlmostEqual(codex["cache_read"], 100_000 * price["cache_read"] / 1e6)
                self.assertAlmostEqual(codex["output"], 100_000 * price["output"] / 1e6)
        cursor_price, _ = meter.price_for("gpt-5.6-sol", "cursor", at=at)
        cursor = meter.cost_of(usage, "gpt-5.6-sol", "cursor", at=at)
        self.assertAlmostEqual(
            cursor["input"], usage["input_tokens"] * cursor_price["input"] * 2.0 / 1e6,
        )
        self.assertAlmostEqual(cursor["output"], 100_000 * cursor_price["output"] * 1.5 / 1e6)

    def test_cursor_long_context_surcharge_boundary_and_cache_savings(self):
        from token_meter.models.catalog import GPT_56_SOL_PRICE_UPDATE_AT as at
        price, _ = meter.price_for("gpt-5.6-sol", "cursor", at=at)
        # Cache writes and cache reads count toward the context threshold.
        for context, multiplier in ((272_000, 1.0), (272_001, 2.0)):
            with self.subTest(context=context):
                usage = {
                    "input_tokens": context - 2_000,
                    "cache_creation_input_tokens": 1_000,
                    "cache_read_input_tokens": 1_000,
                    "output_tokens": 0,
                }
                cost = meter.cost_of(usage, "gpt-5.6-sol", "cursor", at=at)
                self.assertAlmostEqual(
                    cost["input"], (context - 2_000) * price["input"] * multiplier / 1e6,
                )
                self.assertAlmostEqual(
                    cost["cache_write"], 1_000 * price["cache_write"] * multiplier / 1e6,
                )
                savings = meter.cache_savings({}, "cursor", "gpt-5.6-sol", [{
                    "model": "gpt-5.6-sol", "ts": at,
                    "tokens": {"fresh_input": context - 2_000,
                               "cache_read": 1_000, "cache_write": 1_000},
                }])
                self.assertAlmostEqual(
                    savings,
                    1_000 * (price["input"] - price["cache_read"]) * multiplier / 1e6,
                )

    def test_custom_model_price_persists_and_drives_cost_calculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                result = meter.set_model_price(
                    "openai", "gpt-custom-1",
                    {"input": 1.25, "output": 6.5, "cache_write": 0, "cache_read": 0.2},
                )
                price, approximate = meter.price_for("gpt-custom-1", "codex")
                cost = meter.cost_of({
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "cache_creation_input_tokens": 1_000_000,
                    "cache_read_input_tokens": 1_000_000,
                }, "gpt-custom-1", "codex")
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(price, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertFalse(approximate)
        self.assertEqual(cost, {
            "input": 1.25, "output": 6.5, "cache_write": 0.0, "cache_read": 0.2,
        })
        self.assertIn("gpt-custom-1", saved["model_pricing"]["codex"])
        self.assertIsInstance(saved["model_pricing"]["codex"]["gpt-custom-1"], list)
        self.assertIsNotNone(
            saved["model_pricing"]["codex"]["gpt-custom-1"][0]["effective_from"]
        )

    def test_model_price_batch_persists_multiple_rows_with_one_atomic_write(self):
        changes = [
            {
                "provider": "claude", "model": "claude-opus-5",
                "prices": {"input": 6, "output": 30, "cache_write": 7.5, "cache_read": 0.6},
            },
            {
                "provider": "codex", "model": "gpt-5.4",
                "prices": {"input": 3, "output": 18, "cache_write": 0, "cache_read": 0.3},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original_write = meter.atomic_write_text
            with mock.patch.object(
                    meter, "atomic_write_text", side_effect=original_write) as write:
                result = meter.set_model_prices(
                    changes, path=str(path), effective_from=100,
                )
            saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["effective_scope"], "selected_time")
        self.assertEqual(len(result["changes"]), 2)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(
            saved["model_pricing"]["claude"]["claude-opus-5"][0]["effective_from"],
            100.0,
        )
        self.assertEqual(
            saved["model_pricing"]["codex"]["gpt-5.4"][0]["effective_from"],
            100.0,
        )

    def test_invalid_model_price_batch_is_rejected_without_any_write(self):
        changes = [
            {
                "provider": "codex", "model": "gpt-valid",
                "prices": {"input": 1, "output": 4, "cache_write": 0, "cache_read": 0.1},
            },
            {
                "provider": "codex", "model": "gpt-invalid",
                "prices": {"input": -1, "output": 4, "cache_write": 0, "cache_read": 0.1},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"keep": "unchanged"}\n')
            before = path.read_text()
            with mock.patch.object(meter, "atomic_write_text") as write:
                result = meter.set_model_prices(changes, path=str(path))
            after = path.read_text()
        self.assertFalse(result["ok"])
        self.assertEqual(before, after)
        write.assert_not_called()

    def test_model_price_batch_rejects_duplicate_provider_model_entries(self):
        price = {"input": 1, "output": 4, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            result = meter.set_model_prices([
                {"provider": "openai", "model": "gpt-duplicate", "prices": price},
                {"provider": "codex", "model": "gpt-duplicate", "prices": price},
            ], path=str(path))
        self.assertFalse(result["ok"])
        self.assertIn("duplicate", result["error"].lower())
        self.assertFalse(path.exists())

    def test_builtin_override_can_be_restored_without_losing_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"frustration_terms": ["damn"], "unrelated": {"keep": True}}))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                changed = meter.set_model_price(
                    "claude", "claude-sonnet-5",
                    {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4},
                    effective_from=100,
                )
                price, approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=150,
                )
                pricing = meter.model_pricing_settings()
                restored = meter.set_model_price(
                    "claude", "claude-sonnet-5", remove=True,
                    effective_from=200,
                )
                default_price, default_approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=200,
                )
                historical_price, _ = meter.price_for(
                    "claude-sonnet-5", "claude", at=150,
                )
                saved = json.loads(path.read_text())
        row = next(
            item for item in pricing["models"]
            if item["provider"] == "claude" and item["model"] == "claude-sonnet-5"
        )
        self.assertTrue(changed["ok"])
        self.assertEqual(price["input"], 4.0)
        self.assertFalse(approximate)
        self.assertEqual(row["source"], "override")
        self.assertTrue(restored["ok"])
        self.assertEqual(default_price, meter.CLAUDE_PRICE["claude-sonnet-5"])
        self.assertFalse(default_approximate)
        self.assertEqual(historical_price["input"], 4.0)
        self.assertEqual(saved["unrelated"], {"keep": True})
        self.assertNotIn("frustration_terms", saved)
        periods = saved["model_pricing"]["claude"]["claude-sonnet-5"]
        self.assertEqual(periods[0]["effective_from"], 100.0)
        self.assertEqual(periods[1], {"effective_from": 200.0, "use_builtin": True})

    def test_legacy_override_remains_all_history_and_migrates_on_next_edit(self):
        legacy = {"input": 7, "output": 21, "cache_write": 8, "cache_read": 0.7}
        updated = {"input": 4, "output": 12, "cache_write": 5, "cache_read": 0.4}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "model_pricing": {"codex": {"gpt-5.6-terra": legacy}},
                "language_signal_terms": {"positive": ["great"], "friction": ["damn"]},
                "unrelated": {"keep": True},
            }))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                before_migration, _ = meter.price_for("gpt-5.6-terra", "codex", at=1)
                result = meter.set_model_price(
                    "codex", "gpt-5.6-terra", updated, effective_from=500,
                )
                before_cutoff, _ = meter.price_for("gpt-5.6-terra", "codex", at=499)
                at_cutoff, _ = meter.price_for("gpt-5.6-terra", "codex", at=500)
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(before_migration, {key: float(value) for key, value in legacy.items()})
        self.assertEqual(before_cutoff, before_migration)
        self.assertEqual(at_cutoff, {key: float(value) for key, value in updated.items()})
        self.assertEqual(saved["unrelated"], {"keep": True})
        self.assertNotIn("language_signal_terms", saved)
        periods = saved["model_pricing"]["codex"]["gpt-5.6-terra"]
        self.assertIsNone(periods[0]["effective_from"])
        self.assertEqual(periods[1]["effective_from"], 500.0)

    def test_custom_model_can_be_retired_without_losing_historical_price(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                added = meter.set_model_price(
                    "codex", "gpt-retired", prices, effective_from=100,
                )
                retired = meter.set_model_price(
                    "codex", "gpt-retired", remove=True, effective_from=200,
                )
                old, old_approximate = meter.price_for("gpt-retired", "codex", at=150)
                current, current_approximate = meter.price_for("gpt-retired", "codex", at=200)
                pricing = meter.model_pricing_settings()
                saved = json.loads(path.read_text())
        row = next(item for item in pricing["models"] if item["model"] == "gpt-retired")
        self.assertTrue(added["ok"])
        self.assertTrue(retired["ok"])
        self.assertEqual(old, {key: float(value) for key, value in prices.items()})
        self.assertFalse(old_approximate)
        self.assertTrue(current_approximate)
        self.assertNotEqual(current, old)
        self.assertFalse(row["active"])
        self.assertEqual(row["source"], "retired")
        self.assertEqual(row["effective_from"], 200.0)
        self.assertEqual(row["prices"], old)
        self.assertEqual(pricing["retired_custom_models"], 1)
        self.assertEqual(
            saved["model_pricing"]["codex"]["gpt-retired"][-1],
            {"effective_from": 200.0, "inactive": True},
        )

    def test_apply_to_all_history_is_explicit_and_replaces_timeline(self):
        first = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        global_price = {"input": 2, "output": 6, "cache_write": 0, "cache_read": 0.2}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                meter.set_model_price("codex", "gpt-global", first, effective_from=100)
                result = meter.set_model_price(
                    "codex", "gpt-global", global_price, apply_to_all_history=True,
                )
                historical, approximate = meter.price_for("gpt-global", "codex", at=1)
                saved = json.loads(path.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(result["apply_to_all_history"])
        self.assertIsNone(result["effective_from"])
        self.assertFalse(approximate)
        self.assertEqual(historical, {key: float(value) for key, value in global_price.items()})
        self.assertEqual(len(saved["model_pricing"]["codex"]["gpt-global"]), 1)
        self.assertIsNone(saved["model_pricing"]["codex"]["gpt-global"][0]["effective_from"])

    def test_identical_current_price_does_not_add_redundant_period(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            first = meter.set_model_price(
                "codex", "gpt-idempotent", prices, path=str(path), effective_from=100,
            )
            repeated = meter.set_model_price(
                "codex", "gpt-idempotent", prices, path=str(path), effective_from=200,
            )
            saved = json.loads(path.read_text())
        self.assertTrue(first["changed"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(len(saved["model_pricing"]["codex"]["gpt-idempotent"]), 1)

    def test_session_cost_can_span_manual_price_periods(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        override = {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.4}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                meter.set_model_price(
                    "claude", "claude-sonnet-5", override, effective_from=100,
                )
                before = meter.cost_of(usage, "claude-sonnet-5", "claude", at=99)
                after = meter.cost_of(usage, "claude-sonnet-5", "claude", at=100)
        self.assertEqual(before["input"], 2.0)
        self.assertEqual(after["input"], 4.0)
        self.assertEqual(before["input"] + after["input"], 6.0)

    def test_oversized_manual_history_is_ignored_without_repricing_builtin(self):
        prices = {"input": 9, "output": 9, "cache_write": 9, "cache_read": 9}
        periods = [
            {"effective_from": index, "prices": prices}
            for index in range(meter.MAX_MODEL_PRICE_PERIODS + 1)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "model_pricing": {"claude": {"claude-sonnet-5": periods}},
            }))
            with mock.patch.object(meter, "TOKEN_METER_SETTINGS", str(path)):
                price, approximate = meter.price_for(
                    "claude-sonnet-5", "claude", at=meter.MAX_MODEL_PRICE_PERIODS + 2,
                )
                row = next(
                    item for item in meter.model_pricing_settings()["models"]
                    if item["provider"] == "claude" and item["model"] == "claude-sonnet-5"
                )
        self.assertEqual(price, meter.CLAUDE_PRICE["claude-sonnet-5"])
        self.assertFalse(approximate)
        self.assertEqual(row["periods"], 0)

    def test_model_pricing_http_action_forwards_bounded_scope_fields(self):
        source = Path(meter.IMPLEMENTATION_FILE).read_text()
        self.assertIn('if isinstance(payload.get("changes"), list)', source)
        self.assertIn('set_model_prices(', source)
        self.assertIn(
            'apply_to_all_history=payload.get("apply_to_all_history") is True',
            source,
        )
        self.assertIn('effective_from=payload.get("effective_from")', source)

    def test_future_or_conflicting_effective_scope_is_rejected(self):
        prices = {"input": 1, "output": 3, "cache_write": 0, "cache_read": 0.1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            future = meter.set_model_price(
                "codex", "gpt-future", prices, path=str(path),
                effective_from=meter.time.time() + 600,
            )
            conflicting = meter.set_model_price(
                "codex", "gpt-conflict", prices, path=str(path),
                effective_from=100, apply_to_all_history=True,
            )
        self.assertFalse(future["ok"])
        self.assertIn("future", future["error"])
        self.assertFalse(conflicting["ok"])
        self.assertIn("either", conflicting["error"])
        self.assertFalse(path.exists())

    def test_invalid_custom_model_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            bad_model = meter.set_model_price(
                "codex", "model with spaces",
                {"input": 1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
            bad_price = meter.set_model_price(
                "codex", "gpt-custom",
                {"input": -1, "output": 2, "cache_write": 0, "cache_read": 0},
                path=str(path),
            )
        self.assertFalse(bad_model["ok"])
        self.assertFalse(bad_price["ok"])
        self.assertFalse(path.exists())

    def test_longest_model_prefix_wins(self):
        price, approximate = meter.price_for("gpt-5.4-mini-2026-07-01", "codex")
        self.assertEqual(price, meter.OPENAI_PRICE["gpt-5.4-mini"])
        self.assertFalse(approximate)


class CodexLineageAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.trace_root = self.sessions / "2026" / "08" / "11"
        self.trace_root.mkdir(parents=True)
        self.index = self.root / "session_index.jsonl"
        self.index.write_text("")
        self.context = DiscoveryContext(home=str(self.root))
        self.adapter = CodexRuntimeAdapter(
            self.sessions,
            self.index,
            compatibility=meter._codex_compatibility(),
            default_model="gpt-5.6",
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_trace(self, name, rows, mtime):
        path = self.trace_root / f"rollout-{name}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def meta(physical_id, logical_id="task-1", **extra):
        return {
            "timestamp": "2026-08-11T00:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": physical_id,
                "session_id": logical_id,
                "cwd": "/work/project",
                **extra,
            },
        }

    @staticmethod
    def turn(timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "turn_context",
            "payload": {"model": "gpt-5.6", "cwd": "/work/project"},
        }

    @staticmethod
    def tool(name, call_id, timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": name,
                "call_id": call_id,
                "arguments": {},
            },
        }

    @staticmethod
    def tokens(input_tokens, output_tokens, total_input, total_output, timestamp):
        return {
            "timestamp": timestamp.replace("Z", ".000Z"),
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
                "total_token_usage": {
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                },
            }},
        }

    def root_and_child_sources(self):
        self.write_trace("root", [
            self.meta("root-1"),
            self.turn("2026-08-11T00:00:01Z"),
            {"timestamp": "2026-08-11T00:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T00:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:03Z"),
            {"timestamp": "2026-08-11T00:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
        ], 10)
        self.write_trace("child", [
            self.meta("child-1", forked_from_id="root-1"),
            self.meta("root-1"),
            self.turn("2026-08-11T01:00:01Z"),
            {"timestamp": "2026-08-11T01:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T01:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:03Z"),
            {"timestamp": "2026-08-11T01:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T02:00:01Z"),
            {"timestamp": "2026-08-11T02:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("child_tool", "child-call", "2026-08-11T02:00:02Z"),
            self.tokens(50, 5, 150, 15, "2026-08-11T02:00:03Z"),
        ], 20)
        return {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

    def root_child_and_grandchild_sources(self):
        self.root_and_child_sources()
        self.write_trace("grandchild", [
            self.meta("grandchild-1", forked_from_id="child-1"),
            self.turn("2026-08-11T03:00:01Z"),
            {"timestamp": "2026-08-11T03:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("inherited_tool", "inherited-call", "2026-08-11T03:00:02Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T03:00:03Z"),
            {"timestamp": "2026-08-11T03:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T04:00:01Z"),
            {"timestamp": "2026-08-11T04:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("child_tool", "child-call", "2026-08-11T04:00:02Z"),
            self.tokens(50, 5, 150, 15, "2026-08-11T04:00:03Z"),
            {"timestamp": "2026-08-11T04:00:04.000Z", "type": "event_msg",
             "payload": {"type": "task_complete", "duration_ms": 3000}},
            self.turn("2026-08-11T05:00:01Z"),
            {"timestamp": "2026-08-11T05:00:01.000Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            self.tool("grandchild_tool", "grandchild-call", "2026-08-11T05:00:02Z"),
            self.tokens(20, 2, 170, 17, "2026-08-11T05:00:03Z"),
        ], 30)
        return {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

    def test_legacy_detail_and_summary_use_the_same_corrected_child_rows(self):
        sources = self.root_and_child_sources()

        detail = self.adapter.recompute_legacy(sources["child-1"])
        summary = self.adapter.summarize_legacy(sources["child-1"])

        self.assertEqual(detail["total_tokens"], 55)
        self.assertEqual(summary["tokens"], 55)
        self.assertEqual(detail["total_tokens"], summary["tokens"])
        self.assertAlmostEqual(detail["total_cost"], summary["cost"])
        self.assertEqual(len(detail["executions"]), 1)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(
            [tool["name"] for tool in detail["executions"][0]["tools"]],
            ["child_tool"],
        )
        self.assertNotIn("complete", [event["kind"] for event in detail["trace"]])

    def test_live_and_completed_throughput_use_only_corrected_child_rows(self):
        sources = self.root_and_child_sources()

        summary = self.adapter.summarize_legacy(sources["child-1"])

        self.assertFalse(summary["throughput"]["available"])
        self.assertTrue(summary["live_throughput"]["available"])
        self.assertEqual(summary["live_throughput"]["completed_steps"], 1)
        self.assertEqual(summary["live_throughput"]["measured_output_tokens"], 5)
        self.assertEqual(summary["live_throughput"]["measured_seconds"], 2)
        self.assertEqual(summary["live_throughput"]["output_tps"], 2.5)

    def test_auto_review_child_inherits_its_linked_parent_model(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
        ], 10)
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }
        native = next(
            source for source in self.adapter.discover(self.context)
            if source.locator.value.endswith("rollout-auto-review.jsonl")
        )
        detail = self.adapter.recompute_legacy(sources["auto-review-1"])
        summary = self.adapter.summarize_legacy(sources["auto-review-1"])

        self.assertEqual(sources["auto-review-1"]["model"], "gpt-5.6")
        self.assertEqual(native.model_ref.model_id, "gpt-5.6")
        self.assertEqual(detail["primary_model"], "gpt-5.6")
        self.assertTrue(detail["availability"]["cost"])
        self.assertGreater(detail["total_cost"], 0)
        self.assertEqual(summary["primary_model"], "gpt-5.6")
        self.assertTrue(summary["availability"]["cost"])
        self.assertGreater(summary["cost"], 0)

    def test_auto_review_child_without_a_linked_parent_stays_unknown(self):
        self.write_trace("orphaned-auto-review", [
            self.meta("orphaned-auto-review-1", parent_thread_id="missing-parent"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        source = self.adapter.discover_legacy(self.context)[0]

        self.assertEqual(source["model"], "unknown-model")

    def test_auto_review_fork_without_a_desktop_parent_stays_unknown(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
        ], 10)
        self.write_trace("forked-auto-review", [
            self.meta("forked-auto-review-1", forked_from_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

        self.assertEqual(sources["forked-auto-review-1"]["model"], "unknown-model")

    def test_auto_review_child_with_a_cyclic_parent_stays_unknown(self):
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 10)
        self.write_trace("parent", [
            self.meta("parent-1", parent_thread_id="auto-review-1"),
            {
                "timestamp": "2026-08-11T01:00:02.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }

        self.assertEqual(sources["auto-review-1"]["model"], "unknown-model")

    def test_auto_review_child_deduplicates_an_inherited_parent_token_row(self):
        self.write_trace("parent", [
            self.meta("parent-1"),
            self.turn("2026-08-11T00:00:01Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:02Z"),
        ], 10)
        self.write_trace("auto-review", [
            self.meta("auto-review-1", parent_thread_id="parent-1"),
            {
                "timestamp": "2026-08-11T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"model": "codex-auto-review", "cwd": "/work/project"},
            },
            self.tokens(100, 10, 100, 10, "2026-08-11T01:00:02Z"),
        ], 20)

        sources = {
            source["physical_trace_id"]: source
            for source in self.adapter.discover_legacy(self.context)
        }
        summary = self.adapter.summarize_legacy(sources["auto-review-1"])

        self.assertEqual(summary["tokens"], 0)
        self.assertEqual(summary["turns"], 0)

    def test_history_scan_rows_match_cross_session_internal_rows(self):
        self.write_trace("root", [
            self.meta("root-1"),
            self.turn("2026-08-11T00:00:01Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:03Z"),
        ], 10)
        self.write_trace("root-resumed", [
            self.meta("root-1"),
            self.turn("2026-08-11T05:00:01Z"),
            self.tokens(40, 4, 140, 14, "2026-08-11T05:00:03Z"),
        ], 30)
        self.write_trace("spawned", [
            self.meta("spawned-1", parent_thread_id="root-1"),
            self.turn("2026-08-11T06:00:01Z"),
            self.tokens(20, 2, 20, 2, "2026-08-11T06:00:03Z"),
        ], 40)
        self.write_trace("empty", [self.meta("empty-1", logical_id="task-2")], 50)
        sources = list(self.adapter.discover_legacy(self.context))
        saved_xsess = dict(meter._xsess)
        saved_summaries = dict(meter._summary_cache)

        def key(row):
            return (row["session"], row["turns"], row["tokens"], round(row["cost"], 9))

        try:
            meter._xsess.update({"data": None, "at": 0, "sessions": [], "internal_rows": (),
                                 "project_model_stats": {}})
            meter._summary_cache.clear()
            with mock.patch.object(meter, "_codex_native_adapter", return_value=self.adapter):
                meter.cross_session(sources=sources)
                expected = [key(row) for row in meter._xsess["internal_rows"]]
            meter._xsess.update({"data": None, "at": 0, "internal_rows": ()})
            meter._summary_cache.clear()
            with mock.patch.object(meter, "_codex_native_adapter", return_value=self.adapter), \
                    mock.patch.object(meter, "all_session_sources", return_value=sources), \
                    mock.patch.dict(meter._history_scan, {"progress": None, "last_complete": None}):
                rows, coverage = meter.agent_history_rows(time.monotonic() + 10)
                self.assertTrue(meter._history_scan["progress"]["done"].wait(10))
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_xsess)
            meter._summary_cache.clear()
            meter._summary_cache.update(saved_summaries)

        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["sessions_total"], coverage["sessions_scanned"])
        self.assertEqual(len(expected), 3)
        self.assertEqual([key(row) for row in rows], expected)

    def test_all_session_rows_are_one_per_trace_file_with_exact_keys(self):
        self.write_trace("root", [
            self.meta("root-1"),
            self.turn("2026-08-11T00:00:01Z"),
            self.tokens(100, 10, 100, 10, "2026-08-11T00:00:03Z"),
        ], 10)
        self.write_trace("root-resumed", [
            self.meta("root-1"),
            self.turn("2026-08-11T05:00:01Z"),
            self.tokens(40, 4, 140, 14, "2026-08-11T05:00:03Z"),
        ], 30)
        self.write_trace("spawned", [
            self.meta("spawned-1", parent_thread_id="root-1"),
            self.turn("2026-08-11T06:00:01Z"),
            self.tokens(20, 2, 20, 2, "2026-08-11T06:00:03Z"),
        ], 40)
        sources = list(self.adapter.discover_legacy(self.context))
        self.assertEqual({source["id"] for source in sources}, {"task-1"})
        saved_xsess = dict(meter._xsess)
        saved_summaries = dict(meter._summary_cache)
        try:
            meter._xsess.update({
                "data": None, "at": 0, "sessions": [], "internal_rows": (),
                "project_model_stats": {},
            })
            meter._summary_cache.clear()
            with mock.patch.object(meter, "_codex_native_adapter", return_value=self.adapter):
                result = meter.cross_session(sources=sources)
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_xsess)
            meter._summary_cache.clear()
            meter._summary_cache.update(saved_summaries)

        rows = result["sessions"]
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["id"] for row in rows}, {"task-1"})
        keys = {row["session"] for row in rows}
        self.assertEqual(keys, {
            "rollout-root.jsonl", "rollout-root-resumed.jsonl", "rollout-spawned.jsonl",
        })
        self.assertEqual(sum(row["tokens"] for row in rows), result["total_tokens"])
        self.assertEqual(result["total_tokens"], 176)
        self.assertAlmostEqual(sum(row["cost"] for row in rows), result["total_cost"])
        by_key = {row["session"]: row for row in rows}
        self.assertTrue(by_key["rollout-spawned.jsonl"].get("subagent"))
        self.assertNotIn("subagent", by_key["rollout-root.jsonl"])
        for row in rows:
            resolved = meter.find_session(row["session"], sources=sources)
            self.assertEqual(resolved["path"], row["path"])

    def test_cross_session_daily_models_spend_and_budget_share_corrected_totals(self):
        sources = self.root_child_and_grandchild_sources()
        saved_xsess = dict(meter._xsess)
        saved_summaries = dict(meter._summary_cache)
        budget_now = datetime.datetime(2026, 8, 11, tzinfo=datetime.timezone.utc)
        original_budget_status = meter.monthly_budget_status
        try:
            meter._xsess.update({
                "data": None,
                "at": 0,
                "sessions": [],
                "internal_rows": (),
                "project_model_stats": {},
            })
            meter._summary_cache.clear()
            with mock.patch.object(
                meter, "_codex_native_adapter", return_value=self.adapter,
            ), mock.patch.object(
                meter,
                "monthly_budget_status",
                side_effect=lambda months, settings: original_budget_status(
                    months, settings, now=budget_now,
                ),
            ):
                result = meter.cross_session(sources=list(sources.values()))
        finally:
            meter._xsess.clear()
            meter._xsess.update(saved_xsess)
            meter._summary_cache.clear()
            meter._summary_cache.update(saved_summaries)

        self.assertEqual(result["total_tokens"], 187)
        self.assertEqual(result["total_executions"], 3)
        self.assertAlmostEqual(
            result["total_cost"],
            sum(session["cost"] for session in result["sessions"]),
        )
        self.assertEqual(sum(
            row["input_tokens"] + row["output_tokens"]
            for row in result["model_stats"]["models"]
        ), 187)
        self.assertEqual(sum(row["tokens"] for row in result["daily"]), 187)
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["daily"]),
            result["total_cost"],
        )
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["spend"]["days"]),
            result["total_cost"],
        )
        self.assertAlmostEqual(
            sum(row["cost"] for row in result["monthly"]),
            result["total_cost"],
        )
        self.assertEqual(result["budget"]["month"], "2026-08")
        self.assertAlmostEqual(result["budget"]["spend"], result["total_cost"])
        encoded = json.dumps(result)
        for private_field in (
            "physical_trace_id",
            "logical_session_id",
            "forked_from_id",
            "parent_thread_id",
            "lineage_parent_id",
            "lineage_revision",
        ):
            self.assertNotIn(private_field, encoded)

    def test_legacy_cache_signature_changes_with_lineage_revision(self):
        source = {
            "path": str(self.trace_root / "rollout-child.jsonl"),
            "mtime": 10,
            "title": "Child",
            "lineage_revision": ("unresolved", "parent-1"),
        }

        before = meter.source_revision_signature(source)
        after = meter.source_revision_signature({
            **source,
            "lineage_revision": ("resolved", "parent-1", "1", "2"),
        })

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
