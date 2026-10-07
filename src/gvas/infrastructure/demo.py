"""Demo-deployment adapters: nothing leaves the process.

``GVAS_DEMO_MODE`` swaps every outbound channel (customer and owner e-mail,
texts, owner channel messages, sign-in links, reports) for one that writes to the log, and the
booking calendar for generated openings. A fictional business then runs the
real workflows, owner approval included, with no one contacted. The
production composition decides when these are used, and refuses to start a
demo that still holds a real provider credential.
"""

import hashlib
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.config import DemoSettings
from gvas.domain.customers import PortalLoginEmailRequest
from gvas.domain.enums import DeliveryStatus
from gvas.domain.identifiers import BusinessId
from gvas.domain.intake import (
    AvailableSlot,
    BookingKind,
    BookingRequest,
    BookingResult,
    IntakeState,
)
from gvas.domain.messages import (
    AttachmentPayload,
    AttachmentReference,
    ConversationRef,
    CustomerDeliveryRequest,
    CustomerTextRequest,
    DeliveryReceipt,
    OutboundOwnerMessage,
    TextPart,
)
from gvas.domain.owner import CalendarEvent, CalendarEventSource
from gvas.domain.owner_email import OwnerEmailRequest
from gvas.domain.reporting import ReportEmailRequest
from gvas.infrastructure.intake_models import IntakeConversation as IntakeRow
from gvas.infrastructure.models import Business

logger = logging.getLogger(__name__)

DEMO_EVENT_TYPE_URI = "demo:estimate"
DEMO_DETAIL = "demo mode: logged, not sent"
# About one generated hour in this many is shown as already taken, so the
# openings Gus offers look like a real week rather than an empty calendar.
BUSY_ONE_IN = 3
# Sunday is closed.
CLOSED_WEEKDAYS = frozenset({6})
# Requests waiting for the owner and approved bookings hold their slot.
HOLDING_STATES = (IntakeState.AWAITING_OWNER.value, IntakeState.APPROVED.value)
TITLE_MAX_CHARS = 80


