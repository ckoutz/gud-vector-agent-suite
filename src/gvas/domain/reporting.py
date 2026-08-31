import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gvas.domain.completeness import CompletenessChecklist
from gvas.domain.identifiers import BusinessId, JsonValue, OutboxCommandId
from gvas.domain.outbox import OutboxCommand
from gvas.domain.templates import ReportTemplateDefinition

REPORT_SCHEMA_VERSION: Literal["field-notes-report/v1"] = "field-notes-report/v1"
_REPORT_NAMESPACE = UUID("7281d38a-7bbb-5d4b-bbf7-adb9de54dcf8")
_REPORT_VERSION_NAMESPACE = UUID("58c1274a-bf3f-5b52-926b-abd5d4113cd3")
FIELD_NOTES_REPORT_COMMAND_TYPE = "field_notes_report.generate"
FIELD_NOTES_REPORT_COMMAND_NAMESPACE = UUID("c1d9a6f2-3b47-5e81-9a2c-6d4f8b0e7315")


class ReportDomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldNoteCaseStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ChecklistOutcome(StrEnum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


class ReportStatus(StrEnum):
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportClaimResult(StrEnum):
    ACQUIRED = "acquired"
    TERMINAL = "terminal"
    BUSY = "busy"


class EvidenceSource(StrEnum):
    TRANSCRIPT = "transcript"
    CHECKLIST = "checklist"
    ANSWER = "answer"


class ChecklistEvidence(ReportDomainModel):
    item_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    prompt: str = Field(min_length=1)
    outcome: ChecklistOutcome
    evidence: tuple[str, ...] = Field(default_factory=tuple)


class CorrelatedAnswer(ReportDomainModel):
    question_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class FieldNoteCaseSnapshot(ReportDomainModel):
    business_id: BusinessId
    case_id: UUID
    status: FieldNoteCaseStatus
    completed_at: datetime | None = None
    canonical_transcript: str = Field(min_length=1)
    checklist_evidence: tuple[ChecklistEvidence, ...] = Field(default_factory=tuple)
    correlated_answers: tuple[CorrelatedAnswer, ...] = Field(default_factory=tuple)
    report_template_key: str | None = Field(default=None, min_length=1)
    report_template_version: int | None = Field(default=None, ge=1)

    @field_validator("completed_at")
    @classmethod
    def completed_at_is_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)

    @model_validator(mode="after")
    def validate_correlations(self) -> "FieldNoteCaseSnapshot":
        checklist_keys = [item.item_key for item in self.checklist_evidence]
        answer_keys = [answer.question_key for answer in self.correlated_answers]
        if len(checklist_keys) != len(set(checklist_keys)):
            raise ValueError("checklist item keys must be unique")
        if len(answer_keys) != len(set(answer_keys)):
            raise ValueError("answer question keys must be unique")
        if self.status is FieldNoteCaseStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed field-note cases require completed_at")
        if (self.report_template_key is None) != (self.report_template_version is None):
            raise ValueError("report template pins need both a key and a version")
        return self


class ReportEvidenceReference(ReportDomainModel):
    source: EvidenceSource
    key: str = Field(min_length=1)


class ReportBlock(ReportDomainModel):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1)
    evidence_refs: tuple[ReportEvidenceReference, ...] = Field(default_factory=tuple)


class ReportSection(ReportDomainModel):
    section_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    heading: str = Field(min_length=1)
    blocks: tuple[ReportBlock, ...] = Field(min_length=1)


