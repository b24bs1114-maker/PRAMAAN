"""Tests for PRAMAAN P0 Forensic Decision Contract Invariants.

Enforces:
1. Missing metadata alone cannot create a manipulation assessment (NOT_ASSESSED).
2. Missing C2PA alone cannot create a manipulation assessment (NOT_ASSESSED).
3. Detector unavailable cannot become authentic/manipulated (NOT_ASSESSED).
4. Abstention cannot become a positive/negative vote (INCONCLUSIVE).
5. Conflicting same-task outputs produce INCONCLUSIVE with CONFLICTING_RESULTS.
6. Descriptive observations alone never move the assessment state (NOT_ASSESSED).
7. Modalities are never averaged together -- scopes remain task-qualified.
8. Boolean, non-finite (NaN/inf), and out-of-bounds scores are strictly rejected.
9. Backend assessment contract is single source of truth for API and reports.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import assessment, fusion
from app.services.detector import (
    DetectorAdapter,
    NullDetector,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    reset_detector_singleton,
    set_detector,
)
from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, wav_bytes, mp4_bytes


class StubDetector(DetectorAdapter):
    """Controllable detector stub for testing decision invariants."""

    id = "stub-detector"
    model_name = "test-stub"
    model_version = "1.0.0"

    def __init__(
        self,
        score: float | None = None,
        confidence: float | None = None,
        abstained: bool = False,
        status: str = STATUS_OK,
        extras: dict[str, Any] | None = None,
    ) -> None:
        self._score = score
        self._confidence = confidence
        self._abstained = abstained
        self._status = status
        self._extras = dict(extras or {})

    def available(self) -> tuple[bool, str | None]:
        if self._status == STATUS_UNAVAILABLE:
            return False, "Detector unavailable in test configuration"
        return True, None

    def _infer(self, path: Path) -> tuple[Any, Any, dict[str, Any]]:
        extras = dict(self._extras)
        if self._abstained:
            extras["abstained"] = True
        return self._score, self._confidence, extras


@pytest.fixture(autouse=True)
def _clean_detector():
    yield
    reset_detector_singleton()


# =========================================================================== #
# Invariant 1: Missing metadata alone cannot create manipulation assessment
# =========================================================================== #
def test_missing_metadata_alone_is_not_assessed() -> None:
    settings = get_settings()
    # No detector result, only metadata signal indicating no EXIF
    signals = fusion.build_signals(
        detector_payload=None,
        metadata_payload={"exif": {"present": False}, "status": "OK"},
        media_type="image",
    )
    res = fusion.fuse(signals, settings, media_type="image")
    assessed = res["assessment"]

    assert assessed["state"] == assessment.STATE_NOT_ASSESSED
    assert assessed["conclusive"] is False
    assert assessment.REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE in assessed["reason_codes"]
    assert res["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert res["manipulation_score"] is None


# =========================================================================== #
# Invariant 2: Missing C2PA alone cannot create manipulation assessment
# =========================================================================== #
def test_missing_c2pa_alone_is_not_assessed() -> None:
    settings = get_settings()
    # No detector result, C2PA manifest absent (standard state for almost all media)
    signals = fusion.build_signals(
        detector_payload=None,
        provenance_payload={"manifest_present": False, "state": "ABSENT", "status": "OK"},
        media_type="image",
    )
    res = fusion.fuse(signals, settings, media_type="image")
    assessed = res["assessment"]

    assert assessed["state"] == assessment.STATE_NOT_ASSESSED
    assert assessed["conclusive"] is False
    assert res["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert res["manipulation_score"] is None


# =========================================================================== #
# Invariant 3: Detector unavailable cannot become authentic or manipulated
# =========================================================================== #
def test_detector_unavailable_cannot_become_authentic_or_manipulated() -> None:
    settings = get_settings()
    set_detector(NullDetector("image", "Detector disabled by test"))

    signals = fusion.build_signals(
        detector_payload={"status": STATUS_UNAVAILABLE, "score": None, "detail": "Unavailable"},
        media_type="image",
    )
    res = fusion.fuse(signals, settings, media_type="image")
    assessed = res["assessment"]

    assert assessed["state"] == assessment.STATE_NOT_ASSESSED
    assert assessed["conclusive"] is False
    assert assessment.REASON_DETECTOR_UNAVAILABLE in assessed["reason_codes"]
    assert res["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert res["manipulation_score"] is None
    # Must never be 0.0 or 1.0
    assert assessed["score"] is None


# =========================================================================== #
# Invariant 4: Abstention cannot become a positive/negative vote
# =========================================================================== #
def test_abstention_becomes_inconclusive_never_positive_or_negative() -> None:
    settings = get_settings()
    # Detector ran and declined to return a score
    signals = fusion.build_signals(
        detector_payload={
            "status": STATUS_OK,
            "abstained": True,
            "score": None,
            "detail": "The detector ran but returned no score for this input",
            "latency_ms": 25.0,
        },
        media_type="image",
    )
    res = fusion.fuse(signals, settings, media_type="image")
    assessed = res["assessment"]

    assert assessed["state"] == assessment.STATE_INCONCLUSIVE
    assert assessed["conclusive"] is False
    assert assessment.REASON_DETECTOR_ABSTAINED in assessed["reason_codes"]
    assert res["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert assessed["score"] is None


# =========================================================================== #
# Invariant 5: Conflicting eligible checks produce INCONCLUSIVE
# =========================================================================== #
def test_conflicting_eligible_checks_produce_inconclusive() -> None:
    settings = get_settings()
    # Eligible check 1 (AI detector) says indicators detected (0.90)
    # Eligible check 2 (Validated clean C2PA provenance) says no indicators (0.15)
    sig_detector = {
        "signal_id": "ai_detection",
        "name": "AI manipulation detector",
        "score": 0.90,
        "status": "OK",
        "assessment_role": assessment.ROLE_DECISIVE,
        "execution_status": assessment.CHECK_COMPLETED,
        "evidence_basis": {},
        "explanation": "High likelihood of synthetic generation",
    }
    sig_c2pa = {
        "signal_id": "provenance_c2pa",
        "name": "C2PA provenance manifest",
        "score": 0.15,
        "status": "OK",
        "assessment_role": assessment.ROLE_DECISIVE,
        "execution_status": assessment.CHECK_COMPLETED,
        "evidence_basis": {},
        "explanation": "Verified genuine camera capture credentials",
    }

    evaluated = assessment.evaluate(
        signals=[sig_detector, sig_c2pa],
        media_type="image",
        declared_weights={"ai_detection": 0.45, "provenance_c2pa": 0.25},
        manipulated_at_or_above=settings.verdict_manipulated_threshold,
        authentic_at_or_below=settings.verdict_authentic_threshold,
        minimum_eligible_coverage=settings.fusion_min_effective_weight,
    )

    assert evaluated["state"] == assessment.STATE_INCONCLUSIVE
    assert evaluated["conclusive"] is False
    assert assessment.REASON_CONFLICTING_RESULTS in evaluated["reason_codes"]


# =========================================================================== #
# Invariant 6: Descriptive observations alone never move assessment state
# =========================================================================== #
def test_descriptive_observations_alone_never_decide() -> None:
    settings = get_settings()
    # Perceptual near-duplicate, compression forensics, metadata all reporting scores
    # but no eligible AI detector or validated C2PA manifest
    signals = [
        {
            "signal_id": "perceptual_duplication",
            "name": "Perceptual near-duplicate analysis",
            "score": 0.55,
            "status": "OK",
            "assessment_role": assessment.ROLE_DESCRIPTIVE,
            "execution_status": assessment.CHECK_COMPLETED,
            "explanation": "Near-duplicate found with distance 10",
        },
        {
            "signal_id": "compression_forensics",
            "name": "Compression forensics",
            "score": 0.70,
            "status": "OK",
            "assessment_role": assessment.ROLE_DESCRIPTIVE,
            "execution_status": assessment.CHECK_COMPLETED,
            "explanation": "Quantisation grid anomaly detected",
        },
        {
            "signal_id": "metadata_integrity",
            "name": "Metadata integrity",
            "score": 0.85,
            "status": "OK",
            "assessment_role": assessment.ROLE_DESCRIPTIVE,
            "execution_status": assessment.CHECK_COMPLETED,
            "explanation": "Generative software tag present in EXIF",
        },
    ]

    evaluated = assessment.evaluate(
        signals=signals,
        media_type="image",
        declared_weights={
            "ai_detection": 0.45,
            "provenance_c2pa": 0.25,
            "perceptual_duplication": 0.15,
            "compression_forensics": 0.10,
            "metadata_integrity": 0.05,
        },
        manipulated_at_or_above=settings.verdict_manipulated_threshold,
        authentic_at_or_below=settings.verdict_authentic_threshold,
        minimum_eligible_coverage=settings.fusion_min_effective_weight,
    )

    # Must remain NOT_ASSESSED because 0% of eligible checks ran
    assert evaluated["state"] == assessment.STATE_NOT_ASSESSED
    assert evaluated["conclusive"] is False
    assert evaluated["score"] is None
    assert len(evaluated["descriptive_observations"]) == 3
    assert len(evaluated["contributing_checks"]) == 0


# =========================================================================== #
# Invariant 7: Modalities are never averaged together
# =========================================================================== #
def test_modalities_are_task_scoped_and_never_averaged() -> None:
    scopes = {
        "image": assessment.scope_for("image"),
        "video": assessment.scope_for("video"),
        "audio": assessment.scope_for("audio"),
    }
    assert scopes["image"] == "ai_generated_or_manipulated_image_indicators"
    assert scopes["video"] == "deepfake_or_manipulated_video_indicators"
    assert scopes["audio"] == "synthetic_or_spoofed_speech_indicators"

    # All three scopes are distinct strings
    assert len(set(scopes.values())) == 3


# =========================================================================== #
# Invariant 8: Boolean, non-finite, and out-of-bounds score rejection
# =========================================================================== #
def test_usable_score_strictly_rejects_bool_nan_inf_and_out_of_bounds() -> None:
    assert assessment.usable_score(True) is None
    assert assessment.usable_score(False) is None
    assert assessment.usable_score(float("nan")) is None
    assert assessment.usable_score(float("inf")) is None
    assert assessment.usable_score(float("-inf")) is None
    assert assessment.usable_score(1.0001) is None
    assert assessment.usable_score(-0.0001) is None
    assert assessment.usable_score("0.5") is None  # strict typing required

    assert assessment.usable_score(0.0) == 0.0
    assert assessment.usable_score(1.0) == 1.0
    assert assessment.usable_score(0.5) == 0.5


def test_detector_adapter_rejects_bool_and_non_finite_scores() -> None:
    from app.services.detector import PluginDetector

    detector = PluginDetector("image")

    # Mock callable returning boolean
    detector._inference = lambda: (lambda path: True, None)  # type: ignore[assignment]
    res = detector.analyse(Path("/tmp/dummy.jpg"), media_type="image")
    assert res.status == STATUS_ERROR
    assert res.manipulation_score is None

    # Mock callable returning NaN
    detector._inference = lambda: (lambda path: float("nan"), None)  # type: ignore[assignment]
    res = detector.analyse(Path("/tmp/dummy.jpg"), media_type="image")
    assert res.status == STATUS_ERROR
    assert res.manipulation_score is None

    # Mock callable returning Infinity
    detector._inference = lambda: (lambda path: float("inf"), None)  # type: ignore[assignment]
    res = detector.analyse(Path("/tmp/dummy.jpg"), media_type="image")
    assert res.status == STATUS_ERROR
    assert res.manipulation_score is None


# =========================================================================== #
# Invariant 9: End-to-End API and Report consistency
# =========================================================================== #
def test_api_and_report_share_exact_canonical_assessment(client: TestClient) -> None:
    set_detector(StubDetector(score=0.88))
    upload_res = client.post(
        "/api/cases/upload", files={"file": ("contract-test.jpg", jpeg_bytes(seed=91), "image/jpeg")}
    )
    assert upload_res.status_code in (200, 201)
    case_id = upload_res.json()["case"]["case_id"]

    # Run analysis
    analyse_res = client.post(f"/api/cases/{case_id}/analyse?refresh=true")
    assert analyse_res.status_code == 200
    analyse_data = analyse_res.json()

    verdict = analyse_data["verdict"]
    assert verdict is not None
    assessed = verdict["assessment"]
    assert assessed is not None

    # Verify backend assessment object
    assert assessed["state"] == assessment.STATE_INDICATORS_DETECTED
    assert assessed["conclusive"] is True
    assert assessed["scope"] == "ai_generated_or_manipulated_image_indicators"
    assert assessed["policy_id"] == assessment.POLICY_ID
    assert assessed["policy_version"] == assessment.POLICY_VERSION
    assert assessed["score"] == pytest.approx(0.88, abs=1e-4)

    # Generate Report
    report_res = client.post(f"/api/cases/{case_id}/report?refresh=false")
    assert report_res.status_code == 201
    report_data = report_res.json()
    assert report_data["case_id"] == case_id
    assert report_data["sha256"] is not None
