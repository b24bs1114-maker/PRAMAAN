"""Forensic report endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.api.deps import CaseDep, DbDep, SettingsDep
from app.schemas.api import ReportLibraryResponse, ReportListResponse, ReportResponse
from app.services import report as report_service

logger = logging.getLogger("pramaan.api.reports")

router = APIRouter(prefix="/api/cases", tags=["reports"])

#: Reports across every case. Separate from ``router`` only because it hangs off
#: ``/api/reports`` rather than ``/api/cases/{case_id}``; the rows are identical.
library_router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post(
    "/{case_id}/report",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate the forensic PDF report for a case",
)
def generate_report(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    examiner: Annotated[
        str | None,
        Body(embed=True, description="Examiner name to print on the report."),
    ] = None,
    refresh: bool = Query(
        False, description="Re-run every analysis stage before reporting."
    ),
) -> ReportResponse:
    """Run the case to completion if needed, then render and hash the report.

    The PDF records the evidence and its SHA-256, every signal's score, weight and
    contribution, the fused verdict, near-duplicate candidates, the earliest known
    instance in the indexed corpus, the propagation timeline, the methodology and
    model versions used, the audit trail with the chain head hash, the limitations
    that bound every figure, and an examiner sign-off block.

    The PDF's own SHA-256 is returned here and recorded in the audit chain: a
    document cannot contain its own digest.
    """
    result = report_service.generate(
        db,
        case=case,
        settings=settings,
        actor="api",
        examiner=examiner,
        refresh=refresh,
    )
    return ReportResponse(**result)


@router.get(
    "/{case_id}/reports",
    response_model=ReportListResponse,
    summary="List reports generated for a case",
)
def list_reports(case: CaseDep, db: DbDep) -> ReportListResponse:
    """Every report generated for this case, newest first, with its hash."""
    reports = report_service.list_reports(db, case.id)
    return ReportListResponse(case_id=case.id, count=len(reports), reports=reports)


@router.get(
    "/{case_id}/reports/{report_id}",
    response_class=FileResponse,
    summary="Download a generated report PDF",
)
def download_report(
    case: CaseDep, db: DbDep, settings: SettingsDep, report_id: str
) -> FileResponse:
    """Return the stored PDF bytes unchanged, so the recorded SHA-256 still holds."""
    found = report_service.report_file(
        db, case_id=case.id, report_id=report_id, settings=settings
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found for case {case.id}.",
        )
    row, path = found
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=row.filename,
        headers={"X-PRAMAAN-Report-SHA256": row.sha256},
    )


@library_router.get(
    "",
    response_model=ReportLibraryResponse,
    summary="List generated reports across every case",
)
def list_report_library(
    db: DbDep,
    case_id: str | None = Query(None, description="Filter to one case."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ReportLibraryResponse:
    """Every report this deployment has generated, newest first.

    Each row carries the PDF's own SHA-256 and the audit head hash that was
    current when it was rendered, which together are what let a reader confirm a
    document is the one the chain records. No report is re-rendered here, so a
    hash listed is the hash of the bytes on disk.

    Reports are generated per case, so this list is a read across cases -- there
    is no cross-case report object.
    """
    total = report_service.count_reports(db, case_id)
    reports = report_service.list_reports(db, case_id, limit=limit, offset=offset)
    notes = [
        "Each row's sha256 is the digest of the stored PDF; the report cannot "
        "contain its own digest, so it is recorded in the audit chain instead.",
    ]
    if not total:
        notes.append(
            "No reports have been generated on this deployment yet."
            if case_id is None
            else "No reports have been generated for this case yet."
        )
    return ReportLibraryResponse(
        total=total,
        count=len(reports),
        offset=offset,
        truncated=offset + len(reports) < total,
        reports=reports,
        renderer=report_service.renderer_status(),
        document_status=report_service.DOCUMENT_STATUS,
        notes=notes,
    )
