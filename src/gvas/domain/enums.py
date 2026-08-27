from enum import StrEnum


class SenderRole(StrEnum):
    OWNER = "owner"
    TEAMMATE = "teammate"
    SYSTEM = "system"


class MediaKind(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    VIDEO = "video"
    OTHER = "other"


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    FAILED = "failed"


class RecipientAddressKind(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    LINK = "link"


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
