"""Deleting a case: what goes, what stays, and what is left provable.

Deletion is the only destructive operation in the API, so most of what is
asserted here is restraint rather than effect:

* The case, its evidence, its analyses, its matches, its timeline and its
  reports go -- rows *and* bytes. A row without its file, or a file without its
  row, is a case file that cannot be explained.
* Nothing else goes. Another case keeps every row and every byte, the shared
  corpus is untouched, and the global perceptual index loses exactly the deleted
  vectors and no others.
* The deletion is itself evidence. ``CASE_DELETED`` is appended to the hash chain
  in the same transaction as the delete, carries the case number and measured
  counts, and stays verifiable after the case it describes no longer exists.
* A failure before the commit deletes nothing at all -- not the rows and, because
  the filesystem is only touched after the commit, not the files either.
* A path recorded in the database is data, not an instruction: a ``stored_path``
  that escapes the evidence root, or points at the shared index or corpus, is
  refused and reported instead of followed.

The counts in the response are read back from the create/upload responses, never
written as literals, so a test cannot pass by agreeing with a hardcoded number.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.services import audit as audit_service
from app.services import storage
from tests.helpers import jpeg_bytes


# --------------------------------------------------------------------------- #
# Helpers -- every value used in an assertion comes back from the API
# --------------------------------------------------------------------------- #
def _upload(
    client: TestClient,
    *,
    seed: int,
    name: str,
    case_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, str] = {"case_id": case_id} if case_id else {}
    res = client.post(
        "/api/cases/upload",
        files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")},
        data=data,
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _make_case(client: TestClient, *, seed: int, name: str, extra: int = 0) -> dict[str, Any]:
    """Create a case by uploading, then add ``extra`` more items to it."""
    first = _upload(client, seed=seed, name=name)
    case_id = first["case"]["case_id"]
    evidence = [first["evidence"]]
    for offset in range(extra):
        more = _upload(
            client, seed=seed + 1 + offset, name=f"{offset}-{name}", case_id=case_id
        )
        evidence.append(more["evidence"])
    return {"case": first["case"], "case_id": case_id, "evidence": evidence}


def _stored_file(evidence: dict[str, Any], settings: Settings) -> Path:
    return storage.absolute_path(evidence["stored_path"], settings)


def _events(client: TestClient, case_id: str) -> list[dict[str, Any]]:
    """The chain filtered to one case -- readable after the case is deleted."""
    res = client.get("/api/audit", params={"case_id": case_id, "limit": 5000})
    assert res.status_code == 200, res.text
    return res.json()["events"]


def _indexed(settings: Settings, evidence_id: str) -> bool:
    """Whether the global perceptual index still holds a vector for this id."""
    from app.services.index import get_index

    return get_index(settings).contains(evidence_id)


# --------------------------------------------------------------------------- #
# The happy path, and the numbers it reports
# --------------------------------------------------------------------------- #
def test_delete_returns_200_and_measured_counts(client: TestClient) -> None:
    """A successful delete reports what it removed, echoing the real case."""
    made = _make_case(client, seed=71001, name="delete-counts.jpg", extra=2)
    case_id = made["case_id"]
    expected_evidence = len(made["evidence"])

    res = client.delete(f"/api/cases/{case_id}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["status"] == "deleted"
    assert body["case_id"] == case_id
    assert body["case_number"] == made["case"]["case_number"]
    assert body["deleted_evidence_count"] == expected_evidence
    assert body["deleted"]["evidence"] == expected_evidence
    # The top-level convenience field and the breakdown cannot disagree.
    assert body["deleted"]["evidence"] == body["deleted_evidence_count"]
    assert body["storage"]["evidence_files_removed"] == expected_evidence
    assert body["storage"]["evidence_files_missing"] == 0
    assert body["storage"]["case_directory_removed"] is True
    assert body["deleted_at"], "the delete must report when it happened"
    assert body["warnings"] == []


def test_delete_nonexistent_case_returns_404(client: TestClient) -> None:
    """An unknown id is refused, not treated as an empty success."""
    res = client.delete("/api/cases/no-such-case-id")
    assert res.status_code == 404, res.text
    assert "not found" in res.json()["error"]["message"].lower()


def test_repeated_delete_does_not_silently_succeed(client: TestClient) -> None:
    """The second delete of the same case is a 404, never another 200."""
    made = _make_case(client, seed=71011, name="delete-twice.jpg")
    case_id = made["case_id"]

    assert client.delete(f"/api/cases/{case_id}").status_code == 200
    second = client.delete(f"/api/cases/{case_id}")
    assert second.status_code == 404, second.text
    assert case_id in second.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# The case, and everything it owned, is actually gone
# --------------------------------------------------------------------------- #
def test_deleted_case_disappears_from_every_read_path(client: TestClient) -> None:
    """Not just the detail route: the list and the global library too."""
    made = _make_case(client, seed=71021, name="delete-reads.jpg", extra=1)
    case_id = made["case_id"]
    evidence_ids = {ev["evidence_id"] for ev in made["evidence"]}

    listed = client.get("/api/cases", params={"limit": 500})
    assert any(row["case_id"] == case_id for row in listed.json()["cases"])

    assert client.delete(f"/api/cases/{case_id}").status_code == 200

    assert client.get(f"/api/cases/{case_id}").status_code == 404
    assert client.get(f"/api/cases/{case_id}/evidence").status_code == 404
    after = client.get("/api/cases", params={"limit": 500})
    assert after.status_code == 200
    assert all(row["case_id"] != case_id for row in after.json()["cases"])

    library = client.get("/api/cases/library/all", params={"limit": 500})
    assert library.status_code == 200
    remaining = {row["evidence_id"] for row in library.json()["evidence"]}
    assert not (evidence_ids & remaining)


def test_owned_rows_are_cascade_deleted(client: TestClient) -> None:
    """Analyses, matches, timeline rows and reports go with the case."""
    made = _make_case(client, seed=71031, name="delete-cascade.jpg", extra=1)
    case_id = made["case_id"]
    evidence_ids = [ev["evidence_id"] for ev in made["evidence"]]

    assert client.post(f"/api/cases/{case_id}/analyse").status_code in (200, 201)
    assert client.post(f"/api/cases/{case_id}/report").status_code in (200, 201)

    from app.models import (
        AnalysisResult,
        Case,
        Evidence,
        Match,
        Report,
        TimelineEvent,
        get_session_factory,
    )

    session = get_session_factory()()
    try:
        before = session.query(AnalysisResult).filter_by(case_id=case_id).count()
        assert before > 0, "the analysis run must have stored something to delete"
    finally:
        session.close()

    body = client.delete(f"/api/cases/{case_id}").json()
    assert body["deleted"]["analysis_results"] >= before
    assert body["deleted"]["reports"] >= 1

    session = get_session_factory()()
    try:
        assert session.get(Case, case_id) is None
        for evidence_id in evidence_ids:
            assert session.get(Evidence, evidence_id) is None
        for model in (AnalysisResult, Match, TimelineEvent, Report):
            assert session.query(model).filter_by(case_id=case_id).count() == 0
        # Rows keyed on the evidence rather than the case must go too.
        assert (
            session.query(AnalysisResult)
            .filter(AnalysisResult.evidence_id.in_(evidence_ids))
            .count()
            == 0
        )
        assert (
            session.query(Match)
            .filter(Match.query_evidence_id.in_(evidence_ids))
            .count()
            == 0
        )
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Blast radius: everything that must survive
# --------------------------------------------------------------------------- #
def test_unrelated_case_and_its_evidence_survive(
    client: TestClient, settings: Settings
) -> None:
    """Deleting one case leaves the other's rows, bytes and hashes untouched."""
    doomed = _make_case(client, seed=71041, name="delete-target.jpg", extra=1)
    keeper = _make_case(client, seed=71051, name="delete-bystander.jpg", extra=1)
    keeper_files = [_stored_file(ev, settings) for ev in keeper["evidence"]]
    keeper_hashes = {ev["evidence_id"]: ev["sha256"] for ev in keeper["evidence"]}

    assert client.delete(f"/api/cases/{doomed['case_id']}").status_code == 200

    survivor = client.get(f"/api/cases/{keeper['case_id']}")
    assert survivor.status_code == 200
    assert survivor.json()["evidence_count"] == len(keeper["evidence"])

    listed = client.get(f"/api/cases/{keeper['case_id']}/evidence")
    assert listed.status_code == 200
    still_there = {row["evidence_id"]: row["sha256"] for row in listed.json()["evidence"]}
    assert still_there == keeper_hashes

    for path in keeper_files:
        assert path.is_file(), f"bystander evidence file was removed: {path}"

    # The bytes are unchanged, not merely present.
    for evidence_id, digest in keeper_hashes.items():
        fetched = client.get(f"/api/evidence/{evidence_id}/file")
        assert fetched.status_code == 200
        assert hashlib.sha256(fetched.content).hexdigest() == digest


