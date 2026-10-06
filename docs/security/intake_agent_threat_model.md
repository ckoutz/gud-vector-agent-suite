# Threat model: customer-facing intake/booking agent

Scope: the chat widget on `/contact`, `/book` and the customer portal, backed by
`IntakeService` (`src/gvas/application/intake.py`), the OpenAI adapter
(`src/gvas/infrastructure/openai_intake_agent.py`), the public routes
(`src/gvas/interfaces/http/public.py`, `portal.py`) and the Calendly webhook
ingress. Reviewed at commit `c1c66af` (main, deployed to Railway 2026-09-23).

Method: read every code path from HTTP request to database and provider, the
same way as the pre-ProTech review; probed the live deployment for the routes
that exist without credentials. No code was changed.

## Verdict

The three properties asked for hold **by construction**, not by prompt:

| Property | Result |
| --- | --- |
| Tenant scoping is structural | Yes |
| Nothing sensitive is in the model's reach | Yes — the model has no tools at all |
| No bypass of owner approval | Yes — no write reaches a provider without `approve booking <ref>` |

Two medium findings and three low ones are listed below. None is a
cross-tenant path or an approval bypass; the mediums are about an anonymous
customer being able to *claim someone else's email* inside the same business.

## 1. Tenant scoping

**The model has no tools.** `OpenAIIntakeAgent.turn` is one chat-completions
call with a strict JSON schema (`RESPONSE_SCHEMA`). There is no function
calling, no retrieval, no database handle. The model can only return text plus
seven collected strings, three booleans and one timestamp. Whatever it is
told, it cannot *do* anything — the service decides what to do with its output.

**The credential is the conversation token, and the business comes from the
row.** Every authenticated route (`POST …/messages`, `GET …/{id}`) calls
`IntakeService.authenticate(conversation_id, token)`, which looks the row up by
`(id, token_hash)` (`find_by_token`). The `business_id` is then read from that
row; nothing in the request body, headers or model output can set or change
it. All subsequent reads and writes pass `conversation.business_id` as a
where-clause:

- `intake_messages.list_for(business_id, conversation_id)`
- `intake_conversations.lock(business_id, conversation_id)`
- `customers.upsert(business_id, …)`, `service_requests.add(business_id=…)`
- `availability.available_slots(business_id, …)` — Calendly installation is
  chosen by business
- owner notice: `enqueue_intake_owner_notice(unit_of_work, business_id, …)`

**Anonymous start is keyed by public key only.** `start_conversation(public_key)`
→ `businesses.get_by_public_key`; unknown key → generic 404. The public key
identifies the tenant but grants nothing else (no read routes take it).

**Portal start is keyed by the portal session.** `/v1/portal/intake/conversations`
resolves `context.business` and `context.customer` from the portal bearer
token server-side; the site route (`/api/intake/conversations`) forwards the
session cookie as a Bearer, so the customer never holds a business id either.

**Owner decisions are scoped by the owner's channel.** `BookingDecisionHandler`
uses `lock_by_reference(message.business_id, reference)`, where
`message.business_id` is derived from the Slack workspace / Telnyx number the
owner message arrived on. A reference guessed from another tenant does not
resolve.

**Calendly webhooks are bound to an installation.** `CalendlyWebhookIngress`
maps Calendly `user_uri` → `business_id` from configuration before any lookup;
invitee-email matching (`lock_latest_by_invitee_email`) is already inside that
business.

Live probe: `POST /v1/businesses/nonexistent/intake/conversations` → 404;
the real key → 201 with a token; a `GET` on a conversation with no/garbage
bearer → 401. No response includes `business_id`, customer ids, owner contact
or other conversations (`intake_*_payload` shape is explicit).

## 2. What is in the model's context

`_user_content()` serialises exactly:

```
business_name, known_customer (bool), collected_so_far (this conversation's
7 fields), offered_slots (start/end), transcript (last 40 messages of this
conversation)
```

plus the fixed `SYSTEM_PROMPT`. Not present, and not reachable: pricing floors
or any quote data, field notes, owner notes, other customers, other
conversations, the customer record (only its name/email/phone, and only for a
portal-authenticated customer who already knows them), Calendly URLs, API keys.
`business_name` is the only tenant fact shared.

Output side: `scrub_agent_reply` replaces any reply containing a currency
amount with `PRICE_GUARD_REPLY`; replies are capped at 600 chars; the
`collected` fields are trimmed strings only. Token spend is capped per business
by `UsageCeilingGuard(REVIEW_TOKENS)` before the model is called.

