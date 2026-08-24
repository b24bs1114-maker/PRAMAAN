"""Propagation and origin reconstruction.

Given a case's near-duplicate candidates, this reconstructs how copies of the
same visual content appear across the **indexed corpus**: a timeline of dated
instances and a graph of two clearly distinguished relationship types.

* ``recorded_parent`` -- lineage read from provenance fields (``parent_id``,
  ``source_id``, ``generation``) that were supplied when the item was ingested.
  For the demo corpus those fields come from the synthetic manifest.
* ``near_duplicate_candidate`` -- perceptual-hash similarity computed here. It is
  a similarity measurement, not a derivation claim.

The wording of the origin result is deliberate and must not be relaxed: what is
reported is the **earliest known instance in the indexed evidence corpus**. The
corpus is a local, partial view of the world, so an earlier copy may exist
outside it. Nothing here identifies an absolute real-world origin, first
publisher, or creator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    KIND_PROPAGATION,
    ROLE_CASE_EVIDENCE,
    Case,
    Evidence,
    TimelineEvent,
)
from app.services import analysis_store, audit, matching
from app.services.hashing import hamming_distance, similarity_from_distance
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.propagation")

METHOD = "near-duplicate expansion + recorded lineage traversal"

ORIGIN_LABEL = "earliest known instance in the indexed evidence corpus"

INTERPRETATION = (
    "This is a reconstruction of how copies of the same visual content appear in "
    "the LOCAL INDEXED EVIDENCE CORPUS. The origin reported is the EARLIEST KNOWN "
    "INSTANCE IN THE INDEXED EVIDENCE CORPUS -- not the absolute real-world "
    "origin, first publisher or creator. The corpus is a partial view: an earlier "
    "copy may exist outside it, and timestamps recorded by platforms or embedded "
    "in files can be wrong or deliberately altered."
)

CAVEATS = [
    "Coverage is limited to items ingested into this deployment's index; absence "
    "of an earlier copy is not proof that none exists.",
    "Timestamps are taken from recorded observation times where available and fall "
    "back to local ingestion time, which reflects when PRAMAAN saw the file rather "
    "than when the copy was created.",
    "'recorded_parent' edges restate provenance metadata supplied at ingestion "
    "time; they are not independently verified by PRAMAAN.",
    "'near_duplicate_candidate' edges are perceptual-similarity measurements. "
    "Similarity does not establish that one item was derived from the other.",
    "Ordering by timestamp does not establish causation between instances.",
]

RELATION_PARENT = "recorded_parent"
RELATION_NEAR_DUPLICATE = "near_duplicate_candidate"

DISCOVERED_BY_CASE = "case_evidence"
DISCOVERED_BY_MATCH = "perceptual_match"
DISCOVERED_BY_LINEAGE = "recorded_lineage"

# Bound on graph expansion so a pathological lineage group cannot produce an
# unbounded response. Truncation is reported, never silent.
MAX_NODES = 250


def _node_timestamp(evidence: Evidence) -> tuple[datetime | None, str]:
    """Best available time for an instance, plus where it came from."""
    if evidence.observed_at is not None:
        return evidence.observed_at, "recorded_observation"
    return evidence.ingested_at, "local_ingestion"


def _min_distance_to_case(
    evidence: Evidence, case_hashes: list[str]
) -> tuple[int | None, float | None]:
    distances = [
        d
        for d in (hamming_distance(evidence.phash, h) for h in case_hashes)
        if d is not None
    ]
    if not distances:
        return None, None
    best = min(distances)
    return best, similarity_from_distance(best)


def _collect_nodes(
    session: Session,
    *,
    case_evidence: list[Evidence],
    matched: dict[str, int],
) -> tuple[dict[str, Evidence], dict[str, str], bool]:
    """Case evidence + matched candidates + their recorded lineage group.

    Returns ``(rows_by_id, discovered_by, truncated)``.
    """
    rows: dict[str, Evidence] = {e.id: e for e in case_evidence}
    discovered: dict[str, str] = {e.id: DISCOVERED_BY_CASE for e in case_evidence}

    if matched:
        for row in session.execute(
            select(Evidence).where(Evidence.id.in_(list(matched)))
        ).scalars():
            rows.setdefault(row.id, row)
            discovered.setdefault(row.id, DISCOVERED_BY_MATCH)

    # Expand along recorded lineage: every item sharing a lineage group with a
    # matched candidate, plus the group roots those ids point at.
    lineage_ids = {
        row.source_id
        for row in rows.values()
        if row.source_id and discovered.get(row.id) != DISCOVERED_BY_CASE
    }
    if lineage_ids:
        for row in session.execute(
            select(Evidence)
            .where(Evidence.source_id.in_(list(lineage_ids)))
            .order_by(Evidence.observed_at, Evidence.ingested_at)
        ).scalars():
            if len(rows) >= MAX_NODES:
                return rows, discovered, True
            rows.setdefault(row.id, row)
            discovered.setdefault(row.id, DISCOVERED_BY_LINEAGE)
        # A group root is referenced by source_id but may not carry one itself.
        missing_roots = [rid for rid in lineage_ids if rid not in rows]
        if missing_roots:
            for row in session.execute(
                select(Evidence).where(Evidence.id.in_(missing_roots))
            ).scalars():
                if len(rows) >= MAX_NODES:
                    return rows, discovered, True
                rows.setdefault(row.id, row)
                discovered.setdefault(row.id, DISCOVERED_BY_LINEAGE)

    # Walk recorded parents upwards so an ancestor outside the lineage query is
    # still represented.
    frontier = [r.parent_id for r in rows.values() if r.parent_id]
    depth = 0
    while frontier and depth < 32:
        unknown = [pid for pid in set(frontier) if pid and pid not in rows]
        if not unknown:
            break
        parents = list(
            session.execute(select(Evidence).where(Evidence.id.in_(unknown))).scalars()
        )
        frontier = []
        for row in parents:
            if len(rows) >= MAX_NODES:
                return rows, discovered, True
            rows[row.id] = row
            discovered.setdefault(row.id, DISCOVERED_BY_LINEAGE)
            if row.parent_id:
                frontier.append(row.parent_id)
        depth += 1

    return rows, discovered, False


def _origin(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Earliest dated instance among the reconstructed nodes."""
    dated = [n for n in nodes if n["timestamp"]]
    if not dated:
        return None
    earliest = min(
        dated,
        key=lambda n: (
            n["timestamp"],
            n["generation"] if n["generation"] is not None else 99,
            n["evidence_id"],
        ),
    )
    return {
        "label": ORIGIN_LABEL,
        "evidence_id": earliest["evidence_id"],
        "filename": earliest["filename"],
        "timestamp": earliest["timestamp"],
        "timestamp_source": earliest["timestamp_source"],
        "platform": earliest["platform"],
        "generation": earliest["generation"],
        "source_id": earliest["source_id"],
        "role": earliest["role"],
        "is_synthetic": earliest["is_synthetic"],
        "discovered_by": earliest["discovered_by"],
        "distance_to_case_evidence": earliest["distance_to_case_evidence"],
        "is_absolute_origin": False,
        "caveat": (
            "This is the earliest instance PRAMAAN can see in its local index. It "
            "is NOT established as the absolute real-world origin: earlier copies "
            "may exist outside the corpus, and the timestamp itself may be "
            "inaccurate."
        ),
    }


