"""Pure, fail-closed model selection policy for verified model metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Mapping

from jarvis_x.provider_setup.models import ProviderId
from jarvis_x.providers.agentrouter import PRIMARY_MODEL_ID
from jarvis_x.providers.models import (
    AutoRechargeStatus,
    BillingTier,
    PrepaidBalanceStatus,
)


class ModelCapability(StrEnum):
    TEXT = "text"
    CODING = "coding"
    REASONING = "reasoning"
    VISION = "vision"
    AUDIO = "audio"
    TOOLS = "tools"


class ReasoningLevel(IntEnum):
    BASIC = 1
    STANDARD = 2
    ADVANCED = 3


class SpeedLevel(IntEnum):
    SLOW = 1
    STANDARD = 2
    FAST = 3


class RoutingMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class RoutingOutcome(StrEnum):
    SELECTED = "selected"
    STOP_AND_WAIT = "stop_and_wait"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ModelPreference:
    provider: ProviderId
    model_id: str


@dataclass(frozen=True, slots=True)
class TaskRequirements:
    task_type: str = "general"
    required_capabilities: frozenset[ModelCapability] = frozenset()
    minimum_context_tokens: int = 0
    minimum_reasoning: ReasoningLevel | None = None
    prefer_speed: bool = False

    def __post_init__(self) -> None:
        if not self.task_type.strip():
            raise ValueError("Task type must not be blank.")
        if self.minimum_context_tokens < 0:
            raise ValueError("Minimum context tokens must not be negative.")
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(self.required_capabilities),
        )


@dataclass(frozen=True, slots=True)
class RoutingPreferences:
    mode: RoutingMode = RoutingMode.AUTOMATIC
    manual_provider: ProviderId | None = None
    manual_model_id: str | None = None
    preferred: ModelPreference | None = None
    task_specific: Mapping[str, ModelPreference] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoutableModel:
    provider: ProviderId
    model_id: str
    availability_verified: bool
    billing_tier: BillingTier
    capabilities: frozenset[ModelCapability] = frozenset()
    context_window: int | None = None
    reasoning_level: ReasoningLevel | None = None
    speed_level: SpeedLevel | None = None
    prepaid_balance_status: PrepaidBalanceStatus = PrepaidBalanceStatus.UNKNOWN
    auto_recharge_status: AutoRechargeStatus = AutoRechargeStatus.UNKNOWN
    free_tier_verified_model_id: str | None = None

    def __post_init__(self) -> None:
        model_id = self.model_id.strip()
        if not model_id:
            raise ValueError("Routable model ID must not be blank.")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("Context window must be positive when known.")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    outcome: RoutingOutcome
    provider: ProviderId | None
    model_id: str | None
    safe_message: str


class TaskClassifier:
    """Classify explicit task types without inspecting or logging prompt content."""

    _PROFILES: Mapping[str, tuple[frozenset[ModelCapability], ReasoningLevel | None]] = {
        "general": (frozenset({ModelCapability.TEXT}), None),
        "coding": (
            frozenset({ModelCapability.TEXT, ModelCapability.CODING}),
            ReasoningLevel.STANDARD,
        ),
        "reasoning": (
            frozenset({ModelCapability.TEXT, ModelCapability.REASONING}),
            ReasoningLevel.ADVANCED,
        ),
        "vision": (frozenset({ModelCapability.TEXT, ModelCapability.VISION}), None),
        "audio": (frozenset({ModelCapability.TEXT, ModelCapability.AUDIO}), None),
        "tools": (frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}), None),
    }

    def classify(self, requirements: TaskRequirements) -> TaskRequirements:
        task_type = requirements.task_type.strip().lower()
        profile_capabilities, profile_reasoning = self._PROFILES.get(
            task_type,
            self._PROFILES["general"],
        )
        return TaskRequirements(
            task_type=task_type,
            required_capabilities=(
                profile_capabilities | requirements.required_capabilities
            ),
            minimum_context_tokens=requirements.minimum_context_tokens,
            minimum_reasoning=requirements.minimum_reasoning or profile_reasoning,
            prefer_speed=requirements.prefer_speed,
        )


class ModelRouter:
    """Select only verified, capability-matching, billing-safe models."""

    def __init__(self, classifier: TaskClassifier | None = None) -> None:
        self._classifier = classifier or TaskClassifier()

    @staticmethod
    def _billing_safe(model: RoutableModel) -> bool:
        if not model.availability_verified:
            return False
        if model.provider is ProviderId.AGENTROUTER:
            return (
                model.model_id == PRIMARY_MODEL_ID
                and model.billing_tier is BillingTier.PREPAID
                and model.prepaid_balance_status is PrepaidBalanceStatus.AVAILABLE
                and model.auto_recharge_status is AutoRechargeStatus.DISABLED
            )
        if model.provider is ProviderId.GEMINI:
            return (
                model.billing_tier is BillingTier.FREE
                and model.free_tier_verified_model_id == model.model_id
            )
        return False

    @staticmethod
    def _meets_requirements(
        model: RoutableModel,
        requirements: TaskRequirements,
    ) -> bool:
        if not requirements.required_capabilities.issubset(model.capabilities):
            return False
        if requirements.minimum_context_tokens:
            if (
                model.context_window is None
                or model.context_window < requirements.minimum_context_tokens
            ):
                return False
        if requirements.minimum_reasoning is not None:
            if (
                model.reasoning_level is None
                or model.reasoning_level < requirements.minimum_reasoning
            ):
                return False
        return True

    @staticmethod
    def _matches(model: RoutableModel, preference: ModelPreference | None) -> bool:
        return bool(
            preference
            and model.provider is preference.provider
            and model.model_id == preference.model_id
        )

    def _rank(
        self,
        model: RoutableModel,
        *,
        requirements: TaskRequirements,
        preferred: ModelPreference | None,
        task_preferred: ModelPreference | None,
    ) -> tuple[int, int, int, int, int, str]:
        return (
            int(self._matches(model, task_preferred)),
            int(self._matches(model, preferred)),
            int(model.reasoning_level or 0),
            int(model.speed_level or 0) if requirements.prefer_speed else 0,
            1 if model.provider is ProviderId.AGENTROUTER else 0,
            model.model_id,
        )

    def route(
        self,
        requirements: TaskRequirements,
        models: tuple[RoutableModel, ...],
        *,
        preferences: RoutingPreferences | None = None,
    ) -> RoutingDecision:
        classified = self._classifier.classify(requirements)
        preferences = preferences or RoutingPreferences()

        if preferences.mode is RoutingMode.MANUAL:
            if preferences.manual_provider is None:
                return RoutingDecision(
                    RoutingOutcome.BLOCKED,
                    None,
                    None,
                    "Manual routing requires a provider selection.",
                )
            manual_candidates = tuple(
                model
                for model in models
                if model.provider is preferences.manual_provider
                and (
                    preferences.manual_model_id is None
                    or model.model_id == preferences.manual_model_id
                )
            )
            if not manual_candidates:
                return RoutingDecision(
                    RoutingOutcome.BLOCKED,
                    None,
                    None,
                    "The manually selected provider or model is not verified as available.",
                )
            eligible_manual = tuple(
                model
                for model in manual_candidates
                if self._billing_safe(model)
                and self._meets_requirements(model, classified)
            )
            if not eligible_manual:
                return RoutingDecision(
                    RoutingOutcome.BLOCKED,
                    None,
                    None,
                    "The manual selection is not billing-safe or lacks required capabilities.",
                )
            selected = max(
                eligible_manual,
                key=lambda model: self._rank(
                    model,
                    requirements=classified,
                    preferred=preferences.preferred,
                    task_preferred=preferences.task_specific.get(classified.task_type),
                ),
            )
            return RoutingDecision(
                RoutingOutcome.SELECTED,
                selected.provider,
                selected.model_id,
                "Verified manual model selected.",
            )

        eligible = tuple(
            model
            for model in models
            if self._billing_safe(model)
            and self._meets_requirements(model, classified)
        )
        if not eligible:
            return RoutingDecision(
                RoutingOutcome.STOP_AND_WAIT,
                None,
                None,
                "No verified billing-safe model satisfies this task.",
            )

        task_preferred = preferences.task_specific.get(classified.task_type)
        selected = max(
            eligible,
            key=lambda model: self._rank(
                model,
                requirements=classified,
                preferred=preferences.preferred,
                task_preferred=task_preferred,
            ),
        )
        return RoutingDecision(
            RoutingOutcome.SELECTED,
            selected.provider,
            selected.model_id,
            "Verified billing-safe model selected automatically.",
        )
