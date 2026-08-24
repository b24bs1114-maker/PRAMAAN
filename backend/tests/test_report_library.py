"""The report library: what was rendered, and the digest of what was rendered.

``GET /api/reports`` was added because reports are generated per case and the
reports page needs a list across cases. It is a read: no report is re-rendered by
listing it, so a hash shown here is the hash of the bytes on disk and can be
checked by downloading them.

Both views -- the per-case list and the cross-case library -- are built by the
same row serialiser, and one test exists purely to keep them from drifting apart.
A report has to look the same wherever it is read, because the pairing that makes
it verifiable is per row: the PDF's own SHA-256 (a document cannot contain its own
digest, so the chain records it) and the audit head hash that was current when it
was rendered.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi.testclient import TestClient

from app.services import audit as audit_service
from app.services import report as report_service
from tests.helpers import jpeg_bytes


def _library(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/reports", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _case(client: TestClient, seed: int, name: str) -> str:
    res = client.post(
        "/api/cases/upload", files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")}
    )
    assert res.status_code == 201, res.text
    return res.json()["case"]["case_id"]


def _generate(client: TestClient, case_id: str, **body: Any) -> dict[str, Any]:
    res = client.post(f"/api/cases/{case_id}/report", json=body or None)
    assert res.status_code == 201, res.text
    return res.json()


def _generated_count(client: TestClient) -> int:
    res = client.get(
        "/api/audit",
        params={"event": audit_service.EVENT_REPORT_GENERATED, "limit": 1},
    )
    assert res.status_code == 200
    return res.json()["total_rows"]


# --------------------------------------------------------------------------- #
# The row
# --------------------------------------------------------------------------- #
def test_library_row_carries_the_digest_and_the_chain_head(
    client: TestClient,
) -> None:
    case_id = _case(client, 46001, "report-row.jpg")
    generated = _generate(client, case_id, examiner="A. Examiner")

    data = _library(client, case_id=case_id)
    assert data["total"] == 1
    assert data["count"] == 1
    row = data["reports"][0]

    assert row["report_id"] == generated["report_id"]
    assert row["case_id"] == case_id
    assert row["filename"] == generated["filename"]
    assert row["size_bytes"] == generated["size_bytes"]
    assert row["pages"] == generated["pages"]
    assert row["renderer"] == generated["renderer"]
    assert row["generator"] == generated["generator"]
    # The two figures that let a reader tie the document to the record.
    assert row["sha256"] == generated["sha256"]
    assert row["audit_head_hash"] == generated["audit_head_hash"]
    assert row["audit_chain_valid"] == generated["audit_chain_valid"]
    assert row["download_url"] == generated["download_url"]
    # The case identity is joined in, so the page needs no request per row.
    assert row["case_number"]
    assert row["case_title"] is None or isinstance(row["case_title"], str)

    assert data["document_status"] == report_service.DOCUMENT_STATUS
    assert data["renderer"]["writer"]
    assert any("cannot contain its own digest" in note for note in data["notes"])


def test_downloaded_bytes_hash_to_the_recorded_digest(client: TestClient) -> None:
    """The listed hash is only worth listing if the stored bytes still produce it."""
    case_id = _case(client, 46002, "report-download.jpg")
    generated = _generate(client, case_id)

    row = _library(client, case_id=case_id)["reports"][0]
    res = client.get(row["download_url"])
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
    assert hashlib.sha256(res.content).hexdigest() == row["sha256"]
    assert res.headers["x-pramaan-report-sha256"] == row["sha256"]
    assert res.headers["content-type"] == "application/pdf"
    assert len(res.content) == row["size_bytes"] == generated["size_bytes"]


def test_the_digest_is_recorded_in_the_chain_not_in_the_document(
    client: TestClient,
) -> None:
    case_id = _case(client, 46003, "report-chain.jpg")
    generated = _generate(client, case_id)

    recorded = client.get(
        f"/api/cases/{case_id}/audit",
        params={},
    ).json()["events"]
    entry = next(
        row
        for row in recorded
        if row["event"] == audit_service.EVENT_REPORT_GENERATED
        and row["details"].get("report_id") == generated["report_id"]
    )
    assert entry["details"]["sha256"] == generated["sha256"]


# --------------------------------------------------------------------------- #
# One serialiser, two views
# --------------------------------------------------------------------------- #
def test_library_row_is_identical_to_the_per_case_row(client: TestClient) -> None:
    case_id = _case(client, 46004, "report-views.jpg")
    _generate(client, case_id)

    library = _library(client, case_id=case_id)["reports"]
    per_case = client.get(f"/api/cases/{case_id}/reports").json()

    assert per_case["case_id"] == case_id
    assert per_case["count"] == len(library) == 1
    # Not "equivalent" -- equal. Both views are built by the same serialiser.
    assert per_case["reports"] == library


def test_case_filter_is_a_view_over_the_same_reports(client: TestClient) -> None:
    case_id = _case(client, 46005, "report-filter.jpg")
    generated = _generate(client, case_id)

    everything = _library(client, limit=500)
    assert everything["total"] >= 1
    assert generated["report_id"] in {r["report_id"] for r in everything["reports"]}

    scoped = _library(client, case_id=case_id, limit=500)
    assert scoped["total"] <= everything["total"]
    assert all(row["case_id"] == case_id for row in scoped["reports"])


# --------------------------------------------------------------------------- #
# Honest empty state
# --------------------------------------------------------------------------- #
def test_a_case_with_no_report_is_an_empty_list_not_a_placeholder(
    client: TestClient,
) -> None:
    case_id = _case(client, 46006, "report-none.jpg")

    data = _library(client, case_id=case_id)
    assert data["total"] == 0
    assert data["count"] == 0
    assert data["reports"] == []
    assert data["truncated"] is False
    # The empty state says which question was asked, so "none for this case" is
    # never read as "none on this deployment".
    assert any("for this case yet" in note for note in data["notes"])

    per_case = client.get(f"/api/cases/{case_id}/reports").json()
    assert per_case["count"] == 0
    assert per_case["reports"] == []


def test_unknown_case_filter_is_refused_or_empty_never_invented(
    client: TestClient,
) -> None:
    """A filter for a case that does not exist yields no rows, not other cases'."""
    data = _library(client, case_id="no-such-case-id")
    assert data["total"] == 0
    assert data["reports"] == []


