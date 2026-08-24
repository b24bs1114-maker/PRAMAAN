"""Frontend/backend contract verification.

This script exists because the frontend and the backend are separately authored
and nothing else checks that they agree. It drives the real FastAPI application
through the whole investigation workflow, in the order the UI performs it, and
then asserts the things the UI actually depends on:

  * every route the API client calls exists in the app's own route table
  * every response carries the top-level keys the client's TypeScript types claim
  * the error paths return the statuses the UI branches on (400/404/413/422)
  * CORS headers are present for an allowed origin and absent for a foreign one,
    including on an error response

It also writes every real response to a recordings file so the frontend's own
client can be replayed against genuine payloads rather than hand-written mocks
(see frontend/tests/contract.test.ts).

A TCP socket cannot be bound in the sandbox this was developed in, so requests
go through Starlette's ASGI transport instead of uvicorn. That still exercises
the full middleware, routing, validation and error-handling stack -- what it
does not exercise is the network itself and a real browser's CORS enforcement.

Run from the backend directory with the venv active::

    python ../scripts/verify_integration.py

Exit code is 0 only if every check passed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# --- Isolation ---------------------------------------------------------------
# Set before app.config is imported: Settings is cached, so a later change would
# be ignored. A throwaway root means a verification run never touches real cases.
_ROOT = Path(tempfile.mkdtemp(prefix="pramaan-verify-"))
os.environ["PRAMAAN_ENVIRONMENT"] = "testing"
os.environ["PRAMAAN_DEBUG"] = "false"
os.environ["PRAMAAN_LOG_LEVEL"] = "WARNING"
os.environ["PRAMAAN_LOG_ACCESS"] = "false"
os.environ["PRAMAAN_DATA_DIR"] = str(_ROOT / "data")
os.environ["PRAMAAN_REPORTS_DIR"] = str(_ROOT / "reports")
os.environ["PRAMAAN_CORPUS_DIR"] = str(_ROOT / "corpus")

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.helpers import encode, jpeg_with_exif_bytes, make_image  # noqa: E402

ALLOWED_ORIGIN = "http://localhost:5173"
FOREIGN_ORIGIN = "http://evil.example.com"

# --- Result accounting -------------------------------------------------------

_checks: list[tuple[bool, str, str]] = []
_recordings: dict[str, Any] = {}


def check(ok: bool, name: str, detail: str = "") -> bool:
    _checks.append((bool(ok), name, detail))
    return bool(ok)


def record(method: str, path: str, response: Any) -> Any:
    """Store a real response so the frontend client can be replayed against it."""
    body: Any = None
    is_json = "application/json" in response.headers.get("content-type", "")
    if is_json:
        try:
            body = response.json()
        except ValueError:
            body = None
    _recordings[f"{method.upper()} {path}"] = {
        "status": response.status_code,
        "headers": {
            k: v
            for k, v in response.headers.items()
            if k.lower() in {"content-type", "x-request-id", "content-disposition"}
        },
        "json": body,
        # PDFs are recorded by length only; the harness just needs a Blob.
        "bytes_len": None if is_json else len(response.content),
    }
    return body


def keys_of(body: Any) -> list[str]:
    return sorted(body.keys()) if isinstance(body, dict) else []


def expect_keys(body: Any, required: set[str], label: str) -> None:
    """Assert a response carries the keys the client's types declare."""
    actual = set(keys_of(body))
    missing = sorted(required - actual)
    check(
        not missing,
        f"{label}: declared keys present",
        f"missing {missing}" if missing else f"{len(actual)} keys",
    )


# --- Fixtures ----------------------------------------------------------------
# A three-generation lineage in the corpus plus a further recompressed copy as
# the case file, so matches, propagation, origin and the timeline all have real
# content to report rather than empty lists.

SEED = 4242
_BASE = make_image(480, 360, seed=SEED)
CASE_BYTES = jpeg_with_exif_bytes(seed=SEED, size=(480, 360))
GEN_BYTES = [
    encode(_BASE, "JPEG", 95),
    encode(_BASE.resize((384, 288), Image.Resampling.LANCZOS), "JPEG", 70),
    encode(_BASE.resize((320, 240), Image.Resampling.LANCZOS), "JPEG", 45),
]


