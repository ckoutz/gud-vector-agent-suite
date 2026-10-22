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

## Not yet (V1b and later)

- Customer confirmations, reminders and calendar invites. Until V1b, an engine
  booking's confirmation e-mail still says an invite follows, so no business
  should switch before V1b (the doc says the same for Larkspur).
- Sending the halfway-approval reminder and the expiry notice that `sweep()`
  reports.
- A plain public booking page that holds and confirms (website Gus uses the
  existing request-then-approve flow, which books the approved time directly).
- A date's hours set by text can only narrow the weekly hours on the engine, not
  widen them as Calendly date overrides can. `ScheduleBlockPort.serves` is
  synchronous, so it follows the fallback calendar's answer.
