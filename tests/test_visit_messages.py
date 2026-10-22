from datetime import datetime, time, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.visit_email import VISIT_EMAIL_COMMAND_TYPE
from gvas.infrastructure.models import OutboxMessage
from gvas.infrastructure.visit_messages import VisitMessenger
from gvas.scheduling.core.engine import SchedulingEngine, owner_principal
from gvas.scheduling.core.model import Actor, BookingPolicy, EventStatus, WeeklyWindow
from gvas.scheduling.core.records import SchedulingReminderRecord
from gvas.scheduling.field_service.booking import FieldServiceScheduling, VisitDetails
from scheduling_fixtures import LA, START, TUESDAY_10, Clock, seed_business

pytestmark = pytest.mark.postgres
HOUR = timedelta(hours=1)


async def book(
    factory: async_sessionmaker[AsyncSession], clock: Clock
) -> tuple[UUID, UUID, SchedulingEngine]:
    business_id = await seed_business(factory, "larkspur")
    engine = SchedulingEngine(factory, clock=clock)
    owner = owner_principal(business_id)
    setup = await engine.create_calendar(
        business_id, name="Visits", time_zone="America/Los_Angeles", owner=owner,
        policy=BookingPolicy(min_notice=HOUR),
    )  # fmt: skip
    await engine.set_weekly_hours(
        business_id, setup.resource_id, [WeeklyWindow(1, time(8), time(17))], by=owner
    )
    scheduling = FieldServiceScheduling(engine, factory)
    service = await scheduling.create_service(
        business_id, setup.resource_id, name="Estimate", duration=HOUR
    )
    held = await scheduling.hold(
        business_id, service.service_id, start=TUESDAY_10, holder_ref="chat",
        holder_ip=None, actor=Actor.GUS,
    )  # fmt: skip
    await scheduling.confirm(
        business_id,
        held.event_id,
        VisitDetails(name="Jane", email="jane@example.com", phone="+15550100009"),
        actor=Actor.GUS,
    )
    return business_id, held.event_id, engine


async def statuses(factory: async_sessionmaker[AsyncSession], event_id: UUID) -> dict[str, str]:
    async with factory() as session:
        rows = await session.scalars(
            select(SchedulingReminderRecord).where(SchedulingReminderRecord.event_id == event_id)
        )
        return {f"{row.kind}/{row.channel}": row.status for row in rows}


async def emails(factory: async_sessionmaker[AsyncSession]) -> list[dict[str, object]]:
    async with factory() as session:
        rows = await session.scalars(
            select(OutboxMessage.payload).where(
                OutboxMessage.command_type == VISIT_EMAIL_COMMAND_TYPE
            )
        )
        return [dict(row) for row in rows]


async def test_the_confirmation_e_mail_carries_the_invite_and_no_consent_means_no_text(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    _, event_id, _ = await book(postgres_session_factory, clock)
    messenger = VisitMessenger(
        postgres_session_factory, organizer_email="Gud <info@gudvector.com>", clock=clock
    )

    assert await messenger.run_due() == 1

    sent = await emails(postgres_session_factory)
    assert [email["subject"] for email in sent] == ["Your visit is booked"]
    invite = str(sent[0]["invite"])
    assert f"UID:{event_id}@gudvector.com" in invite and "METHOD:REQUEST" in invite
    state = await statuses(postgres_session_factory, event_id)
    assert state["confirmation/email"] == "sent"
    assert state["confirmation/sms"] == "skipped"
    assert state["reminder_24h/email"] == "pending"


async def test_cancelling_sends_a_cancel_invite_and_drops_the_reminders(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    business_id, event_id, engine = await book(postgres_session_factory, clock)
    messenger = VisitMessenger(
        postgres_session_factory, organizer_email="info@gudvector.com", clock=clock
    )
    await messenger.run_due()

    await engine.set_status(business_id, event_id, EventStatus.CANCELLED, actor=Actor.SYSTEM)
    clock.advance(timedelta(days=1))
    await messenger.run_due()

    subjects = [email["subject"] for email in await emails(postgres_session_factory)]
    assert subjects == ["Your visit is booked", "Your visit is cancelled"]
    cancel = [e for e in await emails(postgres_session_factory) if e["cancelled"]][0]
    assert "METHOD:CANCEL" in str(cancel["invite"])
    assert (await statuses(postgres_session_factory, event_id))["reminder_24h/email"] == "skipped"


async def test_a_text_due_in_quiet_hours_waits_for_eight_in_the_morning(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock(START)
    _, event_id, _ = await book(postgres_session_factory, clock)
    clock.now = datetime(2026, 10, 26, 21, 30, tzinfo=LA)
    messenger = VisitMessenger(
        postgres_session_factory, organizer_email="info@gudvector.com", clock=clock
    )

    await messenger.run_due()

    async with postgres_session_factory() as session:
        text = await session.scalar(
            select(SchedulingReminderRecord).where(
                SchedulingReminderRecord.event_id == event_id,
                SchedulingReminderRecord.kind == "confirmation",
                SchedulingReminderRecord.channel == "sms",
            )
        )
    assert text is not None
    assert text.status == "pending"
    assert text.fire_at == datetime(2026, 10, 27, 8, tzinfo=LA)


async def test_the_visit_link_cancels_once_and_asking_for_another_time_keeps_the_visit(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gvas.infrastructure.visit_links import VisitLinkService

    clock = Clock()
    business_id, event_id, engine = await book(postgres_session_factory, clock)
    await VisitMessenger(
        postgres_session_factory,
        organizer_email="info@gudvector.com",
        clock=clock,
        link_base_url="https://api.example/",
    ).run_due()
    body = str((await emails(postgres_session_factory))[0]["body"])
    assert "https://api.example/v1/visits/" in body
    token = body.rsplit("/v1/visits/", 1)[1].strip()
    links = VisitLinkService(postgres_session_factory, engine, clock=clock)

    assert (await links.preview(token)).actions == ("cancel", "reschedule")
    asked = await links.act(token, "reschedule")
    assert "stays booked" in asked.body
    view = await engine.event(business_id, event_id, by=owner_principal(business_id))
    assert view.status is EventStatus.CONFIRMED

    cancelled = await links.act(token, "cancel")
    assert cancelled.title == "Your visit is cancelled"
    view = await engine.event(business_id, event_id, by=owner_principal(business_id))
    assert view.status is EventStatus.CANCELLED
    assert (await links.preview(token)).title == "This link no longer works"
    assert (await links.act(token, "cancel")).title == "This link no longer works"
    assert (await links.preview("not-a-token")).title == "This link no longer works"


async def test_the_visit_link_expires_when_the_visit_ends(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gvas.infrastructure.visit_links import VisitLinkService

    clock = Clock()
    _, _, engine = await book(postgres_session_factory, clock)
    await VisitMessenger(
        postgres_session_factory,
        organizer_email="info@gudvector.com",
        clock=clock,
        link_base_url="https://api.example",
    ).run_due()
    body = str((await emails(postgres_session_factory))[0]["body"])
    token = body.rsplit("/v1/visits/", 1)[1].strip()
    clock.now = TUESDAY_10 + 2 * HOUR

    page = await VisitLinkService(postgres_session_factory, engine, clock=clock).preview(token)

    assert page.title == "This link no longer works"
