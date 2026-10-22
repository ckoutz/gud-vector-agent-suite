"""Sends the visit messages the scheduling engine queued: an e-mail with the
calendar invite, and a text only with the customer's text consent and never
in quiet hours (it waits for 8:00 instead)."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.identifiers import BusinessId, CustomerId
from gvas.domain.intake import format_slot_label, intake_customer_text_command
from gvas.domain.visit_email import VisitEmail, visit_email_command
from gvas.infrastructure.models import Business
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.infrastructure.visit_links import mint_link
from gvas.scheduling.core.messages import Channel, MessageKind, text_send_time, visit_invite
from gvas.scheduling.core.model import EMPLOYEE, EventStatus
from gvas.scheduling.core.notify import PENDING, SKIPPED
from gvas.scheduling.core.records import (
    SchedulingEventRecord,
    SchedulingParticipantRecord,
    SchedulingReminderRecord,
    SchedulingResourceRecord,
)

logger = logging.getLogger(__name__)
SENT = "sent"
_SUBJECTS = {
    MessageKind.CONFIRMATION: "Your visit is booked",
    MessageKind.RESCHEDULED: "Your visit has moved",
    MessageKind.CANCELLED: "Your visit is cancelled",
    MessageKind.REMINDER_24H: "Reminder: your visit tomorrow",
    MessageKind.REMINDER_2H: "Reminder: your visit soon",
}
_WITH_INVITE = frozenset({MessageKind.CONFIRMATION, MessageKind.RESCHEDULED, MessageKind.CANCELLED})


def _body(kind: MessageKind, business: str, when: str) -> str:
    if kind is MessageKind.CANCELLED:
        return f"Your {business} visit for {when} is cancelled."
    if kind is MessageKind.RESCHEDULED:
        return f"Your {business} visit has moved to {when}."
    if kind is MessageKind.CONFIRMATION:
        return f"Your {business} visit is booked for {when}."
    return f"Reminder: your {business} visit is {when}."


class VisitMessenger:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        organizer_email: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        link_base_url: str = "",
    ) -> None:
        self._link_base_url = link_base_url
        self._session_factory = session_factory
        self._unit_of_work_factory = SqlUnitOfWorkFactory(session_factory)
        self._organizer = parseaddr(organizer_email)[1] or "info@gudvector.com"
        self._clock = clock

    async def run_due(self, limit: int = 50) -> int:
        """Queue every message now due; returns how many went to the outbox."""

        now = self._clock()
        queued = 0
        async with self._session_factory() as session, session.begin():
            due = list(
                await session.scalars(
                    select(SchedulingReminderRecord)
                    .where(
                        SchedulingReminderRecord.status == PENDING,
                        SchedulingReminderRecord.fire_at <= now,
                    )
                    .order_by(SchedulingReminderRecord.fire_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for reminder in due:
                if await self._handle(session, reminder, now):
                    queued += 1
        return queued

    async def _handle(
        self, session: AsyncSession, reminder: SchedulingReminderRecord, now: datetime
    ) -> bool:
        event = await session.get(SchedulingEventRecord, reminder.event_id)
        person = await session.get(SchedulingParticipantRecord, reminder.participant_id)
        business = await session.get(Business, reminder.business_id)
        resource = (
            None
            if event is None
            else await session.get(SchedulingResourceRecord, event.resource_id)
        )
        kind = MessageKind(reminder.kind)
        if event is None or person is None or business is None:
            return self._skip(reminder, "gone")
        if kind is not MessageKind.CANCELLED and EventStatus(event.status) is not (
            EventStatus.CONFIRMED
        ):
            return self._skip(reminder, "not confirmed")
        if kind in (MessageKind.REMINDER_24H, MessageKind.REMINDER_2H) and event.starts_at <= now:
            return self._skip(reminder, "visit started")
        zone = ZoneInfo(person.time_zone or event.time_zone)
        name = business.display_name or business.name
        text = _body(kind, name, format_slot_label(event.starts_at, ZoneInfo(event.time_zone)))
        if self._link_base_url and kind in (MessageKind.CONFIRMATION, MessageKind.RESCHEDULED):
            link = await mint_link(session, event, self._link_base_url, now)
            text = f"{text} To cancel or change it: {link}"
        key = f"visit:{reminder.id}"
        business_id = BusinessId(reminder.business_id)
        if Channel(reminder.channel) is Channel.SMS:
            opens = text_send_time(now, zone)
            if opens > now:
                reminder.fire_at = opens
                return False
            async with self._unit_of_work_factory() as unit_of_work:
                customer = (
                    None
                    if person.email is None
                    else await unit_of_work.customers.find_by_email(business_id, person.email)
                )
                if customer is None or customer.sms_consent is not True or not person.phone:
                    return self._skip(reminder, "no text consent")
                await unit_of_work.outbox.enqueue(
                    intake_customer_text_command(
                        business_id,
                        customer_id=CustomerId(customer.customer_id),
                        phone=person.phone,
                        text=text,
                        idempotency_key=key,
                    )
                )
                await unit_of_work.commit()
        else:
            if not person.email:
                return self._skip(reminder, "no e-mail")
            invite = None
            if kind in _WITH_INVITE:
                invite = visit_invite(
                    uid=f"{event.id}@gudvector.com",
                    sequence=event.version,
                    start=event.starts_at,
                    end=event.ends_at,
                    time_zone=event.time_zone,
                    summary=f"{event.title} with {name}",
                    stamp=now,
                    organizer_email=self._organizer,
                    attendee_email=person.email,
                    description=f"With {resource.name}"
                    if resource is not None and resource.kind == EMPLOYEE
                    else None,
                    cancelled=kind is MessageKind.CANCELLED,
                ).decode()
            async with self._unit_of_work_factory() as unit_of_work:
                await unit_of_work.outbox.enqueue(
                    visit_email_command(
                        VisitEmail(
                            business_id=business_id,
                            to=person.email,
                            subject=_SUBJECTS[kind],
                            body=text,
                            invite=invite,
                            cancelled=kind is MessageKind.CANCELLED,
                            idempotency_key=key,
                        )
                    )
                )
                await unit_of_work.commit()
        reminder.status = SENT
        reminder.sent_at = now
        return True

    @staticmethod
    def _skip(reminder: SchedulingReminderRecord, why: str) -> bool:
        reminder.status = SKIPPED
        reminder.detail = why
        return False
