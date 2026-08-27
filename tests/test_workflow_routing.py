from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from gvas.domain.enums import WorkflowRunStatus
from gvas.domain.identifiers import BusinessId, MessageKey, WorkflowIntent, WorkflowRunId
from gvas.domain.messages import InboundOwnerMessage, OutboundOwnerMessage, TextPart
from gvas.domain.workflows import WorkflowContext, WorkflowResult, WorkflowRouter


class EchoHandler:
    intent = WorkflowIntent("echo")

    async def handle(self, context: WorkflowContext) -> WorkflowResult:
        return WorkflowResult(
            status=WorkflowRunStatus.SUCCEEDED,
            replies=[
                OutboundOwnerMessage(
                    business_id=context.message.business_id,
                    conversation_ref=context.message.conversation_ref,
                    parts=[
                        TextPart(text=context.message.parts[0].text)
                        if isinstance(context.message.parts[0], TextPart)
                        else context.message.parts[0]
                    ],
                    correlation_id="correlation",
                    routing=context.message.routing,
                )
            ],
        )


def fixture(transport: str) -> InboundOwnerMessage:
    business_id = BusinessId(UUID("00000000-0000-0000-0000-000000000001"))
    return InboundOwnerMessage(
        message_key=MessageKey("stable"),
        business_id=business_id,
        conversation_ref={
            "business_id": business_id,
            "external_conversation_id": "same",
        },
        sender={"external_id": "owner", "role": "owner"},
        received_at=datetime(2025, 1, 1, tzinfo=UTC),
        parts=[{"kind": "text", "text": "same content"}],
        intent=WorkflowIntent("echo"),
        routing={"transport": transport, "opaque": {"value": 1}},
    )


class FakeChannelAdapter:
    async def translate(self, transport: str) -> InboundOwnerMessage:
        return fixture(transport)


@pytest.mark.asyncio
async def test_routing_ignores_transport_label() -> None:
    router = WorkflowRouter([EchoHandler()])
    adapter = FakeChannelAdapter()
    messages = [await adapter.translate(transport) for transport in ("slack", "twilio")]
    results = [
        await router.route(item.intent).handle(
            WorkflowContext(run_id=WorkflowRunId(uuid4()), message=item)
        )
        for item in messages
    ]
    assert results[0].replies[0].model_copy(update={"routing": {}}).model_dump(
        exclude={"routing"}
    ) == results[1].replies[0].model_copy(update={"routing": {}}).model_dump(exclude={"routing"})
    assert results[0].status == results[1].status
