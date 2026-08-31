from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_DRIVER = "postgresql+asyncpg"
# asyncpg configures TLS through connect arguments, not the libpq query string.
LIBPQ_ONLY_QUERY_KEYS = frozenset({"sslmode", "channel_binding"})


def normalize_async_database_url(url: str) -> str:
    """Accept a managed-provider libpq URL and return a SQLAlchemy asyncpg URL.

    Railway hands out ``postgresql://`` URLs that may carry libpq-only query
    parameters. Rewriting them here keeps the deployment from having to
    maintain a second, hand-edited copy of the same credential.
    """

    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql", ASYNC_DRIVER}:
        return url
    query = [
        (key, value) for key, value in parse_qsl(parts.query) if key not in LIBPQ_ONLY_QUERY_KEYS
    ]
    return urlunsplit((ASYNC_DRIVER, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GVAS_", env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    # Managed Postgres providers inject an unprefixed DATABASE_URL.
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/gvas",
        validation_alias=AliasChoices("GVAS_DATABASE_URL", "DATABASE_URL"),
    )

    @field_validator("database_url")
    @classmethod
    def database_url_uses_async_driver(cls, value: str) -> str:
        return normalize_async_database_url(value)


class OpenAISettings(BaseSettings):
    """OpenAI is used for audio transcription only in this pilot."""

    model_config = SettingsConfigDict(env_prefix="GVAS_OPENAI_", env_file=".env", extra="ignore")

    api_key: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    transcription_model: str = "whisper-1"
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_audio_bytes: int = Field(default=25 * 1024 * 1024, ge=1)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


class ResendSettings(BaseSettings):
    """Resend delivers approved customer quotes by email."""

    model_config = SettingsConfigDict(env_prefix="GVAS_RESEND_", env_file=".env", extra="ignore")

    api_key: str = ""
    api_base_url: str = "https://api.resend.com"
    from_address: str = ""
    reply_to_address: str = ""
    portal_url: str = "https://gudvector.com/portal/login"
    timeout_seconds: float = Field(default=30.0, gt=0)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.from_address)


class WorkerSettings(BaseSettings):
    """Runtime knobs for the continuously running outbox worker service."""

    model_config = SettingsConfigDict(env_prefix="GVAS_WORKER_", env_file=".env", extra="ignore")

    batch_size: int = Field(default=10, ge=1)
    poll_seconds: float = Field(default=1.0, gt=0)
    retry_seconds: float = Field(default=30.0, gt=0)
    lease_seconds: float = Field(default=300.0, gt=0)
    id_prefix: str = "outbox-worker"


class ObjectStorageSettings(BaseSettings):
    """Cloudflare R2 (S3-compatible) settings; values come from the environment only."""

    model_config = SettingsConfigDict(env_prefix="GVAS_R2_", env_file=".env", extra="ignore")

    account_id: str = ""
    bucket: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = "auto"

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @property
    def is_configured(self) -> bool:
        return bool(
            self.account_id and self.bucket and self.access_key_id and self.secret_access_key
        )
