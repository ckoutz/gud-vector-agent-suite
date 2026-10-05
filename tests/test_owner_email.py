"""Owner notice e-mail rendering, reply tokens and inbound normalization."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from gvas.domain.identifiers import BusinessId, IntakeConversationId
from gvas.domain.owner_email import (
    OwnerEmailAction,
    OwnerEmailContent,
    OwnerEmailDetail,
    OwnerEmailRequest,
    owner_notice_content,
    owner_reply_thread,
    owner_reply_token,
    owner_reply_tokens,
    parse_owner_reply_token,
    render_owner_email_html,
    render_owner_email_text,
    reply_subject,
    strip_quoted_reply,
)
from gvas.infrastructure.resend import ResendReceivedEmail
from gvas.infrastructure.resend_inbound import (
    ResendSignatureError,
    SvixWebhookVerifier,
    normalize_received_email,
)

REPLY_KEY = "reply-signing-key"
DOMAIN = "reply.example.test"
WEBHOOK_KEY = b"resend-webhook-test-key"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(WEBHOOK_KEY).decode()
NOW = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)


def sample_content() -> OwnerEmailContent:
    return OwnerEmailContent(
        heading="New booking request #65e7b537",
        intro="Jane <Doe> asked for a visit.",
        details=(
            OwnerEmailDetail(label="Customer", value="Jane <Doe> & Co"),
            OwnerEmailDetail(label="Requested time", value="Tue Oct 6, 9:00 AM (America/Denver)"),
        ),
        actions=(
            OwnerEmailAction(label="Approve", url="https://gvas.example/a?t=1&x=2", primary=True),
            OwnerEmailAction(label="Decline", url="https://gvas.example/d"),
        ),
        commands=("Or reply `approve booking 65e7b537` / `decline booking 65e7b537 <reason>`.",),
        business_name="Güd Vector",
    )


def test_html_is_self_contained_escaped_and_has_both_buttons() -> None:
    html = render_owner_email_html(sample_content())
    assert "max-width:34rem" in html
    assert "<img" not in html.lower()
    assert "<link" not in html.lower() and "<style" not in html.lower()
    assert "-apple-system" in html
    assert "Jane &lt;Doe&gt; &amp; Co" in html
    assert "<Doe>" not in html
    assert 'href="https://gvas.example/a?t=1&amp;x=2"' in html
    assert ">Approve</a>" in html and ">Decline</a>" in html
    assert "approve booking 65e7b537" in html


def test_text_part_keeps_details_links_and_commands() -> None:
    text = render_owner_email_text(sample_content())
    assert text.startswith("New booking request #65e7b537")
    assert "Customer: Jane <Doe> & Co" in text
    assert "Approve: https://gvas.example/a?t=1&x=2" in text
    assert "`approve booking 65e7b537`" in text


def test_action_urls_must_be_web_links() -> None:
    with pytest.raises(ValueError):
        OwnerEmailAction(label="Approve", url="javascript:alert(1)")


def test_channel_notice_text_becomes_structured_content() -> None:
    content = owner_notice_content(
        "Customer needs a person — Jane Doe asked for a human.\n"
        "Phone: +15555550100\n"
        "Reply `approve booking abcd1234` to act."
    )
    assert content.heading == "Customer needs a person"
    assert content.intro == "Jane Doe asked for a human."
    assert content.details == (OwnerEmailDetail(label="Phone", value="+15555550100"),)
    assert content.commands


def test_reply_token_round_trips_and_binds_the_request() -> None:
    business_id, conversation_id = BusinessId(uuid4()), IntakeConversationId(uuid4())
    epoch = int(NOW.timestamp())
    token = owner_reply_token(
        REPLY_KEY,
        business_id=business_id,
        conversation_id=conversation_id,
        reference="65e7b537",
        request_epoch=epoch,
    )
    assert str(business_id) not in token and str(conversation_id) not in token
    thread = owner_reply_thread(token, DOMAIN)
    assert thread.reply_to == f"owner+{token}@{DOMAIN}"
    parsed = parse_owner_reply_token(token)
    assert parsed is not None and parsed.request_epoch == epoch
    assert parsed.verifies(REPLY_KEY, business_id=business_id, conversation_id=conversation_id)
    assert not parsed.verifies("other", business_id=business_id, conversation_id=conversation_id)
    assert not parsed.verifies(
        REPLY_KEY, business_id=BusinessId(uuid4()), conversation_id=conversation_id
    )
    found = owner_reply_tokens([f"Owner <{thread.reply_to.upper()}>", thread.anchor], DOMAIN)
    assert found == (parsed,)
    assert owner_reply_tokens([f"owner+{token}@evil.example"], DOMAIN) == ()


def test_quoted_original_and_signatures_are_stripped() -> None:
    body = (
        "Can't make it, out of town that week\n\n"
        "On Mon, Oct 5, 2026 at 2:52 PM Güd Vector <quotes@gudvector.com>\nwrote:\n"
        "> New booking request #65e7b537\n"
    )
    assert strip_quoted_reply(body) == "Can't make it, out of town that week"
    assert strip_quoted_reply("yes\n--\nCameron\nGüd Vector") == "yes"
    assert strip_quoted_reply("ok\n\nSent from my iPhone") == "ok"
    assert reply_subject("New booking request") == "Re: New booking request"
    assert reply_subject("RE: x") == "RE: x"


def test_owner_email_request_carries_thread_headers() -> None:
    request = OwnerEmailRequest(
        business_id=BusinessId(uuid4()),
        to="owner@example.com",
        subject="Re: x",
        text="Approved.",
        in_reply_to="<a@x>",
        references=("<t@reply>", "<a@x>"),
        idempotency_key="k",
    )
    assert request.headers() == {"In-Reply-To": "<a@x>", "References": "<t@reply> <a@x>"}


def signed(body: bytes, message_id: str, at: datetime, key: bytes = WEBHOOK_KEY) -> dict[str, str]:
    timestamp = str(int(at.timestamp()))
    digest = hmac.new(key, f"{message_id}.{timestamp}.".encode() + body, hashlib.sha256)
    return {
        "svix-id": message_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,bogus v1,{base64.b64encode(digest.digest()).decode()}",
    }


def test_svix_signature_is_verified_with_freshness() -> None:
    verifier = SvixWebhookVerifier(WEBHOOK_SECRET, clock=lambda: NOW)
    body = json.dumps({"type": "email.received"}).encode()
    assert verifier.verify(body, signed(body, "msg_1", NOW)) == "msg_1"
    with pytest.raises(ResendSignatureError):
        verifier.verify(body + b" ", signed(body, "msg_1", NOW))
    with pytest.raises(ResendSignatureError):
        verifier.verify(body, signed(body, "msg_1", NOW - timedelta(minutes=10)))
    with pytest.raises(ResendSignatureError):
        verifier.verify(body, signed(body, "msg_1", NOW, key=b"other"))
    with pytest.raises(ResendSignatureError):
        verifier.verify(body, {})


def test_received_email_is_normalized_with_authentication_verdict() -> None:
    received = ResendReceivedEmail.model_validate(
        {
            "id": "rcv-1",
            "from": "Cameron <Info@GudVector.com>",
            "to": ["owner+abcd-1-00@reply.example.test"],
            "subject": "Re: New booking request",
            "text": None,
            "html": "<div>Yes&nbsp;please</div><blockquote>old</blockquote>",
            "message_id": "<m1@mail>",
            "headers": {"In-Reply-To": "<r@resend>", "References": "<t@reply> <r@resend>"},
            "authentication": {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
        }
    )
    email = normalize_received_email(received, event_id="msg_1", now=NOW)
    assert email is not None
    assert email.sender == "info@gudvector.com"
    assert email.sender_authenticated
    assert email.references == ("<r@resend>", "<t@reply>")
    assert email.text.startswith("Yes")
    spoofed = received.model_copy(
        update={"authentication": received.authentication.model_copy(update={"dmarc": "fail"})}
        if received.authentication
        else {}
    )
    spoofed_email = normalize_received_email(spoofed, event_id="msg_2", now=NOW)
    assert spoofed_email is not None and not spoofed_email.sender_authenticated
