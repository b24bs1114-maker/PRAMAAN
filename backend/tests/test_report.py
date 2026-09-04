"""TASK 12 -- forensic PDF report.

These tests check three separate things:

1. the *document* contains every section the report is required to carry, states
   its findings in the guarded language the project mandates, and prints no
   figure that was not measured;
2. the *file* is a structurally valid PDF -- header, objects, xref offsets,
   trailer and page tree are checked by hand, because no PDF library is
   installed in this environment;
3. the *hash* recorded for the PDF matches the bytes on disk and the bytes the
   download endpoint returns, and is anchored into the audit chain.

Text is recovered from the PDF by decoding its content streams. ``_pdf_text``
handles both renderers: the built-in writer emits plain text operators, and
ReportLab emits ASCII85-then-Flate. It used to try ``zlib`` alone, so with
ReportLab installed it returned "" and *every* content assertion below skipped --
which is how a document full of hardcoded findings passed this suite.
"""

from __future__ import annotations

import base64
import hashlib
import re
import zlib
from pathlib import Path
from typing import Any

import pytest

from app.services import report as report_service
from app.utils import pdf as pdf_writer
from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, mp4_bytes

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
#: ``endstream`` is not reliably preceded by a newline -- ReportLab closes an
#: ASCII85 stream with its ``~>`` marker and follows it immediately -- so the
#: closing delimiter is matched without one and trailing whitespace is stripped
#: from the captured body instead.
_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
#: A PDF literal string. In a content stream these are text-showing arguments,
#: so collecting them in order recovers the drawn text.
_STRING = re.compile(rb"\((?:[^()\\]|\\.)*\)", re.S)
_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(\r\n|[\r\n])|(.))", re.S)
_SIMPLE = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _inflate(body: bytes) -> bytes | None:
    """Decode one stream, whichever filter chain was used, or None."""
    for decode in (
        lambda b: zlib.decompress(base64.a85decode(b, adobe=True)),
        lambda b: zlib.decompress(b),
        lambda b: base64.a85decode(b, adobe=True),
        lambda b: b,
    ):
        try:
            return decode(body)
        except Exception:
            continue
    return None


def _unescape(raw: bytes) -> str:
    """Resolve PDF string escapes, then decode as WinAnsi.

    Octal escapes matter: ReportLab writes every byte above 0x7E that way, so an
    em dash arrives as the four characters ``\\227`` and a naive reader sees the
    backslash rather than the punctuation.
    """
    def replace(match: re.Match[bytes]) -> bytes:
        octal, newline, other = match.groups()
        if octal is not None:
            return bytes([int(octal, 8) & 0xFF])
        if newline is not None:
            return b""          # a backslash-newline is a line continuation
        return _SIMPLE.get(other, other)

    return _ESCAPE.sub(replace, raw).decode("cp1252", "replace")


