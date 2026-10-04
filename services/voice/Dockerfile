# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# No --extra mic: PortAudio is a laptop concern; the image runs the mic-free smoke test.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY nova_sonic ./nova_sonic
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app:app /app /data
USER app
ENV PATH="/opt/venv/bin:$PATH" CAREONEX_DATA_DIR=/data AWS_DEFAULT_REGION=us-east-1
ENTRYPOINT ["careonex-voice-smoke"]
CMD []
