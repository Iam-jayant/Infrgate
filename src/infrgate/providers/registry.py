"""
Provider Registry.

Manages registered provider adapter instances.
"""

from typing import Dict
from infrgate.providers.base import ProviderAdapter


class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        """Register a provider adapter."""
        self._providers[adapter.provider_name] = adapter

    def get(self, name: str) -> ProviderAdapter | None:
        """Get a provider adapter by name."""
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())
