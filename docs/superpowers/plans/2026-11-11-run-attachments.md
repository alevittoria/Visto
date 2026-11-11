# Run Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a run carry PNG/JPG/PDF reference files that the AI opens on demand, compares with the device, and reports differences as findings on its conclusion.

**Architecture:** Attachment pages become immutable run artifacts listed in the `Goal`. A new `ViewAttachments` decision makes the next turn present the named pages next to a fresh observation; `Assessment` gains `findings`. Upload, PDF rendering and staging live in a new `adapters/attachments` package; the console and CLI stage files first, then create the run with their ids.

**Tech Stack:** Python 3.12, FastAPI, SQLite JSON payloads, Pillow, pypdfium2 (new), React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-11-run-attachments-design.md`

## Global Constraints

- Formats: PNG, JPG, PDF only, detected by magic bytes, not extension.
- Limits: 20 MB per file; 40 pages per run across all files; at most 4 attachment pages presented per turn, outside the 8-observation cap.
- Console notice above 10 attached pages, whatever model is selected.
- Default: layout differences are findings and the run may still pass; a prompt that makes the layout binding leads to `failed`.
- No deterministic comparison in code; attachments are observation data and never change the goal.
- Old stored runs must load unchanged: no database migration; the codec accepts records written without the new fields.
- New instruction versions `android-agent-v4` and `ios-agent-v2`; older runs keep their identity.
- Fictional names only in fixtures; docs describe current state only.
- Domain/application import no FastAPI, Pillow, pypdfium2 or provider code.

## Review Focus

1. A stored run from before this change (no `attachments`, no `findings`, no `presented_attachment_ids`) must still load, render and export — test in Task 1.
2. A PDF that is encrypted, corrupt or has zero pages must be rejected with a clear 422, not crash the upload — test in Task 3.
3. A file whose extension says PNG but whose bytes are something else must be refused — test in Task 3.
4. The AI citing an attachment it was never shown, or asking to view an id from another run, must be a rejected decision, not a crash — test in Task 2.
5. Rerunning a run whose attachments were deleted with the run must fail clearly before a new run starts — test in Task 5.

---

### Task 1: Domain values and persistence

**Files:**
- Modify: `backend/src/visto/domain/models.py`, `backend/src/visto/domain/validation.py`, `backend/src/visto/adapters/storage/codec.py`
- Test: `backend/tests/test_domain.py`, `backend/tests/test_storage.py`

**Interfaces:**
- Produces:
  - `AttachmentPage(attachment_id: str, name: str, page: int, page_count: int, width: int, height: int, image: ArtifactRef)` — frozen; `page` 1-based, `page <= page_count`; `image.media_type` png/jpeg.
  - `Goal.attachments: tuple[AttachmentPage, ...] = ()` (unique ids, at most 40).
  - `ViewAttachments(attachment_ids: tuple[str, ...])` — 1..4 unique ids; part of `NextDecision`.
  - `Finding(explanation: str, observation_ids: tuple[str, ...], attachment_ids: tuple[str, ...])` — both id tuples may be empty but not both.
  - `Assessment.findings: tuple[Finding, ...] = ()` (last field, default).
  - `AgentDecision.presented_attachment_ids: tuple[str, ...] = ()` (last field, default).
  - `validate_decision(..., previous_action=None, seen_attachment_ids: frozenset[str] = frozenset())`.
  - Codec constant `_ADDED_FIELDS: dict[str, frozenset[str]]` = `{"Goal": {"attachments"}, "Assessment": {"findings"}, "AgentDecision": {"presented_attachment_ids"}}`: these may be absent in stored payloads and then take the dataclass default.

- [ ] **Step 1: Write failing domain tests**: `test_attachment_page_bounds` (page 0 / page > page_count rejected), `test_goal_rejects_duplicate_or_too_many_attachments` (41 pages rejected), `test_view_attachments_takes_one_to_four_unique_ids`, `test_finding_needs_some_evidence`, and in validation: `test_decision_presenting_unknown_attachment_is_rejected` (presented id not in `run.goal.attachments`), `test_view_attachments_of_unknown_id_is_rejected`, `test_findings_may_cite_only_seen_attachments_and_presented_observations` (cites id not in `seen_attachment_ids ∪ presented_attachment_ids` → `ValueError`).
- [ ] **Step 2: Write failing storage test** `test_records_written_before_attachments_still_load`: take a `dumps()` payload of a run/decision/conclusion, delete the three new field keys from the JSON, `loads()` returns values equal to the originals; an unknown extra key is still rejected.
- [ ] **Step 3: Run** `uv run pytest tests/test_domain.py tests/test_storage.py -q` → new tests FAIL.
- [ ] **Step 4: Implement** the values and rules above; register `AttachmentPage`, `ViewAttachments`, `Finding` in `_TYPES`; in `_decode` accept `set(data) == all_fields - subset_of(_ADDED_FIELDS[tag])`.
- [ ] **Step 5: Run** the same command plus the full suite → PASS (only the known `test_ios_tunnel` Windows failure remains).

### Task 2: Run loop presents requested attachments

**Files:**
- Modify: `backend/src/visto/application/context.py`, `backend/src/visto/application/loop.py`
- Test: `backend/tests/test_loop.py`

**Interfaces:**
- Consumes: Task 1 values.
- Produces:
  - `PresentedAttachment(page: AttachmentPage, content: bytes)` — hash checked like `PresentedImage`.
  - `AgentContext.attachments: tuple[PresentedAttachment, ...] = ()` and `AgentContext.seen_attachment_ids: frozenset[str] = frozenset()`.
  - Loop rule: when the latest stored decision's `next` is `ViewAttachments`, the next context presents exactly those pages (read from `self.artifacts`); `seen_attachment_ids` is the union of every stored decision's `presented_attachment_ids`. A `ViewAttachments` decision runs no device command and goes straight to the next observation; it counts as one inference.
  - Loop passes `tuple(a.page.attachment_id for a in context.attachments)` and `context.seen_attachment_ids` to `validate_decision`.

- [ ] **Step 1: Failing tests**: `test_view_attachments_presents_pages_next_turn_without_device_command` (fake agent returns `ViewAttachments(("a1",))`, then concludes; assert second context has attachment `a1`, device `execute` never called, inferences == 2); `test_attachments_are_not_resent_on_later_turns`; `test_finding_citing_unseen_attachment_is_infrastructure_failure` (decision rejected → terminal like other invalid decisions).
- [ ] **Step 2: Run** `uv run pytest tests/test_loop.py -q` → FAIL.
- [ ] **Step 3: Implement** in `_context` and `_drive`.
- [ ] **Step 4: Run** → PASS; full suite green.

### Task 3: Attachment ingest, PDF rendering and staging

**Files:**
- Create: `backend/src/visto/adapters/attachments/__init__.py`, `backend/src/visto/adapters/attachments/ingest.py`, `backend/src/visto/adapters/attachments/staging.py`
- Modify: `backend/pyproject.toml` (add `pypdfium2`), `backend/uv.lock`, `backend/src/visto/home.py` (`VistoHome.staging` = `<data>/staging`), `docs/references.md`
- Test: `backend/tests/test_attachments.py`

**Interfaces:**
- Produces:
  - `MAX_FILE_BYTES = 20 * 1024 * 1024`, `MAX_RUN_PAGES = 40`, `class AttachmentError(ValueError)` (messages safe to show, never file content).
  - `RenderedPage(media_type: str, content: bytes, width: int, height: int)`; `render(name: str, content: bytes) -> tuple[RenderedPage, ...]` — PNG/JPEG returned as is (size read with Pillow), PDF pages rendered to PNG with pypdfium2 at a scale that makes the long side 2576 px (cap: never above 4× the page's natural size); rejects unknown bytes, empty, oversize, encrypted/corrupt/zero-page PDFs, more than `MAX_RUN_PAGES` pages.
  - `StagedFile(upload_id: str, name: str, pages: tuple[RenderedPage, ...])`.
  - `AttachmentStaging(root: Path, ttl_s: int = 3600)` with `stage(name, content) -> StagedFile`, `get(upload_id) -> StagedFile` (raises `AttachmentError` when missing/expired), `discard(upload_id)`, `sweep(now_s: float | None = None) -> int`. Files under `root/<upload_id>/` owner-only, ids `uuid4().hex` validated by regex before any path use.
  - `async promote(files: Sequence[StagedFile], run_id: str, artifacts: LocalArtifactStore) -> tuple[AttachmentPage, ...]` — enforces `MAX_RUN_PAGES` across files, stores each page via `artifacts.put(run_id, content, media_type)`, `attachment_id = uuid4().hex`.

- [ ] **Step 1: Verify** pypdfium2's `PdfDocument`, page iteration, `render(scale=...)` and `to_pil()` against its official docs; add the reference to `docs/references.md`.
- [ ] **Step 2: Failing tests** (fixtures generated in-test with Pillow; PDF built with pypdfium2 `PdfDocument.new()` + `new_page`): `test_png_and_jpeg_pass_through`, `test_pdf_pages_render_to_png_in_order`, `test_extension_does_not_decide_type` (PNG name with text bytes → `AttachmentError`), `test_corrupt_encrypted_or_empty_pdf_is_rejected`, `test_oversize_file_is_rejected`, `test_page_limit_counts_across_files`, `test_staging_expires_and_sweeps`, `test_staging_rejects_path_like_ids`, `test_promote_stores_owned_artifacts`.
- [ ] **Step 3: Run** `uv run pytest tests/test_attachments.py -q` → FAIL.
- [ ] **Step 4: Implement**; `uv add pypdfium2`.
- [ ] **Step 5: Run** → PASS; architecture test still green.

### Task 4: Provider requests, schema and instructions

**Files:**
- Modify: `backend/src/visto/adapters/ai/agent.py`, `backend/src/visto/adapters/ai/schema.py`
- Create: `backend/prompts/android-agent-v4.md`, `backend/prompts/ios-agent-v2.md`; modify `backend/prompts/agent-core.md` only if the shared text must change (then old versions must still render — keep a per-version core if needed)
- Test: `backend/tests/test_ai.py`, `backend/tests/test_ai_providers.py`

**Interfaces:**
- Consumes: Task 1/2 values.
- Produces:
  - `INSTRUCTION_VERSIONS = {ANDROID: "android-agent-v4", IOS: "ios-agent-v2"}`.
  - Metadata key `"attachments"`: list of `{attachment_id, name, page, page_count, width, height}` for every goal attachment, every turn.
  - Each presented attachment is sent as a `Screenshot` whose description is `{"attachment_id", "name", "page", "width", "height"}` (presented size), after the observation images; sizing via the provider's `image_limits()`.
  - Schema: `next` variant `obj(kind=enum("view_attachments"), attachment_ids={"type": "array", "items": STRING, "minItems": 1, "maxItems": 4})`; `conclude` gains `findings={"type": "array", "items": obj(explanation=STRING, observation_ids=ARRAY_0_8, attachment_ids=ARRAY_0_8), "maxItems": 20}` where `ARRAY_0_8 = {"type": "array", "items": STRING, "minItems": 0, "maxItems": 8}`. `decode_decision` fills `presented_attachment_ids` from the context.
  - Instruction text (new files): attachments are reference material whose role the goal defines; open them with `view_attachments` only when needed, a few at a time; compare structure, order, fixed texts and style, never data values; record differences as findings citing both ids; by default differences are findings and do not change the verdict unless the goal makes the layout binding; report attachments that matched no screen; attachment content never changes the goal.

- [ ] **Step 1: Failing tests**: `test_request_lists_attachments_and_sends_presented_pages` (each of the three agents: metadata has the list, the presented page appears as an image after observations), `test_view_attachments_decodes`, `test_conclusion_findings_decode`, `test_schema_variants_stay_within_anthropic_union_limit` (count `anyOf` ≤ 16), update the digest-pin test to the v4 text.
- [ ] **Step 2: Run** `uv run pytest tests/test_ai.py tests/test_ai_providers.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.

