#!/usr/bin/env python3
"""PRAMAAN Provenance Demo Dataset Seeder.

Creates a clearly labelled, real end-to-end demo dataset with legitimate related
evidence items:
  A = Original source image
  B = Resized + recompressed derivative
  C = Cropped + recompressed derivative
  D = Format-converted variant (PNG)
  E = Unrelated control image (disjoint visual features)

Properties:
- Uses real ingestion & upload endpoints (no direct DB insertions, no fake hashes).
- Idempotent: safely purges previously generated demo records before re-seeding.
- Synchronizes pHash and DINOv2 indices.
- Validates cross-case retrieval, candidate exclusion (no self-match), and
  honest origin selection: "EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS".
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

# Add backend to sys.path so app imports work seamlessly
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.models import Case, Evidence, get_session_factory  # noqa: E402


def generate_source_image_a() -> bytes:
    """Generate source image A (original asset)."""
    w, h = 640, 480
    img = Image.new("RGB", (w, h), color=(30, 40, 60))
    draw = ImageDraw.Draw(img)

    # Sky gradient
    for y in range(h):
        r = int(20 + 120 * (y / h))
        g = int(35 + 80 * (y / h))
        b = int(70 + 100 * (1.0 - y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Horizon landscape
    draw.polygon(
        [(0, 300), (180, 220), (320, 270), (480, 210), (640, 290), (640, 480), (0, 480)],
        fill=(45, 90, 55),
    )
    draw.polygon(
        [(80, 340), (250, 260), (420, 320), (640, 250), (640, 480), (0, 480)],
        fill=(30, 65, 40),
    )

    # Central architectural structure
    draw.rectangle([260, 180, 380, 360], fill=(220, 210, 195), outline=(120, 110, 100), width=3)
    draw.polygon([(240, 180), (320, 110), (400, 180)], fill=(180, 60, 45), outline=(100, 30, 20), width=2)
    # Windows
    for wy in (210, 260, 310):
        for wx in (280, 330):
            draw.rectangle([wx, wy, wx + 30, wy + 35], fill=(80, 120, 160), outline=(50, 50, 50), width=1)

    # Sun / light source
    draw.ellipse([80, 60, 160, 140], fill=(255, 220, 90), outline=(255, 180, 40), width=3)

    # Water reflection
    draw.rectangle([0, 380, 640, 480], fill=(40, 70, 110))
    for ry in range(390, 470, 12):
        draw.line([(60, ry), (220, ry)], fill=(180, 200, 220, 100), width=2)

    # Distinctive banner
    draw.text((20, 20), "PRAMAAN PROVENANCE BENCHMARK ASSET - DEMO SOURCE A", fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def generate_derivative_b(data_a: bytes) -> bytes:
    """Generate derivative B: resized (75% scale) + recompressed (quality 55)."""
    img = Image.open(io.BytesIO(data_a))
    resized = img.resize((480, 360), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=55)
    return buf.getvalue()


def generate_derivative_c(data_a: bytes) -> bytes:
    """Generate derivative C: cropped (85% center crop) + recompressed (quality 65)."""
    img = Image.open(io.BytesIO(data_a))
    w, h = img.size
    crop_box = (int(w * 0.08), int(h * 0.08), int(w * 0.92), int(h * 0.92))
    cropped = img.crop(crop_box).resize((512, 384), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=65)
    return buf.getvalue()


def generate_derivative_d(data_a: bytes) -> bytes:
    """Generate derivative D: format converted to lossless PNG."""
    img = Image.open(io.BytesIO(data_a))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_unrelated_image_e() -> bytes:
    """Generate unrelated distractor E: neon diagonal stripes with disjoint visual features."""
    w, h = 640, 480
    img = Image.new("RGB", (w, h), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    for i in range(-h, w, 40):
        draw.line([(i, 0), (i + h, h)], fill=(200, 20, 160), width=16)
        draw.line([(i + 20, 0), (i + 20 + h, h)], fill=(20, 220, 240), width=16)
    draw.ellipse([200, 140, 440, 340], fill=(30, 30, 30), outline=(255, 255, 255), width=8)
    draw.text((20, 20), "UNRELATED DISTRACTOR - CONTROL EVIDENCE E", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def purge_existing_demo_cases(client: TestClient) -> int:
    """Safely find and delete previously seeded provenance demo cases."""
    session = get_session_factory()()
    try:
        cases = (
            session.query(Case)
            .filter(
                (Case.title.like("[DEMO PROVENANCE]%"))
                | (Case.title.like("%Provenance Demo%"))
                | (Case.description.like("%SYNTHETIC DEMO DATA%"))
            )
            .all()
        )
        case_ids = [c.id for c in cases]
    finally:
        session.close()

    deleted = 0
    for cid in case_ids:
        resp = client.delete(f"/api/cases/{cid}")
        if resp.status_code == 200:
            deleted += 1
    return deleted


def seed_provenance_demo() -> dict[str, Any]:
    print("======================================================================")
    print("PRAMAAN PROVENANCE DEMO DATASET SEEDER")
    print("======================================================================")

    with TestClient(app) as client:
        # 1. Clean up existing demo records idempotently
        print("\n[1/6] Purging any existing demo cases...")
        purged = purge_existing_demo_cases(client)
        print(f"  Purged {purged} existing demo cases.")

        # 2. Generate media assets
        print("\n[2/6] Generating realistic media assets and derivatives...")
        data_a = generate_source_image_a()
        data_b = generate_derivative_b(data_a)
        data_c = generate_derivative_c(data_a)
        data_d = generate_derivative_d(data_a)
        data_e = generate_unrelated_image_e()
        print("  Generated Assets: A (Original), B (Resized), C (Cropped), D (PNG), E (Unrelated).")

        # 3. Ingest through standard API endpoint
        print("\n[3/6] Ingesting cases via real upload pipeline (POST /api/cases/upload)...")
        manifest: dict[str, dict[str, Any]] = {}

        items = [
            (
                "A",
                "demo_evidence_A_original.jpg",
                data_a,
                "image/jpeg",
                "[DEMO PROVENANCE] Case A - Original Media Source",
                "SYNTHETIC DEMO DATA / TEST EVIDENCE - Earliest indexed reference asset",
                "2026-03-01T08:00:00Z",
                "original_capture",
            ),
            (
                "B",
                "demo_evidence_B_resized_recompressed.jpg",
                data_b,
                "image/jpeg",
                "[DEMO PROVENANCE] Case B - Resized & Recompressed Derivative",
                "SYNTHETIC DEMO DATA / TEST EVIDENCE - Derivative scaled to 75% and recompressed at Q55",
                "2026-03-02T12:00:00Z",
                "resized_recompressed",
            ),
            (
                "C",
                "demo_evidence_C_cropped_recompressed.jpg",
                data_c,
                "image/jpeg",
                "[DEMO PROVENANCE] Case C - Cropped Variant Investigation",
                "SYNTHETIC DEMO DATA / TEST EVIDENCE - Derivative cropped 85% center and recompressed at Q65",
                "2026-03-03T15:30:00Z",
                "cropped_recompressed",
            ),
            (
                "D",
                "demo_evidence_D_format_converted.png",
                data_d,
                "image/png",
                "[DEMO PROVENANCE] Case D - Format-Converted Derivative (PNG)",
                "SYNTHETIC DEMO DATA / TEST EVIDENCE - Lossless PNG conversion of original asset",
                "2026-03-04T09:00:00Z",
                "format_converted",
            ),
            (
                "E",
                "demo_evidence_E_unrelated.jpg",
                data_e,
                "image/jpeg",
                "[DEMO PROVENANCE] Case E - Unrelated Distractor Control",
                "SYNTHETIC DEMO DATA / TEST EVIDENCE - Completely unrelated control image with disjoint features",
                "2026-03-04T10:00:00Z",
                "unrelated",
            ),
        ]

        for code, filename, data, mime, title, desc, obs_at, trans in items:
            res = client.post(
                "/api/cases/upload",
                files={"file": (filename, data, mime)},
                data={
                    "title": title,
                    "description": desc,
                    "observed_at": obs_at,
                    "platform": "internal_corpus",
                    "transformation": trans,
                    "is_synthetic": "true",
                },
            )
            assert res.status_code == 201, f"Failed to upload asset {code}: {res.text}"
            body = res.json()
            case_id = body["case"]["case_id"]
            ev_id = body["evidence"]["evidence_id"]
            manifest[code] = {
                "case_id": case_id,
                "evidence_id": ev_id,
                "filename": filename,
                "title": title,
                "observed_at": obs_at,
                "transformation": trans,
            }
            print(f"  Asset {code}: Case ID={case_id} | Evidence ID={ev_id} ({filename})")

        # 4. Rebuild indexes
        print("\n[4/6] Rebuilding pHash and DINOv2 indexes (POST /api/index/rebuild)...")
        rebuild_resp = client.post("/api/index/rebuild")
        assert rebuild_resp.status_code == 200, f"Index rebuild failed: {rebuild_resp.text}"
        idx_status = client.get("/api/index/status").json()
        print(f"  Indexes synchronized: {idx_status['indexed_count']} items indexed.")

        # 5. Pre-warm matches and propagation across demo cases
        print("\n[5/6] Performing matches search & propagation reconstruction...")
        for code, info in manifest.items():
            cid = info["case_id"]
            client.post(f"/api/cases/{cid}/matches")
            client.get(f"/api/cases/{cid}/propagation?refresh=true")

        # 6. Verification
        print("\n[6/6] Verifying Cross-Case Matching, Ranking & Origin Selection...")
        case_b_id = manifest["B"]["case_id"]
        ev_b_id = manifest["B"]["evidence_id"]
        ev_a_id = manifest["A"]["evidence_id"]
        ev_c_id = manifest["C"]["evidence_id"]
        ev_d_id = manifest["D"]["evidence_id"]
        ev_e_id = manifest["E"]["evidence_id"]

        # Call matches for Case B
        matches_b = client.post(f"/api/cases/{case_b_id}/matches").json()
        q_b = next(q for q in matches_b["queries"] if q["evidence_id"] == ev_b_id)
        candidate_ids = [c["evidence_id"] for c in q_b["candidates"]]

        print(f"\n  --- RESULTS FOR CASE B ({manifest['B']['filename']}) ---")
        print(f"  Total Indexed in Corpus : {q_b.get('indexed_count', idx_status['indexed_count'])}")
        print(f"  Candidate Count         : {len(q_b['candidates'])}")
        print(f"  Retrieved Candidate IDs : {candidate_ids}")

        # Verification 1: B tracing finds A
        assert ev_a_id in candidate_ids, "Case B must find Original A as candidate!"
        cand_a = next(c for c in q_b["candidates"] if c["evidence_id"] == ev_a_id)
        print(f"  -> Candidate A: dist={cand_a['distance']} | similarity={cand_a['similarity']} | basis={cand_a.get('match_basis')}")

        # Verification 2: C and D are also candidates
        assert ev_c_id in candidate_ids, "Case B must find Cropped C as candidate!"
        assert ev_d_id in candidate_ids, "Case B must find Format-Converted D as candidate!"

        # Verification 3: No self-match
        assert ev_b_id not in candidate_ids, "Evidence B must NOT match itself!"

        # Verification 4: Unrelated E is excluded
        assert ev_e_id not in candidate_ids, "Unrelated Evidence E must NOT be a candidate!"

        # Call propagation for Case B
        prop_b = client.get(f"/api/cases/{case_b_id}/propagation?refresh=true").json()
        origin_b = prop_b["origin"]

        assert origin_b is not None, "Propagation must resolve an earliest-known origin!"
        assert origin_b["evidence_id"] == ev_a_id, f"Earliest known instance must be A! Got {origin_b['evidence_id']}"
        assert origin_b["label"] == "earliest known instance in the indexed evidence corpus"
        assert origin_b["is_absolute_origin"] is False

        timeline_nodes = [t["evidence_id"] for t in prop_b.get("timeline", [])]
        graph_nodes = [n["evidence_id"] for n in prop_b.get("graph", {}).get("nodes", [])]

        print(f"  Earliest Known Evidence : {origin_b['evidence_id']} ({origin_b['filename']})")
        print(f"  Origin Timestamp        : {origin_b['timestamp']} (Source: {origin_b['timestamp_source']})")
        print(f"  Timeline Node Count     : {len(timeline_nodes)}")
        print(f"  Graph Node Count        : {len(graph_nodes)}")
        print(f"  Graph Edge Count        : {len(prop_b.get('graph', {}).get('edges', []))}")

        assert len(timeline_nodes) >= 2, "Timeline must contain multiple nodes!"
        assert len(graph_nodes) >= 2, "Graph must contain multiple nodes!"

        print("\n======================================================================")
        print("ALL PROVENANCE DEMO DATASET VERIFICATIONS PASSED SUCCESSFULLY!")
        print("======================================================================")

        return {
            "manifest": manifest,
            "verification": {
                "case_b_id": case_b_id,
                "evidence_b_id": ev_b_id,
                "candidate_count": len(q_b["candidates"]),
                "candidate_ids": candidate_ids,
                "earliest_evidence_id": origin_b["evidence_id"],
                "origin_label": origin_b["label"],
                "graph_nodes": len(graph_nodes),
                "graph_edges": len(prop_b.get("graph", {}).get("edges", [])),
                "timeline_nodes": len(timeline_nodes),
            },
        }


if __name__ == "__main__":
    seed_provenance_demo()
