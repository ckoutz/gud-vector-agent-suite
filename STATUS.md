# Güd Vector — Project Status Dashboard

**Last updated 2026-11-21 20:11 UTC**

## Where we are

Railway production's last successful deployment recorded by GitHub is `af29c7a`. GVAS main is `a5ed75d`, so #96 (owner-text customers and invoices, migration `0033`) and #98 (same-business startup check) are **merged and still awaiting a web deploy**. The backend serves `/healthz` (200) and both site domains serve the quote portal. Telnyx toll-free verification is **Verified**. The Stripe sandbox webhook is enabled with seven events. GVAS #97 is ready to merge and #99 is on hold as a draft. Neither repo recorded a merge in the last seven days, and nothing changed since the 2026-11-21 14:09 UTC refresh.

## Demo pipeline

Status reflects public GET probes, provider API results, and the last recorded deployments. This pass was read-only, so it did not book, send messages, sign in, or pay.

| Step | Status | Note |
| --- | --- | --- |
| Contact form | LIVE | Both site homepages return 200. The deployed contact form is wired to Resend. |
| Calendly | LIVE / BLOCKED | Lookup, availability and booking are deployed. The optional `/calendly/events` webhook returned 404 at 20:11 UTC. Scheduling engine V1a is PR OPEN in draft #99. |
| Quote drafting | LIVE / MERGED-NOT-DEPLOYED | Existing Slack and Telnyx owner flows are on `af29c7a`. The owner-text customer and invoice additions in #96 and the startup check in #98 await the web deploy. |
| Owner approval | LIVE / PR OPEN | Slack/SMS commands, signed e-mail links and dashboard approve/decline are live. Natural "yea"-style yeses are in #97 (PR OPEN). |
| Email/SMS delivery | LIVE | Resend is wired and Telnyx toll-free verification is **Verified**. Customer SMS requires recorded consent. |
| /q/\<token\> portal | LIVE | `/q/nonexistent-token` returns 200 "couldn't find" on both domains. |
| Stripe payment | LIVE (sandbox) | Production callback is `enabled` in Stripe test mode with seven events. |
| Customer portal | LIVE / MERGED-NOT-DEPLOYED | `/portal` returns 200 on Vercel. The owner portal's phone-only customer support (site #46) needs the GVAS #96 deployment. |
| AI booking agent | LIVE | `/v1/businesses/{key}/intake/*` is deployed and the site chat widget is live. |

## Open PRs

