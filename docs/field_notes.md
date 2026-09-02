# Field-note intake and transcription

Field-note capture is selected deterministically when the first content part
starts with `field notes:`. The prefix is removed and the remaining normalized
parts are persisted in message order. Replies in a conversation are appended
to the open case recorded in conversation state.

Intake resolves the persisted message location in a short read-only lookup,
then records the case, parts, active-case state, acknowledgement, and
transcription commands in one short transaction. Text and unsupported media
are terminal at intake; audio starts in `pending`.

Transcription claims use the business ID and part ID together with a fenced
lease. The claim transaction commits before the transcription port is called.
A separate transaction records success or failure only when the lease token is
still current. Failed work is retryable
and a succeeded part is terminal, preventing duplicate transcription calls.

Canonical transcripts preserve part sequence. Text and successful, non-blank
transcriptions become segments; pending and failed audio are omitted and
counted, while unsupported media is omitted and counted separately.

## States and ordering

Cases are `open` or `closed`; this workstream only creates and appends to open
cases. Parts use monotonic per-case sequences, and replaying the same inbound
delivery does not create additional parts or commands. Active case state is
scoped by the persisted conversation ID.

## Composed chain

The dispatcher path from intake through canonical transcript, completeness
review, follow-up correlation, and report generation is documented in
[`docs/composition.md`](composition.md), including the deterministic
`field_note.review` and `field_notes_report.generate` commands and the gaps that
remain at the neutral boundary. The generated report is posted as text into the
same thread for review; `approve report` then publishes that exact version as an
editable DOCX into the channel (`field_notes_report.publish`), `send report to
<address>` emails that same published DOCX to one typed address on request
(`field_notes_report.email`; never automatic, and refused with a pointer to
`approve report` until the document is in the thread), and `close notes`
remains the only command that closes the case.

A PDF or image uploaded into an open case thread is still recorded as an
unsupported note part, and additionally registered as a plan-set upload for
that case and copied into object storage (`plan_set.copy_into_custody`) when
plan custody is wired. Re-uploading the same file is idempotent. When custody is
not wired the thread gets one "plan custody is not enabled" reply and nothing
is enqueued; when a copy dead-letters the owner is told to upload the file again
in the thread.

## Open questions

- How long neutral attachment locators and payloads may be retained, and
  whether transcripts must be redacted or purged with source media.
- Which transcription provider/model, maximum audio duration or size, and
  language handling should be selected. Monthly per-business ceilings exist
  (`GVAS_COST_CEILING_*`, see `docs/deployment.md`); the values to set for the
  pilot are still an owner decision.
- Whether transcription retries should have a ceiling beyond the outbox
  `max_attempts`. A command that exhausts them dead-letters and posts one
  owner-facing notice with recovery guidance (see `docs/composition.md`).
- `WorkflowContext` currently exposes no endpoint-scoped persisted identity,
  so workflows re-resolve it from business, external conversation ID, and
  message key. This can be ambiguous when endpoints reuse those external
  identifiers. A later foundation change should carry resolved conversation
  and inbound-message identity (or an endpoint reference) into
  `WorkflowContext`.
