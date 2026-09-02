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
needs the asyncpg driver, and asyncpg names some libpq options differently, so
`gvas.config.normalize_async_database_url` rewrites the scheme to
`postgresql+asyncpg://` and translates `sslmode=<mode>` to asyncpg's `ssl=<mode>`
keyword, keeping a required TLS mode in force. Options asyncpg 0.30 accepts,
including `target_session_attrs`, are passed through; only options it has no
keyword for (`channel_binding`) are dropped, and an unrecognized `sslmode`
value is an error rather than a silent downgrade. Reference the plugin variable
and let the app normalize it:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Set `GVAS_DATABASE_URL` only to point a service at a different database; it
takes precedence over `DATABASE_URL`. Either way the value must be a PostgreSQL
URL naming a host and database: startup refuses SQLite or any other URL, and
refuses to fall back to the local development default when neither variable is
set.

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
  `files:write` to share the approved report DOCX into the thread, plus the
  `*:history` scopes for the conversation types above.
- **Authorization**: this pilot is approved for one workspace and one owner.
  `GVAS_SLACK_INSTALLATIONS` maps `team_id=business_uuid:owner_user_id`, and
  startup rejects a value carrying a second installation or a second owner
  (`user_id|user_id`), even though the parser itself accepts them for later
  tenants. Messages from anyone else are ignored, and there is no
  workspace-wide authorization.

## Environment variables

Set on both services unless noted. Values below are placeholders; see
`.env.example`.

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | Railway Postgres reference; normalized to asyncpg |
| `GVAS_APP_ENV`, `GVAS_LOG_LEVEL` | `production`, `INFO`. Both services log to stderr at this level: one line per outbox command outcome (`completed` / `failed, will retry` / `dead-lettered`, with command type, id, business and attempt), a per-batch summary, and worker start/stop. Errors are the adapters' sanitized text; unknown level names fall back to `INFO` |
| `GVAS_SLACK_SIGNING_SECRET` | Required; request signature verification |
| `GVAS_SLACK_BOT_TOKEN` | Required; sent only as a bearer header |
| `GVAS_SLACK_INSTALLATIONS` | Required; `team=business_uuid:owner_user_id` |
| `GVAS_SLACK_EVENTS_PATH` | Default `/slack/events` |
| `GVAS_SLACK_REQUEST_MAX_AGE_SECONDS` | Default 300 |
| `GVAS_SLACK_ATTACHMENT_MAX_BYTES` | Default 25 MiB; caps voice note downloads |
| `GVAS_SLACK_API_BASE_URL`, `GVAS_SLACK_API_TIMEOUT_SECONDS` | Defaults suffice |
| `GVAS_OPENAI_API_KEY` | Required; transcription, contradiction review, evidence annotation |
| `GVAS_OPENAI_TRANSCRIPTION_MODEL` | Default `whisper-1` |
| `GVAS_OPENAI_REVIEW_MODEL` | Default `gpt-5.6-luna` |
| `GVAS_OPENAI_MAX_AUDIO_BYTES`, `GVAS_OPENAI_TIMEOUT_SECONDS` | Defaults suffice |
| `GVAS_RESEND_API_KEY` | Required; approved quote email and `send report to <address>` |
| `GVAS_RESEND_FROM_ADDRESS` | Required; verified sending domain |
| `GVAS_RESEND_REPLY_TO_ADDRESS` | Optional |
| `GVAS_RESEND_PORTAL_URL` | Default `https://gudvector.com/portal/login` |
| `GVAS_WORKER_BATCH_SIZE`, `_POLL_SECONDS`, `_RETRY_SECONDS`, `_LEASE_SECONDS` | Worker only |
| `GVAS_WORKER_ID_PREFIX` | Worker only; hostname and pid are appended per replica |
| `GVAS_R2_*` | Present in `.env.example` but not read by production wiring yet; the DOCX is not kept in object storage until it is |

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
- **Exhausted retries**: when a command dies, one notice is posted in the
  originating thread with recovery guidance for that kind of failure — start a
  new quote in a new thread, or `close notes` and re-upload the recording in a
  new thread. Provider adapters sanitize their errors before raising, so the
  outbox record keeps a provider-neutral failure message; raw provider
  responses, private file URLs and credentials are never stored or logged.
- **Review and reporting are deterministic, guarded by one model pass**:
  `MarkerCompletenessReviewer` decides which checklist items are missing;
  once it reports the note complete, `OpenAIContradictionGuard` (chat
  completions, `GVAS_OPENAI_REVIEW_MODEL`, default `gpt-5.6-luna`) runs a
  focused hard-contradiction pass and a conflict becomes one follow-up
  question. `MarkerChecklistEvidenceAttributor` decides which checklist items
  are satisfied; `OpenAIChecklistEvidenceAnnotator` (same model) then attaches
  supporting note excerpts to those items only, every excerpt is checked to be
  a verbatim substring of the note, and any model failure is logged and the
  marker evidence stands alone. `DeterministicReportGenerator` remains in
  place of an inference provider; a model for it replaces the port
  implementation in `gvas.composition.production` and nothing else.
