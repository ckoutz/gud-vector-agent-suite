"""Production wiring: concrete providers, the mounted ingress, and settings.

``build_application`` stays provider-neutral so tests can inject fakes; this
module is the only place that decides which providers the deployment uses. It
also refuses to start when a required setting is absent, because a half
configured process would accept Slack events and then fail every command in the
worker instead of failing the deploy.

Completeness review stays deterministic (marker reviewer), but a review may
only complete once the OpenAI contradiction pass has cleared it. Evidence
attribution stays deterministic (marker attributor); the OpenAI annotator only
adds verbatim supporting excerpts to items the markers satisfied and is skipped
on any failure. Report generation remains deterministic. Swapping a model in or
out is a change to this module and the ports it fills, not to the application.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from gvas.application.checklist_evidence import MarkerChecklistEvidenceAttributor
from gvas.application.completeness_review import MarkerCompletenessReviewer
from gvas.application.contradiction_guard import GuardedCompletenessReviewer
from gvas.application.deterministic_report import DeterministicReportGenerator
from gvas.application.docx_report import DocxReportRenderer
from gvas.application.guarded_checklist_evidence import GuardedChecklistEvidenceAttributor
from gvas.composition import Application, ApplicationPorts, build_application
from gvas.composition.report_publication import ReportArtifactAccess
from gvas.config import (
    DatabaseUrlError,
    ObjectStorageSettings,
    OpenAISettings,
    ResendSettings,
    Settings,
    WorkerSettings,
    require_managed_postgres_url,
)
from gvas.infrastructure.db import create_engine, create_session_factory
from gvas.infrastructure.delivery_ledger import SqlChannelDeliveryLedger
from gvas.infrastructure.object_storage import R2ObjectStorage
from gvas.infrastructure.openai_checklist_evidence import OpenAIChecklistEvidenceAnnotator
from gvas.infrastructure.openai_contradiction_guard import OpenAIContradictionGuard
from gvas.infrastructure.openai_transcription import OpenAITranscriber
from gvas.infrastructure.quote_drafting import DeterministicQuoteDrafter
from gvas.infrastructure.reporting_unit_of_work import SqlReportUnitOfWorkFactory
from gvas.infrastructure.resend import ResendQuoteDeliveryAdapter
from gvas.infrastructure.slack.api import (
    SlackFileAttachmentAccess,
    SlackWebApiChatPoster,
    SlackWebApiFileUploader,
)
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
from gvas.interfaces.logging_setup import configure_logging

logger = logging.getLogger(__name__)

R2_SETTING_NAMES = (
    "GVAS_R2_ACCOUNT_ID",
    "GVAS_R2_BUCKET",
    "GVAS_R2_ACCESS_KEY_ID",
    "GVAS_R2_SECRET_ACCESS_KEY",
)


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
    storage: ObjectStorageSettings = field(default_factory=ObjectStorageSettings)


def load_production_settings() -> ProductionSettings:
    settings = ProductionSettings(
        app=Settings(),
        slack=SlackSettings(),
        openai=OpenAISettings(),
        resend=ResendSettings(),
        worker=WorkerSettings(),
        storage=ObjectStorageSettings(),
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
    _require_complete_object_storage(settings.storage)
    return settings


def _require_complete_object_storage(storage: ObjectStorageSettings) -> None:
    """Object storage is optional, but half of it is a misconfiguration.

    With no ``GVAS_R2_*`` set the DOCX is delivered to the channel only; with
    all of them set it is also kept in the bucket. Some-but-not-all means the
    operator intended durability and would silently not get it.
    """

    present = (
        bool(storage.account_id),
        bool(storage.bucket),
        bool(storage.access_key_id),
        bool(storage.secret_access_key),
    )
    if any(present) and not all(present):
        missing = [name for name, ok in zip(R2_SETTING_NAMES, present, strict=True) if not ok]
        raise ProductionConfigurationError(
            f"object storage is partially configured; missing: {', '.join(missing)}"
        )


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
    report_artifacts = ReportArtifactAccess(
        DocxReportRenderer(), SqlReportUnitOfWorkFactory(session_factory)
    )
    object_storage = R2ObjectStorage(settings.storage) if settings.storage.is_configured else None
    if object_storage is None:
        logger.warning("object storage not configured; published reports live in Slack only")
    return ApplicationPorts(
        owner_replies=build_slack_owner_reply_adapter(
            poster,
            session_factory,
            SqlChannelDeliveryLedger(session_factory),
            uploader=SlackWebApiFileUploader(settings.slack, client),
            attachments=report_artifacts,
        ),
        quote_drafting=DeterministicQuoteDrafter(),
        quote_delivery=ResendQuoteDeliveryAdapter(settings.resend, client),
        transcription=OpenAITranscriber(settings.openai, client, attachments),
        completeness_review=GuardedCompletenessReviewer(
            MarkerCompletenessReviewer(), OpenAIContradictionGuard(settings.openai, client)
        ),
        checklist_evidence=GuardedChecklistEvidenceAttributor(
            MarkerChecklistEvidenceAttributor(),
            OpenAIChecklistEvidenceAnnotator(settings.openai, client),
        ),
        report_generation=DeterministicReportGenerator(),
        source_attachments=attachments,
        object_storage=object_storage,
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
    configure_logging(runtime.settings.app.log_level)
    runtime.app.add_event_handler("shutdown", runtime.aclose)
    return runtime.app
