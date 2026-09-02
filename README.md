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
Resend quote delivery, the deterministic quote parser with the OpenAI free-text
fallback, and — because no inference model has been selected for review or
reporting — the marker reviewer,
marker evidence attributor and deterministic report generator. It refuses to
start when a required setting is missing. Entrypoints:

```bash
uv run gvas-migrate           # release / pre-deploy, upgrades to head
uv run gvas-web               # web service, mounts the Slack Request URL
uv run gvas-worker            # continuously running outbox worker
uv run gvas-bootstrap --business-id <uuid> --slug protech --name "ProTech"
```

Railway deployment, environment variables and operational semantics are in
[`docs/deployment.md`](docs/deployment.md). What has shipped, what is next, and
which decisions are open is tracked in [`docs/roadmap.md`](docs/roadmap.md).

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

### Quote delivery

When `GVAS_PORTAL_BASE_URL` and `GVAS_PORTAL_API_TOKEN` are set (see
[deployment](docs/deployment.md)) an approved quote is created in the customer
portal instead: `PortalQuoteDelivery` posts the line items (integer cents,
USD), the customer's name, email, phone and service address to
`POST /api/quotes` with a bearer token, and the portal emails the customer its
`quoteUrl` when there is an email. The returned claim token and URL are kept
against the quote's `quote-delivery:{quote_id}` key, so an outbox retry after a
successful create short-circuits instead of creating a second portal quote.
When the customer has a phone number and Telnyx is configured, one
`customer_quote.text` command then texts
`Your Güd Vector quote for <first item> is ready: <quoteUrl>` from the
business's Telnyx number, trimmed to a single 160-character segment by
shortening the description, never the link. The owner's confirmation carries
the `quoteUrl` and which channels were used (emailed, texting, or neither). A
failed text never undoes the portal quote; after retries the owner gets a
notice saying the quote exists, whether the portal emailed it, and the link to
forward. Without the portal variables delivery is unchanged: Resend emails the
quote body with the generic portal login link, and nothing is texted.

When Calendly is configured (`GVAS_CALENDLY_*`, see [deployment](docs/deployment.md))
`customer:` may be omitted and the customer is taken from the owner's
appointments in the surrounding three UTC days (yesterday, today, tomorrow).
One match drafts straight away and the approval reply opens with
`Customer: Jane Doe (jane@example.com) — Calendly, Tue 2:00pm, 234 Del Rd` so
a wrong match is caught before `approve`. Several matches get one numbered list
(address, or invitee name and time when the event has no address) and the owner
replies with the number; `reject` cancels. No match falls back to the
`customer` required message. An optional `for: <name>` line narrows the
candidates by a case-insensitive substring of the invitee name. If Calendly
cannot be reached the quote is dropped with one reply asking for `customer:`
this time. The same flow runs over Slack and SMS.

### Free-text quotes

When the structured parser refuses the text, and OpenAI is configured, the
review model reads the request instead, so these all draft:

```text
quote: inspection 250
quote: 2 air samples at 125 each plus the report 200, note we'll be there tuesday
quote for jane: mold inspection 350
```

The model proposes line items (quantity, description, unit price as written),
an optional customer note and a list of ambiguities; the matched appointment
(event, time, address, invitee and their booking answers) is passed along so
descriptions can reflect what the customer booked ("attic mold, 2 bedrooms").
GVAS still never invents a price: every unit price the model returns must
appear literally in the owner's text (`250`, `250.00`, `$250`, `1,250`), and
the appointment never contributes one. An item without such a price, or a
request the model finds no items in, drafts nothing — the owner gets one
question naming the items that need a price. Quantities default to 1, the
currency is USD. The draft reply lists items and total as usual plus
`Drafted from your message — check items before approving.`; approval is
unchanged. A request written entirely in `key: value` lines with a mistake in
it keeps the parser's message and never reaches the model. A model or API
failure, or a reached `GVAS_COST_CEILING_REVIEW_TOKENS`, gives one reply with
the structured format; tokens count against that same review ceiling.

Ingress persists the inbound message and one `owner_message.process` command;
processing remains resumable across restarts. Outbox workers claim with an
explicit worker identity; attempts increment at claim time, and failed retries
calculate availability from the supplied current time. Processing and outbox
lease cutoffs are caller-supplied; there is no default lease duration.
