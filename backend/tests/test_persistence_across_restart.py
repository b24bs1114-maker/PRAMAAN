"""Does the chain of custody survive a restart? (Phase 12 regression.)

Every other test in this suite runs against a ``TestClient`` inside the pytest
process. That cannot answer this question: a fixture reset is not a restart. The
failure being guarded against is the deployed one -- the container is replaced,
a new interpreter starts, and it has to find the case database, the stored
evidence bytes and the append-only audit chain exactly as the exited process left
them. So each boot below is a **separate OS process** against one
``PRAMAAN_DATA_DIR``:

    ingest    a fresh store: upload one image, report what was written
    observe   new interpreter, same store: is it all still there, does the chain
              still verify, does appending a new event keep it verifying
    (tamper)  edit one historical row's payload directly in SQLite, leaving its
              stored row_hash untouched -- the realistic attack on an append-only
              log in a file you can open
    check     new interpreter again: is the edit detected, and is the right row
              named

The blueprint's disk (``render.yaml``) is what makes this true in production;
``tests/test_render_blueprint.py`` checks that the disk is declared and that every
artefact resolves inside it. This module checks that persistence actually works.

The worker phases only report observations as JSON. Every assertion lives in the
tests, so a phase cannot quietly decide it passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve()

#: Ingesting one image writes exactly these, in this order.
INGEST_EVENTS = (
    "CASE_CREATED",
    "EVIDENCE_INGESTED",
    "HASH_CALCULATED",
    "PERCEPTUAL_HASH_CALCULATED",
)

#: The chain construction. Recorded here so that changing it -- to a Merkle tree,
#: say -- fails a test rather than silently altering what a verified chain means.
ALGORITHM = "SHA-256(previous_hash || canonical_json(payload))"

CASE_TITLE = "Persistence across a process restart"
EXAMINER = "restart-regression"


def _worker_env(store: Path) -> dict[str, str]:
    """The environment each boot gets: one store, no models, no network.

    The detector is switched off and the model paths blanked for the same reason
    ``conftest.py`` does it -- this is a storage test, and loading 347 MB of
    weights three times would measure something else. Nothing here depends on the
    detector: ingest hashes and indexes, it does not analyse.
    """
    env = dict(os.environ)
    env.update(
        {
            "PRAMAAN_ENVIRONMENT": "testing",
            "PRAMAAN_DEBUG": "false",
            "PRAMAAN_LOG_LEVEL": "WARNING",
            "PRAMAAN_LOG_ACCESS": "false",
            "PRAMAAN_DATA_DIR": str(store / "data"),
            "PRAMAAN_REPORTS_DIR": str(store / "reports"),
            "PRAMAAN_CORPUS_DIR": str(store / "corpus"),
            "PRAMAAN_ENABLE_AI_DETECTOR": "false",
            "PRAMAAN_IMAGE_MODEL_PATH": "",
            "PRAMAAN_VIDEO_MODEL_PATH": "",
            "PRAMAAN_AUDIO_MODEL_PATH": "",
            "PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT": "",
            "PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT": "",
            "PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _boot(phase: str, store: Path) -> dict:
    """Run one phase in a brand-new interpreter and return what it observed."""
    completed = subprocess.run(
        [sys.executable, str(WORKER), phase],
        cwd=str(BACKEND_DIR),
        env=_worker_env(store),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        f"boot {phase!r} exited {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout[-2000:]}\n"
        f"--- stderr ---\n{completed.stderr[-2000:]}"
    )
    payload = completed.stdout.strip().splitlines()
    assert payload, f"boot {phase!r} printed nothing\n{completed.stderr[-2000:]}"
    return json.loads(payload[-1])


# ---------------------------------------------------------------------------
# Worker phases. These run in the subprocess, one per boot, and only report.
# ---------------------------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _audit(client) -> dict:
    trail = client.get("/api/audit", params={"limit": 5000}).json()
    return {
        "count": len(trail["events"]),
        "head_hash": trail["head_hash"],
        "genesis_hash": trail["genesis_hash"],
        "algorithm": trail["algorithm"],
        "events": [event.get("event") for event in trail["events"]],
    }


def _verify(client, *, record: bool = False) -> dict:
    """Verification is itself an auditable act: ``POST /api/audit/verify``
    appends ``AUDIT_CHAIN_VERIFIED`` unless ``record=false``. The observational
    calls pass false, so that measuring the chain does not lengthen it."""
    response = client.post("/api/audit/verify", params={"record": str(record).lower()})
    response.raise_for_status()
    body = response.json()
    return {
        "valid": body["valid"],
        "total_rows": body["total_rows"],
        "first_invalid_seq": body.get("first_invalid_seq"),
        "algorithm": body["algorithm"],
        "issues": body.get("issues", []),
        "interpretation": body.get("interpretation"),
    }


def _phase_ingest() -> dict:
    from tests.helpers import png_bytes

    payload = png_bytes(seed=41)
    with _client() as client:
        response = client.post(
            "/api/cases/upload",
            files={"file": ("restart-regression.png", payload, "image/png")},
            data={"title": CASE_TITLE, "examiner": EXAMINER},
        )
        response.raise_for_status()
        body = response.json()
        evidence = body["evidence"]
        return {
            "status_code": response.status_code,
            "case_id": body["case"]["case_id"],
            "evidence_id": evidence["evidence_id"],
            "sha256": evidence.get("sha256") or evidence.get("hashes", {}).get("sha256"),
            "uploaded_bytes": len(payload),
            "uploaded_sha256": hashlib.sha256(payload).hexdigest(),
            "audit": _audit(client),
            "verify": _verify(client),
        }


def _phase_observe() -> dict:
    """The restart boot. Ordering matters and is the point.

    The chain is measured *first*, before this process does anything auditable:
    re-reading the stored bytes is itself an audited act, so checking afterwards
    would be measuring this test's own footprint rather than what survived.
    """
    with _client() as client:
        observed: dict[str, object] = {
            "audit_at_boot": _audit(client),
            "verify_at_boot": _verify(client),
        }
        state = json.loads((Path(os.environ["PRAMAAN_DATA_DIR"]).parent / "ingest.json").read_text())
        case_id, evidence_id = state["case_id"], state["evidence_id"]

        case = client.get(f"/api/cases/{case_id}")
        observed["case_status_code"] = case.status_code
        observed["case_title"] = case.json().get("title") if case.status_code == 200 else None

        listing = client.get(f"/api/cases/{case_id}/evidence")
        observed["evidence_status_code"] = listing.status_code
        items = listing.json().get("evidence", []) if listing.status_code == 200 else []
        observed["evidence_count"] = len(items)
        observed["evidence_sha256"] = items[0].get("sha256") if items else None
        observed["evidence_filename"] = items[0].get("filename") if items else None

        # Audited from here on.
        raw = client.get(f"/api/evidence/{evidence_id}/file")
        observed["file_status_code"] = raw.status_code
        observed["stored_bytes"] = len(raw.content)
        observed["stored_sha256"] = hashlib.sha256(raw.content).hexdigest()

        # Form fields, and the status field is `case_status` (cases.update_case).
        patched = client.patch(f"/api/cases/{case_id}", data={"case_status": "under_review"})
        observed["patch_status_code"] = patched.status_code
        after_append = _audit(client)
        observed["audit_after_append"] = after_append
        observed["appended_events"] = after_append["events"][
            observed["audit_at_boot"]["count"] :  # type: ignore[index]
        ]
        observed["verify_after_append"] = _verify(client)

        # The recording path: verification writes its own outcome, and that row
        # must itself be correctly chained.
        observed["verify_recorded"] = _verify(client, record=True)
        observed["audit_after_record"] = _audit(client)
        observed["verify_after_record"] = _verify(client)
        return observed


def _phase_check() -> dict:
    with _client() as client:
        return {"verify": _verify(client), "audit": _audit(client)}


PHASES = {"ingest": _phase_ingest, "observe": _phase_observe, "check": _phase_check}


# ---------------------------------------------------------------------------
# The boots, chained: each fixture is one restart.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One store, outlived by nothing: three processes will share it."""
    return tmp_path_factory.mktemp("pramaan-restart")


