# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-15 07:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

The backend is back up. The GVAS web service at `web-production-9d848.up.railway.app` answers `/healthz` 200 `{"status":"ok"}` in 0.34 s. `/health` and `/` return FastAPI 404s, which is expected because the app has no route there; this is not Railway's "Application not found". The deployed OpenAPI lists 41 paths, including `/v1/owner/commands` (#87, merged 2026-10-14), so the web service runs code from at least 2026-10-14. The exact deployed commit is unknown because the Railway API is not reachable from this run. Whether main's latest merges (#88–#92, owner-texts work) are live cannot be told from the public surface. **Telnyx toll-free verification for +18775411550 is now `Verified`** (updated 2026-10-08), so customer SMS is no longer blocked by the carrier; it is still gated by per-customer consent. gudvector.com resolves (216.198.79.1, Vercel) and returns 200. gudvector-site.vercel.app returns 200, and both domains render "couldn't find" for an unknown quote token. Stripe is in sandbox with the production webhook `enabled` (7 events), and the route rejects unsigned posts (401 `invalid_signature`), so the route is live. 16 GVAS PRs and 2 site PRs merged in the last 7 days. Two PRs are open, both clean and CI-green: GVAS #90 and site #34.

## Demo pipeline

Status comes from public endpoints and API checks only. This refresh did not run the pipeline end to end.

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com and gudvector-site.vercel.app 200 (Resend). |
| Calendly | LIVE | Lookup/availability/booking code is deployed and the backend is reachable. `/calendly/events` webhook is **not mounted** (not in OpenAPI), so `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` is unset and scheduling-link bookings fall back to day-only matching. |
| Quote drafting | LIVE | Slack `/slack/events` and Telnyx `/telnyx/messaging` routes are deployed; backend healthy. |
| Owner approval | LIVE | Slack/SMS commands, signed e-mail links (`/v1/intake/booking-decisions/{token}`) and owner dashboard approve/decline routes are deployed. Owner-texts plain-language tools are partly in main (#83–#92); #90 is PR OPEN. Whether #88–#92 are deployed is unknown. |
| Email/SMS delivery | LIVE | Email via Resend. Telnyx TFV is **Verified**, so customer texts go out when the customer has `sms_consent = true`. |
| /q/\<token\> portal | LIVE | `/q/nonexistent-token` returns 200 "couldn't find" on both domains (env vars present); `/v1/quotes/{claim_token}` is deployed. |
| Stripe payment | LIVE (sandbox) | Webhook `…/webhooks/stripe` `enabled`, 7 events, livemode=false; the route answers 401 `invalid_signature` to unsigned posts. |
| Customer portal | LIVE | `/v1/portal/*` deployed; site portal pages live. |
| AI booking agent | LIVE | `/v1/businesses/{key}/intake/*` deployed; the site chat widget is live. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#90](https://github.com/ckoutz/gud-vector-agent-suite/pull/90) | Owner texts: change a customer's name or phone in plain words, behind YES | main | clean | passing (ruff, mypy, pytest, alembic) | 1 day | **1**: independent; merged #91/#92 sit on top of the same owner-texts code, so recheck for conflicts if more owner-texts PRs land first. Deploy web afterwards. |
| gudvector-site | [#34](https://github.com/ckoutz/gudvector-site/pull/34) | Owner Settings: "Changes by text", the log of every change asked for by text | main | clean | passing (Vercel ×3, preview comments) | 1 day | **2**: any time; its backend `GET /v1/owner/commands` (#87) is merged and live. |

No stacked PRs. Site PRs #1, #8, #10 and #11 from the previous refresh are no longer open.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `6b73d70` 2026-10-15 02:16 UTC: Merge #92 (owner texts: message a customer) |
| GVAS web (Railway) `/health` | 404 `{"detail":"Not Found"}` (0.43 s). This is the app's own 404 because the route doesn't exist. `/` gives the same 404 (0.37 s). |
| GVAS web `/healthz` | **200** `{"status":"ok"}` (0.34 s) |
| GVAS web deployed commit | unknown (Railway API not reachable from this run). It includes #87 or later because `/v1/owner/commands` is served. |
| gudvector-site main | `89a6584` 2026-10-12 20:22 UTC: Merge #33 (dashboard behind a proxy) |
| gudvector-site.vercel.app | `/` 200 (0.29 s); `/q/nonexistent-token` 200 "couldn't find" (1.28 s). No "temporarily unavailable", so the GVAS env vars are set. |
| gudvector.com | Resolves (216.198.79.1, served by Vercel); `/` 200 (0.27 s); `/q/nonexistent-token` 200 "couldn't find" (0.44 s). **Domain is up.** |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired and deployed. `/calendly/events` webhook not mounted (`GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` unset). |
| Resend | Wired: contact form, quote links, magic links, owner notification e-mails. |
| Telnyx | TFV `f77d0422-bdb9-5f33-9783-2cba342f865c` (+18775411550): **Verified** (updated 2026-10-08 16:01 UTC). Owner SMS and customer SMS (consent-gated) can be sent. `/telnyx/messaging` deployed. |
| Stripe | Sandbox (livemode=false). `https://web-production-9d848.up.railway.app/webhooks/stripe`: `enabled`, 7 events (checkout.session.completed/async_payment_succeeded/async_payment_failed, invoice.paid/payment_failed, customer.subscription.updated/deleted). A second endpoint, `web-demo-5831.up.railway.app/webhooks/stripe`, is also `enabled` with the same 7 events. |
| Slack | Wired; `/slack/events` deployed on the live web service. |
| OpenAI | Wired (review model, free-text quotes, intake agent, owner texts); `GVAS_COST_CEILING_*`. Runtime status not checked (no public probe). |
| Cloudflare R2 | Wired when `GVAS_R2_*` is set. Runtime status unknown (no public probe). |

## Owner to-do

- [ ] Merge open PRs: GVAS #90, then site #34 (both independent, clean, green). No stacks open.
- [ ] Trigger a Railway **web** service deploy after the backend merges; web does not auto-deploy on push, the worker does. Confirm that #88–#92 (and #90 once merged) are live; the deployed commit could not be verified from here.
- [x] ~~Restore the GVAS web service on Railway.~~ `/healthz` returns 200 again.
- [ ] Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook (`/calendly/events` is not mounted).
- [x] Namecheap: registrant WHOIS verification done. gudvector.com resolves and returns 200.
- [x] Point gudvector.com DNS at Vercel; it serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (not verifiable from public checks).
- [x] Telnyx: toll-free verification is **Verified** (2026-10-08). Customer SMS is no longer carrier-blocked; it remains consent-gated.
- [ ] Fix intake threat-model finding **M1** (anonymous intake linking to an existing customer by unverified email) before building more on the intake path.
- [ ] Update `docs/roadmap.md`: the owner-texts assistant (#78–#92) is not listed under Done/In progress; "Office manager" still reads "Not started".
- [ ] Decide whether the second Stripe webhook endpoint (`web-demo-5831.up.railway.app`) should stay enabled.
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor, Vercel).
- [ ] Stripe: switch to live keys at production cutover. Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md on main, last changed `2348d85` 2026-10-12)

- **Done:** field-note intake (text/voice) and append, evidence-only report, review-model benchmark, contradiction pass, DOCX publish and publish-failure notice, worker logging, unmatched-message triggers, R2 storage, checklist evidence annotator, plan custody, email report, Telnyx owner SMS (quotes), cost ceilings, Calendly lookup, free-text quotes, portal quote handoff, hosted customer quotes + Stripe Checkout, customer portal + recurring quotes, AI booking intake, per-business intake profile, owner notification e-mail, owner dashboard backend, customer SMS consent, booked-mode intake (reschedule/cancel, signed decision links), unified owner notification e-mails, channel-agnostic owner understanding step.
- **In progress:** "Audit follow-ups (2026-09): all landed". The doc does not yet reflect the owner-texts work merged in #78–#92 or open #90.
- **Next (ordered):** 1) Letterhead DOCX templates per business. **Needs decision:** who supplies the template and where the binding manifest lives. 2) Office manager owner assistant over Slack/SMS, with every write behind the owner's YES, owner-only, existing keyword commands matched first. Marked "Not started; design review pending". **Needs decision:** D1–D6 in `docs/office_manager.md`. Prerequisite: fix M1. Later: flag cancel/reschedule hidden in customer notes. 3) Retention and redaction of transcripts, media, reports.
- **Open follow-ups (not built):** Stripe Connect per-business payouts (`businesses.stripe_account_id` unused); contact-form API behind business `public_key`; per-business timezone for Calendly windows/timestamps; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes/voice/reports over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gud-vector-agent-suite | [#92](https://github.com/ckoutz/gud-vector-agent-suite/pull/92) Owner texts: message a customer, full message and recipient shown before YES | 2026-10-15 02:16 |
| gud-vector-agent-suite | [#91](https://github.com/ckoutz/gud-vector-agent-suite/pull/91) Owner texts: block/unblock several days in one YES; plain yes variants; cancels say what was cancelled | 2026-10-15 01:46 |
| gud-vector-agent-suite | [#89](https://github.com/ckoutz/gud-vector-agent-suite/pull/89) Owner texts: block and unblock time on the booking calendar in plain words, behind YES | 2026-10-14 01:51 |
| gud-vector-agent-suite | [#88](https://github.com/ckoutz/gud-vector-agent-suite/pull/88) Owner texts: when the model asks for YES itself, Gus adds a note that nothing is set up | 2026-10-14 01:33 |
| gud-vector-agent-suite | [#87](https://github.com/ckoutz/gud-vector-agent-suite/pull/87) Owner API: GET /v1/owner/commands, the text command log | 2026-10-14 01:22 |
| gud-vector-agent-suite | [#86](https://github.com/ckoutz/gud-vector-agent-suite/pull/86) Owner texts: change Settings (business name, time zone, Gus's wording) behind YES | 2026-10-14 01:21 |
| gud-vector-agent-suite | [#85](https://github.com/ckoutz/gud-vector-agent-suite/pull/85) Owner texts: approve or decline visit requests and quotes in plain words, behind YES | 2026-10-14 01:08 |
| gud-vector-agent-suite | [#84](https://github.com/ckoutz/gud-vector-agent-suite/pull/84) Owner texts: money change tools (send invoice, mark paid/unpaid, record/undo plan payment) behind YES | 2026-10-14 00:58 |
| gud-vector-agent-suite | [#83](https://github.com/ckoutz/gud-vector-agent-suite/pull/83) Owner texts: confirm-then-act framework (one pending change, YES only, 10 min) and command log | 2026-10-14 00:57 |
| gud-vector-agent-suite | [#82](https://github.com/ckoutz/gud-vector-agent-suite/pull/82) Owner texts: e-mail info@gudvector.com on a setup failure, at most once an hour | 2026-10-14 00:26 |
| gud-vector-agent-suite | [#81](https://github.com/ckoutz/gud-vector-agent-suite/pull/81) Owner texts: Gus states every visit's status (booked / requested, waiting for your approval) | 2026-10-13 01:46 |
| gud-vector-agent-suite | [#80](https://github.com/ckoutz/gud-vector-agent-suite/pull/80) Owner texts: OpenAI accepts the read tools (reasoning_effort none); a refused request says so and logs an alert | 2026-10-13 01:38 |
| gud-vector-agent-suite | [#79](https://github.com/ckoutz/gud-vector-agent-suite/pull/79) Owner texts: tell a pending website request from a booking | 2026-10-12 22:23 |
| gud-vector-agent-suite | [#78](https://github.com/ckoutz/gud-vector-agent-suite/pull/78) Owner assistant (read only): owners text Gus in plain language; demo owner-texts endpoint | 2026-10-12 20:23 |
| gud-vector-agent-suite | [#77](https://github.com/ckoutz/gud-vector-agent-suite/pull/77) Invoices (demo version): own records from accepted quotes, per-business switch, seeded mix | 2026-10-12 17:45 |
| gud-vector-agent-suite | [#76](https://github.com/ckoutz/gud-vector-agent-suite/pull/76) Intake: a business's follow-up questions go in one message; the visit answer is saved once | 2026-10-12 15:33 |
| gudvector-site | [#33](https://github.com/ckoutz/gudvector-site/pull/33) Dashboard behind a proxy: relative sign-in redirects; Larkspur's proxy hosts may post forms | 2026-10-12 20:22 |
| gudvector-site | [#32](https://github.com/ckoutz/gudvector-site/pull/32) Owner dashboard: Invoices tab, Send invoice on accepted quotes, Mark paid on invoices | 2026-10-12 17:45 |
