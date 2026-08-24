"""Evidence ingestion tests (TASK 1).

Covers the normal path, duplicate detection, invalid and corrupted input,
SHA-256 correctness against an independent computation, and path-traversal
resistance.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient

from tests.helpers import jpeg_bytes, png_bytes


def _upload(client: TestClient, data: bytes, name: str = "evidence.jpg", **form):
    return client.post(
        "/api/cases/upload",
        files={"file": (name, data, "image/jpeg")},
        data=form,
    )


def test_upload_creates_case_and_evidence(client: TestClient) -> None:
    payload = jpeg_bytes(seed=3)
    response = _upload(client, payload, title="Normal upload")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["duplicate"] is False
    case = body["case"]
    evidence = body["evidence"]

    # Server-generated identifiers, not client controlled.
    uuid.UUID(case["case_id"])
    uuid.UUID(evidence["evidence_id"])
    assert case["case_number"].startswith("PRAMAAN-")
    assert case["evidence_count"] == 1

    assert evidence["filename"] == "evidence.jpg"
    assert evidence["mime_type"] == "image/jpeg"
    assert evidence["media_type"] == "image"
    assert evidence["size_bytes"] == len(payload)
    assert evidence["width"] == 320
    assert evidence["height"] == 240
    assert evidence["format"] == "JPEG"
    assert evidence["ingested_at"].endswith("Z")
    assert evidence["role"] == "case_evidence"


def test_sha256_matches_independent_computation(client: TestClient) -> None:
    payload = jpeg_bytes(seed=21)
    expected = hashlib.sha256(payload).hexdigest()

    body = _upload(client, payload, name="sha-check.jpg").json()
    assert body["evidence"]["sha256"] == expected
    assert len(body["evidence"]["sha256"]) == 64


def test_stored_bytes_hash_to_the_reported_digest(client: TestClient, settings) -> None:
    """The digest must describe the bytes actually on disk, not the request body."""
    payload = jpeg_bytes(seed=22)
    body = _upload(client, payload, name="stored.jpg").json()

    stored = settings.data_dir / body["evidence"]["stored_path"]
    assert stored.is_file()
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == body["evidence"]["sha256"]


def test_perceptual_hashes_are_computed_for_images(client: TestClient) -> None:
    body = _upload(client, jpeg_bytes(seed=5), name="hashes.jpg").json()
    evidence = body["evidence"]
    for field in ("phash", "dhash", "ahash"):
        assert evidence[field] is not None, field
        assert len(evidence[field]) == 16          # 64 bits as hex
        int(evidence[field], 16)


def test_duplicate_upload_returns_existing_record(client: TestClient) -> None:
    payload = jpeg_bytes(seed=31)
    first = _upload(client, payload, name="dup.jpg").json()
    case_id = first["case"]["case_id"]

    second = client.post(
        "/api/cases/upload",
        files={"file": ("dup-again.jpg", payload, "image/jpeg")},
        data={"case_id": case_id},
    )
    assert second.status_code == 200          # not 201: nothing new was created
    body = second.json()
    assert body["duplicate"] is True
    assert body["evidence"]["evidence_id"] == first["evidence"]["evidence_id"]
    assert body["case"]["evidence_count"] == 1
    assert any("SHA-256" in w for w in body["warnings"])


def test_same_bytes_in_a_different_case_is_not_a_duplicate(client: TestClient) -> None:
    payload = jpeg_bytes(seed=32)
    first = _upload(client, payload, name="cross1.jpg").json()
    second = _upload(client, payload, name="cross2.jpg").json()

    assert second["duplicate"] is False
    assert second["case"]["case_id"] != first["case"]["case_id"]
    assert second["evidence"]["sha256"] == first["evidence"]["sha256"]


def test_non_media_file_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/cases/upload",
        files={"file": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "http_error"
    assert "Unsupported" in body["error"]["message"]
    assert "request_id" in body


def test_corrupted_image_is_rejected(client: TestClient) -> None:
    """Valid JPEG magic bytes, truncated payload -- must not be ingested."""
    truncated = jpeg_bytes(seed=41)[:120]
    response = _upload(client, truncated, name="broken.jpg")
    assert response.status_code == 400
    assert "decoded" in response.json()["error"]["message"]


def test_empty_file_is_rejected(client: TestClient) -> None:
    response = _upload(client, b"", name="empty.jpg")
    assert response.status_code == 400
    assert "empty" in response.json()["error"]["message"].lower()


def test_extension_spoofing_uses_sniffed_type(client: TestClient) -> None:
    """A PNG named .jpg with a JPEG content-type is stored as a PNG."""
    response = client.post(
        "/api/cases/upload",
        files={"file": ("mislabelled.jpg", png_bytes(seed=13), "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["evidence"]["mime_type"] == "image/png"
    assert body["evidence"]["format"] == "PNG"
    assert any("does not match" in w for w in body["warnings"])


def test_path_traversal_filename_cannot_escape_storage(
    client: TestClient, settings
) -> None:
    hostile = "../../../../etc/pramaan_pwned.jpg"
    body = _upload(client, jpeg_bytes(seed=51), name=hostile).json()

    # Name is kept only as sanitised metadata...
    assert "/" not in body["evidence"]["filename"]
    assert ".." not in body["evidence"]["filename"]

    # ...and the file lives under the evidence root, named by evidence id.
    stored = (settings.data_dir / body["evidence"]["stored_path"]).resolve()
    assert stored.is_file()
    assert settings.evidence_dir.resolve() in stored.parents
    assert stored.name.startswith(body["evidence"]["evidence_id"])
    assert not (settings.data_dir.parent / "etc" / "pramaan_pwned.jpg").exists()


def test_upload_to_unknown_case_returns_404(client: TestClient) -> None:
    response = _upload(client, jpeg_bytes(seed=61), case_id=str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "http_error"


def test_missing_file_field_is_a_validation_error(client: TestClient) -> None:
    response = client.post("/api/cases/upload", data={"title": "no file"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["type"] == "validation_error"
    assert body["error"]["details"]
