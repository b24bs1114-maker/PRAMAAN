"""Server-side case list filters: verdict, examiner, and created-at range.

These filters exist so the Cases screen can narrow the list against the real
database rather than fetching everything and filtering in the browser. Each test
isolates its own rows with a unique examiner tag, because the test database is
shared across the session.

Intake no longer takes an examiner name -- it stamps the signed-in operator --
so the tag is applied afterwards through ``PATCH /api/cases/{id}``, which is how
a case is reassigned in the API. What is under test is the filter, not where the
name came from.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.utils.timeutil import iso, utcnow
from tests.helpers import jpeg_bytes


def _upload(client: TestClient, *, examiner: str, priority: str = "medium", seed: int = 0) -> dict:
    body = client.post(
        "/api/cases/upload",
        files={"file": (f"f{seed}.jpg", jpeg_bytes(seed=seed), "image/jpeg")},
        data={
            "title": f"Filter fixture {seed}",
            "description": f"Row for the {examiner} filter assertions.",
            "priority": priority,
        },
    )
    assert body.status_code == 201, body.text
    payload = body.json()

    reassigned = client.patch(
        f"/api/cases/{payload['case']['case_id']}", data={"examiner": examiner}
    )
    assert reassigned.status_code == 200, reassigned.text
    payload["case"] = reassigned.json()
    return payload


def _add_analysis(
    case_id: str,
    evidence_id: str,
    verdict: str | None,
    *,
    age_seconds: int = 0,
    kind: str = "fusion",
) -> None:
    """Insert an analysis row directly, so verdict filtering has truth to filter
    on without running the full (heavy) analysis pipeline.

    ``kind`` defaults to fusion because that is the only kind that carries a
    verdict. It is a parameter so a test can reproduce the row order the real
    pipeline writes -- fusion, then propagation -- which is the shape that broke
    the list: every other kind stores ``verdict = NULL``, so "the newest analysis
    row" is not "the newest verdict".
    """
    from app.models import AnalysisResult, get_session_factory

    session = get_session_factory()()
    try:
        session.add(
            AnalysisResult(
                id=str(uuid.uuid4()),
                case_id=case_id,
                evidence_id=evidence_id,
                kind=kind,
                status="OK",
                verdict=verdict,
                created_at=utcnow() - timedelta(seconds=age_seconds),
            )
        )
        session.commit()
    finally:
        session.close()


def test_examiner_filter_is_server_side(client: TestClient) -> None:
    tag = f"Examiner-{uuid.uuid4().hex[:8]}"
    a = _upload(client, examiner=tag, seed=201)
    _upload(client, examiner=f"Other-{uuid.uuid4().hex[:8]}", seed=202)

    listed = client.get("/api/cases", params={"examiner": tag})
    assert listed.status_code == 200
    body = listed.json()
    numbers = {c["case_number"] for c in body["cases"]}
    assert a["case"]["case_number"] in numbers
    # The count is the server's, and it reflects the filter -- exactly the one
    # case tagged with this examiner, not the whole table.
    assert body["count"] == 1
    assert len(body["cases"]) == 1


def test_examiner_filter_matches_substring(client: TestClient) -> None:
    tag = f"Priya-{uuid.uuid4().hex[:8]}"
    a = _upload(client, examiner=f"Insp. {tag} (Cyber Cell)", seed=203)

    listed = client.get("/api/cases", params={"examiner": tag})
    assert listed.status_code == 200
    numbers = {c["case_number"] for c in listed.json()["cases"]}
    assert a["case"]["case_number"] in numbers


def test_verdict_filter_uses_the_latest_analysis(client: TestClient) -> None:
    tag = f"Verdict-{uuid.uuid4().hex[:8]}"
    manip = _upload(client, examiner=tag, seed=204)
    auth = _upload(client, examiner=tag, seed=205)
    _add_analysis(
        manip["case"]["case_id"], manip["evidence"]["evidence_id"], "MANIPULATED"
    )
    _add_analysis(
        auth["case"]["case_id"], auth["evidence"]["evidence_id"], "AUTHENTIC"
    )

    # Compose with the examiner tag so the row set is deterministic.
    manipulated = client.get(
        "/api/cases", params={"verdict": "MANIPULATED", "examiner": tag}
    ).json()
    numbers = {c["case_number"] for c in manipulated["cases"]}
    assert manip["case"]["case_number"] in numbers
    assert auth["case"]["case_number"] not in numbers
    assert manipulated["count"] == 1


def test_verdict_filter_follows_the_newest_verdict(client: TestClient) -> None:
    tag = f"Latest-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=206)
    cid = case["case"]["case_id"]
    eid = case["evidence"]["evidence_id"]
    # Older MANIPULATED, then a newer AUTHENTIC -- the list verdict is the newest.
    _add_analysis(cid, eid, "MANIPULATED", age_seconds=120)
    _add_analysis(cid, eid, "AUTHENTIC", age_seconds=0)

    as_manipulated = client.get(
        "/api/cases", params={"verdict": "MANIPULATED", "examiner": tag}
    ).json()
    as_authentic = client.get(
        "/api/cases", params={"verdict": "AUTHENTIC", "examiner": tag}
    ).json()

    assert as_manipulated["count"] == 0
    assert case["case"]["case_number"] in {c["case_number"] for c in as_authentic["cases"]}


def test_verdict_filter_pending_selects_unanalysed_cases(client: TestClient) -> None:
    """`verdict=PENDING` is the "not yet analysed" filter: it selects cases with
    no fused verdict on record (latest verdict NULL), and excludes any case once a
    verdict lands. It is the server-side twin of the UI's "NOT YET ANALYSED"
    state, which the client must not re-derive by scanning a partial page."""
    tag = f"Pending-{uuid.uuid4().hex[:8]}"
    unscored = _upload(client, examiner=tag, seed=209)
    scored = _upload(client, examiner=tag, seed=210)
    _add_analysis(
        scored["case"]["case_id"], scored["evidence"]["evidence_id"], "AUTHENTIC"
    )

    pending = client.get(
        "/api/cases", params={"verdict": "PENDING", "examiner": tag}
    ).json()
    numbers = {c["case_number"] for c in pending["cases"]}
    assert unscored["case"]["case_number"] in numbers
    assert scored["case"]["case_number"] not in numbers
    assert pending["count"] == 1
    # Every returned row genuinely lacks a verdict -- not merely absent from the
    # page, but null on the record.
    assert all(not c.get("latest_verdict") for c in pending["cases"])

    # Once the pending case is analysed, it leaves the PENDING set.
    _add_analysis(
        unscored["case"]["case_id"],
        unscored["evidence"]["evidence_id"],
        "MANIPULATED",
    )
    after = client.get(
        "/api/cases", params={"verdict": "PENDING", "examiner": tag}
    ).json()
    assert after["count"] == 0



def test_a_later_signal_row_does_not_erase_the_verdict(client: TestClient) -> None:
    """The regression this file did not catch for a while.

    Only fusion rows carry a verdict; metadata, detector, provenance, forensics
    and propagation rows all store ``verdict = NULL``. The pipeline writes
    propagation *after* fusion, so for a fully analysed case the newest analysis
    row is normally a propagation row -- and the list used to read that row's NULL
    verdict and report the case as never analysed.

    On the live store that meant 40 cases with real fused verdicts were shown as
    "NOT YET ANALYSED", every one of the three verdict filters returned nothing at
    all, and "Not Yet Analysed" returned all 103 cases including the analysed
    ones. Every fixture here wrote fusion rows only, which is exactly why no test
    noticed.
    """
    tag = f"Ordered-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=211)
    cid, eid = case["case"]["case_id"], case["evidence"]["evidence_id"]
    number = case["case"]["case_number"]

    # The order the pipeline actually writes: metadata, detector, provenance,
    # fusion, then propagation. Only one of them is a verdict.
    _add_analysis(cid, eid, None, kind="metadata", age_seconds=50)
    _add_analysis(cid, eid, None, kind="detector", age_seconds=40)
    _add_analysis(cid, eid, None, kind="provenance", age_seconds=30)
    _add_analysis(cid, eid, "MANIPULATED", kind="fusion", age_seconds=20)
    _add_analysis(cid, eid, None, kind="propagation", age_seconds=10)

    listed = client.get("/api/cases", params={"examiner": tag}).json()
    assert listed["count"] == 1, listed
    assert listed["cases"][0]["latest_verdict"] == "MANIPULATED", (
        "the newest row is a propagation row with no verdict; the case's verdict "
        f"is still MANIPULATED, not {listed['cases'][0]['latest_verdict']!r}"
    )

    # The filter has to agree with the column it filters on, in both directions.
    as_manipulated = client.get(
        "/api/cases", params={"verdict": "MANIPULATED", "examiner": tag}
    ).json()
    assert number in {c["case_number"] for c in as_manipulated["cases"]}
    assert as_manipulated["count"] == 1

    as_pending = client.get(
        "/api/cases", params={"verdict": "PENDING", "examiner": tag}
    ).json()
    assert as_pending["count"] == 0, (
        "a case with a fused verdict on record is not awaiting analysis"
    )


def test_every_surface_reports_the_same_verdict_for_the_same_case(
    client: TestClient,
) -> None:
    """The case queue, the case record and the dashboard cannot disagree.

    Three screens read a case's verdict from three endpoints. They each used to
    compute it themselves, and one of the three filtered on fusion rows while the
    others did not -- so the dashboard showed INCONCLUSIVE for a case the Cases
    queue showed as NOT YET ANALYSED. They now share one query, and this is the
    assertion that keeps them sharing it.
    """
    tag = f"Agree-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=212)
    cid, eid = case["case"]["case_id"], case["evidence"]["evidence_id"]

    _add_analysis(cid, eid, "INSUFFICIENT_EVIDENCE", kind="fusion", age_seconds=20)
    _add_analysis(cid, eid, None, kind="propagation", age_seconds=10)

    from_list = client.get("/api/cases", params={"examiner": tag}).json()["cases"][0]
    from_record = client.get(f"/api/cases/{cid}").json()
    dashboard = client.get("/api/dashboard/summary").json()
    from_dashboard = next(
        (
            row
            for row in dashboard["recent_investigations"]
            if row["case_id"] == cid
        ),
        None,
    )

    assert from_list["latest_verdict"] == "INSUFFICIENT_EVIDENCE", from_list
    assert from_record["latest_verdict"] == "INSUFFICIENT_EVIDENCE", from_record
    assert from_dashboard is not None, "the case is missing from the dashboard queue"
    assert from_dashboard["latest_verdict"] == "INSUFFICIENT_EVIDENCE", from_dashboard


def test_a_case_with_only_signal_rows_is_still_unanalysed(client: TestClient) -> None:
    """The other direction: signals without fusion are not a verdict.

    A case whose analysis was interrupted after the metadata read has rows in
    ``analysis_results`` but no verdict. Nothing may promote that into one, and it
    must stay in the "not yet analysed" set -- abstaining is a real state, and
    ``NULL`` is not turned into a token here any more than it is turned into 0.
    """
    tag = f"Partial-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=213)
    cid, eid = case["case"]["case_id"], case["evidence"]["evidence_id"]

    _add_analysis(cid, eid, None, kind="metadata", age_seconds=20)
    _add_analysis(cid, eid, None, kind="detector", age_seconds=10)

    listed = client.get("/api/cases", params={"examiner": tag}).json()
    assert listed["cases"][0]["latest_verdict"] is None, listed["cases"][0]
    assert client.get(f"/api/cases/{cid}").json()["latest_verdict"] is None

    pending = client.get(
        "/api/cases", params={"verdict": "PENDING", "examiner": tag}
    ).json()
    assert pending["count"] == 1


def test_created_at_range_bounds_the_list(client: TestClient) -> None:
    tag = f"Dated-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=207)
    number = case["case"]["case_number"]

    future = iso(utcnow() + timedelta(days=1))
    past = iso(utcnow() - timedelta(days=1))

    # Created before now => included when the upper bound is in the future.
    included = client.get(
        "/api/cases", params={"examiner": tag, "created_before": future}
    ).json()
    assert number in {c["case_number"] for c in included["cases"]}

    # Excluded when required to be newer than a future instant.
    excluded = client.get(
        "/api/cases", params={"examiner": tag, "created_after": future}
    ).json()
    assert excluded["count"] == 0

    # Included when required to be newer than a past instant.
    after_past = client.get(
        "/api/cases", params={"examiner": tag, "created_after": past}
    ).json()
    assert number in {c["case_number"] for c in after_past["cases"]}


def test_malformed_date_is_rejected_not_ignored(client: TestClient) -> None:
    bad = client.get("/api/cases", params={"created_after": "not-a-date"})
    assert bad.status_code == 422
    assert "created_after" in bad.text


def test_no_argument_list_is_unfiltered(client: TestClient) -> None:
    tag = f"Plain-{uuid.uuid4().hex[:8]}"
    _upload(client, examiner=tag, seed=208)
    everything = client.get("/api/cases", params={"limit": 500}).json()
    # The tagged case is present in an unfiltered listing.
    assert any(c["examiner"] == tag for c in everything["cases"])
    assert everything["count"] >= 1


def _searched_case_numbers(client: TestClient, term: str) -> set[str]:
    listed = client.get("/api/cases", params={"q": term})
    assert listed.status_code == 200, listed.text
    return {c["case_number"] for c in listed.json()["cases"]}


def test_q_matches_case_fields(client: TestClient) -> None:
    tag = f"QCase-{uuid.uuid4().hex[:8]}"
    case = _upload(client, examiner=tag, seed=209)

    # The term matches the case's own examiner field.
    assert case["case"]["case_number"] in _searched_case_numbers(client, tag)
    # And the case number itself.
    assert case["case"]["case_number"] in _searched_case_numbers(
        client, case["case"]["case_number"]
    )


def test_q_matches_evidence_filename_of_a_case(client: TestClient) -> None:
    # The header search advertises "evidence" too: a term matching a sealed
    # exhibit's filename must surface the case that holds it -- as a case row,
    # never as an evidence row, because this endpoint's contract is a case list.
    case = _upload(client, examiner=f"QEv-{uuid.uuid4().hex[:8]}", seed=210)
    filename = case["evidence"]["filename"]

    assert case["case"]["case_number"] in _searched_case_numbers(client, filename)


def test_q_matches_evidence_sha256_of_a_case(client: TestClient) -> None:
    # "hashes" in the same placeholder: a digest (whole or fragment) reaches
    # the case through its evidence.
    case = _upload(client, examiner=f"QHash-{uuid.uuid4().hex[:8]}", seed=211)
    digest = case["evidence"]["sha256"]

    assert case["case"]["case_number"] in _searched_case_numbers(client, digest)
    assert case["case"]["case_number"] in _searched_case_numbers(client, digest[:16])


def test_q_matching_nothing_yields_an_empty_case_list(client: TestClient) -> None:
    assert _searched_case_numbers(client, f"no-such-term-{uuid.uuid4().hex[:8]}") == set()
