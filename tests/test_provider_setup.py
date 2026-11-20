"""Mock-only tests for Phase 2D provider setup and metadata discovery."""

from __future__ import annotations

import io
import json
import logging
import sys
import unittest
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.provider_setup.cache import InMemoryModelCache  # noqa: E402
from jarvis_x.provider_setup.control_plane import (  # noqa: E402
    AgentRouterUnsupportedControlPlane,
    ControlPlaneError,
    GeminiMetadataControlPlane,
)
from jarvis_x.provider_setup.credentials import (  # noqa: E402
    InMemoryCredentialStore,
    SecretBuffer,
    mask_api_key,
)
from jarvis_x.provider_setup.models import (  # noqa: E402
    ChargeRisk,
    DiscoveredModel,
    DiscoveryPage,
    DiscoveryStatus,
    ProviderId,
    VerificationOperation,
    VerificationStatus,
)
from jarvis_x.provider_setup.service import ProviderSettingsService  # noqa: E402
from jarvis_x.providers.base import HttpRequest, HttpResponse  # noqa: E402
from jarvis_x.providers.models import BillingTier  # noqa: E402


def json_response(status_code: int, payload: object) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


class RecordingTransport:
    """Transport fake that snapshots headers only while send is active."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []
        self.headers_seen: list[dict[str, str]] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        self.headers_seen.append(dict(request.headers))
        if not self.responses:
            raise AssertionError("No mocked metadata response remains.")
        return self.responses.pop(0)


class ScriptedControlPlane:
    supports_metadata_verification = True
    verification_operation = VerificationOperation.MODEL_LIST_METADATA
    charge_risk = ChargeRisk.NO_INFERENCE_EXPECTED

    def __init__(
        self,
        provider: ProviderId,
        *outcomes: DiscoveryPage | ControlPlaneError,
        expected_key: bytes = b"setup-secret-1234",
    ) -> None:
        self.provider = provider
        self.outcomes = list(outcomes)
        self.expected_key = expected_key
        self.page_tokens: list[str | None] = []

    def queue(self, *outcomes: DiscoveryPage | ControlPlaneError) -> None:
        self.outcomes.extend(outcomes)

    def list_models(self, secret, *, page_token=None):
        with secret.copy_bytes() as copied:
            if bytes(copied) != self.expected_key:
                raise AssertionError("Credential did not reach the private adapter boundary.")
        self.page_tokens.append(page_token)
        if not self.outcomes:
            raise AssertionError("No scripted control-plane outcome remains.")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ControlPlaneError):
            raise outcome
        return outcome


def discovered(provider: ProviderId, model_id: str) -> DiscoveredModel:
    return DiscoveredModel(
        provider=provider,
        model_id=model_id,
        provider_capabilities=("generateContent",),
    )


class CredentialAndSettingsTests(unittest.TestCase):
    def test_key_masking_never_returns_short_or_complete_key(self) -> None:
        self.assertEqual(mask_api_key("tiny"), "••••")
        masked = mask_api_key("provider-secret-1234")
        self.assertTrue(masked.endswith("1234"))
        self.assertNotIn("provider-secret-1234", masked)

    def test_save_update_delete_and_input_zeroization(self) -> None:
        store = InMemoryCredentialStore()
        service = ProviderSettingsService(
            credential_store=store,
            control_planes={},
        )
        first = bytearray(b"provider-secret-1234")

        metadata = service.save_api_key(ProviderId.GEMINI, first)

        self.assertTrue(metadata.configured)
        self.assertTrue(all(byte == 0 for byte in first))
        self.assertNotIn("provider-secret-1234", repr(metadata))
        self.assertEqual(
            service.snapshot(ProviderId.GEMINI).verification.status,
            VerificationStatus.NOT_VERIFIED,
        )

        second = bytearray(b"replacement-secret-5678")
        updated = service.save_api_key(ProviderId.GEMINI, second)
        self.assertTrue(all(byte == 0 for byte in second))
        self.assertTrue(updated.masked_key.endswith("5678"))
        self.assertTrue(service.delete_api_key(ProviderId.GEMINI))
        self.assertFalse(service.snapshot(ProviderId.GEMINI).credential.configured)
        self.assertFalse(service.delete_api_key(ProviderId.GEMINI))

    def test_secret_buffer_repr_and_clear_are_safe(self) -> None:
        secret = SecretBuffer(b"never-render-this-secret")
        self.assertNotIn("never-render-this-secret", repr(secret))
        self.assertEqual(str(secret), "<redacted>")
        secret.clear()
        self.assertTrue(secret.is_cleared)
        with self.assertRaises(RuntimeError):
            with secret.copy_bytes():
                pass

    def test_verify_discovers_every_page_and_refreshes_cache(self) -> None:
        first_page = DiscoveryPage(
            (discovered(ProviderId.GEMINI, "models/first"),),
            next_page_token="page-two",
        )
        second_page = DiscoveryPage(
            (discovered(ProviderId.GEMINI, "models/second"),)
        )
        control = ScriptedControlPlane(
            ProviderId.GEMINI,
            first_page,
            second_page,
        )
        cache = InMemoryModelCache()
        service = ProviderSettingsService(
            credential_store=InMemoryCredentialStore(),
            control_planes={ProviderId.GEMINI: control},
            model_cache=cache,
        )
        service.save_api_key(
            ProviderId.GEMINI,
            bytearray(b"setup-secret-1234"),
        )

        result = service.verify_and_discover(ProviderId.GEMINI)

        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertFalse(result.inference_used)
        self.assertEqual(result.charge_risk, ChargeRisk.NO_INFERENCE_EXPECTED)
        self.assertEqual(control.page_tokens, [None, "page-two"])
        snapshot = service.snapshot(ProviderId.GEMINI)
        self.assertEqual(
            [model.model_id for model in snapshot.model_cache.models],
            ["models/first", "models/second"],
        )
        first_timestamp = snapshot.model_cache.refreshed_at

        control.queue(
            DiscoveryPage((discovered(ProviderId.GEMINI, "models/refreshed"),))
        )
        refresh = service.refresh_models(ProviderId.GEMINI)
        self.assertEqual(refresh.status, DiscoveryStatus.SUCCESS)
        self.assertEqual(refresh.cache.models[0].model_id, "models/refreshed")
        self.assertGreaterEqual(refresh.cache.refreshed_at, first_timestamp)

    def test_verification_distinguishes_provider_failures(self) -> None:
        cases = (
            VerificationStatus.INVALID_KEY,
            VerificationStatus.PERMISSION_DENIED,
            VerificationStatus.QUOTA_EXHAUSTED,
            VerificationStatus.NETWORK_FAILURE,
            VerificationStatus.INVALID_RESPONSE,
        )
        for status in cases:
            with self.subTest(status=status):
                control = ScriptedControlPlane(
                    ProviderId.GEMINI,
                    ControlPlaneError(status, f"safe {status.value}"),
                )
                service = ProviderSettingsService(
                    credential_store=InMemoryCredentialStore(),
                    control_planes={ProviderId.GEMINI: control},
                )
                service.save_api_key(
                    ProviderId.GEMINI,
                    bytearray(b"setup-secret-1234"),
                )
                result = service.verify_and_discover(ProviderId.GEMINI)
                self.assertEqual(result.status, status)
                self.assertFalse(result.inference_used)
                self.assertIsNone(service.snapshot(ProviderId.GEMINI).model_cache)

    def test_agentrouter_unsupported_endpoint_and_manual_model_are_fail_closed(self) -> None:
        service = ProviderSettingsService(
            credential_store=InMemoryCredentialStore(),
            control_planes={
                ProviderId.AGENTROUTER: AgentRouterUnsupportedControlPlane()
            },
        )
        service.save_api_key(
            ProviderId.AGENTROUTER,
            bytearray(b"setup-secret-1234"),
        )

        result = service.verify_and_discover(ProviderId.AGENTROUTER)
        manual = service.set_manual_model(ProviderId.AGENTROUTER, "gpt-6.1-sol")

        self.assertEqual(result.status, VerificationStatus.UNSUPPORTED_ENDPOINT)
        self.assertFalse(result.inference_used)
        self.assertFalse(manual.availability_verified)
        self.assertIsNone(service.snapshot(ProviderId.AGENTROUTER).model_cache)
        with self.assertRaises(ValueError):
            service.set_manual_model(ProviderId.AGENTROUTER, "different-model")

    def test_agentrouter_supported_control_plane_cannot_be_injected(self) -> None:
        control = ScriptedControlPlane(
            ProviderId.AGENTROUTER,
            DiscoveryPage(
                (discovered(ProviderId.AGENTROUTER, "gpt-6.1-sol"),)
            ),
        )

        with self.assertRaises(ValueError):
            ProviderSettingsService(
                credential_store=InMemoryCredentialStore(),
                control_planes={ProviderId.AGENTROUTER: control},
            )

    def test_mismatched_control_plane_mapping_is_rejected(self) -> None:
        control = ScriptedControlPlane(
            ProviderId.GEMINI,
            DiscoveryPage((discovered(ProviderId.GEMINI, "models/safe"),)),
        )

        with self.assertRaises(ValueError):
            ProviderSettingsService(
                credential_store=InMemoryCredentialStore(),
                control_planes={ProviderId.AGENTROUTER: control},
            )

    def test_snapshot_and_logs_never_contain_secret(self) -> None:
        secret = "no-log-provider-secret-1234"
        stream = io.StringIO()
        logger = logging.getLogger("test.provider-setup.no-secret")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        control = ScriptedControlPlane(
            ProviderId.GEMINI,
            DiscoveryPage((discovered(ProviderId.GEMINI, "models/safe"),)),
            expected_key=secret.encode(),
        )
        service = ProviderSettingsService(
            credential_store=InMemoryCredentialStore(),
            control_planes={ProviderId.GEMINI: control},
            logger=logger,
        )
        service.save_api_key(ProviderId.GEMINI, bytearray(secret.encode()))
        service.verify_and_discover(ProviderId.GEMINI)

        rendered_snapshot = json.dumps(
            asdict(service.snapshot(ProviderId.GEMINI)),
            default=str,
        )
        self.assertNotIn(secret, rendered_snapshot)
        self.assertNotIn(secret, stream.getvalue())
        self.assertIn("verified", rendered_snapshot)

    def test_app_backend_snapshot_exposes_metadata_but_no_credential_loader(self) -> None:
        service = ProviderSettingsService(
            credential_store=InMemoryCredentialStore(),
            control_planes={},
        )
        secret = "private-bridge-secret-9876"
        service.save_api_key(ProviderId.GEMINI, bytearray(secret.encode()))

        snapshot = service.snapshot(ProviderId.GEMINI)
        public_names = {name for name in dir(service) if not name.startswith("_")}

        self.assertTrue(snapshot.credential.configured)
        self.assertNotIn(secret, repr(snapshot))
        self.assertNotIn("load_api_key", public_names)
        self.assertNotIn("get_api_key", public_names)


class GeminiMetadataControlPlaneTests(unittest.TestCase):
    def test_metadata_request_is_header_authenticated_and_secret_is_not_retained(self) -> None:
        secret_text = "gemini-metadata-secret"
        transport = RecordingTransport(
            json_response(
                200,
                {
                    "models": [
                        {
                            "name": "models/provider-model",
                            "baseModelId": "provider-model",
                            "version": "1",
                            "displayName": "Provider Model",
                            "description": "Provider metadata",
                            "inputTokenLimit": 1000,
                            "outputTokenLimit": 200,
                            "supportedGenerationMethods": ["generateContent"],
                            "thinking": True,
                        }
                    ]
                },
            )
        )
        control = GeminiMetadataControlPlane(transport=transport)
        secret = SecretBuffer(secret_text.encode())

        page = control.list_models(secret)
        secret.clear()

        self.assertEqual(len(transport.requests), 1)
        self.assertNotIn(secret_text, transport.requests[0].url)
        self.assertEqual(
            transport.headers_seen[0]["x-goog-api-key"],
            secret_text,
        )
        self.assertEqual(dict(transport.requests[0].headers), {})
        model = page.models[0]
        self.assertEqual(model.model_id, "models/provider-model")
        self.assertEqual(model.input_token_limit, 1000)
        self.assertEqual(model.output_token_limit, 200)
        self.assertTrue(model.thinking)
        self.assertEqual(model.billing_tier, BillingTier.UNKNOWN)
        self.assertIsNone(model.free_tier_eligible)

    def test_service_handles_gemini_pagination_without_inference(self) -> None:
        secret_text = "gemini-paginated-secret"
        transport = RecordingTransport(
            json_response(
                200,
                {
                    "models": [{"name": "models/one"}],
                    "nextPageToken": "next page",
                },
            ),
            json_response(200, {"models": [{"name": "models/two"}]}),
        )
        control = GeminiMetadataControlPlane(transport=transport)
        service = ProviderSettingsService(
            credential_store=InMemoryCredentialStore(),
            control_planes={ProviderId.GEMINI: control},
        )
        service.save_api_key(ProviderId.GEMINI, bytearray(secret_text.encode()))

        result = service.verify_and_discover(ProviderId.GEMINI)

        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertFalse(result.inference_used)
        self.assertEqual(len(transport.requests), 2)
        self.assertIn("pageToken=next+page", transport.requests[1].url)
        self.assertTrue(all(request.body is None for request in transport.requests))
        self.assertTrue(
            all(secret_text not in request.url for request in transport.requests)
        )

    def test_http_statuses_are_classified_without_raw_error_leakage(self) -> None:
        cases = (
            (401, VerificationStatus.INVALID_KEY),
            (403, VerificationStatus.PERMISSION_DENIED),
            (429, VerificationStatus.QUOTA_EXHAUSTED),
            (404, VerificationStatus.UNSUPPORTED_ENDPOINT),
            (503, VerificationStatus.NETWORK_FAILURE),
        )
        for status_code, expected in cases:
            with self.subTest(status_code=status_code):
                transport = RecordingTransport(
                    json_response(
                        status_code,
                        {"error": {"message": "raw-secret-looking-detail"}},
                    )
                )
                control = GeminiMetadataControlPlane(transport=transport)
                secret = SecretBuffer(b"classification-secret")
                try:
                    with self.assertRaises(ControlPlaneError) as caught:
                        control.list_models(secret)
                finally:
                    secret.clear()
                self.assertEqual(caught.exception.status, expected)
                self.assertNotIn("raw-secret-looking-detail", str(caught.exception))

    def test_generic_bad_request_is_not_misreported_as_invalid_key(self) -> None:
        transport = RecordingTransport(
            json_response(400, {"error": {"message": "bad page token"}})
        )
        control = GeminiMetadataControlPlane(transport=transport)
        secret = SecretBuffer(b"classification-secret")
        try:
            with self.assertRaises(ControlPlaneError) as caught:
                control.list_models(secret, page_token="bad-token")
        finally:
            secret.clear()

        self.assertEqual(caught.exception.status, VerificationStatus.PROVIDER_ERROR)

    def test_gemini_invalid_key_fingerprint_on_400_is_classified(self) -> None:
        transport = RecordingTransport(
            json_response(400, {"error": {"status": "API_KEY_INVALID"}})
        )
        control = GeminiMetadataControlPlane(transport=transport)
        secret = SecretBuffer(b"classification-secret")
        try:
            with self.assertRaises(ControlPlaneError) as caught:
                control.list_models(secret)
        finally:
            secret.clear()

        self.assertEqual(caught.exception.status, VerificationStatus.INVALID_KEY)


if __name__ == "__main__":
    unittest.main()
