# Güd Vector — Project Status Dashboard

**Last updated:** 2026-09-23 01:04 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Everything for the demo pipeline is merged on `main` in both repos: GVAS has the customer portal API (#40), the AI booking intake agent with Calendly booking webhooks (#41/#42) plus the hardening follow-ups (#43, #44); gudvector-site has the portal pages (#4), the booking widget on `/contact`, `/book` and the portal (#5/#7) and the SMS opt-in fix (#6). gudvector-site.vercel.app now serves all of it (`/`, `/contact`, `/book`, `/portal`, `/q/*` all 200 and wired to GVAS). No PRs are open in GVAS; the only open PR anywhere is the stale, conflicting site rebuild PR #1. The GVAS web service on Railway answers `/healthz` 200, but whether it is running the 2026-09-22 merges cannot be verified from here (web does not auto-deploy — trigger a deploy). gudvector.com resolves and returns 200, but it still serves an old build that 404s on `/contact`, `/book` and `/q/*` — the domain is not pointed at the current `gudvector-site` project. Telnyx toll-free verification is still "Waiting For Telnyx" with an evidence request for the opt-in screenshot/link.

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector-site.vercel.app/contact 200 (Resend + intake widget). On gudvector.com it 404s (old deployment). |
| Calendly | LIVE | Customer lookup (#32) and booking (#41, #44 slot lead time) on main; booking webhooks need `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and a web deploy. |
| Quote drafting | LIVE | Structured + free-text quotes (#33) over Slack/SMS. |
| Owner approval | LIVE | `approve`/`send` over Slack; SMS owner channel works but customer SMS is blocked (see Telnyx). |
| Email/SMS delivery | BLOCKED (SMS) | Email via Resend works; SMS to customers blocked until Telnyx toll-free verification is Verified. |
| /q/\<token\> portal | LIVE | gudvector-site.vercel.app/q/nonexistent-token → 200 "couldn't find" (wired to GVAS). |
| Stripe payment | LIVE (sandbox) | Webhook endpoint `enabled`, 7 events, test mode. Live keys at cutover. |
| Customer portal | MERGED-NOT-DEPLOYED | Site #4 live (vercel.app/portal 200); GVAS #40 on main — Railway web deploy not confirmed. |
| AI booking agent | MERGED-NOT-DEPLOYED | Site #5/#7 live (vercel.app/book 200); GVAS #41–#44 on main — Railway web deploy not confirmed. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** (dirty) | Vercel preview: success | 20 days | Superseded by #3–#7 — close or rebase; do not merge as-is |

No open PRs in gud-vector-agent-suite. No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `c1c66af` 2026-09-22 22:52 UTC — Merge #44 (intake: Calendly availability from a few minutes ahead) |
| GVAS web (Railway) `/healthz` | 200 `{"status":"ok"}` in 0.32 s (`/health` and `/` are 404) |
| GVAS web deployed commit | unknown (Railway API not reachable from this session) — web does not auto-deploy; trigger after the 2026-09-22 merges |
| gudvector-site main | `62b4130` 2026-09-22 22:43 UTC — Merge #7 (intake widget on main) |
| gudvector-site.vercel.app `/` | 200 in 0.55 s |
| gudvector-site.vercel.app `/q/nonexistent-token` | 200 "couldn't find" → wired to GVAS |
| gudvector-site.vercel.app `/contact`, `/book`, `/portal` | all 200 (the earlier `/book` 404 is resolved) |
| gudvector.com | Online: resolves (216.198.79.1), 200, Vercel — but `/contact`, `/book`, `/q/*` 404 and `/portal` 307 → old deployment/project. www → 307. Point the domain at the current `gudvector-site` project. |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired (customer lookup, availability, direct booking with scheduling-link fallback). Webhook signing key required for booking confirmations. |
| Resend | Wired (contact form, quote and magic-link emails). |
| Telnyx | Owner SMS channel live. Toll-free verification for +18775411550 (`f77d0422-…`): **Waiting For Telnyx** — Telnyx asks for a screenshot/link of the digital opt-in checkbox (sms-opt-in page fixed in site #6; submit it). Customer SMS blocked until Verified. |
| Stripe | Sandbox. Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events, livemode=false. |
| Slack | Live (owner channel; field notes + quotes). |
| OpenAI | Live (review model, free-text quotes, intake agent; ceilings via `GVAS_COST_CEILING_*`). |
| Cloudflare R2 | Live when `GVAS_R2_*` set (published DOCX storage). |

## Owner to-do

- [ ] Merge open PRs in dependency order — nothing pending in GVAS; decide close vs. rebase for site PR #1 (conflicting, superseded).
- [ ] Trigger Railway **web** service deploy now that #40–#44 are on main (web does not auto-deploy on push; worker does). Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook.
- [x] Namecheap: registrant WHOIS verification — gudvector.com resolves and returns 200 again.
- [ ] Point gudvector.com DNS/Vercel domain at the current `gudvector-site` project (it currently serves an old build that 404s on `/contact`, `/book` and `/q/*`); then swap site_url/CORS/Telnyx URLs from gudvector-site.vercel.app to gudvector.com.
- [ ] Telnyx: respond to "Waiting For Telnyx" evidence request (opt-in screenshot/link); SMS to customers blocked until Verified.
- [x] Confirm Vercel deployed site #5 (`/book` returns 200 on vercel.app).
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
| gud-vector-agent-suite | [#44](https://github.com/ckoutz/gud-vector-agent-suite/pull/44) intake: query Calendly availability from a few minutes ahead, not exactly now | 2026-09-22 22:52 |
| gud-vector-agent-suite | [#43](https://github.com/ckoutz/gud-vector-agent-suite/pull/43) Harden intake webhook matching and close review gaps | 2026-09-22 22:18 |
| gud-vector-agent-suite | [#42](https://github.com/ckoutz/gud-vector-agent-suite/pull/42) Land #41 on main: AI booking intake + Calendly booking webhooks | 2026-09-22 21:46 |
| gud-vector-agent-suite | [#41](https://github.com/ckoutz/gud-vector-agent-suite/pull/41) AI booking intake: web chat agent, Calendly availability, owner-approved booking (into #40 branch) | 2026-09-22 21:43 |
| gud-vector-agent-suite | [#40](https://github.com/ckoutz/gud-vector-agent-suite/pull/40) Customer portal: magic-link login, portal API, recurring quotes with Stripe subscriptions | 2026-09-22 21:21 |
| gudvector-site | [#7](https://github.com/ckoutz/gudvector-site/pull/7) Land the AI booking intake widget on main (/contact, /book, portal) | 2026-09-22 22:43 |
| gudvector-site | [#6](https://github.com/ckoutz/gudvector-site/pull/6) sms-opt-in: remove personal address and phone, contact by email only | 2026-09-22 21:37 |
| gudvector-site | [#5](https://github.com/ckoutz/gudvector-site/pull/5) AI booking intake widget on /contact, /book and the customer portal (into #4 branch) | 2026-09-22 21:21 |
| gudvector-site | [#4](https://github.com/ckoutz/gudvector-site/pull/4) Customer portal: magic-link login, quotes & subscriptions dashboard, service requests | 2026-09-22 21:21 |
