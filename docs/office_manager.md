# Design: the office manager (one owner assistant over Slack and SMS)

Status: **design only**. Nothing here is implemented. The Pydantic sketches are
proposals to be reviewed; none of them is wired into a workflow, migration, or
composition path. Decisions marked **needs decision** block the phase that
depends on them and are not built until answered.

## 0. The ask

> One agent with access to all information intake through the whole process.
> I message it and it updates my Calendly, cancels things, reschedules things,
> adds new customers, pauses services — anything that can be done, the agent
> can do, through Slack or text. Basically an office manager I can talk to.

## 1. What exists today

The owner already talks to GVAS from one Slack workspace and one Telnyx number,
and every message from either lands in the same channel-neutral pipeline
(`docs/architecture.md`): adapter → `InboundOwnerMessage` → `owner_message.process`
outbox command → `IntentResolutionPort.resolve` → one workflow handler → replies
and custom commands, all replay-safe and lease-fenced.

What is *not* one agent is the front of that pipeline. `DeterministicIntentResolver`
(`composition/intents.py`) matches keyword triggers — `quote:`, `notes:`,
`approve booking <ref>`, `send report to …`, `close notes` — and each trigger
owns its own handler and its own conversation. A message that matches nothing
gets the trigger list back (`message.unmatched`). SMS is further scoped by
`ChannelWorkflowPolicy` to quotes and booking decisions only.

Everything the office manager needs to *act on* already has a port or a
repository, and all of it is tenant-scoped by `business_id`:

| Capability | Where it lives today | Read | Write |
| --- | --- | --- | --- |
| Calendar: scheduled events, invitees | `infrastructure/calendly/api.py` (`find`) | yes | — |
| Calendar: availability, book, cancel | `infrastructure/calendly/availability.py` (`available_slots`, `book`, `cancel_booking`) | yes | book, cancel |
| Calendar: inbound event changes | `POST /calendly/events` webhook (`calendly/ingress.py`) | yes | — |
| Customers `(business, email)` | `domain/customers.py` `CustomerRepository` (`find_by_email`, `upsert`) | yes | upsert |
| Quotes: draft, approve, send, hosted link | `application/quotes.py`, `public_quotes.py` | yes | yes |
| Recurring services | `QuoteSubscriptionRecord` + Stripe webhooks; Stripe API adapter has checkout/customer/billing-portal only | yes | **no pause/resume/cancel** |
| Booking requests from the website widget | `application/intake.py`, `IntakeConversation` (`awaiting_owner` → approve/decline) | yes | approve, decline |
| Service requests from the portal | `ServiceRequestRepository` | yes (add only) | — |
| Field-note cases and reports | `application/field_notes.py` and friends | yes | yes |
| Owner-facing model calls, spend caps | `openai_intake_agent.py`, `UsageLedgerPort`, `GVAS_COST_CEILING_*` | — | — |

So the gap is (a) a natural-language front end that maps "move Jane to Friday"
onto those capabilities, (b) a safe way to let it *write*, and (c) four small
capability gaps (§4).

## 2. Shape of the change

One new workflow, `office.assist`, becomes the **default** intent: anything the
deterministic resolver would today send to `message.unmatched` goes to the
office manager instead. Existing triggers keep working unchanged (they are
faster, cheaper and already tested); the assistant is the fallback that makes
the trigger list unnecessary to remember. Over time individual triggers can be
retired once the assistant covers them, but nothing in this design removes one.

```text
owner message (Slack thread or SMS)
  -> DeterministicIntentResolver          existing triggers win, unchanged
  -> office.assist (fallback)             NEW: was message.unmatched
       -> OfficeAssistantPort.plan(...)   one model call: read the message +
                                          conversation memory, return either a
                                          direct answer or a proposed action
       -> read tools run inline           "who's on tomorrow?" answers now
       -> write tools become a            "Cancel Jane Thu 2pm? yes/no"
          PendingAction awaiting "yes"
  -> owner replies "yes" / "no" / edits   resolver sees an open PendingAction
                                          in this conversation -> office.confirm
       -> the action runs as its own outbox command (retry, idempotent, logged)
       -> owner gets one outcome reply
```

The assistant is **one identity across channels**. Memory is per business, not
per channel: a request started over SMS can be confirmed from Slack, because
`PendingAction` is keyed on `business_id` + reference, not on the conversation
(§3.3). Replies are shaped per channel by the existing outbound delivery
adapters (Slack gets threads and formatting; SMS gets ≤ 320 chars and a short
reference).

### 2.1 Why "propose then confirm", not "just do it"

- SMS has no undo, no thread, and autocorrect. A wrong "cancel" reaches a
  customer.
- The quote and booking workflows already established the pattern
  (`approve quote`, `approve booking <ref>`); owners know it.
