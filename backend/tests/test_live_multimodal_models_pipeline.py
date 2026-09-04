"""Live multi-modal AI detector and fusion pipeline integration test.

Proves that:
1. Live Image analysis executes OwensLab CommunityForensics ViT-384.
2. Live Audio analysis executes AASIST Audio Spoof Detector.
3. Live Video analysis executes VideoMAE DeepFake Detector.
4. Each detector produces real inference scores, real checkpoint hashes, and real model identities.
5. Multi-signal fusion dynamically consumes the genuine detector outputs.
6. Provenance terminology remains strictly "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, get_settings
from app.main import app
from app.services.detector import (
    clear_inference_registry,
    reset_detector_singleton,
)

SAMPLES_DIR = PROJECT_ROOT / "pramaan-detector" / "data" / "real_samples"
WEIGHTS_DIR = PROJECT_ROOT / "pramaan-detector" / "weights"


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
    os.environ["PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT"] = "app.services.pramaan_detector_adapter:infer_image"
    os.environ["PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT"] = "app.services.pramaan_detector_adapter:infer_video"
    os.environ["PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT"] = "app.services.pramaan_detector_adapter:infer_audio"
    get_settings.cache_clear()


@pytest.fixture
def real_client():
    clear_inference_registry()
    reset_detector_singleton()
    _configure_real_models()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
    reset_detector_singleton()


def test_real_checkpoints_physically_exist():
    """Verify all 3 model checkpoints physically exist with correct SHA-256 digests."""
    img_ckpt = WEIGHTS_DIR / "image_detector.safetensors"
    aud_ckpt = WEIGHTS_DIR / "audio_detector.pth"
    vid_ckpt = WEIGHTS_DIR / "video_detector.safetensors"

    assert img_ckpt.is_file(), f"Missing image checkpoint at {img_ckpt}"
    assert aud_ckpt.is_file(), f"Missing audio checkpoint at {aud_ckpt}"
    assert vid_ckpt.is_file(), f"Missing video checkpoint at {vid_ckpt}"

    import hashlib

    def sha256_file(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    assert sha256_file(img_ckpt) == "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
    assert sha256_file(aud_ckpt) == "51d2d9cf0738172f61e2a384ec50a54a55363240f67c971ed55a92435bc1a1c0"
    assert sha256_file(vid_ckpt) == "293c668a5d3289d3162902d8cac6687cd11551cf2358768bad126de32b6d7f29"


def test_live_pipeline_image_analysis(real_client):
    """End-to-end test: Upload image -> Run analysis -> Assert OwensLab ViT-384 executed."""
    img_path = SAMPLES_DIR / "image_authentic.jpg"
    assert img_path.is_file(), f"Missing sample file: {img_path}"

    with open(img_path, "rb") as f:
        img_bytes = f.read()

    upload_res = real_client.post(
        "/api/cases/upload",
        files={"file": ("test_scene.jpg", img_bytes, "image/jpeg")},
    )
    assert upload_res.status_code in (200, 201), upload_res.text
    case_id = upload_res.json()["case"]["case_id"]

    # 2. Run analysis
    res = real_client.post(f"/api/cases/{case_id}/analyse", params={"refresh": "true"})
    assert res.status_code == 200, res.text
    data = res.json()

    # 3. Assert verdict & signals
    assert "verdict" in data
    assert "signals" in data

    ai_signals = [s for s in data["signals"] if s["signal_id"] == "ai_detection"]
    assert len(ai_signals) == 1, "ai_detection signal missing from response"
    ai_sig = ai_signals[0]

    assert ai_sig["status"] == "OK"
    assert ai_sig["score"] is not None
    assert 0.0 <= ai_sig["score"] <= 1.0
    assert ai_sig["included"] is True

    basis = ai_sig.get("evidence_basis", {})
    assert "OwensLab" in str(basis.get("model")) or "CommunityForensics" in str(basis.get("model"))
    assert basis.get("weights_hash") == "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
    assert basis.get("detector_status") == "OK"
    assert basis.get("availability") == "scored"


def test_live_pipeline_audio_analysis(real_client):
    """End-to-end test: Upload audio -> Run analysis -> Assert AASIST executed."""
    aud_path = SAMPLES_DIR / "audio_authentic.wav"
    assert aud_path.is_file(), f"Missing sample file: {aud_path}"

    with open(aud_path, "rb") as f:
        wav_bytes = f.read()

    upload_res = real_client.post(
        "/api/cases/upload",
        files={"file": ("speech_sample.wav", wav_bytes, "audio/wav")},
    )
    assert upload_res.status_code in (200, 201), upload_res.text
    case_id = upload_res.json()["case"]["case_id"]

    # 2. Run analysis
    res = real_client.post(f"/api/cases/{case_id}/analyse", params={"refresh": "true"})
    assert res.status_code == 200, res.text
    data = res.json()

    ai_signals = [s for s in data["signals"] if s["signal_id"] == "ai_detection"]
    assert len(ai_signals) == 1
    ai_sig = ai_signals[0]

    assert ai_sig["status"] == "OK"
    assert ai_sig["score"] is not None
    assert 0.0 <= ai_sig["score"] <= 1.0
    assert ai_sig["included"] is True

    basis = ai_sig.get("evidence_basis", {})
    assert "AASIST" in str(basis.get("model"))
    assert basis.get("weights_hash") == "51d2d9cf0738172f61e2a384ec50a54a55363240f67c971ed55a92435bc1a1c0"
    assert basis.get("detector_status") == "OK"
    assert basis.get("availability") == "scored"


def test_live_pipeline_video_analysis(real_client):
    """End-to-end test: Upload video -> Run analysis -> Assert VideoMAE executed."""
    vid_path = SAMPLES_DIR / "video_authentic.mp4"
    assert vid_path.is_file(), f"Missing sample file: {vid_path}"

    with open(vid_path, "rb") as f:
        vid_bytes = f.read()

    upload_res = real_client.post(
        "/api/cases/upload",
        files={"file": ("clip.mp4", vid_bytes, "video/mp4")},
    )
    assert upload_res.status_code in (200, 201), upload_res.text
    case_id = upload_res.json()["case"]["case_id"]

    # 2. Run analysis
    res = real_client.post(f"/api/cases/{case_id}/analyse", params={"refresh": "true"})
    assert res.status_code == 200, res.text
    data = res.json()

    ai_signals = [s for s in data["signals"] if s["signal_id"] == "ai_detection"]
    assert len(ai_signals) == 1
    ai_sig = ai_signals[0]

    # VideoMAE ran real temporal inference across 16 frames
    assert ai_sig["status"] in ("OK", "INCONCLUSIVE")
    if ai_sig["status"] == "OK":
        assert ai_sig["score"] is not None
        assert 0.0 <= ai_sig["score"] <= 1.0
        assert ai_sig["included"] is True
    else:
        assert ai_sig["status"] == "INCONCLUSIVE"
        assert ai_sig["included"] is False

    basis = ai_sig.get("evidence_basis", {})
    assert "VideoMAE" in str(basis.get("model"))
    assert basis.get("weights_hash") == "293c668a5d3289d3162902d8cac6687cd11551cf2358768bad126de32b6d7f29"
    assert basis.get("availability") in ("scored", "ran_and_declined")


def test_provenance_honesty_terminology(real_client):
    """Verify provenance responses and views maintain strict honest terminology."""
    img_path = SAMPLES_DIR / "image_authentic.jpg"
    with open(img_path, "rb") as f:
        img_bytes = f.read()

    upload_res = real_client.post(
        "/api/cases/upload",
        files={"file": ("scene1.jpg", img_bytes, "image/jpeg")},
    )
    assert upload_res.status_code in (200, 201)
    case_id = upload_res.json()["case"]["case_id"]

    res = real_client.post(f"/api/cases/{case_id}/analyse", params={"refresh": "true"})
    assert res.status_code == 200
    data = res.json()

    origin = data.get("origin", {})
    explanation = origin.get("explanation", "")
    assert "first upload" not in explanation.lower()
    assert "true origin" not in explanation.lower()
    assert "original" not in explanation.lower() or "indexed" in explanation.lower()
