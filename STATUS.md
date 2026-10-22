# Güd Vector — Project Status Dashboard

**Last updated:** 2026-10-22 13:05 UTC (auto-refreshed by the Devin dashboard automation)

## Where we are

Nothing changed since the 07:05 UTC refresh: no merges, no new deployments, same two open GVAS PRs. **Deploy gap (still open, ~9 h):** GVAS main is now `a5ed75d` (#98, merged 03:55 UTC). #96 merged at 02:43 UTC with migration `0033`. The most recent GitHub deployment for Railway production and the Larkspur demo is still `af29c7a` (#95, 2026-10-20 23:19 UTC). So #96 (owner-text customers, draft/send/void invoices) and #98 (owner texts reach the same business as Slack) are **merged but not deployed** on web. Trigger a Railway web deploy. The Larkspur demo's last `larkspur-demo / demo` deployment record is `bccabb5`, though its web/worker commit statuses report success on `af29c7a`; nothing is recorded for #96/#98. Main CI is 4/4 green. No Railway commit status (web or worker) is posted on `a5ed75d`, so the worker may not have redeployed either. `/healthz` returns 200 `{"status":"ok"}` (0.33–0.40 s). Site main is `f82cb5d` (#46, phone-only customers). Vercel reports success on it for gudvector-site, gudvector-sitev2 and larkspur-dashboard. Site #46 depends on GVAS #96 (null customer e-mail), which only takes effect after the web deploy. Two GVAS PRs are open. [#97](https://github.com/ckoutz/gud-vector-agent-suite/pull/97) (natural yeses confirm) is mergeable and green. [#99](https://github.com/ckoutz/gud-vector-agent-suite/pull/99) (scheduling engine V1a) is a **draft marked "Do not merge yet"**. No site PRs are open. gudvector.com and gudvector-site.vercel.app both return 200 and show "couldn't find" for an unknown quote token. Telnyx TFV for +18775411550 is **Verified**. Stripe is in sandbox, and its production webhook is `enabled` with 7 events. **Tooling note:** git fetch through the Devin git proxy returned 403 again. Data comes from `gh api` and public endpoints, and this file was written through the GitHub contents API.

## Demo pipeline

Status comes from public endpoints and API checks only. This refresh did not run the pipeline end to end.

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | gudvector.com and gudvector-site.vercel.app 200 (Resend); Vercel prod `f82cb5d` (#46). |
| Calendly | LIVE | Lookup/availability/booking deployed. The `/calendly/events` webhook is not mounted (POST returns 404, re-checked). Scheduling engine V1a is in #99 (draft, PR OPEN, not to merge yet). |
| Quote drafting | LIVE / MERGED-NOT-DEPLOYED | Slack/Telnyx owner channels are live on `af29c7a`. Owner-text customers and invoices (#96) and the same-business startup check (#98) are merged but not deployed. |
| Owner approval | LIVE / PR OPEN | Slack/SMS commands, signed e-mail links and dashboard approve/decline are live. Natural "yea"-style yeses are in #97 (PR OPEN). |
| Email/SMS delivery | LIVE | Resend e-mail. Telnyx TFV **Verified**; customer SMS goes out when `sms_consent = true`. |
| /q/\<token\> portal | LIVE | `/q/nonexistent-token` 200 "couldn't find" on both domains. |
| Stripe payment | LIVE (sandbox) | Webhook `enabled`, 7 events, livemode=false. |
| Customer portal | LIVE / MERGED-NOT-DEPLOYED | `/v1/portal/*` deployed; site live on `f82cb5d`. Phone-only customers (site #46) need GVAS #96 deployed. |
| AI booking agent | LIVE | `/v1/businesses/{key}/intake/*` deployed; site chat widget live. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#97](https://github.com/ckoutz/gud-vector-agent-suite/pull/97) | Owner texts: natural yeses confirm; a short unclear reply keeps the change waiting | main | MERGEABLE (clean) | ruff, mypy, alembic, pytest: success (5/5 incl. review) | ~10 h (opened 2026-10-22 03:23 UTC) | **1**. No migration. Deploy web afterwards, together with #96/#98. |
| gud-vector-agent-suite | [#99](https://github.com/ckoutz/gud-vector-agent-suite/pull/99) | Scheduling engine V1a: the engine, a database no-overlap guarantee, and a per-business switch (Calendly by default) | main | MERGEABLE (clean), **draft** | ruff, mypy, alembic, pytest: success | ~9 h (opened 2026-10-22 04:09 UTC) | **Hold.** The PR says "Do not merge yet" (branch only). Migration `0035_scheduling_engine` adds `btree_gist`. |

No stacked PRs; both target `main`. No open PRs in gudvector-site.

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `a5ed75d` 2026-10-22 03:55 UTC: Merge #98. Main CI 4/4 success (ruff, mypy, alembic, pytest). No Railway web/worker commit statuses on this commit (`af29c7a` has all four: production and Larkspur web/worker success). |
| GVAS web deployed commit | **`af29c7a`** (#95). Latest GitHub deployment `gud-vector-agent-suite / production` was 2026-10-20 23:19 UTC (success). There are no deployments for #96 or #98: **deploy gap of 2 merges, including migration `0033`.** |
| Larkspur demo (Railway) | The latest `larkspur-demo / demo` deployment is `bccabb5` (2026-10-20 20:42 UTC, now inactive). No newer `larkspur-demo / demo` deployment is recorded, but commit statuses `larkspur-demo - web`/`- worker` report success on `af29c7a`. Host not probed. |
| GVAS web `/healthz` | **200** `{"status":"ok"}` (0.33 s; 0.40 s on re-check) |
| GVAS web `/health`, `/` | 404 `{"detail":"Not Found"}` (0.98 s / 0.36 s): the app's own 404, because those routes don't exist. |
| gudvector-site main | `f82cb5d` 2026-10-22 02:43 UTC: Merge #46. Vercel success for gudvector-site, larkspur-dashboard and gudvector-sitev2 (02:44–02:45 UTC). |
| gudvector-site.vercel.app | `/` 200 (0.44 s); `/q/nonexistent-token` 200 "couldn't find" (1.49 s). No "temporarily unavailable". |
| gudvector.com | `/` 200 (0.43 s, resolves 216.198.79.1); `/q/nonexistent-token` 200 "couldn't find" (0.40 s). www redirects 308. **Domain is up.** |

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired and deployed. `/calendly/events` webhook not mounted (POST 404, re-checked). The in-house scheduling engine is in draft #99; Calendly stays the default. |
| Resend | Wired: contact form, quote links, magic links, owner notification e-mails. |
| Telnyx | TFV `f77d0422-bdb9-5f33-9783-2cba342f865c` (+18775411550): **Verified** (updated 2026-10-08 16:01 UTC). Campaign text status is not checked here. |
| Stripe | Sandbox. `web-production-9d848…/webhooks/stripe`: `enabled`, 7 events, livemode=false. A second endpoint, `web-demo-5831.up.railway.app/webhooks/stripe`, is also `enabled` (7 events). |
| Slack | Wired; `/slack/events` deployed. |
| OpenAI | Wired (review model, free-text quotes, intake agent, owner texts). Runtime status unknown (no public probe). |
| Cloudflare R2 | Wired when `GVAS_R2_*` is set. Runtime status unknown (no public probe). |

## Owner to-do

- [ ] **Trigger the Railway web deploy now** (still outstanding since 03:55 UTC), for production and the Larkspur demo. Web is on `af29c7a`; main `a5ed75d` adds #96 (migration `0033`) and #98. Web does not auto-deploy on push; the worker does.
- [ ] Merge GVAS #97 (natural yeses), then deploy web again. You can merge it before the deploy above and do a single deploy.
- [ ] Hold GVAS #99 (scheduling engine V1a, draft, "Do not merge yet"). Merging it runs migration `0035` on the next deploy.
- [x] GVAS #96 and #98 merged; site #46 and #48 merged and live on Vercel prod `f82cb5d`.
- [ ] Confirm the new Telnyx campaign text, which matches the #35 consent wording, was approved or submitted (not verifiable here).
- [ ] Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook (`/calendly/events` still 404).
- [x] Namecheap: registrant WHOIS verification done. gudvector.com resolves and returns 200.
- [x] gudvector.com DNS points at Vercel and serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in GVAS business config from gudvector-site.vercel.app to gudvector.com (not verifiable from public checks).
- [x] Telnyx: toll-free verification **Verified** (2026-10-08). Customer SMS stays consent-gated.
- [ ] Fix intake threat-model finding **M1** before building more on the intake path.
- [ ] Update `docs/roadmap.md`: the owner-texts assistant (#78–#98) and the scheduling engine (#99) are not listed, and "Office manager" still reads "Not started".
- [ ] Decide whether the second Stripe webhook endpoint (`web-demo-5831.up.railway.app`) should stay enabled.
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor, Vercel).
- [ ] Stripe: switch to live keys at production cutover. Stripe Connect for multi-tenant payouts is deferred.

## Roadmap (docs/roadmap.md on main, last changed `2348d85` 2026-10-12)

- **Done:** field-note intake (text/voice) and append, evidence-only report, review-model benchmark, contradiction pass, DOCX publish and publish-failure notice, worker logging, unmatched-message triggers, R2 storage, checklist evidence annotator, plan custody, email report, Telnyx owner SMS (quotes), cost ceilings, Calendly lookup, free-text quotes, portal quote handoff, hosted customer quotes + Stripe Checkout, customer portal + recurring quotes, AI booking intake, per-business intake profile, owner notification e-mail, owner dashboard backend, customer SMS consent, booked-mode intake (reschedule/cancel, signed decision links), unified owner notification e-mails, channel-agnostic owner understanding step.
- **In progress:** "Audit follow-ups (2026-09): all four items landed (#23, #25, #26, #28)". The doc does not yet cover the owner-texts work merged in #78–#98, open #97, or the scheduling engine in #99.
- **Next (ordered):** 1) Letterhead DOCX templates per business. **Needs decision:** who supplies the template and where the binding manifest lives. 2) Office manager owner assistant over Slack/SMS, with every write behind the owner's YES, owner-only, existing keyword commands matched first. Marked "Not started; design review pending". **Needs decision:** D1–D6 in `docs/office_manager.md`. Prerequisite: fix M1. Later: flag cancel/reschedule hidden in customer notes. 3) Retention and redaction of transcripts, media, reports.
- **Open follow-ups (not built):** Stripe Connect per-business payouts (`businesses.stripe_account_id` unused); contact-form API behind business `public_key`; per-business timezone for Calendly windows/timestamps; customer-editable phone in portal.
- **Not planned for pilot:** second workspace/owner, auto-distribution outside the thread, workspace-wide auth, field notes/voice/reports over SMS.

## Recent changes (merged, last 7 days)

| Repo | PR | Merged (UTC) |
| --- | --- | --- |
| gud-vector-agent-suite | [#98](https://github.com/ckoutz/gud-vector-agent-suite/pull/98) Startup: owner texts must reach the same business as Slack | 2026-10-22 03:55 |
| gudvector-site | [#46](https://github.com/ckoutz/gudvector-site/pull/46) Owner customers: a customer with only a phone (no e-mail) lists, opens and shows no portal sign-in | 2026-10-22 02:44 |
| gud-vector-agent-suite | [#96](https://github.com/ckoutz/gud-vector-agent-suite/pull/96) Owner texts: create a customer, make a draft invoice, send it with its own YES, void-and-reissue an amount | 2026-10-22 02:43 |
| gudvector-site | [#48](https://github.com/ckoutz/gudvector-site/pull/48) Trade pages close with the made-up-company line; footer and Who we help link: "No software to learn" | 2026-10-22 02:40 |
| gudvector-site | [#47](https://github.com/ckoutz/gudvector-site/pull/47) Who we help replaces /concord; no area-specific claims; consistent trade names and addresses; "Open the Demo" | 2026-10-21 23:55 |
| gudvector-site | [#45](https://github.com/ckoutz/gudvector-site/pull/45) Owner invoices: show drafts made by text and void invoices; not counted as owed | 2026-10-21 23:04 |
| gudvector-site | [#44](https://github.com/ckoutz/gudvector-site/pull/44) Quote answer: one period after "any time" (trade pages, owner-approved quoting) | 2026-10-21 21:25 |
| gudvector-site | [#43](https://github.com/ckoutz/gudvector-site/pull/43) Trade pages: one template for cleaning, handyman, plumbing, painting, HVAC; Concord "Who we help" links all six | 2026-10-21 21:21 |
| gudvector-site | [#42](https://github.com/ckoutz/gudvector-site/pull/42) Booking chat picture: current checkbox wording; remove 2 unused old consent images | 2026-10-21 14:40 |
| gudvector-site | [#41](https://github.com/ckoutz/gudvector-site/pull/41) Concord & Contra Costa hub page: what we build, where we work, trades, the Larkspur demo | 2026-10-21 14:40 |
| gudvector-site | [#39](https://github.com/ckoutz/gudvector-site/pull/39) "Don't want to learn software" page: run the office by text, nothing changes until YES | 2026-10-21 14:32 |
| gudvector-site | [#40](https://github.com/ckoutz/gudvector-site/pull/40) Custom AI: field-notes iPad Pro illustration; Güd Office no longer repeats the home page's phones up top | 2026-10-21 13:49 |
| gudvector-site | [#38](https://github.com/ckoutz/gudvector-site/pull/38) Owner-approved quoting page: every quote waits for the owner's OK | 2026-10-21 12:35 |
| gudvector-site | [#37](https://github.com/ckoutz/gudvector-site/pull/37) Landscapers page: Gus for landscaping crews, the Larkspur demo, the 925 texting line | 2026-10-21 11:50 |
| gudvector-site | [#35](https://github.com/ckoutz/gudvector-site/pull/35) SMS consent: name Güd Vector, frequency, HELP, Terms/Privacy links; owners opt in by texting | 2026-10-21 01:36 |
| gud-vector-agent-suite | [#95](https://github.com/ckoutz/gud-vector-agent-suite/pull/95) Telnyx: a second number may answer the one pilot owner; customer texts keep the first | 2026-10-20 23:18 |
| gudvector-site | [#36](https://github.com/ckoutz/gudvector-site/pull/36) Owner decline reason: cap the input at 200, the length the customer is sent | 2026-10-20 19:08 |
| gud-vector-agent-suite | [#93](https://github.com/ckoutz/gud-vector-agent-suite/pull/93) Demo: owner texts block and unblock time on the visitor's copy; blocks show on its dashboard | 2026-10-20 18:28 |
| gud-vector-agent-suite | [#94](https://github.com/ckoutz/gud-vector-agent-suite/pull/94) Chat view: the owner's decline note, once the request is declined | 2026-10-20 15:48 |
| gudvector-site | [#34](https://github.com/ckoutz/gudvector-site/pull/34) Owner Settings: "Changes by text", the log of every change asked for by text | 2026-10-20 15:14 |
| gud-vector-agent-suite | [#90](https://github.com/ckoutz/gud-vector-agent-suite/pull/90) Owner texts: change a customer's name or phone in plain words, behind YES | 2026-10-20 15:11 |
