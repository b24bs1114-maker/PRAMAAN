"""
PRAMAAN Multi-Modal Detector — shared output contract.
All three detectors return a DetectionResult.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import time


LABEL_AUTHENTIC = "AUTHENTIC"
LABEL_MANIPULATED = "MANIPULATED"
LABEL_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

ABSTENTION_THRESHOLD = 0.15   # |score - 0.5| < threshold → abstain


@dataclass
class DetectionResult:
    media_type: str                          # image | video | audio
    label: str                               # AUTHENTIC | MANIPULATED | INSUFFICIENT_EVIDENCE
    manipulation_score: Optional[float]      # 0–1; None when abstained
    confidence: Optional[float]             # 0–1; None when abstained OR when the
                                            # model reports no calibrated confidence.
                                            # Never derived from manipulation_score.
    abstained: bool
    model: str
    model_version: str
    weights_hash: str
    latency_ms: float
    explanation: str
    evidence: dict = field(default_factory=dict)
    heatmap_available: bool = False
    regions: list = field(default_factory=list)   # image suspicious regions
    timestamps: list = field(default_factory=list) # video/audio suspicious times

    def to_dict(self) -> dict:
        return asdict(self)


def make_result(
    media_type: str,
    score: Optional[float],
    confidence: Optional[float],
    model: str,
    model_version: str,
    weights_hash: str,
    latency_ms: float,
    explanation: str,
    evidence: dict = None,
    heatmap_available: bool = False,
    regions: list = None,
    timestamps: list = None,
) -> DetectionResult:
    """Build a DetectionResult, applying abstention logic automatically.

    Abstention is decided by the SCORE alone -- either the detector returned no
    score, or the score sits inside the abstention band around the 0.5 midpoint.

    ``confidence`` deliberately does NOT participate. The condition used to be
    ``if score is None or confidence is None``, which meant a detector with no
    calibrated confidence to report had two options: fabricate one, or throw its
    real measurement away. Both are wrong. A model that has never been evaluated
    against a labelled ground-truth set has no calibrated probability to publish,
    and the backend contract says so outright -- "A number derived from the score
    is not a confidence" (``backend/app/services/detector.py``). So
    ``confidence=None`` now means exactly what it says -- this model reports no
    calibrated confidence -- and the score it did measure survives.
    """
    evidence = evidence or {}
    regions = regions or []
    timestamps = timestamps or []

    if score is None:
        return DetectionResult(
            media_type=media_type,
            label=LABEL_INSUFFICIENT,
            manipulation_score=None,
            confidence=None,
            abstained=True,
            model=model,
            model_version=model_version,
            weights_hash=weights_hash,
            latency_ms=latency_ms,
            explanation=explanation,
            evidence=evidence,
            heatmap_available=heatmap_available,
            regions=regions,
            timestamps=timestamps,
        )

    abstained = abs(score - 0.5) < ABSTENTION_THRESHOLD
    if abstained:
        label = LABEL_INSUFFICIENT
        score = None
        confidence = None
    else:
        label = LABEL_MANIPULATED if score >= 0.5 else LABEL_AUTHENTIC

    return DetectionResult(
        media_type=media_type,
        label=label,
        manipulation_score=score,
        confidence=confidence,
        abstained=abstained,
        model=model,
        model_version=model_version,
        weights_hash=weights_hash,
        latency_ms=latency_ms,
        explanation=explanation,
        evidence=evidence,
        heatmap_available=heatmap_available,
        regions=regions,
        timestamps=timestamps,
    )
