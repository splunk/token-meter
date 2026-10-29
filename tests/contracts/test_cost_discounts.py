import copy
import json
import pickle
import time
import unittest

from token_meter.models import discounts as d


def rule(rule_id="d_aaaaaaaa", price_list="anthropic", channel="direct", percent=30,
         start=None, end=None, **extra):
    return {"id": rule_id, "price_list": price_list, "channel": channel,
            "percent": percent, "start": start, "end": end, **extra}


class NormalizeRuleTests(unittest.TestCase):
    def test_valid_rule_is_canonical(self):
        normalized = d.normalize_rule(rule(percent=12.345, start=100.0, end=200,
                                           label=" Team plan ", external_id="sync:a-1"))
        self.assertEqual(normalized, {
            "id": "d_aaaaaaaa", "external_id": "sync:a-1", "price_list": "anthropic",
            "channel": "direct", "percent": 12.35, "start": 100, "end": 200,
            "label": "Team plan", "source": "dashboard", "set_by": "Settings",
            "updated_at": None,
        })

    def test_percent_bounds(self):
        for bad in (0, 100, -1, 100.001, 0.001, 99.999, float("nan"), float("inf"), True, "30", None):
            with self.subTest(percent=bad):
                with self.assertRaises(ValueError):
                    d.normalize_rule(rule(percent=bad))
        self.assertEqual(d.normalize_rule(rule(percent=0.01))["percent"], 0.01)
        self.assertEqual(d.normalize_rule(rule(percent=99.99))["percent"], 99.99)

    def test_fixed_price_lists_require_any_channel(self):
        for price_list in ("cursor", "kiro", "opencode"):
            with self.subTest(price_list=price_list):
                with self.assertRaises(ValueError):
                    d.normalize_rule(rule(price_list=price_list, channel="direct"))
                self.assertEqual(
                    d.normalize_rule(rule(price_list=price_list, channel="any"))["channel"], "any")

    def test_enums_and_identity_fields(self):
        bad_rules = (
            rule(price_list="google"), rule(channel="other"), rule(channel="unknown"),
            rule(rule_id="x_aaaaaaaa"), rule(rule_id="d_short"), rule(rule_id="d_AAAAAAAA"),
            rule(external_id="has space"), rule(external_id="x" * 65),
            rule(label="x" * 61), rule(label="two\nlines"),
            rule(source="mdm"), rule(set_by="bad/name"), rule(set_by="x" * 41),
            rule(start=200, end=200), rule(start=300, end=200), rule(start=-1),
            rule(updated_at="yesterday"),
        )
        for raw in bad_rules:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    d.normalize_rule(raw)
        self.assertEqual(d.normalize_rule(rule(source="api"))["set_by"], "Another tool")

    def test_iso_dates_need_an_offset(self):
        self.assertEqual(
            d.normalize_rule(rule(start="2026-09-01T00:00:00-07:00"))["start"], 1788246000)
        self.assertEqual(
            d.normalize_rule(rule(start="2026-09-01T07:00:00Z"))["start"], 1788246000)
        for naive in ("2026-09-01", "2026-09-01T00:00:00", "September 1"):
            with self.subTest(value=naive):
                with self.assertRaises(ValueError):
                    d.normalize_rule(rule(start=naive))

    def test_client_names_are_sanitized_for_display(self):
        self.assertEqual(d.normalize_client_name("contract-sync"), "contract-sync")
        self.assertEqual(d.normalize_client_name("<script>"), "Another tool")
        self.assertEqual(d.normalize_client_name(""), "Another tool")
        self.assertEqual(d.normalize_client_name(None, "dashboard"), "Settings")


