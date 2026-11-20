"""Mock-only tests for Phase 2A provider adapters and failover policy."""

from __future__ import annotations

import io
import json
import logging
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.config import SecretValue  # noqa: E402
from jarvis_x.logging_setup import configure_logging  # noqa: E402
from jarvis_x.providers.agentrouter import AgentRouterProvider  # noqa: E402
from jarvis_x.providers.base import (  # noqa: E402
    HttpRequest,
    HttpResponse,
    ProviderError,
    TransportTimeout,
)
from jarvis_x.providers.gemini import GeminiProvider  # noqa: E402
from jarvis_x.providers.manager import ProviderManager  # noqa: E402
from jarvis_x.providers.models import (  # noqa: E402
    AutoRechargeStatus,
    BillingTier,
    ChatRequest,
    FailureReason,
    ProviderOutcome,
    ProviderState,
    PrepaidBalanceStatus,
)


class FakeTransport:
    """Deterministic transport that can never access a real network."""

    def __init__(self, *outcomes: HttpResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("No mocked transport outcome remains.")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def json_response(status_code: int, payload: object) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


def quiet_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def agentrouter_provider(
    transport: FakeTransport,
    *,
    api_key: str | None = "router-test-key",
    model_id: str | None = "gpt-6.1-sol",
    billing_tier: BillingTier = BillingTier.PREPAID,
    prepaid_balance_status: PrepaidBalanceStatus = PrepaidBalanceStatus.AVAILABLE,
    auto_recharge_status: AutoRechargeStatus = AutoRechargeStatus.DISABLED,
    discovery: bool = False,
) -> AgentRouterProvider:
    return AgentRouterProvider(
        api_key=SecretValue(api_key) if api_key else None,
        base_url="https://agentrouter.org/v1",
        model_id=model_id,
        billing_tier=billing_tier,
        prepaid_balance_status=prepaid_balance_status,
        auto_recharge_status=auto_recharge_status,
        models_endpoint_supported=discovery,
        transport=transport,
    )


def gemini_provider(
    transport: FakeTransport,
    *,
    api_key: str | None = "gemini-test-key",
    model_id: str | None = "gemini-configured-model",
    free_tier_verified: bool = True,
    free_tier_verified_model: str | None = None,
) -> GeminiProvider:
    verified_model = model_id if free_tier_verified_model is None else free_tier_verified_model
    return GeminiProvider(
        api_key=SecretValue(api_key) if api_key else None,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_id=model_id,
        free_tier_verified=free_tier_verified,
        free_tier_verified_model=verified_model,
        transport=transport,
    )


def request() -> ChatRequest:
    return ChatRequest.from_text("Mock-only provider test")


class ProviderAdapterTests(unittest.TestCase):
    def test_normalized_provider_outcome_contract_is_complete(self) -> None:
        self.assertEqual(
            {outcome.value for outcome in ProviderOutcome},
            {
                "success",
                "fallback_allowed",
                "stop_and_wait",
                "blocked",
                "failed",
                "outcome_unknown",
            },
        )

    def test_max_output_tokens_are_mapped_without_retry_settings(self) -> None:
        limited_request = ChatRequest(
            messages=request().messages,
            task_type="connection_test",
            max_output_tokens=32,
        )
        router_transport = FakeTransport(
            json_response(200, {"choices": [{"message": {"content": "ok"}}]})
        )
        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            )
        )

        agentrouter_provider(router_transport).generate(limited_request)
        gemini_provider(gemini_transport).generate(limited_request)

        router_payload = json.loads(router_transport.requests[0].body or b"{}")
        gemini_payload = json.loads(gemini_transport.requests[0].body or b"{}")
        self.assertEqual(router_payload["max_tokens"], 32)
        self.assertEqual(gemini_payload["generationConfig"]["maxOutputTokens"], 32)
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(len(gemini_transport.requests), 1)

    def test_agentrouter_success_and_usage_record(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {
                    "id": "router-request-123",
                    "model": "gpt-6.1-sol",
                    "choices": [
                        {"message": {"content": "AgentRouter response"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                },
            )
        )
        provider = agentrouter_provider(transport)
        manager = ProviderManager(
            primary=provider,
            fallbacks=(),
            allow_paid_providers=False,
            logger=quiet_logger("test.agentrouter.success"),
        )

        execution = manager.execute(request())
        self.assertEqual(execution.outcome, ProviderOutcome.SUCCESS)
        self.assertTrue(execution.is_success)
        response = execution.response
        assert response is not None

        self.assertEqual(response.content, "AgentRouter response")
        self.assertEqual(response.model_id, "gpt-6.1-sol")
        self.assertEqual(response.provider_request_id, "router-request-123")
        self.assertEqual(response.usage.input_tokens, 11)
        self.assertEqual(response.usage.output_tokens, 7)
        self.assertEqual(response.usage.total_tokens, 18)
        self.assertEqual(manager.usage_records[0].provider_name, "AgentRouter")
        self.assertEqual(manager.usage_records[0].model_id, "gpt-6.1-sol")
        self.assertEqual(
            manager.usage_records[0].provider_request_id,
            "router-request-123",
        )
        self.assertEqual(manager.usage_records[0].input_tokens, 11)
        self.assertEqual(manager.usage_records[0].output_tokens, 7)
        self.assertFalse(hasattr(manager.usage_records[0], "estimated_cost_usd"))
        self.assertEqual(len(transport.requests), 1)
        sent = transport.requests[0]
        self.assertEqual(sent.url, "https://agentrouter.org/v1/chat/completions")
        self.assertEqual(sent.headers["Authorization"], "Bearer router-test-key")
        self.assertEqual(json.loads(sent.body or b"{}")["model"], "gpt-6.1-sol")

    def test_gemini_success_uses_header_authentication(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {
                    "responseId": "gemini-request-456",
                    "modelVersion": "gemini-response-model",
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Gemini response"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 5,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 8,
                    },
                },
            )
        )
        provider = gemini_provider(
            transport,
            model_id="gemini-response-model",
        )

        response = provider.generate(request())

        self.assertEqual(response.content, "Gemini response")
        self.assertEqual(response.provider_name, "Google Gemini")
        self.assertEqual(response.model_id, "gemini-response-model")
        self.assertEqual(response.provider_request_id, "gemini-request-456")
        self.assertEqual(response.usage.input_tokens, 5)
        self.assertEqual(response.usage.output_tokens, 3)
        self.assertEqual(response.usage.total_tokens, 8)
        self.assertEqual(len(transport.requests), 1)
        sent = transport.requests[0]
        self.assertIn("models/gemini-response-model:generateContent", sent.url)
        self.assertEqual(sent.headers["x-goog-api-key"], "gemini-test-key")
        self.assertNotIn("gemini-test-key", sent.url)

    def test_agentrouter_quota_exhausted_falls_back_to_gemini(self) -> None:
        router_secret = "router-fallback-secret"
        gemini_secret = "gemini-fallback-secret"
        router_transport = FakeTransport(
            json_response(
                429,
                {"error": {"code": "insufficient_quota", "message": "quota exhausted"}},
            )
        )
        gemini_transport = FakeTransport(
            json_response(
                200,
                {
                    "candidates": [{"content": {"parts": [{"text": "Fallback response"}]}}],
                    "usageMetadata": {"totalTokenCount": 4},
                },
            )
        )
        log_stream = io.StringIO()
        logger = configure_logging(
            "INFO",
            secrets=(router_secret, gemini_secret),
            stream=log_stream,
        )
        manager = ProviderManager(
            primary=agentrouter_provider(router_transport, api_key=router_secret),
            fallbacks=(gemini_provider(gemini_transport, api_key=gemini_secret),),
            allow_paid_providers=False,
            logger=logger,
        )

        response = manager.generate(request())

        self.assertEqual(response.provider_name, "Google Gemini")
        self.assertEqual(response.content, "Fallback response")
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(len(gemini_transport.requests), 1)
        self.assertIn("reason=quota_exhausted", log_stream.getvalue())
        self.assertIn("Provider failover", log_stream.getvalue())
        self.assertNotIn(router_secret, log_stream.getvalue())
        self.assertNotIn(gemini_secret, log_stream.getvalue())

    def test_gemini_quota_exhausted_stops_task(self) -> None:
        router_transport = FakeTransport(
            json_response(429, {"error": {"code": "insufficient_quota"}})
        )
        gemini_transport = FakeTransport(
            json_response(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
        )
        manager = ProviderManager(
            primary=agentrouter_provider(router_transport),
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.gemini.quota"),
        )

        execution = manager.execute(request())

        self.assertEqual(execution.outcome, ProviderOutcome.STOP_AND_WAIT)
        self.assertEqual(execution.reason, FailureReason.QUOTA_EXHAUSTED)
        self.assertEqual(
            execution.attempts[-1].outcome,
            ProviderOutcome.STOP_AND_WAIT,
        )
        self.assertEqual(execution.attempts[-1].reason, FailureReason.QUOTA_EXHAUSTED)
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(len(gemini_transport.requests), 1)
        self.assertEqual(manager.usage_records, ())

    def test_balance_unavailable_is_explicitly_fallback_allowed(self) -> None:
        transport = FakeTransport()
        provider = agentrouter_provider(
            transport,
            prepaid_balance_status=PrepaidBalanceStatus.UNAVAILABLE,
        )

        with self.assertRaises(ProviderError) as caught:
            provider.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.BALANCE_EXHAUSTED)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.FALLBACK_ALLOWED)
        self.assertEqual(transport.requests, [])

        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "Free fallback"}]}}]},
            )
        )
        manager = ProviderManager(
            primary=provider,
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.prepaid.unavailable"),
        )
        response = manager.generate(request())
        self.assertEqual(response.provider_name, "Google Gemini")
        self.assertEqual(len(gemini_transport.requests), 1)

    def test_prepaid_balance_exhausted_skips_agentrouter_and_uses_gemini(self) -> None:
        router_transport = FakeTransport()
        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "Free fallback"}]}}]},
            )
        )
        manager = ProviderManager(
            primary=agentrouter_provider(
                router_transport,
                prepaid_balance_status=PrepaidBalanceStatus.EXHAUSTED,
            ),
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.prepaid.exhausted"),
        )

        response = manager.generate(request())

        self.assertEqual(response.provider_name, "Google Gemini")
        self.assertEqual(router_transport.requests, [])
        self.assertEqual(len(gemini_transport.requests), 1)

    def test_runtime_balance_exhaustion_falls_back_without_retry(self) -> None:
        router_transport = FakeTransport(
            json_response(402, {"error": {"message": "credit exhausted"}})
        )
        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "Free fallback"}]}}]},
            )
        )
        manager = ProviderManager(
            primary=agentrouter_provider(router_transport),
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.prepaid.runtime-exhausted"),
        )

        response = manager.generate(request())

        self.assertEqual(response.provider_name, "Google Gemini")
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(len(gemini_transport.requests), 1)

    def test_paid_provider_is_blocked_before_request(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {"choices": [{"message": {"content": "must not be used"}}]},
            )
        )
        manager = ProviderManager(
            primary=agentrouter_provider(transport, billing_tier=BillingTier.PAID),
            fallbacks=(),
            allow_paid_providers=False,
            logger=quiet_logger("test.paid.blocked"),
        )

        with self.assertRaises(ProviderError) as caught:
            manager.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.PAID_PROVIDER_BLOCKED)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.BLOCKED)
        self.assertEqual(transport.requests, [])

        with self.assertRaises(ProviderError) as policy_error:
            ProviderManager(
                primary=agentrouter_provider(FakeTransport()),
                fallbacks=(),
                allow_paid_providers=True,
                logger=quiet_logger("test.paid.global-policy"),
            )
        self.assertEqual(policy_error.exception.reason, FailureReason.PAID_PROVIDER_BLOCKED)
        self.assertEqual(policy_error.exception.outcome, ProviderOutcome.BLOCKED)

    def test_unknown_billing_and_auto_recharge_are_blocked(self) -> None:
        cases = (
            (
                agentrouter_provider(
                    FakeTransport(),
                    billing_tier=BillingTier.UNKNOWN,
                ),
                FailureReason.BILLING_SAFETY_UNVERIFIED,
            ),
            (
                agentrouter_provider(
                    FakeTransport(),
                    auto_recharge_status=AutoRechargeStatus.UNKNOWN,
                ),
                FailureReason.BILLING_SAFETY_UNVERIFIED,
            ),
            (
                agentrouter_provider(
                    FakeTransport(),
                    auto_recharge_status=AutoRechargeStatus.ENABLED,
                ),
                FailureReason.AUTO_RECHARGE_RISK,
            ),
        )

        for provider, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                manager = ProviderManager(
                    primary=provider,
                    fallbacks=(),
                    allow_paid_providers=False,
                    logger=quiet_logger(f"test.billing.{expected_reason.value}"),
                )
                with self.assertRaises(ProviderError) as caught:
                    manager.generate(request())
                self.assertEqual(caught.exception.reason, expected_reason)
                self.assertEqual(caught.exception.outcome, ProviderOutcome.BLOCKED)

    def test_unverified_gemini_free_tier_is_blocked_before_request(self) -> None:
        transport = FakeTransport()
        provider = gemini_provider(transport, free_tier_verified=False)

        with self.assertRaises(ProviderError) as caught:
            provider.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.FREE_TIER_UNVERIFIED)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.BLOCKED)
        self.assertEqual(transport.requests, [])

    def test_invalid_provider_configuration_is_clear_and_local(self) -> None:
        transport = FakeTransport()
        provider = agentrouter_provider(transport, api_key=None, model_id=None)

        self.assertEqual(provider.status().state, ProviderState.UNCONFIGURED)
        with self.assertRaises(ProviderError) as caught:
            provider.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.INVALID_CONFIGURATION)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.FAILED)
        self.assertIn("AGENTROUTER_API_KEY is missing", str(caught.exception))
        self.assertIn("AGENTROUTER_MODEL is missing", str(caught.exception))
        self.assertEqual(transport.requests, [])

    def test_non_primary_agentrouter_model_is_blocked_before_request(self) -> None:
        transport = FakeTransport()
        provider = agentrouter_provider(transport, model_id="different-model")

        with self.assertRaises(ProviderError) as caught:
            provider.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.INVALID_CONFIGURATION)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.FAILED)
        self.assertEqual(transport.requests, [])

    def test_agentrouter_rejects_mismatched_response_model(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {
                    "model": "different-model",
                    "choices": [{"message": {"content": "wrong model"}}],
                },
            )
        )
        provider = agentrouter_provider(transport)

        with self.assertRaises(ProviderError) as caught:
            provider.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.INVALID_RESPONSE)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.FAILED)
        self.assertEqual(len(transport.requests), 1)

    def test_authentication_failure_never_uses_gemini_fallback(self) -> None:
        router_transport = FakeTransport(json_response(401, {"error": {"code": "unauthorized"}}))
        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "must not run"}]}}]},
            )
        )
        manager = ProviderManager(
            primary=agentrouter_provider(router_transport),
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.auth.no-fallback"),
        )

        execution = manager.execute(request())

        self.assertEqual(execution.outcome, ProviderOutcome.FAILED)
        self.assertEqual(execution.reason, FailureReason.AUTHENTICATION)
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(gemini_transport.requests, [])

    def test_provider_errors_and_request_repr_do_not_expose_secret(self) -> None:
        secret = "provider-secret-must-not-leak"
        transport = FakeTransport(
            json_response(401, {"error": {"message": f"rejected {secret}"}})
        )
        stream = io.StringIO()
        manager = ProviderManager(
            primary=agentrouter_provider(transport, api_key=secret),
            fallbacks=(),
            allow_paid_providers=False,
            logger=configure_logging("INFO", secrets=(secret,), stream=stream),
        )

        with self.assertRaises(ProviderError) as caught:
            manager.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.AUTHENTICATION)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.FAILED)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(transport.requests[0]))
        self.assertNotIn(secret, stream.getvalue())

    def test_timeout_never_issues_duplicate_fallback_request(self) -> None:
        router_transport = FakeTransport(TransportTimeout())
        gemini_transport = FakeTransport(
            json_response(
                200,
                {"candidates": [{"content": {"parts": [{"text": "must not run"}]}}]},
            )
        )
        manager = ProviderManager(
            primary=agentrouter_provider(router_transport),
            fallbacks=(gemini_provider(gemini_transport),),
            allow_paid_providers=False,
            logger=quiet_logger("test.timeout"),
        )

        with self.assertRaises(ProviderError) as caught:
            manager.generate(request())

        self.assertEqual(caught.exception.reason, FailureReason.TIMEOUT)
        self.assertEqual(caught.exception.outcome, ProviderOutcome.OUTCOME_UNKNOWN)
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(len(router_transport.requests), 1)
        self.assertEqual(gemini_transport.requests, [])

    def test_duplicate_request_id_is_blocked_concurrently(self) -> None:
        transport = FakeTransport(
            json_response(200, {"choices": [{"message": {"content": "once"}}]})
        )
        manager = ProviderManager(
            primary=agentrouter_provider(transport),
            fallbacks=(),
            allow_paid_providers=False,
            logger=quiet_logger("test.duplicate.concurrent"),
        )
        shared_request = ChatRequest.from_text(
            "Execute once",
            task_type="duplicate_test",
        )
        barrier = threading.Barrier(2)
        results = []

        def execute_shared() -> None:
            barrier.wait()
            results.append(manager.execute(shared_request))

        threads = [threading.Thread(target=execute_shared) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        outcomes = sorted(result.outcome.value for result in results)
        self.assertEqual(
            outcomes,
            sorted([ProviderOutcome.SUCCESS.value, ProviderOutcome.BLOCKED.value]),
        )
        blocked = next(
            result for result in results if result.outcome is ProviderOutcome.BLOCKED
        )
        self.assertEqual(blocked.reason, FailureReason.DUPLICATE_REQUEST)
        self.assertEqual(len(transport.requests), 1)

    def test_model_discovery_errors_are_safe_and_do_not_auto_probe(self) -> None:
        unsupported_transport = FakeTransport()
        unsupported = agentrouter_provider(unsupported_transport, discovery=False)

        with self.assertRaises(ProviderError) as unsupported_error:
            unsupported.discover_models()

        self.assertEqual(
            unsupported_error.exception.reason,
            FailureReason.DISCOVERY_UNSUPPORTED,
        )
        self.assertEqual(unsupported_transport.requests, [])

        requested_transport = FakeTransport(
            json_response(200, {"data": [{"id": "must-not-be-used"}]})
        )
        requested = agentrouter_provider(requested_transport, discovery=True)
        self.assertFalse(requested.supports_model_discovery)
        self.assertEqual(requested.status().state, ProviderState.INVALID)
        with self.assertRaises(ProviderError) as requested_error:
            requested.discover_models()

        self.assertEqual(
            requested_error.exception.reason,
            FailureReason.DISCOVERY_UNSUPPORTED,
        )
        self.assertEqual(requested_transport.requests, [])

    def test_gemini_model_discovery_uses_api_results(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {
                    "models": [
                        {
                            "name": "models/provider-returned-id",
                            "displayName": "Provider Returned Model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        )
        provider = gemini_provider(transport)

        models = provider.discover_models()

        self.assertEqual(models[0].model_id, "models/provider-returned-id")
        self.assertEqual(models[0].capabilities, ("generateContent",))
        self.assertEqual(models[0].billing_tier, BillingTier.UNKNOWN)
        self.assertEqual(len(transport.requests), 1)


if __name__ == "__main__":
    unittest.main()
