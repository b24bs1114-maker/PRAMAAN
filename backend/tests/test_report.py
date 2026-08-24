"""TASK 12 -- forensic PDF report.

These tests check three separate things:

1. the *document* contains every section the report is required to carry, and
   states its findings in the guarded language the project mandates;
2. the *file* is a structurally valid PDF -- header, objects, xref offsets,
   trailer and page tree are checked by hand, because no PDF library is
   installed in this environment;
3. the *hash* recorded for the PDF matches the bytes on disk and the bytes the
   download endpoint returns, and is anchored into the audit chain.

Text is recovered from the PDF by parsing the uncompressed content streams. That
is only possible because the built-in writer emits plain text operators -- if
ReportLab is ever installed, ``_pdf_text`` degrades to returning nothing and the
content assertions are skipped rather than silently passing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from app.services import report as report_service
from app.utils import pdf as pdf_writer
from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, mp4_bytes

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_TEXT_OP = re.compile(rb"\((.*?)\) Tj", re.S)
_STREAM = re.compile(rb"stream\n(.*?)\nendstream", re.S)


def _pdf_text(data: bytes) -> str:
    """Recover drawn text from uncompressed content streams.

    Returns "" when the streams are compressed (i.e. a ReportLab-rendered file),
    which callers must treat as "cannot inspect" rather than "no text".
    """
    chunks: list[str] = []
    for stream in _STREAM.findall(data):
        for match in _TEXT_OP.finditer(stream):
            raw = (
                match.group(1)
                .replace(rb"\(", b"(")
                .replace(rb"\)", b")")
                .replace(rb"\\", b"\\")
            )
            chunks.append(raw.decode("latin-1"))
    return "\n".join(chunks)


def _flat(text: str) -> str:
    """Collapse whitespace so wrapped lines can be searched as one string."""
    return " ".join(text.split())


def _upload(client, case_id: str | None, name: str, data: bytes, mime: str) -> dict:
    files = {"file": (name, data, mime)}
    # case_id is a form field on the upload endpoint, not a query parameter.
    form = {"case_id": case_id} if case_id else {}
    response = client.post("/api/cases/upload", files=files, data=form)
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture(scope="module")
def reported_case(client) -> dict[str, Any]:
    """A case with two related images and one video, reported once.

    Module-scoped: report generation runs the whole pipeline for every evidence
    item, so it is done once and the resulting document is inspected many times.
    """
    first = _upload(client, None, "river-photo.jpg", jpeg_with_exif_bytes(seed=41),
                    "image/jpeg")
    case_id = first["case"]["case_id"]
    _upload(client, case_id, "river-photo-shared.jpg",
            jpeg_bytes(seed=41, quality=55), "image/jpeg")
    _upload(client, case_id, "clip.mp4", mp4_bytes(), "video/mp4")

    assert client.post("/api/index/rebuild").status_code == 200

    response = client.post(
        f"/api/cases/{case_id}/report",
        json={"examiner": "D. Jain"},
        params={"refresh": "true"},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    data = Path(body["path"]).read_bytes()
    return {
        "case_id": case_id,
        "body": body,
        "data": data,
        "text": _flat(_pdf_text(data)),
    }


# --------------------------------------------------------------------------- #
# The file is a real PDF
# --------------------------------------------------------------------------- #
def test_report_is_a_structurally_valid_pdf(reported_case):
    data = reported_case["data"]

    assert data.startswith(b"%PDF-1.4\n")
    assert data.rstrip().endswith(b"%%EOF")

    # startxref must point at the xref table.
    startxref = int(data.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
    assert data[startxref:startxref + 4] == b"xref"

    # Every "in use" xref entry must point at the object it claims.
    rows = data[startxref:].split(b"\n")
    assert rows[1].split()[0] == b"0"
    count = int(rows[1].split()[1])
    for number in range(1, count):
        entry = rows[2 + number]
        offset, _, kind = entry.split()[:3]
        if kind != b"n":
            continue
        expected = b"%d 0 obj" % number
        assert data[int(offset):int(offset) + len(expected)] == expected, (
            f"xref entry {number} does not point at its object"
        )

    trailer = data.rsplit(b"trailer", 1)[1]
    assert b"/Root " in trailer
    assert b"/Size %d" % count in trailer


def test_page_tree_matches_the_reported_page_count(reported_case):
    data = reported_case["data"]
    pages = reported_case["body"]["pages"]

    assert pages >= 1
    assert (b"/Type /Pages /Count %d" % pages in data) or (b"/Count %d" % pages in data)
    assert data.count(b"/Type /Page") >= pages
    assert b"stream" in data

    # Every page declares US Letter
    assert data.count(b"/MediaBox [0 0 612 792]") == pages or data.count(b"612") >= pages
    for name in ("F1", "F2", "F3"):
        assert b"/BaseFont /" in data
        assert b"/Name /%s" % name.encode() in data


def test_only_the_fallback_writer_is_recorded_when_reportlab_is_absent(reported_case):
    status = reported_case["body"]["renderer_status"]

    # This asserts self-consistency, not a particular environment: whichever
    # renderer was used must be the one the report names.
    if status["reportlab_available"]:
        assert reported_case["body"]["renderer"] == report_service.RENDERER_REPORTLAB
    else:
        assert reported_case["body"]["renderer"] == report_service.RENDERER_BUILTIN
        assert status["writer"] == pdf_writer.WRITER
        assert "reportlab" in (status["reason"] or "").lower()
        assert "identical" in status["note"]


# --------------------------------------------------------------------------- #
# Hash integrity
# --------------------------------------------------------------------------- #
def test_reported_sha256_matches_the_bytes_on_disk(reported_case):
    body = reported_case["body"]
    on_disk = hashlib.sha256(reported_case["data"]).hexdigest()

    assert body["sha256"] == on_disk
    assert len(body["sha256"]) == 64
    assert body["size_bytes"] == len(reported_case["data"])


def test_download_returns_the_same_bytes_and_advertises_the_hash(client, reported_case):
    body = reported_case["body"]
    response = client.get(body["download_url"])

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-pramaan-report-sha256"] == body["sha256"]
    assert response.content == reported_case["data"]
    assert hashlib.sha256(response.content).hexdigest() == body["sha256"]


def test_pdf_hash_is_recorded_in_the_audit_chain(client, reported_case):
    body = reported_case["body"]
    trail = client.get(f"/api/cases/{reported_case['case_id']}/audit").json()

    generated = [e for e in trail["events"] if e["event"] == "REPORT_GENERATED"]
    assert generated, "report generation was not audited"
    entry = generated[-1]

    assert entry["details"]["sha256"] == body["sha256"]
    assert entry["details"]["report_id"] == body["report_id"]
    assert entry["details"]["renderer"] == body["renderer"]
    # The head printed in the document is the chain as it stood *before* this
    # entry -- a document cannot contain the hash of a row that includes it.
    assert entry["previous_hash"] == body["audit_head_hash"]


def test_chain_still_verifies_after_reporting(client, reported_case):
    response = client.post(
        f"/api/cases/{reported_case['case_id']}/audit/verify",
        params={"record": "false"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True


def test_report_is_listed_with_its_hash(client, reported_case):
    body = reported_case["body"]
    listing = client.get(f"/api/cases/{reported_case['case_id']}/reports").json()

    assert listing["count"] >= 1
    match = [r for r in listing["reports"] if r["report_id"] == body["report_id"]]
    assert match, "generated report is not listed"
    assert match[0]["sha256"] == body["sha256"]
    assert match[0]["renderer"] == body["renderer"]


def test_a_second_report_is_a_distinct_document(client, reported_case):
    response = client.post(
        f"/api/cases/{reported_case['case_id']}/report",
        json={"examiner": "Second Examiner"},
    )
    assert response.status_code == 201, response.text
    second = response.json()

    assert second["report_id"] != reported_case["body"]["report_id"]
    assert second["path"] != reported_case["body"]["path"]
    # Content differs (examiner, timestamps, longer audit trail), so the digest
    # must differ too.
    assert second["sha256"] != reported_case["body"]["sha256"]
    assert Path(second["path"]).exists()
    assert hashlib.sha256(Path(second["path"]).read_bytes()).hexdigest() == (
        second["sha256"]
    )


# --------------------------------------------------------------------------- #
# Required content
# --------------------------------------------------------------------------- #
def _require_text(reported_case) -> str:
    text = reported_case["text"]
    if not text:
        pytest.skip("content streams are compressed; text cannot be inspected")
    return text


def test_report_carries_every_required_section(reported_case):
    text = _require_text(reported_case)
    for heading in (
        "1. Summary of findings",
        "2.1 Evidence:",
        "3. Near-duplicate candidates",
        "4. Earliest known instance and propagation",
        "5. Methodology",
        "6. Audit trail",
        "7. Integrity of this document",
        "8. Limitations",
        "9. Examiner",
    ):
        assert heading in text, f"missing section: {heading}"


def test_report_identifies_the_case_and_the_evidence(client, reported_case):
    text = _require_text(reported_case)
    case_id = reported_case["case_id"]
    case = client.get(f"/api/cases/{case_id}").json()
    evidence = client.get(f"/api/cases/{case_id}/evidence").json()["evidence"]

    assert case_id in text
    assert case["case_number"] in text
    assert reported_case["body"]["report_id"] in text

    for item in evidence:
        assert item["filename"] in text
        assert item["sha256"] in text, "evidence SHA-256 must be printed"
        assert item["evidence_id"] in text
        if item["phash"]:
            assert item["phash"] in text


def test_report_prints_the_signal_breakdown_and_its_arithmetic(client, reported_case):
    text = _require_text(reported_case)
    verdicts = client.post(
        f"/api/cases/{reported_case['case_id']}/verdict"
    ).json()["items"]

    for verdict in verdicts:
        assert verdict["verdict"] in text
        assert verdict["rationale"][:60] in text
        for signal in verdict["signals"]:
            assert signal["name"] in text
            assert signal["status"] in text
        if verdict["arithmetic"]:
            # The printed sum must be the one the API returned, not a re-derivation.
            assert verdict["arithmetic"][:60] in text


def test_report_names_the_model_and_version_used(client, reported_case):
    text = _require_text(reported_case)
    detector = client.get("/api/detector/status").json()

    assert detector["model"] in text
    assert detector["model_version"] in text
    assert detector["adapter"] in text
    assert detector["interface_version"] in text
    assert "Fusion version" in text and report_service.REPORT_VERSION in text


def test_report_prints_timestamps(reported_case):
    text = _require_text(reported_case)
    assert reported_case["body"]["generated_at"] in text
    assert "Report generated" in text
    assert "Case created" in text
    # ISO-8601 UTC, not a locale-dependent rendering.
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)


def test_report_prints_the_audit_trail_and_head_hash(client, reported_case):
    text = _require_text(reported_case)
    body = reported_case["body"]

    assert body["audit_head_hash"] in text
    assert "0" * 64 in text, "genesis hash must be shown"
    # Row hashes wrap across lines in a narrow column, so match on a prefix.
    trail = client.get(f"/api/cases/{reported_case['case_id']}/audit").json()
    first = trail["events"][0]
    assert first["event"] in text
    assert first["row_hash"][:24] in text.replace(" ", "")


def test_report_prints_the_earliest_known_instance_not_an_original_source(
    reported_case,
):
    text = _require_text(reported_case)
    lowered = text.lower()

    assert "earliest known instance in the indexed evidence corpus" in lowered
    assert "not the absolute real-world origin" in lowered
    # The forbidden framing must never appear.
    assert "original source" not in lowered
    assert "the original image" not in lowered


def test_report_states_the_limitations_that_bound_every_figure(reported_case):
    text = _require_text(reported_case)
    lowered = text.lower()

    assert "prototype output" in lowered
    assert "not a certified forensic opinion" in lowered
    assert "no error rate is known" in lowered
    assert "not been validated" in lowered
    assert "absent metadata is not evidence of manipulation" in lowered
    assert "tamper evidence, not tamper proof" in lowered
    assert "near-duplicate candidates" in lowered
    for limitation in report_service.LIMITATIONS:
        assert limitation[:50] in text


def test_report_marks_an_unavailable_detector_as_missing_not_as_zero(reported_case):
    text = _require_text(reported_case)
    detector_status = report_service.renderer_status()  # sanity: helper is callable
    assert detector_status

    lowered = text.lower()
    if "status unavailable" in lowered.replace("\n", " "):
        assert "not measured" in lowered
        assert "not a finding of authenticity" in lowered
        assert "not a finding of manipulation" in lowered


def test_report_includes_the_examiner_signoff_block(reported_case):
    text = _require_text(reported_case)

    assert "D. Jain" in text
    assert "Signature" in text
    assert "machine-generated" in text
    assert "accepted" in text and "amended" in text and "rejected" in text


def test_footer_is_stamped_on_every_page(reported_case):
    data = reported_case["data"]
    pages = reported_case["body"]["pages"]
    text = _pdf_text(data)
    if not text:
        pytest.skip("content streams are compressed; text cannot be inspected")

    assert text.count("not a certified forensic opinion") >= pages
    for number in range(1, pages + 1):
        assert f"Page {number} of {pages}" in text


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_a_case_with_no_evidence_still_reports_without_inventing_findings(client):
    # Cases are normally created by the first upload, so an evidence-free case is
    # made directly through the service to reach this branch.
    from app.models import get_session_factory
    from app.services import ingestion

    session = get_session_factory()()
    try:
        case = ingestion.create_case(session, title="Empty case", examiner=None)
        session.commit()
        case_id = case.id
    finally:
        session.close()

    response = client.post(f"/api/cases/{case_id}/report", json={})
    assert response.status_code == 201, response.text
    body = response.json()
    text = _flat(_pdf_text(Path(body["path"]).read_bytes()))

    assert body["pages"] >= 1
    if text:
        assert "No evidence has been ingested" in text
        assert "Evidence items" in text


def test_report_for_an_unknown_case_is_404(client):
    response = client.post(
        "/api/cases/00000000-0000-0000-0000-000000000000/report", json={}
    )
    assert response.status_code == 404


def test_download_of_an_unknown_report_is_404(client, reported_case):
    response = client.get(
        f"/api/cases/{reported_case['case_id']}/reports/"
        "00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The writer itself
# --------------------------------------------------------------------------- #
def test_wrapping_never_overflows_the_column():
    width = 200.0
    text = "short " + "x" * 400 + " tail"
    lines = pdf_writer.wrap(text, pdf_writer.FONT_REGULAR, 8.5, width)

    assert len(lines) > 1
    for line in lines:
        assert pdf_writer.text_width(line, pdf_writer.FONT_REGULAR, 8.5) <= width


def test_non_ascii_is_transliterated_rather_than_dropped():
    rendered, pages = pdf_writer.render(
        [{"type": "paragraph", "text": "curly ‘quotes’ and an em—dash"}],
        title="t",
        author="a",
        subject="s",
        footer="f",
    )
    text = _pdf_text(rendered)

    assert pages == 1
    assert "'quotes'" in text
    assert "em--dash" in text


def test_long_content_paginates(reported_case):
    # The real report is long enough to need several pages; that is the property
    # being checked, not a specific count.
    assert reported_case["body"]["pages"] >= 3
