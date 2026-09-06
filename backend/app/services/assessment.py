"""The assessment contract: what PRAMAAN claims, about what, and on what basis.

This module is the *vocabulary and the policy* for one narrow question. It is
not a second decision layer: ``fusion.fuse`` is the only caller, it calls
:func:`evaluate` exactly once per evidence item, and nothing downstream -- not
the API, not the frontend, not the PDF -- is permitted to re-derive any of these
values from scores and thresholds of its own.

Five concepts were previously carried by one three-token enum
(``AUTHENTIC`` / ``MANIPULATED`` / ``INSUFFICIENT_EVIDENCE``). They are
different kinds of statement and they are kept apart here:

**A. MODEL RESULT** -- a number a network emitted for the input it was given.
   Lives on the detector result (``manipulation_score``, ``abstained``,
   ``status``) and never carries a verdict token.

**B. FORENSIC OBSERVATION** -- something measured about the file that a human
   examiner should see: an EXIF software tag, a near-duplicate distance, a
   quantisation-table anomaly, a C2PA signature that failed to validate. Real,
   useful, and *not* a synthetic-media finding. Observations appear in
   ``descriptive_observations`` and are excluded from the assessment by
   construction, not by weight.

**C. EVIDENCE ASSESSMENT** -- the scoped, automated interpretation produced by
   this policy: ``state`` + ``reason_codes`` + ``scope`` + ``policy_version``.
   This is the only thing in the system entitled to be called a finding, and it
   is qualified by the task it was computed for.

**D. EXAMINER CONCLUSION** -- a human judgement. This build has no workflow that
   records one, so ``examiner_conclusion`` is ``None`` and says why. It is not
   filled in from C.

**E. EXECUTION STATUS** -- whether processing completed. Orthogonal to C: an
   examination can complete cleanly and still reach ``NOT_ASSESSED``, and that
   is the distinction ``COMPLETED`` vs ``CONCLUSIVE`` exists to preserve.

Policy rules, in order of precedence:

1. **Only an eligible check can produce a finding.** The eligible set for this
   task is :data:`ELIGIBLE_CHECKS`. A descriptive observation cannot move the
   state in either direction, however numeric it happens to be. Missing EXIF, a
   missing C2PA manifest, a compression irregularity, a perceptual distance, a
   SHA-256 and a filename are not synthetic-media evidence.
2. **Missingness is preserved.** ``UNAVAILABLE`` / ``ABSTAINED`` / ``FAILED`` /
   ``NOT_APPLICABLE`` stay distinct all the way to the response. None of them
   becomes 0.0, and none of them becomes a vote in either direction.
3. **Disagreement is not averaged away.** Two eligible checks that point in
   opposite directions produce ``INCONCLUSIVE`` with
   ``CONFLICTING_RESULTS``, because no conflict policy has been validated for
   this system.
4. **No threshold here is invented.** ``manipulated_at_or_above``,
   ``authentic_at_or_below`` and ``minimum_eligible_coverage`` are the
   deployment's existing configured fusion thresholds, passed in by the caller
   and republished with every assessment. They are uncalibrated, and
   ``limitations`` says so in every response.
"""

from __future__ import annotations

import math
from typing import Any

# --------------------------------------------------------------------------- #
# Policy identity (concept C carries its own version)
# --------------------------------------------------------------------------- #
#: Identity of the decision policy implemented below. Recorded on every
#: assessment so an examination stays interpretable under the policy that
#: produced it: a stored result whose ``policy_version`` differs from this
#: constant was decided by different rules and must not be re-read as though it
#: were decided by these.
POLICY_ID = "pramaan.synthetic_media_indicators"
POLICY_VERSION = "1.0"

POLICY_NOTE = (
    "The assessment state is derived only from checks eligible for the assessed "
    "task. Descriptive forensic observations are reported but never move the "
    "state. Thresholds are the deployment's configured fusion thresholds and "
    "are not calibrated against a labelled reference set."
)

