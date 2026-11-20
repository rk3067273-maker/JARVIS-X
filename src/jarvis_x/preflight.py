"""Network-free secure configuration and live-readiness preflight checks."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from jarvis_x.config import CONFIG_ENV_KEYS, Settings
from jarvis_x.core.errors import ConfigurationError
from jarvis_x.env_file import merge_configuration_environment
from jarvis_x.orchestrator.models import TaskState
from jarvis_x.providers.manager import ProviderManager
from jarvis_x.providers.models import ProviderState
from jarvis_x.runtime_config import CONFIGURATION_PRECEDENCE, load_runtime_values
from jarvis_x.task_interface import run_local_orchestrator_health


class PreflightStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NEEDS_MANUAL_VERIFICATION = "NEEDS_MANUAL_VERIFICATION"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: PreflightStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]
    precedence: str = CONFIGURATION_PRECEDENCE

    @property
    def status(self) -> PreflightStatus:
        statuses = {check.status for check in self.checks}
        if PreflightStatus.FAIL in statuses:
            return PreflightStatus.FAIL
        if PreflightStatus.NOT_CONFIGURED in statuses:
            return PreflightStatus.NOT_CONFIGURED
        if PreflightStatus.NEEDS_MANUAL_VERIFICATION in statuses:
            return PreflightStatus.NEEDS_MANUAL_VERIFICATION
        return PreflightStatus.PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "network_calls": 0,
            "provider_requests": 0,
            "model_discovery": False,
            "paid_operations": 0,
            "configuration_precedence": self.precedence,
            "checks": [check.to_dict() for check in self.checks],
        }


def _value(values: Mapping[str, str], key: str) -> str:
    return values.get(key, "").strip()


def _boolean(values: Mapping[str, str], key: str) -> bool | None:
    value = _value(values, key).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _configured_check(name: str, configured: bool, label: str) -> PreflightCheck:
    return PreflightCheck(
        name=name,
        status=PreflightStatus.PASS if configured else PreflightStatus.NOT_CONFIGURED,
        message=f"{label} is configured." if configured else f"{label} is blank or missing.",
    )


def run_preflight(
    workspace: Path,
    *,
    process_environment: Mapping[str, str] | None = None,
) -> PreflightReport:
    """Inspect local readiness without constructing a request or using a network."""

    workspace = workspace.resolve()
    process_values = os.environ if process_environment is None else process_environment
    checks: list[PreflightCheck] = []

    workspace_ok = (workspace / "pyproject.toml").is_file()
    checks.append(
        PreflightCheck(
            "workspace",
            PreflightStatus.PASS if workspace_ok else PreflightStatus.FAIL,
            "JARVIS X workspace detected." if workspace_ok else "pyproject.toml is missing.",
        )
    )
    python_ok = sys.version_info >= (3, 12) and sys.prefix != sys.base_prefix
    checks.append(
        PreflightCheck(
            "python_environment",
            PreflightStatus.PASS if python_ok else PreflightStatus.FAIL,
            "Project-local Python 3.12+ environment is active."
            if python_ok
            else "Required project-local Python environment is not active.",
        )
    )

    gitignore = workspace / ".gitignore"
    ignored = False
    if gitignore.is_file():
        patterns = {
            line.strip()
            for line in gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        ignored = ".env" in patterns
    checks.append(
        PreflightCheck(
            "env_gitignore",
            PreflightStatus.PASS if ignored else PreflightStatus.FAIL,
            ".env is explicitly Git-ignored."
            if ignored
            else ".env is not explicitly Git-ignored.",
        )
    )

    env_path = workspace / ".env"
    env_exists = env_path.exists() or env_path.is_symlink()
    checks.append(
        PreflightCheck(
            "env_present",
            PreflightStatus.PASS if env_exists else PreflightStatus.NOT_CONFIGURED,
            "Workspace-local .env is present."
            if env_exists
            else "Workspace-local .env is missing.",
        )
    )
    is_symlink = env_path.is_symlink() if env_exists else False
    checks.append(
        PreflightCheck(
            "env_not_symlink",
            PreflightStatus.FAIL if is_symlink else PreflightStatus.PASS,
            ".env symlink is rejected." if is_symlink else ".env is not a symlink.",
        )
    )

    exact_permissions = False
    if env_exists and not is_symlink:
        try:
            exact_permissions = stat.S_IMODE(env_path.stat().st_mode) == 0o600
        except OSError:
            exact_permissions = False
    checks.append(
        PreflightCheck(
            "env_permissions",
            PreflightStatus.PASS
            if exact_permissions
            else PreflightStatus.NOT_CONFIGURED
            if not env_exists
            else PreflightStatus.FAIL,
            ".env permission is exactly 0600."
            if exact_permissions
            else ".env must use exact permission 0600.",
        )
    )

    values = merge_configuration_environment(
        {},
        process_values,
        allowed_keys=CONFIG_ENV_KEYS,
    )
    if env_exists and not is_symlink and exact_permissions:
        try:
            values = load_runtime_values(
                workspace,
                process_environment=process_values,
            )
        except ConfigurationError:
            checks.append(
                PreflightCheck(
                    "env_parse",
                    PreflightStatus.FAIL,
                    ".env could not be parsed safely.",
                )
            )
        else:
            checks.append(
                PreflightCheck("env_parse", PreflightStatus.PASS, ".env parsed safely.")
            )
    else:
        checks.append(
            PreflightCheck(
                "env_parse",
                PreflightStatus.NOT_CONFIGURED,
                ".env parsing was skipped until file safety checks pass.",
            )
        )

    checks.append(
        PreflightCheck(
            "configuration_precedence",
            PreflightStatus.PASS,
            "Recognized process-environment keys override .env values.",
        )
    )

    checks.append(
        PreflightCheck(
            "primary_provider",
            PreflightStatus.PASS
            if _value(values, "JARVIS_PRIMARY_PROVIDER") == "agentrouter"
            else PreflightStatus.FAIL,
            "Primary provider is AgentRouter."
            if _value(values, "JARVIS_PRIMARY_PROVIDER") == "agentrouter"
            else "Primary provider must be agentrouter.",
        )
    )
    checks.append(
        PreflightCheck(
            "paid_providers_disabled",
            PreflightStatus.PASS
            if _boolean(values, "JARVIS_ALLOW_PAID_PROVIDERS") is False
            else PreflightStatus.FAIL,
            "Paid providers are disabled."
            if _boolean(values, "JARVIS_ALLOW_PAID_PROVIDERS") is False
            else "JARVIS_ALLOW_PAID_PROVIDERS must be false.",
        )
    )
    checks.append(
        _configured_check(
            "agentrouter_api_key",
            bool(_value(values, "AGENTROUTER_API_KEY")),
            "AgentRouter API key",
        )
    )

    agent_base_url = _value(values, "AGENTROUTER_BASE_URL")
    checks.append(
        PreflightCheck(
            "agentrouter_base_url",
            PreflightStatus.PASS
            if agent_base_url == "https://agentrouter.org/v1"
            else PreflightStatus.FAIL,
            "AgentRouter base URL matches policy."
            if agent_base_url == "https://agentrouter.org/v1"
            else "AgentRouter base URL does not match policy.",
        )
    )

    agent_model = _value(values, "AGENTROUTER_MODEL")
    checks.append(
        PreflightCheck(
            "agentrouter_model",
            PreflightStatus.PASS
            if agent_model == "gpt-6.1-sol"
            else PreflightStatus.NOT_CONFIGURED
            if not agent_model
            else PreflightStatus.FAIL,
            "AgentRouter model matches enforced gpt-6.1-sol."
            if agent_model == "gpt-6.1-sol"
            else "AgentRouter model must be exactly gpt-6.1-sol.",
        )
    )
    billing_mode = _value(values, "AGENTROUTER_BILLING_MODE")
    checks.append(
        PreflightCheck(
            "agentrouter_billing_mode",
            PreflightStatus.PASS
            if billing_mode == "prepaid"
            else PreflightStatus.FAIL,
            "AgentRouter existing-prepaid mode is attested."
            if billing_mode == "prepaid"
            else "AgentRouter billing mode must be prepaid; unknown is not accepted.",
        )
    )
    discovery_enabled = _boolean(values, "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED")
    checks.append(
        PreflightCheck(
            "agentrouter_model_discovery_disabled",
            PreflightStatus.PASS
            if discovery_enabled is False
            else PreflightStatus.FAIL,
            "Automatic AgentRouter model discovery is disabled."
            if discovery_enabled is False
            else "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED must be false.",
        )
    )

    balance_status = _value(values, "AGENTROUTER_PREPAID_BALANCE_STATUS")
    checks.append(
        PreflightCheck(
            "agentrouter_prepaid_balance",
            PreflightStatus.PASS
            if balance_status == "available"
            else PreflightStatus.NEEDS_MANUAL_VERIFICATION
            if balance_status in {"", "unknown"}
            else PreflightStatus.FAIL,
            "Prepaid balance is manually attested as available; current live balance is not verified."
            if balance_status == "available"
            else "Prepaid balance requires fresh manual dashboard verification.",
        )
    )
    recharge_status = _value(values, "AGENTROUTER_AUTO_RECHARGE_STATUS")
    checks.append(
        PreflightCheck(
            "agentrouter_auto_recharge",
            PreflightStatus.PASS
            if recharge_status == "disabled"
            else PreflightStatus.NEEDS_MANUAL_VERIFICATION
            if recharge_status in {"", "unknown"}
            else PreflightStatus.FAIL,
            "Auto recharge is manually attested as disabled; dashboard state is not independently verified."
            if recharge_status == "disabled"
            else "Auto recharge requires manual dashboard verification as disabled.",
        )
    )

    checks.append(
        _configured_check(
            "gemini_api_key",
            bool(_value(values, "GEMINI_API_KEY")),
            "Gemini API key",
        )
    )
    gemini_base_url = _value(values, "GEMINI_BASE_URL")
    checks.append(
        PreflightCheck(
            "gemini_base_url",
            PreflightStatus.PASS
            if gemini_base_url
            == "https://generativelanguage.googleapis.com/v1beta"
            else PreflightStatus.FAIL,
            "Gemini base URL matches policy."
            if gemini_base_url
            == "https://generativelanguage.googleapis.com/v1beta"
            else "Gemini base URL does not match policy.",
        )
    )
    gemini_model = _value(values, "GEMINI_MODEL")
    checks.append(
        _configured_check("gemini_model", bool(gemini_model), "Gemini model")
    )
    gemini_verified = _boolean(values, "GEMINI_FREE_TIER_VERIFIED") is True
    verified_model = _value(values, "GEMINI_FREE_TIER_VERIFIED_MODEL")
    if not gemini_verified:
        gemini_status = PreflightStatus.NEEDS_MANUAL_VERIFICATION
        gemini_message = "Gemini Free Tier requires explicit manual verification."
    elif not gemini_model or not verified_model:
        gemini_status = PreflightStatus.NOT_CONFIGURED
        gemini_message = "Gemini model and verified-model fields must both be configured."
    elif gemini_model != verified_model:
        gemini_status = PreflightStatus.FAIL
        gemini_message = "Gemini Free Tier verification does not match the configured model."
    else:
        gemini_status = PreflightStatus.PASS
        gemini_message = (
            "Exact-model Gemini Free Tier manual attestation is configured; current quota is not verified."
        )
    checks.append(
        PreflightCheck("gemini_free_tier_exact_model", gemini_status, gemini_message)
    )

    try:
        settings = Settings.from_env(values)
        manager = ProviderManager.from_settings(settings)
        statuses = manager.statuses()
    except (ConfigurationError, ValueError):
        checks.append(
            PreflightCheck(
                "provider_manager",
                PreflightStatus.FAIL,
                "ProviderManager configuration is invalid.",
            )
        )
    else:
        states = {status.state for status in statuses}
        if states == {ProviderState.READY}:
            manager_status = PreflightStatus.PASS
            manager_message = "ProviderManager local configuration is ready."
        elif ProviderState.INVALID in states:
            manager_status = PreflightStatus.FAIL
            manager_message = "ProviderManager contains an invalid provider configuration."
        else:
            manager_status = PreflightStatus.NOT_CONFIGURED
            manager_message = "ProviderManager is local-only but one or more providers are unconfigured."
        checks.append(
            PreflightCheck("provider_manager", manager_status, manager_message)
        )

    orchestrator_health = run_local_orchestrator_health()
    orchestrator_ok = (
        orchestrator_health.get("status") == "healthy"
        and orchestrator_health.get("state") == TaskState.COMPLETED.name
        and orchestrator_health.get("provider_api_calls") == 0
    )
    checks.append(
        PreflightCheck(
            "orchestrator",
            PreflightStatus.PASS if orchestrator_ok else PreflightStatus.FAIL,
            "Local orchestrator health passed with zero provider API calls."
            if orchestrator_ok
            else "Local orchestrator health failed.",
        )
    )

    return PreflightReport(tuple(checks))
