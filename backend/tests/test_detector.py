"""AI detector interface tests (TASK 9).

The binding requirements under test: no fabricated scores when no model is
installed, and no detector failure may reach the client as a 500.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import detector as detector_service
from app.services.detector import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    STATUS_UNSUPPORTED,
    DetectorAdapter,
    NullDetector,
    build_detector,
    postprocess,
    reset_detector_singleton,
    set_detector,
)
from tests.helpers import jpeg_bytes, mp4_bytes


class FixedScoreDetector(DetectorAdapter):
    """Stand-in for a real pretrained model: returns a value it was handed."""

    id = "test-fixed"
    model_name = "pramaan-test-detector"
    model_version = "1.2.3"

    def __init__(self, score: float) -> None:
        self._score = score

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def _infer(self, image_path: Path) -> tuple[float, dict]:
        assert image_path.is_file()
        return self._score, {"runtime": "test"}


class ExplodingDetector(DetectorAdapter):
    id = "test-exploding"
    model_name = "broken-model"
    model_version = "0.0.1"

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def _infer(self, image_path: Path) -> tuple[float, dict]:
        raise RuntimeError("simulated model crash")


class OutOfRangeDetector(FixedScoreDetector):
    id = "test-out-of-range"


@pytest.fixture(autouse=True)
def _restore_detector():
    yield
    reset_detector_singleton()


def _upload(client: TestClient, data: bytes, name: str, mime: str = "image/jpeg"):
    return client.post("/api/cases/upload", files={"file": (name, data, mime)}).json()


def test_no_model_installed_reports_unavailable_not_a_score(
    client: TestClient, settings
) -> None:
    """This environment has no detector; the API must say so, not invent 0.0."""
    reset_detector_singleton()
    body = client.get("/api/detector/status").json()

    assert body["available"] is False
    assert body["adapter"] == "null"
    assert "score" not in body  # a status report carries no score at all
    assert body["configured_backend"] in ("auto", "null")
    assert body["reason"]
    assert "not evidence of authenticity" in body["notes"].lower()

    # Each candidate adapter explains its own unavailability. Three sockets can
    # supply an image detector: an installed inference callable, an ONNX model
    # file, a TorchScript model file.
    reasons = {c["adapter"]: c["reason"] for c in body["candidate_adapters"]}
    assert set(reasons) == {"image-plugin", "onnxruntime", "torchscript"}
    assert all(reason for reason in reasons.values())


def test_unavailable_detector_yields_null_score_on_a_case(client: TestClient) -> None:
    reset_detector_singleton()
    case_id = _upload(client, jpeg_bytes(seed=701), "detect-none.jpg")["case"]["case_id"]

    response = client.post(f"/api/cases/{case_id}/detect")
    assert response.status_code == 200, response.text
    detection = response.json()["items"][0]["detection"]

    assert detection["status"] == STATUS_UNAVAILABLE
    assert detection["score"] is None  # never 0.0 -- that would be a measurement
    assert detection["model"] == "none"
    assert detection["detail"]
    assert "not a finding of authenticity" in detection["detail"].lower()


def test_injected_model_produces_the_documented_contract(client: TestClient) -> None:
    set_detector(FixedScoreDetector(0.73))
    case_id = _upload(client, jpeg_bytes(seed=702), "detect-model.jpg")["case"][
        "case_id"
    ]

    detection = client.post(f"/api/cases/{case_id}/detect").json()["items"][0][
        "detection"
    ]

    # The contract every consumer codes against.
    assert set(("score", "model", "model_version", "status")) <= set(detection)
    assert detection["status"] == STATUS_OK
    assert detection["score"] == 0.73
    assert detection["model"] == "pramaan-test-detector"
    assert detection["model_version"] == "1.2.3"
    assert detection["adapter"] == "test-fixed"
    assert detection["inference_ms"] is not None
    assert detection["label"] == "ai_manipulation_likelihood"


def test_detector_result_is_persisted_and_audited(client: TestClient) -> None:
    set_detector(FixedScoreDetector(0.41))
    body = _upload(client, jpeg_bytes(seed=703), "detect-store.jpg")
    case_id = body["case"]["case_id"]
    evidence_id = body["evidence"]["evidence_id"]

    first = client.post(f"/api/cases/{case_id}/detect").json()["items"][0]["detection"]
    cached = client.post(f"/api/cases/{case_id}/detect").json()["items"][0]["detection"]
    assert first["cached"] is False
    assert cached["cached"] is True
    assert cached["score"] == first["score"]

    from app.models import KIND_DETECTOR, AnalysisResult, AuditLog, get_session_factory

    session = get_session_factory()()
    try:
        rows = (
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == KIND_DETECTOR,
            )
            .all()
        )
        entry = (
            session.query(AuditLog)
            .filter(AuditLog.case_id == case_id, AuditLog.event == "DETECTOR_RUN")
            .first()
        )
    finally:
        session.close()

    assert len(rows) == 1
    assert rows[0].score == 0.41
    assert rows[0].model == "pramaan-test-detector"
    assert rows[0].model_version == "1.2.3"
    assert rows[0].status == STATUS_OK
    assert entry is not None
    assert entry.details["score"] == 0.41
    assert entry.details["model_version"] == "1.2.3"


def test_failing_model_does_not_break_the_backend(client: TestClient) -> None:
    set_detector(ExplodingDetector())
    case_id = _upload(client, jpeg_bytes(seed=704), "detect-crash.jpg")["case"][
        "case_id"
    ]

    response = client.post(f"/api/cases/{case_id}/detect")
    assert response.status_code == 200, response.text
    detection = response.json()["items"][0]["detection"]

    assert detection["status"] == STATUS_ERROR
    assert detection["score"] is None
    assert "RuntimeError" in detection["detail"]
    # The client-facing detail must not leak a traceback.
    assert "Traceback" not in detection["detail"]
    assert client.get("/health").json() == {"status": "ok"}


def test_out_of_range_score_is_rejected_not_clamped(client: TestClient) -> None:
    set_detector(OutOfRangeDetector(1.8))
    case_id = _upload(client, jpeg_bytes(seed=705), "detect-range.jpg")["case"][
        "case_id"
    ]

    detection = client.post(f"/api/cases/{case_id}/detect").json()["items"][0][
        "detection"
    ]
    assert detection["status"] == STATUS_ERROR
    assert detection["score"] is None


def test_video_input_is_reported_as_unsupported(client: TestClient) -> None:
    set_detector(FixedScoreDetector(0.5))
    case_id = _upload(client, mp4_bytes(), "detect-video.mp4", "video/mp4")["case"][
        "case_id"
    ]

    detection = client.post(f"/api/cases/{case_id}/detect").json()["items"][0][
        "detection"
    ]
    assert detection["status"] == STATUS_UNSUPPORTED
    assert detection["score"] is None


def test_null_backend_setting_disables_detection(settings) -> None:
    disabled = settings.model_copy(update={"detector_backend": "null"})
    adapter = build_detector(disabled)
    assert isinstance(adapter, NullDetector)
    usable, reason = adapter.available()
    assert usable is False
    assert "disabled by configuration" in reason


def test_missing_model_file_is_reported_precisely(settings, tmp_path) -> None:
    configured = settings.model_copy(
        update={
            "detector_backend": "auto",
            "detector_model_path": str(tmp_path / "absent.onnx"),
        }
    )
    described = detector_service.status(configured)
    onnx = next(
        c for c in described["candidate_adapters"] if c["adapter"] == "onnxruntime"
    )
    assert onnx["available"] is False
    assert "not found" in onnx["reason"] or "not installed" in onnx["reason"]


def test_postprocess_reduces_logits_to_a_probability() -> None:
    import numpy as np

    spec = {"output_activation": "softmax", "positive_index": 1}
    assert postprocess(np.array([[0.0, 0.0]]), spec) == pytest.approx(0.5)
    high = postprocess(np.array([[-4.0, 4.0]]), spec)
    assert 0.99 < high < 1.0
    sigmoid = postprocess(np.array([0.0]), {"output_activation": "sigmoid"})
    assert sigmoid == pytest.approx(0.5)


def test_detect_for_unknown_case_returns_404(client: TestClient) -> None:
    assert client.post(f"/api/cases/{uuid.uuid4()}/detect").status_code == 404


def test_repeated_and_concurrent_inference_handles_memory_safely(client: TestClient) -> None:
    """Repeated inference requests must succeed cleanly without memory leaks or crashes."""
    from concurrent.futures import ThreadPoolExecutor

    set_detector(FixedScoreDetector(0.85))
    case_id = _upload(client, jpeg_bytes(seed=808), "repeated_infer.jpg")["case"]["case_id"]

    def run_detect():
        return client.post(f"/api/cases/{case_id}/detect")

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(run_detect) for _ in range(10)]
        responses = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in responses)
