"""Transparent multi-signal fusion and verdict generation.

Design rules, in order of precedence:

1. **Nothing is hidden.** Every signal reports its own score, declared weight,
   effective (normalised) weight, contribution to the fused score, status, a
   plain-language explanation, and the measurements that produced it. The fused
   score is reproducible by hand from the ``signals`` list.
2. **A missing signal is missing, not zero.** Signals that could not produce a
   measurement are given status ``INCONCLUSIVE`` / ``UNAVAILABLE`` / ``ERROR`` /
   ``UNSUPPORTED_MEDIA``, are excluded from the weighted mean, and the remaining
   weights are renormalised. Absent EXIF, an absent C2PA manifest, an empty
   perceptual index and an uninstalled detector all mean *we do not know* -- they
   never push the score toward either verdict.
3. **Low coverage produces no verdict.** If the available signals do not account
   for at least ``fusion_min_effective_weight`` of the declared total, the verdict
   is ``INSUFFICIENT_EVIDENCE`` regardless of what the fused score happens to be.
4. **AUTHENTIC needs a primary signal.** The weak heuristics in this prototype
   (metadata leads, perceptual derivation, compression history) cannot establish
   authenticity. A low fused score only becomes an AUTHENTIC verdict when a
   primary signal -- a working AI detector, or a cryptographically validated C2PA
   manifest -- is among the available signals. Otherwise the honest answer is
   ``INSUFFICIENT_EVIDENCE``.

**The weights and thresholds are prototype defaults, not validated science.**
They are configurable (``PRAMAAN_FUSION_WEIGHT_*``, ``PRAMAAN_VERDICT_*``) and
every response says so. This module is a decision aid for a human examiner; it
does not certify authenticity and its output is not admissible evidence on its
own.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.config import Settings
from app.services import detector as detector_service
from app.services import forensics as forensics_service
from app.services import provenance as provenance_service

logger = logging.getLogger("pramaan.fusion")

FUSION_METHOD = "weighted mean of available signals, renormalised over coverage"
FUSION_VERSION = "1.0"

# --- Signal statuses ------------------------------------------------------- #
SIGNAL_OK = "OK"                      # produced a score; included in the mean
SIGNAL_INCONCLUSIVE = "INCONCLUSIVE"  # ran, could not decide; excluded
SIGNAL_UNAVAILABLE = "UNAVAILABLE"    # could not run at all; excluded
SIGNAL_ERROR = "ERROR"                # failed; excluded
SIGNAL_UNSUPPORTED = "UNSUPPORTED_MEDIA"  # not applicable to this media; excluded

EXCLUDED_STATUSES = (
    SIGNAL_INCONCLUSIVE,
    SIGNAL_UNAVAILABLE,
    SIGNAL_ERROR,
    SIGNAL_UNSUPPORTED,
)

# --- Verdicts -------------------------------------------------------------- #
VERDICT_AUTHENTIC = "AUTHENTIC"
VERDICT_MANIPULATED = "MANIPULATED"
VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

CONFIDENCE_NONE = "none"
CONFIDENCE_LOW = "low"
CONFIDENCE_MODERATE = "moderate"

# Signals that can carry an authenticity finding on their own strength.
PRIMARY_SIGNALS = ("ai_detection", "provenance_c2pa")

SCORE_SEMANTICS = (
    "manipulation_score runs 0.0 to 1.0, where higher means more evidence "
    "consistent with manipulation or synthetic generation. It is a weighted mean "
    "of the available signals only -- it is not a probability, and it has no "
    "calibrated error rate."
)

CAVEAT = (
    "PROTOTYPE OUTPUT. The weights and thresholds used here are configurable "
    "defaults chosen for demonstration; they have NOT been validated against a "
    "forensic reference dataset, and no error rate is known for them. This "
    "verdict is a decision aid for a qualified examiner, not a certification of "
    "authenticity, and it must not be presented as conclusive on its own."
)

# Uncalibrated ramp for the perceptual signal: Hamming distance to the closest
# near-duplicate candidate -> concern score. Capped well below the manipulation
# threshold because re-encoding and resizing produce the same distances as edits.
PERCEPTUAL_BASE = 0.15
PERCEPTUAL_PER_BIT = 0.04
PERCEPTUAL_CEILING = 0.60

# Metadata indicator scores. Each is a *lead* for an examiner, not a finding.
METADATA_GENERATIVE_SOFTWARE = 0.85
METADATA_TIMESTAMP_CONFLICT = 0.60
METADATA_EDITOR_SOFTWARE = 0.55
METADATA_CONSISTENT_CAPTURE = 0.25

# Provenance manifest states -> score.
PROVENANCE_VERIFIED_GENERATIVE = 0.85
PROVENANCE_INVALID_SIGNATURE = 0.70
PROVENANCE_UNVERIFIED_GENERATIVE = 0.60
PROVENANCE_VERIFIED_CLEAN = 0.15


SIGNAL_NAMES = {
    "ai_detection": "AI manipulation detector",
    "perceptual_duplication": "Perceptual near-duplicate analysis",
    "metadata_integrity": "Metadata integrity",
    "provenance_c2pa": "C2PA provenance manifest",
    "compression_forensics": "Compression forensics",
}


def _signal(
    signal_id: str,
    *,
    score: float | None,
    status: str,
    explanation: str,
    basis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one signal record. Weights are filled in by ``fuse``."""
    return {
        "signal_id": signal_id,
        "name": SIGNAL_NAMES.get(signal_id, signal_id),
        "score": score,
        "status": status,
        "explanation": explanation,
        "evidence_basis": basis or {},
        # Filled in during normalisation so the arithmetic stays in one place.
        "weight": 0.0,
        "effective_weight": 0.0,
        "contribution": None,
        "included": False,
    }


