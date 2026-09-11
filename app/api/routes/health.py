"""Liveness and readiness (NFR-2.3)."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.agent.graph import langgraph_available
from app.agent.llm import SovereignLLM
from app.api.schemas import HealthResponse, ReadinessResponse
from app.config import get_settings
from app.core.exceptions import ResidencyViolationError
from app.rag.store import load_corpus

router = APIRouter(prefix="/health", tags=["health"])


def _presidio_available() -> bool:
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        return False
    return True


@router.get("/live", response_model=HealthResponse, summary="فحص الحياة")
async def live() -> HealthResponse:
    """Process is up. Intentionally checks nothing else.

    A liveness probe that depends on the inference endpoint or the corpus will
    restart healthy pods during an unrelated outage, which turns a degraded
    service into no service at all.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok", version=__version__, region=settings.data_residency_region
    )


@router.get("/ready", response_model=ReadinessResponse, summary="فحص الجاهزية")
async def ready() -> ReadinessResponse:
    """Readiness, including whether the corpus is certified or still seeded."""
    settings = get_settings()
    _, corpus_meta = load_corpus()

    try:
        inference_available = SovereignLLM().is_available()
    except ResidencyViolationError:
        # A misconfigured endpoint is reported as unavailable rather than
        # raising, so the probe reflects it instead of the pod flapping.
        inference_available = False

    return ReadinessResponse(
        status="ready",
        region=settings.data_residency_region,
        corpus_version=str(corpus_meta["corpus_version"]),
        corpus_status=str(corpus_meta["source_status"]),
        corpus_documents=int(corpus_meta["document_count"]),
        inference_endpoint=settings.llm_base_url,
        inference_available=inference_available,
        vector_backend=settings.vector_backend,
        langgraph_installed=langgraph_available(),
        presidio_installed=_presidio_available(),
    )
