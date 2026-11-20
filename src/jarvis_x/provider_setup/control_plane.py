"""Non-inference provider verification and model-discovery adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlencode

from jarvis_x.provider_setup.credentials import SecretBuffer
from jarvis_x.provider_setup.models import (
    ChargeRisk,
    DiscoveredModel,
    DiscoveryPage,
    ProviderId,
    VerificationOperation,
    VerificationStatus,
)
from jarvis_x.providers.base import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    ProviderError,
    UrllibTransport,
    decode_json_object,
    error_fingerprint,
    join_endpoint,
    normalize_https_base_url,
    send_once,
)
from jarvis_x.providers.models import BillingTier, optional_token_count


class ControlPlaneError(Exception):
    """Classified error that never contains response bodies or credentials."""

    def __init__(self, status: VerificationStatus, safe_message: str) -> None:
        self.status = status
        self.safe_message = safe_message
        super().__init__(safe_message)


class ProviderControlPlane(Protocol):
    @property
    def provider(self) -> ProviderId: ...

    @property
    def supports_metadata_verification(self) -> bool: ...

    @property
    def verification_operation(self) -> VerificationOperation: ...

    @property
    def charge_risk(self) -> ChargeRisk: ...

    def list_models(
        self,
        secret: SecretBuffer,
        *,
        page_token: str | None = None,
    ) -> DiscoveryPage: ...


class AgentRouterUnsupportedControlPlane:
    """Fail closed until AgentRouter documents a non-inference metadata API."""

    provider = ProviderId.AGENTROUTER
    supports_metadata_verification = False
    verification_operation = VerificationOperation.UNSUPPORTED
    charge_risk = ChargeRisk.NOT_APPLICABLE

    def list_models(
        self,
        secret: SecretBuffer,
        *,
        page_token: str | None = None,
    ) -> DiscoveryPage:
        del secret, page_token
        raise ControlPlaneError(
            VerificationStatus.UNSUPPORTED_ENDPOINT,
            "AgentRouter does not document a non-inference model-discovery or key-verification endpoint.",
        )


class GeminiMetadataControlPlane:
    """Gemini Models API adapter; it never sends prompts or inference payloads."""

    provider = ProviderId.GEMINI
    supports_metadata_verification = True
    verification_operation = VerificationOperation.MODEL_LIST_METADATA
    charge_risk = ChargeRisk.NO_INFERENCE_EXPECTED

    def __init__(
        self,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
        page_size: int = 1000,
    ) -> None:
        self._base_url = normalize_https_base_url(base_url)
        self._transport = transport or UrllibTransport()
        if timeout_seconds <= 0:
            raise ValueError("Metadata timeout must be positive.")
        if not 1 <= page_size <= 1000:
            raise ValueError("Gemini page size must be between 1 and 1000.")
        self._timeout_seconds = timeout_seconds
        self._page_size = page_size

    def _url(self, page_token: str | None) -> str:
        query: dict[str, str | int] = {"pageSize": self._page_size}
        if page_token:
            query["pageToken"] = page_token
        return f"{join_endpoint(self._base_url, '/models')}?{urlencode(query)}"

    def _raise_http_error(self, response: HttpResponse) -> None:
        status_code = response.status_code
        fingerprint = error_fingerprint(response)
        if status_code == 401 or any(
            marker in fingerprint
            for marker in ("api_key_invalid", "api key not valid", "invalid api key")
        ):
            status = VerificationStatus.INVALID_KEY
            message = "Gemini rejected the API key."
        elif status_code == 403:
            status = VerificationStatus.PERMISSION_DENIED
            message = "Gemini denied permission for model metadata."
        elif status_code == 429 or "resource_exhausted" in fingerprint:
            status = VerificationStatus.QUOTA_EXHAUSTED
            message = "Gemini metadata quota is exhausted."
        elif status_code == 404:
            status = VerificationStatus.UNSUPPORTED_ENDPOINT
            message = "Gemini model discovery endpoint is unavailable."
        elif status_code in {408, 504} or status_code >= 500:
            status = VerificationStatus.NETWORK_FAILURE
            message = "Gemini metadata service is temporarily unavailable."
        elif status_code == 400:
            status = VerificationStatus.PROVIDER_ERROR
            message = "Gemini rejected the metadata request."
        else:
            status = VerificationStatus.PROVIDER_ERROR
            message = "Gemini returned an unsupported metadata error."
        raise ControlPlaneError(status, message)

    def _send(self, secret: SecretBuffer, page_token: str | None) -> Mapping[str, object]:
        headers: dict[str, str] = {}
        try:
            with secret.copy_bytes() as key_material:
                try:
                    key_text = key_material.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ControlPlaneError(
                        VerificationStatus.INVALID_KEY,
                        "Gemini API key encoding is invalid.",
                    ) from error
                headers.update(
                    {
                        "Accept": "application/json",
                        "x-goog-api-key": key_text,
                    }
                )
                try:
                    response = send_once(
                        "Google Gemini metadata",
                        self._transport,
                        HttpRequest(
                            method="GET",
                            url=self._url(page_token),
                            headers=headers,
                            timeout_seconds=self._timeout_seconds,
                        ),
                    )
                except ProviderError as error:
                    raise ControlPlaneError(
                        VerificationStatus.NETWORK_FAILURE,
                        "Gemini metadata request could not be completed.",
                    ) from error
                finally:
                    key_text = ""
        finally:
            # HttpRequest retains the same mapping. Clear it after transport use so
            # fakes, traces, or delayed repr calls cannot retain authorization data.
            headers.clear()

        if not 200 <= response.status_code < 300:
            self._raise_http_error(response)
        try:
            return decode_json_object(response)
        except ValueError as error:
            raise ControlPlaneError(
                VerificationStatus.INVALID_RESPONSE,
                "Gemini returned invalid model metadata.",
            ) from error

    def list_models(
        self,
        secret: SecretBuffer,
        *,
        page_token: str | None = None,
    ) -> DiscoveryPage:
        data = self._send(secret, page_token)
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise ControlPlaneError(
                VerificationStatus.INVALID_RESPONSE,
                "Gemini model discovery returned an invalid model list.",
            )

        models: list[DiscoveredModel] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("name")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            raw_methods = item.get("supportedGenerationMethods")
            methods = (
                tuple(method for method in raw_methods if isinstance(method, str))
                if isinstance(raw_methods, list)
                else ()
            )
            thinking_value = item.get("thinking")
            models.append(
                DiscoveredModel(
                    provider=self.provider,
                    model_id=model_id,
                    display_name=item.get("displayName")
                    if isinstance(item.get("displayName"), str)
                    else None,
                    description=item.get("description")
                    if isinstance(item.get("description"), str)
                    else None,
                    base_model_id=item.get("baseModelId")
                    if isinstance(item.get("baseModelId"), str)
                    else None,
                    version=item.get("version")
                    if isinstance(item.get("version"), str)
                    else None,
                    provider_capabilities=methods,
                    input_token_limit=optional_token_count(item.get("inputTokenLimit")),
                    output_token_limit=optional_token_count(item.get("outputTokenLimit")),
                    thinking=thinking_value if isinstance(thinking_value, bool) else None,
                    # The Models API does not report account billing or Free Tier.
                    billing_tier=BillingTier.UNKNOWN,
                    free_tier_eligible=None,
                )
            )

        raw_next_token = data.get("nextPageToken")
        next_token = (
            raw_next_token.strip()
            if isinstance(raw_next_token, str) and raw_next_token.strip()
            else None
        )
        return DiscoveryPage(tuple(models), next_page_token=next_token)
