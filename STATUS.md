# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-02 19:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

No change since the last refresh (2026-10-02 13:06 UTC) — both regressions are still open. **GVAS web on Railway is still down:** `/health`, `/healthz`, `/` and `/openapi.json` all return Railway's edge fallback `404 {"message":"Application not found"}` (`x-railway-fallback: true`), so everything behind the backend is offline — quote API, Stripe webhook delivery, portal API, intake agent, Slack/Telnyx owner channels. Cause unknown (Railway API not reachable from this run). **Telnyx toll-free verification is still Rejected** (2026-09-30 16:09 UTC, "Message Content Does Not Align with Use Case or is Incomplete"); the opt-in copy fixes (site #9 / duplicate #8) are still unmerged. **gudvector.com is online and serves the current site** (all key routes 200). No code has merged in either repo since 2026-09-22 (GVAS `c1c66af`, site `62b4130`). All blockers are owner actions, not code.

## Demo pipeline

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE (frontend) | `/contact` 200 on gudvector.com and vercel.app (Resend). Intake widget on the page depends on GVAS, which is down. |
| Calendly | BLOCKED | Lookup/availability/booking run in GVAS (down). `/calendly/events` webhook also unmounted at last good check (`GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` unset). |
| Quote drafting | BLOCKED | Runs in GVAS (web down; worker status unknown). |
| Owner approval | BLOCKED | Slack/SMS owner flow depends on GVAS web (Slack request URL, Telnyx webhook). |
| Email/SMS delivery | BLOCKED | GVAS down; customer SMS additionally blocked — Telnyx TFV **Rejected**. |
| /q/\<token\> portal | BLOCKED | Page renders 200 "couldn't find" on both hosts, but GVAS answers every path with 404, so real tokens would also show "couldn't find". |
| Stripe payment | BLOCKED | Webhook endpoint `enabled` (sandbox, 7 events) but its target `…/webhooks/stripe` is 404 on Railway. |
| Customer portal | BLOCKED | `/portal` 200 on both hosts; `/v1/portal/*` backend is down. |
| AI booking agent | BLOCKED | `/book` 200 on both hosts; `/v1/businesses/{key}/intake/*` backend is down. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gudvector-site | [#9](https://github.com/ckoutz/gudvector-site/pull/9) | sms-opt-in: state the type of SMS (business updates) in the consent copy | main | Mergeable (clean) | Vercel previews: success (2/2) | 5.1 days | **1 — merge ONE of #9/#8** (same file, same Telnyx fix; close the other), then file a new Telnyx verification |
| gudvector-site | [#8](https://github.com/ckoutz/gudvector-site/pull/8) | sms-opt-in: name the message types in the consent text (Telnyx TFV feedback) | main | Mergeable (clean) | Vercel previews: success (2/2) | 9.5 days | Duplicate of #9 — close whichever is not merged |
| gud-vector-agent-suite | [#45](https://github.com/ckoutz/gud-vector-agent-suite/pull/45) | docs: office manager design + intake agent threat model | main | Mergeable (clean) | ruff/mypy/pytest/alembic: success (4/4) | 9.5 days | 2 — docs only, no deploy needed |
| gudvector-site | [#1](https://github.com/ckoutz/gudvector-site/pull/1) | Rebuild Güd Vector marketing site as crawlable Next.js pages | main | **Conflicts** (dirty) | Vercel: success | 29.7 days | Superseded by #3–#7 — close; do not merge |

No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `c1c66af` 2026-09-22 22:52 UTC — Merge #44 (intake: Calendly availability from a few minutes ahead) |
| GVAS web (Railway) `/health` | **404 in 0.39 s** — Railway fallback "Application not found" |
| GVAS web `/healthz`, `/`, `/openapi.json` | **404** (same fallback; last 200 on `/healthz` was 2026-09-27) |
| GVAS deployment / worker | unknown (Railway API not reachable from this run) |
| gudvector-site main | `62b4130` 2026-09-22 22:43 UTC — Merge #7 (intake widget on main) |
| gudvector-site.vercel.app `/` | 200 in 0.58 s |
| gudvector-site.vercel.app `/q/nonexistent-token` | 200 "couldn't find" in 0.38 s (site env set; see GVAS-down caveat above) |
| gudvector-site.vercel.app `/contact`, `/book`, `/portal`, `/sms-opt-in` | all 200 |
| gudvector.com | **Online and current:** `/` 200 in 0.39 s (Vercel, `216.198.79.1`); `www` 308 → apex; `/contact`, `/book`, `/portal`, `/sms-opt-in` 200; `/q/nonexistent-token` 200 "couldn't find" |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Code wired; **down with GVAS web**. Booking-confirmation webhook was unmounted at last good check (`GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` unset). |
| Resend | Site contact form live; GVAS-sent emails (quotes, magic links) down with GVAS. |
| Telnyx | Toll-free verification +18775411550 (`f77d0422-…`): **Rejected** (last updated 2026-09-30 16:09 UTC) — "Message Content Does Not Align with Use Case or is Incomplete". Customer SMS blocked. Owner SMS webhook down with GVAS. |
| Stripe | Sandbox. Webhook `…/webhooks/stripe`: `enabled`, 7 enabled events, livemode=false — but target URL currently 404. |
| Slack | Down with GVAS web (request URL on Railway). |
| OpenAI | Configured in GVAS; unusable while GVAS is down. |
| Cloudflare R2 | Configured when `GVAS_R2_*` set; status unknown while GVAS is down. |

## Owner to-do

- [ ] **Restore GVAS web on Railway** — `web-production-9d848.up.railway.app` returns "Application not found". Check the web service still exists/is not paused, its latest deploy succeeded, and the generated domain is still attached; redeploy `main` (`c1c66af`). If the domain changed, update the GVAS URL on Vercel, the Stripe webhook URL, Slack request URL and Telnyx webhook URL.
- [ ] Merge open PRs in dependency order: site #9 **or** #8 (duplicates — merge one, close the other), then GVAS #45 (docs); close site #1 (conflicting, superseded). No stacked PRs.
- [ ] Trigger Railway **web** deploy after backend merges (web does not auto-deploy on push; worker does).
- [ ] Railway web: set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY`, redeploy, and register the Calendly webhook at `…/calendly/events`.
- [x] Namecheap: registrant WHOIS verification — gudvector.com resolves and returns 200.
- [x] Point gudvector.com at the current `gudvector-site` Vercel project — confirmed: `/contact`, `/book`, `/sms-opt-in`, `/q/*` 200.
- [ ] Swap site_url/CORS/Telnyx URLs from gudvector-site.vercel.app to gudvector.com (not verifiable from here).
- [ ] Telnyx: verification **Rejected** (2026-09-30). Merge the opt-in copy fix (#9/#8), confirm it is live on gudvector.com/sms-opt-in, then submit a new/appealed toll-free verification with use case + sample messages matching the opt-in text. SMS to customers blocked until Verified.
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2).
- [ ] Stripe: switch to live keys at production cutover; Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md, main)

- **Done:** field-note intake (text/voice), completeness review + contradiction pass, DOCX publish + R2 storage, email report, Telnyx owner SMS (quotes only), cost ceilings, Calendly customer lookup, free-text quotes, portal quote handoff, hosted `/q/<token>` quotes with Stripe Checkout, customer portal + recurring quotes/subscriptions, AI booking intake with owner-approved Calendly booking.
- **In progress:** none (2026-09 audit follow-ups all landed: #23, #25, #26, #28).
- **Next:** 1) Letterhead DOCX templates per business — **needs decision:** who supplies the template and where the binding manifest lives. 2) Retention and redaction of transcripts, media, reports.
- **Open follow-ups (not built):** Stripe Connect per-business payouts; contact-form API behind business `public_key`; per-business timezone; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes over SMS.

## Recent changes (merged, last 7 days)

None — no PRs merged in either repo since 2026-09-22 22:52 UTC (GVAS #44 / site #7).
