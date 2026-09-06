"""Media-aware signal applicability (the /api/system/signals contract).

The binding requirement: the backend is the single source of truth for which
forensic signals apply to which media type. The endpoint publishes the fusion
engine's own applicability map, the verdicts carry counts scoped to that map,
and inapplicable signals are hidden -- never rendered as failed or zero rows,
never counted in any coverage denominator.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import fusion


def _applicability(client: TestClient) -> dict:
    response = client.get("/api/system/signals")
    assert response.status_code == 200, response.text
    return response.json()


def test_endpoint_publishes_the_fusion_applicability_map(client: TestClient) -> None:
    body = _applicability(client)

    assert set(body["applicability"]) == {"image", "video", "audio"}
    assert body["note"] == fusion.SIGNAL_APPLICABILITY_NOTE

    # The endpoint cannot disagree with the engine: every media type lists
    # exactly the signals the fusion map allows, with the engine's own names.
    for media_type, allowed in fusion.SIGNAL_APPLICABILITY.items():
        published = [s["signal_id"] for s in body["applicability"][media_type]]
        assert set(published) == set(allowed)
        assert len(published) == len(allowed)
        for entry in body["applicability"][media_type]:
            assert entry["name"] == fusion.SIGNAL_NAMES[entry["signal_id"]]


def test_image_carries_all_five_signals(client: TestClient) -> None:
    image = [s["signal_id"] for s in _applicability(client)["applicability"]["image"]]
    assert image == [
        "ai_detection",
        "perceptual_duplication",
        "metadata_integrity",
        "provenance_c2pa",
        "compression_forensics",
    ]


def test_video_carries_no_image_only_signals(client: TestClient) -> None:
    """No perceptual indexing, no compression forensics, for video."""
    video = [s["signal_id"] for s in _applicability(client)["applicability"]["video"]]
    assert video == ["ai_detection", "metadata_integrity", "provenance_c2pa"]
    assert "perceptual_duplication" not in video
    assert "compression_forensics" not in video


def test_audio_carries_only_what_genuinely_applies(client: TestClient) -> None:
    """No image perceptual matching, no image compression forensics, for audio."""
    audio = [s["signal_id"] for s in _applicability(client)["applicability"]["audio"]]
    assert audio == ["ai_detection"]


def test_endpoint_publishes_declared_weights(client: TestClient, settings) -> None:
    body = _applicability(client)
    assert body["signal_names"] == fusion.SIGNAL_NAMES
    assert body["declared_weights"] == settings.fusion_weights


def test_system_status_exposes_the_same_applicability(client: TestClient) -> None:
    """The status page's fusion block carries the same map, so the settings
    screen and the analysis UI cannot drift apart."""
    status = client.get("/api/system/status").json()
    block = status["fusion"]["signal_applicability"]

    assert set(block) == {"image", "video", "audio"}
    for media_type, allowed in fusion.SIGNAL_APPLICABILITY.items():
        assert set(block[media_type]) == set(allowed)
