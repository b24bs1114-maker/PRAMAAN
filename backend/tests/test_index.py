"""Perceptual index tests (TASK 6).

Covers build/load/rebuild/add/query, persistence across a simulated restart, and
the exactness of the flat search.
"""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.services.hashing import calculate_phash, hamming_distance
from app.services.index import (
    BACKEND_FAISS,
    BACKEND_NUMPY,
    FAISS_AVAILABLE,
    PerceptualIndex,
    get_index,
    hex_to_vector,
    reset_index_singleton,
)
from tests.helpers import encode, jpeg_bytes, make_image


def _upload(client: TestClient, seed: int, name: str = "indexed.jpg"):
    return client.post(
        "/api/cases/upload",
        files={"file": (name, jpeg_bytes(seed=seed), "image/jpeg")},
    ).json()


def test_status_reports_backend_and_exactness(client: TestClient) -> None:
    body = client.get("/api/index/status").json()
    assert body["exact_search"] is True
    assert body["hash_bits"] == 64
    assert body["dimensions"] == 8
    assert body["faiss_available"] is FAISS_AVAILABLE
    assert body["backend"] == (BACKEND_FAISS if FAISS_AVAILABLE else BACKEND_NUMPY)
    if not FAISS_AVAILABLE:
        assert "faiss-cpu is not installed" in body["notes"]


def test_rebuild_indexes_existing_evidence(client: TestClient) -> None:
    first = _upload(client, 301, "rebuild-a.jpg")
    second = _upload(client, 302, "rebuild-b.jpg")

    body = client.post("/api/index/rebuild").json()
    assert body["status"] == "rebuilt"
    assert body["indexed_count"] >= 2
    assert body["index_version"] >= 1

    from app.services.index import get_index as _get_index

    index = _get_index(client.app.state.settings)
    assert index.contains(first["evidence"]["evidence_id"])
    assert index.contains(second["evidence"]["evidence_id"])


def test_add_endpoint_is_idempotent(client: TestClient) -> None:
    evidence_id = _upload(client, 303, "add-once.jpg")["evidence"]["evidence_id"]

    # Evidence is already indexed at upload time, so both explicit add calls
    # return "already_indexed" — the endpoint is truly idempotent.
    first = client.post(f"/api/index/add/{evidence_id}")
    second = client.post(f"/api/index/add/{evidence_id}")
    assert first.status_code == 200
    assert first.json()["status"] == "already_indexed"
    assert first.json()["added"] == 0
    assert second.json()["status"] == "already_indexed"
    assert second.json()["added"] == 0
    assert second.json()["indexed_count"] == first.json()["indexed_count"]


def test_add_unknown_evidence_returns_404(client: TestClient) -> None:
    assert client.post("/api/index/add/does-not-exist").status_code == 404


def test_index_version_increments_on_change(client: TestClient) -> None:
    before = client.get("/api/index/status").json()["index_version"]
    evidence_id = _upload(client, 304, "version.jpg")["evidence"]["evidence_id"]
    client.post(f"/api/index/add/{evidence_id}")
    after = client.get("/api/index/status").json()
    assert after["index_version"] > before
    assert after["last_updated"].endswith("Z")


def test_index_survives_restart(client: TestClient, settings) -> None:
    """A fresh index object must read the persisted vectors from disk."""
    evidence_id = _upload(client, 305, "persist.jpg")["evidence"]["evidence_id"]
    client.post("/api/index/rebuild")
    before = client.get("/api/index/status").json()

    # Simulate a process restart: drop the singleton and construct a new index.
    reset_index_singleton()
    revived = PerceptualIndex(settings)
    revived.load()

    assert revived.count == before["indexed_count"]
    assert revived.contains(evidence_id)
    assert revived.status()["index_version"] == before["index_version"]


