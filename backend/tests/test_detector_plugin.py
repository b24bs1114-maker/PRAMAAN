"""The detector plug-in contract.

These tests are the specification for what an external AI engine must provide and
what the backend guarantees in return. The module-level functions below stand in
for Rahul's inference code: they are deliberately trivial, because what is under
test is the socket, not a model.

Nothing here fabricates a detection result for the product to display. Every
score in this file comes from a function that a test explicitly installed, and
every uninstalled modality is asserted to abstain with ``None``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import detector as detector_service
from app.services.detector import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    STATUS_UNSUPPORTED,
    MultiModalDetectorService,
    clear_inference_registry,
    register_inference,
    reset_detector_singleton,
    weights_digest,
)
from tests.helpers import jpeg_bytes, mp4_bytes


# --------------------------------------------------------------------------- #
# Stand-ins for a supplied inference implementation
# --------------------------------------------------------------------------- #
def image_infer(path, *, media_type, model_path, spec):
    """A full signature: every keyword the socket offers is declared."""
    assert Path(path).is_file()
    assert media_type == "image"
    return {
        "score": 0.61,
        "confidence": 0.8,
        "model": "entrypoint-image",
        "model_version": "3.1",
        "explanation": "Frequency-domain residuals consistent with a diffusion model.",
        "heatmap_available": True,
        "regions": [{"x": 12, "y": 30, "width": 64, "height": 64, "score": 0.71}],
    }


def minimal_infer(path):
    """The smallest possible plug-in: one positional argument, one float."""
    assert Path(path).is_file()
    return 0.25


def video_infer(path, *, media_type):
    return {
        "score": 0.9,
        "confidence": 0.77,
        "model": "rahul-video-net",
        "model_version": "0.9.0",
        "explanation": "Temporal inconsistency across three segments.",
        "heatmap_available": True,
        "timestamps": [{"start_s": 1.0, "end_s": 2.5, "score": 0.94}],
        "regions": [{"x": 10, "y": 20, "width": 50, "height": 50}],
        "device": "cpu",
    }


def audio_infer(path):
    return {"score": 0.42, "model": "rahul-audio-net", "model_version": "0.2"}


def declining_infer(path):
    """A model that runs but will not commit to a score."""
    return {"score": None, "note": "input too short to assess"}


def exploding_infer(path):
    raise RuntimeError("model weights are corrupt")


def out_of_range_infer(path):
    return 1.4


def unusable_confidence_infer(path):
    return {"score": 0.5, "confidence": "high"}


@pytest.fixture(autouse=True)
def _clean_detector_state() -> Iterator[None]:
    """No registration or singleton may leak into another test."""
    clear_inference_registry()
    reset_detector_singleton()
    yield
    clear_inference_registry()
    reset_detector_singleton()


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(data)
    return target


# --------------------------------------------------------------------------- #
# Installing a model
# --------------------------------------------------------------------------- #
def test_registered_video_model_produces_a_real_result(settings, tmp_path) -> None:
    """Registering an inference callable is the whole video integration."""
    register_inference(
        "video", video_infer, model_name="rahul-video-net", model_version="0.9.0"
    )
    service = MultiModalDetectorService(settings)
    clip = _write(tmp_path, "clip.mp4", mp4_bytes())

    result = service.analyse(clip, media_type="video")

    assert result.status == STATUS_OK
    assert result.abstained is False
    assert result.manipulation_score == 0.9
    assert result.confidence == 0.77           # the model's own, not derived
    assert result.model == "rahul-video-net"
    assert result.model_version == "0.9.0"
    assert result.label == "ai_manipulation_likelihood"
    assert result.media_type == "video"
    assert result.latency_ms is not None and result.latency_ms >= 0
    # Model-supplied explanation and localisation reach the UI unaltered.
    assert "Temporal inconsistency" in result.explanation
    assert result.heatmap_available is True
    assert result.timestamps == [{"start_s": 1.0, "end_s": 2.5, "score": 0.94}]
    assert result.regions[0]["width"] == 50
    # Anything the contract does not name is carried through, not dropped.
    assert result.extras["device"] == "cpu"
    assert result.extras["routed_modality"] == "video"
    assert result.extras["routed_adapter"] == "video_classifier"


def test_registered_audio_model_produces_a_real_result(settings, tmp_path) -> None:
    register_inference("audio", audio_infer)
    service = MultiModalDetectorService(settings)
    clip = _write(tmp_path, "voice.wav", b"RIFF____WAVEfmt ")

    result = service.analyse(clip, media_type="audio")

    assert result.status == STATUS_OK
    assert result.manipulation_score == 0.42
    assert result.model == "rahul-audio-net"
    # The model reported no confidence, so there is none. Not 0.0, not derived.
    assert result.confidence is None
    assert result.extras["routed_adapter"] == "audio_classifier"


def test_minimal_plugin_signature_is_enough(settings, tmp_path) -> None:
    register_inference("image", minimal_infer)
    service = MultiModalDetectorService(settings)
    image = _write(tmp_path, "frame.jpg", jpeg_bytes(seed=41))

    result = service.analyse(image, media_type="image")

    assert result.status == STATUS_OK
    assert result.manipulation_score == 0.25
    assert result.confidence is None


def test_entrypoint_configuration_is_resolved(settings, tmp_path) -> None:
    """The file/config route: no code in the backend imports the model."""
    configured = settings.model_copy(
        update={"image_detector_entrypoint": "tests.test_detector_plugin:image_infer"}
    )
    service = MultiModalDetectorService(configured)
    image = _write(tmp_path, "cfg.jpg", jpeg_bytes(seed=42))

    result = service.analyse(image, media_type="image")

    assert result.status == STATUS_OK
    assert result.manipulation_score == 0.61
    assert result.confidence == 0.8
    assert result.model == "entrypoint-image"
    assert result.model_version == "3.1"
    assert result.regions[0]["score"] == 0.71


def test_registration_takes_precedence_over_configuration(settings, tmp_path) -> None:
    configured = settings.model_copy(
        update={"image_detector_entrypoint": "tests.test_detector_plugin:image_infer"}
    )
    register_inference("image", minimal_infer, model_name="in-process")
    service = MultiModalDetectorService(configured)
    image = _write(tmp_path, "prec.jpg", jpeg_bytes(seed=43))

    assert service.analyse(image, media_type="image").manipulation_score == 0.25


def test_weights_hash_identifies_the_model_file(settings, tmp_path) -> None:
    """A result names the weights that produced it."""
    weights = _write(tmp_path, "audio-net.bin", b"pretend model weights")
    expected = hashlib.sha256(b"pretend model weights").hexdigest()
    configured = settings.model_copy(update={"audio_model_path": str(weights)})
    register_inference("audio", audio_infer)
    service = MultiModalDetectorService(configured)

    result = service.analyse(
        _write(tmp_path, "spoken.wav", b"RIFF____WAVEfmt "), media_type="audio"
    )

    assert result.weights_hash == expected
    assert weights_digest(weights) == expected
    # No file, no hash -- an unknown digest is empty, never a placeholder.
    assert weights_digest(tmp_path / "absent.bin") == ""
    assert weights_digest(None) == ""


# --------------------------------------------------------------------------- #
# Abstention: unavailable is never zero
# --------------------------------------------------------------------------- #
def test_uninstalled_modality_abstains_with_null(settings, tmp_path) -> None:
    service = MultiModalDetectorService(settings)
    result = service.analyse(_write(tmp_path, "c.mp4", mp4_bytes()), media_type="video")

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None      # not 0.0 and not 0.5
    assert result.confidence is None
    assert result.abstained is True
    assert result.model == "none"
    assert "not a finding of authenticity" in result.detail.lower()
    # It names both sockets, so an operator knows what to install.
    assert "PRAMAAN_VIDEO_MODEL_PATH" in result.detail
    assert "PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT" in result.detail


def test_model_file_without_inference_code_says_exactly_that(settings, tmp_path) -> None:
    weights = _write(tmp_path, "video-net.pt", b"weights")
    configured = settings.model_copy(update={"video_model_path": str(weights)})
    service = MultiModalDetectorService(configured)

    result = service.analyse(_write(tmp_path, "d.mp4", mp4_bytes()), media_type="video")

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None
    assert "no inference code is installed" in result.detail
    assert "PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT" in result.detail


def test_configured_model_file_that_is_absent_is_reported(settings, tmp_path) -> None:
    configured = settings.model_copy(
        update={"video_model_path": str(tmp_path / "missing.pt")}
    )
    service = MultiModalDetectorService(configured)

    result = service.analyse(_write(tmp_path, "e.mp4", mp4_bytes()), media_type="video")

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None
    assert "was not found" in result.detail


def test_broken_entrypoint_abstains_instead_of_raising(settings, tmp_path) -> None:
    configured = settings.model_copy(
        update={"video_detector_entrypoint": "no_such_module_at_all:run"}
    )
    service = MultiModalDetectorService(configured)

    result = service.analyse(_write(tmp_path, "f.mp4", mp4_bytes()), media_type="video")

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None
    assert "could not be imported" in result.detail


def test_entrypoint_naming_a_missing_attribute_is_reported(settings, tmp_path) -> None:
    configured = settings.model_copy(
        update={
            "video_detector_entrypoint": "tests.test_detector_plugin:not_a_function"
        }
    )
    service = MultiModalDetectorService(configured)

    result = service.analyse(_write(tmp_path, "g.mp4", mp4_bytes()), media_type="video")

    assert result.status == STATUS_UNAVAILABLE
    assert "no attribute" in result.detail


def test_media_outside_the_three_modalities_is_unsupported(settings, tmp_path) -> None:
    """UNSUPPORTED_MEDIA and UNAVAILABLE are different facts."""
    service = MultiModalDetectorService(settings)
    result = service.analyse(
        _write(tmp_path, "statement.pdf", b"%PDF-1.4"), media_type="document"
    )

    assert result.status == STATUS_UNSUPPORTED
    assert result.manipulation_score is None
    assert "not one of the media types" in result.detail
    assert "not a finding about the file" in result.detail


def test_null_backend_abstains_for_every_modality(settings, tmp_path) -> None:
    configured = settings.model_copy(update={"detector_backend": "null"})
    register_inference("image", minimal_infer)  # ignored: configuration wins
    service = MultiModalDetectorService(configured)

    result = service.analyse(
        _write(tmp_path, "off.jpg", jpeg_bytes(seed=44)), media_type="image"
    )

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None
    assert "disabled by configuration" in result.detail


# --------------------------------------------------------------------------- #
# Bad model behaviour is contained
# --------------------------------------------------------------------------- #
def test_model_that_declines_to_score_is_not_an_error(settings, tmp_path) -> None:
    register_inference("image", declining_infer)
    service = MultiModalDetectorService(settings)

    result = service.analyse(
        _write(tmp_path, "short.jpg", jpeg_bytes(seed=45)), media_type="image"
    )

    assert result.status == STATUS_UNAVAILABLE
    assert result.manipulation_score is None
    assert "returned no score" in result.detail
    assert result.latency_ms is not None  # it did run, and that was measured


def test_crashing_model_is_contained(settings, tmp_path) -> None:
    register_inference("image", exploding_infer)
    service = MultiModalDetectorService(settings)

    result = service.analyse(
        _write(tmp_path, "boom.jpg", jpeg_bytes(seed=46)), media_type="image"
    )

    assert result.status == STATUS_ERROR
    assert result.manipulation_score is None
    assert "RuntimeError" in result.detail
    assert "Traceback" not in result.detail


def test_out_of_range_plugin_score_is_rejected(settings, tmp_path) -> None:
    register_inference("image", out_of_range_infer)
    service = MultiModalDetectorService(settings)

    result = service.analyse(
        _write(tmp_path, "wild.jpg", jpeg_bytes(seed=47)), media_type="image"
    )

    assert result.status == STATUS_ERROR
    assert result.manipulation_score is None


def test_unusable_confidence_is_dropped_not_invented(settings, tmp_path) -> None:
    """A bad confidence must not invalidate a good score, nor become a number."""
    register_inference("image", unusable_confidence_infer)
    service = MultiModalDetectorService(settings)

    result = service.analyse(
        _write(tmp_path, "conf.jpg", jpeg_bytes(seed=48)), media_type="image"
    )

    assert result.status == STATUS_OK
    assert result.manipulation_score == 0.5
    assert result.confidence is None


# --------------------------------------------------------------------------- #
# Routing and status
# --------------------------------------------------------------------------- #
def test_routing_uses_declared_type_then_extension(settings) -> None:
    service = MultiModalDetectorService(settings)

    assert service.resolve_modality("image") == "image"
    assert service.resolve_modality("VIDEO") == "video"
    assert service.resolve_modality("voice") == "audio"
    assert service.resolve_modality(None, Path("a.mp4")) == "video"
    assert service.resolve_modality(None, Path("a.flac")) == "audio"
    assert service.resolve_modality(None, Path("a.png")) == "image"
    assert service.resolve_modality(None, Path("a.pdf")) is None
    assert service.resolve_modality("", None) is None
    # A declared type wins over a misleading extension.
    assert service.resolve_modality("audio", Path("a.jpg")) == "audio"


def test_status_reports_installed_and_missing_sockets(settings) -> None:
    register_inference("audio", audio_infer, model_name="rahul-audio-net")
    reset_detector_singleton()

    report = detector_service.status(settings)

    assert report["available"] is True  # one modality is enough to be usable
    assert report["registered_inference"]["audio"]["callable"] == "audio_infer"
    assert report["registered_inference"]["video"] is None
    assert report["modalities"]["audio"]["available"] is True
    assert report["modalities"]["audio"]["model"] == "rahul-audio-net"
    assert report["modalities"]["video"]["available"] is False
    assert report["modalities"]["video"]["reason"]
    assert set(report["entrypoints"]) == {"image", "video", "audio"}


# --------------------------------------------------------------------------- #
# POST /api/detect
# --------------------------------------------------------------------------- #
def test_detect_endpoint_runs_an_installed_model(client: TestClient) -> None:
    register_inference("image", image_infer)
    reset_detector_singleton()

    response = client.post(
        "/api/detect",
        files={"file": ("plug.jpg", jpeg_bytes(seed=49), "image/jpeg")},
        data={"media_type": "image"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["abstained"] is False
    assert body["manipulation_score"] == 0.61
    assert body["confidence"] == 0.8
    assert body["model"] == "entrypoint-image"
    assert body["latency_ms"] is not None
    assert body["regions"][0]["score"] == 0.71
    assert body["heatmap_available"] is True


def test_detect_endpoint_routes_on_extension_without_a_declared_type(
    client: TestClient,
) -> None:
    register_inference("video", video_infer)
    reset_detector_singleton()

    response = client.post(
        "/api/detect", files={"file": ("no-type.mp4", mp4_bytes(), "video/mp4")}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["media_type"] == "video"
    assert body["manipulation_score"] == 0.9
    assert body["timestamps"]


def test_detect_endpoint_does_not_guess_image_for_unknown_media(
    client: TestClient,
) -> None:
    register_inference("image", minimal_infer)
    reset_detector_singleton()

    response = client.post(
        "/api/detect", files={"file": ("evidence.pdf", b"%PDF-1.4 body", "application/pdf")}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["abstained"] is True
    assert body["manipulation_score"] is None
    assert "not one of the media types" in body["explanation"]


def test_detect_endpoint_rejects_an_oversized_upload(client: TestClient) -> None:
    from app.main import app

    settings = get_settings()
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"max_upload_bytes": 1024}
    )
    try:
        response = client.post(
            "/api/detect",
            files={"file": ("big.jpg", jpeg_bytes(seed=50), "image/jpeg")},
            data={"media_type": "image"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 413
    # The partially written file is not left behind.
    assert not list(settings.temp_dir.glob("detect-*"))


def test_detect_endpoint_requires_an_input(client: TestClient) -> None:
    assert client.post("/api/detect").status_code == 400


def test_detect_endpoint_on_stored_evidence_is_audited(client: TestClient) -> None:
    register_inference("image", minimal_infer)
    reset_detector_singleton()
    uploaded = client.post(
        "/api/cases/upload",
        files={"file": ("stored.jpg", jpeg_bytes(seed=51), "image/jpeg")},
    ).json()
    evidence_id = uploaded["evidence"]["evidence_id"]
    case_id = uploaded["case"]["case_id"]

    body = client.post("/api/detect", data={"evidence_id": evidence_id}).json()
    assert body["manipulation_score"] == 0.25

    trail = client.get(f"/api/cases/{case_id}/audit").json()
    probe = [
        event
        for event in trail["events"]
        if event["event"] == "DETECTOR_RUN"
        and event["details"].get("route") == "POST /api/detect"
    ]
    assert probe, "an ad-hoc examination of registered evidence must be audited"
    assert probe[-1]["details"]["persisted"] is False
    assert probe[-1]["details"]["score"] == 0.25
    # Recording the examination must not break the chain.
    assert client.post(f"/api/cases/{case_id}/audit/verify").json()["valid"] is True


def test_detect_endpoint_404s_for_unknown_evidence(client: TestClient) -> None:
    assert client.post("/api/detect", data={"evidence_id": "nope"}).status_code == 404
