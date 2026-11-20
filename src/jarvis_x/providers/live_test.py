"""Explicit, single-request live connectivity test with fail-closed billing checks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from jarvis_x.config import Settings
from jarvis_x.core.errors import ConfigurationError
from jarvis_x.env_file import load_local_env
from jarvis_x.providers.agentrouter import AgentRouterProvider
from jarvis_x.providers.base import AIProvider, ProviderError
from jarvis_x.providers.gemini import GeminiProvider
from jarvis_x.providers.models import (
    AutoRechargeStatus,
    BillingTier,
    ChatRequest,
    FailureReason,
    PrepaidBalanceStatus,
    ProviderState,
)

_MAX_OUTPUT_TOKENS = 32
_TEST_PROMPT = "Reply with exactly: JARVIS X connection OK"


def build_provider(settings: Settings, provider_name: str) -> AIProvider:
    """Build one live-test provider without fallback or model discovery."""

    if provider_name == "agentrouter":
        return AgentRouterProvider(
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
            models_endpoint_supported=False,
        )
    if provider_name == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            model_id=settings.gemini_model,
            free_tier_verified=settings.gemini_free_tier_verified,
            free_tier_verified_model=settings.gemini_free_tier_verified_model,
        )
    raise ConfigurationError("Unsupported live-test provider.")


def preflight_provider(provider: AIProvider) -> None:
    """Validate local configuration and billing facts without network I/O."""

    status = provider.status()
    if status.state is ProviderState.UNCONFIGURED:
        raise ProviderError(
            provider.name,
            FailureReason.INVALID_CONFIGURATION,
            status.message,
        )
    provider.ensure_billing_safe()
    if status.state is not ProviderState.READY:
        raise ProviderError(
            provider.name,
            FailureReason.INVALID_CONFIGURATION,
            status.message,
        )


def _safe_result(response: object) -> dict[str, object]:
    from jarvis_x.providers.models import ProviderResponse

    if not isinstance(response, ProviderResponse):
        raise TypeError("Unexpected provider response type.")
    return {
        "status": "success",
        "outcome": "success",
        "provider": response.provider_name,
        "model_id": response.model_id,
        "provider_request_id": response.provider_request_id,
        "content_received": bool(response.content),
        "finish_reason": response.finish_reason,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "automatic_retry": False,
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-x-live-test",
        description="Run at most one billing-safe provider request",
    )
    parser.add_argument("--provider", choices=("agentrouter", "gemini"), required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="send exactly one request after all local safety checks pass",
    )
    parser.add_argument("--confirm-auto-recharge-disabled", action="store_true")
    parser.add_argument("--confirm-prepaid-balance-available", action="store_true")
    parser.add_argument("--confirm-free-tier", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    workspace = Path.cwd().resolve()

    try:
        if not (workspace / "pyproject.toml").is_file():
            raise ConfigurationError("Run the live test from the JARVIS X workspace root.")
        env_values = load_local_env(workspace / ".env")
        settings = Settings.from_env(env_values)
        provider = build_provider(settings, arguments.provider)
        preflight_provider(provider)

        if not arguments.execute:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "provider": provider.name,
                        "model_id": provider.model_id,
                        "billing_safety_verified": provider.billing_safety.free_tier_verified
                        or (
                            provider.billing_safety.billing_tier is BillingTier.PREPAID
                            and provider.billing_safety.prepaid_balance_status
                            is PrepaidBalanceStatus.AVAILABLE
                            and provider.billing_safety.auto_recharge_status
                            is AutoRechargeStatus.DISABLED
                        ),
                        "request_sent": False,
                    },
                    sort_keys=True,
                )
            )
            return 0

        if arguments.provider == "agentrouter" and not (
            arguments.confirm_auto_recharge_disabled
            and arguments.confirm_prepaid_balance_available
        ):
            raise ConfigurationError(
                "AgentRouter live execution requires fresh dashboard confirmation of disabled auto recharge and available prepaid balance."
            )
        if arguments.provider == "gemini" and not arguments.confirm_free_tier:
            raise ConfigurationError(
                "Gemini live execution requires explicit confirmation that the configured model is in Free Tier."
            )

        base_request = ChatRequest.from_text(_TEST_PROMPT, task_type="connection_test")
        response = provider.generate(
            ChatRequest(
                messages=base_request.messages,
                task_type=base_request.task_type,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )
        )
        print(json.dumps(_safe_result(response), sort_keys=True))
        return 0
    except ProviderError as error:
        print(
            json.dumps(
                {
                    "status": "blocked" if error.status_code is None else "failed",
                    "provider": error.provider_name,
                    "reason": error.reason.value,
                    "outcome": error.outcome.value,
                    "outcome_unknown": error.outcome_unknown,
                    "request_sent": error.reason
                    not in {
                        FailureReason.INVALID_CONFIGURATION,
                        FailureReason.PAID_PROVIDER_BLOCKED,
                        FailureReason.BILLING_SAFETY_UNVERIFIED,
                        FailureReason.AUTO_RECHARGE_RISK,
                        FailureReason.FREE_TIER_UNVERIFIED,
                        FailureReason.BALANCE_EXHAUSTED,
                    },
                    "message": error.safe_message,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except ConfigurationError as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": error.code,
                    "request_sent": False,
                    "message": error.safe_message,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # Defensive boundary: never print raw exception text.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "unexpected_error",
                    "outcome_unknown": True,
                    "request_sent": True,
                    "message": f"Live test failed with {type(error).__name__}; no retry was attempted.",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