# --------------------------------------------------------------------------- #
# Signal 1: AI detection
# --------------------------------------------------------------------------- #
def _detector_availability(
    payload: dict[str, Any], status: str, abstained: bool | None
) -> str:
    """Which of the five detector outcomes this payload represents.

    The API vocabulary for ``status`` has four tokens, and two genuinely
    different situations share ``UNAVAILABLE``: no detector is installed, and a
    detector that ran and declined to answer. Collapsing them loses the fact that
    a model was actually applied to the evidence. The distinction is recoverable
    from fields the payload already carries -- a declined run has a measured
    inference time and the declined explanation -- so it is derived here and
    published in ``evidence_basis`` rather than by adding a status token.

    ``abstained`` is ``None`` when the payload did not declare the field at all.
    An explicit ``True`` blocks inclusion (abstention is a valid outcome and the
    detector's own word on it is final); absence does not, because absence is not
    an abstention and inventing one would discard a real measurement.

    Returns one of: ``scored``, ``ran_and_declined``, ``disabled_by_config``,
    ``not_installed``, ``errored``, ``unsupported_media``.
    """
    if status == detector_service.STATUS_ERROR:
        return "errored"
    if status == detector_service.STATUS_UNSUPPORTED:
        return "unsupported_media"
    if status == detector_service.STATUS_OK and abstained is not True:
        return "scored"
    if status == detector_service.STATUS_OK and abstained is True:
        # The detector reported success and an abstention in the same breath.
        # It ran, so say so, and exclude it.
        return "ran_and_declined"

    detail = str(payload.get("detail") or payload.get("reason") or "")
    if "disabled" in detail.lower():
        return "disabled_by_config"
    if detector_service.DECLINED_EXPLANATION[:60] in detail:
        return "ran_and_declined"
    # A measured inference time means the model was loaded and applied, whatever
    # else went wrong afterwards.
    if payload.get("inference_ms") is not None or payload.get("latency_ms") is not None:
        return "ran_and_declined"
    return "not_installed"


#: How each availability outcome maps onto the fusion signal vocabulary. Every
#: one of them is excluded from the fused score; the difference is what the
#: record says happened, which is what a reader needs to judge the verdict.
_AVAILABILITY_TO_SIGNAL_STATUS = {
    "ran_and_declined": SIGNAL_INCONCLUSIVE,
    "errored": SIGNAL_ERROR,
    "unsupported_media": SIGNAL_UNSUPPORTED,
    "disabled_by_config": SIGNAL_UNAVAILABLE,
    "not_installed": SIGNAL_UNAVAILABLE,
}


