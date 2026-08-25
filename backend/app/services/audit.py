"""Hash-chained audit log.

Each row commits to the row before it::

    row_hash = SHA-256( previous_hash || canonical_json(payload) )

``payload`` covers every field a reader can see (audit_id, case_id, event,
timestamp, actor, details), so editing any of them -- or deleting, reordering or
inserting a row -- breaks the chain at that point and every row after it.

The chain is **global**, not per case: a single sequence across the whole
database means rows cannot be moved between cases undetected. Case-scoped
verification therefore validates the whole chain and additionally reports what
belongs to the case in question.

This is tamper *evidence*, not tamper *proof*: an attacker with write access to
the database can recompute the entire chain. Anchoring the head hash externally
(printed in the report, or countersigned) is what makes it meaningful -- the
generated PDF records the head hash for that reason.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog
from app.utils.canonical import canonical_bytes
from app.utils.timeutil import iso, utcnow
import hashlib

logger = logging.getLogger("pramaan.audit")

GENESIS_HASH = "0" * 64
ALGORITHM = "SHA-256(previous_hash || canonical_json(payload))"

INTERPRETATION = (
    "Each audit row commits to the row before it, so any edit, deletion, "
    "reordering or insertion breaks the chain at that row and at every row after "
    "it. This is tamper EVIDENCE, not tamper PROOF: anyone with write access to "
    "the database can recompute the whole chain. The chain only becomes "
    "meaningful once its head hash is anchored outside the database -- the "
    "generated report prints the head hash for that purpose."
)

# Canonical event vocabulary.
EVENT_CASE_CREATED = "CASE_CREATED"
EVENT_CASE_UPDATED = "CASE_UPDATED"
EVENT_CASE_DELETED = "CASE_DELETED"
EVENT_EVIDENCE_INGESTED = "EVIDENCE_INGESTED"
EVENT_EVIDENCE_DUPLICATE = "EVIDENCE_DUPLICATE_DETECTED"
EVENT_EVIDENCE_REJECTED = "EVIDENCE_REJECTED"
EVENT_HASH_CALCULATED = "HASH_CALCULATED"
EVENT_METADATA_EXTRACTED = "METADATA_EXTRACTED"
EVENT_PERCEPTUAL_HASHED = "PERCEPTUAL_HASH_CALCULATED"
EVENT_INDEX_UPDATED = "INDEX_UPDATED"
EVENT_MATCH_SEARCHED = "MATCH_SEARCHED"
EVENT_PROPAGATION_RECONSTRUCTED = "PROPAGATION_RECONSTRUCTED"
EVENT_DETECTOR_RUN = "DETECTOR_RUN"
EVENT_PROVENANCE_INSPECTED = "PROVENANCE_INSPECTED"
EVENT_FORENSICS_ANALYSED = "FORENSICS_ANALYSED"
EVENT_VERDICT_GENERATED = "VERDICT_GENERATED"
EVENT_ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
EVENT_REPORT_GENERATED = "REPORT_GENERATED"
EVENT_AUDIT_VERIFIED = "AUDIT_CHAIN_VERIFIED"
EVENT_AUDIT_REPAIRED = "AUDIT_CHAIN_REPAIRED"

KNOWN_EVENTS = frozenset(
    {
        EVENT_CASE_CREATED,
        EVENT_CASE_UPDATED,
        EVENT_CASE_DELETED,
        EVENT_EVIDENCE_INGESTED,
        EVENT_EVIDENCE_DUPLICATE,
        EVENT_EVIDENCE_REJECTED,
        EVENT_HASH_CALCULATED,
        EVENT_METADATA_EXTRACTED,
        EVENT_PERCEPTUAL_HASHED,
        EVENT_INDEX_UPDATED,
        EVENT_MATCH_SEARCHED,
        EVENT_PROPAGATION_RECONSTRUCTED,
        EVENT_DETECTOR_RUN,
        EVENT_PROVENANCE_INSPECTED,
        EVENT_FORENSICS_ANALYSED,
        EVENT_VERDICT_GENERATED,
        EVENT_ANALYSIS_COMPLETED,
        EVENT_REPORT_GENERATED,
        EVENT_AUDIT_VERIFIED,
        EVENT_AUDIT_REPAIRED,
    }
)

# Appends must serialise: two writers reading the same head would fork the chain.
#
# This lock is a same-process fast path, not the guarantee. It cannot be the
# guarantee, for two reasons: it does not span processes (two uvicorn workers
# hold two different locks), and it is released at ``flush()`` -- before the
# caller's transaction commits -- so a second thread reading the head on its own
# connection would not see the flushed row anyway. The actual serialisation comes
# from ``BEGIN IMMEDIATE`` on every transaction (see ``app.models.base``), which
# makes the head read and the dependent append atomic against other writers.
_append_lock = threading.Lock()


def row_payload(
    *,
    audit_id: str,
    case_id: str | None,
    event: str,
    timestamp: str,
    actor: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    """The exact structure that gets hashed. Order-independent by construction."""
    return {
        "audit_id": audit_id,
        "case_id": case_id,
        "event": event,
        "timestamp": timestamp,
        "actor": actor,
        "details": details,
    }


def compute_row_hash(previous_hash: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(previous_hash.encode("ascii"))
    digest.update(canonical_bytes(payload))
    return digest.hexdigest()


def head_hash(session: Session) -> str:
    """Row hash of the most recent entry, or the genesis constant."""
    last = session.execute(
        select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)
    ).scalar_one_or_none()
    return last.row_hash if last else GENESIS_HASH


def record(
    session: Session,
    *,
    event: str,
    case_id: str | None = None,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    flush: bool = True,
) -> AuditLog:
    """Append one event to the chain.

    Unknown event names are recorded (never silently dropped -- losing an audit
    event is worse than logging an unexpected one) but logged as a warning.
    """
    if event not in KNOWN_EVENTS:
        logger.warning("Recording unrecognised audit event %r", event)

    details = details or {}
    audit_id = str(uuid.uuid4())
    timestamp = iso(utcnow()) or ""

    with _append_lock:
        if not session.in_transaction():
            try:
                session.exec_driver_sql("BEGIN IMMEDIATE")
            except Exception:
                pass
        previous = head_hash(session)
        payload = row_payload(
            audit_id=audit_id,
            case_id=case_id,
            event=event,
            timestamp=timestamp,
            actor=actor,
            details=details,
        )
        entry = AuditLog(
            audit_id=audit_id,
            case_id=case_id,
            event=event,
            timestamp=timestamp,
            actor=actor,
            details=details,
            previous_hash=previous,
            row_hash=compute_row_hash(previous, payload),
        )
        session.add(entry)
        if flush:
            session.flush()
    return entry


def entry_to_dict(entry: AuditLog) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "audit_id": entry.audit_id,
        "case_id": entry.case_id,
        "event": entry.event,
        "timestamp": entry.timestamp,
        "actor": entry.actor,
        "details": entry.details,
        "previous_hash": entry.previous_hash,
        "row_hash": entry.row_hash,
    }


def verify_chain(session: Session, case_id: str | None = None) -> dict[str, Any]:
    """Recompute the whole chain and report the first point of failure.

    Two independent checks per row: the stored ``previous_hash`` must equal the
    preceding row's ``row_hash`` (ordering/deletion), and the recomputed
    ``row_hash`` must equal the stored one (content edits).
    """
    rows = list(session.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars())

    issues: list[dict[str, Any]] = []
    expected_previous = GENESIS_HASH
    first_invalid: int | None = None

    for row in rows:
        payload = row_payload(
            audit_id=row.audit_id,
            case_id=row.case_id,
            event=row.event,
            timestamp=row.timestamp,
            actor=row.actor,
            details=row.details or {},
        )
        recomputed = compute_row_hash(row.previous_hash, payload)

        if row.previous_hash != expected_previous:
            issues.append(
                {
                    "seq": row.seq,
                    "audit_id": row.audit_id,
                    "case_id": row.case_id,
                    "problem": "broken_link",
                    "detail": (
                        "previous_hash does not match the preceding row's row_hash "
                        "(row deleted, reordered or inserted)"
                    ),
                    "expected": expected_previous,
                    "found": row.previous_hash,
                }
            )
            first_invalid = first_invalid or row.seq

        if recomputed != row.row_hash:
            issues.append(
                {
                    "seq": row.seq,
                    "audit_id": row.audit_id,
                    "case_id": row.case_id,
                    "problem": "content_modified",
                    "detail": "row_hash does not match a recomputation of the row",
                    "expected": recomputed,
                    "found": row.row_hash,
                }
            )
            first_invalid = first_invalid or row.seq

        expected_previous = row.row_hash

    case_rows = [r for r in rows if case_id is None or r.case_id == case_id]
    valid = not issues

    return {
        "valid": valid,
        "scope": "global_chain",
        "case_id": case_id,
        "total_rows": len(rows),
        "case_rows": len(case_rows),
        "first_invalid_seq": first_invalid,
        "head_hash": rows[-1].row_hash if rows else GENESIS_HASH,
        "genesis_hash": GENESIS_HASH,
        "algorithm": ALGORITHM,
        "issues": issues,
        "events": [entry_to_dict(r) for r in case_rows],
    }


def trail(
    session: Session, case_id: str | None = None, *, limit: int | None = None
) -> dict[str, Any]:
    """Read the audit trail in chain order, newest last.

    ``limit`` keeps the most recent N rows. Truncation is reported so a caller
    never mistakes a partial view for the whole history.
    """
    statement = select(AuditLog).order_by(AuditLog.seq.asc())
    if case_id is not None:
        statement = statement.where(AuditLog.case_id == case_id)
    rows = list(session.execute(statement).scalars())

    truncated = limit is not None and len(rows) > limit
    visible = rows[-limit:] if truncated else rows

    return {
        "case_id": case_id,
        "count": len(visible),
        "total_rows": len(rows),
        "truncated": truncated,
        "events": [entry_to_dict(r) for r in visible],
        "head_hash": head_hash(session),
        "genesis_hash": GENESIS_HASH,
        "algorithm": ALGORITHM,
        "interpretation": INTERPRETATION,
    }

