"""Safe domain errors for JARVIS X."""

from __future__ import annotations


class JarvisXError(Exception):
    """Base error carrying only a user-safe message and stable code."""

    default_code = "jarvis_x_error"

    def __init__(self, safe_message: str, *, code: str | None = None) -> None:
        self.safe_message = safe_message
        self.code = code or self.default_code
        super().__init__(safe_message)


class ConfigurationError(JarvisXError):
    """Raised when environment-based configuration is invalid."""

    default_code = "configuration_error"


class HealthCheckError(JarvisXError):
    """Raised when the health-check subsystem cannot produce a report."""

    default_code = "health_check_error"
