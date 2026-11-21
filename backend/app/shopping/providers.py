from functools import lru_cache

from app.shopping.errors import ProviderNotConnected
from app.shopping.models import ProviderName
from app.shopping.ports import QuoteProvider


NOT_CONNECTED_MESSAGE = "Basket pricing is not connected for this store yet."


class ProviderRegistry:
    """Explicit registry for provider capabilities verified against live schemas."""

    def __init__(self) -> None:
        self._providers: dict[ProviderName, QuoteProvider] = {}

    def register(self, provider: QuoteProvider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: ProviderName) -> None:
        self._providers.pop(name, None)

    def get(self, name: ProviderName) -> QuoteProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderNotConnected(NOT_CONNECTED_MESSAGE)
        return provider


@lru_cache
def get_provider_registry() -> ProviderRegistry:
    return ProviderRegistry()


def get_quote_provider(name: ProviderName) -> QuoteProvider:
    return get_provider_registry().get(name)
