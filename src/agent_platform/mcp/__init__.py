"""MCP Gateway: unified tool access layer (03-mcp-gateway-architecture.md).

The gateway implements the platform ToolCapability interface; Agent
Runtime consumes it through the capability abstraction and never imports
this package directly.
"""

from agent_platform.errors import ToolPermissionDeniedError
from agent_platform.mcp.audit import AuditEntry, AuditSink, InMemoryAuditSink
from agent_platform.mcp.gateway import McpToolGateway, ToolRegistrationError, ToolTimeoutError
from agent_platform.mcp.source import (
    AsyncMcpSession,
    McpToolCallError,
    McpToolDescriptor,
    McpToolSource,
    SdkMcpToolSource,
)

__all__ = [
    "AsyncMcpSession",
    "AuditEntry",
    "AuditSink",
    "InMemoryAuditSink",
    "McpToolCallError",
    "McpToolDescriptor",
    "McpToolGateway",
    "McpToolSource",
    "SdkMcpToolSource",
    "ToolPermissionDeniedError",
    "ToolRegistrationError",
    "ToolTimeoutError",
]