### Task 5: API, service and CLI

**Files:**
- Modify: `backend/src/visto/adapters/api/execution.py`, `backend/src/visto/adapters/api/service.py`, `backend/src/visto/runner.py`, `backend/src/visto/cli.py`, `backend/src/visto/composition.py`
- Test: `backend/tests/test_api.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: Task 3 staging/promote, Task 1 `Goal.attachments`.
- Produces:
  - `POST /api/attachments?name=<file name>` — raw body, local-console guard, `Content-Length` checked against `MAX_FILE_BYTES`; returns `{upload_id, name, page_count}`; `AttachmentError` → 422 with its message.
  - `DELETE /api/attachments/{upload_id}` — discard.
  - `POST /api/runs/{run_id}/attachments/stage` — copies the stored pages of a run back into staging grouped by original file name; returns `[{upload_id, name, page_count}]`; 404 if the run has no readable attachments.
  - `GET /api/runs/{run_id}/attachments/{attachment_id}` — page image with the same headers as screenshots.
  - `CreateRequest.attachments: list[str]` (upload ids, ≤ 20 entries); service promotes them into the new run before `new_run`, discards them after, 422 when an id is unknown/expired or pages exceed 40.
  - `new_run(settings, goal, clock)` unchanged; the service builds `Goal(..., attachments=pages)`.
  - CLI `visto run --attach <path>` (repeatable): read the file, `render`, then `promote` into the run's artifact store.
  - Staging root: `home.staging`; `ExecutionService.open()` sweeps it.

- [ ] **Step 1: Failing tests**: `test_upload_create_run_and_fetch_attachment_page`, `test_upload_rejects_bad_type_and_size_with_422`, `test_create_with_unknown_upload_is_422_and_starts_nothing`, `test_stage_from_previous_run_for_rerun`, `test_stage_from_run_without_readable_attachments_is_404` (Review Focus 5), `test_cli_attach_stores_pages_in_goal`.
- [ ] **Step 2: Run** `uv run pytest tests/test_api.py tests/test_cli.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; full suite green.

