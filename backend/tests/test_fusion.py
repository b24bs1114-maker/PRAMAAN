"""Fusion and verdict tests (TASK 10).

The properties under test are the ones that make the verdict defensible: the
arithmetic is reproducible from the published signal list, a signal that could
not measure anything is excluded rather than scored zero, missing metadata never
counts against a file, and no threshold is presented as validated.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import fusion, provenance
from app.services.detector import DetectorAdapter, reset_detector_singleton, set_detector
from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, mp4_bytes


class StubDetector(DetectorAdapter):
    """Injected stand-in so verdict behaviour can be tested without a real model."""

    id = "test-stub"
    model_name = "stub-detector"
    model_version = "9.9.9"

    def __init__(self, score: float) -> None:
        self._score = score

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def _infer(self, image_path: Path) -> tuple[float, dict]:
        return self._score, {}


@pytest.fixture(autouse=True)
def _restore_detector():
    yield
    reset_detector_singleton()


def _verdict(client: TestClient, data: bytes, name: str, mime: str = "image/jpeg"):
    """Upload one file and return its fused verdict item."""
    case_id = client.post(
        "/api/cases/upload", files={"file": (name, data, mime)}
    ).json()["case"]["case_id"]
    response = client.post(f"/api/cases/{case_id}/verdict?refresh=true")
    assert response.status_code == 200, response.text
    body = response.json()
    return case_id, body, body["items"][0]


def _by_id(item: dict) -> dict[str, dict]:
    return {s["signal_id"]: s for s in item["signals"]}


# --------------------------------------------------------------------------- #
# Transparency
# --------------------------------------------------------------------------- #
def test_every_signal_publishes_its_full_breakdown(client: TestClient) -> None:
    reset_detector_singleton()
    _, body, item = _verdict(client, jpeg_bytes(seed=801), "fuse-breakdown.jpg")

    signals = _by_id(item)
    assert set(signals) == {
        "ai_detection",
        "perceptual_duplication",
        "metadata_integrity",
        "provenance_c2pa",
        "compression_forensics",
    }
    for signal in item["signals"]:
        # The six fields TASK 10 requires of every signal, present on all of them.
        assert set(signal) >= {
            "score",
            "weight",
            "contribution",
            "status",
            "explanation",
            "effective_weight",
        }
        assert signal["explanation"], f"{signal['signal_id']} has no explanation"
        assert signal["weight"] > 0.0
        if signal["included"]:
            assert isinstance(signal["score"], float)
            assert signal["contribution"] is not None
        else:
            assert signal["score"] is None
            assert signal["contribution"] is None
            assert signal["effective_weight"] == 0.0

    assert body["method"] == fusion.FUSION_METHOD
    assert item["verdict"] in (
        fusion.VERDICT_AUTHENTIC,
        fusion.VERDICT_MANIPULATED,
        fusion.VERDICT_INSUFFICIENT,
    )


def test_fused_score_is_reproducible_from_the_published_signals(
    client: TestClient,
) -> None:
    set_detector(StubDetector(0.5))
    _, _, item = _verdict(client, jpeg_bytes(seed=802), "fuse-arithmetic.jpg")

    included = [s for s in item["signals"] if s["included"]]
    assert included, "at least one signal must be included"

    # Normalised weights form a distribution, and contributions sum to the score.
    assert sum(s["effective_weight"] for s in included) == pytest.approx(1.0, abs=1e-6)
    recomputed = sum(s["score"] * s["effective_weight"] for s in included)
    assert recomputed == pytest.approx(item["manipulation_score"], abs=1e-6)
    assert sum(s["contribution"] for s in included) == pytest.approx(
        item["manipulation_score"], abs=1e-6
    )
    assert item["arithmetic"] is not None


def test_thresholds_and_caveat_are_published_and_not_claimed_as_validated(
    client: TestClient,
) -> None:
    reset_detector_singleton()
    _, body, item = _verdict(client, jpeg_bytes(seed=803), "fuse-caveat.jpg")

    assert set(item["thresholds"]) == {
        "manipulated_at_or_above",
        "authentic_at_or_below",
        "minimum_signal_coverage",
    }
    assert item["declared_weights"]["ai_detection"] == 0.35
    caveat = (item["caveat"] + body["caveat"]).lower()
    assert "not been validated" in caveat
    assert "prototype" in caveat
    assert "not a probability" in item["score_semantics"].lower()


# --------------------------------------------------------------------------- #
# Missing signals
# --------------------------------------------------------------------------- #
def test_unavailable_detector_is_excluded_not_scored_zero(client: TestClient) -> None:
    reset_detector_singleton()
    _, _, item = _verdict(client, jpeg_bytes(seed=804), "fuse-no-detector.jpg")

    ai = _by_id(item)["ai_detection"]
    assert ai["status"] == fusion.SIGNAL_UNAVAILABLE
    assert ai["score"] is None
    assert ai["included"] is False
    assert ai["contribution"] is None  # not 0.0 -- it did not contribute at all

    # Its 0.35 declared weight is removed from the denominator, not counted.
    assert item["signal_coverage"] == pytest.approx(
        item["available_weight"] / item["declared_weight_total"], abs=1e-6
    )
    assert item["primary_signal_available"] is False
    assert any(e["signal_id"] == "ai_detection" for e in item["excluded_signals"])


def test_stripped_metadata_is_inconclusive_and_never_counted_against_the_file(
    client: TestClient,
) -> None:
    """A platform-stripped file must not be penalised for having no EXIF."""
    reset_detector_singleton()
    _, _, item = _verdict(client, jpeg_bytes(seed=805), "fuse-stripped.jpg")

    metadata = _by_id(item)["metadata_integrity"]
    assert metadata["status"] == fusion.SIGNAL_INCONCLUSIVE
    assert metadata["score"] is None
    assert metadata["included"] is False
    assert "not evidence of manipulation" in metadata["explanation"].lower()


def test_absent_c2pa_manifest_is_inconclusive_not_adverse(client: TestClient) -> None:
    reset_detector_singleton()
    _, _, item = _verdict(client, jpeg_bytes(seed=806), "fuse-no-c2pa.jpg")

    prov = _by_id(item)["provenance_c2pa"]
    assert prov["status"] == fusion.SIGNAL_INCONCLUSIVE
    assert prov["score"] is None
    assert prov["evidence_basis"]["state"] == provenance.STATE_ABSENT
    assert "not" in prov["explanation"].lower()
    assert "evidence of manipulation" in prov["explanation"].lower()


def test_video_evidence_yields_no_verdict_at_all(client: TestClient) -> None:
    """Nothing in this build measures video, so no score may be produced."""
    reset_detector_singleton()
    _, _, item = _verdict(client, mp4_bytes(), "fuse-video.mp4", "video/mp4")

    assert item["verdict"] == fusion.VERDICT_INSUFFICIENT
    assert item["manipulation_score"] is None
    assert item["confidence"] == fusion.CONFIDENCE_NONE
    assert item["signals_available"] == 0
    assert "absence of evidence" in item["rationale"].lower()
    statuses = {s["signal_id"]: s["status"] for s in item["signals"]}
    # UNAVAILABLE, not UNSUPPORTED: no video detector is installed in this
    # deployment, and video becomes measurable the moment one is plugged in. The
    # compression forensics signal is genuinely UNSUPPORTED -- it analyses JPEG
    # quantisation tables, which a video container does not have. Both statuses
    # are excluded from the weighted mean, so this distinction changes the label's
    # truthfulness and not the arithmetic.
    assert statuses["ai_detection"] == fusion.SIGNAL_UNAVAILABLE
    assert statuses["compression_forensics"] == fusion.SIGNAL_UNSUPPORTED


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #
def test_high_detector_score_drives_a_manipulated_verdict(client: TestClient) -> None:
    set_detector(StubDetector(0.97))
    _, _, item = _verdict(client, jpeg_bytes(seed=807), "fuse-manipulated.jpg")

    assert item["verdict"] == fusion.VERDICT_MANIPULATED
    assert item["manipulation_score"] >= item["thresholds"]["manipulated_at_or_above"]
    assert _by_id(item)["ai_detection"]["score"] == 0.97
    assert "at or above the manipulated threshold" in item["rationale"]


def test_low_detector_score_with_a_primary_signal_yields_authentic(
    client: TestClient,
) -> None:
    set_detector(StubDetector(0.02))
    _, _, item = _verdict(
        client,
        jpeg_with_exif_bytes(seed=808, software="Canon EOS Utility"),
        "fuse-authentic.jpg",
    )

    assert item["primary_signal_available"] is True
    assert item["verdict"] == fusion.VERDICT_AUTHENTIC
    assert item["manipulation_score"] <= item["thresholds"]["authentic_at_or_below"]
    # An authenticity finding must still be stated as bounded.
    assert "not a guarantee" in item["rationale"].lower()


def test_authentic_is_withheld_when_no_primary_signal_ran(client: TestClient) -> None:
    """Weak heuristics alone must never produce an authenticity finding."""
    reset_detector_singleton()
    _, _, item = _verdict(client, jpeg_bytes(seed=809), "fuse-no-primary.jpg")

    assert item["primary_signal_available"] is False
    assert item["verdict"] != fusion.VERDICT_AUTHENTIC


def test_middle_band_score_reports_insufficient_evidence(client: TestClient) -> None:
    set_detector(StubDetector(0.5))
    _, _, item = _verdict(client, jpeg_bytes(seed=810), "fuse-middle.jpg")

    score = item["manipulation_score"]
    low = item["thresholds"]["authentic_at_or_below"]
    high = item["thresholds"]["manipulated_at_or_above"]
    assert low < score < high
    assert item["verdict"] == fusion.VERDICT_INSUFFICIENT
    assert "no clear direction" in item["rationale"]


# --------------------------------------------------------------------------- #
# Persistence and audit
# --------------------------------------------------------------------------- #
def test_verdict_is_persisted_and_audited(client: TestClient) -> None:
    set_detector(StubDetector(0.88))
    case_id, _, item = _verdict(client, jpeg_bytes(seed=811), "fuse-audit.jpg")

    from app.models import KIND_FUSION, AnalysisResult, AuditLog, get_session_factory

    session = get_session_factory()()
    try:
        row = (
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == item["evidence_id"],
                AnalysisResult.kind == KIND_FUSION,
            )
            .one()
        )
        entry = (
            session.query(AuditLog)
            .filter(
                AuditLog.case_id == case_id, AuditLog.event == "VERDICT_GENERATED"
            )
            .one()
        )
    finally:
        session.close()

    assert row.verdict == item["verdict"]
    assert row.score == pytest.approx(item["manipulation_score"])
    assert row.model_version == "9.9.9"
    assert entry.details["verdict"] == item["verdict"]
    assert entry.details["weights"]["ai_detection"] == 0.35
    assert entry.details["signal_coverage"] == pytest.approx(item["signal_coverage"])
    assert any(
        s["signal_id"] == "ai_detection" for s in entry.details["included_signals"]
    )


def test_second_call_reuses_the_stored_verdict(client: TestClient) -> None:
    set_detector(StubDetector(0.3))
    case_id, _, first = _verdict(client, jpeg_bytes(seed=812), "fuse-cache.jpg")

    again = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]
    assert again["cached"] is True
    assert again["manipulation_score"] == first["manipulation_score"]
    assert again["verdict"] == first["verdict"]


def test_verdict_for_unknown_case_returns_404(client: TestClient) -> None:
    assert client.post(f"/api/cases/{uuid.uuid4()}/verdict").status_code == 404


# --------------------------------------------------------------------------- #
# fuse() as a pure function
# --------------------------------------------------------------------------- #
def _signals(**payloads):
    return fusion.build_signals(**payloads)


def test_weights_are_configurable_and_change_the_arithmetic(settings) -> None:
    detector_payload = {
        "status": "OK",
        "score": 0.9,
        "model": "m",
        "model_version": "1",
    }
    forensics_payload = {
        "status": "OK",
        "score": 0.1,
        "recompression": {},
        "block_grid": {},
        "explanation": "x",
    }

    default = fusion.fuse(
        _signals(
            detector_payload=detector_payload, forensics_payload=forensics_payload
        ),
        settings,
    )
    reweighted = fusion.fuse(
        _signals(
            detector_payload=detector_payload, forensics_payload=forensics_payload
        ),
        settings.model_copy(
            update={
                "fusion_weight_ai_detection": 0.10,
                "fusion_weight_forensics": 0.90,
            }
        ),
    )

    # 0.9 and 0.1 fused; shifting weight toward the low signal must lower the score.
    assert default["manipulation_score"] > reweighted["manipulation_score"]
    assert default["declared_weights"]["ai_detection"] == 0.35
    assert reweighted["declared_weights"]["ai_detection"] == 0.10


def test_low_coverage_blocks_a_verdict(settings) -> None:
    """A single low-weight signal cannot carry a conclusion on its own."""
    only_forensics = fusion.fuse(
        _signals(
            forensics_payload={
                "status": "OK",
                "score": 0.95,
                "recompression": {},
                "block_grid": {},
                "explanation": "x",
            }
        ),
        settings,
    )

    assert only_forensics["signals_available"] == 1
    assert only_forensics["signal_coverage"] == pytest.approx(0.10, abs=1e-6)
    assert only_forensics["verdict"] == fusion.VERDICT_INSUFFICIENT
    assert only_forensics["manipulation_score"] == pytest.approx(0.95)
    assert "below the" in only_forensics["rationale"]
    assert "minimum required" in only_forensics["rationale"]


def test_low_score_without_a_primary_signal_cannot_reach_authentic(settings) -> None:
    """With coverage satisfied but no primary signal, AUTHENTIC is still withheld."""
    result = fusion.fuse(
        _signals(
            match_payload={
                "candidates": [{"evidence_id": "e1", "distance": 0, "sha256": "other"}],
                "indexed_count": 5,
            },
            metadata_payload={
                "status": "OK",
                "software": {"present": False},
                "camera": {"present": True, "make": "PRAMAAN"},
                "timestamps": {"exif_datetime_original": "2026:01:15 09:30:00"},
                "presence_summary": {"fields_present": ["camera"], "fields_missing": []},
            },
            forensics_payload={
                "status": "OK",
                "score": 0.1,
                "recompression": {},
                "block_grid": {},
                "explanation": "x",
            },
            sha256="mine",
        ),
        settings,
    )

    assert result["primary_signal_available"] is False
    assert result["signal_coverage"] >= settings.fusion_min_effective_weight
    assert result["manipulation_score"] <= settings.verdict_authentic_threshold
    assert result["verdict"] == fusion.VERDICT_INSUFFICIENT
    assert "no primary signal" in result["rationale"].lower()


def test_verified_c2pa_manifest_declaring_ai_is_scored_higher_than_unverified(
    settings,
) -> None:
    verified = fusion.provenance_signal(
        {
            "status": "OK",
            "state": provenance.STATE_VERIFIED,
            "manifest_present": True,
            "signature_validated": True,
            "declared": {
                "declares_generative_ai": True,
                "generative_source_types": ["trainedAlgorithmicMedia"],
            },
        }
    )
    unverified = fusion.provenance_signal(
        {
            "status": "OK",
            "state": provenance.STATE_UNVERIFIED,
            "manifest_present": True,
            "signature_validated": False,
            "declared": {
                "declares_generative_ai": True,
                "generative_source_types": ["trainedAlgorithmicMedia"],
            },
        }
    )
    invalid = fusion.provenance_signal(
        {
            "status": "OK",
            "state": provenance.STATE_INVALID,
            "manifest_present": True,
            "signature_validated": False,
        }
    )

    assert verified["score"] > unverified["score"]
    assert verified["status"] == unverified["status"] == fusion.SIGNAL_OK
    assert "self-declaration" in unverified["explanation"]
    assert invalid["score"] == fusion.PROVENANCE_INVALID_SIGNATURE
    assert "FAILED validation" in invalid["explanation"]


def test_metadata_generative_software_outranks_an_editor_tag() -> None:
    generative = fusion.metadata_signal(
        {
            "status": "OK",
            "software": {
                "present": True,
                "value": "Stable Diffusion 1.5",
                "generative_hint": "stable diffusion",
                "editor_hint": None,
            },
            "camera": {},
            "timestamps": {},
            "presence_summary": {"fields_present": ["software"], "fields_missing": []},
        }
    )
    editor = fusion.metadata_signal(
        {
            "status": "OK",
            "software": {
                "present": True,
                "value": "Adobe Photoshop 25.0",
                "generative_hint": None,
                "editor_hint": "photoshop",
            },
            "camera": {},
            "timestamps": {},
            "presence_summary": {"fields_present": ["software"], "fields_missing": []},
        }
    )

    assert generative["score"] == fusion.METADATA_GENERATIVE_SOFTWARE
    assert editor["score"] == fusion.METADATA_EDITOR_SOFTWARE
    assert generative["score"] > editor["score"]
    # Editing software must be explicitly framed as not proof of deception.
    assert (
        "not by itself evidence of deceptive alteration"
        in editor["explanation"].lower()
    )


def test_perceptual_signal_reports_derivation_not_deception() -> None:
    signal = fusion.perceptual_signal(
        {
            "candidates": [
                {
                    "evidence_id": "e1",
                    "distance": 8,
                    "similarity": 0.875,
                    "confidence_band": "possible_candidate",
                    "sha256": "other",
                }
            ],
            "indexed_count": 10,
            "max_distance": 12,
        },
        sha256="mine",
    )

    assert signal["status"] == fusion.SIGNAL_OK
    assert signal["score"] == pytest.approx(
        fusion.PERCEPTUAL_BASE + 8 * fusion.PERCEPTUAL_PER_BIT
    )
    assert signal["score"] < fusion.PERCEPTUAL_CEILING
    assert "DERIVATION, not deception" in signal["explanation"]

    empty = fusion.perceptual_signal({"candidates": [], "notes": ["index is empty"]})
    assert empty["status"] == fusion.SIGNAL_INCONCLUSIVE
    assert empty["score"] is None
    assert "not evidence of authenticity" in empty["explanation"].lower()


def test_no_signal_at_all_produces_no_score(settings) -> None:
    result = fusion.fuse(_signals(), settings)

    assert result["verdict"] == fusion.VERDICT_INSUFFICIENT
    assert result["manipulation_score"] is None
    assert result["signals_available"] == 0
    assert result["confidence"] == fusion.CONFIDENCE_NONE
    assert len(result["excluded_signals"]) == 5


# --------------------------------------------------------------------------- #
# Detector signal admission (PHASE 5) and the five detector states (PHASE 6)
# --------------------------------------------------------------------------- #
def test_valid_detector_score_is_included() -> None:
    signal = fusion.ai_detection_signal(
        {"status": "OK", "abstained": False, "score": 0.72, "model": "m", "model_version": "1"}
    )
    assert signal["status"] == fusion.SIGNAL_OK
    assert signal["score"] == pytest.approx(0.72)
    assert signal["evidence_basis"]["availability"] == "scored"


@pytest.mark.parametrize(
    "status",
    ["ERROR", "UNSUPPORTED_MEDIA", "UNAVAILABLE"],
)
def test_non_ok_status_never_contributes_a_score(status: str) -> None:
    """A stray number on a failed payload is not a measurement.

    The previous admission test was `(status == STATUS_OK or not abstained)`. The
    `or` meant any payload that simply did not declare an abstention was treated
    as OK, so an ERROR payload still carrying a leftover score was fused in as a
    valid assessed signal.
    """
    signal = fusion.ai_detection_signal({"status": status, "score": 0.91})
    assert signal["status"] != fusion.SIGNAL_OK
    assert signal["score"] is None


def test_explicit_abstention_is_not_overridden_by_an_ok_status() -> None:
    """Abstention is a valid forensic outcome; the detector's own word is final."""
    signal = fusion.ai_detection_signal(
        {"status": "OK", "abstained": True, "score": 0.44, "latency_ms": 12.0}
    )
    assert signal["status"] == fusion.SIGNAL_INCONCLUSIVE
    assert signal["score"] is None
    assert signal["evidence_basis"]["availability"] == "ran_and_declined"


