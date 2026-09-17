"""Runtime metrics: event-driven counters over the EventBus.

V1 metrics (04-observability-architecture.md section 3) are derived
entirely from RuntimeEvents: the collector subscribes to the bus the
runtime publishes on and aggregates counters and run durations in
memory. It never participates in execution decisions and never breaks
them (handler failures are swallowed and logged).

A process-wide singleton survives worker composition: worker tasks
build a fresh orchestrator (and EventBus) per execution, so the
collector is re-attached per build while its counters accumulate.
Multi-process deployments aggregate per process; exporting to a shared
metrics backend (Prometheus/OTel) is a future adapter.
"""

import logging
import threading
import weakref
from typing import Any

from agent_platform.runtime.core.events import EventBus, RuntimeEvent, RuntimeEventType

logger = logging.getLogger(__name__)

_TURNAROUND_EVENTS = (RuntimeEventType.RUN_COMPLETED, RuntimeEventType.RUN_FAILED, RuntimeEventType.RUN_CANCELLED)


class MetricsCollector:
    """Aggregates RuntimeEvents into in-memory metric snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._tokens: dict[str, int] = {"input": 0, "output": 0}
        self._run_started_at: dict[str, float] = {}
        self._durations: list[float] = []

    # -- bus wiring ----------------------------------------------------------

    def attach(self, event_bus: EventBus) -> None:
        event_bus.subscribe(self._on_event)

    def reset(self) -> None:
        """Clear all accumulated state (tests, admin resets)."""
        with self._lock:
            self._counters.clear()
            self._tokens = {"input": 0, "output": 0}
            self._run_started_at.clear()
            self._durations.clear()

    # -- collection ------------------------------------------------------------

    def _on_event(self, event: RuntimeEvent) -> None:
        try:
            with self._lock:
                self._handle(event)
        except Exception:  # noqa: BLE001 - metrics must never break execution
            logger.warning("metrics collection failed for %s", event.event_type, exc_info=True)

    def _handle(self, event: RuntimeEvent) -> None:
        et = event.event_type
        self._counters[et.value] = self._counters.get(et.value, 0) + 1
        if et is RuntimeEventType.RUN_STARTED:
            self._run_started_at[event.run_id] = event.created_at.timestamp()
        elif et in _TURNAROUND_EVENTS:
            started = self._run_started_at.pop(event.run_id, None)
            if started is not None:
                self._durations.append(max(0.0, event.created_at.timestamp() - started))
        elif et is RuntimeEventType.LLM_COMPLETED:
            payload = event.payload
            self._tokens["input"] += int(payload.get("input_tokens") or 0)
            self._tokens["output"] += int(payload.get("output_tokens") or 0)

    # -- export -------------------------------------------------------------------

    def prometheus(self) -> str:
        """Render the snapshot in Prometheus text exposition format (v0.0.4).

        Hand-rolled so the platform carries no client dependency: all
        event counters become one labeled series, run turnaround a
        count/max pair, LLM tokens a typed counter. Process-local per
        the module docstring; aggregation happens in the scraper.
        """
        snap = self.snapshot()
        lines: list[str] = []

        def family(name: str, help_text: str, samples: list[tuple[dict[str, str], Any]]) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            for labels, value in samples:
                label_str = ""
                if labels:
                    joined = ",".join(f'{key}="{val}"' for key, val in labels.items())
                    label_str = "{" + joined + "}"
                lines.append(f"{name}{label_str} {value}")

        family(
            "agent_platform_events_total",
            "RuntimeEvents observed, by event type.",
            [
                ({"event": event_type}, count)
                for event_type, count in sorted(snap["counters"].items())
            ],
        )

        duration = snap["runs"]["duration_seconds"]
        family(
            "agent_platform_run_duration_seconds_count",
            "Terminal runs observed with a measured turnaround.",
            [({}, duration["count"])],
        )
        if duration["max"] is not None:
            family(
                "agent_platform_run_duration_seconds_max",
                "Longest observed run turnaround in seconds.",
                [({}, duration["max"])],
            )

        tokens = snap["llm"]
        family(
            "agent_platform_llm_tokens_total",
            "LLM tokens observed, by direction.",
            [
                ({"type": "input"}, tokens["input_tokens"]),
                ({"type": "output"}, tokens["output_tokens"]),
            ],
        )

        return "\n".join(lines) + "\n"

    # -- snapshot ----------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """JSON-serializable aggregation of everything collected so far."""
        with self._lock:
            counters = dict(self._counters)
            durations = sorted(self._durations)
            tokens = dict(self._tokens)
        return {
            "counters": counters,
            "runs": {
                "started": counters.get(RuntimeEventType.RUN_STARTED.value, 0),
                "completed": counters.get(RuntimeEventType.RUN_COMPLETED.value, 0),
                "failed": counters.get(RuntimeEventType.RUN_FAILED.value, 0),
                "cancelled": counters.get(RuntimeEventType.RUN_CANCELLED.value, 0),
                "paused": counters.get(RuntimeEventType.RUN_PAUSED.value, 0),
                "resumed": counters.get(RuntimeEventType.RUN_RESUMED.value, 0),
                "duration_seconds": _summary(durations),
            },
            "llm": {
                "started": counters.get(RuntimeEventType.LLM_STARTED.value, 0),
                "completed": counters.get(RuntimeEventType.LLM_COMPLETED.value, 0),
                "failed": counters.get(RuntimeEventType.LLM_FAILED.value, 0),
                "input_tokens": tokens["input"],
                "output_tokens": tokens["output"],
            },
            "tools": {
                "started": counters.get(RuntimeEventType.TOOL_CALL_STARTED.value, 0),
                "completed": counters.get(RuntimeEventType.TOOL_CALL_COMPLETED.value, 0),
                "failed": counters.get(RuntimeEventType.TOOL_CALL_FAILED.value, 0),
            },
            "workflow_nodes": {
                "started": counters.get(RuntimeEventType.NODE_STARTED.value, 0),
                "completed": counters.get(RuntimeEventType.NODE_COMPLETED.value, 0),
                "failed": counters.get(RuntimeEventType.NODE_FAILED.value, 0),
            },
            "checkpoints_created": counters.get(RuntimeEventType.CHECKPOINT_CREATED.value, 0),
            "human": {
                "approvals_required": counters.get(RuntimeEventType.APPROVAL_REQUIRED.value, 0),
                "responses_received": counters.get(
                    RuntimeEventType.HUMAN_RESPONSE_RECEIVED.value, 0
                ),
            },
        }


def _summary(durations: list[float]) -> dict[str, int | float | None]:
    if not durations:
        return {"count": 0, "avg": None, "max": None}
    return {
        "count": len(durations),
        "avg": round(sum(durations) / len(durations), 6),
        "max": round(durations[-1], 6),
    }


_collector: MetricsCollector | None = None
_attached_buses: weakref.WeakSet[EventBus] = weakref.WeakSet()
_singleton_lock = threading.Lock()


def get_metrics_collector() -> MetricsCollector:
    """Return the process-wide collector (created on first use)."""
    global _collector
    with _singleton_lock:
        if _collector is None:
            _collector = MetricsCollector()
        return _collector


def attach_metrics_collector(event_bus: EventBus) -> MetricsCollector:
    """Wire the process-wide collector onto a bus (idempotent per bus).

    Idempotence is keyed on live bus identity: a new EventBus instance is
    always subscribed, while re-attaching to an already-wired bus is a
    no-op (double counting would otherwise corrupt counters).
    """
    collector = get_metrics_collector()
    with _singleton_lock:
        if event_bus not in _attached_buses:
            _attached_buses.add(event_bus)
            collector.attach(event_bus)
    return collector
