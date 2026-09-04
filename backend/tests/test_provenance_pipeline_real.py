"""End-to-end real tests for PRAMAAN Provenance & Trace Pipeline.

Tests:
1. identical files -> exact duplicate
2. recompressed/resized variant -> near duplicate
3. unrelated files -> no candidate
4. cross-case matching
5. three-stage lineage (A -> B -> C)
6. missing hashes (unhashable)
7. unsupported media (video / audio)
8. empty index handling
9. current evidence excluded from self-match
10. deleted evidence removed from searchable provenance
11. provenance graph consistency
12. earliest-known-instance ordering by timestamp
13. API response contract (propagation + matches + analysis)
14. SImProv transformation awareness
"""

from __future__ import annotations

import io
from PIL import Image
import pytest
from starlette.testclient import TestClient

from app.models import get_session_factory
from tests.helpers import make_image, encode, jpeg_bytes, mp4_bytes


# --------------------------------------------------------------------------- #
# 1. Identical Files -> Exact Duplicate
# --------------------------------------------------------------------------- #
def test_1_identical_files_exact_duplicate(client: TestClient) -> None:
    img = make_image(300, 300, seed=1001)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()

    # Ingest in Case 1
    res1 = client.post(
        "/api/cases/upload",
        files={"file": ("img1.jpg", data, "image/jpeg")},
        data={"title": "Exact Dup Case 1"},
    )
    case1_id = res1.json()["case"]["case_id"]
    ev1 = res1.json()["evidence"]

    # Ingest same bytes in Case 2 (different filename)
    res2 = client.post(
        "/api/cases/upload",
        files={"file": ("img2_copy.jpg", data, "image/jpeg")},
        data={"title": "Exact Dup Case 2"},
    )
    case2_id = res2.json()["case"]["case_id"]
    ev2 = res2.json()["evidence"]

    client.post("/api/index/rebuild")

    matches = client.post(f"/api/cases/{case2_id}/matches").json()
    query = next(q for q in matches["queries"] if q["evidence_id"] == ev2["evidence_id"])
    hit = next((c for c in query["candidates"] if c["evidence_id"] == ev1["evidence_id"]), None)

    assert hit is not None
    assert hit["distance"] == 0
    assert hit["similarity"] == 1.0
    assert hit["phash_distance"] == 0
    assert hit["dhash_distance"] == 0
    assert hit["relationship"] == "exact_match"


