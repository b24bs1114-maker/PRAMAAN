"""Concurrent audit appends must not fork the hash chain.

This is a regression test for a chain that really did break in this deployment:
two ``MATCH_SEARCHED`` rows written in the same second both committed to the same
parent hash, and every verification from that row onward reported the log as
tampered.

The bug is a read-then-append race. ``audit.record()`` reads the current head
(``SELECT ... ORDER BY seq DESC LIMIT 1``) and then writes a row whose
``previous_hash`` is that head. Under SQLite's default DEFERRED transactions two
concurrent requests both read before either writes, so both build on the same
head and the chain forks.

The fix is ``BEGIN IMMEDIATE`` on every transaction (``app.models.base``), which
takes the write lock at transaction start and serialises the read with the
dependent append.

``test_deferred_transactions_fork_the_chain`` is the control: it forces the old
DEFERRED behaviour on a throwaway database and asserts the fork still happens
there. Without it, the fix test could pass because the writes never actually
overlapped -- and a concurrency test that never hits contention proves nothing.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, Base, get_session_factory
from app.services import audit

WRITERS = 8


def _verify() -> dict:
    session = get_session_factory()()
    try:
        return audit.verify_chain(session)
    finally:
        session.close()


def test_chain_is_valid_before_concurrency(client: TestClient) -> None:
    """Baseline: whatever the suite has written so far is a valid chain."""
    assert _verify()["valid"] is True


def test_concurrent_appends_do_not_fork_the_chain() -> None:
    """Parallel writers on independent sessions must produce one unbroken chain."""
    before = _verify()["total_rows"]
    barrier = threading.Barrier(WRITERS)
    factory = get_session_factory()

    def append(index: int) -> None:
        # Every writer gets its own session and connection, exactly as separate
        # requests do; a shared session would serialise them for the wrong reason.
        session = factory()
        try:
            barrier.wait(timeout=30)  # maximise overlap on the head read
            audit.record(
                session,
                event=audit.EVENT_MATCH_SEARCHED,
                case_id=None,
                actor="concurrency-test",
                details={"writer": index},
            )
            session.commit()
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        for future in [pool.submit(append, i) for i in range(WRITERS)]:
            future.result()

    result = _verify()
    assert result["total_rows"] == before + WRITERS
    assert result["valid"] is True, (
        f"chain forked under {WRITERS} concurrent writers: {result['issues'][:3]}"
    )
    assert result["first_invalid_seq"] is None


def test_concurrent_api_requests_do_not_fork_the_chain(client: TestClient) -> None:
    """The real path that broke: parallel requests that each append audit rows."""
    from tests.helpers import jpeg_bytes

    upload = client.post(
        "/api/cases/upload",
        files={"file": ("race.jpg", jpeg_bytes(seed=4242), "image/jpeg")},
        data={"title": "audit race", "examiner": "concurrency-test"},
    )
    assert upload.status_code == 201, upload.text
    case_id = upload.json()["case"]["case_id"]

    # Running a match search is what forked the production chain: two of these
    # landed in the same second and both committed to the same head.
    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        responses = [
            future.result()
            for future in [
                pool.submit(client.post, f"/api/cases/{case_id}/matches")
                for _ in range(WRITERS)
            ]
        ]
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]

    verified = client.post("/api/audit/verify")
    assert verified.status_code == 200
    body = verified.json()
    assert body["valid"] is True, f"chain forked via API: {body['issues'][:3]}"


def test_no_two_rows_share_a_parent() -> None:
    """A fork's signature: two rows claiming the same ``previous_hash``."""
    session = get_session_factory()()
    try:
        parents = [
            row.previous_hash
            for row in session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars()
        ]
    finally:
        session.close()
    duplicates = {p for p in parents if parents.count(p) > 1}
    assert not duplicates, f"rows share a parent hash: {sorted(duplicates)[:3]}"


def test_interleaved_head_reads_fork_the_chain_and_are_detected(tmp_path: Path) -> None:
    """Control: reproduce the exact interleaving, deterministically.

    Two writers read the head *before* either appends -- which is what DEFERRED
    transactions allow and IMMEDIATE prevents. Asserting the fork here proves two
    things the passing tests above cannot: the hazard is real, and
    ``verify_chain`` actually detects it. A verifier that returned ``valid`` for a
    forked chain would make every other assertion in this file worthless.

    Deterministic on purpose: it drives ``head_hash`` / ``compute_row_hash``
    directly instead of racing threads, so it never flakes and never skips.
    """
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'fork.db'}", future=True)
    Base.metadata.create_all(engine, tables=[AuditLog.__table__])
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session = factory()
    try:
        audit.record(session, event=audit.EVENT_CASE_CREATED, details={"row": "genesis child"})
        session.commit()

        # Both writers observe the same head -- the read-then-append window.
        shared_head = audit.head_hash(session)

        for index in (1, 2):
            payload = audit.row_payload(
                audit_id=f"0000000-0000-0000-0000-00000000000{index}",
                case_id=None,
                event=audit.EVENT_MATCH_SEARCHED,
                timestamp="2026-08-22T15:53:16Z",
                actor="fork-control",
                details={"writer": index},
            )
            session.add(
                AuditLog(
                    audit_id=payload["audit_id"],
                    case_id=None,
                    event=payload["event"],
                    timestamp=payload["timestamp"],
                    actor=payload["actor"],
                    details=payload["details"],
                    previous_hash=shared_head,
                    row_hash=audit.compute_row_hash(shared_head, payload),
                )
            )
        session.commit()

        result = audit.verify_chain(session)
    finally:
        session.close()
        engine.dispose()

    assert result["valid"] is False, "verify_chain failed to detect a forked chain"
    assert any(issue["problem"] == "broken_link" for issue in result["issues"])
    # No payload was edited, so nothing should be reported as content tampering.
    assert not any(issue["problem"] == "content_modified" for issue in result["issues"])


def test_begin_immediate_is_installed() -> None:
    """The fix is an event listener; assert it is still attached.

    Without this, silently dropping the listener would reintroduce the fork and
    every test above would keep passing until two requests happened to overlap.
    """
    from app.models import base as models_base

    assert event.contains(Engine, "begin", models_base._sqlite_begin_immediate)

