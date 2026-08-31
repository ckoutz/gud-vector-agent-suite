from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gvas.application.report_generation import GenerateFieldNotesReportService
from gvas.domain.identifiers import BusinessId, JsonValue
from gvas.domain.reporting import (
    FieldNoteCaseSnapshot,
    FieldNoteCaseStatus,
    FieldNotesReportDocument,
    IncompleteFieldNoteCaseError,
    LostReportLeaseError,
    MalformedGeneratedReportError,
    ReportGenerationFailedError,
    ReportGenerationPort,
    ReportGenerationRequest,
    ReportStatus,
    ReportUnitOfWork,
    field_note_source_fingerprint,
    field_notes_report_id,
    field_notes_report_version_id,
)
from gvas.infrastructure.models import (
    Business,
    FieldNoteReport,
)
from gvas.infrastructure.models import (
    FieldNoteReportVersion as FieldNoteReportVersionRow,
)
from gvas.infrastructure.reporting_repositories import SqlFieldNotesReportRepository
from gvas.infrastructure.reporting_unit_of_work import (
    SqlReportUnitOfWork,
    SqlReportUnitOfWorkFactory,
)

NOW = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
STALE_BEFORE = NOW - timedelta(minutes=5)


class UnitOfWorkTracker:
    def __init__(self) -> None:
        self.open_count = 0


class TrackingSqlReportUnitOfWork(SqlReportUnitOfWork):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tracker: UnitOfWorkTracker,
    ) -> None:
        super().__init__(session_factory)
        self._tracker = tracker

    async def __aenter__(self) -> "TrackingSqlReportUnitOfWork":
        self._tracker.open_count += 1
        try:
            await super().__aenter__()
        except BaseException:
            self._tracker.open_count -= 1
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        try:
            await super().__aexit__(exc_type, exc, traceback)
        finally:
            self._tracker.open_count -= 1


class TrackingUnitOfWorkFactory:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tracker: UnitOfWorkTracker,
    ) -> None:
        self._session_factory = session_factory
        self._tracker = tracker

    def __call__(self) -> ReportUnitOfWork:
        return TrackingSqlReportUnitOfWork(self._session_factory, self._tracker)


class DeterministicGenerator(ReportGenerationPort):
    def __init__(
        self,
        results: list[dict[str, JsonValue] | Exception],
        tracker: UnitOfWorkTracker | None = None,
    ) -> None:
        self.results = results
        self.tracker = tracker
        self.requests: list[ReportGenerationRequest] = []

    async def generate(self, request: ReportGenerationRequest) -> dict[str, JsonValue]:
        if self.tracker is not None:
            assert self.tracker.open_count == 0
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def source(
    business_id: BusinessId,
    *,
    case_id: UUID | None = None,
    status: FieldNoteCaseStatus = FieldNoteCaseStatus.COMPLETED,
    transcript: str = "Canonical field observation.",
) -> FieldNoteCaseSnapshot:
    return FieldNoteCaseSnapshot(
        business_id=business_id,
        case_id=case_id or uuid4(),
        status=status,
        completed_at=NOW if status is FieldNoteCaseStatus.COMPLETED else None,
        canonical_transcript=transcript,
        checklist_evidence=(
            {
                "item_key": "safety.panel",
                "prompt": "Inspect the safety panel",
                "outcome": "observed",
                "evidence": ("Panel was secured.",),
            },
        ),
        correlated_answers=(
            {
                "question_key": "access.condition",
                "question": "Was access restricted?",
                "answer": "No.",
            },
        ),
    )


def valid_document(title: str = "Field Notes") -> dict[str, JsonValue]:
    return {
        "schema_version": "field-notes-report/v1",
        "title": title,
        "sections": [
            {
                "section_key": "observations",
                "heading": "Observations",
                "blocks": [
                    {
                        "kind": "text",
                        "text": "The safety panel was secured and access was unrestricted.",
                        "evidence_refs": [
                            {"source": "transcript", "key": "canonical"},
                            {"source": "checklist", "key": "safety.panel"},
                            {"source": "answer", "key": "access.condition"},
                        ],
                    }
                ],
            }
        ],
    }


