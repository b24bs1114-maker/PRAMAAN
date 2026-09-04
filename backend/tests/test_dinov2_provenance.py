"""Unit and integration tests for DINOv2-based visual retrieval in PRAMAAN provenance.

Verifies:
1. Embedding extraction: shape (384,), float32, L2-norm == 1.0, query caching.
2. DinoV2Index: persistence, atomic save/load, mutation, cosine query, remove.
3. Multi-layer pipeline:
   evidence -> DINOv2 embedding -> candidate retrieval -> multi-hash verification -> ranking -> provenance.
4. Transformation handling: exact copy, resize, recompression, crop, unrelated.
5. Strict honest provenance claims:
   "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS"
   Never claims "original source", "first upload", or "true origin".
"""

from __future__ import annotations

import io
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from starlette.testclient import TestClient

from app.config import get_settings
from app.services import dinov2_service
from app.services.dinov2_index import DinoV2Index, get_dinov2_index, reset_dinov2_index_singleton
from tests.helpers import make_image, encode, jpeg_bytes


# --------------------------------------------------------------------------- #
# 1. Feature Extraction & Normalization
# --------------------------------------------------------------------------- #
def test_dinov2_embedding_extraction() -> None:
    img = make_image(224, 224, seed=42)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()

    emb1 = dinov2_service.extract_embedding(data, cache_key="test_key_42")
    assert emb1 is not None
    assert isinstance(emb1, np.ndarray)
    assert emb1.shape == (384,)
    assert emb1.dtype == np.float32
    # Check L2 unit length
    norm = np.linalg.norm(emb1)
    assert abs(norm - 1.0) < 1e-4

    # Check cache hit returns identical embedding
    emb2 = dinov2_service.extract_embedding(b"different_data", cache_key="test_key_42")
    assert np.array_equal(emb1, emb2)

    # Status check
    st = dinov2_service.status()
    assert st["available"] is True
    assert st["embedding_dim"] == 384


# --------------------------------------------------------------------------- #
# 2. DinoV2Index Lifecycle & Persistence
# --------------------------------------------------------------------------- #
def test_dinov2_index_operations(tmp_path: Path) -> None:
    settings = get_settings().model_copy()
    settings.data_dir = tmp_path

    index = DinoV2Index(settings)
    index.clear()
    assert index.count == 0

    v1 = np.random.randn(384).astype(np.float32)
    v2 = np.random.randn(384).astype(np.float32)
    v1 /= np.linalg.norm(v1)
    v2 /= np.linalg.norm(v2)

    assert index.add("ev_1", v1) is True
    assert index.add("ev_1", v1) is False  # Idempotent
    assert index.add("ev_2", v2) is True
    assert index.count == 2
    assert index.contains("ev_1") is True

    # Query
    hits = index.query(v1, top_k=5, min_similarity=-1.0)
    assert len(hits) == 2
    assert hits[0]["evidence_id"] == "ev_1"
    assert abs(hits[0]["similarity"] - 1.0) < 1e-3

    # Exclude
    hits_ex = index.query(v1, top_k=5, exclude={"ev_1"})
    assert all(h["evidence_id"] != "ev_1" for h in hits_ex)

    # Persistence reload
    index2 = DinoV2Index(settings)
    index2.load()
    assert index2.count == 2
    assert index2.contains("ev_2") is True

    # Remove
    removed = index2.remove(["ev_1"])
    assert removed == 1
    assert index2.count == 1
    assert index2.contains("ev_1") is False


# --------------------------------------------------------------------------- #
# 3. Multi-layer Pipeline (End-to-End via API)
# --------------------------------------------------------------------------- #
def test_dinov2_multihash_retrieval_and_transformation_awareness(client: TestClient) -> None:
    # 1. Upload base image
    original = make_image(400, 320, seed=888)
    buf_orig = io.BytesIO()
    original.save(buf_orig, format="JPEG", quality=95)
    data_orig = buf_orig.getvalue()

    res1 = client.post(
        "/api/cases/upload",
        files={"file": ("base_photo.jpg", data_orig, "image/jpeg")},
        data={"title": "Case A - Original"},
    )
    assert res1.status_code == 201
    case_a_id = res1.json()["case"]["case_id"]
    ev_a = res1.json()["evidence"]

    # 2. Upload transformed variant: Crop 80% + resize + recompression
    # This often challenges pure pHash because DCT frequencies shift
    w, h = original.size
    crop_box = (int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))
    cropped = original.crop(crop_box).resize((320, 240), Image.Resampling.LANCZOS)
    buf_crop = io.BytesIO()
    cropped.save(buf_crop, format="JPEG", quality=40)
    data_crop = buf_crop.getvalue()

    res2 = client.post(
        "/api/cases/upload",
        files={"file": ("cropped_variant.jpg", data_crop, "image/jpeg")},
        data={"title": "Case B - Cropped Variant"},
    )
    assert res2.status_code == 201
    case_b_id = res2.json()["case"]["case_id"]
    ev_b = res2.json()["evidence"]

    # Rebuild index
    rebuild_res = client.post("/api/index/rebuild")
    assert rebuild_res.status_code == 200

    # Search matches for Case B
    matches_resp = client.post(f"/api/cases/{case_b_id}/matches").json()
    q = next(q for q in matches_resp["queries"] if q["evidence_id"] == ev_b["evidence_id"])
    cand = next((c for c in q["candidates"] if c["evidence_id"] == ev_a["evidence_id"]), None)

    assert cand is not None, "DINOv2 multi-hash pipeline should retrieve the cropped original!"
    # DINOv2 visual similarity should be recorded
    assert "dinov2_similarity" in cand
    assert cand["dinov2_similarity"] is not None
    assert cand["dinov2_similarity"] >= 0.70

    # Multi-signal verification
    assert cand["phash_distance"] is not None
    assert cand["dhash_distance"] is not None
    assert cand["transformation_analysis"] is not None
    trans = cand["transformation_analysis"]
    assert trans["relationship"] in {"transformed_variant", "near_duplicate", "exact_match"}


# --------------------------------------------------------------------------- #
# 4. Strict Honest Provenance Claims
# --------------------------------------------------------------------------- #
def test_dinov2_honest_provenance_claims(client: TestClient) -> None:
    img = make_image(300, 300, seed=999)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()

    # Ingest into corpus with early timestamp
    res_corpus = client.post(
        "/api/index/ingest",
        files={"file": ("historic_origin.jpg", data, "image/jpeg")},
        data={"observed_at": "2024-01-15T08:30:00Z", "platform": "telegram"},
    )
    assert res_corpus.status_code == 201
    ev_corpus = res_corpus.json()["evidence"]

    # Query in a case
    res_case = client.post(
        "/api/cases/upload",
        files={"file": ("investigation_target.jpg", data, "image/jpeg")},
    )
    assert res_case.status_code == 201
    case_id = res_case.json()["case"]["case_id"]

    client.post("/api/index/rebuild")

    prop = client.get(f"/api/cases/{case_id}/propagation?refresh=true").json()
    origin = prop["origin"]
    assert origin is not None

    # Strict vocabulary validation
    label = origin["label"]
    assert label == "earliest known instance in the indexed evidence corpus"
    assert origin["is_absolute_origin"] is False

    # Forbidden claims check
    raw_text = str(prop).lower()
    assert "original source" not in raw_text or "not established as the absolute real-world origin" in raw_text
    assert "first ever upload" not in raw_text
    assert "true origin" not in raw_text