# --------------------------------------------------------------------------- #
# A. Assessment scope -- the task, named per modality (never a universal claim)
# --------------------------------------------------------------------------- #
#: Each modality's detector answers a *different* question with a different
#: training distribution: an image model separates generated stills from
#: photographs, a video model separates face-swapped video from real footage,
#: an audio model separates synthesised or replayed speech from bona fide
#: speech. Naming them apart is what stops one model's output from being read as
#: a universal authenticity claim, and it is why no score from one modality is
#: ever averaged with a score from another.
SCOPE_BY_MEDIA: dict[str, str] = {
    "image": "ai_generated_or_manipulated_image_indicators",
    "video": "deepfake_or_manipulated_video_indicators",
    "audio": "synthetic_or_spoofed_speech_indicators",
}

SCOPE_UNSCOPED = "unscoped_media_indicators"

SCOPE_NOTES: dict[str, str] = {
    "ai_generated_or_manipulated_image_indicators": (
        "Whether the examined still image carries indicators of AI generation or "
        "digital manipulation. Not a statement about the truthfulness of what "
        "the image depicts, and not a certification that it is unaltered."
    ),
    "deepfake_or_manipulated_video_indicators": (
        "Whether the examined video carries indicators of face manipulation or "
        "synthetic generation in the frames that were sampled. Frames outside "
        "the sample were not examined."
    ),
    "synthetic_or_spoofed_speech_indicators": (
        "Whether the examined audio carries indicators of synthesised, converted "
        "or replayed speech. Not a speaker-identification result and not a "
        "statement about what was said."
    ),
    SCOPE_UNSCOPED: (
        "No task-scoped detector applies to this media type in this deployment, "
        "so there is no assessed question -- only observations."
    ),
}


def scope_for(media_type: str) -> str:
    """The task this assessment is scoped to, for one media type."""
    return SCOPE_BY_MEDIA.get(str(media_type).lower(), SCOPE_UNSCOPED)


# --------------------------------------------------------------------------- #
# C. Assessment state -- task-qualified, four values, no broad claims
# --------------------------------------------------------------------------- #
STATE_INDICATORS_DETECTED = "INDICATORS_DETECTED"
STATE_NO_INDICATORS_DETECTED = "NO_INDICATORS_DETECTED"
STATE_INCONCLUSIVE = "INCONCLUSIVE"
STATE_NOT_ASSESSED = "NOT_ASSESSED"

ASSESSMENT_STATES = (
    STATE_INDICATORS_DETECTED,
    STATE_NO_INDICATORS_DETECTED,
    STATE_INCONCLUSIVE,
    STATE_NOT_ASSESSED,
)

STATE_NOTES: dict[str, str] = {
    STATE_INDICATORS_DETECTED: (
        "An eligible check for the assessed task reached its positive threshold. "
        "Indicators were detected; this is not a determination that the media is "
        "fake, and it carries no calibrated error rate."
    ),
    STATE_NO_INDICATORS_DETECTED: (
        "An eligible check for the assessed task reached its negative threshold. "
        "No indicators were detected by the checks that ran -- this is not a "
        "certification of authenticity, and checks that did not run could not "
        "contribute."
    ),
    STATE_INCONCLUSIVE: (
        "An eligible check ran but the result does not support a finding in "
        "either direction: it abstained, it landed between the thresholds, or "
        "eligible checks disagreed. A statement about the evidence available, "
        "not about the media."
    ),
    STATE_NOT_ASSESSED: (
        "No eligible check produced a result for the assessed task, so the "
        "question was not assessed. Any observations reported alongside this "
        "state are context for an examiner, not a finding."
    ),
}

#: A state is CONCLUSIVE when the policy reached a finding. Distinct from
#: execution status: an examination can run to completion and be inconclusive.
CONCLUSIVE_STATES = (STATE_INDICATORS_DETECTED, STATE_NO_INDICATORS_DETECTED)

