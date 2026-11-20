"""Task validation, state transitions, and provider outcome mapping."""

from __future__ import annotations

import re

from jarvis_x.core.errors import JarvisXError
from jarvis_x.orchestrator.models import AgentTask, TaskState
from jarvis_x.providers.models import ProviderOutcome

_TASK_TYPE = re.compile(r"^[A-Za-z0-9_.-]+$")

_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.VALIDATING, TaskState.CANCELLED}),
    TaskState.VALIDATING: frozenset(
        {
            TaskState.RUNNING,
            TaskState.CANCELLED,
            TaskState.BLOCKED,
            TaskState.FAILED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.STOP_AND_WAIT,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.OUTCOME_UNKNOWN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.STOP_AND_WAIT: frozenset(),
    TaskState.BLOCKED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.OUTCOME_UNKNOWN: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class InvalidTaskTransition(JarvisXError):
    default_code = "invalid_task_transition"


class TaskValidationError(JarvisXError):
    default_code = "task_validation_error"


class OrchestratorPolicy:
    """Pure policy object; it never calls a provider or bypasses billing checks."""

    def validate_task(self, task: AgentTask) -> None:
        if not task.prompt or not task.prompt.strip():
            raise TaskValidationError("Task prompt must not be empty.")
        if not task.task_type or not _TASK_TYPE.fullmatch(task.task_type):
            raise TaskValidationError("Task type contains unsupported characters.")
        if task.max_output_tokens is not None and task.max_output_tokens <= 0:
            raise TaskValidationError("Task max_output_tokens must be positive.")

    def ensure_transition(self, current: TaskState, target: TaskState) -> None:
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidTaskTransition(
                f"Task transition {current.value} -> {target.value} is not allowed."
            )

    def state_for_outcome(self, outcome: ProviderOutcome) -> TaskState:
        mapping = {
            ProviderOutcome.SUCCESS: TaskState.COMPLETED,
            # ProviderManager must resolve fallback internally. If this transient
            # result escapes, do not issue another provider request.
            ProviderOutcome.FALLBACK_ALLOWED: TaskState.FAILED,
            ProviderOutcome.STOP_AND_WAIT: TaskState.STOP_AND_WAIT,
            ProviderOutcome.BLOCKED: TaskState.BLOCKED,
            ProviderOutcome.FAILED: TaskState.FAILED,
            ProviderOutcome.OUTCOME_UNKNOWN: TaskState.OUTCOME_UNKNOWN,
        }
        return mapping[outcome]
