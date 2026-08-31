FROM ghcr.io/astral-sh/uv:0.6.14 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
# The console scripts run from the synced environment; `uv run` would try to
# re-sync dev dependencies into a read-only image.
ENV PATH="/app/.venv/bin:${PATH}"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8000
CMD ["gvas-web"]
