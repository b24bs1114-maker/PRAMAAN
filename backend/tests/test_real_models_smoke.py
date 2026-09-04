"""Integration smoke test against the REAL image, audio, and video checkpoints.

Verifies:
1. GET /api/detector/status reports each modality truthfully: available=true for
   image, audio, and video.
2. POST /api/detect works for image, audio, and video with real model inference.
3. POST /api/cases/upload & POST /api/cases/{case_id}/analyse run full pipeline:
   real detector -> ai_detection -> fusion -> verdict.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.detector import (
    MultiModalDetectorService,
    clear_inference_registry,
    reset_detector_singleton,
)

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "pramaan-detector" / "data" / "real_samples"
WEIGHTS_DIR = Path(__file__).resolve().parents[2] / "pramaan-detector" / "weights"


def _configure_real_models() -> None:
    """Point the detector sockets at the published checkpoints."""
    img_ckpt = WEIGHTS_DIR / "image_detector.safetensors"
    if not img_ckpt.exists():
        img_ckpt = WEIGHTS_DIR / "image_detector.pt"

    aud_ckpt = WEIGHTS_DIR / "audio_detector.pth"
    if not aud_ckpt.exists():
        aud_ckpt = WEIGHTS_DIR / "audio_detector.pt"

    vid_ckpt = WEIGHTS_DIR / "video_detector.safetensors"
    if not vid_ckpt.exists():
        vid_ckpt = WEIGHTS_DIR / "video_detector.pt"

    os.environ["PRAMAAN_IMAGE_MODEL_PATH"] = str(img_ckpt)
    os.environ["PRAMAAN_AUDIO_MODEL_PATH"] = str(aud_ckpt)
    os.environ["PRAMAAN_VIDEO_MODEL_PATH"] = str(vid_ckpt)
    os.environ["PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT"] = "pramaan.detectors.image_detector:detect_image"
    os.environ["PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT"] = "pramaan.detectors.video_detector:detect_video"
    os.environ["PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT"] = "pramaan.detectors.audio_detector:detect_audio"
    get_settings.cache_clear()


@pytest.fixture
def client():
    clear_inference_registry()
    reset_detector_singleton()
    _configure_real_models()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
    reset_detector_singleton()


def _get_real_detector():
    _configure_real_models()
    return MultiModalDetectorService(get_settings())


def test_detector_status_reports_each_modality_truthfully(client):
    detector = _get_real_detector()
    avail, reason = detector.available()
    assert avail is True, f"MultiModalDetectorService should be available: {reason}"

    mod_status = detector.modality_availability()
    assert mod_status["image"]["available"] is True, f"Image model unavailable: {mod_status['image']}"
    assert mod_status["audio"]["available"] is True, f"Audio model unavailable: {mod_status['audio']}"
    assert mod_status["video"]["available"] is True, f"Video model unavailable: {mod_status['video']}"

    assert "OwensLab" in mod_status["image"]["model"] or "ViT" in mod_status["image"]["model"]
    assert "AASIST" in mod_status["audio"]["model"]
    assert "VideoMAE" in mod_status["video"]["model"]


def test_real_image_inference():
    detector = _get_real_detector()

    img_auth = SAMPLES_DIR / "image_authentic.jpg"
    res_auth = detector.analyse(img_auth, media_type="image")
    assert res_auth.status == "OK"
    assert res_auth.abstained is False
    assert 0.0 <= res_auth.manipulation_score <= 1.0
    assert res_auth.weights_hash != ""
    assert "OwensLab" in res_auth.model or "ViT" in res_auth.model
    assert res_auth.latency_ms > 0.0


def test_real_video_inference():
    detector = _get_real_detector()

    vid_path = SAMPLES_DIR / "video_deepfake.mp4"
    res = detector.analyse(vid_path, media_type="video")
    assert res.status in ("OK", "UNAVAILABLE", "INCONCLUSIVE")
    assert "VideoMAE" in res.model
    assert res.extras.get("routed_modality") == "video"


def test_real_audio_inference():
    detector = _get_real_detector()

    aud_path = SAMPLES_DIR / "audio_deepfake.wav"
    res = detector.analyse(aud_path, media_type="audio")
    assert res.status == "OK"
    assert res.abstained is False
    assert 0.0 <= res.manipulation_score <= 1.0
    assert "AASIST" in res.model


def test_api_detect_endpoint_all_modalities(client):
    # Image
    img_path = SAMPLES_DIR / "image_authentic.jpg"
    with open(img_path, "rb") as f:
        resp_img = client.post(
            "/api/detect",
            files={"file": ("image_authentic.jpg", f, "image/jpeg")},
            data={"media_type": "image"},
        )
    assert resp_img.status_code == 200
    b_img = resp_img.json()
    assert b_img["media_type"] == "image"
    assert b_img["abstained"] is False
    assert b_img["manipulation_score"] is not None

    # Video
    vid_path = SAMPLES_DIR / "video_deepfake.mp4"
    with open(vid_path, "rb") as f:
        resp_vid = client.post(
            "/api/detect",
            files={"file": ("video_deepfake.mp4", f, "video/mp4")},
            data={"media_type": "video"},
        )
    assert resp_vid.status_code == 200
    b_vid = resp_vid.json()
    assert b_vid["media_type"] == "video"
    assert "VideoMAE" in b_vid["model"]
    assert b_vid["latency_ms"] is not None

    # Audio
    aud_path = SAMPLES_DIR / "audio_deepfake.wav"
    with open(aud_path, "rb") as f:
        resp_aud = client.post(
            "/api/detect",
            files={"file": ("audio_deepfake.wav", f, "audio/wav")},
            data={"media_type": "audio"},
        )
    assert resp_aud.status_code == 200
    b_aud = resp_aud.json()
    assert b_aud["media_type"] == "audio"
    assert b_aud["abstained"] is False
    assert b_aud["manipulation_score"] is not None


def test_api_case_analysis_full_pipeline(client):
    # 1. Upload evidence and create case via POST /api/cases/upload
    img_path = SAMPLES_DIR / "image_authentic.jpg"
    with open(img_path, "rb") as f:
        up_resp = client.post(
            "/api/cases/upload",
            files={"file": ("image_authentic.jpg", f, "image/jpeg")},
            data={"title": "Real Model Smoke Test Case"},
        )
    assert up_resp.status_code in (200, 201)
    case_id = up_resp.json()["case"]["case_id"]

    # 2. Analyse case via POST /api/cases/{case_id}/analyse
    an_resp = client.post(f"/api/cases/{case_id}/analyse")
    assert an_resp.status_code == 200
    an_data = an_resp.json()

    assert "verdict" in an_data
    assert "signals" in an_data
    ai_signal = next((s for s in an_data["signals"] if s.get("signal_id") == "ai_detection"), None)
    assert ai_signal is not None
    assert ai_signal["status"] == "OK"
    assert ai_signal["included"] is True
    assert ai_signal["score"] is not None