- The model never holds a Calendly or Stripe credential. It emits a typed
  `ProposedAction`; the *service layer* validates it against the database and
  runs the adapter — the same boundary `openai_intake_agent.py` keeps today
  (model returns JSON; the service decides what happens).

Reads (calendar lookups, "what's Jane's status", "how many open quotes") need
no confirmation and answer in one turn.

## 3. Contracts (proposed)

```python
# PROPOSED — not implemented, not wired.

class OfficeToolName(StrEnum):
    # reads
    CALENDAR_LIST = "calendar.list"            # window -> appointments with invitees
    CALENDAR_AVAILABILITY = "calendar.availability"
    CUSTOMER_LOOKUP = "customer.lookup"        # by name fragment / email / phone
    CUSTOMER_HISTORY = "customer.history"      # quotes, bookings, subscriptions, requests
    QUOTES_OPEN = "quotes.open"
    BOOKINGS_PENDING = "bookings.pending"      # intake requests awaiting owner
    # writes (always confirmed)
    CALENDAR_CANCEL = "calendar.cancel"
    CALENDAR_RESCHEDULE = "calendar.reschedule"   # cancel + book, one action
    CALENDAR_BOOK = "calendar.book"
    CUSTOMER_ADD = "customer.add"
    CUSTOMER_UPDATE = "customer.update"           # phone, address
    SERVICE_PAUSE = "service.pause"
    SERVICE_RESUME = "service.resume"
    SERVICE_CANCEL = "service.cancel"
    BOOKING_DECIDE = "booking.decide"             # approve/decline intake request
    CUSTOMER_MESSAGE = "customer.message"         # email/text a customer (templated)


class ProposedAction(OfficeModel):
    tool: OfficeToolName
    arguments: dict[str, str]              # validated per tool by the service, not the model
    confirmation: str = Field(max_length=280)   # the sentence the owner reads before "yes"


class OfficePlan(OfficeModel):
    reply: str = Field(max_length=600)     # what the owner reads now
    reads: tuple[ToolCall, ...] = ()       # executed inline, result fed back for the reply
    action: ProposedAction | None = None   # at most one write per turn
    needs_clarification: bool = False


class PendingAction(OfficeModel):
    action_id: PendingActionId
    business_id: BusinessId
    reference: str                         # short, owner-typable: "A7K2"
    action: ProposedAction
    resolved_arguments: dict[str, str]     # ids the service resolved (event_uri, customer_id, …)
    proposed_in: ConversationRef
    expires_at: datetime                   # default 24h; needs decision
    state: PendingActionState              # proposed | confirmed | declined | expired | done | failed


class OfficeAssistantPort(Protocol):
    async def plan(self, request: OfficeTurnRequest) -> OfficePlan: ...
```

### 3.1 The model call

One chat-completions call per owner message with a strict JSON schema, the same
pattern and module layout as `openai_intake_agent.py`. Context passed in:

- the last N owner/assistant turns in this conversation (bounded);
- the owner's open `PendingAction`s (so "yes", "the second one", "make it 3pm
  instead" resolve);
- a compact **business snapshot** built by the service before the call: next
  7 days of appointments (name, start, event uri hidden behind a short index),
  bookings awaiting the owner, open quotes, active subscriptions. The model
  refers to rows by index; the service maps indices back to ids. The model
  never sees or emits provider URIs.

Read tools it asks for run inline and the model is called once more with the
results (two calls max per turn). Token use is metered through the existing
`UsageLedgerPort` and blocked by the existing review-token ceiling; a held-back
turn gets the same one-line ceiling notice quotes get today.

### 3.2 Validation before anything is proposed

The service, not the model, resolves every reference:

- customer by fragment → exactly one `CustomerRecord`, else a numbered choice
  (reusing the Calendly customer-selection reply from quotes);
- appointment → exactly one scheduled Calendly event in the window, else choice;
- reschedule target → must be in `available_slots` (never a free-typed time);
- subscription → one active `QuoteSubscriptionRecord` for that customer.

If resolution fails the turn ends with a question, not a `PendingAction`.

### 3.3 Confirmation

The reply that carries a `PendingAction` ends with its reference:

> Cancel **Jane Alvarez — Thu Sep 25, 2:00pm** and tell her by email?
> Reply **yes A7K2** or **no A7K2**.

Rules:

- `yes <ref>` / `no <ref>` are exact, deterministic triggers resolved *before*
  the model (like `approve booking <ref>` today). A bare `yes` is accepted only
  when exactly one action is pending for the business.
- Confirmation from another channel is allowed (same business); the outcome is
  replied to **both** the channel that proposed and the one that confirmed.
- An edit ("make it 3pm") declines the pending action and proposes a new one.
- Expiry: **needs decision** — proposal is 24 hours, after which the reference
  is dead and the owner is told once.

### 3.4 Execution

