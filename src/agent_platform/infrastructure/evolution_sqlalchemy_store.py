"""SQLAlchemy-backed EvolutionStore.

Mirrors evaluation_sqlalchemy_store.py conventions: JSON payload columns
keep aggregate shapes schemaless; queryable columns exist for filtering;
in-memory SQLite URLs share a per-URL singleton so the API and tests see
one store (dispatch.orchestrator.create_runtime_store pattern).
"""

from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, insert, select

from agent_platform.evolution.domain.candidate import EvolutionCandidate
from agent_platform.evolution.domain.run import (
    DeploymentRecord,
    EvolutionDecision,
    EvolutionEvent,
    EvolutionRun,
    EvolutionVersion,
    Experiment,
)
from agent_platform.evolution.domain.task import EvolutionTask
from agent_platform.evolution.serialization import dump, load
from agent_platform.infrastructure.sqlalchemy_store import create_sqlalchemy_engine, ensure_schema

metadata = MetaData()

_tasks = Table(
    "evolution_tasks", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_runs = Table(
    "evolution_runs", metadata,
    Column("id", String(64), primary_key=True),
    Column("task_id", String(64), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_candidates = Table(
    "evolution_candidates", metadata,
    Column("id", String(64), primary_key=True),
    Column("evolution_run_id", String(64), nullable=False, index=True),
    Column("task_id", String(64), nullable=False),
    Column("validation_status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_experiments = Table(
    "evolution_experiments", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("evolution_run_id", String(64), nullable=False, index=True),
    Column("candidate_id", String(64), nullable=False, index=True),
    Column("purpose", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_decisions = Table(
    "evolution_decisions", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("evolution_run_id", String(64), nullable=False, index=True),
    Column("decision", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_versions = Table(
    "evolution_versions", metadata,
    Column("id", String(64), primary_key=True),
    Column("target_type", String(32), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("version", String(32), nullable=False),
    Column("deployment_status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_deployments = Table(
    "evolution_deployments", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("version_id", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_events = Table(
    "evolution_events", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("evolution_run_id", String(64), nullable=False, index=True),
    Column("event_type", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)


def _upsert(conn, table, pk: str, values: dict[str, Any]) -> None:
    existing = conn.execute(select(table.c[pk]).where(table.c[pk] == values[pk])).first()
    if existing:
        conn.execute(table.update().where(table.c[pk] == values[pk]).values(**values))
    else:
        conn.execute(insert(table).values(**values))


def _payload(table, columns: tuple[str, ...], row: dict[str, Any], cls: type) -> Any:
    data = dict(row["payload"] or {})
    # Queryable columns win over payload copies (they are the write path).
    for column in columns:
        if column in row and row[column] is not None:
            data[column] = row[column]
    return load(cls, data)


class SQLEvolutionStore:
    """EvolutionStore implementation over SQLAlchemy engines."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_sqlalchemy_engine(database_url)
        ensure_schema(self.engine, metadata)

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: EvolutionTask) -> None:
        values = {
            "id": task.id, "name": task.name, "status": task.status.value,
            "created_at": task.created_at, "payload": dump(task),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _tasks, "id", values)

    def get_task(self, task_id: str) -> EvolutionTask | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_tasks).where(_tasks.c.id == task_id)).mappings().first()
        return _payload(_tasks, ("status",), row, EvolutionTask) if row else None

    def list_tasks(self) -> list[EvolutionTask]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_tasks).order_by(_tasks.c.created_at)).mappings().all()
        return [_payload(_tasks, ("status",), row, EvolutionTask) for row in rows]

    # -- runs ------------------------------------------------------------------
    def save_run(self, run: EvolutionRun) -> None:
        values = {
            "id": run.id, "task_id": run.task_id, "status": run.status.value,
            "created_at": run.created_at, "payload": dump(run),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _runs, "id", values)

    def get_run(self, run_id: str) -> EvolutionRun | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_runs).where(_runs.c.id == run_id)).mappings().first()
        return _payload(_runs, ("status",), row, EvolutionRun) if row else None

    def list_runs(self, task_id: str | None = None) -> list[EvolutionRun]:
        conditions = [_runs.c.task_id == task_id] if task_id else []
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_runs).where(*conditions).order_by(_runs.c.created_at)
            ).mappings().all()
        return [_payload(_runs, ("status",), row, EvolutionRun) for row in rows]

    # -- candidates --------------------------------------------------------------
    def save_candidate(self, candidate: EvolutionCandidate) -> None:
        values = {
            "id": candidate.id,
            "evolution_run_id": candidate.evolution_run_id,
            "task_id": candidate.task_id,
            "validation_status": candidate.validation_status.value,
            "created_at": candidate.created_at,
            "payload": dump(candidate),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _candidates, "id", values)

    def get_candidate(self, candidate_id: str) -> EvolutionCandidate | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_candidates).where(_candidates.c.id == candidate_id)
            ).mappings().first()
        return _payload(_candidates, ("validation_status",), row, EvolutionCandidate) if row else None

    def list_candidates_for_run(self, evolution_run_id: str) -> list[EvolutionCandidate]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_candidates)
                .where(_candidates.c.evolution_run_id == evolution_run_id)
                .order_by(_candidates.c.created_at)
            ).mappings().all()
        return [
            _payload(_candidates, ("validation_status",), row, EvolutionCandidate)
            for row in rows
        ]

    # -- experiments ---------------------------------------------------------------
    def save_experiment(self, experiment: Experiment) -> None:
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(_experiments.c.id).where(_experiments.c.id == experiment.id)
            ).first()
            values = {
                "id": experiment.id,
                "evolution_run_id": experiment.evolution_run_id,
                "candidate_id": experiment.candidate_id,
                "purpose": experiment.purpose,
                "status": experiment.status.value,
                "created_at": experiment.created_at,
                "payload": dump(experiment),
            }
            if existing:
                conn.execute(
                    _experiments.update().where(_experiments.c.id == experiment.id).values(**values)
                )
            else:
                conn.execute(insert(_experiments).values(**values))

    def list_experiments_for_run(self, evolution_run_id: str) -> list[Experiment]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_experiments)
                .where(_experiments.c.evolution_run_id == evolution_run_id)
                .order_by(_experiments.c.seq)
            ).mappings().all()
        return [_payload(_experiments, ("status",), row, Experiment) for row in rows]

    # -- decisions -----------------------------------------------------------------
    def save_decision(self, decision: EvolutionDecision) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_decisions).values(
                    id=decision.id,
                    evolution_run_id=decision.evolution_run_id,
                    decision=decision.decision.value,
                    created_at=decision.created_at,
                    payload=dump(decision),
                )
            )

    # -- versions ---------------------------------------------------------------
    def save_version(self, version: EvolutionVersion) -> None:
        values = {
            "id": version.id,
            "target_type": version.target_type,
            "resource_id": version.resource_id,
            "version": version.version,
            "deployment_status": version.deployment_status.value,
            "created_at": version.created_at,
            "payload": dump(version),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _versions, "id", values)

    def get_version(self, version_id: str) -> EvolutionVersion | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_versions).where(_versions.c.id == version_id)).mappings().first()
        return _payload(_versions, ("deployment_status",), row, EvolutionVersion) if row else None

    def list_versions_for_target(
        self, target_type: str, resource_id: str
    ) -> list[EvolutionVersion]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_versions)
                .where(
                    _versions.c.target_type == target_type,
                    _versions.c.resource_id == resource_id,
                )
                .order_by(_versions.c.created_at)
            ).mappings().all()
        return [
            _payload(_versions, ("deployment_status",), row, EvolutionVersion)
            for row in rows
        ]

    # -- deployments ------------------------------------------------------------
    def record_deployment(self, record: DeploymentRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_deployments).values(
                    id=record.id,
                    target_type=record.target_type,
                    resource_id=record.resource_id,
                    version_id=record.version_id,
                    kind=record.kind.value,
                    created_at=record.created_at,
                    payload=dump(record),
                )
            )

    def list_deployments_for_target(
        self, target_type: str, resource_id: str
    ) -> list[DeploymentRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_deployments)
                .where(
                    _deployments.c.target_type == target_type,
                    _deployments.c.resource_id == resource_id,
                )
                .order_by(_deployments.c.seq)
            ).mappings().all()
        return [_payload(_deployments, (), row, DeploymentRecord) for row in rows]

    # -- lifecycle events ----------------------------------------------------------
    def append_event(self, event: EvolutionEvent) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_events).values(
                    id=event.id,
                    evolution_run_id=event.evolution_run_id,
                    event_type=event.event_type,
                    created_at=event.created_at,
                    payload=dump(event),
                )
            )

    def list_events_for_run(self, evolution_run_id: str) -> list[EvolutionEvent]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_events)
                .where(_events.c.evolution_run_id == evolution_run_id)
                .order_by(_events.c.seq)
            ).mappings().all()
        return [_payload(_events, (), row, EvolutionEvent) for row in rows]


# In-memory SQLite singletons (evaluation_sqlalchemy_store pattern): the API
# and in-process tests must observe one shared store per memory URL.
_memory_stores: dict[str, Any] = {}


def create_evolution_store(database_url: str):
    """Build an EvolutionStore for a database URL.

    `sqlite:///:memory:` keeps one shared store per URL so the API and
    in-process tests observe the same state; durable URLs get their own
    engine against the shared database.
    """
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        from agent_platform.evolution.storage import InMemoryEvolutionStore

        store = _memory_stores.get(database_url)
        if store is None:
            store = InMemoryEvolutionStore()
            _memory_stores[database_url] = store
        return store
    return SQLEvolutionStore(database_url)
