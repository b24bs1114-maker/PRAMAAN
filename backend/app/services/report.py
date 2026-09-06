"""Forensic report generation.

Produces a self-contained PDF examination report for a case, in the standard
PRAMAAN section order across two pages:

    Page 1 -- Identity and final assessment: case identity, final verdict with
        signal coverage and confidence band, executive finding, evidence
        identity (and any additional exhibits), forensic signals, fusion.
    Page 2 -- Provenance and accountability: provenance and lineage, model
        record, audit integrity, examiner review, limitations.

The two-page layout is the intended shape, and an explicit page break separates
the two halves. The page count is still whatever the content needs -- it is
measured at render time and returned, not assumed. It used to be described here
and in the footer as a fixed number, which produced a "Page 4 of 3" footer on
any case with enough signals or audit events to spill over, and a reader
auditing a forensic document for completeness cannot distinguish that from a
missing page.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    KIND_DETECTOR,
    KIND_FORENSICS,
    KIND_METADATA,
    KIND_PROVENANCE,
    Case,
    Evidence,
    Report,
)
from app.services import (
    analysis_store,
    audit,
    detector as detector_service,
    fusion as fusion_service,
    matching,
    pipeline,
    propagation as propagation_service,
)
from app.utils import pdf
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.report")

REPORT_VERSION = "1.0"
RENDERER_REPORTLAB = "reportlab"
RENDERER_BUILTIN = "builtin-minipdf"

# Audit rows printed in the case timeline. Truncation is stated in the document
# whenever it happens, so a short table is never mistaken for a short history.
TIMELINE_ROW_LIMIT = 8

# Same honesty rule for the other printed tables: a truncated listing always
# says how much was held back and where the full set lives.
MATCH_ROW_LIMIT = 8
ISSUE_ROW_LIMIT = 8

TITLE = "PRAMAAN DIGITAL EVIDENCE EXAMINATION REPORT"

DOCUMENT_STATUS = (
    "PROTOTYPE OUTPUT -- Not a certified forensic opinion. Thresholds and weights "
    "are demonstration defaults and have not been validated against a forensic "
    "reference dataset. Findings require qualified examiner review."
)

LIMITATIONS = (
    "Limitations: Scores are model outputs, not calibrated probabilities, and no error "
    "rate is known for this configuration. Excluded signals are not treated as zero. "
    "Missing metadata/C2PA is not evidence of manipulation. Near-duplicate candidates "
    "measure visual similarity and do not establish derivation or origin. A detector "
    "that did not run is not a finding of authenticity and not a finding of "
    "manipulation. The audit chain is tamper evidence, not tamper proof: it is a linear "
    "SHA-256 hash chain that detects retrospective edits to rows it already covers."
)


def renderer_status() -> dict[str, Any]:
    """Which renderer will be used, and why."""
    try:
        import reportlab  # noqa: F401
    except Exception as exc:
        return {
            "renderer": RENDERER_BUILTIN,
            "reportlab_available": False,
            "reason": f"reportlab not importable ({type(exc).__name__}: {exc})",
            "writer": pdf.WRITER,
            "note": (
                "Rendered by PRAMAAN's built-in minimal PDF writer. Both renderers "
                "lay out the same block list, so the content of the report -- every "
                "section, value and caveat -- is identical either way; only the "
                "typography differs."
            ),
        }
    return {
        "renderer": RENDERER_REPORTLAB,
        "reportlab_available": True,
        "reason": None,
        "writer": f"reportlab {getattr(reportlab, 'Version', 'unknown')}",
        "note": None,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def _score(value: Any) -> str:
    return "-" if value is None else f"{float(value):.4f}"


#: Rendered wherever the record holds no value. The report is a forensic
#: document, so an absent measurement is printed as absent. Every default that
#: used to stand in for one of these -- 512x512 dimensions, a pHash of
#: b487e4860d796b65, 166.23 ms of inference, 1105 audit rows, a 12:53:09
#: timeline -- described a different case entirely and would have been read as a
#: measurement of this one.
NOT_RECORDED = "Not recorded"
NOT_MEASURED = "Not measured"


def _or_none(value: Any, placeholder: str = NOT_RECORDED) -> str:
    """The value as text, or ``placeholder`` when there is nothing to print."""
    if value is None:
        return placeholder
    text = str(value).strip()
    return text or placeholder


def _ms(value: Any) -> str:
    """A duration in milliseconds, or an honest placeholder."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return NOT_MEASURED
    return f"{float(value):.2f} ms"


#: Rendered in place of a state for a record written before assessments were
#: stored. Such a row genuinely holds no assessment, and reconstructing one from
#: its score and today's thresholds would date-stamp a finding this deployment
#: never made. §14: missing historical information renders as not recorded.
NOT_ASSESSED_HISTORICAL = "NOT RECORDED"

#: How each assessment state prints, and in what colour. The report renders the
#: backend's state; it does not classify anything itself. Amber covers both
#: INCONCLUSIVE and NOT_ASSESSED because neither is a finding about the media --
#: but the two print differently, because "we could not decide" and "we did not
#: assess this" are different statements to put in front of a court.
STATE_DISPLAY: dict[str, str] = {
    "INDICATORS_DETECTED": "INDICATORS DETECTED",
    "NO_INDICATORS_DETECTED": "NO INDICATORS DETECTED",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "NOT_ASSESSED": "NOT ASSESSED",
}

STATE_COLOUR: dict[str, str] = {
    "INDICATORS_DETECTED": "#dc2626",
    "NO_INDICATORS_DETECTED": "#16a34a",
    "INCONCLUSIVE": "#d97706",
    "NOT_ASSESSED": "#d97706",
}

#: Colour for a record with no stored assessment. Slate, not amber: the absence
#: of a record is not an inconclusive examination.
NEUTRAL_COLOUR = "#64748b"


def _assessment_of(verdict: dict[str, Any]) -> dict[str, Any]:
    """The stored assessment for one exhibit, or ``{}`` if the row predates it.

    Never synthesises one. An empty dict propagates to
    :data:`NOT_ASSESSED_HISTORICAL` everywhere a state would print, which is the
    honest rendering of a row written under an earlier contract.
    """
    assessed = verdict.get("assessment")
    return dict(assessed) if isinstance(assessed, dict) else {}


def _state_label(assessed: dict[str, Any]) -> str:
    """How this exhibit's state prints. Read, never derived."""
    state = str(assessed.get("state") or "")
    if not state:
        return NOT_ASSESSED_HISTORICAL
    return STATE_DISPLAY.get(state, state.replace("_", " "))


