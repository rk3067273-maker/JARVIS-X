"""OpenAI-compatible AgentRouter provider adapter."""

from __future__ import annotations

from typing import NoReturn

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
    AutoRechargeStatus,
    BillingSafety,
    BillingTier,
    ChatRequest,
    FailureReason,
    ModelInfo,
    ProviderOutcome,
    ProviderResponse,
    ProviderState,
    ProviderStatus,
    PrepaidBalanceStatus,
    TokenUsage,
    optional_token_count,
)

PRIMARY_MODEL_ID = "gpt-6.1-sol"


class AgentRouterProvider(AIProvider):
    """AgentRouter adapter using OpenAI-compatible Chat Completions."""

    def __init__(
        self,
        *,
        api_key: SecretValue | None,
        base_url: str,
        model_id: str | None,
        billing_tier: BillingTier = BillingTier.UNKNOWN,
        prepaid_balance_status: PrepaidBalanceStatus = PrepaidBalanceStatus.UNKNOWN,
        auto_recharge_status: AutoRechargeStatus = AutoRechargeStatus.UNKNOWN,
        models_endpoint_supported: bool = False,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model_id = model_id.strip() if model_id else None
        self._billing_tier = billing_tier
        self._prepaid_balance_status = prepaid_balance_status
        self._auto_recharge_status = auto_recharge_status
        self._models_endpoint_requested = models_endpoint_supported
        self._transport = transport or UrllibTransport()
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "AgentRouter"

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def billing_tier(self) -> BillingTier:
        return self._billing_tier

    @property
    def billing_safety(self) -> BillingSafety:
        return BillingSafety(
            billing_tier=self._billing_tier,
            prepaid_balance_status=self._prepaid_balance_status,
            auto_recharge_status=self._auto_recharge_status,
        )

    @property
    def supports_model_discovery(self) -> bool:
        return False

    def _billing_policy_error(self) -> ProviderError | None:
        if self._billing_tier is BillingTier.PAID:
            return ProviderError(
                self.name,
                FailureReason.PAID_PROVIDER_BLOCKED,
                "AgentRouter paid spending is disabled; only explicit prepaid balance mode is allowed.",
                outcome=ProviderOutcome.BLOCKED,
            )
        if self._billing_tier is not BillingTier.PREPAID:
            return ProviderError(
                self.name,
                FailureReason.BILLING_SAFETY_UNVERIFIED,
                "AgentRouter prepaid billing mode is not explicitly confirmed.",
                outcome=ProviderOutcome.BLOCKED,
            )
        if self._auto_recharge_status is AutoRechargeStatus.ENABLED:
            return ProviderError(
                self.name,
                FailureReason.AUTO_RECHARGE_RISK,
                "AgentRouter auto recharge is enabled; requests are blocked.",
                outcome=ProviderOutcome.BLOCKED,
            )
        if self._auto_recharge_status is not AutoRechargeStatus.DISABLED:
            return ProviderError(
                self.name,
                FailureReason.BILLING_SAFETY_UNVERIFIED,
                "AgentRouter auto-recharge status is not dashboard-verified as disabled.",
                outcome=ProviderOutcome.BLOCKED,
            )
        if self._prepaid_balance_status in {
            PrepaidBalanceStatus.EXHAUSTED,
            PrepaidBalanceStatus.UNAVAILABLE,
        }:
            return ProviderError(
                self.name,
                FailureReason.BALANCE_EXHAUSTED,
                "AgentRouter prepaid balance is exhausted or unavailable.",
                outcome=ProviderOutcome.FALLBACK_ALLOWED,
            )
        if self._prepaid_balance_status is not PrepaidBalanceStatus.AVAILABLE:
            return ProviderError(
                self.name,
                FailureReason.BILLING_SAFETY_UNVERIFIED,
                "AgentRouter prepaid balance availability is not confirmed.",
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
            issues.append("AGENTROUTER_API_KEY is missing")
        if require_model and not self._model_id:
            issues.append("AGENTROUTER_MODEL is missing")
        elif require_model and self._model_id != PRIMARY_MODEL_ID:
            issues.append(f"AGENTROUTER_MODEL must be {PRIMARY_MODEL_ID}")
        if self._timeout_seconds <= 0:
            issues.append("request timeout must be positive")
        if self._models_endpoint_requested:
            issues.append("AgentRouter model discovery must be disabled")
        try:
            normalize_https_base_url(self._base_url)
        except ValueError:
            issues.append("AGENTROUTER_BASE_URL is invalid")
        return tuple(issues)

    def status(self) -> ProviderStatus:
        issues = self._configuration_issues(require_model=True)
        billing_error = self._billing_policy_error()
        invalid = any(
            "invalid" in issue or "timeout" in issue or "must be" in issue
            for issue in issues
        )
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
            billing_tier=self._billing_tier,
            billing_safety_verified=billing_error is None,
            model_discovery_supported=False,
            message="Ready" if not messages else "; ".join(messages),
        )

    def _require_configuration(self, *, require_model: bool = True) -> None:
        issues = self._configuration_issues(require_model=require_model)
        if issues:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_CONFIGURATION,
                f"AgentRouter configuration is invalid: {'; '.join(issues)}.",
            )

    def _headers(self) -> dict[str, str]:
        assert self._api_key is not None
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key.reveal()}",
            "Content-Type": "application/json",
        }

    def _raise_http_error(self, response: HttpResponse) -> NoReturn:
        status = response.status_code
        fingerprint = error_fingerprint(response)
        outcome = ProviderOutcome.FAILED

        if status in {401, 403}:
            reason = FailureReason.AUTHENTICATION
            message = "AgentRouter authentication or authorization failed."
        elif status in {408, 504}:
            raise ProviderError(
                self.name,
                FailureReason.TIMEOUT,
                "AgentRouter timed out; the outcome is unknown and no fallback was attempted.",
                status_code=status,
                outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                outcome_unknown=True,
            )
        elif status == 402 or any(
            marker in fingerprint
            for marker in (
                "credit exhausted",
                "credits exhausted",
                "insufficient_credit",
                "balance exhausted",
            )
        ):
            reason = FailureReason.BALANCE_EXHAUSTED
            message = "AgentRouter prepaid balance is exhausted."
            outcome = ProviderOutcome.FALLBACK_ALLOWED
        elif any(
            marker in fingerprint
            for marker in ("insufficient_quota", "quota_exhausted", "resource_exhausted")
        ):
            reason = FailureReason.QUOTA_EXHAUSTED
            message = "AgentRouter quota is exhausted."
            outcome = ProviderOutcome.FALLBACK_ALLOWED
        elif status == 429:
            reason = FailureReason.RATE_LIMITED
            message = "AgentRouter rate limit was reached."
            outcome = ProviderOutcome.FALLBACK_ALLOWED
        elif status >= 500:
            reason = FailureReason.UNAVAILABLE
            message = "AgentRouter is currently unavailable."
            outcome = ProviderOutcome.FALLBACK_ALLOWED
        elif status in {400, 404, 422}:
            reason = FailureReason.INVALID_CONFIGURATION
            message = "AgentRouter rejected the configured endpoint, model, or request."
            outcome = ProviderOutcome.FAILED
        else:
            reason = FailureReason.INVALID_RESPONSE
            message = "AgentRouter returned an unsupported response."
            outcome = ProviderOutcome.FAILED

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
            unavailable_outcome=ProviderOutcome.FALLBACK_ALLOWED,
        )
        if not 200 <= response.status_code < 300:
            self._raise_http_error(response)
        try:
            return decode_json_object(response)
        except ValueError as error:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "AgentRouter returned an invalid JSON response.",
                status_code=response.status_code,
            ) from error

    def generate(self, request: ChatRequest) -> ProviderResponse:
        self._require_configuration()
        self.ensure_billing_safe()
        assert self._model_id is not None

        payload: dict[str, object] = {
            "model": self._model_id,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens

        data = self._send(
            HttpRequest(
                method="POST",
                url=join_endpoint(self._base_url, "/chat/completions"),
                headers=self._headers(),
                body=encode_json(payload),
                timeout_seconds=self._timeout_seconds,
            )
        )

        try:
            choices = data["choices"]
            first_choice = choices[0]  # type: ignore[index]
            message = first_choice["message"]  # type: ignore[index]
            content = message["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "AgentRouter response did not contain a chat completion.",
            ) from error

        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "AgentRouter returned empty or unsupported content.",
            )

        usage_data = data.get("usage")
        usage = usage_data if isinstance(usage_data, dict) else {}
        response_model = data.get("model")
        if (
            isinstance(response_model, str)
            and response_model.strip()
            and response_model.strip() != self._model_id
        ):
            raise ProviderError(
                self.name,
                FailureReason.INVALID_RESPONSE,
                "AgentRouter returned a model different from the enforced primary model.",
                outcome=ProviderOutcome.FAILED,
            )
        raw_request_id = data.get("id")
        provider_request_id = (
            raw_request_id.strip()
            if isinstance(raw_request_id, str) and raw_request_id.strip()
            else None
        )
        finish_reason = first_choice.get("finish_reason") if isinstance(first_choice, dict) else None

        return ProviderResponse(
            provider_name=self.name,
            model_id=(
                response_model.strip()
                if isinstance(response_model, str) and response_model.strip()
                else self._model_id
            ),
            content=content,
            usage=TokenUsage(
                input_tokens=optional_token_count(usage.get("prompt_tokens")),
                output_tokens=optional_token_count(usage.get("completion_tokens")),
                total_tokens=optional_token_count(usage.get("total_tokens")),
            ),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            provider_request_id=provider_request_id,
        )

    def discover_models(self) -> tuple[ModelInfo, ...]:
        raise ProviderError(
            self.name,
            FailureReason.DISCOVERY_UNSUPPORTED,
            "AgentRouter does not document a supported non-inference model-discovery endpoint.",
        )
