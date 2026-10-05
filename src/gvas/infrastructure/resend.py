"""Resend email delivery for approved customer quotes and published reports.

Only this module talks HTTP to Resend. The domain keeps hosted links opaque, so
the known portal token is resolved to the configured portal URL here; unknown
tokens are refused rather than guessed at. Report documents travel as inline
base64 attachments on the same ``/emails`` endpoint.
"""

import base64
from datetime import UTC, datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gvas.config import ResendSettings
from gvas.domain.customers import PortalLoginEmailRequest
from gvas.domain.enums import DeliveryStatus, RecipientAddressKind
from gvas.domain.messages import CustomerDeliveryRequest, DeliveryReceipt
from gvas.domain.owner_email import OwnerEmailRequest
from gvas.domain.reporting import ReportEmailRequest
from gvas.infrastructure.hosted_links import PORTAL_LOGIN_LINK_REFERENCE

EMAILS_PATH: Final = "/emails"
RECEIVED_EMAILS_PATH: Final = "/emails/receiving/"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ResendDeliveryError(RuntimeError):
    """Raised when a quote email attempt should be retried by the dispatcher."""


class ResendEmailResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str


class _ResendEmailClient:
    def __init__(self, settings: ResendSettings, client: httpx.AsyncClient) -> None:
        if not settings.is_configured:
            raise ResendDeliveryError("resend api key and from address are not configured")
        self._settings = settings
        self._client = client

    async def _send(
        self, to: str, subject: str, text: str, idempotency_key: str, **extra: object
    ) -> DeliveryReceipt:
        payload: dict[str, object] = {
            "from": self._settings.from_address,
            "to": [to],
            "subject": subject,
            "text": text,
            **extra,
        }
        if self._settings.reply_to_address and "reply_to" not in payload:
            payload["reply_to"] = self._settings.reply_to_address
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url}{EMAILS_PATH}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Idempotency-Key": idempotency_key,
                },
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise ResendDeliveryError("resend was unreachable") from error
        return self._receipt(response)

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


class ResendQuoteDeliveryAdapter(_ResendEmailClient):
    """Sends the approved quote body to the customer's email address."""

    async def deliver(self, request: CustomerDeliveryRequest) -> DeliveryReceipt:
        if request.recipient.address_kind is not RecipientAddressKind.EMAIL:
            raise ResendDeliveryError("resend delivers to email recipients only")
        if request.attachments:
            raise ResendDeliveryError("quote emails do not carry attachments")
        receipt = await self._send(
            request.recipient.address,
            request.subject or "Your quote",
            self._body(request),
            request.idempotency_key,
        )
        if request.quote_url is not None:
            receipt = receipt.model_copy(
                update={"customer_link": request.quote_url, "emailed": True}
            )
        return receipt

    def _body(self, request: CustomerDeliveryRequest) -> str:
        if request.quote_url is not None:
            sender = request.business_name or "Your provider"
            return "\n\n".join(
                [
                    f"{sender} sent you a quote.",
                    request.body_text,
                    f"View and pay your quote: {request.quote_url}",
                ]
            )
        links = [self._resolve_link(reference) for reference in request.links]
        if not links:
            return request.body_text
        return "\n\n".join([request.body_text, *links])

    def _resolve_link(self, reference: str) -> str:
        if reference == PORTAL_LOGIN_LINK_REFERENCE:
            return self._settings.portal_url
        raise ResendDeliveryError("quote carries an unknown hosted link reference")


class ResendPortalLoginEmailAdapter(_ResendEmailClient):
    """Sends the customer portal magic link. The link is the only copy of
    the raw token, so it appears in the e-mail body and nowhere else."""

    async def send_login_link(self, request: PortalLoginEmailRequest) -> DeliveryReceipt:
        return await self._send(
            request.to,
            request.subject,
            "\n\n".join(
                [
                    f"Sign in to your {request.business_display_name} account"
                    " with the link below. It works once and expires in 15 minutes.",
                    request.login_url,
                    "If you did not ask for this e-mail you can ignore it.",
                ]
            ),
            request.idempotency_key,
        )


class ResendReportEmailAdapter(_ResendEmailClient):
    """Emails a rendered report document to the address the owner typed."""

    async def deliver(self, request: ReportEmailRequest) -> DeliveryReceipt:
        return await self._send(
            request.recipient_address,
            request.subject,
            request.body_text,
            request.idempotency_key,
            attachments=[
                {
                    "filename": request.artifact.filename,
                    "content": base64.b64encode(request.artifact.content).decode("ascii"),
                    "content_type": request.artifact.media_type,
                }
            ],
        )


class ResendOwnerEmailAdapter(_ResendEmailClient):
    """Owner notices and owner-channel replies: text + HTML, the per-request
    reply address and thread headers when the request carries them."""

    async def send(self, request: OwnerEmailRequest) -> DeliveryReceipt:
        extra: dict[str, object] = {}
        if request.html:
            extra["html"] = request.html
        if request.reply_to:
            extra["reply_to"] = [request.reply_to]
        headers = request.headers()
        if headers:
            extra["headers"] = headers
        return await self._send(
            request.to, request.subject, request.text, request.idempotency_key, **extra
        )


class ResendReceivedAuthentication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None


class ResendReceivedEmail(BaseModel):
    """``GET /emails/receiving/{id}``; only the fields the adapter reads."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    id: str
    sender: str = Field(alias="from")
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] | None = None
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    message_id: str | None = None
    headers: dict[str, object] | None = None
    received_for: tuple[str, ...] | None = None
    authentication: ResendReceivedAuthentication | None = None
    created_at: datetime | None = None


class ResendReceivingClient:
    """Fetches a received e-mail's content; the webhook carries metadata only."""

    def __init__(self, settings: ResendSettings, client: httpx.AsyncClient) -> None:
        if not settings.is_configured:
            raise ResendDeliveryError("resend api key and from address are not configured")
        self._settings = settings
        self._client = client

    async def get(self, email_id: str) -> ResendReceivedEmail:
        try:
            response = await self._client.get(
                f"{self._settings.api_base_url}{RECEIVED_EMAILS_PATH}{email_id}",
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise ResendDeliveryError("resend was unreachable") from error
        if response.status_code >= 400:
            raise ResendDeliveryError(f"resend returned http {response.status_code}")
        try:
            return ResendReceivedEmail.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ResendDeliveryError("resend returned an unreadable received e-mail") from error
