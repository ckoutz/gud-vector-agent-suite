"""Production wiring: concrete providers, the mounted ingress, and settings.

``build_application`` stays provider-neutral so tests can inject fakes; this
module is the only place that decides which providers the deployment uses. It
also refuses to start when a required setting is absent, because a half
configured process would accept Slack events and then fail every command in the
worker instead of failing the deploy.

Completeness review stays deterministic (marker reviewer), but a review may
only complete once the OpenAI contradiction pass has cleared it. Evidence
attribution and report generation remain deterministic. Swapping a model in or
out is a change to this module and the ports it fills, not to the application.
"""

import os
from dataclasses import dataclass
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gvas.application.checklist_evidence import MarkerChecklistEvidenceAttributor
from gvas.application.completeness_review import MarkerCompletenessReviewer
from gvas.application.contradiction_guard import GuardedCompletenessReviewer
from gvas.application.deterministic_report import DeterministicReportGenerator
from gvas.composition import Application, ApplicationPorts, build_application
from gvas.config import (
    DatabaseUrlError,
    OpenAISettings,
    ResendSettings,
    Settings,
    WorkerSettings,
    require_managed_postgres_url,
)
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.delivery_ledger import SqlChannelDeliveryLedger
from gvas.infrastructure.openai_contradiction_guard import OpenAIContradictionGuard
from gvas.infrastructure.openai_transcription import OpenAITranscriber
from gvas.infrastructure.quote_drafting import DeterministicQuoteDrafter
from gvas.infrastructure.resend import ResendQuoteDeliveryAdapter
from gvas.infrastructure.slack.api import SlackFileAttachmentAccess, SlackWebApiChatPoster
from gvas.infrastructure.slack.composition import (
    build_slack_event_router,
    build_slack_owner_reply_adapter,
)
from gvas.infrastructure.slack.config import SlackSettings
from gvas.infrastructure.slack.installations import (
    SlackInstallationError,
    parse_slack_installations,
)
from gvas.interfaces.http.app import create_app


class ProductionConfigurationError(RuntimeError):
    """Raised at startup when required settings are missing or malformed.

    The message names the environment variables only; values never appear.
    """


@dataclass(frozen=True)
class ProductionSettings:
    app: Settings
    slack: SlackSettings
    openai: OpenAISettings
    resend: ResendSettings
    worker: WorkerSettings


def load_production_settings() -> ProductionSettings:
    settings = ProductionSettings(
        app=Settings(),
        slack=SlackSettings(),
        openai=OpenAISettings(),
        resend=ResendSettings(),
        worker=WorkerSettings(),
    )
    missing = [
        name
        for name, present in (
            # The localhost default exists for development; a deployed process
            # that inherited it would quietly run against nothing.
            (
                "GVAS_DATABASE_URL or DATABASE_URL",
                "database_url" in settings.app.model_fields_set and bool(settings.app.database_url),
            ),
            ("GVAS_SLACK_SIGNING_SECRET", bool(settings.slack.signing_secret)),
            ("GVAS_SLACK_BOT_TOKEN", bool(settings.slack.bot_token)),
            ("GVAS_SLACK_INSTALLATIONS", bool(settings.slack.installations)),
            ("GVAS_OPENAI_API_KEY", settings.openai.is_configured),
            ("GVAS_RESEND_API_KEY", bool(settings.resend.api_key)),
            ("GVAS_RESEND_FROM_ADDRESS", bool(settings.resend.from_address)),
        )
        if not present
    ]
    if missing:
        raise ProductionConfigurationError(f"missing required settings: {', '.join(missing)}")
    _require_managed_database(settings.app.database_url)
    _require_single_owner(settings.slack.installations)
    return settings


def _require_managed_database(url: str) -> None:
    try:
        require_managed_postgres_url(url)
    except DatabaseUrlError as error:
        raise ProductionConfigurationError(f"GVAS_DATABASE_URL or DATABASE_URL: {error}") from error


def _require_single_owner(value: str) -> None:
    """The accepted pilot boundary: one ProTech workspace, one owner user.

    The parser stays general so later tenants need no new code, but a
    deployment that authorized a second workspace or a second owner would go
    past what this pilot was approved for, so it must not start.
    """

    try:
        installations = parse_slack_installations(value)
    except SlackInstallationError as error:
        raise ProductionConfigurationError(f"GVAS_SLACK_INSTALLATIONS: {error}") from error
    if len(installations) != 1 or len(installations[0].owner_user_ids) != 1:
        raise ProductionConfigurationError(
            "GVAS_SLACK_INSTALLATIONS must configure exactly one installation "
            "with exactly one owner user"
        )


def worker_identity(prefix: str) -> str:
    """Each replica claims outbox rows under its own name.

    Replicas that shared one identity would steal each other's leases, so the
    hostname the platform assigns is appended.
    """

    return f"{prefix}-{os.uname().nodename}-{os.getpid()}"


@dataclass(frozen=True)
class ProductionRuntime:
    settings: ProductionSettings
    application: Application
    app: FastAPI
    http_client: httpx.AsyncClient
    engine: AsyncEngine

    async def aclose(self) -> None:
        await self.http_client.aclose()
        await self.engine.dispose()


def build_production_ports(
    settings: ProductionSettings,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> ApplicationPorts:
    poster = SlackWebApiChatPoster(settings.slack, client)
    attachments = SlackFileAttachmentAccess(settings.slack, client)
    return ApplicationPorts(
        owner_replies=build_slack_owner_reply_adapter(
            poster, session_factory, SqlChannelDeliveryLedger(session_factory)
        ),
        quote_drafting=DeterministicQuoteDrafter(),
        quote_delivery=ResendQuoteDeliveryAdapter(settings.resend, client),
        transcription=OpenAITranscriber(settings.openai, client, attachments),
        completeness_review=GuardedCompletenessReviewer(
            MarkerCompletenessReviewer(), OpenAIContradictionGuard(settings.openai, client)
        ),
        checklist_evidence=MarkerChecklistEvidenceAttributor(),
        report_generation=DeterministicReportGenerator(),
    )


def build_production_runtime(settings: ProductionSettings | None = None) -> ProductionRuntime:
    resolved = settings or load_production_settings()
    engine = create_engine(resolved.app.database_url)
    session_factory = create_session_factory(engine)
    # Redirects are refused so a provider cannot move an authenticated request.
    client = httpx.AsyncClient(follow_redirects=False)
    application = build_application(
        build_production_ports(resolved, client, session_factory),
        resolved.app,
        session_factory=session_factory,
        lease_ttl=timedelta(seconds=resolved.worker.lease_seconds),
    )
    router = build_slack_event_router(application.ingest_service, resolved.slack)
    return ProductionRuntime(
        settings=resolved,
        application=application,
        app=create_app(resolved.app, (router,)),
        http_client=client,
        engine=engine,
    )


def create_production_app() -> FastAPI:
    """Uvicorn target for the web service; mounts the Slack Request URL."""

    runtime = build_production_runtime()
    runtime.app.add_event_handler("shutdown", runtime.aclose)
    return runtime.app
