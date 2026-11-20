"""Provider policy, usage recording, and conservative automatic failover."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

from jarvis_x.config import Settings
from jarvis_x.providers.agentrouter import AgentRouterProvider
from jarvis_x.providers.base import (
    AIProvider,
    HttpTransport,
    ProviderChainError,
    ProviderError,
)
from jarvis_x.providers.gemini import GeminiProvider
from jarvis_x.providers.models import (
    AutoRechargeStatus,
    BillingTier,
    ChatRequest,
    FailureReason,
    ProviderAttempt,
    ProviderExecutionResult,
    ProviderOutcome,
    ProviderResponse,
    ProviderStatus,
    PrepaidBalanceStatus,
    UsageRecord,
)

class ProviderManager:
    """Use AgentRouter first and fail over only after a confirmed safe failure."""

    def __init__(
        self,
        *,
        primary: AIProvider,
        fallbacks: Sequence[AIProvider],
        allow_paid_providers: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        if allow_paid_providers:
            raise ProviderError(
                "ProviderManager",
                FailureReason.PAID_PROVIDER_BLOCKED,
                "Phase 2C.1 does not permit paid providers.",
                outcome=ProviderOutcome.BLOCKED,
            )
        self._primary = primary
        self._fallbacks = tuple(fallbacks)
        self._allow_paid_providers = allow_paid_providers
        self._logger = logger or logging.getLogger("jarvis_x.providers")
        self._usage_records: list[UsageRecord] = []
        self._claimed_request_ids: set[str] = set()
        self._state_lock = threading.Lock()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        agentrouter_transport: HttpTransport | None = None,
        gemini_transport: HttpTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> "ProviderManager":
        """Build the fixed AgentRouter → verified-free Gemini chain."""

        primary = AgentRouterProvider(
            api_key=settings.agentrouter_api_key,
            base_url=settings.agentrouter_base_url,
            model_id=settings.agentrouter_model,
            billing_tier=BillingTier(settings.agentrouter_billing_mode),
            prepaid_balance_status=PrepaidBalanceStatus(
                settings.agentrouter_prepaid_balance_status
            ),
            auto_recharge_status=AutoRechargeStatus(
                settings.agentrouter_auto_recharge_status
            ),
            models_endpoint_supported=settings.agentrouter_models_endpoint_supported,
            transport=agentrouter_transport,
        )
        backup = GeminiProvider(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            model_id=settings.gemini_model,
            free_tier_verified=settings.gemini_free_tier_verified,
            free_tier_verified_model=settings.gemini_free_tier_verified_model,
            transport=gemini_transport,
        )
        return cls(
            primary=primary,
            fallbacks=(backup,),
            allow_paid_providers=settings.allow_paid_providers,
            logger=logger,
        )

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        with self._state_lock:
            return tuple(self._usage_records)

    @property
    def primary_provider_name(self) -> str:
        return self._primary.name

    @property
    def primary_model_id(self) -> str | None:
        return self._primary.model_id

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return (self._primary.status(), *(provider.status() for provider in self._fallbacks))

    def _enforce_billing_policy(self, provider: AIProvider) -> None:
        provider.ensure_billing_safe()

    def _invoke(self, provider: AIProvider, request: ChatRequest) -> ProviderResponse:
        self._enforce_billing_policy(provider)
        try:
            return provider.generate(request)
        except ProviderError:
            raise
        except Exception as error:
            # A provider may already have sent the request. Treat unexpected errors
            # as outcome-unknown and never issue a duplicate fallback request.
            raise ProviderError(
                provider.name,
                FailureReason.UNKNOWN,
                f"{provider.name} failed unexpectedly; the outcome is unknown.",
                outcome=ProviderOutcome.OUTCOME_UNKNOWN,
                outcome_unknown=True,
            ) from error

    def _record(self, response: ProviderResponse) -> ProviderResponse:
        with self._state_lock:
            self._usage_records.append(response.to_usage_record())
        return response

    def _claim_request(self, request_id: str) -> None:
        """Claim one in-memory request ID exactly once, including concurrent calls."""

        with self._state_lock:
            if request_id in self._claimed_request_ids:
                raise ProviderError(
                    "ProviderManager",
                    FailureReason.DUPLICATE_REQUEST,
                    "The request ID has already been executed; duplicate sending is blocked.",
                    outcome=ProviderOutcome.BLOCKED,
                )
            self._claimed_request_ids.add(request_id)

    def generate(self, request: ChatRequest) -> ProviderResponse:
        """Generate once per provider, with no retry after an uncertain outcome."""

        self._claim_request(request.request_id)
        attempts: list[ProviderAttempt] = []

        try:
            return self._record(self._invoke(self._primary, request))
        except ProviderError as error:
            attempts.append(
                ProviderAttempt(
                    provider_name=error.provider_name,
                    reason=error.reason,
                    outcome=error.outcome,
                    outcome_unknown=error.outcome_unknown,
                )
            )
            self._logger.warning(
                "Provider failed: provider=%s reason=%s outcome=%s",
                error.provider_name,
                error.reason.value,
                error.outcome.value,
            )
            if error.outcome is not ProviderOutcome.FALLBACK_ALLOWED:
                raise

        for fallback in self._fallbacks:
            self._logger.warning(
                "Provider failover: from=%s to=%s reason=%s",
                attempts[-1].provider_name,
                fallback.name,
                attempts[-1].reason.value,
            )
            try:
                return self._record(self._invoke(fallback, request))
            except ProviderError as error:
                attempts.append(
                    ProviderAttempt(
                        provider_name=error.provider_name,
                        reason=error.reason,
                        outcome=error.outcome,
                        outcome_unknown=error.outcome_unknown,
                    )
                )
                self._logger.error(
                    "Fallback failed: provider=%s reason=%s outcome=%s",
                    error.provider_name,
                    error.reason.value,
                    error.outcome.value,
                )
                if error.outcome is not ProviderOutcome.FALLBACK_ALLOWED:
                    raise ProviderChainError(
                        f"Fallback provider {error.provider_name} failed with {error.reason.value}.",
                        tuple(attempts),
                        outcome=error.outcome,
                        outcome_unknown=error.outcome_unknown,
                    ) from error

        raise ProviderChainError(
            "All permitted providers are unavailable or out of quota.",
            tuple(attempts),
            outcome=ProviderOutcome.STOP_AND_WAIT,
        )

    def execute(self, request: ChatRequest) -> ProviderExecutionResult:
        """Return a normalized outcome for the future orchestrator boundary."""

        try:
            response = self.generate(request)
            return ProviderExecutionResult(
                request_id=request.request_id,
                outcome=ProviderOutcome.SUCCESS,
                response=response,
            )
        except ProviderChainError as error:
            reason = error.attempts[-1].reason if error.attempts else error.reason
            return ProviderExecutionResult(
                request_id=request.request_id,
                outcome=error.outcome,
                attempts=error.attempts,
                reason=reason,
                safe_message=error.safe_message,
            )
        except ProviderError as error:
            attempt = ProviderAttempt(
                provider_name=error.provider_name,
                reason=error.reason,
                outcome=error.outcome,
                outcome_unknown=error.outcome_unknown,
            )
            return ProviderExecutionResult(
                request_id=request.request_id,
                outcome=error.outcome,
                attempts=(attempt,),
                reason=error.reason,
                safe_message=error.safe_message,
            )
