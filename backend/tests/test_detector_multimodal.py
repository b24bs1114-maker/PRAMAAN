"""Unit and integration tests for multi-modal AI detector adapter system."""

from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.detector import (
    AudioDetector,
    ImageDetector,
    MultiModalDetectorService,
    VideoDetector,
    reset_detector_singleton,
)
from tests.helpers import jpeg_bytes


def test_detector_status_endpoint(settings):
    reset_detector_singleton()
    app = create_app(settings)
    client = TestClient(app)

    res = client.get("/api/detector/status")
    assert res.status_code == 200
    data = res.json()
    assert "adapter" in data
    assert "modalities" in data
    assert "image" in data["modalities"]
    assert "video" in data["modalities"]
    assert "audio" in data["modalities"]


def test_detect_endpoint_uninstalled_model(settings):
    orig = settings.detector_backend
    settings.detector_backend = "null"
    reset_detector_singleton()
    try:
        app = create_app(settings)
        client = TestClient(app)

        unique_bytes = jpeg_bytes(seed=88888)
        res = client.post(
            "/api/detect",
            files={"file": ("sample_multimodal.jpg", unique_bytes, "image/jpeg")},
            data={"media_type": "image"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["media_type"] == "image"
        assert data["abstained"] is True
        assert data["manipulation_score"] is None
        assert data["label"] == "ai_manipulation_likelihood"
    finally:
        settings.detector_backend = orig
        reset_detector_singleton()


def test_multimodal_routing(settings):
    reset_detector_singleton()
    svc = MultiModalDetectorService(settings)

    adapter_img = svc.get_adapter_for("image", Path("file.jpg"))
    assert isinstance(adapter_img, ImageDetector)

    adapter_vid = svc.get_adapter_for("video", Path("clip.mp4"))
    assert isinstance(adapter_vid, VideoDetector)

    adapter_aud = svc.get_adapter_for("audio", Path("voice.wav"))
    assert isinstance(adapter_aud, AudioDetector)


def test_model_path_configuration(tmp_path):
    img_model = tmp_path / "dummy_img.onnx"
    img_model.write_text("fake onnx bytes")

    settings = Settings(
        image_model_path=str(img_model),
        video_model_path="",
        audio_model_path="",
    )
    reset_detector_singleton()
    svc = MultiModalDetectorService(settings)
    assert svc.image_detector.model_path == img_model
    reset_detector_singleton()
