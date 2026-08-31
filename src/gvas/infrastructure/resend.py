"""Resend email delivery for approved customer quotes.

Only this module talks HTTP to Resend. The domain keeps hosted links opaque, so
the known portal token is resolved to the configured portal URL here; unknown
tokens are refused rather than guessed at.
"""

from datetime import UTC, datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.config import ResendSettings
from gvas.domain.enums import DeliveryStatus, RecipientAddressKind
from gvas.domain.messages import CustomerDeliveryRequest, DeliveryReceipt
from gvas.infrastructure.hosted_links import PORTAL_LOGIN_LINK_REFERENCE

EMAILS_PATH: Final = "/emails"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ResendDeliveryError(RuntimeError):
    """Raised when a quote email attempt should be retried by the dispatcher."""


class ResendEmailResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str


class ResendQuoteDeliveryAdapter:
    """Sends the approved quote body to the customer's email address."""

    def __init__(self, settings: ResendSettings, client: httpx.AsyncClient) -> None:
        if not settings.is_configured:
            raise ResendDeliveryError("resend api key and from address are not configured")
        self._settings = settings
        self._client = client

    async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt:
        if request.recipient.address_kind is not RecipientAddressKind.EMAIL:
            raise ResendDeliveryError("resend delivers to email recipients only")
        if request.attachments:
            raise ResendDeliveryError("quote emails do not carry attachments")
        payload: dict[str, object] = {
            "from": self._settings.from_address,
            "to": [request.recipient.address],
            "subject": request.subject or "Your quote",
            "text": self._body(request),
        }
        if self._settings.reply_to_address:
            payload["reply_to"] = self._settings.reply_to_address
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url}{EMAILS_PATH}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Idempotency-Key": request.idempotency_key,
                },
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise ResendDeliveryError("resend was unreachable") from error
        return self._receipt(response)

    def _body(self, request: CustomerDeliveryRequest) -> str:
        links = [self._resolve_link(reference) for reference in request.links]
        if not links:
            return request.body_text
        return "\n\n".join([request.body_text, *links])

    def _resolve_link(self, reference: str) -> str:
        if reference == PORTAL_LOGIN_LINK_REFERENCE:
            return self._settings.portal_url
        raise ResendDeliveryError("quote carries an unknown hosted link reference")

    @staticmethod
    def _receipt(response: httpx.Response) -> DeliveryReceipt:
        if response.status_code >= 400:
            raise ResendDeliveryError(f"resend returned http {response.status_code}")
        try:
            parsed = ResendEmailResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ResendDeliveryError("resend returned an unreadable response") from error
        return DeliveryReceipt(
            status=DeliveryStatus.ACCEPTED,
            provider_message_id=parsed.id,
            occurred_at=_utc_now(),
        )