A confirmed action is one outbox command (`office.action.run`) with a
deterministic id derived from `action_id`, so a confirmation is enqueued once.
That only deduplicates the *row*; it says nothing about whether a crashed
worker already made the provider call. Every write tool therefore defines its
own execution idempotency, following `ArrangeIntakeBookingService`
(`application/intake.py`): persist an attempt marker on the `PendingAction`
before the provider call, and on retry **reconcile first** — `find_booking`
for `calendar.book`, the event's `status` for `calendar.cancel`, the Stripe
subscription's current state for `service.*`, `find_by_email` for
`customer.add` — and only call the provider when the reconciled state shows the
write has not happened. Each tool maps to an existing adapter call or a small
new one (§4).
Outcome goes back as one reply; failures are sanitized the way quote drafting
failures are (no provider error text to SMS).

Customer-facing side effects (cancellation email, reschedule text) ride the
existing `intake_customer_email`/`intake_customer_text` command types from the
business's Resend sender and Telnyx number; they are part of the confirmation
sentence so the owner knows the customer will be contacted.

## 4. Capability gaps to fill

| Gap | Size | Notes |
| --- | --- | --- |
| Reschedule | medium | Calendly's API has no "move" write. `calendar.reschedule` = `book` **then** `cancel_booking`, never the other way round: the original event is cancelled only once the replacement is provider-confirmed (`BookingKind.DIRECT`). When the adapter falls back to a scheduling link (`BookingKind.LINK`, plans without direct booking) the original stays on the calendar, the customer gets the prefilled link, and the existing `POST /calendly/events` webhook cancels the original when the replacement event is created — the `PendingAction` stays in `confirmed` until then and expires back to the owner with a notice if the customer never books. |
| Add a customer with no quote | small | `CustomerRepository.upsert` exists; needs an owner-initiated path and an optional phone/address. Sends nothing to the customer unless asked. |
| Pause / resume / cancel a service | medium | Stripe adapter gains `pause_subscription` (`pause_collection`), `resume_subscription`, `cancel_subscription(at_period_end)`. The existing `customer.subscription.updated` webhook already folds the status back into `quote_subscriptions`, so the record stays the source of truth and the reply can quote the new state. |
| Calendar read window with invitee contact | none | `find(window)` returns invitees already; needs a 7-day default window and per-business timezone (open follow-up in the roadmap). |
| Message a customer ad hoc | small | Reuse intake email/text commands; owner text is the body; **no prices** — same `contains_currency_amount` scrub as the intake agent, needs decision whether to hard-refuse or ask the owner to confirm the amount. |

## 5. Guardrails

- **Scope**: only the tools in §3 exist. There is no "run arbitrary API call".
- **Tenant**: every tool takes `business_id` from the message, never from the
  model.
- **Writes**: never without a matching `yes <ref>`. The model cannot emit a
  confirmed action.
- **Provider secrets**: unchanged — stay in adapters behind `ApplicationPorts`.
- **Spend**: `GVAS_COST_CEILING_*` review tokens cover the assistant; a new
  per-business daily turn cap (`GVAS_OFFICE_DAILY_TURN_CAP`, default 200) like
  the intake widget's.
- **Channel**: SMS policy widens from "quotes only" to "quotes, bookings,
  office"; field-note capture stays Slack-only (voice notes, files).
- **Audit**: every `PendingAction` and its outcome is a row; the assistant's
  proposals are reviewable after the fact.

## 6. Phases

Each phase ships behind its own flag and is independently useful.

1. **Read-only office manager** — `office.assist` as fallback intent, business
   snapshot, read tools, clarification replies, SMS widening, turn cap and
   token metering. No writes. *Owner can ask "what's on tomorrow" from either
   channel.*
2. **Calendar writes** — `PendingAction`, `yes/no <ref>`, `office.action.run`,
   `calendar.cancel`, `calendar.reschedule`, `calendar.book`, customer notice
   on cancel/reschedule. Cross-channel confirmation.
3. **Customers and services** — `customer.add/update`, Stripe pause/resume/
   cancel, `service.*` tools, `booking.decide` (so "approve Jane's booking"
   works without the exact trigger), `customer.message`.
4. **Retire triggers** — measure which keyword triggers are still typed; fold
   the unused ones into the assistant and shorten the unmatched reply.

Estimate: phases 1–2 in one build session, phase 3 in a second. Phase 4 is
later and small.

## 7. Decisions needed

| # | Question | Proposal |
| --- | --- | --- |
| D1 | Pending action expiry | 24 hours, one expiry notice |
| D2 | Bare `yes` with several actions pending | refuse and list the references |
| D3 | Cancelling an appointment: notify the customer by default? | yes, by email (and text when a number is on file), owner can say "don't tell her" |
| D4 | Ad-hoc customer message containing a price | refuse and point to `quote:` |
| D5 | Which model | same review model as quotes (`GVAS_OPENAI_*`), one setting override for the office manager |
| D6 | Should the assistant see field-note case *content* (transcripts) or only status? | status only in phase 1; content is a later, Slack-only read tool |
