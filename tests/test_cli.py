"""Local and mock-only tests for the Phase 2C.3 CLI task interface."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.__main__ import build_parser, main  # noqa: E402
from jarvis_x.config import Settings  # noqa: E402
from jarvis_x.core.errors import ConfigurationError  # noqa: E402
from jarvis_x.orchestrator.models import TaskResult, TaskState  # noqa: E402
from jarvis_x.preflight import (  # noqa: E402
    PreflightCheck,
    PreflightReport,
    PreflightStatus,
)
from jarvis_x.providers.models import (  # noqa: E402
    FailureReason,
    ProviderExecutionResult,
    ProviderOutcome,
    ProviderResponse,
    TokenUsage,
)
from jarvis_x.runtime_config import (  # noqa: E402
    RuntimeConfiguration,
    load_runtime_configuration,
)
from jarvis_x.task_interface import task_result_payload  # noqa: E402


class CliProviderManager:
    primary_provider_name = "AgentRouter"
    primary_model_id = "gpt-6.1-sol"

    def __init__(
        self,
        *,
        outcome: ProviderOutcome = ProviderOutcome.SUCCESS,
        content: str = "CLI mock result",
        reason: FailureReason | None = None,
        provider_request_id: str = "cli-provider-request",
    ) -> None:
        self.outcome = outcome
        self.content = content
        self.reason = reason
        self.provider_request_id = provider_request_id
        self.call_count = 0

    def execute(self, request):
        self.call_count += 1
        response = None
        if self.outcome is ProviderOutcome.SUCCESS:
            response = ProviderResponse(
                provider_name="AgentRouter",
                model_id="gpt-6.1-sol",
                content=self.content,
                usage=TokenUsage(input_tokens=9, output_tokens=4, total_tokens=13),
                provider_request_id=self.provider_request_id,
            )
        return ProviderExecutionResult(
            request_id=request.request_id,
            outcome=self.outcome,
            response=response,
            reason=self.reason,
            safe_message="safe mock error" if response is None else None,
        )


class RecordingFactory:
    def __init__(self, manager: CliProviderManager) -> None:
        self.manager = manager
        self.call_count = 0
        self.settings = None

    def __call__(self, settings, logger):
        self.call_count += 1
        self.settings = settings
        return self.manager


VALID_TASK_VALUES = {
    "JARVIS_PRIMARY_PROVIDER": "agentrouter",
    "JARVIS_ALLOW_PAID_PROVIDERS": "false",
    "AGENTROUTER_BASE_URL": "https://agentrouter.org/v1",
    "AGENTROUTER_API_KEY": "fake-agent-key",
    "AGENTROUTER_MODEL": "gpt-6.1-sol",
    "AGENTROUTER_BILLING_MODE": "prepaid",
    "AGENTROUTER_PREPAID_BALANCE_STATUS": "available",
    "AGENTROUTER_AUTO_RECHARGE_STATUS": "disabled",
    "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED": "false",
    "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
    "GEMINI_API_KEY": "fake-gemini-key",
    "GEMINI_MODEL": "verified-free-model",
    "GEMINI_FREE_TIER_VERIFIED": "true",
    "GEMINI_FREE_TIER_VERIFIED_MODEL": "verified-free-model",
}


def runtime_configuration(values=None):
    merged = dict(VALID_TASK_VALUES)
    if values:
        merged.update(values)
    return RuntimeConfiguration(settings=Settings.from_env(merged), _values=merged)


def run_cli(
    arguments,
    *,
    factory=None,
    environment=None,
    runtime_loader=None,
):
    stdout = io.StringIO()
    stderr = io.StringIO()
    if runtime_loader is None and "--task" in arguments:
        configuration = runtime_configuration(environment)
        runtime_loader = lambda _workspace: configuration
    with patch.dict(os.environ, environment or {}, clear=True):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                arguments,
                provider_manager_factory=factory,
                runtime_configuration_loader=runtime_loader,
            )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None
    return exit_code, payload, stderr.getvalue()


class CliIntegrationTests(unittest.TestCase):
    def test_help_lists_backward_compatible_and_task_commands(self) -> None:
        help_text = build_parser().format_help()

        for option in (
            "--health",
            "--version",
            "--orchestrator-health",
            "--preflight",
            "--task",
            "--dry-run-task",
        ):
            self.assertIn(option, help_text)

    def test_preflight_cli_prints_safe_report(self) -> None:
        report = PreflightReport(
            (
                PreflightCheck(
                    "fixture",
                    PreflightStatus.NOT_CONFIGURED,
                    "Fixture is not configured.",
                ),
            )
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("jarvis_x.__main__.run_preflight", return_value=report):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["--preflight"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "NOT_CONFIGURED")
        self.assertEqual(payload["network_calls"], 0)

    def test_health_command_remains_compatible(self) -> None:
        exit_code, payload, _stderr = run_cli(["--health"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "healthy")

    def test_orchestrator_health_is_entirely_local(self) -> None:
        exit_code, payload, _stderr = run_cli(["--orchestrator-health"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["state"], "COMPLETED")
        self.assertEqual(payload["provider_api_calls"], 0)

    def test_dry_run_task_never_builds_real_provider_manager(self) -> None:
        manager = CliProviderManager()
        factory = RecordingFactory(manager)
        exit_code, payload, _stderr = run_cli(
            ["--dry-run-task", "validate locally"],
            factory=factory,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["state"], "COMPLETED")
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["task_id"])
        self.assertEqual(payload["provider"], "Local Dry Run")
        self.assertEqual(factory.call_count, 0)
        self.assertEqual(manager.call_count, 0)

    def test_task_success_displays_status_result_and_usage(self) -> None:
        manager = CliProviderManager()
        factory = RecordingFactory(manager)

        exit_code, payload, _stderr = run_cli(
            ["--task", "perform mock task", "--max-output-tokens", "32"],
            factory=factory,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["state"], "COMPLETED")
        self.assertTrue(payload["task_id"])
        self.assertTrue(payload["created_at"])
        self.assertTrue(payload["updated_at"])
        self.assertEqual(payload["result"], "CLI mock result")
        self.assertEqual(payload["provider"], "AgentRouter")
        self.assertEqual(payload["model"], "gpt-6.1-sol")
        self.assertEqual(payload["usage"]["input_tokens"], 9)
        self.assertEqual(payload["usage"]["output_tokens"], 4)
        self.assertEqual(
            payload["usage"]["provider_request_id"],
            "cli-provider-request",
        )
        self.assertEqual(manager.call_count, 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.settings.agentrouter_model, "gpt-6.1-sol")
        self.assertEqual(factory.settings.agentrouter_billing_mode, "prepaid")

    def test_actual_task_uses_centralized_env_file_configuration(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name)
        env_text = "\n".join(f"{key}={value}" for key, value in VALID_TASK_VALUES.items()) + "\n"
        env_path = workspace / ".env"
        env_path.write_text(env_text)
        os.chmod(env_path, 0o600)
        manager = CliProviderManager()
        factory = RecordingFactory(manager)

        exit_code, payload, _stderr = run_cli(
            ["--task", "centralized config task"],
            factory=factory,
            runtime_loader=lambda _workspace: load_runtime_configuration(
                workspace,
                process_environment={},
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["state"], "COMPLETED")
        self.assertEqual(factory.call_count, 1)
        self.assertTrue(factory.settings.agentrouter_api_key)
        self.assertTrue(factory.settings.gemini_api_key)

    def test_terminal_outcomes_have_clear_states_and_exit_codes(self) -> None:
        cases = (
            (ProviderOutcome.STOP_AND_WAIT, FailureReason.QUOTA_EXHAUSTED, "STOP_AND_WAIT", 10),
            (ProviderOutcome.BLOCKED, FailureReason.PAID_PROVIDER_BLOCKED, "BLOCKED", 11),
            (ProviderOutcome.FAILED, FailureReason.AUTHENTICATION, "FAILED", 12),
            (ProviderOutcome.OUTCOME_UNKNOWN, FailureReason.TIMEOUT, "OUTCOME_UNKNOWN", 13),
        )

        for outcome, reason, expected_state, expected_exit in cases:
            with self.subTest(outcome=outcome):
                manager = CliProviderManager(outcome=outcome, reason=reason)
                exit_code, payload, _stderr = run_cli(
                    ["--task", "mock terminal outcome"],
                    factory=RecordingFactory(manager),
                )
                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(payload["state"], expected_state)
                self.assertEqual(payload["error"]["reason"], reason.value)
                self.assertEqual(manager.call_count, 1)

    def test_cancelled_result_payload_is_supported(self) -> None:
        result = TaskResult(
            task_id="task-id",
            request_id="request-id",
            state=TaskState.CANCELLED,
            provider_outcome=ProviderOutcome.BLOCKED,
            cancellation_requested=True,
        )

        payload = task_result_payload(result)

        self.assertEqual(payload["state"], "CANCELLED")
        self.assertTrue(payload["cancellation_requested"])

    def test_task_output_redacts_configured_secret_everywhere(self) -> None:
        secret = "cli-output-secret"
        manager = CliProviderManager(
            content=f"response accidentally included {secret}",
            provider_request_id=f"request-{secret}",
        )
        exit_code, payload, stderr = run_cli(
            ["--task", "secret-redaction-test"],
            factory=RecordingFactory(manager),
            environment={"AGENTROUTER_API_KEY": secret},
        )
        rendered = json.dumps(payload)

        self.assertEqual(exit_code, 0)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, stderr)
        self.assertIn("<redacted>", rendered)

    def test_invalid_runtime_configuration_never_builds_provider_manager(self) -> None:
        manager = CliProviderManager()
        factory = RecordingFactory(manager)

        def invalid_loader(_workspace):
            raise ConfigurationError("Runtime configuration is not ready.")

        exit_code, payload, stderr = run_cli(
            ["--task", "must not execute"],
            factory=factory,
            runtime_loader=invalid_loader,
        )

        self.assertEqual(exit_code, 2)
        self.assertIsNone(payload)
        self.assertEqual(factory.call_count, 0)
        self.assertEqual(manager.call_count, 0)
        self.assertIn("Runtime configuration failed", stderr)


if __name__ == "__main__":
    unittest.main()
