"""Run Orchestrator: routes dispatched runs to their runtime.

Worker-side entry point (runtime-dispatch-spec.md section 4):

1. Load the Run from the durable store.
2. Skip terminal runs (idempotency: retried tasks never re-execute).
3. Route by `runtime_type` to the Agent Runtime adapter (or the
   workflow path once definition loading is wired).
"""

import asyncio
import logging

from agent_platform.config import Settings
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    RuntimeStore,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

# In-memory stores are process-wide singletons so the eager worker path
# (tests, local dev) shares durable state with the API in the same process.
_memory_stores: dict[str, InMemoryRuntimeStore] = {}


def create_runtime_store(database_url: str) -> RuntimeStore:
    """Build a RuntimeStore for a database URL.

    `sqlite:///:memory:` keeps one shared in-memory store per URL;
    every durable URL gets its own engine against the shared database.
    """
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        store = _memory_stores.get(database_url)
        if store is None:
            store = InMemoryRuntimeStore()
            _memory_stores[database_url] = store
        return store
    from agent_platform.infrastructure.sqlalchemy_store import SQLAlchemyRuntimeStore

    return SQLAlchemyRuntimeStore(database_url)


class RuntimeOrchestrator:
    """Executes dispatched runs; subscriber-aware through `events`."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        agent_adapter=None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._store = store
        self._runs = RunManager(store)
        self.events = event_bus or EventBus(store)
        self._agent_adapter = agent_adapter

    def execute(self, run_id: str) -> None:
        run = self._runs.get_run(run_id)
        if run.status in _TERMINAL_STATUSES:
            logger.info(
                "run %s already terminal (%s); skipping re-execution",
                run_id,
                run.status.value,
            )
            return
        if run.runtime_type == "agent":
            self._execute_agent(run_id)
        elif run.runtime_type == "workflow":
            self._execute_workflow(run_id)
        else:
            self._fail(run_id, f"unknown runtime_type: {run.runtime_type}")

    # --- routing ------------------------------------------------------------

    def _execute_agent(self, run_id: str) -> None:
        if self._agent_adapter is None:
            self._fail(run_id, "agent runtime adapter is not configured")
            return
        asyncio.run(self._agent_adapter.run(run_id))

    def _execute_workflow(self, run_id: str) -> None:
        # V1: workflow definition loading is not wired to dispatch yet.
        self._runs.start_run(run_id)
        self._fail(run_id, "workflow dispatch is not available in V1")

    def _fail(self, run_id: str, error: str) -> None:
        self._runs.fail_run(run_id, error=error)
        self.events.publish(
            run_id=run_id,
            event_type=RuntimeEventType.RUN_FAILED,
            payload={"error": error, "reason": "dispatch"},
        )


def _default_model_factory(model_spec: str):
    if model_spec == "default":
        raise ValueError(
            "no model configured: set application metadata "
            "{'agent': {'model': '<provider>:<model-name>'}}"
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(model_spec)


def build_agent_adapter(store: RuntimeStore, *, event_bus: EventBus):
    """Compose the default DeepAgents adapter over Runtime Core."""
    from agent_platform.runtime.agent import DeepAgentsRuntimeAdapter
    from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability

    return DeepAgentsRuntimeAdapter(
        store,
        model_factory=_default_model_factory,
        tool_capability=InMemoryToolCapability(),
        event_bus=event_bus,
    )


def build_default_orchestrator(settings: Settings | None = None) -> RuntimeOrchestrator:
    """Worker-side default composition: store + agent adapter + shared bus."""
    settings = settings or Settings()
    store = create_runtime_store(settings.database_url)
    events = EventBus(store)
    return RuntimeOrchestrator(
        store,
        agent_adapter=build_agent_adapter(store, event_bus=events),
        event_bus=events,
    )