@pytest.fixture(scope="module")
def ingested(store: Path) -> dict:
    observed = _boot("ingest", store)
    (store / "ingest.json").write_text(json.dumps(observed), encoding="utf-8")
    return observed


@pytest.fixture(scope="module")
def reopened(store: Path, ingested: dict) -> dict:
    return _boot("observe", store)


@pytest.fixture(scope="module")
def tampered(store: Path, reopened: dict) -> dict:
    """Edit one historical row's payload in place, leaving its ``row_hash``.

    Not deleting a row -- that is obvious -- but changing what one says, with the
    stored hash left exactly as an editor who did not know the hashing scheme
    would leave it. No other row is touched.
    """
    from app.config import Settings

    database = Settings(data_dir=store / "data").db_path
    assert database.is_file(), database
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT seq, actor, event FROM audit_log ORDER BY seq LIMIT 1"
        ).fetchone()
        assert row is not None, "no audit rows to tamper with"
        seq, actor, event = row
        connection.execute(
            "UPDATE audit_log SET actor = ? WHERE seq = ?", ("tampered-by-hand", seq)
        )
        connection.commit()
    finally:
        connection.close()
    return {"seq": seq, "previous_actor": actor, "event": event}


@pytest.fixture(scope="module")
def rechecked(store: Path, tampered: dict) -> dict:
    return _boot("check", store)


