"""Runtime Dispatch: separates API request handling from runtime execution.

The API creates queued Runs and enqueues `TASK_NAME`; workers execute
them through the Agent/Workflow runtimes (runtime-dispatch-spec.md).
"""

from agent_platform.runtime.dispatch.celery_app import TASK_NAME, create_celery_app, enqueue_run
from agent_platform.runtime.dispatch.orchestrator import (
    RuntimeOrchestrator,
    build_agent_adapter,
    build_default_orchestrator,
    create_runtime_store,
)

__all__ = [
    "TASK_NAME",
    "RuntimeOrchestrator",
    "build_agent_adapter",
    "build_default_orchestrator",
    "create_celery_app",
    "create_runtime_store",
    "enqueue_run",
]
