"""API hardening middleware (plan-productionization task 3).

Two pure-ASGI middlewares, registered inside CORS so preflight requests
are answered before any limit applies:

* BodyLimitMiddleware rejects request bodies over the configured size
  with 413 (Content-Length short-circuit; chunked bodies are buffered).
* RateLimitMiddleware applies a per client-IP + method sliding-window
  limit and answers 429 once exceeded. Redis-backed in production, with
  an in-memory implementation for tests and single-process use. Limiter
  backend failures fail open: availability beats throttling accuracy.
"""

import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Protocol
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY_PREFIX = "agent_platform:ratelimit"
RATE_LIMIT_WINDOW_SECONDS = 60
# Probes and scraper endpoints are never throttled: orchestrator health
# checks and Prometheus scraping must survive a client-side burst.
_DEFAULT_EXEMPT_PATHS = ("/api/v1/health", "/api/v1/metrics")


class RateLimiter(Protocol):
    """Sliding-window limiter contract: True = request allowed."""

    def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


class InMemoryRateLimiter:
    """Per-key sliding window, process-local (tests / single process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


class RedisRateLimiter:
    """Redis ZSET sliding window, one pipeline round trip per check.

    Members are unique per hit and scores are hit timestamps. The window
    is trimmed before counting and a TTL evicts idle keys. Redis failures
    are logged and fail open.
    """

    def __init__(self, redis_url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        try:
            pipeline = self._client.pipeline()
            pipeline.zremrangebyscore(key, "-inf", now - window_seconds)
            pipeline.zadd(key, {uuid4().hex: now})
            pipeline.zcard(key)
            pipeline.expire(key, window_seconds + 1)
            count = pipeline.execute()[2]
            return count <= limit
        except Exception:  # noqa: BLE001 - fail open, never break serving
            logger.warning("rate limiter backend unavailable; failing open", exc_info=True)
            return True


class BodyLimitMiddleware:
    """413 for request bodies over ``max_bytes``.

    Content-Length is rejected up front without reading the body. Chunked
    bodies (no Content-Length) are buffered up to the limit and replayed
    to the inner app; every platform endpoint is a buffered JSON request,
    so replay costs nothing in practice.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > self._max_bytes:
                    await _reject_body(scope, receive, send)
                    return
            except ValueError:
                pass  # malformed header; let the framework surface it
            await self._app(scope, receive, send)
            return
        buffered: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self._max_bytes:
                await _reject_body(scope, receive, send)
                return
            buffered.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(buffered)
        # Replay the buffered body exactly once, then hand control back to
        # the original receive. Streaming responses spawn a concurrent
        # listen_for_disconnect task that loops on receive() until it sees
        # http.disconnect -- a stub that answers forever would spin that
        # loop and never let the response finish.
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


async def _reject_body(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse({"detail": "request body too large"}, status_code=413)
    await response(scope, receive, send)


class RateLimitMiddleware:
    """Per client-IP + method sliding-window request limit (429 over limit).

    The client IP is the socket peer (``scope["client"]``); proxy headers
    such as X-Forwarded-For are intentionally not trusted here. Behind a
    reverse proxy all traffic would share the proxy IP -- deployments must
    terminate rate limiting at a trusted proxy layer instead (see
    .env.example).
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        *,
        requests_per_minute: int,
        exempt_paths: tuple[str, ...] = _DEFAULT_EXEMPT_PATHS,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._limit = requests_per_minute
        self._window_seconds = RATE_LIMIT_WINDOW_SECONDS
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt_paths):
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        key = f"{RATE_LIMIT_KEY_PREFIX}:{ip}:{scope.get('method', '')}"
        try:
            allowed = self._limiter.allow(key, self._limit, self._window_seconds)
        except Exception:  # noqa: BLE001 - fail open on injected limiter errors
            logger.warning("rate limiter raised; failing open", exc_info=True)
            allowed = True
        if not allowed:
            response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self._window_seconds)},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
