from enum import StrEnum
from typing import NewType, Protocol

from pydantic import BaseModel, ConfigDict, Field

from gvas.domain.completeness import ChecklistKey
from gvas.domain.identifiers import BusinessId, ConversationId

TemplateSetKey = NewType("TemplateSetKey", str)
IndustryKey = NewType("IndustryKey", str)


class TemplateSetStatus(StrEnum):
    """DRAFT resolves only by explicit version; DEPRECATED stays readable."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class TemplateModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TemplateSetRef(TemplateModel):
    """Pin recorded on an in-flight case."""

    business_id: BusinessId
    template_set_key: TemplateSetKey
    version: int = Field(ge=1)


class ReportTemplateRef(TemplateModel):
    """Report template a case was pinned to, carried into report generation."""

    business_id: BusinessId
    report_template_key: str = Field(min_length=1)
    report_template_version: int = Field(ge=1)


class TemplateSet(TemplateModel):
    """Per-business unit of versioning for a note schema and its report template.

    A template set version pins one checklist version, so publishing a new
    checklist version requires publishing a new template-set version. The
    industry is an attribute rather than a resolution axis: a business working in
    two industries has two template-set keys.
    """

    business_id: BusinessId
    template_set_key: TemplateSetKey
    version: int = Field(ge=1)
    industry_key: IndustryKey
    status: TemplateSetStatus
    checklist_key: ChecklistKey
    checklist_version: int = Field(ge=1)
    report_template_key: str = Field(min_length=1)
    report_template_version: int = Field(ge=1)

    @property
    def ref(self) -> TemplateSetRef:
        return TemplateSetRef(
            business_id=self.business_id,
            template_set_key=self.template_set_key,
            version=self.version,
        )

    @property
    def report_template(self) -> ReportTemplateRef:
        return ReportTemplateRef(
            business_id=self.business_id,
            report_template_key=self.report_template_key,
            report_template_version=self.report_template_version,
        )


class BusinessTemplateProfile(TemplateModel):
    """Which template-set key a business resolves, and its seeded fallback.

    `template_set_key` is what onboarding or a later reassignment points at;
    `default_template_set_key` is the key seeded for the business's industry, so
    a business that never published a set of its own still resolves.
    """

    business_id: BusinessId
    industry_key: IndustryKey
    template_set_key: TemplateSetKey
    default_template_set_key: TemplateSetKey


class TemplateResolutionPort(Protocol):
    """Selects the template set for a new case. No provider, no channel."""

    async def resolve_for_new_case(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> TemplateSetRef: ...

    async def load(self, ref: TemplateSetRef) -> TemplateSet: ...


class UnknownTemplateSetError(LookupError):
    """No template set resolves for the business; reviewing would be a guess."""


class TemplateSetVersionConflictError(ValueError):
    """A published template-set version is immutable apart from its status."""
