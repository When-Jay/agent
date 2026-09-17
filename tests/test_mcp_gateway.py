"""MCP Gateway V1 tests (03-mcp-gateway-architecture.md section 5).

Covers the registry (native + MCP sources), invocation through the
ToolCapability contract, permission, timeout, basic retry and the audit
trail, plus the async-session bridge.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from agent_platform.errors import NotFoundError, ToolPermissionDeniedError
from agent_platform.mcp import (
    AuditSink,
    InMemoryAuditSink,
    McpToolCallError,
    McpToolDescriptor,
    McpToolGateway,
    SdkMcpToolSource,
    ToolRegistrationError,
)
from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolSpec


def _request(name: str, **arguments) -> ToolCallRequest:
    return ToolCallRequest(id=f"req-{name}", name=name, arguments=arguments)


class _FakeServer:
    """Duck-typed McpToolSource with scriptable per-tool behavior."""

    def __init__(self, name: str, tools: dict[str, str]) -> None:
        self._name = name
        self._tools = tools  # tool name -> description
        self.behaviors: dict[str, object] = {}  # tool name -> callable(args)->str
        self.calls: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self) -> list[McpToolDescriptor]:
        return [
            McpToolDescriptor(
                name=tool,
                description=description,
                input_schema={"type": "object", "properties": {}},
            )
            for tool, description in self._tools.items()
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        behavior = self.behaviors.get(name)
        if callable(behavior):
            return behavior(arguments)
        return f"ok:{name}"

    def set_behavior(self, name: str, behavior) -> None:
        self.behaviors[name] = behavior


def test_lists_native_and_server_tools():
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway()
    gateway.register_native(ToolSpec(name="ping", description="native ping"), lambda args: "pong")
    gateway.register_server(server)

    names = [spec.name for spec in gateway.list_tools()]
    assert names == ["ping", "read_file"]
    assert gateway.describe_tool("read_file").description == "read a file"
    assert gateway.describe_tool("ping").description == "native ping"


def test_invoke_native_and_server_tools():
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway()
    seen: dict = {}
    gateway.register_native(
        ToolSpec(name="ping"), lambda args: (seen.update(args), "pong")[1]
    )
    gateway.register_server(server)

    native = gateway.invoke(_request("ping", echo="hi"))
    assert native.error is None
    assert native.content == "pong"
    assert seen == {"echo": "hi"}

    remote = gateway.invoke(_request("read_file", path="/tmp/a"))
    assert remote.error is None
    assert remote.content == "ok:read_file"
    assert server.calls == [("read_file", {"path": "/tmp/a"})]


def test_duplicate_tool_name_rejected():
    server = _FakeServer("files", {"ping": "colliding remote tool"})
    gateway = McpToolGateway()
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")

    with pytest.raises(ToolRegistrationError, match="already registered"):
        gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    with pytest.raises(ToolRegistrationError, match="already registered"):
        gateway.register_server(server)


def test_permission_denied_raises_and_audits():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway(allowed_tools={"ping"}, audit_sink=sink)
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    gateway.register_server(server)

    with pytest.raises(ToolPermissionDeniedError, match="not allowed"):
        gateway.invoke(_request("read_file"))

    assert server.calls == []  # denied before execution
    denied = [entry for entry in sink.entries if entry.outcome == "denied"]
    assert len(denied) == 1
    assert denied[0].tool == "read_file"
    assert denied[0].server == "files"


def test_unknown_tool_raises_not_found_and_audits():
    sink = InMemoryAuditSink()
    gateway = McpToolGateway(audit_sink=sink)

    with pytest.raises(NotFoundError, match="tool not found"):
        gateway.invoke(_request("missing"))

    assert sink.entries[-1].outcome == "not_found"
    assert sink.entries[-1].tool == "missing"


def test_timeout_returns_error_result_without_retry():
    sink = InMemoryAuditSink()
    server = _FakeServer("slow", {"hang": "hangs forever"})

    def hang(args):
        time.sleep(1.0)
        return "done"

    server.set_behavior("hang", hang)
    gateway = McpToolGateway(timeout_seconds=0.1, max_retries=3, audit_sink=sink)
    gateway.register_server(server)

    result = gateway.invoke(_request("hang"))

    assert result.error is not None
    assert "timed out" in result.error
    assert len(server.calls) == 1  # timeout aborts; retry must not multiply the wait
    assert [entry.outcome for entry in sink.entries if entry.action == "invoke"] == ["timeout"]


def test_retry_succeeds_on_transient_failure():
    sink = InMemoryAuditSink()
    server = _FakeServer("flaky", {"unstable": "fails once"})
    attempts = {"count": 0}

    def flaky(args):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return "recovered"

    server.set_behavior("unstable", flaky)
    gateway = McpToolGateway(max_retries=1, audit_sink=sink)
    gateway.register_server(server)

    result = gateway.invoke(_request("unstable"))

    assert result.error is None
    assert result.content == "recovered"
    assert [entry.outcome for entry in sink.entries if entry.action == "invoke"] == [
        "error",
        "ok",
    ]


def test_retry_exhausted_returns_error_result():
    server = _FakeServer("broken", {"dead": "always fails"})
    server.set_behavior("dead", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))
    gateway = McpToolGateway(max_retries=2)
    gateway.register_server(server)

    result = gateway.invoke(_request("dead"))

    assert result.error == "boom"
    assert len(server.calls) == 3  # 1 attempt + 2 retries


def test_mcp_server_error_result_becomes_error_content():
    server = _FakeServer("files", {"read_file": "read a file"})
    server.set_behavior(
        "read_file",
        lambda args: (_ for _ in ()).throw(McpToolCallError("permission denied on server")),
    )
    gateway = McpToolGateway()
    gateway.register_server(server)

    result = gateway.invoke(_request("read_file"))

    assert result.error == "permission denied on server"


def test_audit_sink_receives_registers_and_invokes():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway(audit_sink=sink)
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    gateway.register_server(server)
    gateway.invoke(_request("ping"))
    gateway.invoke(_request("read_file"))

    registers = [entry for entry in sink.entries if entry.action == "register"]
    invokes = [entry for entry in sink.entries if entry.action == "invoke"]
    assert [entry.tool for entry in registers] == ["ping", "read_file"]
    assert registers[0].server is None and registers[1].server == "files"
    assert all(entry.outcome == "ok" and entry.duration_ms is not None for entry in invokes)


class _RecordingSink(AuditSink):
    """AuditSink protocol conformance check helper (raises on purpose)."""

    def __init__(self) -> None:
        self.recorded = []
        self.fail = False

    def record(self, entry) -> None:
        if self.fail:
            raise RuntimeError("sink failure")
        self.recorded.append(entry)


def test_audit_sink_failure_does_not_break_invocation():
    sink = _RecordingSink()
    sink.fail = True
    gateway = McpToolGateway(audit_sink=sink)
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")

    result = gateway.invoke(_request("ping"))

    assert result.content == "pong"  # audit breakage must not fail the call


def test_sdk_source_bridges_async_session():
    class _FakeAsyncSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="read_file", description="read", inputSchema={"type": "object"}
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "fail":
                return SimpleNamespace(
                    content=[SimpleNamespace(text="server said no")], isError=True
                )
            return SimpleNamespace(
                content=[SimpleNamespace(text="part1"), SimpleNamespace(text="part2")],
                isError=False,
            )

    session = _FakeAsyncSession()
    source = SdkMcpToolSource("files", session)
    try:
        descriptors = source.list_tools()
        assert [d.name for d in descriptors] == ["read_file"]
        assert descriptors[0].input_schema == {"type": "object"}

        assert source.call_tool("read_file", {"path": "x"}) == "part1\npart2"
        assert session.calls == [("read_file", {"path": "x"})]

        with pytest.raises(McpToolCallError, match="server said no"):
            source.call_tool("fail", {})
    finally:
        source.close()
        source.close()  # idempotent


def test_concurrent_invocations_are_thread_safe():
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway()
    gateway.register_server(server)
    results: list = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            result = gateway.invoke(_request("read_file"))
            with lock:
                results.append(result.error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [None] * 80