def ai_detection_signal(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap the detector adapter's output as a fusion signal."""
    if not payload:
        return _signal(
            "ai_detection",
            score=None,
            status=SIGNAL_UNAVAILABLE,
            explanation=(
                "The AI detector stage did not run for this item, so no score is "
                "available. " + detector_service.UNAVAILABLE_EXPLANATION
            ),
            basis={"availability": "not_installed", "detector_status": None},
        )

    status = str(payload.get("status", detector_service.STATUS_UNAVAILABLE))
    score = payload.get("score") if payload.get("score") is not None else payload.get("manipulation_score")
    # Tri-state on purpose: True, False, or "the payload never said". An explicit
    # abstention is decisive; silence is not turned into one. The previous code
    # read `payload.get("abstained", False)`, which combined with the `or` below
    # meant an ERROR payload carrying a leftover number was admitted to the fused
    # score as a valid measurement.
    declared = payload.get("abstained")
    abstained = bool(declared) if declared is not None else None
    availability = _detector_availability(payload, status, abstained)
    basis = {
        "model": payload.get("model"),
        "model_version": payload.get("model_version"),
        "weights_hash": payload.get("weights_hash"),
        "adapter": payload.get("adapter"),
        "detector_status": status,
        "availability": availability,
        "interface_version": payload.get("interface_version"),
        "inference_ms": payload.get("inference_ms") or payload.get("latency_ms"),
        # Kept distinct from inference: a cold worker pays the checkpoint load,
        # and folding that into the per-file time overstates it. ``None`` means
        # either no load happened on this call or the adapter cannot split them.
        "model_load_ms": payload.get("model_load_ms"),
        "score_direction": "Higher value [0.0 to 1.0] indicates higher likelihood of AI manipulation/generation",
    }

    # Inclusion requires BOTH a genuinely OK status AND a usable score. The
    # previous condition was `(status == STATUS_OK or not abstained)`, whose `or`
    # let any payload that merely failed to declare an abstention in as OK --
    # including STATUS_ERROR and STATUS_UNSUPPORTED_MEDIA payloads that still
    # carried a number. A score is usable only if it is a real, finite number in
    # the 0..1 range the semantics declare; bools are rejected because `True`
    # passes `isinstance(x, int)`.
    score_is_valid = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(float(score))
        and 0.0 <= float(score) <= 1.0
    )
    if availability == "scored" and score_is_valid:
        return _signal(
            "ai_detection",
            score=float(score),  # type: ignore[arg-type]
            status=SIGNAL_OK,
            explanation=(
                f"Model {payload.get('model')} v{payload.get('model_version')} "
                f"returned {float(score):.4f} for "  # type: ignore[arg-type]
                f"{payload.get('label', 'ai_manipulation_likelihood')}. "
                + detector_service.SCORE_SEMANTICS
            ),
            basis=basis,
        )

    if availability == "scored" and not score_is_valid:
        # The detector reported success and then handed over something that is
        # not a score. That is a detector fault, recorded as one.
        basis["rejected_score"] = repr(score)
        return _signal(
            "ai_detection",
            score=None,
            status=SIGNAL_ERROR,
            explanation=(
                "The detector reported success but returned no usable score "
                f"({score!r} is not a finite number in 0..1), so the AI-detection "
                "signal is excluded from fusion. This is NOT a finding of "
                "authenticity and NOT a finding of manipulation."
            ),
            basis=basis,
        )

    mapped = _AVAILABILITY_TO_SIGNAL_STATUS.get(availability, SIGNAL_UNAVAILABLE)
    detail = payload.get("detail") or detector_service.UNAVAILABLE_EXPLANATION
    prefix = {
        "ran_and_declined": "The detector ran and returned no score",
        "disabled_by_config": "The detector is disabled by configuration",
        "not_installed": "No detector is installed in this deployment",
        "errored": "The detector failed during analysis",
        "unsupported_media": "The detector does not handle this media type",
    }.get(availability, "No detector score")
    return _signal(
        "ai_detection",
        score=None,
        status=mapped,
        explanation=f"{prefix} ({status}). {detail}",
        basis=basis,
    )


