# Güd Vector Agent Suite

GVAS receives normalized owner message envelopes, durably queues processing
commands, and processes quote and field-note workflows replay-safely. Intent
resolution and handler execution happen outside database transactions; replies
and delivery commands are persisted atomically. Slack is the first channel, and
every provider (Slack, OpenAI transcription, Resend) lives behind a port in
infrastructure.

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
uv run mypy src tests evals
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
uv run gvas-web  # or: uv run uvicorn gvas.interfaces.http.app:app --reload
docker compose up --build
```

The health endpoint is available at `http://localhost:8000/healthz`.

The migration graph has a single head; `uv run alembic heads` must report exactly
one revision.

## Composition root and worker

`gvas.composition` wires the accepted workflows into one application and an
outbox worker. Every external capability is injected as a port, so this module
names no provider:

```python
from gvas.composition import ApplicationPorts, build_application

application = build_application(ApplicationPorts(...))
await application.worker.drain()
```

Deterministic fakes for every port live in `tests/composition_fakes.py`, which is
how the integration tests run the full quote and field-note paths without a
provider. See [`docs/composition.md`](docs/composition.md) for the dispatched
command types, the field-note chain, where production adapters plug in, and the
decisions that are still open.

Model-selection evaluation tooling for field-note extraction lives in
[`evals/field_notes`](evals/field_notes/README.md). It is isolated from the product:
it imports no `gvas` module, reaches no network, and selects no provider.

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

The generic `create_app` stays provider-free for tests; the deployed process is
`gvas.composition.production:create_production_app`, which mounts the router:

```python
create_app(routers=(create_slack_router(ingress, path=settings.events_path),))
```

Settings use the `GVAS_SLACK_` prefix; see [`.env.example`](.env.example).
`GVAS_SLACK_INSTALLATIONS` maps Slack team IDs to a business and its authorized
owner users (`T0000000000=<business-uuid>:U0000000000|U0000000001,...`).
Unmapped workspaces are rejected, and workspace membership alone grants nothing:
a message from a human who is not a configured owner of that business is
acknowledged but never ingested. Each entry must list at least one owner user,
so a misconfiguration cannot silently authorize a whole workspace. The parser is
general, but the production runtime is narrower: `load_production_settings`
refuses to start unless exactly one installation with exactly one owner user is
configured, which is the boundary this pilot was approved for.

Reply correlation follows persisted conversation/thread state — the adapter
resolves the Slack channel and thread from stored routing and never expects
Slack to echo internal outbound correlation IDs back to us.

## Production runtime

`gvas.composition.production` is the only module that chooses providers. It
wires the Slack chat poster and private-file access, OpenAI transcription,
Resend quote delivery, the deterministic quote parser, and — because no
inference model has been selected for review or reporting — the marker reviewer,
marker evidence attributor and deterministic report generator. It refuses to
start when a required setting is missing. Entrypoints:

```bash
uv run gvas-migrate           # release / pre-deploy, upgrades to head
uv run gvas-web               # web service, mounts the Slack Request URL
uv run gvas-worker            # continuously running outbox worker
uv run gvas-bootstrap --business-id <uuid> --slug protech --name "ProTech"
```

Railway deployment, environment variables and operational semantics are in
[`docs/deployment.md`](docs/deployment.md).

## Quote requests

GVAS structures the owner's own prices and never estimates one, so a quote
request states them explicitly:

```text
quote:
customer: person@example.com
currency: USD
item: 2 | Air sampling | 125.00
item: 1 | Report | 200.00
note: on-site visit scheduled for Tuesday
```

`customer`, `currency` and at least one `item` are required; keys and
surrounding whitespace are case- and space-insensitive; amounts are read as
exact minor units and rendered with integer arithmetic only. The pilot prices
in USD; another currency is refused rather than priced with a guessed
minor-unit exponent. A missing or unparsable field is rejected with an
owner-facing message rather than guessed, and corrections are sent in the same
format. The owner still approves before anything is emailed; approved quotes go
out through Resend and link to the customer portal.

Ingress persists the inbound message and one `owner_message.process` command;
processing remains resumable across restarts. Outbox workers claim with an
explicit worker identity; attempts increment at claim time, and failed retries
calculate availability from the supplied current time. Processing and outbox
lease cutoffs are caller-supplied; there is no default lease duration.
