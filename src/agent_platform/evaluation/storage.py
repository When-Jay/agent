"""EvaluationStore contract + in-memory implementation.

Mirrors RuntimeStore (runtime/core/interfaces.py): evaluation services
depend on this protocol only; durable persistence lives in the
infrastructure layer (agent_platform.infrastructure.evaluation_sqlalchemy_store).
"""

from typing import Protocol, runtime_checkable

from agent_platform.evaluation.domain import (
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


@runtime_checkable
class EvaluationStore(Protocol):
    """Persistence contract for evaluation aggregates."""

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: Task) -> None: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def list_tasks(self) -> list[Task]: ...

    # -- rubrics ---------------------------------------------------------------
    def save_rubric(self, rubric: Rubric) -> None: ...
    def get_rubric(self, rubric_id: str) -> Rubric | None: ...
    def list_rubrics(self) -> list[Rubric]: ...

    # -- suites ----------------------------------------------------------------
    def save_suite(self, suite: EvaluationSuite) -> None: ...
    def get_suite(self, suite_id: str) -> EvaluationSuite | None: ...
    def list_suites(self) -> list[EvaluationSuite]: ...

    # -- assets ----------------------------------------------------------------
    def save_asset(self, asset: EvaluationAsset) -> None: ...
    def get_asset(self, asset_id: str) -> EvaluationAsset | None: ...
    def list_assets(self, asset_type: str | None = None) -> list[EvaluationAsset]: ...

    # -- environments ------------------------------------------------------------
    def save_environment(self, environment: EvaluationEnvironment) -> None: ...
    def get_environment(self, environment_id: str) -> EvaluationEnvironment | None: ...

    # -- evaluation runs ---------------------------------------------------------
    def save_evaluation_run(self, run: EvaluationRun) -> None: ...
    def get_evaluation_run(self, run_id: str) -> EvaluationRun | None: ...
    def list_evaluation_runs(self, suite_id: str | None = None) -> list[EvaluationRun]: ...

    # -- trials ----------------------------------------------------------------
    def save_trial(self, trial: Trial) -> None: ...
    def get_trial(self, trial_id: str) -> Trial | None: ...
    def list_trials_for_run(self, evaluation_run_id: str) -> list[Trial]: ...

    # -- results ---------------------------------------------------------------
    def save_result(self, result: EvaluationResult) -> None: ...
    def list_results_for_run(self, evaluation_run_id: str) -> list[EvaluationResult]: ...

    # -- gates -----------------------------------------------------------------
    def save_gate(self, gate: QualityGate) -> None: ...
    def get_gate(self, gate_id: str) -> QualityGate | None: ...
    def list_gates(self) -> list[QualityGate]: ...


class InMemoryEvaluationStore:
    """Dict-backed EvaluationStore (tests / local development)."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.rubrics: dict[str, Rubric] = {}
        self.suites: dict[str, EvaluationSuite] = {}
        self.assets: dict[str, EvaluationAsset] = {}
        self.environments: dict[str, EvaluationEnvironment] = {}
        self.runs: dict[str, EvaluationRun] = {}
        self.trials: dict[str, Trial] = {}
        self.trials_by_run: dict[str, list[str]] = {}
        self.results: dict[str, EvaluationResult] = {}
        self.results_by_run: dict[str, list[str]] = {}
        self.gates: dict[str, QualityGate] = {}

    # -- tasks -----------------------------------------------------------------
    def save_task(self, task: Task) -> None:
        self.tasks[task.id] = task

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        return list(self.tasks.values())

    # -- rubrics ---------------------------------------------------------------
    def save_rubric(self, rubric: Rubric) -> None:
        self.rubrics[rubric.id] = rubric

    def get_rubric(self, rubric_id: str) -> Rubric | None:
        return self.rubrics.get(rubric_id)

    def list_rubrics(self) -> list[Rubric]:
        return list(self.rubrics.values())

    # -- suites ----------------------------------------------------------------
    def save_suite(self, suite: EvaluationSuite) -> None:
        self.suites[suite.id] = suite

    def get_suite(self, suite_id: str) -> EvaluationSuite | None:
        return self.suites.get(suite_id)

    def list_suites(self) -> list[EvaluationSuite]:
        return list(self.suites.values())

    # -- assets ----------------------------------------------------------------
    def save_asset(self, asset: EvaluationAsset) -> None:
        self.assets[asset.id] = asset

    def get_asset(self, asset_id: str) -> EvaluationAsset | None:
        return self.assets.get(asset_id)

    def list_assets(self, asset_type: str | None = None) -> list[EvaluationAsset]:
        assets = self.assets.values()
        if asset_type is not None:
            assets = [a for a in assets if a.type == asset_type]
        return list(assets)

    # -- environments ------------------------------------------------------------
    def save_environment(self, environment: EvaluationEnvironment) -> None:
        self.environments[environment.id] = environment

    def get_environment(self, environment_id: str) -> EvaluationEnvironment | None:
        return self.environments.get(environment_id)

    # -- evaluation runs ---------------------------------------------------------
    def save_evaluation_run(self, run: EvaluationRun) -> None:
        self.runs[run.id] = run

    def get_evaluation_run(self, run_id: str) -> EvaluationRun | None:
        return self.runs.get(run_id)

    def list_evaluation_runs(self, suite_id: str | None = None) -> list[EvaluationRun]:
        runs = self.runs.values()
        if suite_id is not None:
            runs = [r for r in runs if r.suite_id == suite_id]
        return list(runs)

    # -- trials ----------------------------------------------------------------
    def save_trial(self, trial: Trial) -> None:
        if trial.id not in self.trials:
            self.trials_by_run.setdefault(trial.evaluation_run_id, []).append(trial.id)
        self.trials[trial.id] = trial

    def get_trial(self, trial_id: str) -> Trial | None:
        return self.trials.get(trial_id)

    def list_trials_for_run(self, evaluation_run_id: str) -> list[Trial]:
        return [self.trials[t] for t in self.trials_by_run.get(evaluation_run_id, [])]

    # -- results ---------------------------------------------------------------
    def save_result(self, result: EvaluationResult) -> None:
        if result.id not in self.results:
            self.results_by_run.setdefault(result.run_id, []).append(result.id)
        self.results[result.id] = result

    def list_results_for_run(self, evaluation_run_id: str) -> list[EvaluationResult]:
        return [
            self.results[r] for r in self.results_by_run.get(evaluation_run_id, [])
        ]

    # -- gates -----------------------------------------------------------------
    def save_gate(self, gate: QualityGate) -> None:
        self.gates[gate.id] = gate

    def get_gate(self, gate_id: str) -> QualityGate | None:
        return self.gates.get(gate_id)

    def list_gates(self) -> list[QualityGate]:
        return list(self.gates.values())