# --------------------------------------------------------------------------- #
# Signal 2: perceptual near-duplicate analysis
# --------------------------------------------------------------------------- #
def perceptual_signal(
    match_payload: dict[str, Any] | None, *, sha256: str | None = None
) -> dict[str, Any]:
    """Score how far this item has drifted from its closest indexed near-duplicate.

    What this measures is *derivation*, not deception: a re-encoded, resized or
    cropped copy differs from its parent exactly as an edited copy does. The score
    is capped accordingly and the explanation says so.
    """
    if not match_payload:
        return _signal(
            "perceptual_duplication",
            score=None,
            status=SIGNAL_UNAVAILABLE,
            explanation=(
                "Near-duplicate retrieval did not run for this item, so no "
                "comparison against the indexed corpus is available."
            ),
        )

    candidates = match_payload.get("candidates") or []
    basis = {
        "indexed_count": match_payload.get("indexed_count"),
        "index_backend": match_payload.get("index_backend"),
        "candidate_count": len(candidates),
        "max_distance": match_payload.get("max_distance"),
        "notes": match_payload.get("notes", []),
    }

    if not candidates:
        reason = (
            "; ".join(match_payload.get("notes", []))
            or "No near-duplicate candidates were retrieved from the indexed corpus."
        )
        return _signal(
            "perceptual_duplication",
            score=None,
            status=SIGNAL_INCONCLUSIVE,
            explanation=(
                f"{reason} Absence of candidates is NOT evidence of authenticity "
                "or of manipulation: the local corpus is a partial view of what "
                "exists."
            ),
            basis=basis,
        )

    closest = candidates[0]
    distance = int(closest["distance"])
    byte_identical = bool(sha256) and any(c.get("sha256") == sha256 for c in candidates)
    basis.update(
        closest_evidence_id=closest.get("evidence_id"),
        closest_distance=distance,
        closest_similarity=closest.get("similarity"),
        closest_confidence_band=closest.get("confidence_band"),
        byte_identical_copy_indexed=byte_identical,
    )

    if byte_identical:
        return _signal(
            "perceptual_duplication",
            score=PERCEPTUAL_BASE,
            status=SIGNAL_OK,
            explanation=(
                "A byte-identical copy of this file is already in the indexed "
                "corpus (matching SHA-256), so there is no content difference to "
                "account for. Scored at the floor: redistribution is not "
                "manipulation."
            ),
            basis=basis,
        )

    score = min(PERCEPTUAL_BASE + PERCEPTUAL_PER_BIT * distance, PERCEPTUAL_CEILING)
    return _signal(
        "perceptual_duplication",
        score=round(score, 4),
        status=SIGNAL_OK,
        explanation=(
            f"The closest indexed near-duplicate candidate differs by a Hamming "
            f"distance of {distance} bits (similarity "
            f"{closest.get('similarity')}), so this item is visually near-identical "
            f"to an indexed instance but not identical to it. Uncalibrated ramp: "
            f"{PERCEPTUAL_BASE:.2f} + {PERCEPTUAL_PER_BIT:.2f} per bit, capped at "
            f"{PERCEPTUAL_CEILING:.2f}. This measures DERIVATION, not deception -- "
            "re-encoding, resizing and cropping produce the same distances as an "
            "edit, which is why the cap sits below the manipulation threshold."
        ),
        basis=basis,
    )