def seed_corpus(client: TestClient) -> list[str]:
    """Ingest a lineage straight into the searchable corpus."""
    ids: list[str] = []
    parent: str | None = None
    for gen, payload in enumerate(GEN_BYTES):
        form = {
            "source_id": "lineage-verify-1",
            "generation": str(gen),
            "platform": ["OriginalCamera", "WhatsApp", "Twitter"][gen],
            "observed_at": f"2026-0{gen + 1}-10T08:0{gen}:00Z",
            "transformation": ["none", "resize+recompress", "resize+recompress"][gen],
            "is_synthetic": "true",
        }
        if parent:
            form["parent_id"] = parent
        response = client.post(
            "/api/index/ingest",
            files={"file": (f"gen{gen}.jpg", payload, "image/jpeg")},
            data=form,
        )
        check(
            response.status_code in (200, 201),
            f"corpus ingest generation {gen}",
            f"HTTP {response.status_code}",
        )
        if response.status_code not in (200, 201):
            return ids
        evidence_id = response.json()["evidence"]["evidence_id"]
        ids.append(evidence_id)
        parent = evidence_id
    return ids


# --- Workflow ----------------------------------------------------------------


def run_workflow(client: TestClient) -> dict[str, Any]:
    """Walk the workflow in the order the UI performs it."""
    context: dict[str, Any] = {}

    # 0. System probes -- the UI calls these before anything else.
    body = record("GET", "/health", client.get("/health"))
    check(isinstance(body, dict) and "status" in body, "GET /health", str(body))

    body = record("GET", "/api/index/status", client.get("/api/index/status"))
    expect_keys(
        body,
        {
            "indexed_count",
            "last_updated",
            "index_version",
            "backend",
            "exact_search",
            "hash_bits",
            "dimensions",
            "persisted",
            "index_path",
            "faiss_available",
            "notes",
        },
        "IndexStatus",
    )

    body = record("GET", "/api/detector/status", client.get("/api/detector/status"))
    expect_keys(
        body,
        {
            "adapter",
            "model",
            "model_version",
            "available",
            "reason",
            "interface_version",
            "score_semantics",
            "configured_backend",
            "configured_model_path",
            "candidate_adapters",
            "notes",
        },
        "DetectorStatus",
    )
    context["detector_available"] = bool(body.get("available")) if body else None

    seed_corpus(client)
    client.post("/api/index/rebuild")

    # 1. UPLOAD -- creates the case, since no case_id is supplied.
    response = client.post(
        "/api/cases/upload",
        files={"file": ("complaint-photo.jpg", CASE_BYTES, "image/jpeg")},
        data={
            "title": "Verification case",
            "description": "Contract verification run",
            "examiner": "automated",
        },
    )
    body = record("POST", "/api/cases/upload", response)
    check(response.status_code == 201, "POST /api/cases/upload -> 201", f"HTTP {response.status_code}")
    expect_keys(body, {"case", "evidence", "duplicate", "warnings"}, "UploadResponse")
    if not isinstance(body, dict) or "case" not in body:
        return context
    case_id = body["case"]["case_id"]
    context["case_id"] = case_id
    context["evidence_id"] = body["evidence"]["evidence_id"]
    context["sha256"] = body["evidence"]["sha256"]
    expect_keys(
        body["case"],
        {
            "case_id",
            "case_number",
            "title",
            "description",
            "examiner",
            "status",
            "created_at",
            "updated_at",
            "evidence_count",
        },
        "CaseRecord",
    )
    expect_keys(
        body["evidence"],
        {
            "evidence_id",
            "case_id",
            "role",
            "filename",
            "media_type",
            "mime_type",
            "size_bytes",
            "sha256",
            "ingested_at",
            "width",
            "height",
            "format",
            "phash",
            "dhash",
            "ahash",
            "source_id",
            "parent_id",
            "generation",
            "platform",
            "observed_at",
            "transformation",
            "is_synthetic",
            "indexed",
        },
        "Evidence",
    )

    # Duplicate submission: 200 + duplicate:true, not a second stored copy.
    dup = client.post(
        "/api/cases/upload",
        files={"file": ("complaint-photo.jpg", CASE_BYTES, "image/jpeg")},
        data={"case_id": case_id},
    )
    check(
        dup.status_code == 200 and dup.json().get("duplicate") is True,
        "duplicate upload -> 200 duplicate:true",
        f"HTTP {dup.status_code} duplicate={dup.json().get('duplicate')}",
    )

    # 2. CASE reads.
    body = record("GET", "/api/cases", client.get("/api/cases"))
    check(
        isinstance(body, dict) and "cases" in body,
        "GET /api/cases carries 'cases'",
        f"keys={keys_of(body)}",
    )

    body = record("GET", f"/api/cases/{case_id}", client.get(f"/api/cases/{case_id}"))
    check(
        isinstance(body, dict) and body.get("case_id") == case_id,
        f"GET /api/cases/{{id}}",
        f"keys={keys_of(body)}",
    )

    body = record(
        "GET", f"/api/cases/{case_id}/evidence", client.get(f"/api/cases/{case_id}/evidence")
    )
    check(
        isinstance(body, dict) and "evidence" in body,
        "GET /evidence carries 'evidence'",
        f"keys={keys_of(body)}",
    )

    # 3. ANALYSE -- the authoritative call the whole UI hangs off.
    response = client.post(f"/api/cases/{case_id}/analyse")
    analysis = record("POST", f"/api/cases/{case_id}/analyse", response)
    check(response.status_code == 200, "POST /analyse -> 200", f"HTTP {response.status_code}")
    expect_keys(
        analysis,
        {
            "case",
            "evidence",
            "verdict",
            "signals",
            "matches",
            "origin",
            "timeline",
            "audit",
            "processing_time_ms",
            "verdicts",
            "verdict_selection",
            "verdict_evidence_id",
            "propagation",
            "detector",
            "index",
            "stages",
            "analysis_version",
            "fusion_method",
            "score_semantics",
            "caveat",
            "warnings",
            "analysed_at",
            "refreshed",
        },
        "AnalysisResponse",
    )

    if isinstance(analysis, dict):
        verify_verdict(analysis)
        verify_propagation_nesting(analysis)

    # 4. Per-panel refresh endpoints.
    body = record("POST", f"/api/cases/{case_id}/verdict", client.post(f"/api/cases/{case_id}/verdict"))
    check(
        isinstance(body, dict) and "items" in body,
        "POST /verdict carries 'items'",
        f"keys={keys_of(body)}",
    )

    body = record("POST", f"/api/cases/{case_id}/matches", client.post(f"/api/cases/{case_id}/matches"))
    expect_keys(
        body,
        {"case_id", "interpretation", "queries", "total_candidates", "thresholds"},
        "MatchesResponse",
    )
    if isinstance(body, dict) and body.get("queries"):
        query = body["queries"][0]
        expect_keys(
            query,
            {
                "evidence_id",
                "filename",
                "media_type",
                "phash",
                "dhash",
                "top_k",
                "max_distance",
                "method",
                "algorithm",
                "index_backend",
                "indexed_count",
                "index_version",
                "candidates",
                "strong_candidates",
                "notes",
            },
            "MatchQuery",
        )
        check(
            len(query["candidates"]) > 0,
            "matches: real candidates returned",
            f"{len(query['candidates'])} candidates",
        )
        if query["candidates"]:
            expect_keys(
                query["candidates"][0],
                {
                    "evidence_id",
                    "distance",
                    "similarity",
                    "phash_distance",
                    "dhash_distance",
                    "source_id",
                    "parent_id",
                    "generation",
                    "timestamp",
                    "observed_at",
                    "ingested_at",
                    "platform",
                    "transformation",
                    "filename",
                    "sha256",
                    "role",
                    "is_synthetic",
                    "confidence_band",
                    "rank",
                },
                "MatchCandidate",
            )

    body = record(
        "GET", f"/api/cases/{case_id}/propagation", client.get(f"/api/cases/{case_id}/propagation")
    )
    expect_keys(
        body,
        {
            "case_id",
            "method",
            "interpretation",
            "origin",
            "timeline",
            "graph",
            "instance_count",
            "matched_candidate_count",
            "platforms",
            "generations",
            "truncated",
            "notes",
            "caveats",
        },
        "PropagationResponse (standalone GET)",
    )
    if isinstance(body, dict) and body.get("origin"):
        verify_origin_wording(body["origin"])

    body = record(
        "GET", f"/api/cases/{case_id}/metadata", client.get(f"/api/cases/{case_id}/metadata")
    )
    check(
        isinstance(body, dict) and "items" in body,
        "GET /metadata carries 'items'",
        f"keys={keys_of(body)}",
    )
    context["metadata_keys"] = keys_of(body)

    # 5. AUDIT.
    body = record("GET", f"/api/cases/{case_id}/audit", client.get(f"/api/cases/{case_id}/audit"))
    expect_keys(
        body,
        {
            "case_id",
            "count",
            "total_rows",
            "truncated",
            "events",
            "head_hash",
            "genesis_hash",
            "algorithm",
            "interpretation",
        },
        "AuditTrail",
    )
    if isinstance(body, dict) and body.get("events"):
        expect_keys(
            body["events"][0],
            {
                "seq",
                "audit_id",
                "case_id",
                "event",
                "timestamp",
                "actor",
                "details",
                "previous_hash",
                "row_hash",
            },
            "AuditEvent",
        )

    body = record(
        "POST",
        f"/api/cases/{case_id}/audit/verify",
        client.post(f"/api/cases/{case_id}/audit/verify"),
    )
    check(
        isinstance(body, dict) and body.get("valid") is True,
        "audit chain verifies",
        f"valid={body.get('valid') if isinstance(body, dict) else body}",
    )
    context["audit_verify_keys"] = keys_of(body)

    # 6. REPORT. `examiner` is an embedded JSON body field, not a form field --
    # there is no file here, so the backend does not accept multipart.
    response = client.post(f"/api/cases/{case_id}/report", json={"examiner": "automated"})
    body = record("POST", f"/api/cases/{case_id}/report", response)
    check(
        response.status_code in (200, 201),
        "POST /report -> 2xx",
        f"HTTP {response.status_code}",
    )
    expect_keys(
        body,
        {
            "case_id",
            "report_id",
            "filename",
            "path",
            "size_bytes",
            "sha256",
            "generated_at",
            "generator",
            "renderer",
            "pages",
            "audit_head_hash",
            "audit_chain_valid",
            "document_status",
            "renderer_status",
            "download_url",
        },
        "ReportResponse",
    )
    if isinstance(body, dict) and body.get("download_url"):
        download_url = body["download_url"]
        context["download_url"] = download_url
        context["report_id"] = body.get("report_id")
        pdf = client.get(download_url)
        record("GET", download_url, pdf)
        check(
            pdf.status_code == 200 and pdf.content[:4] == b"%PDF",
            "report download is a PDF",
            f"HTTP {pdf.status_code} magic={pdf.content[:4]!r}",
        )

    body = record(
        "GET", f"/api/cases/{case_id}/reports", client.get(f"/api/cases/{case_id}/reports")
    )
    check(
        isinstance(body, dict) and "reports" in body,
        "GET /reports carries 'reports'",
        f"keys={keys_of(body)}",
    )

    # The client omits the examiner by sending an explicit null rather than no
    # body, so that exact shape has to be accepted.
    anonymous = client.post(f"/api/cases/{case_id}/report", json={"examiner": None})
    check(
        anonymous.status_code in (200, 201),
        "report with examiner:null (the client's no-name case) -> 2xx",
        f"HTTP {anonymous.status_code}",
    )

    # Guards the fix: multipart is what a form-style client would send, and the
    # backend does not accept it here. If this ever starts passing, the client
    # could go back to FormData -- until then it must send JSON.
    multipart = client.post(
        f"/api/cases/{case_id}/report", data={"examiner": "form-style"}
    )
    check(
        multipart.status_code == 422,
        "report rejects multipart (client must send JSON)",
        f"HTTP {multipart.status_code}",
    )

    return context


