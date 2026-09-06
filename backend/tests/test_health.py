"""Tests for the PRAMAAN system endpoints.

The ``GET /health`` payload is a fixed contract consumed by monitoring and by
the frontend, so it is asserted exactly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
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


def test_health_allows_port_5174_cors_origins(client: TestClient) -> None:
    for origin in ("http://localhost:5174", "http://127.0.0.1:5174"):
        response = client.get("/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

        # Also verify preflight OPTIONS request
        preflight = client.options(
            "/api/auth/me",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-allow-origin") == origin



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


# --- Wildcard CORS origins -------------------------------------------------
# The three tests above pass on an exact-match entry in the default allow-list,
# so they never exercised the wildcard branch. These do: a preview deployment
# on a *generated* Vercel hostname is only allowed by the compiled regex.


def test_cors_wildcard_pattern_matches_generated_subdomain() -> None:
    settings = Settings(cors_allow_origins="https://*.vercel.app")
    pattern = settings.cors_origin_regex
    assert pattern is not None
    assert re.match(pattern, "https://pramaan-git-main-daksh.vercel.app")


def test_cors_wildcard_pattern_rejects_lookalike_host() -> None:
    settings = Settings(cors_allow_origins="https://*.vercel.app")
    pattern = settings.cors_origin_regex
    assert pattern is not None
    assert not re.match(pattern, "https://vercel.app.attacker.example")
    assert not re.match(pattern, "https://attacker.example/x.vercel.app")


def test_cors_wildcard_pattern_matches_any_localhost_port() -> None:
    settings = Settings(cors_allow_origins="http://localhost:*")
    pattern = settings.cors_origin_regex
    assert pattern is not None
    assert re.match(pattern, "http://localhost:5173")
    assert re.match(pattern, "http://localhost:50122")
    assert not re.match(pattern, "http://localhost.attacker.example:80")


def test_cors_origin_regex_is_none_without_wildcards() -> None:
    settings = Settings(cors_allow_origins="http://localhost:5173,http://127.0.0.1:3000")
    assert settings.cors_origin_regex is None


def test_cors_wildcard_origin_is_allowed_end_to_end() -> None:
    """A generated preview hostname gets an allow-origin header from the app."""
    origin = "https://pramaan-preview-abc123.vercel.app"
    with TestClient(app) as wildcard_client:
        response = wildcard_client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
