"""MCP Gateway tests (03-mcp-gateway-architecture.md + mcp-gateway-spec.md).

Covers the registry (native + namespaced MCP sources), invocation through
the ToolCapability contract, permission, timeout, the side_effects retry
matrix (spec section 7.2), idempotency dedup + meta key (section 7.3),
input validation (section 10), output truncation (section 11), the
redacted audit trail (section 9.3) and the async-session bridge.
"""

import hashlib
import json
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


def _request(name: str, *, call_id: str | None = None, **arguments) -> ToolCallRequest:
    return ToolCallRequest(id=call_id or f"req-{name}", name=name, arguments=arguments)


class _FakeServer:
    """Duck-typed McpToolSource with scriptable per-tool behavior."""

    def __init__(self, name: str, tools: dict[str, str]) -> None:
        self._name = name
        self._tools = tools  # server-local tool name -> description
        self.schemas: dict[str, dict] = {}  # server-local tool name -> input schema
        self.behaviors: dict[str, object] = {}  # tool name -> callable(args)->str
        self.calls: list[tuple[str, dict]] = []  # (local name, arguments)
        self.metas: list[dict | None] = []

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self) -> list[McpToolDescriptor]:
        return [
            McpToolDescriptor(
                name=tool,
                description=description,
                input_schema=self.schemas.get(tool, {"type": "object", "properties": {}}),
            )
            for tool, description in self._tools.items()
        ]

    def call_tool(self, name: str, arguments: dict, *, meta: dict | None = None) -> str:
        self.calls.append((name, arguments))
        self.metas.append(meta)
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
    assert names == ["ping", "files__read_file"]
    assert gateway.describe_tool("files__read_file").description == "read a file"
    assert gateway.describe_tool("ping").description == "native ping"


def test_invoke_native_and_server_tools_uses_local_names_upstream():
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

    remote = gateway.invoke(_request("files__read_file", path="/tmp/a"))
    assert remote.error is None
    assert remote.content == "ok:read_file"
    # Upstream receives the server-local name, not the namespaced one.
    assert server.calls == [("read_file", {"path": "/tmp/a"})]


def test_same_tool_name_on_two_servers_coexists():
    files = _FakeServer("files", {"query": "files query"})
    db = _FakeServer("db", {"query": "db query"})
    gateway = McpToolGateway()
    gateway.register_server(files)
    gateway.register_server(db)

    assert [spec.name for spec in gateway.list_tools()] == ["files__query", "db__query"]
    result = gateway.invoke(_request("db__query"))
    assert result.content == "ok:query"
    assert db.calls == [("query", {})]
    assert files.calls == []


def test_duplicate_tool_name_rejected():
    server = _FakeServer("files", {"read_file": "colliding remote tool"})
    gateway = McpToolGateway()
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")

    with pytest.raises(ToolRegistrationError, match="already registered"):
        gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    # A native tool occupying a server's namespaced name rejects the server.
    gateway.register_native(
        ToolSpec(name="files__read_file"), lambda args: "native"
    )
    with pytest.raises(ToolRegistrationError, match="already registered"):
        gateway.register_server(server)


def test_invalid_side_effects_class_rejected():
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway()

    with pytest.raises(ValueError, match="side_effects"):
        gateway.register_server(server, side_effects="sometimes")
    with pytest.raises(ValueError, match="side_effects"):
        gateway.register_native(
            ToolSpec(name="ping"), lambda args: "pong", side_effects="sometimes"
        )
    assert gateway.list_tools() == []
    assert server.calls == []


def test_permission_denied_raises_and_audits():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway(allowed_tools={"ping"}, audit_sink=sink)
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    gateway.register_server(server)

    with pytest.raises(ToolPermissionDeniedError, match="not allowed"):
        gateway.invoke(_request("files__read_file"))

    assert server.calls == []  # denied before execution
    denied = [entry for entry in sink.entries if entry.outcome == "denied"]
    assert len(denied) == 1
    assert denied[0].tool == "files__read_file"
    assert denied[0].server == "files"


def test_unknown_tool_raises_not_found_and_audits():
    sink = InMemoryAuditSink()
    gateway = McpToolGateway(audit_sink=sink)

    with pytest.raises(NotFoundError, match="tool not found"):
        gateway.invoke(_request("missing"))

    assert sink.entries[-1].outcome == "not_found"
    assert sink.entries[-1].tool == "missing"


def test_invalid_arguments_return_model_visible_error_without_dispatch():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    server.schemas["read_file"] = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "mode": {"enum": ["r", "w"]}},
    }
    gateway = McpToolGateway(audit_sink=sink)
    gateway.register_server(server)

    result = gateway.invoke(_request("files__read_file", path=42))

    assert result.error is not None
    assert "expected type string" in result.error
    assert "42" not in result.error  # argument values never leak into errors
    assert server.calls == []  # validated before dispatch
    assert [entry.outcome for entry in sink.entries if entry.action == "invoke"] == ["invalid"]

    enum_reject = gateway.invoke(_request("files__read_file", path="a", mode="x"))
    assert "enum" in (enum_reject.error or "")

    missing = gateway.invoke(_request("files__read_file", mode="r"))
    assert "required property missing" in (missing.error or "")


