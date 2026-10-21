# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-21 07:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Nothing is broken or blocked. Since the 01:05 refresh, site #35 (SMS consent wording: names Güd Vector, frequency, HELP, Terms/Privacy links) was merged at 01:36 UTC. Vercel production for all three site projects is now on site main `97afbcd` (#35). There are **no open PRs** in either repo. GVAS main is unchanged at `af29c7a` (#95), with main CI 4/4 green, and Railway web is deployed on that commit. GVAS `/healthz` returns 200 `{"status":"ok"}` (0.32 s). gudvector.com and gudvector-site.vercel.app both return 200 and show "couldn't find" for an unknown quote token, so the site is wired to GVAS. Telnyx TFV for +18775411550 is **Verified**. Stripe is in sandbox and its production webhook is `enabled` with 7 events. Carried-forward gaps: the Larkspur demo has no deployment record for `af29c7a`, and the Calendly webhook is not mounted. **Tooling note:** git fetch through the Devin git proxy returned 403 again. Data comes from `gh api` and public endpoints, and this file was written through the GitHub contents API.

## Demo pipeline

Status comes from public endpoints and API checks only. This refresh did not run the pipeline end to end.

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com and gudvector-site.vercel.app 200 (Resend). The consent wording update (#35) is live on Vercel prod `97afbcd`. |
| Calendly | LIVE | Lookup/availability/booking deployed. The `/calendly/events` webhook is not mounted (carried forward, not re-checked), so scheduling-link bookings fall back to day-only matching. |
| Quote drafting | LIVE | Slack/Telnyx owner channels deployed; backend healthy. |
| Owner approval | LIVE | Slack/SMS commands, signed e-mail links and dashboard approve/decline deployed. Web is on `af29c7a` (#95). |
| Email/SMS delivery | LIVE | Resend e-mail. Telnyx TFV **Verified**; customer SMS goes out when `sms_consent = true`. |
| /q/\<token\> portal | LIVE | `/q/nonexistent-token` 200 "couldn't find" on both domains. |
| Stripe payment | LIVE (sandbox) | Webhook `enabled`, 7 events, livemode=false. |
| Customer portal | LIVE | `/v1/portal/*` deployed; site portal pages live (Vercel prod `97afbcd`). |
| AI booking agent | LIVE | `/v1/businesses/{key}/intake/*` deployed; site chat widget live. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| — | — | No open PRs in gud-vector-agent-suite or gudvector-site | — | — | — | — | — |

No stacked PRs.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `af29c7a` 2026-10-20 23:18 UTC: #95, Telnyx second owner number. Main CI 4/4 success (ruff, mypy, alembic, pytest). |
| GVAS web deployed commit | **`af29c7a`** (main). GitHub deployment `gud-vector-agent-suite / production` success at 2026-10-20 23:20 UTC. No deploy gap. |
| Larkspur demo (Railway) | Latest `larkspur-demo / demo` record: `bccabb5` (#93), marked `inactive` at 2026-10-20 23:20 UTC. **No record for `af29c7a`**, so the demo's current state is unknown. `web-demo-5831.up.railway.app` was not probed because it is not on this session's network allowlist. |
| GVAS web `/healthz` | **200** `{"status":"ok"}` (0.32 s; 0.40 s on a re-check) |
| GVAS web `/health`, `/` | 404 `{"detail":"Not Found"}` (0.38 s): the app's own 404, because those routes don't exist. |
| gudvector-site main | `97afbcd` 2026-10-21 01:36 UTC: Merge #35. Vercel Production success for gudvector-site, gudvector-sitev2 and larkspur-dashboard (01:37 UTC). |
| gudvector-site.vercel.app | `/` 200 (0.32 s); `/q/nonexistent-token` 200 "couldn't find" (1.39 s). No "temporarily unavailable". |
| gudvector.com | Resolves (216.198.79.1, Vercel); `/` 200 (0.14 s); `/q/nonexistent-token` 200 "couldn't find" (0.59 s). www redirects 308 to the apex. **Domain is up.** |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired and deployed. `/calendly/events` webhook not mounted (carried forward). |
| Resend | Wired: contact form, quote links, magic links, owner notification e-mails. |
| Telnyx | TFV `f77d0422-bdb9-5f33-9783-2cba342f865c` (+18775411550): **Verified** (updated 2026-10-08 16:01 UTC). Campaign text status is not checked here. |
| Stripe | Sandbox. `web-production-9d848…/webhooks/stripe`: `enabled`, 7 events, livemode=false. A second endpoint, `web-demo-5831.up.railway.app/webhooks/stripe`, is also `enabled` (7 events). |
| Slack | Wired; `/slack/events` deployed. |
| OpenAI | Wired (review model, free-text quotes, intake agent, owner texts). Runtime status unknown (no public probe). |
| Cloudflare R2 | Wired when `GVAS_R2_*` is set. Runtime status unknown (no public probe). |

## Owner to-do

- [x] Merge site #35 (merged 2026-10-21 01:36 UTC; live on Vercel prod `97afbcd`). Merge open PRs in dependency order: none open now.
- [ ] Confirm the new Telnyx campaign text, which matches the #35 consent wording, was approved or submitted (not verifiable here).
- [x] Railway web is on GVAS main `af29c7a` (#95), deployed 2026-10-20 23:20 UTC. Web does not auto-deploy on push; the worker does.
- [ ] Confirm the Larkspur demo service is running and decide whether it needs `af29c7a`. Its last deployment record (`bccabb5`) went `inactive` at 2026-10-20 23:20 UTC with no newer one.
- [ ] Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook (`/calendly/events` not mounted).
- [x] Namecheap: registrant WHOIS verification done. gudvector.com resolves and returns 200.
- [x] gudvector.com DNS points at Vercel and serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (not verifiable from public checks).
- [x] Telnyx: toll-free verification **Verified** (2026-10-08). Customer SMS stays consent-gated.
- [ ] Fix intake threat-model finding **M1** before building more on the intake path.
- [ ] Update `docs/roadmap.md`: the owner-texts assistant (#78–#95) is not listed, and "Office manager" still reads "Not started".
- [ ] Decide whether the second Stripe webhook endpoint (`web-demo-5831.up.railway.app`) should stay enabled.
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor, Vercel).
- [ ] Stripe: switch to live keys at production cutover. Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md on main, last changed `2348d85` 2026-10-12)

- **Done:** field-note intake (text/voice) and append, evidence-only report, review-model benchmark, contradiction pass, DOCX publish and publish-failure notice, worker logging, unmatched-message triggers, R2 storage, checklist evidence annotator, plan custody, email report, Telnyx owner SMS (quotes), cost ceilings, Calendly lookup, free-text quotes, portal quote handoff, hosted customer quotes + Stripe Checkout, customer portal + recurring quotes, AI booking intake, per-business intake profile, owner notification e-mail, owner dashboard backend, customer SMS consent, booked-mode intake (reschedule/cancel, signed decision links), unified owner notification e-mails, channel-agnostic owner understanding step.
- **In progress:** "Audit follow-ups (2026-09): all landed". The doc does not yet reflect the owner-texts work merged in #78–#95.
- **Next (ordered):** 1) Letterhead DOCX templates per business. **Needs decision:** who supplies the template and where the binding manifest lives. 2) Office manager owner assistant over Slack/SMS, with every write behind the owner's YES, owner-only, existing keyword commands matched first. Marked "Not started; design review pending". **Needs decision:** D1–D6 in `docs/office_manager.md`. Prerequisite: fix M1. Later: flag cancel/reschedule hidden in customer notes. 3) Retention and redaction of transcripts, media, reports.
- **Open follow-ups (not built):** Stripe Connect per-business payouts (`businesses.stripe_account_id` unused); contact-form API behind business `public_key`; per-business timezone for Calendly windows/timestamps; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes/voice/reports over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gudvector-site | [#35](https://github.com/ckoutz/gudvector-site/pull/35) SMS consent: name Güd Vector, frequency, HELP, Terms/Privacy links; owners opt in by texting | 2026-10-21 01:36 |
| gud-vector-agent-suite | [#95](https://github.com/ckoutz/gud-vector-agent-suite/pull/95) Telnyx: a second number may answer the one pilot owner; customer texts keep the first | 2026-10-20 23:18 |
| gudvector-site | [#36](https://github.com/ckoutz/gudvector-site/pull/36) Owner decline reason: cap the input at 200, the length the customer is sent | 2026-10-20 19:08 |
| gud-vector-agent-suite | [#93](https://github.com/ckoutz/gud-vector-agent-suite/pull/93) Demo: owner texts block and unblock time on the visitor's copy; blocks show on its dashboard | 2026-10-20 18:28 |
| gud-vector-agent-suite | [#94](https://github.com/ckoutz/gud-vector-agent-suite/pull/94) Chat view: the owner's decline note, once the request is declined | 2026-10-20 15:48 |
| gudvector-site | [#34](https://github.com/ckoutz/gudvector-site/pull/34) Owner Settings: "Changes by text", the log of every change asked for by text | 2026-10-20 15:14 |
| gud-vector-agent-suite | [#90](https://github.com/ckoutz/gud-vector-agent-suite/pull/90) Owner texts: change a customer's name or phone in plain words, behind YES | 2026-10-20 15:11 |
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