@pytest.mark.parametrize("bad_score", [None, "0.5", float("nan"), float("inf"), 1.5, -0.1, True])
def test_unusable_score_on_an_ok_payload_is_a_detector_fault(bad_score) -> None:
    """A score must be a finite real number in 0..1 or it is not a score.

    `True` is in this list deliberately: `isinstance(True, int)` is true in
    Python, so a boolean would otherwise have been fused in as the score 1.0.
    """
    signal = fusion.ai_detection_signal(
        {"status": "OK", "abstained": False, "score": bad_score}
    )
    assert signal["score"] is None
    assert signal["status"] != fusion.SIGNAL_OK


def test_five_detector_states_stay_distinguishable() -> None:
    """PHASE 6: not-installed, disabled, ran-and-declined and scored are separate.

    All four non-scored outcomes are excluded from the fused mean, so the score is
    unaffected; what must survive is the RECORD of which one happened. A reader
    cannot weigh a verdict if "we own no model" and "our model looked and would
    not say" print the same sentence.
    """
    from app.services import detector as detector_service

    not_installed = fusion.ai_detection_signal(
        {"status": "UNAVAILABLE", "abstained": True,
         "detail": detector_service.UNAVAILABLE_EXPLANATION}
    )
    disabled = fusion.ai_detection_signal(
        {"status": "UNAVAILABLE", "abstained": True,
         "detail": f"{detector_service.DISABLED_REASON} {detector_service.UNAVAILABLE_EXPLANATION}"}
    )
    declined = fusion.ai_detection_signal(
        {"status": "UNAVAILABLE", "abstained": True, "latency_ms": 143.7,
         "detail": detector_service.DECLINED_EXPLANATION}
    )
    scored = fusion.ai_detection_signal(
        {"status": "OK", "abstained": False, "score": 0.31, "inference_ms": 88.1}
    )
    stage_never_ran = fusion.ai_detection_signal(None)

    assert not_installed["evidence_basis"]["availability"] == "not_installed"
    assert disabled["evidence_basis"]["availability"] == "disabled_by_config"
    assert declined["evidence_basis"]["availability"] == "ran_and_declined"
    assert scored["evidence_basis"]["availability"] == "scored"
    assert stage_never_ran["evidence_basis"]["availability"] == "not_installed"

    # Four distinct availability records, and the one that ran is marked as
    # having run rather than as an absent detector.
    assert declined["status"] == fusion.SIGNAL_INCONCLUSIVE
    assert not_installed["status"] == fusion.SIGNAL_UNAVAILABLE
    assert disabled["status"] == fusion.SIGNAL_UNAVAILABLE
    assert "disabled by configuration" in disabled["explanation"]
    assert "ran and returned no score" in declined["explanation"]
    assert "No detector is installed" in not_installed["explanation"]

    # None of the four excluded states asserts anything about the file.
    for excluded in (not_installed, disabled, declined, stage_never_ran):
        assert excluded["score"] is None
        assert excluded["included"] is False


