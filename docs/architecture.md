# Güd Vector Agent Suite — Architecture (Round 1 foundation)

Round 1 is a channel-agnostic modular monolith foundation. It contains no channel
adapters, vendor SDKs, provider calls, worker runtime, quote generation, reports,
payments, hosting integration, or webhook routes.

## Layering

```text
src/gvas/
  domain/          pure contracts, ports, workflow protocol, and router
  application/     ingestion, intent resolution, and outbox services
  infrastructure/  SQLAlchemy models, repositories, UoW, and Alembic
  interfaces/      FastAPI health endpoint
  composition.py   concrete wiring
```

Domain imports only the standard library and Pydantic. Application imports only
the standard library, Pydantic, and `gvas.domain`. Neither layer imports
infrastructure, interfaces, SQLAlchemy, FastAPI, HTTP clients, or vendor SDKs.
The boundary test also rejects channel/provider names in domain and application
source.

## Channel-neutral flow

An adapter translates a provider event into an `InboundOwnerMessage` envelope:

```text
adapter
  -> InboundOwnerMessage(message, endpoint, routing)
  -> application ingestion
  -> IntentResolutionPort.resolve(NormalizedOwnerMessage)
  -> WorkflowContext(run_id, intent, NormalizedOwnerMessage)
  -> handler replies and custom commands
```

`NormalizedOwnerMessage` is the routing-free business view. It contains stable
message identity, conversation, sender, timestamp, and ordered content parts.
`ChannelEndpointRef` contains the adapter-owned opaque `source_namespace` and
`external_endpoint_id`; core code stores and compares these values but never
branches on their contents. `routing` exists only on the ingestion envelope and
in persistence read models needed by a future delivery adapter.

Ingestion upserts the channel endpoint from the adapter-supplied
`ChannelEndpointRef` and stores the message's opaque routing blob as that
endpoint's routing on first sight. A future administrative provisioning flow can
pre-register endpoints with richer routing. `source_namespace` is compared for
identity but is never branched on.

Adapters never assign intent. The application resolves it through
`IntentResolutionPort`. The default `UnconfiguredIntentResolver` records the
inbound message and returns an accepted outcome without creating a workflow run
when no resolver is configured.

## Domain contracts

All domain models are frozen Pydantic models with `extra="forbid"` and immutable
tuple collections. Message timestamps must be timezone-aware. Inbound and
outbound business IDs must match their conversation reference.

`InboundOwnerMessage` has `message: NormalizedOwnerMessage`, a required endpoint
reference, and opaque routing. `OutboundOwnerMessage` has no routing; handlers
cannot inspect or propagate channel routing.

`IntentResolution` carries a `WorkflowIntent`, optional bounded confidence, and
detail. `IntentUnresolvedError` represents an unavailable resolution.

## Replies and outbox commands

Every handler reply is persisted as one `outbound_messages` row and exactly one
`owner_reply.deliver` outbox command in the same unit of work. The command
contains the outbound message ID, uses a deterministic UUIDv5 command ID, and
has a unique `owner_reply:<message-id>` deduplication key. Workflow handlers may
return custom commands, but the reserved owner-reply command type is rejected.

The outbox command has a nullable foreign key to `outbound_messages.id` with
`CASCADE` delete and a unique constraint, so an outbound reply has at most one
delivery command while custom commands remain unlinked.

## Persistence

`0001_initial_shared_records` is the only unreleased migration and is kept in
agreement with `infrastructure/models.py`.

| table | ownership and key columns |
| --- | --- |
| `businesses` | business identity, unique slug, name, timestamps |
| `owner_channel_endpoints` | business, opaque source namespace, external endpoint ID, optional owner metadata, routing |
| `conversations` | required endpoint, external conversation ID, routing; unique per endpoint |
| `inbound_messages` | required endpoint and conversation, message key, normalized content, envelope routing; unique per endpoint |
| `outbound_messages` | business/conversation correlation, content, delivery status and receipt metadata |
| `workflow_runs` | resolved intent, inbound message, execution status and attempts; unique per inbound/intent |
| `outbox_messages` | custom or owner-reply command, retry state, lock metadata, optional outbound link |

Identity is endpoint-scoped. Two endpoints belonging to one business may use
the same external conversation and message keys without deduplicating each
other. Endpoint, conversation, inbound, and outbound delivery repositories
return typed domain read models.

## Application services

`IngestOwnerMessageService` upserts the endpoint, upserts the endpoint-scoped
conversation, inserts the inbound envelope idempotently, resolves intent, then
creates and executes a workflow run. Duplicate inbound inserts explicitly roll
back. Unknown workflow intent creates a failed run with no outputs. Replies and
commands commit atomically.

`OutboxService` enqueues commands, claims available rows with worker identity,
increments attempts at claim time, and computes retry availability from an
explicit timezone-aware current time. No dispatcher or external delivery
implementation exists in Round 1.

Custom outbox deduplication is business-scoped: the same deduplication key can
be used independently by different businesses, while repeated commands for one
business are collapsed. Endpoint and conversation references are also
business-consistent; composite foreign keys prevent a conversation or inbound
message from linking an endpoint owned by another business.

## HTTP and composition

`create_app()` exposes only `GET /healthz`; module-level `app` remains available
for Uvicorn. `build_application()` accepts an optional intent resolver and
defaults to `UnconfiguredIntentResolver`.
