#!/usr/bin/env python3
"""Corpus ingestion and index build (TASK 6).

Reads ``corpus/manifest.json``, ingests every listed image as a corpus evidence
record (SHA-256, perceptual hashes, lineage fields, audit entries), then rebuilds
the perceptual index from the database.

    .venv/bin/python scripts/build_index.py
    .venv/bin/python scripts/build_index.py --reset          # drop corpus rows first
    .venv/bin/python scripts/build_index.py --limit 20       # partial build
    .venv/bin/python scripts/build_index.py --rebuild-only   # index from existing rows

Corpus items keep the manifest's identifiers so ``parent_id``/``source_id``
lineage stays intact, and are stored with ``is_synthetic`` set when the manifest
says so -- reports must never present synthetic demo data as real evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import ROLE_CORPUS, Evidence, init_db, session_scope  # noqa: E402
from app.services import indexing, ingestion  # noqa: E402
from app.services.storage import StorageError, absolute_path  # noqa: E402


def load_manifest(corpus_dir: Path) -> dict:
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"No manifest at {manifest_path}. Generate one first:\n"
            "  .venv/bin/python scripts/generate_corpus.py"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the corpus and build the index")
    parser.add_argument("--corpus", type=Path, default=None, help="Corpus directory")
    parser.add_argument("--limit", type=int, default=None, help="Ingest at most N items")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing corpus evidence rows (and their files) first.",
    )
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Skip ingestion; rebuild the index from rows already in the database.",
    )
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    init_db(settings)
    corpus_dir = args.corpus or settings.corpus_dir

    ingested = skipped = failed = 0

    with session_scope() as session:
        if args.reset:
            existing = list(
                session.execute(
                    select(Evidence).where(Evidence.role == ROLE_CORPUS)
                ).scalars()
            )
            for row in existing:
                try:
                    absolute_path(row.stored_path, settings).unlink(missing_ok=True)
                except (StorageError, OSError):
                    pass
                session.delete(row)
            session.flush()
            print(f"reset: removed {len(existing)} corpus evidence rows")

        if not args.rebuild_only:
            manifest = load_manifest(corpus_dir)
            items = manifest.get("items", [])
            if args.limit:
                items = items[: args.limit]
            print(
                f"manifest: {manifest.get('dataset', 'unknown dataset')} -- "
                f"{len(items)} item(s) to ingest"
            )

            for item in items:
                evidence_id = item["evidence_id"]
                image_path = corpus_dir / item["filename"]
                if session.get(Evidence, evidence_id) is not None:
                    skipped += 1
                    continue
                if not image_path.is_file():
                    print(f"  missing file, skipped: {image_path}", file=sys.stderr)
                    failed += 1
                    continue
                try:
                    with open(image_path, "rb") as handle:
                        ingestion.ingest_stream(
                            session,
                            stream=handle,
                            filename=image_path.name,
                            settings=settings,
                            case=None,
                            role=ROLE_CORPUS,
                            actor="build_index.py",
                            provenance={
                                "source_id": item.get("source_id"),
                                "parent_id": item.get("parent_id"),
                                "generation": item.get("generation"),
                                "platform": item.get("platform"),
                                "observed_at": item.get("timestamp"),
                                "transformation": item.get("transformation"),
                            },
                            is_synthetic=bool(item.get("synthetic", True)),
                            evidence_id=evidence_id,
                        )
                    ingested += 1
                except StorageError as exc:
                    print(f"  rejected {image_path.name}: {exc}", file=sys.stderr)
                    failed += 1
                if ingested and ingested % 25 == 0:
                    print(f"  ingested {ingested} ...")

        result = indexing.rebuild(session, settings=settings, actor="build_index.py")

    print()
    print("corpus ingestion")
    print(f"  ingested : {ingested}")
    print(f"  skipped  : {skipped} (already present)")
    print(f"  failed   : {failed}")
    print("index")
    print(f"  backend       : {result['backend']}")
    print(f"  indexed_count : {result['indexed_count']}")
    if "dinov2_indexed_count" in result:
        print(f"  dinov2_count  : {result['dinov2_indexed_count']}")
    print(f"  index_version : {result['index_version']}")
    print(f"  path          : {result['index_path']}")
    if result.get("notes"):
        print(f"  note          : {result['notes']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
