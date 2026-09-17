"""Evaluation API routes (evaluation-spec.md section 32).

V1 路径前缀对齐平台约定：/api/v1/evaluation/...。评测 Run 经注入的
run_dispatcher 派发（eager/dev 模式内联执行完成；broker 模式返回
RUNNING 由 worker 执行）——API 层不感知 celery/broker。
"""

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_platform.evaluation.application import EvaluationService
from agent_platform.errors import NotFoundError


class CreateTaskRequest(BaseModel):
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    user_context: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)
    process_assertions: list[dict[str, Any]] = Field(default_factory=list)
    outcome_assertions: list[dict[str, Any]] = Field(default_factory=list)
    environment_id: str | None = None
    suite_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateRubricRequest(BaseModel):
    name: str
    dimension: str = ""
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    aggregation: dict[str, Any] = Field(default_factory=dict)


class CreateSuiteRequest(BaseModel):
    name: str
    type: str = "E2E"
    task_ids: list[str] = Field(default_factory=list)
    rubric_ids: list[str] = Field(default_factory=list)
    environment_id: str | None = None


class CreateAssetRequest(BaseModel):
    name: str
    type: str = "GOLDEN"
    task_ids: list[str] = Field(default_factory=list)
    suite_id: str | None = None
    source: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateEnvironmentRequest(BaseModel):
    name: str
    version: str = "1"
    config: dict[str, Any] = Field(default_factory=dict)


class StartEvaluationRunRequest(BaseModel):
    suite_id: str
    application_id: str
    runtime_type: str = "agent"
    agent_version: str = "dev"
    environment_id: str | None = None
    trials_per_task: int = Field(default=1, ge=1, le=20)
    evaluator_ids: list[str] = Field(default_factory=lambda: ["rule"])


class CreateGateRequest(BaseModel):
    name: str
    rules: list[dict[str, Any]] = Field(default_factory=list)


class GateCheckRequest(BaseModel):
    gate_id: str
    run_id: str


def create_evaluation_router(
    service: EvaluationService, *, run_dispatcher: Callable[[str], None]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/evaluation", tags=["evaluation"])

    @router.post("/tasks", status_code=201)
    def create_task(request: CreateTaskRequest) -> dict:
        return _dump(service.create_task(name=request.name, payload=request.model_dump()))

    @router.get("/tasks")
    def list_tasks() -> list[dict]:
        return [_dump(t) for t in service.list_tasks()]

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return _dump(service.get_task(task_id))

    @router.post("/rubrics", status_code=201)
    def create_rubric(request: CreateRubricRequest) -> dict:
        return _dump(service.create_rubric(name=request.name, payload=request.model_dump()))

    @router.get("/rubrics/{rubric_id}")
    def get_rubric(rubric_id: str) -> dict:
        return _dump(service.get_rubric(rubric_id))

    @router.post("/suites", status_code=201)
    def create_suite(request: CreateSuiteRequest) -> dict:
        return _dump(service.create_suite(name=request.name, payload=request.model_dump()))

    @router.get("/suites/{suite_id}")
    def get_suite(suite_id: str) -> dict:
        return _dump(service.get_suite(suite_id))

    @router.post("/assets", status_code=201)
    def create_asset(request: CreateAssetRequest) -> dict:
        return _dump(service.create_asset(name=request.name, payload=request.model_dump()))

    @router.get("/assets")
    def list_assets(type: str | None = None) -> list[dict]:
        return [_dump(a) for a in service.list_assets(type)]

    @router.post("/environments", status_code=201)
    def create_environment(request: CreateEnvironmentRequest) -> dict:
        return _dump(
            service.create_environment(name=request.name, payload=request.model_dump())
        )

    @router.get("/environments/{environment_id}")
    def get_environment(environment_id: str) -> dict:
        return _dump(service.get_environment(environment_id))

    @router.post("/runs", status_code=201)
    def start_evaluation_run(request: StartEvaluationRunRequest) -> dict:
        """Create and dispatch an evaluation run (spec section 32).

        eager 模式下 dispatcher 内联执行，返回终态 run；broker 模式下
        返回 RUNNING，worker 完成执行与 summary 汇总。
        """
        run = service.create_evaluation_run(**request.model_dump())
        run_dispatcher(run.id)
        return _dump(service.get_evaluation_run(run.id))

    @router.get("/runs")
    def list_evaluation_runs(suite_id: str | None = None) -> list[dict]:
        return [_dump(r) for r in service.list_evaluation_runs(suite_id)]

    @router.get("/runs/{run_id}")
    def get_evaluation_run(run_id: str) -> dict:
        return _dump(service.get_evaluation_run(run_id))

    @router.get("/runs/{run_id}/results")
    def list_results(run_id: str) -> list[dict]:
        return [_dump(r) for r in service.list_results(run_id)]

    @router.post("/runs/{run_id}/cancel")
    def cancel_evaluation_run(run_id: str) -> dict:
        return _dump(service.cancel_evaluation_run(run_id))

    @router.post("/gates", status_code=201)
    def create_gate(request: CreateGateRequest) -> dict:
        return _dump(service.create_gate(name=request.name, payload=request.model_dump()))

    @router.post("/gates/check")
    def check_gate(request: GateCheckRequest) -> dict:
        return _dump(service.check_gate(request.gate_id, request.run_id))

    return router


def _dump(value) -> dict:
    from agent_platform.evaluation.serialization import dump

    return dump(value)


def attach_evaluation_routes(
    app, service: EvaluationService, *, run_dispatcher: Callable[[str], None]
) -> None:
    """Mount evaluation routes; NotFoundError from evaluation handlers maps to 404.

    Existing app.py routes catch NotFoundError per-route, so this handler only
    fires for uncaught (evaluation) NotFoundErrors. run_dispatcher hands the
    created run to the dispatch layer (celery, injected by the composition root).
    """
    app.include_router(create_evaluation_router(service, run_dispatcher=run_dispatcher))

    @app.exception_handler(NotFoundError)
    async def _not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})
