"""Secret-free model metadata cache abstraction."""

from __future__ import annotations

import threading
from typing import Protocol

from jarvis_x.provider_setup.models import DiscoveredModel, ModelCacheEntry, ProviderId


class ModelCache(Protocol):
    def save(
        self,
        provider: ProviderId,
        models: tuple[DiscoveredModel, ...],
    ) -> ModelCacheEntry: ...

    def get(self, provider: ProviderId) -> ModelCacheEntry | None: ...

    def delete(self, provider: ProviderId) -> None: ...


class InMemoryModelCache:
    """Process-local cache; Android may persist this non-secret metadata safely."""

    def __init__(self) -> None:
        self._entries: dict[ProviderId, ModelCacheEntry] = {}
        self._lock = threading.Lock()

    def save(
        self,
        provider: ProviderId,
        models: tuple[DiscoveredModel, ...],
    ) -> ModelCacheEntry:
        entry = ModelCacheEntry(provider=provider, models=models)
        with self._lock:
            self._entries[provider] = entry
        return entry

    def get(self, provider: ProviderId) -> ModelCacheEntry | None:
        with self._lock:
            return self._entries.get(provider)

    def delete(self, provider: ProviderId) -> None:
        with self._lock:
            self._entries.pop(provider, None)
