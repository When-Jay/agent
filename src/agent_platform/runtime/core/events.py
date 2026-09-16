import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from agent_platform.runtime.core.interfaces import RuntimeStore

logger = logging.getLogger(__name__)

Subscriber = Callable[["RuntimeEvent"], None]


class RuntimeEventType(str, Enum):
    # Run lifecycle
    RUN_STARTED = "RunStarted"
    RUN_RESUMED = "RunResumed"
    RUN_COMPLETED = "RunCompleted"
    RUN_FAILED = "RunFailed"
    RUN_CANCELLED = "RunCancelled"

    # Agent loop
    TURN_STARTED = "TurnStarted"
    TURN_COMPLETED = "TurnCompleted"
    DECISION_CREATED = "DecisionCreated"

    # Model invocations
    LLM_STARTED = "LLMStarted"
    LLM_COMPLETED = "LLMCompleted"
    LLM_FAILED = "LLMFailed"

    # Tool invocations
    TOOL_CALL_STARTED = "ToolCallStarted"
    TOOL_CALL_COMPLETED = "ToolCallCompleted"
    TOOL_CALL_FAILED = "ToolCallFailed"

    # Workflow nodes
    NODE_STARTED = "NodeStarted"
    NODE_COMPLETED = "NodeCompleted"
    NODE_FAILED = "NodeFailed"

    # Persistence
    CHECKPOINT_CREATED = "CheckpointCreated"

    # Reserved (see runtime-spec.md section 8):
    # RunPaused, ApprovalRequired, UserInputRequired, HumanResponseReceived.


@dataclass(frozen=True)
class RuntimeEvent:
    id: str
    run_id: str
    event_type: RuntimeEventType
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    def __init__(self, store: "RuntimeStore") -> None:
        self._store = store
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a callback for every published event; returns an unsubscribe callable."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def publish(
        self,
        *,
        run_id: str,
        event_type: RuntimeEventType,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            id=str(uuid4()),
            run_id=run_id,
            event_type=event_type,
            payload=payload or {},
        )
        self._store.save_event(event)
        self._notify(event)
        return event

    def list_events(self, run_id: str) -> list[RuntimeEvent]:
        return self._store.list_events(run_id)

    def _notify(self, event: RuntimeEvent) -> None:
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                logger.warning("event subscriber failed for %s", event.event_type, exc_info=True)
