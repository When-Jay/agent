"""SQLAlchemy-backed RuntimeStore implementing the core persistence contract.

Durable adapter for PostgreSQL / SQLite (runtime-dispatch-spec.md:
PostgreSQL is the durable source of truth for Runs and events). JSON
columns keep payloads schemaless; datetime columns are timezone-aware.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from agent_platform.runtime.core.events import RuntimeEvent, RuntimeEventType
from agent_platform.runtime.core.models import (
    Application,
    Artifact,
    Checkpoint,
    Run,
    Session,
    State,
)

metadata = MetaData()

_applications = Table(
    "applications", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
)
_sessions = Table(
    "sessions", metadata,
    Column("id", String(64), primary_key=True),
    Column("application_id", String(64), nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
)
_runs = Table(
    "runs", metadata,
    Column("id", String(64), primary_key=True),
    Column("application_id", String(64), nullable=False),
    Column("session_id", String(64), nullable=False),
    Column("runtime_type", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("input", JSON, nullable=False, default=dict),
    Column("output", JSON, nullable=False, default=dict),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)
_states = Table(
    "states", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("values", JSON, nullable=False, default=dict),
)
_events = Table(
    "events", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False, index=True),
    Column("event_type", String(64), nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
_checkpoints = Table(
    "checkpoints", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False, index=True),
    Column("state", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
_artifacts = Table(
    "artifacts", metadata,
    Column("id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("uri", Text, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_sqlalchemy_engine(database_url: str) -> Engine:
    if _is_sqlite(database_url) and ":memory:" in database_url:
        # A single shared connection keeps the in-memory schema alive.
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(database_url)


def _iso(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class SQLAlchemyRuntimeStore:
    """RuntimeStore implementation over SQLAlchemy engines."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_sqlalchemy_engine(database_url)
        metadata.create_all(self.engine)

    # -- applications / sessions ---------------------------------------------

    def save_application(self, application: Application) -> None:
        values = {
            "id": application.id,
            "name": application.name,
            "metadata": application.metadata,  # noqa: A003 - column name
        }
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(_applications.c.id).where(_applications.c.id == application.id)
            ).first()
            if existing:
                conn.execute(
                    _applications.update().where(_applications.c.id == application.id).values(**values)
                )
            else:
                conn.execute(insert(_applications).values(**values))

    def get_application(self, application_id: str) -> Application | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_applications).where(_applications.c.id == application_id)
            ).mappings().first()
        return (
            Application(id=row["id"], name=row["name"], metadata=row["metadata"])
            if row
            else None
        )

    def list_applications(self) -> list[Application]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_applications)).mappings().all()
        return [
            Application(id=row["id"], name=row["name"], metadata=row["metadata"] or {})
            for row in rows
        ]

    def save_session(self, session: Session) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_sessions).values(
                    id=session.id,
                    application_id=session.application_id,
                    metadata=session.metadata,
                )
            )

    def get_session(self, session_id: str) -> Session | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_sessions).where(_sessions.c.id == session_id)).mappings().first()
        return (
            Session(id=row["id"], application_id=row["application_id"], metadata=row["metadata"])
            if row
            else None
        )

    def list_sessions(self, application_id: str | None = None) -> list[Session]:
        conditions = []
        if application_id is not None:
            conditions.append(_sessions.c.application_id == application_id)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_sessions).where(*conditions)
            ).mappings().all()
        return [
            Session(
                id=row["id"], application_id=row["application_id"], metadata=row["metadata"] or {}
            )
            for row in rows
        ]

    # -- runs ------------------------------------------------------------------

    def save_run(self, run: Run) -> None:
        values = {
            "id": run.id,
            "application_id": run.application_id,
            "session_id": run.session_id,
            "runtime_type": run.runtime_type,
            "status": run.status.value,
            "input": run.input,
            "output": run.output,
            "error": run.error,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
        }
        with self.engine.begin() as conn:
            existing = conn.execute(select(_runs.c.id).where(_runs.c.id == run.id)).first()
            if existing:
                conn.execute(_runs.update().where(_runs.c.id == run.id).values(**values))
            else:
                conn.execute(insert(_runs).values(**values))

    def get_run(self, run_id: str) -> Run | None:
        from agent_platform.runtime.core.models import RunStatus

        with self.engine.connect() as conn:
            row = conn.execute(select(_runs).where(_runs.c.id == run_id)).mappings().first()
        if not row:
            return None
        return Run(
            id=row["id"],
            application_id=row["application_id"],
            session_id=row["session_id"],
            runtime_type=row["runtime_type"],
            status=RunStatus(row["status"]),
            input=row["input"] or {},
            output=row["output"] or {},
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def list_runs(
        self, application_id: str | None = None, session_id: str | None = None
    ) -> list[Run]:
        from agent_platform.runtime.core.models import RunStatus

        conditions = []
        if application_id is not None:
            conditions.append(_runs.c.application_id == application_id)
        if session_id is not None:
            conditions.append(_runs.c.session_id == session_id)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_runs).where(*conditions).order_by(_runs.c.created_at)
            ).mappings().all()
        return [
            Run(
                id=row["id"],
                application_id=row["application_id"],
                session_id=row["session_id"],
                runtime_type=row["runtime_type"],
                status=RunStatus(row["status"]),
                input=row["input"] or {},
                output=row["output"] or {},
                error=row["error"],
                created_at=row["created_at"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
            )
            for row in rows
        ]

    # -- state -----------------------------------------------------------------

    def get_state(self, run_id: str) -> State | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_states).where(_states.c.run_id == run_id)).mappings().first()
        return State(run_id=row["run_id"], values=row["values"] or {}) if row else None

    def save_state(self, state: State) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(_states.c.run_id).where(_states.c.run_id == state.run_id)
            ).first()
            if existing:
                conn.execute(
                    _states.update().where(_states.c.run_id == state.run_id).values(values=state.values)
                )
            else:
                conn.execute(
                    insert(_states).values(run_id=state.run_id, values=state.values)
                )

    # -- events ----------------------------------------------------------------

    def save_event(self, event: RuntimeEvent) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_events).values(
                    id=event.id,
                    run_id=event.run_id,
                    event_type=event.event_type.value,
                    payload=event.payload,
                    created_at=_iso(event.created_at),
                )
            )

    def list_events(self, run_id: str) -> list[RuntimeEvent]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_events).where(_events.c.run_id == run_id).order_by(_events.c.seq)
            ).mappings().all()
        return [
            RuntimeEvent(
                id=row["id"],
                run_id=row["run_id"],
                event_type=RuntimeEventType(row["event_type"]),
                payload=row["payload"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # -- checkpoints / artifacts ----------------------------------------------

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_checkpoints).values(
                    id=checkpoint.id,
                    run_id=checkpoint.run_id,
                    state=checkpoint.state,
                    created_at=_iso(checkpoint.created_at),
                )
            )

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_checkpoints).where(_checkpoints.c.id == checkpoint_id)
            ).mappings().first()
        return (
            Checkpoint(
                id=row["id"], run_id=row["run_id"], state=row["state"], created_at=row["created_at"]
            )
            if row
            else None
        )

    def latest_checkpoint_for_run(self, run_id: str) -> Checkpoint | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_checkpoints)
                .where(_checkpoints.c.run_id == run_id)
                .order_by(_checkpoints.c.seq.desc())
                .limit(1)
            ).mappings().first()
        return (
            Checkpoint(
                id=row["id"], run_id=row["run_id"], state=row["state"], created_at=row["created_at"]
            )
            if row
            else None
        )

    def save_artifact(self, artifact: Artifact) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_artifacts).values(
                    id=artifact.id,
                    run_id=artifact.run_id,
                    name=artifact.name,
                    uri=artifact.uri,
                    metadata=artifact.metadata,
                    created_at=_iso(artifact.created_at),
                )
            )

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_artifacts).where(_artifacts.c.id == artifact_id)
            ).mappings().first()
        return (
            Artifact(
                id=row["id"],
                run_id=row["run_id"],
                name=row["name"],
                uri=row["uri"],
                metadata=row["metadata"] or {},
                created_at=row["created_at"],
            )
            if row
            else None
        )

    def list_artifacts_for_run(self, run_id: str) -> list[Artifact]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_artifacts)
                .where(_artifacts.c.run_id == run_id)
                .order_by(_artifacts.c.created_at, _artifacts.c.id)
            ).mappings().all()
        return [
            Artifact(
                id=row["id"],
                run_id=row["run_id"],
                name=row["name"],
                uri=row["uri"],
                metadata=row["metadata"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]
