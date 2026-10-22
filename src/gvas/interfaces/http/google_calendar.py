"""Google Calendar change notices. A notice carries no details: a valid one
only asks the worker to re-read that calendar's busy times next round."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.infrastructure.google_calendar import channel_token_ok
from gvas.scheduling.core.sync import request_sync


def create_google_notify_router(
    session_factory: async_sessionmaker[AsyncSession],
    secret: str,
    *,
    path: str = "/v1/calendar/google/notify",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter()

    @router.post(path)
    async def google_notice(request: Request) -> Response:
        channel_id = request.headers.get("X-Goog-Channel-ID", "")
        token = request.headers.get("X-Goog-Channel-Token", "")
        if not channel_id or not channel_token_ok(secret, channel_id, token):
            return Response(status_code=401)
        if request.headers.get("X-Goog-Resource-State") != "sync":
            await request_sync(session_factory, channel_id, clock)
        return Response(status_code=204)

    return router
