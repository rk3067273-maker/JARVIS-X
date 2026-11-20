"""Mock-only tests for the Phase 2C.2 Main Agent Orchestrator."""

from __future__ import annotations

import io
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_x.logging_setup import configure_logging  # noqa: E402
from jarvis_x.orchestrator.events import EventType, SafeEventRecorder  # noqa: E402
from jarvis_x.orchestrator.models import AgentTask, TaskState  # noqa: E402
from jarvis_x.orchestrator.orchestrator import MainAgentOrchestrator  # noqa: E402
from jarvis_x.orchestrator.policy import (  # noqa: E402
    InvalidTaskTransition,
    OrchestratorPolicy,
)
from jarvis_x.providers.models import (  # noqa: E402
    FailureReason,
    ProviderExecutionResult,
    ProviderOutcome,
    ProviderResponse,
    TokenUsage,
)


def response(
    *,
    provider_name: str = "AgentRouter",
    model_id: str = "gpt-6.1-sol",
    content: str = "Mock response",
) -> ProviderResponse:
    return ProviderResponse(
        provider_name=provider_name,
        model_id=model_id,
        content=content,
        usage=TokenUsage(input_tokens=5, output_tokens=3, total_tokens=8),
        provider_request_id="provider-request-123",
    )


class ScriptedProviderManager:
    """ProviderManager-shaped mock that cannot perform network I/O."""

    primary_provider_name = "AgentRouter"
    primary_model_id = "gpt-6.1-sol"

    def __init__(
        self,
        *,
        outcome: ProviderOutcome = ProviderOutcome.SUCCESS,
        provider_response: ProviderResponse | None = None,
        reason: FailureReason | None = None,
        block: bool = False,
        raise_once: bool = False,
    ) -> None:
        self.outcome = outcome
        self.provider_response = provider_response
        self.reason = reason
        self.block = block
        self.raise_once = raise_once
        self.call_count = 0
        self.requests = []
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    def execute(self, request):
        with self._lock:
            self.call_count += 1
            current_call = self.call_count
            self.requests.append(request)
        self.started.set()
        if self.block:
            self.release.wait(timeout=5)
        if self.raise_once and current_call == 1:
            raise RuntimeError("mock provider boundary failure")
        provider_response = self.provider_response
        if self.outcome is ProviderOutcome.SUCCESS and provider_response is None:
            provider_response = response()
        return ProviderExecutionResult(
            request_id=request.request_id,
            outcome=self.outcome,
            response=provider_response,
            reason=self.reason,
            safe_message="mock safe message" if self.outcome is not ProviderOutcome.SUCCESS else None,
        )


