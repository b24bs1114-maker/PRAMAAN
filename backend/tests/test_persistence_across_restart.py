"""Does the chain of custody survive a restart? (Phase 12 regression.)

Every other test in this suite runs against a ``TestClient`` inside the pytest
process. That cannot answer this question: a fixture reset is not a restart. The
failure being guarded against is the deployed one -- the container is replaced,
a new interpreter starts, and it has to find the case database, the stored
evidence bytes and the append-only audit chain exactly as the exited process left
them. So each boot below is a **separate OS process** against one
``PRAMAAN_DATA_DIR``:

    ingest    a fresh store: seed an operator, sign in, upload one image, report
              what was written
    observe   new interpreter, same store: sign in against the account the last
              boot seeded -- nothing can be read without a token -- then check that
              it is all still there, that the chain still verifies, that this
              boot's own sign-in linked onto the head hash the exited process left,
              and that appending further events keeps it verifying
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

#: Ingesting one image writes exactly these, in this order. The login comes
#: first: intake is authenticated, and signing in is itself an auditable act, so
#: a cold boot that ingests one file starts its chain with the operator's
#: sign-in and not with the case.
INGEST_EVENTS = (
    "USER_LOGIN",
    "CASE_CREATED",
    "EVIDENCE_INGESTED",
    "HASH_CALCULATED",
    "PERCEPTUAL_HASH_CALCULATED",
    "INDEX_UPDATED",
)

#: The chain construction. Recorded here so that changing it -- to a Merkle tree,
#: say -- fails a test rather than silently altering what a verified chain means.
ALGORITHM = "SHA-256(previous_hash || canonical_json(payload))"

CASE_TITLE = "Persistence across a process restart"
CASE_DESCRIPTION = "Ingested once, then re-read from a new interpreter."
#: The operator each boot signs in as. Pinned here rather than relying on the
#: shipped defaults, so this test states the credentials it depends on. The
#: examiner recorded against the case is this operator's display name -- intake
#: takes no examiner field.
OPERATOR_USERNAME = "restart-operator"
OPERATOR_PASSWORD = "restart-regression-password"
EXAMINER = "Restart Regression Examiner"


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
            # The operator the first boot seeds and every boot signs in as. Pinned
            # so this test does not depend on the shipped placeholder credentials,
            # and so the seeding itself is exercised on a genuinely empty store.
            "PRAMAAN_SEED_OPERATOR_USERNAME": OPERATOR_USERNAME,
            "PRAMAAN_SEED_OPERATOR_DISPLAY_NAME": EXAMINER,
            "PRAMAAN_SEED_OPERATOR_PASSWORD": OPERATOR_PASSWORD,
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


def _sign_in(client) -> dict:
    """Sign in for real and leave the bearer token on ``client``.

    No dependency override: the point of a separate process is that nothing from
    the pytest interpreter reaches it, and that includes ``conftest.py``'s stand-in
    operator. This boot has to seed an account, authenticate against it and carry
    the token, exactly as a browser does.
    """
    response = client.post(
        "/api/auth/login",
        json={"username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD},
    )
    response.raise_for_status()
    body = response.json()
    client.headers["Authorization"] = f"{body['token_type']} {body['token']}"
    return {
        "status_code": response.status_code,
        "token_type": body["token_type"],
        "username": body["user"]["username"],
        "display_name": body["user"]["display_name"],
    }


def _audit(client) -> dict:
    trail = client.get("/api/audit", params={"limit": 5000}).json()
    return {
        "count": len(trail["events"]),
        "head_hash": trail["head_hash"],
        "genesis_hash": trail["genesis_hash"],
        "algorithm": trail["algorithm"],
        "events": [event.get("event") for event in trail["events"]],
        # Row identity, not just the event names: "the chain survived" is then
        # checkable as "these exact rows came back", which a list of labels cannot
        # distinguish from a chain rewritten with the same shape.
        "rows": [
            {
                "seq": event.get("seq"),
                "event": event.get("event"),
                "previous_hash": event.get("previous_hash"),
                "row_hash": event.get("row_hash"),
            }
            for event in trail["events"]
        ],
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
        login = _sign_in(client)
        response = client.post(
            "/api/cases/upload",
            files={"file": ("restart-regression.png", payload, "image/png")},
            data={"title": CASE_TITLE, "description": CASE_DESCRIPTION},
        )
        response.raise_for_status()
        body = response.json()
        evidence = body["evidence"]
        return {
            "status_code": response.status_code,
            "login": login,
            "case_id": body["case"]["case_id"],
            "case_number": body["case"]["case_number"],
            "case_examiner": body["case"]["examiner"],
            "evidence_id": evidence["evidence_id"],
            "sha256": evidence.get("sha256") or evidence.get("hashes", {}).get("sha256"),
            "uploaded_bytes": len(payload),
            "uploaded_sha256": hashlib.sha256(payload).hexdigest(),
            "audit": _audit(client),
            "verify": _verify(client),
        }


def _phase_observe() -> dict:
    """The restart boot. Ordering matters and is the point.

    The chain cannot be read anonymously: ``/api/audit`` is authenticated like
    everything else that touches case material, and signing in is itself an
    audited act. So this boot cannot look at the store without leaving exactly one
    mark on it, and the honest thing is to put that mark first and name it. The
    sign-in is therefore the boot's first and only footprint before the
    measurement, which is what lets the tests state precisely what must hold: the
    rows the exited process wrote come back unchanged, and this boot's login
    chains onto the head hash that process left behind.
    """
    with _client() as client:
        boot_login = _sign_in(client)
        observed: dict[str, object] = {
            "boot_login": boot_login,
            "audit_at_boot": _audit(client),
            "verify_at_boot": _verify(client),
        }
        state = json.loads((Path(os.environ["PRAMAAN_DATA_DIR"]).parent / "ingest.json").read_text())
        case_id, evidence_id = state["case_id"], state["evidence_id"]

        case = client.get(f"/api/cases/{case_id}")
        observed["case_status_code"] = case.status_code
        observed["case_title"] = case.json().get("title") if case.status_code == 200 else None
        observed["case_number"] = case.json().get("case_number") if case.status_code == 200 else None
        observed["case_examiner"] = case.json().get("examiner") if case.status_code == 200 else None

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

        # A second sign-in, at the end. That the seeded account survived is already
        # settled by `boot_login` above -- this boot could not have read anything
        # otherwise. What this adds is the accounting: one sign-in appends exactly
        # one row, and the chain still verifies with it in place.
        observed["relogin"] = _sign_in(client)
        observed["audit_after_relogin"] = _audit(client)
        observed["verify_after_relogin"] = _verify(client)
        return observed


def _phase_check() -> dict:
    """The post-tamper boot. Signs in first, for the same reason ``observe`` does.

    Appending that row cannot conceal the tamper: ``record()`` chains onto the
    *stored* head hash, so the edited row stays the earliest one whose recomputed
    hash disagrees with what is on disk.
    """
    with _client() as client:
        login = _sign_in(client)
        return {"login": login, "verify": _verify(client), "audit": _audit(client)}


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
    """A new interpreter, a new engine, a new connection pool, the same rows.

    The restart boot has to authenticate before it can read anything, and signing
    in appends a row, so the chain it reads is the surviving chain *plus one*. That
    makes the claim sharper rather than weaker, and all four parts of it are
    checked here: every row the exited process wrote comes back with the same seq,
    event and row_hash; the genesis is unchanged; the new boot's first row links
    onto the head hash the old process left -- which is what makes the restart
    boundary a link in the chain and not a seam in it -- and the whole thing still
    verifies.
    """
    before, after = ingested["audit"], reopened["audit_at_boot"]
    surviving = before["count"]

    assert after["count"] == surviving + 1, (
        f"{after['count']} audit rows after the restart; expected the {surviving} "
        "that were written before it, plus this boot's own sign-in"
    )
    assert after["rows"][:surviving] == before["rows"], (
        "the rows written before the restart did not come back unchanged"
    )
    assert after["events"][:surviving] == before["events"], after["events"]
    assert after["genesis_hash"] == before["genesis_hash"], "the genesis hash changed"

    boot_row = after["rows"][surviving]
    assert boot_row["event"] == "USER_LOGIN", (
        f"the only row this boot should have added is its sign-in, not {boot_row}"
    )
    assert boot_row["previous_hash"] == before["head_hash"], (
        "this boot's first row does not chain onto the head hash the exited "
        f"process left: {boot_row['previous_hash']} vs {before['head_hash']}"
    )
    assert after["head_hash"] == boot_row["row_hash"], (
        "the head after the restart is not the row this boot appended"
    )
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


def test_the_examiner_recorded_is_the_signed_in_operator(ingested: dict) -> None:
    """Intake takes no examiner field: the name on the case is the display name of
    whoever was authenticated, which is the only reason it can be trusted."""
    assert ingested["login"]["status_code"] == 200, ingested["login"]
    assert ingested["login"]["username"] == OPERATOR_USERNAME, ingested["login"]
    assert ingested["login"]["display_name"] == EXAMINER, ingested["login"]
    assert ingested["case_examiner"] == EXAMINER, (
        f"the case records {ingested['case_examiner']!r} as examiner, but the "
        f"operator who signed in to ingest it is {EXAMINER!r}"
    )


def test_the_examiner_and_case_number_carry_over_a_restart(
    ingested: dict, reopened: dict
) -> None:
    """Both are database columns, so both have to come back unchanged. The case
    number is also checked for shape: a compact sequential identifier, not a UUID."""
    assert reopened["case_examiner"] == ingested["case_examiner"] == EXAMINER, (
        f"the examiner read back after the restart is {reopened['case_examiner']!r}"
    )
    assert reopened["case_number"] == ingested["case_number"], (
        f"the case number changed across the restart: {ingested['case_number']!r} "
        f"became {reopened['case_number']!r}"
    )
    number = ingested["case_number"]
    assert isinstance(number, str) and number.startswith("PRAMAAN-"), number
    sequence = number.removeprefix("PRAMAAN-")
    assert sequence.isdigit(), f"{number!r} is not PRAMAAN-<digits>"
    assert int(sequence) >= 1001, f"the first issued number should be 1001 or later: {number}"


def test_the_operator_account_survives_a_restart(reopened: dict) -> None:
    """Credentials are not process state. A new interpreter authenticates against
    the row the previous one seeded, and that sign-in chains onto the log it left.

    Two sign-ins are checked, because they answer different questions. The boot
    login is the one that proves the account survived -- this boot could not have
    read the chain at all without it. The second one is the arithmetic: a sign-in
    appends exactly one row, no more, and the chain still verifies afterwards.
    """
    boot_login = reopened["boot_login"]
    assert boot_login["status_code"] == 200, boot_login
    assert boot_login["username"] == OPERATOR_USERNAME, boot_login
    assert boot_login["display_name"] == EXAMINER, boot_login

    relogin = reopened["relogin"]
    assert relogin["status_code"] == 200, relogin
    assert relogin["display_name"] == EXAMINER, relogin
    assert reopened["audit_after_relogin"]["events"][-1] == "USER_LOGIN", (
        reopened["audit_after_relogin"]["events"][-3:]
    )
    assert (
        reopened["audit_after_relogin"]["count"]
        == reopened["audit_after_record"]["count"] + 1
    ), "signing in wrote something other than exactly one row"
    assert reopened["verify_after_relogin"]["valid"] is True, reopened["verify_after_relogin"]


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
