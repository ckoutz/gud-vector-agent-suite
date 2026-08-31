# Design: per-tenant template sets and site-plan artifacts

Status: **design only**. Nothing here is implemented, and no contract in this
document is accepted as code. Every Pydantic sketch below is a *proposal*
written to be reviewed and revised; none of it is wired into a workflow,
migration, or composition path.

The product decisions D1–D8 below are **accepted by the owner**, with one
exception: D7 is split into a retention decision and a storage-custody decision
(§5, D7), both now accepted. They constrain the implementation phases in §7; the
contract shapes that realize them are still subject to review when each phase
ships.

This design extends what exists today rather than introducing a parallel
abstraction. Read [`docs/composition.md`](composition.md) first: it already
records these two items as open gaps at the neutral boundary (gap 7,
"Per-business templates and plan artifacts").

## 1. What exists today

The pieces this design builds on, as of the current `main`:

| concern | today |
| --- | --- |
| checklist definition | `CompletenessChecklist(business_id, checklist_key, version, items)` in `gvas.domain.completeness`; per-business, keyed, versioned rows behind `ChecklistDefinitionRepository.get(business_id, checklist_key, version=None)` |
| checklist selection | `build_application(..., checklist_key=DEFAULT_CHECKLIST_KEY)`; one key per wired application, chosen outside domain code |
| version pinning | `FieldNoteReviewRecord` persists `checklist_key` **and** `checklist_version`; the snapshot builder re-reads the checklist at that exact version |
| review loop | `FieldNoteCompletenessService` asks one persisted `ASKED` question at a time, correlated by `follow_up_correlation_id(review_id, round_index, item_key)` |
| report source | `FieldNoteCaseSnapshot` (transcript + `ChecklistEvidence` + `CorrelatedAnswer`), fingerprinted by `field_note_source_fingerprint` |
| report document | `FieldNotesReportDocument` (`schema_version="field-notes-report/v1"`), text blocks with `ReportEvidenceReference(source ∈ {transcript, checklist, answer}, key)`, validated by `validate_evidence_against(snapshot)` |
| delivery | every owner-facing message goes through an `outbound_messages` row plus one `owner_reply.deliver` outbox command, delivered by `OwnerReplyPort` |

Two things follow directly and shape everything below:

1. **The tenant-resolution seam already exists**, but is only half used. The
   *data* is per-business and versioned; only the *selection* of which key to
   use is fixed at wiring time. The work is to move selection from
   `build_application`'s argument into a runtime, tenant-scoped resolver — not
   to redesign checklists.
2. **Report evidence validation is closed over a fixed enum.** Any new evidence
   kind (a plan annotation) has to be added to `EvidenceSource` and to
   `FieldNotesReportDocument.validate_evidence_against`, which means a report
   schema version bump. That is the single largest existing-contract impact in
   this document.

## 2. Requirement 1 — multiple businesses, multiple industries

### 2.1 Template set: identity and versioning

Today the only per-tenant artifact is the checklist. A report template, a note
schema, and (later) evidence rules must vary together — a checklist item that a
report section cites has to exist in the same generation. Versioning them
independently makes "which report template was this case rendered with?"
unanswerable after a change.

**Proposal: a `TemplateSet` is the unit of versioning.** It is a per-business,
keyed, versioned row — exactly the shape `CompletenessChecklist` already has —
that *references* a checklist version and carries the report template and
evidence rules alongside it.

```python
# PROPOSED — not implemented, not wired.
TemplateSetKey = NewType("TemplateSetKey", str)
IndustryKey = NewType("IndustryKey", str)

class TemplateSetRef(TemplateModel):
    """Pin recorded on an in-flight case."""

    business_id: BusinessId
    template_set_key: TemplateSetKey
    version: int = Field(ge=1)

class TemplateSetStatus(StrEnum):
    DRAFT = "draft"          # resolvable only by explicit version
    ACTIVE = "active"        # resolvable as "latest" for new cases
    DEPRECATED = "deprecated"  # readable for historical cases, never selected

class TemplateSet(TemplateModel):
    business_id: BusinessId
    template_set_key: TemplateSetKey
    version: int = Field(ge=1)
    industry_key: IndustryKey
    status: TemplateSetStatus
    checklist_key: ChecklistKey
    checklist_version: int = Field(ge=1)
    report_template_key: str = Field(min_length=1)
    report_template_version: int = Field(ge=1)
```

`(business_id, template_set_key, version)` is the primary identity; `version` is
a monotonic integer per key, matching `CompletenessChecklist.version`. The
industry is an attribute of the template set, not a separate resolution axis:
a business in two industries has two template-set keys, which keeps resolution
a single lookup and avoids a `(business, industry)` composite that would have
no meaning for single-industry tenants.

`checklist_version` inside a template set is **required, not nullable**: a
template set version pins its checklist version, so publishing a new checklist
version requires publishing a new template-set version. That is the cost of the
guarantee in §2.2, and it is deliberate.

### 2.2 Pinning an in-flight case

Already solved for checklists, and the same mechanism extends. `FieldNoteReviewRecord`
persists `checklist_key` + `checklist_version` at `get_or_create` time, and
`BuildFieldNoteCaseSnapshotService` re-reads that exact version rather than the
latest. The rule generalizes:

