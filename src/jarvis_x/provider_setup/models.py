"""Secret-free models for app-managed provider setup and discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from jarvis_x.providers.models import BillingTier


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ProviderId(StrEnum):
    """Stable provider identifiers shared by the app and backend boundary."""

    AGENTROUTER = "agentrouter"
    GEMINI = "gemini"


class VerificationStatus(StrEnum):
    """User-facing result of a non-inference credential check."""

    NOT_CONFIGURED = "not_configured"
    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"
    INVALID_KEY = "invalid_key"
    PERMISSION_DENIED = "permission_denied"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
    NETWORK_FAILURE = "network_failure"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_ERROR = "provider_error"


class VerificationOperation(StrEnum):
    """Operation used to verify a credential without hidden inference."""

    MODEL_LIST_METADATA = "model_list_metadata"
    UNSUPPORTED = "unsupported"
    NONE = "none"


class ChargeRisk(StrEnum):
    """Conservative charge disclosure for a control-plane operation."""

    NO_INFERENCE_EXPECTED = "no_inference_expected"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class DiscoveryStatus(StrEnum):
    """Model discovery/cache refresh state."""

    NOT_ATTEMPTED = "not_attempted"
    SUCCESS = "success"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    provider: ProviderId
    status: VerificationStatus
    operation: VerificationOperation
    charge_risk: ChargeRisk
    inference_used: bool
    safe_message: str
    checked_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """Only metadata actually returned by a provider metadata endpoint."""

    provider: ProviderId
    model_id: str
    display_name: str | None = None
    description: str | None = None
    base_model_id: str | None = None
    version: str | None = None
    provider_capabilities: tuple[str, ...] = ()
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    thinking: bool | None = None
    billing_tier: BillingTier = BillingTier.UNKNOWN
    free_tier_eligible: bool | None = None

    def __post_init__(self) -> None:
        model_id = self.model_id.strip()
        if not model_id:
            raise ValueError("Discovered model ID must not be empty.")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "provider_capabilities", tuple(self.provider_capabilities))


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    models: tuple[DiscoveredModel, ...]
    next_page_token: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", tuple(self.models))
        token = self.next_page_token.strip() if self.next_page_token else None
        object.__setattr__(self, "next_page_token", token or None)


@dataclass(frozen=True, slots=True)
class ModelCacheEntry:
    provider: ProviderId
    models: tuple[DiscoveredModel, ...]
    refreshed_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "models", tuple(self.models))


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    provider: ProviderId
    status: DiscoveryStatus
    safe_message: str
    cache: ModelCacheEntry | None = None
