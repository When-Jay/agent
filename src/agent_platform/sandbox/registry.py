"""Provider registry. The sandbox core depends on the provider interface only."""

from agent_platform.sandbox.errors import ProviderError
from agent_platform.sandbox.interface import SandboxProvider


class SandboxProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, SandboxProvider] = {}

    def register(self, name: str, provider: SandboxProvider) -> None:
        if name in self._providers:
            raise ProviderError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> SandboxProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderError(f"provider not registered: {name}")
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)