def _leading_contributor(verdict: dict[str, Any]) -> str:
    """The signal that actually contributed most to the fused score.

    Previously hardcoded as "Leading contributor: AI manipulation detector",
    which named the detector even on verdicts the detector abstained from -- and
    on cases whose fused score came entirely from metadata and perceptual
    signals. Fusion publishes each signal's contribution, so the largest one is
    a fact that can be read off the record.
    """
    included = [
        s
        for s in (verdict.get("signals") or [])
        if s.get("included") and isinstance(s.get("contribution"), (int, float))
    ]
    if not included:
        return "Leading contributor: none -- no signal was included in the fused score"
    top = max(included, key=lambda s: float(s["contribution"]))
    return (
        f"Leading contributor: {top.get('name') or top.get('signal_id') or 'unnamed signal'} "
        f"({float(top['contribution']):.4f} of the fused score)"
    )


def _collect(
    session: Session,
    *,
    case: Case,
    settings: Settings,
    actor: str,
    refresh: bool,
) -> dict[str, Any]:
    """Gather everything the report needs."""
    evidence_rows = list(
        session.execute(
            select(Evidence)
            .where(Evidence.case_id == case.id)
            .order_by(Evidence.ingested_at)
        ).scalars()
    )

    items: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        verdict = pipeline.run_fusion(
            session,
            evidence=evidence,
            settings=settings,
            actor=actor,
            refresh=refresh,
        )
        items.append(
            {
                "evidence": evidence,
                "verdict": verdict,
                "metadata": _stage(session, evidence, KIND_METADATA),
                "detector": _stage(session, evidence, KIND_DETECTOR),
                "provenance": _stage(session, evidence, KIND_PROVENANCE),
                "forensics": _stage(session, evidence, KIND_FORENSICS),
            }
        )

    matches = matching.search_case(session, case=case, settings=settings, actor=actor)
    propagation = propagation_service.reconstruct_case(
        session, case=case, settings=settings, actor=actor, refresh=False
    )
    verification = audit.verify_chain(session, case.id)

    return {
        "items": items,
        "matches": matches,
        "propagation": propagation,
        "verification": verification,
        "detector_status": detector_service.status(settings),
        "generated_at": iso(utcnow()),
    }


def _stage(session: Session, evidence: Evidence, kind: str) -> dict[str, Any]:
    row = analysis_store.latest_result(session, evidence_id=evidence.id, kind=kind)
    return dict(row.payload) if row is not None and isinstance(row.payload, dict) else {}