> Resolution happens **once**, at the first write of a case (review creation).
> The resolved `TemplateSetRef` is persisted on the review row. Every later step
> — follow-up rounds, snapshot assembly, report generation, regeneration after a
> failure — reads the pinned reference and never re-resolves.

Concretely this replaces the two columns `checklist_key`/`checklist_version` on
`FieldNoteReviewRecord` with three: `template_set_key`, `template_set_version`,
and the checklist pair derived from it (kept, so the snapshot builder's existing
`checklists.get(business_id, key, version)` call is unchanged). Keeping the
derived pair denormalized is intentional: it means the existing snapshot path
needs no new lookup, and it makes the pin auditable even if a template-set row
is later corrected.

An in-flight case therefore *cannot* be migrated onto a newer template version.
If the owner needs that, the only safe operation is closing the case and opening
a new one; a mid-case template swap would invalidate already-answered follow-up
questions whose `item_key` may no longer exist.

### 2.3 Introducing and deprecating versions

- **Introduce**: insert a new `(key, version+1)` row with `status=DRAFT`, then
  flip it to `ACTIVE` and the prior `ACTIVE` row to `DEPRECATED` in one
  transaction. At most one `ACTIVE` version per `(business_id, key)`, enforced
  by a partial unique index.
- **Resolve for a new case**: the single `ACTIVE` version.
- **Resolve for an existing case**: by explicit `(key, version)`, regardless of
  status. Rows are never deleted or mutated in place.
- **Historical reports** are unaffected by construction: they are already
  persisted as `FieldNotesReportVersion` documents with a
  `source_fingerprint`, so a rendered report never needs its template again.
  Deprecation only affects *future* resolution.
- **Regeneration** of an old case uses its pinned version and so reproduces the
  same document; this is what makes the existing fingerprint-based dedup in
  `FieldNotesReportRepository.claim` still correct.

**D1 — accepted: deprecated template versions are never hard deleted.**
Historical reports must stay reproducible, so a version that has ever been
resolved for a case is retained for at least the lifetime of the business
account. Tradeoff accepted: template rows accumulate; that cost is negligible
relative to losing audit reproducibility.

### 2.4 Runtime resolution by tenant

Today: `build_application(checklist_key=...)`. Proposed: a port, resolved per
case, injected exactly like every other port.

```python
# PROPOSED — not implemented, not wired.
class TemplateResolutionPort(Protocol):
    """Selects the template set for a new case. No provider, no channel."""

    async def resolve_for_new_case(
        self, business_id: BusinessId, conversation_id: ConversationId
    ) -> TemplateSetRef: ...

    async def load(self, ref: TemplateSetRef) -> TemplateSet: ...
```

The resolver lives in composition (like `DeterministicIntentResolver`), reads
per-business rows through a repository, and never branches on channel or
industry names in domain or application code. `checklist_key` disappears from
`build_application`'s signature only once the resolver ships; until then the
argument remains as the single-tenant default path.

**Where the industry actually enters.** Nowhere in code. An industry is a
*seeded set of template rows*. `MarkerCompletenessReviewer` already takes its
markers from the checklist definition precisely so no operational policy lives
in application code, and that property must be preserved.

### 2.5 Onboarding a new industry

What it actually requires, given the above:

1. Author a checklist definition (items, prompts, requirement, evidence
   markers) and `upsert` it — already supported by
   `ChecklistDefinitionRepository`.
2. Author a report template (section keys, headings, which checklist items each
   section cites).
3. Insert one `TemplateSet` row with `status=DRAFT`, promote to `ACTIVE`.
4. Nothing else. No migration, no code change, no deploy.

Steps 1–3 need an authoring surface. There is none today, and the MVP does not
need a UI: a checked-in seed file per industry plus a load command is enough,
and it makes the industry definitions reviewable in git.

**D2 — accepted: copy-on-onboard.** The alternative considered was global,
system-owned catalog rows that a business *references*; the accepted option
copies industry template rows into the business at onboarding. It keeps every
row tenant-scoped (consistent with every existing table's business-scoped
composite keys), lets a business diverge from the catalog without a fork
mechanism, and avoids a cross-tenant read path in the resolver. Tradeoff accepted: a
catalog fix does not propagate to existing tenants; each needs a new version
published. The rejected catalog option is cheaper to maintain across many
identical tenants but introduces the system's first non-tenant-scoped readable
row, which is a real architectural cost.

### 2.6 Defaults when a business has no custom template

Resolution order: business `ACTIVE` template set for the key → the seeded
default industry set → error. The third case must be a hard, loud error
(`UnknownTemplateSetError`), not a silent built-in fallback: silently reviewing
a roofing job against a generic inspection checklist produces a plausible-looking
but wrong report, which is worse than a failure the owner sees.

Under D2 (copy-on-onboard), the middle step collapses: every
business has rows because onboarding created them, and "no template" genuinely
means misconfiguration.

## 3. Requirement 2 — site plans paired with notes

### 3.1 Site and artifact identity

What an owner uploads is not "a plan" — it is a **plan set**: one PDF containing
cover sheets, schedules, life-safety diagrams, floor plans, elevations, and
details. Only some of its pages are plans an annotation can sit on. The contract
therefore separates three things: the site, the immutable uploaded set version,
and the immutable per-page sheet records extracted from it.

