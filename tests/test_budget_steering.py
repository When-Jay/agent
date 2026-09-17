"""Budget + Steering runtime control tests (budget-steering-spec.md).

覆盖：
* 多维 Budget 引擎（time/token/turn 的 soft/hard、FINISHING、grace、
  final turns —— spec sections 11-25）
* BudgetMiddleware（notice 注入、hard stop、事件 —— sections 23/35）
* SteeringMiddleware + InMemorySteeringChannel（合并注入、consume-once、
  run 隔离 —— sections 7/9/36/37）
* Adapter 接线（steering 通道、watchdog 硬超时 —— sections 26/42）
"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_platform.errors import PlatformError
from agent_platform.runtime.agent import AskUserMiddleware
from agent_platform.runtime.agent.adapter import DeepAgentsRuntimeAdapter
from agent_platform.runtime.agent.ask_user import _parse_answers, _validate_questions
from agent_platform.runtime.agent.middleware import (
    BudgetExceededError,
    BudgetMiddleware,
    SteeringMiddleware,
    ToolPermissionMiddleware,
)
from agent_platform.runtime.capabilities.budget import (
    BudgetCapability,
    BudgetSpec,
    InMemoryBudget,
    PHASE_EXHAUSTED,
    PHASE_FINISHING,
    PHASE_RUNNING,
)
from agent_platform.runtime.capabilities.steering import InMemorySteeringChannel
from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunStatus,
    RuntimeEventType,
    SessionManager,
    RunManager,
)


class _FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self


def _make_request(messages=None):
    return ModelRequest(
        model=_FakeModel(messages=iter([AIMessage("ok")])),
        messages=list(messages or [HumanMessage("hello")]),
    )


class _Clock:
    """Fake monotonic clock (seconds)."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _collector():
    events: list[tuple[RuntimeEventType, dict]] = []

    def emit(event_type, payload):
        events.append((event_type, payload))

    return events, emit


# --- budget engine (spec sections 11-25) ---------------------------------------


def test_budget_time_soft_then_hard():
    clock = _Clock()
    budget = InMemoryBudget(BudgetSpec(max_time_ms=100_000), clock=clock)

    decision = budget.decision()
    assert not decision.exceeded and decision.phase == PHASE_RUNNING

    clock.advance(80)  # 80s of 100s: soft (ratio 0.8)
    decision = budget.decision()
    assert decision.entering_finishing
    assert decision.newly_soft == ["time"]
    assert not decision.hard_exceeded
    assert budget.state().phase == PHASE_FINISHING

    # entering_finishing 只在第一次出现（notice 只注入一次）
    assert not budget.decision().entering_finishing

    clock.advance(20)  # 100s: hard
    decision = budget.decision()
    assert decision.hard_exceeded
    assert "time" in decision.newly_hard
    assert budget.state().phase == PHASE_EXHAUSTED
    assert budget.exceeded() == "time"


def test_budget_token_soft_and_hard_with_usage():
    budget = InMemoryBudget(BudgetSpec(max_tokens=1000), clock=_Clock())
    budget.record(input_tokens=850, output_tokens=50)  # 900 >= 800 soft

    decision = budget.decision()
    assert decision.newly_soft == ["tokens"]
    assert decision.entering_finishing
    assert decision.remaining["tokens"] == 100.0

    budget.record(input_tokens=100, output_tokens=50)  # 1050 >= 1000 hard
    assert budget.decision().hard_exceeded


def test_budget_turn_includes_final_turn():
    budget = InMemoryBudget(BudgetSpec(max_turns=10), clock=_Clock())
    for _ in range(8):
        budget.enter_turn()
    decision = budget.decision()
    assert decision.newly_soft == ["turns"]
    assert decision.entering_finishing

    for _ in range(2):
        budget.enter_turn()
    assert budget.decision().hard_exceeded  # turn 10: hard (final turn 计入)


def test_budget_final_turns_exhaustion_after_finishing():
    budget = InMemoryBudget(BudgetSpec(max_turns=100, max_final_turns=1), clock=_Clock())
    for _ in range(80):
        budget.enter_turn()
    assert budget.decision().entering_finishing  # 80 >= 80 soft

    # FINISHING 后第 1 个 model call（final turn）允许
    budget.enter_turn()
    decision = budget.decision()
    assert not decision.hard_exceeded

    # 第 2 个 finishing model call → hard（超过 max_final_turns=1）
    budget.enter_turn()
    decision = budget.decision()
    assert decision.hard_exceeded
    assert "final_turns" in decision.newly_hard


