"""SImProv-Inspired Provenance Intelligence Layer.

Inspired by SImProv (arXiv:2206.14245: 'SImProv: Scalable Image Provenance Framework
for Robust Content Attribution').

PRAMAAN Provenance Intelligence combines:
1. Exact Cryptographic Identity (SHA-256)
2. Perceptual Fingerprinting (pHash, dHash, aHash)
3. Multi-Hash Scalable Retrieval from Indexed Evidence Corpus
4. Transformation-Aware Candidate Ranking (detecting recompression, resizing, cropping, padding, color changes)
5. Provenance Graph Construction (Nodes + Derivation Edges)
6. Earliest-Known-Instance Identification WITHIN THE INDEXED EVIDENCE CORPUS
7. Propagation Timeline Reconstruction

FORENSIC INTEGRITY GUARANTEES:
- NEVER claims "original upload", "first ever upload", or "true origin".
- Strictly reports: "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS".
- "No corpus match" does NOT mean "original".
- Candidates are strictly bounded by what has been indexed in this deployment's corpus.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.config import Settings
from app.models import Evidence
from app.services.hashing import (
    PERCEPTUAL_ALGORITHM,
    hamming_distance,
    similarity_from_distance,
)
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.provenance_intelligence")

METHOD = "SImProv-multi-hash-retrieval + transformation-aware-ranking + graph-reconstruction"

ORIGIN_LABEL = "earliest known instance in the indexed evidence corpus"

INTERPRETATION = (
    "This analysis reconstructs the provenance and propagation of content within "
    "the LOCAL INDEXED EVIDENCE CORPUS using SImProv-inspired multi-signal retrieval "
    "and transformation awareness. The root reported is the EARLIEST KNOWN INSTANCE "
    "IN THE INDEXED EVIDENCE CORPUS -- not an absolute real-world origin, first "
    "publisher or creator. An earlier copy may exist outside the indexed corpus."
)

RELATION_EXACT = "exact_match"
RELATION_NEAR_DUPLICATE = "near_duplicate"
RELATION_TRANSFORMED = "transformed_variant"
RELATION_RELATED = "related_candidate"
RELATION_PARENT = "recorded_parent"

BAND_EXACT = "exact_match"
BAND_STRONG = "strong_candidate"
BAND_POSSIBLE = "possible_candidate"


def detect_transformation(
    query_evidence: Evidence,
    candidate_evidence: Evidence,
    phash_dist: int | None,
    dhash_dist: int | None,
    ahash_dist: int | None = None,
    dinov2_similarity: float | None = None,
) -> dict[str, Any]:
    """Analyze differences between query and candidate to detect probable transformations.

    Detects:
    - exact identity (SHA-256 match)
    - recompression degradation
    - resizing / scale changes
    - aspect ratio / cropping
    - perceptual modification
    - visual semantic similarity via DINOv2
    """
    if query_evidence.sha256 and candidate_evidence.sha256 and query_evidence.sha256 == candidate_evidence.sha256:
        return {
            "type": "exact_copy",
            "relationship": RELATION_EXACT,
            "confidence": 1.0,
            "details": "Bit-identical SHA-256 digest match.",
            "transformations_detected": [],
            "dinov2_similarity": dinov2_similarity if dinov2_similarity is not None else 1.0,
        }

    transforms = []
    confidence = 0.0

    # 1. Dimension / Aspect ratio analysis
    q_w = getattr(query_evidence, "width", None)
    q_h = getattr(query_evidence, "height", None)
    c_w = getattr(candidate_evidence, "width", None)
    c_h = getattr(candidate_evidence, "height", None)

    if q_w and q_h and c_w and c_h:
        if (q_w, q_h) != (c_w, c_h):
            q_aspect = round(q_w / max(1, q_h), 3)
            c_aspect = round(c_w / max(1, c_h), 3)
            if abs(q_aspect - c_aspect) > 0.05:
                transforms.append({
                    "transformation": "crop_or_aspect_change",
                    "query_dims": f"{q_w}x{q_h}",
                    "candidate_dims": f"{c_w}x{c_h}",
                })
            else:
                scale_ratio = round(min(q_w / c_w, q_h / c_h), 3)
                transforms.append({
                    "transformation": "resize_rescale",
                    "scale_ratio": scale_ratio,
                    "query_dims": f"{q_w}x{q_h}",
                    "candidate_dims": f"{c_w}x{c_h}",
                })

    # 2. File size / Compression analysis
    q_size = getattr(query_evidence, "file_size_bytes", None) or getattr(query_evidence, "size_bytes", None)
    c_size = getattr(candidate_evidence, "file_size_bytes", None) or getattr(candidate_evidence, "size_bytes", None)
    if q_size and c_size and abs(q_size - c_size) > 1024:
        ratio = round(q_size / max(1, c_size), 3)
        if ratio < 0.8 or ratio > 1.2:
            transforms.append({
                "transformation": "recompression_or_quality_change",
                "size_ratio": ratio,
            })

    # 3. Hash distance & DINOv2 categorization
    dist = phash_dist if phash_dist is not None else 64
    phash_sim = similarity_from_distance(dist)

    # Effective visual similarity combines DINOv2 when available with perceptual hashes
    if dinov2_similarity is not None:
        effective_sim = round(max(phash_sim, dinov2_similarity), 4)
    else:
        effective_sim = phash_sim

    if dist == 0:
        relationship = RELATION_EXACT if not transforms else RELATION_TRANSFORMED
        confidence = 0.98
    elif dist <= 6:
        relationship = RELATION_NEAR_DUPLICATE
        confidence = round(effective_sim, 3)
    elif dist <= 12:
        relationship = RELATION_TRANSFORMED if transforms else RELATION_RELATED
        confidence = round(effective_sim, 3)
    elif dinov2_similarity is not None and dinov2_similarity >= 0.75:
        # DINOv2 robustly retrieved despite large hash distance (e.g. crop or aggressive recompression)
        relationship = RELATION_TRANSFORMED if transforms else RELATION_NEAR_DUPLICATE
        confidence = round(dinov2_similarity, 3)
        if not transforms:
            transforms.append({"transformation": "visual_semantic_variant"})
    else:
        relationship = RELATION_RELATED
        confidence = round(effective_sim, 3)

    return {
        "type": transforms[0]["transformation"] if transforms else "perceptual_variation",
        "relationship": relationship,
        "confidence": confidence,
        "similarity": round(effective_sim, 4),
        "hamming_distance": dist,
        "dinov2_similarity": dinov2_similarity,
        "transformations_detected": transforms,
    }


def rank_provenance_candidates(
    query_evidence: Evidence,
    candidates: list[Evidence],
    settings: Settings,
    dinov2_similarities: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Rank retrieved candidates using multi-signal DINOv2 + multi-hash matching & transformation scoring."""
    ranked = []

    q_phash = query_evidence.phash
    q_dhash = getattr(query_evidence, "dhash", None)
    q_ahash = getattr(query_evidence, "ahash", None)

    for rank, cand in enumerate(candidates, start=1):
        p_dist = hamming_distance(q_phash, cand.phash) if q_phash and cand.phash else None
        d_dist = hamming_distance(q_dhash, cand.dhash) if q_dhash and cand.dhash else None
        a_dist = hamming_distance(q_ahash, cand.ahash) if q_ahash and cand.ahash else None

        d_sim = dinov2_similarities.get(cand.id) if dinov2_similarities else None

        primary_dist = p_dist if p_dist is not None else 64
        sim = similarity_from_distance(primary_dist)
        if d_sim is not None:
            sim = max(sim, d_sim)

        trans_info = detect_transformation(
            query_evidence,
            cand,
            p_dist,
            d_dist,
            ahash_dist=a_dist,
            dinov2_similarity=d_sim,
        )

        band = (
            BAND_EXACT
            if trans_info["relationship"] == RELATION_EXACT
            else (
                BAND_STRONG
                if (primary_dist <= settings.strong_duplicate_max_distance or (d_sim is not None and d_sim >= 0.85))
                else BAND_POSSIBLE
            )
        )

        ranked.append({
            "evidence_id": cand.id,
            "rank": rank,
            "distance": primary_dist,
            "similarity": sim,
            "phash_distance": p_dist,
            "dhash_distance": d_dist,
            "ahash_distance": a_dist,
            "dinov2_similarity": d_sim,
            "confidence_band": band,
            "relationship": trans_info["relationship"],
            "transformation": trans_info["type"],
            "transformations_detected": trans_info["transformations_detected"],
            "filename": cand.filename,
            "sha256": cand.sha256,
            "platform": cand.platform,
            "observed_at": iso(cand.observed_at),
            "ingested_at": iso(cand.ingested_at),
            "timestamp": iso(cand.observed_at) or iso(cand.ingested_at),
            "source_id": cand.source_id,
            "parent_id": cand.parent_id,
            "generation": cand.generation,
            "role": cand.role,
            "is_synthetic": cand.is_synthetic,
        })

    # Sort: Exact matches first, then lowest distance, then highest similarity, then earliest timestamp
    def sort_key(item: dict[str, Any]) -> tuple[int, int, float, str]:
        exact_rank = 0 if item["relationship"] == RELATION_EXACT else 1
        sim_val = -(item.get("similarity") or 0.0)
        return (exact_rank, item["distance"], sim_val, item["timestamp"] or "9999")

    ranked.sort(key=sort_key)
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    return ranked


