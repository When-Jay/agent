"""Celery tasks for runtime dispatch (runtime-dispatch-spec.md section 4).

The module-level `celery_app` is the worker application; task bodies
build a fresh orchestrator per execution so each run sees current
configuration. `set_orchestrator_builder` lets tests and alternative
deployments override composition without touching the task contract.
"""

import logging
from collections.abc import Callable

from agent_platform.config import Settings
from agent_platform.runtime.dispatch.celery_app import RESUME_TASK_NAME, TASK_NAME, create_celery_app

logger = logging.getLogger(__name__)

_settings = Settings()
celery_app = create_celery_app(_settings.redis_url, eager=_settings.celery_task_always_eager)

_orchestrator_builder: Callable[[], object] | None = None


def set_orchestrator_builder(builder: Callable[[], object]) -> None:
    """Override worker composition (tests / custom deployments)."""
    global _orchestrator_builder
    _orchestrator_builder = builder


def _default_builder():
    from agent_platform.observability.langfuse_adapter import attach_langfuse_subscriber
    from agent_platform.runtime.dispatch.orchestrator import build_default_orchestrator
    from agent_platform.runtime.dispatch.redis_stream import RedisEventPublisher, RedisStreamBackend

    orchestrator = build_default_orchestrator(_settings)
    # Optional, non-blocking trace export (dispatch-spec section 7).
    attach_langfuse_subscriber(orchestrator.events, _settings)
    # Optional, non-blocking live fanout (dispatch-spec section 6).
    if _settings.redis_event_fanout_enabled:
        orchestrator.events.subscribe(RedisEventPublisher(RedisStreamBackend(_settings.redis_url)))
    return orchestrator


@celery_app.task(name=TASK_NAME)
def execute_run(run_id: str) -> str:
    builder = _orchestrator_builder or _default_builder
    orchestrator = builder()
    orchestrator.execute(run_id)
    return run_id


@celery_app.task(name=RESUME_TASK_NAME)
def resume_run(run_id: str) -> str:
    builder = _orchestrator_builder or _default_builder
    orchestrator = builder()
    orchestrator.resume(run_id)
    return run_id