| Repo | PR | Title | Base | Mergeable | CI | Age | Merge order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gud-vector-agent-suite | [#97](https://github.com/ckoutz/gud-vector-agent-suite/pull/97) | Owner texts: natural yeses confirm; a short unclear reply keeps the change waiting | `main` | Clean, mergeable | 4/4 CI passed on `287143e`. | 30d 17h (opened Oct 22 03:23 UTC) | **1.** Merge before the web deploy if bundling with #96 and #98. |
| gud-vector-agent-suite | [#99](https://github.com/ckoutz/gud-vector-agent-suite/pull/99) | Scheduling engine V1a: the engine, a database no-overlap guarantee, and a per-business switch (Calendly by default) | `main` | Clean, mergeable, **draft** | 4/4 CI passed on `a446d7c`. | 30d 16h (opened Oct 22 04:09 UTC) | **Hold.** The PR says “Do not merge yet.” Migration `0035_scheduling_engine` adds `btree_gist`. |
| gudvector-site | — | No open PRs | — | — | — | — | — |

Both open PRs target `main` independently, so none are stacked. If the stacked branches become PRs, merge #99 → GVAS V1b → V1c → V1d → V1e, then site owner calendar views (`bf9da14`) → team calendars (`97c172b`, Vercel preview).

## Deploy & health

| Target | Result |
| --- | --- |
| GVAS main | `a5ed75d` (#98), 2026-10-22 03:55 UTC. Main CI 4/4 passed (ruff, mypy, alembic, pytest). |
| GVAS Railway production | Latest GitHub deployment `af29c7a` (#95), 2026-10-20 23:19 UTC, `success`. #96 and #98 are absent from the latest recorded deployment. |
| GVAS Railway HTTP probes | `/health` **404** (387 ms) and fallback `/` **404** (350 ms). The app's `/healthz` returns **200** `{"status":"ok"}` (407 ms). |
| Larkspur demo on Railway | Latest GitHub deployment `bccabb5`, 2026-10-20 20:42 UTC, `inactive`. Current demo host health is unknown. |
| gudvector-site main and Vercel production | Main `f82cb5d` (#46), 2026-10-22 02:43 UTC. `Production – gudvector-site` deployed the same commit at 02:44 UTC. |
| gudvector-site.vercel.app | `/` **200** (299 ms). `/q/nonexistent-token` **200** with “couldn't find” (1,360 ms), so the quote route reaches GVAS. `/portal` **200**. |
| gudvector.com | Resolves to `216.198.79.1`. `/` **200** (291 ms), `/q/nonexistent-token` **200** with “couldn't find” (395 ms), and `www` redirects with **308**. |

PR and deployment data came from GitHub deployment records. Railway and Vercel runtime commit IDs were inferred from those records.

## Integrations

| Integration | Status |
| --- | --- |
| Calendly | Wired for lookup, availability and booking. Provider runtime is unknown from this pass. `POST /calendly/events` returned 404 at 20:11 UTC (webhook not mounted). |
| Resend | Wired for contact forms, quote links, magic links and owner email. Delivery status is unknown. |
| Telnyx | Request `f77d0422-bdb9-5f33-9783-2cba342f865c` (+18775411550) is **Verified** (updated 2026-10-08 16:01 UTC). Separate campaign text status is unknown. |
| Stripe | Sandbox (`livemode=false`). `web-production-9d848.up.railway.app/webhooks/stripe` is `enabled` with **7 events**. The demo endpoint `web-demo-5831.up.railway.app/webhooks/stripe` is also `enabled` with 7 events, but its host does not resolve in DNS (re-checked 20:11 UTC). |
| Slack | `/slack/events` is wired on GVAS. Live Slack status is unknown; this pass made no Slack calls. |
| OpenAI | Wired for review, free-text quotes, intake and owner texts. Runtime status is unknown. |
| Cloudflare R2 | Wired when `GVAS_R2_*` is configured. Runtime status is unknown. |

## Owner to-do

- [ ] **Trigger the Railway web deploy** for production and the Larkspur demo. The latest recorded production deploy is `af29c7a`. Main `a5ed75d` adds #96 (migration `0033`) and #98. Web needs a manual deploy after backend merges; the worker auto-deploys on push.
- [ ] Merge GVAS #97 (natural yeses), then deploy web. Merging it before the deploy above lets one deploy cover all three.
- [ ] Hold GVAS #99 (scheduling engine V1a, draft, "Do not merge yet"). Merging it runs migration `0035` on the next deploy. V1b–V1e (branches `scheduling-v1b` … `scheduling-v1e`, stacked on V1a) have no PRs yet.
- [ ] Decide on site branches `devin/1792644805-owner-calendar-views` → `devin/1792677187-team-calendars` (Google Calendar connect, team view). They have no PRs and depend on the scheduling stack.
- [x] GVAS #96 and #98 are merged. Site #46 and #48 are merged and live on Vercel prod `f82cb5d`.
- [ ] Confirm whether the new Telnyx campaign text (matching the #35 consent wording) was approved or submitted. This dashboard can't check it.
- [ ] Set `GVAS_CALENDLY_WEBHOOK_SIGNING_KEY` and register the Calendly webhook. `/calendly/events` still returns 404 (20:11 UTC).
- [x] Namecheap: registrant WHOIS verification is done. gudvector.com resolves and returns 200.
- [x] gudvector.com DNS points at Vercel and serves the current gudvector-site build.
- [ ] Swap site_url/CORS/Telnyx URLs in the GVAS business config from gudvector-site.vercel.app to gudvector.com. Public checks can't confirm this.
- [x] Telnyx: toll-free verification is **Verified** (2026-10-08). Customer SMS stays consent-gated.
- [ ] Fix intake threat-model finding **M1** before building more on the intake path.
- [ ] Update `docs/roadmap.md`. It doesn't list the owner-texts assistant (#78–#98) or the scheduling engine (#99), and "Office manager" still reads "Not started".
- [ ] Remove or disable the second Stripe webhook endpoint (`web-demo-5831.up.railway.app`). It is still enabled and its host still does not resolve in DNS.
- [ ] Revoke temporary tokens given to Devin (Railway, Stripe test key, Calendly, Cloudflare R2, Telnyx, OpenRouter, Cursor, Vercel).
- [ ] Stripe: switch to live keys at production cutover. Stripe Connect for multi-tenant payouts is deferred.

## Roadmap

The [roadmap on main](https://github.com/ckoutz/gud-vector-agent-suite/blob/main/docs/roadmap.md) last changed on 2026-10-12 (`2348d85`). It predates the latest owner-text work and the scheduling PR.

- **Done.** Text and voice field notes, evidence review, DOCX publishing and email, worker logging, R2 storage and plan custody, cost ceilings, Calendly lookup, Slack/Telnyx quotes, hosted quotes and Stripe Checkout, customer portal and recurring quotes, AI booking and per-business intake, owner notifications and dashboard, SMS consent, and reschedule/cancel with signed decision links.
- **In progress.** The roadmap lists the September audit follow-ups here and records all four as landed (#23, #25, #26, #28).
- **Next in order.** Business-specific DOCX letterhead templates, then the office manager over Slack/SMS with owner-confirmed writes (design review pending; intake finding M1 is a prerequisite), then retention and redaction of transcripts, media and reports.
- **Open decisions.** Decide who supplies the DOCX templates and where the binding manifest lives. Resolve D1–D6 in the [office manager design](https://github.com/ckoutz/gud-vector-agent-suite/blob/main/docs/office_manager.md).
- **Other follow-ups.** Stripe Connect payouts, business-key contact form API, per-business Calendly timezone, and customer-editable phone in the portal.

## Recent changes

Neither repo merged a PR in the seven days since 2026-11-14 20:11 UTC. The latest merges were GVAS #98 (2026-10-22 03:55 UTC) and gudvector-site #46 (2026-10-22 02:44 UTC).
