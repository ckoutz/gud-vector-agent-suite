# Owner assistant threat model

The owner assistant answers an owner's plain-language messages ("hello",
"what's outstanding?", "what's on tomorrow?") on SMS and Slack, and in the demo's
in-page Texts view. It reads freely; it changes things only through change tools, each
of which waits for the owner's YES (below). The tools themselves land in later
PRs; this document is extended with each one.

## How a message gets here

1. Telnyx ingress accepts a text only from a number registered as an owner of
   that installation (`is_authorized_owner`); anyone else is rejected before
   any workflow runs. Slack ingress likewise maps the workspace to one business.
2. The deterministic resolver runs first. Exact commands (`quote: ...`,
   `approve`/`decline` replies, `unavailable 8-12`) keep their existing
   workflows. Only a message that matches nothing reaches the assistant
   (`UnmatchedMessageHandler`).
3. On SMS, `sms_quotes_only_policy(..., owner_assistant=True)` lets the
   unmatched intent through; field notes and report commands still get the
   scope reply.

## Boundaries

| Concern | Control |
|---|---|
| Cross-business reads | The business id comes from the message's endpoint, never from the model. Every tool runs with that `BusinessRecord` bound in code (`OwnerReadTools.run`). The model has no argument that names a business. |
| Changes | Only through an `OwnerChangeTool`, and only after YES (see "Confirm then act"). The adapter refuses any tool name not offered in the request and never calls it. |
| Prompt injection from customers | Customer-typed text (names, addresses, job details, notes, quote line descriptions) is returned inside `{"customer_wrote": ...}` and the system prompt says to report it, never follow it. A successful injection can at worst produce a wrong reply or propose a change, which the owner sees spelled out by code and must answer YES to. |
| Prompt injection from the owner | The owner is the authorized principal for their own business; the prompt ignores attempts to change rules or reach another business, and the code makes the latter impossible anyway. |
| Data volume / cost | Lists capped at 15 items, schedule at 7 days, tool results at 12k chars, 3 tool rounds, reply at 640 chars. Tokens are recorded as `REVIEW_TOKENS` and the monthly `UsageCeilingGuard` is checked before every answer. |
| Model outage | Any model error becomes a short apology; the message never retries into a loop. |
| Logging | Each answer is a workflow run whose `detail` holds the question and answer (truncated to 300 chars each), plus a log line with the tools called. |

## Confirm then act

Every change goes through the same steps (`OwnerAssistant`, `domain/owner_commands.py`):

1. The model calls a `change_*` tool. Its `prepare` runs in code for the business bound from the endpoint, validates the request, resolves names to ids, and returns the exact `summary` sentence. Refusals go back to the model as data. Only one change per message; a second call is refused.
2. Code, not the model, writes the reply: the summary, then "Reply YES in the next 10 minutes to do it, or NO to cancel it." The model's own text is dropped, so a misreading shows up in plain words before anything happens.
3. The proposal is stored as an `owner_commands` row (`proposed`) with the resolved arguments, the owner's message, the thread it came from and `expires_at` = 10 minutes. Proposing closes any other pending command and any pending calendar block; proposing a block closes a pending command. One pending change per business, enforced by a partial unique index, so two texts racing can't leave two proposals (the loser is told to send it again). The thread is stored as a SHA-256 key, so any thread length fits.
4. The next message decides it, before any workflow or model sees it (`DeterministicIntentResolver.resolve`, and again in `OwnerAssistant.answer` for the demo):
   - a plain yes on its own (`yes`, `y`, `yep`, `yeah`, `yup`, `yes please`, `go ahead`, `do it`; any case, trailing `.`/`!`) in the same thread, before expiry: the row is claimed with a conditional update (`proposed` → `applying`, so two YESes can't apply it twice), then `apply` runs with the stored arguments, never re-read from the model. The outcome is stored (`done` or `failed`) and texted back.
   - a yes with more on it ("Yes and Friday too", "yes but make it 2 to 4") in the same thread: nothing is applied or cancelled; code replies that the change is still waiting, repeats its summary and asks for a plain YES or NO, since the extra words may change what the yes meant. The model never sees it.
   - anything else, including "ok", "sure", a YES from another thread, or a new request: the row becomes `cancelled`, and the reply starts with "I cancelled the pending change: <summary>. Nothing was changed." in code's words. While a change waits, messages go to Gus (booking decisions and `unavailable`/`unblock` included, since he has tools for them), so that line is never skipped. Only `quote:` and field-note commands keep their own workflows; they leave the change waiting, untouched. If a YES claimed the change first, the reply says so instead of "cancelled".
   - if the model tries a second change in the same message, the proposal says in code's words that the rest of the text isn't in it.
   - after 10 minutes the row becomes `expired`; a late YES gets "Nothing is waiting for a YES".
5. The model is never in the confirm path: YES needs no model call, so an outage or a prompt injection can't confirm or alter a stored command.

| Concern | Control |
|---|---|
| Model misreads the owner | The code-written summary is what YES confirms; the stored arguments are what runs. |
| Customer text proposes a change | It can only reach the model as `customer_wrote` data; even then the owner reads the summary and must reply YES. |
| Replay / double apply | Conditional claim on `proposed` and `expires_at > now`. |
| Stale proposals | 10-minute expiry; one pending per business; any other message cancels. |
| Cancel racing a YES | Cancel and expire are conditional on `proposed`, so they never overwrite a claimed (`applying`) row. |
| Crash mid-change | Known limit: if the process stops after `apply` but before the outcome is saved, the row stays `applying`. The command log shows it as unfinished, and the owner checks the dashboard. The change itself runs in one transaction, so it is either fully done or not done. |
| Audit | Every proposal stays as a row with its outcome, the owner's request and timestamps (the command log; the dashboard page comes in a later PR). |
| Sensitive changes | Each tool says whether it's `sensitive` (money, customer-facing messages). A PIN for those is planned as an off-by-default per-business setting; until then YES alone confirms. |
| Security settings | PIN, owner numbers, sign-in, notification e-mail and connected accounts have no change tool: dashboard only. |

## Demo Texts view

The demo endpoint answers in the page for the visitor's own sandbox copy
(authenticated by the sandbox token, which names one business). Nothing is
sent through Telnyx, and the demo backend refuses real provider keys.

## Not in this slice

- The change tools themselves, the PIN (off by default) and the dashboard command log page.
- Security changes (PIN, owner numbers, sign-in, connected accounts): dashboard only, never by text.

## Money tools (send invoice, mark paid / unpaid, plan payments)

- Each tool resolves its target only among the texting business's own quotes, invoices and plans, by customer name or invoice number; an unknown or ambiguous target is refused before any proposal.
- The proposal names the customer, the amount and the method in words written by code; YES runs the same dashboard service the button calls, with the stored arguments.
- Card payments can't be undone by text (they're refunds, out of scope); only check, cash or other can be recorded; dates can't be in the future; months are 1-24.
- A repeated YES or a crash-retry can't double-charge: mark paid and send invoice refuse a second run ("nothing to mark paid", "nothing to invoice"), and a plan payment carries a per-command key.
- All five are marked `sensitive`, so the off-by-default PIN covers them once it's switched on.


