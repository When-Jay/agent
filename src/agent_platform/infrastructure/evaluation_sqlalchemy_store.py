"""SQLAlchemy-backed EvaluationStore.

Durable adapter mirroring sqlalchemy_store.py conventions: JSON payload
columns keep aggregate shapes schemaless; queryable columns exist for
filtering; datetime columns are timezone-aware. In-memory SQLite URLs
share a per-URL singleton so the API and tests see one store, mirroring
dispatch.orchestrator.create_runtime_store.
"""

from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, insert, select

from agent_platform.evaluation.domain import (
    ABAssignment,
    ABTest,
    Case,
    DiagnosisResult,
    EvaluationAsset,
    EvaluationEnvironment,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    QualityGate,
    Rubric,
    Task,
    Trial,
)
from agent_platform.evaluation.serialization import dump, load
from agent_platform.infrastructure.sqlalchemy_store import create_sqlalchemy_engine

metadata = MetaData()

_tasks = Table(
    "evaluation_tasks", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_rubrics = Table(
    "evaluation_rubrics", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_suites = Table(
    "evaluation_suites", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("type", String(32), nullable=False),
    Column("version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_assets = Table(
    "evaluation_assets", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("type", String(32), nullable=False),
    Column("suite_id", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_environments = Table(
    "evaluation_environments", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_runs = Table(
    "evaluation_runs", metadata,
    Column("id", String(64), primary_key=True),
    Column("suite_id", String(64), nullable=False, index=True),
    Column("application_id", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_trials = Table(
    "evaluation_trials", metadata,
    Column("id", String(64), primary_key=True),
    Column("evaluation_run_id", String(64), nullable=False, index=True),
    Column("task_id", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("run_id", String(64), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_results = Table(
    "evaluation_results", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("evaluation_run_id", String(64), nullable=False, index=True),
    Column("trial_id", String(64), nullable=False, index=True),
    Column("task_id", String(64), nullable=False),
    Column("result", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_gates = Table(
    "evaluation_gates", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("version", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_cases = Table(
    "evaluation_cases", metadata,
    Column("id", String(64), primary_key=True),
    Column("source", String(32), nullable=False, index=True),
    Column("type", String(32), nullable=False, index=True),
    Column("status", String(32), nullable=False, index=True),
    Column("task_id", String(64), nullable=True),
    Column("trace_id", String(64), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_diagnoses = Table(
    "evaluation_diagnoses", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(64), nullable=False),
    Column("case_id", String(64), nullable=False, index=True),
    Column("category", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_ab_tests = Table(
    "evaluation_ab_tests", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("application_id", String(64), nullable=False, index=True),
    Column("runtime_type", String(32), nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_ab_assignments = Table(
    "evaluation_ab_assignments", metadata,
    Column("id", String(64), primary_key=True),
    Column("ab_test_id", String(64), nullable=False, index=True),
    Column("run_id", String(64), nullable=False, unique=True),
    Column("session_id", String(64), nullable=False),
    Column("variant_key", String(64), nullable=False),
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


class SQLEvaluationStore:
    """EvaluationStore implementation over SQLAlchemy engines."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_sqlalchemy_engine(database_url)
        metadata.create_all(self.engine)

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: Task) -> None:
        values = {
            "id": task.id, "name": task.name, "version": task.version,
            "created_at": task.created_at, "payload": dump(task),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _tasks, "id", values)

    def get_task(self, task_id: str) -> Task | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_tasks).where(_tasks.c.id == task_id)).mappings().first()
        return _payload(_tasks, (), row, Task) if row else None

    def list_tasks(self) -> list[Task]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_tasks).order_by(_tasks.c.created_at)).mappings().all()
        return [_payload(_tasks, (), row, Task) for row in rows]

    # -- rubrics ---------------------------------------------------------------
    def save_rubric(self, rubric: Rubric) -> None:
        values = {
            "id": rubric.id, "name": rubric.name, "version": rubric.version,
            "created_at": rubric.created_at, "payload": dump(rubric),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _rubrics, "id", values)

    def get_rubric(self, rubric_id: str) -> Rubric | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_rubrics).where(_rubrics.c.id == rubric_id)).mappings().first()
        return _payload(_rubrics, (), row, Rubric) if row else None

    def list_rubrics(self) -> list[Rubric]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_rubrics).order_by(_rubrics.c.created_at)).mappings().all()
        return [_payload(_rubrics, (), row, Rubric) for row in rows]

    # -- suites ----------------------------------------------------------------
    def save_suite(self, suite: EvaluationSuite) -> None:
        values = {
            "id": suite.id, "name": suite.name, "type": suite.type,
            "version": suite.version, "created_at": suite.created_at,
            "payload": dump(suite),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _suites, "id", values)

    def get_suite(self, suite_id: str) -> EvaluationSuite | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_suites).where(_suites.c.id == suite_id)).mappings().first()
        return _payload(_suites, (), row, EvaluationSuite) if row else None

    def list_suites(self) -> list[EvaluationSuite]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_suites).order_by(_suites.c.created_at)).mappings().all()
        return [_payload(_suites, (), row, EvaluationSuite) for row in rows]

    # -- assets ----------------------------------------------------------------
    def save_asset(self, asset: EvaluationAsset) -> None:
        values = {
            "id": asset.id, "name": asset.name, "type": asset.type,
            "suite_id": asset.suite_id, "created_at": asset.created_at,
            "payload": dump(asset),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _assets, "id", values)

    def get_asset(self, asset_id: str) -> EvaluationAsset | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_assets).where(_assets.c.id == asset_id)).mappings().first()
        return _payload(_assets, (), row, EvaluationAsset) if row else None

    def list_assets(self, asset_type: str | None = None) -> list[EvaluationAsset]:
        conditions = [_assets.c.type == asset_type] if asset_type else []
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_assets).where(*conditions).order_by(_assets.c.created_at)
            ).mappings().all()
        return [_payload(_assets, (), row, EvaluationAsset) for row in rows]

    # -- environments ------------------------------------------------------------
    def save_environment(self, environment: EvaluationEnvironment) -> None:
        values = {
            "id": environment.id, "name": environment.name,
            "version": environment.version, "created_at": environment.created_at,
            "payload": dump(environment),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _environments, "id", values)

    def get_environment(self, environment_id: str) -> EvaluationEnvironment | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_environments).where(_environments.c.id == environment_id)
            ).mappings().first()
        return _payload(_environments, (), row, EvaluationEnvironment) if row else None

    # -- evaluation runs ---------------------------------------------------------
    def save_evaluation_run(self, run: EvaluationRun) -> None:
        values = {
            "id": run.id, "suite_id": run.suite_id,
            "application_id": run.application_id, "status": run.status.value,
            "created_at": run.created_at, "payload": dump(run),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _runs, "id", values)

    def get_evaluation_run(self, run_id: str) -> EvaluationRun | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_runs).where(_runs.c.id == run_id)).mappings().first()
        return _payload(_runs, ("status",), row, EvaluationRun) if row else None

    def list_evaluation_runs(self, suite_id: str | None = None) -> list[EvaluationRun]:
        conditions = [_runs.c.suite_id == suite_id] if suite_id else []
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_runs).where(*conditions).order_by(_runs.c.created_at)
            ).mappings().all()
        return [_payload(_runs, ("status",), row, EvaluationRun) for row in rows]

    # -- trials ----------------------------------------------------------------
    def save_trial(self, trial: Trial) -> None:
        values = {
            "id": trial.id, "evaluation_run_id": trial.evaluation_run_id,
            "task_id": trial.task_id, "status": trial.status.value,
            "run_id": trial.run_id or None, "started_at": trial.started_at,
            "payload": dump(trial),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _trials, "id", values)

    def get_trial(self, trial_id: str) -> Trial | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_trials).where(_trials.c.id == trial_id)).mappings().first()
        return _payload(_trials, ("status",), row, Trial) if row else None

    def list_trials_for_run(self, evaluation_run_id: str) -> list[Trial]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_trials).where(_trials.c.evaluation_run_id == evaluation_run_id)
                .order_by(_trials.c.started_at)
            ).mappings().all()
        return [_payload(_trials, ("status",), row, Trial) for row in rows]

    # -- results ---------------------------------------------------------------
    def save_result(self, result: EvaluationResult) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_results).values(
                    id=result.id, evaluation_run_id=result.run_id,
                    trial_id=result.trial_id, task_id=result.task_id,
                    result=result.result.value, created_at=result.created_at,
                    payload=dump(result),
                )
            )

    def list_results_for_run(self, evaluation_run_id: str) -> list[EvaluationResult]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_results).where(_results.c.evaluation_run_id == evaluation_run_id)
                .order_by(_results.c.seq)
            ).mappings().all()
        return [_payload(_results, (), row, EvaluationResult) for row in rows]

    # -- gates -----------------------------------------------------------------
    def save_gate(self, gate: QualityGate) -> None:
        values = {
            "id": gate.id, "name": gate.name, "version": gate.version,
            "created_at": gate.created_at, "payload": dump(gate),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _gates, "id", values)

    def get_gate(self, gate_id: str) -> QualityGate | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_gates).where(_gates.c.id == gate_id)).mappings().first()
        return _payload(_gates, (), row, QualityGate) if row else None

    def list_gates(self) -> list[QualityGate]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_gates).order_by(_gates.c.created_at)).mappings().all()
        return [_payload(_gates, (), row, QualityGate) for row in rows]

    # -- cases -----------------------------------------------------------------
    def save_case(self, case: Case) -> None:
        values = {
            "id": case.id,
            "source": case.source.value,
            "type": case.type.value,
            "status": case.status,
            "task_id": case.task_id,
            "trace_id": case.trace_id,
            "created_at": case.created_at,
            "payload": dump(case),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _cases, "id", values)

    def get_case(self, case_id: str) -> Case | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_cases).where(_cases.c.id == case_id)).mappings().first()
        return _payload(_cases, ("status",), row, Case) if row else None

    def list_cases(
        self,
        *,
        case_type: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> list[Case]:
        conditions = []
        if case_type is not None:
            conditions.append(_cases.c.type == case_type)
        if source is not None:
            conditions.append(_cases.c.source == source)
        if status is not None:
            conditions.append(_cases.c.status == status)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_cases).where(*conditions).order_by(_cases.c.created_at)
            ).mappings().all()
        return [_payload(_cases, ("status",), row, Case) for row in rows]

    def find_case_by_trace(self, source: str, trace_id: str) -> Case | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_cases)
                .where(_cases.c.source == source, _cases.c.trace_id == trace_id)
                .order_by(_cases.c.created_at)
            ).mappings().first()
        return _payload(_cases, ("status",), row, Case) if row else None

    # -- diagnoses ---------------------------------------------------------------
    def save_diagnosis(self, diagnosis: DiagnosisResult) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(_diagnoses).values(
                    id=diagnosis.id,
                    case_id=diagnosis.case_id,
                    category=diagnosis.category.value,
                    created_at=diagnosis.created_at,
                    payload=dump(diagnosis),
                )
            )

    def list_diagnoses_for_case(self, case_id: str) -> list[DiagnosisResult]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_diagnoses)
                .where(_diagnoses.c.case_id == case_id)
                .order_by(_diagnoses.c.seq)
            ).mappings().all()
        return [_payload(_diagnoses, (), row, DiagnosisResult) for row in rows]

    # -- online evaluation: A/B ----------------------------------------------------
    def save_ab_test(self, test: ABTest) -> None:
        values = {
            "id": test.id,
            "name": test.name,
            "application_id": test.application_id,
            "runtime_type": test.runtime_type,
            "status": test.status,
            "created_at": test.created_at,
            "payload": dump(test),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _ab_tests, "id", values)

    def get_ab_test(self, ab_test_id: str) -> ABTest | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_ab_tests).where(_ab_tests.c.id == ab_test_id)
            ).mappings().first()
        return _payload(_ab_tests, ("status",), row, ABTest) if row else None

    def list_ab_tests(
        self,
        *,
        application_id: str | None = None,
        status: str | None = None,
    ) -> list[ABTest]:
        conditions = []
        if application_id is not None:
            conditions.append(_ab_tests.c.application_id == application_id)
        if status is not None:
            conditions.append(_ab_tests.c.status == status)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_ab_tests).where(*conditions).order_by(_ab_tests.c.created_at)
            ).mappings().all()
        return [_payload(_ab_tests, ("status",), row, ABTest) for row in rows]

    def save_assignment(self, assignment: ABAssignment) -> None:
        values = {
            "id": assignment.id,
            "ab_test_id": assignment.ab_test_id,
            "run_id": assignment.run_id,
            "session_id": assignment.session_id,
            "variant_key": assignment.variant_key,
            "created_at": assignment.created_at,
            "payload": dump(assignment),
        }
        with self.engine.begin() as conn:
            _upsert(conn, _ab_assignments, "id", values)

    def get_assignment_for_run(self, run_id: str) -> ABAssignment | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_ab_assignments).where(_ab_assignments.c.run_id == run_id)
            ).mappings().first()
        return _payload(_ab_assignments, (), row, ABAssignment) if row else None

    def list_assignments_for_test(self, ab_test_id: str) -> list[ABAssignment]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_ab_assignments)
                .where(_ab_assignments.c.ab_test_id == ab_test_id)
                .order_by(_ab_assignments.c.created_at)
            ).mappings().all()
        return [_payload(_ab_assignments, (), row, ABAssignment) for row in rows]


# In-memory SQLite singletons (dispatch.orchestrator pattern): the API and
# in-process tests must observe one shared store per memory URL.
_memory_stores: dict[str, Any] = {}


def create_evaluation_store(database_url: str):
    """Build an EvaluationStore for a database URL.

    `sqlite:///:memory:` keeps one shared store per URL so the API and
    in-process tests observe the same state; durable URLs get their own
    engine against the shared database.
    """
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        from agent_platform.evaluation.storage import InMemoryEvaluationStore

        store = _memory_stores.get(database_url)
        if store is None:
            store = InMemoryEvaluationStore()
            _memory_stores[database_url] = store
        return store
    return SQLEvaluationStore(database_url)
