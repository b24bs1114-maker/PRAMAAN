"""The real authentication path: seeded accounts, tokens, and what they identify.

Every other module in this suite reaches the API through a dependency override
that hands the endpoint the seeded operator without a login (see
``conftest.py``). That override is a convenience for tests about hashing, fusion
or reporting; it would also hide a broken login. So every test here takes the
override off (``anonymous_client``) and goes through the front door:

* a seeded operator can sign in, and the password is verified, not compared
* the token identifies exactly that operator, and only while it is valid
* no token, a malformed header, a wrong token or a revoked one is a 401
* a wrong username and a wrong password are indistinguishable in the response
* nothing that reads or writes case material answers an anonymous caller, and
  the only two routes deliberately left open are login and the liveness probe
* the examiner recorded on a case is the display name of whoever was signed in,
  which is the whole reason intake no longer takes an examiner field
* seeding is idempotent per username: an account configured later is created,
  one that already exists is left exactly as it is
* revoking sessions administratively ends live tokens and nothing else: every
  account, password hash and audit entry survives it, and signing in again works

The development auth bypass has its own module, ``test_dev_auth_bypass.py``.

What is deliberately *not* asserted: the seeded credentials themselves. Those
come from settings, and the ``operator`` fixture reads them from the same place
the application does.
"""

from __future__ import annotations

import json
import uuid

from tests.helpers import jpeg_bytes

TITLE = "Authentication contract"
DESCRIPTION = "Opened by a genuinely signed-in operator."


def _login(client, operator: dict) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(session: dict) -> dict[str, str]:
    return {"Authorization": f"{session['token_type']} {session['token']}"}


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #
def test_a_seeded_operator_can_sign_in_and_is_told_who_they_are(
    anonymous_client, operator: dict
) -> None:
    session = _login(anonymous_client, operator)

    assert session["token"], "login returned no token"
    assert session["token_type"] == "bearer"
    assert session["expires_at"], "a session with no expiry never ends"
    assert session["user"]["username"] == operator["username"]
    assert session["user"]["display_name"] == operator["display_name"]
    assert session["user"]["role"] == operator["role"]
    # The response is the only place the token ever appears; the password must not
    # come back with it, in any form.
    assert operator["password"] not in json.dumps(session)


def test_the_password_is_actually_verified(anonymous_client, operator: dict) -> None:
    """A near-miss password is refused. Without this, a hash comparison that always
    succeeded would pass every other test in this file."""
    wrong = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"] + "x"},
    )
    assert wrong.status_code == 401, wrong.text


def test_a_wrong_username_and_a_wrong_password_look_identical(
    anonymous_client, operator: dict
) -> None:
    """Otherwise the 401s tell an attacker which usernames exist."""
    no_such_user = anonymous_client.post(
        "/api/auth/login",
        json={"username": f"nobody-{uuid.uuid4().hex[:8]}", "password": "irrelevant"},
    )
    bad_password = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": "not-the-password"},
    )

    assert no_such_user.status_code == bad_password.status_code == 401
    # Everything except the per-request id, which is deliberately unique and says
    # nothing about the credentials.
    assert no_such_user.json()["error"] == bad_password.json()["error"], (
        "the two failures are distinguishable, which enumerates usernames"
    )


def test_an_empty_password_is_rejected_by_the_schema(anonymous_client, operator: dict) -> None:
    empty = anonymous_client.post(
        "/api/auth/login", json={"username": operator["username"], "password": ""}
    )
    assert empty.status_code == 422, empty.text


# --------------------------------------------------------------------------- #
# What a token identifies
# --------------------------------------------------------------------------- #
def test_the_token_identifies_the_operator_it_was_minted_for(
    anonymous_client, operator: dict
) -> None:
    session = _login(anonymous_client, operator)

    me = anonymous_client.get("/api/auth/me", headers=_bearer(session))
    assert me.status_code == 200, me.text
    assert me.json() == session["user"], "/me disagrees with what login reported"


