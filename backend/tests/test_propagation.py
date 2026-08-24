"""Propagation reconstruction tests (TASK 8).

The binding requirement under test: the earliest instance found is reported as
the **earliest known instance in the indexed evidence corpus** and never as an
absolute real-world origin.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from PIL import Image

from tests.helpers import encode, jpeg_bytes, make_image


def _ingest_corpus(client: TestClient, data: bytes, name: str, **provenance) -> dict:
    form = {k: str(v) for k, v in provenance.items() if v is not None}
    response = client.post(
        "/api/index/ingest", files={"file": (name, data, "image/jpeg")}, data=form
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _build_chain(
    client: TestClient,
    seed: int,
    tag: str,
    *,
    root_time: str = "2026-01-02T10:00:00Z",
    gen1_time: str = "2026-01-04T12:30:00Z",
    gen2_time: str = "2026-01-09T18:45:00Z",
) -> dict:
    """A three-generation lineage in the corpus, plus the case upload.

    generation 0 (earliest, platform_x) -> 1 (platform_y) -> 2 (platform_z);
    the case evidence is a recompression of generation 2. ``root_time`` is
    explicit because the corpus is shared across tests: a test that asserts
    *which* instance is earliest must own the earliest timestamp.
    """
    original = make_image(480, 360, seed=seed)
    gen1_image = original.resize((360, 270), Image.Resampling.LANCZOS)
    gen2_image = gen1_image.resize((300, 225), Image.Resampling.LANCZOS)

    root = _ingest_corpus(
        client,
        encode(original, "JPEG", quality=92),
        f"{tag}-gen0.jpg",
        platform="platform_x",
        generation=0,
        observed_at=root_time,
        is_synthetic=True,
    )["evidence"]
    _ingest_corpus(
        client,
        encode(gen1_image, "JPEG", quality=80),
        f"{tag}-gen1.jpg",
        source_id=root["evidence_id"],
        parent_id=root["evidence_id"],
        platform="platform_y",
        generation=1,
        observed_at=gen1_time,
        transformation="resize",
        is_synthetic=True,
    )
    gen2 = _ingest_corpus(
        client,
        encode(gen2_image, "JPEG", quality=65),
        f"{tag}-gen2.jpg",
        source_id=root["evidence_id"],
        parent_id=root["evidence_id"],
        platform="platform_z",
        generation=2,
        observed_at=gen2_time,
        transformation="resize+jpeg65",
        is_synthetic=True,
    )["evidence"]

    uploaded = client.post(
        "/api/cases/upload",
        files={
            "file": (
                f"{tag}-case.jpg",
                encode(gen2_image, "JPEG", quality=60),
                "image/jpeg",
            )
        },
    ).json()
    return {"root": root, "gen2": gen2, "uploaded": uploaded}


def test_propagation_reports_earliest_known_instance(client: TestClient) -> None:
    chain = _build_chain(client, 601, "prop", root_time="2026-01-01T00:01:00Z")
    case_id = chain["uploaded"]["case"]["case_id"]

    response = client.get(f"/api/cases/{case_id}/propagation")
    assert response.status_code == 200, response.text
    body = response.json()

    origin = body["origin"]
    assert origin is not None
    assert origin["evidence_id"] == chain["root"]["evidence_id"]
    assert origin["timestamp"] == "2026-01-01T00:01:00Z"
    assert origin["platform"] == "platform_x"
    assert origin["generation"] == 0
    assert origin["timestamp_source"] == "recorded_observation"

    # The origin is the earliest dated node in the reconstruction, nothing more.
    dated = [n["timestamp"] for n in body["graph"]["nodes"] if n["timestamp"]]
    assert origin["timestamp"] == min(dated)

    # Wording: earliest KNOWN instance in the INDEXED CORPUS, never absolute.
    assert origin["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False
    assert "NOT established as the absolute real-world origin" in origin["caveat"]

    # Wording: earliest KNOWN instance in the INDEXED CORPUS, never absolute.
    assert origin["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False
    assert "NOT established as the absolute real-world origin" in origin["caveat"]


def test_timeline_is_chronological_and_labels_its_basis(client: TestClient) -> None:
    chain = _build_chain(client, 602, "timeline")
    case_id = chain["uploaded"]["case"]["case_id"]

    body = client.get(f"/api/cases/{case_id}/propagation").json()
    timeline = body["timeline"]
    assert len(timeline) >= 4  # three corpus generations + the case evidence

    stamps = [entry["occurred_at"] for entry in timeline]
    assert stamps == sorted(stamps)
    assert all(entry["timestamp_source"] in
               ("recorded_observation", "local_ingestion") for entry in timeline)
    assert any(entry["event_type"] == "CASE_EVIDENCE_INGESTED" for entry in timeline)
    assert any(entry["event_type"] == "CORPUS_INSTANCE_OBSERVED" for entry in timeline)

    platforms = body["platforms"]
    assert {"platform_x", "platform_y", "platform_z"} <= set(platforms)
    assert body["generations"] == [0, 1, 2]


def test_graph_separates_recorded_lineage_from_measured_similarity(
    client: TestClient,
) -> None:
    chain = _build_chain(client, 603, "graph")
    case_id = chain["uploaded"]["case"]["case_id"]
    query_id = chain["uploaded"]["evidence"]["evidence_id"]

    graph = client.get(f"/api/cases/{case_id}/propagation").json()["graph"]
    node_ids = {node["evidence_id"] for node in graph["nodes"]}
    assert query_id in node_ids
    assert chain["root"]["evidence_id"] in node_ids
    assert graph["node_count"] == len(graph["nodes"])

    parent_edges = [e for e in graph["edges"] if e["relation"] == "recorded_parent"]
    similarity_edges = [
        e for e in graph["edges"] if e["relation"] == "near_duplicate_candidate"
    ]
    assert parent_edges, "recorded lineage edges missing"
    assert similarity_edges, "near-duplicate edges missing"

    # Recorded lineage is restated metadata; similarity is measured here.
    assert all(e["verified_by_pramaan"] is False for e in parent_edges)
    nodes_by_id = {n["evidence_id"]: n for n in graph["nodes"]}
    # Every lineage edge restates the target's recorded parent_id verbatim.
    assert all(
        nodes_by_id[e["target"]]["parent_id"] == e["source"] for e in parent_edges
    )
    assert any(e["source"] == chain["root"]["evidence_id"] for e in parent_edges)
    assert all(e["verified_by_pramaan"] is True for e in similarity_edges)
    assert all(e["source"] == query_id for e in similarity_edges)
    assert all(
        isinstance(e["distance"], int) and 0 <= e["distance"] <= 64
        for e in similarity_edges
    )
    assert set(graph["relations"]) == {"recorded_parent", "near_duplicate_candidate"}


def test_case_evidence_is_marked_and_measured_against_itself(
    client: TestClient,
) -> None:
    chain = _build_chain(client, 604, "self")
    case_id = chain["uploaded"]["case"]["case_id"]
    query_id = chain["uploaded"]["evidence"]["evidence_id"]

    nodes = client.get(f"/api/cases/{case_id}/propagation").json()["graph"]["nodes"]
    own = next(n for n in nodes if n["evidence_id"] == query_id)
    assert own["is_case_evidence"] is True
    assert own["discovered_by"] == "case_evidence"
    assert own["distance_to_case_evidence"] == 0
    assert own["similarity_to_case_evidence"] == 1.0

    corpus_node = next(
        n for n in nodes if n["evidence_id"] == chain["root"]["evidence_id"]
    )
    assert corpus_node["is_case_evidence"] is False
    assert corpus_node["discovered_by"] in ("perceptual_match", "recorded_lineage")
    assert corpus_node["distance_to_case_evidence"] is not None


def test_interpretation_and_caveats_never_claim_absolute_origin(
    client: TestClient,
) -> None:
    chain = _build_chain(client, 605, "wording")
    case_id = chain["uploaded"]["case"]["case_id"]
    body = client.get(f"/api/cases/{case_id}/propagation").json()

    interpretation = body["interpretation"]
    assert "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS" in interpretation
    lowered = interpretation.lower()
    assert "not the absolute real-world origin" in lowered
    assert "partial view" in lowered

    caveats = " ".join(body["caveats"]).lower()
    assert "not proof that none exists" in caveats
    assert "does not establish causation" in caveats
    assert "not independently verified" in caveats

    # Synthetic corpus items must be flagged as such, not passed off as real.
    assert any("synthetic demo data" in note.lower() for note in body["notes"])


def test_timeline_events_are_persisted_and_replaced(client: TestClient) -> None:
    chain = _build_chain(client, 606, "persist")
    case_id = chain["uploaded"]["case"]["case_id"]
    expected = len(client.get(f"/api/cases/{case_id}/propagation").json()["timeline"])

    from app.models import TimelineEvent, get_session_factory

    session = get_session_factory()()
    try:
        rows = (
            session.query(TimelineEvent).filter(TimelineEvent.case_id == case_id).all()
        )
        assert len(rows) == expected
        assert all(row.occurred_at is not None for row in rows)
        session.close()

        client.get(f"/api/cases/{case_id}/propagation", params={"refresh": "true"})

        session = get_session_factory()()
        again = (
            session.query(TimelineEvent).filter(TimelineEvent.case_id == case_id).all()
        )
        assert len(again) == expected  # replaced, not accumulated
    finally:
        session.close()


def test_propagation_result_is_stored_and_audited(client: TestClient) -> None:
    chain = _build_chain(client, 607, "audit", root_time="2026-01-01T00:00:30Z")
    case_id = chain["uploaded"]["case"]["case_id"]
    evidence_id = chain["uploaded"]["evidence"]["evidence_id"]
    client.get(f"/api/cases/{case_id}/propagation")

    from app.models import (
        KIND_PROPAGATION,
        AnalysisResult,
        AuditLog,
        get_session_factory,
    )

    session = get_session_factory()()
    try:
        stored = (
            session.query(AnalysisResult)
            .filter(
                AnalysisResult.evidence_id == evidence_id,
                AnalysisResult.kind == KIND_PROPAGATION,
            )
            .all()
        )
        entry = (
            session.query(AuditLog)
            .filter(
                AuditLog.case_id == case_id,
                AuditLog.event == "PROPAGATION_RECONSTRUCTED",
            )
            .first()
        )
    finally:
        session.close()

    assert len(stored) == 1
    assert stored[0].payload["origin"]["evidence_id"] == chain["root"]["evidence_id"]
    assert entry is not None
    assert entry.details["earliest_known_instance"] == chain["root"]["evidence_id"]
    assert (
        entry.details["origin_label"]
        == "earliest known instance in the indexed evidence corpus"
    )


def test_case_with_no_candidates_states_it_plainly(client: TestClient, settings) -> None:
    """No matches must produce a note, not an implied 'nothing else exists'."""
    from app.services.index import get_index

    uploaded = client.post(
        "/api/cases/upload",
        files={"file": ("lonely.jpg", jpeg_bytes(seed=608), "image/jpeg")},
    ).json()
    case_id = uploaded["case"]["case_id"]
    evidence_id = uploaded["evidence"]["evidence_id"]

    try:
        # Deterministic "nothing to compare against" state.
        get_index(settings).clear()
        body = client.get(
            f"/api/cases/{case_id}/propagation", params={"refresh": "true"}
        ).json()
    finally:
        assert client.post("/api/index/rebuild").status_code == 200

    assert body["matched_candidate_count"] == 0
    # The case's own evidence is still the single instance in the reconstruction.
    assert body["instance_count"] == 1
    assert body["graph"]["nodes"][0]["evidence_id"] == evidence_id
    assert body["graph"]["edges"] == []
    assert body["origin"]["evidence_id"] == evidence_id
    assert body["origin"]["timestamp_source"] == "local_ingestion"
    note = " ".join(body["notes"]).lower()
    assert "not evidence that no other copies exist" in note


def test_propagation_for_unknown_case_returns_404(client: TestClient) -> None:
    assert client.get(f"/api/cases/{uuid.uuid4()}/propagation").status_code == 404
