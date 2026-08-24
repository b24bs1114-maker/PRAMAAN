"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Case, Evidence, get_session_factory
from app.services import ingestion


def get_db() -> Iterator[Session]:
    """Per-request database session; commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def require_case(case_id: str, db: DbDep) -> Case:
    case = ingestion.get_case(db, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case {case_id} not found."
        )
    return case


CaseDep = Annotated[Case, Depends(require_case)]


def require_evidence(evidence_id: str, db: DbDep) -> Evidence:
    """Look up one evidence row by id, whatever case (or corpus) it belongs to.

    Evidence is addressed globally because a corpus item has no case, and a
    near-duplicate candidate found for one case routinely lives in another.
    """
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence {evidence_id} not found.",
        )
    return evidence


EvidenceDep = Annotated[Evidence, Depends(require_evidence)]
