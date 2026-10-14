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
2. Code, not the model, writes the reply: the summary, then "Reply YES in the next 10 minutes to do it. Any other reply cancels it." The model's own text is dropped, so a misreading shows up in plain words before anything happens.
3. The proposal is stored as an `owner_commands` row (`proposed`) with the resolved arguments, the owner's message, the thread it came from and `expires_at` = 10 minutes. Proposing closes any other pending command and any pending calendar block; proposing a block closes a pending command. One pending change per business.
4. The next message decides it, before any workflow or model sees it (`DeterministicIntentResolver.resolve`, and again in `OwnerAssistant.answer` for the demo):
   - exactly `YES` (any case, trailing `.`/`!`) in the same thread, before expiry: the row is claimed with a conditional update (`proposed` → `applying`, so two YESes can't apply it twice), then `apply` runs with the stored arguments, never re-read from the model. The outcome is stored (`done` or `failed`) and texted back.
   - anything else, including "ok", "sure", a YES from another thread, or a new request: the row becomes `cancelled`, and the message is handled as usual.
   - after 10 minutes the row becomes `expired`; a late YES gets "Nothing is waiting for a YES".
5. The model is never in the confirm path: YES needs no model call, so an outage or a prompt injection can't confirm or alter a stored command.

| Concern | Control |
|---|---|
| Model misreads the owner | The code-written summary is what YES confirms; the stored arguments are what runs. |
| Customer text proposes a change | It can only reach the model as `customer_wrote` data; even then the owner reads the summary and must reply YES. |
| Replay / double apply | Conditional claim on `proposed` and `expires_at > now`. |
| Stale proposals | 10-minute expiry; one pending per business; any other message cancels. |
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
