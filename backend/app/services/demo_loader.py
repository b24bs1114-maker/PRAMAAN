"""Synthetic demo data loader (developer tool -- not a runtime code path).

Populates a PRAMAAN store with the three checked-in synthetic demo images by
putting them through the *normal* ingestion pipeline, so a developer or reviewer
has something to click on without waiting for real evidence.

Two things this deliberately does not do:

* **It fabricates no forensic values.** The SHA-256, the perceptual hash, the
  stored bytes, the case number and the audit rows are all produced by
  ``ingestion.create_case`` / ``ingestion.ingest_stream``, exactly as they are
  for an uploaded file. No verdict, score, confidence, provenance or platform is
  written: a demo case arrives *un-analysed*, and analysing it later yields
  whatever the installed detectors actually produce -- including
  ``INSUFFICIENT_EVIDENCE`` when none are installed.

* **It invents no people and no triage.** An earlier version of this module
  wrote examiner names ("Officer Analyst - Cyber Forensics") that nobody holds,
  and a ``priority`` derived from whether the title happened to contain the word
  "Market". Both read, in the UI and in an exported audit trail, as facts about a
  real investigation. The actor is now the tool's own name, and priority is left
  at the model default that ``ingestion.create_case`` documents as meaning "not
  triaged yet".

Every case created here is labelled ``SYNTHETIC DEMO DATA`` in its title and its
description -- and therefore in the ``CASE_CREATED`` audit details, which record
the title -- so it cannot be mistaken for seized evidence in a screenshot, an
export or a report.

Nothing imports this module: it is wired into no route and no startup hook. Run
it explicitly against a configured store:

    python -m app.services.demo_loader
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import get_session_factory
from app.services import ingestion

logger = logging.getLogger("pramaan.demo_loader")

#: The label that must appear on anything this module writes.
SYNTHETIC_LABEL = "SYNTHETIC DEMO DATA"

#: Actor recorded in the audit chain and stored as the case examiner. Named so
#: that it cannot be read as a person or a badge number.
DEMO_ACTOR = "demo_loader (synthetic fixture, not an examiner)"

DEMO_IMAGES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "demo_images"

#: ``subject`` describes what the generated image *depicts*. It is not a claim
#: about where the image came from, because it came from this repository.
DEMO_SPEC: list[dict[str, str]] = [
    {"filename": "demo_market_screen.png", "subject": "financial market screenshot"},
    {"filename": "demo_news_screenshot.png", "subject": "press release screenshot"},
    {"filename": "demo_social_media_post.png", "subject": "social media post"},
]


def _title(spec: dict[str, str]) -> str:
    return f"{SYNTHETIC_LABEL} - {spec['subject'][:1].upper()}{spec['subject'][1:]}"


def _description(spec: dict[str, str]) -> str:
    return (
        f"{SYNTHETIC_LABEL}. Generated fixture {spec['filename']}, depicting a "
        f"{spec['subject']}, loaded from tests/fixtures/demo_images by "
        "app.services.demo_loader. This is not seized evidence: it has no chain "
        "of custody before this ingest, no known provenance and no verdict. The "
        "hashes and audit rows attached to it are real computations over these "
        "bytes."
    )


def ingest_demo_data(db: Session, settings: Settings) -> list[dict[str, Any]]:
    """Ingest the synthetic demo images through the normal ingestion pipeline.

    Returns one record per file that was actually ingested. A fixture missing
    from disk is skipped with a warning and omitted from the result -- there is
    no stand-in for a file that is not there, and a shorter list is the honest
    report of what happened.
    """
    results: list[dict[str, Any]] = []
    for spec in DEMO_SPEC:
        file_path = DEMO_IMAGES_DIR / spec["filename"]
        if not file_path.is_file():
            logger.warning("Synthetic demo image not found, skipping: %s", file_path)
            continue

        # create_case mints the real PRAMAAN-YYYYMMDD-NNNN case number and writes
        # the CASE_CREATED row itself, so a demo case is shaped exactly like one
        # opened through the API. Only its contents say it is synthetic.
        case = ingestion.create_case(
            db,
            title=_title(spec),
            description=_description(spec),
            examiner=DEMO_ACTOR,
            actor=DEMO_ACTOR,
        )

        with file_path.open("rb") as handle:
            result = ingestion.ingest_stream(
                db,
                stream=handle,
                filename=spec["filename"],
                settings=settings,
                case=case,
                declared_mime="image/png",
                actor=DEMO_ACTOR,
            )
        evidence = result.evidence

        results.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "evidence_id": evidence.id,
                "filename": spec["filename"],
                "sha256": evidence.sha256,
                "synthetic": True,
            }
        )
        logger.info(
            "Ingested %s image %s into case %s", SYNTHETIC_LABEL, spec["filename"], case.id
        )

    return results


if __name__ == "__main__":  # pragma: no cover
    settings = get_settings()
    factory = get_session_factory(settings)
    with factory() as db:
        res = ingest_demo_data(db, settings)
        db.commit()

    print(f"Ingested {len(res)} of {len(DEMO_SPEC)} {SYNTHETIC_LABEL} item(s):")
    for item in res:
        # Full digest, not a truncated one: a shortened hash in tool output is
        # the kind of thing that ends up on a slide as if it were the hash.
        print(f"  - {item['case_number']}  {item['filename']}  SHA-256 {item['sha256']}")
    if len(res) != len(DEMO_SPEC):
        print(f"  {len(DEMO_SPEC) - len(res)} fixture(s) missing from {DEMO_IMAGES_DIR}")
