# Güd Vector Agent Suite — Architecture (Round 1 foundation)

Round 1 delivers a channel-agnostic foundation only. No channel adapter (Slack, Twilio),
no quote generation, no transcription/delivery provider, no report generation.

## Layering

```
src/gvas/
  domain/          pure, channel-neutral contracts, ports, workflow protocol + router
  application/     use cases: ingestion, outbox service (depend on domain only)
  infrastructure/  SQLAlchemy models, repositories, unit of work, alembic migrations
  interfaces/      FastAPI app + health endpoint
  config.py        pydantic-settings
  composition.py   wiring of concrete implementations into use cases
```

Dependency rule (enforced by `tests/test_architecture_boundaries.py`):

- `gvas.domain` may import stdlib + pydantic only.
- `gvas.application` may import stdlib + pydantic + `gvas.domain`.
- Neither may import `gvas.infrastructure`, `gvas.interfaces`, `sqlalchemy`, `fastapi`,
  any HTTP client, or any vendor SDK (`slack_sdk`, `twilio`, `openai`, `boto3`, ...).
- The literal substrings `slack` and `twilio` (case-insensitive) must not appear anywhere
  in `gvas/domain` or `gvas/application` sources.

## Channel neutrality

A channel adapter is a transport translator. It converts a provider webhook into an
`InboundOwnerMessage` and nothing else. Everything transport-specific is confined to:

- `routing: RoutingData` — an opaque, JSON-serializable mapping that core code stores and
  passes through verbatim and never inspects. It is the only place a transport identifier
  may live (`owner_channel_endpoints.transport` in the DB is infrastructure routing data).
- `AttachmentReference.locator` — an opaque adapter-scoped token. Domain objects must never
  contain provider URLs or tokens; the model rejects locators that look like URLs, and
  binary retrieval goes through `AttachmentAccessPort`.

Workflow selection uses `WorkflowIntent` (an explicit, open string identifier) and never the
transport. The same normalized message produces identical behavior whether its fixture is
labeled Slack or Twilio (`tests/test_workflow_routing.py`).

## Domain contracts (`gvas.domain`)

`identifiers.py`
- `BusinessId`, `ConversationId`, `MessageId`, `WorkflowRunId`, `OutboxCommandId` (UUID newtypes)
- `MessageKey` (str newtype) — transport-supplied stable idempotency key
- `WorkflowIntent` (str newtype), `RoutingData = Mapping[str, JsonValue]`

`enums.py`
- `SenderRole` = `owner | teammate | system`
- `MediaKind` = `audio | image | document | video | other`
- `DeliveryStatus` = `accepted | delivered | failed`
- `RecipientAddressKind` = `email | phone | link`
- `WorkflowRunStatus` = `pending | running | succeeded | failed`
- `OutboxStatus` = `pending | in_progress | succeeded | failed | dead`

`messages.py` (frozen Pydantic v2 models, `extra="forbid"`)
- `AttachmentReference(attachment_id, media_kind, locator, mime_type?, filename?, byte_size?)`
- `TextPart(kind="text", text)` / `AttachmentPart(kind="attachment", attachment)`;
  `ContentPart` is the discriminated union on `kind`
- `SenderRef(external_id, role)`
- `ConversationRef(business_id, external_conversation_id)`
- `ReplyRef(correlation_id, external_message_id?)`
- `InboundOwnerMessage(message_key, business_id, conversation_ref, sender, received_at,
   parts (non-empty, ordered tuple), intent, reply_to?, routing)`
- `OutboundOwnerMessage(business_id, conversation_ref, parts (non-empty tuple), correlation_id,
   reply_to?, routing)`
- `DeliveryReceipt(status, provider_message_id?, occurred_at, detail?)`
- `received_at`/timestamps must be timezone-aware.

`ports.py` (async `Protocol`s)
- `OwnerReplyPort.send(conversation_ref: ConversationRef, message: OutboundOwnerMessage) -> DeliveryReceipt`
- `AttachmentAccessPort.fetch(attachment: AttachmentReference) -> AttachmentPayload`
- `TranscriptionPort.transcribe(audio: AudioReference) -> TranscriptResult`
  (`AudioReference` wraps an `AttachmentReference` validated to `MediaKind.AUDIO`;
  `TranscriptResult(text, language?, confidence?, duration_seconds?, provider_ref?)`)
- `CustomerQuoteDeliveryPort.deliver(request: CustomerDeliveryRequest) -> DeliveryReceipt`
  (`CustomerDeliveryRequest(business_id, recipient: CustomerRecipient(address, address_kind,
  display_name?), subject?, body_text, links, attachments)` — hosted links only, never
  payment credentials)

