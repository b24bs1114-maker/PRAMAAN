"""Database and case CRUD tests (TASK 2)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from tests.helpers import jpeg_bytes

EXPECTED_TABLES = {
    "cases",
    "evidence",
    "analysis_results",
    "matches",
    "timeline_events",
    "audit_log",
}


def test_schema_contains_all_tables(client: TestClient) -> None:
    from app.models import get_engine

    names = set(inspect(get_engine()).get_table_names())
    assert EXPECTED_TABLES <= names, EXPECTED_TABLES - names


def test_expected_indexes_exist(client: TestClient) -> None:
    from app.models import get_engine

    inspector = inspect(get_engine())
    evidence_indexed = {
        column
        for index in inspector.get_indexes("evidence")
        for column in index["column_names"]
    }
    assert {"sha256", "case_id", "phash", "source_id"} <= evidence_indexed


def test_foreign_keys_are_enforced(client: TestClient) -> None:
    """SQLite disables FK enforcement by default; the PRAGMA must be applied."""
    from app.models import get_session_factory

    session = get_session_factory()()
    try:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        session.close()


def test_upload_is_persisted_to_the_database(client: TestClient) -> None:
    body = client.post(
        "/api/cases/upload",
        files={"file": ("persist.jpg", jpeg_bytes(seed=71), "image/jpeg")},
    ).json()

    from app.models import Case, Evidence, get_session_factory

    session = get_session_factory()()
    try:
        case = session.get(Case, body["case"]["case_id"])
        evidence = session.get(Evidence, body["evidence"]["evidence_id"])
        assert case is not None
        assert evidence is not None
        assert evidence.case_id == case.id
        assert evidence.sha256 == body["evidence"]["sha256"]
        assert evidence.phash is not None
    finally:
        session.close()


def test_audit_rows_written_for_ingestion(client: TestClient) -> None:
    body = client.post(
        "/api/cases/upload",
        files={"file": ("audited.jpg", jpeg_bytes(seed=72), "image/jpeg")},
    ).json()
    case_id = body["case"]["case_id"]

    from app.models import AuditLog, get_session_factory

    session = get_session_factory()()
    try:
        events = [
            row.event
            for row in session.execute(
                select(AuditLog).where(AuditLog.case_id == case_id)
            ).scalars()
        ]
    finally:
        session.close()

    assert "CASE_CREATED" in events
    assert "EVIDENCE_INGESTED" in events
    assert "HASH_CALCULATED" in events
    assert "PERCEPTUAL_HASH_CALCULATED" in events


def test_case_crud_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/cases/upload",
        files={"file": ("crud.jpg", jpeg_bytes(seed=73), "image/jpeg")},
        data={"title": "Original title", "examiner": "A. Examiner"},
    ).json()
    case_id = created["case"]["case_id"]
    evidence_id = created["evidence"]["evidence_id"]

    # READ (one)
    read = client.get(f"/api/cases/{case_id}")
    assert read.status_code == 200
    assert read.json()["title"] == "Original title"
    assert read.json()["evidence_count"] == 1

    # READ (list)
    listed = client.get("/api/cases")
    assert listed.status_code == 200
    assert listed.json()["count"] >= 1
    assert any(c["case_id"] == case_id for c in listed.json()["cases"])

    # READ (evidence)
    evidence = client.get(f"/api/cases/{case_id}/evidence")
    assert evidence.status_code == 200
    assert [e["evidence_id"] for e in evidence.json()["evidence"]] == [evidence_id]

    # UPDATE
    patched = client.patch(
        f"/api/cases/{case_id}",
        data={"title": "Updated title", "case_status": "under_review"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Updated title"
    assert patched.json()["status"] == "under_review"

    # DELETE (cascades to evidence)
    deleted = client.delete(f"/api/cases/{case_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_evidence_count"] == 1
    assert client.get(f"/api/cases/{case_id}").status_code == 404

    from app.models import Evidence, get_session_factory

    session = get_session_factory()()
    try:
        assert session.get(Evidence, evidence_id) is None
    finally:
        session.close()


def test_unknown_case_returns_404(client: TestClient) -> None:
    assert client.get(f"/api/cases/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize("path", ["/api/cases/{}/evidence"])
def test_unknown_case_subresources_return_404(client: TestClient, path: str) -> None:
    assert client.get(path.format(uuid.uuid4())).status_code == 404
