"""Node execution with events, retry and timeout (framework-neutral)."""

import concurrent.futures
from collections.abc import Callable
from typing import Any

from agent_platform.runtime.workflow.definition import NodeSpec


class WorkflowNodeError(RuntimeError):
    """Raised when a node exhausts its retries."""

    def __init__(self, node_name: str, error: str) -> None:
        super().__init__(f"node {node_name} failed: {error}")
        self.node_name = node_name
        self.error = error


class NodeExecutor:
    """Executes one node: emits events, applies retry and timeout policy."""

    def __init__(self, emit_event: Callable[[str, dict[str, Any]], None]) -> None:
        self._emit_event = emit_event

    def execute(self, node: NodeSpec, state: dict[str, Any]) -> dict[str, Any] | None:
        self._emit_event(
            "NodeStarted",
            {"node": node.name, "attempt": 1, "retries": node.retries},
        )
        attempts = node.retries + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                update = self._invoke(node, state)
                self._emit_event(
                    "NodeCompleted",
                    {"node": node.name, "attempt": attempt},
                )
                return update
            except Exception as exc:  # noqa: BLE001 - failures are policy, not crashes
                last_error = exc
        self._emit_event(
            "NodeFailed",
            {"node": node.name, "error": str(last_error)},
        )
        raise WorkflowNodeError(node.name, str(last_error))

    def _invoke(self, node: NodeSpec, state: dict[str, Any]) -> dict[str, Any] | None:
        if node.timeout_seconds is None:
            return node.handler(state)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(node.handler, state)
            return future.result(timeout=node.timeout_seconds)
