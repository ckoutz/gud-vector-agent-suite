# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-02 22:40 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Two things changed for the worse since the last refresh (2026-09-22). First, the GVAS web service URL `web-production-9d848.up.railway.app` now returns Railway's own `404 Application not found` on every path (`/healthz`, `/`, `/docs`), so the backend is **down or was removed/renamed** at that URL — Stripe and Telnyx webhooks, the Calendly webhook and the booking widget all point at it. Second, Telnyx **Rejected** the toll-free verification for +18775411550 ("Message Content Does Not Align with Use Case or is Incomplete"); the consent-copy fix (site #9) is live on gudvector.com/sms-opt-in, so the request must be resubmitted. On the bright side, gudvector.com now serves the current gudvector-site build (`/contact`, `/portal`, `/q/*` all 200) and `/q/nonexistent-token` renders the "couldn't find" page on both domains (note: the site also shows that page when GVAS answers 404, so this no longer proves the backend is reachable). GVAS main also carries two post-dashboard merges (#43 intake webhook hardening, #44 availability lead time) plus the earlier #40–#42, none of which can be confirmed as deployed. Four PRs are open: GVAS #45 (docs: office-manager design + intake threat model, clean), site #10 (chat bubble replaces `/book`, clean), site #8 (sms-opt-in message types — conflicts with merged #9, likely redundant) and the stale site #1 (conflicting, superseded).

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com/contact and gudvector-site.vercel.app/contact 200 (Resend). |
| Calendly | BLOCKED | Code in main (#32, #41, #44) but the GVAS web URL answers 404 "Application not found" — nothing server-side is reachable. |
| Quote drafting | BLOCKED | Slack/SMS quote flow is served by the GVAS web/worker services; web URL is down (worker state unknown). |
| Owner approval | BLOCKED | Same as above; `approve`/`send` over Slack depend on the Slack request URL on the web service. |
| Email/SMS delivery | BLOCKED | Email via Resend depends on GVAS; customer SMS blocked until Telnyx TFV is Verified (currently **Rejected**). |
| /q/\<token\> portal | LIVE (frontend) / BLOCKED (backend) | gudvector.com/q/* and vercel.app/q/* 200 with "couldn't find", but real tokens cannot resolve while GVAS is down. |
| Stripe payment | BLOCKED | Webhook endpoint `enabled` (7 events, sandbox) but points at the down GVAS URL — events will fail delivery. |
| Customer portal | MERGED-NOT-DEPLOYED / BLOCKED | Site pages live (gudvector.com/portal 200); GVAS #40 API unreachable. |
| AI booking agent | MERGED-NOT-DEPLOYED / BLOCKED | Site #7 live (`/book` 200 on vercel.app and gudvector.com); PR #10 moves it to a chat bubble; GVAS #41–#44 unreachable. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#45](https://github.com/ckoutz/gud-vector-agent-suite/pull/45) | docs: office manager design + intake agent threat model | main | clean | passing (ruff, mypy, pytest, alembic) | 10 days | 1 — docs only (3 files); merge any time. Flags **M1** (intake can link to an existing customer by unverified email) as a prerequisite before building the office manager. |
| gudvector-site | [#10](https://github.com/ckoutz/gudvector-site/pull/10) | Replace /book with a floating chat bubble on public pages | main | clean | passing (Vercel previews) | <1 day | 2 — independent; removes `/book` (308 → `/contact`). |
| gudvector-site | [#8](https://github.com/ckoutz/gudvector-site/pull/8) | sms-opt-in: name the message types in the consent text (Telnyx TFV feedback) | main | **Conflicts** (dirty) | passing | 10 days | 3 — superseded by merged #9 (same copy fix); close unless it adds wording #9 lacks. |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** (dirty) | passing | 30 days | Superseded by #3–#9 — close; do not merge as-is. |

No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `c1c66af` 2026-09-22 22:52 UTC — Merge #44 (intake availability lead time); CI green on main |
| GVAS web (Railway) `/healthz` | **404** `{"status":"error","code":404,"message":"Application not found"}` from `railway-hikari` with `x-railway-fallback: true` in 0.30 s — `/health`, `/`, `/docs` also 404. The service is not bound to this domain (deleted, renamed, or domain removed). Was 200 on 2026-09-22. |
| GVAS web deployed commit | unknown (service unreachable; Railway API not reachable from this session) |
| gudvector-site main | `390a677` 2026-10-02 22:24 UTC — Merge #9 (sms-opt-in consent copy names message type) |
| gudvector-site.vercel.app | `/` 200 (0.60 s); `/q/nonexistent-token` 200 "couldn't find"; `/contact`, `/book`, `/portal`, `/sms-opt-in` all 200 |
| gudvector.com | Resolves (216.198.79.1), 200 in 0.37 s, **now serving the current site**: `/contact` 200, `/portal` 200, `/q/x` 200 "couldn't find". `www.gudvector.com` 308 → apex. |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired in code (lookup, availability, direct booking + scheduling-link fallback, `/calendly/events` webhook). Unreachable while GVAS web is down; webhook route is unmounted in prod until `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` is set (threat model L2, PR #45). |
| Resend | Wired (contact form on the site works independently; quote/magic-link emails depend on GVAS). |
| Telnyx | Owner SMS channel wired. Toll-free verification for +18775411550 (`f77d0422-…`): **Rejected** — "Message Content Does Not Align with Use Case or is Incomplete". Consent copy now names message types (site #9, live). Resubmit the verification pointing at https://gudvector.com/sms-opt-in. Customer SMS blocked until Verified. |
| Stripe | Sandbox. Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events (checkout.session.*, invoice.paid/payment_failed, customer.subscription.updated/deleted), livemode=false — but the target URL currently 404s, so deliveries fail. |
| Slack | Wired (owner channel; field notes + quotes). Request URL points at the down GVAS web service → slash/event delivery failing. |
| OpenAI | Wired (review model, free-text quotes, intake agent; ceilings via `GVAS_COST_CEILING_*`). |
| Cloudflare R2 | Wired when `GVAS_R2_*` set (published DOCX storage). |

## Owner to-do

- [ ] **Restore the GVAS web service on Railway** — `web-production-9d848.up.railway.app` returns "Application not found". Check whether the service/domain was deleted or renamed; if the URL changed, update the Stripe webhook endpoint, Slack request URL, Telnyx webhook, and the site's `GVAS_*` env vars on Vercel. Then deploy main (`c1c66af`, includes #40–#44).
- [ ] Merge open PRs in dependency order: GVAS #45 (docs) → site #10 (chat bubble). Close site #8 (superseded by #9) and site #1 (superseded, conflicting).
- [ ] Trigger Railway **web** service deploy after backend merges (web does not auto-deploy on push; worker does). Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook.
- [x] Namecheap: registrant WHOIS verification — gudvector.com resolves and returns 200.
- [x] Point gudvector.com at the current `gudvector-site` project — `/contact`, `/portal`, `/q/*` now 200 on the apex domain.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (verify once the backend is back).
- [ ] Telnyx: verification **Rejected** — resubmit `f77d0422-…` with the updated opt-in page (https://gudvector.com/sms-opt-in, site #9) and a screenshot of the consent checkbox; SMS to customers blocked until Verified.
- [x] Confirm Vercel deployed site #5/#7 — `/book` returns 200 on vercel.app (will redirect to `/contact` once #10 merges).
- [ ] Fix intake threat-model finding **M1** (anonymous intake linking to an existing customer by unverified email) before building the office manager (PR #45).
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor).
- [ ] Stripe: switch to live keys at production cutover; Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md on main)

- **Done:** field-note intake (text/voice), completeness review, DOCX publish + R2 storage, email report, Telnyx owner SMS (quotes only), cost ceilings, Calendly customer lookup, free-text quotes, portal quote handoff, hosted `/q/<token>` quotes with Stripe Checkout, customer portal + recurring quotes/subscriptions, AI booking intake with owner-approved Calendly booking.
- **In progress:** none (2026-09 audit follow-ups all landed).
- **Next:** 1) Letterhead DOCX templates per business — **needs decision:** who supplies the template and where the binding manifest lives. 2) Retention and redaction of transcripts, media, reports. PR #45 inserts an **Office manager** owner assistant (routing layer over existing actions, every write needs `yes <ref>`, owner-only) as item 2 — not started, design review pending, **needs decisions D1–D6**, prerequisite M1.
- **Open follow-ups (not built):** Stripe Connect per-business payouts; contact-form API behind business `public_key`; per-business timezone; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gudvector-site | [#9](https://github.com/ckoutz/gudvector-site/pull/9) sms-opt-in: state the type of SMS (business updates) in the consent copy | 2026-10-02 22:24 |

No GVAS PRs merged in the last 7 days. Most recent (2026-09-22): [#44](https://github.com/ckoutz/gud-vector-agent-suite/pull/44) intake availability lead time, [#43](https://github.com/ckoutz/gud-vector-agent-suite/pull/43) harden intake webhook matching; site [#7](https://github.com/ckoutz/gudvector-site/pull/7) landed the booking widget on main.
