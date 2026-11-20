"""No-network tests for the Phase 2B live-test safety gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.config import Settings  # noqa: E402
from jarvis_x.core.errors import ConfigurationError  # noqa: E402
from jarvis_x.env_file import parse_env_text  # noqa: E402
from jarvis_x.providers.base import ProviderError  # noqa: E402
from jarvis_x.providers.live_test import build_provider, preflight_provider  # noqa: E402
from jarvis_x.providers.models import FailureReason  # noqa: E402


class LiveSafetyTests(unittest.TestCase):
    def test_env_parser_does_not_execute_shell_syntax(self) -> None:
        values = parse_env_text(
            """
            # local values
            AGENTROUTER_API_KEY='literal-$(not-executed)'
            GEMINI_API_KEY="literal-gemini-key"
            JARVIS_ALLOW_PAID_PROVIDERS=false
            """
        )

        self.assertEqual(values["AGENTROUTER_API_KEY"], "literal-$(not-executed)")
        self.assertEqual(values["GEMINI_API_KEY"], "literal-gemini-key")

    def test_env_parser_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(ConfigurationError):
            parse_env_text("GEMINI_API_KEY=first\nGEMINI_API_KEY=second\n")

    def test_agentrouter_preflight_requires_all_prepaid_safety_facts(self) -> None:
        safe_settings = Settings.from_env(
            {
                "AGENTROUTER_API_KEY": "mock-router-key",
                "AGENTROUTER_MODEL": "gpt-6.1-sol",
                "AGENTROUTER_BILLING_MODE": "prepaid",
                "AGENTROUTER_PREPAID_BALANCE_STATUS": "available",
                "AGENTROUTER_AUTO_RECHARGE_STATUS": "disabled",
            }
        )
        preflight_provider(build_provider(safe_settings, "agentrouter"))

        unsafe_settings = Settings.from_env(
            {
                "AGENTROUTER_API_KEY": "mock-router-key",
                "AGENTROUTER_MODEL": "gpt-6.1-sol",
                "AGENTROUTER_BILLING_MODE": "prepaid",
                "AGENTROUTER_PREPAID_BALANCE_STATUS": "unknown",
                "AGENTROUTER_AUTO_RECHARGE_STATUS": "disabled",
            }
        )
        with self.assertRaises(ProviderError) as caught:
            preflight_provider(build_provider(unsafe_settings, "agentrouter"))

        self.assertEqual(
            caught.exception.reason,
            FailureReason.BILLING_SAFETY_UNVERIFIED,
        )

    def test_gemini_preflight_binds_free_verification_to_exact_model(self) -> None:
        settings = Settings.from_env(
            {
                "GEMINI_API_KEY": "mock-gemini-key",
                "GEMINI_MODEL": "configured-model",
                "GEMINI_FREE_TIER_VERIFIED": "true",
                "GEMINI_FREE_TIER_VERIFIED_MODEL": "different-model",
            }
        )

        with self.assertRaises(ProviderError) as caught:
            preflight_provider(build_provider(settings, "gemini"))

        self.assertEqual(caught.exception.reason, FailureReason.FREE_TIER_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
