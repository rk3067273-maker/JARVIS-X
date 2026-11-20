"""Command-line entry point for JARVIS X."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from jarvis_x import __version__
from jarvis_x.config import Settings
from jarvis_x.core.errors import ConfigurationError, JarvisXError
from jarvis_x.core.health import run_health_checks
from jarvis_x.logging_setup import configure_logging
from jarvis_x.orchestrator.orchestrator import ProviderExecutor
from jarvis_x.preflight import PreflightStatus, run_preflight
from jarvis_x.providers.manager import ProviderManager
from jarvis_x.runtime_config import RuntimeConfiguration, load_runtime_configuration
from jarvis_x.task_interface import (
    build_dry_run_interface,
    build_task_interface,
    run_local_orchestrator_health,
    task_exit_code,
    task_result_payload,
)

ProviderManagerFactory = Callable[[Settings, logging.Logger], ProviderExecutor]
RuntimeConfigurationLoader = Callable[[Path], RuntimeConfiguration]


def build_parser() -> argparse.ArgumentParser:
    """Build the backward-compatible JARVIS X command-line interface."""

    parser = argparse.ArgumentParser(
        prog="jarvis-x",
        description="JARVIS X modular AI assistant foundation",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--health",
        action="store_true",
        help="run local, non-destructive health checks",
    )
    actions.add_argument(
        "--version",
        action="store_true",
        help="show the installed JARVIS X version",
    )
    actions.add_argument(
        "--orchestrator-health",
        action="store_true",
        help="run an entirely local orchestrator smoke check",
    )
    actions.add_argument(
        "--preflight",
        action="store_true",
        help="inspect secure local readiness without any provider request",
    )
    actions.add_argument(
        "--task",
        metavar="DESCRIPTION",
        help="submit one task through Orchestrator and ProviderManager",
    )
    actions.add_argument(
        "--dry-run-task",
        metavar="DESCRIPTION",
        help="validate and execute a task locally without any provider API call",
    )
    parser.add_argument(
        "--task-type",
        default="general",
        help="safe task category used by the orchestrator (default: general)",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="optional provider output-token limit for --task",
    )
    return parser


def _default_provider_manager_factory(
    settings: Settings,
    logger: logging.Logger,
) -> ProviderManager:
    return ProviderManager.from_settings(settings, logger=logger)


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_manager_factory: ProviderManagerFactory | None = None,
    runtime_configuration_loader: RuntimeConfigurationLoader | None = None,
) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.version:
        print(f"JARVIS X {__version__}")
        return 0

    if arguments.orchestrator_health:
        payload = run_local_orchestrator_health()
        _print_json(payload)
        return 0 if payload["status"] == "healthy" else 1

    if arguments.preflight:
        report = run_preflight(Path.cwd())
        _print_json(report.to_dict())
        if report.status is PreflightStatus.PASS:
            return 0
        if report.status is PreflightStatus.FAIL:
            return 1
        return 2

    if arguments.dry_run_task is not None:
        interface, _manager = build_dry_run_interface()
        result = interface.submit(
            arguments.dry_run_task,
            task_type=arguments.task_type,
            max_output_tokens=arguments.max_output_tokens,
        )
        _print_json(task_result_payload(result, dry_run=True))
        return task_exit_code(result.state)

    if not arguments.health and arguments.task is None:
        parser.print_help()
        return 0

    if arguments.health:
        try:
            settings = Settings.from_env()
        except ConfigurationError as error:
            logger = configure_logging("ERROR")
            logger.error("Configuration failed: %s", error.safe_message)
            return 2
    else:
        loader = runtime_configuration_loader or load_runtime_configuration
        try:
            runtime_configuration = loader(Path.cwd())
            settings = runtime_configuration.settings
        except ConfigurationError as error:
            logger = configure_logging("ERROR")
            logger.error("Runtime configuration failed: %s", error.safe_message)
            return 2

    logger = configure_logging(
        settings.log_level,
        secrets=settings.secret_values(),
    )
    logger.debug("Configuration loaded: %s", settings.safe_summary())

    if arguments.health:
        try:
            report = run_health_checks(settings)
        except JarvisXError as error:
            logger.error("Health check failed [%s]: %s", error.code, error.safe_message)
            return 3
        except Exception as error:  # Defensive CLI boundary; never expose raw details.
            logger.error("Unexpected health-check failure: %s", type(error).__name__)
            return 4

        print(report.to_json())
        return 0 if report.is_healthy else 1

    factory = provider_manager_factory or _default_provider_manager_factory
    try:
        provider_manager = factory(settings, logger)
        interface = build_task_interface(provider_manager, logger=logger)
        result = interface.submit(
            arguments.task,
            task_type=arguments.task_type,
            max_output_tokens=arguments.max_output_tokens,
        )
    except JarvisXError as error:
        logger.error("Task setup failed [%s]: %s", error.code, error.safe_message)
        return 3
    except Exception as error:  # Never expose raw exception text or settings.
        logger.error("Unexpected task-interface failure: %s", type(error).__name__)
        return 4

    _print_json(
        task_result_payload(
            result,
            secrets=settings.secret_values(),
            dry_run=False,
        )
    )
    return task_exit_code(result.state)


if __name__ == "__main__":
    raise SystemExit(main())
