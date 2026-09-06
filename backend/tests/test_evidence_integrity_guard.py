"""Pre-analysis integrity verification: the digest is checked, not assumed.

SHA-256 is taken once, at intake. From that moment the digest is the only thing
tying an analysis result back to the exhibit that was booked in -- so it has to
be re-checked against the bytes every time they are read, not treated as a
permanent property of the record.

The settings page claims this control. These tests are what make the claim true:
that a tampered file is refused rather than analysed, that the refusal reaches
the examiner as a conflict rather than a crash, that it is written to the audit
chain and survives the request being rolled back, and that a *missing* file is
still reported as missing rather than being recast as tampering.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from app.services import audit as audit_service
from app.services import pipeline, storage
from tests.helpers import jpeg_bytes


def _upload(client: TestClient, data: bytes, name: str) -> dict[str, Any]:
    res = client.post(
        "/api/cases/upload", files={"file": (name, data, "image/jpeg")}
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _audit_events(client: TestClient, case_id: str) -> list[dict[str, Any]]:
    res = client.get(f"/api/cases/{case_id}/audit")
    assert res.status_code == 200
    return res.json()["events"]


@contextmanager
def _tampered(evidence: dict[str, Any], settings, replacement: bytes):
    """Swap the stored bytes, then put the original back.

    The database is shared across the session, so an exhibit left altered would
    fail every later test that touches it -- and would do so with this exact
    error, which is a confusing way to learn that a fixture leaked.
    """
    path = storage.absolute_path(evidence["stored_path"], settings)
    original = path.read_bytes()
    path.write_bytes(replacement)
    try:
        yield path
    finally:
        path.write_bytes(original)


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #
def test_analysis_is_refused_when_the_stored_bytes_changed(
    client: TestClient, settings
) -> None:
    """A verdict computed over swapped bytes would name the wrong exhibit."""
    uploaded = _upload(client, jpeg_bytes(seed=52001), "guard-verdict.jpg")
    case_id = uploaded["case"]["case_id"]

    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52002)):
        res = client.post(f"/api/cases/{case_id}/verdict")

    assert res.status_code == 409, res.text
    body = res.json()
    # The standard envelope, not FastAPI's `detail`, and not a 500 with an
    # opaque "an internal error occurred".
    assert "detail" not in body
    assert body["error"]["type"] == "evidence_integrity_mismatch"
    assert "no longer hashes" in body["error"]["message"]
    assert body["request_id"]

    reported = body["error"]["details"][0]
    assert reported["evidence_id"] == uploaded["evidence"]["evidence_id"]
    assert reported["recorded_sha256"] == uploaded["evidence"]["sha256"]
    assert reported["recomputed_sha256"] != reported["recorded_sha256"]


def test_the_refusal_survives_the_request_being_rolled_back(
    client: TestClient, settings
) -> None:
    """The most serious finding this deployment can make must not vanish.

    ``get_db`` rolls the session back on any exception, so recording the mismatch
    and then raising would erase it. The check commits before it raises.
    """
    uploaded = _upload(client, jpeg_bytes(seed=52003), "guard-audited.jpg")
    case_id = uploaded["case"]["case_id"]
    evidence_id = uploaded["evidence"]["evidence_id"]

    before = len(_audit_events(client, case_id))
    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52004)):
        assert client.post(f"/api/cases/{case_id}/verdict").status_code == 409

    events = _audit_events(client, case_id)
    assert len(events) == before + 1

    recorded = events[-1]
    assert recorded["event"] == audit_service.EVENT_HASH_CALCULATED
    details = recorded["details"]
    assert details["evidence_id"] == evidence_id
    assert details["purpose"] == "pre-analysis integrity verification"
    assert details["matches"] is False
    assert details["outcome"] == "ANALYSIS_REFUSED"
    assert details["recorded_sha256"] == uploaded["evidence"]["sha256"]
    assert details["recomputed_sha256"] != details["recorded_sha256"]

    # The append is a real chain row, not an orphan.
    assert client.post("/api/audit/verify").json()["valid"] is True


def test_nothing_is_stored_for_an_item_that_was_refused(
    client: TestClient, settings
) -> None:
    """Refusing means no result, not a result marked as suspect."""
    uploaded = _upload(client, jpeg_bytes(seed=52005), "guard-nostore.jpg")
    case_id = uploaded["case"]["case_id"]
    evidence_id = uploaded["evidence"]["evidence_id"]

    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52006)):
        assert client.post(f"/api/cases/{case_id}/verdict").status_code == 409

    detail = client.get(f"/api/evidence/{evidence_id}").json()
    assert detail["verdict"] is None
    assert detail["stages_stored"] == []
    assert all(stage is None for stage in detail["stages"].values())


def test_the_full_case_pipeline_and_reporting_refuse_too(
    client: TestClient, settings
) -> None:
    """Every route that reaches the stage runners inherits the guard.

    Reporting matters most: a PDF is the artefact that leaves the building, and
    it must not be generated over bytes that changed after intake.
    """
    uploaded = _upload(client, jpeg_bytes(seed=52007), "guard-routes.jpg")
    case_id = uploaded["case"]["case_id"]

    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52008)):
        assert client.post(f"/api/cases/{case_id}/analyse").status_code == 409
        assert client.post(f"/api/cases/{case_id}/report").status_code == 409

    # And the same routes work once the exhibit is itself again.
    assert client.post(f"/api/cases/{case_id}/analyse").status_code == 200


def test_the_ad_hoc_detector_route_refuses_registered_evidence_that_changed(
    client: TestClient, settings
) -> None:
    """``POST /api/detect`` is neural analysis on a booked-in exhibit."""
    uploaded = _upload(client, jpeg_bytes(seed=52009), "guard-detect.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]

    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52010)):
        res = client.post("/api/detect", data={"evidence_id": evidence_id})

    assert res.status_code == 409, res.text
    assert res.json()["error"]["type"] == "evidence_integrity_mismatch"


# --------------------------------------------------------------------------- #
# What the guard must NOT do
# --------------------------------------------------------------------------- #
def test_untouched_evidence_analyses_normally(client: TestClient) -> None:
    """The check is a guard, not a gate: the ordinary path is unchanged."""
    uploaded = _upload(client, jpeg_bytes(seed=52011), "guard-clean.jpg")
    case_id = uploaded["case"]["case_id"]

    res = client.post(f"/api/cases/{case_id}/verdict")
    assert res.status_code == 200, res.text
    assert res.json()["items"][0]["sha256"] == uploaded["evidence"]["sha256"]

    # A clean read appends no integrity row: only a mismatch is newsworthy, and
    # one HASH_CALCULATED entry per stage per analysis would bury the chain.
    integrity_rows = [
        e
        for e in _audit_events(client, case_id)
        if e["event"] == audit_service.EVENT_HASH_CALCULATED
        and e["details"].get("purpose") == "pre-analysis integrity verification"
    ]
    assert integrity_rows == []


def test_a_missing_file_is_still_missing_not_tampered(
    client: TestClient, settings
) -> None:
    """Absent is a different fact from altered, and stays reported as absent."""
    uploaded = _upload(client, jpeg_bytes(seed=52012), "guard-absent.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    path = storage.absolute_path(uploaded["evidence"]["stored_path"], settings)
    original = path.read_bytes()

    path.unlink()
    try:
        res = client.post("/api/detect", data={"evidence_id": evidence_id})
        assert res.status_code == 409
        # The pre-existing "registered but not on this host" message, not an
        # integrity mismatch invented out of an empty directory entry.
        assert res.json()["error"]["type"] == "http_error"
        assert "missing from this host" in res.json()["error"]["message"]
    finally:
        path.write_bytes(original)


def test_a_stored_result_is_reused_without_rehashing_and_the_text_says_so(
    client: TestClient, settings
) -> None:
    """The boundary of the guard, pinned so the description cannot outgrow it.

    The check fires when a stage reads bytes. A stage that reuses a stored
    result reads nothing, so a case analysed before its file was altered still
    serves the verdict it computed at the time -- which is correct (the result
    does describe the bytes it saw) but is not the same as the file being
    re-checked. The capability text discloses exactly that, and this test fails
    if the text ever stops disclosing it.
    """
    uploaded = _upload(client, jpeg_bytes(seed=52013), "guard-cached.jpg")
    case_id = uploaded["case"]["case_id"]
    assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200

    with _tampered(uploaded["evidence"], settings, jpeg_bytes(seed=52014)):
        cached = client.get(f"/api/cases/{case_id}/verdict")
        assert cached.status_code == 200
        assert cached.json()["items"][0]["sha256"] == uploaded["evidence"]["sha256"]

        # Re-running is a read, and a read is refused.
        rerun = client.post(
            f"/api/cases/{case_id}/analyse", params={"refresh": "true"}
        )
        assert rerun.status_code == 409

    detail = client.get("/api/system/status").json()["capabilities"][
        "integrity_verification"
    ]["detail"]
    assert "reuses a stored result reads no file" in detail
    assert "verify=true" in detail


# --------------------------------------------------------------------------- #
# What the settings page is allowed to say
# --------------------------------------------------------------------------- #
def test_status_reports_the_guarantee_the_pipeline_actually_enforces(
    client: TestClient,
) -> None:
    """The settings row is read from here, so here must match the code.

    Before this control existed the screen printed "Pre-Inference Digest
    Verification -- ACTIVE" as a hardcoded string, describing a safeguard the
    pipeline did not have. The block below is the pipeline's own declaration:
    the constants live next to the check, and the tests above are what make them
    true.
    """
    res = client.get("/api/system/status")
    assert res.status_code == 200
    block = res.json()["capabilities"]["integrity_verification"]

    assert block["algorithm"] == pipeline.INTEGRITY_ALGORITHM == "SHA-256"
    assert block["on_mismatch"] == pipeline.INTEGRITY_ON_MISMATCH == "REFUSE"
    assert block["detail"] == pipeline.INTEGRITY_DETAIL
    assert "metadata" in block["scope"]
    assert "AI detection" in block["scope"]
