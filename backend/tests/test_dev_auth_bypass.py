"""The local development auth bypass: what it opens, and what it must never open.

The bypass exists so an agent or a browser can drive the console locally without
signing in. It is one branch at one dependency (``app.api.deps.get_current_user``)
and it is off by default. The risk it carries is obvious -- a server that stops
requiring authentication -- so the tests here are as much about its limits as its
behaviour:

MODE A, normal authentication (the default, and what production runs)
    * a protected request with no token is 401
    * a real login still works and identifies the real operator
    * ``/api/auth/me`` is 401 for an anonymous request

MODE B, bypass active in development
    * a protected request with no token succeeds, as the development operator
    * ``/api/auth/me`` reports that identity and marks it ``dev_bypass``
    * evidence ingested this way records the development identity, named as such,
      and never a seeded examiner's name
    * a *real* login still works and still wins over the bypass
    * an invalid or revoked token is still 401 -- the bypass covers the absence of
      a token, not a bad one

THE GUARD
    * flag on + ``environment=production`` MUST NOT bypass anything

Every test builds its own application with its own settings, so nothing here can
leak the bypass into the rest of the suite.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import jpeg_bytes

TITLE = "Development bypass contract"
DESCRIPTION = "Opened while the local development auth bypass was active."


def _app_with(**overrides):
    """A fresh application whose settings carry ``overrides``.

    ``create_app`` builds its own router graph and its own ``dependency_overrides``
    dict, so the suite-wide "pretend the seeded operator is signed in" override
    does not apply and the real dependency runs. ``get_settings`` is overridden
    rather than mutating the global settings object, which would bleed into every
    later test in the session.
    """
    from app.config import get_settings
    from app.main import create_app

    patched = get_settings().model_copy(update=overrides)
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: patched
    return application, patched


def _client(application):
    from fastapi.testclient import TestClient

    return TestClient(application)


def _upload(client, **fields):
    return client.post(
        "/api/cases/upload",
        data={"title": TITLE, "description": DESCRIPTION, **fields},
        files={"file": (f"{uuid.uuid4().hex[:8]}.jpg", jpeg_bytes(seed=901), "image/jpeg")},
    )


# --------------------------------------------------------------------------- #
# Mode A: normal authentication
# --------------------------------------------------------------------------- #
def test_without_the_flag_an_anonymous_request_is_still_refused() -> None:
    """The default. Nothing about the bypass changes the unauthenticated path."""
    application, settings = _app_with(dev_auth_bypass=False)
    assert settings.dev_auth_bypass_active is False
    with _client(application) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/cases").status_code == 401
        assert _upload(client).status_code == 401


def test_without_the_flag_a_real_login_still_identifies_the_real_operator(
    operator: dict,
) -> None:
    application, _ = _app_with(dev_auth_bypass=False)
    with _client(application) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": operator["username"], "password": operator["password"]},
        )
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["username"] == operator["username"]
        assert me.json()["dev_bypass"] is False, "a real login must never be flagged as a bypass"


# --------------------------------------------------------------------------- #
# Mode B: bypass active in development
# --------------------------------------------------------------------------- #
def test_the_bypass_opens_protected_endpoints_without_any_credential() -> None:
    application, settings = _app_with(dev_auth_bypass=True, environment="development")
    assert settings.dev_auth_bypass_active is True
    with _client(application) as client:
        assert client.get("/api/cases").status_code == 200
        assert client.get("/api/dashboard/summary").status_code == 200


def test_the_bypass_identity_is_named_as_a_bypass_and_is_not_a_real_operator(
    operator: dict,
) -> None:
    """``/api/auth/me`` answers, and the answer does not impersonate anyone."""
    application, settings = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        me = client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["dev_bypass"] is True, body
        assert body["username"] == settings.dev_auth_bypass_username
        assert body["username"] != operator["username"]
        assert body["display_name"] != operator["display_name"]
        # The identity says out loud what it is, in both fields the UI renders.
        assert "bypass" in body["display_name"].lower(), body["display_name"]
        assert "bypass" in body["role"].lower(), body["role"]


def test_the_bypass_identity_has_no_account_and_cannot_be_signed_into() -> None:
    """It is a transient object, not a credential-free row in the users table."""
    from sqlalchemy import select

    from app.models import User, session_scope

    application, settings = _app_with(dev_auth_bypass=True, environment="development")
    username = settings.dev_auth_bypass_username
    with session_scope() as session:
        assert (
            session.execute(select(User).where(User.username == username)).first() is None
        ), "the bypass created a real account, which would outlive the flag"

    with _client(application) as client:
        # No password could work, because there is no account and no hash.
        for password in ("", "anything", username):
            refused = client.post(
                "/api/auth/login", json={"username": username, "password": password}
            )
            assert refused.status_code in (401, 422), refused.text


def test_evidence_ingested_under_the_bypass_is_attributed_to_the_bypass(
    operator: dict,
) -> None:
    """The examiner field and the audit actor must both name the bypass.

    This is the honesty requirement: work done with authentication disabled has to
    be legible as such afterwards. Silently stamping a seeded examiner's name on it
    would put a real person's attestation on evidence they never handled.
    """
    application, settings = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        response = _upload(client)
        assert response.status_code == 201, response.text
        case_id = response.json()["case"]["case_id"]

        case = client.get(f"/api/cases/{case_id}").json()
        assert case["examiner"] == settings.dev_auth_bypass_display_name, case["examiner"]
        assert case["examiner"] != operator["display_name"]

        trail = client.get("/api/audit", params={"case_id": case_id, "limit": 100}).json()
        actors = {event["actor"] for event in trail["events"]}
        # Machine steps of the ingest -- indexing, in particular -- are recorded as
        # "api" and always were; they are not attributed to a person either way.
        # What matters is that every actor standing in for an *operator* is the
        # bypass, and that no seeded examiner's name appears anywhere on a case
        # they never touched.
        assert settings.dev_auth_bypass_username in actors, actors
        assert actors - {"api"} == {settings.dev_auth_bypass_username}, actors
        assert operator["username"] not in actors


def test_a_real_login_still_works_and_wins_while_the_bypass_is_active(
    operator: dict,
) -> None:
    """The bypass covers a missing token; it does not shadow a present one.

    An operator who does sign in must be recorded as themselves, or the bypass
    would quietly rewrite real attribution whenever it was left on.
    """
    application, _ = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": operator["username"], "password": operator["password"]},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        me = client.get("/api/auth/me", headers=headers)
        assert me.json()["username"] == operator["username"], me.text
        assert me.json()["dev_bypass"] is False, me.text

        response = client.post(
            "/api/cases/upload",
            data={"title": TITLE, "description": DESCRIPTION},
            files={"file": ("real-operator.jpg", jpeg_bytes(seed=902), "image/jpeg")},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        case_id = response.json()["case"]["case_id"]
        case = client.get(f"/api/cases/{case_id}", headers=headers).json()
        assert case["examiner"] == operator["display_name"], case["examiner"]


@pytest.mark.parametrize(
    ("label", "header", "expected"),
    [
        # A token is present and does not resolve. Refused, bypass or not.
        ("unknown token", "Bearer not-a-real-token", 401),
        ("token-shaped garbage", "Bearer " + "x" * 43, 401),
        # No bearer token is present at all, so these are anonymous requests and
        # the bypass answers them exactly as it answers a bare request.
        ("empty bearer", "Bearer ", 200),
        ("wrong scheme", "Basic dXNlcjpwYXNz", 200),
    ],
)
def test_a_present_but_bad_token_is_still_rejected_under_the_bypass(
    label: str, header: str, expected: int
) -> None:
    """A token that does not resolve is an error, not an invitation.

    The line the bypass draws is between *no credential* and *a bad credential*.
    Upgrading a stale or tampered token to the development identity would mean a
    token that should have stopped working silently kept working, so a token that
    fails to resolve stays a 401 even here. ``Bearer`` with nothing after it, and a
    non-bearer scheme, carry no token to resolve and are simply anonymous.
    """
    application, _ = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        response = client.get("/api/auth/me", headers={"Authorization": header})
        assert response.status_code == expected, f"{label}: {response.text}"


def test_a_revoked_token_is_still_rejected_under_the_bypass(operator: dict) -> None:
    application, _ = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": operator["username"], "password": operator["password"]},
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        after = client.get("/api/auth/me", headers=headers)
        assert after.status_code == 401, after.text


# --------------------------------------------------------------------------- #
# The guard: production must never bypass
# --------------------------------------------------------------------------- #
def test_the_flag_alone_does_not_bypass_authentication_in_production() -> None:
    """Both conditions are required, and this is the one that matters.

    A single mistaken environment variable in a deployment would otherwise leave
    the evidence store open. The flag is set here exactly as a careless deploy
    would set it, and authentication has to hold anyway.
    """
    application, settings = _app_with(dev_auth_bypass=True, environment="production")
    assert settings.dev_auth_bypass is True
    assert settings.is_production is True
    assert settings.dev_auth_bypass_active is False, "production honoured the bypass flag"
    with _client(application) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/cases").status_code == 401
        assert _upload(client).status_code == 401


def test_the_environment_alone_does_not_bypass_authentication() -> None:
    """Development is not enough either; the bypass must be asked for explicitly."""
    application, settings = _app_with(dev_auth_bypass=False, environment="development")
    assert settings.dev_auth_bypass_active is False
    with _client(application) as client:
        assert client.get("/api/auth/me").status_code == 401


def test_dev_bypass_full_workflow_case_intake_report_audit_delete() -> None:
    """Every core workflow step functions under the dev bypass without credentials."""
    application, settings = _app_with(dev_auth_bypass=True, environment="development")
    with _client(application) as client:
        # 1. Evidence intake / case creation
        upload_resp = _upload(client)
        assert upload_resp.status_code == 201, upload_resp.text
        case_id = upload_resp.json()["case"]["case_id"]
        evidence_id = upload_resp.json()["evidence"]["evidence_id"]

        # 2. Case detail and evidence retrieval
        case_resp = client.get(f"/api/cases/{case_id}")
        assert case_resp.status_code == 200
        assert case_resp.json()["examiner"] == settings.dev_auth_bypass_display_name

        ev_resp = client.get(f"/api/cases/{case_id}/evidence")
        assert ev_resp.status_code == 200
        assert ev_resp.json()["count"] >= 1

        file_resp = client.get(f"/api/evidence/{evidence_id}/file")
        assert file_resp.status_code == 200
        assert len(file_resp.content) > 0

        # 3. Analysis / System endpoints
        sys_resp = client.get("/api/system/status")
        assert sys_resp.status_code == 200

        # 4. Report generation. A report is a read over the examinations of
        # record, so the case is examined first; the workflow stays intact.
        assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200
        report_resp = client.post(f"/api/cases/{case_id}/report", json={})
        assert report_resp.status_code == 201, report_resp.text
        report_id = report_resp.json()["report_id"]

        reports_list = client.get(f"/api/cases/{case_id}/reports")
        assert reports_list.status_code == 200
        assert any(r["report_id"] == report_id for r in reports_list.json()["reports"])

        # 5. Audit trail and chain verification
        audit_resp = client.get("/api/audit", params={"case_id": case_id})
        assert audit_resp.status_code == 200

        verify_resp = client.post("/api/audit/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["valid"] is True

        # 6. Case deletion
        del_resp = client.delete(f"/api/cases/{case_id}")
        assert del_resp.status_code == 200, del_resp.text

        # Verify case is gone
        assert client.get(f"/api/cases/{case_id}").status_code == 404


def test_normal_mode_rejects_delete_and_report_when_unauthenticated() -> None:
    """Under Mode A, destructive and generative case operations require authentication."""
    application, _ = _app_with(dev_auth_bypass=False, environment="development")
    dummy_case_id = f"case-{uuid.uuid4().hex}"
    with _client(application) as client:
        assert client.delete(f"/api/cases/{dummy_case_id}").status_code == 401
        assert client.post(f"/api/cases/{dummy_case_id}/report", json={}).status_code == 401

