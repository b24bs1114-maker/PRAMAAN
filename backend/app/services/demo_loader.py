"""Demo data loader for PRAMAAN.

Provides a developer/demo mechanism to quickly populate PRAMAAN with the 3
standard demo evidence images via the normal ingestion pipeline.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Case, get_session_factory
from app.services import audit, ingestion
from app.utils.timeutil import utcnow

logger = logging.getLogger("pramaan.demo_loader")

DEMO_IMAGES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo_images"

DEMO_SPEC = [
    {
        "filename": "demo_market_screen.png",
        "title": "Financial Market Screenshot Evidence",
        "examiner": "Officer Analyst - Market Surveillance",
    },
    {
        "filename": "demo_news_screenshot.png",
        "title": "Press Release Broadcast Sample",
        "examiner": "Officer Analyst - Media Verification",
    },
    {
        "filename": "demo_social_media_post.png",
        "title": "Social Media Viral Broadcast Evidence",
        "examiner": "Officer Analyst - Cyber Forensics",
    },
]


def ingest_demo_data(db: Session, settings: Settings) -> list[dict[str, Any]]:
    """Ingest the 3 standard demo images through the normal ingestion pipeline.

    Calculates normal SHA-256, perceptual hash, creates case and evidence
    records, and records audit trail entries without fabricating analysis
    verdicts.
    """
    results = []
    for spec in DEMO_SPEC:
        file_path = DEMO_IMAGES_DIR / spec["filename"]
        if not file_path.is_file():
            logger.warning("Demo image file not found: %s", file_path)
            continue

        case_id = str(uuid.uuid4())
        case_num = f"CASE-{utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        case = Case(
            id=case_id,
            case_number=case_num,
            title=spec["title"],
            description=f"Standard demo investigation item for {spec['filename']}",
            priority="high" if "Market" in spec["title"] else "medium",
            examiner=spec["examiner"],
        )
        db.add(case)
        db.flush()
        audit.record(
            db,
            event=audit.EVENT_CASE_CREATED,
            case_id=case.id,
            actor=spec["examiner"],
            details={"title": case.title, "priority": case.priority, "source": "demo_loader"},
        )

        with file_path.open("rb") as handle:
            result = ingestion.ingest_stream(
                db,
                stream=handle,
                filename=spec["filename"],
                settings=settings,
                case=case,
                declared_mime="image/png",
                actor=spec["examiner"],
            )
            evidence = result.evidence
        results.append({
            "case_id": case.id,
            "evidence_id": evidence.id,
            "filename": spec["filename"],
            "sha256": evidence.sha256,
        })
        logger.info("Ingested demo image %s into case %s", spec["filename"], case.id)

    return results


if __name__ == "__main__":  # pragma: no cover
    settings = get_settings()
    factory = get_session_factory(settings)
    with factory() as db:
        res = ingest_demo_data(db, settings)
        db.commit()
        print(f"Ingested {len(res)} demo evidence item(s):")
        for item in res:
            print(f"  - Case {item['case_id']}: {item['filename']} (SHA-256: {item['sha256'][:12]}...)")