def verify_verdict(analysis: dict[str, Any]) -> None:
    """The forensic guarantees the UI is required to preserve."""
    verdict = analysis.get("verdict")
    if not isinstance(verdict, dict):
        check(False, "verdict present in analyse response", str(verdict))
        return

    check(
        verdict.get("verdict") in {"AUTHENTIC", "MANIPULATED", "INSUFFICIENT_EVIDENCE"},
        "verdict band is one of the three documented values",
        str(verdict.get("verdict")),
    )
    expect_keys(
        verdict,
        {
            "evidence_id",
            "filename",
            "sha256",
            "verdict",
            "manipulation_score",
            "confidence",
            "method",
            "fusion_version",
            "signals",
            "signals_available",
            "signals_total",
            "declared_weights",
            "signal_coverage",
            "primary_signal_available",
            "thresholds",
            "excluded_signals",
            "arithmetic",
            "rationale",
            "score_semantics",
            "caveat",
            "fused_at",
            "cached",
            "media_type",
            "declared_weight_total",
            "available_weight",
            "primary_signals",
        },
        "Verdict",
    )

    signals = verdict.get("signals") or []
    statuses = {s.get("status") for s in signals}
    known = {"OK", "INCONCLUSIVE", "UNAVAILABLE", "ERROR", "UNSUPPORTED_MEDIA"}
    check(
        statuses <= known,
        "all signal statuses are known to the frontend",
        f"saw {sorted(statuses)}",
    )

    # A signal that could not be measured must carry null, never 0 -- this is the
    # distinction the whole UI is built to preserve.
    bad = [
        s["signal_id"]
        for s in signals
        if s.get("status") != "OK" and s.get("score") == 0
    ]
    check(not bad, "unmeasured signals are null, not zero", f"zero-scored: {bad}")

    # Excluded signals must be out of the denominator too.
    excluded_weight = sum(
        s.get("weight", 0) for s in signals if s.get("status") != "OK"
    )
    included_weight = sum(s.get("weight", 0) for s in signals if s.get("status") == "OK")
    check(
        abs(verdict.get("available_weight", -1) - included_weight) < 1e-6,
        "available_weight equals the sum of contributing weights",
        f"available={verdict.get('available_weight')} sum_ok={included_weight:.4f} "
        f"sum_excluded={excluded_weight:.4f}",
    )
    check(
        bool(verdict.get("arithmetic")),
        "backend publishes its fusion arithmetic",
        str(verdict.get("arithmetic"))[:80],
    )
    check(
        bool(verdict.get("rationale")),
        "backend publishes a verdict rationale",
        str(verdict.get("rationale"))[:80],
    )


