"""The per-evidence endpoints: the record, the bytes, and the stored analysis.

Three routes are covered, and what is asserted about each is mostly what it
refuses to do:

* ``GET /api/evidence/{id}`` reports which analysis stages exist. A stage that
  never ran must come back as ``null`` -- not as a zero score, not as an
  empty-but-successful result, and not by being silently omitted from the map.
* ``GET /api/evidence/{id}/file`` returns the stored bytes unchanged, so the
  SHA-256 recorded at ingestion still describes what the client received.
* ``GET /api/evidence/{id}/analysis`` reads stored payloads only. Opening an
  analysis page must not be able to change the case file.

The integrity check is the one thing here that writes: ``verify=true`` re-hashes
the bytes on disk, and because re-verifying an exhibit is a deliberate act, it
has to appear in the audit chain.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.evidence import FILE_MISSING_DETAIL, STAGES
from app.services import audit as audit_service
from app.services import storage
from tests.helpers import jpeg_bytes, wav_bytes


def _upload(client: TestClient, data: bytes, name: str, mime: str = "image/jpeg"):
    res = client.post("/api/cases/upload", files={"file": (name, data, mime)})
    assert res.status_code in (200, 201), res.text
    return res.json()


def _audit_events(client: TestClient, case_id: str) -> list[dict[str, Any]]:
    res = client.get(f"/api/cases/{case_id}/audit")
    assert res.status_code == 200
    return res.json()["events"]


def _stored_file(evidence: dict[str, Any], settings):
    return storage.absolute_path(evidence["stored_path"], settings)


# --------------------------------------------------------------------------- #
# The record, and the stages that have not run
# --------------------------------------------------------------------------- #
def test_evidence_detail_reports_unrun_stages_as_null(client: TestClient) -> None:
    """An un-analysed item has six null stages, not six zero scores."""
    uploaded = _upload(client, jpeg_bytes(seed=41001), "detail-fresh.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]

    res = client.get(f"/api/evidence/{evidence_id}")
    assert res.status_code == 200
    data = res.json()

    assert data["evidence"]["evidence_id"] == evidence_id
    assert data["case"]["case_id"] == uploaded["case"]["case_id"]

    # Every stage is present as a key so the client can tell "not run" from
    # "unknown stage"; every value is null because nothing has run.
    assert set(data["stages"]) == set(STAGES)
    assert all(value is None for value in data["stages"].values())
    assert data["stages_stored"] == []
    assert sorted(data["stages_not_run"]) == sorted(STAGES)

    assert data["verdict"] is None  # No verdict exists, so none is invented.
    assert data["near_duplicate_candidate_count"] == 0
    assert data["report_count"] == 0
    assert data["analysis_url"] == f"/api/evidence/{evidence_id}/analysis"
    assert data["file_url"] == f"/api/evidence/{evidence_id}/file"
    assert data["run_analysis_url"] == (
        f"/api/cases/{uploaded['case']['case_id']}/analyse"
    )
    assert any("have not run" in note for note in data["notes"])


def test_unverified_integrity_is_null_not_false(client: TestClient) -> None:
    """Not having checked is a third state, distinct from pass and from fail."""
    uploaded = _upload(client, jpeg_bytes(seed=41002), "detail-unverified.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]

    file_state = client.get(f"/api/evidence/{evidence_id}").json()["file"]
    assert file_state["available"] is True
    assert file_state["stored_sha256"] == uploaded["evidence"]["sha256"]
    assert file_state["integrity_verified"] is None
    assert file_state["recomputed_sha256"] is None
    assert file_state["verified_at"] is None
    assert "verify=true" in file_state["detail"]


def test_evidence_detail_runs_nothing_and_records_nothing(client: TestClient) -> None:
    """Reading the record must not analyse the media or write to the chain."""
    uploaded = _upload(client, jpeg_bytes(seed=41003), "detail-readonly.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]

    before = len(_audit_events(client, case_id))
    for _ in range(3):
        assert client.get(f"/api/evidence/{evidence_id}").status_code == 200
    after = _audit_events(client, case_id)

    assert len(after) == before
    # Still nothing stored: three page loads produced no analysis.
    assert client.get(f"/api/evidence/{evidence_id}").json()["stages_stored"] == []


def test_verify_rehashes_the_bytes_and_audits_the_check(client: TestClient) -> None:
    uploaded = _upload(client, jpeg_bytes(seed=41004), "detail-verify.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]

    before = [
        e
        for e in _audit_events(client, case_id)
        if e["event"] == audit_service.EVENT_HASH_CALCULATED
    ]

    data = client.get(f"/api/evidence/{evidence_id}", params={"verify": "true"}).json()
    file_state = data["file"]
    assert file_state["integrity_verified"] is True
    assert file_state["recomputed_sha256"] == uploaded["evidence"]["sha256"]
    assert file_state["verified_at"] is not None

    after = [
        e
        for e in _audit_events(client, case_id)
        if e["event"] == audit_service.EVENT_HASH_CALCULATED
    ]
    assert len(after) == len(before) + 1
    recorded = after[-1]["details"]
    assert recorded["evidence_id"] == evidence_id
    assert recorded["purpose"] == "integrity re-verification"
    assert recorded["matches"] is True
    assert recorded["recomputed_sha256"] == uploaded["evidence"]["sha256"]


def test_changed_bytes_are_reported_as_a_storage_finding(
    client: TestClient, settings
) -> None:
    """A digest mismatch is a fact about storage, not a verdict on the media."""
    original = jpeg_bytes(seed=41005)
    uploaded = _upload(client, original, "detail-tampered.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    path = _stored_file(uploaded["evidence"], settings)

    path.write_bytes(jpeg_bytes(seed=41006))
    try:
        data = client.get(
            f"/api/evidence/{evidence_id}", params={"verify": "true"}
        ).json()
    finally:
        # Restore the exhibit: the rest of the session shares this database.
        path.write_bytes(original)

    file_state = data["file"]
    assert file_state["integrity_verified"] is False
    assert file_state["recomputed_sha256"] != file_state["stored_sha256"]
    assert "do NOT hash" in file_state["detail"]
    assert any("no longer match" in note for note in data["notes"])
    # A failed integrity check is not a verdict about the content.
    assert data["verdict"] is None

    restored = client.get(
        f"/api/evidence/{evidence_id}", params={"verify": "true"}
    ).json()
    assert restored["file"]["integrity_verified"] is True


def test_audio_evidence_is_outside_perceptual_retrieval_not_absent_from_it(
    client: TestClient,
) -> None:
    uploaded = _upload(client, wav_bytes(), "detail-audio.wav", "audio/wav")
    evidence = uploaded["evidence"]
    assert evidence["media_type"] == "audio"
    assert evidence["phash"] is None  # Null, not a placeholder hash.

    data = client.get(f"/api/evidence/{evidence['evidence_id']}").json()
    assert data["near_duplicate_candidate_count"] == 0
    assert any("No perceptual hash" in note for note in data["notes"])
    assert data["near_duplicate_interpretation"]


def test_unknown_evidence_id_is_a_404_in_the_error_envelope(
    client: TestClient,
) -> None:
    res = client.get("/api/evidence/no-such-evidence-id")
    assert res.status_code == 404
    body = res.json()
    assert "error" in body and "detail" not in body
    assert body["error"]["type"] == "http_error"
    assert body["request_id"]


# --------------------------------------------------------------------------- #
# The bytes
# --------------------------------------------------------------------------- #
def test_evidence_file_serves_the_stored_bytes_unchanged(client: TestClient) -> None:
    """Byte-for-byte: what the examiner sees is what was hashed."""
    payload = jpeg_bytes(seed=41007)
    uploaded = _upload(client, payload, "bytes-inline.jpg")
    evidence = uploaded["evidence"]

    res = client.get(f"/api/evidence/{evidence['evidence_id']}/file")
    assert res.status_code == 200
    assert res.content == payload
    assert res.headers["x-pramaan-evidence-sha256"] == evidence["sha256"]
    assert res.headers["x-pramaan-evidence-id"] == evidence["evidence_id"]
    assert res.headers["content-type"].startswith(evidence["mime_type"])
    assert res.headers["content-disposition"].startswith("inline")
    # Case material may be cached by the examiner's browser, never by a proxy.
    assert "private" in res.headers["cache-control"]


def test_download_switches_the_disposition_only(client: TestClient) -> None:
    payload = jpeg_bytes(seed=41008)
    uploaded = _upload(client, payload, "bytes-download.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]

    res = client.get(
        f"/api/evidence/{evidence_id}/file", params={"download": "true"}
    )
    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.content == payload  # Same bytes, different header.


def test_previewing_evidence_is_not_written_to_the_audit_chain(
    client: TestClient,
) -> None:
    """One request per thumbnail would bury the record that matters."""
    uploaded = _upload(client, jpeg_bytes(seed=41009), "bytes-quiet.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]

    before = len(_audit_events(client, case_id))
    for _ in range(5):
        assert client.get(f"/api/evidence/{evidence_id}/file").status_code == 200
    assert len(_audit_events(client, case_id)) == before


def test_missing_file_is_a_404_naming_a_storage_problem(
    client: TestClient, settings
) -> None:
    original = jpeg_bytes(seed=41010)
    uploaded = _upload(client, original, "bytes-missing.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    path = _stored_file(uploaded["evidence"], settings)

    path.unlink()
    try:
        res = client.get(f"/api/evidence/{evidence_id}/file")
        assert res.status_code == 404
        assert FILE_MISSING_DETAIL in res.json()["error"]["message"]

        detail = client.get(f"/api/evidence/{evidence_id}").json()
        assert detail["file"]["available"] is False
        assert detail["file"]["url"] is None
        assert detail["file"]["detail"] == FILE_MISSING_DETAIL
        # The recorded digest is still reported: it is what the file should hash to.
        assert detail["file"]["stored_sha256"] == uploaded["evidence"]["sha256"]
        # And a missing file is never reported as a failed integrity check.
        assert detail["file"]["integrity_verified"] is None
    finally:
        path.write_bytes(original)


# --------------------------------------------------------------------------- #
# The stored analysis
# --------------------------------------------------------------------------- #
def test_evidence_analysis_lists_missing_stages_instead_of_guessing(
    client: TestClient,
) -> None:
    uploaded = _upload(client, jpeg_bytes(seed=41011), "analysis-empty.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]

    data = client.get(f"/api/evidence/{evidence_id}/analysis").json()
    assert data["source"] == "stored"
    assert data["stored_stages"] == []
    assert sorted(data["missing_stages"]) == sorted(STAGES)
    assert all(value is None for value in data["stages"].values())
    assert data["verdict"] is None
    assert data["signals"] == []
    assert data["run_analysis_url"] == f"/api/cases/{uploaded['case']['case_id']}/analyse"
    assert any("does not run analysis" in note for note in data["notes"])
    assert any("absence of analysis" in note for note in data["notes"])
    # The detector's capability is reported even when nothing has run, because it
    # is what bounds any analysis that later does.
    assert "available" in data["detector_capability"]


def test_evidence_analysis_returns_what_fusion_stored(client: TestClient) -> None:
    uploaded = _upload(client, jpeg_bytes(seed=41012), "analysis-full.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]

    ran = client.post(f"/api/cases/{case_id}/analyse")
    assert ran.status_code == 200, ran.text
    fused = next(
        item
        for item in ran.json()["verdicts"]
        if item["evidence_id"] == evidence_id
    )

    data = client.get(f"/api/evidence/{evidence_id}/analysis").json()
    assert data["missing_stages"] == []
    assert sorted(data["stored_stages"]) == sorted(STAGES)
    assert data["verdict"]["verdict"] == fused["verdict"]
    assert data["verdict"]["manipulation_score"] == fused["manipulation_score"]
    assert data["signals"] and len(data["signals"]) == fused["signals_total"]
    # Signal statuses are reported per signal; an excluded one is excluded, not
    # scored zero.
    for signal in data["signals"]:
        if signal["included"] is False:
            assert signal["contribution"] is None
            assert signal["effective_weight"] == 0.0

    for kind in STAGES:
        stage = data["stages"][kind]
        assert stage["kind"] == kind
        assert stage["created_at"]
        assert isinstance(stage["payload"], dict)


def test_reading_the_analysis_does_not_re_run_it(client: TestClient) -> None:
    """The stored payload is returned unchanged, and the chain does not grow."""
    uploaded = _upload(client, jpeg_bytes(seed=41013), "analysis-stable.jpg")
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]
    assert client.post(f"/api/cases/{case_id}/analyse").status_code == 200

    first = client.get(f"/api/evidence/{evidence_id}/analysis").json()
    before = len(_audit_events(client, case_id))
    second = client.get(f"/api/evidence/{evidence_id}/analysis").json()

    assert len(_audit_events(client, case_id)) == before
    assert first["stages"] == second["stages"]
    assert first["verdict"] == second["verdict"]