def test_shared_corpus_and_index_assets_are_not_touched(
    client: TestClient, settings: Settings
) -> None:
    """A case delete must never reach the shared corpus or the index files."""
    ingested = client.post(
        "/api/index/ingest",
        files={"file": ("corpus-keep.jpg", jpeg_bytes(seed=71061), "image/jpeg")},
    )
    assert ingested.status_code in (200, 201), ingested.text
    corpus_evidence_id = ingested.json()["evidence"]["evidence_id"]

    library = client.get("/api/cases/library/all", params={"limit": 500})
    corpus_row = next(
        row
        for row in library.json()["evidence"]
        if row["evidence_id"] == corpus_evidence_id
    )
    corpus_file = _stored_file(corpus_row, settings)
    assert corpus_file.is_file()

    made = _make_case(client, seed=71071, name="delete-vs-corpus.jpg")
    assert client.delete(f"/api/cases/{made['case_id']}").status_code == 200

    assert corpus_file.is_file(), "the shared corpus file was deleted"
    assert (settings.evidence_dir / "corpus").is_dir()
    assert client.get(f"/api/evidence/{corpus_evidence_id}").status_code == 200
    assert _indexed(settings, corpus_evidence_id), "corpus vector was dropped"


def test_stored_case_files_and_directory_are_removed(
    client: TestClient, settings: Settings
) -> None:
    """Rows and bytes go together; the case's own directory goes with them."""
    made = _make_case(client, seed=71081, name="delete-files.jpg", extra=2)
    case_id = made["case_id"]
    files = [_stored_file(ev, settings) for ev in made["evidence"]]
    for path in files:
        assert path.is_file()
    case_dir = settings.evidence_dir / "cases" / case_id
    assert case_dir.is_dir()

    body = client.delete(f"/api/cases/{case_id}").json()

    for path in files:
        assert not path.exists(), f"evidence file survived the delete: {path}"
    assert not case_dir.exists()
    assert body["storage"]["evidence_files_removed"] == len(files)
    assert body["storage"]["case_directory"] == f"evidence/cases/{case_id}"
    # The parent bucket is shared with every other case and must remain.
    assert (settings.evidence_dir / "cases").is_dir()


