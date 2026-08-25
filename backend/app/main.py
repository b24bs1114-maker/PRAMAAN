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

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api import (
    alerts as alerts_api,
    analysis,
    audit as audit_api,
    cases,
    dashboard as dashboard_api,
    detector as detector_api,
    evidence as evidence_api,
    index as index_api,
    reports as reports_api,
    system as system_api,
)
from app.config import Settings, configure_logging, get_settings
from app.models import init_db
from app.services import detector as detector_service

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
) -> JSONResponse:
    """Build the single error envelope every failure path returns.

    Internal details -- stack traces, file paths, driver messages -- are logged
    server side and never serialised into the response.
    """
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
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
        detector_service.get_detector(current_settings)
        logger.info("Detector pre-warm completed")
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
    app.include_router(cases.router)
    app.include_router(dashboard_api.router)
    app.include_router(analysis.router)
    app.include_router(evidence_api.router)
    app.include_router(index_api.router)
    app.include_router(detector_api.router)
    app.include_router(reports_api.router)
    app.include_router(reports_api.library_router)
    app.include_router(alerts_api.router)
    app.include_router(audit_api.router)
    app.include_router(system_api.router)

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