# --------------------------------------------------------------------------- #
# E. Execution status -- did processing complete (never a finding)
# --------------------------------------------------------------------------- #
EXECUTION_COMPLETED = "COMPLETED"
EXECUTION_PARTIAL = "PARTIAL"
EXECUTION_FAILED = "FAILED"

EXECUTION_NOTES: dict[str, str] = {
    EXECUTION_COMPLETED: "Every applicable check ran to completion.",
    EXECUTION_PARTIAL: "At least one applicable check failed during processing.",
    EXECUTION_FAILED: "Every applicable check failed during processing.",
}

# --------------------------------------------------------------------------- #
# Per-check execution status -- explicit missingness, six distinct values
# --------------------------------------------------------------------------- #
CHECK_COMPLETED = "COMPLETED"
CHECK_ABSTAINED = "ABSTAINED"
CHECK_UNAVAILABLE = "UNAVAILABLE"
CHECK_FAILED = "FAILED"
CHECK_NOT_APPLICABLE = "NOT_APPLICABLE"
CHECK_NOT_REQUESTED = "NOT_REQUESTED"

CHECK_STATUSES = (
    CHECK_COMPLETED,
    CHECK_ABSTAINED,
    CHECK_UNAVAILABLE,
    CHECK_FAILED,
    CHECK_NOT_APPLICABLE,
    CHECK_NOT_REQUESTED,
)

CHECK_STATUS_NOTES: dict[str, str] = {
    CHECK_COMPLETED: "Ran and produced a measurement.",
    CHECK_ABSTAINED: "Ran and declined to answer. Not a measurement of zero.",
    CHECK_UNAVAILABLE: (
        "Could not run in this deployment. Says nothing about the media."
    ),
    CHECK_FAILED: "Failed during processing. Says nothing about the media.",
    CHECK_NOT_APPLICABLE: (
        "Does not apply to this media type, so it was never attempted."
    ),
    CHECK_NOT_REQUESTED: "Was not requested for this examination.",
}

# --------------------------------------------------------------------------- #
# Evidence role -- B (observation) vs C (assessment input)
# --------------------------------------------------------------------------- #
ROLE_DECISIVE = "DECISIVE"
ROLE_DESCRIPTIVE = "DESCRIPTIVE"

ROLE_NOTES: dict[str, str] = {
    ROLE_DECISIVE: (
        "Eligible to determine the assessment state for the assessed task."
    ),
    ROLE_DESCRIPTIVE: (
        "A forensic observation reported for examiner review. It does not move "
        "the assessment state in either direction."
    ),
}

#: The checks eligible to determine this task's state. Both are task-scoped
#: statements about generation: a detector trained to separate synthetic from
#: real media for this modality, and a C2PA manifest whose signature validated
#: and which therefore constitutes a signed declaration about how the asset was
#: produced.
#:
#: Everything else the system measures is an observation. That is not a
#: judgement about its forensic worth -- a failed C2PA signature and a
#: generative software tag are both worth an examiner's attention -- it is a
#: statement that no validated policy exists in this build for turning them into
#: a synthetic-media finding, so they are not permitted to manufacture one.
ELIGIBLE_CHECKS: tuple[str, ...] = ("ai_detection", "provenance_c2pa")

# --------------------------------------------------------------------------- #
# Reason codes -- structured, so no consumer has to match prose
# --------------------------------------------------------------------------- #
REASON_DETECTOR_UNAVAILABLE = "DETECTOR_UNAVAILABLE"
REASON_DETECTOR_ABSTAINED = "DETECTOR_ABSTAINED"
REASON_BELOW_THRESHOLD = "BELOW_THRESHOLD"
REASON_ABOVE_THRESHOLD = "ABOVE_THRESHOLD"
REASON_INTERMEDIATE_SCORE = "INTERMEDIATE_SCORE"
REASON_CONFLICTING_RESULTS = "CONFLICTING_RESULTS"
REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE = "INSUFFICIENT_ELIGIBLE_EVIDENCE"
REASON_PROCESSING_FAILURE = "PROCESSING_FAILURE"

