"""Celery tasks for runtime dispatch (runtime-dispatch-spec.md section 4).

The module-level `celery_app` is the worker application; task bodies
build a fresh orchestrator per execution so each run sees current
configuration. `set_orchestrator_builder` lets tests and alternative
deployments override composition without touching the task contract.
"""

import logging
from collections.abc import Callable
from typing import Any

from agent_platform.config import Settings
from agent_platform.runtime.dispatch.celery_app import (
    EVALUATION_TASK_NAME,
    EVOLUTION_TASK_NAME,
    RESUME_TASK_NAME,
    TASK_NAME,
    create_celery_app,
)

logger = logging.getLogger(__name__)

_settings = Settings()
celery_app = create_celery_app(_settings.redis_url, eager=_settings.celery_task_always_eager)

_orchestrator_builder: Callable[[], object] | None = None


def set_orchestrator_builder(builder: Callable[[], object]) -> None:
    """Override worker composition (tests / custom deployments)."""
    global _orchestrator_builder
    _orchestrator_builder = builder


_evaluation_runner_builder: Callable[[], object] | None = None
_evaluation_score_exporter_builder: Callable[[], object | None] | None = None


def set_evaluation_runner_builder(builder: Callable[[], object]) -> None:
    """Override evaluation worker composition (tests / custom deployments)."""
    global _evaluation_runner_builder
    _evaluation_runner_builder = builder


def set_evaluation_score_exporter_builder(
    builder: Callable[[], object | None],
) -> None:
    """Override the Langfuse Score exporter composition (tests)."""
    global _evaluation_score_exporter_builder
    _evaluation_score_exporter_builder = builder


def _default_builder():
    from agent_platform.observability.langfuse_adapter import attach_langfuse_subscriber
    from agent_platform.observability.metrics import attach_metrics_collector
    from agent_platform.runtime.dispatch.orchestrator import build_default_orchestrator
    from agent_platform.runtime.dispatch.redis_stream import RedisEventPublisher, RedisStreamBackend

    orchestrator = build_default_orchestrator(_settings)
    # Optional, non-blocking trace export (dispatch-spec section 7).
    attach_langfuse_subscriber(orchestrator.events, _settings)
    # In-memory runtime metrics (04-observability-architecture.md section 3).
    attach_metrics_collector(orchestrator.events)
    # Optional, non-blocking live fanout (dispatch-spec section 6).
    if _settings.redis_event_fanout_enabled:
        orchestrator.events.subscribe(RedisEventPublisher(RedisStreamBackend(_settings.redis_url)))
    return orchestrator


@celery_app.task(name=TASK_NAME)
def execute_run(run_id: str) -> str:
    builder = _orchestrator_builder or _default_builder
    orchestrator = builder()
    orchestrator.execute(run_id)
    return run_id


@celery_app.task(name=RESUME_TASK_NAME)
def resume_run(run_id: str, response: Any = None) -> str:
    builder = _orchestrator_builder or _default_builder
    orchestrator = builder()
    orchestrator.resume(run_id, response=response)
    return run_id


def _default_evaluation_builder():
    """Compose the worker-side EvaluationService (evaluation-spec section 32).

    Mirrors the API composition root: trials dispatch Runtime Runs through
    the standard enqueue_run path (broker: nested publish; eager: inline).
    """
    from agent_platform.evaluation.application import EvaluationService
    from agent_platform.evaluation.evaluators import EvaluatorRegistry, RuleEvaluator
    from agent_platform.evaluation.harness import TrialRunner
    from agent_platform.infrastructure.evaluation_sqlalchemy_store import (
        create_evaluation_store,
    )
    from agent_platform.runtime.dispatch.celery_app import enqueue_run
    from agent_platform.runtime.dispatch.orchestrator import create_runtime_store

    runtime_store = create_runtime_store(_settings.database_url)
    evaluation_store = create_evaluation_store(_settings.database_url)
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())
    return EvaluationService(
        runtime_store,
        evaluation_store,
        TrialRunner(
            runtime_store,
            evaluation_store,
            dispatcher=lambda run_id: enqueue_run(celery_app, run_id),
            registry=registry,
        ),
    )


def _default_score_exporter_builder():
    """Langfuse Score exporter when Langfuse is configured (spec section 33)."""
    from agent_platform.infrastructure.evaluation_sqlalchemy_store import (
        create_evaluation_store,
    )
    from agent_platform.observability.evaluation_scores import LangfuseScoreExporter

    exporter = LangfuseScoreExporter(
        create_evaluation_store(_settings.database_url), settings=_settings
    )
    return exporter if exporter.enabled else None


def _export_evaluation_scores(evaluation_run_id: str) -> None:
    """Best-effort Score export after a finished evaluation run.

    Failure-isolated: export problems never fail the evaluation task
    (same non-blocking contract as the Langfuse trace subscriber).
    """
    try:
        builder = _evaluation_score_exporter_builder or _default_score_exporter_builder
        exporter = builder()
        if exporter is not None:
            exporter.export_run(evaluation_run_id)
    except Exception:  # noqa: BLE001 - observability must never break execution
        logger.warning(
            "evaluation score export failed for run %s", evaluation_run_id, exc_info=True
        )


_evolution_runner_builder: Callable[[], object] | None = None


def set_evolution_runner_builder(builder: Callable[[], object]) -> None:
    """Override evolution worker composition (tests / custom deployments)."""
    global _evolution_runner_builder
    _evolution_runner_builder = builder


def _default_evolution_builder():
    """Compose the worker-side EvolutionService (plan 060 section 22).

    Mirrors the API composition root: experiments invoke Evaluation through
    the gateway; candidate generation uses the same optional LLM port as
    the judge evaluator (None -> generation-stage failure with a clear
    error, never a silent empty candidate set).
    """
    from agent_platform.evolution.application import EvolutionService
    from agent_platform.evolution.experiment import (
        EvaluationServiceGateway,
        ExperimentRunner,
    )
    from agent_platform.evolution.optimizers import default_optimizers
    from agent_platform.infrastructure.evolution_sqlalchemy_store import (
        create_evolution_store,
    )

    evolution_store = create_evolution_store(_settings.database_url)
    optimizers = {o.name: o for o in default_optimizers(_evolution_model_fn())}
    runner = ExperimentRunner(
        EvaluationServiceGateway(_default_evaluation_builder()), evolution_store
    )
    return EvolutionService(evolution_store, runner, optimizers=optimizers)


def _evolution_model_fn():
    """Optional generation model port for candidate optimizers.

    Deployments wire an LLM here (e.g. via set_evolution_runner_builder in
    tests); without one, generation fails explicitly at the GENERATING
    stage instead of producing silent no-op candidates.
    """
    return None


@celery_app.task(name=EVALUATION_TASK_NAME)
def execute_evaluation_run(evaluation_run_id: str) -> str:
    builder = _evaluation_runner_builder or _default_evaluation_builder
    service = builder()
    service.run_evaluation_run(evaluation_run_id)
    _export_evaluation_scores(evaluation_run_id)
    return evaluation_run_id


@celery_app.task(name=EVOLUTION_TASK_NAME)
def execute_evolution_run(evolution_run_id: str) -> str:
    builder = _evolution_runner_builder or _default_evolution_builder
    service = builder()
    service.execute_run(evolution_run_id)
    return evolution_run_id
