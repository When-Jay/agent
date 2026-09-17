"""Composition root for the MCP gateway (mcp-gateway-spec.md sections 3, 4).

The official MCP SDK is a runtime dependency used ONLY here: this module
builds ready sessions (transport + ClientSession + initialize) that
session holders enter in one task on their own loop (task-bound anyio
scopes — mcp/sessions.py stays SDK-free by design).
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from agent_platform.config import Settings
from agent_platform.mcp.audit import AuditSink
from agent_platform.mcp.credentials import CredentialMaterial, CredentialResolver
from agent_platform.mcp.gateway import McpToolGateway
from agent_platform.mcp.sessions import McpServerConfig, SessionFactory, SessionPool

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
    """Parse the MCP_SERVERS_JSON setting into server configs (spec section 4)."""
    text = (raw or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("MCP_SERVERS_JSON must be a JSON array of server objects")
    servers: list[McpServerConfig] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("name") or not item.get("url"):
            raise ValueError(f"MCP_SERVERS_JSON[{index}] must have 'name' and 'url'")
        servers.append(
            McpServerConfig(
                name=str(item["name"]),
                url=str(item["url"]),
                transport=str(item.get("transport", "streamable-http")),
                credential_ref=item.get("credential_ref"),
                side_effects=str(item.get("side_effects", "mutating")),
            )
        )
    return servers


def build_mcp_gateway(
    servers: list[McpServerConfig],
    *,
    resolver: CredentialResolver | None = None,
    allowed_tools: set[str] | None = None,
    audit_sink: AuditSink | None = None,
    pool: SessionPool | None = None,
) -> McpToolGateway:
    """Compose the gateway from server configs: resolve credentials, share
    holders per (server, credential fingerprint), register namespaced."""
    pool = pool or SessionPool(builder=sdk_session_factory)
    gateway = McpToolGateway(allowed_tools=allowed_tools, audit_sink=audit_sink)
    for config in servers:
        material = (
            resolver.resolve(config.credential_ref)
            if config.credential_ref and resolver is not None
            else CredentialMaterial()
        )
        source = pool.get(config, material)
        gateway.register_server(source, side_effects=config.side_effects)
    return gateway