def test_ingest_leaves_the_case_and_the_bytes_on_disk(store: Path, ingested: dict) -> None:
    """Checked on the filesystem, not through the API: an in-memory database
    would satisfy every HTTP assertion in this module and survive nothing."""
    from app.config import Settings

    settings = Settings(data_dir=store / "data")
    assert settings.db_path.is_file(), f"no case database at {settings.db_path}"
    assert settings.db_path.stat().st_size > 0

    stored = [path for path in settings.evidence_dir.rglob("*") if path.is_file()]
    assert stored, f"nothing written under {settings.evidence_dir}"
    digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in stored}
    assert ingested["sha256"] in digests, (
        f"no file under {settings.evidence_dir} hashes to the recorded digest "
        f"{ingested['sha256']}; found {sorted(digests)}"
    )
    assert ingested["sha256"] == ingested["uploaded_sha256"], (
        "the digest recorded at ingest does not match the bytes that were uploaded"
    )


def test_the_chain_is_valid_and_complete_the_moment_it_is_written(ingested: dict) -> None:
    assert ingested["status_code"] == 201, ingested["status_code"]
    audit = ingested["audit"]
    assert tuple(audit["events"]) == INGEST_EVENTS, audit["events"]
    assert ingested["verify"]["valid"] is True, ingested["verify"]
    assert ingested["verify"]["total_rows"] == len(INGEST_EVENTS), ingested["verify"]


def test_the_audit_chain_carries_over_a_restart(ingested: dict, reopened: dict) -> None:
    """A new interpreter, a new engine, a new connection pool, the same rows."""
    before, after = ingested["audit"], reopened["audit_at_boot"]
    assert after["count"] == before["count"], (
        f"{after['count']} audit rows after the restart, {before['count']} before"
    )
    assert after["head_hash"] == before["head_hash"], "the head hash changed across the restart"
    assert after["genesis_hash"] == before["genesis_hash"], "the genesis hash changed"
    assert after["events"] == before["events"], after["events"]
    assert reopened["verify_at_boot"]["valid"] is True, reopened["verify_at_boot"]