REASON_CODES = (
    REASON_DETECTOR_UNAVAILABLE,
    REASON_DETECTOR_ABSTAINED,
    REASON_BELOW_THRESHOLD,
    REASON_ABOVE_THRESHOLD,
    REASON_INTERMEDIATE_SCORE,
    REASON_CONFLICTING_RESULTS,
    REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE,
    REASON_PROCESSING_FAILURE,
)

REASON_NOTES: dict[str, str] = {
    REASON_DETECTOR_UNAVAILABLE: (
        "An eligible check could not run in this deployment."
    ),
    REASON_DETECTOR_ABSTAINED: (
        "An eligible check ran and declined to return a score."
    ),
    REASON_BELOW_THRESHOLD: (
        "An eligible check's score is at or below the negative threshold."
    ),
    REASON_ABOVE_THRESHOLD: (
        "An eligible check's score is at or above the positive threshold."
    ),
    REASON_INTERMEDIATE_SCORE: (
        "An eligible check's score falls between the two thresholds, so it "
        "supports no finding in either direction."
    ),
    REASON_CONFLICTING_RESULTS: (
        "Two eligible checks for the same task point in opposite directions and "
        "no validated conflict policy exists, so no finding is issued."
    ),
    REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE: (
        "No eligible check contributed enough of the declared eligible weight "
        "for this task to be assessed."
    ),
    REASON_PROCESSING_FAILURE: "An eligible check failed during processing.",
}

# --------------------------------------------------------------------------- #
# D. Examiner conclusion -- absent by design in this build
# --------------------------------------------------------------------------- #
EXAMINER_CONCLUSION_NOTE = (
    "This build records no examiner conclusion: there is no workflow for an "
    "examiner to enter, sign or supersede a finding. The automated assessment "
    "is therefore never presented as a human judgement, and this field stays "
    "null rather than being filled in from it."
)

SCORE_SEMANTICS = (
    "assessment.score is the weighted mean of the ELIGIBLE checks that "
    "contributed, on the same 0.0-1.0 axis they report (higher means more "
    "indication of synthetic generation or manipulation for the assessed task). "
    "It is not a probability and has no calibrated error rate. Descriptive "
    "observations are not in it."
)

BASE_LIMITATIONS = (
    "No threshold in this policy has been calibrated against a labelled "
    "forensic reference set, so no error rate is known for it.",
    "The assessment covers only the checks that ran, on only the data they "
    "examined. It is a decision aid for a qualified examiner, not a "
    "certification.",
)


# --------------------------------------------------------------------------- #
# Mapping to and from the legacy verdict enum
# --------------------------------------------------------------------------- #
#: The legacy three-token enum is retained in the API as a *projection* of the
#: state, not as a second decision. Note that it is lossy in exactly the way
#: that motivated this module: ``NOT_ASSESSED`` and ``INCONCLUSIVE`` both land
#: on ``INSUFFICIENT_EVIDENCE``, which is why consumers that need to tell "we
#: did not assess this" from "we assessed it and could not decide" must read
#: ``state``.
LEGACY_VERDICT_BY_STATE: dict[str, str] = {
    STATE_INDICATORS_DETECTED: "MANIPULATED",
    STATE_NO_INDICATORS_DETECTED: "AUTHENTIC",
    STATE_INCONCLUSIVE: "INSUFFICIENT_EVIDENCE",
    STATE_NOT_ASSESSED: "INSUFFICIENT_EVIDENCE",
}


