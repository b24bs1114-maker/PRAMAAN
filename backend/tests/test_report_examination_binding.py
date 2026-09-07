"""Report consistency: the report consumes the finalized examination.

P0 invariant, report side: the PDF and the API must refer to the same finalized
examination snapshot. Generating a report must not rerun detector inference,
fusion or provenance analysis, and must not mutate any stored examination --
a report is a read over the record, not another examination.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.models import (
    KIND_DETECTOR,
    KIND_FUSION,
    AnalysisResult,
    get_session_factory,
)
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
    return {
        "case_id": body["case"]["case_id"],
        "evidence_id": body["evidence"]["evidence_id"],
    }


def _rows(evidence_id: str, kind: str) -> list[AnalysisResult]:
    session = get_session_factory()()
    try:
        return list(
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == kind,
            )
            .order_by(AnalysisResult.created_at)
            .all()
        )
    finally:
        session.close()


def _row_fingerprint(rows: list[AnalysisResult]) -> list[tuple]:
    """Content identity of analysis rows, independent of ORM session identity.

    Two reads of the same rows through two different sessions produce distinct
    ORM objects; comparing the objects directly would compare identity, not
    content, and always differ. The id, timestamps, score and payload are the
    row.
    """
    return [
        (
            row.id,
            row.created_at,
            row.status,
            row.score,
            row.verdict,
            row.payload,
        )
        for row in rows
    ]


def _pdf_text(data: bytes) -> str:
    """Extract text from the PDF for substring assertions."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", _DECODE_SCRIPT, str(len(data))],
        input=data,
        capture_output=True,
    )
    if proc.returncode != 0:
        # No text extraction available; tests relying on it should be skipped.
        return ""
    return proc.stdout.decode("utf-8", "replace")


_DECODE_SCRIPT = (
    "import sys, zlib\n"
    "data = sys.stdin.buffer.read()\n"
    "out = []\n"
    "i = 0\n"
    "while True:\n"
    "    i = data.find(b'stream', i)\n"
    "    if i < 0: break\n"
    "    start = i + 6\n"
    "    if data[start:start+2] == b'\\r\\n': start += 2\n"
    "    elif data[start:start+1] in (b'\\n', b'\\r'): start += 1\n"
    "    end = data.find(b'endstream', start)\n"
    "    if end < 0: break\n"
    "    try:\n"
    "        out.append(zlib.decompress(data[start:end]))\n"
    "    except Exception:\n"
    "        pass\n"
    "    i = end\n"
    "sys.stdout.write(b'\\n'.join(out).decode('latin-1'))\n"
)


# --------------------------------------------------------------------------- #
# The report is a read over finalized examinations
# --------------------------------------------------------------------------- #
def test_report_generation_does_not_run_new_analysis(client: TestClient) -> None:
    """A report generated straight after an examination adds no analysis rows.

    The report must consume the stored finalized examination. If generating it
    ran the pipeline, the PDF's findings could differ from what /verdict
    returned moments before -- and a stage rerun would overwrite the
    examination the report is supposed to snapshot.
    """
    ids = _open_case(client, "no-rerun.jpg", jpeg_bytes(seed=301))
    case_id = ids["case_id"]
    evidence_id = ids["evidence_id"]

    client.post(f"/api/cases/{case_id}/verdict")

    before_fusion = _rows(evidence_id, KIND_FUSION)
    before_detector = _rows(evidence_id, KIND_DETECTOR)
    assert before_fusion, "precondition: an examination exists"

    response = client.post(f"/api/cases/{case_id}/report", json={"examiner": None})
    assert response.status_code == 201, response.text

    assert _row_fingerprint(_rows(evidence_id, KIND_FUSION)) == _row_fingerprint(
        before_fusion
    ), "report generation changed the stored examination"
    assert _row_fingerprint(_rows(evidence_id, KIND_DETECTOR)) == _row_fingerprint(
        before_detector
    ), "report generation ran the detector"


def test_report_generation_cannot_be_asked_to_rerun_analysis(
    client: TestClient,
) -> None:
    """refresh=true on report generation is refused, with the alternative named.

    A report action that also re-examines makes 'the report describes the
    examination' false by construction: it creates a new examination and then
    describes that one, leaving the operator's held verdicts stale. Re-examine
    first, then report.
    """
    case_id = _open_case(client, "no-refresh.jpg", jpeg_bytes(seed=302))["case_id"]
    client.post(f"/api/cases/{case_id}/verdict")

    response = client.post(
        f"/api/cases/{case_id}/report",
        json={"examiner": None},
        params={"refresh": "true"},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    detail = str(body.get("error", {}).get("message") or body)
    assert "analyse" in detail.lower() or "verdict" in detail.lower(), (
        "the refusal must name the endpoint that re-examines"
    )


def test_report_refers_to_the_same_examination_as_the_api(client: TestClient) -> None:
    """The stored report's payload names the examination it rendered.

    API RESULT = FINALIZED EXAMINATION = PDF. The report row must carry the
    examination id it snapshot, so a later re-examination cannot make it
    ambiguous which examination the PDF describes.
    """
    ids = _open_case(client, "same-exam.jpg", jpeg_bytes(seed=303))
    case_id = ids["case_id"]

    verdict = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]
    report = client.post(f"/api/cases/{case_id}/report", json={"examiner": None})
    assert report.status_code == 201, report.text
    body = report.json()

    assert body["examination_id"] == verdict["examination_id"], (
        "the report must name the same examination the verdict endpoint returned"
    )

    # And the report payload persists the binding per exhibit.
    listing = client.get(f"/api/cases/{case_id}/reports").json()["reports"]
    assert listing, "the generated report must be listed"


# --------------------------------------------------------------------------- #
# Re-examination does not disturb an existing report
# --------------------------------------------------------------------------- #
def test_old_report_still_refers_to_the_old_examination(client: TestClient) -> None:
    """After a re-examination, the first report describes the first examination.

    The stored PDF bytes never change (their digest is on record), and the
    examination it snapshotted remains identifiable and intact.
    """
    ids = _open_case(client, "old-report.jpg", jpeg_bytes(seed=304))
    case_id = ids["case_id"]
    evidence_id = ids["evidence_id"]

    first_verdict = client.post(f"/api/cases/{case_id}/verdict").json()["items"][0]
    first_report = client.post(
        f"/api/cases/{case_id}/report", json={"examiner": "First"}
    ).json()
    first_bytes = Path(first_report["path"]).read_bytes()

    client.post(f"/api/cases/{case_id}/verdict", params={"refresh": "true"})

    # The PDF on disk is untouched: same bytes, same recorded digest.
    assert Path(first_report["path"]).read_bytes() == first_bytes
    listing = client.get(f"/api/cases/{case_id}/reports").json()["reports"]
    match = [r for r in listing if r["report_id"] == first_report["report_id"]]
    assert match and match[0]["sha256"] == first_report["sha256"]

    # The examination the report named still exists, unchanged.
    rows = _rows(evidence_id, KIND_FUSION)
    assert any(
        (r.payload or {}).get("examination_id") == first_verdict["examination_id"]
        for r in rows
    ), "the examination the first report described must survive the re-examination"
    first_row = next(
        r for r in rows
        if (r.payload or {}).get("examination_id") == first_verdict["examination_id"]
    )
    assert (
        first_row.payload.get("examination_digest")
        == first_verdict["examination_digest"]
    )
    assert first_row.payload.get("manipulation_score") == first_verdict[
        "manipulation_score"
    ]
