"""Authentication endpoints: sign in, sign out, and identify the operator.

Login mints a bearer token; the server stores only its hash (see
``app.services.identity``). The token is returned once, here, and the client
sends it back as ``Authorization: Bearer <token>`` on every subsequent request.
There are no cookies -- this fits the existing ``allow_credentials=False`` CORS
posture, where the browser omits credentials and an explicit header is the only
way to carry identity.

The operator resolved from that token is what the ingestion endpoint stamps as
the examiner on evidence. Identity therefore has exactly one source: a real
login, never a client-typed name.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.api.deps import CurrentUserDep, DbDep, SettingsDep
from app.schemas import LoginRequest, LoginResponse, UserOut
from app.services import audit, identity
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse, summary="Sign in and get a bearer token")
def login(credentials: LoginRequest, db: DbDep, settings: SettingsDep) -> LoginResponse:
    """Exchange username/password for a bearer token.

    A wrong username and a wrong password are reported identically (401, same
    message) so the response does not reveal which usernames exist. The plaintext
    password is verified against a PBKDF2 hash and never stored or logged.
    """
    user = identity.authenticate(
        db, username=credentials.username, password=credentials.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_row, raw_token = identity.create_session(
        db, user, ttl_hours=settings.auth_session_ttl_hours
    )
    identity.touch_last_login(db, user)
    audit.record(
        db,
        event=audit.EVENT_USER_LOGIN,
        case_id=None,
        actor=user.username,
        details={
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "session_id": session_row.id,
            "expires_at": iso(session_row.expires_at),
        },
    )
    logger.info("Operator %s signed in (session %s)", user.username, session_row.id)
    return LoginResponse(
        token=raw_token,
        token_type="bearer",
        expires_at=iso(session_row.expires_at) or "",
        user=UserOut(**identity.user_to_dict(user)),
    )


@router.get("/me", response_model=UserOut, summary="Who the current token belongs to")
def me(current: CurrentUserDep, settings: SettingsDep) -> UserOut:
    """Return the operator behind the request's bearer token.

    401 when there is no identity -- except under the local development bypass,
    where an unauthenticated request resolves to the development operator and the
    response says so via ``dev_bypass``. That flag is how the frontend can open
    straight into the console without inventing a session of its own: the answer
    to "who am I" comes from the server in both modes.
    """
    return UserOut(
        **identity.user_to_dict(current),
        dev_bypass=current.id == identity.DEV_BYPASS_USER_ID,
    )


@router.post("/logout", summary="Revoke the current bearer token")
def logout(
    current: CurrentUserDep,
    db: DbDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Revoke the presented session token. Idempotent for an already-gone token.

    Requires a valid token (``current`` enforces 401 otherwise), then revokes
    exactly that token so other sessions for the same operator stay valid.
    """
    token = identity.parse_bearer(authorization)
    revoked = identity.revoke_session(db, token)
    if revoked:
        audit.record(
            db,
            event=audit.EVENT_USER_LOGOUT,
            case_id=None,
            actor=current.username,
            details={"user_id": current.id, "username": current.username},
        )
        logger.info("Operator %s signed out", current.username)
    return {"status": "signed_out", "revoked": revoked}
