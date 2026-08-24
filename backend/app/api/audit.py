"""The audit trail across every case.

The per-case trail already exists at ``GET /api/cases/{case_id}/audit``. This
router serves the chain itself, which is global: rows from every case are
interleaved in one hash chain, and that is the only order in which the chain can
be verified. A case-scoped view is a filter over it, never a chain of its own.

Filtering happens here in the router rather than inside ``services.audit`` so the
service's tested surface -- the function the rest of the pipeline depends on to
read and verify the chain -- is left exactly as it is.

One property makes the ``since``/``until`` filters safe: ``audit_log.timestamp``
is stored as a fixed-width ISO-8601 string (``iso()`` writes
``YYYY-MM-DDTHH:MM:SSZ``), so string comparison over it is chronological
comparison. The bounds are parsed and re-serialised through the same helpers to
guarantee they are in that exact form before being compared.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import DbDep
from app.models import AuditLog
from app.schemas.api import AuditTrailGlobalResponse, AuditVerifyResponse
from app.services import audit as audit_service
from app.utils.timeutil import iso, parse_iso

logger = logging.getLogger("pramaan.api.audit")

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _bound(value: str | None, label: str) -> str | None:
    """Normalise a timestamp bound to the stored string form, or reject it.

    A bound that cannot be parsed is refused rather than ignored: silently
    dropping a filter would return rows outside the range the caller asked for
    and let them believe the range was applied.
    """
    if value is None:
        return None
    parsed = parse_iso(value)
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{label} is not a valid ISO-8601 timestamp: {value!r}. "
                "Expected e.g. 2026-01-31T09:00:00Z."
            ),
        )
    return iso(parsed)


@router.get(
    "",
    response_model=AuditTrailGlobalResponse,
    summary="Read the audit chain across every case",
)
def get_audit_trail(
    db: DbDep,
    case_id: str | None = Query(None, description="Filter to one case."),
    event: str | None = Query(None, description="Filter to one event type."),
    actor: str | None = Query(None, description="Filter to one actor."),
    since: str | None = Query(
        None, description="Only entries at or after this ISO-8601 timestamp."
    ),
    until: str | None = Query(
        None, description="Only entries at or before this ISO-8601 timestamp."
    ),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0, description="Skip N matching entries from the start."),
) -> AuditTrailGlobalResponse:
    """The chain in chain order (oldest first), filtered and paged.

    Reading the trail does not modify it. Every entry carries its
    ``previous_hash`` and ``row_hash``, so a reader can recompute the chain
    independently -- but note that verification requires the *unfiltered* chain in
    ``seq`` order, which is what ``POST /api/audit/verify`` walks. A filtered page
    is for reading, not for verifying.

    ``total_rows`` counts the entries matching the filters, so ``truncated`` means
    "more matching entries exist", not "more entries exist somewhere".
    """
    since_bound = _bound(since, "since")
    until_bound = _bound(until, "until")

    conditions: list[Any] = []
    if case_id is not None:
        conditions.append(AuditLog.case_id == case_id)
    if event is not None:
        conditions.append(AuditLog.event == event)
    if actor is not None:
        conditions.append(AuditLog.actor == actor)
    # Fixed-width ISO-8601 strings: lexicographic order is chronological order.
    if since_bound is not None:
        conditions.append(AuditLog.timestamp >= since_bound)
    if until_bound is not None:
        conditions.append(AuditLog.timestamp <= until_bound)

    total = int(
        db.execute(
            select(func.count()).select_from(AuditLog).where(*conditions)
        ).scalar_one()
    )
    rows = list(
        db.execute(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.seq.asc())
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    events = [audit_service.entry_to_dict(row) for row in rows]

    return AuditTrailGlobalResponse(
        scope="all_cases" if case_id is None else "case",
        case_id=case_id,
        count=len(events),
        total_rows=total,
        truncated=offset + len(events) < total,
        offset=offset,
        returned_from=rows[0].seq if rows else None,
        events=events,
        head_hash=audit_service.head_hash(db),
        genesis_hash=audit_service.GENESIS_HASH,
        algorithm=audit_service.ALGORITHM,
        interpretation=audit_service.INTERPRETATION,
        filters={
            "case_id": case_id,
            "event": event,
            "actor": actor,
            "since": since_bound,
            "until": until_bound,
            "limit": limit,
            "offset": offset,
        },
        known_events=sorted(audit_service.KNOWN_EVENTS),
    )


@router.post(
    "/verify",
    response_model=AuditVerifyResponse,
    summary="Verify the integrity of the whole audit chain",
)
def verify_audit_chain(
    db: DbDep,
    record: bool = Query(
        True, description="Append the verification result to the chain."
    ),
    include_events: bool = Query(
        False,
        description=(
            "Include every verified entry in the response. Off by default: the "
            "chain covers the entire deployment."
        ),
    ),
) -> AuditVerifyResponse:
    """Recompute every row hash and link, and report the first failure.

    ``valid: false`` names the failing ``seq`` and whether the row's content was
    edited or its link was broken -- the two failures are distinguished because
    they mean different things.

    The check is itself an auditable act, so by default its outcome is appended to
    the chain as ``AUDIT_CHAIN_VERIFIED`` *after* the recomputation, which is what
    lets a later reader see when integrity was last confirmed without re-walking
    the chain.

    ``issues`` is always complete; ``events`` is omitted unless asked for, because
    returning the full history of the deployment on every integrity check would
    make the answer unreadable rather than more trustworthy.
    """
    result = audit_service.verify_chain(db, None)
    result["interpretation"] = audit_service.INTERPRETATION

    if record:
        audit_service.record(
            db,
            event=audit_service.EVENT_AUDIT_VERIFIED,
            case_id=None,
            actor="api",
            details={
                "valid": result["valid"],
                "total_rows": result["total_rows"],
                "case_rows": result["case_rows"],
                "first_invalid_seq": result["first_invalid_seq"],
                "verified_head_hash": result["head_hash"],
                "issue_count": len(result["issues"]),
                "algorithm": result["algorithm"],
                "scope": "all_cases",
            },
        )

    verified_count = len(result["events"])
    if not include_events:
        result["events"] = []
    result["events_included"] = include_events
    result["verified_row_count"] = verified_count

    return AuditVerifyResponse(**result)
