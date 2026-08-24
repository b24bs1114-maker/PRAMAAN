"""Alerts, which are derived rather than stored.

There is no alerts table in this build, so every assertion here is about a
derivation: an alert must be traceable to the row it came from, must carry the
figures it was derived from, and must disappear when that fact does.

Four properties are load-bearing:

* An alert never re-thresholds a score. The severity follows the verdict token
  fusion wrote, not a second opinion formed in the alerts layer.
* An **absent** C2PA manifest is never an alert. Almost no media in circulation
  carries one.
* A near-duplicate alert is worded as candidates, and one alert covers one query
  item rather than one per pair.
* Reading the alerts list does not verify the audit chain. What is reported is
  the last verification that was actually run -- and "never verified" is stated
  as that, not as a failure.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api import alerts as alerts_api
from app.services import audit as audit_service
from app.services import fusion as fusion_service
from tests.helpers import jpeg_bytes


def _alerts(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/alerts", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _analysed_case(client: TestClient, seed: int, name: str) -> dict[str, Any]:
    res = client.post(
        "/api/cases/upload", files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    case_id = body["case"]["case_id"]
    analysed = client.post(f"/api/cases/{case_id}/analyse")
    assert analysed.status_code == 200, analysed.text
    return {
        "case_id": case_id,
        "evidence_id": body["evidence"]["evidence_id"],
        "analysis": analysed.json(),
    }


def _verification_count(client: TestClient) -> int:
    res = client.get(
        "/api/audit", params={"event": audit_service.EVENT_AUDIT_VERIFIED, "limit": 1}
    )
    assert res.status_code == 200
    return res.json()["total_rows"]


# --------------------------------------------------------------------------- #
# The envelope
# --------------------------------------------------------------------------- #
def test_alerts_publish_their_own_vocabulary(client: TestClient) -> None:
    data = _alerts(client)

    assert data["severities"] == list(alerts_api.SEVERITIES)
    assert data["categories"] == list(alerts_api.CATEGORIES)
    # Every severity and category the response can emit is defined in the same
    # response, so a client never has to infer what "medium" means.
    assert set(data["severity_definitions"]) == set(alerts_api.SEVERITIES)
    assert set(data["category_definitions"]) == set(alerts_api.CATEGORIES)
    assert set(data["severity_counts"]) == set(alerts_api.SEVERITIES)
    assert set(data["category_counts"]) == set(alerts_api.CATEGORIES)

    assert alerts_api.DERIVED_NOTE in data["notes"]
    assert data["generated_at"]
    assert data["filters"]["limit"] == 100
    assert data["filters"]["offset"] == 0

    for alert in data["alerts"]:
        # Nothing has to be taken on trust: each alert names its row and its basis.
        assert alert["source"]
        assert alert["severity"] in alerts_api.SEVERITIES
        assert alert["category"] in alerts_api.CATEGORIES


def test_alerts_are_sorted_most_serious_first(client: TestClient) -> None:
    data = _alerts(client, limit=500)
    ranks = [alerts_api.SEVERITY_RANK[a["severity"]] for a in data["alerts"]]
    assert ranks == sorted(ranks)


def test_counts_are_computed_before_filtering(client: TestClient) -> None:
    """The totals must not shift as the client filters or pages."""
    unfiltered = _alerts(client, limit=500)
    filtered = _alerts(client, category=alerts_api.CATEGORY_CAPABILITY, limit=500)

    assert filtered["severity_counts"] == unfiltered["severity_counts"]
    assert filtered["category_counts"] == unfiltered["category_counts"]
    # ``total`` is the size of the filtered selection, which is what paging walks.
    assert filtered["total"] == unfiltered["category_counts"][
        alerts_api.CATEGORY_CAPABILITY
    ]
    assert all(
        a["category"] == alerts_api.CATEGORY_CAPABILITY for a in filtered["alerts"]
    )


def test_paging_reports_whether_more_matches_exist(client: TestClient) -> None:
    everything = _alerts(client, limit=500)
    if everything["total"] < 2:
        return  # Nothing to page through on an empty deployment.

    first = _alerts(client, limit=1)
    assert first["count"] == 1
    assert first["truncated"] is True
    assert first["offset"] == 0

    second = _alerts(client, limit=1, offset=1)
    assert second["alerts"][0]["alert_id"] != first["alerts"][0]["alert_id"]


# --------------------------------------------------------------------------- #
# Derived from stored analysis
# --------------------------------------------------------------------------- #
def test_verdict_alert_follows_the_stored_verdict_token(client: TestClient) -> None:
    """Severity is decided by fusion's verdict, never by re-scoring it here."""
    case = _analysed_case(client, 43001, "alert-verdict.jpg")
    stored = client.get(f"/api/cases/{case['case_id']}/verdict").json()["items"][0]

    data = _alerts(client, case_id=case["case_id"], limit=500)
    by_category = {a["category"]: a for a in data["alerts"]}

    if stored["verdict"] == fusion_service.VERDICT_MANIPULATED:
        alert = by_category[alerts_api.CATEGORY_VERDICT]
        assert alert["severity"] == alerts_api.SEVERITY_HIGH
    elif stored["verdict"] == fusion_service.VERDICT_INSUFFICIENT:
        alert = by_category[alerts_api.CATEGORY_COVERAGE]
        # "We could not tell" is deliberately not raised at the same severity as
        # "we found something".
        assert alert["severity"] == alerts_api.SEVERITY_LOW
        assert "says nothing about whether the media is authentic" in alert["detail"]
    else:
        assert alerts_api.CATEGORY_VERDICT not in by_category
        assert alerts_api.CATEGORY_COVERAGE not in by_category
        return

    assert alert["evidence_id"] == case["evidence_id"]
    assert alert["case_id"] == case["case_id"]
    assert alert["source"].startswith("analysis_results/")
    # The basis is the stored figures, so the alert can be checked rather than
    # trusted.
    assert alert["basis"]["verdict"] == stored["verdict"]
    assert alert["basis"]["manipulation_score"] == stored["manipulation_score"]
    assert alert["basis"]["signal_coverage"] == stored["signal_coverage"]
    assert alert["basis"]["signals_available"] == stored["signals_available"]