def test_the_case_and_its_evidence_carry_over_a_restart(
    ingested: dict, reopened: dict
) -> None:
    assert reopened["case_status_code"] == 200, "the case did not survive the restart"
    assert reopened["case_title"] == CASE_TITLE, reopened["case_title"]
    assert reopened["evidence_status_code"] == 200
    assert reopened["evidence_count"] == 1, reopened["evidence_count"]
    assert reopened["evidence_sha256"] == ingested["sha256"], (
        "the recorded SHA-256 changed across the restart"
    )


def test_the_stored_bytes_reread_after_a_restart_still_hash_to_the_ingest_digest(
    ingested: dict, reopened: dict
) -> None:
    """The digest is the whole claim. Re-reading through the API and re-hashing
    the response is what a defence expert would do."""
    assert reopened["file_status_code"] == 200, "the stored bytes were unreadable"
    assert reopened["stored_bytes"] == ingested["uploaded_bytes"], (
        f"{reopened['stored_bytes']} bytes came back, {ingested['uploaded_bytes']} went in"
    )
    assert reopened["stored_sha256"] == ingested["sha256"], (
        "the bytes on disk no longer hash to the digest recorded at ingest"
    )


def test_appending_an_event_after_a_restart_keeps_the_chain_valid(reopened: dict) -> None:
    """The restart boundary must not be a seam: a row written by the new process
    has to chain onto the hash left by the old one."""
    assert reopened["patch_status_code"] == 200, reopened["patch_status_code"]
    assert "CASE_UPDATED" in reopened["appended_events"], reopened["appended_events"]
    assert (
        reopened["audit_after_append"]["count"] > reopened["audit_at_boot"]["count"]
    ), "appending an event added no audit row"
    assert reopened["verify_after_append"]["valid"] is True, reopened["verify_after_append"]


def test_a_recorded_verification_is_itself_correctly_chained(reopened: dict) -> None:
    """``POST /api/audit/verify`` appends its own outcome by default, so the act
    of checking the chain extends it -- and that row must verify too."""
    assert reopened["verify_recorded"]["valid"] is True, reopened["verify_recorded"]
    assert reopened["audit_after_record"]["events"][-1] == "AUDIT_CHAIN_VERIFIED", (
        reopened["audit_after_record"]["events"]
    )
    assert (
        reopened["audit_after_record"]["count"]
        == reopened["audit_after_append"]["count"] + 1
    ), "recording a verification wrote something other than exactly one row"
    assert reopened["verify_after_record"]["valid"] is True, reopened["verify_after_record"]


def test_editing_one_historical_row_is_detected_and_the_row_is_named(
    tampered: dict, rechecked: dict
) -> None:
    """Tamper *evidence*, not tamper *proof*: whoever can edit the row can
    recompute the hashes. What the chain gives is that an edit made without
    recomputing them cannot hide."""
    verdict = rechecked["verify"]
    assert verdict["valid"] is False, "a tampered chain was reported as valid"
    assert verdict["first_invalid_seq"] == tampered["seq"], (
        f"the edited row was seq {tampered['seq']}, but verification named "
        f"{verdict['first_invalid_seq']}"
    )
    assert verdict["issues"], "invalid, but with no issue describing why"
    reported = json.dumps(verdict["issues"]).lower()
    assert "row_hash" in reported or "content_modified" in reported, reported
    assert tampered["previous_actor"] != "tampered-by-hand"


def test_the_chain_is_the_documented_hash_chain_and_not_a_merkle_tree(
    ingested: dict, rechecked: dict
) -> None:
    for label, algorithm in (
        ("the audit trail", ingested["audit"]["algorithm"]),
        ("verification at ingest", ingested["verify"]["algorithm"]),
        ("verification after tampering", rechecked["verify"]["algorithm"]),
    ):
        assert algorithm == ALGORITHM, f"{label} reports {algorithm!r}"
        assert "merkle" not in algorithm.lower(), f"{label} describes a Merkle tree"


if __name__ == "__main__":  # one boot, invoked by _boot() above
    sys.path.insert(0, str(BACKEND_DIR))
    print(json.dumps(PHASES[sys.argv[1]]()))
