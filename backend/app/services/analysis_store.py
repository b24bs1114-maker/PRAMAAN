"""Persistence helpers for per-evidence analysis results.

Every analysis stage (metadata, detector, provenance, forensics, fusion,
propagation) writes an ``AnalysisResult`` row through here so results are
reproducible from the database without re-running the pipeline, and so the
report generator has a single place to read from.
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
    replace: bool = True,
) -> AnalysisResult:
    """Insert an analysis result, optionally replacing the previous one.

    ``replace=True`` keeps one current row per (evidence, kind) so repeated
    analysis does not accumulate stale rows. The audit log -- not this table --
    is the append-only history.
    """
    if replace:
        stale = session.execute(
            select(AnalysisResult).where(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == kind,
            )
        ).scalars().all()
        for row in stale:
            session.delete(row)
        if stale:
            session.flush()

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
    """Most recent stored result of a kind for one evidence item."""
    return session.execute(
        select(AnalysisResult)
        .where(
            AnalysisResult.evidence_id == evidence_id,
            AnalysisResult.kind == kind,
        )
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    ).scalars().first()


def case_results(session: Session, *, case_id: str, kind: str) -> list[AnalysisResult]:
    """All stored results of a kind for a case, oldest first."""
    return list(
        session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.case_id == case_id, AnalysisResult.kind == kind)
            .order_by(AnalysisResult.created_at)
        ).scalars()
    )


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
