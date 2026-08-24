"""TASK 14 -- direct corpus ingestion and index status.

``POST /api/index/ingest`` is the path that puts an image into the searchable
corpus without attaching it to a case. Two properties matter and are both checked
here: the item really becomes retrievable (a near-duplicate query finds it), and
the index counters reported by ``GET /api/index/status`` stay truthful --
``indexed_count`` matches the database, ``index_version`` only moves when the
index actually changes, and ``last_updated`` moves with it.

Rejections (unsupported bytes, empty file, duplicate bytes, video with no
perceptual hash) must leave the index exactly as it was.
"""

from __future__ import annotations

import hashlib

import pytest

from app.models import ROLE_CORPUS, get_session_factory
from app.services import audit, indexing
from app.services.hashing import hamming_distance
from tests.helpers import encode, jpeg_bytes, make_image, mp4_bytes, png_bytes

REQUIRED_STATUS_FIELDS = ("indexed_count", "last_updated", "index_version")


def _ingest(client, name: str, data: bytes, mime: str = "image/jpeg", **form):
    """POST one file to the corpus ingestion endpoint; returns the raw response."""
    return client.post(
        "/api/index/ingest",
        files={"file": (name, data, mime)},
        data={k: v for k, v in form.items() if v is not None},
    )


def _status(client) -> dict:
    response = client.get("/api/index/status")
    assert response.status_code == 200, response.text
    return response.json()


def _session():
    return get_session_factory()()


# --------------------------------------------------------------------------- #
# Status contract
# --------------------------------------------------------------------------- #
def test_status_reports_the_three_required_fields(client):
    body = _status(client)

    for field in REQUIRED_STATUS_FIELDS:
        assert field in body, f"missing required status field: {field}"

    assert isinstance(body["indexed_count"], int)
    assert body["indexed_count"] >= 0
    assert isinstance(body["index_version"], int)
    assert body["index_version"] >= 0
    # Never yet written is a legitimate state; anything else must be ISO-8601 UTC.
    assert body["last_updated"] is None or body["last_updated"].endswith("Z")


def test_status_indexed_count_matches_the_database(client):
    assert client.post("/api/index/rebuild").status_code == 200
    body = _status(client)

    session = _session()
    try:
        indexable = indexing.indexable_evidence(session)
    finally:
        session.close()

    # The index is derived state: after a rebuild it must hold exactly the rows
    # that carry a perceptual hash -- no more, no fewer.
    assert body["indexed_count"] == len(indexable)


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
def test_ingest_stores_hashes_and_indexes_one_image(client):
    before = _status(client)
    data = jpeg_bytes(seed=511)

    response = _ingest(client, "corpus-a.jpg", data)
    assert response.status_code == 201, response.text
    body = response.json()

    assert set(body) >= {"evidence", "duplicate", "warnings", "index", "searchable"}
    assert body["duplicate"] is False
    assert body["searchable"] is True

    evidence = body["evidence"]
    assert evidence["role"] == ROLE_CORPUS
    assert evidence["case_id"] is None, "corpus items belong to no case"
    assert evidence["sha256"] == hashlib.sha256(data).hexdigest()
    assert evidence["mime_type"] == "image/jpeg"
    assert evidence["media_type"] == "image"
    assert evidence["size_bytes"] == len(data)
    assert len(evidence["phash"]) == 16 and int(evidence["phash"], 16) >= 0
    assert evidence["indexed"] is True

    assert body["index"]["status"] == "added"
    assert body["index"]["added"] == 1
    assert body["index"]["indexed_count"] == before["indexed_count"] + 1
    assert body["index"]["index_version"] > before["index_version"]

    after = _status(client)
    assert after["indexed_count"] == body["index"]["indexed_count"]
    assert after["index_version"] == body["index"]["index_version"]