```python
# PROPOSED — not implemented, not wired.
SiteId = NewType("SiteId", UUID)
SitePlanSetId = NewType("SitePlanSetId", UUID)
SitePlanSetVersionId = NewType("SitePlanSetVersionId", UUID)
PlanSheetId = NewType("PlanSheetId", UUID)
PlanAnnotationId = NewType("PlanAnnotationId", UUID)

class Site(PlanModel):
    site_id: SiteId
    business_id: BusinessId
    label: str = Field(min_length=1)      # owner's name for the place
    external_ref: str | None = None       # opaque owner-supplied key

class SitePlanSetVersion(PlanModel):
    """One immutable uploaded revision of a plan set (the whole document)."""

    version_id: SitePlanSetVersionId
    plan_set_id: SitePlanSetId
    business_id: BusinessId
    site_id: SiteId
    version: int = Field(ge=1)
    artifact: AttachmentReference       # existing opaque adapter token
    page_count: int = Field(ge=1)
    content_digest: str = Field(min_length=64, max_length=64)
    uploaded_at: datetime
```

`artifact` reuses `AttachmentReference` unchanged — its validator already
forbids URLs and requires an opaque adapter token, which is exactly the
neutrality property a plan file needs. No new attachment concept is introduced.

A field-note case gains an optional `site_id`. It is optional because the
existing intake path (`field notes: ...` with no site context) must keep
working; a case with no site simply has no plan annotations.

### 3.2 Plan-set versioning and immutability

`SitePlanSetVersion` rows are append-only. A re-upload creates `version+1`; it
never mutates a row. Annotations reference a `PlanSheetId` (§3.3), which pins a
single page of a single set version, so:

- A set version that has at least one annotation is frozen by construction —
  there is no update path to freeze.
- Uploading a newer set does **not** invalidate, migrate, or re-project existing
  annotations. They stay attached to the sheet they were made against, and a
  report generated from an old case renders against that old sheet.
- `content_digest` makes a re-upload of a byte-identical file detectable, so an
  accidental duplicate upload can return the existing version instead of
  creating a phantom one. This is the same idempotency shape as
  `field_note_source_fingerprint`.

**Not designed here, deliberately:** carrying annotations forward from version
*n* to *n+1*, even when the sheet number is unchanged. That requires geometric
registration between two documents and is open-ended; the honest MVP answer is
that annotations do not migrate.

### 3.3 Sheet discovery, extraction, and classification

Grounded in the sample set the owner supplied (`Kinder's Phase 2C REV1 B01`,
26 pages), inspected read-only for this design:

- Every page is 3024 × 2160 pt, unrotated, and produced by Bluebeam Stapler
  from vector CAD output — it is a **text/vector** set, not a scan. Text
  extraction works; OCR is not required for this class of file.
- The title block sits in a fixed band at the right edge of every page. The
  sheet number is a single large run (≈50 pt) at the bottom of that band, and
  the sheet title is the run stack immediately above it. Extracted verbatim:

  | page (1-based) | sheet number | sheet title |
  | --- | --- | --- |
  | 1–2 | `2C-AN-1.0`, `2C-AN-1.1` | project information, green building checklist |
  | 3–4 | `2C-AN-4.0`, `2C-AN-4.1` | fourth floor exiting diagram (full floor / southeast) |
  | 5–6 | `2C-AN-5.1`, `2C-AN-5.2` | door and hardware schedules, finishes |
  | 7 | `2C-4A-0.0` | fourth floor demolition plan — phase 2C |
  | 8 | `2C-4A-0.1` | fourth floor demolition reflected ceiling plan — phase 2C |
  | 9 | `2C-4A-1.0` | fourth floor partition plan — phase 2C |
  | 10 | `2C-4A-2.0` | fourth floor power and signal plan — phase 2C |
  | 11 | `2C-4A-3.0` | fourth floor reflected ceiling plan — phase 2C |
  | 12 | `2C-4A-4.0` | fourth floor finish plan — phase 2C |
  | 13 | `4A-6.0` | enlarged beer garden views |
  | 14–16 | `4A-7.0`–`4A-7.2` | interior elevations |
  | 17–26 | `A-8.0`–`A-8.9` | typical framing, ceiling, partition, millwork details |

  Note pages 13+ drop the `2C-` prefix: sheet numbering is **not** uniform even
  within one set, which is exactly why the sheet number must be stored as
  extracted text and never parsed into meaning.
- The set also carries a delta/revision table and an issue date (`2/24/2026`)
  in the same title block, and the file name carries `REV1`. Revision belongs on
  the sheet record, not derived from the file name.

So the design adds an explicit discovery stage between upload and annotation:

```text
upload plan set version
  -> plan_set.extract   (leased outbox command, one per set version)
       per page: page size, rotation, title-block text, digest of page content
  -> PlanSheet rows, immutable, one per source page
  -> classification: is this page an annotatable plan, and of what kind?
       confident      -> sheet is selectable for annotation
       uncertain      -> owner confirms through the existing follow-up loop
  -> annotations may only reference a sheet marked annotatable
```

