"""Durable checkpoint payloads shared by Agent and Workflow runtimes.

Both runtimes execute on LangGraph threads keyed by `thread_id == run_id`;
this package provides the platform bridge that persists checkpoint data to
the platform database (runtime-spec.md section 9: one shared Checkpoint
concept, not per-runtime versions). It imports sqlalchemy and langgraph
but never agent_platform.infrastructure, keeping runtime -> infra
boundaries intact.
"""

from agent_platform.runtime.checkpointing.checkpoint_saver import StoreCheckpointSaver

__all__ = ["StoreCheckpointSaver"]
