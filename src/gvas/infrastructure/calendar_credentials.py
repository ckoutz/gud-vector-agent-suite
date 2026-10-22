"""Outside-calendar refresh tokens, encrypted at rest (FS-26). AES-GCM with
a 32-byte key from ``GVAS_CALENDAR_TOKEN_KEY`` (base64); the link id is
bound in as associated data, so a ciphertext copied onto another link
won't decrypt."""

import base64
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.scheduling.core.records import SchedulingExternalLinkRecord
from gvas.scheduling.core.sync import ExternalAccessRevokedError

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


class StoredRefreshTokens:
    """Resolves a link's ``credentials_ref`` (its own id) to the refresh token
    sealed on that link. A missing or unopenable token means reconnect."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], cipher: CalendarTokenCipher
    ) -> None:
        self._sessions = session_factory
        self._cipher = cipher

    async def store(self, link_id: UUID, refresh_token: str) -> None:
        async with self._sessions() as session, session.begin():
            link = await session.get(SchedulingExternalLinkRecord, link_id)
            if link is None:
                raise CalendarTokenError("no such calendar link")
            link.refresh_token_sealed = self._cipher.seal(link_id, refresh_token)
            link.credentials_ref = str(link_id)

    async def __call__(self, credentials_ref: str) -> str:
        try:
            link_id = UUID(credentials_ref)
        except ValueError as error:
            raise ExternalAccessRevokedError() from error
        async with self._sessions() as session:
            link = await session.get(SchedulingExternalLinkRecord, link_id)
        if link is None or not link.refresh_token_sealed:
            raise ExternalAccessRevokedError()
        try:
            return self._cipher.open(link_id, link.refresh_token_sealed)
        except CalendarTokenError as error:
            raise ExternalAccessRevokedError() from error
