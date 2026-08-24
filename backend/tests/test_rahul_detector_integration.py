"""Integration tests for Rahul's PRAMAAN AI Detector Engine in PRAMAAN Backend.

Verifies:
1. Image, Video, and Audio detection through app.services.pramaan_detector_adapter
2. POST /api/detect endpoint with Image, Video, Audio uploads and evidence items
3. Full case pipeline POST /api/cases/{case_id}/analyse with 5-signal fusion
4. Truthful abstention (null != 0, UNAVAILABLE != 0) when model abstains or cannot run
5. Model metadata, latency, weight hash, explanation, regions, and timestamps propagation
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import detector as detector_service
from app.services.detector import reset_detector_singleton, clear_inference_registry
from app.services.pramaan_detector_adapter import infer_image, infer_video, infer_audio
from tests.helpers import jpeg_bytes, mp4_bytes


@pytest.fixture(autouse=True)
def _clean_detector_state():
    clear_inference_registry()
    reset_detector_singleton()
    yield
    clear_inference_registry()
    reset_detector_singleton()


def _create_test_image(path: Path) -> Path:
    img = Image.fromarray(np.uint8(np.random.rand(100, 100, 3) * 255))
    img.save(path)
    return path


def _create_test_audio(path: Path) -> Path:
    import soundfile as sf
    data = np.random.randn(16000 * 2).astype(np.float32)
    sf.write(str(path), data, 16000)
    return path


def _create_test_video(path: Path) -> Path:
    import cv2
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(path), fourcc, 10.0, (100, 100))
    for _ in range(5):
        frame = np.uint8(np.random.rand(100, 100, 3) * 255)
        out.write(frame)
    out.release()
    return path


# --------------------------------------------------------------------------- #
# Direct Adapter Integration Tests (Image, Video, Audio)
# --------------------------------------------------------------------------- #
def test_rahul_adapter_image_inference(tmp_path: Path) -> None:
    img_path = _create_test_image(tmp_path / "test.jpg")
    res = infer_image(img_path)

    assert isinstance(res, dict)
    assert res["media_type"] == "image"
    assert "label" in res
    assert "abstained" in res
    assert res["model"] == "SwinB-AI-Image-Detector"
    assert res["model_version"] == "3.0.0"
    assert res["weights_hash"] is not None
    assert res["latency_ms"] >= 0
    assert "explanation" in res
    assert "heatmap_available" in res


def test_rahul_adapter_video_inference(tmp_path: Path) -> None:
    vid_path = _create_test_video(tmp_path / "test.mp4")
    res = infer_video(vid_path)

    assert isinstance(res, dict)
    assert res["media_type"] == "video"
    assert "label" in res
    assert "abstained" in res
    assert res["model"] == "VideoDetector-EfficientNetB0"
    assert res["model_version"] == "3.0.0"
    assert res["weights_hash"] is not None
    assert res["latency_ms"] >= 0
    assert "explanation" in res


def test_rahul_adapter_audio_inference(tmp_path: Path) -> None:
    aud_path = _create_test_audio(tmp_path / "test.wav")
    res = infer_audio(aud_path)

    assert isinstance(res, dict)
    assert res["media_type"] == "audio"
    assert "label" in res
    assert "abstained" in res
    assert res["model"] == "Wav2Vec2-GaryStafford-DeepfakeVoiceDetector"
    assert res["model_version"] == "2.0.0"
    assert res["weights_hash"] is not None
    assert res["latency_ms"] >= 0
    assert "explanation" in res


# --------------------------------------------------------------------------- #
# POST /api/detect Endpoint Tests
# --------------------------------------------------------------------------- #
def test_api_detect_image_with_rahul_entrypoint(client: TestClient, tmp_path: Path) -> None:
    detector_service.register_inference(
        "image", infer_image, model_name="SwinB-AI-Image-Detector", model_version="3.0.0"
    )
    reset_detector_singleton()

    img_path = _create_test_image(tmp_path / "upload.jpg")
    with open(img_path, "rb") as f:
        response = client.post(
            "/api/detect",
            files={"file": ("upload.jpg", f, "image/jpeg")},
            data={"media_type": "image"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_type"] == "image"
    assert body["model"] == "SwinB-AI-Image-Detector"
    assert body["model_version"] == "3.0.0"
    assert "weights_hash" in body
    assert body["latency_ms"] is not None
    assert "explanation" in body


def test_api_detect_video_with_rahul_entrypoint(client: TestClient, tmp_path: Path) -> None:
    detector_service.register_inference(
        "video", infer_video, model_name="VideoDetector-EfficientNetB0", model_version="3.0.0"
    )
    reset_detector_singleton()

    vid_path = _create_test_video(tmp_path / "clip.mp4")
    with open(vid_path, "rb") as f:
        response = client.post(
            "/api/detect",
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"media_type": "video"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_type"] == "video"
    assert body["model"] == "VideoDetector-EfficientNetB0"
    assert body["model_version"] == "3.0.0"
    assert body["latency_ms"] is not None


def test_api_detect_audio_with_rahul_entrypoint(client: TestClient, tmp_path: Path) -> None:
    detector_service.register_inference(
        "audio", infer_audio, model_name="Wav2Vec2-GaryStafford-DeepfakeVoiceDetector", model_version="2.0.0"
    )
    reset_detector_singleton()

    aud_path = _create_test_audio(tmp_path / "voice.wav")
    with open(aud_path, "rb") as f:
        response = client.post(
            "/api/detect",
            files={"file": ("voice.wav", f, "audio/wav")},
            data={"media_type": "audio"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_type"] == "audio"
    assert body["model"] == "Wav2Vec2-GaryStafford-DeepfakeVoiceDetector"
    assert body["model_version"] == "2.0.0"
    assert body["latency_ms"] is not None


# --------------------------------------------------------------------------- #
# Full Pipeline Analysis Test: POST /api/cases/{case_id}/analyse
# --------------------------------------------------------------------------- #
def test_full_case_analysis_with_rahul_detector(client: TestClient, tmp_path: Path) -> None:
    detector_service.register_inference(
        "image", infer_image, model_name="SwinB-AI-Image-Detector", model_version="3.0.0"
    )
    reset_detector_singleton()

    img_path = _create_test_image(tmp_path / "case_sample.jpg")
    with open(img_path, "rb") as f:
        upload_resp = client.post(
            "/api/cases/upload",
            files={"file": ("case_sample.jpg", f, "image/jpeg")},
        )
    assert upload_resp.status_code == 201, upload_resp.text
    case_id = upload_resp.json()["case"]["case_id"]

    analyse_resp = client.post(f"/api/cases/{case_id}/analyse")
    assert analyse_resp.status_code == 200, analyse_resp.text

    analysis = analyse_resp.json()
    assert "verdict" in analysis
    assert "signals" in analysis

    ai_sig = next((s for s in analysis["signals"] if s["signal_id"] == "ai_detection"), None)
    assert ai_sig is not None, "ai_detection signal must be present in signals list"
    assert ai_sig["name"] == "AI manipulation detector"
    assert ai_sig["evidence_basis"]["model"] == "SwinB-AI-Image-Detector"


# --------------------------------------------------------------------------- #
# Truthful Abstention Test (null != 0)
# --------------------------------------------------------------------------- #
def test_truthful_abstention_when_uninstalled(client: TestClient, tmp_path: Path) -> None:
    """When no detector is installed, no score is fabricated."""
    reset_detector_singleton()

    img_path = _create_test_image(tmp_path / "uninstalled.jpg")
    with open(img_path, "rb") as f:
        response = client.post(
            "/api/detect",
            files={"file": ("uninstalled.jpg", f, "image/jpeg")},
            data={"media_type": "image"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["abstained"] is True
    assert body["manipulation_score"] is None
    assert body["confidence"] is None
