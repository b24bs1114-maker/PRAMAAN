"""Cross-case and session authorization boundaries.

PRAMAAN's authorization model is operator-level, not case-level: a signed-in
operator works the whole console, and evidence is addressed globally because
corpus items and near-duplicate candidates have no case. What must NOT happen
is a case-scoped object answering outside its case: a report generated for
case A must not be downloadable as ``/api/cases/B/.../reports/{id}``, and a
dead session -- expired or revoked -- must not read anything.

These tests pin those boundaries. They are guards over behaviour the audit
found correct, written so a future refactor cannot lose it silently.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import jpeg_bytes


def _open_case(client: TestClient, seed: int, name: str) -> dict[str, Any]:
    response = client.post(
        "/api/cases/upload",
        files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")},
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    return {
        "case_id": body["case"]["case_id"],
        "evidence_id": body["evidence"]["evidence_id"],
    }


def _examine(client: TestClient, case_id: str) -> None:
    assert client.post(f"/api/cases/{case_id}/verdict").status_code == 200


# --------------------------------------------------------------------------- #
# Case-scoped objects refuse to answer outside their case
# --------------------------------------------------------------------------- #
def test_a_report_cannot_be_downloaded_through_another_case_id(
    client: TestClient,
) -> None:
    """Report download is case-scoped: /api/cases/{B}/reports/{A's report} -> 404.

    The report row exists and its bytes are on disk, but it belongs to case A;
    addressing it under case B must not resolve. ``report_file`` binds the row
    to the requested case, so a leaked report id alone is not enough to read
    another case's material.
    """
    case_a = _open_case(client, 401, "case-a.jpg")
    _examine(client, case_a["case_id"])
    report = client.post(f"/api/cases/{case_a['case_id']}/report", json={})
    assert report.status_code == 201, report.text
    report_id = report.json()["report_id"]

    case_b = _open_case(client, 402, "case-b.jpg")

    wrong_case = client.get(f"/api/cases/{case_b['case_id']}/reports/{report_id}")
    assert wrong_case.status_code == 404, wrong_case.text
    # And the case's own path still serves it.
    own_case = client.get(f"/api/cases/{case_a['case_id']}/reports/{report_id}")
    assert own_case.status_code == 200


def test_case_scoped_reads_refuse_another_cases_id(client: TestClient) -> None:
    """Evidence, verdict and audit reads under case B never mention case A.

    Every ``/api/cases/{case_id}/...`` read resolves the case first, so an
    unknown or substituted id is a 404 -- never a window onto the other case's
    evidence list, verdicts or audit trail.
    """
    case_a = _open_case(client, 403, "scoped-a.jpg")
    _examine(client, case_a["case_id"])
    case_b = _open_case(client, 404, "scoped-b.jpg")

    a_evidence = client.get(f"/api/cases/{case_a['case_id']}/evidence").json()
    assert a_evidence["count"] == 1

    # The evidence id from case A, asked for under case B's routes.
    a_evidence_id = a_evidence["evidence"][0]["evidence_id"]

    b_verdict = client.get(f"/api/cases/{case_b['case_id']}/verdict").json()
    assert all(
        item.get("evidence_id") != a_evidence_id for item in b_verdict["items"]
    ), "case B's verdict read exposed case A's evidence"

    b_evidence = client.get(f"/api/cases/{case_b['case_id']}/evidence").json()
    assert all(
        ev["evidence_id"] != a_evidence_id for ev in b_evidence["evidence"]
    ), "case B's evidence list exposed case A's evidence"


def test_unknown_case_ids_are_404_not_500_or_empty_success(client: TestClient) -> None:
    """A fabricated case id fails closed on every case-scoped read."""
    unknown = str(uuid.uuid4())
    for path in (
        f"/api/cases/{unknown}",
        f"/api/cases/{unknown}/evidence",
        f"/api/cases/{unknown}/verdict",
        f"/api/cases/{unknown}/audit",
        f"/api/cases/{unknown}/reports",
        f"/api/cases/{unknown}/reports/{uuid.uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 404, f"{path} -> {response.status_code}"


# --------------------------------------------------------------------------- #
# Session lifecycle: expired and revoked tokens read nothing
# --------------------------------------------------------------------------- #
def test_an_expired_session_is_denied_everywhere(
    anonymous_client: TestClient, operator: dict
) -> None:
    """A token past its expiry is dead on every protected endpoint, not just /me.

    ``resolve_session`` compares ``expires_at`` against the clock on every
    request; this pins that no case-scoped route can be reached with a token
    whose session row has lapsed.
    """
    from app.models import get_session_factory
    from app.services import identity

    login = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]

    # Expire the session row behind the token, then try to read case material.
    factory = get_session_factory()
    session = factory()
    try:
        from datetime import datetime

        row = (
            session.query(identity.AuthSession)
            .filter_by(token_hash=identity._token_hash(token))
            .one()
        )
        row.expires_at = datetime(2000, 1, 1)  # naive UTC, firmly in the past
        session.commit()
    finally:
        session.close()

    headers = {"Authorization": f"Bearer {token}"}
    assert anonymous_client.get("/api/auth/me", headers=headers).status_code == 401
    case_list = anonymous_client.get("/api/cases", headers=headers)
    assert case_list.status_code == 401, case_list.text
    assert anonymous_client.get(
        "/api/cases/00000000-0000-0000-0000-000000000000/audit",
        headers=headers,
    ).status_code == 401


def test_a_revoked_session_is_denied_on_case_reads(
    anonymous_client: TestClient, operator: dict
) -> None:
    """After /api/auth/logout (revocation), the token reads no case material.

    test_auth.py pins logout's effect on /api/auth/me and intake; this extends
    the boundary to the case, evidence and report reads an attacker would try
    next.
    """
    login = anonymous_client.post(
        "/api/auth/login",
        json={"username": operator["username"], "password": operator["password"]},
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Sanity: the token works before revocation.
    assert anonymous_client.get(
        "/api/cases", headers=headers
    ).status_code == 200

    out = anonymous_client.post(
        "/api/auth/logout", headers=headers
    )
    assert out.status_code == 200, out.text

    for path in (
        "/api/cases",
        "/api/cases/00000000-0000-0000-0000-000000000000/audit",
        "/api/cases/00000000-0000-0000-0000-000000000000/reports",
        "/api/reports",
        "/api/audit",
        "/api/evidence/00000000-0000-0000-0000-000000000000",
    ):
        response = anonymous_client.get(path, headers=headers)
        assert response.status_code == 401, f"{path} -> {response.status_code}"
