# Design: per-tenant template sets and site-plan artifacts

Status: **design only**. Nothing here is implemented, and no contract in this
document is accepted as code. Every Pydantic sketch below is a *proposal*
written to be reviewed and revised; none of it is wired into a workflow,
migration, or composition path.

The product decisions D1–D8 below are **accepted by the owner**. They constrain
the implementation phases in §6; the contract shapes that realize them are still
subject to review when each phase ships.

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

Two new scoping levels, both strictly tenant-scoped with the same
business-scoped composite foreign keys every existing table uses:

```python
# PROPOSED — not implemented, not wired.
SiteId = NewType("SiteId", UUID)
SitePlanId = NewType("SitePlanId", UUID)
SitePlanVersionId = NewType("SitePlanVersionId", UUID)
PlanAnnotationId = NewType("PlanAnnotationId", UUID)

class Site(PlanModel):
    site_id: SiteId
    business_id: BusinessId
    label: str = Field(min_length=1)      # owner's name for the place
    external_ref: str | None = None       # opaque owner-supplied key

class SitePlanVersion(PlanModel):
    """One immutable uploaded revision of one plan."""

    version_id: SitePlanVersionId
    plan_id: SitePlanId
    business_id: BusinessId
    site_id: SiteId
    version: int = Field(ge=1)
    attachment: AttachmentReference     # existing opaque adapter token
    page_count: int = Field(ge=1)
    content_digest: str = Field(min_length=64, max_length=64)
    uploaded_at: datetime
```

`attachment` reuses `AttachmentReference` unchanged — its validator already
forbids URLs and requires an opaque adapter token, which is exactly the
neutrality property a plan file needs. No new attachment concept is introduced.

A field-note case gains an optional `site_id`. It is optional because the
existing intake path (`field notes: ...` with no site context) must keep
working; a case with no site simply has no plan annotations.

### 3.2 Plan versioning and immutability

`SitePlanVersion` rows are append-only. A re-upload creates `version+1`; it never
mutates a row. Annotations reference a `version_id`, never a `plan_id`, so:

- A version that has at least one annotation is frozen by construction — there
  is no update path to freeze.
- Uploading a newer version does **not** invalidate, migrate, or re-project
  existing annotations. They stay attached to the version they were made
  against, and a report generated from an old case renders against that old
  version.
- `content_digest` makes a re-upload of a byte-identical file detectable, so an
  accidental duplicate upload can return the existing version instead of
  creating a phantom one. This is the same idempotency shape as
  `field_note_source_fingerprint`.

**Not designed here, deliberately:** carrying annotations forward from version
*n* to *n+1*. That requires geometric registration between two documents and is
open-ended; the honest MVP answer is that annotations do not migrate.

### 3.3 Annotation model

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
    page_index: int = Field(ge=0)
    sheet_label: str | None = None      # "A-101" as printed on the sheet
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
    plan_version_id: SitePlanVersionId
    case_id: FieldNoteCaseId
    kind: AnnotationKind
    region: PlanRegion
    text: str = Field(min_length=1)
    confidence: AnnotationConfidence
    evidence_refs: tuple[ReportEvidenceReference, ...] = Field(min_length=1)
```

Both `page_index` and `sheet_label` are retained: the page index is what a
renderer needs, the sheet label is what the owner and the report reader
recognize, and neither reliably derives from the other.

### 3.4 Bidirectional evidence links

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

### 3.5 How annotations surface in the report

`ReportBlock.kind` is `Literal["text"]` today. The proposal adds a second block
kind rather than encoding plan references in prose:

```python
# PROPOSED — not implemented, not wired.
class PlanReferenceBlock(ReportDomainModel):
    kind: Literal["plan_reference"] = "plan_reference"
    plan_version_id: SitePlanVersionId
    annotation_ids: tuple[PlanAnnotationId, ...] = Field(min_length=1)
    caption: str = Field(min_length=1)
    evidence_refs: tuple[ReportEvidenceReference, ...] = Field(default_factory=tuple)

ReportBlockUnion = Annotated[TextBlock | PlanReferenceBlock, Field(discriminator="kind")]
```

The block references annotations; it does not embed an image. Whether the
consumer draws them is a rendering concern (§4, D5), and keeping the document
free of rendered bytes preserves its current property of being a small,
diffable, fingerprint-stable JSON structure.

This is a **breaking change to `field-notes-report/v1`**: a discriminated union
where a bare model used to be. It requires `REPORT_SCHEMA_VERSION` to move to
`field-notes-report/v2`, with v1 documents readable unchanged (they are
persisted rows, never re-validated against the new model).

### 3.6 How a spoken note references a location at all

This is the hardest part and it is a *conversation* problem, not a geometry
problem. An owner says "the back corner by the loading dock"; the plan says
"Zone 4 / Grid C-7". Nothing bridges those automatically with any reliability.

Proposed flow, which reuses the existing follow-up loop rather than inventing a
second interaction channel:

```text
transcript segment mentions a location
  -> location-candidate extraction (a port, no provider in domain)
  -> candidate matched against plan labels for the pinned plan version
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

## 4. Accepted plan decisions

Each decision below was the recommended option and is accepted; the alternatives
and their costs are kept so the reasoning survives.

### D4 — Supported plan formats

| option | cost | notes |
| --- | --- | --- |
| Raster image (PNG/JPEG) | lowest | no parsing library; page count is always 1; no text extraction, so sheet labels and room labels must be entered by the owner |
| PDF | moderate | needs a PDF library (a new dependency); gives page count, embedded text for label matching, and page dimensions; covers what small-business owners actually receive from architects |
| CAD / BIM (DWG, IFC, RVT) | high | proprietary or heavyweight parsing, real geometry and layer semantics, licensing questions, and a much larger processing pipeline |

