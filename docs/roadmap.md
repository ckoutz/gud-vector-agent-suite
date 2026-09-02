# Roadmap

Living status for the pilot. Every PR that changes behaviour moves an item here;
"Done" links the PR that landed it. Decisions marked **needs decision** are
waiting on the owner and are not built until answered.

## Done

| Capability | PR |
| --- | --- |
| Text and voice field-note intake in one thread | #15 |
| Append voice notes to the active case | #16 |
| Report shows evidence, never placeholder status words | #17 |
| Opt-in live benchmark harness for review models (GPT-5.6 Luna selected) | #18 |
| Focused GPT contradiction pass before notes are marked complete | #19 |
| `approve report` publishes the exact reviewed version as a generic DOCX into the case thread | #20 |
| Owner notice when a DOCX publish dead-letters; `approve report` retries it | #21 |
| Worker and web log one line per command outcome to stderr at `GVAS_LOG_LEVEL` | #23 |
| Unmatched messages get the available triggers once instead of retrying to dead-letter | #25 |
| R2 object storage wired in production when `GVAS_R2_*` is set; published DOCX kept durably | #26 |
| Review model annotates marker-satisfied checklist items with verbatim note excerpts (deterministic-first) | #27 |
| PDF/image uploads in an open case thread enter plan custody; dead-lettered copies notify the owner; "not enabled" replied once when custody is unwired | #28 |
| `send report to <address>` emails the published DOCX to one typed recipient on request | #29 |
| Telnyx SMS as a second owner channel, quotes only (`quote:`, follow-ups, approve/send); other triggers get one "quotes only, field notes belong in Slack" reply | #31 |
| Per-business monthly ceilings on transcription seconds and review tokens (`GVAS_COST_CEILING_*`); a held-back command completes with one owner notice and the case stays open | #30 |
| Calendly-backed customer lookup: `quote:` may omit `customer:` when `GVAS_CALENDLY_*` is set; one match names the customer in the draft reply, several get a numbered selection over Slack or SMS | #32 |

## In progress

- Audit follow-ups (2026-09): all four items landed (#23, #25, #26, #28).

## Next (ordered)

1. Letterhead DOCX templates per business (`docs/templates_and_site_plans.md`
   §4). **Needs decision:** who supplies the template and where the binding
   manifest lives.
2. Retention and redaction of transcripts, media, and reports.

## Not planned for the pilot

- Second workspace or second owner (startup refuses both by design).
- Automatic distribution of any report outside the originating thread.
- Workspace-wide authorization; only the configured owner is heard.
- Field notes, voice notes or reports over SMS. SMS is quotes only; a DOCX
  cannot travel over SMS, so field notes stay in Slack and MMS media is ignored.
