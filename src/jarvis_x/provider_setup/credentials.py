"""Credential-store contract with zeroizable process-memory test support."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from jarvis_x.provider_setup.models import ProviderId, utc_now


def zeroize(value: bytearray) -> None:
    """Best-effort overwrite of mutable secret material."""

    for index in range(len(value)):
        value[index] = 0


def mask_api_key(value: str | bytes | bytearray) -> str:
    """Return a stable masked label without ever returning a complete key."""

    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if not raw:
        return ""
    if len(raw) <= 8:
        return "•" * len(raw)
    suffix = raw[-4:].decode("utf-8", errors="replace")
    return f"••••••••{suffix}"


class SecretBuffer:
    """Mutable secret container whose repr and string form are always redacted."""

    __slots__ = ("_value", "_cleared")

    def __init__(self, value: bytes | bytearray) -> None:
        self._value = bytearray(value)
        self._cleared = False
        if not self._value or not any(byte not in b" \t\r\n" for byte in self._value):
            self.clear()
            raise ValueError("API key must not be blank.")

    @contextmanager
    def copy_bytes(self) -> Iterator[bytearray]:
        if self._cleared:
            raise RuntimeError("Secret buffer has been cleared.")
        copied = bytearray(self._value)
        try:
            yield copied
        finally:
            zeroize(copied)

    def masked(self) -> str:
        if self._cleared:
            return ""
        return mask_api_key(self._value)

    def clear(self) -> None:
        if not getattr(self, "_cleared", True):
            zeroize(self._value)
            self._cleared = True

    @property
    def is_cleared(self) -> bool:
        return self._cleared

    def __repr__(self) -> str:
        return "SecretBuffer(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __del__(self) -> None:
        self.clear()


@dataclass(frozen=True, slots=True)
class CredentialMetadata:
    provider: ProviderId
    configured: bool
    masked_key: str | None = None
    updated_at: str | None = None


class CredentialStore(Protocol):
    """Platform adapter contract; Android must back this with Android Keystore."""

    def save(self, provider: ProviderId, secret: SecretBuffer) -> CredentialMetadata: ...

    def load(self, provider: ProviderId) -> SecretBuffer | None: ...

    def delete(self, provider: ProviderId) -> bool: ...

    def metadata(self, provider: ProviderId) -> CredentialMetadata: ...


class InMemoryCredentialStore:
    """Test-only store; it is not a production substitute for Android Keystore."""

    def __init__(self) -> None:
        self._values: dict[ProviderId, bytearray] = {}
        self._updated_at: dict[ProviderId, str] = {}
        self._lock = threading.Lock()

    def save(self, provider: ProviderId, secret: SecretBuffer) -> CredentialMetadata:
        with secret.copy_bytes() as copied:
            replacement = bytearray(copied)
        with self._lock:
            previous = self._values.get(provider)
            if previous is not None:
                zeroize(previous)
            self._values[provider] = replacement
            self._updated_at[provider] = utc_now()
            return self._metadata_locked(provider)

    def load(self, provider: ProviderId) -> SecretBuffer | None:
        with self._lock:
            value = self._values.get(provider)
            return SecretBuffer(value) if value is not None else None

    def delete(self, provider: ProviderId) -> bool:
        with self._lock:
            value = self._values.pop(provider, None)
            self._updated_at.pop(provider, None)
            if value is None:
                return False
            zeroize(value)
            return True

    def metadata(self, provider: ProviderId) -> CredentialMetadata:
        with self._lock:
            return self._metadata_locked(provider)

    def _metadata_locked(self, provider: ProviderId) -> CredentialMetadata:
        value = self._values.get(provider)
        if value is None:
            return CredentialMetadata(provider=provider, configured=False)
        return CredentialMetadata(
            provider=provider,
            configured=True,
            masked_key=mask_api_key(value),
            updated_at=self._updated_at[provider],
        )
