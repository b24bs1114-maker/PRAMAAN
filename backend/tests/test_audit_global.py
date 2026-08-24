"""The audit chain across every case.

The chain is global: rows from every case are interleaved in one hash chain, and
that is the only order in which it can be verified. A case-scoped trail is a
filter over it, never a chain of its own -- so what these tests check is that the
filtered view stays a view: the same rows, the same order, the same head hash.

Two behaviours matter more than the rest:

* An unparseable ``since``/``until`` bound is refused with a 400. Dropping the
  filter silently would return rows outside the range the caller asked for while
  letting them believe the range was applied.
* Reading never writes. Verification does -- deliberately, because confirming
  integrity is itself an auditable act -- and that write is asserted rather than
  assumed.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.services import audit as audit_service
from tests.helpers import jpeg_bytes


def _trail(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/audit", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _seed_case(client: TestClient, seed: int, name: str) -> str:
    res = client.post(
        "/api/cases/upload", files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")}
    )
    assert res.status_code == 201, res.text
    return res.json()["case"]["case_id"]


# --------------------------------------------------------------------------- #
# Reading the chain
# --------------------------------------------------------------------------- #
def test_global_trail_spans_every_case(client: TestClient) -> None:
    first = _seed_case(client, 44001, "audit-global-a.jpg")
    second = _seed_case(client, 44002, "audit-global-b.jpg")

    data = _trail(client, limit=5000)
    assert data["scope"] == "all_cases"
    assert data["case_id"] is None
    case_ids = {row["case_id"] for row in data["events"]}
    assert {first, second} <= case_ids

    assert data["genesis_hash"] == audit_service.GENESIS_HASH
    assert data["algorithm"] == audit_service.ALGORITHM
    assert data["interpretation"] == audit_service.INTERPRETATION
    assert data["head_hash"]
    assert set(data["known_events"]) == set(audit_service.KNOWN_EVENTS)
    # Chain order, oldest first, so a reader can recompute the links in sequence.
    seqs = [row["seq"] for row in data["events"]]
    assert seqs == sorted(seqs)
    for row in data["events"]:
        assert row["previous_hash"] and row["row_hash"]


def test_case_filter_is_a_view_over_the_same_rows(client: TestClient) -> None:
    case_id = _seed_case(client, 44003, "audit-view.jpg")

    scoped = _trail(client, case_id=case_id, limit=5000)
    per_case = client.get(f"/api/cases/{case_id}/audit").json()

    assert scoped["scope"] == "case"
    assert scoped["case_id"] == case_id
    assert [row["audit_id"] for row in scoped["events"]] == [
        row["audit_id"] for row in per_case["events"]
    ]
    # The head hash is the head of the whole chain in both views: a case does not
    # have a chain head of its own.
    assert scoped["head_hash"] == per_case["head_hash"]
    assert all(row["case_id"] == case_id for row in scoped["events"])


def test_event_and_actor_filters_narrow_the_selection(client: TestClient) -> None:
    case_id = _seed_case(client, 44004, "audit-filter.jpg")

    ingested = _trail(
        client,
        case_id=case_id,
        event=audit_service.EVENT_EVIDENCE_INGESTED,
        limit=5000,
    )
    assert ingested["total_rows"] == 1
    assert ingested["events"][0]["event"] == audit_service.EVENT_EVIDENCE_INGESTED
    assert ingested["filters"]["event"] == audit_service.EVENT_EVIDENCE_INGESTED

    by_actor = _trail(client, case_id=case_id, actor="api", limit=5000)
    assert by_actor["total_rows"] >= 1
    assert all(row["actor"] == "api" for row in by_actor["events"])

    # An event that exists in the vocabulary but not in this case is an honest
    # empty page, not an error.
    none_yet = _trail(
        client, case_id=case_id, event=audit_service.EVENT_REPORT_GENERATED
    )
    assert none_yet["total_rows"] == 0
    assert none_yet["events"] == []
    assert none_yet["returned_from"] is None


def test_since_and_until_bound_the_range(client: TestClient) -> None:
    case_id = _seed_case(client, 44005, "audit-bounds.jpg")
    rows = _trail(client, case_id=case_id, limit=5000)["events"]
    pivot = rows[0]["timestamp"]

    since = _trail(client, case_id=case_id, since=pivot, limit=5000)
    assert since["filters"]["since"] == pivot
    assert all(row["timestamp"] >= pivot for row in since["events"])

    until = _trail(client, case_id=case_id, until=pivot, limit=5000)
    assert all(row["timestamp"] <= pivot for row in until["events"])

    # The bound is normalised to the stored fixed-width form, which is what makes
    # string comparison over the timestamp column chronological.
    offset_form = _trail(
        client, case_id=case_id, since=pivot.replace("Z", "+00:00"), limit=5000
    )
    assert offset_form["filters"]["since"] == pivot
    assert len(offset_form["events"]) == len(since["events"])


def test_unparseable_bound_is_refused_not_ignored(client: TestClient) -> None:
    for field in ("since", "until"):
        res = client.get("/api/audit", params={field: "last tuesday"})
        assert res.status_code == 400
        body = res.json()
        assert "error" in body and "detail" not in body
        assert field in body["error"]["message"]
        assert body["request_id"]


def test_paging_reports_whether_more_matching_rows_exist(client: TestClient) -> None:
    case_id = _seed_case(client, 44006, "audit-paging.jpg")
    everything = _trail(client, case_id=case_id, limit=5000)
    assert everything["total_rows"] >= 2

    first = _trail(client, case_id=case_id, limit=1)
    assert first["count"] == 1
    assert first["truncated"] is True
    assert first["total_rows"] == everything["total_rows"]
    assert first["returned_from"] == everything["events"][0]["seq"]

    second = _trail(client, case_id=case_id, limit=1, offset=1)
    assert second["offset"] == 1
    assert second["returned_from"] == everything["events"][1]["seq"]
    assert second["events"][0]["audit_id"] != first["events"][0]["audit_id"]

    # ``total_rows`` counts rows matching the filters, so ``truncated`` means
    # "more matching rows exist", not "more rows exist somewhere".
    last = _trail(
        client, case_id=case_id, limit=1, offset=everything["total_rows"] - 1
    )
    assert last["truncated"] is False


def test_reading_the_trail_does_not_modify_it(client: TestClient) -> None:
    before = _trail(client, limit=1)
    for _ in range(3):
        _trail(client, limit=5000)
    after = _trail(client, limit=1)

    assert after["head_hash"] == before["head_hash"]
    assert after["total_rows"] == before["total_rows"]


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def test_verification_walks_the_whole_chain_and_records_itself(
    client: TestClient,
) -> None:
    _seed_case(client, 44007, "audit-verify.jpg")
    before = _trail(client, limit=1)["total_rows"]

    res = client.post("/api/audit/verify")
    assert res.status_code == 200
    data = res.json()

    assert data["valid"] is True
    assert data["scope"] == "global_chain"
    assert data["case_id"] is None
    assert data["first_invalid_seq"] is None
    assert data["issues"] == []
    assert data["total_rows"] >= before
    assert data["verified_row_count"] == data["total_rows"]
    # The whole history of a deployment on every integrity check would make the
    # answer unreadable rather than more trustworthy, so events are opt-in.
    assert data["events_included"] is False
    assert data["events"] == []
    assert data["interpretation"] == audit_service.INTERPRETATION

    # The check is itself auditable, and it is appended after the recomputation.
    appended = _trail(
        client, event=audit_service.EVENT_AUDIT_VERIFIED, limit=5000
    )["events"][-1]
    assert appended["details"]["scope"] == "all_cases"
    assert appended["details"]["valid"] is True
    assert appended["details"]["total_rows"] == data["total_rows"]
    assert appended["details"]["verified_head_hash"] == data["head_hash"]
    assert _trail(client, limit=1)["total_rows"] == data["total_rows"] + 1


def test_verification_can_be_run_without_recording_it(client: TestClient) -> None:
    before = _trail(client, limit=1)["total_rows"]
    res = client.post("/api/audit/verify", params={"record": "false"})
    assert res.status_code == 200
    assert res.json()["valid"] is True
    assert _trail(client, limit=1)["total_rows"] == before


def test_include_events_returns_the_rows_that_were_verified(
    client: TestClient,
) -> None:
    res = client.post(
        "/api/audit/verify",
        params={"record": "false", "include_events": "true"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["events_included"] is True
    assert len(data["events"]) == data["verified_row_count"] == data["total_rows"]
    seqs = [row["seq"] for row in data["events"]]
    assert seqs == sorted(seqs)


def test_a_tampered_row_is_detected_and_named(client: TestClient, settings) -> None:
    """The chain is only worth having if an edit to a historical row is caught."""
    from sqlalchemy import select

    from app.models import AuditLog, get_session_factory

    case_id = _seed_case(client, 44008, "audit-tamper.jpg")
    factory = get_session_factory(settings)

    with factory() as session:
        row = (
            session.execute(
                select(AuditLog)
                .where(AuditLog.case_id == case_id)
                .order_by(AuditLog.seq.asc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        assert row is not None
        seq, original_actor = row.seq, row.actor
        row.actor = "someone-else"
        session.commit()

    try:
        data = client.post("/api/audit/verify", params={"record": "false"}).json()
        assert data["valid"] is False
        assert data["first_invalid_seq"] == seq
        assert data["issues"]
        assert any(issue["seq"] == seq for issue in data["issues"])
    finally:
        with factory() as session:
            restored = session.get(AuditLog, seq)
            assert restored is not None
            restored.actor = original_actor
            session.commit()

    # And the chain verifies again once the row is put back as it was.
    assert client.post("/api/audit/verify", params={"record": "false"}).json()["valid"]
