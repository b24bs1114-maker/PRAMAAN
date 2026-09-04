"""Integration smoke test against the REAL image and audio checkpoints.

Verifies:
1. GET /api/detector/status reports each modality truthfully: available=true for
   image and audio, available=false with a stated reason for video.
2. POST /api/detect works for image and audio with real model inference, and
   abstains for video.
3. POST /api/cases/upload & POST /api/cases/{case_id}/analyse run full pipeline:
   real detector -> ai_detection -> fusion -> verdict.

Video has no published checkpoint (see ``weights/model_manifest.json``), so
there is no configuration of this repository in which it produces a score. This
file used to point ``PRAMAAN_VIDEO_MODEL_PATH`` at ``image_detector.pt`` and
assert that the video slot named a model, which made the suite ratify the exact
misconfiguration it should catch: Swin-B image parameters cannot load into the
EfficientNet-B0 frame model, so that setting installs no video detector while
making the status endpoint advertise one. The video path is now left
deliberately empty and the honest unavailable state is what gets asserted.
"""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings
import os
from app.services.detector import MultiModalDetectorService, reset_detector_singleton, clear_inference_registry

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "pramaan-detector" / "data" / "real_samples"
WEIGHTS_DIR = Path(__file__).resolve().parents[2] / "pramaan-detector" / "weights"


def _configure_real_models() -> None:
    """Point the detector sockets at the checkpoints this repository actually has.

    ``PRAMAAN_VIDEO_MODEL_PATH`` is cleared, not set: no video checkpoint is
    published, and any other modality's checkpoint installs no video detector.
    The video *entrypoint* stays configured because the plug-in module is
    genuinely installed -- that is the deployment this asserts on, a loadable
    module with no weights to load.
    """
    os.environ["PRAMAAN_IMAGE_MODEL_PATH"] = str(WEIGHTS_DIR / "image_detector.pt")
    os.environ["PRAMAAN_VIDEO_MODEL_PATH"] = ""
    os.environ["PRAMAAN_AUDIO_MODEL_PATH"] = str(WEIGHTS_DIR / "audio_detector.pt")
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

    # No video checkpoint is published, so the socket must report itself
    # unavailable, say why, and name no model. A slot that cannot score a single
    # frame must not appear in the status payload as a working deepfake detector,
    # and must not borrow the identity of a checkpoint it never loaded.
    video_status = mod_status["video"]
    assert video_status["available"] is False, (
        f"Video reported available with no published checkpoint: {video_status}"
    )
    assert "no trained video detector is installed" in (video_status.get("reason") or "").lower()
    assert video_status["model"] == "none"
    assert video_status["model_version"] == ""
    assert video_status["weights_hash"] == ""

    assert "SwinB" in mod_status["image"]["model"] or "EfficientNet" in mod_status["image"]["model"]
    assert mod_status["audio"]["model"] == "Wav2Vec2-GaryStafford-DeepfakeVoiceDetector"
    # The image socket's digest belongs to the image socket alone.
    assert mod_status["image"]["weights_hash"] not in ("", video_status["weights_hash"])


def test_real_image_inference():
    detector = _get_real_detector()

    img_auth = SAMPLES_DIR / "image_authentic.jpg"
    res_auth = detector.analyse(img_auth, media_type="image")
    assert res_auth.status == "OK"
    assert res_auth.abstained is False
    assert 0.0 <= res_auth.manipulation_score <= 1.0
    assert res_auth.weights_hash != ""
    assert "SwinB" in res_auth.model or "EfficientNet" in res_auth.model
    assert res_auth.latency_ms > 0.0

    img_fake = SAMPLES_DIR / "image_deepfake.jpg"
    res_fake = detector.analyse(img_fake, media_type="image")
    assert res_fake.status in ("OK", "UNAVAILABLE")


def test_real_video_abstains_without_a_published_checkpoint():
    detector = _get_real_detector()

    vid_path = SAMPLES_DIR / "video_deepfake.mp4"
    res = detector.analyse(vid_path, media_type="video")

    # Abstention is the correct forensic outcome here, and it has to look like
    # one: no score, no confidence, and no model named. NULL is not 0.0 and
    # "unavailable" is neither a finding of authenticity nor of manipulation.
    assert res.status == "UNAVAILABLE"
    assert res.abstained is True
    assert res.manipulation_score is None
    assert res.confidence is None
    assert res.model == "none"
    assert res.model_version == ""
    assert res.weights_hash == ""
    assert "SwinB" not in res.model
    assert "no trained video detector is installed" in (res.explanation or "").lower()
    # It must route to the video socket rather than silently fall through to an
    # adapter for another modality.
    assert res.extras.get("routed_modality") == "video"


def test_real_audio_inference():
    detector = _get_real_detector()

    aud_path = SAMPLES_DIR / "audio_deepfake.wav"
    res = detector.analyse(aud_path, media_type="audio")
    assert res.status == "OK"
    assert res.abstained is False
    assert 0.0 <= res.manipulation_score <= 1.0
    assert "Wav2Vec2" in res.model
    assert "chunk_scores" in res.extras.get("evidence", {}) or "chunk_scores" in res.evidence


def test_api_detect_endpoint_all_modalities(client):
    # Image
    img_path = SAMPLES_DIR / "image_authentic.jpg"
    with open(img_path, "rb") as f:
        resp_img = client.post(
            "/api/detect",
            files={"file": ("image_authentic.jpg", f, "image/jpeg")},
            data={"media_type": "image"}
        )
    assert resp_img.status_code == 200
    b_img = resp_img.json()
    assert b_img["media_type"] == "image"
    assert b_img["abstained"] is False
    assert b_img["manipulation_score"] is not None

    # Video: the endpoint must answer, and answer with an abstention rather than
    # a number. A 200 carrying score=null is the contract; a score here would
    # mean a model that cannot exist had scored the file.
    vid_path = SAMPLES_DIR / "video_deepfake.mp4"
    with open(vid_path, "rb") as f:
        resp_vid = client.post(
            "/api/detect",
            files={"file": ("video_deepfake.mp4", f, "video/mp4")},
            data={"media_type": "video"}
        )
    assert resp_vid.status_code == 200
    b_vid = resp_vid.json()
    assert b_vid["media_type"] == "video"
    assert b_vid["status"] == "UNAVAILABLE"
    assert b_vid["abstained"] is True
    assert b_vid["manipulation_score"] is None
    assert b_vid["confidence"] is None

    # Audio
    aud_path = SAMPLES_DIR / "audio_deepfake.wav"
    with open(aud_path, "rb") as f:
        resp_aud = client.post(
            "/api/detect",
            files={"file": ("audio_deepfake.wav", f, "audio/wav")},
            data={"media_type": "audio"}
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
            data={"title": "Real Model Smoke Test Case"}
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
