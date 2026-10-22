"""Who hears about an event and when: participants, and the per-person
messages queued when an event is confirmed or cancelled. Sending them (and
checking consent) is the caller's job; this only keeps the schedule."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gvas.scheduling.core.messages import Channel, MessageKind, reminder_times
from gvas.scheduling.core.records import (
    SchedulingEventParticipantRecord,
    SchedulingEventRecord,
    SchedulingParticipantRecord,
    SchedulingReminderRecord,
)

#: The role whose people get visit messages (the field-service customer).
NOTIFIED_ROLE = "customer"
PENDING = "pending"
SKIPPED = "skipped"


async def add_participant(
    session: AsyncSession,
    event: SchedulingEventRecord,
    *,
    name: str,
    email: str | None,
    phone: str | None,
    role: str = NOTIFIED_ROLE,
    time_zone: str | None = None,
    now: datetime,
) -> UUID:
    participant_id = uuid4()
    session.add(
        SchedulingParticipantRecord(
            id=participant_id,
            business_id=event.business_id,
            name=name,
            email=email,
            phone=phone,
            time_zone=time_zone,
            created_at=now,
        )
    )
    await session.flush()
    session.add(
        SchedulingEventParticipantRecord(
            event_id=event.id, participant_id=participant_id, role=role
        )
    )
    await session.flush()
    return participant_id


async def _notified(
    session: AsyncSession, event: SchedulingEventRecord
) -> list[SchedulingParticipantRecord]:
    rows = await session.scalars(
        select(SchedulingParticipantRecord)
        .join(
            SchedulingEventParticipantRecord,
            SchedulingEventParticipantRecord.participant_id == SchedulingParticipantRecord.id,
        )
        .where(
            SchedulingEventParticipantRecord.event_id == event.id,
            SchedulingEventParticipantRecord.role == NOTIFIED_ROLE,
        )
    )
    return list(rows)


def _channels(person: SchedulingParticipantRecord) -> list[Channel]:
    return [
        channel
        for channel, address in ((Channel.EMAIL, person.email), (Channel.SMS, person.phone))
        if address
    ]


def _queue(
    session: AsyncSession,
    event: SchedulingEventRecord,
    person: SchedulingParticipantRecord,
    kind: MessageKind,
    fire_at: datetime,
    now: datetime,
) -> None:
    for channel in _channels(person):
        session.add(
            SchedulingReminderRecord(
                id=uuid4(),
                business_id=event.business_id,
                event_id=event.id,
                participant_id=person.id,
                kind=kind.value,
                channel=channel.value,
                fire_at=fire_at,
                event_version=event.version,
                status=PENDING,
                created_at=now,
            )
        )


async def _skip_pending(session: AsyncSession, event: SchedulingEventRecord) -> None:
    await session.execute(
        update(SchedulingReminderRecord)
        .where(
            SchedulingReminderRecord.event_id == event.id,
            SchedulingReminderRecord.status == PENDING,
        )
        .values(status=SKIPPED, detail="superseded")
    )


async def queue_confirmed(
    session: AsyncSession,
    event: SchedulingEventRecord,
    now: datetime,
    kind: MessageKind = MessageKind.CONFIRMATION,
) -> None:
    """A confirmation (or a moved-visit notice) now, then the reminders
    still ahead. Reminders for the old time are skipped."""

    await _skip_pending(session, event)
    for person in await _notified(session, event):
        _queue(session, event, person, kind, now, now)
        for kind, fire_at in reminder_times(event.starts_at, now):
            _queue(session, event, person, kind, fire_at, now)


async def queue_cancelled(
    session: AsyncSession, event: SchedulingEventRecord, now: datetime
) -> None:
    await _skip_pending(session, event)
    for person in await _notified(session, event):
        _queue(session, event, person, MessageKind.CANCELLED, now, now)
