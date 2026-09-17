"""Composition root for the MCP gateway (mcp-gateway-spec.md sections 3, 4).

The official MCP SDK is a runtime dependency used ONLY here: this module
builds ready sessions (transport + ClientSession + initialize) that
session holders enter in one task on their own loop (task-bound anyio
scopes — mcp/sessions.py stays SDK-free by design). stdio servers are
materialized as sandbox workloads through a ServerRunner (section 6.2)
and reached over Streamable HTTP, so all transports converge on HTTP
sessions.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from agent_platform.config import Settings
from agent_platform.mcp.audit import AuditSink
from agent_platform.mcp.credentials import CredentialMaterial, CredentialResolver
from agent_platform.mcp.gateway import McpToolGateway
from agent_platform.mcp.runner import ServerRunner
from agent_platform.mcp.sessions import (
    HttpMcpToolSource,
    McpServerConfig,
    SessionFactory,
    SessionFactoryBuilder,
    SessionPool,
)

logger = logging.getLogger(__name__)


def sdk_session_factory(transport: str) -> SessionFactory:
    """Build the official-SDK session factory for a transport kind.

    The returned context manager yields a READY (initialized) session and
    must be entered on the caller's loop; holders satisfy that by running
    it inside their single lifecycle task.
    """
    if transport == "streamable-http":
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]) -> Any:
            # Credential material rides on the HTTP client (spec section 9.2).
            client = create_mcp_http_client(headers=headers or None)
            async with streamable_http_client(url, http_client=client) as streams:
                session = ClientSession(streams[0], streams[1])
                async with session:
                    await session.initialize()
                    yield session
    elif transport == "sse":
        from mcp.client.session import ClientSession
        from mcp.client.sse import sse_client

        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]) -> Any:
            async with sse_client(url, headers=headers or None) as streams:
                session = ClientSession(streams[0], streams[1])
                async with session:
                    await session.initialize()
                    yield session
    else:
        raise ValueError(
            f"unsupported MCP transport: {transport!r} (expected 'streamable-http' or 'sse')"
        )
    return factory


def parse_mcp_servers_json(raw: str) -> list[McpServerConfig]:
    """Parse the MCP_SERVERS_JSON setting into server configs (spec section 4).

    ``streamable-http``/``sse`` entries need ``url``; ``stdio`` entries
    need ``command`` (argv list; ``env`` carries non-sensitive config
    only — credentials go by ``credential_ref``).
    """
    text = (raw or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("MCP_SERVERS_JSON must be a JSON array of server objects")
    servers: list[McpServerConfig] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError(f"MCP_SERVERS_JSON[{index}] must have a 'name'")
        transport = str(item.get("transport", "streamable-http"))
        url = str(item.get("url") or "")
        command = tuple(str(part) for part in item.get("command") or ())
        env = {str(key): str(value) for key, value in (item.get("env") or {}).items()}
        if transport in ("streamable-http", "sse"):
            if not url:
                raise ValueError(f"MCP_SERVERS_JSON[{index}]: {transport} servers need 'url'")
        elif transport == "stdio":
            if not command:
                raise ValueError(f"MCP_SERVERS_JSON[{index}]: stdio servers need 'command'")
        else:
            raise ValueError(
                f"MCP_SERVERS_JSON[{index}]: unsupported transport {transport!r} "
                "(expected 'streamable-http', 'sse' or 'stdio')"
            )
        servers.append(
            McpServerConfig(
                name=str(item["name"]),
                url=url,
                transport=transport,
                credential_ref=item.get("credential_ref"),
                side_effects=str(item.get("side_effects", "mutating")),
                command=command,
                env=env,
            )
        )
    return servers


def runner_session_factory(
    runner: ServerRunner,
    config: McpServerConfig,
    material: CredentialMaterial,
    builder: SessionFactoryBuilder,
) -> SessionFactory:
    """Session factory for a runner-hosted stdio server (spec section 6.2).

    The endpoint is resolved FRESH on every (re)connect — runner
    workloads may be restarted or swept, and endpoints change across
    recovery (sandbox-spec.md section 9.2). The bridge inside the sandbox
    serves Streamable HTTP, so the platform connects with the plain
    streamable-http transport. Credentials ride on the workload
    environment (spec section 6.4), not on HTTP headers.
    """
    inner = builder("streamable-http")

    @asynccontextmanager
    async def factory(_url: str, headers: dict[str, str]) -> Any:
        endpoint = runner.ensure_running(config, material)
        async with inner(endpoint, headers) as session:
            yield session

    return factory


def build_mcp_gateway(
    servers: list[McpServerConfig],
    *,
    resolver: CredentialResolver | None = None,
    allowed_tools: set[str] | None = None,
    audit_sink: AuditSink | None = None,
    pool: SessionPool | None = None,
    runner: ServerRunner | None = None,
    factory_builder: SessionFactoryBuilder | None = None,
) -> McpToolGateway:
    """Compose the gateway from server configs: resolve credentials, share
    holders per (server, credential fingerprint), register namespaced.

    stdio servers require ``runner`` (spec section 6.2) — the platform
    never spawns stdio processes locally. Gateway shutdown closes the
    sources, internally-owned pools and the runner's workloads.
    """
    internal_pool = pool is None
    pool = pool or SessionPool(builder=factory_builder or sdk_session_factory)
    builder = factory_builder or sdk_session_factory
    gateway = McpToolGateway(allowed_tools=allowed_tools, audit_sink=audit_sink)
    for config in servers:
        material = (
            resolver.resolve(config.credential_ref)
            if config.credential_ref and resolver is not None
            else CredentialMaterial()
        )
        if config.transport == "stdio":
            if runner is None:
                raise ValueError(
                    f"stdio server {config.name!r} requires a ServerRunner "
                    "(no sandbox runner configured)"
                )
            source = HttpMcpToolSource(
                name=config.name,
                url=f"runner://{config.name}",
                session_factory=runner_session_factory(runner, config, material, builder),
            )
        else:
            source = pool.get(config, material)
        gateway.register_server(source, side_effects=config.side_effects)

    if internal_pool:
        gateway.on_close(pool.close_all)
    if runner is not None:
        gateway.on_close(runner.close)
    return gateway
