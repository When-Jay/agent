"""Celery wiring for runtime dispatch (runtime-dispatch-spec.md).

The API only enqueues a task named by `TASK_NAME`; it never imports
execution modules. Eager mode (tests / local dev) executes in-process
via `Task.apply`, which does not require a broker.
"""

from celery import Celery

TASK_NAME = "agent_platform.runtime.dispatch.execute_run"
RESUME_TASK_NAME = "agent_platform.runtime.dispatch.resume_run"
_TASKS_MODULE = "agent_platform.runtime.dispatch.tasks"


def create_celery_app(broker_url: str, *, eager: bool = False) -> Celery:
    app = Celery("agent_platform", broker=broker_url, include=[_TASKS_MODULE])
    app.conf.update(
        task_always_eager=eager,
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
    )
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


def enqueue_resume(app: Celery, run_id: str) -> None:
    """Dispatch one Run for worker-side resume (same contract as enqueue_run)."""
    if app.conf.task_always_eager:
        from agent_platform.runtime.dispatch import tasks

        tasks.resume_run.apply(args=[run_id])
        return
    app.send_task(RESUME_TASK_NAME, args=[run_id])
