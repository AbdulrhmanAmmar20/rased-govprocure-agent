"""FastAPI application assembly (SRS section 5).

Startup is where the sovereignty posture is asserted rather than assumed: the
residency of both the deployment and the inference endpoint is checked before
the first request is served, and a seed corpus is announced loudly. A service
that only discovers a misconfiguration when a report is already wrong has
discovered it too late.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.agent.llm import assert_sovereign_endpoint
from app.api.routes import approvals, audit, auth, health, procurement
from app.config import get_settings
from app.core.exceptions import RasedError, ResidencyViolationError
from app.core.logging import configure_logging, get_logger
from app.rag.retriever import Retriever

logger = get_logger("rased.main")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    logger.info(
        "starting Rased",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "region": settings.data_residency_region,
        },
    )

    # NFR-1.1 - refuse to serve through a non-sovereign inference endpoint.
    try:
        assert_sovereign_endpoint(settings.llm_base_url)
    except ResidencyViolationError as exc:
        logger.error(
            "configured inference endpoint is outside the private network",
            extra={"base_url": settings.llm_base_url},
        )
        if settings.environment == "production":
            raise
        logger.warning("continuing in %s despite: %s", settings.environment, exc.message)

    # Build the index once at startup rather than per request.
    retriever = Retriever()
    app.state.retriever = retriever
    status = retriever.corpus_metadata.get("source_status")
    if status != "official":
        logger.warning(
            "regulation corpus is not the official text; reports are not legally "
            "certifiable until it is replaced (see docs/corpus-governance.md)",
            extra={"corpus_status": status, "corpus_version": retriever.corpus_metadata.get("corpus_version")},
        )

    yield
    logger.info("shutting down Rased")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="راصد — Rased GovProcure Agent",
        description=(
            "وكيل ذكاء اصطناعي للامتثال وتدقيق المشتريات الحكومية، "
            "يعمل بالكامل داخل سحابة سيادية محلية."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # TLS termination happens at the ingress (NFR-1.2); HSTS is asserted
        # here so the header survives a misconfigured proxy that forgets it.
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RasedError)
    async def rased_error_handler(request: Request, exc: RasedError) -> JSONResponse:
        logger.warning(
            "request failed",
            extra={"code": exc.code, "path": request.url.path},
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(procurement.router, prefix=API_PREFIX)
    app.include_router(approvals.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)

    # An endpoint that mints a Director token on request must never exist in
    # production. Gated on configuration, not on deployment discipline.
    if settings.environment != "production":
        app.include_router(auth.router, prefix=API_PREFIX)
    else:
        logger.info("development token endpoint not mounted (environment=production)")

    return app


app = create_app()
