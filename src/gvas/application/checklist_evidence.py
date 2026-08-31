from gvas.domain.completeness import ChecklistItemRequirement
from gvas.domain.reporting import (
    ChecklistEvidence,
    ChecklistEvidenceRequest,
    ChecklistOutcome,
)


class MarkerChecklistEvidenceAttributor:
    """Deterministic evidence attribution used until a review provider is selected.

    Evidence comes from the correlated answer for an item, otherwise from the
    configured evidence markers found in the canonical transcript. Markers are
    checklist configuration, so no operational policy is encoded here.
    """

    async def attribute(self, request: ChecklistEvidenceRequest) -> tuple[ChecklistEvidence, ...]:
        answers = {answer.question_key: answer.answer for answer in request.correlated_answers}
        transcript = request.canonical_transcript.casefold()
        attributed: list[ChecklistEvidence] = []
        for item in request.checklist.items:
            answer = answers.get(item.key)
            markers = tuple(
                marker for marker in item.evidence_markers if marker.casefold() in transcript
            )
            evidence: tuple[str, ...]
            if answer is not None:
                outcome = ChecklistOutcome.OBSERVED
                evidence = (answer,)
            elif markers:
                outcome = ChecklistOutcome.OBSERVED
                evidence = markers
            else:
                outcome = (
                    ChecklistOutcome.NOT_OBSERVED
                    if item.requirement is ChecklistItemRequirement.REQUIRED
                    else ChecklistOutcome.NOT_APPLICABLE
                )
                evidence = ()
            attributed.append(
                ChecklistEvidence(
                    item_key=item.key,
                    prompt=item.prompt,
                    outcome=outcome,
                    evidence=evidence,
                )
            )
        return tuple(attributed)