def test_already_missing_file_is_reported_not_fatal(
    client: TestClient, settings: Settings
) -> None:
    """A file the filesystem already lost is counted, and the delete still runs."""
    made = _make_case(client, seed=71091, name="delete-missing.jpg", extra=1)
    vanished = _stored_file(made["evidence"][0], settings)
    vanished.unlink()

    res = client.delete(f"/api/cases/{made['case_id']}")
    assert res.status_code == 200, res.text
    storage_report = res.json()["storage"]
    assert storage_report["evidence_files_missing"] == 1
    assert storage_report["evidence_files_removed"] == len(made["evidence"]) - 1
    assert client.get(f"/api/cases/{made['case_id']}").status_code == 404


def test_report_pdf_is_removed_from_the_shared_reports_directory(
    client: TestClient, settings: Settings
) -> None:
    """Reports live in one flat directory, so each PDF is removed by name."""
    made = _make_case(client, seed=71101, name="delete-report.jpg")
    case_id = made["case_id"]
    assert client.post(f"/api/cases/{case_id}/analyse").status_code in (200, 201)
    generated = client.post(f"/api/cases/{case_id}/report")
    assert generated.status_code in (200, 201), generated.text

    listed = client.get(f"/api/cases/{case_id}/reports")
    assert listed.status_code == 200
    stored_names = [row["filename"] for row in listed.json()["reports"]]
    assert stored_names
    pdfs = [settings.reports_dir / name for name in stored_names]
    for pdf in pdfs:
        assert pdf.is_file(), f"report PDF was not written: {pdf}"

    body = client.delete(f"/api/cases/{case_id}").json()
    assert body["deleted"]["reports"] == len(stored_names)
    assert body["storage"]["report_files_removed"] == len(pdfs)
    for pdf in pdfs:
        assert not pdf.exists(), f"report PDF survived the delete: {pdf}"
    assert settings.reports_dir.is_dir()


