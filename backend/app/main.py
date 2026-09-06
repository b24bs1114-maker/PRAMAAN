"""PRAMAAN backend entrypoint.

Foundation only: application factory, CORS, request logging, error handling and
a health probe. Forensic capabilities (ingestion, hashing, perceptual retrieval,
provenance, fusion, audit log, reporting) arrive in later tasks under
``app/api``, ``app/services``, ``app/models`` and ``app/schemas``.

Run with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api import (
    alerts as alerts_api,
    analysis,
    audit as audit_api,
    auth as auth_api,
    cases,
    dashboard as dashboard_api,
    detector as detector_api,
    evidence as evidence_api,
    index as index_api,
    reports as reports_api,
    system as system_api,
)
from app.api.deps import get_current_user
from app.config import Settings, configure_logging, get_settings
from app.models import init_db, session_scope
from app.services import detector as detector_service, identity
from app.services.pipeline import EvidenceIntegrityError

logger = logging.getLogger("pramaan.app")
access_logger = logging.getLogger("pramaan.access")

REQUEST_ID_HEADER = "X-Request-ID"


def _request_id(request: Request) -> str:
    """Return the request id assigned by middleware, or a placeholder."""
    return getattr(request.state, "request_id", "-")


def _error_response(
    *,
    status_code: int,
    error_type: str,
    message: str,
    request_id: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the single error envelope every failure path returns.

    Internal details -- stack traces, file paths, driver messages -- are logged
    server side and never serialised into the response.

    ``headers`` carries the response headers an ``HTTPException`` declared. Those
    are part of the HTTP semantics of the failure, not decoration: a 401 without
    ``WWW-Authenticate`` tells the client it is unauthorised but not how to
    authenticate, which RFC 9110 forbids. The request-id header is applied last so
    a raising endpoint cannot displace it.
    """
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "request_id": request_id},
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach a request id, then log method, path, status and duration.

        Only the URL path is logged -- query strings and headers may carry
        credentials or case-sensitive identifiers and stay out of the log.
        """
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            access_logger.error(
                "%s %s -> 500 in %.1fms rid=%s",
                request.method,
                request.url.path,
                elapsed_ms,
                request_id,
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        if settings.log_access:
            access_logger.info(
                "%s %s -> %d in %.1fms rid=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
        return response

    # CORSMiddleware registered LAST so Starlette's build_middleware_stack()
    # places it as the OUTERMOST user middleware on the final ASGI pipeline.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers,
        allow_credentials=settings.cors_allow_credentials,
        expose_headers=[REQUEST_ID_HEADER],
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = _request_id(request)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code >= 500:
            logger.error(
                "Server HTTP error %s on %s rid=%s",
                exc.status_code,
                request.url.path,
                request_id,
            )
        else:
            logger.info(
                "Client HTTP error %s on %s rid=%s",
                exc.status_code,
                request.url.path,
                request_id,
            )
        return _error_response(
            status_code=exc.status_code,
            error_type="http_error",
            message=message,
            request_id=request_id,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = _request_id(request)
        # Report where and why validation failed, but do not echo the submitted
        # values back -- they can contain sensitive payload data.
        details = [
            {
                "location": list(err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        logger.info(
            "Validation failed on %s rid=%s (%d issue(s))",
            request.url.path,
            request_id,
            len(details),
        )
        return _error_response(
            status_code=422,  # numeric: the Starlette constant name is in flux
            error_type="validation_error",
            message="Request validation failed.",
            request_id=request_id,
            details=details,
        )

    @app.exception_handler(EvidenceIntegrityError)
    async def evidence_integrity_handler(
        request: Request, exc: EvidenceIntegrityError
    ) -> JSONResponse:
        """A refused analysis, answered as a conflict rather than a crash.

        The pipeline raises this when a stored file no longer hashes to the
        digest recorded at intake. Without a handler it would fall through to the
        catch-all below and reach the examiner as "an internal error occurred",
        which is both wrong and the single least useful thing to say about a
        chain-of-custody failure. 409 is the same status this deployment already
        uses for evidence that is registered but not present on the host: the
        request is well formed, the stored state contradicts it.

        The digests are in the response because they are what the examiner needs
        to act -- neither of them is a secret; one is already published on the
        evidence record and the other is a hash of bytes the operator holds.
        """
        request_id = _request_id(request)
        logger.error(
            "Refused analysis of evidence %s on %s rid=%s: stored bytes no longer "
            "match the intake digest",
            exc.evidence_id,
            request.url.path,
            request_id,
        )
        return _error_response(
            status_code=status.HTTP_409_CONFLICT,
            error_type="evidence_integrity_mismatch",
            message=str(exc),
            request_id=request_id,
            details=[
                {
                    "evidence_id": exc.evidence_id,
                    "filename": exc.filename,
                    "recorded_sha256": exc.expected,
                    "recomputed_sha256": exc.recomputed,
                }
            ],
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = _request_id(request)
        # Full traceback to the server log; opaque message to the client.
        logger.exception(
            "Unhandled error on %s %s rid=%s",
            request.method,
            request.url.path,
            request_id,
        )
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="internal_server_error",
            message=(
                "An internal error occurred. Quote the request id when "
                "reporting this issue."
            ),
            request_id=request_id,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current_settings = get_settings()
        app.state.settings = current_settings
        current_settings.ensure_directories()
        init_db(current_settings)
        # Operator accounts are required before anyone can sign in, and identity
        # is what the ingestion endpoint stamps as the examiner. Seeding is a
        # no-op once the users table is populated, so restarts never overwrite
        # a password an operator has changed.
        with session_scope() as session:
            identity.seed_operators(session, current_settings)
        logger.info(
            "%s v%s starting (environment=%s, debug=%s)",
            current_settings.app_name,
            current_settings.app_version,
            current_settings.environment,
            current_settings.debug,
        )
        logger.info(
            "Paths: data=%s reports=%s corpus=%s",
            current_settings.data_dir,
            current_settings.reports_dir,
            current_settings.corpus_dir,
        )
        logger.info("CORS allowed origins: %s", ", ".join(current_settings.cors_origins))
        if current_settings.dev_auth_bypass_active:
            # Loud, every start, at WARNING. A server that stopped requiring
            # authentication must never be something an operator has to go
            # looking for -- and if this line appears anywhere it should not,
            # that is exactly the signal it exists to give.
            logger.warning(
                "DEVELOPMENT AUTH BYPASS IS ACTIVE (environment=%s). Requests "
                "without a bearer token resolve to %r. Real login still works and "
                "is unchanged. Set PRAMAAN_DEV_AUTH_BYPASS=false to restore "
                "authentication.",
                current_settings.environment,
                current_settings.dev_auth_bypass_username,
            )
        elif current_settings.dev_auth_bypass:
            # The flag is on but the environment refused it. Say so, or someone
            # will spend an afternoon wondering why the bypass "does not work".
            logger.warning(
                "PRAMAAN_DEV_AUTH_BYPASS is set but IGNORED because "
                "environment=%s. Authentication remains required.",
                current_settings.environment,
            )
        if not current_settings.enable_ai_detector:
            logger.info("AI detector disabled for demo/stability mode")
        else:
            # Construct the adapter (cheap: resolves configuration, loads no
            # model) so the first analysis does not pay for it.
            adapter = detector_service.get_detector(current_settings)
            usable, reason = adapter.available()
            logger.info(
                "Detector adapter ready: %s (available=%s)%s",
                adapter.id,
                usable,
                "" if usable else f" reason={reason}",
            )
            if current_settings.detector_prewarm:
                # Opt-in only. Loading 347 MB of weights before the first
                # health check answers is how a slow startup becomes a failed
                # deploy; off by default, the model loads on first analysis.
                loaded = detector_service.prewarm(current_settings)
                logger.info("Detector pre-warm: %s", loaded)
        yield
        logger.info("%s shutting down", current_settings.app_name)

    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version=settings.app_version,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    _register_middleware(app, settings)
    _register_exception_handlers(app)

    # --- Routing and authorization ------------------------------------------
    #
    # Authorization is applied here, at the point every router is mounted, and
    # not route by route: this is the one list of what the API exposes, so a
    # router added without a decision about who may call it is visible in the
    # same three lines as the decision itself.
    #
    # `PROTECTED` is everything that reads or writes case material -- cases,
    # evidence, analysis, provenance, the audit chain, reports, the perceptual
    # index, alerts, and the capability probes that describe this deployment.
    # Every one of these was reachable with no credentials at all until now,
    # including `DELETE /api/cases/{case_id}`, which destroys a case, its
    # evidence files, its report PDFs and its index vectors. The console has
    # always claimed there is "no coherent way to run intake anonymously"; this
    # is what makes that true of reading and deletion as well as of ingest.
    #
    # Deliberately public: `POST /api/auth/login`, which is how a token is
    # obtained in the first place, and `GET /health`, which a load balancer or
    # container orchestrator probes without credentials. `auth_api.router`
    # therefore carries no blanket dependency -- `/api/auth/me` and
    # `/api/auth/logout` authenticate themselves, per-route.
    PROTECTED = (
        cases.router,
        dashboard_api.router,
        analysis.router,
        evidence_api.router,
        index_api.router,
        detector_api.router,
        reports_api.router,
        reports_api.library_router,
        alerts_api.router,
        audit_api.router,
        system_api.router,
    )

    app.include_router(auth_api.router)
    for protected in PROTECTED:
        app.include_router(protected, dependencies=[Depends(get_current_user)])

    @app.get("/health", tags=["system"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Return ``{"status": "ok"}`` when the service is responsive.

        The payload is a fixed contract -- monitoring and the frontend depend
        on it, so no fields are added here.
        """
        return {"status": "ok"}

    @app.get("/", tags=["system"], summary="Service information")
    async def root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs_url": "/docs" if settings.enable_docs else None,
            "health_url": "/health",
        }

    return app


app = create_app()
