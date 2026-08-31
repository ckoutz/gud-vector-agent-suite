"""OpenAI audio transcription.

The audio never lands in GVAS storage: the channel's attachment access adapter
hands over bytes in memory, they are posted to the transcription endpoint, and
only the resulting text is persisted by the application. OpenAI is used for
transcription alone; review, reporting and quote drafting stay deterministic.
"""

from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from gvas.config import OpenAISettings
from gvas.domain.messages import AudioReference, TranscriptResult
from gvas.domain.ports import AttachmentAccessPort

TRANSCRIPTION_PATH: Final = "/audio/transcriptions"
DEFAULT_FILENAME: Final = "audio"
DEFAULT_MIME_TYPE: Final = "application/octet-stream"


class TranscriptionError(RuntimeError):
    """Raised when transcription should be retried by the dispatcher."""


class OpenAITranscriptionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    text: str
    language: str | None = None
    duration: float | None = None


class OpenAITranscriber:
    """Transcribes owner audio through the documented transcription endpoint."""

    def __init__(
        self,
        settings: OpenAISettings,
        client: httpx.AsyncClient,
        attachment_access: AttachmentAccessPort,
    ) -> None:
        if not settings.is_configured:
            raise TranscriptionError("openai api key is not configured")
        self._settings = settings
        self._client = client
        self._attachment_access = attachment_access

    async def transcribe(self, audio: AudioReference) -> TranscriptResult:
        payload = await self._attachment_access.fetch(audio.attachment)
        if not payload.content:
            raise TranscriptionError("audio attachment carried no bytes")
        if len(payload.content) > self._settings.max_audio_bytes:
            raise TranscriptionError("audio attachment is larger than the configured limit")
        files = {
            "file": (
                payload.filename or DEFAULT_FILENAME,
                payload.content,
                payload.mime_type or DEFAULT_MIME_TYPE,
            )
        }
        try:
            response = await self._client.post(
                f"{self._settings.api_base_url}{TRANSCRIPTION_PATH}",
                data={"model": self._settings.transcription_model},
                files=files,
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise TranscriptionError("openai was unreachable") from error
        return _transcript(response)


def _transcript(response: httpx.Response) -> TranscriptResult:
    if response.status_code >= 400:
        raise TranscriptionError(f"openai returned http {response.status_code}")
    try:
        parsed = OpenAITranscriptionResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise TranscriptionError("openai returned an unreadable transcription") from error
    return TranscriptResult(
        text=parsed.text,
        language=parsed.language,
        duration_seconds=parsed.duration,
    )
