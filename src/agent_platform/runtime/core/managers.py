from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.runtime.core.interfaces import RuntimeStore
from agent_platform.runtime.core.models import Application, Run, RunStatus, Session, State


class RunManager:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def create_run(
        self,
        *,
        application_id: str,
        session_id: str,
        runtime_type: str,
        input: dict[str, Any] | None = None,
        status: RunStatus = RunStatus.CREATED,
    ) -> Run:
        run = Run(
            id=str(uuid4()),
            application_id=application_id,
            session_id=session_id,
            runtime_type=runtime_type,
            status=status,
            input=input or {},
        )
        self._store.save_run(run)
        return run

    def start_run(self, run_id: str) -> Run:
        run = self._get_run(run_id)
        if run.status not in {RunStatus.CREATED, RunStatus.QUEUED}:
            raise InvalidStateTransitionError(f"cannot start run from {run.status.value}")
        return self._save(replace(run, status=RunStatus.RUNNING, started_at=_now()))

    def complete_run(self, run_id: str, *, output: dict[str, Any] | None = None) -> Run:
        run = self._get_run(run_id)
        if run.status is not RunStatus.RUNNING:
            raise InvalidStateTransitionError(f"cannot complete run from {run.status.value}")
        return self._save(
            replace(run, status=RunStatus.COMPLETED, output=output or {}, completed_at=_now())
        )

    def fail_run(self, run_id: str, *, error: str) -> Run:
        run = self._get_run(run_id)
        # queued is failable: workers may reject a run before starting it
        # (unknown runtime_type, dispatch unavailable, ...).
        if run.status not in {RunStatus.QUEUED, RunStatus.CREATED, RunStatus.RUNNING}:
            raise InvalidStateTransitionError(f"cannot fail run from {run.status.value}")
        return self._save(replace(run, status=RunStatus.FAILED, error=error, completed_at=_now()))

    def cancel_run(self, run_id: str) -> Run:
        run = self._get_run(run_id)
        if run.status not in {
            RunStatus.QUEUED,
            RunStatus.CREATED,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_HUMAN,
        }:
            raise InvalidStateTransitionError(f"cannot cancel run from {run.status.value}")
        return self._save(replace(run, status=RunStatus.CANCELLED, completed_at=_now()))

    def pause_run(self, run_id: str) -> Run:
        """HITL transition: suspend a running run on a Human node."""
        run = self._get_run(run_id)
        if run.status is not RunStatus.RUNNING:
            raise InvalidStateTransitionError(f"cannot pause run from {run.status.value}")
        return self._save(replace(run, status=RunStatus.WAITING_FOR_HUMAN))

    def restart_run(self, run_id: str) -> Run:
        """Recovery transition: move a failed or waiting run back to RUNNING."""
        run = self._get_run(run_id)
        if run.status not in {RunStatus.FAILED, RunStatus.WAITING_FOR_HUMAN}:
            raise InvalidStateTransitionError(f"cannot restart run from {run.status.value}")
        return self._save(replace(run, status=RunStatus.RUNNING, error=None))

    def requeue_run(self, run_id: str) -> Run:
        """Retry transition: move a failed/cancelled run back to QUEUED for re-dispatch."""
        run = self._get_run(run_id)
        if run.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise InvalidStateTransitionError(f"cannot requeue run from {run.status.value}")
        return self._save(
            replace(run, status=RunStatus.QUEUED, error=None, completed_at=None)
        )

    def get_run(self, run_id: str) -> Run:
        return self._get_run(run_id)

    def _get_run(self, run_id: str) -> Run:
        run = self._store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"run not found: {run_id}")
        return run

    def _save(self, run: Run) -> Run:
        self._store.save_run(run)
        return run


class SessionManager:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def create_application(self, *, name: str, metadata: dict[str, Any] | None = None) -> Application:
        application = Application(id=str(uuid4()), name=name, metadata=metadata or {})
        self._store.save_application(application)
        return application

    def get_application(self, application_id: str) -> Application:
        application = self._store.get_application(application_id)
        if application is None:
            raise NotFoundError(f"application not found: {application_id}")
        return application

    def update_application(
        self, application_id: str, *, metadata: dict[str, Any]
    ) -> Application:
        """Replace an application's metadata (control-plane configuration)."""
        application = self.get_application(application_id)
        updated = replace(application, metadata=metadata)
        self._store.save_application(updated)
        return updated

    def create_session(
        self, *, application_id: str, metadata: dict[str, Any] | None = None
    ) -> Session:
        if self._store.get_application(application_id) is None:
            raise NotFoundError(f"application not found: {application_id}")
        session = Session(id=str(uuid4()), application_id=application_id, metadata=metadata or {})
        self._store.save_session(session)
        return session

    def get_session(self, session_id: str) -> Session:
        session = self._store.get_session(session_id)
        if session is None:
            raise NotFoundError(f"session not found: {session_id}")
        return session


class StateManager:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def get_state(self, run_id: str) -> State:
        self._ensure_run(run_id)
        state = self._store.get_state(run_id)
        return state if state is not None else State(run_id=run_id)

    def update_state(self, run_id: str, *, values: dict[str, Any]) -> State:
        self._ensure_run(run_id)
        current = self._store.get_state(run_id)
        merged = {**(current.values if current else {}), **values}
        state = State(run_id=run_id, values=merged)
        self._store.save_state(state)
        return state

    def _ensure_run(self, run_id: str) -> None:
        if self._store.get_run(run_id) is None:
            raise NotFoundError(f"run not found: {run_id}")


def _now() -> datetime:
    return datetime.now(timezone.utc)