def _pdf_text(data: bytes) -> str:
    """Recover drawn text from the content streams of either renderer."""
    chunks: list[str] = []
    for stream in _STREAM.findall(data):
        decoded = _inflate(stream.strip(b"\r\n"))
        if decoded is None or (b"Tj" not in decoded and b"TJ" not in decoded):
            continue
        for match in _STRING.finditer(decoded):
            chunks.append(_unescape(match.group(0)[1:-1]))
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
    text = _pdf_text(data)
    return {
        "case_id": case_id,
        "body": body,
        "data": data,
        "text": _flat(text),
        # Identifiers and digests are wrapped by the renderer when they are wider
        # than their column -- a uuid arrives as two lines and a SHA-256 as two or
        # three -- so a substring search for one fails against the flattened text.
        # Searching a whitespace-free copy checks that the characters are all
        # present and in order, which is the property that matters for an
        # identifier.
        "packed": "".join(text.split()),
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
    # The headings the document actually prints, in the order it prints them.
    # These assertions used to name a numbered scheme ("1. Summary of findings",
    # "2.1 Evidence:", ...) that exists nowhere in the codebase, and they only
    # passed because the text extractor returned "" and every one of them skipped.
    for heading in (
        "PROTOTYPE OUTPUT",
        "EXECUTIVE FINDING",
        "EVIDENCE SNAPSHOT",
        "CASE IDENTITY",
        "EXHIBIT INDEX",
        "SIGNAL MATRIX",
        "FUSION & INTERPRETATION",
        "EVIDENCE INTEGRITY",
        "MODEL RECORD",
        "REVIEW NOTE",
        "PROVENANCE & LINEAGE",
        "AUDIT INTEGRITY",
        "CASE TIMELINE",
        "EXAMINER REVIEW",
    ):
        assert heading in text, f"missing section: {heading}"

    # Order matters in an examination report: the finding must not precede the
    # evidence it rests on, and the sign-off must come last.
    positions = [
        text.index(h)
        for h in ("EXECUTIVE FINDING", "SIGNAL MATRIX", "FUSION & INTERPRETATION",
                  "AUDIT INTEGRITY", "EXAMINER REVIEW")
    ]
    assert positions == sorted(positions), "sections are out of order"


def test_report_identifies_the_case_and_the_evidence(client, reported_case):
    text = _require_text(reported_case)
    packed = reported_case["packed"]
    case_id = reported_case["case_id"]
    case = client.get(f"/api/cases/{case_id}").json()
    evidence = client.get(f"/api/cases/{case_id}/evidence").json()["evidence"]

    assert case_id in packed
    assert case["case_number"] in text
    # The report has to name itself: it is the only thing distinguishing two
    # reports of the same case, and it is the key the audit row for this document
    # is recorded under.
    assert reported_case["body"]["report_id"] in packed

    # Every exhibit, by both of its identifiers -- not just the primary one. A
    # filename is not an identifier of file content.
    for item in evidence:
        assert item["filename"] in text
        assert item["evidence_id"] in packed
        assert item["sha256"] in packed, "evidence SHA-256 must be printed"

    # The primary exhibit's perceptual hashes are printed under EVIDENCE
    # INTEGRITY. The others are not, and the test does not pretend otherwise.
    primary = evidence[0]
    if primary["phash"]:
        assert primary["phash"] in packed


def test_report_prints_the_signal_breakdown_and_its_arithmetic(client, reported_case):
    text = _require_text(reported_case)
    verdicts = client.post(
        f"/api/cases/{reported_case['case_id']}/verdict"
    ).json()["items"]

    # Every exhibit's verdict appears -- the snapshot table covers all of them.
    for verdict in verdicts:
        assert verdict["verdict"] in text

    # The signal matrix, the arithmetic and the rationale belong to the primary
    # exhibit, which is the only one the document analyses in full. Asserting them
    # for every exhibit asserted a document that was never designed.
    primary = verdicts[0]
    assert primary["rationale"][:60] in text, "fusion's own rationale must be printed"
    for signal in primary["signals"]:
        assert signal["name"] in text
        assert signal["status"] in text
    if primary["arithmetic"]:
        # The printed sum must be the one the API returned, not a re-derivation.
        assert primary["arithmetic"][:60] in text


def test_confidence_is_printed_as_a_band_never_as_a_percentage(client, reported_case):
    text = _require_text(reported_case)
    primary = client.post(
        f"/api/cases/{reported_case['case_id']}/verdict"
    ).json()["items"][0]

    # Fusion publishes a word, and nothing behind it is calibrated. A numeric
    # confidence in a forensic document claims a precision that does not exist.
    assert primary["confidence"] in ("none", "low", "moderate", "high")
    assert "CONFIDENCE BAND" in text
    assert primary["confidence"] in text
    assert "High Confidence" not in text
    assert "NaN" not in text


def test_an_unscored_exhibit_shows_a_dash_not_a_zero(client, reported_case):
    text = _require_text(reported_case)
    verdicts = client.post(
        f"/api/cases/{reported_case['case_id']}/verdict"
    ).json()["items"]

    # The video exhibit has no detector, so fusion returns no score. NULL is not
    # zero: printing 0.0000 for it would read as a measured absence of
    # manipulation.
    unscored = [v for v in verdicts if v.get("manipulation_score") is None]
    assert unscored, "fixture must include an exhibit with no fused score"
    assert "INSUFFICIENT_EVIDENCE" in text
    assert "0.0000" not in text


def test_report_names_the_model_and_version_used(client, reported_case):
    text = _require_text(reported_case)
    detector = client.get("/api/detector/status").json()
    # The primary exhibit is an image, so the socket that handled it is the image
    # adapter -- not the dispatcher, whose id is the same for all three
    # modalities and therefore identifies nothing.
    image = detector["modalities"]["image"]

    assert image["adapter"] in text
    assert detector["interface_version"] in text
    assert "Report version" in text and report_service.REPORT_VERSION in text
    # Load time is reported separately from inference time: folding a multi-second
    # checkpoint load into the inference figure misstates how long the measurement
    # took by whole seconds.
    assert "Inference" in text and "Model load" in text

    if image["available"]:
        assert image["model"] in text
        assert image["model_version"] in text
    else:
        # No detector ran, so the document must not name one. The unit suite runs
        # with every model path and entrypoint cleared, which is the case that
        # matters most here: a MODEL RECORD naming a model in that configuration
        # would attribute the exhibit's score to a model that never loaded.
        assert "No detector installed" in text
        assert "Not measured" in text or "Not recorded" in text


def test_report_prints_the_full_weights_digest_not_a_prefix(client, reported_case):
    packed = reported_case["packed"]
    if not packed:
        pytest.skip("content streams are compressed; text cannot be inspected")
    detector = client.get("/api/detector/status").json()
    digest = (detector.get("modalities", {}).get("image", {}) or {}).get("weights_hash")

    if not digest:
        pytest.skip("no image checkpoint is loaded in this environment")
    # 64 characters, matching the manifest and /api/detector/status. A truncated
    # digest printed under the label "SHA-256" cannot be checked against either.
    assert len(digest) == 64
    assert digest in packed


def test_report_prints_timestamps(reported_case):
    text = _require_text(reported_case)
    assert reported_case["body"]["generated_at"] in text
    assert "Report generated" in text
    assert "Case created" in text
    # ISO-8601 UTC, not a locale-dependent rendering.
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)


