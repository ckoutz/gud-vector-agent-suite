"""Owner decisions shared by every surface the owner acts from.

``approve booking <ref>`` in the owner channel, and the Approve button on the
dashboard, run the same transition here, so the customer sees the same
outcome whichever one the owner used.
"""

from datetime import datetime

from gvas.domain.customer_linking import link_quote_customer
from gvas.domain.customers import (
    PortalLoginEmailRequest,
    hash_portal_token,
    new_portal_token,
    portal_login_email_command,
    portal_login_url,
)
from gvas.domain.enums import QuoteStatus
from gvas.domain.identifiers import BusinessId, MessageKey
from gvas.domain.intake import (
    BookingDecision,
    BookingDecisionAction,
    IntakeConversation,
    IntakeCustomerEmail,
    IntakeState,
    format_slot_label,
    intake_booking_arrange_command,
    intake_booking_cancel_command,
    intake_customer_email_command,
)
from gvas.domain.owner import OWNER_LOGIN_TOKEN_TTL, OwnerLoginToken
from gvas.domain.quotes import InvalidQuoteTransitionError, Quote, quote_delivery_command
from gvas.domain.repositories import BusinessRecord, UnitOfWork


class BookingDecisionOutcome:
    """The reply text and whether the decision changed anything."""

    def __init__(self, text: str, *, applied: bool) -> None:
        self.text = text
        self.applied = applied


async def decide_booking(
    unit_of_work: UnitOfWork,
    business_id: BusinessId,
    decision: BookingDecision,
    now: datetime,
) -> BookingDecisionOutcome:
    """Approve or decline one website booking request of ``business_id``.

    Deciding the same reference twice is a no-op with a clear reply; an
    unknown reference gets one clear reply. Commits only when applied.
    """

    reference = decision.reference
    conversation = await unit_of_work.intake_conversations.lock_by_reference(business_id, reference)
    if conversation is None:
        return BookingDecisionOutcome(f"I can't find booking {reference}.", applied=False)
    if conversation.state is IntakeState.APPROVED:
        return BookingDecisionOutcome(f"Booking {reference} is already approved.", applied=False)
    if conversation.state is IntakeState.DECLINED:
        return BookingDecisionOutcome(f"Booking {reference} was already declined.", applied=False)
    if conversation.state is not IntakeState.AWAITING_OWNER:
        return BookingDecisionOutcome(
            f"Booking {reference} isn't waiting on a decision.", applied=False
        )
    if decision.action is BookingDecisionAction.APPROVE:
        return await _approve_booking(unit_of_work, conversation, now)
    return await _decline_booking(unit_of_work, business_id, conversation, decision, now)


async def _approve_booking(
    unit_of_work: UnitOfWork, conversation: IntakeConversation, now: datetime
) -> BookingDecisionOutcome:
    if conversation.requested_slot_start is not None and conversation.requested_slot_start <= now:
        return BookingDecisionOutcome(
            f"Booking {conversation.reference}'s requested time has already "
            "passed — decline it or line up a new time with the customer.",
            applied=False,
        )
    updated = conversation.with_updates(now, state=IntakeState.APPROVED, decision_at=now)
    await unit_of_work.intake_conversations.save(updated)
    await unit_of_work.outbox.enqueue(intake_booking_arrange_command(updated))
    await unit_of_work.commit()
    name = updated.collected.name or "the customer"
    slot = (
        format_slot_label(updated.requested_slot_start)
        if updated.requested_slot_start is not None
        else "their requested time"
    )
    return BookingDecisionOutcome(
        f"Approved booking {updated.reference} — arranging {slot} for {name}.", applied=True
    )


async def _decline_booking(
    unit_of_work: UnitOfWork,
    business_id: BusinessId,
    conversation: IntakeConversation,
    decision: BookingDecision,
    now: datetime,
) -> BookingDecisionOutcome:
    updated = conversation.with_updates(
        now,
        state=IntakeState.DECLINED,
        decision_at=now,
        decision_reason=decision.reason,
    )
    await unit_of_work.intake_conversations.save(updated)
    business = await unit_of_work.businesses.get(business_id)
    email = updated.collected.email
    notified = "the customer has been notified"
    if email:
        booking_link = business.calendly_url if business is not None else None
        await unit_of_work.outbox.enqueue(
            intake_customer_email_command(
                IntakeCustomerEmail(
                    business_id=business_id,
                    to=email,
                    subject="About your requested appointment",
                    body=decline_body(updated, booking_link),
                    idempotency_key=f"intake_decline:{conversation.conversation_id}",
                )
            )
        )
    else:
        notified = "no customer email was collected, so nothing was sent"
    if updated.booked_event_uri:
        await unit_of_work.outbox.enqueue(intake_booking_cancel_command(updated))
        notified = f"{notified}, and the Calendly event is being canceled"
    await unit_of_work.commit()
    return BookingDecisionOutcome(
        f"Declined booking {updated.reference}; {notified}.", applied=True
    )


def decline_body(conversation: IntakeConversation, booking_link: str | None) -> str:
    lines = [
        "Thanks for reaching out. The owner reviewed your request and can't "
        "take it on at the requested time.",
    ]
    if conversation.decision_reason:
        lines.append(f"Reason: {conversation.decision_reason}")
    if booking_link:
        lines.append(f"If another time works, you can pick one directly: {booking_link}")
    else:
        lines.append("If another time works, reply here and we'll sort it out.")
    return "\n".join(lines)


async def approve_quote(
    unit_of_work: UnitOfWork, quote: Quote, message_key: MessageKey, now: datetime
) -> Quote:
    """The owner's OK: link the recipient as a customer and queue delivery —
    exactly what replying ``approve`` does. The caller commits."""

    approved = quote.approve(message_key, now)
    approved, _ = await link_quote_customer(unit_of_work, approved, now)
    await unit_of_work.quotes.save(approved, expected_version=quote.version)
    await unit_of_work.outbox.enqueue(quote_delivery_command(approved))
    return approved


async def reject_quote(
    unit_of_work: UnitOfWork, quote: Quote, message_key: MessageKey, now: datetime
) -> Quote:
    if quote.status is not QuoteStatus.AWAITING_APPROVAL:
        raise InvalidQuoteTransitionError(f"cannot reject quote in {quote.status}")
    rejected = quote.reject(message_key, now)
    await unit_of_work.quotes.save(rejected, expected_version=quote.version)
    return rejected


async def issue_owner_login(
    unit_of_work: UnitOfWork, business: BusinessRecord, email: str, now: datetime
) -> None:
    """Mint one owner magic link and queue it to ``email``. The link has the
    same shape as a customer's so one sign-in page serves both."""

    if not business.site_url:
        return
    raw = new_portal_token()
    token_hash = hash_portal_token(raw)
    expires_at = now + OWNER_LOGIN_TOKEN_TTL
    await unit_of_work.owner_login_tokens.add(
        OwnerLoginToken(
            token_hash=token_hash,
            business_id=business.business_id,
            email=email,
            expires_at=expires_at,
            created_at=now,
        )
    )
    request = PortalLoginEmailRequest(
        business_id=business.business_id,
        to=email,
        business_display_name=business.display_name or business.name,
        login_url=portal_login_url(business.site_url, raw),
        idempotency_key=f"owner-login:{token_hash}",
        expires_at=expires_at,
    )
    await unit_of_work.outbox.enqueue(portal_login_email_command(request, token_hash=token_hash))


__all__ = [
    "BookingDecisionOutcome",
    "approve_quote",
    "decide_booking",
    "decline_body",
    "issue_owner_login",
    "reject_quote",
]
