"""Public, customer-facing routes: claim-token quotes, booking links, and the
card-payment webhook.

Claim tokens are the only credential on these routes: every response is shaped
by the application service and contains no internal ids, no owner contact
details, and nothing about other customers. Errors are generic on purpose —
an unknown or expired token and a real miss are the same 404.

Rate limiting is a per-client-IP token bucket held in process; behind a proxy
the client ip is the rightmost ``X-Forwarded-For`` value when present.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, params
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gvas.application.intake import (
    IntakeAuthenticationError,
    IntakeClosedError,
    IntakeError,
    IntakeLimitError,
    IntakeNotFoundError,
    IntakeReply,
    IntakeService,
    IntakeStart,
)
from gvas.application.public_quotes import (
    OpenCheckoutUnavailableError,
    PublicQuoteService,
    QuoteNotFoundError,
    UnknownPaymentSessionError,
)
from gvas.domain.identifiers import IntakeConversationId
from gvas.domain.intake import (
    INTAKE_MESSAGE_MAX_CHARS,
    IntakeConversation,
    IntakeMessage,
    IntakeState,
)
from gvas.domain.payments import PaymentCheckoutError
from gvas.domain.quotes import InvalidQuoteTransitionError
from gvas.infrastructure.stripe.events import StripeEventError, parse_checkout_event
from gvas.infrastructure.stripe.signature import (
    SIGNATURE_HEADER,
    StripeSignatureError,
    StripeWebhookVerifier,
)

logger = logging.getLogger(__name__)

GENERIC_NOT_FOUND = "not found"
GENERIC_UNAUTHORIZED = "unauthorized"


class IntakeMessageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=INTAKE_MESSAGE_MAX_CHARS)


def _slot_payloads(
    conversation: IntakeConversation,
) -> list[dict[str, str]] | None:
    if conversation.state is not IntakeState.PROPOSING_SLOTS:
        return None
    return [
        {"start": slot.start.isoformat(), "end": slot.end.isoformat()}
        for slot in conversation.proposed_slots
    ]


def intake_start_payload(start: IntakeStart) -> dict[str, object]:
    """The create-conversation contract the chat widget was built against."""

    return {
        "conversationId": str(start.conversation.conversation_id),
        "conversationToken": start.token,
        "state": start.conversation.state.value,
        "reply": start.reply,
        "slots": None,
    }


def intake_reply_payload(result: IntakeReply) -> dict[str, object]:
    conversation = result.conversation
    return {
        "state": conversation.state.value,
        "reply": result.reply,
        "slots": _slot_payloads(conversation),
        "summary": conversation.collected.summary(),
    }


def intake_view_payload(
    conversation: IntakeConversation, messages: tuple[IntakeMessage, ...]
) -> dict[str, object]:
    return {
        "state": conversation.state.value,
        "messages": [
            {
                "role": message.role.value,
                "content": message.content,
                "createdAt": message.created_at.isoformat(),
            }
            for message in messages
        ],
        "slots": _slot_payloads(conversation),
        "summary": conversation.collected.summary(),
    }


class PerIpRateLimiter:
    """Token bucket per client ip: ``burst`` tokens refill at ``per_second``.

    Entries idle for longer than the burst window are dropped so the table
    stays bounded.
    """

    def __init__(
        self,
        per_minute: float,
        *,
        burst: int | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_second = per_minute / 60.0
        self._burst = float(burst if burst is not None else per_minute)
        self._now = now
        # ip -> (tokens, last refill monotonic)
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = self._now()
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._per_second)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        self._evict(now)
        return True

    def _evict(self, now: float) -> None:
        if len(self._buckets) <= 10_000:
            return
        idle_for = self._burst / self._per_second if self._per_second else 60.0
        stale = [key for key, (_, last) in self._buckets.items() if now - last > idle_for]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) > 10_000:
            self._buckets.clear()


def client_ip(request: Request) -> str:
    """The rightmost ``X-Forwarded-For`` hop: the entry the edge in front of
    us appended. The leftmost is caller-controlled and would let a client
    rotate the header to dodge the bucket."""

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


class SiteOriginCorsMiddleware:
    """Answers CORS for the business site origins configured in the database,
    plus any static extras (preview deployments). GET, POST and DELETE only.

    The allowed set is re-read at most once per ``ttl_seconds`` so configuring
    a business takes effect without a redeploy.
    """

    def __init__(
        self,
        app: ASGIApp,
        origins: Callable[[], Awaitable[frozenset[str]]],
        *,
        ttl_seconds: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._origins = origins
        self._ttl = ttl_seconds
        self._now = now
        self._cached: frozenset[str] = frozenset()
        # None, not 0.0: monotonic time starts near zero on fresh hosts, so a
        # zeroed stamp would read as "still fresh" and serve an empty set.
        self._cached_at: float | None = None

    async def _allowed(self) -> frozenset[str]:
        if self._cached_at is None or self._now() - self._cached_at >= self._ttl:
            self._cached = await self._origins()
            self._cached_at = self._now()
        return self._cached

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1")
        if not origin:
            await self.app(scope, receive, send)
            return
        allowed = await self._allowed()
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            if origin not in allowed:
                response: Response = PlainTextResponse("Disallowed CORS origin", status_code=400)
                await response(scope, receive, send)
                return
            allow_headers = headers.get(b"access-control-request-headers", b"").decode("latin-1")
            response_headers = {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, DELETE",
                "Vary": "Origin",
            }
            if allow_headers:
                response_headers["Access-Control-Allow-Headers"] = allow_headers
            response = PlainTextResponse("OK", status_code=200, headers=response_headers)
            await response(scope, receive, send)
            return
        if origin not in allowed:
            await self.app(scope, receive, send)
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_mut = MutableHeaders(raw=message.setdefault("headers", []))
                headers_mut["Access-Control-Allow-Origin"] = origin
                headers_mut["Vary"] = "Origin"
            await send(message)

        await self.app(scope, receive, send_with_cors)


def create_public_router(
    service: PublicQuoteService,
    *,
    webhook_verifier: StripeWebhookVerifier | None = None,
    rate_limiter: PerIpRateLimiter | None = None,
    intake: IntakeService | None = None,
) -> APIRouter:
    """The customer surface: quote view/accept/decline, booking link, webhook,
    and — when an intake service is mounted — the website booking chat."""

    router = APIRouter()
    limiter = rate_limiter or PerIpRateLimiter(per_minute=120, burst=30)

    async def rate_limit(request: Request) -> None:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, detail="rate limited")

    limited = [Depends(rate_limit)]

    @router.get("/v1/quotes/{claim_token}", dependencies=limited)
    async def fetch_quote(claim_token: str) -> JSONResponse:
        try:
            view = await service.fetch_quote(claim_token)
        except QuoteNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        return JSONResponse(view.as_payload(), status_code=200)

    @router.post("/v1/quotes/{claim_token}/accept", dependencies=limited)
    async def accept_quote(claim_token: str) -> JSONResponse:
        try:
            checkout_url = await service.accept_quote(claim_token)
        except QuoteNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        except InvalidQuoteTransitionError:
            return JSONResponse({"detail": "conflict"}, status_code=409)
        except OpenCheckoutUnavailableError:
            return JSONResponse({"detail": "checkout unavailable"}, status_code=503)
        except PaymentCheckoutError:
            logger.warning("checkout provider failed for an accept")
            return JSONResponse({"detail": "payment provider unavailable"}, status_code=502)
        return JSONResponse({"checkoutUrl": checkout_url}, status_code=200)

    @router.post("/v1/quotes/{claim_token}/decline", dependencies=limited)
    async def decline_quote(claim_token: str) -> JSONResponse:
        try:
            status = await service.decline_quote(claim_token)
        except QuoteNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        except InvalidQuoteTransitionError:
            return JSONResponse({"detail": "conflict"}, status_code=409)
        return JSONResponse({"status": status}, status_code=200)

    @router.get("/v1/businesses/{public_key}/booking-link", dependencies=limited)
    async def booking_link(public_key: str) -> JSONResponse:
        try:
            payload = await service.booking_link(public_key)
        except QuoteNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        return JSONResponse(payload, status_code=200)

    if intake is not None:
        _mount_intake(router, intake, limited)

    @router.post("/webhooks/stripe")
    async def stripe_webhook(request: Request) -> JSONResponse:
        if webhook_verifier is None:
            return JSONResponse({"detail": "webhooks are not configured"}, status_code=503)
        body = await request.body()
        try:
            webhook_verifier.verify(body, request.headers.get(SIGNATURE_HEADER))
        except StripeSignatureError:
            return JSONResponse({"status": "invalid_signature"}, status_code=401)
        try:
            event = parse_checkout_event(body)
        except StripeEventError:
            return JSONResponse({"status": "invalid_payload"}, status_code=400)
        try:
            recorded = await service.record_payment_event(event)
        except UnknownPaymentSessionError:
            return JSONResponse({"status": "retry later"}, status_code=503)
        return JSONResponse({"status": "recorded" if recorded else "ignored"}, status_code=200)

    return router


def _intake_conversation_id(raw: str) -> IntakeConversationId:
    try:
        return IntakeConversationId(UUID(raw))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=GENERIC_NOT_FOUND) from error


def _intake_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail=GENERIC_UNAUTHORIZED)
    return token.strip()


async def _intake_authenticate(
    service: IntakeService, request: Request, conversation_id: IntakeConversationId
) -> IntakeConversation:
    try:
        token = _intake_bearer_token(request)
        return await service.authenticate(conversation_id, token)
    except IntakeAuthenticationError as error:
        raise HTTPException(status_code=401, detail=GENERIC_UNAUTHORIZED) from error


def _mount_intake(router: APIRouter, intake: IntakeService, limited: list[params.Depends]) -> None:
    """The website chat: create a conversation, post a message, poll the view.

    The ``conversationToken`` is the only credential — it is hashed at rest
    and expires in 24h, and no route leaks it or the business id.
    """

    @router.post(
        "/v1/businesses/{public_key}/intake/conversations",
        dependencies=limited,
        status_code=201,
    )
    async def create_intake_conversation(public_key: str) -> JSONResponse:
        try:
            start = await intake.start_conversation(public_key)
        except IntakeNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        except IntakeLimitError:
            return JSONResponse({"detail": "rate limited"}, status_code=429)
        return JSONResponse(intake_start_payload(start), status_code=201)

    @router.post("/v1/intake/conversations/{conversation_id}/messages", dependencies=limited)
    async def post_intake_message(
        conversation_id: str, body: IntakeMessageBody, request: Request
    ) -> JSONResponse:
        resolved = _intake_conversation_id(conversation_id)
        conversation = await _intake_authenticate(intake, request, resolved)
        if not body.message.strip():
            return JSONResponse({"detail": "message is required"}, status_code=422)
        try:
            result = await intake.post_message(conversation, body.message)
        except IntakeClosedError:
            return JSONResponse({"detail": "conversation closed"}, status_code=409)
        except IntakeNotFoundError:
            return JSONResponse({"detail": GENERIC_NOT_FOUND}, status_code=404)
        except IntakeError:
            return JSONResponse({"detail": "invalid message"}, status_code=422)
        return JSONResponse(intake_reply_payload(result), status_code=200)

    @router.get("/v1/intake/conversations/{conversation_id}", dependencies=limited)
    async def fetch_intake_conversation(conversation_id: str, request: Request) -> JSONResponse:
        resolved = _intake_conversation_id(conversation_id)
        conversation = await _intake_authenticate(intake, request, resolved)
        messages = await intake.get_view(conversation)
        return JSONResponse(intake_view_payload(conversation, messages), status_code=200)