def test_budget_grace_time_exhaustion():
    clock = _Clock()
    budget = InMemoryBudget(
        BudgetSpec(max_time_ms=1_000_000, max_tokens=1000, grace_time_ms=5_000),
        clock=clock,
    )
    # token soft 触发 FINISHING
    budget.record(input_tokens=900)
    assert budget.decision().entering_finishing

    # grace 内允许
    clock.advance(4)
    assert not budget.decision().hard_exceeded

    # grace 超时 → hard
    clock.advance(2)
    decision = budget.decision()
    assert decision.hard_exceeded
    assert "time" in decision.newly_hard


def test_budget_legacy_capability_keeps_hard_only_semantics():
    class _Legacy(BudgetCapability):
        def __init__(self):
            self.recorded = []

        def record(self, **kwargs):
            self.recorded.append(kwargs)

        def usage(self):
            return {}

        def exceeded(self):
            return "cost"

    budget = _Legacy()
    decision = budget.decision()
    assert decision.hard_exceeded
    assert decision.triggered_dimensions == ["cost"]
    assert not decision.entering_finishing  # 无 notice 注入路径
    assert budget.timeout_seconds() is None
    budget.enter_turn()  # no-op 不报错


def test_budget_timeout_seconds():
    budget = InMemoryBudget(BudgetSpec(max_time_ms=30_000), clock=_Clock())
    assert budget.timeout_seconds() == 30.0
    assert InMemoryBudget(clock=_Clock()).timeout_seconds() is None


# --- budget middleware (spec sections 23/35) ------------------------------------


def test_budget_middleware_hard_stop_before_model_call():
    budget = InMemoryBudget(BudgetSpec(max_time_ms=1), clock=_Clock())
    budget._started_at = -1.0  # elapsed 1000ms >= max_time_ms 1ms
    events, emit = _collector()
    middleware = BudgetMiddleware(budget, emit=emit)
    called = []

    async def handler(request):
        called.append(True)
        return AIMessage("ok")

    with pytest.raises(BudgetExceededError) as excinfo:
        asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert excinfo.value.dimension == "time"
    assert not called
    assert RuntimeEventType.BUDGET_HARD_LIMIT in [e for e, _ in events]
    assert RuntimeEventType.BUDGET_EXHAUSTED in [e for e, _ in events]


def test_budget_middleware_injects_finishing_notice_once():
    clock = _Clock()
    budget = InMemoryBudget(BudgetSpec(max_tokens=1000), clock=clock)
    budget.record(input_tokens=900)  # soft
    events, emit = _collector()
    middleware = BudgetMiddleware(budget, emit=emit)
    seen_requests: list = []

    async def handler(request):
        seen_requests.append(list(request.messages))
        return AIMessage("ok")

    asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert len(seen_requests[0]) == 2
    assert seen_requests[0][-1].content.startswith(
        "[SYSTEM NOTICE — run time budget nearly exhausted]"
    )
    assert "Produce the required final deliverable" in seen_requests[0][-1].content
    assert RuntimeEventType.BUDGET_FINISHING in [e for e, _ in events]

    # 第二次调用不再注入（consume once）
    asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert len(seen_requests[1]) == 1


def test_budget_middleware_records_usage_and_hard_stops_after_overshoot():
    budget = InMemoryBudget(BudgetSpec(max_tokens=1000), clock=_Clock())
    middleware = BudgetMiddleware(budget)

    async def handler(request):
        return AIMessage("ok", usage_metadata={"input_tokens": 700, "output_tokens": 400, "total_tokens": 1100})

    with pytest.raises(BudgetExceededError) as excinfo:
        asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert excinfo.value.dimension == "tokens"  # 1100 >= 1000 after the call
    assert budget.state().total_tokens == 1100  # overshoot 被记录（spec section 20）


def test_budget_middleware_legacy_capability_path():
    """旧 BudgetCapability 子类（仅 record/usage/exceeded）依旧 hard stop。"""

    class _Exhausted(BudgetCapability):
        def record(self, **kwargs):
            pass

        def usage(self):
            return {}

        def exceeded(self):
            return "cost"

    middleware = BudgetMiddleware(_Exhausted())

    async def handler(request):
        return AIMessage("reply")

    with pytest.raises(BudgetExceededError) as excinfo:
        asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))
    assert excinfo.value.dimension == "cost"


# --- steering (spec sections 7/9/36/37) ------------------------------------------