def legacy_verdict(state: str) -> str:
    """The legacy verdict token for a state. Never the other way round."""
    return LEGACY_VERDICT_BY_STATE.get(state, "INSUFFICIENT_EVIDENCE")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def usable_score(value: Any) -> float | None:
    """``value`` as a score in 0..1, or ``None`` if it is not one.

    Rejects bools (``True`` satisfies ``isinstance(x, int)``), NaN and both
    infinities. A malformed number is not clamped into range: clamping turns a
    broken check into a plausible-looking measurement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def check_status_for(signal_status: str, *, signal_module: Any) -> str:
    """Map a fusion signal status onto the per-check execution vocabulary."""
    return {
        signal_module.SIGNAL_OK: CHECK_COMPLETED,
        signal_module.SIGNAL_INCONCLUSIVE: CHECK_ABSTAINED,
        signal_module.SIGNAL_UNAVAILABLE: CHECK_UNAVAILABLE,
        signal_module.SIGNAL_ERROR: CHECK_FAILED,
        signal_module.SIGNAL_UNSUPPORTED: CHECK_NOT_APPLICABLE,
    }.get(str(signal_status), CHECK_UNAVAILABLE)


def _direction(
    score: float, *, manipulated_at_or_above: float, authentic_at_or_below: float
) -> tuple[str, str]:
    """One eligible check's own state and reason code, from its own score."""
    if score >= manipulated_at_or_above:
        return STATE_INDICATORS_DETECTED, REASON_ABOVE_THRESHOLD
    if score <= authentic_at_or_below:
        return STATE_NO_INDICATORS_DETECTED, REASON_BELOW_THRESHOLD
    return STATE_INCONCLUSIVE, REASON_INTERMEDIATE_SCORE


