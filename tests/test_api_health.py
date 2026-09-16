from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def test_health_endpoint_reports_service_status():
    response = TestClient(create_app()).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "ai-runtime-platform",
        "status": "ok",
        "version": "0.1.0",
    }
