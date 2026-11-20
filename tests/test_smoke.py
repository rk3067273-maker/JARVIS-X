"""Smoke tests for logging, health checks, and the CLI."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.__main__ import main  # noqa: E402
from jarvis_x.config import Settings  # noqa: E402
from jarvis_x.core.health import run_health_checks  # noqa: E402
from jarvis_x.logging_setup import configure_logging  # noqa: E402


class FoundationSmokeTests(unittest.TestCase):
    def test_health_report_is_healthy_and_secret_free(self) -> None:
        secret = "health-check-secret"
        settings = Settings.from_env({"AGENTROUTER_API_KEY": secret})

        report = run_health_checks(settings, PROJECT_ROOT)
        rendered = report.to_json()

        self.assertTrue(report.is_healthy)
        self.assertNotIn(secret, rendered)
        self.assertEqual(json.loads(rendered)["status"], "healthy")

    def test_logging_redacts_known_and_structured_secrets(self) -> None:
        secret = "log-secret-value"
        stream = io.StringIO()
        logger = configure_logging("INFO", secrets=(secret,), stream=stream)

        logger.info(
            "api_key=%s authorization=Bearer %s password=visible-looking-value",
            secret,
            secret,
        )
        rendered = stream.getvalue()

        self.assertNotIn(secret, rendered)
        self.assertNotIn("visible-looking-value", rendered)
        self.assertIn("<redacted>", rendered)

    def test_health_cli_returns_json_without_secret(self) -> None:
        secret = "cli-secret-value"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.dict(
            os.environ,
            {"AGENTROUTER_API_KEY": secret, "JARVIS_LOG_LEVEL": "INFO"},
            clear=False,
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["--health"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "healthy")
        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn(secret, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