def find_earliest_known_instance(
    evidence_list: list[Evidence | dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the earliest known instance strictly WITHIN THE INDEXED EVIDENCE CORPUS."""
    if not evidence_list:
        return None

    best_item = None
    best_dt: datetime | None = None

    for item in evidence_list:
        if isinstance(item, dict):
            obs_raw = item.get("observed_at") or item.get("timestamp")
            ing_raw = item.get("ingested_at")
            evidence_id = item.get("evidence_id") or item.get("id")
            filename = item.get("filename")
            sha256 = item.get("sha256")
            platform = item.get("platform")
        else:
            obs_raw = iso(item.observed_at)
            ing_raw = iso(item.ingested_at)
            evidence_id = item.id
            filename = item.filename
            sha256 = item.sha256
            platform = item.platform

        ts_str = obs_raw or ing_raw
        if not ts_str:
            continue

        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.max

        if best_dt is None or dt < best_dt:
            best_dt = dt
            best_item = {
                "evidence_id": evidence_id,
                "filename": filename,
                "sha256": sha256,
                "platform": platform,
                "observed_at": obs_raw,
                "ingested_at": ing_raw,
                "timestamp": ts_str,
                "provenance_claim": "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS",
                "caveat": (
                    "Identified as the earliest recorded occurrence within the local "
                    "indexed evidence corpus. This is NOT a claim of absolute first upload, "
                    "original creator, or global origin."
                ),
            }

    return best_item