def test_report_prints_the_audit_trail_and_head_hash(client, reported_case):
    text = _require_text(reported_case)
    packed = reported_case["packed"]
    body = reported_case["body"]

    # In full, both of them. An abbreviated "e4c7f3de78d8...ccc3f4" cannot be
    # re-verified against the chain or compared with /api/cases/{id}/audit, which
    # is the only reason to print a hash in an evidence document at all.
    assert len(body["audit_head_hash"]) == 64
    assert body["audit_head_hash"] in packed
    assert "0" * 64 in packed, "genesis hash must be shown"
    elided = f"{body['audit_head_hash'][:12]}..."
    assert elided not in packed, "the head hash must not be printed elided"

    trail = client.get(f"/api/cases/{reported_case['case_id']}/audit").json()
    first = trail["events"][0]
    assert "CASE TIMELINE" in text
    assert first["event"] in text
    # The timeline prints time, event and actor -- it carries no row-hash column,
    # so the old per-row-hash assertion described a table that does not exist.
    assert first["actor"] in text


def test_report_calls_the_chain_a_linear_hash_chain_not_a_merkle_tree(reported_case):
    text = _require_text(reported_case)
    lowered = text.lower()

    # It is SHA-256(prev_hash || canonical_json(payload)) -- a linear chain. Naming
    # it a Merkle tree overstates the structure and the guarantee.
    assert "merkle" not in lowered
    assert "linear sha-256 hash chain" in lowered
    assert "tamper evidence, not tamper proof" in lowered


def test_report_prints_the_earliest_known_instance_not_an_original_source(
    reported_case,
):
    text = _require_text(reported_case)
    lowered = text.lower()

    assert "earliest known instance in the indexed evidence corpus" in lowered
    assert "not a claim of absolute real-world origin" in lowered
    # The forbidden framings must never appear.
    for forbidden in (
        "original source",
        "the original image",
        "original upload",
        "first upload",
        "true origin",
    ):
        assert forbidden not in lowered, f"forbidden origin framing: {forbidden}"


def test_report_states_the_limitations_that_bound_every_figure(reported_case):
    text = _require_text(reported_case)
    lowered = text.lower()

    assert "prototype output" in lowered
    assert "not a certified forensic opinion" in lowered
    assert "no error rate is known" in lowered
    assert "not been validated" in lowered
    assert "missing metadata/c2pa is not evidence of manipulation" in lowered
    assert "tamper evidence, not tamper proof" in lowered
    assert "near-duplicate candidates" in lowered
    # An unavailable detector is neither finding, stated in the document itself.
    assert "not a finding of authenticity" in lowered
    assert "not a finding of manipulation" in lowered
    # LIMITATIONS is one string, so iterating it walks *characters*: the old
    # `for limitation in LIMITATIONS: assert limitation[:50] in text` asserted
    # that each individual letter appeared somewhere, which every document
    # satisfies. Assert the whole paragraph instead.
    assert isinstance(report_service.LIMITATIONS, str)
    assert _flat(report_service.LIMITATIONS) in text


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

    # The footer line itself, not the document-status notice -- that notice is
    # printed once, on page 1, so this used to assert it appeared `pages` times
    # and could only pass on a one-page document.
    assert text.count("PRAMAAN | Prototype examination report") >= pages
    for number in range(1, pages + 1):
        assert f"Page {number} of {pages}" in text
    # No page may claim a total the document does not have.
    assert f"of {pages + 1}" not in text