`workflows.py` / `routing.py`
- `WorkflowContext(run_id, message)`
- `WorkflowResult(status: WorkflowRunStatus, replies, commands, detail?)`; collection fields
  are immutable tuples.
- `WorkflowHandler` protocol: `intent: WorkflowIntent`, `async handle(context) -> WorkflowResult`
- `WorkflowRouter(handlers)`: `route(intent) -> WorkflowHandler`, raises `UnknownWorkflowIntentError`

`outbox.py`
- `OutboxCommand(command_id, business_id, command_type, payload, dedup_key?)`
- `OutboxRecord(..., status, attempts, max_attempts, available_at, last_error?)` with
  validated transitions; `InvalidOutboxTransitionError`

`repositories.py` (Protocols)
- `BusinessRepository`, `OwnerChannelEndpointRepository`, `ConversationRepository`,
  `InboundMessageRepository`, `OutboundMessageRepository`, `WorkflowRunRepository`,
  `OutboxRepository`, and `UnitOfWork` (async context manager exposing the repositories,
  `commit()`, `rollback()`; rollback on exception, no implicit commit).
- `BusinessRecord(business_id, slug, name)` and
  `OwnerChannelEndpointRecord(endpoint_id, business_id, owner_external_id, routing)` are
  the typed read models returned by the corresponding repositories.
  `ConversationRepository.get_or_create` accepts `(reference, routing, endpoint_id=None)`.

## Application services

`IngestOwnerMessageService.ingest(message) -> IngestionOutcome`
1. Open unit of work.
2. Insert inbound message keyed by `(business_id, message_key)`. If it already exists →
   return `IngestionOutcome(status=DUPLICATE, ...)` with no workflow run, no outbox rows,
   no replies.
3. Upsert conversation, create a `running` `workflow_runs` row (unique on
   `(inbound_message_id, intent)`),
   run the routed handler, persist replies to `outbound_messages`, persist
   `WorkflowResult.commands` to the outbox, commit once.
4. Unknown intent → run recorded as `failed`, no replies/commands, no exception escape.

Replies are persisted as outbound message rows plus outbox commands; actual sending through
`OwnerReplyPort` is a Round 2 dispatcher concern.

`OutboxService`: `enqueue`, `claim_batch(limit, now, claimed_by)`, `mark_succeeded`,
`mark_failed(retry_in, error, now)`, `mark_dead`. Retry metadata (`attempts`, `max_attempts`,
`available_at`, `last_error`) lives on the row; attempts increment at claim time, and retry
availability is computed from the supplied current time. No external queue or provider call
exists in this round.

## Persistence (migration `0001_initial_shared_records`)

Shared records only — quote and field-note tables are deliberately out of scope.

| table | key columns |
| --- | --- |
| `businesses` | `id`, `slug` (unique), `name`, timestamps |
| `owner_channel_endpoints` | `business_id`, `transport`, `external_endpoint_id`, `owner_external_id`, `routing`, unique `(transport, external_endpoint_id)` |
| `conversations` | `business_id`, `endpoint_id`, `external_conversation_id`, `routing`, unique `(business_id, external_conversation_id)` |
| `inbound_messages` | `business_id`, `conversation_id`, `message_key`, `sender_external_id`, `sender_role`, `intent`, `received_at`, `parts`, `reply_to`, `routing`, unique `(business_id, message_key)` |
| `outbound_messages` | `business_id`, `conversation_id`, `inbound_message_id?`, `parts`, `reply_to`, `routing`, `status`, `provider_message_id?`, `correlation_id` |
| `workflow_runs` | `business_id`, `inbound_message_id`, `intent`, `status`, `attempts`, `started_at`, `finished_at?`, `error?`, unique `(inbound_message_id, intent)` |
| `outbox_messages` | `business_id`, `command_type`, `payload`, `status`, `attempts`, `max_attempts`, `available_at`, `last_error?`, `dedup_key` (unique, nullable), `locked_at?`, `locked_by?` |

Portability: `sqlalchemy.Uuid`, `DateTime(timezone=True)`, and `JSON().with_variant(JSONB, "postgresql")`
so the same models run on PostgreSQL (production, CI migration check) and SQLite (unit tests).

## Configuration & composition

`Settings` (pydantic-settings, `GVAS_` prefix): `app_env`, `log_level`, `database_url`.
`.env.example` holds names/placeholders only. `composition.py` builds the engine,
session factory, unit-of-work factory, workflow router, and ingestion service.
`interfaces/http/app.py` exposes `GET /healthz` only — no channel webhook routes.
