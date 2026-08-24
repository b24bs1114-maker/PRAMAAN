"""Near-duplicate retrieval.

Two stages, deliberately separated:

1. **Retrieval** -- the flat perceptual index returns the closest pHash vectors.
   Exhaustive, so nothing is missed by an approximate quantiser.
2. **Verification** -- pHash *and* dHash Hamming distances are recomputed
   directly from the stored hashes of each candidate. Two independent hash
   families agreeing is a stronger signal than either alone.

Terminology is load-bearing: results are **near-duplicate candidates**. A small
Hamming distance means two images are perceptually similar; it does not prove
they are the same file, that one was derived from the other, or that they share a
real-world origin. Nothing in this module asserts identity.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Case, Evidence, Match
from app.services import audit
from app.services.hashing import (
    PERCEPTUAL_ALGORITHM,
    hamming_distance,
    similarity_from_distance,
)
from app.services.index import get_index
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.matching")

METHOD = "phash-flat-retrieval + exact-hamming(phash,dhash)"

INTERPRETATION = (
    "These are NEAR-DUPLICATE CANDIDATES ranked by perceptual hash distance, not "
    "a definitive identification. A low Hamming distance means two images are "
    "perceptually similar (consistent with resizing, recompression, cropping or "
    "screenshotting); it does not by itself establish that they are the same "
    "file, that one is derived from the other, or that they share a real-world "
    "origin. Candidates are limited to what exists in the local indexed corpus."
)

BAND_STRONG = "strong_candidate"
BAND_POSSIBLE = "possible_candidate"


def _band(distance: int, settings: Settings) -> str:
    return (
        BAND_STRONG
        if distance <= settings.strong_duplicate_max_distance
        else BAND_POSSIBLE
    )


def _candidate_dict(
    candidate: Evidence,
    *,
    phash_distance: int | None,
    dhash_distance: int | None,
    distance: int,
    band: str,
    rank: int,
) -> dict[str, Any]:
    return {
        "evidence_id": candidate.id,
        "distance": distance,
        "similarity": similarity_from_distance(distance),
        "phash_distance": phash_distance,
        "dhash_distance": dhash_distance,
        "source_id": candidate.source_id,
        "parent_id": candidate.parent_id,
        "generation": candidate.generation,
        "timestamp": iso(candidate.observed_at) or iso(candidate.ingested_at),
        "observed_at": iso(candidate.observed_at),
        "ingested_at": iso(candidate.ingested_at),
        "platform": candidate.platform,
        "transformation": candidate.transformation,
        "filename": candidate.filename,
        "sha256": candidate.sha256,
        "role": candidate.role,
        "is_synthetic": candidate.is_synthetic,
        "confidence_band": band,
        "rank": rank,
    }


def search_evidence(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    top_k: int | None = None,
    max_distance: int | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Find near-duplicate candidates for one evidence item."""
    top_k = top_k or settings.retrieval_top_k
    max_distance = (
        settings.near_duplicate_max_distance if max_distance is None else max_distance
    )
    index = get_index(settings)
    index_status = index.status()

    result: dict[str, Any] = {
        "evidence_id": evidence.id,
        "filename": evidence.filename,
        "media_type": evidence.media_type,
        "phash": evidence.phash,
        "dhash": evidence.dhash,
        "top_k": top_k,
        "max_distance": max_distance,
        "method": METHOD,
        "algorithm": PERCEPTUAL_ALGORITHM,
        "index_backend": index_status["backend"],
        "indexed_count": index_status["indexed_count"],
        "index_version": index_status["index_version"],
        "candidates": [],
        "notes": [],
    }

    if not evidence.phash:
        result["notes"].append(
            "No perceptual hash for this item, so no retrieval was performed. "
            "Perceptual hashing covers images; videos are not perceptually "
            "indexed in this build."
        )
        return result

    if index_status["indexed_count"] == 0:
        result["notes"].append(
            "The perceptual index is empty, so there is nothing to compare "
            "against. Absence of candidates here is not evidence of anything -- "
            "build the index with POST /api/index/rebuild or "
            "scripts/build_index.py."
        )
        return result

    # Retrieve more than needed: verification below filters by distance, and the
    # query itself is excluded.
    raw = index.query(
        evidence.phash, top_k=max(top_k * 3, top_k), exclude={evidence.id}
    )
    if not raw:
        result["notes"].append("No candidates were returned by the index.")
        return result

    rows = {
        row.id: row
        for row in session.execute(
            select(Evidence).where(Evidence.id.in_([r["evidence_id"] for r in raw]))
        ).scalars()
    }

    verified: list[tuple[int, int | None, int | None, Evidence]] = []
    for hit in raw:
        candidate = rows.get(hit["evidence_id"])
        if candidate is None:
            # Indexed id with no row: stale index. Report it, do not guess.
            result["notes"].append(
                f"Index entry {hit['evidence_id']} has no database row; the index "
                "may be stale. Rebuild with POST /api/index/rebuild."
            )
            continue
        phash_distance = hamming_distance(evidence.phash, candidate.phash)
        dhash_distance = hamming_distance(evidence.dhash, candidate.dhash)
        if phash_distance is None:
            continue
        if phash_distance > max_distance:
            continue
        verified.append((phash_distance, phash_distance, dhash_distance, candidate))

    # pHash distance decides ranking; dHash then filename break ties so ordering
    # is deterministic across runs.
    verified.sort(
        key=lambda item: (
            item[0],
            item[2] if item[2] is not None else 999,
            item[3].id,
        )
    )
    verified = verified[:top_k]

    candidates = [
        _candidate_dict(
            candidate,
            phash_distance=phash_distance,
            dhash_distance=dhash_distance,
            distance=distance,
            band=_band(distance, settings),
            rank=rank,
        )
        for rank, (distance, phash_distance, dhash_distance, candidate) in enumerate(
            verified, start=1
        )
    ]
    result["candidates"] = candidates
    result["strong_candidates"] = sum(
        1 for c in candidates if c["confidence_band"] == BAND_STRONG
    )

    if persist:
        _persist_matches(session, evidence=evidence, candidates=candidates)
    return result


