# Scheduling engine (V1a)

The Calendly replacement, built to "Güd Scheduling Engine: Requirements
(Revision 2)". V1a is the engine alone: nothing in GVAS calls it yet, and every
business stays on Calendly.

## Layout

- `gvas/scheduling/core` is pack-neutral. It covers calendars, per-calendar roles,
  resources (whose time is booked), weekly hours, time off, events, holds,
  audit log and slot-offer log.
- `gvas/scheduling/field_service` is the field-service pack. It covers services
  (length, buffers, notice, approval, questions) and the visit details a booking
  carries. Customers are linked by id only. A test keeps the package from
  importing customer, quote, invoice or intake code.

## Guarantees

- **No double-booking, enforced by Postgres.** `scheduling_events` has
  `EXCLUDE USING gist (resource_id WITH =, tstzrange(blocked_start, blocked_end) WITH &&)`
  over occupying statuses (`held`, `pending_approval`, `confirmed`). This needs
  `btree_gist` (migration 0035 creates it). The blocked range includes the
  buffers. A constraint can't read the clock, so lapsed holds are marked
  `expired` inside each booking transaction before the insert.
- **One booking path per resource at a time.** Hold, confirm and manual adds
  lock the resource row (`SELECT ... FOR UPDATE`). The constraint is the
  backstop.
- **Time.** Instants are stored in UTC. Weekly hours and repeating blocks expand
  in the resource's IANA zone with `dateutil.rrule`, so 9:00 stays 9:00 across
  daylight saving.
- **Tenancy.** Every query filters by `business_id`. Another business's ids
  read as "not found".

## Statuses

`held → pending_approval | confirmed | cancelled | expired`;
`pending_approval → confirmed | cancelled | expired`;
`confirmed → cancelled | completed | no_show`. Every other change is refused.

## Where generic vs business-specific is hard

1. **Who a principal is.** No identity layer spans businesses yet (the dashboard
   sign-in is one e-mail link per business). Permissions key on an opaque
   `principal` string (`owner:<business_id>` today). A household with several
   adults needs a real identity/membership table. The core won't change, but
   sign-in will.
2. **Participants.** V1a has no participant table. The pack keeps `customer_id`
   on the booking. Reminders "to any person" (V1b, household) need
   `event_participant`. That gets added then, as a core table.
3. **Buffers and services.** The core takes durations and buffers as inputs.
   Only the field-service pack knows a "service". The daily visit limit counts
   every occupying event on a resource, which suits a business but probably not
   a household.
4. **Repeating events** (weekly mowing, family events) are designed for
   (`rrule` exists on time off) but not built on events. The no-overlap
   constraint only sees concrete rows, so repeating visits must be stored one
   occurrence per row to stay protected.
5. **Hold limits** are counted per business, so demo holds never touch a real
   business. A shared "per visitor across businesses" limit would need a
   global table.

## The switch

`businesses.scheduler` is `calendly` (the default) or `engine`. `SchedulerRouter`
stands where the Calendly (production) or demo adapter stood, so website Gus,
owner approval and blocks by text keep one interface. `SchedulerBookedEvents`
does the same for the dashboard calendar. An engine business books through its
first active service. A date's hours set by text become text-sourced blocks.

    gvas-scheduling-setup --business-id <uuid> --hours "mon-fri=08:00-12:00,13:00-17:00" \
        --service "Estimate walk-through" --minutes 30     # sets up; stays on calendly
    gvas-scheduling-setup --business-id <uuid> --switch engine   # or back: calendly

The worker runs `sweep()` every minute.

## Customer messages (V1b)

- When an event becomes `confirmed`, `queue_confirmed` (core `notify.py`) queues a
  confirmation now plus reminders 24 h and 2 h before, for each `customer`
  participant, by e-mail and/or text. Cancelling skips whatever is still
  pending and queues a cancellation.