def test_absent_c2pa_manifest_raises_no_alert(client: TestClient) -> None:
    case = _analysed_case(client, 43002, "alert-provenance.jpg")
    provenance = client.get(f"/api/cases/{case['case_id']}/provenance").json()
    assert provenance["items"][0]["provenance"]["state"] == "ABSENT"

    data = _alerts(client, case_id=case["case_id"], limit=500)
    assert not [
        a for a in data["alerts"] if a["category"] == alerts_api.CATEGORY_PROVENANCE
    ]


def test_near_duplicate_alerts_are_worded_as_candidates(client: TestClient) -> None:
    """One image copied twice is one thing to look at, not three alerts."""
    original = jpeg_bytes(seed=43003)
    first = client.post(
        "/api/cases/upload", files={"file": ("alert-dup-a.jpg", original, "image/jpeg")}
    )
    assert first.status_code == 201
    case_id = first.json()["case"]["case_id"]
    query_id = first.json()["evidence"]["evidence_id"]

    # Two near-identical copies in the index give the query strong candidates.
    for index, seed_bytes in enumerate(
        (jpeg_bytes(seed=43003, quality=55), jpeg_bytes(seed=43003, quality=35))
    ):
        ingested = client.post(
            "/api/index/ingest",
            files={"file": (f"alert-dup-copy-{index}.jpg", seed_bytes, "image/jpeg")},
        )
        assert ingested.status_code in (200, 201), ingested.text

    assert client.post(f"/api/cases/{case_id}/matches").status_code == 200
    stored = client.get(f"/api/cases/{case_id}/matches").json()
    strong = stored["queries"][0]["strong_candidates"]

    alerts = [
        a
        for a in _alerts(client, case_id=case_id, limit=500)["alerts"]
        if a["category"] == alerts_api.CATEGORY_NEAR_DUPLICATE
    ]
    if not strong:
        assert alerts == []
        return

    assert len(alerts) == 1  # Aggregated per query item, not per pair.
    alert = alerts[0]
    assert alert["alert_id"] == f"near-duplicate:{query_id}"
    assert alert["evidence_id"] == query_id
    assert alert["severity"] == alerts_api.SEVERITY_MEDIUM
    assert alert["basis"]["candidate_count"] == strong
    assert "candidates for comparison, not proof" in alert["detail"]
    assert "candidate" in alert["title"]


# --------------------------------------------------------------------------- #
# Capability and audit
# --------------------------------------------------------------------------- #
def test_capability_alerts_state_what_bounds_every_conclusion(
    client: TestClient,
) -> None:
    detector = client.get("/api/detector/status").json()
    data = _alerts(client, category=alerts_api.CATEGORY_CAPABILITY, limit=500)
    by_id = {a["alert_id"]: a for a in data["alerts"]}

    if not detector["available"]:
        alert = by_id["capability:detector-unavailable"]
        assert alert["severity"] == alerts_api.SEVERITY_MEDIUM
        assert alert["case_id"] is None  # A deployment limit, not a case finding.
        assert "not a finding about any file" in alert["detail"]
        assert set(alert["basis"]["modalities"]) == {"image", "video", "audio"}
    else:
        assert "capability:detector-unavailable" not in by_id


def test_reading_alerts_never_verifies_the_audit_chain(client: TestClient) -> None:
    before = _verification_count(client)
    for _ in range(3):
        _alerts(client, limit=500)
    assert _verification_count(client) == before


def test_unverified_chain_is_reported_as_unverified_not_broken(
    client: TestClient,
) -> None:
    # Make sure the chain has rows to talk about.
    seeded = client.post(
        "/api/cases/upload",
        files={"file": ("alert-audit.jpg", jpeg_bytes(seed=43004), "image/jpeg")},
    )
    assert seeded.status_code == 201

    if _verification_count(client) == 0:
        never = [
            a
            for a in _alerts(client, limit=500)["alerts"]
            if a["alert_id"] == "audit:never-verified"
        ]
        assert len(never) == 1
        assert never[0]["severity"] == alerts_api.SEVERITY_LOW
        assert never[0]["detail"] == alerts_api.AUDIT_NEVER_VERIFIED
        assert never[0]["basis"]["total_rows"] > 0

    verified = client.post("/api/audit/verify")
    assert verified.status_code == 200
    assert verified.json()["valid"] is True

    audit_alerts = [
        a
        for a in _alerts(client, limit=500)["alerts"]
        if a["category"] == alerts_api.CATEGORY_AUDIT
    ]
    # A chain that verifies produces no audit alert at all -- neither a warning
    # that it is unverified nor a critical failure.
    assert audit_alerts == []
