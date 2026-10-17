# Same image runs under docker compose locally and on Cloud Run.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first for layer caching. The gcp extra is included so switching a
# *_PROVIDER to Google Cloud needs only env vars, not a rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gcp --no-install-project

COPY . .

RUN useradd --create-home --uid 10001 app && mkdir -p /data/media && chown app /data/media
USER app

# Cloud Run injects $PORT. Run migrations separately (Cloud Run Job: `alembic upgrade head`).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