```python
# PROPOSED — not implemented, not wired.
class PlanSheetKind(StrEnum):
    FLOOR_PLAN = "floor_plan"
    DEMOLITION_PLAN = "demolition_plan"
    REFLECTED_CEILING_PLAN = "reflected_ceiling_plan"
    FINISH_PLAN = "finish_plan"
    POWER_SIGNAL_PLAN = "power_signal_plan"
    LIFE_SAFETY_PLAN = "life_safety_plan"
    ENLARGED_PLAN = "enlarged_plan"
    ELEVATION = "elevation"
    DETAIL = "detail"
    SCHEDULE = "schedule"
    COVER = "cover"
    UNKNOWN = "unknown"

class ExtractionMethod(StrEnum):
    EMBEDDED_TEXT = "embedded_text"   # vector/text PDF, as in the sample
    RASTER = "raster"                 # image-only page; no title-block text

class PageFrame(PlanModel):
    """The source page's own coordinate frame, kept so normalized
    coordinates remain interpretable without re-reading the file."""

    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation_degrees: Literal[0, 90, 180, 270] = 0

class PlanSheet(PlanModel):
    """One immutable extracted page of one plan-set version."""

    sheet_id: PlanSheetId
    business_id: BusinessId
    site_id: SiteId
    plan_set_version_id: SitePlanSetVersionId
    source_page_index: int = Field(ge=0)          # 0-based, as stored
    sheet_number: str | None = None               # "2C-4A-1.0", verbatim
    sheet_title: str | None = None                # "FOURTH FLOOR PARTITION PLAN"
    revision: str | None = None                   # "REV1" / delta, verbatim
    issued_on: date | None = None
    frame: PageFrame
    extraction_method: ExtractionMethod
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    kind: PlanSheetKind
    classification_confidence: float = Field(ge=0.0, le=1.0)
    owner_confirmed: bool = False
    annotatable: bool = False
```

Rules that fall out of the sample:

- `source_page_index` is authoritative and never renumbered. `sheet_number` is
  what a human recognizes but is not unique across a business, not sortable, and
  sometimes absent — it is a label, not a key.
- Coordinates are normalized **in the source page frame** (§3.4), and `frame`
  is stored on the sheet so a later renderer or a rotated page cannot
  reinterpret an existing annotation.
- `annotatable` is a stored decision, not a derived predicate: it is set when
  classification is confident, or when the owner confirms. An annotation may
  only reference a sheet with `annotatable=True`, which keeps observations off
  schedules and detail sheets.
- `owner_confirmed` sheets are never re-classified. Re-running extraction on the
  same set version must be idempotent — the command is keyed on the version and
  fenced by a lease, and rows are get-or-create by
  `(plan_set_version_id, source_page_index)`.

Both stages are neutral ports; no provider or library is chosen here, and the
choice is not implied by the sketch:

```python
# PROPOSED — not implemented, not wired.
class PlanSheetExtractionPort(Protocol):
    """Reads structural page facts out of a plan-set artifact."""

    async def extract(
        self, request: PlanSheetExtractionRequest
    ) -> tuple[ExtractedPlanSheet, ...]: ...

class PlanSheetClassificationPort(Protocol):
    """Decides what each extracted page is. May be rules, a model, or both."""

    async def classify(
        self, request: PlanSheetClassificationRequest
    ) -> tuple[PlanSheetClassification, ...]: ...
```

The sample is favourable — a rules-only classifier keyed on title text would
likely handle it — but the port must not assume that. A raster-only set has no
title-block text at all, in which case extraction returns `RASTER` with low
confidence and every page goes to owner confirmation.

### 3.4 Annotation model

```python
# PROPOSED — not implemented, not wired.
class AnnotationKind(StrEnum):
    OBSERVATION = "observation"     # something the owner described
    DEFICIENCY = "deficiency"       # a problem to fix
    MEASUREMENT = "measurement"
    LOCATION_MARK = "location_mark"  # "this is where X is"

class AnnotationShape(StrEnum):
    POINT = "point"
    RECTANGLE = "rectangle"
    POLYGON = "polygon"

class PlanCoordinate(PlanModel):
    """Page-relative, origin top-left, unitless in [0, 1]."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)

class PlanRegion(PlanModel):
    """Normalized in the source page frame recorded on the sheet."""

    shape: AnnotationShape
    points: tuple[PlanCoordinate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def points_match_shape(self) -> "PlanRegion":
        expected = {AnnotationShape.POINT: 1, AnnotationShape.RECTANGLE: 2}
        required = expected.get(self.shape)
        if required is not None and len(self.points) != required:
            raise ValueError(f"{self.shape} requires exactly {required} points")
        if self.shape is AnnotationShape.POLYGON and len(self.points) < 3:
            raise ValueError("polygon regions require at least three points")
        return self

class AnnotationConfidence(StrEnum):
    OWNER_CONFIRMED = "owner_confirmed"
    INFERRED = "inferred"

class PlanAnnotation(PlanModel):
    annotation_id: PlanAnnotationId
    business_id: BusinessId
    site_id: SiteId
    sheet_id: PlanSheetId          # pins set version + source page + frame
    case_id: FieldNoteCaseId
    kind: AnnotationKind
    region: PlanRegion
    text: str = Field(min_length=1)
    confidence: AnnotationConfidence
    evidence_refs: tuple[ReportEvidenceReference, ...] = Field(min_length=1)
```

The region carries no page identity of its own: `sheet_id` already pins the set
version, the source page index, and the frame the coordinates are normalized
against. That is deliberate — duplicating `page_index` on the region would let a
region and its sheet disagree.

### 3.5 Bidirectional evidence links

`evidence_refs` on an annotation is `min_length=1` — **an annotation with no
supporting evidence cannot exist**. It reuses the existing
`ReportEvidenceReference` type verbatim, so an annotation points back at a
transcript span, a checklist item, or a correlated answer using the same keys
the report already validates against.

