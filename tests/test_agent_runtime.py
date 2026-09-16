"""DeepAgents Agent Runtime tests (ADR-0002, deepagents-runtime-spec.md).

Covers the adapter contract: run lifecycle + event mapping, platform
tool execution through the LangChain tool loop, budget stops, tool
permission middleware and the sandbox backend primitives.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent_platform.runtime.agent import (
    BudgetExceededError,
    BudgetMiddleware,
    LangChainToolAdapter,
    PlatformSandboxBackend,
    ToolAdaptationError,
    ToolPermissionDeniedError,
    ToolPermissionMiddleware,
    DeepAgentsRuntimeAdapter,
)
from agent_platform.runtime.capabilities.budget import BudgetCapability
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
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import SandboxSpec, WorkspaceMount
from agent_platform.sandbox.registry import SandboxProviderRegistry

from test_sandbox_core import FakeSandboxProvider


class _FakeModel(GenericFakeChatModel):
    """Fake chat model that tolerates bind_tools() from agent construction."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self


def _make_run(store, *, agent_config=None, input_message="hello"):
    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(name="demo", metadata={"agent": agent_config or {}})
    session = apps.create_session(application_id=application.id)
    return runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="agent",
        input={"message": input_message},
    )


def _event_types(store, run_id):
    return [event.event_type for event in EventBus(store).list_events(run_id)]


# --- adapter ------------------------------------------------------------------


def test_run_completes_and_maps_events():
    store = InMemoryRuntimeStore()
    run = _make_run(store)
    model = _FakeModel(messages=iter([AIMessage("hello!")]))
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=InMemoryToolCapability(),
    )

    result = asyncio.run(adapter.run(run.id))

    assert result.status == "completed"
    assert result.output["final"] == "hello!"
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output == result.output

    event_types = _event_types(store, run.id)
    assert event_types[0] is RuntimeEventType.RUN_STARTED
    assert RuntimeEventType.LLM_STARTED in event_types
    assert RuntimeEventType.LLM_COMPLETED in event_types
    assert event_types[-1] is RuntimeEventType.RUN_COMPLETED


def test_tool_loop_executes_platform_tool():
    store = InMemoryRuntimeStore()
    capability = InMemoryToolCapability()
    capability.register(
        ToolSpec(
            name="add",
            description="Add two integers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "first"},
                    "b": {"type": "integer", "description": "second"},
                },
                "required": ["a", "b"],
            },
        ),
        lambda arguments: str(arguments["a"] + arguments["b"]),
    )
    run = _make_run(store, agent_config={"tools": ["add"]})
    model = _FakeModel(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "add",
                            "args": {"a": 2, "b": 3},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("sum is 5"),
            ]
        )
    )
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=capability,
    )

    result = asyncio.run(adapter.run(run.id))

    assert result.status == "completed"
    assert result.output["final"] == "sum is 5"
    event_types = _event_types(store, run.id)
    assert RuntimeEventType.TOOL_CALL_STARTED in event_types
    assert RuntimeEventType.TOOL_CALL_COMPLETED in event_types


def test_budget_exhaustion_fails_run():
    class _Exhausted(BudgetCapability):
        def record(self, **kwargs):  # noqa: ANN003
            pass

        def usage(self):
            return {}

        def exceeded(self):
            return "tokens"

    store = InMemoryRuntimeStore()
    run = _make_run(store)
    model = _FakeModel(messages=iter([AIMessage("never reached")]))
    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: model,
        tool_capability=InMemoryToolCapability(),
        budget_factory=lambda spec: _Exhausted(),
    )

    result = asyncio.run(adapter.run(run.id))

    assert result.status == "failed"
    assert "budget" in (result.error or "")
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.FAILED
    failed_events = [
        event
        for event in EventBus(store).list_events(run.id)
        if event.event_type is RuntimeEventType.RUN_FAILED
    ]
    assert failed_events and failed_events[-1].payload.get("reason") == "budget"


# --- middleware -----------------------------------------------------------------


def test_tool_permission_middleware_denies_disallowed_tools():
    middleware = ToolPermissionMiddleware(["allowed"])
    request = SimpleNamespace(
        tool=SimpleNamespace(name="secret"), tool_call={"name": "secret"}
    )

    async def handler(_request):
        return "ok"

    with pytest.raises(ToolPermissionDeniedError):
        asyncio.run(middleware.awrap_tool_call(request, handler))


def test_tool_permission_middleware_allows_configured_tools():
    middleware = ToolPermissionMiddleware(["allowed"])
    request = SimpleNamespace(
        tool=SimpleNamespace(name="allowed"), tool_call={"name": "allowed"}
    )

    async def handler(_request):
        return "ok"

    assert asyncio.run(middleware.awrap_tool_call(request, handler)) == "ok"


def test_budget_middleware_raises_when_exceeded():
    class _Exhausted(BudgetCapability):
        def record(self, **kwargs):  # noqa: ANN003
            pass

        def usage(self):
            return {}

        def exceeded(self):
            return "cost"

    middleware = BudgetMiddleware(_Exhausted())

    async def handler(_request):
        return AIMessage("reply")

    with pytest.raises(BudgetExceededError) as excinfo:
        asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))
    assert excinfo.value.dimension == "cost"


# --- tool adaptation ------------------------------------------------------------


def test_tool_adapter_builds_langchain_tools_from_specs():
    capability = InMemoryToolCapability()
    capability.register(
        ToolSpec(
            name="echo",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        lambda arguments: arguments.get("text", ""),
    )

    tools = LangChainToolAdapter(capability).adapt(None)
    assert [tool.name for tool in tools] == ["echo"]
    assert tools[0].invoke({"text": "hi"}) == "hi"


def test_tool_adapter_rejects_unknown_names():
    capability = InMemoryToolCapability()
    with pytest.raises(ToolAdaptationError):
        LangChainToolAdapter(capability).adapt(["missing"])


# --- sandbox backend ------------------------------------------------------------


def test_platform_sandbox_backend_primitives():
    registry = SandboxProviderRegistry()
    registry.register("fake", FakeSandboxProvider())
    manager = SandboxManager(registry, default_provider="fake")
    spec = SandboxSpec(
        image="fake:latest",
        tenant_id="tenant",
        user_id="user",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id="ws-session-1"),
    )
    sandbox = asyncio.run(manager.create(spec))
    backend = PlatformSandboxBackend(manager, sandbox.sandbox_id)

    uploads = asyncio.run(backend.upload_files([("notes.txt", b"data")]))
    assert uploads[0].path == "notes.txt"
    assert uploads[0].error is None

    downloads = asyncio.run(backend.download_files(["notes.txt"]))
    assert downloads[0].content == b"data"

    response = asyncio.run(backend.execute("echo hello", timeout=5))
    assert response.exit_code == 0
    assert response.output.strip() == "hello"