def test_an_excluded_detector_never_drags_the_fused_score_toward_zero(settings) -> None:
    """NULL is not 0. PHASE 5's renormalisation must hold for every excluded state."""
    forensics_payload = {
        "status": "OK",
        "score": 0.80,
        "recompression": {},
        "block_grid": {},
        "explanation": "x",
    }
    for detector_payload in (
        {"status": "ERROR", "score": 0.0},
        {"status": "UNAVAILABLE", "abstained": True},
        {"status": "OK", "abstained": True, "score": 0.0, "latency_ms": 5.0},
    ):
        result = fusion.fuse(
            _signals(
                detector_payload=detector_payload, forensics_payload=forensics_payload
            ),
            settings,
        )
        ai = next(s for s in result["signals"] if s["signal_id"] == "ai_detection")
        assert ai["included"] is False
        assert ai["contribution"] is None
        # The forensics score survives untouched; it is not averaged with a zero
        # that was never measured.
        assert result["manipulation_score"] == pytest.approx(0.80)


def test_a_low_uncalibrated_detector_score_is_never_called_verified(settings) -> None:
    """An AUTHENTIC band is a non-finding, and must read as one.

    The image checkpoint scores known Midjourney output as low as 0.0245, so a
    low score genuinely does drive this branch. That is the model's documented
    weakness and is NOT corrected by moving the threshold. What must hold is that
    the verdict never upgrades "no evidence of manipulation was found" into a
    verification of authenticity.
    """
    result = fusion.fuse(
        _signals(
            detector_payload={
                "status": "OK",
                "abstained": False,
                "score": 0.0245,
                "model": "SwinB",
                "model_version": "1",
            }
        ),
        settings,
    )

    rationale = result["rationale"].lower()
    assert "not a guarantee that the media is unaltered" in rationale
    for forbidden in ("verified authentic", "confirmed authentic", "proven", "genuine"):
        assert forbidden not in rationale
    # The strongest band this system emits, even here.
    assert result["confidence"] in {
        fusion.CONFIDENCE_NONE,
        fusion.CONFIDENCE_LOW,
        fusion.CONFIDENCE_MODERATE,
    }
    assert result["confidence"] != "high"
