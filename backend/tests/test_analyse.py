"""TASK 13 -- the one-call full analysis pipeline.

The contract these tests pin down: ``POST /api/cases/{case_id}/analyse`` returns
the nine documented keys, every value is derived from *this* case, and nothing is
a fixed demo result. The strongest check is
``test_two_different_cases_get_different_results``: the same endpoint run against
different evidence must produce different hashes, verdicts and timings.
"""

from __future__ import annotations

import pytest

from app.services import audit, pipeline
from tests.helpers import jpeg_bytes, jpeg_with_exif_bytes, mp4_bytes, png_bytes

REQUIRED_KEYS = (
    "case",
    "evidence",
    "verdict",
    "signals",
    "matches",
    "origin",
    "timeline",
    "audit",
    "processing_time_ms",
)


def _upload(client, case_id, name, data, mime="image/jpeg"):
    # case_id is a form field on the upload endpoint, not a query parameter.
    form = {"case_id": case_id} if case_id else {}
    response = client.post(
        "/api/cases/upload", files={"file": (name, data, mime)}, data=form
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _analyse(client, case_id, **params):
    response = client.post(f"/api/cases/{case_id}/analyse", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def analysed(client) -> dict:
    """A two-image case (one with EXIF, one recompressed) analysed once."""
    first = _upload(client, None, "protest-photo.jpg", jpeg_with_exif_bytes(seed=63))
    case_id = first["case"]["case_id"]
    _upload(client, case_id, "protest-photo-forwarded.jpg",
            jpeg_bytes(seed=63, quality=45))
    assert client.post("/api/index/rebuild").status_code == 200

    return {"case_id": case_id, "body": _analyse(client, case_id, refresh="true")}


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_response_contains_every_documented_key(analysed):
    body = analysed["body"]
    for key in REQUIRED_KEYS:
        assert key in body, f"missing documented key: {key}"


def test_case_and_evidence_blocks_describe_this_case(client, analysed):
    body = analysed["body"]
    case_id = analysed["case_id"]

    assert body["case"]["case_id"] == case_id
    assert body["case"]["evidence_count"] == 2
    assert len(body["evidence"]) == 2

    listed = client.get(f"/api/cases/{case_id}/evidence").json()["evidence"]
    assert {e["sha256"] for e in body["evidence"]} == {e["sha256"] for e in listed}
    for item in body["evidence"]:
        assert len(item["sha256"]) == 64
        assert item["case_id"] == case_id


def test_verdict_and_signals_are_consistent_with_each_other(analysed):
    body = analysed["body"]
    verdict = body["verdict"]

    assert verdict is not None
    assert verdict["evidence_id"] == body["verdict_evidence_id"]
    assert body["signals"] == verdict["signals"]
    assert verdict["verdict"] in {"AUTHENTIC", "MANIPULATED", "INSUFFICIENT_EVIDENCE"}

    # The leading item really is the highest-scoring one.
    scores = [
        v["manipulation_score"] for v in body["verdicts"]
        if v["manipulation_score"] is not None
    ]
    if scores and verdict["manipulation_score"] is not None:
        assert verdict["manipulation_score"] == max(scores)


def test_every_verdict_in_the_case_is_returned(client, analysed):
    body = analysed["body"]
    per_item = client.post(
        f"/api/cases/{analysed['case_id']}/verdict"
    ).json()["items"]

    assert len(body["verdicts"]) == len(per_item) == 2
    assert {v["evidence_id"] for v in body["verdicts"]} == {
        v["evidence_id"] for v in per_item
    }


def test_signal_arithmetic_still_adds_up_in_this_response(analysed):
    for verdict in analysed["body"]["verdicts"]:
        included = [s for s in verdict["signals"] if s["included"]]
        if not included:
            assert verdict["manipulation_score"] is None
            continue

        assert sum(s["effective_weight"] for s in included) == pytest.approx(1.0)
        assert sum(s["contribution"] for s in included) == pytest.approx(
            verdict["manipulation_score"], abs=1e-6
        )
        for signal in verdict["signals"]:
            if not signal["included"]:
                # Excluded means excluded -- not scored as zero.
                assert signal["contribution"] is None


def test_matches_are_candidates_not_identifications(analysed):
    matches = analysed["body"]["matches"]
    lowered = matches["interpretation"].lower()

    assert "candidate" in lowered
    assert "not a definitive identification" in lowered
    assert len(matches["queries"]) == 2
    for query in matches["queries"]:
        for candidate in query["candidates"]:
            assert 0 <= candidate["distance"] <= 64
            assert candidate["confidence_band"] in {
                "strong_candidate", "candidate", "weak_candidate"
            }


def test_origin_is_the_earliest_indexed_instance_not_an_absolute_origin(analysed):
    origin = analysed["body"]["origin"]
    assert origin is not None

    assert origin["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False
    assert "not" in origin["caveat"].lower()
    assert "absolute real-world origin" in origin["caveat"].lower()

    ids = {e["evidence_id"] for e in analysed["body"]["evidence"]}
    assert origin["evidence_id"] in ids


def test_timeline_is_ordered_and_refers_to_real_evidence(client, analysed):
    body = analysed["body"]
    timeline = body["timeline"]
    assert timeline

    stamps = [event["occurred_at"] for event in timeline if event["occurred_at"]]
    assert stamps == sorted(stamps), "timeline must be chronological"

    own = {e["evidence_id"] for e in body["evidence"]}
    seen = {event["evidence_id"] for event in timeline}
    # The case's own evidence must appear; near-duplicate expansion may also pull
    # in indexed items belonging to other cases, which is the point of the index.
    assert own & seen
    for event in timeline:
        assert event["evidence_id"]
        assert event["timestamp_source"]
        assert event["discovered_by"] in {
            "case_evidence", "near_duplicate_candidate", "recorded_parent",
            "recorded_source_group",
        }


def test_audit_block_is_the_chain_for_this_case(analysed):
    block = analysed["body"]["audit"]

    assert block["case_id"] == analysed["case_id"]
    assert block["count"] >= 1
    assert block["chain_valid"] is True
    assert block["first_invalid_seq"] is None
    assert block["issues"] == []
    assert block["algorithm"] == audit.ALGORITHM
    assert len(block["head_hash"]) == 64
    assert block["genesis_hash"] == "0" * 64
    assert "tamper EVIDENCE, not tamper PROOF" in block["interpretation"]
    # The read happened before the completion entry was appended.
    assert "ANALYSIS_COMPLETED" in block["note"]
    assert "ANALYSIS_COMPLETED" not in {e["event"] for e in block["events"]}


def test_pipeline_stages_are_reported_in_order(analysed):
    body = analysed["body"]
    assert body["stages"] == list(pipeline.ANALYSIS_STAGES)
    assert body["analysis_version"] == pipeline.ANALYSIS_VERSION
    assert body["stages"].index("metadata_extraction") < body["stages"].index(
        "signal_fusion"
    )
    assert body["stages"].index("signal_fusion") < body["stages"].index(
        "propagation_reconstruction"
    )


def test_processing_time_is_measured(analysed):
    elapsed = analysed["body"]["processing_time_ms"]
    assert isinstance(elapsed, float)
    assert elapsed > 0.0
    # A full pipeline over two images cannot plausibly take under a microsecond
    # or over five minutes; this catches a hardcoded or unset value.
    assert 0.001 < elapsed < 300_000


def test_caveats_travel_with_the_result(analysed):
    body = analysed["body"]
    assert "not been validated" in body["caveat"].lower()
    assert "PROTOTYPE" in body["caveat"]
    assert "not a probability" in body["score_semantics"]
    assert body["fusion_method"]
    assert "NOT a judgement about the case" in body["verdict_selection"]


def test_unavailable_detector_is_reported_as_a_warning_not_a_score(analysed):
    body = analysed["body"]
    detector = body["detector"]

    if detector["available"]:
        pytest.skip("a detector model is installed in this environment")

    assert any("not a finding of authenticity" in w.lower() for w in body["warnings"])
    ai_signals = [
        s for v in body["verdicts"] for s in v["signals"]
        if s["signal_id"] == "ai_detection"
    ]
    assert ai_signals
    for signal in ai_signals:
        assert signal["score"] is None
        assert signal["contribution"] is None
        assert signal["included"] is False


# --------------------------------------------------------------------------- #
# Not a fixed demo result
# --------------------------------------------------------------------------- #
def test_two_different_cases_get_different_results(client):
    first = _upload(client, None, "scene-a.jpg", jpeg_with_exif_bytes(seed=91))
    second = _upload(client, None, "scene-b.png", png_bytes(seed=12), "image/png")

    a = _analyse(client, first["case"]["case_id"], refresh="true")
    b = _analyse(client, second["case"]["case_id"], refresh="true")

    assert a["case"]["case_id"] != b["case"]["case_id"]
    assert a["evidence"][0]["sha256"] != b["evidence"][0]["sha256"]
    assert a["audit"]["head_hash"] != b["audit"]["head_hash"]
    assert a["processing_time_ms"] != b["processing_time_ms"]
    # Different pixels must produce different perceptual hashes and signal detail.
    assert a["evidence"][0]["phash"] != b["evidence"][0]["phash"]

    a_metadata = next(
        s for s in a["signals"] if s["signal_id"] == "metadata_integrity"
    )
    b_metadata = next(
        s for s in b["signals"] if s["signal_id"] == "metadata_integrity"
    )
    # One carries EXIF with an editor tag, the other is a stripped PNG.
    assert a_metadata["explanation"] != b_metadata["explanation"]


def test_rerunning_is_idempotent_in_substance(client, analysed):
    again = _analyse(client, analysed["case_id"])
    first = analysed["body"]

    assert again["verdict"]["verdict"] == first["verdict"]["verdict"]
    assert again["verdict"]["manipulation_score"] == first["verdict"][
        "manipulation_score"
    ]
    assert again["origin"]["evidence_id"] == first["origin"]["evidence_id"]
    # But the audit chain has grown, and the timing is measured afresh.
    assert again["audit"]["total_rows"] > first["audit"]["total_rows"]
    assert again["processing_time_ms"] != first["processing_time_ms"]


def test_analysis_is_recorded_in_the_audit_chain(client, analysed):
    trail = client.get(f"/api/cases/{analysed['case_id']}/audit").json()
    entries = [e for e in trail["events"] if e["event"] == "ANALYSIS_COMPLETED"]
    assert entries

    details = entries[0]["details"]
    assert details["analysis_version"] == pipeline.ANALYSIS_VERSION
    assert details["stages"] == list(pipeline.ANALYSIS_STAGES)
    assert details["evidence_count"] == 2
    assert details["processing_time_ms"] > 0
    assert len(details["verdicts"]) == 2
    assert entries[0]["previous_hash"] == analysed["body"]["audit"]["head_hash"]


def test_chain_still_verifies_after_analysis(client, analysed):
    response = client.post(
        f"/api/cases/{analysed['case_id']}/audit/verify", params={"record": "false"}
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


# --------------------------------------------------------------------------- #
# Degraded inputs
# --------------------------------------------------------------------------- #
def test_a_case_with_no_evidence_returns_nulls_not_a_placeholder_finding(client):
    from app.models import get_session_factory
    from app.services import ingestion

    session = get_session_factory()()
    try:
        case = ingestion.create_case(session, title="Nothing ingested", examiner=None)
        session.commit()
        case_id = case.id
    finally:
        session.close()

    body = _analyse(client, case_id)

    assert body["evidence"] == []
    assert body["verdict"] is None
    assert body["signals"] == []
    assert body["verdicts"] == []
    assert body["origin"] is None
    assert body["timeline"] == []
    assert body["matches"]["total_candidates"] == 0
    assert any("No evidence has been ingested" in w for w in body["warnings"])
    assert body["processing_time_ms"] > 0


def test_a_video_is_analysed_without_inventing_image_signals(client):
    uploaded = _upload(client, None, "clip.mp4", mp4_bytes(), "video/mp4")
    body = _analyse(client, uploaded["case"]["case_id"], refresh="true")

    verdict = body["verdict"]
    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert verdict["manipulation_score"] is None
    assert verdict["signals_available"] == 0
    for signal in verdict["signals"]:
        assert signal["score"] is None
        assert signal["contribution"] is None
    assert body["evidence"][0]["phash"] is None


def test_analyse_for_an_unknown_case_is_404(client):
    response = client.post(
        "/api/cases/00000000-0000-0000-0000-000000000000/analyse"
    )
    assert response.status_code == 404