class NormalizeRulesTests(unittest.TestCase):
    def test_touching_ranges_are_allowed(self):
        rules = d.normalize_rules([
            rule("d_bbbbbbbb", start=200, end=None, percent=20),
            rule("d_aaaaaaaa", start=None, end=200, percent=10),
        ])
        self.assertEqual([item["id"] for item in rules], ["d_aaaaaaaa", "d_bbbbbbbb"])

    def test_overlap_is_rejected_in_plain_words(self):
        with self.assertRaises(ValueError) as caught:
            d.normalize_rules([
                rule("d_aaaaaaaa", start=100, end=300, percent=30),
                rule("d_bbbbbbbb", start=299, end=None, percent=20),
            ])
        self.assertEqual(
            str(caught.exception),
            "Anthropic billed by the provider already has 30% for overlapping dates.")

    def test_overlap_is_scoped_by_price_list_and_channel(self):
        rules = d.normalize_rules([
            rule("d_aaaaaaaa", channel="direct"),
            rule("d_bbbbbbbb", channel="any"),
            rule("d_cccccccc", channel="bedrock"),
            rule("d_dddddddd", price_list="openai", channel="direct"),
        ])
        self.assertEqual(len(rules), 4)

    def test_unique_ids_and_external_ids(self):
        with self.assertRaises(ValueError):
            d.normalize_rules([rule("d_aaaaaaaa"), rule("d_aaaaaaaa", channel="any")])
        with self.assertRaises(ValueError):
            d.normalize_rules([
                rule("d_aaaaaaaa", external_id="sync:x"),
                rule("d_bbbbbbbb", channel="any", external_id="sync:x"),
            ])

    def test_bounds(self):
        per_scope = [
            rule("d_%08d" % index, start=index * 10, end=index * 10 + 10)
            for index in range(d.MAX_RULES_PER_SCOPE + 1)
        ]
        with self.assertRaises(ValueError):
            d.normalize_rules(per_scope)
        self.assertEqual(len(d.normalize_rules(per_scope[:-1])), d.MAX_RULES_PER_SCOPE)
        with self.assertRaises(ValueError):
            d.normalize_rules([rule("d_%08d" % index) for index in range(d.MAX_RULES + 1)])

    def test_lenient_load_keeps_valid_rules(self):
        rules, invalid = d.normalize_rules_lenient([
            rule("d_aaaaaaaa", start=0, end=100),
            {"id": "broken"},
            rule("d_bbbbbbbb", start=50, end=150),  # clashes with the first
            rule("d_cccccccc", start=100, end=None),
        ])
        self.assertEqual([item["id"] for item in rules], ["d_aaaaaaaa", "d_cccccccc"])
        self.assertEqual(invalid, 2)
        self.assertEqual(d.normalize_rules_lenient(None), ([], 0))
        self.assertEqual(d.normalize_rules_lenient("x"), ([], 1))


class LookupTests(unittest.TestCase):
    def test_interval_boundaries_are_half_open(self):
        index = d.build_index(d.normalize_rules([rule(start=100, end=200, percent=25)]))
        self.assertIsNone(d.discount_for(index, "anthropic", "direct", 99.999))
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 100), (0.25, "d_aaaaaaaa"))
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 199.999)[0], 0.25)
        self.assertIsNone(d.discount_for(index, "anthropic", "direct", 200))

    def test_open_ends(self):
        index = d.build_index(d.normalize_rules([
            rule("d_aaaaaaaa", start=None, end=100, percent=10),
            rule("d_bbbbbbbb", start=100, end=None, percent=20),
        ]))
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 0)[0], 0.10)
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 100)[0], 0.20)
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 10 ** 12)[0], 0.20)
        self.assertEqual(d.discount_for(index, "anthropic", "direct", None)[0], 0.20)

    def test_exact_channel_beats_any_then_falls_back(self):
        index = d.build_index(d.normalize_rules([
            rule("d_aaaaaaaa", channel="bedrock", start=100, end=200, percent=40),
            rule("d_bbbbbbbb", channel="any", percent=10),
        ]))
        self.assertEqual(d.discount_for(index, "anthropic", "bedrock", 150), (0.40, "d_aaaaaaaa"))
        self.assertEqual(d.discount_for(index, "anthropic", "bedrock", 250), (0.10, "d_bbbbbbbb"))
        self.assertEqual(d.discount_for(index, "anthropic", "direct", 150), (0.10, "d_bbbbbbbb"))
        self.assertEqual(d.discount_for(index, "anthropic", "unknown", 150), (0.10, "d_bbbbbbbb"))
        self.assertEqual(d.discount_for(index, "anthropic", "other", 150), (0.10, "d_bbbbbbbb"))

    def test_vendor_rules_never_reach_other_price_lists(self):
        index = d.build_index(d.normalize_rules([
            rule("d_aaaaaaaa", channel="any"), rule("d_bbbbbbbb", price_list="openai", channel="any"),
        ]))
        for price_list in ("cursor", "kiro", "opencode"):
            self.assertIsNone(d.discount_for(index, price_list, "direct", 1))
        self.assertIsNone(d.discount_for({}, "anthropic", "direct", 1))

    def test_bisect_over_full_scope(self):
        rules = [
            rule("d_%08d" % index, start=index * 100, end=index * 100 + 50,
                 percent=index + 1)
            for index in range(d.MAX_RULES_PER_SCOPE)
        ]
        index = d.build_index(d.normalize_rules(list(reversed(rules))))
        for position in range(d.MAX_RULES_PER_SCOPE):
            self.assertEqual(
                d.discount_for(index, "anthropic", "direct", position * 100 + 49.5),
                ((position + 1) / 100.0, "d_%08d" % position))
            self.assertIsNone(d.discount_for(index, "anthropic", "direct", position * 100 + 50))
        started = time.perf_counter()
        for _ in range(20000):
            d.discount_for(index, "anthropic", "direct", 3149)
        self.assertLess(time.perf_counter() - started, 1.0)