# --------------------------------------------------------------------------- #
# The policy
# --------------------------------------------------------------------------- #
def evaluate(
    *,
    signals: list[dict[str, Any]],
    media_type: str,
    declared_weights: dict[str, float],
    manipulated_at_or_above: float,
    authentic_at_or_below: float,
    minimum_eligible_coverage: float,
) -> dict[str, Any]:
    """Derive the one assessment for one evidence item.

    ``signals`` are fusion's own signal records, each already carrying
    ``signal_id``, ``score``, ``status``, ``explanation``, ``evidence_basis``
    and the ``assessment_role`` its builder declared. This function reads them;
    it does not measure anything, and it does not recompute any check's score.

    The state is derived from each eligible check's own score against the
    published thresholds, and the per-check verdicts are then combined. Scores
    are *not* averaged first: averaging is what lets a strong result and a
    contradicting result cancel into a confident-looking middle, and it is what
    lets a descriptive observation dilute or inflate a check that actually
    measured the task.
    """
    # ``fusion`` is imported lazily and only for its status vocabulary: this
    # module must not depend on fusion at import time, because fusion imports it.
    from app.services import fusion as fusion_module

    scope = scope_for(media_type)
    thresholds = {
        "manipulated_at_or_above": manipulated_at_or_above,
        "authentic_at_or_below": authentic_at_or_below,
        "minimum_eligible_coverage": minimum_eligible_coverage,
    }

    by_id = {str(s.get("signal_id")): s for s in signals}
    # Eligibility is a property of the check, not of its outcome, so the
    # denominator is fixed before anything runs. An eligible check that could
    # not run therefore *lowers* coverage instead of quietly shrinking the
    # denominator until the survivors look like full coverage.
    eligible_ids = [sid for sid in ELIGIBLE_CHECKS if sid in by_id]
    eligible_declared = sum(
        float(declared_weights.get(sid, 0.0) or 0.0) for sid in eligible_ids
    )

    contributing: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    per_check_states: list[str] = []

    for sid in eligible_ids:
        signal = by_id[sid]
        role = str(signal.get("assessment_role") or ROLE_DESCRIPTIVE)
        status = str(signal.get("status"))
        # A builder may state the execution status directly when its signal
        # status is ambiguous -- e.g. an absent C2PA manifest is UNAVAILABLE
        # input, not a check that ran and abstained.
        check_status = str(
            signal.get("execution_status")
            or check_status_for(status, signal_module=fusion_module)
        )
        score = usable_score(signal.get("score"))
        weight = float(declared_weights.get(sid, 0.0) or 0.0)

        if role == ROLE_DECISIVE and check_status == CHECK_COMPLETED and score is not None and weight > 0.0:
            state, reason = _direction(
                score,
                manipulated_at_or_above=manipulated_at_or_above,
                authentic_at_or_below=authentic_at_or_below,
            )
            contributing.append(
                {
                    "check_id": sid,
                    "name": signal.get("name", sid),
                    "score": score,
                    "declared_weight": round(weight, 6),
                    "execution_status": CHECK_COMPLETED,
                    "check_state": state,
                    "reason_code": reason,
                    "basis": signal.get("evidence_basis") or {},
                }
            )
            per_check_states.append(state)
            continue

        if role != ROLE_DECISIVE and check_status == CHECK_COMPLETED:
            # It ran and measured something, but what it measured is not eligible
            # for this task -- a failed C2PA signature is an integrity finding,
            # not a generation finding. It is reported as an observation below,
            # not as a check that went missing, because nothing is absent here.
            continue

        # Not contributing. Record *why*, with the distinction intact.
        if check_status == CHECK_ABSTAINED:
            reason = REASON_DETECTOR_ABSTAINED
        elif check_status == CHECK_FAILED:
            reason = REASON_PROCESSING_FAILURE
        elif check_status == CHECK_NOT_APPLICABLE:
            reason = None
        else:
            reason = REASON_DETECTOR_UNAVAILABLE
        unavailable.append(
            {
                "check_id": sid,
                "name": signal.get("name", sid),
                "declared_weight": round(weight, 6),
                "execution_status": check_status,
                "reason_code": reason,
                "detail": str(signal.get("explanation") or ""),
            }
        )
        if reason is not None and reason not in reason_codes:
            reason_codes.append(reason)

    contributed_weight = sum(float(c["declared_weight"]) for c in contributing)
    coverage = (
        round(contributed_weight / eligible_declared, 6)
        if eligible_declared > 0.0
        else 0.0
    )

    # Observations: every signal that is not an eligible contributor and did
    # produce a measurement. They are published in full -- an examiner needs
    # them -- and they are structurally incapable of changing the state.
    observations: list[dict[str, Any]] = []
    for signal in signals:
        sid = str(signal.get("signal_id"))
        if any(c["check_id"] == sid for c in contributing):
            continue
        if str(signal.get("status")) != fusion_module.SIGNAL_OK:
            continue
        observations.append(
            {
                "observation_id": sid,
                "name": signal.get("name", sid),
                "score": signal.get("score"),
                "role": str(signal.get("assessment_role") or ROLE_DESCRIPTIVE),
                "execution_status": CHECK_COMPLETED,
                "detail": str(signal.get("explanation") or ""),
            }
        )

    # --- state ------------------------------------------------------------- #
    if not contributing:
        # Nothing eligible produced a measurement. Whether that is "we could not
        # assess this" or "we assessed it and could not decide" is the
        # distinction §7 turns on: a check that RAN and declined leaves us
        # inconclusive; a check that never ran leaves the question unassessed.
        if REASON_DETECTOR_ABSTAINED in reason_codes:
            state = STATE_INCONCLUSIVE
        else:
            state = STATE_NOT_ASSESSED
        if not reason_codes:
            reason_codes.append(REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE)
        elif (
            state == STATE_NOT_ASSESSED
            and REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE not in reason_codes
        ):
            reason_codes.append(REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE)
        score: float | None = None
        arithmetic = None
    elif coverage < minimum_eligible_coverage:
        # An existing, configured gate, kept: below this share of the eligible
        # declared weight the deployment has said it does not want a finding.
        state = STATE_NOT_ASSESSED
        if REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE not in reason_codes:
            reason_codes.append(REASON_INSUFFICIENT_ELIGIBLE_EVIDENCE)
        score = None
        arithmetic = None
    else:
        positive = STATE_INDICATORS_DETECTED in per_check_states
        negative = STATE_NO_INDICATORS_DETECTED in per_check_states
        if positive and negative:
            state = STATE_INCONCLUSIVE
            reason_codes = [REASON_CONFLICTING_RESULTS] + [
                code for code in reason_codes if code != REASON_CONFLICTING_RESULTS
            ]
        elif positive:
            state = STATE_INDICATORS_DETECTED
            if REASON_ABOVE_THRESHOLD not in reason_codes:
                reason_codes.insert(0, REASON_ABOVE_THRESHOLD)
        elif negative:
            state = STATE_NO_INDICATORS_DETECTED
            if REASON_BELOW_THRESHOLD not in reason_codes:
                reason_codes.insert(0, REASON_BELOW_THRESHOLD)
        else:
            state = STATE_INCONCLUSIVE
            if REASON_INTERMEDIATE_SCORE not in reason_codes:
                reason_codes.insert(0, REASON_INTERMEDIATE_SCORE)

        # The score is published for traceability, renormalised over the checks
        # that actually contributed so the arithmetic below reproduces it. It
        # did not decide anything: the state above came from each check's own
        # score against the thresholds.
        terms = []
        total = 0.0
        for check in contributing:
            effective = float(check["declared_weight"]) / contributed_weight
            check["effective_weight"] = round(effective, 6)
            check["contribution"] = round(float(check["score"]) * effective, 6)
            total += float(check["score"]) * effective
            terms.append(f"{float(check['score']):.4f}x{effective:.4f}")
        score = round(total, 6)
        arithmetic = f"{' + '.join(terms)} = {score:.4f}"

    # --- execution status (concept E, never a finding) --------------------- #
    applicable_statuses = [
        str(
            s.get("execution_status")
            or check_status_for(str(s.get("status")), signal_module=fusion_module)
        )
        for s in signals
    ]
    failures = [s for s in applicable_statuses if s == CHECK_FAILED]
    if not failures:
        execution_status = EXECUTION_COMPLETED
    elif len(failures) == len(applicable_statuses) and applicable_statuses:
        execution_status = EXECUTION_FAILED
    else:
        execution_status = EXECUTION_PARTIAL

    # --- limitations ------------------------------------------------------- #
    limitations = list(BASE_LIMITATIONS)
    if state == STATE_NOT_ASSESSED:
        limitations.append(
            "No eligible check produced a result, so nothing here is a finding "
            "about the media -- neither adverse nor exculpatory."
        )
    if observations:
        limitations.append(
            f"{len(observations)} forensic observation(s) are reported for "
            "examiner review. They did not contribute to the assessment state "
            "and must not be read as if they had."
        )
    if unavailable:
        limitations.append(
            "Eligible checks that did not contribute: "
            + ", ".join(
                f"{item['check_id']} ({item['execution_status']})"
                for item in unavailable
            )
            + "."
        )

    return {
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "policy_note": POLICY_NOTE,
        "scope": scope,
        "scope_note": SCOPE_NOTES.get(scope, ""),
        "media_type": media_type,
        "state": state,
        "state_note": STATE_NOTES[state],
        "conclusive": state in CONCLUSIVE_STATES,
        "execution_status": execution_status,
        "execution_note": EXECUTION_NOTES[execution_status],
        "reason_codes": reason_codes,
        "reason_notes": {code: REASON_NOTES[code] for code in reason_codes},
        "score": score,
        "score_semantics": SCORE_SEMANTICS,
        "arithmetic": arithmetic,
        "thresholds": thresholds,
        "eligible_checks": list(eligible_ids),
        "eligible_declared_weight": round(eligible_declared, 6),
        "contributed_weight": round(contributed_weight, 6),
        "coverage": coverage,
        "coverage_basis": (
            "Share of the declared weight of the checks ELIGIBLE for this task "
            "that actually contributed. Ineligible observations are in neither "
            "the numerator nor the denominator."
        ),
        "contributing_checks": contributing,
        "unavailable_checks": unavailable,
        "descriptive_observations": observations,
        "examiner_conclusion": None,
        "examiner_conclusion_note": EXAMINER_CONCLUSION_NOTE,
        "limitations": limitations,
    }
