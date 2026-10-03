# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-03 01:06 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

The GVAS web service URL `web-production-9d848.up.railway.app` is **still down**. Every path (`/health`, `/healthz`, `/`) returns Railway's `404 Application not found`, so this is the second refresh in a row with the backend unreachable. Stripe, Telnyx, Slack, Calendly and the site's booking widget all point at that URL, which means everything server-side is blocked until the service (or its domain) is restored and main (`c1c66af`, #40–#44) is deployed. Telnyx toll-free verification for +18775411550 is still **Rejected** ("Message Content Does Not Align with Use Case or is Incomplete", last updated 2026-09-30). Three new stacked PR pairs since the last refresh are meant to fix that: GVAS #46 (per-business intake profile) → #47 (customer SMS consent capture + gating), and site #10 (chat bubble) → #11 (SMS consent checkbox + `/sms-opt-in` rewrite for Telnyx). All of them are clean and CI-green. On the frontend, gudvector.com and gudvector-site.vercel.app both return 200 and render the "couldn't find" page for an unknown quote token. The site also shows that page when GVAS answers 404, so this does not prove the backend is reachable. No PRs merged in either repo since the last refresh.

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com and gudvector-site.vercel.app 200 (Resend, independent of GVAS). Site #11 adds an SMS consent checkbox. |
| Calendly | BLOCKED | Code in main (#32, #41, #44); GVAS web URL answers 404 "Application not found". |
| Quote drafting | BLOCKED | Served by GVAS web/worker over Slack/SMS; web URL is down (worker state unknown). |
| Owner approval | BLOCKED | `approve`/`send` over Slack depend on the Slack request URL on the down web service. |
| Email/SMS delivery | BLOCKED | Email via Resend depends on GVAS. Customer SMS is blocked until Telnyx TFV is Verified (currently **Rejected**). Consent gating is in PR OPEN (GVAS #47, site #11). |
| /q/\<token\> portal | LIVE (frontend) / BLOCKED (backend) | `/q/nonexistent-token` 200 "couldn't find" on both domains; real tokens cannot resolve while GVAS is down. |
| Stripe payment | BLOCKED | Webhook endpoint `enabled` (7 events, sandbox) but targets the down GVAS URL. |
| Customer portal | MERGED-NOT-DEPLOYED / BLOCKED | Site pages live; GVAS #40 portal API unreachable. |
| AI booking agent | MERGED-NOT-DEPLOYED / BLOCKED | Site #7 live (`/book`); GVAS #41–#44 unreachable. PR OPEN: GVAS #46 makes the agent generic per business; site #10 moves it into a chat bubble. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#45](https://github.com/ckoutz/gud-vector-agent-suite/pull/45) | docs: office manager design + intake agent threat model | main | clean | passing (ruff, mypy, pytest, alembic) | 10 days | Any time — docs only (3 files). |
| gud-vector-agent-suite | [#46](https://github.com/ckoutz/gud-vector-agent-suite/pull/46) | Per-business intake profile; generic booking-agent contract | main | clean | passing (ruff, mypy, pytest, alembic) | <1 day | **G1** — base of stack; migration `0017`. |
| gud-vector-agent-suite | [#47](https://github.com/ckoutz/gud-vector-agent-suite/pull/47) | Customer SMS consent: capture on intake, copy to customer, gate every customer text | `devin/1790980306-intake-profile` (**stacked on #46**) | clean | passing (ruff, mypy, pytest, alembic) | <1 day | **G2** — after #46; retarget to main. Migration `0018`. |
| gudvector-site | [#10](https://github.com/ckoutz/gudvector-site/pull/10) | Replace /book with a floating chat bubble on public pages | main | clean | passing (Vercel) | <1 day | **S1** — base of stack. |
| gudvector-site | [#11](https://github.com/ckoutz/gudvector-site/pull/11) | SMS consent capture in intake chat + contact form; rewrite /sms-opt-in for Telnyx | `devin/1790980187-chat-bubble` (**stacked on #10**) | clean | passing (Vercel) | <1 day | **S2** — after #10; retarget to main. Additive for GVAS (`sms_consent` is ignored until #47 is deployed). |
| gudvector-site | [#8](https://github.com/ckoutz/gudvector-site/pull/8) | sms-opt-in: name the message types in the consent text (Telnyx TFV feedback) | main | **Conflicts** (dirty) | passing | 10 days | Close — superseded by merged #9 and by #11. |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** (dirty) | passing | 30 days | Close — superseded by #3–#9. |

Two stacks: GVAS #46 → #47, site #10 → #11. GVAS #46/#47 do not depend on the site PRs, and the site PRs do not depend on GVAS #46/#47. The SMS-consent pair (#47 + #11) only takes effect once both are live.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `c1c66af` 2026-09-22 22:52 UTC — Merge #44 (intake availability lead time) |
| GVAS web (Railway) `/health` | **404** `{"status":"error","code":404,"message":"Application not found"}` in 0.32 s. `/healthz` 404 (0.45 s) and `/` 404 (0.34 s) too. This is Railway's fallback, so no service is bound to this domain. Down since at least 2026-10-02 22:40 UTC. |
| GVAS web deployed commit | unknown (service unreachable) |
| gudvector-site main | `390a677` 2026-10-02 22:24 UTC — Merge #9 (sms-opt-in consent copy) |
| gudvector-site.vercel.app | `/` 200 (0.34 s); `/q/nonexistent-token` 200 "couldn't find" (no "temporarily unavailable"; env vars present) |
| gudvector.com | Resolves (216.198.79.1); `/` 200 (0.47 s); `/q/nonexistent-token` 200 "couldn't find". Domain is back and serving the current site. |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired in code (lookup, availability, direct booking + scheduling-link fallback, `/calendly/events` webhook). Unreachable while GVAS web is down. The webhook route stays unmounted until `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` is set. |
| Resend | Wired. The site contact form works on its own; quote and magic-link emails depend on GVAS (down). |
| Telnyx | Owner SMS channel wired. TFV `f77d0422-…` (+18775411550): **Rejected**, "Message Content Does Not Align with Use Case or is Incomplete" (updated 2026-09-30). Customer consent capture/gating is in PR (GVAS #47, site #11). Resubmit after those merge and deploy. |
| Stripe | Sandbox (livemode=false). Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events (checkout.session.completed/async_payment_succeeded/async_payment_failed, invoice.paid/payment_failed, customer.subscription.updated/deleted). Target URL 404s, so deliveries fail. |
| Slack | Wired (owner channel; field notes + quotes). Request URL is on the down GVAS web service. |
| OpenAI | Wired (review model, free-text quotes, intake agent; `GVAS_COST_CEILING_*`). Status unknown while GVAS is down. |
| Cloudflare R2 | Wired when `GVAS_R2_*` is set (published DOCX storage). Status unknown while GVAS is down. |

## Owner to-do

- [ ] **Restore the GVAS web service on Railway.** `web-production-9d848.up.railway.app` returns "Application not found". Check whether the service or its domain was deleted or renamed. If the URL changed, update the Stripe webhook endpoint, Slack request URL, Telnyx webhook and the site's `GVAS_*` env vars on Vercel. Then deploy main (`c1c66af`, includes #40–#44).
- [ ] Merge open PRs in dependency order (stacked PRs: base first): GVAS #45 (docs, any time); GVAS #46, then retarget and merge #47; site #10, then retarget and merge #11. Close site #8 and site #1 (superseded, conflicting).
- [ ] Trigger a Railway **web** service deploy after the backend merges; web does not auto-deploy on push, the worker does. #46/#47 add migrations `0017`/`0018`. Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook.
- [ ] After #46 deploys, configure the Güd Vector intake profile (`gvas-configure-business --intake-brief/--intake-questions/--intake-opening`).
- [x] Namecheap: registrant WHOIS verification. gudvector.com resolves and returns 200.
- [x] Point gudvector.com DNS at Vercel; it serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (verify once the backend is back).
- [ ] Telnyx: verification is **Rejected**. Once GVAS #47 and site #11 are live, resubmit `f77d0422-…` pointing at https://gudvector.com/sms-opt-in with consent-checkbox screenshots. SMS to customers stays blocked until Verified.
- [ ] Fix intake threat-model finding **M1** (anonymous intake linking to an existing customer by unverified email) before building the office manager (PR #45).
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor, Vercel).
- [ ] Stripe: switch to live keys at production cutover. Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md on main)

- **Done:** field-note intake (text/voice) and append, evidence-only report, review-model benchmark, contradiction pass, DOCX publish + publish-failure notice, worker logging, unmatched-message triggers, R2 storage, checklist evidence annotator, plan custody, email report, Telnyx owner SMS (quotes only), cost ceilings, Calendly customer lookup, free-text quotes, portal quote handoff, hosted `/q/<token>` quotes with Stripe Checkout, customer portal + recurring quotes/subscriptions, AI booking intake with owner-approved Calendly booking.
- **In progress:** none on main (2026-09 audit follow-ups all landed). Open PRs: #46 intake profile, #47 SMS consent.
- **Next (ordered):** 1) Letterhead DOCX templates per business. **Needs decision:** who supplies the template and where the binding manifest lives. 2) Retention and redaction of transcripts, media, reports. PR #45 proposes an **Office manager** owner assistant as a new item 2. It needs decisions D1–D6, and M1 must be fixed first.
- **Open follow-ups (not built):** Stripe Connect per-business payouts; contact-form API behind business `public_key`; per-business timezone; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gudvector-site | [#9](https://github.com/ckoutz/gudvector-site/pull/9) sms-opt-in: state the type of SMS (business updates) in the consent copy | 2026-10-02 22:24 |

No GVAS PRs merged in the last 7 days. Most recent: [#44](https://github.com/ckoutz/gud-vector-agent-suite/pull/44), intake availability lead time (2026-09-22). New since last refresh: GVAS [#46](https://github.com/ckoutz/gud-vector-agent-suite/pull/46)/[#47](https://github.com/ckoutz/gud-vector-agent-suite/pull/47) and site [#10](https://github.com/ckoutz/gudvector-site/pull/10)/[#11](https://github.com/ckoutz/gudvector-site/pull/11) opened (all unmerged).
