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
| Review model annotates marker-satisfied checklist items with verbatim note excerpts (deterministic-first) | #PR_NUMBER |

## In progress

- Audit follow-ups (2026-09), in priority order (items 1 and 2 landed in #23
  and #25):
  3. Object storage (`GVAS_R2_*`) is configured and implemented but not wired in
     `gvas.composition.production`, so the published DOCX has no durable copy
     outside Slack. Wire it, or remove the settings and say so.
  4. `plan_set.copy_into_custody` has no owner-facing failure guidance and is not reachable
     from any channel command yet.

## Next (ordered)

1. Letterhead DOCX templates per business (`docs/templates_and_site_plans.md`
   §4). **Needs decision:** who supplies the template and where the binding
   manifest lives.
2. Opt-in email of a published report on request (`send report to <address>`).
   **Needs decision:** allowed recipients (typed address only, or a
   per-business office inbox too).
3. Retention and redaction of transcripts, media, and reports.
4. Cost ceilings per business for transcription and review calls.

## Not planned for the pilot

- Second workspace or second owner (startup refuses both by design).
- Automatic distribution of any report outside the originating thread.
- Workspace-wide authorization; only the configured owner is heard.
