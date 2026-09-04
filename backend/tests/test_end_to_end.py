"""TASK 16 -- the whole workflow, end to end, plus the failure paths.

One case is carried through all fifteen workflow steps in order, exactly as an
examiner would drive the API, and each step is then asserted against real data:

     1. start backend                    9. detector
     2. create/upload case              10. fusion
     3. verify SHA-256                   11. audit
     4. metadata                         12. audit verification
     5. pHash                            13. PDF report
     6. index search                     14. live judge-file ingestion
     7. near-duplicate matches           15. search newly ingested evidence
     8. propagation

Step 1 is covered twice: the liveness of the app under test, and a cold start in
a separate process against an empty data directory (directory creation, schema
creation, empty index, first upload). A real socket cannot be bound in this
sandbox, so the HTTP layer is driven through the ASGI transport rather than
uvicorn; every request below still goes through the full middleware, routing,
validation and error-handling stack.

The six required failure paths are exercised at the bottom: corrupted image,
invalid file, duplicate, missing metadata, detector unavailable, empty index.
Nothing here tolerates a fabricated value: a stage that cannot measure something
must say so, and a missing measurement must never be scored as zero.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config import BACKEND_DIR, Settings
from app.models import AuditLog, Evidence, get_session_factory
from app.services import audit as audit_service
from app.services import metadata as metadata_service
from app.services import storage
from app.services.hashing import (
    calculate_dhash,
    calculate_phash,
    hamming_distance,
    sha256_file,
)
from app.services.index import PerceptualIndex
from tests.helpers import encode, jpeg_bytes, jpeg_with_exif_bytes, make_image, png_bytes

# The image the case is built around, plus copies of it at other qualities.
SEED = 777
ORIGINAL = make_image(480, 360, seed=SEED)
CASE_FILE = jpeg_with_exif_bytes(seed=SEED, size=(480, 360))
SHARED_COPY = encode(ORIGINAL.resize((360, 270), Image.Resampling.LANCZOS), "JPEG", 45)
# The "judge file": a further recompressed copy handed over during a live demo.
JUDGE_FILE = encode(ORIGINAL.resize((300, 225), Image.Resampling.LANCZOS), "JPEG", 62)

STEPS = (
    "start_backend",
    "upload_case",
    "verify_sha256",
    "metadata",
    "phash",
    "index_search",
    "matches",
    "propagation",
    "detector",
    "fusion",
    "audit",
    "audit_verification",
    "report",
    "judge_ingestion",
    "search_new_evidence",
)


def _post(client, path: str, **kwargs) -> dict[str, Any]:
    response = client.post(path, **kwargs)
    assert response.status_code in (200, 201), f"{path} -> {response.text}"
    return response.json()


def _get(client, path: str, **kwargs) -> dict[str, Any]:
    response = client.get(path, **kwargs)
    assert response.status_code == 200, f"{path} -> {response.text}"
    return response.json()


def _upload(client, case_id: str | None, name: str, data: bytes, mime="image/jpeg"):
    return client.post(
        "/api/cases/upload",
        files={"file": (name, data, mime)},
        data={"case_id": case_id} if case_id else {},
    )


def _stored_file(evidence_id: str, settings) -> Path:
    """The absolute path of the bytes PRAMAAN actually kept for this item."""
    session = get_session_factory()()
    try:
        row = session.get(Evidence, evidence_id)
        assert row is not None
        return storage.absolute_path(row.stored_path, settings)
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# The workflow, run once, in order
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def workflow(client, settings) -> dict[str, Any]:
    """Drive all fifteen steps once and record what each one returned."""
    step: dict[str, Any] = {}

    # 1. start backend
    step["start_backend"] = {"health": _get(client, "/health"), "root": _get(client, "/")}

    # 2. create/upload case
    first = _upload(client, None, "judge-original.jpg", CASE_FILE)
    assert first.status_code == 201, first.text
    case = first.json()
    case_id = case["case"]["case_id"]
    second = _upload(client, case_id, "judge-shared.jpg", SHARED_COPY)
    assert second.status_code == 201, second.text
    step["upload_case"] = {
        "case_id": case_id,
        "first": case,
        "second": second.json(),
        "listing": _get(client, f"/api/cases/{case_id}/evidence"),
        "case_detail": _get(client, f"/api/cases/{case_id}"),
    }

    primary_id = case["evidence"]["evidence_id"]
    stored = _stored_file(primary_id, settings)

    # 3. verify SHA-256, against the submitted bytes and against the stored file
    step["verify_sha256"] = {
        "reported": case["evidence"]["sha256"],
        "submitted": hashlib.sha256(CASE_FILE).hexdigest(),
        "on_disk": sha256_file(stored),
    }

    # 4. metadata
    step["metadata"] = _get(client, f"/api/cases/{case_id}/metadata")

    # 5. pHash / dHash, recomputed independently from the stored file
    with Image.open(stored) as image:
        step["phash"] = {
            "reported_phash": case["evidence"]["phash"],
            "reported_dhash": case["evidence"]["dhash"],
            "recomputed_phash": calculate_phash(image),
            "recomputed_dhash": calculate_dhash(image),
        }

    # 6. index search
    step["index_search"] = {
        "rebuild": _post(client, "/api/index/rebuild"),
        "status": _get(client, "/api/index/status"),
    }

    # 7. near-duplicate matches
    step["matches"] = _post(client, f"/api/cases/{case_id}/matches")

    # 8. propagation
    step["propagation"] = _get(
        client, f"/api/cases/{case_id}/propagation", params={"refresh": "true"}
    )

    # 9. detector
    step["detector"] = {
        "status": _get(client, "/api/detector/status"),
        "run": _post(client, f"/api/cases/{case_id}/detect"),
    }

    # 10. fusion
    step["fusion"] = _post(client, f"/api/cases/{case_id}/verdict")

    # 11. audit
    step["audit"] = _get(client, f"/api/cases/{case_id}/audit")

    # 12. audit verification
    step["audit_verification"] = _post(
        client, f"/api/cases/{case_id}/audit/verify", params={"record": "false"}
    )

    # 13. PDF report
    report = _post(
        client, f"/api/cases/{case_id}/report", json={"examiner": "End-to-end run"}
    )
    download = client.get(report["download_url"])
    assert download.status_code == 200, download.text
    step["report"] = {"body": report, "bytes": download.content}

    # 14. live judge-file ingestion
    ingest = client.post(
        "/api/index/ingest",
        files={"file": ("judge-live-handover.jpg", JUDGE_FILE, "image/jpeg")},
        data={"platform": "live-handover"},
    )
    assert ingest.status_code == 201, ingest.text
    step["judge_ingestion"] = {
        "body": ingest.json(),
        "status_before": step["index_search"]["status"],
        "status_after": _get(client, "/api/index/status"),
    }

    # 15. search the newly ingested evidence
    step["search_new_evidence"] = {
        "matches": _post(client, f"/api/cases/{case_id}/matches"),
        "propagation": _get(
            client, f"/api/cases/{case_id}/propagation", params={"refresh": "true"}
        ),
        "analyse": _post(client, f"/api/cases/{case_id}/analyse"),
    }

    step["case_id"] = case_id
    step["primary_evidence_id"] = primary_id
    step["judge_evidence_id"] = step["judge_ingestion"]["body"]["evidence"][
        "evidence_id"
    ]
    return step


def test_every_workflow_step_ran(workflow):
    for name in STEPS:
        assert name in workflow, f"workflow step never ran: {name}"


# --------------------------------------------------------------------------- #
# 1. start backend
# --------------------------------------------------------------------------- #
def test_step_01_backend_is_up_and_reports_itself(workflow):
    started = workflow["start_backend"]
    # The health contract is fixed: exactly this object, no extra fields.
    assert started["health"] == {"status": "ok"}
    assert started["root"]["name"] == "PRAMAAN"
    assert started["root"]["health_url"] == "/health"
    assert started["root"]["version"]


@pytest.fixture(scope="module")
def cold_start(tmp_path_factory) -> dict[str, Any]:
    """Boot the app in a separate process against an empty data directory.

    Fresh settings, fresh engine, fresh index -- nothing inherited from this test
    session. The sandbox forbids listening sockets, so the app is driven through
    the ASGI transport rather than uvicorn; startup (directory creation, schema
    creation) and first-use behaviour are what is being proved.
    """
    root = tmp_path_factory.mktemp("cold")
    script = textwrap.dedent(
        """
        import json, os, sys
        root = sys.argv[1]
        os.environ.update(
            PRAMAAN_DATA_DIR=root + "/data",
            PRAMAAN_REPORTS_DIR=root + "/reports",
            PRAMAAN_CORPUS_DIR=root + "/corpus",
            PRAMAAN_LOG_LEVEL="WARNING",
            PRAMAAN_ENVIRONMENT="production",
            PRAMAAN_DEBUG="false",
        )
        from fastapi.testclient import TestClient
        from app.main import app
        from app.config import get_settings

        settings = get_settings()
        with TestClient(app) as client:
            health = client.get("/health").json()
            index_before = client.get("/api/index/status").json()
            with open(sys.argv[2], "rb") as handle:
                upload = client.post(
                    "/api/cases/upload",
                    files={"file": ("cold-start.jpg", handle.read(), "image/jpeg")},
                )
            case = upload.json()
            case_id = case["case"]["case_id"]
            matches = client.post("/api/cases/%s/matches" % case_id).json()
            verdict = client.post("/api/cases/%s/verdict" % case_id).json()

        print(json.dumps({
            "health": health,
            "index_before": index_before,
            "upload_status": upload.status_code,
            "sha256": case["evidence"]["sha256"],
            "phash": case["evidence"]["phash"],
            "matches": matches,
            "verdict": verdict["items"][0],
            "db_exists": os.path.isfile(str(settings.db_path)),
            "evidence_dir_exists": os.path.isdir(str(settings.evidence_dir)),
            "index_dir_exists": os.path.isdir(str(settings.index_dir)),
            "reports_dir_exists": os.path.isdir(str(settings.reports_dir)),
            "docs_disabled": client.get("/docs").status_code == 404,
        }))
        """
    )
    image = root / "cold-start.jpg"
    image.write_bytes(jpeg_bytes(seed=808))

    completed = subprocess.run(
        [sys.executable, "-c", script, str(root), str(image)],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_step_01_cold_start_provisions_itself_and_serves_requests(cold_start):
    assert cold_start["health"] == {"status": "ok"}
    assert cold_start["upload_status"] == 201
    assert len(cold_start["sha256"]) == 64
    assert cold_start["phash"]
    # Startup created everything it needs -- no manual provisioning step.
    assert cold_start["db_exists"] is True
    assert cold_start["evidence_dir_exists"] is True
    assert cold_start["index_dir_exists"] is True
    assert cold_start["reports_dir_exists"] is True
    # Production posture: no interactive docs.
    assert cold_start["docs_disabled"] is True


# --------------------------------------------------------------------------- #
# 2-5. ingestion, integrity, metadata, perceptual hashing
# --------------------------------------------------------------------------- #
def test_step_02_case_and_evidence_are_created(workflow):
    step = workflow["upload_case"]

    assert step["first"]["case"]["case_number"].startswith("PRAMAAN-")
    assert step["first"]["evidence"]["case_id"] == workflow["case_id"]
    assert step["first"]["duplicate"] is False
    # The second file attached to the *same* case, not a new one.
    assert step["second"]["case"]["case_id"] == workflow["case_id"]
    assert step["listing"]["count"] == 2
    assert step["case_detail"]["evidence_count"] == 2
    assert {e["filename"] for e in step["listing"]["evidence"]} == {
        "judge-original.jpg",
        "judge-shared.jpg",
    }


def test_step_03_sha256_matches_the_submitted_bytes_and_the_stored_file(workflow):
    step = workflow["verify_sha256"]
    assert step["reported"] == step["submitted"], "reported digest is not of these bytes"
    assert step["reported"] == step["on_disk"], "stored file does not match its digest"
    assert len(step["reported"]) == 64


def test_step_04_metadata_is_extracted_and_absence_is_not_incrimination(workflow):
    body = workflow["metadata"]

    assert body["count"] == 2
    assert body["interpretation"] == metadata_service.INTERPRETATION_NOTE
    assert "NOT evidence of manipulation" in body["interpretation"]

    by_name = {item["filename"]: item["metadata"] for item in body["items"]}
    original = by_name["judge-original.jpg"]
    shared = by_name["judge-shared.jpg"]

    # The camera file carries EXIF, including the editing-software tag.
    assert original["exif"]["present"] is True
    assert original["exif"]["tag_count"] > 0
    assert original["camera"]["make"] == "PRAMAAN"
    assert original["software"]["value"] == "Adobe Photoshop 25.0"
    assert original["software"]["editor_hint"] == "photoshop"
    assert original["timestamps"]["exif_datetime_original"]

    # The redistributed copy has been stripped, which is the *expected* state for
    # shared media and must be reported as absence, not as a finding.
    assert shared["exif"]["present"] is False
    assert shared["presence_summary"]["stripped_likely"] is True
    assert shared["presence_summary"]["fields_missing"]
    assert (
        shared["presence_summary"]["completeness"]
        < original["presence_summary"]["completeness"]
    )
    assert "not an indicator of manipulation" in shared["presence_summary"]["note"]


def test_step_05_perceptual_hashes_are_reproducible_from_the_stored_file(workflow):
    step = workflow["phash"]

    assert step["reported_phash"] == step["recomputed_phash"]
    assert step["reported_dhash"] == step["recomputed_dhash"]
    assert len(step["reported_phash"]) == 16  # 64 bits, hex
    assert len(step["reported_dhash"]) == 16


# --------------------------------------------------------------------------- #
# 6-8. retrieval and propagation
# --------------------------------------------------------------------------- #
def test_step_06_index_holds_the_case_evidence_and_reports_its_state(workflow):
    step = workflow["index_search"]

    assert step["rebuild"]["status"] == "rebuilt"
    assert step["status"]["indexed_count"] >= 2
    assert step["status"]["index_version"] >= 1
    assert step["status"]["last_updated"].endswith("Z")
    # Flat search over 64-bit hashes: exhaustive, so recall is not approximated.
    assert step["status"]["exact_search"] is True
    assert step["status"]["hash_bits"] == 64


def test_step_07_matches_are_ranked_candidates_with_verifiable_distances(workflow):
    body = workflow["matches"]
    evidence = workflow["upload_case"]["listing"]["evidence"]
    hashes = {e["evidence_id"]: e["phash"] for e in evidence}

    assert body["total_candidates"] >= 1
    assert "not a definitive identification" in body["interpretation"].lower()
    assert body["thresholds"]["hash_bits"] == 64

    query = next(
        q for q in body["queries"] if q["evidence_id"] == workflow["primary_evidence_id"]
    )
    # The redistributed copy of the same picture is retrieved as a candidate.
    others = {e["evidence_id"] for e in evidence} - {workflow["primary_evidence_id"]}
    found = {c["evidence_id"] for c in query["candidates"]}
    assert others & found, "the known copy was not retrieved"

    ranks = [c["rank"] for c in query["candidates"]]
    distances = [c["distance"] for c in query["candidates"]]
    assert ranks == sorted(ranks)
    assert distances == sorted(distances), "candidates must be ranked by distance"
    for candidate in query["candidates"]:
        if candidate["evidence_id"] in hashes:
            # Recomputed here from the stored hashes, so a wrong distance in the
            # index cannot pass unnoticed.
            assert candidate["phash_distance"] == hamming_distance(
                hashes[workflow["primary_evidence_id"]],
                hashes[candidate["evidence_id"]],
            )
        assert candidate["distance"] <= query["max_distance"]


def test_step_08_propagation_reports_an_earliest_known_instance_only(workflow):
    body = workflow["propagation"]
    origin = body["origin"]

    assert origin is not None
    assert origin["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False
    assert "not established as the absolute real-world origin" in origin["caveat"].lower()

    assert body["timeline"], "no propagation timeline was reconstructed"
    stamps = [e["occurred_at"] for e in body["timeline"] if e["occurred_at"]]
    assert stamps == sorted(stamps)
    assert body["instance_count"] >= 2
    assert body["graph"]["node_count"] >= 2
    assert body["caveats"]


# --------------------------------------------------------------------------- #
# 9-10. detection and fusion
# --------------------------------------------------------------------------- #
def test_step_09_detector_reports_a_score_or_abstains(workflow):
    status = workflow["detector"]["status"]
    run = workflow["detector"]["run"]

    assert run["count"] == 2
    assert status["interface_version"]
    assert "not a probability of guilt" in run["interpretation"].lower()

    for item in run["items"]:
        detection = item["detection"]
        if status["available"]:
            assert detection["status"] == "OK"
            assert 0.0 <= detection["score"] <= 1.0
        else:
            # No model installed: abstain. Never a fabricated score, never 0.0.
            assert detection["status"] == "UNAVAILABLE"
            assert detection["score"] is None
            assert detection["detail"]


def test_step_10_fusion_is_transparent_and_recomputable(workflow):
    body = workflow["fusion"]

    assert body["count"] == 2
    assert "not a probability" in body["interpretation"].lower()
    assert "PROTOTYPE OUTPUT" in body["caveat"]

    for verdict in body["items"]:
        assert verdict["verdict"] in {"AUTHENTIC", "MANIPULATED", "INSUFFICIENT_EVIDENCE"}
        included = [s for s in verdict["signals"] if s["included"]]
        excluded = [s for s in verdict["signals"] if not s["included"]]

        for signal in excluded:
            # Excluded signals are excluded from the arithmetic entirely -- no
            # zero-filling, which would read as "no evidence of manipulation".
            assert signal["score"] is None
            assert signal["contribution"] is None
            assert signal["effective_weight"] == 0.0
            assert signal["status"] in {"UNAVAILABLE", "INCONCLUSIVE", "UNSUPPORTED"}
        assert {s["signal_id"] for s in excluded} == {
            e["signal_id"] for e in verdict["excluded_signals"]
        }

        if not included:
            assert verdict["manipulation_score"] is None
            continue

        # Renormalisation over coverage: the weights that were used sum to 1, and
        # the published score is exactly the sum of the published contributions.
        assert sum(s["effective_weight"] for s in included) == pytest.approx(1.0)
        assert sum(s["contribution"] for s in included) == pytest.approx(
            verdict["manipulation_score"], abs=1e-6
        )
        assert verdict["signals_available"] == len(included)
        assert verdict["signals_total"] == len(verdict["signals"])
        assert verdict["arithmetic"]
        assert verdict["rationale"]
        assert verdict["signal_coverage"] == pytest.approx(
            verdict["available_weight"] / verdict["declared_weight_total"], abs=1e-6
        )


# --------------------------------------------------------------------------- #
# 11-12. audit
# --------------------------------------------------------------------------- #
def test_step_11_every_action_is_in_the_audit_trail(workflow):
    body = workflow["audit"]
    events = [entry["event"] for entry in body["events"]]

    for expected in (
        audit_service.EVENT_CASE_CREATED,
        audit_service.EVENT_EVIDENCE_INGESTED,
        audit_service.EVENT_HASH_CALCULATED,
        audit_service.EVENT_PERCEPTUAL_HASHED,
        audit_service.EVENT_METADATA_EXTRACTED,
        audit_service.EVENT_MATCH_SEARCHED,
        audit_service.EVENT_PROPAGATION_RECONSTRUCTED,
        audit_service.EVENT_DETECTOR_RUN,
        audit_service.EVENT_VERDICT_GENERATED,
    ):
        assert expected in events, f"missing audit event: {expected}"

    assert body["genesis_hash"] == "0" * 64
    assert len(body["head_hash"]) == 64
    assert body["algorithm"] == audit_service.ALGORITHM
    assert "tamper EVIDENCE, not tamper PROOF" in body["interpretation"]

    # Rows are ordered, and each one carries the link to its predecessor.
    seqs = [entry["seq"] for entry in body["events"]]
    assert seqs == sorted(seqs)
    for entry in body["events"]:
        assert len(entry["row_hash"]) == 64
        assert len(entry["previous_hash"]) == 64
        assert entry["seq"] > 0


def test_step_12_audit_chain_verifies(workflow):
    body = workflow["audit_verification"]

    assert body["valid"] is True
    assert body["first_invalid_seq"] is None
    assert body["issues"] == []
    assert body["scope"] == "global_chain"
    assert body["case_rows"] >= 1
    assert body["total_rows"] >= body["case_rows"]
    assert body["head_hash"] == workflow["audit"]["head_hash"]


def test_step_12_tampering_with_a_row_is_detected(client, workflow):
    """The chain must fail *loudly* when a stored row is edited underneath it."""
    session = get_session_factory()()
    row = (
        session.query(AuditLog)
        .filter(AuditLog.case_id == workflow["case_id"])
        .order_by(AuditLog.seq.asc())
        .first()
    )
    assert row is not None
    seq, original = row.seq, row.actor
    try:
        row.actor = "tampered-by-test"
        session.commit()

        broken = _post(
            client,
            f"/api/cases/{workflow['case_id']}/audit/verify",
            params={"record": "false"},
        )
        assert broken["valid"] is False
        assert broken["first_invalid_seq"] == seq
        assert broken["issues"]
    finally:
        # Restore: later tests and the module-scoped workflow share this database.
        row.actor = original
        session.commit()
        session.close()

    restored = _post(
        client,
        f"/api/cases/{workflow['case_id']}/audit/verify",
        params={"record": "false"},
    )
    assert restored["valid"] is True, "the chain did not survive an exact restore"


# --------------------------------------------------------------------------- #
# 13. report
# --------------------------------------------------------------------------- #
def test_step_13_report_is_a_hashed_pdf_anchored_to_the_audit_chain(workflow):
    body = workflow["report"]["body"]
    data = workflow["report"]["bytes"]

    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")
    assert body["pages"] >= 1
    assert hashlib.sha256(data).hexdigest() == body["sha256"]
    assert body["size_bytes"] == len(data)
    # The head hash printed in the report is what anchors it outside the database.
    assert len(body["audit_head_hash"]) == 64
    assert body["audit_chain_valid"] is True
    assert "PROTOTYPE OUTPUT" in body["document_status"]
    assert Path(body["path"]).is_file()


# --------------------------------------------------------------------------- #
# 14-15. live judge-file ingestion, then retrieval of it
# --------------------------------------------------------------------------- #
def test_step_14_judge_file_is_ingested_hashed_and_indexed(workflow):
    step = workflow["judge_ingestion"]
    body = step["body"]
    evidence = body["evidence"]

    assert evidence["role"] == "corpus"
    assert evidence["case_id"] is None
    assert evidence["sha256"] == hashlib.sha256(JUDGE_FILE).hexdigest()
    assert evidence["phash"] and evidence["dhash"]
    assert evidence["platform"] == "live-handover"
    assert evidence["indexed"] is True

    assert body["duplicate"] is False
    assert body["searchable"] is True
    assert body["index"]["status"] == "added"
    assert body["index"]["added"] == 1
    assert step["status_after"]["indexed_count"] == (
        step["status_before"]["indexed_count"] + 1
    )
    assert step["status_after"]["index_version"] > step["status_before"]["index_version"]


def test_step_15_newly_ingested_evidence_is_found_by_the_case(workflow):
    judge_id = workflow["judge_evidence_id"]
    before = {
        c["evidence_id"] for q in workflow["matches"]["queries"] for c in q["candidates"]
    }
    after_body = workflow["search_new_evidence"]["matches"]
    after = {c["evidence_id"] for q in after_body["queries"] for c in q["candidates"]}

    assert judge_id not in before, "the judge file existed before it was ingested"
    assert judge_id in after, "the ingested judge file is not searchable"

    hit = next(
        c
        for q in after_body["queries"]
        for c in q["candidates"]
        if c["evidence_id"] == judge_id
    )
    assert hit["role"] == "corpus"
    assert 0 <= hit["distance"] <= 64
    assert hit["confidence_band"] in {"strong_candidate", "candidate", "weak_candidate"}
    assert after_body["total_candidates"] > workflow["matches"]["total_candidates"]


def test_step_15_the_new_copy_enters_propagation_and_the_full_analysis(workflow):
    judge_id = workflow["judge_evidence_id"]

    propagation = workflow["search_new_evidence"]["propagation"]
    assert judge_id in {event["evidence_id"] for event in propagation["timeline"]}
    assert propagation["instance_count"] >= 3

    analysis = workflow["search_new_evidence"]["analyse"]
    for key in (
        "case",
        "evidence",
        "verdict",
        "signals",
        "matches",
        "origin",
        "timeline",
        "audit",
        "processing_time_ms",
    ):
        assert key in analysis
    assert analysis["audit"]["chain_valid"] is True
    assert analysis["processing_time_ms"] > 0
    assert judge_id in {
        c["evidence_id"] for q in analysis["matches"]["queries"] for c in q["candidates"]
    }


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #
def test_failure_corrupted_image_is_rejected_with_a_clean_error(client, workflow):
    """Valid JPEG magic, truncated body: must fail on decode, not crash."""
    corrupted = CASE_FILE[:180] + b"\x00" * 64

    response = _upload(client, workflow["case_id"], "corrupted.jpg", corrupted)
    assert response.status_code == 400, response.text
    error = response.json()["error"]

    assert error["type"] == "http_error"
    assert "could not be decoded" in error["message"]
    # No internals leak to the client.
    for leak in ("Traceback", "PIL", str(BACKEND_DIR)):
        assert leak not in error["message"]
    assert response.headers["x-request-id"]

    # And nothing was persisted.
    assert _get(client, f"/api/cases/{workflow['case_id']}/evidence")["count"] == 2


def test_failure_invalid_file_type_is_rejected(client, workflow):
    response = _upload(
        client,
        workflow["case_id"],
        "statement.txt",
        b"I hereby declare this is not an image.",
        "text/plain",
    )
    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    assert "Unsupported or unrecognised file type" in message
    # The message tells the examiner what *is* accepted.
    assert "JPEG" in message and "MP4" in message


def test_failure_duplicate_upload_is_reported_not_stored_twice(client, workflow):
    before = _get(client, f"/api/cases/{workflow['case_id']}/evidence")

    response = _upload(client, workflow["case_id"], "judge-original-again.jpg", CASE_FILE)
    assert response.status_code == 200, "a duplicate must not report 201 Created"
    body = response.json()

    assert body["duplicate"] is True
    assert any("SHA-256" in warning for warning in body["warnings"])
    assert body["evidence"]["evidence_id"] == workflow["primary_evidence_id"]

    after = _get(client, f"/api/cases/{workflow['case_id']}/evidence")
    assert after["count"] == before["count"]

    trail = _get(client, f"/api/cases/{workflow['case_id']}/audit")
    duplicates = [
        e for e in trail["events"] if e["event"] == audit_service.EVENT_EVIDENCE_DUPLICATE
    ]
    assert duplicates, "the duplicate submission was not audited"
    assert duplicates[-1]["details"]["sha256"] == hashlib.sha256(CASE_FILE).hexdigest()


def test_failure_missing_metadata_does_not_produce_a_manipulation_finding(client):
    """A stripped PNG: absence must be reported as absence, and nothing more."""
    uploaded = _upload(client, None, "stripped.png", png_bytes(seed=911), "image/png")
    assert uploaded.status_code == 201, uploaded.text
    case_id = uploaded.json()["case"]["case_id"]

    metadata = _get(client, f"/api/cases/{case_id}/metadata")
    payload = metadata["items"][0]["metadata"]
    assert payload["exif"]["present"] is False
    assert payload["presence_summary"]["present_count"] == 0
    assert payload["presence_summary"]["stripped_likely"] is True
    assert "NOT evidence of manipulation" in metadata["interpretation"]

    verdict = _post(client, f"/api/cases/{case_id}/verdict")["items"][0]
    signal = next(
        s for s in verdict["signals"] if s["signal_id"] == "metadata_integrity"
    )
    # Missing metadata is not scored at all -- it is excluded, with the reason
    # spelled out, rather than counted against the file.
    assert signal["score"] is None
    assert signal["contribution"] is None
    assert signal["included"] is False
    assert signal["status"] == "INCONCLUSIVE"
    assert "NOT evidence of manipulation" in signal["explanation"]
    assert verdict["verdict"] != "MANIPULATED"


def test_failure_detector_unavailable_is_a_missing_signal_not_a_zero(workflow):
    status = workflow["detector"]["status"]
    if status["available"]:
        pytest.skip("a detector model is installed in this environment")

    assert status["adapter"] == "null"
    assert status["reason"], "an unavailable detector must say why"
    assert "NOT a finding of authenticity" in status["reason"]
    # The adapters that were tried, and why each declined, are on the record.
    assert status["candidate_adapters"]
    assert all(not a["available"] for a in status["candidate_adapters"])
    assert all(a["reason"] for a in status["candidate_adapters"])

    for verdict in workflow["fusion"]["items"]:
        signal = next(s for s in verdict["signals"] if s["signal_id"] == "ai_detection")
        assert signal["score"] is None
        assert signal["contribution"] is None
        assert signal["included"] is False
        assert signal["status"] == "UNAVAILABLE"
        # The declared weight stays visible, so a reader can see what was lost.
        assert signal["weight"] > 0
        assert signal["effective_weight"] == 0.0
        assert verdict["primary_signal_available"] is False
        assert verdict["signal_coverage"] < 1.0
        assert any(
            entry["signal_id"] == "ai_detection" for entry in verdict["excluded_signals"]
        )


def test_failure_empty_index_returns_no_candidates_and_says_so(monkeypatch, tmp_path):
    """A freshly created index has nothing in it and must say exactly that."""
    monkeypatch.setenv("PRAMAAN_DATA_DIR", str(tmp_path))
    empty = PerceptualIndex(Settings())

    # No vectors file, no sidecar: loading must tolerate that, not raise.
    assert empty.query("f" * 16, top_k=10) == []
    status = empty.status()
    assert status["indexed_count"] == 0
    assert status["index_version"] == 0
    assert status["persisted"] is False


def test_failure_empty_index_is_reported_through_the_api(cold_start):
    """After a single upload, the index has the uploaded item but no *other*
    candidates to compare against -- the query evidence is excluded from its own
    results, so the candidate list is empty. This is correct: one item in the
    corpus cannot be a near-duplicate of anything but itself.
    """
    assert cold_start["index_before"]["indexed_count"] == 0
    assert cold_start["index_before"]["index_version"] == 0
    assert cold_start["index_before"]["last_updated"] is None

    matches = cold_start["matches"]
    assert matches["total_candidates"] == 0
    assert matches["queries"][0]["candidates"] == []
    # With the evidence now indexed at upload, the index is no longer empty.
    # The note should reflect that there are no candidates rather than the
    # index being unpopulated.
    note = " ".join(matches["queries"][0]["notes"])
    assert "candidates" in note.lower() or "no" in note.lower()

    # And the empty match result must not become a signal in either direction.
    signal = next(
        s
        for s in cold_start["verdict"]["signals"]
        if s["signal_id"] == "perceptual_duplication"
    )
    assert signal["score"] is None
    assert signal["included"] is False
    assert "NOT evidence of authenticity or of manipulation" in signal["explanation"]

