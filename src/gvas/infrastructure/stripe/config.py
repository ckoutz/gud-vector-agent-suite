from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StripeSettings(BaseSettings):
    """Card-checkout settings, all read from ``GVAS_STRIPE_*``.

    Optional as a set: ``secret_key`` opens checkout sessions and
    ``webhook_secret`` verifies the payment notifications; the production
    composition rejects a deployment that sets only one of them. With neither
    set the public quote routes still serve views and declines, and accepting
    answers 503.
    """

    model_config = SettingsConfigDict(env_prefix="GVAS_STRIPE_", env_file=".env", extra="ignore")

    secret_key: str = ""
    webhook_secret: str = ""
    api_base_url: str = "https://api.stripe.com/v1"
    timeout_seconds: float = Field(default=30.0, gt=0)

    @property
    def required_settings(self) -> dict[str, bool]:
        return {
            "GVAS_STRIPE_SECRET_KEY": bool(self.secret_key),
            "GVAS_STRIPE_WEBHOOK_SECRET": bool(self.webhook_secret),
        }

    @property
    def is_configured(self) -> bool:
        return all(self.required_settings.values())

    @property
    def is_partially_configured(self) -> bool:
        return any(self.required_settings.values()) and not self.is_configured
