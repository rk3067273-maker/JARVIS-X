"""Secret-free lifecycle events and in-memory event recording."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from jarvis_x.orchestrator.models import TaskState
from jarvis_x.providers.models import FailureReason


class EventType(StrEnum):
    TASK_CREATED = "task_created"
    TASK_VALIDATING = "task_validating"
    TASK_STARTED = "task_started"
    PROVIDER_SELECTED = "provider_selected"
    FALLBACK_SELECTED = "fallback_selected"
    TASK_COMPLETED = "task_completed"
    TASK_STOPPED = "task_stopped"
    TASK_BLOCKED = "task_blocked"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """Lifecycle metadata that never contains prompts, responses, or secrets."""

    task_id: str
    event_type: EventType
    state: TaskState
    provider_name: str | None = None
    model_id: str | None = None
    reason: FailureReason | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class SafeEventRecorder:
    """Thread-safe event recorder with controlled, secret-free log fields."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._events: list[LifecycleEvent] = []
        self._lock = threading.Lock()
        self._logger = logger or logging.getLogger("jarvis_x.orchestrator")

    def emit(self, event: LifecycleEvent) -> None:
        with self._lock:
            self._events.append(event)
        self._logger.info(
            "lifecycle_event=%s task_id=%s state=%s provider=%s model=%s reason=%s",
            event.event_type.value,
            event.task_id,
            event.state.value,
            event.provider_name or "none",
            event.model_id or "none",
            event.reason.value if event.reason else "none",
        )

    @property
    def events(self) -> tuple[LifecycleEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def for_task(self, task_id: str) -> tuple[LifecycleEvent, ...]:
        with self._lock:
            return tuple(event for event in self._events if event.task_id == task_id)
