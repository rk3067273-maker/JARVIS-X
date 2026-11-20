"""Tests for environment configuration and secret handling."""

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


class SettingsTests(unittest.TestCase):
    def test_defaults_are_safe_and_predictable(self) -> None:
        settings = Settings.from_env({})

        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.data_dir, Path("data"))
        self.assertEqual(settings.log_dir, Path("logs"))
        self.assertEqual(settings.primary_provider, "agentrouter")
        self.assertFalse(settings.allow_paid_providers)
        self.assertEqual(settings.agentrouter_base_url, "https://agentrouter.org/v1")
        self.assertEqual(settings.agentrouter_model, "gpt-6.1-sol")
        self.assertEqual(settings.agentrouter_billing_mode, "unknown")
        self.assertEqual(settings.agentrouter_prepaid_balance_status, "unknown")
        self.assertEqual(settings.agentrouter_auto_recharge_status, "unknown")
        self.assertIsNone(settings.agentrouter_api_key)
        self.assertIsNone(settings.gemini_api_key)

    def test_environment_values_are_loaded(self) -> None:
        settings = Settings.from_env(
            {
                "JARVIS_ENV": "testing",
                "JARVIS_LOG_LEVEL": "debug",
                "JARVIS_DATA_DIR": "local-data",
                "JARVIS_LOG_DIR": "local-logs",
                "JARVIS_ALLOW_PAID_PROVIDERS": "false",
                "AGENTROUTER_MODEL": "gpt-6.1-sol",
                "AGENTROUTER_BILLING_MODE": "prepaid",
                "AGENTROUTER_PREPAID_BALANCE_STATUS": "available",
                "AGENTROUTER_AUTO_RECHARGE_STATUS": "disabled",
                "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED": "false",
                "GEMINI_MODEL": "configured-gemini-model",
                "GEMINI_FREE_TIER_VERIFIED": "true",
                "GEMINI_FREE_TIER_VERIFIED_MODEL": "configured-gemini-model",
            }
        )

        self.assertEqual(settings.environment, "testing")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.data_dir, Path("local-data"))
        self.assertEqual(settings.log_dir, Path("local-logs"))
        self.assertFalse(settings.allow_paid_providers)
        self.assertEqual(settings.agentrouter_model, "gpt-6.1-sol")
        self.assertEqual(settings.agentrouter_billing_mode, "prepaid")
        self.assertEqual(settings.agentrouter_prepaid_balance_status, "available")
        self.assertEqual(settings.agentrouter_auto_recharge_status, "disabled")
        self.assertFalse(settings.agentrouter_models_endpoint_supported)
        self.assertEqual(settings.gemini_model, "configured-gemini-model")
        self.assertTrue(settings.gemini_free_tier_verified)
        self.assertEqual(
            settings.gemini_free_tier_verified_model,
            "configured-gemini-model",
        )

    def test_invalid_log_level_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"JARVIS_LOG_LEVEL": "verbose"})

    def test_invalid_provider_policy_is_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"JARVIS_PRIMARY_PROVIDER": "gemini"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"JARVIS_ALLOW_PAID_PROVIDERS": "sometimes"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"JARVIS_ALLOW_PAID_PROVIDERS": "true"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"AGENTROUTER_BILLING_MODE": "maybe"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"AGENTROUTER_MODEL": "different-model"})
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"AGENTROUTER_MODELS_ENDPOINT_SUPPORTED": "true"})

    def test_secret_is_absent_from_repr_and_safe_summary(self) -> None:
        secret = "phase-one-test-secret"
        gemini_secret = "phase-two-gemini-secret"
        credentialed_url = "https://user:password@example.invalid/v1"
        settings = Settings.from_env(
            {
                "AGENTROUTER_API_KEY": secret,
                "AGENTROUTER_BASE_URL": credentialed_url,
                "GEMINI_API_KEY": gemini_secret,
            }
        )
        rendered = f"{settings!r} {settings.safe_summary()}"

        self.assertNotIn(secret, rendered)
        self.assertNotIn(gemini_secret, rendered)
        self.assertNotIn(credentialed_url, rendered)
        self.assertTrue(settings.safe_summary()["agentrouter_api_key_configured"])
        self.assertTrue(settings.safe_summary()["agentrouter_base_url_configured"])
        self.assertEqual(settings.secret_values(), (secret, gemini_secret))


if __name__ == "__main__":
    unittest.main()