class FieldNotesReportDocument(ReportDomainModel):
    schema_version: Literal["field-notes-report/v1"] = REPORT_SCHEMA_VERSION
    title: str = Field(min_length=1)
    sections: tuple[ReportSection, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def section_keys_are_unique(self) -> "FieldNotesReportDocument":
        section_keys = [section.section_key for section in self.sections]
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("report section keys must be unique")
        return self

    def validate_evidence_against(self, source: FieldNoteCaseSnapshot) -> None:
        valid_keys = {
            EvidenceSource.TRANSCRIPT: {"canonical"},
            EvidenceSource.CHECKLIST: {item.item_key for item in source.checklist_evidence},
            EvidenceSource.ANSWER: {answer.question_key for answer in source.correlated_answers},
        }
        for section in self.sections:
            for block in section.blocks:
                for reference in block.evidence_refs:
                    if reference.key not in valid_keys[reference.source]:
                        raise ValueError(
                            f"unknown {reference.source.value} evidence key: {reference.key}"
                        )

    def validate_against_template(self, template: ReportTemplateDefinition) -> None:
        """Reject a document whose structure is not the one the business configured.

        Without this the pinned definition would be advisory: a generator could
        invent headings or file an item's evidence under a section that does not
        bind it, and the report would stop being reproducible from the template.
        """
        if self.title != template.title:
            raise ValueError(
                f"report title {self.title!r} does not match the configured "
                f"title {template.title!r}"
            )
        expected_keys = [section.section_key for section in template.sections]
        actual_keys = [section.section_key for section in self.sections]
        if actual_keys != expected_keys:
            raise ValueError(
                f"report sections {actual_keys} do not match the configured "
                f"sections {expected_keys}"
            )
        for section, configured in zip(self.sections, template.sections, strict=True):
            if section.heading != configured.heading:
                raise ValueError(
                    f"section {section.section_key} heading {section.heading!r} does not "
                    f"match the configured heading {configured.heading!r}"
                )
            bound = set(configured.checklist_item_keys)
            for block in section.blocks:
                for reference in block.evidence_refs:
                    if reference.source is EvidenceSource.TRANSCRIPT:
                        continue
                    if reference.key not in bound:
                        raise ValueError(
                            f"section {section.section_key} cites {reference.source.value} "
                            f"evidence {reference.key} it does not bind"
                        )


class ChecklistEvidenceRequest(ReportDomainModel):
    """Inputs for attributing a completed review's checklist items to evidence."""

    business_id: BusinessId
    case_id: UUID
    checklist: CompletenessChecklist
    canonical_transcript: str = Field(min_length=1)
    correlated_answers: tuple[CorrelatedAnswer, ...] = Field(default_factory=tuple)


class ReportGenerationRequest(ReportDomainModel):
    report_id: UUID
    report_version: int = Field(ge=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    source: FieldNoteCaseSnapshot
    report_template: ReportTemplateDefinition

    @model_validator(mode="after")
    def report_template_matches_pin(self) -> "ReportGenerationRequest":
        """The generator renders the case's pinned schema, never the active one."""
        if self.report_template.business_id != self.source.business_id:
            raise ValueError("report template must belong to the case's business")
        pinned_key = self.source.report_template_key
        pinned_version = self.source.report_template_version
        if pinned_key is None or pinned_version is None:
            return self
        if (
            self.report_template.report_template_key != pinned_key
            or self.report_template.version != pinned_version
        ):
            raise ValueError("report template does not match the case's pinned version")
        return self


class FieldNotesReportVersion(ReportDomainModel):
    report_id: UUID
    report_version_id: UUID
    business_id: BusinessId
    case_id: UUID
    version: int = Field(ge=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    generated_at: datetime
    document: FieldNotesReportDocument

    _generated_at_aware = field_validator("generated_at")(_aware)


class ReportGenerationClaim(ReportDomainModel):
    result: ReportClaimResult
    report_id: UUID
    business_id: BusinessId
    case_id: UUID
    status: ReportStatus
    attempts: int = Field(ge=1)
    report_version: int = Field(ge=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    lease_token: UUID | None = None
    completed_version: FieldNotesReportVersion | None = None

    @model_validator(mode="after")
    def validate_result_payload(self) -> "ReportGenerationClaim":
        acquired = self.result is ReportClaimResult.ACQUIRED
        terminal = self.result is ReportClaimResult.TERMINAL
        if acquired != (self.lease_token is not None):
            raise ValueError("only acquired report claims may carry a lease token")
        if terminal != (self.completed_version is not None):
            raise ValueError("only terminal report claims carry a completed version")
        return self


class IncompleteFieldNoteCaseError(ValueError):
    pass


class ReportGenerationBusyError(RuntimeError):
    pass


class ReportGenerationFailedError(RuntimeError):
    pass


class MalformedGeneratedReportError(ValueError):
    pass


class LostReportLeaseError(ValueError):
    pass


class ReportGenerationPort(Protocol):
    async def generate(self, request: ReportGenerationRequest) -> dict[str, JsonValue]: ...


class FieldNotesReportRepository(Protocol):
    async def claim(
        self,
        source: FieldNoteCaseSnapshot,
        source_fingerprint: str,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> ReportGenerationClaim: ...

    async def complete(
        self,
        claim: ReportGenerationClaim,
        document: FieldNotesReportDocument,
        *,
        generated_at: datetime,
    ) -> FieldNotesReportVersion: ...

    async def fail(
        self, claim: ReportGenerationClaim, error: str, *, failed_at: datetime
    ) -> None: ...

    async def get_completed(
        self, business_id: BusinessId, report_id: UUID
    ) -> FieldNotesReportVersion | None: ...


class ReportUnitOfWork(Protocol):
    reports: FieldNotesReportRepository

    async def __aenter__(self) -> "ReportUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...


def field_notes_report_id(business_id: BusinessId, case_id: UUID) -> UUID:
    return uuid5(_REPORT_NAMESPACE, f"{business_id}:{case_id}")


def field_notes_report_version_id(report_id: UUID, version: int, source_fingerprint: str) -> UUID:
    return uuid5(_REPORT_VERSION_NAMESPACE, f"{report_id}:{version}:{source_fingerprint}")


def field_notes_report_command(
    business_id: BusinessId,
    case_id: UUID,
    review_id: UUID,
    completed_at: datetime,
) -> OutboxCommand:
    """Requests one report per completed review revision of a field-note case.

    The command identity is the reviewed revision, so recovery and replay of the
    same completed review reuse one command and one report version, while notes
    added to an open case are reviewed again and produce the next version.
    ``completed_at`` is persisted in the command payload so retries reuse the
    same snapshot fingerprint.
    """

    return OutboxCommand(
        command_id=OutboxCommandId(
            uuid5(FIELD_NOTES_REPORT_COMMAND_NAMESPACE, f"{business_id}:{case_id}:{review_id}")
        ),
        business_id=business_id,
        command_type=FIELD_NOTES_REPORT_COMMAND_TYPE,
        payload={
            "field_note_case_id": str(case_id),
            "field_note_review_id": str(review_id),
            "completed_at": _aware(completed_at).isoformat(),
        },
        dedup_key=f"field_notes_report:{case_id}:{review_id}",
    )


def field_note_source_fingerprint(source: FieldNoteCaseSnapshot) -> str:
    canonical = json.dumps(
        source.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
