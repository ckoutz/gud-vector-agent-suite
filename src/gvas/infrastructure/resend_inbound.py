"""Resend inbound e-mail (``email.received``) webhook adapter.

Verifies the Svix signature over the raw body, fetches the e-mail content
(the event carries metadata only), normalizes it into ``InboundOwnerEmail``
and hands it to the owner e-mail reply service. Everything after the
handoff — sender authorization, the reply token, first-writer-wins on the
``svix-id`` — is the application's.
"""

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.domain.owner_email import InboundOwnerEmail, message_ids
from gvas.infrastructure.resend import ResendReceivedEmail

RESEND_EVENT_SOURCE: Final = "resend"
EMAIL_RECEIVED_EVENT: Final = "email.received"
SVIX_ID_HEADER: Final = "svix-id"
SVIX_TIMESTAMP_HEADER: Final = "svix-timestamp"
SVIX_SIGNATURE_HEADER: Final = "svix-signature"
_WHSEC: Final = "whsec_"
_TAG = re.compile(r"<[^>]+>")
_BLOCK_TAG = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h\d)\b[^>]*>", re.IGNORECASE)
_QUOTE_START = re.compile(
    r"<blockquote\b|<div\b[^>]*\bclass=\"[^\"]*\bgmail_quote\b"
    r"|<div\b[^>]*\bid=\"(?:divRplyFwdMsg|appendonsend)\"",
    re.IGNORECASE,
)
_HIDDEN = re.compile(r"<(script|style|head)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)


class ResendSignatureError(RuntimeError):
    """Missing, stale or forged webhook signature; answered 401."""


class ResendInboundPayloadError(RuntimeError):
    """Verified but unreadable event; answered 400."""


class ReceivedEmailSource(Protocol):
    async def get(self, email_id: str) -> ResendReceivedEmail: ...


class OwnerEmailReceiver(Protocol):
    async def receive(self, email: InboundOwnerEmail) -> object: ...


class SvixWebhookVerifier:
    def __init__(
        self,
        secret: str,
        *,
        tolerance_seconds: int = 300,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        raw = secret.strip()
        if raw.startswith(_WHSEC):
            raw = raw[len(_WHSEC) :]
        try:
            self._key = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("resend webhook secret is not a whsec_ base64 secret") from error
        if not self._key:
            raise ValueError("resend webhook secret is empty")
        self._tolerance = tolerance_seconds
        self._clock = clock

    def verify(self, body: bytes, headers: Mapping[str, str]) -> str:
        """Returns the ``svix-id`` of a genuine, fresh delivery."""

        message_id = headers.get(SVIX_ID_HEADER)
        timestamp = headers.get(SVIX_TIMESTAMP_HEADER)
        signatures = headers.get(SVIX_SIGNATURE_HEADER)
        if not message_id or not timestamp or not signatures:
            raise ResendSignatureError("missing svix headers")
        try:
            sent_at = int(timestamp)
        except ValueError as error:
            raise ResendSignatureError("malformed svix timestamp") from error
        if abs(self._clock().timestamp() - sent_at) > self._tolerance:
            raise ResendSignatureError("svix timestamp outside tolerance")
        signed = message_id.encode() + b"." + timestamp.encode() + b"." + body
        expected = base64.b64encode(hmac.new(self._key, signed, hashlib.sha256).digest())
        for candidate in signatures.split():
            version, _, value = candidate.partition(",")
            if version == "v1" and hmac.compare_digest(value.encode(), expected):
                return message_id
        raise ResendSignatureError("svix signature mismatch")


class _EventData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    email_id: str


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str
    data: _EventData | None = None


class ResendReceivingIngress:
    def __init__(
        self,
        verifier: SvixWebhookVerifier,
        emails: ReceivedEmailSource,
        receiver: OwnerEmailReceiver,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._verifier = verifier
        self._emails = emails
        self._receiver = receiver
        self._clock = clock

    async def handle(self, *, body: bytes, headers: Mapping[str, str]) -> str:
        event_id = self._verifier.verify(body, headers)
        try:
            event = _Event.model_validate(json.loads(body))
        except (ValueError, ValidationError) as error:
            raise ResendInboundPayloadError("unreadable resend event") from error
        if event.type != EMAIL_RECEIVED_EVENT:
            return "ignored"
        if event.data is None:
            raise ResendInboundPayloadError("email.received event without data")
        received = await self._emails.get(event.data.email_id)
        email = normalize_received_email(received, event_id=event_id, now=self._clock())
        if email is None:
            return "ignored"
        status = await self._receiver.receive(email)
        return str(getattr(status, "value", status))


def normalize_received_email(
    received: ResendReceivedEmail, *, event_id: str, now: datetime
) -> InboundOwnerEmail | None:
    _, sender = parseaddr(received.sender)
    if "@" not in sender:
        return None
    headers = {key.lower(): value for key, value in (received.headers or {}).items()}
    thread = [
        value
        for name in ("in-reply-to", "references")
        for value in _header_values(headers.get(name))
    ]
    recipients = [
        address
        for _, address in getaddresses(
            [*received.to, *(received.cc or ()), *(received.received_for or ())]
        )
        if address
    ]
    return InboundOwnerEmail(
        event_source=RESEND_EVENT_SOURCE,
        event_id=event_id,
        email_id=received.id,
        sender=sender,
        sender_authenticated=_authenticated(received),
        recipients=tuple(recipients),
        subject=received.subject or "",
        message_id=received.message_id,
        references=message_ids(thread),
        text=received.text or _html_text(received.html or ""),
        received_at=received.created_at or now,
    )


def _header_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _authenticated(received: ResendReceivedEmail) -> bool:
    """The receiving server's verdict (not forgeable headers). Only a DMARC
    pass proves SPF or DKIM aligned with the From domain; a bare SPF/DKIM
    pass may be for any domain."""

    result = received.authentication
    return result is not None and result.dmarc == "pass"


def _html_text(markup: str) -> str:
    quote = _QUOTE_START.search(markup)
    text = _HIDDEN.sub("", markup if quote is None else markup[: quote.start()])
    text = _BLOCK_TAG.sub("\n", text)
    return html.unescape(_TAG.sub("", text))
