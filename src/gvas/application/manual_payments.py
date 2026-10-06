"""Worker side of a manual payment: close the card checkout it replaced,
and send the customer's receipt unless the payment was voided first."""

from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from gvas.domain.identifiers import BusinessId, QuoteId
from gvas.domain.payments import PaymentCheckoutClosedError
from gvas.domain.ports import PaymentCheckoutPort
from gvas.domain.repositories import UnitOfWork


class ManualPaymentEffectsService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        *,
        checkout: PaymentCheckoutPort | None,
        receipts: Callable[[BusinessId, Mapping[str, object]], Awaitable[None]] | None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._checkout = checkout
        self._receipts = receipts

    async def expire_checkout(self, payload: Mapping[str, object]) -> str:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("checkout expiry payload is incomplete")
        if self._checkout is None:
            return "unconfigured"
        try:
            await self._checkout.expire_checkout(session_id)
        except PaymentCheckoutClosedError:
            return "already closed"
        return "closed"

    async def send_receipt(self, business_id: BusinessId, payload: Mapping[str, object]) -> str:
        try:
            quote_id = QuoteId(UUID(str(payload.get("quote_id"))))
            payment_id = UUID(str(payload.get("payment_id")))
        except ValueError as error:
            raise ValueError("manual receipt payload is incomplete") from error
        async with self._unit_of_work_factory() as unit_of_work:
            payments = await unit_of_work.payments.list_for_quote(business_id, quote_id)
        if not any(p.payment_id == payment_id and p.voided_at is None for p in payments):
            return "voided"
        if self._receipts is None:
            raise RuntimeError("customer e-mail is not wired")
        await self._receipts(business_id, payload)
        return "sent"
