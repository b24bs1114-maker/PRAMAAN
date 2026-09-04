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
from app.services.dinov2_index import get_dinov2_index
from app.services.dinov2_service import extract_embedding
from app.services.index import get_index
from app.services.storage import StorageError, absolute_path

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

    # Rebuild or synchronize DINOv2 visual embedding index
    dinov2_added = 0
    if getattr(settings, "enable_dinov2_retrieval", True):
        try:
            dinov2_idx = get_dinov2_index(settings)
            dinov2_entries = []
            for row in rows:
                emb = dinov2_idx.get_embedding(row.id)
                if emb is None:
                    try:
                        img_path = absolute_path(row.stored_path, settings)
                        if img_path.is_file():
                            emb = extract_embedding(
                                img_path,
                                model_name=settings.dinov2_model_name,
                                device_pref=settings.dinov2_device,
                                cache_key=row.sha256,
                            )
                    except (StorageError, OSError, Exception) as exc:
                        logger.debug("Failed extracting DINOv2 embedding for %s: %s", row.id, exc)
                if emb is not None:
                    dinov2_entries.append((row.id, emb))
            dinov2_added = dinov2_idx.replace_all(dinov2_entries)
        except Exception as exc:
            logger.warning("DINOv2 index rebuild encountered error: %s", exc)

    indexed_ids = {row.id for row in rows}
    for row in session.execute(select(Evidence)).scalars():
        row.indexed = row.id in indexed_ids and index.contains(row.id)
    session.flush()

    status_dict = index.status()
    if getattr(settings, "enable_dinov2_retrieval", True):
        status_dict["dinov2_index"] = get_dinov2_index(settings).status()
        status_dict["dinov2_indexed_count"] = dinov2_added

    audit.record(
        session,
        event=audit.EVENT_INDEX_UPDATED,
        case_id=None,
        actor=actor,
        details={
            "operation": "rebuild",
            "indexed_count": status_dict["indexed_count"],
            "dinov2_indexed_count": dinov2_added,
            "index_version": status_dict["index_version"],
            "backend": status_dict["backend"],
            "candidates": len(rows),
        },
    )
    logger.info("Index rebuilt: %d pHash vectors, %d DINOv2 vectors", added, dinov2_added)
    return {
        "status": "rebuilt",
        "added": added,
        "dinov2_added": dinov2_added,
        "skipped": len(rows) - added,
        **status_dict,
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

    # Index DINOv2 embedding idempotently
    if getattr(settings, "enable_dinov2_retrieval", True):
        try:
            dinov2_idx = get_dinov2_index(settings)
            if not dinov2_idx.contains(evidence.id):
                img_path = absolute_path(evidence.stored_path, settings)
                if img_path.is_file():
                    emb = extract_embedding(
                        img_path,
                        model_name=settings.dinov2_model_name,
                        device_pref=settings.dinov2_device,
                        cache_key=evidence.sha256,
                    )
                    if emb is not None:
                        dinov2_idx.add(evidence.id, emb)
        except Exception as exc:
            logger.debug("DINOv2 add_evidence skipped: %s", exc)

    evidence.indexed = True
    session.flush()

    status_dict = index.status()
    audit.record(
        session,
        event=audit.EVENT_INDEX_UPDATED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "operation": "add",
            "evidence_id": evidence.id,
            "already_present": not added,
            "indexed_count": status_dict["indexed_count"],
            "index_version": status_dict["index_version"],
            "backend": status_dict["backend"],
        },
    )
    return {
        "status": "added" if added else "already_indexed",
        "added": 1 if added else 0,
        "skipped": 0 if added else 1,
        "detail": None if added else "This evidence id was already in the index.",
        **status_dict,
    }


def status(settings: Settings) -> dict[str, Any]:
    stat = get_index(settings).status()
    if getattr(settings, "enable_dinov2_retrieval", True):
        stat["dinov2"] = get_dinov2_index(settings).status()
    return stat
