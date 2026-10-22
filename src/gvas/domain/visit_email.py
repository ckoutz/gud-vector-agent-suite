"""A visit e-mail with its calendar invite attached (confirmation, change,
cancellation, reminder), queued through the outbox like every send."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.identifiers import BusinessId, OutboxCommandId
from gvas.domain.messages import DeliveryReceipt
from gvas.domain.outbox import OutboxCommand

VISIT_EMAIL_COMMAND_TYPE = "scheduling.visit_email"
_NAMESPACE = UUID("6f1f3c0e-6a43-4d55-9c0b-0f3d7a9b2c11")


class VisitEmail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    business_id: BusinessId
    to: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    #: The iCalendar text; absent for a plain reminder.
    invite: str | None = None
    cancelled: bool = False
    idempotency_key: str = Field(min_length=1)


class VisitEmailPort(Protocol):
    async def send(self, email: VisitEmail) -> DeliveryReceipt: ...


def visit_email_command(email: VisitEmail) -> OutboxCommand:
    return OutboxCommand(
        command_id=OutboxCommandId(uuid5(_NAMESPACE, email.idempotency_key)),
        business_id=email.business_id,
        command_type=VISIT_EMAIL_COMMAND_TYPE,
        payload=email.model_dump(mode="json", exclude={"business_id"}),
        dedup_key=f"visit_email:{email.idempotency_key}",
    )


def visit_email_request(business_id: BusinessId, payload: Mapping[str, object]) -> VisitEmail:
    return VisitEmail.model_validate({**payload, "business_id": business_id})
