"""Celery wiring for runtime dispatch (runtime-dispatch-spec.md).

The API only enqueues a task named by `TASK_NAME`; it never imports
execution modules. Eager mode (tests / local dev) executes in-process
via `Task.apply`, which does not require a broker.
"""

from typing import Any

from celery import Celery

TASK_NAME = "agent_platform.runtime.dispatch.execute_run"
RESUME_TASK_NAME = "agent_platform.runtime.dispatch.resume_run"
EVALUATION_TASK_NAME = "agent_platform.evaluation.execute_evaluation_run"
EVOLUTION_TASK_NAME = "agent_platform.evolution.execute_evolution_run"
PATROL_TASK_NAME = "agent_platform.evaluation.execute_evaluation_patrol"
_TASKS_MODULE = "agent_platform.runtime.dispatch.tasks"


def create_celery_app(
    broker_url: str,
    *,
    eager: bool = False,
    beat_schedule: dict[str, Any] | None = None,
) -> Celery:
    app = Celery("agent_platform", broker=broker_url, include=[_TASKS_MODULE])
    app.conf.update(
        task_always_eager=eager,
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
        # Queue separation: runtime traffic stays on "default"; evaluation/
        # evolution/patrol work runs on a dedicated queue consumed by a
        # single-concurrency worker (compose service worker-eval).
        task_default_queue="default",
        task_routes={
            EVALUATION_TASK_NAME: {"queue": "evaluation"},
            EVOLUTION_TASK_NAME: {"queue": "evaluation"},
            PATROL_TASK_NAME: {"queue": "evaluation"},
        },
    )
    if beat_schedule:
        app.conf.beat_schedule = beat_schedule
    return app


def enqueue_run(app: Celery, run_id: str) -> None:
    """Dispatch one Run for worker execution.

    * eager: run in-process (tests / local development).
    * broker: publish by name -- no local task registration needed, so
      the API process never imports Agent/Workflow execution code.
    """
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        tasks.execute_run.apply(args=[run_id])
        return
    app.send_task(TASK_NAME, args=[run_id])


def enqueue_resume(app: Celery, run_id: str, response: Any = None) -> None:
    """Dispatch one Run for worker-side resume (same contract as enqueue_run).

    response carries the human response for runs paused on a Human node.
    """
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        tasks.resume_run.apply(args=[run_id, response])
        return
    app.send_task(RESUME_TASK_NAME, args=[run_id, response])


def enqueue_evaluation_run(app: Celery, evaluation_run_id: str) -> None:
    """Dispatch one evaluation run for worker execution (spec section 32).

    The API only enqueues by name; evaluation composition happens on the
    worker (same pattern as enqueue_run, so the API process never imports
    evaluation execution modules).
    """
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        tasks.execute_evaluation_run.apply(args=[evaluation_run_id])
        return
    app.send_task(EVALUATION_TASK_NAME, args=[evaluation_run_id])


def enqueue_evolution_run(app: Celery, evolution_run_id: str) -> None:
    """Dispatch one evolution run for worker execution (plan 060).

    Same enqueue-by-name contract as evaluation: the API process never
    imports evolution/evaluation execution modules; eager mode runs
    in-process for tests and local development.
    """
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        tasks.execute_evolution_run.apply(args=[evolution_run_id])
        return
    app.send_task(EVOLUTION_TASK_NAME, args=[evolution_run_id])


def enqueue_evaluation_patrol(app: Celery) -> str | None:
    """Trigger one regression-patrol pass (manual path; beat schedules the rest).

    eager 模式返回 patrol evaluation run id；broker 模式仅投递，返回 None。
    """
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        return tasks.execute_evaluation_patrol.apply().get()
    app.send_task(PATROL_TASK_NAME)
    return None