class RevisionAndBreakdownTests(unittest.TestCase):
    def test_revision_is_stable_and_sensitive(self):
        rules = d.normalize_rules([rule()])
        first = d.revision(rules, {"claude_code": "auto"})
        self.assertRegex(first, r"^r_[0-9a-f]{16}$")
        self.assertEqual(first, d.revision(copy.deepcopy(rules), None))
        self.assertNotEqual(first, d.revision(rules, {"claude_code": "bedrock"}))
        self.assertNotEqual(first, d.revision(d.normalize_rules([rule(percent=31)])))
        self.assertEqual(d.revision([], None), d.revision(None, {}))

    def test_runtime_channels_normalization(self):
        self.assertEqual(d.normalize_runtime_channels(None),
                         {"claude_code": "auto", "claude_desktop": "auto"})
        self.assertEqual(d.normalize_runtime_channels({"claude_code": "BEDROCK", "x": 1}),
                         {"claude_code": "bedrock", "claude_desktop": "auto"})
        with self.assertRaises(ValueError):
            d.normalize_runtime_channels({"claude_code": "azure"}, strict=True)

    def test_cost_breakdown_is_a_dict_that_survives_copies(self):
        breakdown = d.CostBreakdown({"input": 0.7, "output": 0.0}, discount_usd=0.3)
        self.assertEqual(breakdown, {"input": 0.7, "output": 0.0})
        self.assertAlmostEqual(breakdown.list_usd, 1.0)
        for clone in (copy.deepcopy(breakdown), pickle.loads(pickle.dumps(breakdown))):
            self.assertEqual(clone, breakdown)
            self.assertAlmostEqual(clone.discount_usd, 0.3)
        self.assertEqual(json.loads(json.dumps(breakdown)), {"input": 0.7, "output": 0.0})


class ChannelMappingTests(unittest.TestCase):
    def test_provider_names(self):
        cases = {
            "amazon-bedrock": "bedrock", "aws-bedrock": "bedrock", "Bedrock": "bedrock",
            "google-vertex": "vertex", "google-vertex-anthropic": "vertex",
            "azure": "azure", "azure-openai": "azure",
            "anthropic": "direct", "openai": "direct",
            "openrouter": "other", "opencode": "other", "ollama": "other",
            "": "unknown", None: "unknown",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(d.channel_from_provider_name(name), expected)

    def test_claude_request_id_shapes(self):
        self.assertEqual(d.claude_channel_from_request_id("req_011CXyZ"), "direct")
        self.assertEqual(
            d.claude_channel_from_request_id("0b7c0a8e-3f7d-4c55-9b1e-6c2f6d1c9a10"), "bedrock")
        for value in (None, "", "msg_123", "0b7c0a8e3f7d4c559b1e6c2f6d1c9a10", 42):
            self.assertIsNone(d.claude_channel_from_request_id(value))

    def test_vendor_price_list(self):
        self.assertEqual(d.vendor_price_list("x", "anthropic"), "anthropic")
        self.assertEqual(d.vendor_price_list("claude-opus-4-5", "amazon-bedrock"), "anthropic")
        self.assertEqual(d.vendor_price_list("global.anthropic.claude-sonnet-4-5"), "anthropic")
        self.assertEqual(d.vendor_price_list("gpt-5.4", "azure"), "openai")
        self.assertEqual(d.vendor_price_list("openai/gpt-oss-120b", "openrouter"), "openai")
        self.assertIsNone(d.vendor_price_list("amazon.nova-pro-v1", "amazon-bedrock"))
        self.assertIsNone(d.vendor_price_list("gemini-2.5-pro", "google"))


if __name__ == "__main__":
    unittest.main()
