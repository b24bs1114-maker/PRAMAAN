"""``GET /api/cases/{id}/propagation`` must be readable without writing.

The route is a page load: the Provenance screen calls it the moment it mounts.
It used to append two rows to the case's audit chain every time -- a
``MATCH_SEARCHED`` from the near-duplicate retrieval it kicked off for any case
with none stored, and a ``PROPAGATION_RECONSTRUCTED`` for the reconstruction
itself -- so the chain's head hash moved because somebody *looked* at a case.

Reconstructing is a derivation from records already held. Only actually
computing something is an act on the evidence, and only an act belongs in the
chain. ``record=false`` is that read, and these tests pin it:

* it appends nothing and moves no head hash;
* it computes nothing, so it cannot smuggle a write in through the matcher;
* it says which of those two situations produced its graph, because "searched
  and found nothing" and "never searched" yield an identical empty graph and
  mean opposite things;
* and the recording default is unchanged, so an explicit trace is still an
  auditable act.

The chain is global, so every assertion here compares a before/after pair taken
around one call rather than asserting an absolute row count.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import jpeg_bytes


def _open_case(client: TestClient, name: str, *, seed: int = 0) -> str:
    """Seal one image into a fresh case and return its case id."""
    res = client.post(
        "/api/cases/upload",
        files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")},
        data={"title": f"Propagation fixture {name}"},
    )
    assert res.status_code == 201, res.text
    return res.json()["case"]["case_id"]


def _chain(client: TestClient, case_id: str) -> tuple[int, str]:
    """The two fields that pin chain invariance: length and head hash."""
    res = client.get(f"/api/cases/{case_id}/audit")
    assert res.status_code == 200, res.text
    body = res.json()
    return body["total_rows"], body["head_hash"]


def _propagation(client: TestClient, case_id: str, **params) -> dict:
    res = client.get(f"/api/cases/{case_id}/propagation", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_reading_propagation_writes_nothing_to_the_chain(client: TestClient) -> None:
    case_id = _open_case(client, "read-only-a.jpg", seed=11)

    before = _chain(client, case_id)
    body = _propagation(client, case_id, record="false")
    after = _chain(client, case_id)

    assert after == before, (
        "reading a case's propagation moved the audit chain; opening the "
        "Provenance screen must not write forensic history"
    )
    assert body["recorded"] is False


def test_repeated_reads_leave_the_chain_identical(client: TestClient) -> None:
    """Three page loads in a row are still zero rows, not one per visit."""
    case_id = _open_case(client, "read-only-b.jpg", seed=12)

    before = _chain(client, case_id)
    for _ in range(3):
        _propagation(client, case_id, record="false")

    assert _chain(client, case_id) == before


def test_a_read_does_not_run_near_duplicate_retrieval(client: TestClient) -> None:
    """The read must not compute, not merely decline to record what it computed.

    Suppressing the audit row while still running the search would be worse than
    the original bug: the work would happen and the chain would not say so.
    ``trace_status`` is the observable -- a fresh case has no retrieval on
    record, so a read of it must report ``NOT_RUN``.
    """
    case_id = _open_case(client, "read-only-c.jpg", seed=13)

    body = _propagation(client, case_id, record="false")

    assert body["trace_status"] == "NOT_RUN"
    assert body["matched_candidate_count"] == 0
    assert "match_search" not in body or body.get("match_search") is None


def test_an_unsearched_case_is_not_described_as_having_no_copies(
    client: TestClient,
) -> None:
    """"None were found" is a measurement. "None were looked for" is not.

    Both produce an empty graph. Reporting the second in the wording of the
    first would present the absence of a search as evidence that no other copies
    exist, which is precisely the inference this system must never invite.
    """
    case_id = _open_case(client, "read-only-d.jpg", seed=14)

    notes = " ".join(_propagation(client, case_id, record="false")["notes"])

    assert "has been run" in notes or "has not been" in notes or "No near-duplicate retrieval" in notes
    assert "were found in the index" not in notes


def test_recording_is_still_the_default(client: TestClient) -> None:
    """An explicit trace remains an auditable act.

    The fix narrows what a *read* does; it does not stop the system recording
    work that genuinely happened. A caller that names no preference still gets
    the recorded behaviour.
    """
    case_id = _open_case(client, "recorded-a.jpg", seed=15)

    before_rows, _ = _chain(client, case_id)
    body = _propagation(client, case_id)
    after_rows, _ = _chain(client, case_id)

    assert body["recorded"] is True
    assert after_rows > before_rows, "a recorded trace appended nothing"

    events = [
        row["event"]
        for row in client.get(f"/api/cases/{case_id}/audit").json()["events"]
    ]
    assert "PROPAGATION_RECONSTRUCTED" in events


def test_a_recorded_trace_reports_that_it_computed(client: TestClient) -> None:
    case_id = _open_case(client, "recorded-b.jpg", seed=16)

    first = _propagation(client, case_id)
    assert first["trace_status"] == "COMPUTED"
    assert first["trace_status_meaning"]

    # Retrieval is now on record, so the next call reuses it rather than
    # recomputing -- and says so.
    second = _propagation(client, case_id, record="false")
    assert second["trace_status"] == "STORED"


def test_a_read_after_a_trace_still_writes_nothing(client: TestClient) -> None:
    """The read path stays read-only even when there is something to reuse."""
    case_id = _open_case(client, "recorded-c.jpg", seed=17)
    _propagation(client, case_id)

    before = _chain(client, case_id)
    body = _propagation(client, case_id, record="false")

    assert _chain(client, case_id) == before
    assert body["trace_status"] == "STORED"
    assert body["recorded"] is False


def test_refresh_cannot_be_combined_with_record_false(client: TestClient) -> None:
    """A search that genuinely ran must never go unrecorded.

    ``refresh=true`` asks for recomputation. Honouring it silently under
    ``record=false`` would do real work to the case and leave the chain saying
    nothing happened -- the inverse of the bug being fixed, and a worse one. It
    is rejected rather than resolved in either direction.
    """
    case_id = _open_case(client, "conflict.jpg", seed=18)

    before = _chain(client, case_id)
    res = client.get(
        f"/api/cases/{case_id}/propagation",
        params={"refresh": "true", "record": "false"},
    )

    assert res.status_code == 422, res.text
    assert "record=false" in res.json()["error"]["message"]
    assert _chain(client, case_id) == before, "the rejected call still wrote"
