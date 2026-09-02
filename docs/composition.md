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
  failure_notices.py     one sanitized owner notice per dead-lettered command
  report_delivery.py     report text posted into the originating thread
  report_publication.py  approved report version -> DOCX -> object storage -> thread
  production.py          the deployment's concrete providers and settings
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
| `field_notes_report.publish` | `PublishFieldNotesReportService` | `ReportArtifactRendererPort`, `ObjectStoragePort` (optional), `OwnerReplyPort` |
| `plan_set.copy_into_custody` | `CopyPlanSetIntoCustodyService` (only when `object_storage` and `source_attachments` are supplied) | `AttachmentAccessPort`, `ObjectStoragePort` |

`field_note.review` and `field_notes_report.generate` are new composition
commands with deterministic UUIDv5 IDs and business-scoped deduplication keys
(`field_note_review:<case>:<trigger>:<key>`, `field_notes_report:<case>:<review>`),
so a replayed inbound message or a retried transcription cannot duplicate review
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
  -> report text posted into the originating thread for the owner to review
  -> owner sends `approve report`
  -> field_notes_report.publish pinned to that exact report version
  -> DOCX rendered, kept in object storage when configured, shared as a file
     into the same channel/thread
  -> case stays open until the owner sends `close notes`
```

Report snapshots are assembled only from persisted evidence: the review's
checklist version, its correlated answers, and the canonical transcript.

A case is reviewed once per transcript revision: `get_or_create` keys a review on
the inbound message plus a SHA-256 fingerprint of the transcript it reviews, so
the same transcript always resolves to the same review, and content added to an
open case after a completed review opens the next revision. Snapshots use the
transcript the review ran against rather than the live canonical transcript, so a
retried report command cannot silently change its own source.

The review commit and the report enqueue are separate transactions, so review
coordination requests the report for an already-complete review too. The report
command's id and dedup key are derived from the case and the reviewed revision —
stable persisted data, never wall-clock time — so the recovery path enqueues at
most one report command per reviewed revision, and each new completed revision
deterministically produces the next `FieldNotesReportVersion`.

## Report review and publication (decided)

The text report in the thread is the review step: the technician or owner reads
it there and can add notes to the open case, which produces the next version.
Nothing leaves the thread until the owner types `approve report`. Approval
resolves the latest completed report version for the conversation's active case
and enqueues `field_notes_report.publish`, whose command id and dedup key derive
from that report version id plus the approving message key: a redelivered
approval collapses to one command, while a fresh `approve report` is a new
attempt that recovers a dead-lettered publish. The published outbound message is
correlated on the version alone, so repeated approvals still yield one posted
document. Publication renders the pinned version — never
the newest one — into a generic editable DOCX with the standard library alone
(`DocxReportRenderer`; letterhead templates are a documented follow-up), stores
it under the business's object-storage prefix when a store is wired, and hands
the owner-reply outbox an attachment whose opaque locator names the version.
`SlackOwnerReplyAdapter` shares attachment parts as real files through Slack's
external upload flow when an uploader and attachment source are composed, with
the text parts as the file comment; `ReportArtifactAccess` serves those bytes by
re-rendering the persisted version, which is byte-for-byte reproducible. The
channel is the system of record: nothing is emailed by default, and `approve
report` with no active case or no completed report replies with what is missing
instead of failing. Publication does not close the case.

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
still extend the same case and are reviewed against the updated canonical
transcript, producing the next report version once complete; after closure, the
next `field notes:` message starts a new case.

## One workflow per conversation (decided)

A conversation runs one workflow. A `field notes:` trigger while a quote is
active, a `quote:` trigger while a field-note case is open, and a conversation
that carries both resolve to `workflow.conflict`, whose handler creates no second
workflow and replies through `OwnerReplyPort` and the outbox. The reply says the
conversation is reserved for its active workflow and asks the owner to start the
other one in a separate thread or conversation; the active workflow is left
running and never has to be closed first. Intent resolution never guesses a
precedence and never switches workflows silently.

`close notes` is a field-note command, so in a quote-only conversation it returns
the same cross-workflow guidance instead of hijacking the quote conversation into
the field-note handler. When a legacy conversation carries both an active quote
and an open case, `close notes` is allowed through to the field-note workflow so
the owner can repair that state.

A message that carries no trigger and lands in a conversation with no active
workflow resolves to `message.unmatched`, whose handler replies once with the
available triggers (`quote:`, `field notes:`, `approve report`, `close notes`)
and starts nothing. Not matching is a property of the message, so it is not a
retryable failure; `IntentUnresolvedError` is reserved for resolver faults
(unpersisted or ambiguous rows) that a retry can fix.

## Where production adapters plug in

Each port has exactly one implementation site, all outside domain and
application:

- `OwnerReplyPort` — `gvas.infrastructure.slack` (`SlackWebApiChatPoster` and
  `SlackWebApiFileUploader`). `build_slack_owner_reply_adapter` requires an
  explicit `SlackDeliveryLedger` (`SqlChannelDeliveryLedger` in production);
  there is no in-memory default, because outbox retries can be claimed by any
  worker process.
- `CustomerQuoteDeliveryPort` — `ResendQuoteDeliveryAdapter` (email only).
- `TranscriptionPort` — `OpenAITranscriber`; `AttachmentAccessPort` —
  `SlackFileAttachmentAccess` for voice notes, `ReportArtifactAccess` for the
  DOCX the owner-reply adapter attaches.
- `CompletenessReviewPort` — `GuardedCompletenessReviewer` wrapping the marker
  reviewer and `OpenAIContradictionGuard`. `ChecklistEvidencePort`,
  `ReportGenerationPort`, `QuoteDraftingPort` — deterministic implementations;
  a model replaces them here and nowhere else.
- `ObjectStoragePort` — `R2ObjectStorage` exists but is **not wired** in
  `gvas.composition.production`, so published DOCX files currently live only in
  Slack and are re-rendered from the persisted report on demand.
- `IntentResolutionPort` — `DeterministicIntentResolver` today; a future
  classifier can replace or precede it.

`gvas.interfaces.worker` runs `worker.run_once()` in a poll loop for the
deployed worker service; tests call `drain()` directly.

## Documented gaps at the neutral boundary

These need a product or provider decision and are wired only up to the port:

1. **Checklist evidence for satisfied items.** Completeness review reports only
   what is missing, so evidence for satisfied items is attributed through
   `ChecklistEvidencePort`. The in-repo attributor is marker-based, mirroring
   `MarkerCompletenessReviewer`; an AI provider decision is still open.
2. **Report distribution beyond the channel.** The approved DOCX lands in the
   originating channel; email to a client or office inbox is opt-in and has no
   command or recipient contract yet. Owner-supplied letterhead templates are
   designed in [`docs/templates_and_site_plans.md`](templates_and_site_plans.md).
3. **Permanent failure notices.** A command that exhausts its retries enqueues
   one sanitized notice into its conversation via
   `NotifyExhaustedCommandService`, with per-command recovery guidance
   (`FAILURE_GUIDANCE`): re-send, add a note, approve again, or start a fresh
   case/quote in a new thread. Owner-reply delivery is excluded so a broken
   channel cannot notify about itself. Operator-facing alerting (beyond logs and
   the dead outbox rows) remains open.
4. **Report and transcript retention.** Unchanged from the accepted
   workstreams: retention, redaction, and cost ceilings remain open.
5. **Per-business templates and plan artifacts.** Composition hardcodes no
   industry: checklists are per-business, keyed, and versioned rows, and
   `build_application` takes `checklist_key` as an argument, so a future
   template resolver can select note/checklist/report templates per business
   without touching domain code. Tenant/site-scoped building-plan artifacts and
   evidence-linked annotations have no accepted contract yet; they would attach
   to checklist evidence and report snapshots, and are out of scope here. A
   design proposal for both, with the owner decisions it still needs, is in
   [`docs/templates_and_site_plans.md`](templates_and_site_plans.md).
