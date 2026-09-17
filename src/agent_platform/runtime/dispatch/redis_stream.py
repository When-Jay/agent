"""Redis Stream event fanout (runtime-dispatch-spec.md section 6).

PostgreSQL stays the durable event log; the stream is a short-lived,
low-latency delivery channel only (dispatch-spec section 3). Workers
publish events through `RedisEventPublisher` (an EventBus subscriber);
the API streaming endpoint consumes them through a `StreamBackend`.

`InMemoryStreamBackend` covers single-process deployments and tests;
`RedisStreamBackend` wires a real Redis Stream (lazy `redis` import).
"""

import itertools
import json
import logging
import threading
import time
from typing import Protocol

from agent_platform.config import Settings
from agent_platform.runtime.core.events import RuntimeEvent, event_to_json

logger = logging.getLogger(__name__)

EVENT_STREAM_KEY = "agent_platform:events"
# Short-lived delivery channel: cap stream length so Redis memory stays
# bounded. PostgreSQL remains the source of truth for replay.
EVENT_STREAM_MAX_LEN = 10_000

_TERMINAL_EVENT_TYPES = {"RunCompleted", "RunFailed", "RunCancelled"}


class StreamBackend(Protocol):
    """Minimal stream contract shared by the in-memory and Redis backends."""

    def add(self, data: str) -> str:
        """Append one message; returns its stream entry id."""
        ...

    def read(self, after: str, block_ms: int, count: int) -> list[tuple[str, str]]:
        """Return up to `count` entries with id > `after`, blocking at most `block_ms`."""
        ...


class InMemoryStreamBackend:
    """Thread-safe in-memory stream; `read` blocks until data or timeout."""

    def __init__(self, max_len: int = EVENT_STREAM_MAX_LEN) -> None:
        self._max_len = max_len
        self._entries: list[tuple[str, str]] = []
        self._condition = threading.Condition()
        self._counter = itertools.count(1)

    def add(self, data: str) -> str:
        with self._condition:
            entry_id = f"{time.time_ns()}-{next(self._counter)}"
            self._entries.append((entry_id, data))
            if len(self._entries) > self._max_len:
                self._entries = self._entries[-self._max_len :]
            self._condition.notify_all()
            return entry_id

    def read(self, after: str, block_ms: int, count: int) -> list[tuple[str, str]]:
        deadline = time.monotonic() + block_ms / 1000
        with self._condition:
            while True:
                entries = [(eid, data) for eid, data in self._entries if eid > after]
                if entries:
                    return entries[:count]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)


class RedisStreamBackend:
    """Redis Stream adapter; the `redis` package is imported lazily."""

    def __init__(self, redis_url: str, key: str = EVENT_STREAM_KEY) -> None:
        import redis

        self._key = key
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def add(self, data: str) -> str:
        return self._client.xadd(
            self._key, {"data": data}, maxlen=EVENT_STREAM_MAX_LEN, approximate=True
        )

    def read(self, after: str, block_ms: int, count: int) -> list[tuple[str, str]]:
        cursor = after or "0-0"
        response = self._client.xread({self._key: cursor}, block=block_ms, count=count)
        entries: list[tuple[str, str]] = []
        for _key, stream_entries in response:
            for entry_id, fields in stream_entries:
                entries.append((entry_id, fields.get("data", "")))
        return entries


class RedisEventPublisher:
    """EventBus subscriber that fans events out to a stream backend.

    Publishing failures are logged and swallowed: the stream is an
    optimization and must never break runtime execution.
    """

    def __init__(self, backend: StreamBackend) -> None:
        self._backend = backend

    def __call__(self, event: RuntimeEvent) -> None:
        try:
            self._backend.add(event_to_json(event))
        except Exception:  # noqa: BLE001 - fanout must not break execution
            logger.warning("event fanout failed for %s", event.event_type, exc_info=True)


def is_terminal_event(data: str) -> bool:
    """True when a stream payload carries a run-terminal lifecycle event."""
    try:
        return json.loads(data).get("event_type") in _TERMINAL_EVENT_TYPES
    except (json.JSONDecodeError, AttributeError):
        return False


def create_stream_backend(settings: Settings) -> StreamBackend:
    """Compose the stream backend: Redis when fanout is enabled, else in-memory."""
    if settings.redis_event_fanout_enabled:
        return RedisStreamBackend(settings.redis_url)
    return InMemoryStreamBackend()
