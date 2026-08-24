"""Index maintenance: keep the perceptual index in step with the database.

The database is authoritative. The index is a derived artefact that can always
be rebuilt from ``evidence.phash``, which is why ``rebuild`` is a supported
operation rather than a recovery hack.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Evidence
from app.services import audit
from app.services.index import get_index

logger = logging.getLogger("pramaan.indexing")


def indexable_evidence(session: Session) -> list[Evidence]:
    """Evidence that can be indexed: anything with a perceptual hash.

    Videos have no perceptual hash in this build and are skipped rather than
    silently represented by a placeholder vector.
    """
    return list(
        session.execute(
            select(Evidence)
            .where(Evidence.phash.is_not(None))
            .order_by(Evidence.ingested_at)
        ).scalars()
    )


def rebuild(
    session: Session, *, settings: Settings, actor: str = "system"
) -> dict[str, Any]:
    """Rebuild the whole index from the database and re-sync ``indexed`` flags."""
    index = get_index(settings)
    rows = indexable_evidence(session)
    added = index.replace_all([(row.id, row.phash) for row in rows])

    indexed_ids = {row.id for row in rows}
    for row in session.execute(select(Evidence)).scalars():
        row.indexed = row.id in indexed_ids and index.contains(row.id)
    session.flush()

    status = index.status()
    audit.record(
        session,
        event=audit.EVENT_INDEX_UPDATED,
        case_id=None,
        actor=actor,
        details={
            "operation": "rebuild",
            "indexed_count": status["indexed_count"],
            "index_version": status["index_version"],
            "backend": status["backend"],
            "candidates": len(rows),
        },
    )
    logger.info("Index rebuilt: %d vectors (backend %s)", added, status["backend"])
    return {
        "status": "rebuilt",
        "added": added,
        "skipped": len(rows) - added,
        **status,
    }


def add_evidence(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
) -> dict[str, Any]:
    """Add one evidence item to the index, idempotently."""
    index = get_index(settings)
    if not evidence.phash:
        return {
            "status": "skipped",
            "added": 0,
            "skipped": 1,
            "detail": (
                "Evidence has no perceptual hash (videos are not perceptually "
                "indexed in this build); nothing was added."
            ),
            **index.status(),
        }

    added = index.add(evidence.id, evidence.phash)
    evidence.indexed = True
    session.flush()

    status = index.status()
    audit.record(
        session,
        event=audit.EVENT_INDEX_UPDATED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "operation": "add",
            "evidence_id": evidence.id,
            "already_present": not added,
            "indexed_count": status["indexed_count"],
            "index_version": status["index_version"],
            "backend": status["backend"],
        },
    )
    return {
        "status": "added" if added else "already_indexed",
        "added": 1 if added else 0,
        "skipped": 0 if added else 1,
        "detail": None if added else "This evidence id was already in the index.",
        **status,
    }


def status(settings: Settings) -> dict[str, Any]:
    return get_index(settings).status()
