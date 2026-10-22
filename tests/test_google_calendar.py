from datetime import UTC, datetime

import httpx
import pytest

from gvas.infrastructure.google_calendar import (
    FREEBUSY_URL,
    GoogleBusyCalendar,
    GoogleCalendarError,
)
from gvas.scheduling.core.sync import ExternalAccessRevokedError

START = datetime(2026, 10, 27, 15, tzinfo=UTC)
END = datetime(2026, 10, 28, 15, tzinfo=UTC)


async def token(ref: str) -> str:
    assert ref == "secret:google:larkspur"
    return "access-token"


def calendar(handler: httpx.MockTransport) -> GoogleBusyCalendar:
    return GoogleBusyCalendar(httpx.AsyncClient(transport=handler), token)


async def test_busy_times_come_back_without_titles() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == FREEBUSY_URL
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={
                "calendars": {
                    "primary": {
                        "busy": [{"start": "2026-10-27T17:00:00Z", "end": "2026-10-27T18:00:00Z"}]
                    }
                }
            },
        )

    blocks = await calendar(httpx.MockTransport(answer)).busy("secret:google:larkspur", START, END)

    assert [(block.start.hour, block.end.hour) for block in blocks] == [(17, 18)]


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_token_means_reconnect(status: int) -> None:
    google = calendar(httpx.MockTransport(lambda request: httpx.Response(status)))

    with pytest.raises(ExternalAccessRevokedError):
        await google.busy("secret:google:larkspur", START, END)


async def test_a_google_outage_is_retried_not_treated_as_revoked() -> None:
    google = calendar(httpx.MockTransport(lambda request: httpx.Response(503)))

    with pytest.raises(GoogleCalendarError):
        await google.busy("secret:google:larkspur", START, END)
