"""Forensic report generation.

Produces a self-contained PDF examination report for a case, in the standard
PRAMAAN section order:

    Executive summary and verdict, evidence snapshot, case identity
    Forensic signal matrix, evidence integrity
    Fusion arithmetic and interpretation, provenance and lineage
    Audit integrity, case timeline, examiner sign-off

The page count is whatever the content needs -- it is measured at render time and
returned, not assumed. It used to be described here and in the footer as exactly
three, which produced a "Page 4 of 3" footer on any case with enough signals or
audit events to spill over, and a reader auditing a forensic document for
completeness cannot distinguish that from a missing page.
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
    KIND_FUSION,
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
    forensics as forensics_service,
    fusion as fusion_service,
    matching,
    metadata as metadata_service,
    pipeline,
    propagation as propagation_service,
    provenance as provenance_service,
)
from app.utils import pdf
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.report")

REPORT_VERSION = "1.0"
RENDERER_REPORTLAB = "reportlab"
RENDERER_BUILTIN = "builtin-minipdf"

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
            "note": "Rendered by PRAMAAN's built-in minimal PDF writer.",
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

    # Pick primary verdict info
    verdict_dict = primary_item["verdict"] if primary_item else {}
    verdict_str = str(verdict_dict.get("verdict") or "INSUFFICIENT_EVIDENCE")
    fused_score = verdict_dict.get("manipulation_score")
    avail_sig = verdict_dict.get("signals_available", 0)
    # The declared signal count comes from fusion. Defaulting it to 5 printed
    # "0 / 5 signals available" for a case with no verdict at all, which reads as
    # five signals having been attempted and none having produced a measurement.
    total_sig = verdict_dict.get("signals_total")
    total_sig_str = str(total_sig) if isinstance(total_sig, int) else "-"
    cov_pct = (
        f"{float(verdict_dict['signal_coverage']) * 100:.0f}%"
        if isinstance(verdict_dict.get("signal_coverage"), (int, float))
        else NOT_MEASURED
    )

    primary_evidence = primary_item["evidence"] if primary_item else None
    detector_payload = primary_item["detector"] if primary_item else {}
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
    elif "MANIPULATED" in verdict_str:
        ai_score = detector_payload.get("score")
        ai_str = f"The model returned {_score(ai_score)} manipulation likelihood." if ai_score is not None else ""
        exec_finding = (
            f"AI-generated/manipulated evidence detected. {ai_str} "
            f"The PRAMAAN fusion score is {_score(fused_score)}, above the manipulated threshold of {settings.verdict_manipulated_threshold:.2f}. "
            "This result is a decision aid for examiner review, not a certification."
        )
    elif "AUTHENTIC" in verdict_str:
        exec_finding = (
            "No evidence of AI generation or synthetic manipulation was detected across the assessed signals. "
            f"The PRAMAAN fusion score is {_score(fused_score)}, at or below the authentic threshold of {settings.verdict_authentic_threshold:.2f}. "
            "This result is a decision aid for examiner review, not a certification."
        )
    else:
        exec_finding = (
            "Ambiguous or insufficient forensic signal measurements were obtained. "
            f"The PRAMAAN fusion score is {_score(fused_score)}. "
            "This result is a decision aid for examiner review, not a certification."
        )

    # -----------------------------------------------------------------------
    # PAGE 1: OVERALL ASSESSMENT & EVIDENCE SNAPSHOT
    # -----------------------------------------------------------------------
    blocks.append({
        "type": "page_header",
        "case_number": case.case_number,
        "title": _or_none(case.title, "No case title recorded"),
        "page_num": 1,
    })
    blocks.append({
        "type": "notice",
        "text": DOCUMENT_STATUS,
    })
    blocks.append({
        "type": "summary_bar",
        "rows": [
            ["CASE ID", case.id],
            # "integration-check" is the actor name a verification script passes.
            # Printing it as the examiner of record attributed every unattributed
            # report to a script that never reviewed anything.
            ["EXAMINER", _or_none(examiner or case.examiner, "Not specified")],
            ["STATUS", case.status.upper()],
            ["EVIDENCE", f"{len(items)} items"],
        ],
    })
    blocks.append({
        "type": "verdict_card",
        "verdict": verdict_str,
        "score_line": f"Fused score {_score(fused_score)} | {avail_sig} / {total_sig_str} signals available | Coverage {cov_pct}",
        "leading": _leading_contributor(verdict_dict),
    })
    blocks.append({"type": "heading", "text": "EXECUTIVE FINDING"})
    blocks.append({"type": "paragraph", "text": exec_finding})

    blocks.append({"type": "heading", "text": "EVIDENCE SNAPSHOT"})
    snapshot_rows = []
    for it in items:
        v = it["verdict"]
        ev = it["evidence"]
        row_total = v.get("signals_total")
        row_cov = v.get("signal_coverage")
        snapshot_rows.append([
            ev.filename,
            str(v.get("verdict") or "UNASSESSED"),
            _score(v.get("manipulation_score")),
            # ASCII separator on purpose. ReportLab reverse-maps U+2022 onto
            # WinAnsi 0x7F, which is undefined in that encoding, so a bullet came
            # out of the renderer as a missing glyph ("4 / 5 <?> 85%"); the
            # built-in writer has no bullet at all and substitutes "?".
            "{} / {} | {}".format(
                v.get("signals_available", 0),
                row_total if isinstance(row_total, int) else "-",
                f"{float(row_cov) * 100:.0f}%" if isinstance(row_cov, (int, float)) else "-",
            ),
        ])
    if not snapshot_rows:
        snapshot_rows = [["No evidence items", "—", "—", "—"]]

    blocks.append({
        "type": "table",
        "columns": ["Evidence", "Verdict", "Score", "Coverage"],
        # The verdict column has to hold INSUFFICIENT_EVIDENCE, the longest label
        # fusion can produce; at the previous weights it was 6pt too narrow and
        # the renderer broke it mid-word ("INSUFFICIENT_EVIDENC" / "E").
        "widths": [2.8, 2.0, 1.0, 1.6],
        "rows": snapshot_rows,
    })

    blocks.append({"type": "heading", "text": "CASE IDENTITY"})
    blocks.append({
        "type": "kv_grid",
        "rows": [
            ["Case number", case.case_number, "Report version", REPORT_VERSION],
            # Full ISO-8601 with the Z designator. Truncating to 19 characters and
            # swapping the T for a space produced "2026-09-04 08:50:33", which
            # carries no time zone at all -- in an evidence document a reader
            # cannot tell whether that is UTC or the examiner's local clock.
            [
                "Case created",
                iso(case.created_at),
                "Report generated",
                collected["generated_at"],
            ],
            # The report's own id and the renderer that produced it. ``report_id``
            # was passed into this function and never printed, so the document
            # could not be tied back to the row recording its SHA-256 in the audit
            # chain, and a reader could not tell which of a case's reports they
            # were holding. Two reports of the same case differ only here and in
            # their timestamps.
            [
                "Report ID",
                report_id,
                "Renderer",
                renderer_status()["writer"],
            ],
        ],
    })

    # Every exhibit, identified by the two things that identify it: the evidence
    # id the audit chain refers to, and the SHA-256 of the bytes. Only the primary
    # exhibit's hash was printed (further down, under EVIDENCE INTEGRITY), so a
    # multi-exhibit case produced a document in which most exhibits appeared by
    # filename alone -- and a filename is not an identifier of file content.
    blocks.append({"type": "heading", "text": "EXHIBIT INDEX"})
    exhibit_rows = [
        [it["evidence"].filename, it["evidence"].id, it["evidence"].sha256]
        for it in items
    ]
    if not exhibit_rows:
        exhibit_rows = [["No evidence items", "—", "—"]]
    blocks.append({
        "type": "table",
        "columns": ["Filename", "Evidence ID", "SHA-256"],
        "widths": [1.9, 2.5, 2.6],
        "rows": exhibit_rows,
        "mono_columns": [1, 2],
    })

    blocks.append({"type": "pagebreak"})

    # -----------------------------------------------------------------------
    # PAGE 2: FORENSIC FINDINGS & SIGNAL MATRIX
    # -----------------------------------------------------------------------
    blocks.append({
        "type": "page_header",
        "case_number": case.case_number,
        "title": "Forensic findings",
        "page_num": 2,
    })

    blocks.append({"type": "heading", "text": "SIGNAL MATRIX"})

    # One row per signal fusion actually produced, in fusion's declared order.
    #
    # This table used to be five fixed rows with fixed prose: perceptual matching
    # always read "No retained near-duplicate candidate in indexed corpus",
    # metadata always read "No EXIF metadata available for analysis", C2PA always
    # read "No C2PA manifest found in file", and the role column was hardcoded
    # Primary/Excluded/Secondary regardless of whether the signal was included.
    # Every one of those was a forensic finding printed without being measured --
    # a case whose EXIF parsed cleanly still reported no metadata. The compression
    # score also fell back to 0.2097, a number from an unrelated sample.
    signals_list = verdict_dict.get("signals") or []
    primary_ids = set(verdict_dict.get("primary_signals") or [])
    matrix_rows = []
    for sig in signals_list:
        # Three mutually exclusive values, so "Primary" already implies inclusion:
        # a primary signal that fusion dropped reads "Excluded" like any other.
        if sig.get("included"):
            role = "Primary" if sig.get("signal_id") in primary_ids else "Included"
        else:
            role = "Excluded"
        matrix_rows.append([
            _or_none(sig.get("name") or sig.get("signal_id"), "Unnamed signal"),
            _or_none(sig.get("status"), NOT_RECORDED).upper(),
            _score(sig.get("score")),
            role,
            # The signal's own explanation, produced by the code that measured it.
            _or_none(sig.get("explanation"), "No finding recorded for this signal."),
        ])
    if not matrix_rows:
        matrix_rows = [["No signals recorded", "-", "-", "-", "Fusion produced no signal record for this exhibit."]]

    blocks.append({
        "type": "table",
        "columns": ["Signal", "Status", "Score", "Role", "Finding"],
        "widths": [2.2, 1.4, 0.9, 1.1, 2.6],
        "rows": matrix_rows,
    })

    blocks.append({"type": "heading", "text": "FUSION & INTERPRETATION"})
    # The weights that were actually applied, read from the verdict record. The
    # hardcoded "AI 0.35 - pHash 0.20 - Metadata 0.20 - C2PA 0.15 - Compression
    # 0.10" line was printed even when the deployment was configured with
    # different weights, i.e. it described a fusion that had not been run.
    declared_weights = verdict_dict.get("declared_weights") or {}
    if declared_weights:
        weights_str = ";  ".join(
            f"{fusion_service.SIGNAL_NAMES.get(sid, sid)} {float(w):.2f}"
            for sid, w in declared_weights.items()
        )
    else:
        weights_str = NOT_RECORDED
    # No fallback arithmetic. The old default -- "0.9969 x 0.7778 + 0.2097 x
    # 0.2222 = 0.8220" -- was a complete worked fusion for a case that had none,
    # and is the source of the 82% figure that appeared in reports with no score.
    arithmetic_str = _or_none(
        verdict_dict.get("arithmetic"), "No fused arithmetic (no signal was included)"
    )
    if isinstance(fused_score, (int, float)):
        # Worded rather than written "<=" / ">=". ReportLab renders kv values
        # through Paragraph, whose parser treats "<" as the start of markup, and
        # the comparison came out of the renderer broken across lines
        # ("authentic <" / "= 0.35"). Words also read better in a document a
        # non-technical reader has to follow.
        decision_str = (
            f"{verdict_str} — fused score {_score(fused_score)} against thresholds: "
            f"authentic at or below {settings.verdict_authentic_threshold:.2f}, "
            f"manipulated at or above {settings.verdict_manipulated_threshold:.2f}"
        )
    else:
        decision_str = (
            f"{verdict_str} — no fused score was produced, so no threshold was applied"
        )
    blocks.append({
        "type": "kv",
        "rows": [
            ["DECLARED WEIGHTS", weights_str],
            ["AVAILABLE COVERAGE", f"{avail_sig} / {total_sig_str} signals | {cov_pct} of declared weight"],
            ["FUSED SCORE", arithmetic_str],
            ["DECISION", decision_str],
            # Fusion's own words for why it reached this verdict, printed verbatim.
            # Everything else in this section is a figure; without the rationale
            # the only prose explaining the decision was the executive finding,
            # which report.py composes itself from the score and the thresholds --
            # a re-derivation that can drift from the reasoning fusion actually
            # applied. This row cannot drift: it is the string fusion returned.
            [
                "RATIONALE",
                _or_none(verdict_dict.get("rationale"), "No rationale was recorded"),
            ],
            # The band, not a number. Fusion publishes low/moderate/none and there
            # is no calibration behind any of them, so a percentage here would be
            # invented precision.
            [
                "CONFIDENCE BAND",
                _or_none(verdict_dict.get("confidence"), NOT_RECORDED),
            ],
        ]
    })

    blocks.append({"type": "heading", "text": "EVIDENCE INTEGRITY"})
    if primary_evidence:
        # Dimensions and perceptual hashes are printed only when they were
        # actually extracted. The previous defaults -- 512 x 512 and the pHash
        # /dHash pair b487e4860d796b65 / ccac8c3acc8c8c3a -- belonged to one
        # sample file and were printed for every exhibit that lacked them,
        # inside the section a reader uses to identify the exhibit.
        if primary_evidence.width and primary_evidence.height:
            dim_str = (
                f"{primary_evidence.width} x {primary_evidence.height} "
                f"{primary_evidence.image_format or primary_evidence.media_type.upper()}"
            )
        else:
            dim_str = _or_none(
                primary_evidence.image_format or primary_evidence.media_type.upper()
            ) + f" (dimensions {NOT_RECORDED.lower()})"
        phash_str = f"{_or_none(primary_evidence.phash)} / {_or_none(primary_evidence.dhash)}"
        blocks.append({
            "type": "kv",
            "rows": [
                ["SHA-256", primary_evidence.sha256, True],
                ["Dimensions", dim_str],
                ["pHash / dHash", phash_str, True],
                [
                    "Synthetic corpus",
                    # "True"/"False" is a Python repr, not a finding a reader can
                    # act on. The flag marks SYNTHETIC DEMO DATA, so it is spelled
                    # out either way -- a blank or a bare "False" would leave a
                    # reader unsure whether the question had even been asked.
                    "Yes -- SYNTHETIC DEMO DATA, not a real-world observation"
                    if primary_evidence.is_synthetic
                    else "No -- ingested as real evidence",
                ],
            ]
        })
    else:
        blocks.append({"type": "paragraph", "text": "No evidence details available."})

    blocks.append({"type": "heading", "text": "MODEL RECORD"})
    det_status = collected["detector_status"]
    # Identity comes from the detection record for *this* exhibit first, because
    # that is the model that produced the score being reported; the live status is
    # only a fallback for exhibits with no detection row. There is no fabricated
    # fallback: "SwinB-AI-Image-Detector" / "3.0.0" named a model in reports
    # produced by deployments with no detector installed at all.
    #
    # Load and inference are separate rows. The checkpoint load is a property of
    # the worker process, not of the exhibit, and folding it into one number
    # reported a multi-second cold start as this file's inference time.
    # The fallback is scoped to the exhibit's own modality. The top-level status
    # summarises the dispatcher, whose "model" is the image model, so a case whose
    # exhibit is a video -- for which no detector is installed and none ran --
    # fell through to a MODEL RECORD naming SwinB-AI-Image-Detector and its
    # weights digest.
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
            [
                "Model load",
                _ms(load_ms) if load_ms is not None else "Not loaded on this call",
            ],
            ["Weights SHA-256", _or_none(_identity("weights_hash"), NOT_RECORDED), True],
        ]
    })

    blocks.append({"type": "heading", "text": "REVIEW NOTE"})
    blocks.append({
        "type": "paragraph",
        "text": "Missing metadata, missing C2PA, and absence of a near-duplicate are not treated as evidence of authenticity or manipulation. The fused verdict reflects only the signals that produced measurements."
    })

    blocks.append({"type": "pagebreak"})

    # -----------------------------------------------------------------------
    # PAGE 3: PROVENANCE, AUDIT & EXAMINER REVIEW
    # -----------------------------------------------------------------------
    blocks.append({
        "type": "page_header",
        "case_number": case.case_number,
        "title": "Provenance, audit & examiner review",
        "page_num": 3,
    })

    blocks.append({"type": "heading", "text": "PROVENANCE & LINEAGE"})
    origin = collected["propagation"].get("origin") or {}
    origin_filename = origin.get("filename")
    blocks.append({
        "type": "lineage_flow",
        "current": primary_evidence.filename if primary_evidence else "No evidence item",
        "corpus": "No retained candidate" if not collected["matches"].get("total_candidates") else f"{collected['matches'].get('total_candidates')} candidates",
        # Falling back to the current file labelled this exhibit as the earliest
        # known instance even when propagation found nothing to compare it with.
        # No prior instance in the corpus is a distinct statement from "this file
        # is the earliest one", and only the first is supported by the record.
        "earliest": _or_none(origin_filename, "No earlier instance in corpus"),
    })
    lineage_note = (
        "Origin wording is deliberately scoped: earliest known instance in the "
        "indexed evidence corpus. It is not a claim of absolute real-world origin."
    )
    if origin.get("timestamp_is_tied"):
        # Otherwise the diagram above names one file as the earliest instance
        # while the record only establishes that several share the same recorded
        # time -- the ordering among them was a deterministic tie-break, not a
        # measurement.
        tied = origin.get("tied_earliest_evidence_ids") or []
        lineage_note += (
            f" {len(tied)} instances share the earliest recorded timestamp "
            f"({origin.get('timestamp')}), so which of them came first is NOT "
            "established by the record; the instance named above was selected "
            "deterministically."
        )
    blocks.append({"type": "paragraph", "text": lineage_note})

    blocks.append({"type": "heading", "text": "AUDIT INTEGRITY"})
    verification = collected["verification"]
    total_rows = verification.get("total_rows")
    case_rows = verification.get("case_rows")
    blocks.append({
        "type": "kv",
        "rows": [
            ["CHAIN STATUS", "VALID" if verification.get("valid") else "INVALID"],
            # Counts are read from the verification result or reported as absent.
            # The old defaults claimed 1,105 rows in the chain and 32 for the
            # case whenever verification returned neither -- a count of records
            # that were never written, in the section attesting to the integrity
            # of the record.
            ["ROWS IN CHAIN", f"{total_rows:,}" if isinstance(total_rows, int) else NOT_RECORDED],
            ["ROWS FOR CASE", str(case_rows) if isinstance(case_rows, int) else NOT_RECORDED],
            ["FIRST INVALID ROW", str(verification.get("first_invalid_seq") or "None")],
            # Printed in full. An abbreviated "a1b2c3d4e5f6...9f8e7d" cannot be
            # checked against anything: re-verifying the chain, or comparing this
            # document with the audit endpoint, needs all 64 characters. The kv
            # renderer sets it in Courier and wraps it rather than clipping.
            ["HEAD HASH", audit_head or NOT_RECORDED, True],
            # The chain's fixed starting value, so a reader can confirm the head
            # was reached from a known genesis rather than an arbitrary seed.
            ["GENESIS HASH", audit.GENESIS_HASH, True],
        ]
    })

    blocks.append({"type": "heading", "text": "CASE TIMELINE"})
    t_rows = []
    for ev in (verification.get("events") or [])[:6]:
        ts = str(ev.get("timestamp") or "")[11:19]
        t_rows.append([
            ts or NOT_RECORDED,
            str(ev.get("event") or "").upper() or NOT_RECORDED,
            _or_none(ev.get("actor")),
        ])
    if not t_rows:
        # No invented timeline. The previous fallback printed four events at
        # 12:53:09-12:54:25 attributed to "api" for any case whose chain returned
        # no events, which reads as a custody record of things that did not
        # happen at times that were never recorded.
        t_rows = [["-", "NO AUDIT EVENTS RECORDED FOR THIS CASE", "-"]]

    blocks.append({
        "type": "table",
        "columns": ["TIME", "EVENT", "ACTOR"],
        "widths": [1.5, 4.5, 1.8],
        "rows": t_rows,
    })

    blocks.append({"type": "heading", "text": "EXAMINER REVIEW"})
    blocks.append({
        "type": "kv",
        "rows": [
            ["Examiner", _or_none(examiner or case.examiner, "Not specified")],
            ["Organisation", "____________________________"],
            ["Signature", "____________________________"],
            ["Date", "____________________________"],
            # Unchecked. The decision belongs to the examiner who signs above;
            # shipping the document with "accepted" already ticked recorded a
            # review conclusion before any review took place.
            ["Review decision", "[ ] accepted   [ ] amended   [ ] rejected"],
        ]
    })
    blocks.append({
        "type": "paragraph",
        # Says who produced the document and who is accountable for it. Without
        # this the sign-off block reads as though an examiner had already
        # authored the findings above, when in fact every figure in this report
        # is machine-generated and unreviewed until the box above is ticked.
        "text": (
            "This report is machine-generated by PRAMAAN from the measurements recorded "
            "for this case. It carries no examiner opinion until the review decision "
            "above is completed and signed."
        ),
    })

    blocks.append({
        "type": "paragraph",
        "text": LIMITATIONS
    })

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
            v_color = "#dc2626" if "MANIPULATED" in block["verdict"] else "#16a34a" if "AUTHENTIC" in block["verdict"] else "#d97706"
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
    filename = f"PRAMAAN-{safe_case}-{report_id[:8]}.pdf"
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / filename
    path.write_bytes(data)

    verification = collected["verification"]
    row = Report(
        id=report_id,
        case_id=case.id,
        filename=filename,
        stored_path=filename,
        size_bytes=len(data),
        sha256=digest,
        generator=f"pramaan-report/{REPORT_VERSION}",
        renderer=renderer,
        pages=pages,
        examiner=examiner or case.examiner,
        audit_head_hash=audit_head,
        audit_valid=bool(verification.get("valid")),
        payload={
            "verdicts": [
                {
                    "evidence_id": item["evidence"].id,
                    "filename": item["evidence"].filename,
                    "sha256": item["evidence"].sha256,
                    "verdict": item["verdict"].get("verdict"),
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
            "filename": filename,
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
        "filename": filename,
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