def test_no_token_is_a_401_that_asks_for_one(anonymous_client) -> None:
    me = anonymous_client.get("/api/auth/me")
    assert me.status_code == 401, me.text
    assert me.headers.get("WWW-Authenticate") == "Bearer"


def test_a_wrong_or_malformed_token_is_a_401(anonymous_client, operator: dict) -> None:
    session = _login(anonymous_client, operator)
    real = session["token"]

    for label, header in (
        ("no scheme", real),
        ("wrong scheme", f"Basic {real}"),
        ("empty token", "Bearer "),
        ("a token that was never issued", f"Bearer {uuid.uuid4().hex}"),
        # One character changed. The server stores only a hash of the token, so a
        # near-miss cannot be recognised as "close".
        ("a token altered by one character", f"Bearer {real[:-1]}{'a' if real[-1] != 'a' else 'b'}"),
    ):
        response = anonymous_client.get("/api/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, f"{label} was accepted: {response.text}"


# --------------------------------------------------------------------------- #
# Signing out
# --------------------------------------------------------------------------- #
def test_signing_out_revokes_that_token_and_only_that_token(
    anonymous_client, operator: dict
) -> None:
    """Two sessions for one operator are independent: revoking one must not sign
    the operator out of the other, which is the difference between a session and
    a password."""
    first = _login(anonymous_client, operator)
    second = _login(anonymous_client, operator)
    assert first["token"] != second["token"], "two logins returned the same token"

    out = anonymous_client.post("/api/auth/logout", headers=_bearer(first))
    assert out.status_code == 200, out.text
    assert out.json() == {"status": "signed_out", "revoked": True}

    assert anonymous_client.get("/api/auth/me", headers=_bearer(first)).status_code == 401
    assert anonymous_client.get("/api/auth/me", headers=_bearer(second)).status_code == 200


def test_signing_out_without_a_token_is_a_401_not_a_no_op(anonymous_client) -> None:
    assert anonymous_client.post("/api/auth/logout").status_code == 401


def test_a_revoked_token_cannot_sign_itself_out_again(anonymous_client, operator: dict) -> None:
    session = _login(anonymous_client, operator)
    assert anonymous_client.post("/api/auth/logout", headers=_bearer(session)).status_code == 200
    again = anonymous_client.post("/api/auth/logout", headers=_bearer(session))
    assert again.status_code == 401, again.text


# --------------------------------------------------------------------------- #
# What identity is actually for
# --------------------------------------------------------------------------- #
def test_intake_refuses_an_unauthenticated_upload(anonymous_client) -> None:
    """The gate is before the file is read: no token, no case, nothing stored."""
    response = anonymous_client.post(
        "/api/cases/upload",
        files={"file": ("unauthenticated.jpg", jpeg_bytes(seed=501), "image/jpeg")},
        data={"title": TITLE, "description": DESCRIPTION},
    )
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_the_examiner_on_a_case_is_the_signed_in_operator(
    anonymous_client, operator: dict
) -> None:
    """The point of the whole login layer: the name on the evidence is not typed
    by the client, it is the identity the server authenticated."""
    session = _login(anonymous_client, operator)
    response = anonymous_client.post(
        "/api/cases/upload",
        files={"file": ("signed-in.jpg", jpeg_bytes(seed=502), "image/jpeg")},
        data={"title": TITLE, "description": DESCRIPTION},
        headers=_bearer(session),
    )
    assert response.status_code == 201, response.text
    case = response.json()["case"]
    assert case["examiner"] == operator["display_name"], case["examiner"]

    # And it is what was persisted, not just what the response said. Read back
    # with the same token: case reads are authenticated too, so the client that
    # skipped the header here would only be proving that the gate works.
    reread = anonymous_client.get(f"/api/cases/{case['case_id']}", headers=_bearer(session))
    assert reread.status_code == 200, reread.text
    assert reread.json()["examiner"] == operator["display_name"]


def test_a_client_supplied_examiner_field_cannot_override_the_operator(
    anonymous_client, operator: dict
) -> None:
    """Intake takes no examiner field. Sending one anyway must not be honoured --
    otherwise the authenticated name is only a default, and the audit trail
    records whoever asked nicely."""
    session = _login(anonymous_client, operator)
    response = anonymous_client.post(
        "/api/cases/upload",
        files={"file": ("spoofed.jpg", jpeg_bytes(seed=503), "image/jpeg")},
        data={
            "title": TITLE,
            "description": DESCRIPTION,
            "examiner": "Someone Else Entirely",
        },
        headers=_bearer(session),
    )
    assert response.status_code == 201, response.text
    assert response.json()["case"]["examiner"] == operator["display_name"]


def test_signing_in_is_recorded_in_the_audit_trail(anonymous_client, operator: dict) -> None:
    """Who was in the system, and when, is part of the chain of custody.

    Filtered by event and actor, and bounded to the last few seconds: the trail is
    returned oldest-first and the suite writes thousands of rows, so an unfiltered
    page would be the beginning of the chain rather than this login.
    """
    from datetime import timedelta

    from app.utils.timeutil import iso, utcnow

    since = iso(utcnow() - timedelta(seconds=5))
    session = _login(anonymous_client, operator)
    trail = anonymous_client.get(
        "/api/audit",
        params={
            "event": "USER_LOGIN",
            "actor": operator["username"],
            "since": since,
            "limit": 50,
        },
        headers=_bearer(session),
    )
    assert trail.status_code == 200, trail.text
    events = trail.json()["events"]
    assert events, "signing in left no audit row"
    assert all(event["event"] == "USER_LOGIN" for event in events), events
    assert any(
        (event.get("details") or {}).get("display_name") == operator["display_name"]
        for event in events
    ), f"the login row does not record who signed in: {events}"


# --------------------------------------------------------------------------- #
# The authorization boundary
# --------------------------------------------------------------------------- #
#: One probe per operation that reads or writes case material, across every
#: mounted router. The ids in the templates are invented, which is the point: a
#: handler that ran would answer 404, so insisting on 401 asserts that the gate
#: closes *before* the handler -- otherwise the status code alone would confirm or
#: deny that a given case id exists.
_ANONYMOUS_PROBES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/cases"),
    ("GET", "/api/cases/library/all"),
    ("GET", "/api/cases/{case_id}"),
    ("PATCH", "/api/cases/{case_id}"),
    ("DELETE", "/api/cases/{case_id}"),
    ("GET", "/api/cases/{case_id}/evidence"),
    ("POST", "/api/cases/{case_id}/analyse"),
    ("POST", "/api/cases/{case_id}/matches"),
    ("GET", "/api/cases/{case_id}/matches"),
    ("GET", "/api/cases/{case_id}/metadata"),
    ("GET", "/api/cases/{case_id}/provenance"),
    ("GET", "/api/cases/{case_id}/propagation"),
    ("POST", "/api/cases/{case_id}/detect"),
    ("POST", "/api/cases/{case_id}/verdict"),
    ("GET", "/api/cases/{case_id}/verdict"),
    ("GET", "/api/cases/{case_id}/audit"),
    ("POST", "/api/cases/{case_id}/audit/verify"),
    ("POST", "/api/cases/{case_id}/web-discovery"),
    ("GET", "/api/cases/{case_id}/web-discovery"),
    ("POST", "/api/cases/{case_id}/report"),
    ("GET", "/api/cases/{case_id}/reports"),
    ("GET", "/api/evidence/{evidence_id}"),
    ("GET", "/api/evidence/{evidence_id}/file"),
    ("GET", "/api/evidence/{evidence_id}/analysis"),
    ("GET", "/api/dashboard/summary"),
    ("GET", "/api/index/status"),
    ("POST", "/api/index/rebuild"),
    ("POST", "/api/index/ingest"),
    ("GET", "/api/detector/status"),
    ("GET", "/api/detector/manifest"),
    ("POST", "/api/detect"),
    ("GET", "/api/reports"),
    ("GET", "/api/alerts"),
    ("GET", "/api/audit"),
    ("POST", "/api/audit/verify"),
    ("GET", "/api/system/signals"),
    ("GET", "/api/system/status"),
)