def test_steering_channel_consume_once_and_isolation():
    channel = InMemorySteeringChannel()
    channel.publish("run-a", "first")
    channel.publish("run-a", "second")
    channel.publish("run-b", "other run")

    pending = channel.pending("run-a")
    assert [m.message for m in pending] == ["first", "second"]  # oldest first

    channel.acknowledge("run-a", [pending[0].id])
    assert [m.message for m in channel.pending("run-a")] == ["second"]
    assert [m.message for m in channel.pending("run-b")] == ["other run"]  # run 隔离

    channel.acknowledge("run-a", [pending[1].id])
    assert channel.pending("run-a") == []


def test_steering_middleware_injects_and_consumes_once():
    channel = InMemorySteeringChannel()
    channel.publish("run-1", "stop searching, summarize now")
    channel.publish("run-1", "also mention caveats")
    events, emit = _collector()
    middleware = SteeringMiddleware(channel, "run-1", emit=emit)
    seen_requests: list = []

    async def handler(request):
        seen_requests.append(list(request.messages))
        return AIMessage("ok")

    asyncio.run(middleware.awrap_model_call(_make_request(), handler))

    # 多条 steering 合并为一条 System Notice（spec section 9）
    assert len(seen_requests[0]) == 2
    notice = seen_requests[0][-1].content
    assert "[SYSTEM NOTICE — USER STEERING]" in notice
    assert "stop searching, summarize now" in notice
    assert "also mention caveats" in notice
    assert RuntimeEventType.STEERING_INJECTED in [e for e, _ in events]

    # consume-once：ack 后不再注入（spec sections 3/36）
    asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert len(seen_requests[1]) == 1
    assert channel.pending("run-1") == []
    consumed_events = [p for e, p in events if e is RuntimeEventType.STEERING_CONSUMED]
    assert len(consumed_events) == 1


def test_steering_middleware_keeps_pending_when_model_call_fails():
    """consume-after-successful-injection：调用失败 → steering 保持 pending。"""
    channel = InMemorySteeringChannel()
    channel.publish("run-1", "steer me")
    middleware = SteeringMiddleware(channel, "run-1")

    async def handler(request):
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        asyncio.run(middleware.awrap_model_call(_make_request(), handler))

    assert len(channel.pending("run-1")) == 1


def test_steering_middleware_noop_without_pending():
    middleware = SteeringMiddleware(InMemorySteeringChannel(), "run-1")
    seen: list = []

    async def handler(request):
        seen.append(list(request.messages))
        return AIMessage("ok")

    result = asyncio.run(middleware.awrap_model_call(_make_request(), handler))
    assert len(seen[0]) == 1
    assert result.content == "ok"


# --- adapter wiring (spec sections 26/27/42) --------------------------------------


def _store_with_run(agent_config=None):
    store = InMemoryRuntimeStore()
    apps = SessionManager(store)
    application = apps.create_application(name="demo", metadata={"agent": agent_config or {}})
    session = apps.create_session(application_id=application.id)
    run = RunManager(store).create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="agent",
        input={"message": "hello"},
    )
    return store, run.id


def test_adapter_consumes_steering_during_run():
    store, run_id = _store_with_run()
    channel = InMemorySteeringChannel()
    channel.publish(run_id, "wrap it up quickly")
    model = _FakeModel(messages=iter([AIMessage("done!")]))
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=InMemoryToolCapability(),
        steering_channel=channel,
    )

    result = asyncio.run(adapter.run(run_id))

    assert result.status == "completed"
    # steering 在 run 的唯一一次 model call 前被消费
    assert channel.pending(run_id) == []
    events = [e.event_type for e in EventBus(store).list_events(run_id)]
    assert RuntimeEventType.STEERING_INJECTED in events
    assert RuntimeEventType.STEERING_CONSUMED in events


def test_adapter_watchdog_hard_timeout_fails_run():
    """LLM 挂死 → watchdog（max_time hard cap）终止 run（spec section 42）。"""

    class _HangingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.sleep(30)
            raise AssertionError("watchdog 应当先终止")

    store, run_id = _store_with_run(
        agent_config={"budget": {"max_time_ms": 200, "soft_ratio": 0.8}}
    )
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: _HangingModel(messages=iter([AIMessage("never")])),
        tool_capability=InMemoryToolCapability(),
    )

    result = asyncio.run(adapter.run(run_id))

    assert result.status == "failed"
    assert "time" in (result.error or "")
    assert RunManager(store).get_run(run_id).status is RunStatus.FAILED
    events = [e.event_type for e in EventBus(store).list_events(run_id)]
    assert RuntimeEventType.RUN_FAILED in events


