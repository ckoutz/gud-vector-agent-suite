"""Owner notification e-mails and e-mailed owner replies.

Every owner notice e-mail — booking requests, escalations, portal service
requests, quote acceptances and payments — is rendered from one
``OwnerEmailContent`` into a plain-text part and a small inline-styled HTML
part, so they all read the same.

Booking-request notices carry a per-request reply address
``owner+<token>@<reply domain>``. The token names the request reference and
its request epoch and is signed (HMAC-SHA256, like the one-click decision
links): it is never a raw id, and a tampered or stale token is refused.
The same address doubles as a ``References`` thread anchor so a reply that
loses the address (forwarded, edited recipients) can still be matched.
"""

import hashlib
import hmac
import html
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gvas.domain.identifiers import BusinessId, IntakeConversationId

OWNER_EMAIL_SOURCE_NAMESPACE = "email"
OWNER_EMAIL_REPLY_COMMAND_TYPE = "owner_email.reply"
OWNER_EMAIL_REPLY_COMMAND_NAMESPACE = UUID("4d7a2c19-8e35-4b61-a0f8-6c3e9b1d5a27")
OWNER_REPLY_LOCAL_PART = "owner"
OWNER_EMAIL_REPLY_MAX_CHARS = 2000
OWNER_EMAIL_SUBJECT_MAX_CHARS = 78
OWNER_CHANNEL_FOOTER = "Reply in your owner channel to act on this."
OWNER_EMAIL_UNSUPPORTED_REPLY = (
    "By e-mail I can approve or decline booking requests and block time "
    "(`unavailable 8-12`). Use your owner channel for everything else."
)

_SIGNATURE_HEX_CHARS = 32
_TOKEN = re.compile(
    r"^(?P<reference>[0-9a-z]{4,32})-(?P<epoch>[0-9a-z]{1,13})-(?P<sig>[0-9a-f]{32})$"
)
_CODE = re.compile(r"`([^`]+)`")
_DETAIL_LINE = re.compile(r"^(?P<label>[A-Z][A-Za-z ]{0,29}):\s+(?P<value>\S.*)$")


class OwnerEmailModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OwnerEmailDetail(OwnerEmailModel):
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)


class OwnerEmailAction(OwnerEmailModel):
    """A button; only absolute http(s) links are rendered."""

    label: str = Field(min_length=1)
    url: str = Field(min_length=1)
    primary: bool = False

    @field_validator("url")
    @classmethod
    def _web_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("owner e-mail actions must be http(s) links")
        return value


class OwnerEmailContent(OwnerEmailModel):
    """The structured notice; ``commands`` are the owner-channel fallback
    commands shown in the muted footer (backticks render as code)."""

    heading: str = Field(min_length=1)
    intro: str | None = None
    details: tuple[OwnerEmailDetail, ...] = ()
    paragraphs: tuple[str, ...] = ()
    actions: tuple[OwnerEmailAction, ...] = ()
    action_note: str | None = None
    commands: tuple[str, ...] = ()
    footer: str | None = None
    business_name: str | None = None
    subject: str | None = None

    def with_actions(
        self, actions: tuple[OwnerEmailAction, ...], note: str | None
    ) -> "OwnerEmailContent":
        if not actions:
            return self
        return self.model_copy(update={"actions": actions, "action_note": note})

    def with_footer(self, footer: str | None) -> "OwnerEmailContent":
        return self.model_copy(update={"footer": footer})

    def email_subject(self) -> str:
        subject = (self.subject or self.heading)[:OWNER_EMAIL_SUBJECT_MAX_CHARS]
        return f"[{self.business_name}] {subject}" if self.business_name else subject


class OwnerEmailThread(OwnerEmailModel):
    """Reply routing for one request: ``reply_to`` receives the owner's reply
    and ``anchor`` is the stable thread id carried in ``References``."""

    reply_to: str = Field(min_length=3)
    anchor: str = Field(min_length=3)