def build_blocks(
    *,
    case: Case,
    collected: dict[str, Any],
    settings: Settings,
    examiner: str | None,
    report_id: str,
    audit_head: str,
) -> list[dict[str, Any]]:
    """Compose the document block list. Length follows the case, not a page budget."""
    blocks: list[dict[str, Any]] = []
    items = collected["items"]
    primary_item = items[0] if items else None

    # Pick primary verdict info. `assessed` is the authoritative object: the
    # report renders the state the backend decided and never classifies the
    # exhibit itself. The old code branched on `"MANIPULATED" in verdict_str`,
    # which was a second decision layer living in the document generator -- it
    # could disagree with the API for the same stored row, and it read
    # "INSUFFICIENT_EVIDENCE" as authentic-adjacent because the substring test
    # happened to fall through.
    verdict_dict = primary_item["verdict"] if primary_item else {}
    assessed = _assessment_of(verdict_dict)
    state = str(assessed.get("state") or "")
    state_label = _state_label(assessed)
    reason_codes = [str(c) for c in (assessed.get("reason_codes") or [])]
    # Legacy token, still printed for continuity with earlier reports of the same
    # case, but never branched on.
    verdict_str = str(verdict_dict.get("verdict") or "INSUFFICIENT_EVIDENCE")
    fused_score = verdict_dict.get("manipulation_score")
    avail_sig = verdict_dict.get("signals_available", 0)
    # The declared signal count comes from fusion. Defaulting it to 5 printed
    # "0 / 5 signals available" for a case with no verdict at all, which reads as
    # five signals having been attempted and none having produced a measurement.
    total_sig = verdict_dict.get("signals_total")
    total_sig_str = str(total_sig) if isinstance(total_sig, int) else "-"
    # Media-aware: how many of the APPLICABLE signals actually ran (evaluated),
    # and how many contributed (available). Inapplicable signals are in neither
    # number -- they are not failed, not zero, and not in the denominator.
    primary_evidence = primary_item["evidence"] if primary_item else None
    detector_payload = primary_item["detector"] if primary_item else {}
    evaluated_sig = verdict_dict.get("signals_evaluated")
    evaluated_sig_str = str(evaluated_sig) if isinstance(evaluated_sig, int) else "-"
    media_type_str = str(
        (primary_evidence.media_type if primary_evidence is not None else "") or "unknown"
    ).upper()
    cov_pct = (
        f"{float(verdict_dict['signal_coverage']) * 100:.0f}%"
        if isinstance(verdict_dict.get("signal_coverage"), (int, float))
        else NOT_MEASURED
    )

    # The metadata, provenance and forensics stage payloads are not read here:
    # the signal matrix is built from fusion's own signal records, which are
    # derived from those payloads and carry the explanation written by the code
    # that did the measuring.

    # Executive finding calculation
    if not items:
        # Distinct from an ambiguous measurement. The generic branch below reads
        # "Ambiguous or insufficient forensic signal measurements were obtained",
        # which describes signals that were measured and came back weak -- on a
        # case with nothing in it, nothing was measured at all.
        exec_finding = (
            "No evidence has been ingested into this case, so no forensic measurement was "
            "attempted and no finding is available. This is not a finding of authenticity "
            "and not a finding of manipulation."
        )
    elif not state:
        # A stored row written before assessments were recorded. Its score and
        # legacy token are printed elsewhere; no finding is asserted here,
        # because re-deriving one from today's thresholds would put a conclusion
        # in the examiner's hands that this deployment never reached.
        exec_finding = (
            "This exhibit's stored analysis predates the recorded assessment "
            "contract, so no assessment state, reason code or scope was kept "
            f"with it. Its legacy verdict token was {verdict_str} and its stored "
            f"score {_score(fused_score)}. No finding is restated here: it cannot "
            "be reconstructed from the record without applying present-day "
            "thresholds to a past examination. Re-run the analysis to obtain a "
            "recorded assessment."
        )
    else:
        # One sentence per state, plus the scope it applies to and the reasons
        # the backend gave. Every branch is keyed on `state` -- not on a
        # substring of a legacy token -- so the document cannot say something
        # the API does not.
        scope_note = str(assessed.get("scope_note") or "").strip()
        reason_text = ""
        if reason_codes:
            notes = assessed.get("reason_notes") or {}
            rendered = [
                str(notes.get(code) or code).rstrip(".") for code in reason_codes
            ]
            reason_text = " Basis: " + "; ".join(rendered) + "."

        if state == "INDICATORS_DETECTED":
            ai_score = detector_payload.get("score")
            ai_str = (
                f"The model returned {_score(ai_score)} manipulation likelihood. "
                if ai_score is not None
                else ""
            )
            exec_finding = (
                "INDICATORS DETECTED. An eligible check reached its positive "
                f"threshold of {settings.verdict_manipulated_threshold:.2f}. "
                f"{ai_str}"
                f"The assessed score is {_score(assessed.get('score'))}."
                f"{reason_text} This states that indicators were found for the "
                "assessed task; it is not a determination that the media is "
                "fake, and it carries no calibrated error rate."
            )
        elif state == "NO_INDICATORS_DETECTED":
            exec_finding = (
                "NO INDICATORS DETECTED. An eligible check reached its negative "
                f"threshold of {settings.verdict_authentic_threshold:.2f}. "
                f"The assessed score is {_score(assessed.get('score'))}."
                f"{reason_text} This is not a certification of authenticity: "
                "checks that did not run could not contribute, and the "
                "assessment covers only the data that was examined."
            )
        elif state == "INCONCLUSIVE":
            exec_finding = (
                "INCONCLUSIVE. An eligible check ran but its result supports no "
                "finding in either direction."
                f"{reason_text} This is a statement about the evidence "
                "available, not about the media: it is neither an indication of "
                "manipulation nor an indication of authenticity."
            )
        else:
            exec_finding = (
                "NOT ASSESSED. No check eligible for the assessed task produced "
                "a result, so the question was not assessed."
                f"{reason_text} Any forensic observations recorded for this "
                "exhibit are context for an examiner, not a finding. This is "
                "not an indication of manipulation and not an indication of "
                "authenticity."
            )

        if scope_note:
            exec_finding += f" Scope of the assessed question: {scope_note}"
        exec_finding += (
            " This result is a decision aid for examiner review, not a "
            "certification."
        )

    # -----------------------------------------------------------------------
    # PAGE 1: IDENTITY, ASSESSMENT, EVIDENCE, SIGNALS, FUSION
    # -----------------------------------------------------------------------
    blocks.append({
        "type": "page_header",
        "case_number": case.case_number,
        "title": _or_none(case.title, "No case title recorded"),
        "page_num": 1,
    })
    blocks.append({"type": "notice", "text": DOCUMENT_STATUS})
    blocks.append({
        "type": "summary_bar",
        "rows": [
            ["CASE ID", case.id],
            # "integration-check" is the actor a verification script passes;
            # printing it as examiner attributed every unattributed report to a
            # script that reviewed nothing.
            ["EXAMINER", _or_none(examiner or case.examiner, "Not specified")],
            ["STATUS", case.status.upper()],
            ["EVIDENCE", f"{len(items)} items"],
        ],
    })
    blocks.append({
        "type": "kv_grid",
        "rows": [
            ["Case number", case.case_number, "Report ID", report_id],
            ["Report version", REPORT_VERSION, "Renderer", renderer_status()["writer"]],
            # Full ISO-8601 with the Z designator: a truncated "2026-09-04
            # 08:50:33" carries no time zone, and a reader cannot tell UTC from
            # the examiner's local clock in an evidence document.
            ["Case created", iso(case.created_at), "Report generated", collected["generated_at"]],
        ],
    })

    # -- FINAL ASSESSMENT: the state, its scope, its basis and its coverage --
    blocks.append({"type": "heading", "text": "FINAL ASSESSMENT"})
    # The card prints the backend's state and carries its own colour, so the
    # renderer does no classification either. `state` is passed through for the
    # colour lookup; an empty one renders neutral, not amber.
    blocks.append({
        "type": "verdict_card",
        "verdict": state_label,
        "state": state,
        "score_line": (
            f"Assessed score: {_score(assessed.get('score'))}"
            if state
            else f"Stored legacy score: {_score(fused_score)}"
        ),
        "leading": _leading_contributor(verdict_dict),
    })
    # The assessment record: what question was asked, under which policy, on what
    # basis, and how much of the eligible evidence it rests on. Printed from the
    # stored object; nothing here is recomputed.
    if state:
        eligible = [str(c) for c in (assessed.get("eligible_checks") or [])]
        contributing = [
            str(c.get("check_id"))
            for c in (assessed.get("contributing_checks") or [])
            if isinstance(c, dict)
        ]
        unavailable = [
            f"{c.get('check_id')} ({c.get('execution_status')})"
            for c in (assessed.get("unavailable_checks") or [])
            if isinstance(c, dict)
        ]
        observations = [
            str(o.get("name") or o.get("observation_id"))
            for o in (assessed.get("descriptive_observations") or [])
            if isinstance(o, dict)
        ]
        assessed_coverage = assessed.get("coverage")
        assessed_cov_pct = (
            f"{float(assessed_coverage) * 100:.0f}%"
            if isinstance(assessed_coverage, (int, float))
            else NOT_MEASURED
        )
        blocks.append({
            "type": "kv",
            "rows": [
                ["ASSESSMENT STATE", state_label],
                [
                    "ASSESSED QUESTION",
                    _or_none(assessed.get("scope_note") or assessed.get("scope")),
                ],
                [
                    "DECISION POLICY",
                    f"{_or_none(assessed.get('policy_id'))} "
                    f"v{_or_none(assessed.get('policy_version'))}",
                ],
                # Reason codes verbatim. Structured, so this line cannot drift
                # from the reasons the backend recorded.
                ["BASIS", ", ".join(reason_codes) or NOT_RECORDED],
                # Completed vs conclusive, side by side, because they are
                # different facts and a reader must not collapse them.
                [
                    "PROCESSING",
                    f"{_or_none(assessed.get('execution_status'))} — "
                    f"finding reached: {'yes' if assessed.get('conclusive') else 'no'}",
                ],
                [
                    "ELIGIBLE CHECKS",
                    ", ".join(eligible) or NOT_RECORDED,
                ],
                [
                    "CONTRIBUTED TO STATE",
                    ", ".join(contributing)
                    or "None — no eligible check contributed",
                ],
                [
                    "ELIGIBLE BUT ABSENT",
                    ", ".join(unavailable) or "None",
                ],
                [
                    "ASSESSED COVERAGE",
                    f"{assessed_cov_pct} of the eligible checks' declared weight",
                ],
                # Named as observations, in their own row, so nothing implies
                # they moved the state.
                [
                    "OBSERVATIONS (NOT PART OF THE STATE)",
                    ", ".join(observations) or "None recorded",
                ],
                [
                    "EXAMINER CONCLUSION",
                    _or_none(assessed.get("examiner_conclusion"), NOT_RECORDED),
                ],
            ],
        })
    else:
        blocks.append({
            "type": "kv",
            "rows": [
                ["ASSESSMENT STATE", NOT_ASSESSED_HISTORICAL],
                ["LEGACY VERDICT TOKEN", verdict_str],
                [
                    "ASSESSED QUESTION",
                    "Not recorded — this analysis predates the assessment "
                    "contract, and its scope was not stored.",
                ],
                ["DECISION POLICY", NOT_ASSESSED_HISTORICAL],
                ["BASIS", NOT_ASSESSED_HISTORICAL],
            ],
        })
    blocks.append({
        "type": "kv",
        "rows": [
            # Media-aware summary: Applicable / Evaluated / Contributing, all
            # from fusion's own counts for this exhibit's media type. Hidden
            # (inapplicable) signals are in none of the three numbers.
            [
                "SIGNAL COVERAGE",
                f"Applicable: {total_sig_str} ({media_type_str}) | "
                f"Evaluated: {evaluated_sig_str} | Contributing: {avail_sig} | "
                f"{cov_pct} of applicable declared weight",
            ],
            # The band, never a number: fusion emits low/moderate/none and none
            # is calibrated, so a percentage would be invented precision.
            ["CONFIDENCE BAND", _or_none(verdict_dict.get("confidence"), NOT_RECORDED)],
        ],
    })

    # -- EXECUTIVE FINDING --
    blocks.append({"type": "heading", "text": "EXECUTIVE FINDING"})
    blocks.append({"type": "paragraph", "text": exec_finding})

    # -- EVIDENCE IDENTITY: the primary exhibit, in full --
    blocks.append({"type": "heading", "text": "EVIDENCE IDENTITY"})
    if primary_evidence is not None:
        if primary_evidence.width and primary_evidence.height:
            dim_str = (
                f"{primary_evidence.width} x {primary_evidence.height} "
                f"{primary_evidence.image_format or primary_evidence.media_type.upper()}"
            )
        else:
            dim_str = _or_none(
                primary_evidence.image_format or primary_evidence.media_type.upper()
            ) + f" (dimensions {NOT_RECORDED.lower()})"
        blocks.append({
            "type": "kv",
            "rows": [
                ["Filename", primary_evidence.filename],
                ["Evidence ID", primary_evidence.id, True],
                ["SHA-256", primary_evidence.sha256, True],
                ["Media type", f"{primary_evidence.media_type} ({primary_evidence.mime_type})"],
                ["Dimensions", dim_str],
                ["Size", f"{primary_evidence.size_bytes:,} bytes"],
                # pHash/dHash only when extracted -- the old 512x512 and the
                # b487.../ccac... pair belonged to one sample and were printed
                # for every exhibit that lacked them.
                ["pHash / dHash", f"{_or_none(primary_evidence.phash)} / {_or_none(primary_evidence.dhash)}", True],
                [
                    "Synthetic corpus",
                    "Yes -- SYNTHETIC DEMO DATA, not a real-world observation"
                    if primary_evidence.is_synthetic
                    else "No -- ingested as real evidence",
                ],
            ],
        })
    else:
        blocks.append({"type": "paragraph", "text": "No evidence has been ingested into this case."})

    # Additional exhibits are still identified by evidence id and SHA-256 -- the
    # two things that identify a file -- so a multi-exhibit case never lists one
    # by filename alone. Their verdicts appear here; the primary's is above.
    extra_items = items[1:]
    if extra_items:
        blocks.append({"type": "heading", "text": "ADDITIONAL EXHIBITS"})
        blocks.append({
            "type": "table",
            "columns": ["Filename", "Evidence ID", "SHA-256", "Assessment", "Score"],
            # The verdict column is widened so a long token like
            # "INSUFFICIENT_EVIDENCE" prints on one line rather than splitting
            # mid-word; the id/hash columns are monospace and wrap cleanly.
            "widths": [1.4, 1.5, 1.4, 2.2, 0.6],
            # Each extra exhibit prints its own assessment state, read from its
            # own stored assessment. Previously the legacy token, which could not
            # distinguish an unassessed exhibit from an undecidable one.
            "rows": [
                [
                    it["evidence"].filename,
                    it["evidence"].id,
                    it["evidence"].sha256,
                    _state_label(_assessment_of(it["verdict"])),
                    _score(
                        _assessment_of(it["verdict"]).get("score")
                        if _assessment_of(it["verdict"])
                        else it["verdict"].get("manipulation_score")
                    ),
                ]
                for it in extra_items
            ],
            "mono_columns": [1, 2],
        })

    # -- FORENSIC SIGNALS: one row per signal fusion produced, in its order --
    blocks.append({"type": "heading", "text": "FORENSIC SIGNALS"})
    signals_list = verdict_dict.get("signals") or []
    primary_ids = set(verdict_dict.get("primary_signals") or [])
    matrix_rows = []
    for sig in signals_list:
        # The signal's own explanation and status are printed verbatim; nothing
        # here re-derives a finding. Named `role_str`, not `state`: the exhibit's
        # assessment state is a different quantity read above, and reusing the
        # name here shadowed it for the FUSION block below.
        #
        # Four values, because "measured but not eligible" and "produced no
        # measurement" are different facts and the earlier three-value column
        # printed both as EXCLUDED. A compression anomaly that scored 0.95 and a
        # detector that could not load both read as excluded, which made a real
        # observation look like a failure.
        if sig.get("included"):
            role_str = "PRIMARY" if sig.get("signal_id") in primary_ids else "INCLUDED"
        elif sig.get("measured"):
            role_str = "OBSERVATION"
        else:
            role_str = "NO MEASUREMENT"
        matrix_rows.append([
            _or_none(sig.get("name") or sig.get("signal_id"), "Unnamed signal"),
            _or_none(sig.get("status"), NOT_RECORDED).upper(),
            _score(sig.get("score")),
            role_str,
            _or_none(sig.get("explanation"), "No finding recorded for this signal."),
        ])
    if not matrix_rows:
        matrix_rows = [["No signals recorded", "-", "-", "-", "Fusion produced no signal record for this exhibit."]]
    blocks.append({
        "type": "table",
        "columns": ["Signal", "Status", "Score", "Role", "Finding"],
        "widths": [1.9, 1.3, 0.8, 1.1, 3.1],
        "rows": matrix_rows,
    })
    blocks.append({
        "type": "paragraph",
        "text": (
            "Role column: PRIMARY and INCLUDED signals contributed to the "
            "assessment state. OBSERVATION means the signal produced a real "
            "measurement that is reported for examiner review but is not "
            "eligible to determine this task's state -- missing metadata, an "
            "absent C2PA manifest, compression characteristics, perceptual "
            "distances and file hashes are forensic observations, not "
            "synthetic-media evidence. NO MEASUREMENT means the signal produced "
            "no value at all: unavailable, abstained, failed or inapplicable. "
            "None of these is a score of zero."
        ),
    })

    # -- FUSION: the weights, the arithmetic, the decision, fusion's rationale --
    blocks.append({"type": "heading", "text": "FUSION"})
    # Weights actually applied, read from the verdict record -- never the old
    # hardcoded "AI 0.35 - pHash 0.20 ..." line, which described a fusion that
    # had not been run whenever the deployment was configured differently.
    # Media-aware: only the APPLICABLE signals' declared weights are printed,
    # so a video or audio report does not list image-only weights that fusion
    # never considered (their weight sits outside the coverage denominator).
    declared_weights = verdict_dict.get("declared_weights") or {}
    applicable_ids = {
        s.get("signal_id") for s in (verdict_dict.get("signals") or [])
    }
    if declared_weights:
        weights_str = ";  ".join(
            f"{fusion_service.SIGNAL_NAMES.get(sid, sid)} {float(w):.2f}"
            for sid, w in declared_weights.items()
            if not applicable_ids or sid in applicable_ids
        ) or NOT_RECORDED
    else:
        weights_str = NOT_RECORDED
    # No fallback arithmetic: the old "0.9969 x 0.7778 + ... = 0.8220" default
    # was a complete worked fusion for a case that had none.
    arithmetic_str = _or_none(
        verdict_dict.get("arithmetic"), "No fused arithmetic (no signal was included)"
    )
    # The interpretation line names the state and the thresholds it was decided
    # against. Both come from the stored assessment, which republishes the
    # thresholds that were in force when it was made -- reading today's settings
    # would describe a past examination with present configuration.
    if state:
        stored_thresholds = assessed.get("thresholds") or {}
        positive = stored_thresholds.get(
            "manipulated_at_or_above", settings.verdict_manipulated_threshold
        )
        negative = stored_thresholds.get(
            "authentic_at_or_below", settings.verdict_authentic_threshold
        )
        assessed_score = assessed.get("score")
        if isinstance(assessed_score, (int, float)):
            decision_str = (
                f"{state_label} — assessed score {_score(assessed_score)} against "
                f"the thresholds recorded with this assessment: negative at or "
                f"below {float(negative):.2f}, positive at or above "
                f"{float(positive):.2f}"
            )
        else:
            decision_str = (
                f"{state_label} — no eligible check produced a score, so no "
                "threshold was applied. Not a score of zero."
            )
    elif isinstance(fused_score, (int, float)):
        decision_str = (
            f"{verdict_str} (legacy token) — stored score {_score(fused_score)}. "
            "No assessment state, and no thresholds, were recorded with this "
            "analysis, so the score is reported without an interpretation."
        )
    else:
        decision_str = (
            f"{verdict_str} (legacy token) — no score was produced, so no "
            "threshold was applied"
        )
    blocks.append({
        "type": "kv",
        "rows": [
            ["DECLARED WEIGHTS", weights_str],
            ["FUSED SCORE", arithmetic_str],
            ["INTERPRETATION", decision_str],
            # Fusion's own words, verbatim -- the only decision prose that cannot
            # drift from the reasoning fusion actually applied.
            ["RATIONALE", _or_none(verdict_dict.get("rationale"), "No rationale was recorded")],
        ],
    })

    blocks.append({"type": "pagebreak"})

    # -----------------------------------------------------------------------
    # PAGE 2: PROVENANCE, MODEL, AUDIT, EXAMINER REVIEW, LIMITATIONS
    # -----------------------------------------------------------------------
    blocks.append({
        "type": "page_header",
        "case_number": case.case_number,
        "title": "Provenance, model, audit & examiner review",
        "page_num": 2,
    })

    # -- PROVENANCE & LINEAGE --
    blocks.append({"type": "heading", "text": "PROVENANCE & LINEAGE"})
    origin = collected["propagation"].get("origin") or {}
    origin_filename = origin.get("filename")
    total_candidates = collected["matches"].get("total_candidates")
    blocks.append({
        "type": "lineage_flow",
        "current": primary_evidence.filename if primary_evidence else "No evidence item",
        "corpus": "No retained candidate" if not total_candidates else f"{total_candidates} candidates",
        # Falling back to the current file labelled this exhibit the earliest
        # known instance even when nothing was found to compare it with.
        "earliest": _or_none(origin_filename, "No earlier instance in corpus"),
    })
    lineage_note = (
        "Origin wording is deliberately scoped: earliest known instance in the "
        "indexed evidence corpus. It is not a claim of absolute real-world origin."
    )
    if origin.get("timestamp_is_tied"):
        tied = origin.get("tied_earliest_evidence_ids") or []
        lineage_note += (
            f" {len(tied)} instances share the earliest recorded timestamp "
            f"({origin.get('timestamp')}), so which came first is NOT established "
            "by the record; the instance named above was selected deterministically."
        )
    blocks.append({"type": "paragraph", "text": lineage_note})

    # -- MODEL RECORD: identity of the model that scored the primary exhibit --
    blocks.append({"type": "heading", "text": "MODEL RECORD"})
    det_status = collected["detector_status"]
    # Identity comes from this exhibit's detection record first, scoped to its own
    # modality; the live status is only a fallback. No fabricated default names a
    # model ("SwinB-AI-Image-Detector") on a deployment with none installed.
    modality_status: dict[str, Any] = {}
    if primary_evidence is not None:
        modality_status = (det_status.get("modalities") or {}).get(
            primary_evidence.media_type, {}
        ) or {}

    def _identity(key: str) -> Any:
        for source in (detector_payload, modality_status):
            value = source.get(key)
            if value not in (None, "", "none", "0"):
                return value
        return None

    load_ms = detector_payload.get("model_load_ms")
    blocks.append({
        "type": "kv",
        "rows": [
            ["Model", _or_none(_identity("model"), "No detector installed")],
            ["Version", _or_none(_identity("model_version"), NOT_RECORDED)],
            ["Adapter", _or_none(_identity("adapter"), NOT_RECORDED)],
            ["Interface version", _or_none(_identity("interface_version"), NOT_RECORDED)],
            ["Inference", _ms(detector_payload.get("inference_ms"))],
            ["Model load", _ms(load_ms) if load_ms is not None else "Not loaded on this call"],
            ["Weights SHA-256", _or_none(_identity("weights_hash"), NOT_RECORDED), True],
        ],
    })

    # -- AUDIT INTEGRITY --
    blocks.append({"type": "heading", "text": "AUDIT INTEGRITY"})
    verification = collected["verification"]
    total_rows = verification.get("total_rows")
    case_rows = verification.get("case_rows")
    blocks.append({
        "type": "kv",
        "rows": [
            ["CHAIN STATUS", "VALID" if verification.get("valid") else "INVALID"],
            # Counts are read from the verification result or reported absent --
            # never the old 1,105 / 32 defaults, a count of records never written.
            ["ROWS FOR CASE", str(case_rows) if isinstance(case_rows, int) else NOT_RECORDED],
            ["ROWS IN CHAIN", f"{total_rows:,}" if isinstance(total_rows, int) else NOT_RECORDED],
            ["FIRST INVALID ROW", str(verification.get("first_invalid_seq") or "None")],
            # Printed in full: an abbreviated head cannot be re-verified against
            # the chain or compared with /api/cases/{id}/audit.
            ["HEAD HASH", audit_head or NOT_RECORDED, True],
            ["GENESIS HASH", audit.GENESIS_HASH, True],
        ],
    })
    issues = [i for i in (verification.get("issues") or []) if isinstance(i, dict)]
    if issues:
        shown_issues = issues[:ISSUE_ROW_LIMIT]
        blocks.append({
            "type": "table",
            "columns": ["Seq", "Problem", "Detail"],
            "widths": [0.8, 1.7, 4.0],
            "rows": [
                [
                    f"{int(i.get('seq')):,}" if isinstance(i.get("seq"), int) and not isinstance(i.get("seq"), bool) else NOT_RECORDED,
                    _or_none(i.get("problem")),
                    _or_none(i.get("detail")),
                ]
                for i in shown_issues
            ],
        })
        if len(issues) > len(shown_issues):
            blocks.append({
                "type": "paragraph",
                "text": (
                    f"Showing the first {len(shown_issues)} of {len(issues)} issues; "
                    "the full set is served by the audit verify endpoint."
                ),
            })
    blocks.append({
        "type": "paragraph",
        "text": (
            "The audit trail is a linear SHA-256 hash chain over this case's events; "
            "the head hash above anchors this document to the chain as it stood at "
            "generation. Individual events remain available in full on the case audit "
            "trail. This is tamper evidence, not tamper proof. "
            "This PDF's own SHA-256 digest and size in bytes are recorded in the "
            "audit chain in the REPORT_GENERATED row written when this document was "
            "produced. A document cannot contain its own digest: the row's hash "
            "covers the digest of the bytes on disk, so the pairing is verifiable "
            "outside this file by recomputing sha256 over the downloaded bytes and "
            "checking the chain."
        ),
    })

    # -- EXAMINER REVIEW --
    blocks.append({"type": "heading", "text": "EXAMINER REVIEW"})
    blocks.append({
        "type": "kv",
        "rows": [
            ["Examiner", _or_none(examiner or case.examiner, "Not specified")],
            ["Organisation", "____________________________"],
            ["Signature", "____________________________"],
            ["Date", "____________________________"],
            # Unchecked: shipping "accepted" pre-ticked records a conclusion
            # before any review took place.
            ["Review decision", "[ ] accepted   [ ] amended   [ ] rejected"],
        ],
    })
    blocks.append({
        "type": "paragraph",
        "text": (
            "This report is machine-generated by PRAMAAN from the measurements recorded "
            "for this case. It carries no examiner opinion until the review decision "
            "above is completed and signed."
        ),
    })

    # -- LIMITATIONS --
    blocks.append({"type": "heading", "text": "LIMITATIONS"})
    blocks.append({"type": "paragraph", "text": LIMITATIONS})

    return blocks


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render(
    blocks: list[dict[str, Any]], *, case: Case, examiner: str | None, created: str | None
) -> tuple[bytes, int, str]:
    """Render blocks to PDF bytes, returning (bytes, page count, renderer)."""
    footer = "PRAMAAN | Prototype examination report"
    status = renderer_status()

    if status["renderer"] == RENDERER_REPORTLAB:
        try:
            # Two passes: the first measures the document, the second stamps the
            # real page total into every footer. Rendering is cheap relative to
            # the pipeline that produced these blocks, and a footer that
            # contradicts the document is not acceptable in a forensic report.
            _, measured = _render_reportlab(
                blocks,
                title=TITLE,
                author=examiner or "PRAMAAN",
                footer=footer,
                case_number=case.case_number,
            )
            data, pages = _render_reportlab(
                blocks,
                title=TITLE,
                author=examiner or "PRAMAAN",
                footer=footer,
                case_number=case.case_number,
                total_pages=measured,
            )
            return data, pages, RENDERER_REPORTLAB
        except Exception:
            logger.exception("ReportLab rendering failed; using the built-in writer")

    data, pages = pdf.render(
        blocks,
        title=TITLE,
        author=examiner or "PRAMAAN",
        subject=f"Case {case.case_number}",
        footer=footer,
        created=created,
    )
    return data, pages, RENDERER_BUILTIN


