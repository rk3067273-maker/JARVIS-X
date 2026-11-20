"""Common provider interface, safe errors, and standard-library HTTP transport."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from jarvis_x.core.errors import JarvisXError
from jarvis_x.providers.models import (
    BillingSafety,
    BillingTier,
    ChatRequest,
    FailureReason,
    ModelInfo,
    ProviderAttempt,
    ProviderOutcome,
    ProviderResponse,
    ProviderStatus,
)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """Internal request object whose repr cannot expose headers or body."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Small transport response with a secret-safe repr."""

    status_code: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)


class TransportTimeout(Exception):
    """The request may have reached the provider before timing out."""


class TransportUnavailable(Exception):
    """The provider could not be reached with a confirmed response."""


class HttpTransport(Protocol):
    """Injectable transport used to guarantee mock-only unit tests."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send one request without automatic retries."""


class UrllibTransport:
    """Standard-library transport with strict response-size and retry limits."""

    def send(self, request: HttpRequest) -> HttpResponse:
        raw_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )

        try:
            with urllib.request.urlopen(  # noqa: S310 - HTTPS is validated before use.
                raw_request,
                timeout=request.timeout_seconds,
            ) as response:
                return HttpResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(_MAX_RESPONSE_BYTES + 1)[:_MAX_RESPONSE_BYTES],
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(_MAX_RESPONSE_BYTES + 1)[:_MAX_RESPONSE_BYTES],
            )
        except (TimeoutError, socket.timeout) as error:
            raise TransportTimeout() from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TransportTimeout() from error
            raise TransportUnavailable() from error
        except OSError as error:
            raise TransportUnavailable() from error


class ProviderError(JarvisXError):
    """A provider failure carrying only classified, safe information."""

    def __init__(
        self,
        provider_name: str,
        reason: FailureReason,
        safe_message: str,
        *,
        status_code: int | None = None,
        outcome: ProviderOutcome = ProviderOutcome.FAILED,
        outcome_unknown: bool = False,
    ) -> None:
        self.provider_name = provider_name
        self.reason = reason
        self.status_code = status_code
        self.outcome = (
            ProviderOutcome.OUTCOME_UNKNOWN if outcome_unknown else outcome
        )
        self.outcome_unknown = self.outcome is ProviderOutcome.OUTCOME_UNKNOWN
        super().__init__(safe_message, code=f"provider_{reason.value}")


class ProviderChainError(ProviderError):
    """All permitted provider attempts ended without a usable result."""

    def __init__(
        self,
        safe_message: str,
        attempts: tuple[ProviderAttempt, ...],
        *,
        outcome: ProviderOutcome = ProviderOutcome.STOP_AND_WAIT,
        outcome_unknown: bool = False,
    ) -> None:
        self.attempts = attempts
        super().__init__(
            "ProviderManager",
            FailureReason.ALL_PROVIDERS_FAILED,
            safe_message,
            outcome=outcome,
            outcome_unknown=outcome_unknown,
        )


class AIProvider(ABC):
    """Common interface implemented by every AI provider adapter."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @property
    @abstractmethod
    def model_id(self) -> str | None:
        """Configured model ID, never a hardcoded catalogue entry."""

    @property
    @abstractmethod
    def billing_tier(self) -> BillingTier:
        """Configured billing classification used by policy."""

    @property
    @abstractmethod
    def billing_safety(self) -> BillingSafety:
        """Explicit billing facts used to fail closed before transport I/O."""

    @abstractmethod
    def ensure_billing_safe(self) -> None:
        """Raise a safe policy error unless this provider is permitted."""

    @property
    @abstractmethod
    def supports_model_discovery(self) -> bool:
        """Whether model discovery was explicitly enabled/supported."""

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Return local configuration status without network access."""

    @abstractmethod
    def generate(self, request: ChatRequest) -> ProviderResponse:
        """Execute exactly one generation request without retries."""

    @abstractmethod
    def discover_models(self) -> tuple[ModelInfo, ...]:
        """Discover models only when the adapter explicitly supports it."""


def normalize_https_base_url(value: str) -> str:
    """Validate a credential-safe HTTPS base URL and remove trailing slashes."""

    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("A valid HTTPS base URL is required.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL must not contain credentials, query, or fragment.")
    normalized = urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))
    return normalized.rstrip("/")


def join_endpoint(base_url: str, endpoint: str) -> str:
    return f"{normalize_https_base_url(base_url)}/{endpoint.lstrip('/')}"


def encode_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json_object(response: HttpResponse) -> dict[str, Any]:
    """Decode a response as a JSON object without exposing its raw body."""

    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Provider returned invalid JSON.") from error
    if not isinstance(decoded, dict):
        raise ValueError("Provider response must be a JSON object.")
    return decoded


def error_fingerprint(response: HttpResponse) -> str:
    """Build an internal-only lowercase error fingerprint for classification."""

    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    try:
        return json.dumps(decoded, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return ""


def send_once(
    provider_name: str,
    transport: HttpTransport,
    request: HttpRequest,
    *,
    unavailable_outcome: ProviderOutcome = ProviderOutcome.FAILED,
) -> HttpResponse:
    """Send once and map transport failures without retrying or leaking details."""

    try:
        return transport.send(request)
    except TransportTimeout as error:
        raise ProviderError(
            provider_name,
            FailureReason.TIMEOUT,
            f"{provider_name} request timed out; the outcome is unknown and was not retried.",
            outcome=ProviderOutcome.OUTCOME_UNKNOWN,
            outcome_unknown=True,
        ) from error
    except TransportUnavailable as error:
        raise ProviderError(
            provider_name,
            FailureReason.UNAVAILABLE,
            f"{provider_name} is currently unavailable.",
            outcome=unavailable_outcome,
        ) from error
