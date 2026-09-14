# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencies are resolved from whatever index is configured for the build.
# In the sovereign pipeline that must be the in-Kingdom mirror, never a public
# one (NFR-1.1) - see deploy/README.md.
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Rased GovProcure Agent" \
      org.opencontainers.image.description="AI compliance and audit agent for Saudi government procurement" \
      org.opencontainers.image.licenses="MIT"

# Run unprivileged. A compromised agent process must not be able to alter the
# audit trail's directory permissions or the corpus it reasons from.
RUN groupadd --gid 10001 rased \
 && useradd --uid 10001 --gid rased --create-home --shell /usr/sbin/nologin rased

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    RASED_ENVIRONMENT=production

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=rased:rased app ./app
COPY --chown=rased:rased scripts ./scripts
COPY --chown=rased:rased pyproject.toml README.md ./

# The audit trail is the one thing the process must be able to append to.
RUN mkdir -p /var/lib/rased/audit /var/lib/rased/chroma \
 && chown -R rased:rased /var/lib/rased

ENV RASED_AUDIT_LOG_PATH=/var/lib/rased/audit/audit.jsonl \
    RASED_VECTOR_PATH=/var/lib/rased/chroma

USER rased
EXPOSE 8000

# Liveness only. Readiness depends on the inference endpoint, and wiring that
# into the container healthcheck would have the orchestrator kill healthy
# replicas during an inference outage the service is designed to survive.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/v1/health/live', timeout=2).status_code==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
