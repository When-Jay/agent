"""Evolution API routes (evolution-platform-spec.md section 25).

V1 路径前缀对齐平台约定：/api/v1/evolution/...。Run 执行经注入的
run_dispatcher 派发到 worker（eager/dev 内联完成）；approve/reject/
rollback 是同步控制面操作。API 层不感知 celery/broker，也不导入任何
执行模块。
"""

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_platform.errors import NotFoundError
from agent_platform.evolution.application import EvolutionService
from agent_platform.evolution.serialization import dump


class RegisterTargetRequest(BaseModel):
    """注册进化目标并写入基线版本（种子 v1）。"""

    target_type: str = "SKILL"
    resource_id: str
    content: str
    risk_level: str = "LOW"
    created_by: str = ""


class CreateEvolutionTaskRequest(BaseModel):
    name: str
    description: str = ""
    trigger: dict[str, Any] = Field(default_factory=dict)
    diagnosis: dict[str, Any] = Field(default_factory=dict)
    target_type: str = "SKILL"
    resource_id: str = ""
    objective: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    # {"application_id", "runtime_type", "suites": {"validation", ...}, "gate_id"?}
    evaluation_assets: dict[str, Any] = Field(default_factory=dict)
    strategy: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "LOW"
    auto_release: bool = False
    created_by: str = ""


class ApprovalRequest(BaseModel):
    approver: str
    reason: str = ""


class RollbackRequest(BaseModel):
    actor: str = ""
    reason: str = ""


def create_evolution_router(
    service: EvolutionService, *, run_dispatcher: Callable[[str], None]
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/evolution", tags=["evolution"])

    @router.post("/targets", status_code=201)
    def register_target(request: RegisterTargetRequest) -> dict:
        return dump(
            service.register_target(
                target_type=request.target_type,
                resource_id=request.resource_id,
                content=request.content,
                risk_level=request.risk_level,
                created_by=request.created_by,
            )
        )

    @router.post("/tasks", status_code=201)
    def create_task(request: CreateEvolutionTaskRequest) -> dict:
        try:
            return dump(
                service.create_task(name=request.name, payload=request.model_dump())
            )
        except ValueError as exc:
            # 资产/策略配置不完整属于请求校验错误，而非服务器故障。
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.get("/tasks")
    def list_tasks() -> list[dict]:
        return [dump(t) for t in service.list_tasks()]

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return dump(service.get_task(task_id))

    @router.post("/tasks/{task_id}/runs", status_code=201)
    def start_run(task_id: str) -> dict:
        """创建并派发一个 Evolution Run（spec section 25）。

        幂等：任务已有未终结 Run 时返回该 Run（不重复派发）。
        """
        try:
            run = service.start_run(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if run.status.value == "CREATED":
            run_dispatcher(run.id)
        return _run_detail(service, run.id)

    @router.get("/runs")
    def list_runs(task_id: str | None = None) -> list[dict]:
        return [dump(r) for r in service.list_runs(task_id)]

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return _run_detail(service, run_id)

    @router.get("/runs/{run_id}/candidates")
    def list_candidates(run_id: str) -> list[dict]:
        return [dump(c) for c in service.list_candidates(run_id)]

    @router.post("/runs/{run_id}/approve")
    def approve(run_id: str, request: ApprovalRequest) -> dict:
        return dump(service.approve(run_id, approver=request.approver, reason=request.reason))

    @router.post("/runs/{run_id}/reject")
    def reject(run_id: str, request: ApprovalRequest) -> dict:
        return dump(service.reject(run_id, approver=request.approver, reason=request.reason))

    @router.post("/versions/{version_id}/rollback")
    def rollback(version_id: str, request: RollbackRequest) -> dict:
        record = service.rollback(version_id, actor=request.actor, reason=request.reason)
        return dump(record)

    return router


def _run_detail(service: EvolutionService, run_id: str) -> dict:
    run = service.get_run(run_id)
    detail = dump(run)
    detail["candidates"] = [dump(c) for c in service.list_candidates(run_id)]
    detail["experiments"] = [dump(e) for e in service.list_experiments(run_id)]
    detail["events"] = [dump(e) for e in service.list_events(run_id)]
    return detail


def attach_evolution_routes(
    app, service: EvolutionService, *, run_dispatcher: Callable[[str], None]
) -> None:
    """Mount evolution routes; evaluation already maps NotFoundError -> 404.

    The evaluation API attachment registers the shared NotFoundError handler
    on the app; evolution skips re-registration when present so standalone
    mounting still maps missing resources to 404.
    """
    app.include_router(create_evolution_router(service, run_dispatcher=run_dispatcher))
    if NotFoundError not in getattr(app, "exception_handlers", {}):
        @app.exception_handler(NotFoundError)
        async def _not_found_handler(request, exc: NotFoundError):
            return JSONResponse(status_code=404, content={"detail": str(exc)})
