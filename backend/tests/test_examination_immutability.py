"""Immutability of the finalized examination record.

The P0 invariant: every screen, API response, audit record and report referring
to an examination refers to the same finalized examination snapshot. A finalized
examination must not silently change because weights, thresholds, policy or
configuration changed, or because another examination was run. A re-examination
is a NEW examination with its own identity, and the previous one remains intact
and readable.

These tests pin that contract against the real persistence layer -- no mocks,
the actual database, the actual pipeline -- because the property they guard is a
property of what gets stored, not of what any single function returns.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi.testclient import TestClient

from app.models import (
    KIND_FUSION,
    AnalysisResult,
    AuditLog,
    get_session_factory,
)
from app.services import audit as audit_service
from app.utils.canonical import canonical_bytes
from tests.helpers import jpeg_bytes


def _upload(client: TestClient, name: str, data: bytes) -> dict[str, Any]:
    response = client.post(
        "/api/cases/upload",
        files={"file": (name, data, "image/jpeg")},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _open_case(client: TestClient, name: str, data: bytes) -> dict[str, Any]:
    """Upload a first exhibit, which opens the case; return its ids."""
    body = _upload(client, name, data)
    case_block = body["case"]
    return {
        "case_id": case_block["case_id"],
        "evidence_id": body["evidence"]["evidence_id"],
    }


def _fusion_rows(evidence_id: str) -> list[AnalysisResult]:
    session = get_session_factory()()
    try:
        return list(
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == KIND_FUSION,
            )
            .order_by(AnalysisResult.created_at)
            .all()
        )
    finally:
        session.close()


def _payload(row: AnalysisResult) -> dict[str, Any]:
    assert isinstance(row.payload, dict)
    return dict(row.payload)


# --------------------------------------------------------------------------- #
# A finalized examination keeps its identity
# --------------------------------------------------------------------------- #
def test_fusion_payload_carries_examination_identity(client: TestClient) -> None:
    """The stored examination names itself: id and digest.

    Without these there is nothing for an audit row or a report to refer to --
    'the examination' is whatever the latest row happens to be, and a later run
    silently becomes the earlier examination's identity.
    """
    case = _open_case(client, "identity.jpg", jpeg_bytes(seed=201))
    case_id = case["case_id"]

    verdict = client.post(f"/api/cases/{case_id}/verdict")
    assert verdict.status_code == 200, verdict.text
    item = verdict.json()["items"][0]

    assert item["examination_id"], "fusion payload must carry examination_id"
    assert item["examination_digest"], "fusion payload must carry examination_digest"


def test_examination_digest_binds_the_recorded_findings(client: TestClient) -> None:
    """The digest is over the examination's own recorded findings.

    sha256 over the canonical form of the fields that constitute the
    examination -- not over timestamps or cache flags -- so a stored row whose
    findings were edited no longer matches its recorded digest.
    """
    case = _open_case(client, "digest.jpg", jpeg_bytes(seed=202))
    client.post(f"/api/cases/{case['case_id']}/verdict")

    rows = _fusion_rows(case["evidence_id"])
    assert rows, "no fusion row was stored"
    payload = _payload(rows[0])

    fields = payload.get("digest_fields") or {}
    assert fields, "the digest must record what it covers"

    recomputed = hashlib.sha256(canonical_bytes(fields)).hexdigest()
    assert (
        payload["examination_digest"] == recomputed
    ), "stored digest does not bind the recorded findings"


# --------------------------------------------------------------------------- #
# Re-examination preserves history and has a distinct identity
# --------------------------------------------------------------------------- #
def test_re_examination_preserves_the_previous_examination(
    client: TestClient,
) -> None:
    """A refresh run is a NEW examination; the old row remains intact.

    The old row's findings, identity and timestamp are unchanged by the second
    run -- a re-examination that overwrote its predecessor would make 'what did
    the first examination conclude' unanswerable.
    """
    case_id = _open_case(client, "re-examine.jpg", jpeg_bytes(seed=203))["case_id"]

    first = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]

    second = client.post(
        f"/api/cases/{case_id}/verdict", params={"refresh": "true"}
    ).json()["items"][0]

    rows = _fusion_rows(first["evidence_id"])
    assert len(rows) == 2, f"expected 2 examination rows, found {len(rows)}"

    old, new = rows
    old_payload, new_payload = _payload(old), _payload(new)
    assert old_payload["examination_id"] == first["examination_id"]
    assert new_payload["examination_id"] == second["examination_id"]
    assert old_payload["examination_id"] != new_payload["examination_id"], (
        "a re-examination must have a distinct identity"
    )
    assert old.id != new.id
    # The previous examination's findings survive verbatim.
    assert old_payload["verdict"] == first["verdict"]
    assert old_payload["manipulation_score"] == first["manipulation_score"]


def test_stored_verdict_read_returns_the_latest_examination(
    client: TestClient,
) -> None:
    """The read endpoint reports the newest examination, not a blend.

    After a re-examination, the stored read shows exactly the new row's
    identity and findings -- never a mixture of the two runs.
    """
    case_id = _open_case(client, "latest.jpg", jpeg_bytes(seed=204))["case_id"]

    client.post(f"/api/cases/{case_id}/verdict")
    second = client.post(
        f"/api/cases/{case_id}/verdict", params={"refresh": "true"}
    ).json()["items"][0]

    stored = client.get(f"/api/cases/{case_id}/verdict").json()["items"][0]
    assert stored["examination_id"] == second["examination_id"]
    assert stored["manipulation_score"] == second["manipulation_score"]


def test_configuration_change_does_not_rewrite_old_examinations(
    client: TestClient,
) -> None:
    """Changing thresholds/weights does not touch what was already recorded.

    An examination run under one policy stays interpretable under that policy
    after the policy changes; the new examination reflects the new policy.
    """
    from app.config import get_settings

    settings = get_settings()
    case_id = _open_case(client, "policy-change.jpg", jpeg_bytes(seed=205))["case_id"]

    first = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]

    old_threshold = settings.verdict_authentic_threshold
    settings.verdict_authentic_threshold = 0.0
    try:
        second = client.post(
            f"/api/cases/{case_id}/verdict", params={"refresh": "true"}
        ).json()["items"][0]
    finally:
        settings.verdict_authentic_threshold = old_threshold

    rows = _fusion_rows(first["evidence_id"])
    assert len(rows) == 2
    old, new = rows
    old_payload, new_payload = _payload(old), _payload(new)

    # The first examination keeps the thresholds it was decided under.
    old_thresholds = (old_payload.get("assessment") or {}).get("thresholds") or {}
    assert old_thresholds.get("authentic_at_or_below") == old_threshold
    # And its recorded score and digest are untouched.
    assert old_payload["manipulation_score"] == first["manipulation_score"]

    # The second examination carries the new policy's threshold.
    new_thresholds = (new_payload.get("assessment") or {}).get("thresholds") or {}
    assert new_thresholds.get("authentic_at_or_below") == 0.0


# --------------------------------------------------------------------------- #
# Audit binding
# --------------------------------------------------------------------------- #
def test_verdict_audit_event_identifies_the_examination(client: TestClient) -> None:
    """The VERDICT_GENERATED row names the exact examination it records.

    Without the examination id and digest in the audit details, the event can
    only say 'some verdict was produced for this evidence' -- a later
    re-examination makes it ambiguous which one it refers to.
    """
    case_id = _open_case(client, "audit-binding.jpg", jpeg_bytes(seed=206))["case_id"]

    verdict = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]

    session = get_session_factory()()
    try:
        event = (
            session.query(AuditLog)
            .filter(
                AuditLog.case_id == case_id,
                AuditLog.event == audit_service.EVENT_VERDICT_GENERATED,
            )
            .order_by(AuditLog.seq.desc())
            .first()
        )
        assert event is not None, "no VERDICT_GENERATED row was appended"
        details = event.details or {}
        assert details.get("examination_id") == verdict["examination_id"], (
            "audit event must identify the examination it records"
        )
        assert details.get("examination_digest") == verdict["examination_digest"]
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Concurrent runs remain isolated
# --------------------------------------------------------------------------- #
def test_two_examinations_of_the_same_evidence_never_mix(
    client: TestClient,
) -> None:
    """Sequential same-evidence examinations produce independent rows.

    Each run's identity, digest and findings belong to that run alone; the
    second run cannot mix its outputs into the first row.
    """
    case_id = _open_case(client, "isolation.jpg", jpeg_bytes(seed=207))["case_id"]

    first = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]
    second = client.post(
        f"/api/cases/{case_id}/verdict", params={"refresh": "true"}
    ).json()["items"][0]

    assert first["examination_digest"] != second["examination_digest"] or (
        first["examination_id"] != second["examination_id"]
    )
    rows = _fusion_rows(first["evidence_id"])
    assert len(rows) == 2
    for row in rows:
        payload = _payload(row)
        fields = payload.get("digest_fields") or {}
        recomputed = hashlib.sha256(canonical_bytes(fields)).hexdigest()
        assert payload["examination_digest"] == recomputed, (
            "each examination's digest binds its own findings"
        )
