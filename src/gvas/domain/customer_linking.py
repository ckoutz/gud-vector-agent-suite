"""Customer identity bookkeeping shared by the quote workflow and the portal.

A customer is ``(business, lowercased e-mail)``. Quotes addressed to an e-mail
are linked to that customer whenever they pass through approval or delivery,
and the portal links any older ones the first time the customer signs in — so
no historical backfill is ever required.
"""

from datetime import datetime

from gvas.domain.customers import CustomerRecord
from gvas.domain.messages import OutboundOwnerMessage, TextPart
from gvas.domain.outbox import owner_reply_command
from gvas.domain.quotes import Quote
from gvas.domain.repositories import UnitOfWork


async def enqueue_quote_owner_notice(
    unit_of_work: UnitOfWork, quote: Quote, *, correlation_id: str, text: str
) -> bool:
    """Queue ``text`` for the owner in the conversation where ``quote`` was
    requested, anchored to the message that started it — the same routing an
    owner reply takes, so it lands in the same owner conversation.

    Returns False when the anchor is gone; True when queued or when the same
    ``correlation_id`` was queued before (the notice is idempotent).
    """

    source = await unit_of_work.inbound_messages.find_by_key(
        quote.business_id, quote.conversation_id, quote.source_message_key
    )
    if source is None:
        return False
    existing = await unit_of_work.outbound_messages.find_by_correlation(
        quote.business_id, quote.conversation_id, correlation_id
    )
    if existing is not None:
        return True
    message = OutboundOwnerMessage(
        business_id=quote.business_id,
        conversation_ref=quote.conversation_ref,
        parts=(TextPart(text=text),),
        correlation_id=correlation_id,
    )
    outbound_message_id = await unit_of_work.outbound_messages.create(
        message, quote.conversation_id, source.inbound_message_id
    )
    await unit_of_work.outbox.enqueue(owner_reply_command(quote.business_id, outbound_message_id))
    return True


async def link_quote_customer(
    unit_of_work: UnitOfWork, quote: Quote, now: datetime
) -> tuple[Quote, CustomerRecord | None]:
    """Upsert the recipient as a customer and return the quote linked to it.

    Quotes going to a phone number only have no customer identity and come
    back unchanged. The returned quote carries a bumped version when it
    changed; the caller decides whether and how to persist it.
    """

    email = quote.recipient_email
    draft = quote.draft
    if email is None or draft is None:
        return quote, None
    customer = await unit_of_work.customers.upsert(
        quote.business_id,
        email,
        display_name=draft.recipient.display_name,
        phone=draft.recipient.phone_number,
        now=now,
    )
    return quote.link_customer(customer.customer_id, now), customer


__all__ = ["link_quote_customer"]
