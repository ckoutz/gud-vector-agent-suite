from pydantic_settings import BaseSettings, SettingsConfigDict


class SlackSettings(BaseSettings):
    """Slack adapter settings; values come from the environment only."""

    model_config = SettingsConfigDict(env_prefix="GVAS_SLACK_", env_file=".env", extra="ignore")

    signing_secret: str = ""
    events_path: str = "/slack/events"
    request_max_age_seconds: int = 300
    installations: str = ""
