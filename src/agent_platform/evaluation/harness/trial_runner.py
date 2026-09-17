"""TrialRunner: executes one Task attempt against the Runtime (plan 050 section 5).

Harness 的核心目标是控制变量（架构文档 section 12）：Trial 通过标准 dispatch
路径执行 —— 创建 QUEUED Run 并交给注入的 dispatcher（eager celery / broker），
从不直接调用 Agent/Workflow 执行代码。Trial.run_id 即 Runtime Run id，同时
作为 Langfuse trace 关联 id（observability 适配器以 UUID(run_id).hex 为
trace id，deepagents-runtime-spec.md section 10）。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import replace

from agent_platform.evaluation.domain import (
    EvaluationRun,
    ResultValue,
    Task,
    Trial,
    TrialStatus,
    new_id,
)
from agent_platform.evaluation.evaluators import (
    EvaluatorRegistry,
    EvaluationContext,
    rule_result_value,
)
from agent_platform.runtime.core.managers import RunManager, SessionManager
from agent_platform.runtime.core.models import RunStatus
from agent_platform.runtime.core.stores import RuntimeStore

logger = logging.getLogger(__name__)

Dispatcher = Callable[[str], None]

_TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


class TrialRunner:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        evaluation_store,
        *,
        dispatcher: Dispatcher,
        registry: EvaluatorRegistry,
        timeout_s: float = 300.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._runtime_store = runtime_store
        self._evaluation_store = evaluation_store
        self._runs = RunManager(runtime_store)
        self._sessions = SessionManager(runtime_store)
        self._dispatcher = dispatcher
        self._registry = registry
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s

    def execute_trial(
        self,
        evaluation_run: EvaluationRun,
        task: Task,
        attempt: int,
        *,
        rubrics: list = (),
    ) -> Trial:
        """Run one attempt: dispatch -> await terminal -> evaluate -> verdict."""
        run_input = dict(task.input)
        if task.user_context:
            run_input["user_context"] = task.user_context
        session = self._sessions.create_session(
            application_id=evaluation_run.application_id,
            metadata={"evaluation_run_id": evaluation_run.id, "purpose": "evaluation"},
        )
        run = self._runs.create_run(
            application_id=evaluation_run.application_id,
            session_id=session.id,
            runtime_type=evaluation_run.runtime_type,
            input=run_input,
            status=RunStatus.QUEUED,
        )
        trial = Trial(
            id=new_id(),
            task_id=task.id,
            evaluation_run_id=evaluation_run.id,
            attempt=attempt,
            agent_version=evaluation_run.agent_version,
            environment_version=evaluation_run.environment_version,
            status=TrialStatus.RUNNING,
            run_id=run.id,
            trace_id=run.id,
        )
        self._evaluation_store.save_trial(trial)

        try:
            self._dispatcher(run.id)
            run = self._await_terminal(run.id)
        except Exception as exc:  # noqa: BLE001 - harness errors are first-class
            logger.exception("trial dispatch failed for run %s", run.id)
            return self._finish(trial, TrialStatus.ERROR, outcome={
                "task_completed": False,
                "error": f"harness dispatch failed: {type(exc).__name__}: {exc}",
            })

        if run is None:
            return self._finish(trial, TrialStatus.TIMEOUT, outcome={
                "task_completed": False,
                "error": f"run did not reach a terminal state within {self._timeout_s}s",
            })

        outcome = self._collect_outcome(run.id)
        if run.status is RunStatus.CANCELLED:
            return self._finish(trial, TrialStatus.CANCELLED, outcome)

        # Evaluate regardless of run status: a failed run is still evidence
        # (process assertions diagnose why it failed, spec section 14).
        context = EvaluationContext(
            task=task,
            trial=trial,
            outcome=outcome,
            output=run.output,
            events=self._runtime_store.list_events(run.id),
            artifacts=self._list_artifacts(run.id),
            rubrics=list(rubrics),
        )
        results = self._registry.evaluate(evaluation_run.evaluator_ids, context)
        for result in results:
            self._evaluation_store.save_result(result)

        if run.status is RunStatus.FAILED:
            return self._finish(trial, TrialStatus.FAILED, outcome)

        verdict = rule_result_value(results)
        status = TrialStatus.PASSED if verdict is ResultValue.PASS else TrialStatus.FAILED
        return self._finish(trial, status, outcome)

    # --- internals ------------------------------------------------------------

    def _await_terminal(self, run_id: str):
        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            run = self._runs.get_run(run_id)
            if run.status in _TERMINAL_RUN_STATUSES:
                return run
            time.sleep(self._poll_interval_s)
        return None

    def _collect_outcome(self, run_id: str) -> dict:
        run = self._runs.get_run(run_id)
        return {
            "task_completed": run.status is RunStatus.COMPLETED,
            "status": run.status.value,
            "output": run.output,
            "error": run.error,
            "artifacts": self._list_artifacts(run_id),
            # business_result / environment_state / user_feedback: reserved
            # outcome surfaces (spec section 12), collected in later phases.
        }

    def _list_artifacts(self, run_id: str) -> list[dict]:
        return [
            {"name": artifact.name, "uri": artifact.uri, "metadata": artifact.metadata}
            for artifact in self._runtime_store.list_artifacts_for_run(run_id)
        ]

    def _finish(self, trial: Trial, status: TrialStatus, outcome: dict) -> Trial:
        from agent_platform.evaluation.domain import utcnow

        finished = replace(trial, status=status, outcome=outcome, finished_at=utcnow())
        self._evaluation_store.save_trial(finished)
        return finished
