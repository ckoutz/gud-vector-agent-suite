"""Slack Web API adapters.

Only this module talks HTTP to Slack. Provider errors are translated into the
adapter errors the dispatcher already understands, and neither the bot token nor
Slack's private file URLs ever leave the module.

Slack has no idempotency key on ``chat.postMessage``, so a delivery key is
attached as message ``metadata`` for server-side reconciliation and duplicate
suppression is the ledger's job; see ``ledger.py`` for the honest semantics.
"""

from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.domain.enums import MediaKind
from gvas.domain.messages import AttachmentPayload, AttachmentReference
from gvas.infrastructure.slack.config import SlackSettings
from gvas.infrastructure.slack.delivery import (
    SlackChatPostRequest,
    SlackChatPostResult,
    SlackDeliveryError,
)
from gvas.infrastructure.slack.normalization import ATTACHMENT_LOCATOR_PREFIX

DELIVERY_METADATA_EVENT_TYPE: Final = "gvas_owner_reply"
ALLOWED_FILE_HOSTS: Final = frozenset({"files.slack.com", "slack.com"})


class SlackAttachmentError(RuntimeError):
    """Raised when a Slack-hosted attachment cannot be safely retrieved."""


class SlackResponseModel(BaseModel):
    """Slack responses carry fields we do not model; only the contract is read."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class SlackChatPostResponse(SlackResponseModel):
    ok: bool
    ts: str | None = None
    channel: str | None = None
    error: str | None = None


class SlackFileObject(SlackResponseModel):
    id: str
    name: str | None = None
    mimetype: str | None = None
    size: int | None = None
    url_private_download: str | None = None
    url_private: str | None = None

    @property
    def download_url(self) -> str | None:
        return self.url_private_download or self.url_private


class SlackFileInfoResponse(SlackResponseModel):
    ok: bool
    file: SlackFileObject | None = None
    error: str | None = None


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def file_id_of(attachment: AttachmentReference) -> str:
    """Reverse the opaque locator this channel issued; reject anything else."""

    prefix = f"{ATTACHMENT_LOCATOR_PREFIX}:"
    if not attachment.locator.startswith(prefix):
        raise SlackAttachmentError("attachment locator was not issued by the slack channel")
    file_id = attachment.locator[len(prefix) :]
    if not file_id:
        raise SlackAttachmentError("slack attachment locator carries no file identifier")
    return file_id


def _require_allowed_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname is None:
        raise SlackAttachmentError("slack returned a non-https download location")
    host = parts.hostname.lower()
    if host not in ALLOWED_FILE_HOSTS and not host.endswith(".slack.com"):
        raise SlackAttachmentError("slack returned a download location outside slack")


class SlackWebApiChatPoster:
    """Posts owner replies through ``chat.postMessage``."""

    def __init__(self, settings: SlackSettings, client: httpx.AsyncClient) -> None:
        if not settings.bot_token:
            raise SlackDeliveryError("slack bot token is not configured")
        self._settings = settings
        self._client = client

    async def post_message(self, request: SlackChatPostRequest) -> SlackChatPostResult:
        payload: dict[str, object] = {
            "channel": request.channel,
            "text": request.text,
            "metadata": {
                "event_type": DELIVERY_METADATA_EVENT_TYPE,
                "event_payload": {"delivery_key": request.idempotency_key},
            },
        }
        if request.thread_ts is not None:
            payload["thread_ts"] = request.thread_ts
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url}/chat.postMessage",
                json=payload,
                headers=_authorization(self._settings.bot_token),
                timeout=self._settings.api_timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise SlackDeliveryError("slack was unreachable") from error
        return _chat_post_result(response)


def _chat_post_result(response: httpx.Response) -> SlackChatPostResult:
    if response.status_code >= 400:
        raise SlackDeliveryError(f"slack returned http {response.status_code}")
    try:
        parsed = SlackChatPostResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise SlackDeliveryError("slack returned an unreadable response") from error
    if not parsed.ok or parsed.ts is None:
        return SlackChatPostResult(
            detail=f"slack rejected the message: {parsed.error or 'unknown'}"
        )
    return SlackChatPostResult(message_ts=parsed.ts)


class SlackFileAttachmentAccess:
    """Fetches private Slack file bytes into memory and nowhere else.

    ``files.info`` is called first so the download is validated against Slack's
    own metadata: identity, media type and size are checked before any bytes are
    read, and the private URL is never returned to callers or persisted.
    """

    def __init__(self, settings: SlackSettings, client: httpx.AsyncClient) -> None:
        if not settings.bot_token:
            raise SlackAttachmentError("slack bot token is not configured")
        self._settings = settings
        self._client = client

    async def fetch(self, attachment: AttachmentReference) -> AttachmentPayload:
        file_id = file_id_of(attachment)
        info = await self._file_info(file_id)
        if info.id != file_id:
            raise SlackAttachmentError("slack returned metadata for a different file")
        self._require_within_limit(info.size)
        self._require_expected_media(attachment, info)
        download_url = info.download_url
        if download_url is None:
            raise SlackAttachmentError("slack file has no download location")
        content = await self._download(download_url)
        return AttachmentPayload(
            content=content,
            mime_type=info.mimetype or attachment.mime_type,
            filename=info.name or attachment.filename,
        )

    async def _file_info(self, file_id: str) -> SlackFileObject:
        try:
            response = await self._client.get(
                f"{self._settings.api_base_url}/files.info",
                params={"file": file_id},
                headers=_authorization(self._settings.bot_token),
                timeout=self._settings.api_timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise SlackAttachmentError("slack was unreachable") from error
        if response.status_code >= 400:
            raise SlackAttachmentError(f"slack returned http {response.status_code}")
        try:
            parsed = SlackFileInfoResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise SlackAttachmentError("slack returned unreadable file metadata") from error
        if not parsed.ok or parsed.file is None:
            raise SlackAttachmentError(
                f"slack refused the file request: {parsed.error or 'unknown'}"
            )
        return parsed.file

    def _require_within_limit(self, size: int | None) -> None:
        if size is not None and size > self._settings.attachment_max_bytes:
            raise SlackAttachmentError(
                f"slack file exceeds the {self._settings.attachment_max_bytes} byte limit"
            )

    @staticmethod
    def _require_expected_media(attachment: AttachmentReference, info: SlackFileObject) -> None:
        if attachment.media_kind is not MediaKind.AUDIO:
            return
        mimetype = info.mimetype or attachment.mime_type
        if mimetype is None or not mimetype.lower().startswith("audio/"):
            raise SlackAttachmentError("slack file is not audio content")

    async def _download(self, url: str) -> bytes:
        _require_allowed_host(url)
        try:
            response = await self._client.get(
                url,
                headers=_authorization(self._settings.bot_token),
                timeout=self._settings.api_timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise SlackAttachmentError("slack file download failed") from error
        if response.is_redirect:
            raise SlackAttachmentError("slack file download attempted a redirect")
        if response.status_code >= 400:
            raise SlackAttachmentError(f"slack file download returned http {response.status_code}")
        self._require_declared_length(response.headers)
        content = response.content
        if len(content) > self._settings.attachment_max_bytes:
            raise SlackAttachmentError(
                f"slack file exceeds the {self._settings.attachment_max_bytes} byte limit"
            )
        return content

    def _require_declared_length(self, headers: Mapping[str, str]) -> None:
        declared = headers.get("content-length")
        if declared is None:
            return
        try:
            length = int(declared)
        except ValueError as error:
            raise SlackAttachmentError("slack file download declared an unreadable size") from error
        if length > self._settings.attachment_max_bytes:
            raise SlackAttachmentError(
                f"slack file exceeds the {self._settings.attachment_max_bytes} byte limit"
            )
