import unittest
from datetime import datetime, timezone

from token_meter.contracts import EvidenceBasis, ModelRef, PriceQuery
from token_meter.models.catalog import (
    GPT_56_PRICE_UPDATE_AT,
    GPT_56_SOL_PRICE_UPDATE_AT,
    canonical_model_provider,
)
from token_meter.models.pricing import quote_for


class ModelPricingBoundaryTests(unittest.TestCase):
    def test_legacy_runtime_names_map_explicitly_to_model_providers(self):
        self.assertEqual(canonical_model_provider("claude"), "anthropic")
        self.assertEqual(canonical_model_provider("codex"), "openai")
        self.assertEqual(canonical_model_provider("openai"), "openai")
        self.assertIsNone(canonical_model_provider("kiro"))

    def test_model_provider_pricing_is_independent_of_runtime_identity(self):
        runtime_id = "kiro"
        query = PriceQuery(ModelRef("anthropic", "claude-sonnet-5"))

        quote = quote_for(query)

        self.assertEqual(runtime_id, "kiro")
        self.assertEqual(quote.model.provider_id, "anthropic")
        self.assertEqual(quote.input_per_million, 2.0)
        self.assertEqual(quote.output_per_million, 10.0)
        self.assertEqual(quote.basis, EvidenceBasis.ESTIMATED)
        self.assertEqual(quote.matched_rule, "claude-sonnet-5")

    def test_fable_and_mythos_5_1_use_their_published_cache_read_price(self):
        for model_id in ("claude-fable-5-1", "claude-mythos-5-1"):
            with self.subTest(model_id=model_id):
                quote = quote_for(PriceQuery(ModelRef("anthropic", model_id)))

                self.assertTrue(quote.available)
                self.assertEqual(quote.input_per_million, 10.0)
                self.assertEqual(quote.output_per_million, 50.0)
                self.assertEqual(quote.cache_write_per_million, 12.50)
                self.assertEqual(quote.cache_read_per_million, 0.25)
                self.assertEqual(quote.matched_rule, model_id)

    def test_unknown_provider_is_explicitly_unavailable_without_cross_fallback(self):
        unknown_provider = quote_for(PriceQuery(ModelRef("unknown", "gpt-5.6")))
        wrong_provider = quote_for(PriceQuery(ModelRef("anthropic", "gpt-5.6")))

        for quote in (unknown_provider, wrong_provider):
            self.assertEqual(quote.basis, EvidenceBasis.UNAVAILABLE)
            self.assertIsNone(quote.input_per_million)
            self.assertIsNone(quote.output_per_million)
            self.assertIsNone(quote.matched_rule)

    def test_price_history_switches_at_the_existing_exact_boundary(self):
        before = datetime.fromtimestamp(GPT_56_PRICE_UPDATE_AT - 1, timezone.utc)
        boundary = datetime.fromtimestamp(GPT_56_PRICE_UPDATE_AT, timezone.utc)
        model = ModelRef("openai", "gpt-5.6-terra")

        old_quote = quote_for(PriceQuery(model, before))
        current_quote = quote_for(PriceQuery(model, boundary))

        self.assertEqual(old_quote.input_per_million, 5.0)
        self.assertEqual(old_quote.output_per_million, 30.0)
        self.assertEqual(current_quote.input_per_million, 2.0)
        self.assertEqual(current_quote.output_per_million, 12.0)

    def test_sol_price_history_switches_at_the_august_21_boundary(self):
        before = datetime.fromtimestamp(GPT_56_SOL_PRICE_UPDATE_AT - 1, timezone.utc)
        boundary = datetime.fromtimestamp(GPT_56_SOL_PRICE_UPDATE_AT, timezone.utc)

        for model_id in ("gpt-5.6", "gpt-5.6-sol"):
            with self.subTest(model_id=model_id):
                old_quote = quote_for(PriceQuery(ModelRef("openai", model_id), before))
                new_quote = quote_for(PriceQuery(ModelRef("openai", model_id), boundary))
                current_quote = quote_for(PriceQuery(ModelRef("openai", model_id)))

                self.assertEqual(
                    (
                        old_quote.input_per_million,
                        old_quote.cache_read_per_million,
                        old_quote.cache_write_per_million,
                        old_quote.output_per_million,
                    ),
                    (5.0, 0.5, 6.25, 30.0),
                )
                for quote in (new_quote, current_quote):
                    self.assertEqual(
                        (
                            quote.input_per_million,
                            quote.cache_read_per_million,
                            quote.cache_write_per_million,
                            quote.output_per_million,
                        ),
                        (4.0, 0.4, 5.0, 20.0),
                    )

    def test_longest_catalog_prefix_is_reported_as_the_matching_rule(self):
        quote = quote_for(
            PriceQuery(ModelRef("openai", "gpt-5.4-mini-2026-07-01"))
        )

        self.assertEqual(quote.input_per_million, 0.75)
        self.assertEqual(quote.matched_rule, "gpt-5.4-mini")

    def test_provider_qualified_model_aliases_resolve_inside_the_provider_catalog(self):
        aliases = (
            "anthropic.claude-haiku-4-5-20251001-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/"
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )

        for model_id in aliases:
            with self.subTest(model_id=model_id):
                quote = quote_for(PriceQuery(ModelRef("anthropic", model_id)))
                self.assertEqual(quote.matched_rule, "claude-haiku-4-5")
                self.assertEqual(quote.input_per_million, 1.0)
                self.assertEqual(quote.output_per_million, 5.0)

        openai_quote = quote_for(PriceQuery(ModelRef(
            "openai", "openai.gpt-5.4-mini-2026-07-01",
        )))
        self.assertEqual(openai_quote.matched_rule, "gpt-5.4-mini")
        self.assertEqual(openai_quote.input_per_million, 0.75)

        application_profile = quote_for(PriceQuery(ModelRef(
            "anthropic",
            "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/"
            "anthropic.claude-haiku-4-5-20251001-v1:0",
        )))
        self.assertFalse(application_profile.available)
        self.assertIsNone(application_profile.matched_rule)

    def test_provider_alias_matching_does_not_cross_provider_boundaries(self):
        quote = quote_for(PriceQuery(
            ModelRef("anthropic", "openai.claude-haiku-4-5-20251001")
        ))

        self.assertFalse(quote.available)
        self.assertEqual(quote.basis, EvidenceBasis.UNAVAILABLE)
        self.assertIsNone(quote.matched_rule)

    def test_prefix_matching_requires_a_version_or_namespace_boundary(self):
        for model_id in (
            "claude-haiku-4-5ish",
            "anthropic.claude-haiku-4-5ish",
            "claude-haiku-4-5-ish",
            "openai.anthropic.claude-haiku-4-5-20251001-v1:0",
            "evil/anthropic/claude-haiku-4-5-20251001-v1:0",
        ):
            with self.subTest(model_id=model_id):
                quote = quote_for(PriceQuery(ModelRef("anthropic", model_id)))
                self.assertFalse(quote.available)
                self.assertIsNone(quote.matched_rule)

        unknown_openai_variant = quote_for(PriceQuery(
            ModelRef("openai", "gpt-5.6-mars")
        ))
        self.assertFalse(unknown_openai_variant.available)
        self.assertIsNone(unknown_openai_variant.matched_rule)

        short_numeric_variant = quote_for(PriceQuery(
            ModelRef("openai", "gpt-5.6-99")
        ))
        self.assertFalse(short_numeric_variant.available)
        self.assertIsNone(short_numeric_variant.matched_rule)

    def test_cursor_variant_is_canonicalized_inside_the_model_boundary(self):
        quote = quote_for(
            PriceQuery(ModelRef("cursor", "Composer 2.5", variant="fast"))
        )

        self.assertEqual(quote.input_per_million, 3.0)
        self.assertEqual(quote.output_per_million, 15.0)
        self.assertEqual(quote.matched_rule, "composer-2.5-fast")

        qualified = quote_for(PriceQuery(
            ModelRef("cursor", "cursor.composer-2.5", variant="fast")
        ))
        self.assertTrue(qualified.available)
        self.assertEqual(qualified.matched_rule, "composer-2.5-fast")

        for model_id in ("composer-2.5ish", "composer-2.5-future"):
            with self.subTest(model_id=model_id):
                unknown = quote_for(PriceQuery(
                    ModelRef("cursor", model_id, variant="fast")
                ))
                self.assertFalse(unknown.available)
                self.assertIsNone(unknown.matched_rule)


if __name__ == "__main__":
    unittest.main()
