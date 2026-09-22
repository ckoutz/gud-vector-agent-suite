# Güd Vector — Project Status Dashboard

**Last updated:** 2026-09-22 21:58 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Both repos landed their big pilot features today: GVAS main now has the customer portal API (#40) and the AI booking intake agent with Calendly booking webhooks (#41/#42), and gudvector-site main has the portal pages (#4), the booking widget (#5) and the SMS opt-in page fix (#6). No PRs are open in GVAS; the only open PR anywhere is the stale, conflicting site rebuild PR #1. The GVAS web service on Railway answers `/healthz` 200, but it is not verifiable from here whether it is running today's merges (web does not auto-deploy — trigger a deploy). gudvector.com is back online (Vercel, 200), but it is serving an older site that 404s on `/contact` and `/q/*`, so the production domain is not yet pointed at the current `gudvector-site` project. Telnyx toll-free verification is still pending with a request for opt-in screenshot evidence.

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector-site.vercel.app/contact 200 (Resend). On gudvector.com it 404s (old deployment). |
| Calendly | LIVE | Customer lookup (#32) and booking (#41) in main; booking webhooks need `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and a web deploy. |
| Quote drafting | LIVE | Structured + free-text quotes (#33) over Slack/SMS. |
| Owner approval | LIVE | `approve`/`send` over Slack; SMS owner channel works but customer SMS is blocked (see Telnyx). |
| Email/SMS delivery | BLOCKED (SMS) | Email via Resend works; SMS to customers blocked until Telnyx toll-free verification is Verified. |
| /q/\<token\> portal | LIVE | gudvector-site.vercel.app/q/nonexistent-token → 200 "couldn't find" (wired to GVAS). |
| Stripe payment | LIVE (sandbox) | Webhook endpoint `enabled`, 7 events, test mode. Live keys at cutover. |
| Customer portal | MERGED-NOT-DEPLOYED | Site #4 merged (vercel.app/portal 200); GVAS #40 merged — Railway web deploy not confirmed. |
| AI booking agent | MERGED-NOT-DEPLOYED | Site #5 merged (`/book` currently 404 on vercel.app — check Vercel deploy); GVAS #41/#42 merged — Railway web deploy not confirmed. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** (dirty) | passing | 19 days | Superseded by #3–#6 — close or rebase; do not merge as-is |

No open PRs in gud-vector-agent-suite. No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `543e2ce` 2026-09-22 21:46 UTC — Merge #42 (AI booking intake + Calendly webhooks) |
| GVAS web (Railway) `/healthz` | 200 `{"status":"ok"}` in 0.40 s (`/health` and `/` are 404) |
| GVAS web deployed commit | unknown (Railway API not reachable from this session) — web does not auto-deploy; trigger after today's merges |
| gudvector-site main | `c1c340a` 2026-09-22 21:37 UTC — Merge #6 (sms-opt-in contact fix) |
| gudvector-site.vercel.app `/` | 200 in 0.42 s |
| gudvector-site.vercel.app `/q/nonexistent-token` | 200 "couldn't find" → wired to GVAS |
| gudvector-site.vercel.app `/portal` | 200; `/book` 404 (verify latest Vercel deploy of #5; `/contact` 200, `/q/*` 200) |
| gudvector.com | **Back online**: resolves (216.198.79.1), 200, served by Vercel — but `/contact`, `/q/*` 404 and `/portal` 307 → old deployment/project. Point the domain at the current `gudvector-site` project. |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired (customer lookup, availability, direct booking with scheduling-link fallback). Webhook signing key required for booking confirmations. |
| Resend | Wired (contact form, quote and magic-link emails). |
| Telnyx | Owner SMS channel live. Toll-free verification for +18775411550 (`f77d0422-…`): **Waiting For Telnyx** — Telnyx asks for a screenshot/link of the digital opt-in checkbox (sms-opt-in page fixed in site #6; submit it). Customer SMS blocked until Verified. |
| Stripe | Sandbox. Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events, livemode=false. Enabled set covers checkout.session.*, invoice.paid/payment_failed, customer.subscription.updated/deleted (subscriptions from #40 covered). |
| Slack | Live (owner channel; field notes + quotes). |
| OpenAI | Live (review model, free-text quotes, intake agent; ceilings via `GVAS_COST_CEILING_*`). |
| Cloudflare R2 | Live when `GVAS_R2_*` set (published DOCX storage). |

## Owner to-do

- [ ] Merge open PRs in dependency order — nothing pending in GVAS; decide close vs. rebase for site PR #1 (conflicting, superseded).
- [ ] Trigger Railway **web** service deploy now that #40/#41/#42 are on main (web does not auto-deploy on push; worker does). Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook.
- [x] Namecheap: registrant WHOIS verification — gudvector.com resolves and returns 200 again.
- [ ] Point gudvector.com DNS/Vercel domain at the current `gudvector-site` project (it currently serves an old build that 404s on `/contact` and `/q/*`); then swap site_url/CORS/Telnyx URLs from gudvector-site.vercel.app to gudvector.com.
- [ ] Telnyx: respond to "Waiting For Telnyx" evidence request (opt-in screenshot/link); SMS to customers blocked until Verified.
- [ ] Confirm Vercel deployed site #5 (`/book` returns 404 on vercel.app).
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2).
- [ ] Stripe: switch to live keys at production cutover; Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md)

- **Done:** field-note intake (text/voice), completeness review, DOCX publish + R2 storage, email report, Telnyx owner SMS (quotes only), cost ceilings, Calendly customer lookup, free-text quotes, portal quote handoff, hosted `/q/<token>` quotes with Stripe Checkout, customer portal + recurring quotes/subscriptions, AI booking intake with owner-approved Calendly booking.
- **In progress:** none (2026-09 audit follow-ups all landed).
- **Next:** 1) Letterhead DOCX templates per business — **needs decision:** who supplies the template and where the binding manifest lives. 2) Retention and redaction of transcripts, media, reports.
- **Open follow-ups (not built):** Stripe Connect per-business payouts; contact-form API behind business `public_key`; per-business timezone; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gud-vector-agent-suite | [#42](https://github.com/ckoutz/gud-vector-agent-suite/pull/42) Land #41 on main: AI booking intake + Calendly booking webhooks | 2026-09-22 21:46 |
| gud-vector-agent-suite | [#41](https://github.com/ckoutz/gud-vector-agent-suite/pull/41) AI booking intake: web chat agent, Calendly availability, owner-approved booking | 2026-09-22 21:43 |
| gud-vector-agent-suite | [#40](https://github.com/ckoutz/gud-vector-agent-suite/pull/40) Customer portal: magic-link login, portal API, recurring quotes with Stripe subscriptions | 2026-09-22 21:21 |
| gudvector-site | [#6](https://github.com/ckoutz/gudvector-site/pull/6) sms-opt-in: remove personal address and phone, contact by email only | 2026-09-22 21:37 |
| gudvector-site | [#5](https://github.com/ckoutz/gudvector-site/pull/5) AI booking intake widget on /contact, /book and the customer portal | 2026-09-22 21:21 |
| gudvector-site | [#4](https://github.com/ckoutz/gudvector-site/pull/4) Customer portal: magic-link login, quotes & subscriptions dashboard, service requests | 2026-09-22 21:21 |
