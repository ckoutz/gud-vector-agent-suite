from collections.abc import Mapping
from typing import NewType
from uuid import UUID

BusinessId = NewType("BusinessId", UUID)
ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
WorkflowRunId = NewType("WorkflowRunId", UUID)
OutboxCommandId = NewType("OutboxCommandId", UUID)
EndpointId = NewType("EndpointId", UUID)
MessageKey = NewType("MessageKey", str)
WorkflowIntent = NewType("WorkflowIntent", str)

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
RoutingData = Mapping[str, JsonValue]