The reverse direction (evidence → annotations) is a query, not a stored field:
annotations are indexed by `(business_id, case_id)`, so the snapshot builder can
load them all for a case and group by referenced key. Storing the reverse link
would create two sources of truth for one relationship.

This is what lets evidence validation extend cleanly. Today:

```python
valid_keys = {
    EvidenceSource.TRANSCRIPT: {"canonical"},
    EvidenceSource.CHECKLIST: {item.item_key for item in source.checklist_evidence},
    EvidenceSource.ANSWER: {answer.question_key for answer in source.correlated_answers},
}
```

Proposed: `FieldNoteCaseSnapshot` gains
`plan_annotations: tuple[PlanAnnotationSummary, ...]`, `EvidenceSource` gains
`PLAN_ANNOTATION = "plan_annotation"`, and the dict gains a fourth entry keyed
by annotation ID. Two validations are then required, and the second is new:

1. Report blocks may only cite annotation IDs present in the snapshot (the
   existing rule, extended).
2. Every annotation's own `evidence_refs` must resolve against the *other three*
   sources in the same snapshot — an annotation may not cite an annotation, so
   the evidence graph stays acyclic and one validation pass is sufficient.

Because `plan_annotations` enters `FieldNoteCaseSnapshot`, it enters
`field_note_source_fingerprint`. Adding or confirming an annotation therefore
changes the fingerprint and legitimately produces a new report version, which is
the correct behavior and needs no new machinery.

### 3.6 How annotations surface in the report

`ReportBlock.kind` is `Literal["text"]` today. The proposal adds a second block
kind rather than encoding plan references in prose:

```python
# PROPOSED — not implemented, not wired.
class PlanReferenceBlock(ReportDomainModel):
    kind: Literal["plan_reference"] = "plan_reference"
    sheet_id: PlanSheetId
    annotation_ids: tuple[PlanAnnotationId, ...] = Field(min_length=1)
    caption: str = Field(min_length=1)
    evidence_refs: tuple[ReportEvidenceReference, ...] = Field(default_factory=tuple)

ReportBlockUnion = Annotated[TextBlock | PlanReferenceBlock, Field(discriminator="kind")]
```

The block references annotations; it does not embed an image. Whether the
consumer draws them is a rendering concern (§5, D5), and keeping the document
free of rendered bytes preserves its current property of being a small,
diffable, fingerprint-stable JSON structure.

This is a **breaking change to `field-notes-report/v1`**: a discriminated union
where a bare model used to be. It requires `REPORT_SCHEMA_VERSION` to move to
`field-notes-report/v2`, with v1 documents readable unchanged (they are
persisted rows, never re-validated against the new model).

### 3.7 How a spoken note references a location at all

This is the hardest part and it is a *conversation* problem, not a geometry
problem. An owner says "the back corner by the loading dock"; the plan says
"Zone 4 / Grid C-7". Nothing bridges those automatically with any reliability.

Proposed flow, which reuses the existing follow-up loop rather than inventing a
second interaction channel:

```text
transcript segment mentions a location
  -> location-candidate extraction (a port, no provider in domain)
  -> candidate matched against labels on the pinned annotatable sheets
       high confidence + unique match -> INFERRED annotation
       ambiguous / no match           -> a follow-up question
  -> the question is a normal FollowUpQuestionRecord in the same review
  -> asked one at a time, through the same OwnerReplyPort and outbox
  -> the answer is a CorrelatedAnswer, and becomes the annotation's evidence
  -> annotation recorded with confidence = OWNER_CONFIRMED
```

The important property: **a location question is not a new mechanism.** It is a
follow-up question whose `item_key` names a location slot instead of a checklist
item, so the existing one-question-at-a-time invariant, the deterministic
`follow_up_correlation_id`, the duplicate-reply handling, and the round
bookkeeping all apply unchanged.

Two consequences worth naming:

- `_validate_outcome` currently rejects any missing item not in the checklist.
  Location slots would violate that. The clean fix is for location slots to be
  *real checklist items* declared by the template set (requirement `OPTIONAL`,
  with a marker set), not a new escape hatch — which is another reason template
  sets and plans are one design and not two.
- Asking a location question costs a round trip. A template set should be able
  to cap location questions per case, or the loop will interrogate an owner who
  mentioned six rooms.

**D3 — accepted: an unresolvable location does not block the report.** The
observation is recorded as a normal text block with no annotation. A field
report that arrives with one unplaced note is more useful than one that never
arrives.

## 4. Editable DOCX output

The owner-facing artifact of a field-note case is an **editable Word document**,
not a PDF and not JSON: the owner expects to open it, adjust wording, and send
it on. The report document (`FieldNotesReportDocument`) stays the canonical,
fingerprinted, evidence-validated structure; DOCX is a *projection* of it. This
section defines only the boundary a later implementation task consumes.

### 4.1 A template set pins an immutable DOCX asset

A DOCX template is a binary file an owner (or an operator on their behalf)
supplies. It must be versioned and immutable for the same reason checklist
versions are: a report generated in March must still be reproducible in
September after the letterhead changed.

