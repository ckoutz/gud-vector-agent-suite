from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GVAS_", env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/gvas"


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
