"""Stripe webhook payloads: only the checkout-session events GVAS consumes.

Verification happens in ``signature.py`` before this parsing runs; this module
only normalizes the JSON into the provider-neutral ``PaymentWebhookEvent``.
"""

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.domain.payments import (
    STRIPE_PROVIDER,
    PaymentEventOutcome,
    PaymentWebhookEvent,
)

CHECKOUT_COMPLETED: Final = "checkout.session.completed"
CHECKOUT_ASYNC_SUCCEEDED: Final = "checkout.session.async_payment_succeeded"
CHECKOUT_ASYNC_FAILED: Final = "checkout.session.async_payment_failed"

#: Events whose payment has settled.
PAYMENT_SUCCEEDED_EVENTS: Final = frozenset({CHECKOUT_COMPLETED, CHECKOUT_ASYNC_SUCCEEDED})
#: Every event type the webhook endpoint subscribes to.
HANDLED_EVENTS: Final = frozenset(
    {CHECKOUT_COMPLETED, CHECKOUT_ASYNC_SUCCEEDED, CHECKOUT_ASYNC_FAILED}
)


class StripeEventError(ValueError):
    pass


class _StripeSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    payment_intent: str | None = None
    metadata: dict[str, str] = {}


class _StripeEventData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    object: _StripeSession


class _StripeEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    type: str
    data: _StripeEventData


def parse_checkout_event(body: bytes) -> PaymentWebhookEvent:
    """Decode one event body; non-checkout payloads parse with empty fields."""

    try:
        parsed = _StripeEvent.model_validate(json.loads(body))
    except (ValueError, ValidationError) as error:
        raise StripeEventError("stripe event is not readable") from error
    session = parsed.data.object
    if parsed.type in PAYMENT_SUCCEEDED_EVENTS:
        outcome = PaymentEventOutcome.SUCCEEDED
    elif parsed.type == CHECKOUT_ASYNC_FAILED:
        outcome = PaymentEventOutcome.FAILED
    else:
        outcome = PaymentEventOutcome.OTHER
    return PaymentWebhookEvent(
        provider=STRIPE_PROVIDER,
        event_id=parsed.id,
        event_type=parsed.type,
        outcome=outcome,
        checkout_session_id=session.id or None,
        payment_intent_id=session.payment_intent,
        metadata=session.metadata,
    )