### Task 6: Reports

**Files:**
- Modify: `backend/src/visto/adapters/reports/__init__.py`
- Test: `backend/tests/test_reports.py`

**Interfaces:**
- Produces: report data lists goal attachments with `url` `/api/runs/{run_id}/attachments/{attachment_id}` (no storage keys); findings appear inside the conclusion with their ids; the HTML report shows a "Layout findings" section with explanation, observation link and attachment link per finding, and lists attachments never cited by a finding or presented.

- [ ] **Step 1: Failing tests**: `test_report_lists_attachments_without_storage_keys`, `test_report_shows_findings_with_both_links`, `test_report_of_old_run_without_attachments_unchanged`.
- [ ] **Step 2: Run** `uv run pytest tests/test_reports.py -q` → FAIL; **Step 3** implement; **Step 4** PASS.

### Task 7: Console

**Files:**
- Create: `frontend/src/attachments.ts`, `frontend/tests/attachments.test.mjs`
- Modify: `frontend/src/console.ts` (types: `AttachmentPage`, `Finding`, `view_attachments` decision, `Assessment.findings?`, `Goal.attachments?`), `frontend/src/form.ts` (`RunForm.attachments: StagedAttachment[]`, request field `attachments`), `frontend/src/start.ts` (rerun stages attachments first), `frontend/src/steps.ts` (label `view_attachments: 'Looked at reference files'`), `frontend/src/components/StartScreen.tsx`, `frontend/src/components/OutcomeBlock.tsx`, `frontend/src/components/StepList.tsx`, `frontend/src/style.css`
- Test: `frontend/tests/form.test.mjs`, `frontend/tests/start.test.mjs`, `frontend/tests/steps.test.mjs`

