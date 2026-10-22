"""The customer's no-login link for one visit: see it, cancel it, or ask for
another time. Every reschedule waits for the owner (the safe default until
Cameron decides otherwise). The link works for that visit only and expires
when the visit ends."""

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.domain.customer_linking import enqueue_intake_owner_notice
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import format_slot_label
from gvas.infrastructure.models import Business
from gvas.infrastructure.unit_of_work import SqlUnitOfWorkFactory
from gvas.scheduling.core.engine import SchedulingEngine
from gvas.scheduling.core.messages import hash_action_token, new_action_token
from gvas.scheduling.core.model import OCCUPYING, Actor, EventStatus
from gvas.scheduling.core.records import SchedulingActionTokenRecord, SchedulingEventRecord

LINK_PATH = "/v1/visits/"
ACTIONS = ["cancel", "reschedule"]


@dataclass(frozen=True)
class VisitPage:
    title: str
    body: str
    actions: tuple[str, ...] = ()


async def mint_link(
    session: AsyncSession, event: SchedulingEventRecord, base_url: str, now: datetime
) -> str:
    token = new_action_token()
    session.add(
        SchedulingActionTokenRecord(
            token_hash=hash_action_token(token),
            business_id=event.business_id,
            event_id=event.id,
            actions=list(ACTIONS),
            expires_at=event.ends_at,
            created_at=now,
        )
    )
    return f"{base_url.rstrip('/')}{LINK_PATH}{token}"


_GONE = VisitPage("This link no longer works", "Contact the business to change your visit.")


class VisitLinkService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: SchedulingEngine,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._unit_of_work_factory = SqlUnitOfWorkFactory(session_factory)
        self._clock = clock

    async def _load(
        self, session: AsyncSession, token: str
    ) -> tuple[SchedulingActionTokenRecord, SchedulingEventRecord, str] | None:
        link = await session.get(SchedulingActionTokenRecord, hash_action_token(token))
        if link is None or link.used_at is not None or link.expires_at <= self._clock():
            return None
        event = await session.get(SchedulingEventRecord, link.event_id)
        if event is None or EventStatus(event.status) not in OCCUPYING:
            return None
        business = await session.get(Business, link.business_id)
        name = "" if business is None else (business.display_name or business.name)
        return link, event, name or "the business"

    async def preview(self, token: str) -> VisitPage:
        async with self._session_factory() as session:
            found = await self._load(session, token)
        if found is None:
            return _GONE
        _, event, name = found
        when = format_slot_label(event.starts_at, ZoneInfo(event.time_zone))
        return VisitPage(f"Your visit with {name}", when, tuple(ACTIONS))

    async def act(self, token: str, action: str) -> VisitPage:
        if action not in ACTIONS:
            return _GONE
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            found = await self._load(session, token)
            if found is None:
                return _GONE
            link, event, name = found
            when = format_slot_label(event.starts_at, ZoneInfo(event.time_zone))
            if action == "cancel":
                link.used_at = now
        business_id = BusinessId(event.business_id)
        if action == "cancel":
            await self._engine.set_status(
                business_id, event.id, EventStatus.CANCELLED, actor=Actor.PAGE
            )
            notice = f"{event.title} cancelled their visit for {when}. The time is open again."
            page = VisitPage("Your visit is cancelled", f"{name} has been told.")
        else:
            notice = (
                f"{event.title} asked to move their visit for {when}. "
                "Their visit stays booked until you change it."
            )
            page = VisitPage(
                "We asked for another time",
                f"{name} will get back to you. Your visit for {when} stays booked until then.",
            )
        async with self._unit_of_work_factory() as unit_of_work:
            await enqueue_intake_owner_notice(
                unit_of_work,
                business_id,
                correlation_id=f"visit:{event.id}:{action}:{int(now.timestamp())}",
                text=notice,
            )
            await unit_of_work.commit()
        return page


def visit_page(page: VisitPage) -> str:
    labels = {"cancel": "Cancel my visit", "reschedule": "Ask for another time"}
    forms = "".join(
        f'<form method="post" action="?action={html.escape(action)}">'
        f"<button type='submit'>{labels[action]}</button></form>"
        for action in page.actions
    )
    title = html.escape(page.title)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:34rem;margin:4rem auto;"
        "padding:0 1rem;color:#222}button{font-size:1rem;padding:.6rem 1.2rem;margin:.3rem 0}"
        "</style></head><body>"
        f"<h1>{title}</h1><p>{html.escape(page.body)}</p>{forms}</body></html>"
    )
