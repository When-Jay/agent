"""API hardening tests (plan-productionization task 3).

Covers body limit (413), rate limit (429 + path exemption + fail-open)
and CORS headers. Rate limiters are injected explicitly so no test ever
touches Redis.
"""

from typing import Any

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.api.middleware import InMemoryRateLimiter, RateLimiter
from agent_platform.config import Settings


class _ExplodingLimiter:
    """Limiter whose backend always raises (simulates a Redis outage)."""

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        raise RuntimeError("limiter backend down")


def test_rate_limit_returns_429_and_exempt_paths_pass():
    settings = Settings(rate_limit_rpm=1)
    client = TestClient(create_app(settings, rate_limiter=InMemoryRateLimiter()))

    first = client.get("/api/v1/runs")
    second = client.get("/api/v1/runs")
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"

    # Health/metrics probes are exempt and never throttled.
    for _ in range(3):
        assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/metrics").status_code == 200


def test_rate_limit_fails_open_when_backend_down():
    settings = Settings(rate_limit_rpm=1)
    client = TestClient(create_app(settings, rate_limiter=_ExplodingLimiter()))

    for _ in range(3):
        assert client.get("/api/v1/runs").status_code == 200


def test_body_limit_returns_413_and_accepts_small_bodies():
    settings = Settings(max_request_body_mb=0.001)  # ~1048 bytes
    client = TestClient(create_app(settings, rate_limiter=InMemoryRateLimiter()))

    oversized = client.post("/api/v1/applications", json={"name": "x" * 2048})
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == "request body too large"

    accepted = client.post("/api/v1/applications", json={"name": "small"})
    assert accepted.status_code == 201


def test_cors_headers_for_configured_origins_only():
    settings = Settings(cors_allow_origins="https://console.example.com, http://localhost:3000")
    client = TestClient(create_app(settings, rate_limiter=InMemoryRateLimiter()))

    allowed = client.get("/api/v1/health", headers={"Origin": "https://console.example.com"})
    assert allowed.headers["access-control-allow-origin"] == "https://console.example.com"

    denied = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in denied.headers

    # Preflight is answered by the outer CORS layer before limits apply.
    preflight = client.options(
        "/api/v1/runs",
        headers={
            "Origin": "https://console.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://console.example.com"


def test_cors_disabled_when_no_origins_configured():
    client = TestClient(create_app(rate_limiter=InMemoryRateLimiter()))

    response = client.get("/api/v1/health", headers={"Origin": "https://console.example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
