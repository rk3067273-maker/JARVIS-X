"""Filesystem-local, no-network tests for secure Phase 2C.4 preflight."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.preflight import PreflightReport, PreflightStatus, run_preflight  # noqa: E402


VALID_ENV = """\
JARVIS_PRIMARY_PROVIDER=agentrouter
JARVIS_ALLOW_PAID_PROVIDERS=false
AGENTROUTER_BASE_URL=https://agentrouter.org/v1
AGENTROUTER_API_KEY=fake-agent-key
AGENTROUTER_MODEL=gpt-6.1-sol
AGENTROUTER_BILLING_MODE=prepaid
AGENTROUTER_PREPAID_BALANCE_STATUS=available
AGENTROUTER_AUTO_RECHARGE_STATUS=disabled
AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=false
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_API_KEY=fake-gemini-key
GEMINI_MODEL=verified-free-model
GEMINI_FREE_TIER_VERIFIED=true
GEMINI_FREE_TIER_VERIFIED_MODEL=verified-free-model
"""


def check(report: PreflightReport, name: str):
    return next(item for item in report.checks if item.name == name)


class PreflightTests(unittest.TestCase):
    def make_workspace(
        self,
        *,
        env_text: str | None = VALID_ENV,
        mode: int = 0o600,
        symlink: bool = False,
    ):
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "tests")
        workspace = Path(temporary.name)
        (workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n")
        (workspace / ".gitignore").write_text(".env\n")
        if env_text is not None:
            if symlink:
                target = workspace / "secret-target"
                target.write_text(env_text)
                os.chmod(target, mode)
                (workspace / ".env").symlink_to(target.name)
            else:
                env_path = workspace / ".env"
                env_path.write_text(env_text)
                os.chmod(env_path, mode)
        return temporary, workspace

    def test_missing_env_is_not_configured(self) -> None:
        temporary, workspace = self.make_workspace(env_text=None)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(
            check(report, "env_present").status,
            PreflightStatus.NOT_CONFIGURED,
        )
        self.assertNotEqual(report.status, PreflightStatus.PASS)

    def test_env_symlink_is_rejected(self) -> None:
        temporary, workspace = self.make_workspace(symlink=True)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(check(report, "env_not_symlink").status, PreflightStatus.FAIL)
        self.assertEqual(report.status, PreflightStatus.FAIL)

    def test_unsafe_permissions_are_rejected(self) -> None:
        temporary, workspace = self.make_workspace(mode=0o644)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(check(report, "env_permissions").status, PreflightStatus.FAIL)

    def test_blank_api_keys_are_not_configured(self) -> None:
        text = VALID_ENV.replace("AGENTROUTER_API_KEY=fake-agent-key", "AGENTROUTER_API_KEY=")
        text = text.replace("GEMINI_API_KEY=fake-gemini-key", "GEMINI_API_KEY=")
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(
            check(report, "agentrouter_api_key").status,
            PreflightStatus.NOT_CONFIGURED,
        )
        self.assertEqual(
            check(report, "gemini_api_key").status,
            PreflightStatus.NOT_CONFIGURED,
        )

    def test_valid_configuration_passes_as_attestation_only(self) -> None:
        temporary, workspace = self.make_workspace()
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(report.status, PreflightStatus.PASS)
        self.assertEqual(check(report, "agentrouter_model").status, PreflightStatus.PASS)
        self.assertEqual(check(report, "provider_manager").status, PreflightStatus.PASS)
        self.assertIn("attested", check(report, "agentrouter_prepaid_balance").message)
        self.assertIn("not verified", check(report, "agentrouter_prepaid_balance").message)

    def test_wrong_agent_model_and_paid_provider_are_rejected(self) -> None:
        text = VALID_ENV.replace("AGENTROUTER_MODEL=gpt-6.1-sol", "AGENTROUTER_MODEL=wrong")
        text = text.replace("JARVIS_ALLOW_PAID_PROVIDERS=false", "JARVIS_ALLOW_PAID_PROVIDERS=true")
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(check(report, "agentrouter_model").status, PreflightStatus.FAIL)
        self.assertEqual(
            check(report, "paid_providers_disabled").status,
            PreflightStatus.FAIL,
        )

    def test_unknown_billing_balance_and_recharge_never_pass(self) -> None:
        text = VALID_ENV.replace("AGENTROUTER_BILLING_MODE=prepaid", "AGENTROUTER_BILLING_MODE=unknown")
        text = text.replace(
            "AGENTROUTER_PREPAID_BALANCE_STATUS=available",
            "AGENTROUTER_PREPAID_BALANCE_STATUS=unknown",
        )
        text = text.replace(
            "AGENTROUTER_AUTO_RECHARGE_STATUS=disabled",
            "AGENTROUTER_AUTO_RECHARGE_STATUS=unknown",
        )
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(
            check(report, "agentrouter_billing_mode").status,
            PreflightStatus.FAIL,
        )
        self.assertEqual(
            check(report, "agentrouter_prepaid_balance").status,
            PreflightStatus.NEEDS_MANUAL_VERIFICATION,
        )
        self.assertEqual(
            check(report, "agentrouter_auto_recharge").status,
            PreflightStatus.NEEDS_MANUAL_VERIFICATION,
        )

    def test_enabled_auto_recharge_is_rejected(self) -> None:
        text = VALID_ENV.replace(
            "AGENTROUTER_AUTO_RECHARGE_STATUS=disabled",
            "AGENTROUTER_AUTO_RECHARGE_STATUS=enabled",
        )
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(
            check(report, "agentrouter_auto_recharge").status,
            PreflightStatus.FAIL,
        )

    def test_agentrouter_model_discovery_must_remain_disabled(self) -> None:
        text = VALID_ENV.replace(
            "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=false",
            "AGENTROUTER_MODELS_ENDPOINT_SUPPORTED=true",
        )
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertEqual(
            check(report, "agentrouter_model_discovery_disabled").status,
            PreflightStatus.FAIL,
        )

    def test_gemini_unverified_mismatch_and_exact_verification(self) -> None:
        cases = (
            (
                VALID_ENV.replace("GEMINI_FREE_TIER_VERIFIED=true", "GEMINI_FREE_TIER_VERIFIED=false"),
                PreflightStatus.NEEDS_MANUAL_VERIFICATION,
            ),
            (
                VALID_ENV.replace(
                    "GEMINI_FREE_TIER_VERIFIED_MODEL=verified-free-model",
                    "GEMINI_FREE_TIER_VERIFIED_MODEL=different-model",
                ),
                PreflightStatus.FAIL,
            ),
            (VALID_ENV, PreflightStatus.PASS),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                temporary, workspace = self.make_workspace(env_text=text)
                try:
                    report = run_preflight(workspace, process_environment={})
                    self.assertEqual(
                        check(report, "gemini_free_tier_exact_model").status,
                        expected,
                    )
                finally:
                    temporary.cleanup()

    def test_process_environment_overrides_only_recognized_env_values(self) -> None:
        text = VALID_ENV.replace("AGENTROUTER_MODEL=gpt-6.1-sol", "AGENTROUTER_MODEL=wrong")
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(
            workspace,
            process_environment={
                "AGENTROUTER_MODEL": "gpt-6.1-sol",
                "UNRECOGNIZED_SECRET": "must-not-appear",
            },
        )

        self.assertEqual(check(report, "agentrouter_model").status, PreflightStatus.PASS)
        rendered = json.dumps(report.to_dict())
        self.assertNotIn("must-not-appear", rendered)

    def test_report_never_contains_api_keys(self) -> None:
        secret = "preflight-secret-must-not-leak"
        text = VALID_ENV.replace("fake-agent-key", secret).replace("fake-gemini-key", secret)
        temporary, workspace = self.make_workspace(env_text=text)
        self.addCleanup(temporary.cleanup)

        report = run_preflight(workspace, process_environment={})

        self.assertNotIn(secret, json.dumps(report.to_dict()))

    def test_preflight_makes_zero_network_calls(self) -> None:
        temporary, workspace = self.make_workspace()
        self.addCleanup(temporary.cleanup)

        with patch("urllib.request.urlopen") as urlopen:
            report = run_preflight(workspace, process_environment={})

        urlopen.assert_not_called()
        self.assertEqual(report.to_dict()["network_calls"], 0)
        self.assertEqual(report.to_dict()["provider_requests"], 0)


if __name__ == "__main__":
    unittest.main()