async def seed_business(
    session_factory: async_sessionmaker[AsyncSession],
    business_id: BusinessId,
    slug: str,
) -> None:
    async with session_factory() as session:
        session.add(
            Business(
                id=business_id,
                slug=slug,
                name=slug,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()


async def test_valid_generation_is_idempotent_and_runs_outside_uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "valid-generation")
    case = source(business_id)
    tracker = UnitOfWorkTracker()
    generator = DeterministicGenerator([valid_document()], tracker)
    service = GenerateFieldNotesReportService(
        TrackingUnitOfWorkFactory(session_factory, tracker),
        generator,
    )

    first = await service.generate(case, now=NOW, stale_before=STALE_BEFORE)
    replay = await service.generate(
        case,
        now=NOW + timedelta(minutes=1),
        stale_before=STALE_BEFORE,
    )

    fingerprint = field_note_source_fingerprint(case)
    expected_report_id = field_notes_report_id(business_id, case.case_id)
    assert first == replay
    assert first.report_id == expected_report_id
    assert first.report_version_id == field_notes_report_version_id(
        expected_report_id, 1, fingerprint
    )
    assert first.version == 1
    assert len(generator.requests) == 1
    assert generator.requests[0].source == case
    async with session_factory() as session:
        report = await session.get(FieldNoteReport, expected_report_id)
        version_count = await session.scalar(
            select(func.count()).select_from(FieldNoteReportVersionRow)
        )
    assert report is not None
    assert report.status == ReportStatus.COMPLETED.value
    assert report.attempts == 1
    assert report.current_version == 1
    assert version_count == 1


async def test_incomplete_case_is_rejected_before_persistence_or_generation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "incomplete")
    generator = DeterministicGenerator([valid_document()])
    service = GenerateFieldNotesReportService(
        SqlReportUnitOfWorkFactory(session_factory),
        generator,
    )

    with pytest.raises(IncompleteFieldNoteCaseError):
        await service.generate(
            source(business_id, status=FieldNoteCaseStatus.IN_PROGRESS),
            now=NOW,
            stale_before=STALE_BEFORE,
        )

    async with session_factory() as session:
        report_count = await session.scalar(select(func.count()).select_from(FieldNoteReport))
    assert report_count == 0
    assert generator.requests == []


async def test_malformed_content_is_failed_and_retry_uses_same_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "malformed")
    case = source(business_id)
    malformed = valid_document()
    sections = malformed["sections"]
    assert isinstance(sections, list)
    section = sections[0]
    assert isinstance(section, dict)
    blocks = section["blocks"]
    assert isinstance(blocks, list)
    block = blocks[0]
    assert isinstance(block, dict)
    block["evidence_refs"] = [{"source": "checklist", "key": "unknown"}]
    generator = DeterministicGenerator([malformed, valid_document()])
    service = GenerateFieldNotesReportService(
        SqlReportUnitOfWorkFactory(session_factory),
        generator,
    )

    with pytest.raises(MalformedGeneratedReportError):
        await service.generate(case, now=NOW, stale_before=STALE_BEFORE)
    report_id = field_notes_report_id(business_id, case.case_id)
    async with session_factory() as session:
        failed_report = await session.get(FieldNoteReport, report_id)
        failed_version_count = await session.scalar(
            select(func.count()).select_from(FieldNoteReportVersionRow)
        )
    assert failed_report is not None
    assert failed_report.status == ReportStatus.FAILED.value
    assert failed_version_count == 0
    completed = await service.generate(
        case,
        now=NOW + timedelta(minutes=1),
        stale_before=STALE_BEFORE,
    )

    assert completed.version == 1
    assert len(generator.requests) == 2
    assert generator.requests[0].report_version == generator.requests[1].report_version == 1
    async with session_factory() as session:
        report = await session.get(FieldNoteReport, completed.report_id)
        version_count = await session.scalar(
            select(func.count()).select_from(FieldNoteReportVersionRow)
        )
    assert report is not None
    assert report.status == ReportStatus.COMPLETED.value
    assert report.attempts == 2
    assert version_count == 1


async def test_generation_failure_is_retryable_without_partial_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "retry")
    case = source(business_id)
    generator = DeterministicGenerator([RuntimeError("temporary failure"), valid_document()])
    service = GenerateFieldNotesReportService(
        SqlReportUnitOfWorkFactory(session_factory),
        generator,
    )

    with pytest.raises(ReportGenerationFailedError):
        await service.generate(case, now=NOW, stale_before=STALE_BEFORE)
    completed = await service.generate(
        case,
        now=NOW + timedelta(minutes=1),
        stale_before=STALE_BEFORE,
    )

    assert completed.version == 1
    async with session_factory() as session:
        report = await session.get(FieldNoteReport, completed.report_id)
        version_count = await session.scalar(
            select(func.count()).select_from(FieldNoteReportVersionRow)
        )
    assert report is not None
    assert report.attempts == 2
    assert version_count == 1