class CancelOnProviderSelectionRecorder(SafeEventRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.task = None

    def emit(self, event) -> None:
        super().emit(event)
        if event.event_type is EventType.PROVIDER_SELECTED and self.task is not None:
            self.task.cancel()


class OrchestratorTests(unittest.TestCase):
    def test_task_creation_uses_unique_task_and_request_ids(self) -> None:
        manager = ScriptedProviderManager()
        orchestrator = MainAgentOrchestrator(manager)

        first = orchestrator.create_task("first")
        second = orchestrator.create_task("second")

        self.assertNotEqual(first.task_id, second.task_id)
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertNotEqual(first.task_id, first.request_id)
        self.assertEqual(
            [event.event_type for event in orchestrator.events],
            [EventType.TASK_CREATED, EventType.TASK_CREATED],
        )

    def test_state_transition_policy_rejects_invalid_transitions(self) -> None:
        policy = OrchestratorPolicy()

        policy.ensure_transition(TaskState.PENDING, TaskState.VALIDATING)
        policy.ensure_transition(TaskState.VALIDATING, TaskState.RUNNING)
        policy.ensure_transition(TaskState.RUNNING, TaskState.COMPLETED)
        with self.assertRaises(InvalidTaskTransition):
            policy.ensure_transition(TaskState.COMPLETED, TaskState.RUNNING)
        with self.assertRaises(InvalidTaskTransition):
            policy.ensure_transition(TaskState.PENDING, TaskState.COMPLETED)

    def test_provider_success_completes_task_and_propagates_usage(self) -> None:
        manager = ScriptedProviderManager(provider_response=response())
        orchestrator = MainAgentOrchestrator(manager)
        task = orchestrator.create_task("safe prompt", max_output_tokens=32)

        result = orchestrator.execute(task)

        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertEqual(result.provider_outcome, ProviderOutcome.SUCCESS)
        self.assertEqual(result.provider_name, "AgentRouter")
        self.assertEqual(result.model_id, "gpt-6.1-sol")
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 3)
        self.assertEqual(result.usage.provider_request_id, "provider-request-123")
        self.assertEqual(manager.call_count, 1)
        self.assertEqual(manager.requests[0].request_id, task.request_id)
        self.assertEqual(
            [event.event_type for event in orchestrator.events],
            [
                EventType.TASK_CREATED,
                EventType.TASK_VALIDATING,
                EventType.TASK_STARTED,
                EventType.PROVIDER_SELECTED,
                EventType.TASK_COMPLETED,
            ],
        )

    def test_gemini_fallback_result_emits_fallback_event(self) -> None:
        manager = ScriptedProviderManager(
            provider_response=response(
                provider_name="Google Gemini",
                model_id="verified-free-model",
                content="Free fallback",
            )
        )
        orchestrator = MainAgentOrchestrator(manager)

        result = orchestrator.execute(orchestrator.create_task("fallback task"))

        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertEqual(result.provider_name, "Google Gemini")
        events = [event.event_type for event in orchestrator.events]
        self.assertIn(EventType.FALLBACK_SELECTED, events)
        self.assertEqual(manager.call_count, 1)

    def test_terminal_provider_outcomes_map_to_task_states(self) -> None:
        cases = (
            (ProviderOutcome.STOP_AND_WAIT, TaskState.STOP_AND_WAIT, EventType.TASK_STOPPED),
            (ProviderOutcome.BLOCKED, TaskState.BLOCKED, EventType.TASK_BLOCKED),
            (ProviderOutcome.FAILED, TaskState.FAILED, EventType.TASK_FAILED),
            (
                ProviderOutcome.OUTCOME_UNKNOWN,
                TaskState.OUTCOME_UNKNOWN,
                EventType.TASK_STOPPED,
            ),
            # Fallback must already be resolved by ProviderManager. The
            # orchestrator never sends a second provider request itself.
            (ProviderOutcome.FALLBACK_ALLOWED, TaskState.FAILED, EventType.TASK_FAILED),
        )

        for outcome, expected_state, expected_event in cases:
            with self.subTest(outcome=outcome):
                manager = ScriptedProviderManager(
                    outcome=outcome,
                    reason=FailureReason.QUOTA_EXHAUSTED,
                )
                orchestrator = MainAgentOrchestrator(manager)
                result = orchestrator.execute(orchestrator.create_task("mapped task"))
                self.assertEqual(result.state, expected_state)
                self.assertEqual(result.provider_outcome, outcome)
                self.assertEqual(orchestrator.events[-1].event_type, expected_event)
                self.assertEqual(manager.call_count, 1)

    def test_concurrent_duplicate_execution_calls_manager_once(self) -> None:
        manager = ScriptedProviderManager(block=True)
        orchestrator = MainAgentOrchestrator(manager)
        task = orchestrator.create_task("concurrent task")
        first_results = []

        thread = threading.Thread(target=lambda: first_results.append(orchestrator.execute(task)))
        thread.start()
        self.assertTrue(manager.started.wait(timeout=2))

        duplicate = orchestrator.execute(task)
        self.assertEqual(duplicate.state, TaskState.BLOCKED)
        self.assertEqual(duplicate.reason, FailureReason.DUPLICATE_REQUEST)
        self.assertEqual(manager.call_count, 1)

        manager.release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first_results[0].state, TaskState.COMPLETED)
        self.assertFalse(orchestrator.is_active(task.task_id))

    def test_cancellation_before_request_never_calls_manager(self) -> None:
        manager = ScriptedProviderManager()
        orchestrator = MainAgentOrchestrator(manager)
        task = orchestrator.create_task("cancel before request")
        task.cancel()

        result = orchestrator.execute(task)

        self.assertEqual(result.state, TaskState.CANCELLED)
        self.assertTrue(result.cancellation_requested)
        self.assertEqual(manager.call_count, 0)
        self.assertEqual(orchestrator.events[-1].event_type, EventType.TASK_CANCELLED)

    def test_cancellation_at_final_dispatch_gate_never_calls_manager(self) -> None:
        manager = ScriptedProviderManager()
        recorder = CancelOnProviderSelectionRecorder()
        orchestrator = MainAgentOrchestrator(manager, events=recorder)
        task = orchestrator.create_task("cancel at dispatch gate")
        recorder.task = task

        result = orchestrator.execute(task)

        self.assertEqual(result.state, TaskState.CANCELLED)
        self.assertEqual(manager.call_count, 0)
        self.assertEqual(orchestrator.events[-1].event_type, EventType.TASK_CANCELLED)

    def test_cancellation_after_request_preserves_unknown_outcome(self) -> None:
        manager = ScriptedProviderManager(
            outcome=ProviderOutcome.OUTCOME_UNKNOWN,
            reason=FailureReason.TIMEOUT,
            block=True,
        )
        orchestrator = MainAgentOrchestrator(manager)
        task = orchestrator.create_task("cancel after send")
        results = []

        thread = threading.Thread(target=lambda: results.append(orchestrator.execute(task)))
        thread.start()
        self.assertTrue(manager.started.wait(timeout=2))
        self.assertFalse(orchestrator.cancel(task.task_id))
        manager.release.set()
        thread.join(timeout=2)

        result = results[0]
        self.assertEqual(result.state, TaskState.OUTCOME_UNKNOWN)
        self.assertNotEqual(result.state, TaskState.CANCELLED)
        self.assertTrue(result.cancellation_requested)
        self.assertEqual(manager.call_count, 1)

    def test_exception_releases_active_lock(self) -> None:
        manager = ScriptedProviderManager(raise_once=True)
        orchestrator = MainAgentOrchestrator(manager)
        first = orchestrator.create_task("first task")

        first_result = orchestrator.execute(first)

        self.assertEqual(first_result.state, TaskState.OUTCOME_UNKNOWN)
        self.assertFalse(orchestrator.is_active(first.task_id))

        second = orchestrator.create_task("second task")
        second_result = orchestrator.execute(second)
        self.assertEqual(second_result.state, TaskState.COMPLETED)
        self.assertFalse(orchestrator.is_active(second.task_id))
        self.assertEqual(manager.call_count, 2)

    def test_invalid_task_fails_before_provider_execution(self) -> None:
        manager = ScriptedProviderManager()
        orchestrator = MainAgentOrchestrator(manager)
        task = orchestrator.create_task("   ")

        result = orchestrator.execute(task)

        self.assertEqual(result.state, TaskState.FAILED)
        self.assertEqual(manager.call_count, 0)
        self.assertFalse(orchestrator.is_active(task.task_id))

    def test_events_and_repr_never_contain_prompt_or_secret(self) -> None:
        secret_prompt = "API_KEY=event-secret-value"
        stream = io.StringIO()
        logger = configure_logging(
            "INFO",
            secrets=("event-secret-value",),
            stream=stream,
        )
        recorder = SafeEventRecorder(logger=logger)
        orchestrator = MainAgentOrchestrator(
            ScriptedProviderManager(),
            events=recorder,
        )
        task = orchestrator.create_task(secret_prompt)

        result = orchestrator.execute(task)
        rendered_events = repr(orchestrator.events)

        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertNotIn(secret_prompt, repr(task))
        self.assertNotIn(secret_prompt, repr(result))
        self.assertNotIn(secret_prompt, rendered_events)
        self.assertNotIn("event-secret-value", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
