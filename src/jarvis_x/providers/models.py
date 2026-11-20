"""Provider-neutral request, response, status, and usage models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class BillingTier(StrEnum):
    """Known billing behavior for a provider or model."""

    FREE = "free"
    PREPAID = "prepaid"
    PAID = "paid"
    UNKNOWN = "unknown"


class PrepaidBalanceStatus(StrEnum):
    """User-verified prepaid balance state; no value is inferred by code."""

    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AutoRechargeStatus(StrEnum):
    """Dashboard-verified auto-recharge state."""

    DISABLED = "disabled"
    ENABLED = "enabled"
    UNKNOWN = "unknown"


class ProviderState(StrEnum):
    """Local provider configuration state; it does not perform network I/O."""

    READY = "ready"
    UNCONFIGURED = "unconfigured"
    INVALID = "invalid"


class ProviderOutcome(StrEnum):
    """Normalized result consumed by the future main orchestrator."""

    SUCCESS = "success"
    FALLBACK_ALLOWED = "fallback_allowed"
    STOP_AND_WAIT = "stop_and_wait"
    BLOCKED = "blocked"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FailureReason(StrEnum):
    """Stable reasons used by failover policy without exposing raw errors."""

    AUTHENTICATION = "authentication"
    INVALID_CONFIGURATION = "invalid_configuration"
    QUOTA_EXHAUSTED = "quota_exhausted"
    BALANCE_EXHAUSTED = "balance_exhausted"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    DISCOVERY_UNSUPPORTED = "discovery_unsupported"
    PAID_PROVIDER_BLOCKED = "paid_provider_blocked"
    BILLING_SAFETY_UNVERIFIED = "billing_safety_unverified"
    AUTO_RECHARGE_RISK = "auto_recharge_risk"
    FREE_TIER_UNVERIFIED = "free_tier_unverified"
    DUPLICATE_REQUEST = "duplicate_request"
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One provider-neutral chat message."""

    role: str
    content: str

    def __post_init__(self) -> None:
        normalized_role = self.role.strip().lower()
        if normalized_role not in {"system", "user", "assistant"}:
            raise ValueError("Message role must be system, user, or assistant.")
        if not self.content or not self.content.strip():
            raise ValueError("Message content must not be empty.")
        object.__setattr__(self, "role", normalized_role)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Provider-neutral generation request prepared for future task routing."""

    messages: tuple[ChatMessage, ...]
    task_type: str = "general"
    temperature: float | None = None
    max_output_tokens: int | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if not self.messages:
            raise ValueError("At least one chat message is required.")
        if not self.task_type or not self.task_type.strip():
            raise ValueError("Task type must not be empty.")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("Temperature must be between 0 and 2.")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive.")
        normalized_request_id = self.request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be empty.")
        object.__setattr__(self, "request_id", normalized_request_id)

    @classmethod
    def from_text(cls, text: str, *, task_type: str = "general") -> "ChatRequest":
        return cls(messages=(ChatMessage(role="user", content=text),), task_type=task_type)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts returned by a provider, when available."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Normalized successful provider response."""

    provider_name: str
    model_id: str
    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_name.strip():
            raise ValueError("Provider name must not be empty.")
        if not self.model_id.strip():
            raise ValueError("Model ID must not be empty.")
        if not self.content.strip():
            raise ValueError("Provider content must not be empty.")

    def to_usage_record(self) -> "UsageRecord":
        return UsageRecord(
            provider_name=self.provider_name,
            model_id=self.model_id,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            total_tokens=self.usage.total_tokens,
            provider_request_id=self.provider_request_id,
        )


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Secret-free accounting record for one successful request."""

    provider_name: str
    model_id: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    provider_request_id: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Provider-reported model metadata without a hardcoded model catalogue."""

    provider_name: str
    model_id: str
    display_name: str | None = None
    billing_tier: BillingTier = BillingTier.UNKNOWN
    capabilities: tuple[str, ...] = ()
    description: str | None = None
    base_model_id: str | None = None
    version: str | None = None
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    thinking: bool | None = None


@dataclass(frozen=True, slots=True)
class BillingSafety:
    """Explicit billing facts required before any live generation call."""

    billing_tier: BillingTier
    prepaid_balance_status: PrepaidBalanceStatus = PrepaidBalanceStatus.UNKNOWN
    auto_recharge_status: AutoRechargeStatus = AutoRechargeStatus.UNKNOWN
    free_tier_verified: bool = False


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Local provider status safe for health output and logs."""

    provider_name: str
    state: ProviderState
    configured_model: str | None
    billing_tier: BillingTier
    billing_safety_verified: bool
    model_discovery_supported: bool
    message: str


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """Secret-free failed attempt retained by a provider chain error."""

    provider_name: str
    reason: FailureReason
    outcome: ProviderOutcome
    outcome_unknown: bool = False


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    """Normalized manager result that never needs to expose a raw exception."""

    request_id: str
    outcome: ProviderOutcome
    response: ProviderResponse | None = None
    attempts: tuple[ProviderAttempt, ...] = ()
    reason: FailureReason | None = None
    safe_message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.outcome is ProviderOutcome.SUCCESS and self.response is not None


def optional_token_count(value: Any) -> int | None:
    """Return a valid non-negative integer token count, otherwise None."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