def test_index_loses_only_the_deleted_vectors(
    client: TestClient, settings: Settings
) -> None:
    """The global index is pruned exactly, and stays exact for everyone else."""
    doomed = _make_case(client, seed=71111, name="delete-indexed.jpg", extra=1)
    keeper = _make_case(client, seed=71121, name="keep-indexed.jpg")
    doomed_ids = [ev["evidence_id"] for ev in doomed["evidence"]]
    keeper_id = keeper["evidence"][0]["evidence_id"]

    for evidence_id in [*doomed_ids, keeper_id]:
        added = client.post(f"/api/index/add/{evidence_id}")
        assert added.status_code in (200, 201), added.text
        assert _indexed(settings, evidence_id)

    before = client.get("/api/index/status").json()
    body = client.delete(f"/api/cases/{doomed['case_id']}").json()

    assert body["index"]["vectors_removed"] == len(doomed_ids)
    assert body["index"]["rebuild_required"] is False
    for evidence_id in doomed_ids:
        assert not _indexed(settings, evidence_id)
    assert _indexed(settings, keeper_id), "an unrelated vector was dropped"

    after = client.get("/api/index/status").json()
    assert after["indexed_count"] == before["indexed_count"] - len(doomed_ids)
    assert after["index_version"] > before["index_version"]
    assert after["exact_search"] is True


# --------------------------------------------------------------------------- #
# The deletion is itself evidence
# --------------------------------------------------------------------------- #
def test_case_deleted_event_is_recorded_with_the_real_case_details(
    client: TestClient,
) -> None:
    """``CASE_DELETED`` names the case and carries measured counts."""
    made = _make_case(client, seed=71131, name="delete-audited.jpg", extra=1)
    case_id = made["case_id"]
    case_number = made["case"]["case_number"]
    evidence_ids = sorted(ev["evidence_id"] for ev in made["evidence"])

    body = client.delete(f"/api/cases/{case_id}").json()

    events = _events(client, case_id)
    deletions = [row for row in events if row["event"] == audit_service.EVENT_CASE_DELETED]
    assert len(deletions) == 1, "exactly one deletion event, recorded once"
    entry = deletions[0]

    assert entry["case_id"] == case_id
    assert entry["actor"] == "api"
    assert entry["timestamp"]
    details = entry["details"]
    assert details["case_number"] == case_number
    assert details["examiner"] == made["case"]["examiner"]
    assert details["deleted_evidence_count"] == len(evidence_ids)
    assert sorted(details["deleted_evidence_ids"]) == evidence_ids
    assert details["deleted_rows"]["evidence"] == len(evidence_ids)
    assert details["deletion_type"] == "hard_delete"
    assert details["audit_history_retained"] is True

    # The response echoes the entry that was actually written to the chain.
    assert body["audit"]["audit_id"] == entry["audit_id"]
    assert body["audit"]["seq"] == entry["seq"]
    assert body["audit"]["row_hash"] == entry["row_hash"]
    assert body["audit"]["previous_hash"] == entry["previous_hash"]
    assert body["audit"]["event"] == audit_service.EVENT_CASE_DELETED
    assert body["audit"]["retained"] is True


def test_case_history_survives_the_case(client: TestClient) -> None:
    """Every earlier event for the case is still readable and still linked."""
    made = _make_case(client, seed=71141, name="delete-history.jpg")
    case_id = made["case_id"]
    before = [row["audit_id"] for row in _events(client, case_id)]
    assert audit_service.EVENT_CASE_CREATED in {
        row["event"] for row in _events(client, case_id)
    }

    assert client.delete(f"/api/cases/{case_id}").status_code == 200

    after = _events(client, case_id)
    assert [row["audit_id"] for row in after][: len(before)] == before
    assert len(after) == len(before) + 1
    assert after[-1]["event"] == audit_service.EVENT_CASE_DELETED


def test_audit_chain_still_verifies_after_deletion(client: TestClient) -> None:
    """The chain is global: removing a case must not break a single link."""
    made = _make_case(client, seed=71151, name="delete-chain.jpg", extra=1)
    case_id = made["case_id"]

    before = client.post("/api/audit/verify", params={"record": "false"})
    assert before.status_code == 200
    assert before.json()["valid"] is True

    body = client.delete(f"/api/cases/{case_id}").json()

    after = client.post("/api/audit/verify", params={"record": "false"})
    assert after.status_code == 200
    verified = after.json()
    assert verified["valid"] is True, verified["issues"]
    assert verified["first_invalid_seq"] is None
    assert verified["issues"] == []
    assert verified["total_rows"] > before.json()["total_rows"]
    # The deletion event is a verified row of that chain, still readable by case.
    retained = _events(client, case_id)
    assert body["audit"]["row_hash"] in {row["row_hash"] for row in retained}
    assert body["audit"]["case_rows_retained"] == len(retained)


