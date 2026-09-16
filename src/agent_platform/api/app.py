"""FastAPI application: Run control plane (runtime-dispatch-spec.md).

The API creates and inspects Runs; execution is delegated to dispatch
workers. Request handlers import Runtime Core and dispatch wiring only
-- never Agent/Workflow execution modules.
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent_platform.config import Settings
from agent_platform.errors import NotFoundError
from agent_platform.logging import configure_logging
from agent_platform.runtime.core import (
    Application,
    EventBus,
    Run,
    RuntimeEvent,
    RuntimeEventType,
    RunManager,
    RunStatus,
    Session,
    SessionManager,
)
from agent_platform.runtime.dispatch.celery_app import create_celery_app, enqueue_resume, enqueue_run
from agent_platform.runtime.dispatch.orchestrator import create_runtime_store

_VALID_RUNTIME_TYPES = {"agent", "workflow"}
# RUNNING cancellation is cooperative (HITL/interrupt) and deferred; terminal
# runs are immutable. Only pre-execution statuses are cancellable via the API.
_CANCELLABLE_STATUSES = {RunStatus.CREATED, RunStatus.QUEUED}
# Retry re-dispatches a run from scratch; resume continues it from a checkpoint.
_RETRYABLE_STATUSES = {RunStatus.FAILED, RunStatus.CANCELLED}


class CreateRunRequest(BaseModel):
    application_id: str
    runtime_type: str = "agent"
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings.log_level)
    app = FastAPI(title=resolved_settings.app_name, version=resolved_settings.version)

    store = create_runtime_store(resolved_settings.database_url)
    runs = RunManager(store)
    sessions = SessionManager(store)
    events = EventBus(store)
    celery = create_celery_app(
        resolved_settings.redis_url, eager=resolved_settings.celery_task_always_eager
    )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "service": resolved_settings.app_name,
            "status": "ok",
            "version": resolved_settings.version,
        }

    @app.post("/api/v1/runs", status_code=201)
    def create_run(request: CreateRunRequest) -> dict[str, Any]:
        if request.runtime_type not in _VALID_RUNTIME_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"runtime_type must be one of {sorted(_VALID_RUNTIME_TYPES)}",
            )
        try:
            sessions.get_application(request.application_id)
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {request.application_id}"
            ) from None
        if request.session_id is not None:
            try:
                sessions.get_session(request.session_id)
            except NotFoundError:
                raise HTTPException(
                    status_code=404, detail=f"session not found: {request.session_id}"
                ) from None
            session_id = request.session_id
        else:
            session_id = sessions.create_session(application_id=request.application_id).id
        run = runs.create_run(
            application_id=request.application_id,
            session_id=session_id,
            runtime_type=request.runtime_type,
            input=request.input,
            status=RunStatus.QUEUED,
        )
        enqueue_run(celery, run.id)
        return _run_payload(runs.get_run(run.id))

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            run = runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        return _run_payload(run)

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            run = runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        if run.status not in _CANCELLABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} cannot be cancelled from status {run.status.value}",
            )
        cancelled = runs.cancel_run(run_id)
        events.publish(
            run_id=run_id, event_type=RuntimeEventType.RUN_CANCELLED, payload={"reason": "api"}
        )
        return _run_payload(cancelled)

    @app.post("/api/v1/runs/{run_id}/retry")
    def retry_run(run_id: str) -> dict[str, Any]:
        try:
            run = runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        if run.status not in _RETRYABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} cannot be retried from status {run.status.value}",
            )
        runs.requeue_run(run_id)
        enqueue_run(celery, run_id)
        return _run_payload(runs.get_run(run_id))

    @app.post("/api/v1/runs/{run_id}/resume")
    def resume_run(run_id: str) -> dict[str, Any]:
        try:
            run = runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        if run.status is not RunStatus.FAILED:
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} cannot be resumed from status {run.status.value}",
            )
        if run.runtime_type != "workflow":
            # V1 boundary: agent resume needs durable LangGraph checkpoint
            # payloads (deepagents-runtime-spec.md section 8).
            raise HTTPException(
                status_code=409,
                detail=(
                    f"resume is not supported for runtime_type {run.runtime_type}; "
                    "agent resume requires durable checkpoints"
                ),
            )
        enqueue_resume(celery, run_id)
        return _run_payload(runs.get_run(run_id))

    @app.get("/api/v1/runs")
    def list_runs(
        application_id: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        if application_id is not None:
            try:
                sessions.get_application(application_id)
            except NotFoundError:
                raise HTTPException(
                    status_code=404, detail=f"application not found: {application_id}"
                ) from None
        if session_id is not None:
            try:
                sessions.get_session(session_id)
            except NotFoundError:
                raise HTTPException(
                    status_code=404, detail=f"session not found: {session_id}"
                ) from None
        found = store.list_runs(application_id=application_id, session_id=session_id)
        return {"runs": [_run_payload(run) for run in found]}

    @app.get("/api/v1/applications")
    def list_applications() -> dict[str, Any]:
        return {
            "applications": [
                _application_payload(application) for application in store.list_applications()
            ]
        }

    @app.get("/api/v1/applications/{application_id}")
    def get_application(application_id: str) -> dict[str, Any]:
        try:
            application = sessions.get_application(application_id)
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {application_id}"
            ) from None
        return _application_payload(application)

    @app.get("/api/v1/applications/{application_id}/sessions")
    def list_application_sessions(application_id: str) -> dict[str, Any]:
        try:
            sessions.get_application(application_id)
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {application_id}"
            ) from None
        found = store.list_sessions(application_id=application_id)
        return {"sessions": [_session_payload(session) for session in found]}

    @app.get("/api/v1/runs/{run_id}/events")
    def list_run_events(run_id: str) -> dict[str, Any]:
        try:
            runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        return {"events": [_event_payload(event) for event in events.list_events(run_id)]}

    return app


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "application_id": run.application_id,
        "session_id": run.session_id,
        "runtime_type": run.runtime_type,
        "status": run.status.value,
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _application_payload(application: Application) -> dict[str, Any]:
    return {
        "id": application.id,
        "name": application.name,
        "metadata": application.metadata,
    }


def _session_payload(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "application_id": session.application_id,
        "metadata": session.metadata,
    }


def _event_payload(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "event_type": event.event_type.value,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
