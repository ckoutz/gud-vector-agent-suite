# Composition root and worker dispatch

`gvas.composition` is the only layer allowed to import application,
infrastructure, and interface modules together. It contains no provider
implementation: every external capability is injected as a port through
`ApplicationPorts`, and the repository ships deterministic in-repo fakes for
tests only.

```text
src/gvas/composition/
  __init__.py            ApplicationPorts, Application, build_application
  intents.py             deterministic trigger/state intent resolver
  field_note_workflow.py intake plus follow-up-answer routing
  review.py              transcript -> completeness -> report request
  snapshots.py           completed review -> report snapshot
  dispatcher.py          outbox command routing and worker loop
```

Application and domain modules still import only `gvas.domain` (plus the
standard library and Pydantic); modules that must combine several application
services live here instead.

## Wiring an application

```python
from gvas.composition import ApplicationPorts, build_application

application = build_application(
    ApplicationPorts(
        owner_replies=...,          # OwnerReplyPort
        quote_drafting=...,         # QuoteDraftingPort
        quote_delivery=...,         # CustomerQuoteDeliveryPort
        transcription=...,          # TranscriptionPort
        completeness_review=MarkerCompletenessReviewer(),
        checklist_evidence=MarkerChecklistEvidenceAttributor(),
        report_generation=...,      # ReportGenerationPort
    )
)
```

`build_application` accepts an explicit `engine` or `session_factory` (tests pass
the latter), a `checklist_key`, a `now` callable, and an intent resolver
override. It returns the wired services plus `dispatcher` and `worker`.

The Slack Request URL route is composed separately so no channel name leaks into
the composition root:

```python
from gvas.infrastructure.slack.composition import (
    build_slack_event_router,
    build_slack_owner_reply_adapter,
)
from gvas.interfaces.http.app import create_app

app = create_app(routers=(build_slack_event_router(application.ingest_service),))
```

The route verifies, normalizes, ingests, and enqueues only; every workflow step
and provider call runs from the worker.

## Running the worker with fakes

`tests/composition_fakes.py` holds deterministic fakes for each port and
`application_ports(...)` assembles them. A local dispatcher loop is then:

```python
application = build_application(application_ports(...), session_factory=sessions)
await application.worker.drain()          # claim and dispatch until empty
await application.worker.run_once()       # single batch
```

`OutboxWorker.run_once` claims a batch with a worker identity and stale-lease
cutoff, dispatches each record, marks successes, and marks failures with a retry
delay so the existing attempt/dead-letter behaviour applies unchanged. Commands
with no registered handler raise `UnknownCommandTypeError` and stay retryable
until `max_attempts` is exhausted.

## Dispatched command types

| command | service | provider port |
| --- | --- | --- |
| `owner_message.process` | `ProcessOwnerMessageService` | intent resolver |
| `owner_reply.deliver` | `DeliverOwnerReplyService` | `OwnerReplyPort` |
| `customer_quote.deliver` | `DeliverApprovedQuoteService` | `CustomerQuoteDeliveryPort` |
| `field_note.transcribe` | `TranscribeFieldNoteAudioService` | `TranscriptionPort` |
| `field_note.review` | `CoordinateFieldNoteReviewService` | `CompletenessReviewPort` |
| `field_notes_report.generate` | snapshot builder + `GenerateFieldNotesReportService` | `ChecklistEvidencePort`, `ReportGenerationPort` |

`field_note.review` and `field_notes_report.generate` are new composition
commands with deterministic UUIDv5 IDs and business-scoped deduplication keys
(`field_note_review:<case>:<trigger>:<key>`, `field_notes_report:<case>`), so a
replayed inbound message or a retried transcription cannot duplicate review
rounds, owner questions, or report versions.

## Field-note chain

```text
field notes: inbound
  -> intake (case, parts, conversation state)
  -> field_note.transcribe per audio part (provider call after the claim commits)
  -> field_note.review
  -> canonical transcript (blocked while audio is pending or failed)
  -> completeness review, one persisted ASKED question at a time
  -> owner reply routed back to the same review by persisted correlation
  -> field_notes_report.generate on COMPLETE or ALREADY_COMPLETE
  -> snapshot from persisted review, checklist, and answers
  -> report version
  -> case stays open until the owner sends `close notes`
```

