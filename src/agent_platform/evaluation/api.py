"""Evaluation API routes (evaluation-spec.md section 32).

V1 路径前缀对齐平台约定：/api/v1/evaluation/...。评测 Run 经注入的
run_dispatcher 派发（eager/dev 模式内联执行完成；broker 模式返回
RUNNING 由 worker 执行）——API 层不感知 celery/broker。
"""

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.cases import CaseService
from agent_platform.evaluation.diagnosis import DiagnosisService
from agent_platform.evaluation.domain import CaseSource, CaseType
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


class CreateCaseRequest(BaseModel):
    source: str = "USER_FEEDBACK"
    type: str = "BAD"
    task_id: str | None = None
    trace_id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MineCasesRequest(BaseModel):
    """Signal source selector: a production run or an evaluation run."""

    run_id: str | None = None
    evaluation_run_id: str | None = None


class PromoteCaseRequest(BaseModel):
    asset_name: str = "regression-set"


def create_evaluation_router(
    service: EvaluationService,
    *,
    run_dispatcher: Callable[[str], None],
    case_service: CaseService | None = None,
    diagnosis_service: DiagnosisService | None = None,
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

    # --- cases (spec section 32 Second Stage) -----------------------------------

    @router.post("/cases", status_code=201)
    def create_case(request: CreateCaseRequest) -> dict:
        _require_case_service(case_service)
        return _dump(
            case_service.create_case(
                source=CaseSource(request.source),
                type=CaseType(request.type),
                task_id=request.task_id,
                trace_id=request.trace_id,
                input=request.input,
                output=request.output,
                expected_behavior=request.expected_behavior,
                metadata=request.metadata,
            )
        )

    @router.get("/cases")
    def list_cases(
        type: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        _require_case_service(case_service)
        return [
            _dump(c)
            for c in case_service.list_cases(
                type=CaseType(type) if type else None,
                source=CaseSource(source) if source else None,
                status=status,
            )
        ]

    @router.get("/cases/{case_id}")
    def get_case(case_id: str) -> dict:
        _require_case_service(case_service)
        return _dump(case_service.get_case(case_id))

    @router.post("/cases/mine")
    def mine_cases(request: MineCasesRequest) -> dict:
        """Mine cases from a production run or an evaluation run (spec section 23)."""
        _require_case_service(case_service)
        if request.evaluation_run_id:
            mined = case_service.mine_evaluation_run(request.evaluation_run_id)
        elif request.run_id:
            mined = case_service.mine_run(request.run_id)
            mined = [mined] if mined is not None else []
        else:
            raise HTTPException(
                status_code=422, detail="run_id or evaluation_run_id is required"
            )
        return {"cases": [_dump(c) for c in mined]}

    @router.post("/cases/{case_id}/diagnose")
    def diagnose_case(case_id: str) -> dict:
        _require_case_service(case_service)
        _require_diagnosis_service(diagnosis_service)
        return _dump(diagnosis_service.diagnose(case_id))

    @router.get("/cases/{case_id}/diagnoses")
    def list_case_diagnoses(case_id: str) -> list[dict]:
        _require_case_service(case_service)
        _require_diagnosis_service(diagnosis_service)
        return [_dump(d) for d in diagnosis_service.list_diagnoses(case_id)]

    @router.post("/cases/{case_id}/promote")
    def promote_case(case_id: str, request: PromoteCaseRequest) -> dict:
        """Promote a BAD case into the regression set (plan 050 Phase 9)."""
        _require_case_service(case_service)
        case, task, asset = case_service.promote_to_regression(
            case_id, asset_name=request.asset_name
        )
        return {"case": _dump(case), "task": _dump(task), "asset": _dump(asset)}

    @router.post("/cases/{case_id}/dismiss")
    def dismiss_case(case_id: str) -> dict:
        _require_case_service(case_service)
        return _dump(case_service.dismiss_case(case_id))

    return router


def _require_case_service(case_service: CaseService | None) -> CaseService:
    if case_service is None:
        raise RuntimeError("case service is not wired into the evaluation API")
    return case_service


def _require_diagnosis_service(diagnosis_service: DiagnosisService | None) -> DiagnosisService:
    if diagnosis_service is None:
        raise RuntimeError("diagnosis service is not wired into the evaluation API")
    return diagnosis_service


def _dump(value) -> dict:
    from agent_platform.evaluation.serialization import dump

    return dump(value)


def attach_evaluation_routes(
    app,
    service: EvaluationService,
    *,
    run_dispatcher: Callable[[str], None],
    case_service: CaseService | None = None,
    diagnosis_service: DiagnosisService | None = None,
) -> None:
    """Mount evaluation routes; NotFoundError from evaluation handlers maps to 404.

    Existing app.py routes catch NotFoundError per-route, so this handler only
    fires for uncaught (evaluation) NotFoundErrors. run_dispatcher hands the
    created run to the dispatch layer (celery, injected by the composition root).
    """
    app.include_router(
        create_evaluation_router(
            service,
            run_dispatcher=run_dispatcher,
            case_service=case_service,
            diagnosis_service=diagnosis_service,
        )
    )

    @app.exception_handler(NotFoundError)
    async def _not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})
