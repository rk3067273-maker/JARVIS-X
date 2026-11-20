"""Pure policy tests for Phase 2D intelligent model routing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.provider_setup.models import ProviderId  # noqa: E402
from jarvis_x.provider_setup.routing import (  # noqa: E402
    ModelCapability,
    ModelPreference,
    ModelRouter,
    ReasoningLevel,
    RoutableModel,
    RoutingMode,
    RoutingOutcome,
    RoutingPreferences,
    SpeedLevel,
    TaskRequirements,
)
from jarvis_x.providers.models import (  # noqa: E402
    AutoRechargeStatus,
    BillingTier,
    PrepaidBalanceStatus,
)


def safe_agentrouter(**overrides) -> RoutableModel:
    values = {
        "provider": ProviderId.AGENTROUTER,
        "model_id": "gpt-6.1-sol",
        "availability_verified": True,
        "billing_tier": BillingTier.PREPAID,
        "capabilities": frozenset(
            {
                ModelCapability.TEXT,
                ModelCapability.CODING,
                ModelCapability.REASONING,
                ModelCapability.TOOLS,
            }
        ),
        "context_window": 200_000,
        "reasoning_level": ReasoningLevel.ADVANCED,
        "speed_level": SpeedLevel.STANDARD,
        "prepaid_balance_status": PrepaidBalanceStatus.AVAILABLE,
        "auto_recharge_status": AutoRechargeStatus.DISABLED,
    }
    values.update(overrides)
    return RoutableModel(**values)


def safe_gemini(model_id: str = "models/verified-free", **overrides) -> RoutableModel:
    values = {
        "provider": ProviderId.GEMINI,
        "model_id": model_id,
        "availability_verified": True,
        "billing_tier": BillingTier.FREE,
        "capabilities": frozenset(
            {
                ModelCapability.TEXT,
                ModelCapability.CODING,
                ModelCapability.REASONING,
                ModelCapability.VISION,
            }
        ),
        "context_window": 1_000_000,
        "reasoning_level": ReasoningLevel.ADVANCED,
        "speed_level": SpeedLevel.FAST,
        "free_tier_verified_model_id": model_id,
    }
    values.update(overrides)
    return RoutableModel(**values)


class ModelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ModelRouter()

    def test_agentrouter_remains_primary_for_an_equivalent_safe_task(self) -> None:
        decision = self.router.route(
            TaskRequirements(task_type="coding"),
            (safe_gemini(), safe_agentrouter()),
        )

        self.assertEqual(decision.outcome, RoutingOutcome.SELECTED)
        self.assertEqual(decision.provider, ProviderId.AGENTROUTER)
        self.assertEqual(decision.model_id, "gpt-6.1-sol")

    def test_exhausted_prepaid_balance_uses_only_exact_verified_free_gemini(self) -> None:
        exhausted = safe_agentrouter(
            prepaid_balance_status=PrepaidBalanceStatus.EXHAUSTED
        )
        decision = self.router.route(
            TaskRequirements(task_type="coding"),
            (exhausted, safe_gemini()),
        )

        self.assertEqual(decision.outcome, RoutingOutcome.SELECTED)
        self.assertEqual(decision.provider, ProviderId.GEMINI)

    def test_unknown_or_paid_billing_and_free_tier_mismatch_stop(self) -> None:
        unsafe_models = (
            safe_agentrouter(billing_tier=BillingTier.UNKNOWN),
            safe_gemini(
                billing_tier=BillingTier.PAID,
                free_tier_verified_model_id="models/other",
            ),
        )
        decision = self.router.route(
            TaskRequirements(task_type="coding"),
            unsafe_models,
        )

        self.assertEqual(decision.outcome, RoutingOutcome.STOP_AND_WAIT)
        self.assertIsNone(decision.provider)

    def test_non_enforced_agentrouter_model_is_never_selected(self) -> None:
        wrong = safe_agentrouter(model_id="other-agentrouter-model")
        decision = self.router.route(
            TaskRequirements(task_type="coding"),
            (wrong,),
        )

        self.assertEqual(decision.outcome, RoutingOutcome.STOP_AND_WAIT)

    def test_capability_context_and_reasoning_requirements_fail_closed(self) -> None:
        insufficient = safe_gemini(
            capabilities=frozenset({ModelCapability.TEXT}),
            context_window=4_000,
            reasoning_level=None,
        )
        decision = self.router.route(
            TaskRequirements(
                task_type="vision",
                minimum_context_tokens=100_000,
                minimum_reasoning=ReasoningLevel.STANDARD,
            ),
            (insufficient,),
        )

        self.assertEqual(decision.outcome, RoutingOutcome.STOP_AND_WAIT)

    def test_manual_selection_cannot_bypass_billing_safety(self) -> None:
        paid = safe_gemini(billing_tier=BillingTier.PAID)
        preferences = RoutingPreferences(
            mode=RoutingMode.MANUAL,
            manual_provider=ProviderId.GEMINI,
            manual_model_id=paid.model_id,
        )
        decision = self.router.route(
            TaskRequirements(task_type="general"),
            (paid,),
            preferences=preferences,
        )

        self.assertEqual(decision.outcome, RoutingOutcome.BLOCKED)

    def test_manual_provider_and_model_select_verified_candidate(self) -> None:
        gemini = safe_gemini()
        decision = self.router.route(
            TaskRequirements(task_type="vision"),
            (safe_agentrouter(), gemini),
            preferences=RoutingPreferences(
                mode=RoutingMode.MANUAL,
                manual_provider=ProviderId.GEMINI,
                manual_model_id=gemini.model_id,
            ),
        )

        self.assertEqual(decision.outcome, RoutingOutcome.SELECTED)
        self.assertEqual(decision.provider, ProviderId.GEMINI)

    def test_task_specific_preference_wins_only_when_safe(self) -> None:
        gemini = safe_gemini()
        preferences = RoutingPreferences(
            preferred=ModelPreference(
                provider=ProviderId.AGENTROUTER,
                model_id="gpt-6.1-sol",
            ),
            task_specific={
                "coding": ModelPreference(
                    provider=ProviderId.GEMINI,
                    model_id=gemini.model_id,
                )
            },
        )
        preferred = self.router.route(
            TaskRequirements(task_type="coding", prefer_speed=True),
            (safe_agentrouter(), gemini),
            preferences=preferences,
        )
        unsafe_preferred = self.router.route(
            TaskRequirements(task_type="coding"),
            (
                safe_agentrouter(),
                safe_gemini(free_tier_verified_model_id="models/mismatch"),
            ),
            preferences=preferences,
        )

        self.assertEqual(preferred.provider, ProviderId.GEMINI)
        self.assertEqual(unsafe_preferred.provider, ProviderId.AGENTROUTER)


if __name__ == "__main__":
    unittest.main()
