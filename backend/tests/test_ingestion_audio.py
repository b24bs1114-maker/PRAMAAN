"""Audio evidence ingestion.

Audio only became ingestible when the multi-modal detector interface did: before
this, an audio file was rejected at the door, so an audio model would have had
nothing to run on. What is asserted here is the ingestion half of that path --
that the container is identified from its bytes, stored, hashed and registered
with ``media_type: "audio"``, and that the stages which genuinely cannot read
audio say so instead of scoring it.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.services.storage import MEDIA_AUDIO, MEDIA_VIDEO, _sniff
from tests.helpers import (
    jpeg_bytes,
    m4a_bytes,
    mov_bytes,
    mov_bytes_without_ftyp,
    mp3_bytes,
    mp4_bytes,
    wav_bytes,
)


def _upload(client: TestClient, data: bytes, name: str, mime: str = "audio/wav"):
    return client.post("/api/cases/upload", files={"file": (name, data, mime)})


# --------------------------------------------------------------------------- #
# Identification
# --------------------------------------------------------------------------- #
def test_audio_containers_are_identified_from_their_bytes() -> None:
    assert _sniff(wav_bytes()[:32]) == ("audio/wav", MEDIA_AUDIO, "wav")
    assert _sniff(mp3_bytes()[:32]) == ("audio/mpeg", MEDIA_AUDIO, "mp3")
    assert _sniff(b"fLaC\x00\x00\x00\x22" + b"\x00" * 24) == (
        "audio/flac",
        MEDIA_AUDIO,
        "flac",
    )
    assert _sniff(b"OggS\x00\x02" + b"\x00" * 26) == ("audio/ogg", MEDIA_AUDIO, "ogg")
    # A bare MPEG audio frame, with no ID3 tag in front of it.
    assert _sniff(b"\xff\xfb\x90\x00" + b"\x00" * 28) == (
        "audio/mpeg",
        MEDIA_AUDIO,
        "mp3",
    )
    # ADTS AAC differs from MP3 only in the layer bits of the second byte.
    assert _sniff(b"\xff\xf1P\x80" + b"\x00" * 28) == ("audio/aac", MEDIA_AUDIO, "aac")


def test_ftyp_brand_separates_audio_from_video_in_one_container() -> None:
    """M4A, MOV and MP4 are the same box format; only the brand says which."""
    assert _sniff(m4a_bytes()[:32]) == ("audio/mp4", MEDIA_AUDIO, "m4a")
    assert _sniff(mp4_bytes()[:32]) == ("video/mp4", MEDIA_VIDEO, "mp4")
    # QuickTime. Before the brand was read, this returned ("video/mp4", "mp4"):
    # settings.video_extensions and the rejection message both advertise MOV, but
    # nothing could produce it, so QuickTime evidence was recorded as MP4.
    assert _sniff(mov_bytes()[:32]) == ("video/quicktime", MEDIA_VIDEO, "mov")
    # Old-style QuickTime has no ftyp box at all; ISO-BMFF requires one first, so
    # a leading moov identifies the container without guessing.
    assert _sniff(mov_bytes_without_ftyp()[:32]) == ("video/quicktime", MEDIA_VIDEO, "mov")
    # An unrecognised brand stays video, which is what this sniffer did before
    # audio existed. Guessing "audio" for an unknown brand would silently reroute
    # video evidence to an audio model.
    unknown = mp4_bytes()[:8] + b"zzzz" + mp4_bytes()[12:32]
    assert _sniff(unknown) == ("video/mp4", MEDIA_VIDEO, "mp4")


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
def test_wav_upload_is_ingested_as_audio_evidence(client: TestClient) -> None:
    payload = wav_bytes(seed=5)
    response = _upload(client, payload, "interview.wav")

    assert response.status_code == 201, response.text
    evidence = response.json()["evidence"]
    assert evidence["media_type"] == MEDIA_AUDIO
    assert evidence["mime_type"] == "audio/wav"
    assert evidence["size_bytes"] == len(payload)
    assert evidence["sha256"] == hashlib.sha256(payload).hexdigest()
    # Audio has no pixels and no perceptual hash. Null, not a placeholder value.
    assert evidence["width"] is None
    assert evidence["height"] is None
    assert evidence["phash"] is None


def test_stored_audio_bytes_are_unaltered(client: TestClient, settings) -> None:
    payload = wav_bytes(seed=6)
    evidence = _upload(client, payload, "unaltered.wav").json()["evidence"]

    stored = settings.data_dir / evidence["stored_path"]
    assert stored.is_file()
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == evidence["sha256"]


def test_m4a_upload_is_ingested_as_audio_not_video(client: TestClient) -> None:
    evidence = _upload(client, m4a_bytes(seed=7), "call.m4a", "audio/mp4").json()[
        "evidence"
    ]
    assert evidence["media_type"] == MEDIA_AUDIO
    assert evidence["stored_path"].endswith(".m4a")


def test_declared_audio_mime_does_not_override_the_sniffed_type(
    client: TestClient,
) -> None:
    """A JPEG announced as audio is still stored as an image."""
    evidence = _upload(client, jpeg_bytes(seed=8), "not-really.wav", "audio/wav").json()[
        "evidence"
    ]
    assert evidence["media_type"] == "image"
    assert evidence["mime_type"] == "image/jpeg"


def test_disallowed_audio_extension_is_refused(client: TestClient, settings) -> None:
    """The permitted list is configuration, and it is actually consulted."""
    from app.api.deps import get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"allowed_audio_extensions": "flac"}
    )
    try:
        response = _upload(client, wav_bytes(seed=9), "blocked.wav")
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 400
    assert "not permitted" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# What the pipeline can and cannot say about audio
# --------------------------------------------------------------------------- #
def test_audio_analysis_abstains_rather_than_scoring(client: TestClient) -> None:
    """No audio model and no audio metadata reader in this build, and it says so."""
    case_id = _upload(client, wav_bytes(seed=10), "verdict.wav").json()["case"][
        "case_id"
    ]

    response = client.post(f"/api/cases/{case_id}/analyse?refresh=true")
    assert response.status_code == 200, response.text
    body = response.json()
    verdict = body["verdicts"][0]

    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert verdict["manipulation_score"] is None
    statuses = {s["signal_id"]: s["status"] for s in verdict["signals"]}
    # The detector interface covers audio, so the reason is "not installed"...
    assert statuses["ai_detection"] == "UNAVAILABLE"
    # ...while compression forensics genuinely does not apply to a sound file.
    assert statuses["compression_forensics"] == "UNSUPPORTED_MEDIA"
    for signal in verdict["signals"]:
        assert signal["score"] is None
        assert signal["contribution"] is None


def test_audio_evidence_is_not_indexed_for_perceptual_matching(
    client: TestClient,
) -> None:
    """Perceptual matching is an image technique; audio must not appear as a hit."""
    case_id = _upload(client, wav_bytes(seed=11), "matches.wav").json()["case"][
        "case_id"
    ]

    body = client.post(f"/api/cases/{case_id}/matches").json()
    assert body["total_candidates"] == 0
    query = body["queries"][0]
    assert query["candidates"] == []
    assert any("image" in note.lower() for note in query["notes"])