So a prompt-injected model can at worst: answer rudely, set `needs_human`,
fill collected fields with junk, or point `chosen_slot` at a time — and the
service only honours `chosen_slot` if it matches a slot *it* previously offered
(`find_proposed_slot`, state must be `PROPOSING_SLOTS`).

## 3. Owner approval cannot be bypassed

Provider writes in the intake path are exactly two: `availability.book()` and
`availability.cancel_booking()`. Both run only from outbox commands
(`intake.booking.arrange`, `intake.booking.cancel`), and those commands are
enqueued in exactly two places:

- `BookingDecisionHandler._approve` — after the owner's `approve booking <ref>`
  moved the row to `APPROVED`; `ArrangeIntakeBookingService.arrange` re-checks
  `state is APPROVED` before calling the provider.
- `BookingDecisionHandler._decline` — cancel, only when a booked event exists.

`IntakeService` itself never enqueues either command; the furthest a customer
turn can move state is `AWAITING_OWNER`. Customer email/text sends are also
enqueued only from the arrange/decline paths. Grep confirms no other caller of
`intake_booking_arrange_command` / `intake_booking_cancel_command` in `src/`.

The owner inbox cannot be spoofed by the widget: Slack ingest drops
`bot_id` messages (`events.py:82`) so the bot's own "Booking request #ref"
notice is never re-read as a command, and Telnyx inbound is signature-verified
and bound to enrolled owner numbers.

## Findings

### M1 — Anonymous chat can attach itself to an existing customer by email (unverified)

`_handle_slot_pick` calls `customers.upsert(business_id, collected.email, …)`
with the email the *anonymous* customer typed. If it matches an existing
customer of that business, the conversation and a new `ServiceRequest` are
linked to that customer's id, and `upsert` fills in `phone`/`display_name` when
they were empty. Effects inside one tenant: a stranger can put a request into
another customer's portal history, and get their phone attached to that
customer so future confirmation texts for that customer's bookings go to the
attacker's number (until the owner notices). No cross-tenant reach; the owner
still has to approve.

Fix: for anonymous starts, do not link to an existing customer until the email
is verified (portal magic link) or the owner approves; never fill
`phone`/`display_name` from unverified intake; show "unverified email" in the
owner notice when the address already belongs to a customer.

### M2 — Confirmation email/text go to unverified contact details

After approval, `ArrangeIntakeBookingService` emails and texts the collected
address/phone. Combined with M1 this is the delivery vector; on its own it lets
a customer make the business send one message to any address/number (content
is fixed, no attacker text except the business name), i.e. low-volume
messaging abuse bounded by the per-business daily conversation cap (50) and
per-IP rate limit.

Fix: same as M1; optionally cap intake-originated customer texts per day.

### L1 — Customer text reaches the owner's Slack/SMS verbatim

`booking_request_notice` and `escalation_notice` include `name`, `address`,
`problem` and the model `summary`. These are outbound to the owner, not parsed
as commands, so there is no execution path — but a customer can write e.g.
"approve booking abcd" into the address field to social-engineer the owner.
Fix: quote customer-supplied fields (`> …`) and truncate in the notice.

### L2 — Calendly webhook route not mounted in production

`/calendly/events` is registered only when `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY`
is set; the Railway env does not set it, so the route 404s. Not a security
hole (fail-closed), but booked/cancelled events are not reconciled, and the
day-precision `lock_latest_by_invitee_email` match is dormant. Deployment gap,
tracked in the deploy checklist.

### L3 — Coverage gap: cross-tenant owner decision

`tests/test_intake_booking.py::test_token_from_another_business_cannot_read_or_post`
already pins the customer side. There is no test that `approve booking <ref>`
sent from business A's owner channel ignores business B's reference. The code
makes this impossible (`lock_by_reference(message.business_id, …)`), but a
regression test would keep it that way. Fix: add it.

## Recommendations before building more on the agent

1. Fix M1 (unverified-email linkage) — small change in `_handle_slot_pick`.
2. Add L3 cross-tenant regression tests.
3. Set the Calendly webhook key in Railway (L2).
4. Keep the "no tools for the customer-facing model" property as an explicit
   architecture rule; the office-manager design (`docs/office_manager.md`)
   deliberately places tools only behind the owner-authenticated channel.
