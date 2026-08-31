from datetime import datetime
from typing import Protocol

from gvas.application.field_note_transcription import FieldNoteTranscriptService
from gvas.domain.completeness import (
    FieldNoteReviewId,
    FieldNoteReviewStatus,
    UnknownChecklistError,
)
from gvas.domain.completeness_repositories import CompletenessUnitOfWork
from gvas.domain.field_notes import FieldNoteCaseId, FieldNoteCaseNotFoundError
from gvas.domain.identifiers import BusinessId
from gvas.domain.ports import ChecklistEvidencePort
from gvas.domain.reporting import (
    ChecklistEvidenceRequest,
    CorrelatedAnswer,
    FieldNoteCaseSnapshot,
    FieldNoteCaseStatus,
)


class IncompleteFieldNoteReviewError(ValueError):
    pass


class UnknownFieldNoteReviewError(LookupError):
    pass


class UnattributedChecklistEvidenceError(ValueError):
    pass


class CompletenessUnitOfWorkFactory(Protocol):
    def __call__(self) -> CompletenessUnitOfWork: ...


class BuildFieldNoteCaseSnapshotService:
    """Assembles the report source for a completed field-note review.

    Checklist evidence is attributed through a provider-neutral port because the
    accepted completeness outcome only reports items that are still missing.
    """

    def __init__(
        self,
        completeness_unit_of_work_factory: CompletenessUnitOfWorkFactory,
        transcripts: FieldNoteTranscriptService,
        evidence: ChecklistEvidencePort,
    ) -> None:
        self._completeness = completeness_unit_of_work_factory
        self._transcripts = transcripts
        self._evidence = evidence

    async def build(
        self,
        business_id: BusinessId,
        case_id: FieldNoteCaseId,
        review_id: FieldNoteReviewId,
        *,
        completed_at: datetime,
    ) -> FieldNoteCaseSnapshot:
        async with self._completeness() as unit_of_work:
            review = await unit_of_work.field_note_reviews.get(business_id, review_id)
            if review is None:
                await unit_of_work.rollback()
                raise UnknownFieldNoteReviewError("field-note review was not found")
            checklist = await unit_of_work.checklists.get(
                business_id, review.checklist_key, review.checklist_version
            )
            if checklist is None:
                await unit_of_work.rollback()
                raise UnknownChecklistError(f"{review.checklist_key} is not configured")
            answers = await unit_of_work.follow_up_questions.answers_for_review(
                business_id, review_id
            )
            await unit_of_work.commit()
        if review.status is not FieldNoteReviewStatus.COMPLETE:
            raise IncompleteFieldNoteReviewError("only completed reviews produce report snapshots")

        transcript = await self._transcripts.canonical_transcript(business_id, case_id)
        if not transcript.text:
            raise FieldNoteCaseNotFoundError("field-note case has no canonical transcript")
        prompts = {item.key: item.prompt for item in checklist.items}
        correlated = tuple(
            CorrelatedAnswer(
                question_key=answer.item_key,
                question=prompts.get(answer.item_key, answer.item_key),
                answer=answer.text,
            )
            for answer in answers
        )
        evidence = await self._evidence.attribute(
            ChecklistEvidenceRequest(
                business_id=business_id,
                case_id=case_id,
                checklist=checklist,
                canonical_transcript=transcript.text,
                correlated_answers=correlated,
            )
        )
        unknown = sorted(item.item_key for item in evidence if item.item_key not in prompts)
        if unknown:
            raise UnattributedChecklistEvidenceError(
                f"evidence reported items outside checklist {review.checklist_key}: {unknown}"
            )
        return FieldNoteCaseSnapshot(
            business_id=business_id,
            case_id=case_id,
            status=FieldNoteCaseStatus.COMPLETED,
            completed_at=completed_at,
            canonical_transcript=transcript.text,
            checklist_evidence=evidence,
            correlated_answers=correlated,
        )
