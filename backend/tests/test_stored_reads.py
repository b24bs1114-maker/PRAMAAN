"""The read-only views of results that a POST produces.

Three GET routes were added because a page load must never be able to change the
case file: ``GET /api/cases/{id}/verdict`` and ``GET /api/cases/{id}/matches``
read back what fusion and retrieval stored, and ``GET /api/cases/{id}/provenance``
mirrors the metadata route's cache-then-reuse behaviour.

The distinction these tests exist to protect is between *nothing found* and
*never looked*. An empty candidate list from a search that ran is a finding; an
empty candidate list from a search that never ran is not. The same holds for a
verdict: an item with no stored fusion result has no verdict, which is not an
inconclusive one.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.services import audit as audit_service
from app.services import fusion as fusion_service
from app.services import provenance as provenance_service
from tests.helpers import jpeg_bytes, wav_bytes


def _case_with(client: TestClient, *files: tuple[bytes, str, str]) -> dict[str, Any]:
    """Create one case holding the given files, and return its upload payloads."""
    case_id: str | None = None
    evidence: list[dict[str, Any]] = []
    for data, name, mime in files:
        payload = {"case_id": case_id} if case_id else {}
        res = client.post(
            "/api/cases/upload", files={"file": (name, data, mime)}, data=payload
        )
        assert res.status_code in (200, 201), res.text
        body = res.json()
        case_id = body["case"]["case_id"]
        evidence.append(body["evidence"])
    assert case_id is not None
    return {"case_id": case_id, "evidence": evidence}


def _events(client: TestClient, case_id: str, event: str | None = None) -> list[dict]:
    res = client.get(f"/api/cases/{case_id}/audit")
    assert res.status_code == 200
    rows = res.json()["events"]
    return rows if event is None else [r for r in rows if r["event"] == event]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_absent_manifest_is_the_normal_case_not_a_finding(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42001), "prov-plain.jpg", "image/jpeg"))

    res = client.get(f"/api/cases/{case['case_id']}/provenance")
    assert res.status_code == 200
    data = res.json()

    assert data["count"] == 1
    item = data["items"][0]
    assert item["evidence_id"] == case["evidence"][0]["evidence_id"]
    assert item["provenance"]["state"] == provenance_service.STATE_ABSENT

    # All four states are spelled out, and the one that is routinely misread says
    # so explicitly.
    assert set(data["state_definitions"]) == {
        provenance_service.STATE_VERIFIED,
        provenance_service.STATE_INVALID,
        provenance_service.STATE_UNVERIFIED,
        provenance_service.STATE_ABSENT,
    }
    absent_definition = data["state_definitions"][provenance_service.STATE_ABSENT]
    assert "NOT an indicator of manipulation" in absent_definition
    assert any("not a finding about them" in note for note in data["notes"])


def test_provenance_reports_what_this_deployment_can_validate(
    client: TestClient,
) -> None:
    """Without the c2pa library, a manifest is never reported as verified."""
    case = _case_with(client, (jpeg_bytes(seed=42002), "prov-validator.jpg", "image/jpeg"))
    data = client.get(f"/api/cases/{case['case_id']}/provenance").json()

    validator = data["validator"]
    assert "signature_validation_available" in validator
    if not validator["signature_validation_available"]:
        assert any(
            note == provenance_service.CONTAINER_SCAN_ONLY_DETAIL
            for note in data["notes"]
        )
        for item in data["items"]:
            assert item["provenance"]["state"] != provenance_service.STATE_VERIFIED


def test_provenance_is_inspected_once_then_reused(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42003), "prov-cache.jpg", "image/jpeg"))
    case_id = case["case_id"]

    first = client.get(f"/api/cases/{case_id}/provenance").json()["items"][0]
    assert first["provenance"]["cached"] is False
    inspected_at = first["provenance"]["inspected_at"]

    second = client.get(f"/api/cases/{case_id}/provenance").json()["items"][0]
    assert second["provenance"]["cached"] is True
    assert second["provenance"]["inspected_at"] == inspected_at

    # refresh=true re-inspects, which is the only way to get a new reading.
    refreshed = client.get(
        f"/api/cases/{case_id}/provenance", params={"refresh": "true"}
    ).json()["items"][0]
    assert refreshed["provenance"]["cached"] is False


# --------------------------------------------------------------------------- #
# Stored near-duplicate candidates
# --------------------------------------------------------------------------- #
def test_never_searched_is_not_reported_as_nothing_found(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42004), "match-unsearched.jpg", "image/jpeg"))
    case_id = case["case_id"]

    data = client.get(f"/api/cases/{case_id}/matches").json()
    assert data["source"] == "stored"
    assert data["searched"] is False
    assert data["searched_at"] is None
    assert data["total_candidates"] == 0
    # The evidence item still appears as a query: omitting it would be
    # indistinguishable from an item that was searched and matched nothing.
    assert len(data["queries"]) == 1
    assert data["queries"][0]["evidence_id"] == case["evidence"][0]["evidence_id"]
    assert data["queries"][0]["candidates"] == []
    assert any("has never been run" in note for note in data["notes"])
    assert data["run_matches_url"] == f"/api/cases/{case_id}/matches"


def test_stored_matches_agree_exactly_with_the_search_that_stored_them(
    client: TestClient,
) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42005), "match-stored.jpg", "image/jpeg"))
    case_id = case["case_id"]

    searched = client.post(f"/api/cases/{case_id}/matches")
    assert searched.status_code == 200, searched.text
    live = searched.json()

    stored = client.get(f"/api/cases/{case_id}/matches").json()
    assert stored["searched"] is True
    assert stored["searched_at"] is not None
    assert stored["total_candidates"] == live["total_candidates"]
    assert stored["interpretation"] == live["interpretation"]
    assert len(stored["queries"]) == len(live["queries"])

    for stored_query, live_query in zip(stored["queries"], live["queries"], strict=True):
        assert stored_query["evidence_id"] == live_query["evidence_id"]
        assert stored_query["phash"] == live_query["phash"]
        assert len(stored_query["candidates"]) == len(live_query["candidates"])
        for kept, found in zip(
            stored_query["candidates"], live_query["candidates"], strict=True
        ):
            # Distances, similarity, band and rank are read back, not recomputed.
            assert kept["evidence_id"] == found["evidence_id"]
            assert kept["distance"] == found["distance"]
            assert kept["similarity"] == found["similarity"]
            assert kept["phash_distance"] == found["phash_distance"]
            assert kept["dhash_distance"] == found["dhash_distance"]
            assert kept["confidence_band"] == found["confidence_band"]
            assert kept["rank"] == found["rank"]

    if not stored["total_candidates"]:
        assert any("returned no candidates" in note for note in stored["notes"])


def test_reading_stored_matches_does_not_search(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42006), "match-readonly.jpg", "image/jpeg"))
    case_id = case["case_id"]
    assert client.post(f"/api/cases/{case_id}/matches").status_code == 200

    before = _events(client, case_id, audit_service.EVENT_MATCH_SEARCHED)
    first = client.get(f"/api/cases/{case_id}/matches").json()
    second = client.get(f"/api/cases/{case_id}/matches").json()
    after = _events(client, case_id, audit_service.EVENT_MATCH_SEARCHED)

    assert len(after) == len(before)
    assert first["searched_at"] == second["searched_at"]
    assert first["queries"] == second["queries"]


def test_items_outside_perceptual_retrieval_say_so(client: TestClient) -> None:
    case = _case_with(client, (wav_bytes(), "match-audio.wav", "audio/wav"))
    case_id = case["case_id"]
    assert client.post(f"/api/cases/{case_id}/matches").status_code == 200

    query = client.get(f"/api/cases/{case_id}/matches").json()["queries"][0]
    assert query["phash"] is None
    assert query["candidates"] == []
    assert any("No perceptual hash" in note for note in query["notes"])


# --------------------------------------------------------------------------- #
# Stored verdicts
# --------------------------------------------------------------------------- #
def test_unanalysed_evidence_is_pending_not_inconclusive(client: TestClient) -> None:
    case = _case_with(
        client,
        (jpeg_bytes(seed=42007), "verdict-pending-a.jpg", "image/jpeg"),
        (jpeg_bytes(seed=42008), "verdict-pending-b.jpg", "image/jpeg"),
    )
    case_id = case["case_id"]

    data = client.get(f"/api/cases/{case_id}/verdict").json()
    assert data["source"] == "stored"
    assert data["evidence_count"] == 2
    assert data["analysed_count"] == 0
    assert data["count"] == 0
    assert data["items"] == []  # No placeholder verdicts.
    assert len(data["pending_evidence"]) == 2
    for pending in data["pending_evidence"]:
        assert pending["reason"] == "No fused verdict is stored for this item."
        assert pending["sha256"]
    assert any("no stored verdict" in note for note in data["notes"])
    assert data["run_verdict_url"] == f"/api/cases/{case_id}/verdict"
    # The vocabulary is still published, so a client never has to guess it.
    assert data["method"] == fusion_service.FUSION_METHOD
    assert data["caveat"] == fusion_service.CAVEAT


def test_stored_verdict_is_the_verdict_fusion_wrote(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42009), "verdict-stored.jpg", "image/jpeg"))
    case_id = case["case_id"]

    fused = client.post(f"/api/cases/{case_id}/verdict")
    assert fused.status_code == 200, fused.text
    live = fused.json()["items"][0]

    data = client.get(f"/api/cases/{case_id}/verdict").json()
    assert data["evidence_count"] == 1
    assert data["analysed_count"] == 1
    assert data["pending_evidence"] == []
    stored = data["items"][0]

    assert stored["cached"] is True
    assert stored["verdict"] == live["verdict"]
    assert stored["manipulation_score"] == live["manipulation_score"]
    assert stored["signal_coverage"] == live["signal_coverage"]
    assert stored["signals_available"] == live["signals_available"]
    assert stored["fusion_version"] == live["fusion_version"]
    assert stored["fused_at"]
    # The arithmetic is carried through, so the score can be recomputed by hand.
    if stored["manipulation_score"] is not None:
        assert stored["arithmetic"] == live["arithmetic"]


def test_reading_the_verdict_does_not_re_fuse(client: TestClient) -> None:
    case = _case_with(client, (jpeg_bytes(seed=42010), "verdict-readonly.jpg", "image/jpeg"))
    case_id = case["case_id"]
    assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200

    before = _events(client, case_id, audit_service.EVENT_VERDICT_GENERATED)
    first = client.get(f"/api/cases/{case_id}/verdict").json()
    second = client.get(f"/api/cases/{case_id}/verdict").json()
    after = _events(client, case_id, audit_service.EVENT_VERDICT_GENERATED)

    # Fusion audits every verdict it generates: an unchanged count is proof that
    # none was generated by these reads.
    assert len(after) == len(before)
    assert first["items"] == second["items"]
    assert first["items"][0]["fused_at"] == second["items"][0]["fused_at"]


def test_partially_analysed_case_separates_the_two_groups(client: TestClient) -> None:
    """One analysed item and one not: neither is described as the other."""
    case = _case_with(
        client, (jpeg_bytes(seed=42011), "verdict-mixed-a.jpg", "image/jpeg")
    )
    case_id = case["case_id"]
    assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200

    added = client.post(
        "/api/cases/upload",
        files={"file": ("verdict-mixed-b.jpg", jpeg_bytes(seed=42012), "image/jpeg")},
        data={"case_id": case_id},
    )
    assert added.status_code == 201
    late_id = added.json()["evidence"]["evidence_id"]

    data = client.get(f"/api/cases/{case_id}/verdict").json()
    assert data["evidence_count"] == 2
    assert data["analysed_count"] == 1
    assert [p["evidence_id"] for p in data["pending_evidence"]] == [late_id]
    assert data["items"][0]["evidence_id"] != late_id