def owner_notice_content(
    text: str, *, business_name: str | None = None, subject: str | None = None
) -> OwnerEmailContent:
    """Any channel notice text as structured e-mail content.

    The first line's ``Heading — rest`` split gives the heading and intro;
    ``Label: value`` lines become details; lines with backticked commands go
    to the muted footer; the rest stay paragraphs.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = lines[0] if lines else "Notice"
    heading, dash, intro = first.partition(" — ")
    details: list[OwnerEmailDetail] = []
    paragraphs: list[str] = []
    commands: list[str] = []
    for line in lines[1:]:
        detail = _DETAIL_LINE.match(line)
        if "`" in line:
            commands.append(line)
        elif detail is not None:
            details.append(
                OwnerEmailDetail(
                    label=detail.group("label"), value=detail.group("value").rstrip(".")
                )
            )
        else:
            paragraphs.append(line)
    return OwnerEmailContent(
        heading=heading.strip() or first,
        intro=intro.strip() if dash and intro.strip() else None,
        details=tuple(details),
        paragraphs=tuple(paragraphs),
        commands=tuple(commands),
        business_name=business_name,
        subject=subject or first,
    )


def render_owner_email_text(content: OwnerEmailContent) -> str:
    blocks = [content.heading]
    if content.intro:
        blocks.append(content.intro)
    if content.details:
        blocks.append("\n".join(f"{item.label}: {item.value}" for item in content.details))
    blocks.extend(content.paragraphs)
    if content.actions:
        note = f"{content.action_note}\n" if content.action_note else ""
        blocks.append(note + "\n".join(f"{item.label}: {item.url}" for item in content.actions))
    if content.footer:
        blocks.append(content.footer)
    if content.commands:
        blocks.append("\n".join(content.commands))
    return "\n\n".join(blocks)


_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif,"
    "'Apple Color Emoji','Segoe UI Emoji'"
)
_BUTTON = (
    "display:inline-block;padding:10px 22px;margin:0 8px 8px 0;border-radius:6px;"
    "font-weight:600;font-size:15px;text-decoration:none;"
)
_PRIMARY = "background:#14532d;color:#ffffff;border:1px solid #14532d;"
_SECONDARY = "background:#ffffff;color:#7f1d1d;border:1px solid #b91c1c;"


def _inline(text: str) -> str:
    """Escaped text with ``code`` spans; nothing else is interpreted."""

    escaped = html.escape(text, quote=True)
    return _CODE.sub(
        lambda match: (
            '<code style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            'font-size:13px;background:#f3f4f6;padding:1px 4px;border-radius:4px;">'
            f"{match.group(1)}</code>"
        ),
        escaped,
    )


def render_owner_email_html(content: OwnerEmailContent) -> str:
    """Self-contained HTML: inline styles, system font, no images or links
    other than the action buttons."""

    parts = [
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(content.heading)}</title></head>"
        f'<body style="margin:0;padding:24px 12px;background:#f6f7f9;font-family:{_FONT};'
        'color:#111827;">'
        '<div style="max-width:34rem;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:10px;padding:24px 24px 16px;">'
    ]
    if content.business_name:
        parts.append(
            '<p style="margin:0 0 6px;font-size:12px;letter-spacing:.04em;text-transform:uppercase;'
            f'color:#6b7280;">{html.escape(content.business_name)}</p>'
        )
    parts.append(
        '<h1 style="margin:0 0 12px;font-size:20px;line-height:1.3;font-weight:650;">'
        f"{_inline(content.heading)}</h1>"
    )
    if content.intro:
        parts.append(
            '<p style="margin:0 0 16px;font-size:15px;line-height:1.5;">'
            f"{_inline(content.intro)}</p>"
        )
    if content.details:
        parts.append(
            '<table role="presentation" style="width:100%;border-collapse:collapse;'
            'margin:0 0 16px;font-size:15px;line-height:1.45;">'
        )
        for item in content.details:
            parts.append(
                "<tr>"
                '<td style="padding:6px 12px 6px 0;color:#6b7280;vertical-align:top;'
                f'white-space:nowrap;width:1%;">{html.escape(item.label)}</td>'
                '<td style="padding:6px 0;vertical-align:top;">'
                f"{_inline(item.value)}</td></tr>"
            )
        parts.append("</table>")
    for paragraph in content.paragraphs:
        parts.append(
            f'<p style="margin:0 0 14px;font-size:15px;line-height:1.5;">{_inline(paragraph)}</p>'
        )
    if content.actions:
        parts.append('<div style="margin:8px 0 6px;">')
        for action in content.actions:
            style = _BUTTON + (_PRIMARY if action.primary else _SECONDARY)
            parts.append(
                f'<a href="{html.escape(action.url, quote=True)}" style="{style}">'
                f"{html.escape(action.label)}</a>"
            )
        parts.append("</div>")
        if content.action_note:
            parts.append(
                '<p style="margin:0 0 14px;font-size:13px;color:#6b7280;">'
                f"{_inline(content.action_note)}</p>"
            )
    if content.footer or content.commands:
        parts.append(
            '<div style="margin-top:18px;padding-top:12px;border-top:1px solid #e5e7eb;'
            'font-size:12.5px;line-height:1.55;color:#6b7280;">'
        )
        if content.footer:
            parts.append(f'<p style="margin:0 0 6px;">{_inline(content.footer)}</p>')
        for command in content.commands:
            parts.append(f'<p style="margin:0 0 6px;">{_inline(command)}</p>')
        parts.append("</div>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    out = ""
    while value:
        value, remainder = divmod(value, 36)
        out = digits[remainder] + out
    return out


def _reply_signature(
    secret: str,
    *,
    business_id: BusinessId,
    conversation_id: IntakeConversationId,
    reference: str,
    request_epoch: int,
) -> str:
    body = f"owner-reply|{business_id}|{conversation_id}|{reference}|{request_epoch}"
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return digest[:_SIGNATURE_HEX_CHARS]


def owner_reply_token(
    secret: str,
    *,
    business_id: BusinessId,
    conversation_id: IntakeConversationId,
    reference: str,
    request_epoch: int,
) -> str:
    """``<reference>-<epoch base36>-<128-bit signature>``: lowercase so it
    survives mailbox case folding, short enough for an address local part."""

    signature = _reply_signature(
        secret,
        business_id=business_id,
        conversation_id=conversation_id,
        reference=reference,
        request_epoch=request_epoch,
    )
    return f"{reference.lower()}-{_base36(request_epoch)}-{signature}"


class OwnerReplyToken(OwnerEmailModel):
    reference: str
    request_epoch: int
    signature: str

    def verifies(
        self, secret: str, *, business_id: BusinessId, conversation_id: IntakeConversationId
    ) -> bool:
        expected = _reply_signature(
            secret,
            business_id=business_id,
            conversation_id=conversation_id,
            reference=self.reference,
            request_epoch=self.request_epoch,
        )
        return hmac.compare_digest(expected, self.signature)


def parse_owner_reply_token(token: str) -> OwnerReplyToken | None:
    match = _TOKEN.match(token.strip().lower())
    if match is None:
        return None
    return OwnerReplyToken(
        reference=match.group("reference"),
        request_epoch=int(match.group("epoch"), 36),
        signature=match.group("sig"),
    )


def owner_reply_thread(token: str, domain: str) -> OwnerEmailThread:
    address = f"{OWNER_REPLY_LOCAL_PART}+{token}@{domain.lower()}"
    return OwnerEmailThread(reply_to=address, anchor=f"<{address}>")


def owner_reply_tokens(values: Iterable[str], domain: str) -> tuple[OwnerReplyToken, ...]:
    """Every well-formed token addressed at ``domain`` in ``values``
    (recipient addresses or ``In-Reply-To``/``References`` ids), in order."""

    pattern = re.compile(
        rf"{OWNER_REPLY_LOCAL_PART}\+([0-9a-z]+-[0-9a-z]+-[0-9a-f]+)@{re.escape(domain.lower())}\b"
    )
    tokens: list[OwnerReplyToken] = []
    for value in values:
        for match in pattern.finditer(value.lower()):
            token = parse_owner_reply_token(match.group(1))
            if token is not None and token not in tokens:
                tokens.append(token)
    return tuple(tokens)


_QUOTE_BOUNDARY = re.compile(
    r"^(?:-{2,}\s*(?:original message|forwarded message)\s*-{2,}"
    r"|_{5,}"
    r"|from:\s.+"
    r"|sent from my .+"
    r"|get outlook for .+"
    r"|--\s?)$",
    re.IGNORECASE,
)
_ON_WROTE = re.compile(r"^on\s.+", re.IGNORECASE)


def strip_quoted_reply(text: str) -> str:
    """The owner's own words: everything above the quoted original, the
    ``On … wrote:`` attribution (also when wrapped onto two lines), a
    ``--`` signature delimiter or a mobile sign-off."""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">") or _QUOTE_BOUNDARY.match(stripped):
            break
        if _ON_WROTE.match(stripped):
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if stripped.endswith("wrote:") or following.endswith("wrote:"):
                break
        kept.append(line.rstrip())
    return "\n".join(kept).strip()[:OWNER_EMAIL_REPLY_MAX_CHARS]


class InboundOwnerEmail(OwnerEmailModel):
    """A received e-mail, already verified and fetched by the provider
    adapter. ``event_source``/``event_id`` key the replay ledger;
    ``sender_authenticated`` is the provider's SPF/DKIM verdict."""

    event_source: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    email_id: str = Field(min_length=1)
    sender: str = Field(min_length=3)
    sender_authenticated: bool
    recipients: tuple[str, ...] = ()
    subject: str = ""
    message_id: str | None = None
    references: tuple[str, ...] = ()
    text: str = ""
    received_at: datetime

    @field_validator("sender")
    @classmethod
    def _bare_lower(cls, value: str) -> str:
        return value.strip().lower()


