"""App-facing provider settings service with no secret-returning methods."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Mapping

from jarvis_x.provider_setup.cache import InMemoryModelCache, ModelCache
from jarvis_x.provider_setup.control_plane import ControlPlaneError, ProviderControlPlane
from jarvis_x.provider_setup.credentials import (
    CredentialMetadata,
    CredentialStore,
    SecretBuffer,
    zeroize,
)
from jarvis_x.provider_setup.models import (
    ChargeRisk,
    DiscoveryResult,
    DiscoveryStatus,
    ModelCacheEntry,
    ProviderId,
    VerificationOperation,
    VerificationResult,
    VerificationStatus,
)
from jarvis_x.providers.agentrouter import PRIMARY_MODEL_ID

_MAX_DISCOVERY_PAGES = 100


@dataclass(frozen=True, slots=True)
class ManualModelConfiguration:
    provider: ProviderId
    model_id: str
    availability_verified: bool = False


@dataclass(frozen=True, slots=True)
class ProviderSettingsSnapshot:
    """Safe DTO for an Android UI or a private in-process bridge."""

    provider: ProviderId
    credential: CredentialMetadata
    verification: VerificationResult
    model_cache: ModelCacheEntry | None
    manual_model: ManualModelConfiguration | None


class ProviderSettingsService:
    """Own credential lifecycle, verification, discovery, and cache invalidation."""

    def __init__(
        self,
        *,
        credential_store: CredentialStore,
        control_planes: Mapping[ProviderId, ProviderControlPlane],
        model_cache: ModelCache | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._credential_store = credential_store
        self._control_planes = dict(control_planes)
        for provider, control_plane in self._control_planes.items():
            if control_plane.provider is not provider:
                raise ValueError("Control-plane provider mapping does not match.")
            if (
                provider is ProviderId.AGENTROUTER
                and control_plane.supports_metadata_verification
            ):
                raise ValueError(
                    "AgentRouter metadata verification is unsupported until an official endpoint is documented."
                )
        self._model_cache = model_cache or InMemoryModelCache()
        self._logger = logger or logging.getLogger("jarvis_x.provider_setup")
        self._verification: dict[ProviderId, VerificationResult] = {}
        self._manual_models: dict[ProviderId, ManualModelConfiguration] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _not_configured(provider: ProviderId) -> VerificationResult:
        return VerificationResult(
            provider=provider,
            status=VerificationStatus.NOT_CONFIGURED,
            operation=VerificationOperation.NONE,
            charge_risk=ChargeRisk.NOT_APPLICABLE,
            inference_used=False,
            safe_message="API key is not configured.",
        )

    @staticmethod
    def _not_verified(provider: ProviderId) -> VerificationResult:
        return VerificationResult(
            provider=provider,
            status=VerificationStatus.NOT_VERIFIED,
            operation=VerificationOperation.NONE,
            charge_risk=ChargeRisk.NOT_APPLICABLE,
            inference_used=False,
            safe_message="API key is saved but has not been verified.",
        )

    def save_api_key(
        self,
        provider: ProviderId,
        key_material: bytearray,
    ) -> CredentialMetadata:
        """Save/update a key and overwrite the caller-owned mutable input."""

        if not isinstance(key_material, bytearray):
            raise TypeError("API key input must be a mutable bytearray.")
        secret: SecretBuffer | None = None
        try:
            secret = SecretBuffer(key_material)
            metadata = self._credential_store.save(provider, secret)
        finally:
            if secret is not None:
                secret.clear()
            zeroize(key_material)

        with self._lock:
            self._verification[provider] = self._not_verified(provider)
            self._model_cache.delete(provider)
        self._logger.info("Provider credential saved: provider=%s", provider.value)
        return metadata

    def delete_api_key(self, provider: ProviderId) -> bool:
        deleted = self._credential_store.delete(provider)
        with self._lock:
            self._verification[provider] = self._not_configured(provider)
            self._model_cache.delete(provider)
        self._logger.info("Provider credential deleted: provider=%s", provider.value)
        return deleted

    def set_manual_model(
        self,
        provider: ProviderId,
        model_id: str,
    ) -> ManualModelConfiguration:
        normalized = model_id.strip()
        if not normalized:
            raise ValueError("Manual model ID must not be blank.")
        if provider is ProviderId.AGENTROUTER and normalized != PRIMARY_MODEL_ID:
            raise ValueError(f"AgentRouter model must remain {PRIMARY_MODEL_ID}.")
        configuration = ManualModelConfiguration(provider=provider, model_id=normalized)
        with self._lock:
            self._manual_models[provider] = configuration
        return configuration

    def clear_manual_model(self, provider: ProviderId) -> None:
        with self._lock:
            self._manual_models.pop(provider, None)

    def _control_plane(self, provider: ProviderId) -> ProviderControlPlane | None:
        return self._control_planes.get(provider)

    def _fetch_all_models(
        self,
        control_plane: ProviderControlPlane,
        secret: SecretBuffer,
    ) -> tuple:
        models_by_id = {}
        page_token: str | None = None
        seen_tokens: set[str] = set()

        for _page_number in range(_MAX_DISCOVERY_PAGES):
            page = control_plane.list_models(secret, page_token=page_token)
            for model in page.models:
                if model.provider is not control_plane.provider:
                    raise ControlPlaneError(
                        VerificationStatus.INVALID_RESPONSE,
                        "Provider model metadata used an unexpected provider ID.",
                    )
                models_by_id.setdefault(model.model_id, model)
            next_token = page.next_page_token
            if next_token is None:
                return tuple(models_by_id.values())
            if next_token in seen_tokens:
                raise ControlPlaneError(
                    VerificationStatus.INVALID_RESPONSE,
                    "Provider model discovery repeated a pagination token.",
                )
            seen_tokens.add(next_token)
            page_token = next_token

        raise ControlPlaneError(
            VerificationStatus.INVALID_RESPONSE,
            "Provider model discovery exceeded the safe pagination limit.",
        )

    def _unsupported_result(self, provider: ProviderId) -> VerificationResult:
        return VerificationResult(
            provider=provider,
            status=VerificationStatus.UNSUPPORTED_ENDPOINT,
            operation=VerificationOperation.UNSUPPORTED,
            charge_risk=ChargeRisk.NOT_APPLICABLE,
            inference_used=False,
            safe_message=(
                "This provider does not document a safe non-inference verification endpoint."
            ),
        )

    def verify_and_discover(self, provider: ProviderId) -> VerificationResult:
        """Verify by metadata listing and cache every page; never run inference."""

        metadata = self._credential_store.metadata(provider)
        if not metadata.configured:
            result = self._not_configured(provider)
            with self._lock:
                self._verification[provider] = result
                self._model_cache.delete(provider)
            return result

        control_plane = self._control_plane(provider)
        if control_plane is None or not control_plane.supports_metadata_verification:
            result = self._unsupported_result(provider)
            with self._lock:
                self._verification[provider] = result
                self._model_cache.delete(provider)
            self._logger.info(
                "Provider verification finished: provider=%s status=%s",
                provider.value,
                result.status.value,
            )
            return result

        secret = self._credential_store.load(provider)
        if secret is None:
            result = self._not_configured(provider)
            with self._lock:
                self._verification[provider] = result
            return result

        try:
            models = self._fetch_all_models(control_plane, secret)
            self._model_cache.save(provider, models)
            result = VerificationResult(
                provider=provider,
                status=VerificationStatus.VERIFIED,
                operation=control_plane.verification_operation,
                charge_risk=control_plane.charge_risk,
                inference_used=False,
                safe_message=(
                    "API key was accepted by the provider metadata endpoint; no inference was sent."
                ),
            )
        except ControlPlaneError as error:
            self._model_cache.delete(provider)
            result = VerificationResult(
                provider=provider,
                status=error.status,
                operation=control_plane.verification_operation,
                charge_risk=control_plane.charge_risk,
                inference_used=False,
                safe_message=error.safe_message,
            )
        finally:
            secret.clear()

        with self._lock:
            self._verification[provider] = result
        self._logger.info(
            "Provider verification finished: provider=%s status=%s",
            provider.value,
            result.status.value,
        )
        return result

    def refresh_models(self, provider: ProviderId) -> DiscoveryResult:
        with self._lock:
            verification = self._verification.get(provider)
        if verification is None or verification.status is not VerificationStatus.VERIFIED:
            return DiscoveryResult(
                provider=provider,
                status=DiscoveryStatus.BLOCKED,
                safe_message="Verify the API key before refreshing models.",
            )

        control_plane = self._control_plane(provider)
        if control_plane is None or not control_plane.supports_metadata_verification:
            return DiscoveryResult(
                provider=provider,
                status=DiscoveryStatus.UNSUPPORTED,
                safe_message="Provider model discovery is not supported.",
            )

        secret = self._credential_store.load(provider)
        if secret is None:
            return DiscoveryResult(
                provider=provider,
                status=DiscoveryStatus.BLOCKED,
                safe_message="API key is not configured.",
            )
        try:
            models = self._fetch_all_models(control_plane, secret)
            entry = self._model_cache.save(provider, models)
        except ControlPlaneError as error:
            if error.status in {
                VerificationStatus.INVALID_KEY,
                VerificationStatus.PERMISSION_DENIED,
            }:
                with self._lock:
                    self._verification[provider] = VerificationResult(
                        provider=provider,
                        status=error.status,
                        operation=control_plane.verification_operation,
                        charge_risk=control_plane.charge_risk,
                        inference_used=False,
                        safe_message=error.safe_message,
                    )
                self._model_cache.delete(provider)
            return DiscoveryResult(
                provider=provider,
                status=(
                    DiscoveryStatus.UNSUPPORTED
                    if error.status is VerificationStatus.UNSUPPORTED_ENDPOINT
                    else DiscoveryStatus.FAILED
                ),
                safe_message=error.safe_message,
            )
        finally:
            secret.clear()

        self._logger.info("Provider models refreshed: provider=%s", provider.value)
        return DiscoveryResult(
            provider=provider,
            status=DiscoveryStatus.SUCCESS,
            safe_message="Provider model metadata was refreshed.",
            cache=entry,
        )

    def snapshot(self, provider: ProviderId) -> ProviderSettingsSnapshot:
        credential = self._credential_store.metadata(provider)
        with self._lock:
            verification = self._verification.get(provider)
            manual_model = self._manual_models.get(provider)
        if verification is None:
            verification = (
                self._not_verified(provider)
                if credential.configured
                else self._not_configured(provider)
            )
        return ProviderSettingsSnapshot(
            provider=provider,
            credential=credential,
            verification=verification,
            model_cache=self._model_cache.get(provider),
            manual_model=manual_model,
        )
