FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Accept git SHA as build arg to invalidate cache when tag moves
ARG GIT_SHA=unknown
RUN echo "Building provisioner from git SHA: ${GIT_SHA}"

# Copy project files
COPY pyproject.toml README.md ./
COPY python/ ./python/

# Install dependencies (after app code is copied)
RUN uv pip install --system --no-cache .

ENV PYTHONPATH=/app/python
ENV PORT=8700

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:${PORT}/healthz || exit 1

CMD sh -c "uv run python -m hit_modules.provisioner"