def test_adapter_budget_turn_limit_fails_run():
    """max_turns 硬限制：多轮 agent loop 在第 N 轮被终止。"""

    class _LoopingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

    # 无 tool call 的 AIMessage 使 loop 第 1 轮即收敛，因此用 max_turns=1：
    # 首次 enter_turn（turns=1 >= hard 1）在 Model Call 前硬终止。
    model = _LoopingModel(messages=iter([AIMessage("never reached")]))
    store, run_id = _store_with_run(agent_config={"budget": {"max_turns": 1}})
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=InMemoryToolCapability(),
    )

    result = asyncio.run(adapter.run(run_id))

    assert result.status == "failed"
    assert "turns" in (result.error or "")


# --- ask_user (spec sections 27/28/43) ---------------------------------------------


def test_ask_user_middleware_provides_ask_user_tool():
    middleware = AskUserMiddleware()
    assert [t.name for t in middleware.tools] == ["ask_user"]


def test_ask_user_question_validation():
    with pytest.raises(ValueError):
        _validate_questions([])
    with pytest.raises(ValueError):
        _validate_questions([{"question": "", "type": "text"}])
    with pytest.raises(ValueError):
        _validate_questions([{"question": "q", "type": "poll"}])
    with pytest.raises(ValueError):
        _validate_questions([{"question": "q", "type": "multiple_choice", "choices": []}])
    with pytest.raises(ValueError):
        _validate_questions([{"question": "q", "type": "text", "choices": ["a"]}])
    _validate_questions([{"question": "q", "type": "text"}])  # 合法结构不抛


def test_ask_user_parse_answers_contract():
    """上游协议：{"status": "answered"|"cancelled"|"error", "answers": [...]}。"""
    questions = [
        {"question": "Which color?", "type": "text"},
        {"question": "Size?", "type": "multiple_choice", "choices": ["S", "M"]},
    ]
    command = _parse_answers(
        {"status": "answered", "answers": ["blue", "M"]}, questions, "call-1"
    )
    assert isinstance(command, Command)
    message = command.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert "Q: Which color?\nA: blue" in message.content
    assert "Q: Size?\nA: M" in message.content

    cancelled = _parse_answers({"status": "cancelled"}, questions, "call-2")
    assert "(cancelled)" in cancelled.update["messages"][0].content

    malformed = _parse_answers("oops", questions, "call-3")
    assert "(error: invalid ask_user response payload)" in malformed.update["messages"][0].content


def test_ask_user_tool_bypasses_tool_allowlist():
    """ask_user 是平台运行时控制工具，不受用户 allowlist 限制。"""
    middleware = ToolPermissionMiddleware(["add"])
    request = SimpleNamespace(
        tool=SimpleNamespace(name="ask_user"), tool_call={"name": "ask_user"}
    )

    async def handler(_request):
        return "ok"

    assert asyncio.run(middleware.awrap_tool_call(request, handler)) == "ok"


def _ask_user_tool_call():
    return AIMessage(
        "",
        tool_calls=[
            {
                "name": "ask_user",
                "args": {"questions": [{"question": "Which color?", "type": "text"}]},
                "id": "call-ask-1",
                "type": "tool_call",
            }
        ],
    )


def test_agent_parks_on_ask_user_then_resumes_with_answer():
    """spec section 43 链路：Agent → AskUser → interrupt → checkpoint →
    user response → resume → Agent continue。"""
    store, run_id = _store_with_run()
    model = _FakeModel(
        messages=iter([_ask_user_tool_call(), AIMessage("the user picked blue")])
    )
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=InMemoryToolCapability(),
        checkpointer=MemorySaver(),
    )

    result = asyncio.run(adapter.run(run_id))

    assert result.status == "waiting_for_human"
    assert RunManager(store).get_run(run_id).status is RunStatus.WAITING_FOR_HUMAN
    approvals = [
        e
        for e in EventBus(store).list_events(run_id)
        if e.event_type is RuntimeEventType.APPROVAL_REQUIRED
    ]
    request = approvals[0].payload["request"]
    assert request["type"] == "ask_user"
    assert request["questions"][0]["question"] == "Which color?"
    assert request["tool_call_id"] == "call-ask-1"

    resumed = asyncio.run(
        adapter.resume(run_id, response={"status": "answered", "answers": ["blue"]})
    )

    assert resumed.status == "completed"
    assert "blue" in resumed.output["final"]
    types = [e.event_type for e in EventBus(store).list_events(run_id)]
    assert RuntimeEventType.HUMAN_RESPONSE_RECEIVED in types
    assert types[-1] is RuntimeEventType.RUN_COMPLETED


def test_budget_exceeded_error_is_platform_error():
    assert issubclass(BudgetExceededError, PlatformError)
