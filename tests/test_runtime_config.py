"""Tests for the centralized Phase 2C.5 runtime configuration entry point."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.core.errors import ConfigurationError  # noqa: E402
from jarvis_x.runtime_config import load_runtime_configuration  # noqa: E402


VALID_ENV = """\
JARVIS_PRIMARY_PROVIDER=agentrouter
JARVIS_ALLOW_PAID_PROVIDERS=false
AGENTROUTER_BASE_URL=https://agentrouter.org/v1
AGENTROUTER_API_KEY=file-agent-key
AGENTROUTER_MODEL=gpt-6.1-sol
AGENTROUTER_BILLING_MODE=prepaid
AGENTROUTER_PREPAID_BALANCE_STATUS=available
AGENTROUTER_AUTO_RECHARGE_STATUS=disabled
AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=false
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_API_KEY=file-gemini-key
GEMINI_MODEL=verified-free-model
GEMINI_FREE_TIER_VERIFIED=true
GEMINI_FREE_TIER_VERIFIED_MODEL=verified-free-model
"""


class RuntimeConfigurationTests(unittest.TestCase):
    def make_workspace(
        self,
        *,
        env_text: str | None = VALID_ENV,
        mode: int = 0o600,
        symlink: bool = False,
    ):
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tests")
        workspace = Path(temporary.name)
        if env_text is not None:
            if symlink:
                target = workspace / "target"
                target.write_text(env_text)
                os.chmod(target, mode)
                (workspace / ".env").symlink_to(target.name)
            else:
                env_path = workspace / ".env"
                env_path.write_text(env_text)
                os.chmod(env_path, mode)
        return temporary, workspace

    def test_env_configuration_reaches_runtime_settings(self) -> None:
        temporary, workspace = self.make_workspace()
        self.addCleanup(temporary.cleanup)

        runtime = load_runtime_configuration(workspace, process_environment={})

        self.assertEqual(runtime.settings.agentrouter_model, "gpt-6.1-sol")
        self.assertEqual(runtime.settings.agentrouter_billing_mode, "prepaid")
        self.assertTrue(runtime.is_explicitly_configured("AGENTROUTER_API_KEY"))
        self.assertTrue(runtime.is_explicitly_configured("GEMINI_API_KEY"))

    def test_process_environment_has_highest_recognized_precedence(self) -> None:
        temporary, workspace = self.make_workspace()
        self.addCleanup(temporary.cleanup)

        runtime = load_runtime_configuration(
            workspace,
            process_environment={
                "AGENTROUTER_API_KEY": "process-agent-key",
                "UNRECOGNIZED_SECRET": "ignored",
            },
        )

        self.assertEqual(
            runtime.settings.agentrouter_api_key.reveal(),
            "process-agent-key",
        )
        self.assertNotIn("UNRECOGNIZED_SECRET", repr(runtime))
        self.assertNotIn("process-agent-key", repr(runtime))

    def test_missing_symlink_and_wrong_permission_fail_closed(self) -> None:
        cases = (
            {"env_text": None},
            {"symlink": True},
            {"mode": 0o644},
        )
        for options in cases:
            with self.subTest(options=options):
                temporary, workspace = self.make_workspace(**options)
                try:
                    with self.assertRaises(ConfigurationError):
                        load_runtime_configuration(workspace, process_environment={})
                finally:
                    temporary.cleanup()

    def test_unsafe_or_incomplete_values_fail_before_provider_construction(self) -> None:
        replacements = (
            ("AGENTROUTER_API_KEY=file-agent-key", "AGENTROUTER_API_KEY="),
            ("AGENTROUTER_MODEL=gpt-6.1-sol", "AGENTROUTER_MODEL=wrong"),
            ("JARVIS_ALLOW_PAID_PROVIDERS=false", "JARVIS_ALLOW_PAID_PROVIDERS=true"),
            ("AGENTROUTER_BILLING_MODE=prepaid", "AGENTROUTER_BILLING_MODE=unknown"),
            (
                "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=false",
                "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=true",
            ),
            (
                "AGENTROUTER_AUTO_RECHARGE_STATUS=disabled",
                "AGENTROUTER_AUTO_RECHARGE_STATUS=unknown",
            ),
            ("GEMINI_FREE_TIER_VERIFIED=true", "GEMINI_FREE_TIER_VERIFIED=false"),
            (
                "GEMINI_FREE_TIER_VERIFIED_MODEL=verified-free-model",
                "GEMINI_FREE_TIER_VERIFIED_MODEL=different-model",
            ),
        )
        for old, new in replacements:
            with self.subTest(field=old.split("=", 1)[0]):
                temporary, workspace = self.make_workspace(
                    env_text=VALID_ENV.replace(old, new)
                )
                try:
                    with self.assertRaises(ConfigurationError):
                        load_runtime_configuration(workspace, process_environment={})
                finally:
                    temporary.cleanup()

    def test_explicit_exhausted_balance_is_allowed_for_verified_free_fallback(self) -> None:
        text = VALID_ENV.replace(
            "AGENTROUTER_PREPAID_BALANCE_STATUS=available",
            "AGENTROUTER_PREPAID_BALANCE_STATUS=exhausted",
        )
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        runtime = load_runtime_configuration(workspace, process_environment={})

        self.assertEqual(
            runtime.settings.agentrouter_prepaid_balance_status,
            "exhausted",
        )


if __name__ == "__main__":
    unittest.main()