def test_query_returns_exact_ranked_distances(client: TestClient, settings) -> None:
    original = make_image(480, 360, seed=306)
    variant = original.resize((240, 180), Image.Resampling.LANCZOS)

    base = client.post(
        "/api/cases/upload",
        files={"file": ("q-base.jpg", encode(original, "JPEG"), "image/jpeg")},
    ).json()["evidence"]
    other = client.post(
        "/api/cases/upload",
        files={"file": ("q-other.jpg", jpeg_bytes(seed=999), "image/jpeg")},
    ).json()["evidence"]
    client.post("/api/index/rebuild")

    index = get_index(settings)
    results = index.query(calculate_phash(variant), top_k=5)
    assert results, "index returned no candidates"

    # Closest hit is the item the query image was derived from.
    assert results[0]["evidence_id"] == base["evidence_id"]
    # Distances are non-decreasing and match a direct Hamming computation.
    distances = [r["distance"] for r in results]
    assert distances == sorted(distances)
    direct = hamming_distance(calculate_phash(variant), base["phash"])
    assert results[0]["distance"] == direct

    unrelated = next(
        (r for r in results if r["evidence_id"] == other["evidence_id"]), None
    )
    if unrelated is not None:
        assert unrelated["distance"] > results[0]["distance"]


def test_query_can_exclude_ids(client: TestClient, settings) -> None:
    body = _upload(client, 307, "excluded.jpg")
    evidence_id = body["evidence"]["evidence_id"]
    client.post("/api/index/rebuild")

    index = get_index(settings)
    hits = index.query(body["evidence"]["phash"], top_k=5)
    assert any(h["evidence_id"] == evidence_id for h in hits)

    filtered = index.query(
        body["evidence"]["phash"], top_k=5, exclude={evidence_id}
    )
    assert all(h["evidence_id"] != evidence_id for h in filtered)


def test_empty_index_returns_no_candidates(settings, tmp_path) -> None:
    empty = PerceptualIndex(settings)
    empty._ids = []                                    # noqa: SLF001 - direct state
    empty._vectors = np.zeros((0, 8), dtype=np.uint8)  # noqa: SLF001
    empty._loaded = True                               # noqa: SLF001
    assert empty.query("0" * 16, top_k=5) == []


