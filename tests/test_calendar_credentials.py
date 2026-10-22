import base64
from uuid import uuid4

import pytest

from gvas.infrastructure.calendar_credentials import CalendarTokenCipher, CalendarTokenError

KEY = base64.b64encode(b"k" * 32).decode()


def test_a_sealed_token_opens_only_for_its_own_link_and_never_shows_in_clear() -> None:
    cipher = CalendarTokenCipher(KEY)
    link, other = uuid4(), uuid4()

    stored = cipher.seal(link, "1//refresh-token")

    assert "refresh-token" not in stored
    assert cipher.open(link, stored) == "1//refresh-token"
    assert cipher.seal(link, "1//refresh-token") != stored
    with pytest.raises(CalendarTokenError):
        cipher.open(other, stored)
    with pytest.raises(CalendarTokenError):
        CalendarTokenCipher(base64.b64encode(b"x" * 32).decode()).open(link, stored)


@pytest.mark.parametrize("key", ["", "not base64!", base64.b64encode(b"short").decode()])
def test_a_bad_key_is_refused(key: str) -> None:
    with pytest.raises(CalendarTokenError):
        CalendarTokenCipher(key)