def verify_propagation_nesting(analysis: dict[str, Any]) -> None:
    """The analyse response nests propagation differently from the standalone GET.

    The frontend seeds its propagation slice from the analyse response to avoid a
    second round trip, so this difference has to be exactly as the types say.
    """
    propagation = analysis.get("propagation")
    check(
        isinstance(propagation, dict) and "graph" in propagation,
        "analyse.propagation carries a graph",
        f"keys={keys_of(propagation)}",
    )
    check(
        "origin" in analysis and "timeline" in analysis,
        "analyse carries origin/timeline as siblings of propagation",
        f"origin={'origin' in analysis} timeline={'timeline' in analysis}",
    )
    if isinstance(propagation, dict):
        check(
            "origin" not in propagation or propagation.get("origin") is None,
            "analyse.propagation does not duplicate origin",
            f"nested origin={propagation.get('origin') is not None}",
        )


def verify_origin_wording(origin: dict[str, Any]) -> None:
    """The mandated phrasing, checked rather than assumed."""
    label = str(origin.get("label", ""))
    check(
        label == "earliest known instance in the indexed evidence corpus",
        "origin label uses the mandated wording verbatim",
        repr(label),
    )
    check(
        "original" not in label.lower() or "origin" in label.lower(),
        "origin label does not claim 'original source'",
        repr(label),
    )
    check(
        isinstance(origin.get("is_absolute_origin"), bool) and bool(origin.get("caveat")),
        "origin carries is_absolute_origin and a caveat",
        f"absolute={origin.get('is_absolute_origin')} caveat={str(origin.get('caveat'))[:60]}",
    )


