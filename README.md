# Güd Vector Agent Suite

Round 1 is a channel-agnostic foundation for receiving normalized owner message
envelopes, durably queueing processing commands, and replay-safe workflow
processing. Intent resolution and handler execution happen outside database
transactions; replies and delivery commands are persisted atomically. It does
not contain channel adapters or external delivery providers.

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

To execute the PostgreSQL-backed concurrency tests as well as the SQLite suite:

```bash
GVAS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/gvas \
  uv run pytest
```

```bash
uv run alembic upgrade head
uv run alembic downgrade base
uv run uvicorn gvas.interfaces.http.app:app --reload
docker compose up --build
```

The health endpoint is available at `http://localhost:8000/healthz`.

## Slack Request URL adapter

`gvas.infrastructure.slack` and `gvas.interfaces.http.slack` hold every
Slack-specific type; domain and application code stay channel-neutral. The
router verifies the signing secret over the raw body, rejects stale timestamps,
answers the `url_verification` challenge, normalizes message events into
`InboundOwnerMessage`, and returns as soon as ingestion has persisted the
inbound message and its outbox command. Slack retries of an already-ingested
event are reported as duplicates without new work. Owner replies go out through
`OwnerReplyPort` using persisted routing plus a deterministic delivery key, so
posting is skipped only after a recorded success.

Mount it explicitly (it is not part of the default app):

```python
create_app(routers=(create_slack_router(ingress, path=settings.events_path),))
```

Settings use the `GVAS_SLACK_` prefix; see [`.env.example`](.env.example).
`GVAS_SLACK_INSTALLATIONS` maps Slack team IDs to a business and its authorized
owner users (`T0000000000=<business-uuid>:U0000000000|U0000000001,...`).
Unmapped workspaces are rejected, and workspace membership alone grants nothing:
a message from a human who is not a configured owner of that business is
acknowledged but never ingested. Each entry must list at least one owner user,
so a misconfiguration cannot silently authorize a whole workspace.

Reply correlation follows persisted conversation/thread state — the adapter
resolves the Slack channel and thread from stored routing and never expects
Slack to echo internal outbound correlation IDs back to us.

The default composition uses `UnconfiguredIntentResolver`. Ingress persists the
inbound message and one `owner_message.process` command; processing remains
resumable until an intent resolver is configured. Outbox workers claim with an
explicit worker identity; attempts increment at claim time, and failed retries
calculate availability from the supplied current time. Processing and outbox
lease cutoffs are caller-supplied; there is no default lease duration.