def test_nothing_that_touches_case_material_answers_an_anonymous_caller(
    anonymous_client,
) -> None:
    """Every one of these was reachable with no credentials at all, including the
    delete. None of them may be now."""
    case_id = f"case-{uuid.uuid4().hex}"
    evidence_id = f"evidence-{uuid.uuid4().hex}"

    for method, template in _ANONYMOUS_PROBES:
        path = template.format(case_id=case_id, evidence_id=evidence_id)
        response = anonymous_client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} answered {response.status_code} to a caller with no "
            f"token: {response.text[:200]}"
        )
        assert response.headers.get("WWW-Authenticate") == "Bearer", (
            f"{method} {path} refused the request without saying how to authenticate"
        )


def test_only_signing_in_and_the_liveness_probe_are_reachable_without_a_token() -> None:
    """The structural guard, so this does not have to be remembered.

    Authorization is applied where the routers are mounted, which means a router
    added without a decision about who may call it is added *unprotected*. This
    walks the dependency tree of every route on a freshly built application and
    pins the complete set of routes that do not require an operator. A new public
    route therefore fails here, at the one assertion that enumerates them, rather
    than in production.

    The three that are public are public on purpose: login is how a token is
    obtained in the first place, ``/health`` is what a load balancer probes, and
    ``/`` is the service banner -- name, version, environment, where the docs and
    the health probe live. None of them touch case material.
    """
    from fastapi.routing import APIRoute

    from app.api.deps import get_current_user
    from app.main import create_app

    application = create_app()

    def requires_an_operator(route: APIRoute) -> bool:
        pending = list(route.dependant.dependencies)
        while pending:
            dependency = pending.pop()
            if dependency.call is get_current_user:
                return True
            pending.extend(dependency.dependencies)
        return False

    public = {
        (method, route.path)
        for route in application.routes
        if isinstance(route, APIRoute) and not requires_an_operator(route)
        for method in route.methods
    }

    assert public == {
        ("POST", "/api/auth/login"),
        ("GET", "/health"),
        ("GET", "/"),
    }, f"unexpected routes are reachable without a token: {sorted(public)}"


