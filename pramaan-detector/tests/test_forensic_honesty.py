"""Regression tests for the forensic-honesty properties of the detector layer.

These are not accuracy tests. Nothing here asserts that the image model is good,
because nothing in this repository has evaluated it against a labelled
ground-truth corpus. What they assert is that the model does not *claim* more
than it measured -- which is the property a court, or a hackathon judge, can
actually check.

Each test names the defect it locks out, so a future change that reintroduces the
defect fails with an explanation rather than a bare assertion.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIDJOURNEY_DIR = REPO / "data" / "midjourney_samples"
WEIGHTS = REPO / "weights" / "image_detector.pt"


# ══════════════════════════════════════════════════════════════════════════════
# 1. make_result: confidence is optional metadata, not a gate on the measurement
# ══════════════════════════════════════════════════════════════════════════════

class TestConfidenceIsNotAGate:
    def test_a_score_survives_without_a_confidence(self):
        """`confidence=None` must not destroy a real measurement.

        The condition used to be `if score is None or confidence is None`, so a
        detector with no calibrated confidence to publish had to either invent
        one or discard the score it had genuinely computed. Both are fabrications
        -- one of a number, one of an abstention.
        """
        from pramaan.schema import make_result, LABEL_MANIPULATED

        r = make_result("image", 0.88, None, "M", "1.0", "abc", 10.0, "test")
        assert r.manipulation_score == pytest.approx(0.88)
        assert r.confidence is None
        assert r.abstained is False
        assert r.label == LABEL_MANIPULATED

    def test_missing_score_still_abstains(self):
        from pramaan.schema import make_result, LABEL_INSUFFICIENT

        r = make_result("image", None, 0.9, "M", "1.0", "abc", 10.0, "test")
        assert r.abstained is True
        assert r.manipulation_score is None
        assert r.label == LABEL_INSUFFICIENT

    def test_abstention_band_still_nulls_both_fields(self):
        """The score-based abstention band is unchanged by the confidence fix."""
        from pramaan.schema import make_result, LABEL_INSUFFICIENT

        r = make_result("image", 0.52, None, "M", "1.0", "abc", 10.0, "test")
        assert r.label == LABEL_INSUFFICIENT
        assert r.abstained is True
        assert r.manipulation_score is None
        assert r.confidence is None


# ══════════════════════════════════════════════════════════════════════════════
# 2. The image detector reports no invented confidence
# ══════════════════════════════════════════════════════════════════════════════

class TestNoDerivedConfidence:
    def test_source_does_not_pass_a_derived_confidence(self):
        """`min(abs(score - 0.5) * 2.0, 1.0)` must not be reported as confidence.

        It is a deterministic function of the score, so it adds no information: a
        score of 0.133 became "73.4% confidence" by arithmetic alone. The margin
        is still recorded, under a name that says what it is.
        """
        source = (REPO / "pramaan" / "detectors" / "image_detector.py").read_text(
            encoding="utf-8"
        )
        assert "confidence = min(abs(score - 0.5)" not in source
        assert "score_margin_from_midpoint" in source
        assert "confidence=None" in source

    def test_explanation_never_classifies_a_low_score_as_authentic(self):
        """A low score means "no evidence found", never "authentic".

        Known Midjourney output scores as low as 0.0245 on this checkpoint. The
        old wording rendered that as "Image classified as authentic", turning an
        uncalibrated miss into an affirmative finding of provenance.
        """
        from pramaan.detectors.image_detector import _explain_image

        for score in (0.0245, 0.133, 0.2853, 0.34):
            text = _explain_image(score, [])
            assert "classified as authentic" not in text.lower()
            assert "not a finding of authenticity" in text.lower() or (
                "does not separate" in text.lower()
            )

    def test_explanation_does_not_call_grad_cam_regions_suspicious(self):
        """Attention is where the network looked, not a located edit."""
        from pramaan.detectors.image_detector import _explain_image

        text = _explain_image(0.12, [{"x": 1, "y": 1, "w": 2, "h": 2}])
        assert "suspicious regions detected" not in text.lower()
        assert "grad-cam" in text.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Known AI-generated samples: the scores are recorded, not corrected
# ══════════════════════════════════════════════════════════════════════════════

# Scores measured from this checkpoint on 2026-09-04. They are LOW -- i.e. the
# model failed to flag known Midjourney and SDXL output. These numbers are pinned
# so that a future change to thresholds, preprocessing or the checkpoint is
# visible instead of silent. THE TEST DOES NOT ASSERT THE MODEL IS CORRECT. Its
# purpose is the opposite: to keep a documented record that it is not, so nobody
# downstream can describe this detector as reliable on generative imagery.
KNOWN_AI_SAMPLE_SCORES = {
    "midjourney_08.jpg": 0.133,
    "midjourney_20.jpg": 0.0245,
}

pytestmark_real_model = pytest.mark.skipif(
    not WEIGHTS.is_file(), reason="image_detector.pt not present in this checkout"
)


@pytestmark_real_model
class TestKnownGenerativeSamplesAreUnderdetected:
    @pytest.fixture(scope="class")
    def detector(self):
        from pramaan.detectors.image_detector import ImageDetector

        return ImageDetector(str(WEIGHTS))

    @pytest.mark.parametrize("filename,expected", sorted(KNOWN_AI_SAMPLE_SCORES.items()))
    def test_score_is_stable_and_low(self, detector, filename: str, expected: float):
        path = MIDJOURNEY_DIR / filename
        if not path.is_file():
            pytest.skip(f"{filename} not present in this checkout")

        result = detector.detect(str(path))
        raw = result.evidence.get("raw_score")
        assert raw is not None, "the detector must record its raw score as evidence"

        # Tolerance is wide on purpose: the point is the ORDER OF MAGNITUDE, not
        # a bit-exact float. Tightening this into a bit-exact assertion would
        # invite someone to "fix" the model by editing the expectation.
        assert raw == pytest.approx(expected, abs=0.05), (
            f"{filename} now scores {raw:.4f}, was {expected:.4f}. If this moved "
            "because the model genuinely improved, update the constant AND the "
            "documented limitation. Do not update it to make a test pass."
        )
        assert raw < 0.5, (
            "This assertion documents a KNOWN FAILURE: the checkpoint scores "
            "known AI-generated images below its own midpoint."
        )

    def test_no_calibrated_confidence_is_reported(self, detector):
        path = MIDJOURNEY_DIR / "midjourney_08.jpg"
        if not path.is_file():
            pytest.skip("sample not present")

        result = detector.detect(str(path))
        assert result.confidence is None, (
            "This model has no calibration set, so it has no calibrated "
            "confidence to report."
        )
        assert result.evidence.get("calibrated") is False
        margin = result.evidence.get("score_margin_from_midpoint")
        assert margin is not None and 0.0 <= margin <= 1.0
        assert math.isfinite(margin)

    def test_a_low_score_is_never_labelled_authentic_in_the_explanation(self, detector):
        path = MIDJOURNEY_DIR / "midjourney_20.jpg"
        if not path.is_file():
            pytest.skip("sample not present")

        result = detector.detect(str(path))
        assert "classified as authentic" not in result.explanation.lower()
