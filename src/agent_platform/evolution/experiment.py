"""实验引擎与 Evaluation 集成（plan 060 sections 8-9 / spec sections 10-12）。

边界（evaluation-spec / evolution-spec section 11）：Evolution 不实现
Rubric/Evaluator/数据集执行/门禁逻辑，只通过稳定接口调用 Evaluation 平台。
"""

import logging
from dataclasses import replace
from typing import Protocol

from agent_platform.evolution.domain.run import Experiment, ExperimentStatus
from agent_platform.evolution.domain.task import EvolutionTask, new_id
from agent_platform.errors import NotFoundError

logger = logging.getLogger(__name__)

# 资产用途（spec section 12）：VALIDATION 必需，其余可选。
_PURPOSE_ORDER = ("validation", "regression", "challenge")


class EvaluationGateway(Protocol):
    """Evolution 消费 Evaluation 的稳定接口（spec section 11）。"""

    def run_suite(
        self,
        *,
        suite_id: str,
        application_id: str,
        runtime_type: str,
        agent_version: str,
    ) -> object:
        """创建并同步执行一次评测 Run，返回带 summary 的 EvaluationRun。"""
        ...

    def check_gate(self, *, gate_id: str, run_id: str) -> object: ...


class EvaluationServiceGateway:
    """基于 EvaluationService 的网关适配器（组合根注入）。"""

    def __init__(self, evaluation_service) -> None:
        self._service = evaluation_service

    def run_suite(self, *, suite_id, application_id, runtime_type, agent_version):
        run = self._service.create_evaluation_run(
            suite_id=suite_id,
            application_id=application_id,
            runtime_type=runtime_type,
            agent_version=agent_version,
        )
        return self._service.run_evaluation_run(run.id)

    def check_gate(self, *, gate_id, run_id):
        return self._service.check_gate(gate_id, run_id)


class ExperimentRunner:
    """按资产用途执行候选实验，产出 Experiment 记录（含评测 summary）。"""

    def __init__(self, gateway: EvaluationGateway, store) -> None:
        self._gateway = gateway
        self._store = store

    def run_candidate(
        self,
        evolution_run,
        task: EvolutionTask,
        candidate,
    ) -> dict[str, Experiment]:
        """执行一个候选的全部已配置用途实验；返回 {purpose: Experiment}。"""
        assets = task.evaluation_assets or {}
        suites: dict = assets.get("suites") or {}
        application_id = assets.get("application_id") or ""
        runtime_type = assets.get("runtime_type") or "agent"
        agent_version = f"candidate:{candidate.id}"
        experiments: dict[str, Experiment] = {}
        for purpose in _PURPOSE_ORDER:
            suite_id = suites.get(purpose)
            if not suite_id:
                continue
            experiment = self._run_purpose(
                evolution_run, candidate, purpose, suite_id,
                application_id, runtime_type, agent_version,
            )
            experiments[purpose.upper()] = experiment
        return experiments

    def _run_purpose(
        self, evolution_run, candidate, purpose, suite_id,
        application_id, runtime_type, agent_version,
    ) -> Experiment:
        experiment = Experiment(
            id=new_id(),
            evolution_run_id=evolution_run.id,
            candidate_id=candidate.id,
            purpose=purpose.upper(),
            agent_version=agent_version,
            status=ExperimentStatus.RUNNING,
        )
        try:
            evaluation_run = self._gateway.run_suite(
                suite_id=suite_id,
                application_id=application_id,
                runtime_type=runtime_type,
                agent_version=agent_version,
            )
        except NotFoundError:
            raise  # 资产配置错误是任务级失败，由编排层终止整个 Run
        except Exception as exc:  # noqa: BLE001 - 实验失败是一等结果
            logger.warning(
                "experiment failed for candidate %s (%s): %s",
                candidate.id, purpose, exc,
            )
            experiment = replace(
                experiment,
                status=ExperimentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._store.save_experiment(experiment)
            return experiment
        experiment = replace(
            experiment,
            evaluation_run_id=evaluation_run.id,
            environment_id=evaluation_run.environment_id,
            status=ExperimentStatus.COMPLETED,
            summary=dict(evaluation_run.summary or {}),
        )
        self._store.save_experiment(experiment)
        return experiment

    def check_gate(self, *, gate_id: str, evaluation_run_id: str) -> dict:
        """门禁只调用不实现（spec section 15）：结果转换为可序列化 dict。"""
        decision = self._gateway.check_gate(gate_id=gate_id, run_id=evaluation_run_id)
        return _normalize_gate_result(decision)


def _normalize_gate_result(decision) -> dict:
    """网关返回值是 Protocol 级 object，统一为 {action, ...} dict。"""
    from agent_platform.evaluation.serialization import dump

    if isinstance(decision, dict):
        return decision
    dumped = dump(decision)  # evaluation 领域 dataclass -> JSON-safe dict
    if isinstance(dumped, dict):
        return dumped
    namespace = getattr(decision, "__dict__", None)
    if isinstance(namespace, dict):
        return {key: dump(value) for key, value in namespace.items()}
    return {"action": str(dumped)}
