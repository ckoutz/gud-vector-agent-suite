"""Outbox worker entrypoint.

Every provider call happens here rather than in the web request, so this
service must run continuously and separately from the web service. The loop
polls one batch at a time; SIGTERM and SIGINT stop it after the batch in flight
finishes, which leaves claimed rows to expire by lease rather than mid-command.

    gvas-worker
"""

import argparse
import asyncio
import logging
import signal
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from gvas.composition.dispatcher import OutboxWorker, WorkerBatchReport
from gvas.composition.production import (
    ProductionRuntime,
    build_production_runtime,
    worker_identity,
)
from gvas.infrastructure.db import create_session_factory
from gvas.infrastructure.visit_messages import VisitMessenger
from gvas.interfaces.logging_setup import configure_logging
from gvas.scheduling.core.engine import SchedulingEngine

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_worker(runtime: ProductionRuntime) -> OutboxWorker:
    settings = runtime.settings.worker
    application = runtime.application
    return OutboxWorker(
        application.outbox,
        application.dispatcher,
        now=_utcnow,
        worker_id=worker_identity(settings.id_prefix),
        batch_size=settings.batch_size,
        lease_ttl=timedelta(seconds=settings.lease_seconds),
        retry_in=timedelta(seconds=settings.retry_seconds),
        failure_notices=application.failure_notice_service,
    )


async def _sweep_sandboxes(runtime: ProductionRuntime) -> None:
    if runtime.sandboxes is None:
        return
    try:
        await runtime.sandboxes.sweep()
    except Exception:
        logger.exception("demo sandbox sweep failed; retrying next round")


SCHEDULE_SWEEP_SECONDS = 60


async def _sweep_schedules(scheduling: SchedulingEngine, messenger: VisitMessenger) -> None:
    """Lapse old holds, expire approvals nobody gave in time and queue the
    visit messages now due."""

    try:
        result = await scheduling.sweep()
        await messenger.run_due()
    except Exception:
        logger.exception("scheduling sweep failed; retrying next round")
        return
    if result.approvals_expired or result.approval_reminders_due:
        logger.info(
            "scheduling sweep: %d approvals expired, %d reminders due",
            len(result.approvals_expired),
            len(result.approval_reminders_due),
        )


async def run_worker(runtime: ProductionRuntime, stopping: asyncio.Event) -> int:
    worker = build_worker(runtime)
    schedule_sessions = create_session_factory(runtime.engine)
    scheduling = SchedulingEngine(schedule_sessions)
    messenger = VisitMessenger(
        schedule_sessions,
        organizer_email=runtime.settings.resend.reply_to_address
        or runtime.settings.resend.from_address,
        link_base_url=runtime.settings.intake.decision_link_base_url,
    )
    poll_seconds = runtime.settings.worker.poll_seconds
    sweep_every = runtime.settings.demo.sandbox_sweep_minutes * 60
    next_sweep = time.monotonic()
    next_schedule_sweep = time.monotonic()
    batches = 0
    while not stopping.is_set():
        if time.monotonic() >= next_sweep:
            await _sweep_sandboxes(runtime)
            next_sweep = time.monotonic() + sweep_every
        if time.monotonic() >= next_schedule_sweep:
            await _sweep_schedules(scheduling, messenger)
            next_schedule_sweep = time.monotonic() + SCHEDULE_SWEEP_SECONDS
        report: WorkerBatchReport = await worker.run_once()
        batches += 1
        if report.claimed == 0:
            try:
                await asyncio.wait_for(stopping.wait(), timeout=poll_seconds)
            except TimeoutError:
                continue
    return batches


async def _main() -> int:
    runtime = build_production_runtime()
    configure_logging(runtime.settings.app.log_level)
    logger.info(
        "worker started poll=%ss batch=%d lease=%ss retry=%ss",
        runtime.settings.worker.poll_seconds,
        runtime.settings.worker.batch_size,
        runtime.settings.worker.lease_seconds,
        runtime.settings.worker.retry_seconds,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(received, stopping.set)
    try:
        batches = await run_worker(runtime, stopping)
    finally:
        await runtime.aclose()
    logger.info("worker stopped after %d batches", batches)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the GVAS outbox worker")
    parser.parse_args(argv)
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
