"""SQLAlchemy ORM entities for the PRAMAAN case file.

Seven tables, matching the forensic workflow:

* ``cases``            -- an investigation
* ``evidence``         -- an ingested file (case evidence or indexed corpus item)
* ``analysis_results`` -- per-evidence output of one analysis stage
* ``matches``          -- near-duplicate candidate pairs
* ``timeline_events``  -- chronological propagation events
* ``reports``          -- generated forensic PDF reports, with their own hashes
* ``audit_log``        -- hash-chained, tamper-evident action record
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.utils.timeutil import utcnow

# Evidence roles
ROLE_CASE_EVIDENCE = "case_evidence"
ROLE_CORPUS = "corpus"

# Analysis kinds
KIND_METADATA = "metadata"
KIND_DETECTOR = "detector"
KIND_PROVENANCE = "provenance"
KIND_FORENSICS = "forensics"
KIND_FUSION = "fusion"
KIND_PROPAGATION = "propagation"


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    examiner: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="open")
    priority: Mapped[str] = mapped_column(String(32), default="medium")
    complaint_reference: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class Evidence(Base):
    """One ingested media file.

    ``role`` distinguishes evidence submitted for a case from reference items in
    the indexed corpus. Corpus items carry the synthetic-provenance fields
    (``source_id``, ``parent_id``, ``generation``, ``platform``) that make
    propagation reconstruction possible.
    """

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), default=ROLE_CASE_EVIDENCE, index=True)

    # --- File identity ---
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(16))       # image | video | audio
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    # --- Image properties ---
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    image_format: Mapped[str | None] = mapped_column(String(32))

    # --- Perceptual hashes (hex strings, hash_bits wide) ---
    phash: Mapped[str | None] = mapped_column(String(64), index=True)
    dhash: Mapped[str | None] = mapped_column(String(64), index=True)
    ahash: Mapped[str | None] = mapped_column(String(64))

    # --- Synthetic corpus provenance (never asserted for uploaded evidence) ---
    source_id: Mapped[str | None] = mapped_column(String(64), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), index=True)
    generation: Mapped[int | None] = mapped_column(Integer)
    platform: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    transformation: Mapped[str | None] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    case: Mapped[Case | None] = relationship(back_populates="evidence")
    analyses: Mapped[list["AnalysisResult"]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The same bytes may appear in different cases, but only once per case.
        UniqueConstraint("case_id", "sha256", name="uq_evidence_case_sha256"),
        Index("ix_evidence_role_indexed", "role", "indexed"),
    )


class AnalysisResult(Base):
    """Output of one analysis stage for one evidence item."""

    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="OK")
    score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    evidence: Mapped[Evidence] = relationship(back_populates="analyses")

    __table_args__ = (Index("ix_analysis_evidence_kind", "evidence_id", "kind"),)


class Match(Base):
    """A near-duplicate *candidate* pair. Never an assertion of identity."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    query_evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    candidate_evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evidence.id", ondelete="CASCADE"), index=True
    )
    phash_distance: Mapped[int | None] = mapped_column(Integer)
    dhash_distance: Mapped[int | None] = mapped_column(Integer)
    distance: Mapped[int] = mapped_column(Integer, index=True)
    similarity: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(32), default="candidate")
    rank: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(64), default="phash+dhash/hamming")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "query_evidence_id", "candidate_evidence_id", name="uq_match_pair"
        ),
    )


class TimelineEvent(Base):
    """A chronological propagation event derived from indexed evidence."""

    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evidence.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    platform: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Report(Base):
    """A generated forensic report.

    The PDF is written to the reports directory and hashed, so the document that
    left the system can be matched byte for byte against what is recorded here.
    ``audit_head_hash`` anchors the report to the audit chain as it stood at
    generation time.
    """

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    generator: Mapped[str] = mapped_column(String(128))
    renderer: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int | None] = mapped_column(Integer)
    examiner: Mapped[str | None] = mapped_column(String(255))
    audit_head_hash: Mapped[str] = mapped_column(String(64))
    audit_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_report_case_created", "case_id", "created_at"),)


class AuditLog(Base):
    """Append-only, hash-chained record of every significant action.

    ``row_hash = sha256(previous_hash || canonical_json(payload))``. Any edit to
    a historical row breaks the chain and is detected by verification.
    """

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[str] = mapped_column(String(32))       # ISO-8601 UTC string
    actor: Mapped[str] = mapped_column(String(128), default="system")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), index=True)

    __table_args__ = (Index("ix_audit_case_seq", "case_id", "seq"),)
