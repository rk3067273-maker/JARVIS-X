"""Google Gemini GenerateContent provider adapter."""

from __future__ import annotations

from typing import NoReturn
from urllib.parse import quote, urlencode

from jarvis_x.config import SecretValue
from jarvis_x.providers.base import (
    AIProvider,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    ProviderError,
    UrllibTransport,
    decode_json_object,
    encode_json,
    error_fingerprint,
    join_endpoint,
    normalize_https_base_url,
    send_once,
)
from jarvis_x.providers.models import (
    BillingSafety,
    BillingTier,
    ChatRequest,
    FailureReason,
    ModelInfo,
    ProviderOutcome,
    ProviderResponse,
    ProviderState,
    ProviderStatus,
    TokenUsage,
    optional_token_count,
)


class GeminiProvider(AIProvider):
    """Gemini API adapter designated as the free Phase 2A backup."""

    def __init__(
        self,
        *,
        api_key: SecretValue | None,
        base_url: str,
        model_id: str | None,
        free_tier_verified: bool = False,
        free_tier_verified_model: str | None = None,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model_id = model_id.strip() if model_id else None
        self._free_tier_verified = free_tier_verified
        self._free_tier_verified_model = (
            free_tier_verified_model.strip() if free_tier_verified_model else None
        )
        self._transport = transport or UrllibTransport()
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "Google Gemini"

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def billing_tier(self) -> BillingTier:
        return BillingTier.FREE

    @property
    def billing_safety(self) -> BillingSafety:
        return BillingSafety(
            billing_tier=BillingTier.FREE,
            free_tier_verified=self._free_tier_verified
            and self._model_id is not None
            and self._free_tier_verified_model == self._model_id,
        )

    @property
    def supports_model_discovery(self) -> bool:
        return True

    def _billing_policy_error(self) -> ProviderError | None:
        if not self._free_tier_verified:
            return ProviderError(
                self.name,
                FailureReason.FREE_TIER_UNVERIFIED,
                "Gemini Free Tier availability is not explicitly verified.",
                outcome=ProviderOutcome.BLOCKED,
            )
        if not self._model_id or self._free_tier_verified_model != self._model_id:
            return ProviderError(
                self.name,
                FailureReason.FREE_TIER_UNVERIFIED,
                "Gemini Free Tier verification does not match the configured model.",
                outcome=ProviderOutcome.BLOCKED,
            )
        return None

    def ensure_billing_safe(self) -> None:
        error = self._billing_policy_error()
        if error is not None:
            raise error

    def _configuration_issues(self, *, require_model: bool) -> tuple[str, ...]:
        issues: list[str] = []
        if self._api_key is None:
            issues.append("GEMINI_API_KEY is missing")
        if require_model and not self._model_id:
            issues.append("GEMINI_MODEL is missing")
        if self._timeout_seconds <= 0:
            issues.append("request timeout must be positive")
        try:
            normalize_https_base_url(self._base_url)
        except ValueError:
            issues.append("GEMINI_BASE_URL is invalid")
        return tuple(issues)

    def status(self) -> ProviderStatus:
        issues = self._configuration_issues(require_model=True)
        billing_error = self._billing_policy_error()
        invalid = any("invalid" in issue or "timeout" in issue for issue in issues)
        state = (
            ProviderState.INVALID
            if invalid or (not issues and billing_error is not None)
            else ProviderState.UNCONFIGURED
            if issues
            else ProviderState.READY
        )
        messages = list(issues)
        if billing_error is not None:
            messages.append(billing_error.safe_message)
        return ProviderStatus(
            provider_name=self.name,
            state=state,
            configured_model=self._model_id,
            billing_tier=BillingTier.FREE,
            billing_safety_verified=billing_error is None,
            model_discovery_supported=True,
            message="Ready" if not messages else "; ".join(messages),
        )

    def _require_configuration(self, *, require_model: bool = True) -> None:
        issues = self._configuration_issues(require_model=require_model)
        if issues:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_CONFIGURATION,
                f"Gemini configuration is invalid: {'; '.join(issues)}.",
            )

    def _headers(self) -> dict[str, str]:
        assert self._api_key is not None
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key.reveal(),
        }

    def _raise_http_error(self, response: HttpResponse) -> NoReturn:
        status = response.status_code
        fingerprint = error_fingerprint(response)
        outcome = ProviderOutcome.FAILED

        if status in {401, 403}:
            reason = FailureReason.AUTHENTICATION
            message = "Gemini authentication or authorization failed."
        elif status in {408, 504}:
            raise ProviderError(
                self.name,
                FailureReason.TIMEOUT,
                "Gemini timed out; the outcome is unknown and was not retried.",
                status_code=status,
                outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                outcome_unknown=True,
            )
        elif status == 429 or "resource_exhausted" in fingerprint or "quota" in fingerprint:
            reason = FailureReason.QUOTA_EXHAUSTED
            message = "Gemini free quota is exhausted."
            outcome = ProviderOutcome.STOP_AND_WAIT
        elif status >= 500:
            reason = FailureReason.UNAVAILABLE
            message = "Gemini is currently unavailable."
            outcome = ProviderOutcome.STOP_AND_WAIT
        elif status in {400, 404, 422}:
            reason = FailureReason.INVALID_CONFIGURATION
            message = "Gemini rejected the configured model or request."
        else:
            reason = FailureReason.INVALID_RESPONSE
            message = "Gemini returned an unsupported response."

        raise ProviderError(
            self.name,
            reason,
            message,
            status_code=status,
            outcome=outcome,
        )

    def _send(self, request: HttpRequest) -> dict[str, object]:
        response = send_once(
            self.name,
            self._transport,
            request,
            unavailable_outcome=ProviderOutcome.STOP_AND_WAIT,
        )
        if not 200 <= response.status_code < 300:
            self._raise_http_error(response)
        try:
            return decode_json_object(response)
        except ValueError as error:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "Gemini returned an invalid JSON response.",
                status_code=response.status_code,
            ) from error

    def _generation_url(self) -> str:
        assert self._model_id is not None
        model_path = self._model_id.strip("/")
        if not model_path.startswith("models/"):
            model_path = f"models/{model_path}"
        encoded_model = quote(model_path, safe="/")
        return join_endpoint(self._base_url, f"/{encoded_model}:generateContent")

    def generate(self, request: ChatRequest) -> ProviderResponse:
        self._require_configuration()
        self.ensure_billing_safe()
        assert self._model_id is not None

        system_parts = [
            {"text": message.content}
            for message in request.messages
            if message.role == "system"
        ]
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        if not contents:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_CONFIGURATION,
                "Gemini requires at least one non-system message.",
            )

        payload: dict[str, object] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        generation_config: dict[str, object] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        if generation_config:
            payload["generationConfig"] = generation_config

        data = self._send(
            HttpRequest(
                method="POST",
                url=self._generation_url(),
                headers=self._headers(),
                body=encode_json(payload),
                timeout_seconds=self._timeout_seconds,
            )
        )

        try:
            candidates = data["candidates"]
            first_candidate = candidates[0]  # type: ignore[index]
            response_content = first_candidate["content"]  # type: ignore[index]
            parts = response_content["parts"]  # type: ignore[index]
            text_parts = [part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "Gemini response did not contain generated content.",
            ) from error

        content = "".join(text_parts)
        if not content.strip():
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "Gemini returned empty or unsupported content.",
            )

        usage_data = data.get("usageMetadata")
        usage = usage_data if isinstance(usage_data, dict) else {}
        response_model = data.get("modelVersion")
        if isinstance(response_model, str) and response_model.strip():
            configured_model = self._model_id.removeprefix("models/")
            reported_model = response_model.strip().removeprefix("models/")
            if reported_model != configured_model:
                raise ProviderError(
                    self.name,
                    FailureReason.INVALID_RESPONSE,
                    "Gemini returned a model different from the verified Free Tier model.",
                    outcome=ProviderOutcome.FAILED,
                )
        raw_request_id = data.get("responseId")
        provider_request_id = (
            raw_request_id.strip()
            if isinstance(raw_request_id, str) and raw_request_id.strip()
            else None
        )
        finish_reason = first_candidate.get("finishReason") if isinstance(first_candidate, dict) else None

        return ProviderResponse(
            provider_name=self.name,
            model_id=(
                response_model.strip()
                if isinstance(response_model, str) and response_model.strip()
                else self._model_id
            ),
            content=content,
            usage=TokenUsage(
                input_tokens=optional_token_count(usage.get("promptTokenCount")),
                output_tokens=optional_token_count(usage.get("candidatesTokenCount")),
                total_tokens=optional_token_count(usage.get("totalTokenCount")),
            ),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            provider_request_id=provider_request_id,
        )

    def discover_models(self) -> tuple[ModelInfo, ...]:
        self._require_configuration(require_model=False)
        models: list[ModelInfo] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _page_number in range(100):
            query: dict[str, str | int] = {"pageSize": 1000}
            if page_token:
                query["pageToken"] = page_token
            data = self._send(
                HttpRequest(
                    method="GET",
                    url=(
                        f"{join_endpoint(self._base_url, '/models')}?"
                        f"{urlencode(query)}"
                    ),
                    headers=self._headers(),
                    timeout_seconds=self._timeout_seconds,
                )
            )
            raw_models = data.get("models")
            if not isinstance(raw_models, list):
                raise ProviderError(
                    self.name,
                    FailureReason.INVALID_RESPONSE,
                    "Gemini model discovery returned an invalid model list.",
                )

            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("name")
                if not isinstance(model_id, str) or not model_id.strip():
                    continue
                raw_methods = item.get("supportedGenerationMethods")
                capabilities = (
                    tuple(value for value in raw_methods if isinstance(value, str))
                    if isinstance(raw_methods, list)
                    else ()
                )
                display_name = item.get("displayName")
                description = item.get("description")
                base_model_id = item.get("baseModelId")
                version = item.get("version")
                thinking = item.get("thinking")
                models.append(
                    ModelInfo(
                        provider_name=self.name,
                        model_id=model_id,
                        display_name=(
                            display_name if isinstance(display_name, str) else None
                        ),
                        # Availability does not prove Free Tier eligibility.
                        billing_tier=BillingTier.UNKNOWN,
                        capabilities=capabilities,
                        description=(
                            description if isinstance(description, str) else None
                        ),
                        base_model_id=(
                            base_model_id if isinstance(base_model_id, str) else None
                        ),
                        version=version if isinstance(version, str) else None,
                        input_token_limit=optional_token_count(
                            item.get("inputTokenLimit")
                        ),
                        output_token_limit=optional_token_count(
                            item.get("outputTokenLimit")
                        ),
                        thinking=thinking if isinstance(thinking, bool) else None,
                    )
                )

            raw_next_token = data.get("nextPageToken")
            next_token = (
                raw_next_token.strip()
                if isinstance(raw_next_token, str) and raw_next_token.strip()
                else None
            )
            if next_token is None:
                return tuple(models)
            if next_token in seen_tokens:
                raise ProviderError(
                    self.name,
                    FailureReason.INVALID_RESPONSE,
                    "Gemini model discovery repeated a pagination token.",
                )
            seen_tokens.add(next_token)
            page_token = next_token

        raise ProviderError(
            self.name,
            FailureReason.INVALID_RESPONSE,
            "Gemini model discovery exceeded the safe pagination limit.",
        )
