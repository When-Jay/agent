"""Observability: consumes Runtime events without becoming an execution dependency."""

from agent_platform.observability.langfuse_adapter import (
    LangfuseEventSubscriber,
    attach_langfuse_subscriber,
)
from agent_platform.observability.metrics import (
    MetricsCollector,
    attach_metrics_collector,
    get_metrics_collector,
)

__all__ = [
    "LangfuseEventSubscriber",
    "MetricsCollector",
    "attach_langfuse_subscriber",
    "attach_metrics_collector",
    "get_metrics_collector",
]
