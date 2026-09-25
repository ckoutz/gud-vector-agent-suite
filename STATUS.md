# Güd Vector — Project Status Dashboard

**Last updated:** 2026-09-25 19:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Everything for the demo pipeline is merged on `main` in both repos (GVAS #40–#44, site #4–#7) and gudvector-site.vercel.app serves all of it (`/`, `/contact`, `/book`, `/portal`, `/q/*` all 200 and wired to GVAS). No change since the 2026-09-23/24 refreshes (main unchanged in both repos since 2026-09-22): the same three PRs are open — GVAS #45 (docs only: office-manager design + intake threat model, CI green) and site #8 (sms-opt-in consent text names the message types — the fix Telnyx asked for; Vercel preview green, mergeable). Telnyx toll-free verification is still **"Waiting For Customer"** (last updated 2026-09-23 05:19 UTC): they want the opt-in text to state what type of SMS is sent — merge site #8, let Vercel deploy, then resubmit the opt-in URL/screenshot (site #8 has now been open ~61 h). The GVAS web service on Railway answers `/healthz` 200 (0.36 s), but whether it runs the 2026-09-22 merges still cannot be verified from here (web does not auto-deploy — trigger a deploy). gudvector.com resolves and returns 200 but still serves an old build (`/contact`, `/book`, `/q/*` 404) — the domain is not pointed at the current `gudvector-site` project.

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector-site.vercel.app/contact 200 (Resend + intake widget). On gudvector.com it 404s (old deployment). |
| Calendly | LIVE | Customer lookup (#32) and booking (#41, #44) on main; booking webhooks need `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and a web deploy. |
| Quote drafting | LIVE | Structured + free-text quotes (#33) over Slack/SMS. |
| Owner approval | LIVE | `approve`/`send` over Slack; SMS owner channel works but customer SMS is blocked (see Telnyx). |
| Email/SMS delivery | BLOCKED (SMS) | Email via Resend works; SMS to customers blocked until Telnyx toll-free verification is Verified (now "Waiting For Customer" — action on us). |
| /q/\<token\> portal | LIVE | gudvector-site.vercel.app/q/nonexistent-token → 200 "couldn't find" (wired to GVAS). |
| Stripe payment | LIVE (sandbox) | Webhook endpoint `enabled`, 7 events, test mode. Live keys at cutover. |
| Customer portal | MERGED-NOT-DEPLOYED | Site #4 live (vercel.app/portal 200); GVAS #40 on main — Railway web deploy not confirmed. |
| AI booking agent | MERGED-NOT-DEPLOYED | Site #5/#7 live (vercel.app/book 200); GVAS #41–#44 on main — Railway web deploy not confirmed. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gudvector-site | [#8](https://github.com/ckoutz/gudvector-site/pull/8) | sms-opt-in: name the message types in the consent text (Telnyx TFV feedback) | main | Mergeable (clean) | Vercel previews: success | 61 h | **1 — merge first** (unblocks Telnyx resubmission) |
| gud-vector-agent-suite | [#45](https://github.com/ckoutz/gud-vector-agent-suite/pull/45) | docs: office manager design + intake agent threat model | main | Mergeable (clean) | ruff/mypy/pytest/alembic: all success | 61 h | 2 — docs only, no deploy needed |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** | Vercel: success | 23 days | Superseded by #3–#7 — close or rebase; do not merge as-is |

No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `c1c66af` 2026-09-22 22:52 UTC — Merge #44 (intake: Calendly availability from a few minutes ahead) |
| GVAS web (Railway) `/healthz` | 200 in 0.36 s (`/health` and `/` are 404) |
| GVAS web deployed commit | unknown (Railway API not queried from this session) — web does not auto-deploy; trigger after the 2026-09-22 merges |
| gudvector-site main | `62b4130` 2026-09-22 22:43 UTC — Merge #7 (intake widget on main) |
| gudvector-site.vercel.app `/` | 200 in 0.51 s |
| gudvector-site.vercel.app `/q/nonexistent-token` | 200 "couldn't find" in 1.68 s → wired to GVAS |
| gudvector-site.vercel.app `/contact`, `/book`, `/portal`, `/sms-opt-in` | all 200 |
| gudvector.com | Online: 200 in 0.34 s (served by Vercel, resolves to 216.198.79.1); www → 307 to apex — but `/contact`, `/book`, `/sms-opt-in`, `/q/*` 404 and `/portal` 307 → old deployment/project. Point the domain at the current `gudvector-site` project. |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired (customer lookup, availability, direct booking with scheduling-link fallback). Webhook signing key required for booking confirmations. |
| Resend | Wired (contact form, quote and magic-link emails). |
| Telnyx | Owner SMS channel live. Toll-free verification for +18775411550 (`f77d0422-…`): **Waiting For Customer** — reason: "Opt-in must express what type of SMS, this should match the use case and use case summary". Fix is site PR #8; merge, deploy, resubmit. Customer SMS blocked until Verified. |
| Stripe | Sandbox. Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events, livemode=false. |
| Slack | Live (owner channel; field notes + quotes). |
| OpenAI | Live (review model, free-text quotes, intake agent; ceilings via `GVAS_COST_CEILING_*`). |
| Cloudflare R2 | Live when `GVAS_R2_*` set (published DOCX storage). |

## Owner to-do

- [ ] Merge open PRs in dependency order: site #8 first (Telnyx fix), then GVAS #45 (docs); decide close vs. rebase for site PR #1 (conflicting, superseded).
- [ ] Trigger Railway **web** service deploy now that #40–#44 are on main (web does not auto-deploy on push; worker does). Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook.
- [x] Namecheap: registrant WHOIS verification — gudvector.com resolves and returns 200 again.
- [ ] Point gudvector.com DNS/Vercel domain at the current `gudvector-site` project (it currently serves an old build that 404s on `/contact`, `/book` and `/q/*`); then swap site_url/CORS/Telnyx URLs from gudvector-site.vercel.app to gudvector.com.
- [ ] Telnyx: verification is "Waiting For Customer" — after site #8 deploys, resubmit the opt-in page URL/screenshot with the message types named; SMS to customers blocked until Verified.
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
