"""Regression test: provenance consistency between matches and propagation.

Verifies that when two near-duplicate images are in the same case:
- The match search finds cross-candidates (A finds B, B finds A).
- The propagation graph is consistent with the match count.
- The total_candidates count is non-zero when real matches exist.

Also tests:
- Unrelated file → 0 candidates within reasonable distance.
- Single evidence → 0 candidates (only item in index for its query).
"""

from __future__ import annotations

import io
import pytest
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient


def _make_image(width: int, height: int, seed: int, fmt: str = "JPEG") -> bytes:
    """Generate a deterministic synthetic image."""
    import random
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    return buf.getvalue()


def _make_near_duplicate(original_bytes: bytes, scale: float = 0.75, quality: int = 60) -> bytes:
    """Create a near-duplicate: resized + recompressed."""
    img = Image.open(io.BytesIO(original_bytes))
    new_size = (int(img.width * scale), int(img.height * scale))
    resized = img.resize(new_size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _make_unrelated_image(seed: int) -> bytes:
    """Generate an image visually unrelated to any other test image."""
    return _make_image(320, 240, seed=seed)


@pytest.fixture(scope="module")
def client():
    from app.main import app
    from app.services.index import reset_index_singleton
    reset_index_singleton()
    with TestClient(app) as c:
        yield c


def _upload(client: TestClient, data: bytes, filename: str, case_id: str | None = None):
    """Upload a file and return the response JSON."""
    form = {"file": (filename, data, "image/jpeg")}
    payload = {}
    if case_id:
        payload["case_id"] = case_id
    resp = client.post("/api/cases/upload", files=form, data=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


class TestProvenanceConsistencyNearDuplicate:
    """A = original, B = near-duplicate -> trace B -> candidates >= 1."""

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, client):
        """Upload A and B into the same case."""
        original = _make_image(480, 360, seed=7777)
        variant = _make_near_duplicate(original, scale=0.8, quality=55)

        # Upload A
        resp_a = _upload(client, original, "provenance-original.jpg")
        case_id = resp_a["case"]["case_id"]

        # Upload B into the same case
        resp_b = _upload(client, variant, "provenance-variant.jpg", case_id=case_id)

        self.__class__._case_id = case_id
        self.__class__._evidence_a = resp_a["evidence"]
        self.__class__._evidence_b = resp_b["evidence"]
        self.__class__._client = client

    def test_both_evidence_are_indexed(self):
        assert self._evidence_a.get("indexed") is True, "Evidence A should be indexed at upload"
        assert self._evidence_b.get("indexed") is True, "Evidence B should be indexed at upload"

    def test_both_have_perceptual_hashes(self):
        assert self._evidence_a.get("phash"), "Evidence A should have a pHash"
        assert self._evidence_b.get("phash"), "Evidence B should have a pHash"

    def test_match_search_finds_cross_candidates(self):
        """POST /matches should find B when searching A and A when searching B."""
        resp = self._client.post(f"/api/cases/{self._case_id}/matches")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # total_candidates must be > 0 when real near-duplicates exist
        assert data["total_candidates"] > 0, (
            f"Expected total_candidates > 0 for near-duplicate pair, got {data['total_candidates']}. "
            f"Queries: {data.get('queries')}"
        )

        # Each query should find the other as a candidate
        for query in data["queries"]:
            eid = query["evidence_id"]
            candidates = query["candidates"]
            if eid == self._evidence_a["evidence_id"]:
                candidate_ids = {c["evidence_id"] for c in candidates}
                assert self._evidence_b["evidence_id"] in candidate_ids, (
                    f"Evidence A's search should find B as candidate. "
                    f"Got candidates: {candidate_ids}"
                )
            elif eid == self._evidence_b["evidence_id"]:
                candidate_ids = {c["evidence_id"] for c in candidates}
                assert self._evidence_a["evidence_id"] in candidate_ids, (
                    f"Evidence B's search should find A as candidate. "
                    f"Got candidates: {candidate_ids}"
                )

    def test_propagation_is_consistent_with_matches(self):
        """Propagation graph node count must be >= match candidate count + case evidence."""
        prop_resp = self._client.get(f"/api/cases/{self._case_id}/propagation")
        assert prop_resp.status_code == 200, prop_resp.text
        prop = prop_resp.json()

        match_resp = self._client.post(f"/api/cases/{self._case_id}/matches")
        matches = match_resp.json()

        # Propagation should have at least 2 nodes (A and B)
        assert prop["instance_count"] >= 2, (
            f"Expected at least 2 instances, got {prop['instance_count']}"
        )

        # If propagation finds matched candidates, match search should too
        if prop["matched_candidate_count"] > 0:
            assert matches["total_candidates"] > 0, (
                "Propagation reports matched candidates but match search reports 0 -- "
                "this is the inconsistency that must not happen."
            )

    def test_earliest_known_instance_found(self):
        """With 2 instances, an earliest known instance should be identified."""
        prop_resp = self._client.get(f"/api/cases/{self._case_id}/propagation")
        prop = prop_resp.json()

        assert prop["origin"] is not None, "Expected an earliest known instance"
        assert prop["origin"]["label"] == "earliest known instance in the indexed evidence corpus"
        assert prop["origin"]["caveat"], "Origin should carry a caveat"

    def test_analysis_matches_field_consistent(self):
        """The full analysis response's matches.total_candidates must match."""
        resp = self._client.post(f"/api/cases/{self._case_id}/analyse")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # The matches field in the analysis should be consistent
        assert data["matches"]["total_candidates"] > 0, (
            f"Analysis matches.total_candidates should be > 0 for near-duplicate pair, "
            f"got {data['matches']['total_candidates']}"
        )

        # The propagation section should also be consistent
        prop = data.get("propagation", {})
        if prop.get("matched_candidate_count", 0) > 0:
            assert data["matches"]["total_candidates"] > 0, (
                "Analysis propagation reports matched candidates but matches reports 0"
            )


class TestProvenanceConsistencyUnrelated:
    """Unrelated file -> 0 candidates within the near-duplicate threshold."""

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, client):
        unrelated = _make_unrelated_image(seed=9999)
        resp = _upload(client, unrelated, "unrelated-image.jpg")
        self.__class__._case_id = resp["case"]["case_id"]
        self.__class__._client = client

    def test_unrelated_returns_zero_or_only_distant_candidates(self):
        resp = self._client.post(f"/api/cases/{self._case_id}/matches")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # An unrelated image should have 0 strong candidates.
        for query in data.get("queries", []):
            strong = query.get("strong_candidates", 0)
            assert strong == 0, (
                f"Unrelated image should have 0 strong candidates, got {strong}"
            )


class TestProvenanceConsistencySingleEvidence:
    """Single evidence item -> 0 candidates (query is excluded from its own results)."""

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, client):
        solo = _make_image(400, 300, seed=1234)
        resp = _upload(client, solo, "solo-evidence.jpg")
        self.__class__._case_id = resp["case"]["case_id"]
        self.__class__._client = client

    def test_single_evidence_no_self_match(self):
        """A single evidence item must not match itself."""
        resp = self._client.post(f"/api/cases/{self._case_id}/matches")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # The query's own evidence should never appear as its own candidate
        for query in data.get("queries", []):
            candidate_ids = {c["evidence_id"] for c in query.get("candidates", [])}
            assert query["evidence_id"] not in candidate_ids, (
                "Evidence must not appear as its own near-duplicate candidate"
            )
