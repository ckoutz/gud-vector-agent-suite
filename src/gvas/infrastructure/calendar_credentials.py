"""Outside-calendar refresh tokens, encrypted at rest (FS-26). AES-GCM with
a 32-byte key from ``GVAS_CALENDAR_TOKEN_KEY`` (base64); the link id is
bound in as associated data, so a ciphertext copied onto another link
won't decrypt."""

import base64
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE = 12


class CalendarTokenError(ValueError):
    """The key is missing or wrong, or the stored token was tampered with."""


class CalendarTokenCipher:
    def __init__(self, key_b64: str) -> None:
        try:
            key = base64.b64decode(key_b64, validate=True)
        except ValueError as error:
            raise CalendarTokenError("calendar token key is not base64") from error
        if len(key) != 32:
            raise CalendarTokenError("calendar token key must be 32 bytes")
        self._aead = AESGCM(key)

    def seal(self, link_id: UUID, refresh_token: str) -> str:
        nonce = os.urandom(_NONCE)
        sealed = self._aead.encrypt(nonce, refresh_token.encode(), link_id.bytes)
        return base64.b64encode(nonce + sealed).decode("ascii")

    def open(self, link_id: UUID, stored: str) -> str:
        try:
            raw = base64.b64decode(stored, validate=True)
            plain = self._aead.decrypt(raw[:_NONCE], raw[_NONCE:], link_id.bytes)
        except (ValueError, InvalidTag) as error:
            raise CalendarTokenError("stored calendar token could not be opened") from error
        return plain.decode()
