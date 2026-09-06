"""Signal measurement and the traceable arithmetic behind one assessment.

This module *measures*. It turns each stage's raw payload into a signal record
carrying a score, a status, a declared weight and the evidence that produced it.
It does not decide: :func:`fuse` calls
:func:`app.services.assessment.evaluate` exactly once, and the legacy
``verdict`` / ``manipulation_score`` / ``confidence`` / ``rationale`` fields it
returns are a projection of that one assessment. There is no second decision
layer here and no consumer downstream is permitted to build one.

Design rules, in order of precedence:

1. **Nothing is hidden.** Every signal reports its own score, declared weight,
   effective (normalised) weight, contribution to the assessment score, status,
   its assessment role, a plain-language explanation, and the measurements that
   produced it. The score is reproducible by hand from the ``signals`` list.
2. **A missing signal is missing, not zero.** Signals that could not produce a
   measurement are given status ``INCONCLUSIVE`` / ``UNAVAILABLE`` / ``ERROR`` /
   ``UNSUPPORTED_MEDIA``, are excluded from the weighted mean, and the remaining
   weights are renormalised. Absent EXIF, an absent C2PA manifest, an empty
   perceptual index and an uninstalled detector all mean *we do not know* -- they
   never push the score in either direction.
3. **Measuring something is not the same as being allowed to decide.** Each
   signal declares an ``assessment_role``. Only signals eligible for the assessed
   task (:data:`app.services.assessment.ELIGIBLE_CHECKS`) contribute to the
   assessment score; the rest are published as forensic observations for an
   examiner. A metadata lead, a perceptual near-duplicate distance, a
   quantisation-table anomaly and a broken C2PA signature are all real findings
   and none of them is a synthetic-media finding, so none of them is averaged
   into the number behind one.
4. **Different modalities are different questions.** Each media type's detector
   is scoped to its own task and no score crosses modalities.
5. **Low eligible coverage produces no finding.** If the eligible checks do not
   account for at least ``fusion_min_effective_weight`` of their declared weight,
   the state is ``NOT_ASSESSED`` regardless of what any score happens to be.

**The weights and thresholds are prototype defaults, not validated science.**
They are configurable (``PRAMAAN_FUSION_WEIGHT_*``, ``PRAMAAN_VERDICT_*``) and
every response says so. This module is a decision aid for a human examiner; it
does not certify authenticity and its output is not admissible evidence on its
own.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

from app.config import Settings
from app.services import assessment
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

# --- Legacy verdict tokens ------------------------------------------------- #
# Retained as the projection of the assessment state for existing routes, stored
# rows, dashboard counters and alert rules. They are LOSSY: NOT_ASSESSED and
# INCONCLUSIVE both project onto INSUFFICIENT_EVIDENCE, so anything that needs to
# tell "we did not assess this" from "we assessed it and could not decide" must
# read ``assessment.state``. Nothing derives a verdict from a score any more --
# see ``assessment.LEGACY_VERDICT_BY_STATE``.
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

#: Which signals can apply to which media type -- the single source of truth the
#: API, the UI and the report all render from. A signal absent from a media
#: type's set is *not applicable*: it is neither failed nor zero nor part of the
#: coverage denominator, and fusion never builds it for that media.
#:
#: - ``ai_detection``: the detector interface covers all three modalities, and
#:   each abstains for itself, so the signal is built everywhere and the
#:   detector's own status explains any absence.
#: - ``metadata_integrity``: the extractor reads image EXIF and ISO-BMFF video
#:   containers; audio has no reader in this build.
#: - ``provenance_c2pa``: the container scan covers JPEG/PNG/BMFF embedding
#:   containers, which is what both video and still images use.
#: - ``perceptual_duplication``: image-only by construction -- pHash/dHash are
#:   computed from pixels, videos and audio carry no perceptual hash at all.
#: - ``compression_forensics``: analyses JPEG quantisation tables and an 8x8
#:   luminance grid, which only a still image has.
SIGNAL_APPLICABILITY: dict[str, frozenset[str]] = {
    "image": frozenset(
        {
            "ai_detection",
            "perceptual_duplication",
            "metadata_integrity",
            "provenance_c2pa",
            "compression_forensics",
        }
    ),
    "video": frozenset({"ai_detection", "metadata_integrity", "provenance_c2pa"}),
    "audio": frozenset({"ai_detection"}),
}

SIGNAL_APPLICABILITY_NOTE = (
    "Signal applicability is scoped by media type in the fusion engine. Signals "
    "outside a media type's set are NOT APPLICABLE: they are hidden from the "
    "analysis UI, kept out of the coverage denominator, and never treated as "
    "failed or as zero."
)


def applicable_signals(media_type: str) -> list[dict[str, Any]]:
    """The signal set that can apply to this media type, in declared order.

    The single source of truth for the whole system: fusion builds exactly these
    signals, the API exposes them, the UI renders them, and the report prints
    them. A signal missing from this list for a media type is not applicable to
    that media -- it is not "unavailable", not an error, and not part of any
    coverage fraction.
    """
    ordered = [
        "ai_detection",
        "perceptual_duplication",
        "metadata_integrity",
        "provenance_c2pa",
        "compression_forensics",
    ]
    allowed = SIGNAL_APPLICABILITY.get(media_type, SIGNAL_APPLICABILITY["image"])
    return [
        {"signal_id": sid, "name": SIGNAL_NAMES[sid], "applicable": True}
        for sid in ordered
        if sid in allowed
    ]


def _signal(
    signal_id: str,
    *,
    score: float | None,
    status: str,
    explanation: str,
    basis: dict[str, Any] | None = None,
    role: str = assessment.ROLE_DESCRIPTIVE,
    execution: str | None = None,
) -> dict[str, Any]:
    """Assemble one signal record. Weights are filled in by ``fuse``.

    ``role`` declares whether this measurement is eligible to determine the
    assessment state (:data:`assessment.ROLE_DECISIVE`) or is a forensic
    observation for examiner review (:data:`assessment.ROLE_DESCRIPTIVE`). It
    defaults to descriptive: a builder has to say so explicitly before its
    number can move a finding, and for provenance the role depends on *what the
    manifest turned out to say*, not merely on which signal it is.

    ``execution`` overrides the default status-to-execution mapping for cases
    where the signal status alone is ambiguous. ``INCONCLUSIVE`` normally means
    "ran and declined" (``ABSTAINED``), but for provenance it can also mean
    "there was no manifest to validate", which is an ``UNAVAILABLE`` input and
    not an abstention. Keeping those apart is what decides between
    ``INCONCLUSIVE`` and ``NOT_ASSESSED`` downstream.
    """
    return {
        "signal_id": signal_id,
        "name": SIGNAL_NAMES.get(signal_id, signal_id),
        "score": score,
        "status": status,
        "explanation": explanation,
        "evidence_basis": basis or {},
        "assessment_role": role,
        "assessment_role_note": assessment.ROLE_NOTES[role],
        "execution_status": execution,
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
            role=assessment.ROLE_DECISIVE,
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
            role=assessment.ROLE_DECISIVE,
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
            role=assessment.ROLE_DECISIVE,
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
        role=assessment.ROLE_DECISIVE,
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
            # Decisive: a validated signature makes this a signed declaration
            # about how the asset was produced, which is exactly the assessed
            # question -- not an inference from an absence.
            role=assessment.ROLE_DECISIVE,
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
            # Decisive in the negative direction, on the same basis as the
            # generative case: a validated manifest declaring no generative
            # involvement is a signed statement about production.
            role=assessment.ROLE_DECISIVE,
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
            # Descriptive by default: a broken signature is a serious INTEGRITY
            # finding, but it does not say the media was synthetically generated
            # -- re-encoding for a CDN breaks signatures too. It goes to the
            # examiner as an observation instead of manufacturing a
            # synthetic-media finding.
            role=assessment.ROLE_DESCRIPTIVE,
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
            # Descriptive: an unvalidated self-declaration is a strong lead but
            # it is unauthenticated, so it is reported rather than treated as
            # decisive.
            role=assessment.ROLE_DESCRIPTIVE,
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
            # UNAVAILABLE, not ABSTAINED: validation could not be performed
            # because the library is missing. Nothing declined to answer -- the
            # check never got to ask, which is a different kind of absence.
            execution=assessment.CHECK_UNAVAILABLE,
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
        # UNAVAILABLE, not ABSTAINED: there was no manifest to validate, so this
        # check had no input rather than declining to answer. Treating it as an
        # abstention would make every ordinary file without Content Credentials
        # look like a check that ran and could not decide.
        execution=assessment.CHECK_UNAVAILABLE,
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
    media_type: str = "image",
) -> list[dict[str, Any]]:
    """Build the signals *applicable to this media type*, in declared order.

    A signal outside the media type's applicability set is not built at all --
    it is not applicable, so it is neither a row in the response nor part of the
    coverage denominator. Stages whose payload is ``None`` still produce their
    signal (reported UNAVAILABLE); only inapplicable signals are absent.
    """
    allowed = SIGNAL_APPLICABILITY.get(media_type, SIGNAL_APPLICABILITY["image"])
    builders: dict[str, Callable[[], dict[str, Any]]] = {
        "ai_detection": lambda: ai_detection_signal(detector_payload),
        "perceptual_duplication": lambda: perceptual_signal(
            match_payload, sha256=sha256
        ),
        "metadata_integrity": lambda: metadata_signal(metadata_payload),
        "provenance_c2pa": lambda: provenance_signal(provenance_payload),
        "compression_forensics": lambda: forensics_signal(forensics_payload),
    }
    return [builders[sid]() for sid in builders if sid in allowed]


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
    """Assemble the signal arithmetic and attach the one assessment.

    Pure function: no I/O, no database. Mutates the passed signals in place to
    fill in weight, effective_weight, contribution, measured and included, so the
    returned arithmetic is fully traceable.

    The decision itself belongs to :func:`app.services.assessment.evaluate`,
    which is called exactly once here. This function does not decide anything on
    its own: the legacy ``verdict`` / ``manipulation_score`` / ``confidence`` /
    ``rationale`` fields are a *projection* of that assessment, kept so existing
    routes, stored rows and reports stay readable.

    Two coverage-style quantities are published and they mean different things:

    - ``signal_coverage`` -- share of applicable declared weight that produced
      any measurement at all. This is the observational completeness of the
      examination and it is what the UI's coverage line has always shown.
    - ``assessment.coverage`` -- share of the weight of the checks ELIGIBLE for
      the assessed task that contributed to the finding. This is what gates the
      assessment, and it is usually the smaller number.
    """
    declared = settings.fusion_weights
    # Only the declared weights of the signals present (the applicable set)
    # form the denominator. A weight configured for a signal this media type
    # cannot carry is deliberately excluded.
    total_declared = sum(
        declared.get(signal["signal_id"], 0.0) for signal in signals
    )

    for signal in signals:
        signal["weight"] = float(declared.get(signal["signal_id"], 0.0))

    # A signal that produced a usable number. Distinct from ``included`` below:
    # measuring something and being eligible to decide the assessment are two
    # different things now.
    measured = [
        s
        for s in signals
        if s["status"] == SIGNAL_OK
        and assessment.usable_score(s["score"]) is not None
        and s["weight"] > 0.0
    ]
    measured_weight = sum(s["weight"] for s in measured)
    coverage = measured_weight / total_declared if total_declared > 0 else 0.0

    # The one authoritative assessment. Derived here, once, from the same signal
    # records the response carries and the same configured thresholds -- so the
    # API, the frontend and the PDF all read this object instead of deriving a
    # state of their own from scores.
    evidence_assessment = assessment.evaluate(
        signals=signals,
        media_type=media_type,
        declared_weights=declared,
        manipulated_at_or_above=settings.verdict_manipulated_threshold,
        authentic_at_or_below=settings.verdict_authentic_threshold,
        minimum_eligible_coverage=settings.fusion_min_effective_weight,
    )

    # ``included`` means "contributed to the assessment score". Only checks the
    # policy accepted as eligible qualify, which is what stops a metadata lead,
    # a perceptual distance or a compression irregularity from being averaged
    # into the number behind a synthetic-media finding.
    contributing_ids = {
        str(c["check_id"]) for c in evidence_assessment["contributing_checks"]
    }
    measured_marks = {id(s) for s in measured}
    included = [s for s in measured if str(s["signal_id"]) in contributing_ids]
    included_marks = {id(s) for s in included}
    available_weight = sum(s["weight"] for s in included)

    for signal in signals:
        # Did it measure anything? Independent of whether it was allowed to
        # decide, so a descriptive observation is never mistaken for a failure.
        signal["measured"] = id(signal) in measured_marks
        if id(signal) in included_marks:
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

    # Everything that did not contribute to the assessment score, with the two
    # reasons kept apart: it could not measure anything, or it measured something
    # that is not eligible to decide this task. The old shape (signal_id, status,
    # reason) is preserved for existing consumers and extended, not replaced.
    excluded = [
        {
            "signal_id": s["signal_id"],
            "status": s["status"],
            "reason": s["explanation"],
            "measured": s["measured"],
            "assessment_role": s["assessment_role"],
        }
        for s in signals
        if not s["included"]
    ]

    result: dict[str, Any] = {
        "method": FUSION_METHOD,
        "fusion_version": FUSION_VERSION,
        "media_type": media_type,
        "signals": signals,
        # Media-aware counts. ``signals_total`` is the number of signals
        # APPLICABLE to this media type that were considered (== len(signals));
        # ``signals_available`` those that produced a measurement and were
        # folded into the score; ``signals_evaluated`` those that actually ran
        # (were attempted) whether or not they could decide. Inapplicable
        # signals are absent from all three -- they are not failed, not zero,
        # and not in any denominator.
        "signals_total": len(signals),
        # Signals whose score is behind the assessment. Formerly this counted
        # everything that produced a number; ``signals_measured`` now carries
        # that meaning, and this counts the eligible contributors.
        "signals_available": len(included),
        "signals_measured": len(measured),
        "signals_evaluated": sum(1 for s in signals if s["status"] != SIGNAL_UNAVAILABLE),
        "applicable_signals": [
            {"signal_id": s["signal_id"], "name": s["name"]} for s in signals
        ],
        "declared_weights": declared,
        "declared_weight_total": round(total_declared, 6),
        "available_weight": round(available_weight, 6),
        "measured_weight": round(measured_weight, 6),
        # Observational completeness: how much of the applicable declared weight
        # produced any measurement. NOT the gate on the assessment -- that is
        # ``assessment.coverage``, over the eligible checks only.
        "signal_coverage": round(coverage, 6),
        "thresholds": thresholds,
        "excluded_signals": excluded,
        "score_semantics": SCORE_SEMANTICS,
        "caveat": CAVEAT,
        "primary_signals": list(PRIMARY_SIGNALS),
        # The authoritative assessment contract. The legacy fields set below
        # (``verdict``, ``manipulation_score``, ``confidence``, ``rationale``,
        # ``arithmetic``, ``primary_signal_available``) are a PROJECTION of this
        # object, computed from it and never alongside it.
        "assessment": evidence_assessment,
    }

    state = evidence_assessment["state"]
    score = evidence_assessment["score"]
    reason_codes = evidence_assessment["reason_codes"]

    result.update(
        verdict=assessment.legacy_verdict(state),
        manipulation_score=score,
        primary_signal_available=bool(evidence_assessment["contributing_checks"]),
        arithmetic=evidence_assessment["arithmetic"],
        # A band, never a percentage, and never 'high': no threshold here is
        # calibrated. Only a conclusive state gets a band at all.
        confidence=(
            _confidence(evidence_assessment["coverage"], score, settings)
            if state in assessment.CONCLUSIVE_STATES and score is not None
            else CONFIDENCE_NONE
        ),
        rationale=_rationale(evidence_assessment, signals, settings),
    )
    return result


def _rationale(
    evidence_assessment: dict[str, Any],
    signals: list[dict[str, Any]],
    settings: Settings,
) -> str:
    """Prose for the assessment. Rendered FROM the state, never alongside it.

    Every consumer that needs to branch reads ``state`` and ``reason_codes``;
    this text exists so a human reading the response or the PDF sees the same
    reasoning in words. It adds no logic of its own.
    """
    state = evidence_assessment["state"]
    score = evidence_assessment["score"]
    codes = evidence_assessment["reason_codes"]
    contributing = evidence_assessment["contributing_checks"]
    coverage = evidence_assessment["coverage"]
    observations = evidence_assessment["descriptive_observations"]

    contributors = ", ".join(
        f"{c['name']} ({float(c['score']):.4f})" for c in contributing
    )
    observed = (
        " "
        + f"{len(observations)} forensic observation(s) were recorded for examiner "
        "review and did not contribute to this state: "
        + ", ".join(o["name"] for o in observations)
        + "."
        if observations
        else ""
    )

    if state == assessment.STATE_INDICATORS_DETECTED:
        return (
            f"{contributors} reached or exceeded the positive threshold "
            f"{settings.verdict_manipulated_threshold} for "
            f"{evidence_assessment['scope']}. Indicators were detected; this is "
            "not a determination that the media is fake and no calibrated error "
            f"rate is known for this threshold.{observed}"
        )

    if state == assessment.STATE_NO_INDICATORS_DETECTED:
        return (
            f"{contributors} is at or below the negative threshold "
            f"{settings.verdict_authentic_threshold} for "
            f"{evidence_assessment['scope']}. No indicators were detected by the "
            "checks that ran -- this is not a certification that the media is "
            f"unaltered.{observed}"
        )

    if state == assessment.STATE_INCONCLUSIVE:
        if assessment.REASON_CONFLICTING_RESULTS in codes:
            return (
                "Eligible checks for "
                f"{evidence_assessment['scope']} point in opposite directions "
                f"({contributors}) and no validated conflict policy exists, so no "
                f"finding is issued.{observed}"
            )
        if assessment.REASON_DETECTOR_ABSTAINED in codes and not contributing:
            return (
                "An eligible check for "
                f"{evidence_assessment['scope']} ran and declined to return a "
                "score, so the question could not be decided. An abstention is "
                "not a measurement and is not a vote in either "
                f"direction.{observed}"
            )
        return (
            f"{contributors or 'The eligible checks'} produced a score of "
            f"{score if score is None else format(score, '.4f')}, between the "
            f"negative threshold {settings.verdict_authentic_threshold} and the "
            f"positive threshold {settings.verdict_manipulated_threshold}, so it "
            f"supports no finding in either direction.{observed}"
        )

    # NOT_ASSESSED
    missing = "; ".join(
        f"{item['check_id']} ({item['execution_status']})"
        for item in evidence_assessment["unavailable_checks"]
    )
    if assessment.REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE in codes and contributing:
        return (
            f"Eligible checks covered {coverage:.0%} of the declared eligible "
            f"weight for {evidence_assessment['scope']}, below the "
            f"{settings.fusion_min_effective_weight:.0%} minimum this deployment "
            f"requires, so the question was not assessed.{observed}"
        )
    return (
        "No eligible check produced a result for "
        f"{evidence_assessment['scope']}, so the question was not assessed"
        + (f" ({missing})" if missing else "")
        + ". This is an absence of evidence -- neither a finding of manipulation "
        f"nor a finding of authenticity.{observed}"
    )
