# Owner assistant threat model (read-only slice)

The owner assistant answers an owner's plain-language messages ("hello",
"what's outstanding?", "what's on tomorrow?") on SMS and Slack, and in the demo's
in-page Texts view. This slice is **read only**: it has no tool that changes
anything. Change tools (with a YES tied to one pending action) and the money PIN
come later and will extend this document.

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
| Changes | None possible: the tool list is `schedule`, `needs_you`, `money`, `find_customer`, `quotes`, all reads. The adapter refuses any tool name not offered in the request and never calls it. |
| Prompt injection from customers | Customer-typed text (names, addresses, job details, notes, quote line descriptions) is returned inside `{"customer_wrote": ...}` and the system prompt says to report it, never follow it. With no change tools, a successful injection can at worst produce a wrong text reply to the owner. |
| Prompt injection from the owner | The owner is the authorized principal for their own business; the prompt ignores attempts to change rules or reach another business, and the code makes the latter impossible anyway. |
| Data volume / cost | Lists capped at 15 items, schedule at 7 days, tool results at 12k chars, 3 tool rounds, reply at 640 chars. Tokens are recorded as `REVIEW_TOKENS` and the monthly `UsageCeilingGuard` is checked before every answer. |
| Model outage | Any model error becomes a short apology; the message never retries into a loop. |
| Logging | Each answer is a workflow run whose `detail` holds the question and answer (truncated to 300 chars each), plus a log line with the tools called. |

## Demo Texts view

The demo endpoint answers in the page for the visitor's own sandbox copy
(authenticated by the sandbox token, which names one business). Nothing is
sent through Telnyx, and the demo backend refuses real provider keys.

## Not in this slice

- Change tools, YES confirmation, money PIN and lockout.
- Security changes (PIN, owner numbers, sign-in, connected accounts): dashboard only, never by text.