Report snapshots are assembled only from persisted evidence: the review's
checklist version, its correlated answers, and the canonical transcript.

The review commit and the report enqueue are separate transactions, so review
coordination requests the report for an already-complete review too. The report
command's id and dedup key are derived from the case, so the recovery path
enqueues at most one report command per case.

## Case closure is explicit (decided)

Neither a completed review nor a generated report closes a case. A case closes
only when the owner sends `close notes`, matched channel-neutrally and
case-insensitively with surrounding whitespace tolerated, in the same
deterministic style as `field notes:` and `quote:`. Closure runs in one
transaction: it stamps `field_note_cases.closed_at`, sets the case status to
`closed`, and clears the conversation's active case, all scoped by
`business_id`. It is idempotent — a second `close notes`, or a replayed inbound
event, closes nothing again, and the owner reply carries a deterministic
correlation ID (`field_note.close:<message key>`) so no duplicate reply is
persisted or delivered. `close notes` with no active case replies that there is
nothing open instead of failing. While a case is open, notes after a report
still extend the same case; after closure, the next `field notes:` message
starts a new case. Report requests stay keyed on the case, so extending a case
does not produce another report version.

## One workflow per conversation (decided)

A conversation runs one workflow. A `field notes:` trigger while a quote is
active, a `quote:` trigger while a field-note case is open, and a conversation
that carries both resolve to `workflow.conflict`, whose handler creates no second
workflow and replies through `OwnerReplyPort` and the outbox, asking the owner to
use a separate thread or conversation. Intent resolution never guesses a
precedence and never switches workflows silently.

## Where production adapters plug in

Each port has exactly one implementation site, all outside domain and
application:

- `OwnerReplyPort` — `gvas.infrastructure.slack` (owner chat channel).
  `build_slack_owner_reply_adapter` requires an explicit `SlackDeliveryLedger`;
  there is no in-memory default, because outbox retries can be claimed by any
  worker process. Until a production poster exists, Slack delivery is deduped
  only within one process: the future poster must honor
  `SlackChatPostRequest.idempotency_key`, and deployments must inject a durable
  shared ledger.
- `CustomerQuoteDeliveryPort` — future email adapter, or the future SMS/voice
  adapter (Telnyx) for text delivery.
- `TranscriptionPort` and `AttachmentAccessPort` — future media adapters.
- `CompletenessReviewPort`, `ChecklistEvidencePort`, `ReportGenerationPort`,
  `QuoteDraftingPort` — future AI adapters.
- `IntentResolutionPort` — `DeterministicIntentResolver` today; a future
  classifier can replace or precede it.

No hosting, queue runtime, or scheduler is selected: something must call
`worker.run_once()`/`drain()`.

## Documented gaps at the neutral boundary

These need a product or provider decision and are wired only up to the port:

1. **Checklist evidence for satisfied items.** Completeness review reports only
   what is missing, so evidence for satisfied items is attributed through
   `ChecklistEvidencePort`. The in-repo attributor is marker-based, mirroring
   `MarkerCompletenessReviewer`; an AI provider decision is still open.
2. **Report distribution.** Report versions are persisted; no recipient,
   channel, or delivery command exists yet.
3. **Permanent transcription or review failure.** Failures retry through the
   outbox and eventually dead-letter; no owner-facing failure message is
   defined.
4. **Report and transcript retention.** Unchanged from the accepted
   workstreams: retention, redaction, and cost ceilings remain open.
5. **Per-business templates and plan artifacts.** Composition hardcodes no
   industry: checklists are per-business, keyed, and versioned rows, and
   `build_application` takes `checklist_key` as an argument, so a future
   template resolver can select note/checklist/report templates per business
   without touching domain code. Tenant/site-scoped building-plan artifacts and
   evidence-linked annotations have no accepted contract yet; they would attach
   to checklist evidence and report snapshots, and are out of scope here.
