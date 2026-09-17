"""EvolutionStore contract + in-memory implementation.

Mirrors EvaluationStore (evaluation/storage.py): services depend on the
protocol only; durable persistence lives in the infrastructure layer
(agent_platform.infrastructure.evolution_sqlalchemy_store).
"""

from typing import Protocol, runtime_checkable

from agent_platform.evolution.domain.run import (
    DeploymentRecord,
    EvolutionDecision,
    EvolutionEvent,
    EvolutionRun,
    EvolutionVersion,
    Experiment,
)
from agent_platform.evolution.domain.candidate import EvolutionCandidate
from agent_platform.evolution.domain.task import EvolutionTask


@runtime_checkable
class EvolutionStore(Protocol):
    """Persistence contract for evolution aggregates."""

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: EvolutionTask) -> None: ...
    def get_task(self, task_id: str) -> EvolutionTask | None: ...
    def list_tasks(self) -> list[EvolutionTask]: ...

    # -- runs ------------------------------------------------------------------
    def save_run(self, run: EvolutionRun) -> None: ...
    def get_run(self, run_id: str) -> EvolutionRun | None: ...
    def list_runs(self, task_id: str | None = None) -> list[EvolutionRun]: ...

    # -- candidates --------------------------------------------------------------
    def save_candidate(self, candidate: EvolutionCandidate) -> None: ...
    def get_candidate(self, candidate_id: str) -> EvolutionCandidate | None: ...
    def list_candidates_for_run(self, evolution_run_id: str) -> list[EvolutionCandidate]: ...

    # -- experiments ---------------------------------------------------------------
    def save_experiment(self, experiment: Experiment) -> None: ...
    def list_experiments_for_run(self, evolution_run_id: str) -> list[Experiment]: ...

    # -- decisions -----------------------------------------------------------------
    def save_decision(self, decision: EvolutionDecision) -> None: ...

    # -- versions ---------------------------------------------------------------
    def save_version(self, version: EvolutionVersion) -> None: ...
    def get_version(self, version_id: str) -> EvolutionVersion | None: ...
    def list_versions_for_target(
        self, target_type: str, resource_id: str
    ) -> list[EvolutionVersion]: ...

    # -- deployments ------------------------------------------------------------
    def record_deployment(self, record: DeploymentRecord) -> None: ...
    def list_deployments_for_target(
        self, target_type: str, resource_id: str
    ) -> list[DeploymentRecord]: ...

    # -- lifecycle events ----------------------------------------------------------
    def append_event(self, event: EvolutionEvent) -> None: ...
    def list_events_for_run(self, evolution_run_id: str) -> list[EvolutionEvent]: ...


class InMemoryEvolutionStore:
    """Dict-backed EvolutionStore (tests / local development)."""

    def __init__(self) -> None:
        self.tasks: dict[str, EvolutionTask] = {}
        self.runs: dict[str, EvolutionRun] = {}
        self.candidates: dict[str, EvolutionCandidate] = {}
        self.candidates_by_run: dict[str, list[str]] = {}
        self.experiments_by_run: dict[str, list[Experiment]] = {}
        self.decisions_by_run: dict[str, list[EvolutionDecision]] = {}
        self.versions: dict[str, EvolutionVersion] = {}
        self.versions_by_target: dict[tuple[str, str], list[str]] = {}
        self.deployments_by_target: dict[tuple[str, str], list[DeploymentRecord]] = {}
        self.events_by_run: dict[str, list[EvolutionEvent]] = {}

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: EvolutionTask) -> None:
        self.tasks[task.id] = task

    def get_task(self, task_id: str) -> EvolutionTask | None:
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[EvolutionTask]:
        return list(self.tasks.values())

    # -- runs ------------------------------------------------------------------
    def save_run(self, run: EvolutionRun) -> None:
        self.runs[run.id] = run

    def get_run(self, run_id: str) -> EvolutionRun | None:
        return self.runs.get(run_id)

    def list_runs(self, task_id: str | None = None) -> list[EvolutionRun]:
        runs = self.runs.values()
        if task_id is not None:
            runs = [r for r in runs if r.task_id == task_id]
        return list(runs)

    # -- candidates --------------------------------------------------------------
    def save_candidate(self, candidate: EvolutionCandidate) -> None:
        if candidate.id not in self.candidates:
            self.candidates_by_run.setdefault(candidate.evolution_run_id, []).append(
                candidate.id
            )
        self.candidates[candidate.id] = candidate

    def get_candidate(self, candidate_id: str) -> EvolutionCandidate | None:
        return self.candidates.get(candidate_id)

    def list_candidates_for_run(self, evolution_run_id: str) -> list[EvolutionCandidate]:
        return [
            self.candidates[cid]
            for cid in self.candidates_by_run.get(evolution_run_id, [])
        ]

    # -- experiments ---------------------------------------------------------------
    def save_experiment(self, experiment: Experiment) -> None:
        bucket = self.experiments_by_run.setdefault(experiment.evolution_run_id, [])
        for i, existing in enumerate(bucket):
            if existing.id == experiment.id:
                bucket[i] = experiment
                return
        bucket.append(experiment)

    def list_experiments_for_run(self, evolution_run_id: str) -> list[Experiment]:
        return list(self.experiments_by_run.get(evolution_run_id, []))

    # -- decisions -----------------------------------------------------------------
    def save_decision(self, decision: EvolutionDecision) -> None:
        self.decisions_by_run.setdefault(decision.evolution_run_id, []).append(decision)

    # -- versions ---------------------------------------------------------------
    def save_version(self, version: EvolutionVersion) -> None:
        key = (version.target_type, version.resource_id)
        if version.id not in self.versions:
            self.versions_by_target.setdefault(key, []).append(version.id)
        self.versions[version.id] = version

    def get_version(self, version_id: str) -> EvolutionVersion | None:
        return self.versions.get(version_id)

    def list_versions_for_target(
        self, target_type: str, resource_id: str
    ) -> list[EvolutionVersion]:
        return [
            self.versions[vid]
            for vid in self.versions_by_target.get((target_type, resource_id), [])
        ]

    # -- deployments ------------------------------------------------------------
    def record_deployment(self, record: DeploymentRecord) -> None:
        key = (record.target_type, record.resource_id)
        self.deployments_by_target.setdefault(key, []).append(record)

    def list_deployments_for_target(
        self, target_type: str, resource_id: str
    ) -> list[DeploymentRecord]:
        return list(self.deployments_by_target.get((target_type, resource_id), []))

    # -- lifecycle events ----------------------------------------------------------
    def append_event(self, event: EvolutionEvent) -> None:
        self.events_by_run.setdefault(event.evolution_run_id, []).append(event)

    def list_events_for_run(self, evolution_run_id: str) -> list[EvolutionEvent]:
        return list(self.events_by_run.get(evolution_run_id, []))
