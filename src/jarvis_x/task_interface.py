"""CLI-facing task execution helpers with a network-free dry-run boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from jarvis_x.logging_setup import redact_text
from jarvis_x.orchestrator.events import SafeEventRecorder
from jarvis_x.orchestrator.models import TaskResult, TaskState
from jarvis_x.orchestrator.orchestrator import MainAgentOrchestrator, ProviderExecutor
from jarvis_x.providers.models import (
    ChatRequest,
    ProviderExecutionResult,
    ProviderOutcome,
    ProviderResponse,
    TokenUsage,
)


class LocalDryRunProviderManager:
    """ProviderManager-shaped local validator that performs no transport I/O."""

    primary_provider_name = "Local Dry Run"
    primary_model_id = "local-validation"

    def __init__(self) -> None:
        self.execute_count = 0

    def execute(self, request: ChatRequest) -> ProviderExecutionResult:
        self.execute_count += 1
        response = ProviderResponse(
            provider_name=self.primary_provider_name,
            model_id=self.primary_model_id,
            content="Dry-run validation passed; no provider request was sent.",
            usage=TokenUsage(),
        )
        return ProviderExecutionResult(
            request_id=request.request_id,
            outcome=ProviderOutcome.SUCCESS,
            response=response,
        )


class TaskExecutionInterface:
    """Small process-local facade over MainAgentOrchestrator."""

    def __init__(self, orchestrator: MainAgentOrchestrator) -> None:
        self._orchestrator = orchestrator

    def submit(
        self,
        description: str,
        *,
        task_type: str = "general",
        max_output_tokens: int | None = None,
    ) -> TaskResult:
        task = self._orchestrator.create_task(
            description,
            task_type=task_type,
            max_output_tokens=max_output_tokens,
        )
        return self._orchestrator.execute(task)

    def status(self, task_id: str) -> TaskResult | None:
        return self._orchestrator.result_for(task_id)


def build_dry_run_interface(
    *,
    logger: logging.Logger | None = None,
) -> tuple[TaskExecutionInterface, LocalDryRunProviderManager]:
    manager = LocalDryRunProviderManager()
    events = SafeEventRecorder(logger=logger)
    orchestrator = MainAgentOrchestrator(manager, events=events)
    return TaskExecutionInterface(orchestrator), manager


def build_task_interface(
    provider_manager: ProviderExecutor,
    *,
    logger: logging.Logger | None = None,
) -> TaskExecutionInterface:
    events = SafeEventRecorder(logger=logger)
    return TaskExecutionInterface(
        MainAgentOrchestrator(provider_manager, events=events)
    )


def task_result_payload(
    result: TaskResult,
    *,
    secrets: Iterable[str] = (),
    dry_run: bool = False,
) -> dict[str, object]:
    """Return a JSON-ready, redacted task status and result payload."""

    usage = result.usage
    return {
        "task_id": result.task_id,
        "state": result.state.name,
        "provider_outcome": result.provider_outcome.name,
        "created_at": result.created_at,
        "updated_at": result.updated_at,
        "provider": redact_text(result.provider_name, secrets)
        if result.provider_name
        else None,
        "model": redact_text(result.model_id, secrets) if result.model_id else None,
        "result": redact_text(result.content, secrets) if result.content else None,
        "usage": {
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "provider_request_id": redact_text(usage.provider_request_id, secrets)
            if usage and usage.provider_request_id
            else None,
        },
        "error": {
            "reason": result.reason.value if result.reason else None,
            "summary": redact_text(result.safe_message, secrets)
            if result.safe_message
            else None,
        },
        "cancellation_requested": result.cancellation_requested,
        "dry_run": dry_run,
        "history_scope": "process_local",
    }


def task_exit_code(state: TaskState) -> int:
    return {
        TaskState.COMPLETED: 0,
        TaskState.STOP_AND_WAIT: 10,
        TaskState.BLOCKED: 11,
        TaskState.FAILED: 12,
        TaskState.OUTCOME_UNKNOWN: 13,
        TaskState.CANCELLED: 14,
        TaskState.PENDING: 15,
        TaskState.VALIDATING: 15,
        TaskState.RUNNING: 15,
    }[state]


def run_local_orchestrator_health() -> dict[str, object]:
    """Run one entirely local smoke task without building a real provider."""

    interface, manager = build_dry_run_interface(logger=logging.getLogger("jarvis_x.null"))
    result = interface.submit("local orchestrator health check", task_type="health")
    return {
        "status": "healthy" if result.state is TaskState.COMPLETED else "unhealthy",
        "state": result.state.name,
        "provider_outcome": result.provider_outcome.name,
        "local_execute_count": manager.execute_count,
        "provider_api_calls": 0,
        "history_scope": "process_local",
    }
