# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-17 01:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Nothing new merged or opened since the 2026-10-16 19:06 refresh. Two PRs are still open, both clean with checks passing: GVAS #90 (4/4) and site #34 (Vercel previews success). Both are now about 3.0 days old. The Railway web service `gud-vector-agent-suite / production` is on GVAS main `6b73d70` (#92), per the GitHub deployment record (2026-10-15 02:16 UTC); the Larkspur demo is on the same commit. GVAS `/healthz` returns 200 `{"status":"ok"}` (0.28 s). `/health` and `/` return the app's own FastAPI 404 because those routes don't exist. OpenAPI lists 41 paths. Vercel production for all three site projects is on site main `89a6584`. gudvector.com resolves (216.198.79.1, Vercel) and returns 200. On both domains an unknown quote token renders "couldn't find", so both are wired to GVAS. Telnyx toll-free verification for +18775411550 is **Verified**. Stripe is in sandbox and the production webhook is `enabled` with 7 events. **Tooling note:** git fetch through the Devin git proxy returned 403 again. Data comes from the GitHub REST API (`gh api`) and public endpoints, and this file was written through the GitHub contents API.

## Demo pipeline

Status comes from public endpoints and API checks only. This refresh did not run the pipeline end to end.

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com and gudvector-site.vercel.app 200 (Resend). |
| Calendly | LIVE | Lookup/availability/booking deployed. The `/calendly/events` webhook is **not mounted** (not in OpenAPI), so scheduling-link bookings fall back to day-only matching. |
| Quote drafting | LIVE | Slack/Telnyx owner channels deployed; backend healthy. |
| Owner approval | LIVE | Slack/SMS commands, signed e-mail links and dashboard approve/decline deployed. Owner-texts tools #78–#92 deployed (web on `6b73d70`); #90 (edit customer) is PR OPEN. |
| Email/SMS delivery | LIVE | Resend e-mail. Telnyx TFV **Verified**; customer SMS goes out when `sms_consent = true`. |
| /q/\<token\> portal | LIVE | `/q/nonexistent-token` 200 "couldn't find" on both domains. |
| Stripe payment | LIVE (sandbox) | Webhook `enabled`, 7 events, livemode=false; unsigned post → 401 `invalid_signature`. |
| Customer portal | LIVE | `/v1/portal/*` deployed; site portal pages live. |
| AI booking agent | LIVE | `/v1/businesses/{key}/intake/*` deployed; site chat widget live. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#90](https://github.com/ckoutz/gud-vector-agent-suite/pull/90) | Owner texts: change a customer's name or phone in plain words, behind YES | main | MERGEABLE (clean) | 4/4 passing (alembic, ruff, pytest, mypy) on `016a657` | 3.0 days | **1**. Independent. Check the Railway web deploy afterwards. |
| gudvector-site | [#34](https://github.com/ckoutz/gudvector-site/pull/34) | Owner Settings: "Changes by text", the log of every change asked for by text | main | MERGEABLE (clean) | Vercel previews 3/3 success on `92bbc52` | 3.0 days | **2**. Any time; its backend (`GET /v1/owner/commands`, #87) is live. |

No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `6b73d70` 2026-10-15 02:16 UTC: Merge #92 (owner texts: message a customer). Main CI 4/4 success. |
| GVAS web deployed commit | **`6b73d70`** (GitHub deployment `gud-vector-agent-suite / production` success, 2026-10-15 02:17 UTC, web-production-9d848). |
| Larkspur demo (Railway) | `larkspur-demo / demo` on `6b73d70`, success (web-demo-5831). |
| GVAS web `/healthz` | **200** `{"status":"ok"}` (0.28 s) |
| GVAS web `/health`, `/` | 404 `{"detail":"Not Found"}` (0.40 s / 0.59 s): the app's own 404, because the routes don't exist. |
| gudvector-site main | `89a6584` 2026-10-12 20:22 UTC: Merge #33. Vercel Production success for gudvector-site, gudvector-sitev2 and larkspur-dashboard. |
| gudvector-site.vercel.app | `/` 200 (0.28 s); `/q/nonexistent-token` 200 "couldn't find". No "temporarily unavailable". |
| gudvector.com | Resolves (216.198.79.1, Vercel); `/` 200 (0.35 s); `/q/nonexistent-token` 200 "couldn't find". www redirects 308 to the apex. **Domain is up.** |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired and deployed. `/calendly/events` webhook not mounted (not in OpenAPI). |
| Resend | Wired: contact form, quote links, magic links, owner notification e-mails. |
| Telnyx | TFV `f77d0422-bdb9-5f33-9783-2cba342f865c` (+18775411550): **Verified** (updated 2026-10-08 16:01 UTC). |
| Stripe | Sandbox. `web-production-9d848…/webhooks/stripe`: `enabled`, 7 events. A second endpoint, `web-demo-5831.up.railway.app/webhooks/stripe`, is also `enabled` (7 events). |
| Slack | Wired; `/slack/events` deployed. |
| OpenAI | Wired (review model, free-text quotes, intake agent, owner texts). Runtime status unknown (no public probe). |
| Cloudflare R2 | Wired when `GVAS_R2_*` is set. Runtime status unknown (no public probe). |

## Owner to-do

- [ ] Merge open PRs: GVAS #90, then site #34 (independent, clean, green). No stacks.
- [ ] After merging #90, confirm the Railway **web** deploy lands. Web may not auto-deploy on push, although `6b73d70` did reach web 1 minute after merge.
- [x] Railway web is on GVAS main `6b73d70`; #88–#92 are deployed.
- [ ] Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook (`/calendly/events` not mounted).
- [x] Namecheap: registrant WHOIS verification done. gudvector.com resolves and returns 200.
- [x] gudvector.com DNS points at Vercel and serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (not verifiable from public checks).
- [x] Telnyx: toll-free verification **Verified** (2026-10-08). Customer SMS stays consent-gated.
- [ ] Fix intake threat-model finding **M1** before building more on the intake path.
- [ ] Update `docs/roadmap.md`: the owner-texts assistant (#78–#92) is not listed, and "Office manager" still reads "Not started".
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
