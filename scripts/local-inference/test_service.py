"""Smoke tests for the local inference service.

One test per modality, plus health and unsupported-media behaviour. Each
test hits the live FastAPI app (in-process, via TestClient) with a real
sample file from pramaan-detector/data/real_samples/ and asserts only on
contract fields -- never that a score exists (the detectors may abstain
truthfully) -- and that no score is fabricated as 0.

Run:
    .venv/bin/python -m pytest scripts/local-inference/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]  # .../PRAMAAN/scripts/local-inference -> PRAMAAN
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "pramaan-detector"))

from fastapi.testclient import TestClient  # noqa: E402

from service import app  # noqa: E402

SAMPLES = REPO_ROOT / "pramaan-detector" / "data" / "real_samples"

client = TestClient(app)


def _post(path: str, filename: str):
    with (SAMPLES / filename).open("rb") as fh:
        return client.post(path, files={"file": (filename, fh)})


def test_health_reports_all_modality_checkpoints():
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    for modality in ("image", "video", "audio"):
        assert modality in body["modalities"]
        assert body["modalities"][modality]["available"] is True


def test_image_inference():
    res = _post("/detect", "image_authentic.jpg")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["media_type"] == "image"
    assert body["model"] == "OwensLab-CommunityForensics-ViT384"
    assert body["model_version"] == "4.0.0"
    assert isinstance(body["weights_hash"], str) and body["weights_hash"]
    assert body["latency_ms"] > 0
    assert isinstance(body["explanation"], str) and body["explanation"]
    # Score is either a float in 0..1 or null (abstained) -- never a filler 0.
    if body["manipulation_score"] is None:
        assert body["abstained"] is True
    else:
        assert 0.0 <= body["manipulation_score"] <= 1.0
        assert body["abstained"] is False


def test_audio_inference():
    res = _post("/detect", "audio_authentic.wav")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["media_type"] == "audio"
    assert body["model"] == "AASIST-Audio-Spoof-Detector"
    assert body["model_version"] == "3.0.0"
    assert isinstance(body["weights_hash"], str) and body["weights_hash"]
    assert body["latency_ms"] > 0
    if body["manipulation_score"] is None:
        assert body["abstained"] is True
    else:
        assert 0.0 <= body["manipulation_score"] <= 1.0
        assert body["abstained"] is False


def test_video_inference():
    res = _post("/detect", "video_authentic.mp4")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["media_type"] == "video"
    assert body["model"] == "VideoMAE-DeepFake-Detector"
    assert body["model_version"] == "4.0.0"
    assert isinstance(body["weights_hash"], str) and body["weights_hash"]
    assert body["latency_ms"] > 0
    if body["manipulation_score"] is None:
        assert body["abstained"] is True
    else:
        assert 0.0 <= body["manipulation_score"] <= 1.0
        assert body["abstained"] is False


def test_explicit_endpoint_matches_auto_route():
    direct = _post("/detect/image", "image_deepfake.jpg")
    assert direct.status_code == 200, direct.text
    assert direct.json()["media_type"] == "image"


def test_unsupported_media_is_rejected_truthfully():
    res = client.post(
        "/detect",
        files={"file": ("notes.txt", b"not media at all", "text/plain")},
    )
    assert res.status_code == 415
    body = res.json()
    assert body["manipulation_score"] is None
    assert body["abstained"] is True
    assert "Cannot determine modality" in body["explanation"]


def test_api_key_enforced_when_configured(monkeypatch):
    import service as service_module

    monkeypatch.setattr(service_module, "_API_KEY", "sekrit")
    fresh = TestClient(service_module.app)
    no_key = fresh.post(
        "/detect",
        files={"file": ("image_authentic.jpg", (SAMPLES / "image_authentic.jpg").read_bytes())},
    )
    assert no_key.status_code == 401
    good = fresh.post(
        "/detect",
        files={"file": ("image_authentic.jpg", (SAMPLES / "image_authentic.jpg").read_bytes())},
        headers={"X-API-Key": "sekrit"},
    )
    assert good.status_code == 200
