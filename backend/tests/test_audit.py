"""Hash-chained audit log tests (TASK 11).

Covers the two properties that make the chain worth having: every significant
action is recorded with the documented fields, and any edit, deletion or
reordering of a historical row is detected at the row where it happened.

The chain is global and the test database is shared across the suite, so every
tampering test restores the row it touched and re-verifies before finishing.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.models import AuditLog, get_session_factory
from app.services import audit
from app.utils.canonical import canonical_bytes
from tests.helpers import jpeg_with_exif_bytes

REQUIRED_EVENTS = {
    "CASE_CREATED",
    "EVIDENCE_INGESTED",
    "HASH_CALCULATED",
    "METADATA_EXTRACTED",
    "MATCH_SEARCHED",
    "VERDICT_GENERATED",
}


@contextmanager
def _session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def _tampered(seq_of: str):
    """Yield a committed row for editing, then restore it byte for byte.

    ``seq_of`` names the event whose most recent row should be corrupted.
    """
    with _session() as session:
        row = (
            session.query(AuditLog)
            .filter(AuditLog.event == seq_of)
            .order_by(AuditLog.seq.desc())
            .first()
        )
        assert row is not None, f"no {seq_of} row to tamper with"
        original = {
            "event": row.event,
            "timestamp": row.timestamp,
            "actor": row.actor,
            "details": dict(row.details or {}),
            "previous_hash": row.previous_hash,
            "row_hash": row.row_hash,
            "case_id": row.case_id,
        }
        seq = row.seq
        try:
            yield session, row, seq
        finally:
            session.rollback()
            with _session() as restore:
                target = restore.get(AuditLog, seq)
                for field, value in original.items():
                    setattr(target, field, value)
                restore.commit()
            # The chain must be intact again, or later tests are meaningless.
            with _session() as check:
                assert audit.verify_chain(check)["valid"] is True


def _analysed_case(client: TestClient, name: str) -> str:
    """Upload one image and run the stages that should leave audit entries."""
    case_id = client.post(
        "/api/cases/upload",
        files={"file": (name, jpeg_with_exif_bytes(seed=901), "image/jpeg")},
    ).json()["case"]["case_id"]
    client.post(f"/api/cases/{case_id}/verdict?refresh=true")
    return case_id


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def test_workflow_records_the_documented_events(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-events.jpg")

    trail = client.get(f"/api/cases/{case_id}/audit")
    assert trail.status_code == 200
    body = trail.json()
    events = [entry["event"] for entry in body["events"]]

    assert REQUIRED_EVENTS <= set(events)
    assert events[0] == "CASE_CREATED"  # the case exists before anything else
    assert events.index("EVIDENCE_INGESTED") < events.index("METADATA_EXTRACTED")
    assert events.index("METADATA_EXTRACTED") < events.index("VERDICT_GENERATED")
    assert all(event in audit.KNOWN_EVENTS for event in events)


def test_every_entry_carries_the_documented_fields(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-fields.jpg")
    body = client.get(f"/api/cases/{case_id}/audit").json()

    for entry in body["events"]:
        assert set(entry) >= {
            "audit_id",
            "case_id",
            "event",
            "timestamp",
            "actor",
            "details",
            "previous_hash",
            "row_hash",
        }
        uuid.UUID(entry["audit_id"])  # raises if not a UUID
        assert entry["case_id"] == case_id
        assert entry["timestamp"].endswith("Z")
        assert entry["actor"]
        assert isinstance(entry["details"], dict)
        assert len(entry["previous_hash"]) == 64
        assert len(entry["row_hash"]) == 64

    assert body["algorithm"] == audit.ALGORITHM
    assert "tamper EVIDENCE, not tamper PROOF" in body["interpretation"]


def test_reading_the_trail_does_not_modify_it(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-readonly.jpg")

    first = client.get(f"/api/cases/{case_id}/audit").json()
    second = client.get(f"/api/cases/{case_id}/audit").json()

    assert first["total_rows"] == second["total_rows"]
    assert first["head_hash"] == second["head_hash"]


def test_trail_truncation_is_reported(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-limit.jpg")

    body = client.get(f"/api/cases/{case_id}/audit?limit=2").json()
    assert body["count"] == 2
    assert body["truncated"] is True
    assert body["total_rows"] > 2


# --------------------------------------------------------------------------- #
# Chain construction
# --------------------------------------------------------------------------- #
def test_chain_links_every_row_to_its_predecessor(client: TestClient) -> None:
    _analysed_case(client, "audit-links.jpg")

    with _session() as session:
        rows = session.query(AuditLog).order_by(AuditLog.seq.asc()).all()
        assert rows[-1].row_hash == audit.head_hash(session)

    assert rows[0].previous_hash == audit.GENESIS_HASH
    for previous, row in zip(rows, rows[1:]):
        assert row.previous_hash == previous.row_hash
    seqs = [row.seq for row in rows]
    assert seqs == sorted(seqs)


def test_row_hash_is_reproducible_by_hand(client: TestClient) -> None:
    """An independent SHA-256 of the canonical payload must match the stored hash."""
    case_id = _analysed_case(client, "audit-recompute.jpg")

    with _session() as session:
        row = (
            session.query(AuditLog)
            .filter(AuditLog.case_id == case_id)
            .order_by(AuditLog.seq.asc())
            .first()
        )
        payload = {
            "audit_id": row.audit_id,
            "case_id": row.case_id,
            "event": row.event,
            "timestamp": row.timestamp,
            "actor": row.actor,
            "details": row.details,
        }
        digest = hashlib.sha256()
        digest.update(row.previous_hash.encode("ascii"))
        digest.update(canonical_bytes(payload))
        assert digest.hexdigest() == row.row_hash


def test_canonical_serialisation_is_key_order_independent() -> None:
    a = {"case_id": "c", "event": "X", "details": {"b": 1, "a": [1, 2]}}
    b = {"details": {"a": [1, 2], "b": 1}, "event": "X", "case_id": "c"}

    assert canonical_bytes(a) == canonical_bytes(b)
    assert audit.compute_row_hash("0" * 64, a) == audit.compute_row_hash("0" * 64, b)
    # List order, unlike key order, is meaningful and must change the hash.
    c = {"case_id": "c", "event": "X", "details": {"b": 1, "a": [2, 1]}}
    assert audit.compute_row_hash("0" * 64, a) != audit.compute_row_hash("0" * 64, c)


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def test_verify_reports_an_intact_chain(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-valid.jpg")

    response = client.post(f"/api/cases/{case_id}/audit/verify?record=false")
    assert response.status_code == 200
    body = response.json()

    assert body["valid"] is True
    assert body["issues"] == []
    assert body["first_invalid_seq"] is None
    assert body["scope"] == "global_chain"
    assert body["case_rows"] >= len(REQUIRED_EVENTS)
    assert body["case_rows"] <= body["total_rows"]
    assert body["genesis_hash"] == audit.GENESIS_HASH
    assert len(body["head_hash"]) == 64


def test_verification_is_itself_recorded(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-recorded.jpg")

    before = client.get(f"/api/cases/{case_id}/audit").json()
    verified = client.post(f"/api/cases/{case_id}/audit/verify").json()
    after = client.get(f"/api/cases/{case_id}/audit").json()

    assert verified["valid"] is True
    assert after["total_rows"] == before["total_rows"] + 1
    entry = after["events"][-1]
    assert entry["event"] == "AUDIT_CHAIN_VERIFIED"
    assert entry["details"]["valid"] is True
    # The row records the head it checked, which is the head from before itself.
    assert entry["details"]["verified_head_hash"] == before["head_hash"]
    assert entry["previous_hash"] == before["head_hash"]
    # Appending a verification must not invalidate the chain.
    assert (
        client.post(f"/api/cases/{case_id}/audit/verify?record=false").json()["valid"]
        is True
    )


def test_editing_a_recorded_detail_is_detected(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-tamper-details.jpg")

    with _tampered("VERDICT_GENERATED") as (session, row, seq):
        row.details = {**(row.details or {}), "verdict": "AUTHENTIC"}
        session.commit()

        body = client.post(f"/api/cases/{case_id}/audit/verify?record=false").json()
        assert body["valid"] is False
        assert body["first_invalid_seq"] == seq
        problems = {issue["problem"] for issue in body["issues"]}
        assert "content_modified" in problems
        offending = next(i for i in body["issues"] if i["seq"] == seq)
        assert offending["found"] == row.row_hash
        assert offending["expected"] != row.row_hash


def test_editing_a_timestamp_is_detected(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-tamper-time.jpg")

    with _tampered("EVIDENCE_INGESTED") as (session, row, seq):
        row.timestamp = "1999-01-01T00:00:00Z"
        session.commit()

        body = client.post(f"/api/cases/{case_id}/audit/verify?record=false").json()
        assert body["valid"] is False
        assert body["first_invalid_seq"] == seq
        assert any(i["problem"] == "content_modified" for i in body["issues"])


def test_rewriting_a_row_hash_breaks_the_following_link(client: TestClient) -> None:
    """Recomputing one row's hash without the rest cascades to its successor."""
    case_id = _analysed_case(client, "audit-tamper-hash.jpg")

    with _tampered("METADATA_EXTRACTED") as (session, row, seq):
        forged_details = {**(row.details or {}), "status": "TAMPERED"}
        row.details = forged_details
        row.row_hash = audit.compute_row_hash(
            row.previous_hash,
            audit.row_payload(
                audit_id=row.audit_id,
                case_id=row.case_id,
                event=row.event,
                timestamp=row.timestamp,
                actor=row.actor,
                details=forged_details,
            ),
        )
        session.commit()

        body = client.post(f"/api/cases/{case_id}/audit/verify?record=false").json()
        assert body["valid"] is False
        # This row now hashes correctly; the break shows up at the next row.
        assert body["first_invalid_seq"] == seq + 1
        assert any(i["problem"] == "broken_link" for i in body["issues"])


