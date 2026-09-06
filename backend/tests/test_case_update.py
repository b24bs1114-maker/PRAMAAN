"""The case-update contract, ``PATCH /api/cases/{id}``.

The dossier's "Edit Case" dialog is wired straight to this endpoint, so the
shape it depends on is pinned here: status arrives under the form key
``case_status`` (not ``status``); only the fields actually sent are written, so
a partial edit leaves everything else alone; and every accepted edit appends one
``CASE_UPDATED`` audit entry naming exactly the fields that changed. These are
the guarantees that let the frontend send only what the operator touched and
trust that nothing else moved.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.services import audit
from tests.helpers import jpeg_bytes


def _seed_fused_verdict(case_id: str, evidence_id: str, verdict: str) -> None:
    """Write one fused verdict row directly, without the heavy pipeline.

    ``latest_verdict`` is derived from the newest fusion row, so this is enough
    to give the case a verdict for the read/patch shape comparison below.
    """
    from app.models import AnalysisResult, get_session_factory

    session = get_session_factory()()
    try:
        session.add(
            AnalysisResult(
                id=str(uuid.uuid4()),
                case_id=case_id,
                evidence_id=evidence_id,
                kind="fusion",
                status="OK",
                verdict=verdict,
            )
        )
        session.commit()
    finally:
        session.close()


def _open_case(client: TestClient, *, seed: int = 0) -> dict:
    body = client.post(
        "/api/cases/upload",
        files={"file": (f"u{seed}.jpg", jpeg_bytes(seed=seed), "image/jpeg")},
        data={
            "title": f"Original title {seed}",
            "description": f"Original description {seed}.",
            "priority": "medium",
        },
    )
    assert body.status_code == 201, body.text
    return body.json()["case"]


def _case_update_events(client: TestClient, case_id: str) -> list[dict]:
    trail = client.get(f"/api/cases/{case_id}/audit").json()
    return [e for e in trail["events"] if e["event"] == audit.EVENT_CASE_UPDATED]


def test_status_is_accepted_under_the_case_status_form_key(client):
    """The frontend sends status as ``case_status``; ``status`` is not the key.

    FastAPI names the form field after the parameter, and the parameter is
    ``case_status`` to avoid colliding with the response model's ``status``. A
    client that posted ``status=...`` would have it silently ignored, so this
    pins the working key and proves the other does nothing.
    """
    case = _open_case(client, seed=1)
    cid = case["case_id"]

    good = client.patch(f"/api/cases/{cid}", data={"case_status": "closed"})
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "closed"

    # The wrong key is accepted as a request but changes nothing.
    before = client.get(f"/api/cases/{cid}").json()["status"]
    ignored = client.patch(f"/api/cases/{cid}", data={"status": "archived"})
    assert ignored.status_code == 200, ignored.text
    assert client.get(f"/api/cases/{cid}").json()["status"] == before


def test_only_the_fields_sent_are_changed(client):
    """A partial edit touches exactly what it names and leaves the rest intact.

    This is what makes the dialog's "send only the changed fields" approach
    safe: editing the priority must not blank the title, the description or the
    examiner just because they were not in the request body.
    """
    case = _open_case(client, seed=2)
    cid = case["case_id"]
    original = client.get(f"/api/cases/{cid}").json()

    updated = client.patch(f"/api/cases/{cid}", data={"priority": "high"})
    assert updated.status_code == 200, updated.text
    row = updated.json()

    assert row["priority"] == "high"
    assert row["title"] == original["title"]
    assert row["description"] == original["description"]
    assert row["examiner"] == original["examiner"]
    assert row["status"] == original["status"]


def test_an_edit_appends_one_case_updated_entry_naming_the_changed_fields(client):
    """Every accepted edit is one custody event over what changed.

    The audit entry carries ``changed_fields``, and it must list exactly the
    columns that moved -- not the whole record, and not a field that was sent
    equal to what was already stored (the client omits those, and this proves
    the backend agrees on the shape).
    """
    case = _open_case(client, seed=3)
    cid = case["case_id"]

    before = len(_case_update_events(client, cid))

    resp = client.patch(
        f"/api/cases/{cid}",
        data={"title": "Revised subject line", "priority": "low"},
    )
    assert resp.status_code == 200, resp.text

    events = _case_update_events(client, cid)
    assert len(events) == before + 1
    latest = events[-1]
    assert sorted(latest["details"]["changed_fields"]) == ["priority", "title"]


def test_the_chain_still_verifies_after_an_edit(client):
    """An edit is a normal chained event, not a break in the ledger."""
    case = _open_case(client, seed=4)
    cid = case["case_id"]

    client.patch(f"/api/cases/{cid}", data={"examiner": "Assigned Examiner"})

    verify = client.post(
        f"/api/cases/{cid}/audit/verify", params={"record": "false"}
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["valid"] is True


def test_the_patch_response_carries_the_same_verdict_as_a_fresh_read(client):
    """PATCH must return the whole record GET does, verdict included.

    ``latest_verdict`` is not a column, so the shared serializer omits it; the
    read paths reattach it. The edit dialog treats the PATCH response as the
    complete record and re-renders from it, so if PATCH dropped the verdict an
    unrelated edit (a priority change) would make an already-analysed case
    blink to "not yet analysed" in the dossier until the next full reload. This
    pins PATCH to the read shape.
    """
    upload = client.post(
        "/api/cases/upload",
        files={"file": ("verdict.jpg", jpeg_bytes(seed=91), "image/jpeg")},
        data={"title": "Analysed case", "description": "Has a verdict.", "priority": "medium"},
    )
    assert upload.status_code == 201, upload.text
    cid = upload.json()["case"]["case_id"]
    eid = upload.json()["evidence"]["evidence_id"]

    _seed_fused_verdict(cid, eid, "MANIPULATED")

    read = client.get(f"/api/cases/{cid}").json()
    assert read["latest_verdict"] == "MANIPULATED", read

    patched = client.patch(f"/api/cases/{cid}", data={"priority": "high"})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["priority"] == "high"
    assert body["latest_verdict"] == "MANIPULATED", (
        "PATCH must return the verdict already on record, not drop it: "
        f"{body.get('latest_verdict')!r}"
    )
