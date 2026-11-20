"""Main Agent Orchestrator using ProviderManager as the only provider boundary."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jarvis_x.orchestrator.events import EventType, LifecycleEvent, SafeEventRecorder
from jarvis_x.orchestrator.models import AgentTask, TaskResult, TaskState
from jarvis_x.orchestrator.policy import OrchestratorPolicy, TaskValidationError
from jarvis_x.providers.models import (
    ChatMessage,
    ChatRequest,
    FailureReason,
    ProviderExecutionResult,
    ProviderOutcome,
)


class ProviderExecutor(Protocol):
    @property
    def primary_provider_name(self) -> str: ...

    @property
    def primary_model_id(self) -> str | None: ...

    def execute(self, request: ChatRequest) -> ProviderExecutionResult: ...


@dataclass(slots=True)
class _TaskContext:
    task: AgentTask
    state: TaskState = TaskState.PENDING
    request_started: bool = False


class MainAgentOrchestrator:
    """Validate, execute once, map outcomes, and emit safe lifecycle events."""

    def __init__(
        self,
        provider_manager: ProviderExecutor,
        *,
        policy: OrchestratorPolicy | None = None,
        events: SafeEventRecorder | None = None,
    ) -> None:
        self._provider_manager = provider_manager
        self._policy = policy or OrchestratorPolicy()
        self._events = events or SafeEventRecorder()
        self._lock = threading.RLock()
        self._active: dict[str, _TaskContext] = {}
        self._results: dict[str, TaskResult] = {}
        self._created_task_ids: set[str] = set()

    @property
    def events(self) -> tuple[LifecycleEvent, ...]:
        return self._events.events

    def create_task(
        self,
        prompt: str,
        *,
        task_type: str = "general",
        max_output_tokens: int | None = None,
    ) -> AgentTask:
        task = AgentTask(
            prompt=prompt,
            task_type=task_type,
            max_output_tokens=max_output_tokens,
        )
        self._mark_created(task)
        return task

    def _mark_created(self, task: AgentTask) -> None:
        emit = False
        with self._lock:
            if task.task_id not in self._created_task_ids:
                self._created_task_ids.add(task.task_id)
                emit = True
        if emit:
            self._emit(task.task_id, EventType.TASK_CREATED, TaskState.PENDING)

    def _emit(
        self,
        task_id: str,
        event_type: EventType,
        state: TaskState,
        *,
        provider_name: str | None = None,
        model_id: str | None = None,
        reason: FailureReason | None = None,
    ) -> None:
        self._events.emit(
            LifecycleEvent(
                task_id=task_id,
                event_type=event_type,
                state=state,
                provider_name=provider_name,
                model_id=model_id,
                reason=reason,
            )
        )

    def _transition(self, context: _TaskContext, target: TaskState) -> None:
        self._policy.ensure_transition(context.state, target)
        context.state = target

    def cancel(self, task_id: str) -> bool:
        """Request cancellation; return True only if no provider request started."""

        with self._lock:
            context = self._active.get(task_id)
            if context is None:
                return False
            context.task.cancel()
            return not context.request_started

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active

    def result_for(self, task_id: str) -> TaskResult | None:
        with self._lock:
            return self._results.get(task_id)

    def _store_result(self, result: TaskResult) -> TaskResult:
        with self._lock:
            self._results[result.task_id] = result
        return result

    def _duplicate_result(self, task: AgentTask) -> TaskResult:
        result = TaskResult(
            task_id=task.task_id,
            request_id=task.request_id,
            state=TaskState.BLOCKED,
            provider_outcome=ProviderOutcome.BLOCKED,
            created_at=task.created_at,
            updated_at=datetime.now(UTC).isoformat(),
            reason=FailureReason.DUPLICATE_REQUEST,
            safe_message="The task is already active; duplicate execution is blocked.",
            cancellation_requested=task.cancellation.is_cancelled,
        )
        self._emit(
            task.task_id,
            EventType.TASK_BLOCKED,
            TaskState.BLOCKED,
            reason=FailureReason.DUPLICATE_REQUEST,
        )
        return result

    def _event_for_terminal_state(self, state: TaskState) -> EventType:
        return {
            TaskState.COMPLETED: EventType.TASK_COMPLETED,
            TaskState.STOP_AND_WAIT: EventType.TASK_STOPPED,
            TaskState.BLOCKED: EventType.TASK_BLOCKED,
            TaskState.FAILED: EventType.TASK_FAILED,
            TaskState.OUTCOME_UNKNOWN: EventType.TASK_STOPPED,
            TaskState.CANCELLED: EventType.TASK_CANCELLED,
        }[state]

    def execute(self, task: AgentTask) -> TaskResult:
        """Execute a task once without implementing provider retries or fallback."""

        self._mark_created(task)
        with self._lock:
            previous = self._results.get(task.task_id)
            if previous is not None:
                return previous
            if task.task_id in self._active:
                return self._duplicate_result(task)
            context = _TaskContext(task=task)
            self._active[task.task_id] = context

        try:
            self._transition(context, TaskState.VALIDATING)
            self._emit(task.task_id, EventType.TASK_VALIDATING, context.state)
            self._policy.validate_task(task)
            chat_request = ChatRequest(
                messages=(ChatMessage(role="user", content=task.prompt),),
                task_type=task.task_type,
                max_output_tokens=task.max_output_tokens,
                request_id=task.request_id,
            )

            with self._lock:
                if task.cancellation.is_cancelled:
                    self._transition(context, TaskState.CANCELLED)
                else:
                    self._transition(context, TaskState.RUNNING)

            if context.state is TaskState.CANCELLED:
                result = TaskResult(
                    task_id=task.task_id,
                    request_id=task.request_id,
                    state=TaskState.CANCELLED,
                    provider_outcome=ProviderOutcome.BLOCKED,
                    created_at=task.created_at,
                    updated_at=datetime.now(UTC).isoformat(),
                    safe_message="Task was cancelled before provider execution.",
                    cancellation_requested=True,
                )
                self._emit(task.task_id, EventType.TASK_CANCELLED, context.state)
                return self._store_result(result)

            self._emit(task.task_id, EventType.TASK_STARTED, context.state)
            self._emit(
                task.task_id,
                EventType.PROVIDER_SELECTED,
                context.state,
                provider_name=self._provider_manager.primary_provider_name,
                model_id=self._provider_manager.primary_model_id,
            )

            # Final cancellation gate immediately before committing the task to
            # ProviderManager. After request_started becomes true, cancellation
            # is advisory only and can never trigger a retry or replacement call.
            with self._lock:
                if task.cancellation.is_cancelled:
                    self._transition(context, TaskState.CANCELLED)
                else:
                    context.request_started = True

            if context.state is TaskState.CANCELLED:
                result = TaskResult(
                    task_id=task.task_id,
                    request_id=task.request_id,
                    state=TaskState.CANCELLED,
                    provider_outcome=ProviderOutcome.BLOCKED,
                    created_at=task.created_at,
                    updated_at=datetime.now(UTC).isoformat(),
                    safe_message="Task was cancelled before provider execution.",
                    cancellation_requested=True,
                )
                self._emit(task.task_id, EventType.TASK_CANCELLED, context.state)
                return self._store_result(result)

            execution = self._provider_manager.execute(chat_request)
            response = execution.response
            if (
                response is not None
                and response.provider_name != self._provider_manager.primary_provider_name
            ):
                self._emit(
                    task.task_id,
                    EventType.FALLBACK_SELECTED,
                    context.state,
                    provider_name=response.provider_name,
                    model_id=response.model_id,
                )

            target = self._policy.state_for_outcome(execution.outcome)
            self._transition(context, target)
            usage = response.to_usage_record() if response is not None else None
            result = TaskResult(
                task_id=task.task_id,
                request_id=task.request_id,
                state=target,
                provider_outcome=execution.outcome,
                created_at=task.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                content=response.content if response is not None else None,
                provider_name=response.provider_name if response is not None else None,
                model_id=response.model_id if response is not None else None,
                usage=usage,
                reason=execution.reason,
                safe_message=execution.safe_message,
                cancellation_requested=task.cancellation.is_cancelled,
            )
            self._emit(
                task.task_id,
                self._event_for_terminal_state(target),
                target,
                provider_name=result.provider_name,
                model_id=result.model_id,
                reason=result.reason,
            )
            return self._store_result(result)
        except TaskValidationError as error:
            self._transition(context, TaskState.FAILED)
            result = TaskResult(
                task_id=task.task_id,
                request_id=task.request_id,
                state=TaskState.FAILED,
                provider_outcome=ProviderOutcome.FAILED,
                created_at=task.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                safe_message=error.safe_message,
                cancellation_requested=task.cancellation.is_cancelled,
            )
            self._emit(task.task_id, EventType.TASK_FAILED, context.state)
            return self._store_result(result)
        except Exception as error:
            target = (
                TaskState.OUTCOME_UNKNOWN
                if context.request_started
                else TaskState.FAILED
            )
            self._transition(context, target)
            outcome = (
                ProviderOutcome.OUTCOME_UNKNOWN
                if context.request_started
                else ProviderOutcome.FAILED
            )
            result = TaskResult(
                task_id=task.task_id,
                request_id=task.request_id,
                state=target,
                provider_outcome=outcome,
                created_at=task.created_at,
                updated_at=datetime.now(UTC).isoformat(),
                safe_message=f"Task execution stopped after {type(error).__name__}.",
                cancellation_requested=task.cancellation.is_cancelled,
            )
            self._emit(
                task.task_id,
                self._event_for_terminal_state(target),
                target,
            )
            return self._store_result(result)
        finally:
            with self._lock:
                self._active.pop(task.task_id, None)