def test_hex_to_vector_rejects_malformed_input() -> None:
    assert hex_to_vector("00ff00ff00ff00ff").tolist() == [0, 255, 0, 255, 0, 255, 0, 255]
    for bad in ("", "abc", "0" * 15, "0" * 17):
        try:
            hex_to_vector(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


# --- Staleness: another process rewrote the index files ----------------------
#
# The index is a *file-backed* artefact with a process-wide in-memory copy.
# ``scripts/build_index.py`` writes those files from a separate process, and a
# second worker sharing the data directory writes them too. A server that reads
# them once at start-up then answers every later query from that snapshot
# reports "no candidates" for evidence the index can in fact match -- a silently
# wrong forensic result. These tests pin the reload behaviour.


def _isolated_settings(tmp_path, settings):
    """A Settings pointing at an empty index directory of its own."""
    return settings.model_copy(update={"data_dir": tmp_path})


def _other_process_writes(index_settings, entries: list[tuple[str, str]]) -> int:
    """Rewrite the on-disk index through a separate index object."""
    writer = PerceptualIndex(index_settings)
    writer.load()
    return writer.replace_all(entries)


def test_refresh_picks_up_an_out_of_process_rebuild(tmp_path, settings) -> None:
    isolated = _isolated_settings(tmp_path, settings)
    reader = PerceptualIndex(isolated)
    reader.load()
    assert reader.count == 0

    _other_process_writes(isolated, [("ev-a", "0" * 16), ("ev-b", "f" * 16)])

    assert reader.refresh_if_stale() is True
    assert reader.count == 2
    assert reader.contains("ev-a")
    # And the reloaded vectors are searchable, not merely counted.
    assert reader.query("0" * 16, top_k=1)[0]["evidence_id"] == "ev-a"


def test_refresh_is_a_no_op_when_nothing_changed(tmp_path, settings) -> None:
    isolated = _isolated_settings(tmp_path, settings)
    _other_process_writes(isolated, [("ev-a", "0" * 16)])
    reader = PerceptualIndex(isolated)
    reader.load()

    assert reader.refresh_if_stale() is False
    assert reader.refresh_if_stale() is False
    assert reader.count == 1


def test_own_save_does_not_look_stale_to_itself(tmp_path, settings) -> None:
    """Writing the index must not make the writer re-read its own bytes."""
    isolated = _isolated_settings(tmp_path, settings)
    index = PerceptualIndex(isolated)
    index.load()
    index.add("ev-a", "0" * 16)

    assert index.refresh_if_stale() is False
    assert index.count == 1


def test_failed_reload_keeps_the_working_index(tmp_path, settings) -> None:
    """A corrupt *replacement* must not empty an index that is already serving."""
    isolated = _isolated_settings(tmp_path, settings)
    _other_process_writes(isolated, [("ev-a", "0" * 16), ("ev-b", "f" * 16)])
    reader = PerceptualIndex(isolated)
    reader.load()
    assert reader.count == 2

    # Sidecar now disagrees with the vector file: the same failure a half-written
    # or foreign-format write would produce.
    reader.sidecar_path.write_text('{"ids": ["only-one"]}', encoding="utf-8")

    assert reader.refresh_if_stale() is False
    assert reader.count == 2
    assert reader.query("0" * 16, top_k=1)[0]["evidence_id"] == "ev-a"


def test_failed_reload_is_retried_only_when_the_bytes_change_again(
    tmp_path, settings
) -> None:
    isolated = _isolated_settings(tmp_path, settings)
    _other_process_writes(isolated, [("ev-a", "0" * 16)])
    reader = PerceptualIndex(isolated)
    reader.load()

    reader.sidecar_path.write_text("not json at all", encoding="utf-8")
    assert reader.refresh_if_stale() is False
    # Second call sees an unchanged (still broken) file and does not re-read it.
    assert reader.refresh_if_stale() is False

    # A good write is picked up, so the failure is not sticky.
    _other_process_writes(isolated, [("ev-a", "0" * 16), ("ev-c", "1" * 16)])
    assert reader.refresh_if_stale() is True
    assert reader.count == 2


def test_first_load_of_a_corrupt_index_still_starts_empty(tmp_path, settings) -> None:
    """Unchanged behaviour: a corrupt index on first touch must not raise."""
    isolated = _isolated_settings(tmp_path, settings)
    _other_process_writes(isolated, [("ev-a", "0" * 16)])
    isolated.index_dir.joinpath("index_meta.json").write_text("{", encoding="utf-8")

    fresh = PerceptualIndex(isolated)
    fresh.load()
    assert fresh.count == 0
    assert fresh.query("0" * 16, top_k=5) == []


def test_removed_index_files_do_not_empty_a_loaded_index(tmp_path, settings) -> None:
    isolated = _isolated_settings(tmp_path, settings)
    _other_process_writes(isolated, [("ev-a", "0" * 16)])
    reader = PerceptualIndex(isolated)
    reader.load()

    reader.vectors_path.unlink()
    reader.sidecar_path.unlink()

    assert reader.refresh_if_stale() is False
    assert reader.count == 1


def test_get_index_reloads_after_an_out_of_process_rebuild(tmp_path, settings) -> None:
    """The regression this fix exists for, at the accessor every caller uses."""
    isolated = _isolated_settings(tmp_path, settings)
    reset_index_singleton()
    try:
        assert get_index(isolated).count == 0
        _other_process_writes(isolated, [("ev-a", "0" * 16), ("ev-b", "f" * 16)])
        # Same accessor, same settings, no restart.
        assert get_index(isolated).count == 2
    finally:
        reset_index_singleton()