```python
# PROPOSED — not implemented, not wired.
class TemplateAssetFormat(StrEnum):
    DOCX = "docx"

class TemplateAsset(TemplateModel):
    """An immutable binary template pinned by a template-set version."""

    business_id: BusinessId
    asset_key: str = Field(min_length=1)
    version: int = Field(ge=1)
    reference: AttachmentReference        # opaque durable locator, never a URL
    content_digest: str = Field(min_length=64, max_length=64)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    format: TemplateAssetFormat
    report_schema_version: str = Field(min_length=1)  # e.g. "field-notes-report/v2"
```

`TemplateSet` (§2.1) gains the pin:

```python
# PROPOSED — additional fields on the TemplateSet sketch in §2.1.
    report_asset_key: str | None = None
    report_asset_version: int | None = Field(default=None, ge=1)
```

Four properties matter and each is there for a reason:

- **Opaque durable reference.** `AttachmentReference` forbids URLs, so the
  locator stays adapter-resolved and no storage provider leaks into the domain.
  "Durable" is what D7b delivers: template assets live in GVAS-managed object
  storage, not in a channel. A template that disappears makes historical reports
  unreproducible, which is a stronger requirement than for an uploaded plan
  file.
- **Content digest.** Makes the asset verifiable and the rendered output
  attributable: a rendered DOCX can record which template bytes produced it.
- **Media type and format.** Stored explicitly rather than sniffed, so a
  mislabeled upload fails at registration and not at render time.
- **`report_schema_version`.** The template's placeholders are written against a
  specific report structure. Pinning schema compatibility is what stops a v1
  template from silently dropping the `PlanReferenceBlock` content that report
  schema v2 introduces (§3.6). A template set whose asset declares an
  incompatible schema version must fail resolution loudly.

### 4.2 Proposed neutral renderer contracts

```python
# PROPOSED — not implemented, not wired.
class RenderedDocument(DomainModel):
    reference: AttachmentReference        # the produced DOCX, opaque locator
    media_type: str = Field(min_length=1)
    content_digest: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=1)
    template_digest: str = Field(min_length=64, max_length=64)
    source_fingerprint: str = Field(min_length=1)   # field_note_source_fingerprint

class DocumentRenderRequest(DomainModel):
    business_id: BusinessId
    template: TemplateAsset
    snapshot: FieldNoteCaseSnapshot
    document: FieldNotesReportDocument

class DocumentRendererPort(Protocol):
    """Projects a validated report document onto a template asset."""

    async def render(self, request: DocumentRenderRequest) -> RenderedDocument: ...
```

Constraints on any implementation behind that port:

- No DOCX library, template engine, or vendor is chosen here. The port takes
  domain values and returns a reference; `python-docx`, an OOXML writer, or a
  hosted service are all valid adapters in `infrastructure`, and none of them
  may be imported from `domain` or `application`.
- The renderer is a pure projection. It may not query repositories, re-derive
  evidence, or reach a channel; everything it needs arrives in the request. Both
  the snapshot and the document are passed because the document carries the
  narrative blocks and the snapshot carries the evidence those blocks cite —
  a template that prints an evidence appendix needs both.
- Output must remain *editable*: a real DOCX, not a PDF and not a
  DOCX-wrapped image.

### 4.3 Delivery through the existing owner path

Rendering is long-running (binary assembly plus two media round trips), so it is
not inlined into the report command:

```text
report document persisted
  -> report.render_docx  (deterministic UUIDv5 outbox command, business-scoped
                          dedup key over report id + version + template digest)
       claims a fenced lease; a stale claimant raises rather than clobbers
       renders via DocumentRendererPort; stores RenderedDocument
  -> outbound_messages row whose attachment is the rendered DOCX reference
  -> owner_reply.deliver  (existing command, existing OwnerReplyPort)
```

Two things this deliberately does not do:

- It does not add a channel-specific upload path. The DOCX travels as an
  `AttachmentReference` on an ordinary outbound message; how a channel
  materializes it (Slack file upload, an MMS/link path for Telnyx later) is an
  adapter concern behind `OwnerReplyPort`, exactly as message text is today.
- It does not re-render on redelivery. The dedup key includes the report version
  and the template digest, so retries return the existing artifact and only a
  genuine change to the source, the report version, or the template produces new
  bytes.

## 5. Plan decisions

Each decision below was the recommended option and is accepted. The alternatives
and their costs are kept so the reasoning survives; D7 is split because its
original recommendation was withdrawn and replaced.

### D4 — Supported plan formats

| option | cost | notes |
| --- | --- | --- |
| Raster image (PNG/JPEG) | lowest | no parsing library; page count is always 1; no text extraction, so sheet labels and room labels must be entered by the owner |
| PDF | moderate | needs a PDF library (a new dependency); gives page count, embedded text for label matching, and page dimensions; covers what small-business owners actually receive from architects |
| CAD / BIM (DWG, IFC, RVT) | high | proprietary or heavyweight parsing, real geometry and layer semantics, licensing questions, and a much larger processing pipeline |

**Accepted: PDF plus raster images.** PDF is what owners actually have,
and its embedded text is what makes §3.3's extraction and §3.7's label matching
possible at all — the owner's own sample is exactly this case.
CAD/BIM is a different product surface — real coordinate systems, layers,
element identity — and should be considered only if a specific customer needs
it, at which point it is a separate workstream, not an added format.

### D5 — Coordinate system

| option | cost | notes |
| --- | --- | --- |
| Normalized page-relative (`[0,1]`, per page) | low | resolution-independent, trivially validated, meaningless across plan versions or in the real world |
| Real-world scaled / georeferenced | high | requires a scale factor or control points per plan, plus owner input to establish them; enables measurements and cross-plan reasoning |