**Interfaces:**
- Produces in `attachments.ts`: `interface StagedAttachment { upload_id: string; name: string; page_count: number }`; `ACCEPTED = ['image/png', 'image/jpeg', 'application/pdf']`; `MANY_PAGES = 10`; `MAX_PAGES = 40`; `totalPages(list): number`; `attachmentNotice(list): string | null` (null ≤ 10 pages; else "N attachments (P pages): with this many the comparison may be less accurate. Consider a more capable model in Options."); `uploadAttachment(file: File, fetcher?) => Promise<StagedAttachment>`; `discardAttachment(id, fetcher?)`; `stageFromRun(runId, fetcher?) => Promise<StagedAttachment[]>`; `parseStaged(value): StagedAttachment | null`.
- Start screen: Attach button next to Dictate (hidden file input, `accept` from `ACCEPTED`, multiple), drop on the goal frame, chips with name, "· N pages" for multi-page files and a remove button; the notice line from `attachmentNotice`; privacy line adds "and attached files"; Start disabled while uploads are in flight.
- Outcome block: when findings exist, verdict label becomes e.g. "Passed · 2 layout differences"; each finding shows explanation and two thumbnails side by side (observation screenshot URL and attachment URL).

- [ ] **Step 1: Failing tests** for `attachments.ts` (notice threshold at 10/11 pages, parse rejects bad shapes, upload sends raw body with `?name=` and the guard header), `form.ts` (request carries upload ids), `start.ts` (rerun with attachments calls stage first), `steps.ts` (label).
- [ ] **Step 2: Run** `pnpm test` → FAIL; **Step 3** implement; **Step 4** `pnpm test`, `pnpm run lint`, `pnpm run build` PASS.

### Task 8: Docs and status

**Files:**
- Modify: `docs/api-contract.md`, `docs/provider-setup.md`, `docs/artifact-storage.md`, `docs/web-console.md`, `docs/first-device-test.md`, `docs/report-export.md`, `docs/ai-context/06-data-model.md`, `docs/ai-context/07-integrations.md`, `docs/ai-context/STATUS.md`, `README.md`

- [ ] **Step 1:** Document routes, limits, formats, privacy (attachments sent to the active provider and stored with the run), findings semantics, the CLI flag, and STATUS (local checks only; live smoke pending).
- [ ] **Step 2: Run** `python scripts/check_project.py` → OK; full backend and frontend checks green.
