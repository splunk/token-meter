"""Deterministic account-provider quota registry."""

from types import MappingProxyType


class QuotaRegistry:
    def __init__(self, adapters=()):
        ordered = tuple(adapters)
        by_account = {}
        by_public = {}
        for adapter in ordered:
            account_provider_id = adapter.account_provider_id
            public_id = adapter.public_id
            if account_provider_id in by_account:
                raise ValueError(
                    "duplicate quota account provider: {}".format(account_provider_id)
                )
            if public_id in by_public:
                raise ValueError("duplicate public quota provider: {}".format(public_id))
            by_account[account_provider_id] = adapter
            by_public[public_id] = adapter
        self._ordered = ordered
        self._by_account = MappingProxyType(by_account)
        self._by_public = MappingProxyType(by_public)

    @property
    def account_provider_ids(self):
        return tuple(adapter.account_provider_id for adapter in self._ordered)

    def get(self, account_provider_id):
        return self._by_account.get(account_provider_id)

    def require(self, account_provider_id):
        adapter = self.get(account_provider_id)
        if adapter is None:
            raise KeyError("Unknown quota account provider: {}".format(account_provider_id))
        return adapter

    def public_loaders(self):
        return {
            adapter.public_id: adapter.load
            for adapter in self._ordered
        }

    def public_ids(self):
        return tuple(adapter.public_id for adapter in self._ordered)

    def label_for_public(self, public_id):
        adapter = self._by_public.get(public_id)
        if adapter is None:
            return str(public_id or "Provider").title()
        return adapter.label