def test_ingested_item_is_immediately_retrievable_as_a_near_duplicate(client):
    original = make_image(400, 300, seed=512)
    corpus = _ingest(
        client, "corpus-original.jpg", encode(original, "JPEG", quality=92)
    ).json()["evidence"]

    # A recompressed copy of the same picture arrives as case evidence.
    variant = client.post(
        "/api/cases/upload",
        files={
            "file": (
                "case-copy.jpg",
                encode(original, "JPEG", quality=40),
                "image/jpeg",
            )
        },
    ).json()
    case_id = variant["case"]["case_id"]
    evidence_id = variant["evidence"]["evidence_id"]
    assert client.post(f"/api/index/add/{evidence_id}").status_code == 200

    matches = client.post(f"/api/cases/{case_id}/matches").json()
    query = next(q for q in matches["queries"] if q["evidence_id"] == evidence_id)
    hit = next(
        (c for c in query["candidates"] if c["evidence_id"] == corpus["evidence_id"]),
        None,
    )
    assert hit is not None, "the freshly ingested corpus item was not retrievable"

    # The reported distance is the real Hamming distance between the two hashes.
    assert hit["phash_distance"] == hamming_distance(
        variant["evidence"]["phash"], corpus["phash"]
    )
    assert hit["distance"] <= 64


def test_provenance_fields_are_stored_as_submitted(client):
    body = _ingest(
        client,
        "corpus-lineage.png",
        png_bytes(seed=513),
        "image/png",
        source_id="lineage-77",
        parent_id="parent-77",
        generation="3",
        platform="whatsapp",
        observed_at="2026-02-03T10:11:12Z",
        transformation="recompress+resize",
        is_synthetic="true",
    ).json()

    evidence = body["evidence"]
    assert evidence["source_id"] == "lineage-77"
    assert evidence["parent_id"] == "parent-77"
    assert evidence["generation"] == 3
    assert evidence["platform"] == "whatsapp"
    assert evidence["observed_at"] == "2026-02-03T10:11:12Z"
    assert evidence["transformation"] == "recompress+resize"
    assert evidence["is_synthetic"] is True


def test_items_are_not_marked_synthetic_unless_declared(client):
    body = _ingest(client, "corpus-real.jpg", jpeg_bytes(seed=514)).json()
    assert body["evidence"]["is_synthetic"] is False


def test_corpus_items_are_not_listed_as_case_evidence(client):
    corpus = _ingest(client, "corpus-unattached.jpg", jpeg_bytes(seed=515)).json()
    uploaded = client.post(
        "/api/cases/upload",
        files={"file": ("case-only.jpg", jpeg_bytes(seed=516), "image/jpeg")},
    ).json()
    case_id = uploaded["case"]["case_id"]

    listed = client.get(f"/api/cases/{case_id}/evidence").json()["evidence"]
    ids = {item["evidence_id"] for item in listed}
    assert uploaded["evidence"]["evidence_id"] in ids
    assert corpus["evidence"]["evidence_id"] not in ids


# --------------------------------------------------------------------------- #
# Counters move only when the index moves
# --------------------------------------------------------------------------- #
def test_last_updated_and_version_advance_together_on_change(client):
    before = _status(client)
    _ingest(client, "corpus-counter.jpg", jpeg_bytes(seed=517))
    after = _status(client)

    assert after["index_version"] > before["index_version"]
    assert after["last_updated"] is not None
    if before["last_updated"] is not None:
        # ISO-8601 UTC sorts lexicographically, so this is a real time comparison.
        assert after["last_updated"] >= before["last_updated"]


def test_reading_status_does_not_change_the_index(client):
    first = _status(client)
    second = _status(client)

    assert second["index_version"] == first["index_version"]
    assert second["indexed_count"] == first["indexed_count"]
    assert second["last_updated"] == first["last_updated"]


def test_duplicate_bytes_return_200_and_leave_the_index_alone(client):
    data = jpeg_bytes(seed=518)
    first = _ingest(client, "corpus-dupe.jpg", data)
    assert first.status_code == 201, first.text
    after_first = _status(client)

    second = _ingest(client, "corpus-dupe-again.jpg", data)
    # 201 Created would be a lie: nothing new was stored.
    assert second.status_code == 200, second.text
    body = second.json()

    assert body["duplicate"] is True
    assert any("SHA-256" in warning for warning in body["warnings"])
    assert body["evidence"]["evidence_id"] == first.json()["evidence"]["evidence_id"]
    assert body["index"]["status"] == "already_indexed"
    assert body["index"]["added"] == 0

    after_second = _status(client)
    assert after_second["indexed_count"] == after_first["indexed_count"]


