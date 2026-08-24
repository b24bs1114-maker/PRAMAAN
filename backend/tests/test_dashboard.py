"""Unit and integration tests for the dashboard summary API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import jpeg_bytes


def test_dashboard_summary_endpoint(settings):
    app = create_app(settings)
    client = TestClient(app)
    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    data = res.json()
    assert "active_investigations_count" in data
    assert "evidence_items_count" in data
    assert "flagged_media_count" in data
    assert "pending_review_count" in data
    assert data["system_status"] == "online"
    assert isinstance(data["recent_investigations"], list)
    assert isinstance(data["recent_evidence"], list)


def test_dashboard_summary_with_case_and_evidence(settings):
    app = create_app(settings)
    client = TestClient(app)

    unique_bytes = jpeg_bytes(seed=99999)
    upload_res = client.post(
        "/api/cases/upload",
        files={"file": ("test_dashboard.jpg", unique_bytes, "image/jpeg")},
        data={"title": "Dashboard Test Case", "examiner": "Officer Singh"},
    )
    assert upload_res.status_code in (200, 201)

    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    data = res.json()
    assert data["active_investigations_count"] >= 1
    assert data["evidence_items_count"] >= 1
    assert len(data["recent_investigations"]) >= 1
