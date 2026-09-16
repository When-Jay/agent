"""Observability: consumes Runtime events without becoming an execution dependency."""

from agent_platform.observability.langfuse_adapter import (
    LangfuseEventSubscriber,
    attach_langfuse_subscriber,
)

__all__ = ["LangfuseEventSubscriber", "attach_langfuse_subscriber"]