def _persist_timeline(
    session: Session, *, case_id: str, timeline: list[dict[str, Any]]
) -> None:
    stale = list(
        session.execute(
            select(TimelineEvent).where(TimelineEvent.case_id == case_id)
        ).scalars()
    )
    for row in stale:
        session.delete(row)
    if stale:
        session.flush()

    for entry in timeline:
        session.add(
            TimelineEvent(
                id=str(uuid.uuid4()),
                case_id=case_id,
                evidence_id=entry["evidence_id"],
                event_type=entry["event_type"],
                occurred_at=entry["_occurred_at"],
                platform=entry["platform"],
                generation=entry["generation"],
                description=entry["description"],
            )
        )
    session.flush()


def reconstruct_case(
    session: Session,
    *,
    case: Case,
    settings: Settings,
    actor: str = "api",
    refresh: bool = False,
    top_k: int | None = None,
    max_distance: int | None = None,
) -> dict[str, Any]:
    """Reconstruct propagation for a case from matches and recorded lineage."""
    stored = matching.stored_matches(session, case_id=case.id)
    match_search: dict[str, Any] | None = None
    if refresh or not stored:
        match_search = matching.search_case(
            session,
            case=case,
            settings=settings,
            top_k=top_k,
            max_distance=max_distance,
            actor=actor,
        )
        stored = matching.stored_matches(session, case_id=case.id)

    case_evidence = list(
        session.execute(
            select(Evidence)
            .where(Evidence.case_id == case.id)
            .order_by(Evidence.ingested_at)
        ).scalars()
    )
    case_ids = {e.id for e in case_evidence}
    case_hashes = [e.phash for e in case_evidence if e.phash]

    # Best (smallest) distance per matched candidate across the case's evidence.
    matched: dict[str, int] = {}
    for row in stored:
        current = matched.get(row.candidate_evidence_id)
        if current is None or row.distance < current:
            matched[row.candidate_evidence_id] = row.distance

    rows, discovered, truncated = _collect_nodes(
        session, case_evidence=case_evidence, matched=matched
    )

    nodes: list[dict[str, Any]] = []
    for row in rows.values():
        occurred_at, timestamp_source = _node_timestamp(row)
        distance, similarity = _min_distance_to_case(row, case_hashes)
        if row.id in case_ids:
            distance, similarity = 0, 1.0
        nodes.append(
            {
                "evidence_id": row.id,
                "filename": row.filename,
                "role": row.role,
                "is_case_evidence": row.role == ROLE_CASE_EVIDENCE
                and row.id in case_ids,
                "platform": row.platform,
                "generation": row.generation,
                "source_id": row.source_id,
                "parent_id": row.parent_id,
                "transformation": row.transformation,
                "sha256": row.sha256,
                "is_synthetic": row.is_synthetic,
                "timestamp": iso(occurred_at),
                "timestamp_source": timestamp_source,
                "discovered_by": discovered.get(row.id, DISCOVERED_BY_LINEAGE),
                "distance_to_case_evidence": distance,
                "similarity_to_case_evidence": similarity,
                "_occurred_at": occurred_at,
            }
        )

    nodes.sort(
        key=lambda n: (
            n["timestamp"] is None,
            n["timestamp"] or "",
            n["generation"] if n["generation"] is not None else 99,
            n["evidence_id"],
        )
    )

    edges: list[dict[str, Any]] = []
    for node in nodes:
        parent_id = node["parent_id"]
        if parent_id and parent_id in rows:
            edges.append(
                {
                    "source": parent_id,
                    "target": node["evidence_id"],
                    "relation": RELATION_PARENT,
                    "basis": "provenance metadata recorded at ingestion",
                    "transformation": node["transformation"],
                    "verified_by_pramaan": False,
                }
            )
    for row in stored:
        if row.candidate_evidence_id in rows and row.query_evidence_id in rows:
            edges.append(
                {
                    "source": row.query_evidence_id,
                    "target": row.candidate_evidence_id,
                    "relation": RELATION_NEAR_DUPLICATE,
                    "basis": "perceptual hash similarity computed by PRAMAAN",
                    "distance": row.distance,
                    "similarity": row.similarity,
                    "confidence_band": row.confidence_band,
                    "verified_by_pramaan": True,
                }
            )

    timeline = [
        {
            "evidence_id": node["evidence_id"],
            "event_type": (
                "CASE_EVIDENCE_INGESTED"
                if node["is_case_evidence"]
                else "CORPUS_INSTANCE_OBSERVED"
            ),
            "occurred_at": node["timestamp"],
            "timestamp_source": node["timestamp_source"],
            "platform": node["platform"],
            "generation": node["generation"],
            "transformation": node["transformation"],
            "distance_to_case_evidence": node["distance_to_case_evidence"],
            "discovered_by": node["discovered_by"],
            "is_synthetic": node["is_synthetic"],
            "description": (
                f"{node['filename']} "
                + (
                    "submitted as case evidence"
                    if node["is_case_evidence"]
                    else "observed in the indexed corpus"
                    + (f" on {node['platform']}" if node["platform"] else "")
                )
                + (
                    f" (recorded transformation: {node['transformation']})"
                    if node["transformation"]
                    else ""
                )
            ),
            "_occurred_at": node["_occurred_at"],
        }
        for node in nodes
        if node["timestamp"]
    ]
    undated = [
        {"evidence_id": n["evidence_id"], "filename": n["filename"]}
        for n in nodes
        if not n["timestamp"]
    ]

    origin = _origin(nodes)
    _persist_timeline(session, case_id=case.id, timeline=timeline)

    public_nodes = [
        {k: v for k, v in node.items() if not k.startswith("_")} for node in nodes
    ]
    public_timeline = [
        {k: v for k, v in entry.items() if not k.startswith("_")} for entry in timeline
    ]

    notes: list[str] = []
    if not case_hashes:
        notes.append(
            "No perceptually hashable evidence in this case, so no near-duplicate "
            "expansion was possible."
        )
    if not matched:
        notes.append(
            "No near-duplicate candidates were found in the index, so the "
            "reconstruction covers only this case's own evidence. That is not "
            "evidence that no other copies exist."
        )
    if truncated:
        notes.append(
            f"Graph expansion stopped at the {MAX_NODES}-node limit; the "
            "reconstruction shown is partial."
        )
    if any(n["is_synthetic"] for n in public_nodes):
        notes.append(
            "One or more instances are marked SYNTHETIC DEMO DATA and do not "
            "represent real-world observations."
        )

    result: dict[str, Any] = {
        "case_id": case.id,
        "method": METHOD,
        "interpretation": INTERPRETATION,
        "origin": origin,
        "timeline": public_timeline,
        "undated_instances": undated,
        "graph": {
            "nodes": public_nodes,
            "edges": edges,
            "node_count": len(public_nodes),
            "edge_count": len(edges),
            "relations": {
                RELATION_PARENT: "recorded provenance lineage (not verified here)",
                RELATION_NEAR_DUPLICATE: "perceptual similarity measured by PRAMAAN",
            },
        },
        "instance_count": len(public_nodes),
        "matched_candidate_count": len(matched),
        "platforms": sorted({n["platform"] for n in public_nodes if n["platform"]}),
        "generations": sorted(
            {n["generation"] for n in public_nodes if n["generation"] is not None}
        ),
        "truncated": truncated,
        "notes": notes,
        "caveats": CAVEATS,
    }
    if match_search is not None:
        result["match_search"] = {
            "ran": True,
            "total_candidates": match_search["total_candidates"],
            "thresholds": match_search["thresholds"],
        }

    for evidence in case_evidence:
        analysis_store.store_result(
            session,
            case_id=case.id,
            evidence_id=evidence.id,
            kind=KIND_PROPAGATION,
            payload={
                "origin": origin,
                "instance_count": len(public_nodes),
                "matched_candidate_count": len(matched),
                "timeline_length": len(public_timeline),
                "method": METHOD,
                "notes": notes,
            },
            status="OK",
        )

    audit.record(
        session,
        event=audit.EVENT_PROPAGATION_RECONSTRUCTED,
        case_id=case.id,
        actor=actor,
        details={
            "instances": len(public_nodes),
            "edges": len(edges),
            "timeline_events": len(public_timeline),
            "matched_candidates": len(matched),
            "earliest_known_instance": origin["evidence_id"] if origin else None,
            "earliest_known_timestamp": origin["timestamp"] if origin else None,
            "origin_label": ORIGIN_LABEL,
            "method": METHOD,
            "truncated": truncated,
        },
    )
    return result
