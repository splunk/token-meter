import unittest

from token_meter.quotas.base import CallableQuotaAdapter
from token_meter.quotas.registry import QuotaRegistry


class QuotaRegistryTests(unittest.TestCase):
    def adapter(self, account_provider_id, public_id=None):
        return CallableQuotaAdapter(
            account_provider_id=account_provider_id,
            public_id=public_id or account_provider_id,
            label=account_provider_id.title(),
            loader=lambda now=None: {"status": "unavailable", "windows": []},
        )

    def test_registry_rejects_duplicate_account_provider_ids(self):
        with self.assertRaises(ValueError):
            QuotaRegistry((self.adapter("openai"), self.adapter("openai")))

    def test_runtime_without_quota_adapter_remains_a_supported_local_runtime(self):
        registry = QuotaRegistry((self.adapter("openai"),))

        self.assertIsNone(registry.get("no-account-provider"))
        self.assertEqual(registry.account_provider_ids, ("openai",))

    def test_public_loader_mapping_is_explicit_and_ordered(self):
        anthropic = self.adapter("anthropic", "claude")
        openai = self.adapter("openai", "codex")
        registry = QuotaRegistry((anthropic, openai))

        loaders = registry.public_loaders()

        self.assertEqual(tuple(loaders), ("claude", "codex"))
        self.assertIs(loaders["claude"].__self__, anthropic)
        self.assertIs(loaders["codex"].__self__, openai)

    def test_public_label_lookup_does_not_require_account_provider_id(self):
        registry = QuotaRegistry((self.adapter("xai", "grok"),))
        self.assertEqual(registry.public_ids(), ("grok",))
        self.assertEqual(registry.label_for_public("grok"), "Xai")
        self.assertEqual(registry.label_for_public("missing"), "Missing")


if __name__ == "__main__":
    unittest.main()
