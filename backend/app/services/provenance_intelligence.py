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
from typing import Any

from app.models import Evidence
from app.services.hashing import similarity_from_distance

logger = logging.getLogger("pramaan.provenance_intelligence")

# Relationship tokens emitted by detect_transformation. The canonical copies of
# the origin wording and the ranking/persistence pipeline live in propagation.py
# and matching.py respectively; this module's dead duplicates of those were
# removed after the audit found no caller for them.
RELATION_EXACT = "exact_match"
RELATION_NEAR_DUPLICATE = "near_duplicate"
RELATION_TRANSFORMED = "transformed_variant"
RELATION_RELATED = "related_candidate"


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
            "dinov2_similarity": dinov2_similarity,
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
