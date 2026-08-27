# Güd Vector Agent Suite

Round 1 is a channel-agnostic foundation for receiving normalized owner messages,
routing them by workflow intent, and persisting replies and outbox commands. It
does not contain channel adapters or external delivery providers.

The layering and boundary contract is documented in
[`docs/architecture.md`](docs/architecture.md). Domain code is pure and
channel-neutral; application services depend only on domain contracts;
infrastructure supplies persistence; interfaces expose the HTTP API.

## Setup

```bash
uv python install 3.12
uv sync --frozen
cp .env.example .env
```

## Development commands

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
```

```bash
uv run alembic upgrade head
uv run alembic downgrade base
uv run uvicorn gvas.interfaces.http.app:app --reload
docker compose up --build
```

The health endpoint is available at `http://localhost:8000/healthz`.

Outbox workers claim with an explicit worker identity. Attempts increment at claim time, and
failed retries calculate availability from the supplied current time.
