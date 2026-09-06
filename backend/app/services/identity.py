"""Operator identity: accounts, passwords and login sessions.

Built on the Python standard library only -- no new dependency, no change to the
deployment. Passwords are stored as PBKDF2-HMAC-SHA256 hashes in a single encoded
column (``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``); the plaintext is
never persisted. Login mints an opaque ``secrets.token_urlsafe`` bearer token and
stores only its SHA-256 hash, so a database read never yields a usable
credential -- the token itself is shown to the client once and never again.

This module is the single source of truth for *who* an operator is. The API
layer stamps the signed-in operator's display name as the examiner on ingested
evidence; nothing about examiner identity is ever typed in at intake.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import timedelta

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuthSession, User
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.identity")

_PBKDF2_ALGORITHM = "pbkdf2_sha256"

# A well-formed hash of no useful password, verified against when the requested
# username does not exist. Verifying anyway keeps login's response time roughly
# independent of whether the username is real, denying an enumeration oracle.
_DUMMY_HASH = (
    "pbkdf2_sha256$200000$"
    "0000000000000000000000000000000000000000000000000000000000000000$"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


def hash_password(password: str, *, iterations: int) -> str:
    """Encode a password as ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_PBKDF2_ALGORITHM}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against an encoded PBKDF2 hash."""
    try:
        algorithm, iteration_text, salt_hex, hash_hex = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if algorithm != _PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(iteration_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def _token_hash(token: str) -> str:
    """The only representation of a session token that touches the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_by_username(session: Session, username: str) -> User | None:
    if not username:
        return None
    return session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def create_user(
    session: Session,
    *,
    username: str,
    display_name: str,
    role: str,
    password: str,
    iterations: int,
    is_active: bool = True,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        display_name=display_name,
        role=role,
        password_hash=hash_password(password, iterations=iterations),
        is_active=is_active,
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, *, username: str, password: str) -> User | None:
    """Return the operator iff the credentials are valid and the account active.

    A missing or inactive account still runs a password verification against a
    dummy hash so the response time does not reveal whether the username exists.
    """
    user = get_user_by_username(session, username)
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def touch_last_login(session: Session, user: User) -> None:
    user.last_login_at = utcnow()
    session.flush()


def create_session(
    session: Session, user: User, *, ttl_hours: float
) -> tuple[AuthSession, str]:
    """Open a login session, returning ``(row, raw_token)``.

    The raw token is the caller's only chance to see it -- only its hash is
    stored. The row carries an explicit expiry so a leaked token stops working
    even if it is never revoked.
    """
    raw_token = secrets.token_urlsafe(32)
    now = utcnow()
    auth_session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
        revoked_at=None,
    )
    session.add(auth_session)
    session.flush()
    return auth_session, raw_token


