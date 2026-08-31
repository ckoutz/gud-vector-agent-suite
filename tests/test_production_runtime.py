"""Production composition and the service entrypoints built on it."""

import asyncio
from collections.abc import Iterator
from uuid import uuid4

import pytest

from gvas.composition.production import (
    ProductionConfigurationError,
    ProductionSettings,
    build_production_runtime,
    load_production_settings,
    worker_identity,
)
from gvas.config import OpenAISettings, ResendSettings, Settings, WorkerSettings
from gvas.infrastructure.slack.config import SlackSettings
from gvas.interfaces.worker import build_worker, run_worker

BUSINESS_ID = uuid4()
ENVIRONMENT = {
    "GVAS_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "GVAS_SLACK_SIGNING_SECRET": "signing-secret",
    "GVAS_SLACK_BOT_TOKEN": "xoxb-not-a-real-token",
    "GVAS_SLACK_INSTALLATIONS": f"T0000000000={BUSINESS_ID}:U0000000000",
    "GVAS_OPENAI_API_KEY": "sk-not-a-real-key",
    "GVAS_RESEND_API_KEY": "re-not-a-real-key",
    "GVAS_RESEND_FROM_ADDRESS": "quotes@example.test",
}


@pytest.fixture
def production_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    yield


def settings() -> ProductionSettings:
    return ProductionSettings(
        app=Settings(),
        slack=SlackSettings(),
        openai=OpenAISettings(),
        resend=ResendSettings(),
        worker=WorkerSettings(),
    )


@pytest.mark.usefixtures("production_environment")
def test_startup_rejects_a_partially_configured_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GVAS_RESEND_API_KEY")
    monkeypatch.delenv("GVAS_OPENAI_API_KEY")

    with pytest.raises(ProductionConfigurationError) as error:
        load_production_settings()

    message = str(error.value)
    assert "GVAS_RESEND_API_KEY" in message
    assert "GVAS_OPENAI_API_KEY" in message
    assert "signing-secret" not in message


@pytest.mark.usefixtures("production_environment")
def test_production_app_serves_the_slack_request_url_and_health_check() -> None:
    runtime = build_production_runtime(load_production_settings())

    paths = {route.path for route in runtime.app.routes if hasattr(route, "path")}

    assert runtime.settings.slack.events_path in paths
    assert "/healthz" in paths


@pytest.mark.usefixtures("production_environment")
def test_worker_uses_configured_batching_and_a_replica_specific_identity() -> None:
    runtime = build_production_runtime(load_production_settings())

    build_worker(runtime)

    identity = worker_identity(runtime.settings.worker.id_prefix)
    assert identity.startswith(f"{runtime.settings.worker.id_prefix}-")
    assert identity != runtime.settings.worker.id_prefix


@pytest.mark.usefixtures("production_environment")
@pytest.mark.asyncio
async def test_worker_loop_stops_when_the_platform_asks_it_to() -> None:
    runtime = build_production_runtime(load_production_settings())
    stopping = asyncio.Event()
    stopping.set()

    batches = await run_worker(runtime, stopping)

    await runtime.aclose()
    assert batches == 0


def test_worker_identity_separates_replicas_on_one_host() -> None:
    assert worker_identity("outbox-worker") == worker_identity("outbox-worker")
    assert worker_identity("a") != worker_identity("b")


@pytest.mark.usefixtures("production_environment")
def test_production_settings_read_the_deployment_environment() -> None:
    resolved = settings()

    assert resolved.slack.installations
    assert resolved.app.database_url.startswith("sqlite+aiosqlite")
