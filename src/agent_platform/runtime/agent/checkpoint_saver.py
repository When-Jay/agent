"""Durable LangGraph checkpoint bridge (deepagents-runtime-spec.md section 8).

LangGraph checkpoint payloads (channel values, versions, pending writes)
are persisted to the platform database through dedicated tables — the
"platform bridge that persists equivalent data" alternative to the
LangGraph PostgreSQL checkpointer. The adapter receives the saver as its
`checkpointer` argument; `thread_id == run_id`, so resume reattaches to
the interrupted thread even from a fresh process.

The saver receives a SQLAlchemy `Engine` built by the composition root
(dispatch); this module imports sqlalchemy but never agent_platform.
infrastructure, keeping the agent -> infra boundary intact.
"""

import asyncio
from typing import Any, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

_checkpoints = Table(
    "langgraph_checkpoints", metadata,
    Column("thread_id", String(64), primary_key=True),
    Column("checkpoint_ns", Text, primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("parent_checkpoint_id", String(64), nullable=True),
    Column("type", String(64), nullable=False),
    Column("checkpoint", LargeBinary, nullable=False),
    Column("metadata_type", String(64), nullable=False),
    Column("metadata", LargeBinary, nullable=False),
)
_writes = Table(
    "langgraph_checkpoint_writes", metadata,
    Column("thread_id", String(64), primary_key=True),
    Column("checkpoint_ns", Text, primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("task_id", String(64), primary_key=True),
    Column("idx", Integer, primary_key=True),
    Column("channel", String(128), nullable=False),
    Column("type", String(64), nullable=False),
    Column("blob", LargeBinary, nullable=False),
)


class StoreCheckpointSaver(BaseCheckpointSaver):
    """Persists full LangGraph checkpoint payloads over a SQLAlchemy engine."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        metadata.create_all(self.engine)

    # -- helpers ---------------------------------------------------------------

    def _thread(self, config: dict[str, Any]) -> tuple[str, str]:
        configurable = config.get("configurable") or {}
        return configurable["thread_id"], configurable.get("checkpoint_ns", "")

    def _upsert(self, conn, table, values: dict[str, Any], keys: list[Any]) -> None:
        existing = conn.execute(select(1).select_from(table).where(*keys)).first()
        if existing:
            conn.execute(table.update().where(*keys).values(**values))
        else:
            conn.execute(insert(table).values(**values))

    # -- sync contract -----------------------------------------------------------

    def put(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict[str, Any]:
        thread_id, checkpoint_ns = self._thread(config)
        parent = (config.get("configurable") or {}).get("checkpoint_id")
        cp_type, cp_blob = self.serde.dumps_typed(checkpoint)
        md_type, md_blob = self.serde.dumps_typed(metadata)
        keys = [
            _checkpoints.c.thread_id == thread_id,
            _checkpoints.c.checkpoint_ns == checkpoint_ns,
            _checkpoints.c.checkpoint_id == checkpoint["id"],
        ]
        values = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": parent,
            "type": cp_type,
            "checkpoint": cp_blob,
            "metadata_type": md_type,
            "metadata": md_blob,
        }
        with self.engine.begin() as conn:
            self._upsert(conn, _checkpoints, values, keys)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns = self._thread(config)
        checkpoint_id = (config.get("configurable") or {}).get("checkpoint_id") or ""
        rows = []
        for idx, (channel, value) in enumerate(writes):
            w_type, w_blob = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "idx": idx,
                    "channel": channel,
                    "type": w_type,
                    "blob": w_blob,
                }
            )
        with self.engine.begin() as conn:
            conn.execute(
                delete(_writes).where(
                    _writes.c.thread_id == thread_id,
                    _writes.c.checkpoint_ns == checkpoint_ns,
                    _writes.c.checkpoint_id == checkpoint_id,
                    _writes.c.task_id == task_id,
                )
            )
            if rows:
                conn.execute(insert(_writes).values(rows))

    def get_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = self._thread(config)
        requested = (config.get("configurable") or {}).get("checkpoint_id")
        with self.engine.connect() as conn:
            query = select(_checkpoints).where(
                _checkpoints.c.thread_id == thread_id,
                _checkpoints.c.checkpoint_ns == checkpoint_ns,
            )
            if requested:
                query = query.where(_checkpoints.c.checkpoint_id == requested)
            row = (
                conn.execute(query.order_by(_checkpoints.c.checkpoint_id.desc()).limit(1))
                .mappings()
                .first()
            )
            if row is None:
                return None
            write_rows = conn.execute(
                select(_writes)
                .where(
                    _writes.c.thread_id == thread_id,
                    _writes.c.checkpoint_ns == checkpoint_ns,
                    _writes.c.checkpoint_id == row["checkpoint_id"],
                )
                .order_by(_writes.c.task_id, _writes.c.idx)
            ).mappings().all()

        checkpoint = self.serde.loads_typed((row["type"], row["checkpoint"]))
        checkpoint_metadata = self.serde.loads_typed((row["metadata_type"], row["metadata"]))
        pending_writes = [
            (
                wrow["task_id"],
                wrow["channel"],
                self.serde.loads_typed((wrow["type"], wrow["blob"])),
            )
            for wrow in write_rows
        ]
        base_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": row["checkpoint_id"],
            }
        }
        parent_config = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": row["parent_checkpoint_id"],
                }
            }
            if row["parent_checkpoint_id"]
            else None
        )
        return CheckpointTuple(
            config=base_config,
            checkpoint=checkpoint,
            metadata=checkpoint_metadata,
            parent_config=parent_config,
            pending_writes=pending_writes or None,
        )

    def list(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id, checkpoint_ns = self._thread(config)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_checkpoints.c.checkpoint_id)
                .where(
                    _checkpoints.c.thread_id == thread_id,
                    _checkpoints.c.checkpoint_ns == checkpoint_ns,
                )
                .order_by(_checkpoints.c.checkpoint_id.desc())
                .limit(limit)
            ).all()
        for (checkpoint_id,) in rows:
            query = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            }
            yield self.get_tuple(query)

    # -- async contract (agent execution is async; sync I/O off the loop) --------

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)
