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
  -> short ingress transaction
  -> owner_message.process outbox command
  -> application processing
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
`IntentResolutionPort` during processing, after ingress has committed. The
default `UnconfiguredIntentResolver` leaves processing non-terminal and
resumable until a resolver is configured.

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

Ingress persists exactly one `owner_message.process` command for each new inbound
message. Processing runs outside a database transaction while resolving intent
and invoking a handler; only short load/claim and persist transactions are
allowed. Every handler reply is persisted as one `outbound_messages` row and
exactly one `owner_reply.deliver` outbox command in the persistence transaction.
The command contains the outbound message ID, uses a deterministic UUIDv5
command ID, and has a unique `owner_reply:<message-id>` deduplication key.
Workflow handlers may return custom commands, but both framework command types
are reserved.

The `owner_message.process` command has a structural, nullable inbound-message
link in addition to its payload copy. Its composite business-scoped foreign key
and unique constraint ensure one process command per inbound message and
cascade the command when that inbound message is deleted. The outbox command
also has a nullable foreign key to `outbound_messages.id` with `CASCADE` delete
and a unique constraint, so an outbound reply has at most one delivery command
while custom commands remain unlinked.
Handlers must choose a deterministic `correlation_id` for a given inbound
message. It is the replay key for get-or-create outbound replies.

Deleting an inbound message now cascades to its outbound replies, its process
command, and (through the replies) their linked reply commands. This retention
behavior follows from the required business-scoped composite foreign keys:
they enforce that no reference can cross tenant boundaries while preserving the
cascade chain.

## Persistence

`0001_initial_shared_records` is the only unreleased migration and is kept in
agreement with `infrastructure/models.py`.

| table | ownership and key columns |
| --- | --- |
| `businesses` | business identity, unique slug, name, timestamps |
| `owner_channel_endpoints` | business, opaque source namespace, external endpoint ID, optional owner metadata, routing |
| `conversations` | required endpoint, external conversation ID, routing; unique per endpoint |
| `inbound_messages` | required endpoint and conversation, message key, normalized content, envelope routing; unique per endpoint |
| `outbound_messages` | business/conversation correlation, replay key, content, delivery status and receipt metadata |
| `workflow_runs` | optional resolved intent, inbound message, execution status, attempts, and current processing lease; one run per inbound |
| `outbox_messages` | custom, owner-reply, or owner-message-process command, retry state, lock metadata, and optional structural message link |

Identity is endpoint-scoped. Two endpoints belonging to one business may use
the same external conversation and message keys without deduplicating each
other. Endpoint, conversation, inbound, and outbound delivery repositories
return typed domain read models.

## Application services

`IngestOwnerMessageService` performs only the short ingress transaction:
upserting the endpoint and endpoint-scoped conversation, inserting the inbound
envelope idempotently, and enqueueing one `owner_message.process` command.
Duplicate inbound inserts explicitly roll back. `ProcessOwnerMessageService`
claims the one workflow run per inbound message, closes the UoW before intent
resolution and handler execution, then persists the intent, replay-safe replies,
custom commands, and terminal status in a second short transaction. Claims use a
caller-supplied `now` and `stale_before`: a recent `RUNNING` lease returns
`BUSY` without resolving or invoking the handler, while a missing or stale lease
can be reclaimed. There is no default lease duration or stale cutoff. Intent
resolution failures leave the run `RUNNING` with its error for later retry;
unknown intents and handler failures are durable failed outcomes. Provider or
AI calls never happen while a UoW is open.

The lease makes resolver and handler execution exclusive, but it does not fence
the second persistence transaction against the lease that was granted. If a
lease becomes stale and another worker reclaims it, both workers can write
final run state, so terminal status and error are last-writer-wins. Replies and
their delivery commands remain safe because their `(inbound_message_id,
correlation_id)` and outbox-link uniqueness make durable output idempotent; only
the run's final status can be overwritten. A fencing token based on
`leased_at` would address this if the limitation becomes material, but Round 1
does not implement fencing.

`OutboxService` enqueues commands, claims available rows with worker identity,
increments attempts at claim time, and computes retry availability from an
explicit timezone-aware current time. Its caller-supplied `stale_before` allows
reclaiming abandoned in-progress leases. A future dispatcher must still
dead-letter commands that exhaust `max_attempts`; otherwise a poison command can
be reclaimed forever. No dispatcher or external delivery implementation exists
in Round 1.

Custom outbox deduplication is business-scoped: the same deduplication key can
be used independently by different businesses, while repeated commands for one
business are collapsed. Endpoint and conversation references are also
business-consistent. Composite business-scoped foreign keys and repository
checks prevent conversations, inbound messages, workflow runs, outbound
messages, and linked outbox commands from crossing tenant boundaries.

## HTTP and composition

`create_app()` exposes only `GET /healthz`; module-level `app` remains available
for Uvicorn. `build_application()` accepts an optional intent resolver and
defaults to `UnconfiguredIntentResolver`.