def test_deleting_a_row_is_detected(client: TestClient) -> None:
    case_id = _analysed_case(client, "audit-delete.jpg")

    with _session() as session:
        row = (
            session.query(AuditLog)
            .filter(AuditLog.case_id == case_id, AuditLog.event == "HASH_CALCULATED")
            .order_by(AuditLog.seq.asc())
            .first()
        )
        assert row is not None
        snapshot = {
            "seq": row.seq,
            "audit_id": row.audit_id,
            "case_id": row.case_id,
            "event": row.event,
            "timestamp": row.timestamp,
            "actor": row.actor,
            "details": dict(row.details or {}),
            "previous_hash": row.previous_hash,
            "row_hash": row.row_hash,
        }
        session.delete(row)
        session.commit()

    try:
        body = client.post(f"/api/cases/{case_id}/audit/verify?record=false").json()
        assert body["valid"] is False
        assert body["first_invalid_seq"] == snapshot["seq"] + 1
        assert any(i["problem"] == "broken_link" for i in body["issues"])
        # The deleted event is simply gone -- absence is only visible via the chain.
        trail = client.get(f"/api/cases/{case_id}/audit").json()
        assert "HASH_CALCULATED" not in [e["event"] for e in trail["events"]]
    finally:
        with _session() as session:
            session.add(AuditLog(**snapshot))
            session.commit()
        with _session() as session:
            assert audit.verify_chain(session)["valid"] is True


def test_verify_for_unknown_case_returns_404(client: TestClient) -> None:
    assert (
        client.post(f"/api/cases/{uuid.uuid4()}/audit/verify").status_code == 404
    )
    assert client.get(f"/api/cases/{uuid.uuid4()}/audit").status_code == 404


def test_unrecognised_event_is_recorded_not_dropped(caplog) -> None:
    """Losing an audit event is worse than logging an unexpected one."""
    with _session() as session:
        before = audit.head_hash(session)
        with caplog.at_level("WARNING", logger="pramaan.audit"):
            entry = audit.record(
                session, event="NOT_A_REAL_EVENT", actor="test", details={"x": 1}
            )
        session.commit()
        seq = entry.seq
        assert entry.previous_hash == before
        assert "NOT_A_REAL_EVENT" in caplog.text

    with _session() as session:
        assert audit.verify_chain(session)["valid"] is True
        stored = session.get(AuditLog, seq)
        assert stored.event == "NOT_A_REAL_EVENT"
