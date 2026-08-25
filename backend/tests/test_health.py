"""Tests for the PRAMAAN system endpoints.

The ``GET /health`` payload is a fixed contract consumed by monitoring and by
the frontend, so it is asserted exactly.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    # Context manager form runs startup/shutdown, exercising the lifespan.
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_200(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_health_body_is_exactly_status_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_returns_json(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["content-type"].startswith("application/json")


def test_health_carries_request_id_header(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_health_allows_configured_cors_origin(client: TestClient) -> None:
    origin = "http://localhost:5173"
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["type"] == "http_error"
    assert "request_id" in body


def test_cors_headers_on_health_get(client: TestClient) -> None:
    origin = "https://frontendeploy-sigma.vercel.app"
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_headers_on_dashboard_summary_get(client: TestClient) -> None:
    origin = "https://frontendeploy-sigma.vercel.app"
    response = client.get("/api/dashboard/summary", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_headers_on_health_options_preflight(client: TestClient) -> None:
    origin = "https://frontendeploy-sigma.vercel.app"
    response = client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