def resolve_session(session: Session, token: str | None) -> User | None:
    """Return the operator behind a bearer token, or ``None`` if it is not usable.

    A token is usable only when it matches a stored session that is neither
    revoked nor expired and whose operator account is still active.
    """
    if not token:
        return None
    row = session.execute(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at <= utcnow():
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_session(session: Session, token: str | None) -> bool:
    """Revoke the session for ``token``. Idempotent: already-gone returns False."""
    if not token:
        return False
    row = session.execute(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    session.flush()
    return True


def revoke_all_sessions(session: Session, *, username: str | None = None) -> int:
    """Revoke every live login session, or just one operator's. Returns the count.

    Marks ``revoked_at`` on the session rows and touches nothing else: no user is
    deleted, no password hash is changed, no account is deactivated, and no audit
    row is altered. The accounts remain exactly as they were and signing in again
    mints a fresh session, which is what makes this reversible -- the only thing
    destroyed is the ability of *already-issued* tokens to identify anyone.

    Rows are marked rather than deleted so the trail still shows that a session
    existed and when it ended.
    """
    query = select(AuthSession).where(AuthSession.revoked_at.is_(None))
    if username is not None:
        query = query.join(User).where(User.username == username)
    rows = list(session.execute(query).scalars())
    now = utcnow()
    for row in rows:
        row.revoked_at = now
    if rows:
        session.flush()
    return len(rows)


def parse_bearer(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def user_to_dict(user: User) -> dict[str, object]:
    """Public projection of an operator -- never includes the password hash."""
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "last_login_at": iso(user.last_login_at),
    }


#: Stable id for the development-bypass identity, so anything it touches can be
#: traced back to the bypass rather than to a person. A fixed UUID rather than a
#: fresh one per request: the audit trail should show one development actor, not a
#: crowd of unrelated ones.
DEV_BYPASS_USER_ID = "00000000-0000-0000-0000-00000000dev0"


def dev_bypass_user(settings: Settings) -> User:
    """The synthetic operator used when the development auth bypass is active.

    Built in memory and never added to a session, so it creates no row, occupies
    no username, and cannot be signed into: ``authenticate`` only ever looks at
    persisted accounts, and this one has no password hash to verify against. That
    is deliberate -- a bypass identity that existed as a real account would be a
    permanent credential-free door into the users table, still there long after
    the flag was turned off.

    ``is_active`` is True because callers read it, but the object is transient: it
    exists for the lifetime of one request. Its display name and role name the
    bypass out loud, because this string is what lands in the examiner field and
    the audit trail, and evidence ingested this way must not read as a real
    examiner's attested work.
    """
    return User(
        id=DEV_BYPASS_USER_ID,
        username=settings.dev_auth_bypass_username,
        display_name=settings.dev_auth_bypass_display_name,
        role=settings.dev_auth_bypass_role,
        password_hash="",  # unusable: verify_password rejects a non-PBKDF2 string
        is_active=True,
    )


def _secret_value(secret: SecretStr | str) -> str:
    return secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)


def seed_operators(session: Session, settings: Settings) -> list[User]:
    """Create every configured operator account that does not exist yet.

    Idempotent per username rather than per table: an account is created only if
    no user already holds that username, so this is safe to call on every startup
    and an operator added to the configuration actually appears. Gating on an
    empty users table instead -- the obvious reading of "seed" -- would silently
    ignore a newly configured examiner forever, because the table stops being
    empty after the very first start.

    Existing accounts are never touched. Changing a password in the environment
    does not rewrite the stored hash of an account that already exists; that is a
    deliberate password *reset*, not a seed, and it is not what this does.

    A blank username is skipped, which is how the optional third slot stays
    inert. A configured username with a blank password is also skipped: login
    rejects empty passwords, so seeding one would create an account nobody could
    ever sign into.
    """
    specs = [
        (
            settings.seed_operator_username,
            settings.seed_operator_display_name,
            settings.seed_operator_role,
            settings.seed_operator_password,
        ),
        (
            settings.seed_operator_secondary_username,
            settings.seed_operator_secondary_display_name,
            settings.seed_operator_secondary_role,
            settings.seed_operator_secondary_password,
        ),
        (
            settings.seed_operator_tertiary_username,
            settings.seed_operator_tertiary_display_name,
            settings.seed_operator_tertiary_role,
            settings.seed_operator_tertiary_password,
        ),
    ]

    created: list[User] = []
    for username, display_name, role, secret in specs:
        username = (username or "").strip()
        if not username:
            continue
        if get_user_by_username(session, username) is not None:
            continue
        password = _secret_value(secret)
        if not password:
            logger.warning(
                "Operator %r is configured without a password and was NOT "
                "created; login rejects empty passwords, so the account would "
                "be unusable. Set its PRAMAAN_SEED_OPERATOR_*_PASSWORD.",
                username,
            )
            continue
        created.append(
            create_user(
                session,
                username=username,
                display_name=(display_name or "").strip() or username,
                role=role,
                password=password,
                iterations=settings.auth_pbkdf2_iterations,
            )
        )
        if password.startswith("change-me"):
            logger.warning(
                "Seeded operator %r with a PLACEHOLDER password. Set "
                "PRAMAAN_SEED_OPERATOR_PASSWORD (and the secondary) before any "
                "real deployment -- these defaults are public.",
                username,
            )
    if created:
        logger.warning(
            "Seeded %d operator account(s): %s",
            len(created),
            ", ".join(u.username for u in created),
        )
    session.flush()
    return created


__all__ = [
    "DEV_BYPASS_USER_ID",
    "authenticate",
    "create_session",
    "create_user",
    "dev_bypass_user",
    "get_user_by_username",
    "hash_password",
    "parse_bearer",
    "resolve_session",
    "revoke_all_sessions",
    "revoke_session",
    "seed_operators",
    "touch_last_login",
    "user_to_dict",
    "verify_password",
]



