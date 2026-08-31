# Deploying the Slack pilot on Railway

The pilot runs as three Railway resources in one project: a **web** service, a
**worker** service, and Railway's managed **PostgreSQL** database. Both services
deploy the same image and the same code; they differ only in their start
command, and neither works without the other. The web service acknowledges
Slack requests and writes commands to the outbox; every provider call (Slack
posts, OpenAI transcription, Resend email) happens in the worker.

## Database URL

Railway's PostgreSQL plugin publishes `DATABASE_URL` in the libpq form
(`postgresql://…`, sometimes with `sslmode=require`). SQLAlchemy's async engine
needs the asyncpg driver and rejects libpq-only query parameters, so
`gvas.config.normalize_async_database_url` rewrites the scheme to
`postgresql+asyncpg://` and drops `sslmode`/`channel_binding` before the engine
is created. Reference the plugin variable and let the app normalize it:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Set `GVAS_DATABASE_URL` only to point a service at a different database; it
takes precedence over `DATABASE_URL`.

## Commands

| Purpose | Command |
| --- | --- |
| Pre-deploy | `gvas-migrate` (upgrades to head; `--revision` to pin) |
| Web start | `gvas-web` |
| Worker start | `gvas-worker` |
| Health check path | `/healthz` |

The image puts the synced virtualenv on `PATH`, so the console scripts are run
directly; `uv run` inside the container would try to re-sync into a read-only
image. `gvas-web` binds `PORT` when the platform sets it, otherwise 8000. The
Dockerfile's default command is the web service, so the worker service needs its
start command overridden to `gvas-worker`.

Run migrations as the pre-deploy command on the web service only. Running the
same Alembic upgrade from two services concurrently is unnecessary; the schema
is shared.

## First-run bootstrap

The tenant row and its template set are created by an idempotent command. Run it
once from the Railway shell (or as a one-off) after the first migration:

```
gvas-bootstrap --business-id <uuid> --slug protech --name "ProTech" \
  --industry environmental_testing
```

It accepts `GVAS_BOOTSTRAP_BUSINESS_ID`, `GVAS_BOOTSTRAP_SLUG`,
`GVAS_BOOTSTRAP_NAME` and `GVAS_BOOTSTRAP_INDUSTRY` instead of flags, prints
only identifiers, and is safe to re-run: it updates the existing business in
place and republishes the same template set version.

The business UUID it is given must match the one in `GVAS_SLACK_INSTALLATIONS`,
otherwise Slack events arrive for a tenant that does not exist.

## Slack app configuration

- **Request URL**: `https://<web-service-domain>/slack/events`
  (`GVAS_SLACK_EVENTS_PATH` if you change the path). Slack's URL verification
  handshake is answered by the same route.
- **Event subscriptions**: the `message.*` events for the conversation types the
  owner uses (`message.channels`, `message.groups`, `message.im`). Only
  `message` events are handled; voice notes arrive as the `file_share` subtype
  of a message, so no separate file event is needed.
- **Bot scopes**: `chat:write` for replies, `files:read` for voice note bytes,
  plus the `*:history` scopes for the conversation types above.
- **Authorization**: exactly one owner is authorized per installation.
  `GVAS_SLACK_INSTALLATIONS` maps `team_id=business_uuid:user_id|user_id`;
  messages from anyone else are ignored. There is no workspace-wide
  authorization.

## Environment variables

Set on both services unless noted. Values below are placeholders; see
`.env.example`.

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | Railway Postgres reference; normalized to asyncpg |
| `GVAS_APP_ENV`, `GVAS_LOG_LEVEL` | `production`, `INFO` |
| `GVAS_SLACK_SIGNING_SECRET` | Required; request signature verification |
| `GVAS_SLACK_BOT_TOKEN` | Required; sent only as a bearer header |
| `GVAS_SLACK_INSTALLATIONS` | Required; `team=business_uuid:owner_user_id` |
| `GVAS_SLACK_EVENTS_PATH` | Default `/slack/events` |
| `GVAS_SLACK_REQUEST_MAX_AGE_SECONDS` | Default 300 |
| `GVAS_SLACK_ATTACHMENT_MAX_BYTES` | Default 25 MiB; caps voice note downloads |
| `GVAS_SLACK_API_BASE_URL`, `GVAS_SLACK_API_TIMEOUT_SECONDS` | Defaults suffice |
| `GVAS_OPENAI_API_KEY` | Required; transcription only |
| `GVAS_OPENAI_TRANSCRIPTION_MODEL` | Default `whisper-1` |
| `GVAS_OPENAI_MAX_AUDIO_BYTES`, `GVAS_OPENAI_TIMEOUT_SECONDS` | Defaults suffice |
| `GVAS_RESEND_API_KEY` | Required; approved quote email |
| `GVAS_RESEND_FROM_ADDRESS` | Required; verified sending domain |
| `GVAS_RESEND_REPLY_TO_ADDRESS` | Optional |
| `GVAS_RESEND_PORTAL_URL` | Default `https://gudvector.com/portal/login` |
| `GVAS_WORKER_BATCH_SIZE`, `_POLL_SECONDS`, `_RETRY_SECONDS`, `_LEASE_SECONDS` | Worker only |
| `GVAS_WORKER_ID_PREFIX` | Worker only; hostname and pid are appended per replica |

Startup fails immediately when a required variable is missing, so a
misconfigured deploy never accepts Slack traffic.

## Operational notes

- **Worker replicas**: each replica claims outbox rows under
  `<prefix>-<hostname>-<pid>`, so scaling out does not make replicas steal each
  other's leases. Rows claimed by a replica that dies become claimable again
  after `GVAS_WORKER_LEASE_SECONDS`.
- **Shutdown**: SIGTERM and SIGINT stop the worker after the batch in flight,
  which is why the lease exists rather than a mid-command rollback.
- **Delivery semantics**: owner replies are at-least-once. The delivery ledger
  is committed in its own transaction, but a crash between Slack accepting a
  post and that commit will repost on replay. The delivery key travels in Slack
  message metadata so duplicates can be identified afterwards.
- **Exhausted retries**: when a command dies, one sanitized notice is posted in
  the originating thread with retry guidance. Provider responses, exceptions and
  tokens stay in the outbox record.
- **Review and reporting are deterministic**: `MarkerCompletenessReviewer`,
  `MarkerChecklistEvidenceAttributor` and `DeterministicReportGenerator` are
  wired in place of an inference provider. No model was benchmarked or selected
  for review or reporting; when one is, it replaces those three port
  implementations in `gvas.composition.production` and nothing else.