# --------------------------------------------------------------------------- #
# Signal 3: metadata integrity
# --------------------------------------------------------------------------- #
def metadata_signal(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Score metadata *contents*. Absence of metadata is never scored.

    Enforces the project rule: missing metadata must never automatically mean
    manipulation. Only affirmative, readable indicators produce a score.
    """
    if not payload:
        return _signal(
            "metadata_integrity",
            score=None,
            status=SIGNAL_UNAVAILABLE,
            explanation="Metadata extraction did not run for this item.",
        )
    if str(payload.get("status", "OK")) not in ("OK", ""):
        return _signal(
            "metadata_integrity",
            score=None,
            status=SIGNAL_ERROR,
            explanation=(
                "Metadata extraction reported "
                f"{payload.get('status')}: {payload.get('detail', 'no detail')}."
            ),
        )

    software = payload.get("software") or {}
    camera = payload.get("camera") or {}
    timestamps = payload.get("timestamps") or {}
    summary = payload.get("presence_summary") or {}

    basis: dict[str, Any] = {
        "fields_present": summary.get("fields_present", []),
        "fields_missing": summary.get("fields_missing", []),
        "software_value": software.get("value"),
        "editor_hint": software.get("editor_hint"),
        "generative_hint": software.get("generative_hint"),
        "camera_present": bool(camera.get("present")),
        "exif_present": bool((payload.get("exif") or {}).get("present")),
        "indicators": [],
    }

    indicators: list[dict[str, Any]] = []

    if software.get("generative_hint"):
        indicators.append(
            {
                "indicator": "generative_software_declared",
                "score": METADATA_GENERATIVE_SOFTWARE,
                "detail": (
                    f"Metadata names generative software "
                    f"('{software.get('generative_hint')}' in "
                    f"'{software.get('value')}'). This is a self-declaration "
                    "written by whatever last wrote the file; it is strong but not "
                    "verified."
                ),
            }
        )
    if software.get("editor_hint"):
        indicators.append(
            {
                "indicator": "editing_software_present",
                "score": METADATA_EDITOR_SOFTWARE,
                "detail": (
                    f"Metadata names image-editing software "
                    f"('{software.get('editor_hint')}'). This shows the file passed "
                    "through that software -- exporting, resizing or converting "
                    "writes the same tag -- and is NOT by itself evidence of "
                    "deceptive alteration."
                ),
            }
        )

    original = timestamps.get("exif_datetime_original")
    modified = timestamps.get("exif_datetime_modified")
    if original and modified and modified > original:
        indicators.append(
            {
                "indicator": "modification_after_capture",
                "score": METADATA_TIMESTAMP_CONFLICT,
                "detail": (
                    f"EXIF modification time ({modified}) is later than capture "
                    f"time ({original}), so the file was rewritten after capture. "
                    "Lossless rotation and metadata edits also do this."
                ),
            }
        )

    if indicators:
        indicators.sort(key=lambda item: item["score"], reverse=True)
        basis["indicators"] = indicators
        top = indicators[0]
        return _signal(
            "metadata_integrity",
            score=float(top["score"]),
            status=SIGNAL_OK,
            explanation=(
                f"{len(indicators)} metadata indicator(s) found; scored on the "
                f"strongest ('{top['indicator']}' = {top['score']:.2f}). "
                f"{top['detail']} Prototype indicator scores, not calibrated."
            ),
            basis=basis,
        )

    # No adverse indicator. Only claim mild support when there is something to read.
    if camera.get("present") and (
        timestamps.get("exif_datetime_original")
        or timestamps.get("exif_datetime_digitized")
    ):
        basis["indicators"] = [
            {
                "indicator": "consistent_capture_metadata",
                "score": METADATA_CONSISTENT_CAPTURE,
                "detail": (
                    "Camera identification and a capture timestamp are both "
                    "present with no editing-software tag and no timestamp "
                    "conflict."
                ),
            }
        ]
        return _signal(
            "metadata_integrity",
            score=METADATA_CONSISTENT_CAPTURE,
            status=SIGNAL_OK,
            explanation=(
                "Camera information and a capture timestamp are present and "
                f"mutually consistent (scored {METADATA_CONSISTENT_CAPTURE:.2f}). "
                "Weak support only: EXIF is trivially forgeable, so consistent "
                "metadata is not proof of authenticity."
            ),
            basis=basis,
        )

    stripped = bool(summary.get("stripped_likely"))
    return _signal(
        "metadata_integrity",
        score=None,
        status=SIGNAL_INCONCLUSIVE,
        explanation=(
            (
                "No EXIF metadata is present to analyse. "
                if stripped
                else "The metadata present carries no camera, timestamp or "
                "software information to analyse. "
            )
            + "Missing metadata is NOT evidence of manipulation -- platforms strip "
            "EXIF routinely during redistribution -- so this signal is excluded "
            "from the score rather than counted against the file."
        ),
        basis=basis,
    )


# --------------------------------------------------------------------------- #
# Signal 4: C2PA provenance
# --------------------------------------------------------------------------- #
def provenance_signal(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Score a C2PA manifest, distinguishing validated from merely present."""
    if not payload:
        return _signal(
            "provenance_c2pa",
            score=None,
            status=SIGNAL_UNAVAILABLE,
            explanation="Provenance inspection did not run for this item.",
        )
    if payload.get("status") == provenance_service.STATUS_ERROR:
        return _signal(
            "provenance_c2pa",
            score=None,
            status=SIGNAL_ERROR,
            explanation=(
                "Provenance inspection failed: "
                f"{payload.get('detail', 'no detail')}."
            ),
        )

    state = payload.get("state", provenance_service.STATE_ABSENT)
    declared = payload.get("declared") or {}
    generative = bool(declared.get("declares_generative_ai"))
    basis = {
        "state": state,
        "manifest_present": bool(payload.get("manifest_present")),
        "signature_validated": bool(payload.get("signature_validated")),
        "c2pa_library_available": bool(payload.get("c2pa_library_available")),
        "claim_generator": declared.get("claim_generator"),
        "declared_actions": declared.get("actions", []),
        "generative_source_types": declared.get("generative_source_types", []),
    }

    if state == provenance_service.STATE_VERIFIED and generative:
        return _signal(
            "provenance_c2pa",
            score=PROVENANCE_VERIFIED_GENERATIVE,
            status=SIGNAL_OK,
            explanation=(
                "A cryptographically validated C2PA manifest declares generative "
                f"AI involvement ({', '.join(basis['generative_source_types'])}). "
                "This is a signed statement by the producing tool."
            ),
            basis=basis,
        )
    if state == provenance_service.STATE_VERIFIED:
        return _signal(
            "provenance_c2pa",
            score=PROVENANCE_VERIFIED_CLEAN,
            status=SIGNAL_OK,
            explanation=(
                "A C2PA manifest is present and its signature validated, and it "
                "declares no generative AI involvement. This supports an intact "
                "chain of custody from the signer onward -- it says nothing about "
                "what happened in front of the camera."
            ),
            basis=basis,
        )
    if state == provenance_service.STATE_INVALID:
        return _signal(
            "provenance_c2pa",
            score=PROVENANCE_INVALID_SIGNATURE,
            status=SIGNAL_OK,
            explanation=(
                "A C2PA manifest is present but its signature FAILED validation. "
                "The file does not match the manifest it carries, which is a "
                "substantive integrity finding."
            ),
            basis=basis,
        )
    if state == provenance_service.STATE_UNVERIFIED and generative:
        return _signal(
            "provenance_c2pa",
            score=PROVENANCE_UNVERIFIED_GENERATIVE,
            status=SIGNAL_OK,
            explanation=(
                "An UNVERIFIED C2PA manifest declares generative AI involvement "
                f"({', '.join(basis['generative_source_types'])}). Signature "
                "validation was not performed, so this is a self-declaration that "
                "could have been copied from another asset; scored below a "
                "validated declaration for that reason."
            ),
            basis=basis,
        )
    if state == provenance_service.STATE_UNVERIFIED:
        return _signal(
            "provenance_c2pa",
            score=None,
            status=SIGNAL_INCONCLUSIVE,
            explanation=(
                "A C2PA manifest is present but was not cryptographically "
                "validated (the optional 'c2pa' library is not installed in this "
                "deployment). An unvalidated manifest cannot support an "
                "authenticity finding, so this signal is excluded. "
                + str(payload.get("detail", ""))
            ),
            basis=basis,
        )

    return _signal(
        "provenance_c2pa",
        score=None,
        status=SIGNAL_INCONCLUSIVE,
        explanation=(
            "No C2PA manifest is present. Almost no media in circulation carries "
            "Content Credentials, so absence is the expected condition and is NOT "
            "evidence of manipulation; this signal is excluded from the score."
        ),
        basis=basis,
    )


# --------------------------------------------------------------------------- #
# Signal 5: compression forensics
# --------------------------------------------------------------------------- #
def forensics_signal(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap the compression-forensics measurement as a fusion signal."""
    if not payload:
        return _signal(
            "compression_forensics",
            score=None,
            status=SIGNAL_UNAVAILABLE,
            explanation="Compression analysis did not run for this item.",
        )

    status = str(payload.get("status", forensics_service.STATUS_ERROR))
    score = payload.get("score")
    recompression = payload.get("recompression") or {}
    grid = payload.get("block_grid") or {}
    basis = {
        "analyser": payload.get("analyser"),
        "outlier_fraction": recompression.get("outlier_fraction"),
        "outlier_tiles": recompression.get("outlier_tiles"),
        "tiles": recompression.get("tiles"),
        "texture_fit_r_squared": recompression.get("texture_fit_r_squared"),
        "mean_residual": recompression.get("mean_residual"),
        "dominant_grid_phase": grid.get("dominant_phase"),
        "grid_peak_ratio": grid.get("peak_ratio"),
        "off_grid": grid.get("off_grid"),
        "hottest_tile": recompression.get("hottest_tile"),
        "score_ceiling": forensics_service.SCORE_CEILING,
    }

    if status == forensics_service.STATUS_OK and isinstance(score, (int, float)):
        return _signal(
            "compression_forensics",
            score=float(score),
            status=SIGNAL_OK,
            explanation=(
                f"{payload.get('explanation', '')} {forensics_service.INTERPRETATION}"
            ).strip(),
            basis=basis,
        )

    mapped = {
        forensics_service.STATUS_UNSUPPORTED: SIGNAL_UNSUPPORTED,
        forensics_service.STATUS_INSUFFICIENT: SIGNAL_INCONCLUSIVE,
        forensics_service.STATUS_ERROR: SIGNAL_ERROR,
    }.get(status, SIGNAL_ERROR)
    return _signal(
        "compression_forensics",
        score=None,
        status=mapped,
        explanation=(
            f"No compression score ({status}). "
            f"{payload.get('detail', 'No detail reported.')}"
        ),
        basis=basis,
    )


# --------------------------------------------------------------------------- #
# Fusion
# --------------------------------------------------------------------------- #
def build_signals(
    *,
    detector_payload: dict[str, Any] | None = None,
    match_payload: dict[str, Any] | None = None,
    metadata_payload: dict[str, Any] | None = None,
    provenance_payload: dict[str, Any] | None = None,
    forensics_payload: dict[str, Any] | None = None,
    sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Build all five signals from the stage payloads, in declared order."""
    return [
        ai_detection_signal(detector_payload),
        perceptual_signal(match_payload, sha256=sha256),
        metadata_signal(metadata_payload),
        provenance_signal(provenance_payload),
        forensics_signal(forensics_payload),
    ]


def _confidence(coverage: float, score: float, settings: Settings) -> str:
    """Confidence band. Never 'high' -- no threshold here is validated."""
    margin = min(
        abs(score - settings.verdict_manipulated_threshold),
        abs(score - settings.verdict_authentic_threshold),
    )
    if coverage >= 0.70 and margin >= 0.15:
        return CONFIDENCE_MODERATE
    return CONFIDENCE_LOW


def fuse(
    signals: list[dict[str, Any]], settings: Settings, *, media_type: str = "image"
) -> dict[str, Any]:
    """Combine signals into a verdict. Pure function: no I/O, no database.

    Mutates the passed signals in place to fill in weight, effective_weight,
    contribution and included, so the returned arithmetic is fully traceable.
    """
    declared = settings.fusion_weights
    total_declared = sum(declared.values())

    for signal in signals:
        signal["weight"] = float(declared.get(signal["signal_id"], 0.0))

    included = [
        s
        for s in signals
        if s["status"] == SIGNAL_OK
        and isinstance(s["score"], (int, float))
        and s["weight"] > 0.0
    ]
    available_weight = sum(s["weight"] for s in included)
    coverage = available_weight / total_declared if total_declared > 0 else 0.0
    included_ids = {id(s) for s in included}

    for signal in signals:
        if id(signal) in included_ids:
            signal["effective_weight"] = round(signal["weight"] / available_weight, 6)
            signal["contribution"] = round(
                float(signal["score"]) * signal["effective_weight"], 6
            )
            signal["included"] = True
        else:
            signal["effective_weight"] = 0.0
            signal["contribution"] = None
            signal["included"] = False

    thresholds = {
        "manipulated_at_or_above": settings.verdict_manipulated_threshold,
        "authentic_at_or_below": settings.verdict_authentic_threshold,
        "minimum_signal_coverage": settings.fusion_min_effective_weight,
    }
    excluded = [
        {"signal_id": s["signal_id"], "status": s["status"], "reason": s["explanation"]}
        for s in signals
        if not s["included"]
    ]

    result: dict[str, Any] = {
        "method": FUSION_METHOD,
        "fusion_version": FUSION_VERSION,
        "media_type": media_type,
        "signals": signals,
        "signals_total": len(signals),
        "signals_available": len(included),
        "declared_weights": declared,
        "declared_weight_total": round(total_declared, 6),
        "available_weight": round(available_weight, 6),
        "signal_coverage": round(coverage, 6),
        "thresholds": thresholds,
        "excluded_signals": excluded,
        "score_semantics": SCORE_SEMANTICS,
        "caveat": CAVEAT,
        "primary_signals": list(PRIMARY_SIGNALS),
    }

    if not included:
        result.update(
            verdict=VERDICT_INSUFFICIENT,
            manipulation_score=None,
            confidence=CONFIDENCE_NONE,
            primary_signal_available=False,
            rationale=(
                "No signal produced a measurement, so no score was computed. "
                f"All {len(signals)} signals are excluded: "
                + "; ".join(f"{e['signal_id']} ({e['status']})" for e in excluded)
                + ". This is an absence of evidence, not evidence of either "
                "authenticity or manipulation."
            ),
        )
        return result

    score = round(sum(float(s["contribution"]) for s in included), 6)
    primary_available = any(s["signal_id"] in PRIMARY_SIGNALS for s in included)
    arithmetic = " + ".join(
        f"{s['score']:.4f}x{s['effective_weight']:.4f}" for s in included
    )
    result.update(
        manipulation_score=score,
        primary_signal_available=primary_available,
        arithmetic=f"{arithmetic} = {score:.4f}",
    )

    if coverage < settings.fusion_min_effective_weight:
        result.update(
            verdict=VERDICT_INSUFFICIENT,
            confidence=CONFIDENCE_NONE,
            rationale=(
                f"Available signals account for {coverage:.0%} of the declared "
                f"weight, below the {settings.fusion_min_effective_weight:.0%} "
                "minimum required to reach a verdict. A fused score of "
                f"{score:.4f} was computed from "
                f"{len(included)}/{len(signals)} signals but is not sufficient "
                "to support a conclusion."
            ),
        )
        return result

    confidence = _confidence(coverage, score, settings)

    if score >= settings.verdict_manipulated_threshold:
        result.update(
            verdict=VERDICT_MANIPULATED,
            confidence=confidence,
            rationale=(
                f"Fused score {score:.4f} is at or above the manipulated "
                f"threshold {settings.verdict_manipulated_threshold}, computed "
                f"from {len(included)}/{len(signals)} signals covering "
                f"{coverage:.0%} of declared weight. Leading contributors: "
                + ", ".join(
                    f"{s['name']} ({s['contribution']:.4f})"
                    for s in sorted(
                        included, key=lambda s: s["contribution"], reverse=True
                    )[:3]
                )
                + "."
            ),
        )
        return result

    if score <= settings.verdict_authentic_threshold:
        if not primary_available:
            result.update(
                verdict=VERDICT_INSUFFICIENT,
                confidence=CONFIDENCE_NONE,
                rationale=(
                    f"Fused score {score:.4f} is at or below the authentic "
                    f"threshold {settings.verdict_authentic_threshold}, but no "
                    "primary signal was available: neither a working AI detector "
                    "nor a cryptographically validated C2PA manifest contributed. "
                    "The remaining signals (metadata leads, perceptual "
                    "derivation, compression history) are too weak to establish "
                    "authenticity, so no authenticity finding is issued."
                ),
            )
            return result
        result.update(
            verdict=VERDICT_AUTHENTIC,
            confidence=confidence,
            rationale=(
                f"Fused score {score:.4f} is at or below the authentic threshold "
                f"{settings.verdict_authentic_threshold}, with a primary signal "
                "available, computed from "
                f"{len(included)}/{len(signals)} signals covering {coverage:.0%} "
                "of declared weight. This means no evidence of manipulation was "
                "found by the signals that ran -- it is not a guarantee that the "
                "media is unaltered."
            ),
        )
        return result

    result.update(
        verdict=VERDICT_INSUFFICIENT,
        confidence=CONFIDENCE_LOW,
        rationale=(
            f"Fused score {score:.4f} falls between the authentic threshold "
            f"{settings.verdict_authentic_threshold} and the manipulated "
            f"threshold {settings.verdict_manipulated_threshold}, so the signals "
            "point in no clear direction. Computed from "
            f"{len(included)}/{len(signals)} signals covering {coverage:.0%} of "
            "declared weight."
        ),
    )
    return result