class OwnerEmailRequest(OwnerEmailModel):
    """One owner e-mail send: multipart text+HTML with optional reply
    routing and thread headers."""

    business_id: BusinessId
    to: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    text: str = Field(min_length=1)
    html: str | None = None
    reply_to: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    idempotency_key: str = Field(min_length=1)

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.in_reply_to:
            headers["In-Reply-To"] = self.in_reply_to
        if self.references:
            headers["References"] = " ".join(self.references)
        return headers


def reply_subject(subject: str) -> str:
    subject = subject.strip() or "Your reply"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def message_ids(values: Sequence[str]) -> tuple[str, ...]:
    """``<id>`` tokens from ``In-Reply-To``/``References`` header values."""

    found: list[str] = []
    for value in values:
        for match in re.finditer(r"<[^<>\s]+>", value):
            if match.group(0) not in found:
                found.append(match.group(0))
    return tuple(found)


__all__ = [
    "OWNER_CHANNEL_FOOTER",
    "OWNER_EMAIL_REPLY_COMMAND_NAMESPACE",
    "OWNER_EMAIL_REPLY_COMMAND_TYPE",
    "OWNER_EMAIL_REPLY_MAX_CHARS",
    "OWNER_EMAIL_SOURCE_NAMESPACE",
    "OWNER_EMAIL_SUBJECT_MAX_CHARS",
    "InboundOwnerEmail",
    "OwnerEmailAction",
    "OwnerEmailContent",
    "OwnerEmailDetail",
    "OwnerEmailRequest",
    "OwnerEmailThread",
    "OwnerReplyToken",
    "message_ids",
    "owner_notice_content",
    "owner_reply_thread",
    "owner_reply_token",
    "owner_reply_tokens",
    "parse_owner_reply_token",
    "render_owner_email_html",
    "render_owner_email_text",
    "reply_subject",
    "strip_quoted_reply",
]