- Every minute the worker runs `VisitMessenger.run_due()`:
  - E-mails go out as `scheduling.visit_email`, with an iCalendar invite.
    UID is `<event id>@gudvector.com`; SEQUENCE is the event version;
    `METHOD:CANCEL` on cancellation.
  - Texts go only when the GVAS customer with that e-mail has
    `sms_consent = true`. A text due between 20:00 and 08:00 (in the person's
    zone, else the visit's) waits for 08:00. Texts not being available is a
    normal skip, not an error.
- The confirmation carries a one-visit link `/v1/visits/<token>`. Only the
  token's hash is stored, and the link expires when the visit ends. A GET shows
  the visit. A POST cancels it, or asks for another time. Safe default: every
  reschedule waits for the owner, and the visit stays booked meanwhile. The owner
  is told either way through the usual owner notice. Links are built from
  `GVAS_INTAKE_DECISION_LINK_BASE_URL`; with it unset, no link is added.

## Not yet (later)

- Picking the new time on the page itself (today the customer asks and the
  owner moves the visit), and per-service message templates (FS-20, SHOULD).
- There is no e-mail opt-out flag on customers yet, so e-mail is always allowed.
- (Fixed) Intake no longer sends its own "booked" e-mail and text when the engine
  sends the confirmation (`BookingResult.sends_own_messages`), so customers get
  one confirmation.
- A date's hours set by text can only narrow the weekly hours on the engine, not
  widen them as Calendly date overrides can. `ScheduleBlockPort.serves` is
  synchronous, so it follows the fallback calendar's answer.

## Outside calendars (V1c, in progress)

- `scheduling_external_links` connects a resource to an outside calendar.
  `scheduling_external_busy` is a times-only mirror of that calendar's busy blocks
  (never titles). Busy blocks hide slots like time off.
- A read link whose last good sync is older than 15 minutes (`STALE_AFTER`)
  stops every offer for that resource (FS-25). A revoked grant marks the link
  `reconnect` (FS-26 prompt), and offers stay stopped until the owner reconnects.
- `sync_busy` replaces the mirror from an `ExternalCalendarPort`.
  `GoogleBusyCalendar` implements it with Google's freeBusy API. A 401 or 403
  means reconnect; other errors retry.
- `push_events` sends confirmed events to the link's dedicated calendar
  (`write_calendar_id`) once per event version, and removes them once they're
  cancelled. `GoogleVisitWriter` derives the Google event id from ours, so a
  retry updates the same entry instead of making a second one.
- Refresh tokens are sealed with AES-GCM (`CalendarTokenCipher`, key in
  `GVAS_CALENDAR_TOKEN_KEY`), bound to the link id. `GoogleAccessTokens` exchanges
  a refresh token for an access token, and `invalid_grant` means reconnect.
- Still to build: the OAuth connect/reconnect screen (V1d owner tools), the
  worker job (5-minute polling plus push after each change), and Google watch
  channels with renewal. All of it needs a Google Cloud OAuth client before it
  can run against a real account.

## Owner calendar (V1d, in progress)

- `SchedulingEngine.move` moves a confirmed visit. The visit keeps its buffers
  and the database still refuses overlaps (`SlotTakenError`). The version goes
  up, unsent reminders for the old time are skipped, and the customer gets the
  RESCHEDULED message with the updated invite.
- `VisitEditorPort` (`add_visit`, `move_visit`, `cancel_visit`) is implemented by
  `EngineAvailability` and routed by `SchedulerRouter`. A Calendly business is
  refused ("Visits booked through Calendly are changed in Calendly."). A
  phone-in visit goes through `book_approved`, so it gets the same overlap
  guarantee as a website booking.
- HTTP, owner session only: `POST /v1/owner/visits`,
  `POST /v1/owner/visits/{event_id}/move`, `POST /v1/owner/visits/{event_id}/cancel`.
  A refusal is a 409 with an owner-safe `detail`. Calendar entries carry
  `eventId` and `status` for engine visits.
- Not yet: click-and-drag blocked time from the dashboard (blocks still come
  from text), and a status for completed or no-show visits on the calendar.
