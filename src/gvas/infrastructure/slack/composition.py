from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.ingestion import IngestOwnerMessageService
from gvas.infrastructure.slack.config import SlackSettings
from gvas.infrastructure.slack.delivery import (
    InMemorySlackDeliveryLedger,
    SlackChatPoster,
    SlackDeliveryLedger,
    SlackOwnerReplyAdapter,
)
from gvas.infrastructure.slack.ingress import SlackEventIngress
from gvas.infrastructure.slack.installations import StaticSlackInstallationDirectory
from gvas.infrastructure.slack.routing import SqlSlackRoutingResolver
from gvas.interfaces.http.slack import create_slack_router


def build_slack_ingress(
    ingest_service: IngestOwnerMessageService, settings: SlackSettings | None = None
) -> SlackEventIngress:
    resolved = settings or SlackSettings()
    return SlackEventIngress(
        ingest_service,
        StaticSlackInstallationDirectory.from_setting(resolved.installations),
        signing_secret=resolved.signing_secret,
        request_max_age=timedelta(seconds=resolved.request_max_age_seconds),
    )


def build_slack_event_router(
    ingest_service: IngestOwnerMessageService, settings: SlackSettings | None = None
) -> APIRouter:
    """HTTP route that verifies, normalizes, ingests and enqueues only."""

    return create_slack_router(build_slack_ingress(ingest_service, settings))


def build_slack_owner_reply_adapter(
    poster: SlackChatPoster,
    session_factory: async_sessionmaker[AsyncSession],
    ledger: SlackDeliveryLedger | None = None,
) -> SlackOwnerReplyAdapter:
    return SlackOwnerReplyAdapter(
        poster,
        SqlSlackRoutingResolver(session_factory),
        ledger if ledger is not None else InMemorySlackDeliveryLedger(),
    )
