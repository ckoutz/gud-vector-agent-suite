"""E-mails Güd Vector when owner texts fail on a setup problem."""

from datetime import datetime

from gvas.domain.identifiers import BusinessId
from gvas.domain.owner_email import OwnerEmailRequest
from gvas.domain.ports import OwnerEmailPort


class EmailOwnerAssistantAlerts:
    def __init__(self, email: OwnerEmailPort, to: str) -> None:
        self._email = email
        self._to = to

    async def setup_problem(
        self, business_id: BusinessId, business_name: str, detail: str, at: datetime
    ) -> None:
        text = (
            "Owner texts are failing: the AI service refused Gus's request, so the "
            'owner got the "setup problem on Güd Vector\'s side" reply instead of '
            "an answer.\n\n"
            f"Business: {business_name} ({business_id})\n"
            f"When: {at.isoformat(timespec='seconds')}\n"
            f"What failed: {detail}\n\n"
            "Likely causes: the AI key, model name, account credit, or a request the "
            "provider no longer accepts. Retrying won't help until it's fixed.\n"
            "At most one of these e-mails is sent per hour; every failure is logged "
            'as "ALERT owner assistant setup problem" in the worker logs.'
        )
        await self._email.send(
            OwnerEmailRequest(
                business_id=business_id,
                to=self._to,
                subject=f"Güd Office alert: owner texts failing for {business_name}",
                text=text,
                idempotency_key=f"owner-assistant-setup:{business_id}:{at:%Y%m%d%H%M}",
            )
        )
