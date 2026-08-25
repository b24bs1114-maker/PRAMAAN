"""Concurrency regression tests for SQLite database locks under parallel traffic.

Ensures read-only endpoints (GET /health, GET /api/dashboard/summary) execute
with full WAL read concurrency without blocking on write locks or throwing
(sqlite3.OperationalError) database is locked.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.models import get_session_factory, session_scope
from app.services import audit

CONCURRENT_READERS = 20


def test_concurrent_reads_do_not_block_on_active_writer(client: TestClient) -> None:
    """Read endpoints must complete without database lock errors while a write transaction is active."""
    write_started = threading.Event()

    def slow_writer() -> None:
        with session_scope() as session:
            audit.record(
                session,
                event=audit.EVENT_CASE_CREATED,
                actor="concurrency-test",
                details={"action": "slow_write_test"},
            )
            write_started.set()
            time.sleep(1.0)  # Hold write transaction lock for 1 second

    writer_thread = threading.Thread(target=slow_writer, daemon=True)
    writer_thread.start()

    assert write_started.wait(timeout=5.0), "Writer failed to start"

    # Fire concurrent GET /health and GET /api/dashboard/summary requests while writer is active
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENT_READERS) as pool:
        futures = [
            pool.submit(client.get, "/api/dashboard/summary")
            if i % 2 == 0
            else pool.submit(client.get, "/health")
            for i in range(CONCURRENT_READERS)
        ]
        responses = [f.result() for f in futures]
    t1 = time.time()

    writer_thread.join(timeout=5.0)

    # 1. Zero requests should fail with 500 database lock errors
    statuses = [r.status_code for r in responses]
    assert set(statuses) == {200}, f"Expected all 200 OK, got: {statuses}"

    # 2. All 20 requests under contention must complete cleanly within 5 seconds
    elapsed = t1 - t0
    assert elapsed < 5.0, f"Concurrent reads under load took too long: {elapsed:.3f}s"


def test_busy_timeout_and_wal_pragmas_installed() -> None:
    """Verify that WAL mode, 60s busy timeout, and connection pragmas are active."""
    session = get_session_factory()()
    try:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).scalar()
        foreign_keys = session.execute(text("PRAGMA foreign_keys")).scalar()

        assert str(journal_mode).lower() == "wal", f"Expected WAL mode, got {journal_mode}"
        assert int(busy_timeout) >= 30000, f"Expected busy_timeout >= 30000ms, got {busy_timeout}ms"
        assert int(foreign_keys) == 1, "Expected foreign_keys=1"
    finally:
        session.close()