def test_timeout_returns_error_result_without_retry_for_mutating_tools():
    sink = InMemoryAuditSink()
    server = _FakeServer("slow", {"hang": "hangs forever"})

    def hang(args):
        time.sleep(1.0)
        return "done"

    server.set_behavior("hang", hang)
    gateway = McpToolGateway(timeout_seconds=0.1, max_retries=3, audit_sink=sink)
    gateway.register_server(server)  # default class: mutating

    result = gateway.invoke(_request("slow__hang"))

    assert result.error is not None
    assert "timed out" in result.error
    assert len(server.calls) == 1  # mutating: timeout is final, no multiplied wait
    assert [entry.outcome for entry in sink.entries if entry.action == "invoke"] == ["timeout"]


def test_readonly_tool_retries_on_timeout():
    server = _FakeServer("slow", {"peek": "slow read"})

    def peek(args):
        time.sleep(0.3)
        return "done"

    server.set_behavior("peek", peek)
    gateway = McpToolGateway(timeout_seconds=0.05, max_retries=1)
    gateway.register_server(server, side_effects="readonly")

    result = gateway.invoke(_request("slow__peek"))

    assert result.error is not None
    assert "timed out" in result.error
    assert len(server.calls) == 2  # readonly: unknown progress may be retried


def test_server_error_is_never_retried():
    server = _FakeServer("files", {"read_file": "read a file"})
    server.set_behavior(
        "read_file",
        lambda args: (_ for _ in ()).throw(McpToolCallError("permission denied on server")),
    )
    gateway = McpToolGateway(max_retries=3)
    gateway.register_server(server, side_effects="readonly")

    result = gateway.invoke(_request("files__read_file"))

    assert result.error == "permission denied on server"
    assert len(server.calls) == 1  # the tool already executed upstream


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

    result = gateway.invoke(_request("flaky__unstable"))

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

    result = gateway.invoke(_request("broken__dead"))

    assert result.error == "boom"
    assert len(server.calls) == 3  # 1 attempt + 2 retries (dispatch-phase errors)


def test_idempotency_key_sent_via_meta():
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway()
    gateway.register_server(server)

    gateway.invoke(_request("files__read_file", path="x"))

    assert server.metas == [{"platform.idempotency_key": "req-files__read_file"}]


def test_duplicate_inflight_invocation_rejected_without_double_dispatch():
    server = _FakeServer("files", {"slow": "slow tool"})
    started = threading.Event()
    release = threading.Event()

    def slow(args):
        started.set()
        release.wait(5.0)
        return "done"

    server.set_behavior("slow", slow)
    gateway = McpToolGateway()
    gateway.register_server(server)

    first: dict = {}

    def worker():
        first["result"] = gateway.invoke(_request("files__slow", call_id="call-1"))

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(5.0)

    duplicate = gateway.invoke(_request("files__slow", call_id="call-1"))
    release.set()
    thread.join(5.0)

    assert duplicate.error is not None
    assert "duplicate" in duplicate.error
    assert first["result"].error is None
    assert len(server.calls) == 1  # rejected before a second upstream dispatch


def test_output_truncation_bounds_content_and_marks_total():
    server = _FakeServer("big", {"blob": "huge output"})
    total = 300_000
    server.set_behavior("blob", lambda args: "x" * total)
    gateway = McpToolGateway(output_limit_bytes=256 * 1024)
    gateway.register_server(server)

    result = gateway.invoke(_request("big__blob"))

    assert result.error is None
    assert result.content.count("x") == 256 * 1024
    assert result.content.endswith(f"[truncated by mcp gateway, {total} bytes total]")


def test_audit_records_argument_fingerprint_not_values():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway(audit_sink=sink)
    gateway.register_server(server)

    gateway.invoke(_request("files__read_file", path="/tmp/secret.txt"))

    entry = [e for e in sink.entries if e.action == "invoke"][0]
    raw = json.dumps({"path": "/tmp/secret.txt"}, sort_keys=True).encode("utf-8")
    assert entry.arguments_bytes == len(raw)
    assert entry.arguments_digest == hashlib.sha256(raw).hexdigest()
    assert "secret" not in repr(entry)  # redaction: values never reach the audit trail


def test_audit_sink_receives_registers_and_invokes():
    sink = InMemoryAuditSink()
    server = _FakeServer("files", {"read_file": "read a file"})
    gateway = McpToolGateway(audit_sink=sink)
    gateway.register_native(ToolSpec(name="ping"), lambda args: "pong")
    gateway.register_server(server)
    gateway.invoke(_request("ping"))
    gateway.invoke(_request("files__read_file"))

    registers = [entry for entry in sink.entries if entry.action == "register"]
    invokes = [entry for entry in sink.entries if entry.action == "invoke"]
    assert [entry.tool for entry in registers] == ["ping", "files__read_file"]
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
            self.calls: list[tuple[str, dict, dict | None]] = []

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="read_file", description="read", inputSchema={"type": "object"}
                    )
                ]
            )

        async def call_tool(self, name, arguments, *, meta=None):
            self.calls.append((name, arguments, meta))
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
        assert session.calls == [("read_file", {"path": "x"}, None)]

        meta = {"platform.idempotency_key": "call-1"}
        assert source.call_tool("read_file", {"path": "x"}, meta=meta) == "part1\npart2"
        assert session.calls[-1] == ("read_file", {"path": "x"}, meta)

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

    def worker(worker_id: int):
        for i in range(20):
            # Unique logical call ids: concurrent identical keys are
            # rejected as duplicates by design (spec section 7.3).
            result = gateway.invoke(
                _request("files__read_file", call_id=f"call-{worker_id}-{i}")
            )
            with lock:
                results.append(result.error)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [None] * 80