def _render_reportlab(
    blocks: list[dict[str, Any]],
    *,
    title: str,
    author: str,
    footer: str,
    case_number: str,
    total_pages: int | None = None,
) -> tuple[bytes, int]:
    """Render with ReportLab platypus.

    ``total_pages`` is the number to print in the "Page N of ..." footer. It is
    ``None`` on the measuring pass, when the count is not yet known.
    """
    import io
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    sheet = getSampleStyleSheet()
    body = ParagraphStyle("PramaanBody", parent=sheet["BodyText"], fontSize=8.5, leading=11, spaceAfter=2)
    mono = ParagraphStyle("PramaanMono", parent=body, fontName="Courier", fontSize=7.5, leading=9.5)
    heading = ParagraphStyle("PramaanHeading", parent=sheet["Heading2"], fontSize=10, leading=12, spaceBefore=6, spaceAfter=2, fontName="Helvetica-Bold")
    title_style = ParagraphStyle("PramaanTitle", parent=sheet["Title"], fontSize=16, leading=18, spaceAfter=2, fontName="Helvetica-Bold")

    grid = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("LEADING", (0, 0), (-1, -1), 9.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cbd5e1")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])
    
    kv_style = TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.0),
        ("LEADING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ])

    def para(text: Any, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape("" if text is None else str(text)), style)

    def esc(text: Any) -> str:
        """Escape a value for interpolation into ReportLab's inline markup.

        Needed wherever a caller builds its own `<b>`/`<font>` markup: `para()`
        escapes the *whole* string, so `para(f"<b>{c}</b>")` emitted a literal
        `<b>` in the PDF rather than bold text. Interpolating raw values instead
        is the opposite failure -- an `&` or `<` in a case title aborts the
        render -- so the markup is written literally and only the data escaped.
        """
        return escape("" if text is None else str(text))

    story: list[Any] = []
    for block in blocks:
        kind = block.get("type", "paragraph")
        if kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "page_header":
            header_text = "<b>PRAMAAN</b><br/><font size=7 color='#64748b'>DIGITAL EVIDENCE EXAMINATION</font>"
            header_right = f"<b>CASE {esc(block['case_number'])}</b><br/><font size=8 color='#334155'>{esc(block['title'])}</font>"
            t = Table([[Paragraph(header_text, body), Paragraph(header_right, ParagraphStyle("RightHeader", parent=body, alignment=2))]], colWidths=[250, 254])
            t.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'), ('PADDING', (0,0), (-1,-1), 0)]))
            story.append(t)
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0f172a"), spaceBefore=3, spaceAfter=6))
        elif kind == "notice":
            notice_style = ParagraphStyle("Notice", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#334155"))
            t = Table([[Paragraph(f"<b>PROTOTYPE OUTPUT</b>  {esc(block['text'])}", notice_style)]], colWidths=[504])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "summary_bar":
            cells = [[Paragraph(f"<font color='#64748b'>{esc(r[0])}</font><br/><b>{esc(r[1])}</b>", body) for r in block["rows"]]]
            t = Table(cells, colWidths=[126, 126, 126, 126])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "verdict_card":
            # Colour is looked up from the backend's state, not matched out of
            # the displayed text. The old substring test made the renderer a
            # third place that classified an exhibit, and it coloured an
            # unrecorded assessment amber as though it had been undecidable.
            v_color = STATE_COLOUR.get(str(block.get("state") or ""), NEUTRAL_COLOUR)
            v_title = Paragraph(f"<font color='{v_color}' size=18><b>{esc(block['verdict'])}</b></font>", body)
            v_sub = Paragraph(f"<b>{esc(block['score_line'])}</b>", body)
            v_lead = Paragraph(f"<font color='#64748b'>{esc(block['leading'])}</font>", body)
            t = Table([[v_title], [v_sub], [v_lead]], colWidths=[504])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
                ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor(v_color)),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "heading":
            story.append(Paragraph(f"<b><font size=9 color='#0f172a'>{esc(block['text'])}</font></b>", heading))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1"), spaceBefore=1, spaceAfter=4))
        elif kind == "paragraph":
            story.append(para(block["text"], body))
        elif kind == "kv":
            rows = [[para(row[0]), para(row[1], mono if len(row) > 2 and row[2] else body)] for row in block.get("rows", [])]
            if rows:
                t = Table(rows, colWidths=[150, 354])
                t.setStyle(kv_style)
                story.append(t)
                story.append(Spacer(1, 3))
        elif kind == "kv_grid":
            rows = []
            for r in block.get("rows", []):
                rows.append([para(r[0]), para(r[1]), para(r[2]), para(r[3])])
            t = Table(rows, colWidths=[100, 152, 100, 152])
            t.setStyle(kv_style)
            story.append(t)
            story.append(Spacer(1, 3))
        elif kind == "lineage_flow":
            cell1 = Paragraph(f"<b>CURRENT FILE</b><br/>{esc(block['current'])}<br/><font color='#64748b'>Submitted as case evidence</font>", body)
            cell2 = Paragraph(f"<b>INDEXED CORPUS</b><br/>{esc(block['corpus'])}<br/><font color='#64748b'>Local corpus search</font>", body)
            cell3 = Paragraph(f"<b>EARLIEST KNOWN INSTANCE</b><br/>{esc(block['earliest'])}<br/><font color='#64748b'>Earliest in indexed corpus</font>", body)
            t = Table([[cell1, Paragraph("<b>-></b>", ParagraphStyle("Arrow", parent=body, alignment=1)), cell2, Paragraph("<b>-></b>", ParagraphStyle("Arrow", parent=body, alignment=1)), cell3]], colWidths=[150, 20, 150, 20, 164])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f8fafc")),
                ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#f8fafc")),
                ("BACKGROUND", (4, 0), (4, 0), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#cbd5e1")),
                ("BOX", (2, 0), (2, 0), 0.5, colors.HexColor("#cbd5e1")),
                ("BOX", (4, 0), (4, 0), 0.5, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)
            story.append(Spacer(1, 4))
        elif kind == "table":
            columns = block.get("columns", [])
            if not columns:
                continue
            weights = block.get("widths") or [1.0] * len(columns)
            total = sum(weights) or 1.0
            widths = [504 * w / total for w in weights]
            # `para()` escapes its whole argument, so `para(f"<b>{c}</b>")` put a
            # literal `<b>` in front of all 12 column headings in the PDF.
            data = [[Paragraph(f"<b>{esc(c)}</b>", body) for c in columns]]
            # Identifiers and digests are set in Courier so a reader comparing a
            # hash character by character is not fighting proportional glyphs.
            mono_cols = set(block.get("mono_columns") or ())
            for row in block.get("rows", []):
                data.append([
                    para(
                        row[i] if i < len(row) else "",
                        mono if i in mono_cols else body,
                    )
                    for i in range(len(columns))
                ])
            t = Table(data, colWidths=widths, repeatRows=1)
            t.setStyle(grid)
            story.append(t)
            story.append(Spacer(1, 4))

    buffer = io.BytesIO()
    doc_template = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=36,
        bottomMargin=36,
        title=title,
        author=author,
        subject=footer,
    )

    def stamp(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.line(54, 30, letter[0] - 54, 30)
        canvas.drawString(54, 18, footer)
        # "of N" only once N is known. The total was hardcoded to 3, so a report
        # whose content ran onto a fourth page footed it "Page 4 of 3" -- and a
        # reader checking a forensic document for completeness cannot tell that
        # from a missing page. ``render()`` measures the document, then re-renders
        # with the real total.
        label = f"Page {doc.page} of {total_pages}" if total_pages else f"Page {doc.page}"
        canvas.drawRightString(letter[0] - 54, 18, label)
        canvas.restoreState()

    doc_template.build(story, onFirstPage=stamp, onLaterPages=stamp)
    return buffer.getvalue(), doc_template.page


def generate(
    session: Session,
    *,
    case: Case,
    settings: Settings,
    actor: str = "api",
    examiner: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Generate the PDF, hash it, persist it and record it in the audit chain."""
    collected = _collect(
        session, case=case, settings=settings, actor=actor, refresh=refresh
    )
    report_id = str(uuid.uuid4())
    audit_head = audit.head_hash(session)

    blocks = build_blocks(
        case=case,
        collected=collected,
        settings=settings,
        examiner=examiner,
        report_id=report_id,
        audit_head=audit_head,
    )
    data, pages, renderer = render(
        blocks,
        case=case,
        examiner=examiner or case.examiner,
        created=collected["generated_at"],
    )

    digest = hashlib.sha256(data).hexdigest()
    safe_case = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in case.case_number
    )
    # The case number already carries the "PRAMAAN-" prefix, so the download name
    # is "PRAMAAN-<case-number>-Forensic-Report.pdf" without doubling it. This is
    # the friendly name a reader saves; it is deliberately NOT unique, because a
    # case's reports should all download under the same recognisable name.
    download_name = f"{safe_case}-Forensic-Report.pdf"
    # The on-disk name IS unique. A second report of the same case must not
    # overwrite the first -- that would silently invalidate the earlier report's
    # recorded SHA-256 and the bytes its download and audit row point at.
    stored_name = f"{safe_case}-Forensic-Report-{report_id[:8]}.pdf"
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / stored_name
    path.write_bytes(data)

    verification = collected["verification"]
    row = Report(
        id=report_id,
        case_id=case.id,
        filename=download_name,
        stored_path=stored_name,
        size_bytes=len(data),
        sha256=digest,
        generator=f"pramaan-report/{REPORT_VERSION}",
        renderer=renderer,
        pages=pages,
        examiner=examiner or case.examiner,
        audit_head_hash=audit_head,
        audit_valid=bool(verification.get("valid")),
        payload={
            # What this document asserted, recorded per exhibit so a stored
            # report stays checkable against the assessment it rendered. The
            # assessment fields are the authoritative ones; `verdict` is kept
            # because earlier report rows carry it and consumers read it.
            "verdicts": [
                {
                    "evidence_id": item["evidence"].id,
                    "filename": item["evidence"].filename,
                    "sha256": item["evidence"].sha256,
                    "verdict": item["verdict"].get("verdict"),
                    "assessment_state": _assessment_of(item["verdict"]).get("state"),
                    "assessment_scope": _assessment_of(item["verdict"]).get("scope"),
                    "assessment_policy_version": _assessment_of(
                        item["verdict"]
                    ).get("policy_version"),
                    "assessment_reason_codes": _assessment_of(item["verdict"]).get(
                        "reason_codes"
                    ),
                    "assessment_score": _assessment_of(item["verdict"]).get("score"),
                    "manipulation_score": item["verdict"].get("manipulation_score"),
                    "confidence": item["verdict"].get("confidence"),
                    "signals_available": item["verdict"].get("signals_available"),
                }
                for item in collected["items"]
            ],
            "match_candidates": collected["matches"].get("total_candidates"),
            "timeline_events": len(collected["propagation"].get("timeline") or []),
            "origin_evidence_id": (collected["propagation"].get("origin") or {}).get(
                "evidence_id"
            ),
            "renderer_status": renderer_status(),
            "document_status": DOCUMENT_STATUS,
        },
    )
    session.add(row)
    session.flush()

    audit.record(
        session,
        event=audit.EVENT_REPORT_GENERATED,
        case_id=case.id,
        actor=actor,
        details={
            "report_id": report_id,
            "filename": download_name,
            "stored_path": stored_name,
            "sha256": digest,
            "size_bytes": len(data),
            "pages": pages,
            "renderer": renderer,
            "generator": row.generator,
            "audit_head_hash_at_generation": audit_head,
            "audit_chain_valid": bool(verification.get("valid")),
            "evidence_count": len(collected["items"]),
        },
    )

    return {
        "case_id": case.id,
        "report_id": report_id,
        "filename": download_name,
        "path": str(path),
        "size_bytes": len(data),
        "sha256": digest,
        "generated_at": collected["generated_at"],
        "generator": row.generator,
        "renderer": renderer,
        "pages": pages,
        "audit_head_hash": audit_head,
        "audit_chain_valid": bool(verification.get("valid")),
        "document_status": DOCUMENT_STATUS,
        "renderer_status": renderer_status(),
        "download_url": f"/api/cases/{case.id}/reports/{report_id}",
    }


def _report_row(row: Report, case: Case | None = None) -> dict[str, Any]:
    """One stored report, described by what is recorded about it.

    Every field here is read back from the row -- the renderer that produced the
    document, the page count it came out at, the digest of the bytes on disk, the
    audit head that was current when it was sealed. Nothing is recomputed, so a
    listing cannot disagree with the document it lists.

    Two fields the generate response carries are deliberately absent:

    ``renderer_status`` reports which renderer is importable *now*. Attaching it
    to a stored row would describe a document by an environment it was never
    rendered in -- a PDF written by the built-in writer would start claiming
    reportlab the moment reportlab was installed. The row already carries
    ``renderer``, which is the fact about *this* document; current-environment
    state belongs on the envelope, and ``ReportLibraryResponse.renderer`` is
    where it lives.

    ``path`` is the absolute location of the file on the host. The download URL
    is what a client needs, and a list is the wrong place to hand out the
    server's filesystem layout row by row.

    ``document_status`` *is* included: it is the caveat printed into the document
    itself, so it is a property of the report, and a reader inspecting a stored
    report should see the same standing as a reader who just generated one.
    """
    return {
        "case_id": row.case_id,
        "case_number": case.case_number if case is not None else None,
        "case_title": case.title if case is not None else None,
        "report_id": row.id,
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "generated_at": iso(row.created_at),
        "generator": row.generator,
        "renderer": row.renderer,
        "pages": row.pages,
        "audit_head_hash": row.audit_head_hash,
        "audit_chain_valid": row.audit_valid,
        "document_status": DOCUMENT_STATUS,
        "download_url": f"/api/cases/{row.case_id}/reports/{row.id}",
    }


def count_reports(session: Session, case_id: str | None = None) -> int:
    statement = select(func.count()).select_from(Report)
    if case_id is not None:
        statement = statement.where(Report.case_id == case_id)
    return int(session.execute(statement).scalar_one())


def list_reports(
    session: Session,
    case_id: str | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    statement = (
        select(Report, Case)
        .outerjoin(Case, Case.id == Report.case_id)
        .order_by(Report.created_at.desc())
    )
    if case_id is not None:
        statement = statement.where(Report.case_id == case_id)
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return [_report_row(row, case) for row, case in session.execute(statement).all()]


def report_file(
    session: Session, *, case_id: str, report_id: str, settings: Settings
) -> tuple[Report, Path] | None:
    row = session.get(Report, report_id)
    if row is None or row.case_id != case_id:
        return None
    path = settings.reports_dir / row.stored_path
    if not path.is_file():
        return None
    return row, path
