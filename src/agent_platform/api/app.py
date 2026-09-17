"""FastAPI application: Run control plane (runtime-dispatch-spec.md).

The API creates and inspects Runs; execution is delegated to dispatch
workers. Request handlers import Runtime Core and dispatch wiring only
-- never Agent/Workflow execution modules.
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_platform.config import Settings
from agent_platform.errors import NotFoundError
from agent_platform.logging import configure_logging
from agent_platform.observability import get_metrics_collector
from agent_platform.runtime.core import (
    Application,
    Artifact,
    ArtifactStore,
    EventBus,
    Run,
    RuntimeEvent,
    RuntimeEventType,
    RunManager,
    RunStatus,
    Session,
    SessionManager,
    event_from_json,
    event_to_json,
)
from agent_platform.runtime.dispatch.celery_app import create_celery_app, enqueue_resume, enqueue_run
from agent_platform.runtime.dispatch.orchestrator import create_runtime_store
from agent_platform.runtime.dispatch.redis_stream import (
    RedisEventPublisher,
    StreamBackend,
    create_stream_backend,
    is_terminal_event,
)

_VALID_RUNTIME_TYPES = {"agent", "workflow"}
# RUNNING cancellation is cooperative (HITL/interrupt) and deferred; terminal
# runs are immutable. Only pre-execution statuses are cancellable via the API.
_CANCELLABLE_STATUSES = {RunStatus.CREATED, RunStatus.QUEUED}
# Retry re-dispatches a run from scratch; resume continues it from a checkpoint.
_RETRYABLE_STATUSES = {RunStatus.FAILED, RunStatus.CANCELLED}
_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
# Emit an SSE keep-alive comment after this many consecutive empty reads
# (1s blocking read each) so proxies do not close idle streams.
_KEEP_ALIVE_IDLE_READS = 15


class CreateRunRequest(BaseModel):
    application_id: str
    runtime_type: str = "agent"
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class RespondRunRequest(BaseModel):
    response: Any = None


class CreateApplicationRequest(BaseModel):
    name: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowReferenceRequest(BaseModel):
    """Workflow binding stored in application metadata.

    The control plane stores the name@version reference only; graph
    definitions live in the worker-side registry (dispatch spec section 3:
    the API never imports Workflow execution modules).
    """

    name: str
    version: str | None = None


class RegisterArtifactRequest(BaseModel):
    """Client-side artifact registration (reference, not blob upload)."""

    name: str
    uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_app(
    settings: Settings | None = None,
    *,
    stream_backend: StreamBackend | None = None,
    tool_capability=None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings.log_level)
    app = FastAPI(title=resolved_settings.app_name, version=resolved_settings.version)

    store = create_runtime_store(resolved_settings.database_url)
    runs = RunManager(store)
    sessions = SessionManager(store)
    events = EventBus(store)
    artifacts = ArtifactStore(store)
    celery = create_celery_app(
        resolved_settings.redis_url, eager=resolved_settings.celery_task_always_eager
    )
    # Live event fanout (dispatch-spec section 6): in single-process mode the
    # publisher below feeds the SSE endpoint directly; with redis_event_fanout
    # enabled, workers publish to Redis and the API reads the same stream.
    resolved_backend = stream_backend or create_stream_backend(resolved_settings)
    events.subscribe(RedisEventPublisher(resolved_backend))

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "service": resolved_settings.app_name,
            "status": "ok",
            "version": resolved_settings.version,
        }

    @app.get("/api/v1/metrics")
    def get_metrics() -> dict[str, Any]:
        """Runtime metrics aggregated from RuntimeEvents (04-observability-
        architecture.md section 3). Process-local: reflects events published
        on buses this process subscribed the collector to."""
        return get_metrics_collector().snapshot()

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
        enqueue_resume(celery, run_id)
        return _run_payload(runs.get_run(run_id))

    @app.post("/api/v1/runs/{run_id}/respond")
    def respond_run(run_id: str, request: RespondRunRequest) -> dict[str, Any]:
        """Deliver a human response to a run paused on a Human node or an
        agent approval interrupt.

        Agent runs expect a LangChain HITLResponse:
        `{"decisions": [{"type": "approve"} | {"type": "reject", "message": ...}]}`.
        """
        try:
            run = runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        if run.status is not RunStatus.WAITING_FOR_HUMAN:
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} is not waiting for human input (status {run.status.value})",
            )
        enqueue_resume(celery, run_id, response=request.response)
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

    @app.post("/api/v1/applications", status_code=201)
    def create_application(request: CreateApplicationRequest) -> dict[str, Any]:
        if not request.name.strip():
            raise HTTPException(status_code=422, detail="name must not be empty")
        application = sessions.create_application(
            name=request.name, metadata=request.metadata
        )
        return _application_payload(application)

    @app.post(
        "/api/v1/applications/{application_id}/sessions", status_code=201
    )
    def create_application_session(
        application_id: str, request: CreateSessionRequest
    ) -> dict[str, Any]:
        try:
            session = sessions.create_session(
                application_id=application_id, metadata=request.metadata
            )
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {application_id}"
            ) from None
        return _session_payload(session)

    @app.get("/api/v1/applications/{application_id}/workflow")
    def get_application_workflow(application_id: str) -> dict[str, Any]:
        try:
            application = sessions.get_application(application_id)
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {application_id}"
            ) from None
        workflow = (application.metadata or {}).get("workflow")
        if not workflow:
            raise HTTPException(
                status_code=404,
                detail=f"application {application_id} has no workflow binding",
            ) from None
        return {"workflow": dict(workflow)}

    @app.put("/api/v1/applications/{application_id}/workflow")
    def put_application_workflow(
        application_id: str, request: WorkflowReferenceRequest
    ) -> dict[str, Any]:
        """Bind the application to a workflow name@version (section 3 routing).

        Validation is structural only: the graph itself is resolved by the
        worker's registry at dispatch time.
        """
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name must not be empty")
        if request.version is not None and not request.version.strip():
            raise HTTPException(status_code=422, detail="version must not be empty")
        version = request.version.strip() if request.version is not None else None
        try:
            application = sessions.get_application(application_id)
        except NotFoundError:
            raise HTTPException(
                status_code=404, detail=f"application not found: {application_id}"
            ) from None
        metadata = dict(application.metadata or {})
        metadata["workflow"] = {"name": name, "version": version}
        sessions.update_application(application_id, metadata=metadata)
        return {"workflow": metadata["workflow"]}

    @app.get("/api/v1/tools")
    def list_tools() -> dict[str, Any]:
        """Registered tools (runtime-capabilities-spec section Tool).

        Uses the capability injected at composition time; the API layer
        never constructs tool handlers itself.
        """
        specs = tool_capability.list_tools() if tool_capability is not None else []
        return {
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                }
                for spec in specs
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

    @app.get("/api/v1/runs/{run_id}/artifacts")
    def list_run_artifacts(run_id: str) -> dict[str, Any]:
        try:
            runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        return {"artifacts": [_artifact_payload(a) for a in store.list_artifacts_for_run(run_id)]}

    @app.post("/api/v1/runs/{run_id}/artifacts", status_code=201)
    def register_run_artifact(run_id: str, request: RegisterArtifactRequest) -> dict[str, Any]:
        """Register an artifact reference produced outside the platform.

        The URI is stored as-is (s3://, file://, https://, ...); content
        upload/proxying is out of V1 scope (runtime-spec section 10).
        """
        try:
            runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        if not request.name.strip():
            raise HTTPException(status_code=422, detail="name must not be empty")
        if not request.uri.strip():
            raise HTTPException(status_code=422, detail="uri must not be empty")
        artifact = artifacts.create(
            run_id=run_id,
            name=request.name.strip(),
            uri=request.uri.strip(),
            metadata=request.metadata,
        )
        return _artifact_payload(artifact)

    @app.get("/api/v1/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(
                status_code=404, detail=f"artifact not found: {artifact_id}"
            ) from None
        return _artifact_payload(artifact)

    @app.get("/api/v1/runs/{run_id}/events/stream")
    def stream_run_events(run_id: str) -> StreamingResponse:
        """SSE stream of one run's events (02-api-architecture.md section 4).

        Replays the durable PostgreSQL log first, then follows live events
        from the stream backend. Streams for terminal runs end after the
        replay; live streams end once a terminal lifecycle event arrives.
        """
        try:
            runs.get_run(run_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from None
        return StreamingResponse(
            _run_event_stream(run_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def _run_event_stream(run_id: str):
        def frame(event: RuntimeEvent) -> str:
            return (
                f"event: {event.event_type.value}\n"
                f"id: {event.id}\n"
                f"data: {event_to_json(event)}\n\n"
            )

        seen: set[str] = set()
        for event in events.list_events(run_id):
            seen.add(event.id)
            yield frame(event)

        if runs.get_run(run_id).status in _TERMINAL_STATUSES:
            return

        cursor = "0-0"
        idle_reads = 0
        while True:
            entries = await run_in_threadpool(resolved_backend.read, cursor, 1000, 100)
            if not entries:
                idle_reads += 1
                if idle_reads % _KEEP_ALIVE_IDLE_READS == 0:
                    yield ": keep-alive\n\n"
                continue
            idle_reads = 0
            for entry_id, data in entries:
                cursor = entry_id
                event = event_from_json(data)
                if event.run_id != run_id or event.id in seen:
                    continue
                seen.add(event.id)
                yield frame(event)
                if is_terminal_event(data):
                    return

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


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "run_id": artifact.run_id,
        "name": artifact.name,
        "uri": artifact.uri,
        "metadata": artifact.metadata,
        "created_at": artifact.created_at.isoformat(),
    }
