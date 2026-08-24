"""Metadata extraction tests (TASK 3).

The binding requirement under test: **missing metadata must never be reported as
manipulation**. Stripped files are the normal case for redistributed media.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, mp4_bytes, png_bytes


def _upload(client: TestClient, data: bytes, name: str, mime: str = "image/jpeg"):
    return client.post("/api/cases/upload", files={"file": (name, data, mime)})


def test_metadata_endpoint_reports_container_details(client: TestClient) -> None:
    case_id = _upload(client, jpeg_bytes(seed=101), "plain.jpg").json()["case"][
        "case_id"
    ]
    response = client.get(f"/api/cases/{case_id}/metadata")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["case_id"] == case_id
    assert body["count"] == 1
    payload = body["items"][0]["metadata"]
    container = payload["container"]
    assert container["format"] == "JPEG"
    assert container["width"] == 320
    assert container["height"] == 240
    assert container["mode"] == "RGB"
    assert payload["media_type"] == "image"
    assert payload["jpeg"]["quantization_tables"] >= 1
    assert payload["jpeg"]["estimated_quality"] is not None


def test_exif_tags_are_extracted(client: TestClient) -> None:
    body = _upload(client, jpeg_with_exif_bytes(seed=102), "exif.jpg").json()
    case_id = body["case"]["case_id"]

    payload = client.get(f"/api/cases/{case_id}/metadata").json()["items"][0][
        "metadata"
    ]

    assert payload["exif"]["present"] is True
    assert payload["exif"]["tag_count"] > 0
    assert payload["camera"]["present"] is True
    assert payload["camera"]["make"] == "PRAMAAN"
    assert payload["camera"]["model"] == "TestCam 1"
    assert payload["software"]["present"] is True
    assert payload["software"]["editor_hint"] == "photoshop"
    assert payload["software"]["generative_hint"] is None
    assert payload["timestamps"]["exif_datetime_original"] == "2026-01-15T09:30:00Z"
    assert payload["presence_summary"]["stripped_likely"] is False
    assert "exif" in payload["presence_summary"]["fields_present"]


def test_missing_metadata_is_not_reported_as_manipulation(client: TestClient) -> None:
    """A stripped PNG must be described neutrally, with no manipulation claim."""
    body = _upload(client, png_bytes(seed=103), "stripped.png", "image/png").json()
    case_id = body["case"]["case_id"]

    response = client.get(f"/api/cases/{case_id}/metadata").json()
    payload = response["items"][0]["metadata"]

    assert payload["exif"]["present"] is False
    summary = payload["presence_summary"]
    assert summary["stripped_likely"] is True
    assert "exif" in summary["fields_missing"]
    assert "camera_information" in summary["fields_missing"]

    # No verdict, score or manipulation claim may appear in a metadata payload.
    assert "verdict" not in payload
    assert "manipulation_score" not in payload
    note = (payload["interpretation"] + summary["note"]).lower()
    assert "not evidence of manipulation" in note
    assert "not an indicator of manipulation" in note
    assert "not evidence of manipulation" in response["interpretation"].lower()


def test_video_container_metadata_is_extracted(client: TestClient) -> None:
    body = _upload(client, mp4_bytes(), "clip.mp4", "video/mp4").json()
    assert body["evidence"]["media_type"] == "video"
    case_id = body["case"]["case_id"]

    payload = client.get(f"/api/cases/{case_id}/metadata").json()["items"][0][
        "metadata"
    ]

    assert payload["media_type"] == "video"
    assert payload["container"]["major_brand"] == "isom"
    assert payload["container"]["duration_seconds"] == 5.0
    assert payload["container"]["width"] == 640
    assert payload["container"]["height"] == 360
    assert payload["timestamps"]["container_created_at"] == "2026-01-15T09:30:00Z"
    assert "container-level only" in payload["limitations"]


def test_metadata_is_persisted_and_cached(client: TestClient) -> None:
    body = _upload(client, jpeg_with_exif_bytes(seed=104), "cached.jpg").json()
    case_id = body["case"]["case_id"]
    evidence_id = body["evidence"]["evidence_id"]

    first = client.get(f"/api/cases/{case_id}/metadata").json()["items"][0]["metadata"]
    second = client.get(f"/api/cases/{case_id}/metadata").json()["items"][0]["metadata"]
    refreshed = client.get(
        f"/api/cases/{case_id}/metadata", params={"refresh": "true"}
    ).json()["items"][0]["metadata"]

    assert first["cached"] is False
    assert second["cached"] is True
    assert refreshed["cached"] is False
    assert second["exif"]["tags"] == first["exif"]["tags"]

    from app.models import KIND_METADATA, AnalysisResult, get_session_factory

    session = get_session_factory()()
    try:
        rows = (
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == KIND_METADATA,
            )
            .all()
        )
        # Re-extraction replaces the current row rather than accumulating rows.
        assert len(rows) == 1
        assert rows[0].status == "OK"
        assert rows[0].payload["container"]["format"] == "JPEG"
    finally:
        session.close()


def test_metadata_extraction_is_audited(client: TestClient) -> None:
    case_id = _upload(client, jpeg_bytes(seed=105), "audit-meta.jpg").json()["case"][
        "case_id"
    ]
    client.get(f"/api/cases/{case_id}/metadata")

    from app.models import AuditLog, get_session_factory

    session = get_session_factory()()
    try:
        events = [
            row.event
            for row in session.query(AuditLog).filter(AuditLog.case_id == case_id).all()
        ]
    finally:
        session.close()

    assert "METADATA_EXTRACTED" in events


def test_metadata_for_unknown_case_returns_404(client: TestClient) -> None:
    assert client.get(f"/api/cases/{uuid.uuid4()}/metadata").status_code == 404