## Decision tools (approve / decline a visit request, approve / reject a quote)

- The model names a customer; code matches it only against this business's requests or quotes still waiting on the owner. No match, or more than one, is refused before a proposal.
- The proposal says who, when (visit) or how much and which items (quote), and what the customer will be told, including a decline note exactly as the customer will see it (cleaned the same way as the dashboard's).
- YES runs the dashboard's `decide_booking` / `decide_quote`, which refuse a request or quote that was decided meanwhile. Approving a quote is `sensitive` (it sends a price to the customer); the others aren't.
- The exact replies that already worked (`approve <ref>`, `decline <ref>`) still go first and are unchanged.
- In a thread where a quote waits for the owner's OK, only the exact `approve`, `reject` and `correct:` replies stay with the quote workflow; plain words go to Gus, so "approve Hannah's quote" still needs YES.
- A request to *move* a booking says what happens to the original time: approving cancels the old booking (or closes the old request), declining keeps it. A note given with a declined move is kept on the request, not sent.
- `change_settings` covers only the business name, the time zone and Gus's customer-facing wording. Notification e-mail, Calendly link, calendar feed and every security setting stay dashboard-only and aren't in the tool's schema. Values go through the dashboard's own checks (`OwnerService.settings_fields`) before the proposal, and Gus quotes the full new wording before YES, because customers will read it.

### Several days in one change

- `change_block_time` and `change_unblock_day` take a list of dates; one read-back names every day ("all day Thu Oct 15 and all day Fri Oct 16"), and one YES does them all. Days where nothing can change (closed then, or nothing blocked) are left out and the read-back says so.
- At YES each day is done on its own: its own recheck (passed days are skipped), its own undo record saved before Calendly is written, and its own read-back after an error. A day that fails never undoes a day that saved; the reply names each day saved and each day not saved, with why, and every saved day can be unblocked by text.


## Customer messages (`change_message_customer`)

- The owner's exact words go to one customer from the business's own customer list (matched in code; the model only names them). Booking-only contacts with no customer record can't be messaged.
- Before YES, the code-written read-back shows the recipient's name, the address or phone it goes to, the channel, the subject and the full message, word for word. The stored arguments hold exactly that.
- Texts go only to a customer whose record says `sms_consent` is True, and the text worker checks consent again at send time; everyone else, and everyone when the deployment has no texting provider, gets e-mail. If the route (consent, phone or e-mail) changed between the read-back and YES, nothing is sent and the owner is asked again.
- Sending goes through the outbox (`intake_customer.email` / `intake_customer.text`), with a per-proposal idempotency key, so a retried worker never sends twice. The demo's delivery adapters send nothing. If a send fails for good, the owner gets a notice naming the recipient and quoting the message (`Your message to … could not be sent: "…"`), not the booking-customer one, in the business's latest owner thread (the thread the YES came from, unless another owner message arrived since).
- Marked `sensitive`, so the off-by-default PIN covers it when switched on.