class DemoModeError(RuntimeError):
    """Something a demo deployment cannot do because it has no provider."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _receipt(
    key: str, *, customer_link: str | None = None, emailed: bool | None = None
) -> DeliveryReceipt:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return DeliveryReceipt(
        status=DeliveryStatus.DELIVERED,
        provider_message_id=f"demo-{digest}",
        occurred_at=datetime.now(UTC),
        detail=DEMO_DETAIL,
        customer_link=customer_link,
        emailed=emailed,
    )


def _log(kind: str, business_id: BusinessId, to: str, text: str) -> None:
    logger.info("demo mode, not sent: %s to %s (business %s)\n%s", kind, to, business_id, text)


class LoggedCustomerEmail:
    """Customer e-mail (quotes, booking notices), logged instead of sent."""

    async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt:
        lines = [f"Subject: {request.subject or ''}", request.body_text]
        if request.quote_url:
            lines.append(f"Quote: {request.quote_url}")
        lines.extend(link for link in request.links if link != request.quote_url)
        _log("customer e-mail", request.business_id, request.recipient.address, "\n".join(lines))
        return _receipt(
            request.idempotency_key,
            customer_link=request.quote_url,
            emailed=request.recipient.email_address is not None,
        )


class LoggedCustomerText:
    async def send_text(self, request: CustomerTextRequest) -> DeliveryReceipt:
        _log("text", request.business_id, request.phone_number, request.text)
        return _receipt(request.idempotency_key)


class LoggedOwnerEmail:
    async def send(self, request: OwnerEmailRequest) -> DeliveryReceipt:
        _log(
            "owner e-mail",
            request.business_id,
            request.to,
            f"Subject: {request.subject}\n{request.text}",
        )
        return _receipt(request.idempotency_key)


class LoggedOwnerReply:
    """Owner channel messages (chat or text threads), logged instead of posted."""

    async def send(
        self, conversation_ref: ConversationRef, message: OutboundOwnerMessage
    ) -> DeliveryReceipt:
        text = "\n".join(part.text for part in message.parts if isinstance(part, TextPart))
        _log(
            "owner message",
            message.business_id,
            conversation_ref.external_conversation_id,
            text or "(attachment)",
        )
        return _receipt(message.correlation_id)


class LoggedPortalLoginEmail:
    """Sign-in links are logged so a demo can be signed into without e-mail.

    Only the demo composition builds this; it holds fictional data only.
    """

    async def send_login_link(self, request: PortalLoginEmailRequest) -> DeliveryReceipt:
        _log(
            "sign-in e-mail",
            request.business_id,
            request.to,
            f"Subject: {request.subject}\nSign in: {request.login_url}",
        )
        return _receipt(request.idempotency_key)


class LoggedReportEmail:
    async def deliver(self, request: ReportEmailRequest) -> DeliveryReceipt:
        _log(
            "report e-mail",
            request.business_id,
            request.recipient_address,
            f"Subject: {request.subject}\n{request.body_text}",
        )
        return _receipt(request.idempotency_key)


class NoAttachments:
    """A demo has no owner chat workspace, so there are no shared files to fetch."""

    async def fetch(self, attachment: AttachmentReference) -> AttachmentPayload:
        raise DemoModeError("demo mode has no shared files to fetch")


class DemoAvailability:
    """Generated openings in the business's zone, and bookings kept in GVAS.

    Working days (Monday to Saturday) offer one slot per ``slot_minutes``
    inside the configured hours. About one in three is shown as taken, picked
    by a stable hash so the same hour stays taken across calls, and any slot a
    waiting request or an approved booking holds is never offered again.
    ``book`` touches no calendar: the approved request is the booking.
    """

    def __init__(
        self, settings: DemoSettings, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    def serves(self, business_id: BusinessId) -> bool:
        return True

    async def available_slots(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[AvailableSlot, ...]:
        zone, held = await self._zone_and_holds(business_id, start, end)
        length = timedelta(minutes=self._settings.slot_minutes)
        first = self._settings.day_start_hour * 60
        last = self._settings.day_end_hour * 60 - self._settings.slot_minutes
        openings: list[AvailableSlot] = []
        day = start.astimezone(zone).date()
        while day <= end.astimezone(zone).date():
            if day.weekday() not in CLOSED_WEEKDAYS:
                for minute in range(first, last + 1, self._settings.slot_minutes):
                    slot_start = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=zone)
                    slot_end = slot_start + length
                    if not start <= slot_start < end or _busy(business_id, slot_start):
                        continue
                    if any(
                        _utc(slot_start) < h_end and h_start < _utc(slot_end)
                        for h_start, h_end in held
                    ):
                        continue
                    openings.append(AvailableSlot(start=slot_start, end=slot_end))
            day += timedelta(days=1)
        return tuple(openings)

    async def book(self, request: BookingRequest) -> BookingResult:
        logger.info(
            "demo mode: booked %s for %s (business %s), no calendar touched",
            request.slot_start.isoformat(),
            request.invitee_email,
            request.business_id,
        )
        return BookingResult(kind=BookingKind.BOOKED, event_type_uri=DEMO_EVENT_TYPE_URI)

    async def find_booking(self, request: BookingRequest) -> BookingResult | None:
        # Nothing external was ever written, so a retried attempt books again.
        return None

    async def booking_event_type_uri(self, business_id: BusinessId) -> str | None:
        return DEMO_EVENT_TYPE_URI

    async def cancel_booking(self, business_id: BusinessId, event_uri: str) -> None:
        logger.info("demo mode: canceled %s (business %s)", event_uri, business_id)

    async def _zone_and_holds(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[ZoneInfo, tuple[tuple[datetime, datetime], ...]]:
        async with self._session_factory() as session:
            name = await session.scalar(select(Business.timezone).where(Business.id == business_id))
            rows = await session.execute(
                select(IntakeRow.requested_slot_start, IntakeRow.requested_slot_end).where(
                    IntakeRow.business_id == business_id,
                    IntakeRow.state.in_(HOLDING_STATES),
                    IntakeRow.requested_slot_start.is_not(None),
                    IntakeRow.requested_slot_start < end,
                )
            )
        held = tuple(
            (_utc(slot_start), _utc(slot_end or slot_start + timedelta(minutes=1)))
            for slot_start, slot_end in rows
            if slot_start is not None and _utc(slot_end or slot_start) >= _utc(start)
        )
        return _zone(name or self._settings.timezone, self._settings.timezone), held


class DemoBookedEvents:
    """The dashboard's bookings: approved website requests booked in GVAS."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def serves(self, business_id: BusinessId) -> bool:
        return True

    async def upcoming(
        self, business_id: BusinessId, start: datetime, end: datetime
    ) -> tuple[CalendarEvent, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(IntakeRow)
                .where(
                    IntakeRow.business_id == business_id,
                    IntakeRow.state == IntakeState.APPROVED.value,
                    IntakeRow.booking_kind == BookingKind.BOOKED.value,
                    IntakeRow.requested_slot_start >= start,
                    IntakeRow.requested_slot_start < end,
                )
                .order_by(IntakeRow.requested_slot_start)
            )
            return tuple(_booking_event(row) for row in rows if row.requested_slot_start)


def _booking_event(row: IntakeRow) -> CalendarEvent:
    collected = row.collected or {}

    def text(key: str) -> str | None:
        value = collected.get(key)
        return value.strip() or None if isinstance(value, str) else None

    details = text("details")
    title = "Estimate"
    if details:
        title = details if len(details) <= TITLE_MAX_CHARS else details[: TITLE_MAX_CHARS - 1] + "…"
    assert row.requested_slot_start is not None  # noqa: S101 - filtered by the caller
    return CalendarEvent(
        source=CalendarEventSource.BOOKING,
        title=title,
        start=_utc(row.requested_slot_start),
        end=None if row.requested_slot_end is None else _utc(row.requested_slot_end),
        location=text("address"),
        invitee_name=text("name"),
        invitee_email=text("email"),
        reference=row.reference,
    )


def _busy(business_id: BusinessId, slot_start: datetime) -> bool:
    seed = f"{business_id}:{_utc(slot_start).isoformat()}".encode()
    return hashlib.sha256(seed).digest()[0] % BUSY_ONE_IN == 0


def _zone(name: str, fallback: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(fallback)