# --------------------------------------------------------------------------- #
# Failure before the commit deletes nothing at all
# --------------------------------------------------------------------------- #
def test_failure_before_commit_rolls_back_rows_and_files(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the audit append fails, the case, its rows and its bytes all remain.

    This is the reason the filesystem is only touched *after* the commit. The
    audit entry and the row deletion share one transaction, so a failure in
    either rolls back both -- and because no file has been unlinked yet, the
    evidence is still on disk and still matches its recorded digest.
    """
    made = _make_case(client, seed=71161, name="delete-rollback.jpg", extra=1)
    case_id = made["case_id"]
    files = [_stored_file(ev, settings) for ev in made["evidence"]]
    digests = {ev["evidence_id"]: ev["sha256"] for ev in made["evidence"]}

    def refuse_to_record(*args: object, **kwargs: object) -> object:
        raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr(audit_service, "record", refuse_to_record)
    with pytest.raises(RuntimeError, match="audit backend unavailable"):
        client.delete(f"/api/cases/{case_id}")
    monkeypatch.undo()

    # The case is untouched: row, children, files and hashes.
    survived = client.get(f"/api/cases/{case_id}")
    assert survived.status_code == 200, "the case was deleted despite the failure"
    assert survived.json()["evidence_count"] == len(files)
    for path in files:
        assert path.is_file(), f"a file was removed before the commit: {path}"
    for evidence_id, digest in digests.items():
        fetched = client.get(f"/api/evidence/{evidence_id}/file")
        assert fetched.status_code == 200
        assert hashlib.sha256(fetched.content).hexdigest() == digest

    # No half-written deletion event was left in the chain either.
    events = _events(client, case_id)
    assert audit_service.EVENT_CASE_DELETED not in {row["event"] for row in events}
    verified = client.post("/api/audit/verify", params={"record": "false"}).json()
    assert verified["valid"] is True, verified["issues"]

    # And the case is still deletable once the fault clears.
    assert client.delete(f"/api/cases/{case_id}").status_code == 200


# --------------------------------------------------------------------------- #
# A stored path is data, not an instruction
# --------------------------------------------------------------------------- #
def test_stored_paths_outside_case_storage_are_refused_not_followed(
    client: TestClient, settings: Settings, test_root: Path
) -> None:
    """Three tampered ``stored_path`` values, three refusals, nothing deleted.

    Each one is a path that resolves to a real file the delete must not touch: a
    file outside ``data_dir`` entirely, the shared perceptual-index vector file,
    and a file in the shared corpus bucket. The row is still deleted -- the
    database is authoritative -- but the bytes are left alone and each refusal is
    reported in ``warnings`` rather than passed over in silence.
    """
    outside = test_root / "delete-must-not-reach-this.bin"
    outside.write_bytes(b"outside the data root")
    index_asset = settings.index_dir / "phash_vectors.npy"
    index_asset.parent.mkdir(parents=True, exist_ok=True)
    if not index_asset.is_file():
        index_asset.write_bytes(b"index vectors")
    corpus_asset = settings.evidence_dir / "corpus" / "items" / "shared-corpus-item.jpg"
    corpus_asset.parent.mkdir(parents=True, exist_ok=True)
    corpus_asset.write_bytes(jpeg_bytes(seed=71171))

    made = _make_case(client, seed=71181, name="delete-traversal.jpg", extra=2)
    case_id = made["case_id"]
    tampered = {
        made["evidence"][0]["evidence_id"]: f"../{outside.name}",
        made["evidence"][1]["evidence_id"]: "index/phash_vectors.npy",
        made["evidence"][2]["evidence_id"]: "evidence/corpus/items/shared-corpus-item.jpg",
    }

    from app.models import Evidence, get_session_factory

    session = get_session_factory()()
    try:
        for evidence_id, path in tampered.items():
            row = session.get(Evidence, evidence_id)
            assert row is not None
            row.stored_path = path
        session.commit()
    finally:
        session.close()

    res = client.delete(f"/api/cases/{case_id}")
    assert res.status_code == 200, res.text
    body = res.json()

    assert outside.is_file(), "a delete escaped the data root"
    assert index_asset.is_file(), "a delete removed the shared index vectors"
    assert corpus_asset.is_file(), "a delete removed a shared corpus item"

    assert len(body["warnings"]) == len(tampered), body["warnings"]
    assert any("escapes the storage root" in w for w in body["warnings"])
    assert sum("case evidence storage" in w for w in body["warnings"]) == 2
    assert body["storage"]["evidence_files_removed"] == 0
    # The rows still go: the case file is the database, not the filesystem.
    assert client.get(f"/api/cases/{case_id}").status_code == 404
    assert body["deleted"]["evidence"] == len(tampered)


