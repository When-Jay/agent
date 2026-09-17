"""Steering channel: per-run runtime control messages (budget-steering-spec.md §6).

Steering 是 Runtime Control（不是 Tool）：用户在 Agent 执行过程中发送
新的方向指引；SteeringMiddleware 在下一次 ``before_model`` 前一次性取出
pending 的 steering message 并作为 System Notice 注入 Model Context，
不直接终止正在执行的 Tool。

消费语义（spec section 36 第一版最低要求）：peek（注入前只读）+
acknowledge（模型调用成功后才移除，consume-after-successful-injection），
配合 ``SteeringMessage.id`` 幂等。Keys 以 run_id 隔离（spec section 37）。

第一版提供进程内实现（``InMemorySteeringChannel``）；跨进程部署使用
Redis List 实现（``runtime:steering:{run_id}``，dispatch 层提供）。
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True)
class SteeringMessage:
    """A single steering instruction queued for a run (spec section 5)."""

    id: str
    run_id: str
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SteeringChannel(ABC):
    """Per-run steering message queue with consume-once semantics."""

    @abstractmethod
    def publish(self, run_id: str, message: str) -> SteeringMessage:
        """Append one steering message for the given run."""

    @abstractmethod
    def pending(self, run_id: str) -> list[SteeringMessage]:
        """Peek at all pending messages for the run (oldest first, non-destructive)."""

    @abstractmethod
    def acknowledge(self, run_id: str, steering_ids: list[str]) -> None:
        """Remove consumed messages by id (consume-after-successful-injection)."""


class InMemorySteeringChannel(SteeringChannel):
    """Thread-safe process-local steering queue (single-process and tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, list[SteeringMessage]] = {}

    def publish(self, run_id: str, message: str) -> SteeringMessage:
        msg = SteeringMessage(id=str(uuid4()), run_id=run_id, message=message)
        with self._lock:
            self._queues.setdefault(run_id, []).append(msg)
        return msg

    def pending(self, run_id: str) -> list[SteeringMessage]:
        with self._lock:
            return list(self._queues.get(run_id, ()))

    def acknowledge(self, run_id: str, steering_ids: list[str]) -> None:
        if not steering_ids:
            return
        consumed = set(steering_ids)
        with self._lock:
            queue = self._queues.get(run_id)
            if queue is None:
                return
            self._queues[run_id] = [m for m in queue if m.id not in consumed]
