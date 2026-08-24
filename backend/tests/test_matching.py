"""Near-duplicate matching tests (TASK 7).

Two things are under test: that retrieval actually finds transformed copies, and
that the response never overstates what a small Hamming distance means.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from PIL import Image

from tests.helpers import encode, jpeg_bytes, make_image


def _ingest_corpus(
    client: TestClient,
    data: bytes,
    name: str,
    **provenance: object,
) -> dict:
    """Add an image to the searchable corpus (no case)."""
    form = {k: str(v) for k, v in provenance.items() if v is not None}
    response = client.post(
        "/api/index/ingest",
        files={"file": (name, data, "image/jpeg")},
        data=form,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _upload_case(client: TestClient, data: bytes, name: str) -> dict:
    response = client.post(
        "/api/cases/upload", files={"file": (name, data, "image/jpeg")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_matches_find_transformed_copy(client: TestClient) -> None:
    """A resized, recompressed copy in the corpus must surface as candidate #1."""
    original = make_image(480, 360, seed=501)
    variant = original.resize((288, 216), Image.Resampling.LANCZOS)

    corpus = _ingest_corpus(
        client,
        encode(variant, "JPEG", quality=70),
        "match-variant.jpg",
        source_id="lineage-501",
        platform="demo_platform_a",
        generation=1,
        observed_at="2026-01-06T08:00:00Z",
        transformation="resize+jpeg70",
        is_synthetic=True,
    )
    _ingest_corpus(client, jpeg_bytes(seed=888), "match-unrelated.jpg")

    uploaded = _upload_case(client, encode(original, "JPEG"), "match-query.jpg")
    case_id = uploaded["case"]["case_id"]
    query_id = uploaded["evidence"]["evidence_id"]

    response = client.post(f"/api/cases/{case_id}/matches")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["case_id"] == case_id
    assert len(body["queries"]) == 1
    query = body["queries"][0]
    assert query["evidence_id"] == query_id
    assert query["index_backend"]
    assert query["indexed_count"] >= 2

    candidates = query["candidates"]
    assert candidates, "no near-duplicate candidates returned"
    top = candidates[0]
    assert top["evidence_id"] == corpus["evidence"]["evidence_id"]
    assert top["rank"] == 1
    assert top["distance"] <= query["max_distance"]
    assert top["phash_distance"] == top["distance"]
    assert top["dhash_distance"] is not None
    assert abs(top["similarity"] - (1 - top["distance"] / 64)) < 1e-9
    # Provenance recorded at ingestion is echoed back, not inferred.
    assert top["source_id"] == "lineage-501"
    assert top["platform"] == "demo_platform_a"
    assert top["generation"] == 1
    assert top["timestamp"] == "2026-01-06T08:00:00Z"
    assert top["is_synthetic"] is True
    assert top["confidence_band"] in ("strong_candidate", "possible_candidate")

    # Ranking is monotonic in distance.
    distances = [c["distance"] for c in candidates]
    assert distances == sorted(distances)
    assert [c["rank"] for c in candidates] == list(range(1, len(candidates) + 1))


def test_query_evidence_excludes_itself(client: TestClient) -> None:
    uploaded = _upload_case(client, jpeg_bytes(seed=502), "self-exclude.jpg")
    case_id = uploaded["case"]["case_id"]
    evidence_id = uploaded["evidence"]["evidence_id"]
    client.post(f"/api/index/add/{evidence_id}")

    query = client.post(f"/api/cases/{case_id}/matches").json()["queries"][0]
    assert all(c["evidence_id"] != evidence_id for c in query["candidates"])


def test_response_uses_candidate_language_only(client: TestClient) -> None:
    """No part of the response may assert identity or a manipulation verdict."""
    uploaded = _upload_case(client, jpeg_bytes(seed=503), "wording.jpg")
    case_id = uploaded["case"]["case_id"]
    body = client.post(f"/api/cases/{case_id}/matches").json()

    interpretation = body["interpretation"]
    assert "NEAR-DUPLICATE CANDIDATES" in interpretation
    lowered = interpretation.lower()
    assert "not a definitive identification" in lowered
    assert "does not by itself establish" in lowered
    assert "real-world origin" in lowered

    assert "verdict" not in body
    for query in body["queries"]:
        assert "verdict" not in query
        assert "manipulation_score" not in query
        for candidate in query["candidates"]:
            assert "verdict" not in candidate
            assert candidate["confidence_band"].endswith("candidate")

    # Thresholds are disclosed as prototype values, not validated science.
    basis = body["thresholds"]["basis"].lower()
    assert "not validated" in basis


