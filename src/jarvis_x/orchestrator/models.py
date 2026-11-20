"""Task and result models for the Main Agent Orchestrator."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from jarvis_x.providers.models import (
    FailureReason,
    ProviderOutcome,
    UsageRecord,
)


class TaskState(StrEnum):
    """Explicit lifecycle states for one orchestrated task."""

    PENDING = "pending"
    VALIDATING = "validating"
    RUNNING = "running"
    COMPLETED = "completed"
    STOP_AND_WAIT = "stop_and_wait"
    BLOCKED = "blocked"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class CancellationToken:
    """Thread-safe process-local cancellation signal."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def __repr__(self) -> str:
        return f"CancellationToken(cancelled={self.is_cancelled})"


@dataclass(frozen=True, slots=True)
class AgentTask:
    """A user task whose prompt is deliberately excluded from repr output."""

    prompt: str = field(repr=False)
    task_type: str = "general"
    max_output_tokens: int | None = None
    task_id: str = field(default_factory=lambda: uuid4().hex)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    cancellation: CancellationToken = field(
        default_factory=CancellationToken,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        task_id = self.task_id.strip()
        request_id = self.request_id.strip()
        if not task_id:
            raise ValueError("task_id must not be empty.")
        if not request_id:
            raise ValueError("request_id must not be empty.")
        if task_id == request_id:
            raise ValueError("task_id and request_id must be different.")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "request_id", request_id)

    def cancel(self) -> None:
        self.cancellation.cancel()


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Secret-safe task result with optional content and usage metadata."""

    task_id: str
    request_id: str
    state: TaskState
    provider_outcome: ProviderOutcome
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    content: str | None = field(default=None, repr=False)
    provider_name: str | None = None
    model_id: str | None = None
    usage: UsageRecord | None = None
    reason: FailureReason | None = None
    safe_message: str | None = None
    cancellation_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state not in {
            TaskState.PENDING,
            TaskState.VALIDATING,
            TaskState.RUNNING,
        }
