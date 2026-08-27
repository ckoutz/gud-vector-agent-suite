from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.identifiers import BusinessId

SLACK_SOURCE_NAMESPACE = "slack"


class SlackInstallationError(ValueError):
    pass


class SlackInstallation(BaseModel):
    """A Slack bot installation bound to exactly one business."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    business_id: BusinessId
    team_id: str = Field(min_length=1)
    api_app_id: str | None = None

    @property
    def external_endpoint_id(self) -> str:
        if self.api_app_id is None:
            return self.team_id
        return f"{self.team_id}/{self.api_app_id}"


class SlackInstallationDirectory(Protocol):
    async def find(self, team_id: str, api_app_id: str | None) -> SlackInstallation | None: ...


class StaticSlackInstallationDirectory:
    """Configuration-driven installation lookup keyed by Slack workspace."""

    def __init__(self, installations: tuple[SlackInstallation, ...]) -> None:
        self._by_team = {installation.team_id: installation for installation in installations}

    @classmethod
    def from_setting(cls, value: str) -> "StaticSlackInstallationDirectory":
        """Parse ``T0001=<business-uuid>,T0002=<business-uuid>`` settings values."""

        installations: list[SlackInstallation] = []
        for entry in value.split(","):
            candidate = entry.strip()
            if not candidate:
                continue
            team_id, separator, business = candidate.partition("=")
            if not separator or not team_id.strip() or not business.strip():
                raise SlackInstallationError(f"invalid installation entry {candidate!r}")
            try:
                business_uuid = UUID(business.strip())
            except ValueError as error:
                raise SlackInstallationError(
                    f"invalid business identifier in entry {candidate!r}"
                ) from error
            installations.append(
                SlackInstallation(business_id=BusinessId(business_uuid), team_id=team_id.strip())
            )
        return cls(tuple(installations))

    async def find(self, team_id: str, api_app_id: str | None) -> SlackInstallation | None:
        installation = self._by_team.get(team_id)
        if installation is None:
            return None
        if installation.api_app_id is not None and installation.api_app_id != api_app_id:
            return None
        return installation