# --- Error paths -------------------------------------------------------------


def verify_error_paths(client: TestClient, case_id: str | None) -> None:
    """The statuses the UI branches on, each produced by a real request."""
    envelope_keys = {"error", "request_id"}

    # 400 -- unsupported type (magic bytes decide, not the filename).
    response = client.post(
        "/api/cases/upload",
        files={"file": ("notes.txt", b"this is not an image at all", "image/jpeg")},
    )
    body = record("POST", "/api/cases/upload#badtype", response)
    check(response.status_code == 400, "unsupported file -> 400", f"HTTP {response.status_code}")
    expect_keys(body, envelope_keys, "error envelope (400)")

    # 400 -- empty file.
    response = client.post("/api/cases/upload", files={"file": ("empty.jpg", b"", "image/jpeg")})
    check(response.status_code == 400, "empty file -> 400", f"HTTP {response.status_code}")

    # 404 -- unknown case.
    response = client.get("/api/cases/does-not-exist")
    body = record("GET", "/api/cases/does-not-exist", response)
    check(response.status_code == 404, "unknown case -> 404", f"HTTP {response.status_code}")
    expect_keys(body, envelope_keys, "error envelope (404)")

    response = client.post("/api/cases/does-not-exist/analyse")
    record("POST", "/api/cases/does-not-exist/analyse", response)
    check(
        response.status_code == 404,
        "analyse on unknown case -> 404",
        f"HTTP {response.status_code}",
    )

    # 422 -- the multipart field is missing entirely.
    response = client.post("/api/cases/upload", data={"title": "no file attached"})
    body = record("POST", "/api/cases/upload#nofile", response)
    check(response.status_code == 422, "missing file field -> 422", f"HTTP {response.status_code}")
    expect_keys(body, envelope_keys, "error envelope (422)")
    if isinstance(body, dict):
        check(
            isinstance(body.get("error", {}).get("details"), list),
            "422 envelope carries per-field details",
            str(body.get("error", {}).get("details"))[:80],
        )

    # 413 -- oversized. The cap is lowered on the cached Settings for one request.
    settings = get_settings()
    original_cap = settings.max_upload_bytes
    try:
        settings.max_upload_bytes = 1024
        response = client.post(
            "/api/cases/upload",
            files={"file": ("big.jpg", CASE_BYTES, "image/jpeg")},
        )
        body = record("POST", "/api/cases/upload#oversize", response)
        check(
            response.status_code == 413,
            "oversized file -> 413",
            f"HTTP {response.status_code} ({len(CASE_BYTES)} bytes vs 1024 cap)",
        )
        expect_keys(body, envelope_keys, "error envelope (413)")
    finally:
        settings.max_upload_bytes = original_cap

    # A rejection is still an auditable fact about the case.
    if case_id:
        response = client.post(
            "/api/cases/upload",
            files={"file": ("bad.bin", b"\x00\x01\x02not media", "application/octet-stream")},
            data={"case_id": case_id},
        )
        check(response.status_code == 400, "rejection into a known case -> 400", f"HTTP {response.status_code}")
        trail = client.get(f"/api/cases/{case_id}/audit").json()
        events = {event.get("event") for event in trail.get("events", [])}
        check(
            any("reject" in str(event).lower() for event in events),
            "rejected upload is recorded in the audit trail",
            f"events={sorted(events)}",
        )


