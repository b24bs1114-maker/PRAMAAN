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
from app.services.dinov2_index import get_dinov2_index
from app.services.dinov2_service import extract_embedding
from app.services.hashing import (
    PERCEPTUAL_ALGORITHM,
    hamming_distance,
    similarity_from_distance,
)
from app.services.index import get_index
from app.services.storage import StorageError, absolute_path
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.matching")

METHOD = "dinov2-retrieval + exact-hamming(phash,dhash,ahash)"
METHOD_PHASH_ONLY = "phash-flat-retrieval + exact-hamming(phash,dhash)"

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
    query_evidence: Evidence | None = None,
    phash_distance: int | None,
    dhash_distance: int | None,
    ahash_distance: int | None = None,
    dinov2_similarity: float | None = None,
    distance: int,
    similarity: float | None = None,
    band: str,
    rank: int,
) -> dict[str, Any]:
    if similarity is None:
        similarity = similarity_from_distance(distance)
        if dinov2_similarity is not None and (similarity is None or dinov2_similarity > similarity):
            similarity = round(dinov2_similarity, 4)

    cand_dict: dict[str, Any] = {
        "evidence_id": candidate.id,
        "distance": distance,
        "similarity": similarity,
        "phash_distance": phash_distance,
        "dhash_distance": dhash_distance,
        "ahash_distance": ahash_distance,
        "dinov2_similarity": dinov2_similarity,
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

    if query_evidence is not None:
        try:
            from app.services.provenance_intelligence import detect_transformation

            trans_info = detect_transformation(
                query_evidence,
                candidate,
                phash_distance,
                dhash_distance,
                ahash_dist=ahash_distance,
                dinov2_similarity=dinov2_similarity,
            )
            cand_dict["transformation_analysis"] = trans_info
            cand_dict["relationship"] = trans_info.get("relationship")
            cand_dict["transformations_detected"] = trans_info.get("transformations_detected", [])
            if not candidate.transformation and trans_info.get("type"):
                cand_dict["transformation"] = trans_info.get("type")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Transformation detection failed: %s", exc)

    basis_parts = []
    if phash_distance is not None:
        basis_parts.append(f"pHash dist {phash_distance}")
    if dhash_distance is not None:
        basis_parts.append(f"dHash dist {dhash_distance}")
    if ahash_distance is not None:
        basis_parts.append(f"aHash dist {ahash_distance}")
    if dinov2_similarity is not None:
        basis_parts.append(f"DINOv2 sim {dinov2_similarity:.2f}")

    is_exact_match = bool(
        distance == 0
        and candidate.sha256
        and query_evidence
        and query_evidence.sha256 == candidate.sha256
    )
    if is_exact_match:
        match_basis = "Exact SHA-256 byte match"
    elif basis_parts:
        match_basis = f"Multi-hash verified ({', '.join(basis_parts)})"
    else:
        match_basis = "Perceptual match"

    cand_dict["match_basis"] = match_basis
    return cand_dict


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
    enable_dinov2 = getattr(settings, "enable_dinov2_retrieval", True)

    result: dict[str, Any] = {
        "evidence_id": evidence.id,
        "filename": evidence.filename,
        "media_type": evidence.media_type,
        "phash": evidence.phash,
        "dhash": evidence.dhash,
        "ahash": evidence.ahash,
        "top_k": top_k,
        "max_distance": max_distance,
        "method": METHOD if enable_dinov2 else METHOD_PHASH_ONLY,
        "algorithm": PERCEPTUAL_ALGORITHM,
        "index_backend": index_status["backend"],
        "indexed_count": index_status["indexed_count"],
        "index_version": index_status["index_version"],
        "candidates": [],
        "notes": [],
    }

    diagnostics: dict[str, Any] = {
        "evidence_id": evidence.id,
        "indexed_count": index_status["indexed_count"],
        "candidate_count": 0,
        "hashes_available": {
            "phash": bool(evidence.phash),
            "dhash": bool(evidence.dhash),
            "ahash": bool(evidence.ahash),
        },
        "comparison_attempts": 0,
        "successful_comparisons": 0,
        "rejected_candidates": 0,
        "rejection_reasons": [],
        "best_similarity": None,
        "best_candidate_id": None,
    }

    if not evidence.phash:
        result["notes"].append(
            "No perceptual hash for this item, so no retrieval was performed. "
            "Perceptual hashing covers images; videos are not perceptually "
            "indexed in this build."
        )
        diagnostics["rejection_reasons"].append("missing_phash")
        result["diagnostics"] = diagnostics
        logger.info("PROVENANCE_DIAGNOSTICS: %s", diagnostics)
        return result

    if index_status["indexed_count"] == 0:
        result["notes"].append(
            "The perceptual index is empty, so there is nothing to compare "
            "against. Absence of candidates here is not evidence of anything -- "
            "build the index with POST /api/index/rebuild or "
            "scripts/build_index.py."
        )
        diagnostics["rejection_reasons"].append("empty_index")
        result["diagnostics"] = diagnostics
        logger.info("PROVENANCE_DIAGNOSTICS: %s", diagnostics)
        return result

    # 1. DINOv2 Visual Retrieval (when enabled)
    dinov2_hits: dict[str, float] = {}
    query_dinov2_emb = None
    if enable_dinov2:
        try:
            img_path = absolute_path(evidence.stored_path, settings)
            if img_path.is_file():
                query_dinov2_emb = extract_embedding(
                    img_path,
                    model_name=settings.dinov2_model_name,
                    device_pref=settings.dinov2_device,
                    cache_key=evidence.sha256,
                )
                if query_dinov2_emb is not None:
                    dinov2_idx = get_dinov2_index(settings)
                    dinov2_results = dinov2_idx.query(
                        query_dinov2_emb,
                        top_k=max(top_k * 3, top_k),
                        min_similarity=0.40,
                        exclude={evidence.id},
                    )
                    for hit in dinov2_results:
                        dinov2_hits[hit["evidence_id"]] = hit["similarity"]
        except Exception as exc:
            logger.debug("DINOv2 candidate retrieval skipped: %s", exc)

    # 2. Perceptual index retrieval (exhaustive Hamming pHash)
    raw = index.query(
        evidence.phash, top_k=max(top_k * 3, top_k), exclude={evidence.id}
    )

    # Union candidate pool
    all_candidate_ids = set(dinov2_hits.keys())
    for r in raw:
        all_candidate_ids.add(r["evidence_id"])

    diagnostics["comparison_attempts"] = len(all_candidate_ids)
    if not all_candidate_ids:
        result["notes"].append("No candidates were returned by the index.")
        diagnostics["rejection_reasons"].append("no_index_hits")
        result["diagnostics"] = diagnostics
        logger.info("PROVENANCE_DIAGNOSTICS: %s", diagnostics)
        return result

    rows = {
        row.id: row
        for row in session.execute(
            select(Evidence).where(Evidence.id.in_(list(all_candidate_ids)))
        ).scalars()
    }

    # 3. Verification & Multi-Signal Agreement (pHash, dHash, aHash, DINOv2, SHA-256)
    verified: list[tuple[int, int | None, int | None, int | None, float | None, Evidence]] = []
    for cand_id in all_candidate_ids:
        candidate = rows.get(cand_id)
        if candidate is None:
            # Indexed id with no row: stale index
            result["notes"].append(
                f"Index entry {cand_id} has no database row; the index "
                "may be stale. Rebuild with POST /api/index/rebuild."
            )
            diagnostics["rejected_candidates"] += 1
            diagnostics["rejection_reasons"].append(f"stale_index_no_db_row:{cand_id}")
            continue

        phash_distance = hamming_distance(evidence.phash, candidate.phash)
        dhash_distance = hamming_distance(evidence.dhash, candidate.dhash)
        ahash_distance = hamming_distance(evidence.ahash, candidate.ahash)
        d_sim = dinov2_hits.get(candidate.id)
        is_exact = bool(evidence.sha256 and candidate.sha256 and evidence.sha256 == candidate.sha256)

        # Multi-hash verification:
        has_phash_agreement = phash_distance is not None and phash_distance <= max_distance
        has_dhash_agreement = (
            dhash_distance is not None
            and dhash_distance <= max_distance
            and d_sim is not None
            and d_sim >= settings.dinov2_similarity_threshold
        )
        has_ahash_agreement = (
            ahash_distance is not None
            and ahash_distance <= max_distance
            and d_sim is not None
            and d_sim >= settings.dinov2_similarity_threshold
        )

        is_verified = is_exact or has_phash_agreement or has_dhash_agreement or has_ahash_agreement

        if not is_verified:
            diagnostics["rejected_candidates"] += 1
            diagnostics["rejection_reasons"].append(
                f"verification_failed:{candidate.id}(phash={phash_distance},dhash={dhash_distance},ahash={ahash_distance},d_sim={d_sim})"
            )
            continue

        # Effective distance calculation (preserving Hamming distance semantics)
        hash_distances = [d for d in (phash_distance, dhash_distance, ahash_distance) if d is not None]
        min_hash_dist = min(hash_distances) if hash_distances else None

        if is_exact:
            effective_dist = 0
        elif phash_distance is not None and phash_distance <= max_distance:
            effective_dist = phash_distance
        elif min_hash_dist is not None and min_hash_dist <= max_distance:
            effective_dist = min_hash_dist
        else:
            diagnostics["rejected_candidates"] += 1
            diagnostics["rejection_reasons"].append(
                f"distance_exceeds_threshold:{candidate.id}(min_hash={min_hash_dist}>max={max_distance})"
            )
            continue

        verified.append((effective_dist, phash_distance, dhash_distance, ahash_distance, d_sim, candidate))

    diagnostics["successful_comparisons"] = len(verified)

    # 4. Deterministic Multi-Signal Ranking
    def _sort_key(item: tuple[int, int | None, int | None, int | None, float | None, Evidence]) -> tuple:
        cand = item[5]
        exact_rank = 0 if (evidence.sha256 and cand.sha256 and evidence.sha256 == cand.sha256) else 1
        dist_val = item[0]
        # Secondary sort by DINOv2 visual similarity descending
        d_sim_val = -(item[4] if item[4] is not None else similarity_from_distance(dist_val) or 0.0)
        dhash_val = item[2] if item[2] is not None else 999
        return (exact_rank, dist_val, d_sim_val, dhash_val, cand.id)

    verified.sort(key=_sort_key)
    verified = verified[:top_k]

    candidates = [
        _candidate_dict(
            candidate,
            query_evidence=evidence,
            phash_distance=phash_distance,
            dhash_distance=dhash_distance,
            ahash_distance=ahash_distance,
            dinov2_similarity=d_sim,
            distance=distance,
            band=_band(distance, settings) if not (d_sim is not None and d_sim >= 0.85) else BAND_STRONG,
            rank=rank,
        )
        for rank, (distance, phash_distance, dhash_distance, ahash_distance, d_sim, candidate) in enumerate(
            verified, start=1
        )
    ]
    result["candidates"] = candidates
    result["strong_candidates"] = sum(
        1 for c in candidates if c["confidence_band"] == BAND_STRONG
    )

    diagnostics["candidate_count"] = len(candidates)
    if candidates:
        diagnostics["best_similarity"] = candidates[0]["similarity"]
        diagnostics["best_candidate_id"] = candidates[0]["evidence_id"]
    result["diagnostics"] = diagnostics
    logger.info("PROVENANCE_DIAGNOSTICS: %s", diagnostics)

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
                similarity=row.similarity,
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