def test_an_anonymous_delete_cannot_destroy_a_case(
    anonymous_client, operator: dict
) -> None:
    """The 401 is not the assertion that matters here -- the case still being
    there afterwards is.

    ``DELETE /api/cases/{case_id}`` is a hard delete: the case row, its evidence,
    analyses, matches, timeline and reports, the stored files and the index
    vectors. A refusal that had already removed some of that would still be a
    refusal, so the case and its evidence are read back.
    """
    session = _login(anonymous_client, operator)
    created = anonymous_client.post(
        "/api/cases/upload",
        files={"file": ("survives-anonymous-delete.jpg", jpeg_bytes(seed=504), "image/jpeg")},
        data={"title": TITLE, "description": DESCRIPTION},
        headers=_bearer(session),
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["case"]["case_id"]

    refused = anonymous_client.delete(f"/api/cases/{case_id}")
    assert refused.status_code == 401, refused.text
    assert refused.headers.get("WWW-Authenticate") == "Bearer"

    survivor = anonymous_client.get(f"/api/cases/{case_id}", headers=_bearer(session))
    assert survivor.status_code == 200, "the case did not survive an anonymous delete"
    listing = anonymous_client.get(
        f"/api/cases/{case_id}/evidence", headers=_bearer(session)
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["count"] == 1, listing.text


def test_the_stored_bytes_need_a_token_and_are_served_with_one(
    anonymous_client, operator: dict
) -> None:
    """Both halves of the contract the console's evidence previews depend on.

    A browser-initiated ``<img src>`` cannot carry an Authorization header, so the
    UI fetches these bytes through the authenticated transport and wraps them in an
    object URL. That is only worth doing if the route really refuses an anonymous
    reader -- and only usable if it really returns the bytes to a signed-in one.
    """
    session = _login(anonymous_client, operator)
    created = anonymous_client.post(
        "/api/cases/upload",
        files={"file": ("authenticated-bytes.jpg", jpeg_bytes(seed=505), "image/jpeg")},
        data={"title": TITLE, "description": DESCRIPTION},
        headers=_bearer(session),
    )
    assert created.status_code == 201, created.text
    evidence_id = created.json()["evidence"]["evidence_id"]

    anonymous = anonymous_client.get(f"/api/evidence/{evidence_id}/file")
    assert anonymous.status_code == 401, anonymous.text
    assert jpeg_bytes(seed=505) not in anonymous.content, "the bytes were served anyway"

    signed_in = anonymous_client.get(
        f"/api/evidence/{evidence_id}/file", headers=_bearer(session)
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.content == jpeg_bytes(seed=505), (
        "the authenticated read did not return the ingested bytes"
    )


# --------------------------------------------------------------------------- #
# Seeding operator accounts from configuration
# --------------------------------------------------------------------------- #
def _seed_with(**overrides):
    """Run ``seed_operators`` against the test database with patched settings.

    Returns the usernames it created. The settings copy is throwaway, so nothing
    here changes what the rest of the suite sees.
    """
    from app.config import get_settings
    from app.models import session_scope
    from app.services import identity

    patched = get_settings().model_copy(update=overrides)
    with session_scope() as session:
        return [user.username for user in identity.seed_operators(session, patched)]


def _drop_user(username: str) -> None:
    from sqlalchemy import select

    from app.models import User, session_scope

    with session_scope() as session:
        row = session.execute(select(User).where(User.username == username)).scalar_one()
        session.delete(row)


def test_seeding_is_idempotent_per_username_and_never_rewrites_an_account(
    anonymous_client, operator: dict
) -> None:
    """Re-seeding must not duplicate an account or change its stored password.

    ``conftest`` has already seeded, so this second run has nothing left to do.
    The login afterwards is the part that matters: if seeding had overwritten the
    hash of an existing account, the credentials that worked before would stop
    working.
    """
    assert _seed_with() == [], "re-seeding created accounts that already existed"
    _login(anonymous_client, operator)


def test_a_newly_configured_operator_is_created_and_can_sign_in(
    anonymous_client,
) -> None:
    """Adding an operator to the configuration is enough to get an account.

    This is the behaviour that a "seed only into an empty table" gate would
    silently swallow: the table stopped being empty on the first ever startup, so
    a third account configured later would never appear.
    """
    from pydantic import SecretStr

    username = f"late-arrival-{uuid.uuid4().hex[:8]}@example.test"
    created = _seed_with(
        seed_operator_tertiary_username=username,
        seed_operator_tertiary_display_name="Late Arrival",
        seed_operator_tertiary_role="Forensic Examiner",
        seed_operator_tertiary_password=SecretStr("s3curely-configured"),
    )
    try:
        assert created == [username], created
        session = _login(
            anonymous_client, {"username": username, "password": "s3curely-configured"}
        )
        me = anonymous_client.get("/api/auth/me", headers=_bearer(session))
        assert me.status_code == 200, me.text
        assert me.json()["display_name"] == "Late Arrival", me.text
        assert me.json()["role"] == "Forensic Examiner", me.text
    finally:
        _drop_user(username)


def test_an_operator_configured_without_a_password_is_not_created(
    anonymous_client,
) -> None:
    """A blank password would be an account nobody could ever sign into.

    Login rejects an empty password at the schema, so creating the row would only
    put an unusable account in the users table. Skipping it keeps the two facts
    consistent: every seeded account can be signed into.
    """
    from pydantic import SecretStr

    username = f"no-password-{uuid.uuid4().hex[:8]}@example.test"
    created = _seed_with(
        seed_operator_tertiary_username=username,
        seed_operator_tertiary_display_name="No Password",
        seed_operator_tertiary_password=SecretStr(""),
    )
    assert created == [], created
    refused = anonymous_client.post(
        "/api/auth/login", json={"username": username, "password": ""}
    )
    assert refused.status_code in (401, 422), refused.text


# --------------------------------------------------------------------------- #
# Administrative revocation
# --------------------------------------------------------------------------- #
# Revoking every live session is a blunt instrument -- it signs everyone out -- so
# what these assert is mostly what it must NOT reach: accounts, password hashes and
# the audit history all survive it, and the only casualty is the usefulness of
# tokens that had already been issued.
def test_revoking_sessions_kills_live_tokens_without_touching_the_account(
    anonymous_client, operator: dict
) -> None:
    from app.models import session_scope
    from app.services import identity

    login = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    assert anonymous_client.get("/api/auth/me", headers=headers).status_code == 200

    with session_scope() as session:
        revoked = identity.revoke_all_sessions(session)
    assert revoked >= 1

    # The token is dead...
    assert anonymous_client.get("/api/auth/me", headers=headers).status_code == 401
    # ...but the account is not. This is the reversibility claim: the same
    # credentials work immediately, so nothing was destroyed except the tokens.
    again = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    assert again.status_code == 200, again.text
    fresh = {"Authorization": f"Bearer {again.json()['token']}"}
    me = anonymous_client.get("/api/auth/me", headers=fresh)
    assert me.status_code == 200, me.text
    assert me.json()["username"] == operator["username"]


def test_revoking_sessions_preserves_every_account_and_the_audit_history(
    anonymous_client, operator: dict
) -> None:
    """No user row, password hash or audit entry may change. Only sessions end."""
    from sqlalchemy import func, select

    from app.models import AuditLog, User, session_scope
    from app.services import identity

    anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )

    with session_scope() as session:
        before_users = {
            u.username: (u.display_name, u.role, u.password_hash, u.is_active)
            for u in session.execute(select(User)).scalars()
        }
        before_audit = session.execute(select(func.count()).select_from(AuditLog)).scalar()

    with session_scope() as session:
        identity.revoke_all_sessions(session)

    with session_scope() as session:
        after_users = {
            u.username: (u.display_name, u.role, u.password_hash, u.is_active)
            for u in session.execute(select(User)).scalars()
        }
        after_audit = session.execute(select(func.count()).select_from(AuditLog)).scalar()

    assert after_users == before_users, "revocation altered an account"
    assert after_audit == before_audit, "revocation wrote to or rewrote the audit chain"
    # And the chain still verifies, so nothing was rewritten in place either.
    with session_scope() as session:
        from app.services import audit

        assert audit.verify_chain(session)["valid"] is True


def test_revoking_one_operator_leaves_the_others_signed_in(
    anonymous_client, operator: dict, settings
) -> None:
    """The ``username`` argument is a scope, not a suggestion."""
    from app.models import session_scope
    from app.services import identity

    other_username = settings.seed_operator_secondary_username
    other_password = settings.seed_operator_secondary_password.get_secret_value()

    mine = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    theirs = anonymous_client.post(
        "/api/auth/login", json={"username": other_username, "password": other_password}
    )
    assert mine.status_code == 200 and theirs.status_code == 200, theirs.text
    my_headers = {"Authorization": f"Bearer {mine.json()['token']}"}
    their_headers = {"Authorization": f"Bearer {theirs.json()['token']}"}

    with session_scope() as session:
        revoked = identity.revoke_all_sessions(session, username=operator["username"])
    assert revoked >= 1

    assert anonymous_client.get("/api/auth/me", headers=my_headers).status_code == 401
    still_valid = anonymous_client.get("/api/auth/me", headers=their_headers)
    assert still_valid.status_code == 200, still_valid.text
    assert still_valid.json()["username"] == other_username


def test_revoking_twice_is_a_no_op_the_second_time() -> None:
    """Idempotent: only *live* sessions are touched, so a repeat revokes nothing."""
    from app.models import session_scope
    from app.services import identity

    with session_scope() as session:
        identity.revoke_all_sessions(session)
    with session_scope() as session:
        assert identity.revoke_all_sessions(session) == 0