def _persist_matches(
    session: Session, *, evidence: Evidence, candidates: list[dict[str, Any]]
) -> None:
    """Replace stored matches for this query with the current result set."""
    stale = session.execute(
        select(Match).where(Match.query_evidence_id == evidence.id)
    ).scalars().all()
    for row in stale:
        session.delete(row)
    if stale:
        session.flush()

    for candidate in candidates:
        session.add(
            Match(
                id=str(uuid.uuid4()),
                case_id=evidence.case_id,
                query_evidence_id=evidence.id,
                candidate_evidence_id=candidate["evidence_id"],
                phash_distance=candidate["phash_distance"],
                dhash_distance=candidate["dhash_distance"],
                distance=candidate["distance"],
                similarity=candidate["similarity"],
                confidence_band=candidate["confidence_band"],
                rank=candidate["rank"],
                method=METHOD,
            )
        )
    session.flush()


def search_case(
    session: Session,
    *,
    case: Case,
    settings: Settings,
    top_k: int | None = None,
    max_distance: int | None = None,
    actor: str = "api",
) -> dict[str, Any]:
    """Run near-duplicate retrieval for every evidence item in a case."""
    evidence_rows = list(
        session.execute(
            select(Evidence)
            .where(Evidence.case_id == case.id)
            .order_by(Evidence.ingested_at)
        ).scalars()
    )

    queries = [
        search_evidence(
            session,
            evidence=evidence,
            settings=settings,
            top_k=top_k,
            max_distance=max_distance,
        )
        for evidence in evidence_rows
    ]
    total = sum(len(q["candidates"]) for q in queries)

    audit.record(
        session,
        event=audit.EVENT_MATCH_SEARCHED,
        case_id=case.id,
        actor=actor,
        details={
            "queries": len(queries),
            "total_candidates": total,
            "method": METHOD,
            "max_distance": (
                settings.near_duplicate_max_distance
                if max_distance is None
                else max_distance
            ),
            "top_k": top_k or settings.retrieval_top_k,
            "index_version": queries[0]["index_version"] if queries else None,
        },
    )

    return {
        "case_id": case.id,
        "interpretation": INTERPRETATION,
        "queries": queries,
        "total_candidates": total,
        "thresholds": {
            "strong_candidate_max_distance": settings.strong_duplicate_max_distance,
            "near_duplicate_max_distance": (
                settings.near_duplicate_max_distance
                if max_distance is None
                else max_distance
            ),
            "hash_bits": 64,
            "basis": (
                "Prototype defaults chosen empirically on the synthetic corpus; "
                "not validated against a forensic reference dataset."
            ),
        },
    }


