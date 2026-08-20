# ──────────────────────────────────────────────────────────────────────────
# InfrGate — Dockerfile (multi-stage build)
# ──────────────────────────────────────────────────────────────────────────

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

# Create non-root user
RUN groupadd --gid 1000 infrgate \
    && useradd --uid 1000 --gid infrgate --shell /bin/bash --create-home infrgate

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./

# Switch to non-root user
USER infrgate

EXPOSE 8000

CMD ["uvicorn", "infrgate.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
