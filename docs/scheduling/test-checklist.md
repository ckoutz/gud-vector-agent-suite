# Scheduling engine: test checklist for Cameron

Run this only after Cameron says go and Railway is back. Merging to main deploys
web and worker and runs migrations 0035–0039. Use Larkspur on the demo first.
Every other business stays on Calendly.

## 0. Before switching anything
- [ ] The web and worker services are both on the merged commit, and `/healthz` is ok.
- [ ] `gvas-migrate` ran in the web pre-deploy log, and `alembic current` is `0039_scheduling_link_watch`.
- [ ] Railway's Postgres has `btree_gist`. Migration 0035 creates it and fails if it can't.

## 1. Switch Larkspur to the engine
```
gvas-scheduling-setup --business-id <larkspur id> --time-zone America/Los_Angeles \
  --hours "mon-fri=08:00-12:00,13:00-17:00" --service "Free estimate" --minutes 60 \
  --switch engine
```
Undo at any time with `--switch calendly`. Nothing is deleted.

## 2. Booking from the website (Gus)
- [ ] Gus offers at most 3 times, spread over 2 days or more, inside the hours set above.
- [ ] Picking a time holds it for 10 minutes. A second browser doesn't see that time.
- [ ] The owner gets the approval text. YES books the visit, and it shows on the dashboard Calendar.
- [ ] The customer gets one confirmation e-mail with a calendar invite and a change/cancel link.

## 3. Customer messages
- [ ] Reminders arrive 24 h and 2 h before the visit (move a test visit close to now).
- [ ] Texts go only to customers who ticked the text box. A text due between 8pm and 8am arrives at 8am.
- [ ] Cancel link: the visit is cancelled, the time is free again, and the owner is told.
- [ ] "Ask for another time": the visit stays booked and the owner is told (the reschedule waits for your OK).

## 4. Owner calendar (dashboard)
- [ ] Day, Week and Month views work, and the phone opens on Day with nothing running off the edge.
- [ ] Clicking a visit shows the customer, the address (the Maps link opens), the service and Gus's notes.
- [ ] Approve and Decline work from that panel.
- [ ] Dragging a visit moves it, and the customer gets an updated invite.
- [ ] "+ Add visit" adds a phone-in visit. Adding one over a taken time is refused with the reason.
- [ ] A business still on Calendly sees the calendar read-only.

## 5. Google Calendar (needs the OAuth client and settings)
Settings on web and worker: `GVAS_GOOGLE_OAUTH_CLIENT_ID`, `GVAS_GOOGLE_OAUTH_CLIENT_SECRET`,
`GVAS_CALENDAR_TOKEN_KEY` (32 random bytes, base64),
`GVAS_GOOGLE_REDIRECT_URI=https://gudvector.com/portal/owner/calendar/google` and
`GVAS_GOOGLE_NOTIFY_URL=https://<web host>/v1/calendar/google/notify`.
- [ ] Connect Google Calendar → the Google sign-in → back to the Calendar page with "Done".
- [ ] A "Güd Office visits" calendar appears in Google, and confirmed visits appear in it.
- [ ] A new event on the owner's own Google Calendar hides that time from Gus within about a minute.
- [ ] Deleting that event brings the time back.
- [ ] Removing the app's access in Google (myaccount.google.com → Security → Third-party apps): the dashboard shows Reconnect and online booking pauses. Reconnecting restores it without a second visits calendar.

## 6. Roll back
`gvas-scheduling-setup --business-id <id> --switch calendly` puts the business back on Calendly.
The engine's tables stay but go unused.