def stored_matches(session: Session, *, case_id: str) -> list[Match]:
    return list(
        session.execute(
            select(Match)
            .where(Match.case_id == case_id)
            .order_by(Match.query_evidence_id, Match.rank)
        ).scalars()
    )


def stored_case_matches(
    session: Session, *, case: Case, settings: Settings
) -> dict[str, Any]:
    """Rebuild the ``search_case`` envelope from stored ``Match`` rows.

    Nothing is retrieved, re-hashed or re-ranked here: the distances, similarities,
    bands and ranks are read back exactly as retrieval computed and stored them,
    so this view cannot disagree with the search that produced it. Only the
    candidate's own descriptive fields are re-read from its evidence row, because
    those live there and not on the match.

    Every evidence item in the case appears as a query, including items with no
    stored candidates -- an item missing from the list would be indistinguishable
    from an item that was searched and matched nothing.
    """
    evidence_rows = list(
        session.execute(
            select(Evidence)
            .where(Evidence.case_id == case.id)
            .order_by(Evidence.ingested_at)
        ).scalars()
    )
    rows = stored_matches(session, case_id=case.id)

    candidate_ids = {row.candidate_evidence_id for row in rows}
    candidates_by_id: dict[str, Evidence] = {}
    if candidate_ids:
        candidates_by_id = {
            item.id: item
            for item in session.execute(
                select(Evidence).where(Evidence.id.in_(candidate_ids))
            ).scalars()
        }

    by_query: dict[str, list[dict[str, Any]]] = {}
    orphaned: dict[str, list[str]] = {}
    for row in rows:
        candidate = candidates_by_id.get(row.candidate_evidence_id)
        if candidate is None:
            # The candidate row is gone but the match remains: report it rather
            # than dropping the match silently, which would understate coverage.
            orphaned.setdefault(row.query_evidence_id, []).append(
                row.candidate_evidence_id
            )
            continue
        by_query.setdefault(row.query_evidence_id, []).append(
            _candidate_dict(
                candidate,
                phash_distance=row.phash_distance,
                dhash_distance=row.dhash_distance,
                distance=row.distance,
                band=row.confidence_band,
                rank=row.rank,
            )
        )

    queries: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        candidates = by_query.get(evidence.id, [])
        notes: list[str] = []
        if not evidence.phash:
            notes.append(
                "No perceptual hash for this item, so it is outside near-duplicate "
                "retrieval. Perceptual hashing covers images in this build."
            )
        for missing in orphaned.get(evidence.id, []):
            notes.append(
                f"Stored match references candidate {missing}, which no longer has "
                "an evidence row. Re-run retrieval to refresh the stored set."
            )
        queries.append(
            {
                "evidence_id": evidence.id,
                "filename": evidence.filename,
                "media_type": evidence.media_type,
                "phash": evidence.phash,
                "dhash": evidence.dhash,
                "method": METHOD,
                "algorithm": PERCEPTUAL_ALGORITHM,
                "candidates": candidates,
                "strong_candidates": sum(
                    1 for c in candidates if c["confidence_band"] == BAND_STRONG
                ),
                "notes": notes,
            }
        )

    return {
        "case_id": case.id,
        "interpretation": INTERPRETATION,
        "queries": queries,
        "total_candidates": sum(len(q["candidates"]) for q in queries),
        "thresholds": {
            "strong_candidate_max_distance": settings.strong_duplicate_max_distance,
            "near_duplicate_max_distance": settings.near_duplicate_max_distance,
            "hash_bits": 64,
            "basis": (
                "Prototype defaults chosen empirically on the synthetic corpus; "
                "not validated against a forensic reference dataset."
            ),
            "applies_to": (
                "Current configuration. Stored matches were retained under the "
                "cut-off in force when retrieval ran."
            ),
        },
    }
