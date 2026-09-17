"""Agent durable checkpoints, resume and HITL approval tests.

Covers the agent-side HITL loop (deepagents-runtime-spec.md sections 5,
8, 9): HumanApprovalMiddleware pauses the run on pending tool approvals,
POST /runs/{id}/respond decisions fold back through adapter.resume(),
and failed runs resume from durable checkpoint payloads via
StoreCheckpointSaver.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.errors import InvalidStateTransitionError
from agent_platform.runtime.agent import DeepAgentsRuntimeAdapter, StoreCheckpointSaver
from agent_platform.runtime.capabilities.tool import ToolSpec
from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    SessionManager,
)
from agent_platform.runtime.dispatch import RuntimeOrchestrator, create_runtime_store
from agent_platform.runtime.dispatch import tasks as dispatch_tasks

from test_agent_runtime import _FakeModel, _event_types, _make_run


def _launch_capability(calls: dict, *, fail_first: bool = False) -> InMemoryToolCapability:
    def launch(arguments):
        calls["launch"] = calls.get("launch", 0) + 1
        if fail_first and calls["launch"] == 1:
            raise RuntimeError("transient boom")
        return f"launched {arguments['env']}"

    capability = InMemoryToolCapability()
    capability.register(
        ToolSpec(
            name="launch",
            description="Launch a deployment",
            parameters={
                "type": "object",
                "properties": {"env": {"type": "string", "description": "target env"}},
                "required": ["env"],
            },
        ),
        launch,
    )
    return capability


def _launch_tool_call(call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[
            {"name": "launch", "args": {"env": "prod"}, "id": call_id, "type": "tool_call"}
        ],
    )


def _events_of_type(store, run_id, event_type):
    return [
        event for event in EventBus(store).list_events(run_id) if event.event_type is event_type
    ]


# --- durable checkpointer ---------------------------------------------------------


def test_store_checkpoint_saver_roundtrip_and_writes(tmp_path):
    saver = StoreCheckpointSaver(create_engine(f"sqlite:///{tmp_path / 'cp.db'}"))
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "id": "cp-1",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"messages": ["hello"]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
    }

    config_out = saver.put(config, checkpoint, {"source": "input", "step": 1}, {})
    assert config_out["configurable"]["checkpoint_id"] == "cp-1"

    saver.put_writes(config_out, [("messages", "write-1")], task_id="task-1")
    tup = saver.get_tuple(config)
    assert tup.checkpoint["channel_values"]["messages"] == ["hello"]
    assert tup.metadata["source"] == "input"
    assert tup.parent_config is None
    assert tup.pending_writes == [("task-1", "messages", "write-1")]

    checkpoint2 = {**checkpoint, "id": "cp-2", "channel_versions": {"messages": 2}}
    # config_out carries checkpoint_id=cp-1, so cp-2 records it as parent.
    saver.put(config_out, checkpoint2, {"source": "loop", "step": 2}, {})
    latest = saver.get_tuple(config)
    assert latest.checkpoint["id"] == "cp-2"
    assert latest.parent_config["configurable"]["checkpoint_id"] == "cp-1"

    listed = [t.checkpoint["id"] for t in saver.list(config)]
    assert listed == ["cp-2", "cp-1"]


# --- HITL approval loop -----------------------------------------------------------


def test_agent_pauses_on_tool_approval_then_resumes_via_response():
    store = InMemoryRuntimeStore()
    calls: dict = {}
    run = _make_run(
        store,
        agent_config={"tools": ["launch"], "approvals": {"launch": ["approve", "reject"]}},
    )
    # after_model runs as its own graph node: on resume only the hook
    # replays, the model is called once more for the final answer.
    model = _FakeModel(messages=iter([_launch_tool_call(), AIMessage("done")]))
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=_launch_capability(calls),
        checkpointer=MemorySaver(),
    )

    result = asyncio.run(adapter.run(run.id))

    assert result.status == "waiting_for_human"
    assert calls.get("launch", 0) == 0  # nothing executed while awaiting approval
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.WAITING_FOR_HUMAN
    approvals = _events_of_type(store, run.id, RuntimeEventType.APPROVAL_REQUIRED)
    assert len(approvals) == 1
    request = approvals[0].payload["request"]
    assert request["action_requests"][0]["name"] == "launch"
    assert _event_types(store, run.id)[-1] is RuntimeEventType.APPROVAL_REQUIRED

    resumed = asyncio.run(
        adapter.resume(run.id, response={"decisions": [{"type": "approve"}]})
    )

    assert resumed.status == "completed"
    assert resumed.output["final"] == "done"
    assert calls["launch"] == 1  # executed exactly once, after approval
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    types = _event_types(store, run.id)
    assert RuntimeEventType.HUMAN_RESPONSE_RECEIVED in types
    assert RuntimeEventType.RUN_RESUMED in types
    assert types[-1] is RuntimeEventType.RUN_COMPLETED


def test_agent_rejection_skips_tool_execution():
    store = InMemoryRuntimeStore()
    calls: dict = {}
    run = _make_run(
        store,
        agent_config={"tools": ["launch"], "approvals": {"launch": ["approve", "reject"]}},
    )
    model = _FakeModel(messages=iter([_launch_tool_call(), AIMessage("done")]))
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=_launch_capability(calls),
        checkpointer=MemorySaver(),
    )
    asyncio.run(adapter.run(run.id))

    result = asyncio.run(
        adapter.resume(
            run.id, response={"decisions": [{"type": "reject", "message": "not today"}]}
        )
    )

    assert result.status == "completed"
    assert calls.get("launch", 0) == 0  # rejected tool never executed


def test_agent_resume_without_response_is_rejected():
    store = InMemoryRuntimeStore()
    run = _make_run(store)
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: _FakeModel(messages=iter([AIMessage("hi")])),
        tool_capability=InMemoryToolCapability(),
        checkpointer=MemorySaver(),
    )

    with pytest.raises(InvalidStateTransitionError):
        asyncio.run(adapter.resume(run.id))


# --- durable failure resume ---------------------------------------------------------


class _FlakyOnceModel(_FakeModel):
    """Chat model whose first invocation raises (transient outage)."""

    def __init__(self, messages, state: dict):
        super().__init__(messages=messages)
        self._state = state

    async def ainvoke(self, input, config=None, **kwargs):  # noqa: ANN001, ANN003
        self._state["model_calls"] = self._state.get("model_calls", 0) + 1
        if self._state["model_calls"] == 1:
            raise RuntimeError("transient model outage")
        return await super().ainvoke(input, config, **kwargs)


def test_agent_failed_run_resumes_from_durable_checkpoint(tmp_path):
    store = InMemoryRuntimeStore()
    calls: dict = {}
    run = _make_run(store, agent_config={"tools": ["launch"]})
    model = _FlakyOnceModel(
        messages=iter([_launch_tool_call(), AIMessage("done")]), state=calls
    )
    database_path = tmp_path / "cp.db"

    failing = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=_launch_capability(calls),
        checkpointer=StoreCheckpointSaver(create_engine(f"sqlite:///{database_path}")),
    )
    failed = asyncio.run(failing.run(run.id))
    assert failed.status == "failed"
    assert calls.get("launch", 0) == 0

    # A fresh adapter over the same database reattaches to the thread and
    # replays the pending model node (cross-instance durability).
    recovered = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=_launch_capability(calls),
        checkpointer=StoreCheckpointSaver(create_engine(f"sqlite:///{database_path}")),
    )
    resumed = asyncio.run(recovered.resume(run.id))

    assert resumed.status == "completed"
    assert resumed.output["final"] == "done"
    assert calls["launch"] == 1


# --- API respond ---------------------------------------------------------------------


def test_agent_approval_flow_through_respond_api(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    store = create_runtime_store(database_url)
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    apps = SessionManager(store)
    application = apps.create_application(
        name="demo",
        metadata={"agent": {"tools": ["launch"], "approvals": {"launch": ["approve", "reject"]}}},
    )
    calls: dict = {}
    model = _FakeModel(messages=iter([_launch_tool_call(), AIMessage("done")]))
    # One adapter instance across dispatch tasks: the in-process MemorySaver
    # holds the interrupted thread between execute and respond.
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=_launch_capability(calls),
        event_bus=EventBus(store),
        checkpointer=MemorySaver(),
    )
    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, agent_adapter=adapter)
    )
    try:
        client = TestClient(create_app(settings))
        created = client.post(
            "/api/v1/runs",
            json={"application_id": application.id, "runtime_type": "agent", "input": {}},
        )
        run_id = created.json()["id"]
        answered = client.post(
            f"/api/v1/runs/{run_id}/respond",
            json={"response": {"decisions": [{"type": "approve"}]}},
        )
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    assert created.status_code == 201
    assert created.json()["status"] == "waiting_for_human"
    assert answered.status_code == 200
    assert answered.json()["status"] == "completed"
    event_types = [event["event_type"] for event in events]
    assert "ApprovalRequired" in event_types
    assert "HumanResponseReceived" in event_types
    assert "RunResumed" in event_types
    assert event_types[-1] == "RunCompleted"
