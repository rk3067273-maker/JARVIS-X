"""Non-destructive local health checks for JARVIS X."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from jarvis_x import __version__
from jarvis_x.config import Settings


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of one safe health check."""

    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Serializable aggregate health report."""

    application: str
    version: str
    status: str
    timestamp: str
    checks: tuple[CheckResult, ...]

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"

    def to_dict(self) -> dict[str, object]:
        return {
            "application": self.application,
            "version": self.version,
            "status": self.status,
            "timestamp": self.timestamp,
            "checks": [asdict(check) for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _safe_check(name: str, operation: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name=name, status="pass", message=operation())
    except Exception as error:  # Report only the type; raw messages may contain secrets.
        return CheckResult(
            name=name,
            status="fail",
            message=f"Check failed with {type(error).__name__}",
        )


def _check_python() -> str:
    if sys.version_info < (3, 12):
        raise RuntimeError("Unsupported Python runtime")
    return f"Python {platform.python_version()}"


def _check_virtual_environment() -> str:
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Virtual environment is not active")
    return "Project-local virtual environment is active"


def _check_platform() -> str:
    system = platform.system()
    machine = platform.machine()
    if not system or not machine:
        raise RuntimeError("Platform metadata is unavailable")
    return f"{system} {machine}"


def _check_workspace(workspace: Path) -> str:
    if not workspace.is_dir():
        raise RuntimeError("Workspace is unavailable")
    return str(workspace)


def _check_storage(workspace: Path) -> str:
    usage = shutil.disk_usage(workspace)
    if usage.free <= 0:
        raise RuntimeError("No free storage")
    free_gib = usage.free / (1024**3)
    return f"{free_gib:.2f} GiB free"


def run_health_checks(settings: Settings, workspace: Path | None = None) -> HealthReport:
    """Run local checks without creating files or contacting a network."""

    current_workspace = (workspace or Path.cwd()).resolve()
    checks = (
        _safe_check("python", _check_python),
        _safe_check("virtual_environment", _check_virtual_environment),
        _safe_check("platform", _check_platform),
        _safe_check("workspace", lambda: _check_workspace(current_workspace)),
        _safe_check("storage", lambda: _check_storage(current_workspace)),
        CheckResult(
            name="configuration",
            status="pass",
            message=f"environment={settings.environment}, log_level={settings.log_level}",
        ),
        CheckResult(
            name="provider_policy",
            status="pass",
            message=(
                f"primary={settings.primary_provider}, "
                f"agentrouter_configured={settings.agentrouter_api_key is not None and settings.agentrouter_model is not None}, "
                f"agentrouter_billing_mode={settings.agentrouter_billing_mode}, "
                f"prepaid_balance={settings.agentrouter_prepaid_balance_status}, "
                f"auto_recharge={settings.agentrouter_auto_recharge_status}, "
                f"gemini_configured={settings.gemini_api_key is not None and settings.gemini_model is not None}, "
                f"gemini_free_tier_verified={settings.gemini_free_tier_verified and settings.gemini_model is not None and settings.gemini_free_tier_verified_model == settings.gemini_model}, "
                f"paid_providers_allowed={settings.allow_paid_providers}"
            ),
        ),
    )
    status = "healthy" if all(check.status == "pass" for check in checks) else "degraded"

    return HealthReport(
        application="JARVIS X",
        version=__version__,
        status=status,
        timestamp=datetime.now(UTC).isoformat(),
        checks=checks,
    )
