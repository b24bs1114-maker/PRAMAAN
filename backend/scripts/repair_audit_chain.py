"""Repair a hash-chained audit log that was forked by a concurrent append.

WHAT THIS FIXES, AND WHAT IT REFUSES TO FIX
-------------------------------------------
``verify_chain`` reports two independent kinds of failure:

* ``content_modified`` -- a row's ``row_hash`` does not match a recomputation of
  its own payload. Somebody edited the row. This script **refuses to run** when
  it sees one, because relinking would recompute the hash over the edited
  payload and destroy the only evidence that the edit happened.
* ``broken_link`` -- a row's ``previous_hash`` does not match the preceding row's
  ``row_hash``, while every payload still hashes to its own stored ``row_hash``.
  Nothing was edited; the *ordering commitment* is wrong. That is what a
  concurrent read-then-append produces: two rows read the same head, so both
  claim the same parent and one of them is off-chain.

Only the second case is repairable, and this script repairs it the only honest
way: it rewrites ``previous_hash`` and ``row_hash`` from the first broken link
forward, deriving each from the row's **existing, unmodified** payload. No
audit_id, case_id, event, timestamp, actor or details field is touched, no row is
deleted, reordered or inserted, and no row is invented. The recorded history is
preserved exactly; only the linkage that the race corrupted is rebuilt.

The repair is itself appended to the chain as an ``AUDIT_CHAIN_REPAIRED`` event
recording the affected range and the old and new head hashes. Silently rewriting
an audit log is precisely the act this log exists to detect, so the rewrite is
disclosed inside the log it rewrote.

Usage (dry run by default -- nothing is written without ``--apply``)::

    python -m scripts.repair_audit_chain
    python -m scripts.repair_audit_chain --apply

Run from the ``backend`` directory.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import AuditLog, init_db, session_scope  # noqa: E402
from app.services import audit as audit_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("repair")


def _payload_of(row: AuditLog) -> dict:
    """The exact structure the chain hashes, taken verbatim from the stored row."""
    return audit_service.row_payload(
        audit_id=row.audit_id,
        case_id=row.case_id,
        event=row.event,
        timestamp=row.timestamp,
        actor=row.actor,
        details=row.details or {},
    )


def diagnose(rows: list[AuditLog]) -> dict:
    """Classify every link and content failure without changing anything."""
    broken_links: list[dict] = []
    content_modified: list[dict] = []
    expected_previous = audit_service.GENESIS_HASH

    for row in rows:
        if audit_service.compute_row_hash(row.previous_hash, _payload_of(row)) != row.row_hash:
            content_modified.append({"seq": row.seq, "audit_id": row.audit_id})
        if row.previous_hash != expected_previous:
            broken_links.append(
                {
                    "seq": row.seq,
                    "audit_id": row.audit_id,
                    "event": row.event,
                    "case_id": row.case_id,
                    "timestamp": row.timestamp,
                    "expected_previous": expected_previous,
                    "found_previous": row.previous_hash,
                }
            )
        expected_previous = row.row_hash

    return {
        "total_rows": len(rows),
        "broken_links": broken_links,
        "content_modified": content_modified,
        "head_hash": rows[-1].row_hash if rows else audit_service.GENESIS_HASH,
    }


def describe_fork(rows: list[AuditLog], broken_seq: int) -> list[dict]:
    """The two rows that claimed the same parent, so the cause is on the record."""
    by_seq = {row.seq: row for row in rows}
    broken = by_seq.get(broken_seq)
    if broken is None:
        return []
    siblings = [r for r in rows if r.previous_hash == broken.previous_hash]
    return [
        {
            "seq": r.seq,
            "audit_id": r.audit_id,
            "event": r.event,
            "case_id": r.case_id,
            "timestamp": r.timestamp,
            "claimed_parent": r.previous_hash[:12],
            "row_hash": r.row_hash[:12],
        }
        for r in siblings
    ]


def backup_database(db_path: Path) -> Path:
    """Copy the case file (and its WAL/SHM sidecars) before touching it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.stem}.pre-chain-repair-{stamp}{db_path.suffix}")
    shutil.copy2(db_path, target)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(str(target) + suffix))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the repair. Without this flag the script only reports.",
    )
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings)
    db_path = Path(settings.db_path)
    logger.info("Case file: %s", db_path)

    with session_scope(settings) as session:
        rows = list(session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars())
        report = diagnose(rows)

    logger.info("Rows: %d", report["total_rows"])
    logger.info("Head hash (before): %s", report["head_hash"])
    logger.info("content_modified issues: %d", len(report["content_modified"]))
    logger.info("broken_link issues: %d", len(report["broken_links"]))

    if report["content_modified"]:
        logger.error("")
        logger.error("REFUSING TO REPAIR: %d row(s) fail their own hash.", len(report["content_modified"]))
        logger.error("Rows: %s", [i["seq"] for i in report["content_modified"]])
        logger.error(
            "That is edited content, not a fork. Relinking would recompute the "
            "hash over the edited payload and erase the evidence of the edit. "
            "Investigate these rows before running any repair."
        )
        return 2

    if not report["broken_links"]:
        logger.info("")
        logger.info("Chain is already valid. Nothing to repair.")
        return 0

    first_broken = report["broken_links"][0]["seq"]
    logger.info("")
    logger.info("First broken link at seq %d.", first_broken)
    logger.info("Concurrently-created rows claiming the same parent:")
    with session_scope(settings) as session:
        rows = list(session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars())
        for entry in describe_fork(rows, first_broken):
            logger.info(
                "  seq %-5d %-28s case=%s ts=%s parent=%s row=%s",
                entry["seq"],
                entry["event"],
                (entry["case_id"] or "-")[:8],
                entry["timestamp"],
                entry["claimed_parent"],
                entry["row_hash"],
            )

    affected = [r.seq for r in rows if r.seq >= first_broken]
    logger.info("")
    logger.info("Would relink %d row(s): seq %d..%d", len(affected), affected[0], affected[-1])
    logger.info("Payload fields are preserved verbatim; only previous_hash/row_hash are rebuilt.")

    if not args.apply:
        logger.info("")
        logger.info("Dry run. Re-run with --apply to write the repair.")
        return 0

    backup = backup_database(db_path)
    logger.info("")
    logger.info("Backup written: %s", backup)

    old_head = report["head_hash"]
    with session_scope(settings) as session:
        rows = list(session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars())
        expected_previous = audit_service.GENESIS_HASH
        relinked = 0
        for row in rows:
            if row.seq >= first_broken:
                row.previous_hash = expected_previous
                row.row_hash = audit_service.compute_row_hash(expected_previous, _payload_of(row))
                relinked += 1
            expected_previous = row.row_hash
        session.flush()

        # Disclose the rewrite inside the log that was rewritten.
        audit_service.record(
            session,
            event=audit_service.EVENT_AUDIT_REPAIRED,
            actor="scripts.repair_audit_chain",
            details={
                "reason": (
                    "Chain forked by a concurrent read-then-append: two rows read "
                    "the same head and both committed to it."
                ),
                "first_broken_seq": first_broken,
                "relinked_seq_range": [affected[0], affected[-1]],
                "relinked_row_count": relinked,
                "head_hash_before_repair": old_head,
                "payloads_modified": 0,
                "rows_deleted": 0,
                "rows_inserted": 0,
                "scope": (
                    "previous_hash and row_hash recomputed from each row's "
                    "existing payload; no audit_id, case_id, event, timestamp, "
                    "actor or details field was changed"
                ),
                "race_fix": (
                    "BEGIN IMMEDIATE on every transaction (app.models.base) now "
                    "serialises the head read with the dependent append"
                ),
            },
        )

    with session_scope(settings) as session:
        result = audit_service.verify_chain(session)

    logger.info("Relinked %d row(s) and appended the repair record.", relinked)
    logger.info("")
    logger.info("verify_chain after repair: valid=%s issues=%d", result["valid"], len(result["issues"]))
    logger.info("Head hash (after): %s", result["head_hash"])
    logger.info("Rows: %d", result["total_rows"])

    if not result["valid"]:
        logger.error("Chain is still invalid: %s", result["issues"][:3])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
