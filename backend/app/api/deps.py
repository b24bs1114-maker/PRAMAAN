"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Case, Evidence, User, get_session_factory
from app.services import identity, ingestion


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


def get_current_user(
    db: DbDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the operator behind the request's bearer token, or reject with 401.

    The examiner recorded on ingested evidence comes from this operator -- never
    from a client-supplied field -- so an unauthenticated ingest has no identity
    to stamp and is refused here. ``WWW-Authenticate: Bearer`` tells a client the
    scheme to use.

    This is the *only* authentication boundary in the application, which is why
    the development bypass lives here and nowhere else. Every protected endpoint
    depends on this one function, so one branch covers all of them and no screen
    or router carries its own copy of the rule.

    A presented token is always resolved for real, bypass or not: a valid token
    identifies its own operator, and an *invalid* one is refused even under the
    bypass rather than being quietly upgraded to the development identity. The
    bypass only answers the case of no token at all.
    """
    token = identity.parse_bearer(authorization)
    if token is None and settings.dev_auth_bypass_active:
        # Local development convenience. The decision is entirely server-side --
        # nothing the browser sends can switch this on, because the only inputs
        # are the process configuration and the absence of a header.
        return identity.dev_bypass_user(settings)

    user = identity.resolve_session(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Sign in and send a bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


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
