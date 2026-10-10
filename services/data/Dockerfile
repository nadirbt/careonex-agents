# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

# uv for reproducible installs from uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so they cache independently of source changes
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY careonex_data ./careonex_data
COPY catalog ./catalog
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Non-root user; /data is where snapshot manifests land (mount it)
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

ENV PATH="/opt/venv/bin:$PATH" \
    CAREONEX_CATALOG=/app/catalog/ragfile_list.csv \
    CAREONEX_DATA_DIR=/data \
    AWS_DEFAULT_REGION=us-east-1

ENTRYPOINT ["careonex-data"]
CMD ["status"]
