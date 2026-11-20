"""Environment-based configuration with secret-safe representations."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_x.core.errors import ConfigurationError

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_VALID_AGENTROUTER_BILLING_MODES = frozenset({"prepaid", "paid", "unknown"})
_VALID_PREPAID_BALANCE_STATUSES = frozenset(
    {"available", "exhausted", "unavailable", "unknown"}
)
_VALID_AUTO_RECHARGE_STATUSES = frozenset({"disabled", "enabled", "unknown"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_AGENTROUTER_BASE_URL = "https://agentrouter.org/v1"
_AGENTROUTER_PRIMARY_MODEL = "gpt-6.1-sol"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

CONFIG_ENV_KEYS = frozenset(
    {
        "JARVIS_ENV",
        "JARVIS_LOG_LEVEL",
        "JARVIS_DATA_DIR",
        "JARVIS_LOG_DIR",
        "JARVIS_PRIMARY_PROVIDER",
        "JARVIS_ALLOW_PAID_PROVIDERS",
        "AGENTROUTER_BASE_URL",
        "AGENTROUTER_API_KEY",
        "AGENTROUTER_MODEL",
        "AGENTROUTER_BILLING_MODE",
        "AGENTROUTER_BILLING_TIER",
        "AGENTROUTER_PREPAID_BALANCE_STATUS",
        "AGENTROUTER_AUTO_RECHARGE_STATUS",
        "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED",
        "GEMINI_BASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FREE_TIER_VERIFIED",
        "GEMINI_FREE_TIER_VERIFIED_MODEL",
    }
)


def _parse_boolean(name: str, raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


@dataclass(frozen=True, slots=True)
class SecretValue:
    """Keep a secret out of normal string and repr output."""

    _value: str = field(repr=False)

    def reveal(self) -> str:
        """Return the value only for an explicitly authorized integration."""

        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __str__(self) -> str:
        return "<redacted>"

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings loaded from environment variables."""

    environment: str = "development"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    primary_provider: str = "agentrouter"
    allow_paid_providers: bool = False
    agentrouter_base_url: str = field(default=_AGENTROUTER_BASE_URL, repr=False)
    agentrouter_api_key: SecretValue | None = field(default=None, repr=False)
    agentrouter_model: str = _AGENTROUTER_PRIMARY_MODEL
    agentrouter_billing_mode: str = "unknown"
    agentrouter_prepaid_balance_status: str = "unknown"
    agentrouter_auto_recharge_status: str = "unknown"
    agentrouter_models_endpoint_supported: bool = False
    gemini_base_url: str = field(default=_GEMINI_BASE_URL, repr=False)
    gemini_api_key: SecretValue | None = field(default=None, repr=False)
    gemini_model: str | None = None
    gemini_free_tier_verified: bool = False
    gemini_free_tier_verified_model: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Load and validate settings without modifying the filesystem."""

        source = os.environ if environ is None else environ

        environment = source.get("JARVIS_ENV", "development").strip()
        if not environment or not _ENVIRONMENT_NAME.fullmatch(environment):
            raise ConfigurationError(
                "JARVIS_ENV may contain only letters, numbers, dots, underscores, and hyphens."
            )

        log_level = source.get("JARVIS_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
            raise ConfigurationError(f"JARVIS_LOG_LEVEL must be one of: {allowed}.")

        data_dir = Path(source.get("JARVIS_DATA_DIR", "data").strip() or "data").expanduser()
        log_dir = Path(source.get("JARVIS_LOG_DIR", "logs").strip() or "logs").expanduser()

        primary_provider = source.get("JARVIS_PRIMARY_PROVIDER", "agentrouter").strip().lower()
        if primary_provider != "agentrouter":
            raise ConfigurationError("Phase 2C.1 requires JARVIS_PRIMARY_PROVIDER=agentrouter.")

        allow_paid_providers = _parse_boolean(
            "JARVIS_ALLOW_PAID_PROVIDERS",
            source.get("JARVIS_ALLOW_PAID_PROVIDERS", "false"),
        )
        if allow_paid_providers:
            raise ConfigurationError("Phase 2C.1 requires JARVIS_ALLOW_PAID_PROVIDERS=false.")

        agentrouter_base_url = (
            source.get("AGENTROUTER_BASE_URL", _AGENTROUTER_BASE_URL).strip()
            or _AGENTROUTER_BASE_URL
        )
        raw_agentrouter_key = source.get("AGENTROUTER_API_KEY", "").strip()
        agentrouter_api_key = SecretValue(raw_agentrouter_key) if raw_agentrouter_key else None
        agentrouter_model = (
            source.get("AGENTROUTER_MODEL", _AGENTROUTER_PRIMARY_MODEL).strip()
            or _AGENTROUTER_PRIMARY_MODEL
        )
        if agentrouter_model != _AGENTROUTER_PRIMARY_MODEL:
            raise ConfigurationError(
                f"Phase 2C.1 requires AGENTROUTER_MODEL={_AGENTROUTER_PRIMARY_MODEL}."
            )
        agentrouter_billing_mode = source.get(
            "AGENTROUTER_BILLING_MODE",
            source.get("AGENTROUTER_BILLING_TIER", "unknown"),
        ).strip().lower()
        if agentrouter_billing_mode not in _VALID_AGENTROUTER_BILLING_MODES:
            raise ConfigurationError(
                "AGENTROUTER_BILLING_MODE must be prepaid, paid, or unknown."
            )
        agentrouter_prepaid_balance_status = source.get(
            "AGENTROUTER_PREPAID_BALANCE_STATUS", "unknown"
        ).strip().lower()
        if agentrouter_prepaid_balance_status not in _VALID_PREPAID_BALANCE_STATUSES:
            raise ConfigurationError(
                "AGENTROUTER_PREPAID_BALANCE_STATUS must be available, exhausted, unavailable, or unknown."
            )
        agentrouter_auto_recharge_status = source.get(
            "AGENTROUTER_AUTO_RECHARGE_STATUS", "unknown"
        ).strip().lower()
        if agentrouter_auto_recharge_status not in _VALID_AUTO_RECHARGE_STATUSES:
            raise ConfigurationError(
                "AGENTROUTER_AUTO_RECHARGE_STATUS must be disabled, enabled, or unknown."
            )
        agentrouter_models_endpoint_supported = _parse_boolean(
            "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED",
            source.get("AGENTROUTER_MODELS_ENDPOINT_SUPPORTED", "false"),
        )
        if agentrouter_models_endpoint_supported:
            raise ConfigurationError(
                "AgentRouter model discovery is disabled because no official metadata endpoint is documented."
            )

        gemini_base_url = (
            source.get("GEMINI_BASE_URL", _GEMINI_BASE_URL).strip() or _GEMINI_BASE_URL
        )
        raw_gemini_key = source.get("GEMINI_API_KEY", "").strip()
        gemini_api_key = SecretValue(raw_gemini_key) if raw_gemini_key else None
        gemini_model = source.get("GEMINI_MODEL", "").strip() or None
        gemini_free_tier_verified = _parse_boolean(
            "GEMINI_FREE_TIER_VERIFIED",
            source.get("GEMINI_FREE_TIER_VERIFIED", "false"),
        )
        gemini_free_tier_verified_model = (
            source.get("GEMINI_FREE_TIER_VERIFIED_MODEL", "").strip() or None
        )

        return cls(
            environment=environment,
            log_level=log_level,
            data_dir=data_dir,
            log_dir=log_dir,
            primary_provider=primary_provider,
            allow_paid_providers=allow_paid_providers,
            agentrouter_base_url=agentrouter_base_url,
            agentrouter_api_key=agentrouter_api_key,
            agentrouter_model=agentrouter_model,
            agentrouter_billing_mode=agentrouter_billing_mode,
            agentrouter_prepaid_balance_status=agentrouter_prepaid_balance_status,
            agentrouter_auto_recharge_status=agentrouter_auto_recharge_status,
            agentrouter_models_endpoint_supported=agentrouter_models_endpoint_supported,
            gemini_base_url=gemini_base_url,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            gemini_free_tier_verified=gemini_free_tier_verified,
            gemini_free_tier_verified_model=gemini_free_tier_verified_model,
        )

    def secret_values(self) -> tuple[str, ...]:
        """Return configured secrets for the logging redactor."""

        secrets: list[str] = []
        if self.agentrouter_api_key is not None:
            secrets.append(self.agentrouter_api_key.reveal())
        if self.gemini_api_key is not None:
            secrets.append(self.gemini_api_key.reveal())
        return tuple(secrets)

    def safe_summary(self) -> dict[str, object]:
        """Return diagnostics that deliberately exclude secret material."""

        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "data_dir": str(self.data_dir),
            "log_dir": str(self.log_dir),
            "primary_provider": self.primary_provider,
            "allow_paid_providers": self.allow_paid_providers,
            "agentrouter_base_url_configured": bool(self.agentrouter_base_url),
            "agentrouter_api_key_configured": self.agentrouter_api_key is not None,
            "agentrouter_model_configured": self.agentrouter_model is not None,
            "agentrouter_billing_mode": self.agentrouter_billing_mode,
            "agentrouter_prepaid_balance_status": self.agentrouter_prepaid_balance_status,
            "agentrouter_auto_recharge_status": self.agentrouter_auto_recharge_status,
            "agentrouter_models_endpoint_supported": self.agentrouter_models_endpoint_supported,
            "gemini_base_url_configured": bool(self.gemini_base_url),
            "gemini_api_key_configured": self.gemini_api_key is not None,
            "gemini_model_configured": self.gemini_model is not None,
            "gemini_free_tier_verified": self.gemini_free_tier_verified
            and self.gemini_model is not None
            and self.gemini_free_tier_verified_model == self.gemini_model,
        }