async def test_changed_source_creates_next_version_and_old_source_replays(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "versioning")
    case_id = uuid4()
    first_source = source(business_id, case_id=case_id)
    second_source = source(
        business_id,
        case_id=case_id,
        transcript="A corrected canonical field observation.",
    )
    generator = DeterministicGenerator([valid_document("Original"), valid_document("Corrected")])
    service = GenerateFieldNotesReportService(
        SqlReportUnitOfWorkFactory(session_factory),
        generator,
    )

    first = await service.generate(first_source, now=NOW, stale_before=STALE_BEFORE)
    second = await service.generate(
        second_source,
        now=NOW + timedelta(minutes=1),
        stale_before=STALE_BEFORE,
    )
    original_replay = await service.generate(
        first_source,
        now=NOW + timedelta(minutes=2),
        stale_before=STALE_BEFORE,
    )

    assert first.version == 1
    assert second.version == 2
    assert first.report_id == second.report_id
    assert original_replay == first
    assert len(generator.requests) == 2
    assert second.report_version_id == field_notes_report_version_id(
        second.report_id,
        2,
        field_note_source_fingerprint(second_source),
    )


async def test_report_identity_and_reads_are_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_business = BusinessId(uuid4())
    second_business = BusinessId(uuid4())
    await seed_business(session_factory, first_business, "tenant-one")
    await seed_business(session_factory, second_business, "tenant-two")
    case_id = uuid4()
    generator = DeterministicGenerator([valid_document("One"), valid_document("Two")])
    service = GenerateFieldNotesReportService(
        SqlReportUnitOfWorkFactory(session_factory),
        generator,
    )

    first = await service.generate(
        source(first_business, case_id=case_id),
        now=NOW,
        stale_before=STALE_BEFORE,
    )
    second = await service.generate(
        source(second_business, case_id=case_id),
        now=NOW,
        stale_before=STALE_BEFORE,
    )

    assert first.report_id != second.report_id
    async with session_factory() as session:
        repository = SqlFieldNotesReportRepository(session)
        assert await repository.get_completed(first_business, first.report_id) == first
        assert await repository.get_completed(second_business, first.report_id) is None


async def test_stale_report_claim_cannot_complete_after_reclaim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    business_id = BusinessId(uuid4())
    await seed_business(session_factory, business_id, "lease-fencing")
    case = source(business_id)
    fingerprint = field_note_source_fingerprint(case)

    async with session_factory() as session:
        repository = SqlFieldNotesReportRepository(session)
        stale_claim = await repository.claim(
            case,
            fingerprint,
            now=NOW,
            stale_before=STALE_BEFORE,
        )
        await session.commit()
    reclaim_time = NOW + timedelta(minutes=10)
    async with session_factory() as session:
        repository = SqlFieldNotesReportRepository(session)
        current_claim = await repository.claim(
            case,
            fingerprint,
            now=reclaim_time,
            stale_before=NOW + timedelta(minutes=5),
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlFieldNotesReportRepository(session)
        with pytest.raises(LostReportLeaseError):
            await repository.complete(
                stale_claim,
                FieldNotesReportDocument.model_validate(valid_document()),
                generated_at=reclaim_time,
            )

    async with session_factory() as session:
        repository = SqlFieldNotesReportRepository(session)
        completed = await repository.complete(
            current_claim,
            FieldNotesReportDocument.model_validate(valid_document()),
            generated_at=reclaim_time,
        )
        await session.commit()
    assert completed.version == 1


def test_report_core_has_no_transport_or_provider_sdk_leakage() -> None:
    root = Path(__file__).parents[1] / "src" / "gvas"
    sources = "\n".join(
        (root / relative).read_text()
        for relative in (
            "domain/reporting.py",
            "application/report_generation.py",
        )
    ).lower()
    for forbidden in ("slack", "twilio", "openai", "anthropic", "bedrock", "sendgrid"):
        assert forbidden not in sources
