"""MCP composition-root tests (mcp-gateway-spec.md sections 3, 4).

build_mcp_gateway wires configs + credentials + pooled holders into the
gateway; sdk_session_factory is exercised for transport validation only
(http/sse need a real endpoint, out of unit scope).
"""

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer

from agent_platform.mcp.credentials import CredentialMaterial
from agent_platform.mcp.sessions import McpServerConfig, SessionPool
from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolSpec
from agent_platform.runtime.capabilities.tool_capability import (
    CompositeToolCapability,
    InMemoryToolCapability,
)
from agent_platform.runtime.dispatch.mcp_composition import (
    build_mcp_gateway,
    parse_mcp_servers_json,
    sdk_session_factory,
)


def _demo_server() -> MCPServer:
    server = MCPServer("demo")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return f"echo:{text}"

    return server


def _memory_pool(server: MCPServer) -> SessionPool:
    from contextlib import asynccontextmanager

    def builder(transport: str):
        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]):
            async with InMemoryTransport(server, raise_exceptions=True) as streams:
                session = ClientSession(*streams)
                async with session:
                    await session.initialize()
                    yield session

        return factory

    return SessionPool(builder=builder)


def test_gateway_invokes_through_pool_and_reuses_holders():
    server = _demo_server()
    pool = _memory_pool(server)
    try:
        gateway = build_mcp_gateway(
            [McpServerConfig(name="demo", url="memory://demo", side_effects="readonly")],
            pool=pool,
        )
        assert [spec.name for spec in gateway.list_tools()] == ["demo__echo"]

        result = gateway.invoke(
            ToolCallRequest(id="call-1", name="demo__echo", arguments={"text": "hi"})
        )
        assert result.error is None
        assert result.content == "echo:hi"

        holder = pool.get(
            McpServerConfig(name="demo", url="memory://demo"), CredentialMaterial()
        )
        again = pool.get(
            McpServerConfig(name="demo", url="memory://demo"), CredentialMaterial()
        )
        assert holder is again  # same server + material -> shared connection
    finally:
        pool.close_all()


def test_parse_mcp_servers_json():
    raw = (
        '[{"name": "github", "url": "https://mcp.example.com/mcp", '
        '"transport": "sse", "credential_ref": "github_main", "side_effects": "readonly"}]'
    )
    servers = parse_mcp_servers_json(raw)
    assert len(servers) == 1
    assert servers[0].name == "github"
    assert servers[0].transport == "sse"
    assert servers[0].credential_ref == "github_main"
    assert servers[0].side_effects == "readonly"

    assert parse_mcp_servers_json("") == []
    assert parse_mcp_servers_json("  ") == []
    with pytest.raises(ValueError, match="JSON array"):
        parse_mcp_servers_json('{"name": "x"}')
    with pytest.raises(ValueError, match="name"):
        parse_mcp_servers_json('[{"url": "https://x"}]')
    # http/sse without url -> error; stdio without command -> error.
    with pytest.raises(ValueError, match="url"):
        parse_mcp_servers_json('[{"name": "x", "transport": "sse"}]')


def test_sdk_session_factory_rejects_unknown_transport():
    with pytest.raises(ValueError, match="unsupported MCP transport"):
        sdk_session_factory("carrier-pigeon")


def test_composite_capability_prefers_earlier_capability():
    native = InMemoryToolCapability()
    native.register(ToolSpec(name="ping", description="native"), lambda args: "pong")
    other = InMemoryToolCapability()
    other.register(ToolSpec(name="ping", description="shadowed"), lambda args: "other")

    composite = CompositeToolCapability([native, other])
    assert [spec.name for spec in composite.list_tools()] == ["ping"]
    assert composite.describe_tool("ping").description == "native"
    result = composite.invoke(ToolCallRequest(id="c1", name="ping", arguments={}))
    assert result.content == "pong"

    with pytest.raises(Exception, match="tool not found"):
        composite.invoke(ToolCallRequest(id="c2", name="missing", arguments={}))
