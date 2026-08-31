from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gvas.domain.identifiers import BusinessId
from gvas.domain.messages import AttachmentReference
from gvas.domain.object_storage import (
    ObjectCustodyError,
    ObjectCustodyRequest,
    StoredObject,
    content_digest,
)
from gvas.domain.plan_repositories import (
    LostPlanSetCopyLeaseError,
    PlanCustodyUnitOfWork,
    PlanSetCopyClaim,
    PlanSetCopyClaimResult,
    PlanSetUploadRegistration,
)
from gvas.domain.plans import (
    DEFAULT_PLAN_SET_KEY,
    PLAN_SET_CUSTODY_SCOPE,
    PlanSetUploadId,
    SiteId,
    SitePlanSetVersion,
    UnknownSiteError,
    plan_set_copy_command,
    plan_set_custody_name,
)
from gvas.domain.ports import AttachmentAccessPort, ObjectStoragePort


class PlanCustodyUnitOfWorkFactory(Protocol):
    def __call__(self) -> PlanCustodyUnitOfWork: ...


class RegisterPlanSetUploadService:
    """Records an owner's plan-set upload and queues the copy into custody.

    The upload row and its outbox command are written in one transaction, so a
    replayed channel event resolves to the same upload identity and the same
    deterministic command instead of a second copy.
    """

    def __init__(self, unit_of_work_factory: PlanCustodyUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def register(
        self,
        business_id: BusinessId,
        site_id: SiteId,
        source: AttachmentReference,
        *,
        plan_set_key: str = DEFAULT_PLAN_SET_KEY,
        now: datetime,
    ) -> PlanSetUploadRegistration:
        async with self._unit_of_work_factory() as unit_of_work:
            site = await unit_of_work.sites.get(business_id, site_id)
            if site is None:
                await unit_of_work.rollback()
                raise UnknownSiteError("site does not belong to this business")
            plan_set = await unit_of_work.plan_sets.get_or_create(
                business_id, site_id, plan_set_key, now=now
            )
            registration = await unit_of_work.plan_set_uploads.register(
                business_id, site_id, plan_set.plan_set_id, source, now=now
            )
            await unit_of_work.outbox.enqueue(
                plan_set_copy_command(business_id, registration.upload.upload_id)
            )
            await unit_of_work.commit()
        return registration


class PlanSetCustodyOutcome(StrEnum):
    STORED = "stored"
    ALREADY_STORED = "already_stored"
    BUSY = "busy"
    MISSING = "missing"
    FAILED = "failed"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class PlanSetCustodyReport:
    outcome: PlanSetCustodyOutcome
    upload_id: PlanSetUploadId | None = None
    version: SitePlanSetVersion | None = None
    attempts: int = 0
    detail: str | None = None


class CopyPlanSetIntoCustodyService:
    """Copies a source-channel plan set into managed storage (D7b).

    The claim is leased and fenced, and both the stored object and the
    ``SitePlanSetVersion`` are content-addressed, so replaying the command
    produces neither a second object nor a second version.
    """

    def __init__(
        self,
        unit_of_work_factory: PlanCustodyUnitOfWorkFactory,
        attachments: AttachmentAccessPort,
        storage: ObjectStoragePort,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._attachments = attachments
        self._storage = storage

    async def copy(
        self,
        business_id: BusinessId,
        upload_id: PlanSetUploadId,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> PlanSetCustodyReport:
        async with self._unit_of_work_factory() as unit_of_work:
            claim = await unit_of_work.plan_set_uploads.claim(
                business_id, upload_id, now=now, stale_before=stale_before
            )
            await unit_of_work.commit()

        if claim.result is PlanSetCopyClaimResult.MISSING:
            return PlanSetCustodyReport(PlanSetCustodyOutcome.MISSING)
        if claim.result is PlanSetCopyClaimResult.TERMINAL:
            return PlanSetCustodyReport(
                PlanSetCustodyOutcome.ALREADY_STORED,
                upload_id=claim.upload_id,
                version=claim.stored_version,
                attempts=claim.attempts,
            )
        if claim.result is PlanSetCopyClaimResult.BUSY:
            return PlanSetCustodyReport(
                PlanSetCustodyOutcome.BUSY,
                upload_id=claim.upload_id,
                attempts=claim.attempts,
            )
        if claim.source is None or claim.plan_set_id is None or claim.upload_id is None:
            return PlanSetCustodyReport(
                PlanSetCustodyOutcome.LEASE_LOST, detail="acquired claim is incomplete"
            )

        try:
            stored = await self._take_custody(claim)
        except Exception as error:
            detail = repr(error)
            lease_lost = await self._record_failure(claim, detail)
            outcome = (
                PlanSetCustodyOutcome.LEASE_LOST if lease_lost else PlanSetCustodyOutcome.FAILED
            )
            return PlanSetCustodyReport(
                outcome,
                upload_id=claim.upload_id,
                attempts=claim.attempts,
                detail=detail,
            )

        async with self._unit_of_work_factory() as unit_of_work:
            try:
                version = await unit_of_work.plan_set_uploads.record_stored(
                    claim, stored, uploaded_at=now
                )
                await unit_of_work.commit()
            except LostPlanSetCopyLeaseError:
                await unit_of_work.rollback()
                return PlanSetCustodyReport(
                    PlanSetCustodyOutcome.LEASE_LOST,
                    upload_id=claim.upload_id,
                    attempts=claim.attempts,
                )
        return PlanSetCustodyReport(
            PlanSetCustodyOutcome.STORED,
            upload_id=claim.upload_id,
            version=version,
            attempts=claim.attempts,
        )

    async def _take_custody(self, claim: PlanSetCopyClaim) -> StoredObject:
        source = claim.source
        plan_set_id = claim.plan_set_id
        business_id = claim.business_id
        if source is None or plan_set_id is None or business_id is None:
            raise ObjectCustodyError("plan-set copy claim is incomplete")
        payload = await self._attachments.fetch(source)
        digest = content_digest(payload.content)
        stored = await self._storage.put(
            ObjectCustodyRequest(
                business_id=business_id,
                scope=PLAN_SET_CUSTODY_SCOPE,
                name=plan_set_custody_name(plan_set_id, digest),
                content=payload.content,
                media_kind=source.media_kind,
                media_type=payload.mime_type or source.mime_type,
                filename=payload.filename or source.filename,
            )
        )
        if stored.content_digest != digest or stored.byte_size != len(payload.content):
            raise ObjectCustodyError("stored object does not match the fetched source content")
        return stored

    async def _record_failure(self, claim: PlanSetCopyClaim, error: str) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            try:
                await unit_of_work.plan_set_uploads.record_failure(claim, error)
                await unit_of_work.commit()
            except LostPlanSetCopyLeaseError:
                await unit_of_work.rollback()
                return True
        return False