# --------------------------------------------------------------------------- #
# 2 & 4 & 5 & 14: Multi-Stage Lineage Across Cases & Transformations (A -> B -> C)
# --------------------------------------------------------------------------- #
def test_2_4_5_14_controlled_lineage_and_transformations(client: TestClient) -> None:
    # 1. Base Image A (500x400)
    img_a = make_image(500, 400, seed=777)
    buf_a = io.BytesIO()
    img_a.save(buf_a, format="JPEG", quality=95)
    data_a = buf_a.getvalue()

    # 2. Transformed Image B: Resized to 320x256 + Recompressed to JPEG Q45
    img_b = img_a.resize((320, 256), Image.Resampling.LANCZOS)
    buf_b = io.BytesIO()
    img_b.save(buf_b, format="JPEG", quality=45)
    data_b = buf_b.getvalue()

    # 3. Transformed Image C: Cropped slightly (280x224) + Quality 35
    img_c = img_b.crop((10, 10, 290, 234)).resize((280, 224), Image.Resampling.BILINEAR)
    buf_c = io.BytesIO()
    img_c.save(buf_c, format="JPEG", quality=35)
    data_c = buf_c.getvalue()

    # Upload A to Case 1 (earlier observed time)
    res_a = client.post(
        "/api/cases/upload",
        files={"file": ("original_a.jpg", data_a, "image/jpeg")},
        data={"title": "Case 1 - Original Source"},
    )
    case_1_id = res_a.json()["case"]["case_id"]
    ev_a = res_a.json()["evidence"]

    # Upload B to Case 2
    res_b = client.post(
        "/api/cases/upload",
        files={"file": ("transformed_b.jpg", data_b, "image/jpeg")},
        data={"title": "Case 2 - Transformed Copy"},
    )
    case_2_id = res_b.json()["case"]["case_id"]
    ev_b = res_b.json()["evidence"]

    # Upload C to Case 3
    res_c = client.post(
        "/api/cases/upload",
        files={"file": ("derivative_c.jpg", data_c, "image/jpeg")},
        data={"title": "Case 3 - Second Derivative"},
    )
    case_3_id = res_c.json()["case"]["case_id"]
    ev_c = res_c.json()["evidence"]

    # Update database timestamps so A < B < C reflecting real recording times
    session = get_session_factory()()
    try:
        from app.models import Evidence
        from datetime import datetime, timezone
        row_a = session.get(Evidence, ev_a["evidence_id"])
        row_b = session.get(Evidence, ev_b["evidence_id"])
        row_c = session.get(Evidence, ev_c["evidence_id"])
        if row_a: row_a.observed_at = datetime(2026, 8, 1, 10, 0, 0)
        if row_b: row_b.observed_at = datetime(2026, 8, 2, 10, 0, 0)
        if row_c: row_c.observed_at = datetime(2026, 8, 3, 10, 0, 0)
        session.commit()
    finally:
        session.close()

    # Rebuild index
    client.post("/api/index/rebuild")

    # Trace Case 2 (holds B)
    prop_b = client.get(f"/api/cases/{case_2_id}/propagation?refresh=true").json()
    origin_b = prop_b["origin"]
    assert origin_b is not None
    assert origin_b["evidence_id"] == ev_a["evidence_id"]
    assert origin_b["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin_b["is_absolute_origin"] is False
    assert origin_b["distance_to_case_evidence"] is not None
    assert origin_b["similarity_to_case_evidence"] is not None
    assert origin_b["similarity_to_case_evidence"] >= 0.80

    # Near-duplicate candidates for Case 2
    match_b = client.post(f"/api/cases/{case_2_id}/matches").json()
    q_b = next(q for q in match_b["queries"] if q["evidence_id"] == ev_b["evidence_id"])
    candidate_a = next(c for c in q_b["candidates"] if c["evidence_id"] == ev_a["evidence_id"])
    assert candidate_a["distance"] == origin_b["distance_to_case_evidence"]
    assert candidate_a["similarity"] == origin_b["similarity_to_case_evidence"]
    assert candidate_a["transformation"] in {"resize_rescale", "recompression_or_quality_change", "crop_or_aspect_change", "perceptual_variation"}

    # Diagnostics check
    diag = q_b.get("diagnostics")
    assert diag is not None
    assert diag["evidence_id"] == ev_b["evidence_id"]
    assert diag["indexed_count"] >= 3
    assert diag["candidate_count"] >= 1
    assert diag["comparison_attempts"] >= 1
    assert diag["successful_comparisons"] >= 1
    assert diag["best_candidate_id"] == ev_a["evidence_id"]
    assert diag["best_similarity"] == candidate_a["similarity"]

    # Trace Case 3 (holds C)
    prop_c = client.get(f"/api/cases/{case_3_id}/propagation?refresh=true").json()
    origin_c = prop_c["origin"]
    assert origin_c is not None
    assert origin_c["evidence_id"] == ev_a["evidence_id"]


# --------------------------------------------------------------------------- #
# 3. Unrelated Files -> No Candidates
# --------------------------------------------------------------------------- #
def test_3_unrelated_files_no_candidate(client: TestClient) -> None:
    img_x = Image.new("RGB", (300, 300), color=(255, 0, 0))
    buf_x = io.BytesIO()
    img_x.save(buf_x, format="JPEG")

    img_y = make_image(300, 300, seed=55555)
    buf_y = io.BytesIO()
    img_y.save(buf_y, format="JPEG")

    res_x = client.post("/api/cases/upload", files={"file": ("red.jpg", buf_x.getvalue(), "image/jpeg")})
    res_y = client.post("/api/cases/upload", files={"file": ("noise.jpg", buf_y.getvalue(), "image/jpeg")})
    case_x_id = res_x.json()["case"]["case_id"]
    ev_x_id = res_x.json()["evidence"]["evidence_id"]
    ev_y_id = res_y.json()["evidence"]["evidence_id"]

    client.post("/api/index/rebuild")

    matches_x = client.post(f"/api/cases/{case_x_id}/matches?max_distance=16").json()
    q_x = next(q for q in matches_x["queries"] if q["evidence_id"] == ev_x_id)
    cand_ids = {c["evidence_id"] for c in q_x["candidates"]}
    assert ev_y_id not in cand_ids


# --------------------------------------------------------------------------- #
# 6 & 7. Unsupported Media / Missing Hashes
# --------------------------------------------------------------------------- #
def test_6_7_video_and_unsupported_media_gracefully_handled(client: TestClient) -> None:
    res = client.post(
        "/api/cases/upload",
        files={"file": ("clip.mp4", mp4_bytes(seed=4321), "video/mp4")},
        data={"title": "Video Case"},
    )
    case_id = res.json()["case"]["case_id"]
    ev_id = res.json()["evidence"]["evidence_id"]

    client.post("/api/index/rebuild")

    prop = client.get(f"/api/cases/{case_id}/propagation").json()
    assert prop["instance_count"] == 1
    # Video has no perceptual hash -> origin should be None
    assert prop["origin"] is None
    assert any("No perceptually hashable evidence" in n for n in prop["notes"])

    matches = client.post(f"/api/cases/{case_id}/matches").json()
    q = next(q for q in matches["queries"] if q["evidence_id"] == ev_id)
    assert q["candidates"] == []
    assert any("No perceptual hash" in n for n in q["notes"])


# --------------------------------------------------------------------------- #
# 8. Empty Index Handling
# --------------------------------------------------------------------------- #
def test_8_empty_index_handling(client: TestClient) -> None:
    res = client.post(
        "/api/cases/upload",
        files={"file": ("standalone.jpg", jpeg_bytes(seed=808), "image/jpeg")},
    )
    case_id = res.json()["case"]["case_id"]
    ev_id = res.json()["evidence"]["evidence_id"]

    # Explicitly clear index
    from app.services.index import get_index
    get_index(client.app.state.settings).clear()

    matches = client.post(f"/api/cases/{case_id}/matches").json()
    q = next(q for q in matches["queries"] if q["evidence_id"] == ev_id)
    assert q["candidates"] == []
    assert any("index is empty" in n for n in q["notes"])
    assert q["diagnostics"]["rejection_reasons"] == ["empty_index"]


# --------------------------------------------------------------------------- #
# 9. Current Evidence Excluded from Self-Match
# --------------------------------------------------------------------------- #
def test_9_self_match_exclusion(client: TestClient) -> None:
    res = client.post(
        "/api/cases/upload",
        files={"file": ("self_test.jpg", jpeg_bytes(seed=909), "image/jpeg")},
    )
    case_id = res.json()["case"]["case_id"]
    ev_id = res.json()["evidence"]["evidence_id"]

    client.post("/api/index/rebuild")

    matches = client.post(f"/api/cases/{case_id}/matches").json()
    q = next(q for q in matches["queries"] if q["evidence_id"] == ev_id)
    cand_ids = {c["evidence_id"] for c in q["candidates"]}
    assert ev_id not in cand_ids


# --------------------------------------------------------------------------- #
# 10. Deleted Evidence Removed from Searchable Index
# --------------------------------------------------------------------------- #
def test_10_deleted_evidence_pruned_from_index(client: TestClient) -> None:
    res = client.post(
        "/api/cases/upload",
        files={"file": ("del.jpg", jpeg_bytes(seed=1010), "image/jpeg")},
    )
    case_id = res.json()["case"]["case_id"]
    ev_id = res.json()["evidence"]["evidence_id"]

    client.post("/api/index/rebuild")
    status1 = client.get("/api/index/status").json()

    # Delete case
    del_res = client.delete(f"/api/cases/{case_id}")
    assert del_res.status_code == 200

    status2 = client.get("/api/index/status").json()
    assert status2["indexed_count"] == status1["indexed_count"] - 1


# --------------------------------------------------------------------------- #
# 11 & 12 & 13. Graph Consistency & Earliest Known Instance Ordering
# --------------------------------------------------------------------------- #
def test_11_12_13_graph_consistency_and_earliest_instance_ordering(client: TestClient) -> None:
    # Upload A observed earlier (using /api/index/ingest with observed_at)
    res_a = client.post(
        "/api/index/ingest",
        files={"file": ("root_source.jpg", jpeg_bytes(seed=1111), "image/jpeg")},
        data={"observed_at": "2026-01-01T12:00:00Z", "platform": "twitter"},
    )
    assert res_a.status_code == 201
    ev_a = res_a.json()["evidence"]

    # Upload B (copy of A) in a case
    res_b = client.post(
        "/api/cases/upload",
        files={"file": ("case_copy.jpg", jpeg_bytes(seed=1111), "image/jpeg")},
    )
    assert res_b.status_code == 201
    case_b_id = res_b.json()["case"]["case_id"]

    client.post("/api/index/rebuild")

    prop = client.get(f"/api/cases/{case_b_id}/propagation?refresh=true").json()

    # Check origin ordering: A (2026-01-01) is earlier than B (2026-09)
    origin = prop["origin"]
    assert origin is not None
    assert origin["evidence_id"] == ev_a["evidence_id"]
    assert origin["timestamp"] == "2026-01-01T12:00:00Z"
    assert origin["platform"] == "twitter"
    assert origin["label"] == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False
    assert origin["distance_to_case_evidence"] == 0
    assert origin["similarity_to_case_evidence"] == 1.0

    # Graph check
    graph = prop["graph"]
    node_ids = {n["evidence_id"] for n in graph["nodes"]}
    assert ev_a["evidence_id"] in node_ids
    assert len(graph["edges"]) >= 1
    edge = next((e for e in graph["edges"] if e["relation"] == "near_duplicate_candidate"), None)
    assert edge is not None
    assert edge["relation"] == "near_duplicate_candidate"
    assert edge["verified_by_pramaan"] is True
    assert edge["similarity"] == 1.0
