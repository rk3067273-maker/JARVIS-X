"""Central secure runtime configuration for preflight and real task execution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_x.config import CONFIG_ENV_KEYS, Settings
from jarvis_x.core.errors import ConfigurationError
from jarvis_x.env_file import load_local_env, merge_configuration_environment

CONFIGURATION_PRECEDENCE = ".env base; recognized process-environment keys override"


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Validated settings while keeping merged raw values out of repr output."""

    settings: Settings
    _values: Mapping[str, str] = field(repr=False)
    precedence: str = CONFIGURATION_PRECEDENCE

    def is_explicitly_configured(self, key: str) -> bool:
        return bool(self._values.get(key, "").strip())


def load_runtime_values(
    workspace: Path,
    *,
    process_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Load strict local `.env`, then apply recognized process overrides."""

    local_values = load_local_env(workspace.resolve() / ".env")
    process_values = os.environ if process_environment is None else process_environment
    return merge_configuration_environment(
        local_values,
        process_values,
        allowed_keys=CONFIG_ENV_KEYS,
    )


def validate_task_runtime(values: Mapping[str, str], settings: Settings) -> None:
    """Fail closed before ProviderManager is built or any request can be sent."""

    issues: list[str] = []

    def raw(key: str) -> str:
        return values.get(key, "").strip()

    if raw("JARVIS_PRIMARY_PROVIDER").lower() != "agentrouter":
        issues.append("JARVIS_PRIMARY_PROVIDER must be agentrouter")
    if raw("JARVIS_ALLOW_PAID_PROVIDERS").lower() not in {"false", "0", "no", "off"}:
        issues.append("JARVIS_ALLOW_PAID_PROVIDERS must be false")
    if raw("AGENTROUTER_BASE_URL") != "https://agentrouter.org/v1":
        issues.append("AGENTROUTER_BASE_URL must match policy")
    if not raw("AGENTROUTER_API_KEY") or settings.agentrouter_api_key is None:
        issues.append("AGENTROUTER_API_KEY is blank or missing")
    if raw("AGENTROUTER_MODEL") != "gpt-6.1-sol":
        issues.append("AGENTROUTER_MODEL must be gpt-6.1-sol")
    if raw("AGENTROUTER_BILLING_MODE").lower() != "prepaid":
        issues.append("AGENTROUTER_BILLING_MODE must be prepaid")
    if raw("AGENTROUTER_PREPAID_BALANCE_STATUS").lower() not in {
        "available",
        "exhausted",
        "unavailable",
    }:
        issues.append("AGENTROUTER_PREPAID_BALANCE_STATUS requires explicit attestation")
    if raw("AGENTROUTER_AUTO_RECHARGE_STATUS").lower() != "disabled":
        issues.append("AGENTROUTER_AUTO_RECHARGE_STATUS must be disabled")
    if raw("AGENTROUTER_MODELS_ENDPOINT_SUPPORTED").lower() not in {
        "false",
        "0",
        "no",
        "off",
    }:
        issues.append("AGENTROUTER_MODELS_ENDPOINT_SUPPORTED must be false")

    if raw("GEMINI_BASE_URL") != "https://generativelanguage.googleapis.com/v1beta":
        issues.append("GEMINI_BASE_URL must match policy")
    if not raw("GEMINI_API_KEY") or settings.gemini_api_key is None:
        issues.append("GEMINI_API_KEY is blank or missing")
    gemini_model = raw("GEMINI_MODEL")
    verified_model = raw("GEMINI_FREE_TIER_VERIFIED_MODEL")
    if not gemini_model:
        issues.append("GEMINI_MODEL is blank or missing")
    if raw("GEMINI_FREE_TIER_VERIFIED").lower() not in {"true", "1", "yes", "on"}:
        issues.append("GEMINI_FREE_TIER_VERIFIED must be true")
    if not verified_model or verified_model != gemini_model:
        issues.append("GEMINI Free Tier verified model must exactly match GEMINI_MODEL")

    if issues:
        raise ConfigurationError("Runtime configuration is not ready: " + "; ".join(issues) + ".")


def load_runtime_configuration(
    workspace: Path,
    *,
    process_environment: Mapping[str, str] | None = None,
) -> RuntimeConfiguration:
    """Central entry point shared by actual task execution and preflight logic."""

    values = load_runtime_values(
        workspace,
        process_environment=process_environment,
    )
    settings = Settings.from_env(values)
    validate_task_runtime(values, settings)
    return RuntimeConfiguration(settings=settings, _values=values)