def test_unsupported_bytes_are_rejected_without_touching_the_index(client):
    before = _status(client)
    response = _ingest(
        client, "notes.txt", b"this is not an image at all", "text/plain"
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["type"] == "http_error"
    assert "Unsupported or unrecognised file type" in error["message"]
    assert "Traceback" not in error["message"]

    after = _status(client)
    assert after["indexed_count"] == before["indexed_count"]
    assert after["index_version"] == before["index_version"]


def test_an_empty_file_is_rejected(client):
    before = _status(client)
    response = _ingest(client, "empty.jpg", b"")

    assert response.status_code == 400, response.text
    assert "empty" in response.json()["error"]["message"].lower()
    assert _status(client)["indexed_count"] == before["indexed_count"]


def test_a_video_is_stored_but_not_perceptually_indexed(client):
    before = _status(client)
    response = _ingest(client, "corpus-clip-unique.mp4", mp4_bytes(seed=9999), "video/mp4")

    assert response.status_code == 201, response.text
    body = response.json()

    assert body["evidence"]["media_type"] == "video"
    assert body["evidence"]["phash"] is None
    assert body["evidence"]["indexed"] is False
    assert body["index"]["status"] == "skipped"
    assert "no perceptual hash" in body["index"]["detail"]
    assert body["searchable"] is False

    after = _status(client)
    assert after["indexed_count"] == before["indexed_count"]


# --------------------------------------------------------------------------- #
# Auditing
# --------------------------------------------------------------------------- #
def test_ingestion_is_recorded_in_the_audit_chain(client):
    body = _ingest(client, "corpus-audited.jpg", jpeg_bytes(seed=519)).json()
    evidence_id = body["evidence"]["evidence_id"]

    session = _session()
    try:
        trail = audit.trail(session)
        verification = audit.verify_chain(session)
    finally:
        session.close()

    events = trail["events"]
    ingested = [
        e for e in events
        if e["event"] == audit.EVENT_EVIDENCE_INGESTED
        and e["details"].get("evidence_id") == evidence_id
    ]
    assert ingested, "corpus ingestion was not audited"
    assert ingested[0]["details"]["role"] == ROLE_CORPUS
    assert ingested[0]["case_id"] is None

    hashed = [
        e for e in events
        if e["event"] == audit.EVENT_HASH_CALCULATED
        and e["details"].get("evidence_id") == evidence_id
    ]
    assert hashed, "hashing was not audited"

    updated = [
        e for e in events
        if e["event"] == audit.EVENT_INDEX_UPDATED
        and e["details"].get("evidence_id") == evidence_id
    ]
    assert updated, "the index update was not audited"
    assert updated[-1]["details"]["operation"] == "add"
    assert updated[-1]["details"]["already_present"] is False

    assert verification["valid"] is True
    assert verification["first_invalid_seq"] is None


def test_duplicate_ingestion_is_audited_as_a_duplicate(client):
    data = jpeg_bytes(seed=520)
    first = _ingest(client, "corpus-twice.jpg", data).json()
    _ingest(client, "corpus-twice.jpg", data)

    session = _session()
    try:
        events = audit.trail(session)["events"]
    finally:
        session.close()

    duplicates = [
        e for e in events
        if e["event"] == audit.EVENT_EVIDENCE_DUPLICATE
        and e["details"].get("existing_evidence_id") == first["evidence"]["evidence_id"]
    ]
    assert duplicates, "the duplicate submission was not audited"
    assert duplicates[-1]["details"]["sha256"] == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Rebuild consistency
# --------------------------------------------------------------------------- #
def test_rebuild_keeps_ingested_corpus_items(client):
    body = _ingest(client, "corpus-survives.jpg", jpeg_bytes(seed=521)).json()
    evidence_id = body["evidence"]["evidence_id"]

    rebuilt = client.post("/api/index/rebuild").json()
    assert rebuilt["status"] == "rebuilt"

    from app.services.index import get_index

    assert get_index(client.app.state.settings).contains(evidence_id)
    assert _status(client)["indexed_count"] == rebuilt["indexed_count"]


@pytest.mark.parametrize("mime", ["image/jpeg", "application/octet-stream", ""])
def test_the_declared_mime_type_does_not_override_sniffed_content(client, mime):
    """Content is trusted over the client's declaration, in both directions."""
    data = png_bytes(seed=522 + len(mime))
    response = _ingest(client, "declared-wrong.png", data, mime)

    assert response.status_code == 201, response.text
    assert response.json()["evidence"]["mime_type"] == "image/png"