**Accepted: normalized page-relative**, as sketched in `PlanCoordinate`.
Tradeoff accepted: a `MEASUREMENT` annotation can carry the owner's stated measurement as
text but cannot be computed or verified from geometry. Adding scale later is
additive (an optional `scale` on `PlanSheet` plus a derived accessor), so
this is not a one-way door.

### D6 — Annotation placement

| option | cost | notes |
| --- | --- | --- |
| Model-inferred, auto-accepted | low effort, high risk | a wrong mark on a plan the owner sends to a client is worse than no mark |
| Model-inferred, owner-confirmed | moderate | costs conversational round trips; matches the existing follow-up loop exactly |
| Manual only | needs a UI (D7/D5 interplay) | not available before a web UI exists |

**Accepted: inferred with owner confirmation**, with `INFERRED` allowed
only above a configured confidence threshold and everything else routed through
a follow-up question. The `AnnotationConfidence` field exists so the report can
be honest about which is which.

### D7 — Plan file storage and retention

**D7a — retention duration: accepted.** Plan files and their metadata are
retained for at least as long as the business account is open. Nothing in this
system may expire or purge a plan-set version while its account is open, which
also means annotation references and extracted sheets stay resolvable for the
account's lifetime.

**D7b — storage custody: accepted — GVAS-managed object storage.** GVAS takes
custody of uploaded plan files and retains them in managed object storage for
the lifetime of an active business account. The earlier "opaque reference, no
custody" recommendation is withdrawn: it **cannot satisfy D7a**, because

with no custody the bytes live wherever the source channel put them. Slack file
retention is configured by the *customer's* workspace admin, not by this system,
so a file can be deleted, aged out, or lost with a workspace plan change, and
"retained while the account is open" would be a hope rather than a guarantee.
§4.1 raises the same problem, more sharply, for DOCX template assets: an
unavailable template makes historical reports unreproducible.

Custody changes nothing in §3 or §4. `AttachmentReference` already forbids URLs
and hides the resolver, so:

- `domain` and `application` continue to see only opaque references. No bucket
  name, key, region, URL, or presigned locator may appear in a domain value, a
  contract sketch, or an application service.
- `infrastructure` owns the object-store locator, credentials, and access
  (including any presigning), behind the existing `AttachmentAccessPort` shape.
  This is what keeps `tests/test_architecture_boundaries.py` green: a storage
  SDK is an infrastructure import, never a domain one.
- Ingestion is copy-on-upload: the source-channel file is fetched once through
  `AttachmentAccessPort` and persisted to managed storage, after which the
  system-owned copy is authoritative and the channel copy is disposable. The
  copy runs as a leased, idempotent outbox command keyed on the plan-set
  version, like every other long-running step here.

**Still unresolved inside D7b:** the storage vendor and deployment shape (S3,
GCS, R2, self-hosted MinIO, or the database itself for small files), along with
encryption-at-rest, per-tenant key prefixing, and any residency requirement.
None is implied by this decision; each is an infrastructure choice made when
P4 ships.

**Post-closure direction:** the owner's stated direction is an **export** — on
account closure the business gets its data out rather than simply losing it. The
narrower policy is explicitly undecided: no grace period, export deadline,
export scope, deletion obligation, or purge timeline is specified here, and none
should be inferred from this paragraph. That policy joins the existing open
retention question for transcripts and reports in
[`docs/field_notes.md`](field_notes.md).

### D8 — Rendering location

Server-side rendering (burn annotations into a PDF/PNG at report generation) vs.
a future web UI that draws annotations over the plan.

**Accepted: neither, initially.** The report cites annotations
structurally (§3.6) and a rendered artifact is added later. If a rendered
artifact is required for the first release, server-side is the only option that
works over Slack, and it should be a separate outbox command with its own lease
and idempotency key rather than being inlined into report generation.

## 6. Neutrality constraints the implementation must respect

Non-negotiable, and each is already enforced or implied by existing tests:

1. **No provider SDKs in `domain` or `application`.** `tests/test_architecture_boundaries.py`
   rejects non-stdlib, non-Pydantic, non-`gvas.domain` imports and even the
   substrings `slack`/`twilio`. A PDF or CAD library therefore lives in
   `infrastructure` behind a port; the domain sees only `PlanRegion` values.
2. **All owner interaction goes through `OwnerReplyPort` and the outbox.** A
   location-disambiguation question is an `outbound_messages` row plus one
   `owner_reply.deliver` command with a deterministic correlation ID. No plan
   code may talk to a channel.
3. **Tenant-scoped persistence.** Every new table carries `business_id` and uses
   business-scoped composite foreign keys, so no site, plan version, or
   annotation can reference a row in another tenant — the property
   `docs/architecture.md` records for every existing table.
4. **Idempotency and lease fencing for long-running plan processing.** Plan
   ingestion or rendering runs as an outbox command with a deterministic UUIDv5
   ID and a business-scoped dedup key, claims a fenced lease, and calls the
   provider only after the claim transaction commits — the pattern
   `TranscribeFieldNoteAudioService` and `FieldNotesReportRepository.claim`
   already use. A stale claimant must raise rather than clobber.
5. **No customer payment data in this system.** Nothing in this design stores,
   references, or transits payment information, and plan annotations must not
   become a free-text channel for it.
