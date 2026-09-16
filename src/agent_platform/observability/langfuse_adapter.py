"""Langfuse event subscriber: optional, non-blocking trace export.

Maps RuntimeEvents onto Langfuse traces (runtime-dispatch-spec.md
section 7, deepagents-runtime-spec.md section 10):

* RunStarted            -> root span (the trace), id = UUID(run_id).hex
* LLMStarted/Completed  -> generation with token usage
* ToolCall*             -> tool span
* RunCompleted/Failed   -> trace closed and flushed

Disabled unless LANGFUSE_PUBLIC_KEY/SECRET_KEY are configured or a
client is injected (tests). Every handler is failure-isolated: Langfuse
problems must never fail Runtime execution.
"""

import logging
from typing import Any
from uuid import UUID

from agent_platform.config import Settings
from agent_platform.runtime.core.events import EventBus, RuntimeEvent, RuntimeEventType

logger = logging.getLogger(__name__)


class LangfuseEventSubscriber:
    """Subscribes to the EventBus and exports runs as Langfuse traces."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        resolved = settings or Settings()
        self._client = client
        self._enabled = client is not None or bool(
            resolved.langfuse_public_key and resolved.langfuse_secret_key
        )
        self._traces: dict[str, Any] = {}
        self._models: dict[str, str | None] = {}
        self._unsubscribe = None
        if self._enabled and client is None:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=resolved.langfuse_public_key,
                secret_key=resolved.langfuse_secret_key,
                host=resolved.langfuse_host,
            )
        if self._enabled:
            self._unsubscribe = event_bus.subscribe(self._on_event)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()

    # --- event handling -------------------------------------------------------

    def _on_event(self, event: RuntimeEvent) -> None:
        if not self._enabled:
            return
        try:
            self._handle(event)
        except Exception:  # noqa: BLE001 - observability must never break execution
            logger.warning(
                "langfuse export failed for event %s", event.event_type, exc_info=True
            )

    def _handle(self, event: RuntimeEvent) -> None:
        et = event.event_type
        payload = event.payload
        run_id = event.run_id
        if et is RuntimeEventType.RUN_STARTED:
            self._start_trace(event)
        elif et is RuntimeEventType.LLM_STARTED:
            self._models[run_id] = payload.get("model")
        elif et is RuntimeEventType.LLM_COMPLETED:
            self._llm_done(run_id, payload, error=None)
        elif et is RuntimeEventType.LLM_FAILED:
            self._llm_done(run_id, payload, error=payload.get("error") or "llm call failed")
        elif et is RuntimeEventType.TOOL_CALL_COMPLETED:
            self._tool_done(run_id, payload, error=None)
        elif et is RuntimeEventType.TOOL_CALL_FAILED:
            self._tool_done(run_id, payload, error=payload.get("error") or "tool call failed")
        elif et in (RuntimeEventType.RUN_COMPLETED, RuntimeEventType.RUN_FAILED):
            self._end_trace(run_id, payload, failed=et is RuntimeEventType.RUN_FAILED)

    def _start_trace(self, event: RuntimeEvent) -> None:
        run_id = event.run_id
        trace = self._client.start_observation(
            trace_context={"trace_id": UUID(run_id).hex},  # OTEL trace ids: 32 hex chars
            name="runtime-run",
            metadata={"run_id": run_id, **event.payload},
        )
        self._traces[run_id] = trace

    def _llm_done(self, run_id: str, payload: dict[str, Any], *, error: str | None) -> None:
        trace = self._traces.get(run_id)
        if trace is None:
            return
        model = self._models.pop(run_id, None)
        generation = trace.start_observation(
            name="llm-call",
            as_type="generation",
            model=model,
            metadata=payload,
            usage_details=None
            if error
            else {
                "input": int(payload.get("input_tokens", 0) or 0),
                "output": int(payload.get("output_tokens", 0) or 0),
            },
            level="ERROR" if error else None,
            status_message=error,
        )
        generation.end()

    def _tool_done(self, run_id: str, payload: dict[str, Any], *, error: str | None) -> None:
        trace = self._traces.get(run_id)
        if trace is None:
            return
        tool = trace.start_observation(
            name=f"tool:{payload.get('tool', 'unknown')}",
            as_type="tool",
            metadata=payload,
            level="ERROR" if error else None,
            status_message=error,
        )
        tool.end()

    def _end_trace(self, run_id: str, payload: dict[str, Any], *, failed: bool) -> None:
        self._models.pop(run_id, None)
        trace = self._traces.pop(run_id, None)
        if trace is None:
            return
        if failed:
            trace.update(
                level="ERROR",
                status_message=str(payload.get("error") or "run failed"),
                metadata=payload,
            )
        else:
            trace.update(output=payload.get("output"), metadata=payload)
        trace.end()
        self._flush()

    def _flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 - flush is best effort
            logger.warning("langfuse flush failed", exc_info=True)


def attach_langfuse_subscriber(
    event_bus: EventBus,
    settings: Settings | None = None,
    client: Any | None = None,
) -> LangfuseEventSubscriber:
    """Wire a subscriber onto the bus when Langfuse is configured.

    Returns the subscriber either way; when disabled it stays
    unsubscribed and inert.
    """
    return LangfuseEventSubscriber(event_bus, settings=settings, client=client)