def test_matches_are_persisted_and_replaced(client: TestClient) -> None:
    original = make_image(400, 300, seed=504)
    _ingest_corpus(
        client,
        encode(original.resize((200, 150), Image.Resampling.LANCZOS), "JPEG"),
        "persisted-variant.jpg",
        source_id="lineage-504",
    )
    uploaded = _upload_case(client, encode(original, "JPEG"), "persisted-query.jpg")
    case_id = uploaded["case"]["case_id"]
    evidence_id = uploaded["evidence"]["evidence_id"]

    first = client.post(f"/api/cases/{case_id}/matches").json()
    expected = len(first["queries"][0]["candidates"])
    assert expected >= 1

    from app.models import Match, get_session_factory

    session = get_session_factory()()
    try:
        rows = (
            session.query(Match).filter(Match.query_evidence_id == evidence_id).all()
        )
        assert len(rows) == expected
        assert {r.case_id for r in rows} == {case_id}
        assert all(r.method for r in rows)
        assert sorted(r.rank for r in rows) == list(range(1, expected + 1))
        session.close()

        # Re-running replaces rather than duplicating (unique pair constraint).
        client.post(f"/api/cases/{case_id}/matches")

        session = get_session_factory()()
        again = (
            session.query(Match).filter(Match.query_evidence_id == evidence_id).all()
        )
        assert len(again) == expected
    finally:
        session.close()


def test_max_distance_cutoff_is_honoured(client: TestClient) -> None:
    original = make_image(360, 280, seed=505)
    _ingest_corpus(
        client,
        encode(original.resize((180, 140), Image.Resampling.LANCZOS), "JPEG"),
        "cutoff-variant.jpg",
    )
    uploaded = _upload_case(client, encode(original, "JPEG"), "cutoff-query.jpg")
    case_id = uploaded["case"]["case_id"]

    wide = client.post(
        f"/api/cases/{case_id}/matches", params={"max_distance": 32}
    ).json()["queries"][0]
    tight = client.post(
        f"/api/cases/{case_id}/matches", params={"max_distance": 0}
    ).json()["queries"][0]

    assert all(c["distance"] <= 32 for c in wide["candidates"])
    assert all(c["distance"] == 0 for c in tight["candidates"])
    assert len(tight["candidates"]) <= len(wide["candidates"])


def test_top_k_limits_returned_candidates(client: TestClient) -> None:
    original = make_image(320, 240, seed=506)
    for i, quality in enumerate((85, 70, 55, 40)):
        _ingest_corpus(
            client, encode(original, "JPEG", quality=quality), f"topk-{i}.jpg"
        )
    uploaded = _upload_case(client, encode(original, "PNG"), "topk-query.png")
    case_id = uploaded["case"]["case_id"]

    limited = client.post(
        f"/api/cases/{case_id}/matches", params={"top_k": 2, "max_distance": 32}
    ).json()["queries"][0]
    assert limited["top_k"] == 2
    assert len(limited["candidates"]) <= 2


def test_video_evidence_is_reported_not_silently_skipped(client: TestClient) -> None:
    from tests.helpers import mp4_bytes

    response = client.post(
        "/api/cases/upload", files={"file": ("no-phash.mp4", mp4_bytes(), "video/mp4")}
    )
    case_id = response.json()["case"]["case_id"]

    query = client.post(f"/api/cases/{case_id}/matches").json()["queries"][0]
    assert query["candidates"] == []
    assert query["phash"] is None
    assert any("no retrieval was performed" in note for note in query["notes"])


def test_matching_is_audited(client: TestClient) -> None:
    case_id = _upload_case(client, jpeg_bytes(seed=507), "audit-match.jpg")["case"][
        "case_id"
    ]
    client.post(f"/api/cases/{case_id}/matches")

    from app.models import AuditLog, get_session_factory

    session = get_session_factory()()
    try:
        rows = session.query(AuditLog).filter(AuditLog.case_id == case_id).all()
        events = [row.event for row in rows]
        searched = next(r for r in rows if r.event == "MATCH_SEARCHED")
    finally:
        session.close()

    assert "MATCH_SEARCHED" in events
    assert searched.details["queries"] == 1
    assert "hamming" in searched.details["method"]


def test_empty_index_yields_a_note_not_a_conclusion(client: TestClient, settings) -> None:
    """An empty index must be reported as "nothing to compare", never as clean."""
    from app.services.index import get_index

    uploaded = _upload_case(client, jpeg_bytes(seed=508), "empty-index.jpg")
    case_id = uploaded["case"]["case_id"]
    try:
        get_index(settings).clear()

        query = client.post(f"/api/cases/{case_id}/matches").json()["queries"][0]
        assert query["indexed_count"] == 0
        assert query["candidates"] == []
        note = " ".join(query["notes"]).lower()
        assert "index is empty" in note
        assert "not evidence of anything" in note
    finally:
        # The index is derived state: restore it for the remaining tests.
        assert client.post("/api/index/rebuild").status_code == 200


def test_matches_for_unknown_case_returns_404(client: TestClient) -> None:
    assert client.post(f"/api/cases/{uuid.uuid4()}/matches").status_code == 404