6. **One question at a time.** The existing single-outstanding-question
   invariant applies to location questions; they queue behind checklist
   questions in the same review rather than opening a parallel thread.

## 7. Phased plan

Each phase is independently shippable and leaves the system working.

| phase | contents | depends on |
| --- | --- | --- |
| **P1 — template resolution** | `TemplateSet` rows + repository; `TemplateResolutionPort` and a composition resolver; pin `TemplateSetRef` on the review record; keep `checklist_key` as the default path | nothing |
| **P2 — report templates per tenant** | report template rows referenced by the template set; `ReportGenerationPort` receives the template; section keys become tenant data | P1 |
| **P3 — industry seeding** | seed files per industry + load command; onboarding copies rows into the business | P1, P2 |
| **P4 — sites and plan sets** | `Site`, `SitePlanSetVersion`, upload path with copy-on-upload into managed object storage (D7b), immutability, digest-based dedup | nothing |
| **P4b — sheet discovery** | `PlanSheet` rows; `PlanSheetExtractionPort` + `PlanSheetClassificationPort` adapters; leased `plan_set.extract` command; owner confirmation for uncertain pages | P4 |
| **P5 — annotation model** | `PlanAnnotation` with evidence refs; snapshot carries annotations; `EvidenceSource.PLAN_ANNOTATION`; report schema v2 with `PlanReferenceBlock` | P4b |
| **P6 — location disambiguation** | location slots as optional checklist items; extraction port; follow-up questions for ambiguous locations | P1, P5 |
| **P7 — DOCX output** | `TemplateAsset` rows pinned by the template set; `DocumentRendererPort` adapter; leased `report.render_docx` command; delivery as an attachment on the existing owner-reply path | P2 (P5 only if plan blocks must render) |
| **P8 — plan rendering** | annotated-plan image artifact as its own leased outbox command | P5 |

No phase is blocked on a product decision. P4 and P7 both carry the storage
dependency that D7b accepts — a managed object store and its adapter — so the
vendor choice has to be made before P4 starts, but it is an infrastructure
choice, not an owner decision. P1 is the highest-value, lowest-risk phase and
closes half of composition gap 7 on its own. P5 is the phase that breaks a
published contract, so its contract shapes need review before it starts.

## 8. Where this touches existing contracts

Listed so review can weigh the blast radius, in rough order of severity:

| contract | change | phase |
| --- | --- | --- |
| `FieldNotesReportDocument` / `REPORT_SCHEMA_VERSION` | `ReportBlock` becomes a discriminated union; schema bumps to `field-notes-report/v2`; persisted v1 documents stay readable | P5 |
| `EvidenceSource` | new `PLAN_ANNOTATION` member; `validate_evidence_against` gains a fourth key set plus annotation-evidence validation | P5 |
| `FieldNoteCaseSnapshot` | new `plan_annotations` field; changes `field_note_source_fingerprint` output, so post-change regeneration of an old case yields a new version | P5 |
| `FieldNoteReviewRecord` | new `template_set_key` / `template_set_version` columns alongside the existing checklist pin | P1 |
| `build_application` | `checklist_key` argument superseded by `TemplateResolutionPort`; kept until P3 completes | P1 |
| `FieldNoteCase` | optional `site_id` | P4 |
| `TemplateSet` (proposed) | gains `report_asset_key` / `report_asset_version` pinning an immutable DOCX asset | P7 |
| `ChecklistItemRequirement` / `_validate_outcome` | unchanged — location slots are ordinary optional checklist items, which is why no new validation escape hatch is needed | P6 |
| `AttachmentReference`, `OwnerReplyPort`, outbox | unchanged and reused as-is — the rendered DOCX and the plan set are ordinary attachments on ordinary outbound messages | P4–P8 |

## 9. Decision record

Seven of the eight are accepted by the owner in full; D7 is split.

- **D1** — deprecated template versions are never hard deleted.
- **D2** — industry templates are copied into the business on onboarding, not
  referenced from a global catalog.
- **D3** — an unresolvable location does not block the report.
- **D4** — PDF and raster images are supported; CAD/BIM is out of scope.
- **D5** — coordinates are normalized page-relative; real-world scale is a later
  additive option.
- **D6** — placement is model-inferred with owner confirmation.
- **D7a** — *accepted*: plan files are retained for at least the lifetime of an
  open business account.
- **D7b** — *accepted*: GVAS takes custody of plan files in managed object
  storage for the lifetime of an active account. The original "opaque reference,
  no custody" recommendation is withdrawn as insufficient (§5, D7). Domain and
  application keep opaque references; infrastructure owns object-store locators
  and access. Vendor and deployment shape remain open.
- **D8** — no rendered artifact initially; reports cite annotations structurally.

### Still unresolved

- The object-storage vendor and deployment shape under D7b, plus
  encryption-at-rest, per-tenant key prefixing, and residency. An infrastructure
  choice for P4, not a product decision.
- What happens to plan files, transcripts, and report documents *after* a
  business account closes. The owner's direction is an **export**; the grace
  period, export scope and deadline, and any deletion or purge afterwards are
  undecided, and D7a fixes only the lower bound while the account is open.
- Pre-existing open questions this design does not resolve: case closure, report
  distribution, transcript/report retention, and permanent transcription failure
  handling — all recorded in [`docs/composition.md`](composition.md).
