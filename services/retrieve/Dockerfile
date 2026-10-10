# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY careonex_retrieve ./careonex_retrieve
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app:app /app /data
USER app
ENV PATH="/opt/venv/bin:$PATH" CAREONEX_DATA_DIR=/data AWS_DEFAULT_REGION=us-east-1 PORT=8080
EXPOSE 8080
ENTRYPOINT ["careonex-retrieve"]
CMD ["serve"]