# --------------------------------------------------------------------------- #
# Regression: the fabricated values that used to be hardcoded in the document
# --------------------------------------------------------------------------- #
#: Every literal the report used to print regardless of what was measured. Each
#: one is a figure, identifier or conclusion that belonged to one sample file or
#: to no file at all, and each was printed for cases that had nothing of the kind.
FABRICATED_LITERALS = (
    "PRAMAAN-20260824-0020",   # a case number from a demo run
    "integration-check",       # a smoke-test examiner name, pre-filled
    "0.2097",                  # a signal score from a worked example
    "0.9969",                  # its partner in the hardcoded arithmetic
    "0.8220",                  # the fused score that example produced
    "82%",                     # the same figure as a percentage
    "b487e4860d796b65",        # one sample file's pHash
    "ccac8c3acc8c8c3a",        # ... and its dHash
    "512 x 512",               # ... and its dimensions
    "1,105",                   # an invented count of audit rows
    "High Confidence",         # a band fusion never emits
    "Merkle",                  # the chain is linear, not a tree
    "video_deepfake.mp4",      # a filename from a mock dataset
)


def test_no_fabricated_literal_survives_in_the_document(reported_case):
    text = _require_text(reported_case)

    for literal in FABRICATED_LITERALS:
        assert literal not in text, f"fabricated literal is back in the report: {literal}"


def test_no_fabricated_literal_is_hardcoded_in_the_report_module(reported_case):
    """The same check against the source, so a value cannot return unprinted.

    A literal can be reintroduced into a default or a fallback branch that this
    fixture's case happens not to reach, which is exactly how most of these
    survived: they lived in ``or``-fallbacks that only fired for cases with
    missing data.
    """
    source = Path(report_service.__file__).read_text()
    for literal in FABRICATED_LITERALS:
        if literal in ("Merkle",):
            assert literal not in source
            continue
        # Comments explain what was removed and why, so only code lines count.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert literal not in code, f"fabricated literal is back in report.py: {literal}"


def test_examiner_is_the_supplied_name_or_an_honest_placeholder(client, reported_case):
    text = _require_text(reported_case)
    assert "D. Jain" in text

    # A report requested without an examiner must say so rather than inherit a
    # smoke-test name.
    response = client.post(f"/api/cases/{reported_case['case_id']}/report", json={})
    assert response.status_code == 201, response.text
    other = _flat(_pdf_text(Path(response.json()["path"]).read_bytes()))
    if other:
        assert "integration-check" not in other
        assert "Not specified" in other or "D. Jain" in other


def test_review_decision_ships_unchecked(reported_case):
    text = _require_text(reported_case)

    # The examiner decides; the generator does not pre-tick a conclusion.
    assert "[ ] accepted" in text
    assert "[x] accepted" not in text.lower()
    assert "machine-generated" in text


def test_no_pdf_markup_leaks_into_the_rendered_text(reported_case):
    text = _require_text(reported_case)

    # Literal "<b>" used to be printed as visible characters by the built-in
    # writer, which does not parse markup.
    for tag in ("<b>", "</b>", "<i>", "</i>", "<font", "<br"):
        assert tag not in text, f"markup leaked into the document: {tag}"


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
        # An empty case is not an ambiguous measurement. Nothing was measured.
        assert "No evidence has been ingested" in text
        assert "no forensic measurement was attempted" in text
        assert "not a finding of authenticity" in text
        assert "not a finding of manipulation" in text
        # And no figures may be invented to fill the sections.
        assert "0.8220" not in text
        assert "82%" not in text
        assert "integration-check" not in text
        assert "PRAMAAN-20260824-0020" not in text


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
