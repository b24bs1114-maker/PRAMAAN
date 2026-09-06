"""Revoke live login sessions, without touching a single account.

Why this exists: bearer tokens outlive the browser tab that obtained them. A token
handed out days ago still identifies its operator until it expires, so there are
moments -- handing a machine to someone else, turning on the local development auth
bypass, or simply suspecting a token leaked -- when the right move is to invalidate
every token that has already been issued and make everyone sign in again.

What it does NOT do, and this is the point:

  * no user row is deleted, renamed or deactivated
  * no password hash is read, changed or printed
  * no account configuration is altered
  * no historical audit row is modified or removed

Session rows are *marked* revoked rather than deleted, so the record that a session
existed and when it ended survives. The operation is reversible in the only sense
that matters: every account still works exactly as before, and signing in mints a
fresh session immediately. What is destroyed is the usefulness of tokens issued
before now -- deliberately, since that is the whole request.

The revocation is itself recorded in the audit chain as ``AUTH_SESSIONS_REVOKED``,
appended like any other event. Nothing already in the chain is rewritten.

Usage, from the repository root with the venv active::

    .venv/bin/python scripts/revoke_auth_sessions.py                 # everyone
    .venv/bin/python scripts/revoke_auth_sessions.py --user examiner # one operator
    .venv/bin/python scripts/revoke_auth_sessions.py --dry-run       # just count

Exit code is 0 on success, 1 on failure. No password or token is ever printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revoke live login sessions. Accounts and passwords are untouched.",
    )
    parser.add_argument(
        "--user",
        metavar="USERNAME",
        default=None,
        help="Revoke only this operator's sessions. Default: every operator's.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many sessions are live and change nothing.",
    )
    parser.add_argument(
        "--actor",
        default="admin-script",
        help="Actor name recorded on the audit event. Default: admin-script.",
    )
    args = parser.parse_args(argv)

    from sqlalchemy import select

    from app.config import get_settings
    from app.models import AuthSession, User, init_db, session_scope
    from app.services import audit, identity

    settings = get_settings()
    settings.ensure_directories()
    init_db(settings)

    scope = args.user or "all operators"

    with session_scope() as session:
        # Count first, so a dry run and a real run report the same number and the
        # operator sees the blast radius before anything changes.
        live = select(AuthSession).where(AuthSession.revoked_at.is_(None))
        if args.user:
            live = live.join(User).where(User.username == args.user)
        count = len(list(session.execute(live).scalars()))

        if args.dry_run:
            print(f"{count} live session(s) for {scope}. Dry run: nothing changed.")
            return 0

        if count == 0:
            # Nothing to revoke is not a failure, and it is not worth an audit
            # entry either -- recording "revoked 0 sessions" would add noise to a
            # chain that exists to record things that happened.
            print(f"No live sessions for {scope}. Nothing to do.")
            return 0

        revoked = identity.revoke_all_sessions(session, username=args.user)
        audit.record(
            session,
            event=audit.EVENT_SESSIONS_REVOKED,
            actor=args.actor,
            details={
                "scope": args.user or "all",
                "sessions_revoked": revoked,
                "reason": "administrative revocation via scripts/revoke_auth_sessions.py",
            },
        )

    print(
        f"Revoked {revoked} session(s) for {scope}.\n"
        "Accounts, passwords and audit history are unchanged; signing in again "
        "issues a new session."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