# --------------------------------------------------------------------------- #
# Paging and read-only
# --------------------------------------------------------------------------- #
def test_paging_walks_the_library_newest_first(client: TestClient) -> None:
    case_id = _case(client, 46007, "report-paging.jpg")
    first = _generate(client, case_id)
    second = _generate(client, case_id)
    assert first["report_id"] != second["report_id"]

    everything = _library(client, case_id=case_id, limit=500)
    assert everything["total"] == 2
    stamps = [row["generated_at"] for row in everything["reports"]]
    assert stamps == sorted(stamps, reverse=True)  # Newest first.
    assert {row["report_id"] for row in everything["reports"]} == {
        first["report_id"],
        second["report_id"],
    }

    page = _library(client, case_id=case_id, limit=1)
    assert page["count"] == 1
    assert page["total"] == 2
    assert page["truncated"] is True
    assert page["reports"][0] == everything["reports"][0]

    tail = _library(client, case_id=case_id, limit=1, offset=1)
    assert tail["offset"] == 1
    assert tail["truncated"] is False
    assert tail["reports"][0] == everything["reports"][1]

    # Each report is its own document: same case, different bytes, different hash.
    assert first["sha256"] != second["sha256"] or first["size_bytes"] == second[
        "size_bytes"
    ]


def test_listing_reports_renders_nothing(client: TestClient) -> None:
    case_id = _case(client, 46008, "report-readonly.jpg")
    _generate(client, case_id)

    before = _generated_count(client)
    for _ in range(3):
        _library(client, limit=500)
        assert client.get(f"/api/cases/{case_id}/reports").status_code == 200
    # Generation audits itself, so an unchanged count is proof none happened.
    assert _generated_count(client) == before


def test_downloading_a_missing_report_is_a_404_in_the_error_envelope(
    client: TestClient,
) -> None:
    case_id = _case(client, 46009, "report-404.jpg")
    res = client.get(f"/api/cases/{case_id}/reports/no-such-report-id")
    assert res.status_code == 404
    body = res.json()
    assert "error" in body and "detail" not in body
    assert body["request_id"]