# --- CORS --------------------------------------------------------------------


def verify_cors(client: TestClient) -> None:
    """Preflight and simple-request behaviour for allowed and foreign origins."""
    preflight = client.options(
        "/api/cases/upload",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allow_origin = preflight.headers.get("access-control-allow-origin")
    check(
        preflight.status_code in (200, 204) and allow_origin == ALLOWED_ORIGIN,
        "preflight from the Vite dev origin is allowed",
        f"HTTP {preflight.status_code} allow-origin={allow_origin!r}",
    )

    foreign = client.options(
        "/api/cases/upload",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    check(
        foreign.headers.get("access-control-allow-origin") is None,
        "preflight from an unlisted origin is not granted",
        f"allow-origin={foreign.headers.get('access-control-allow-origin')!r}",
    )

    # CORS middleware is registered first so even a failure is readable by the
    # browser -- otherwise a 404 would surface in the UI as an opaque CORS error.
    error = client.get("/api/cases/does-not-exist", headers={"Origin": ALLOWED_ORIGIN})
    check(
        error.status_code == 404
        and error.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN,
        "error responses still carry CORS headers",
        f"HTTP {error.status_code} allow-origin={error.headers.get('access-control-allow-origin')!r}",
    )

    check(
        error.headers.get("access-control-allow-credentials") is None,
        "credentials are not advertised (so a wildcard origin stays legal)",
        f"allow-credentials={error.headers.get('access-control-allow-credentials')!r}",
    )
    check(
        bool(error.headers.get("x-request-id")),
        "every response carries X-Request-ID",
        f"x-request-id={error.headers.get('x-request-id')!r}",
    )


# --- Route table -------------------------------------------------------------

# Every path the frontend's API client can construct, as a route template.
CLIENT_ROUTES: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("GET", "/api/index/status"),
    ("GET", "/api/detector/status"),
    ("POST", "/api/cases/upload"),
    ("GET", "/api/cases"),
    ("GET", "/api/cases/{case_id}"),
    ("GET", "/api/cases/{case_id}/evidence"),
    ("POST", "/api/cases/{case_id}/analyse"),
    ("POST", "/api/cases/{case_id}/verdict"),
    ("POST", "/api/cases/{case_id}/matches"),
    ("GET", "/api/cases/{case_id}/propagation"),
    ("GET", "/api/cases/{case_id}/metadata"),
    ("GET", "/api/cases/{case_id}/audit"),
    ("POST", "/api/cases/{case_id}/audit/verify"),
    ("POST", "/api/cases/{case_id}/report"),
    ("GET", "/api/cases/{case_id}/reports"),
    ("GET", "/api/cases/{case_id}/reports/{report_id}"),
]


def verify_routes(app: Any) -> None:
    """No client function may point at a route the backend does not serve."""
    served: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"GET", "POST", "PATCH", "DELETE", "PUT"}:
                served.add((method.upper(), path))

    missing = [route for route in CLIENT_ROUTES if route not in served]
    check(
        not missing,
        "every route the client calls is served by the backend",
        f"missing {missing}" if missing else f"{len(CLIENT_ROUTES)} routes matched",
    )


# --- Entry point -------------------------------------------------------------


def main() -> int:
    app = create_app()
    verify_routes(app)

    with TestClient(app) as client:
        context = run_workflow(client)
        verify_error_paths(client, context.get("case_id"))
        verify_cors(client)

    # Written where the frontend harness looks for it. /reports is git-ignored,
    # which matters: recordings contain a real (if synthetic) case.
    default_out = Path(__file__).resolve().parent.parent / "reports" / "integration-recordings.json"
    out = Path(os.environ.get("PRAMAAN_RECORDINGS") or default_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"context": context, "responses": _recordings}, indent=2, default=str)
    )

    failures = [(name, detail) for ok, name, detail in _checks if not ok]
    for ok, name, detail in _checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    print()
    print(f"{len(_checks) - len(failures)}/{len(_checks)} checks passed")
    print(f"recordings: {out}")
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  - {name}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_ROOT, ignore_errors=True)
