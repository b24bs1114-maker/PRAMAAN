"""Persistence helpers for per-evidence analysis results.

Every analysis stage (metadata, detector, provenance, forensics, fusion,
propagation) writes an ``AnalysisResult`` row through here so results are
reproducible from the database without re-running the pipeline, and so the
report generator has a single place to read from.

Rows are append-only: every run of a stage inserts a *new* row and never
deletes or overwrites a previous one. A re-examination is a new examination --
the previous one stays on the record with its own identity, so "what did the
first examination conclude" remains answerable after any number of later runs.
``latest_result`` resolves the newest row of a kind; the history is the table,
not the audit log.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Evidence
from app.utils.timeutil import iso, utcnow


def store_result(
    session: Session,
    *,
    case_id: str,
    evidence_id: str | None,
    kind: str,
    payload: dict[str, Any],
    status: str = "OK",
    score: float | None = None,
    verdict: str | None = None,
    model: str | None = None,
    model_version: str | None = None,
    replace: bool = False,
) -> AnalysisResult:
    """Insert an analysis result. Existing rows are never deleted or modified.

    ``replace`` is accepted and ignored. It used to delete the previous row for
    the (evidence, kind) pair, which destroyed the history of every
    re-examination: a refresh run made the previous examination's findings
    unrecoverable while the audit trail still referred to them. The parameter
    stays so call sites need not change; its old behaviour is gone because it
    contradicted the examination-immutability contract.
    """
    result = AnalysisResult(
        id=str(uuid.uuid4()),
        case_id=case_id,
        evidence_id=evidence_id,
        kind=kind,
        status=status,
        score=score,
        verdict=verdict,
        model=model,
        model_version=model_version,
        payload=payload,
        created_at=utcnow(),
    )
    session.add(result)
    session.flush()
    return result


def latest_result(
    session: Session, *, evidence_id: str, kind: str
) -> AnalysisResult | None:
    """Most recent stored result of a kind for one evidence item.

    Newest row wins. Two rows written in the same second (a fast re-run) are
    ordered by insertion id as a tiebreaker so the winner is deterministic;
    the earlier row is history, never garbage.
    """
    return session.execute(
        select(AnalysisResult)
        .where(
            AnalysisResult.evidence_id == evidence_id,
            AnalysisResult.kind == kind,
        )
        .order_by(AnalysisResult.created_at.desc(), AnalysisResult.id.desc())
        .limit(1)
    ).scalars().first()


def result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    """Serialise a stored analysis row for API responses."""
    return {
        "analysis_id": result.id,
        "evidence_id": result.evidence_id,
        "kind": result.kind,
        "status": result.status,
        "score": result.score,
        "verdict": result.verdict,
        "model": result.model,
        "model_version": result.model_version,
        "created_at": iso(result.created_at),
        "payload": result.payload,
    }


def case_evidence(session: Session, case_id: str) -> list[Evidence]:
    """Evidence belonging to a case, in ingestion order."""
    return list(
        session.execute(
            select(Evidence)
            .where(Evidence.case_id == case_id)
            .order_by(Evidence.ingested_at)
        ).scalars()
    )
