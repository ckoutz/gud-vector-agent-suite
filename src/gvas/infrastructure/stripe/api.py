"""Stripe checkout adapter for accepted quotes.

Only this module talks HTTP to ``https://api.stripe.com/v1``. One call shape:
``POST /checkout/sessions`` (mode ``payment``) with a form-encoded body, the
secret key as a bearer header, and the quote's delivery idempotency key as the
``Idempotency-Key`` so a retried accept returns the session already opened.
The key, request bodies and provider responses never leave this module; errors
carry the status code only.
"""

import logging
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.domain.payments import (
    PaymentCheckoutError,
    PaymentCheckoutRequest,
    PaymentCheckoutResult,
)
from gvas.infrastructure.stripe.config import StripeSettings

logger = logging.getLogger(__name__)

CHECKOUT_SESSIONS_PATH: Final = "/checkout/sessions"


class StripeCheckoutError(PaymentCheckoutError):
    """Raised when a checkout call should be retried or surfaced as a failure."""


class _CheckoutSessionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    url: str | None = None
    payment_intent: str | None = None


def checkout_form(request: PaymentCheckoutRequest) -> dict[str, str]:
    """The form-encoded session body: one line per payment item, plus the
    return URLs, the public client reference and our tracking metadata."""

    form: dict[str, str] = {
        "mode": "payment",
        "success_url": request.success_url,
        "cancel_url": request.cancel_url,
        "client_reference_id": request.client_reference,
    }
    for index, item in enumerate(request.line_items):
        prefix = f"line_items[{index}]"
        form[f"{prefix}[quantity]"] = str(item.quantity)
        form[f"{prefix}[price_data][currency]"] = request.currency
        form[f"{prefix}[price_data][unit_amount]"] = str(item.amount_minor)
        form[f"{prefix}[price_data][product_data][name]"] = item.description
    for key, value in request.metadata.items():
        form[f"metadata[{key}]"] = value
    return form


class StripeCheckout:
    """Implements ``PaymentCheckoutPort`` against the checkout sessions API."""

    def __init__(self, settings: StripeSettings, client: httpx.AsyncClient) -> None:
        if not settings.is_configured:
            raise StripeCheckoutError("stripe secret key is not configured")
        self._settings = settings
        self._client = client

    async def create_checkout(self, request: PaymentCheckoutRequest) -> PaymentCheckoutResult:
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url.rstrip('/')}{CHECKOUT_SESSIONS_PATH}",
                data=checkout_form(request),
                headers={
                    "Authorization": f"Bearer {self._settings.secret_key}",
                    "Idempotency-Key": request.idempotency_key,
                },
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            logger.warning("checkout request failed: %s", type(error).__name__)
            raise StripeCheckoutError("payment provider was unreachable") from error
        if response.status_code >= 400:
            logger.warning("checkout session returned http %s", response.status_code)
            raise StripeCheckoutError(f"payment provider returned http {response.status_code}")
        try:
            parsed = _CheckoutSessionResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            logger.warning(
                "checkout session returned an unreadable response: %s",
                type(error).__name__,
            )
            raise StripeCheckoutError("payment provider returned an unreadable response") from error
        if not parsed.url:
            raise StripeCheckoutError("payment provider returned no checkout url")
        return PaymentCheckoutResult(
            session_id=parsed.id,
            checkout_url=parsed.url,
            payment_intent_id=parsed.payment_intent,
        )