**Accepted: PDF plus raster images.** PDF is what owners actually have,
and its embedded text is what makes §3.6's label matching possible at all.
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
additive (an optional `scale` on `SitePlanVersion` plus a derived accessor), so
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

Plan files are large, tenant-owned, and potentially sensitive (building
layouts). Options: keep the bytes out of this system entirely and store only an
opaque `AttachmentReference` resolved through the existing
`AttachmentAccessPort`; or take custody in object storage owned by this system.

**Accepted: opaque reference, no custody.** It preserves the current
architecture (no locator is ever a URL; adapters own media access) and avoids a
storage dependency. Tradeoff accepted: availability of the file is the source
channel's problem, and a plan deleted upstream breaks re-rendering — acceptable
while the report document itself does not embed the plan image.

**Retention, accepted:** plan files and their metadata are retained for at least
as long as the business account is open. Nothing in this system may expire or
purge a plan version while its account is open, which also means annotation
references stay resolvable for the account's lifetime.

Still unresolved, and deliberately not decided here: what happens *after* an
account closes — whether there is a grace period, an export obligation, or a
purge, and whether it differs for plan files, transcripts, and report documents.
That narrower policy joins the existing open retention question for transcripts
and reports in [`docs/field_notes.md`](field_notes.md). Under "no custody", this
system holds only opaque references, so any purge is coordinated with whoever
holds the bytes.

### D8 — Rendering location

Server-side rendering (burn annotations into a PDF/PNG at report generation) vs.
a future web UI that draws annotations over the plan.

**Accepted: neither, initially.** The report cites annotations
structurally (§3.5) and a rendered artifact is added later. If a rendered
artifact is required for the first release, server-side is the only option that
works over Slack, and it should be a separate outbox command with its own lease
and idempotency key rather than being inlined into report generation.

## 5. Neutrality constraints the implementation must respect

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

## 6. Phased plan

Each phase is independently shippable and leaves the system working.

| phase | contents | depends on |
| --- | --- | --- |
| **P1 — template resolution** | `TemplateSet` rows + repository; `TemplateResolutionPort` and a composition resolver; pin `TemplateSetRef` on the review record; keep `checklist_key` as the default path | nothing |
| **P2 — report templates per tenant** | report template rows referenced by the template set; `ReportGenerationPort` receives the template; section keys become tenant data | P1 |
| **P3 — industry seeding** | seed files per industry + load command; onboarding copies rows into the business | P1, P2 |
| **P4 — sites and plan artifacts** | `Site`, `SitePlanVersion`, upload path, immutability, digest-based dedup | nothing |
| **P5 — annotation model** | `PlanAnnotation` with evidence refs; snapshot carries annotations; `EvidenceSource.PLAN_ANNOTATION`; report schema v2 with `PlanReferenceBlock` | P4 |
| **P6 — location disambiguation** | location slots as optional checklist items; extraction port; follow-up questions for ambiguous locations | P1, P5 |
| **P7 — rendering** | annotated-plan artifact as its own leased outbox command | P5 |

No phase is blocked on a product decision any more. P1 is the highest-value,
lowest-risk phase and closes half of composition gap 7 on its own. P5 is the
phase that breaks a published contract, so its contract shapes need review
before it starts.

## 7. Where this touches existing contracts

Listed so review can weigh the blast radius, in rough order of severity:

| contract | change | phase |
| --- | --- | --- |
| `FieldNotesReportDocument` / `REPORT_SCHEMA_VERSION` | `ReportBlock` becomes a discriminated union; schema bumps to `field-notes-report/v2`; persisted v1 documents stay readable | P5 |
| `EvidenceSource` | new `PLAN_ANNOTATION` member; `validate_evidence_against` gains a fourth key set plus annotation-evidence validation | P5 |
| `FieldNoteCaseSnapshot` | new `plan_annotations` field; changes `field_note_source_fingerprint` output, so post-change regeneration of an old case yields a new version | P5 |
| `FieldNoteReviewRecord` | new `template_set_key` / `template_set_version` columns alongside the existing checklist pin | P1 |
| `build_application` | `checklist_key` argument superseded by `TemplateResolutionPort`; kept until P3 completes | P1 |
| `FieldNoteCase` | optional `site_id` | P4 |
| `ChecklistItemRequirement` / `_validate_outcome` | unchanged — location slots are ordinary optional checklist items, which is why no new validation escape hatch is needed | P6 |
| `AttachmentReference`, `OwnerReplyPort`, outbox | unchanged and reused as-is | P4–P7 |

## 8. Decision record

All eight are accepted by the owner.

- **D1** — deprecated template versions are never hard deleted.
- **D2** — industry templates are copied into the business on onboarding, not
  referenced from a global catalog.
- **D3** — an unresolvable location does not block the report.
- **D4** — PDF and raster images are supported; CAD/BIM is out of scope.
- **D5** — coordinates are normalized page-relative; real-world scale is a later
  additive option.
- **D6** — placement is model-inferred with owner confirmation.
- **D7** — plan files are held as opaque references with no custody, and are
  retained for at least the lifetime of the business account.
- **D8** — no rendered artifact initially; reports cite annotations structurally.

### Still unresolved

- What happens to plan files, transcripts, and report documents *after* a
  business account closes (grace period, export, purge). D7 fixes only the
  lower bound.
- Pre-existing open questions this design does not resolve: case closure, report
  distribution, transcript/report retention, and permanent transcription failure
  handling — all recorded in [`docs/composition.md`](composition.md).
