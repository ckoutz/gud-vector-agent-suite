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
    handler = EchoHandler()
    router = WorkflowRouter([handler])
    adapter = FakeChannelAdapter()
    messages = [await adapter.translate(transport) for transport in ("slack", "twilio")]
    assert messages[0].model_dump(exclude={"routing"}) == messages[1].model_dump(
        exclude={"routing"}
    )
    selected_handlers = [router.route(item.intent) for item in messages]
    assert selected_handlers[0] is handler
    assert selected_handlers[1] is handler
    run_id = WorkflowRunId(uuid4())
    results = [
        await selected_handler.handle(WorkflowContext(run_id=run_id, message=item))
        for selected_handler, item in zip(selected_handlers, messages, strict=True)
    ]
    assert results[0].model_dump(exclude={"replies": {"__all__": {"routing"}}}) == results[
        1
    ].model_dump(exclude={"replies": {"__all__": {"routing"}}})
