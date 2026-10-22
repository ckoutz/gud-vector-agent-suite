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


async def test_a_visit_is_written_once_under_our_id_and_cancelling_deletes_it() -> None:
    from gvas.infrastructure.google_calendar import GoogleVisitWriter

    calls: list[tuple[str, str]] = []
    stored: set[str] = set()

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path.rsplit("/", 1)[1]))
        if request.method == "PUT":
            return httpx.Response(200 if request.url.path in stored else 404)
        if request.method == "POST":
            stored.add(f"{request.url.path}/gv{'1' * 32}")
            return httpx.Response(200)
        return httpx.Response(204 if request.method == "DELETE" else 400)

    writer = GoogleVisitWriter(httpx.AsyncClient(transport=httpx.MockTransport(answer)), token)
    visit = {"event_id": "1" * 32, "start": START, "end": END, "summary": "Estimate"}

    await writer.upsert("secret:google:larkspur", "visits", **visit)  # type: ignore[arg-type]
    await writer.upsert("secret:google:larkspur", "visits", **visit)  # type: ignore[arg-type]
    await writer.delete("secret:google:larkspur", "visits", event_id="1" * 32)

    google_id = "gv" + "1" * 32
    assert calls == [
        ("PUT", google_id),
        ("POST", "events"),
        ("PUT", google_id),
        ("DELETE", google_id),
    ]
