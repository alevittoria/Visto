# Run attachments: reference files alongside the goal

## Intent

A run takes files as well as a prompt. The contributor's concrete case: export the
screens of a design from Figma (PNG or PDF), attach them, and ask the AI to check
that the app's UI matches them while it navigates. Today this check is done by hand,
screen by screen, against Figma.

The comparison is about the **interface, not the data**: which elements are present,
their order and arrangement, fixed texts and style. A value that differs ("3 items"
on the device, "4 items" in the mockup) is not a difference; a missing, moved or
reordered element is.

Attachments are also meant for cases nobody has thought of yet. They are generic
reference material that travels with the goal; the prompt says what they are for
("compare with these layouts", "use the data in this file", "follow these steps"),
and the AI adapts. The contributor only attaches files and writes the prompt: no
per-file notes, no manual mapping of files to screens.

This fits the product rules in `AGENTS.md`: the comparison is the AI's semantic
judgement with referenced evidence, not a rule in code.

## Goals

- Attach PNG, JPG and PDF files to a run from the console start screen and from
  `visto run`.
- The AI sees the list of attachments every turn and opens the ones it needs, when it
  needs them, a few at a time, alongside a fresh screenshot.
- The AI reports layout differences as **findings** that cite the attachment (and
  page) and the observation they compare.
- **Default outcome (contributor's decision):** layout differences do not fail the
  run. The run can conclude `passed` with findings listed as warnings. When the prompt
  asks for the layout to be binding (for example "fail if a screen differs from the
  layout"), the AI concludes `failed` with the differences as its cause.
- When the AI cannot find a screen that matches an attachment, or cannot tell which
  screen an attachment depicts, it says so: it asks the user (existing ask/answer) or
  lists the attachment as not compared; it never invents a match.
- Attachments are stored with the run, so the report stays complete after the
  original files are moved or deleted, and Rerun / Edit and rerun reuse them.
- A warning on the start screen when there are many attachments, suggesting a more
  capable model (contributor's request).

## Non-goals

- Figma integration or any design-tool API. Files are exported by the contributor.
- File types other than PNG, JPG and PDF in this iteration. The design keeps the
  attachment concept generic so other types can be added later.
- Comparing pixels, colours to a tolerance, or any measurement in code.

## Anti-goals

These would make the feature a failure even if it technically worked:

- **A deterministic verifier.** No pixel diff, OCR rule, element list or similarity
  score decides anything. Code stores, presents and validates references; the AI
  judges (`AGENTS.md`: no static UI predicates, no deterministic verifier).
- **Attachments as authority.** An attachment is observation data like a screenshot.
  Text inside a PDF or image cannot change the goal, the constraints or the
  instructions, exactly as on-screen text cannot.
- **Sending every attachment every turn.** With 20 layouts and a 40-step run that
  would be about 800 extra images: slow, expensive, and some providers lower
  resolution above 20 images per request (see `docs/references.md`, Anthropic vision).
- **Flagging data differences.** A finding about a value that differs only because
  the app holds different data is noise; the instructions must say so explicitly.

## Decisions

### What the contributor does

- **Console start screen** (mockup approved in brainstorming): an **Attach** button
  next to Dictate, and dropping files on the goal field. Attached files appear as
  removable chips under the text; a PDF chip shows its page count. When more than a
  threshold of pages is attached (10 is my call), one notice line says the comparison
  may be less accurate with the default model and suggests a more capable model in
  Options. The privacy line under the input mentions that attachments are sent to the
  AI provider and stored with the run.
- The many-attachments notice appears whenever the page count is above the threshold,
  whatever model is selected (my call: the catalog does not rank models, so the
  notice suggests a more capable model rather than naming one).
- **CLI**: `visto run --attach <file>`, repeatable.
- Attachments work in observe-only mode too: viewing an attachment is not a device
  mutation (my call).
- **Rerun / Edit and rerun** reuse the stored attachments of the source run; Edit and
  rerun lets the contributor remove some or add new ones.

### How the AI works with attachments

- Every turn's metadata lists the run's attachments: an id per attachment page, the
  original file name, page number and page count for PDFs, and pixel size. This list
  is part of the immutable goal for the whole run.
- A new decision, **view attachments**, names up to 4 attachment pages (my call) to
  look at. It performs no device command. The next turn presents those pages together
  with a fresh observation of the device, so the AI can compare. It counts as one
  inference against the run budget, like any other turn.
- The AI may open attachments at the start to understand the flow, or when it
  believes it reached the matching screen; it decides from the prompt and file names.
- The conclusion gains **findings**: zero or more items, each with an explanation and
  the observation ids and attachment ids it compares. Findings are allowed with any
  verdict. The verdict follows the prompt as described in Goals; the instructions set
  the default (layout differences are warnings unless the prompt makes them binding).
- The instructions also tell the AI to list attachments it was asked to compare but
  could not match, and to ignore data-only differences.
- Validation stays technical: every attachment id the AI cites must exist in the run
  and must have actually been presented to it in some turn; every observation id
  follows today's rules. A decision citing an attachment it never saw is rejected
  like an unpresented observation today.
- The instruction files get new versions (`android-agent-v4`, `ios-agent-v2`) because
  the decision schema changes. Runs recorded with earlier versions keep their identity
  and are shown as before.

### Files, storage and limits

- **PDF pages are rendered locally to PNG at upload** (my call), one image per page,
  with `pypdfium2` (BSD-3/Apache-2.0, wheels for every supported host; to be added to
  `docs/references.md` with its rendering API before use). Reason: all three providers
  then receive the same input, a finding can cite an exact page, the report can show
  the page next to the screenshot, and the existing image sizing (`imaging.py`) applies
  unchanged. The original PDF is stored too, so the report can link it.
- Attachment pages are owned, immutable artifacts of the run, stored next to
  screenshots with the same provenance and integrity checks (byte length, SHA-256).
  The run database gets a numbered migration (backup first, as today).
- Limits (my call, to tune later): up to 20 MB per file, up to 40 pages per run
  across all files, PNG/JPG/PDF only, checked by content type and magic bytes, not by
  extension. Exceeding a limit gives a clear message and starts nothing.
- Attachment pages are downsized for each provider exactly like screenshots; stored
  originals keep full size.
- Per-turn image budget: attachment pages presented in a turn do not count against the
  existing 8-observation limit; they have their own cap of 4.

### API (my call)

- Upload first, then start: `POST /attachments` (local-console guard, one file per
  request) validates and stores a file in a staging area and returns its id(s) and
  page list. `POST /runs` accepts the attachment ids. Staged files not used by a run
  are removed after a bounded time and at startup.
- Rerun passes the source run's attachment ids; the server copies them into the new
  run so each run owns its own artifacts.
- Report JSON/HTML and the run view include attachments and findings.

### Report and run view

- Each finding appears at the step where it was made, highlighted: the AI's
  explanation, the real screenshot and the referenced attachment page side by side
  (mockup approved in brainstorming).
- The outcome block shows `passed · N layout differences` (or the failed verdict),
  and the run summary lists attachments compared and attachments never matched.

### Privacy

Attachments leave the computer for inference with the same provider as the run and
are stored in the Visto home like screenshots. `docs/provider-setup.md`,
`docs/artifact-storage.md` and the console privacy line say so.

## Constraints

- Clean Architecture: domain/application stay platform- and provider-neutral; PDF
  rendering and file validation live in adapters.
- One runtime AI conversation; no extra model call to "pre-classify" attachments.
- Typed, bounded operations; no generic file access exposed to the AI (it can only
  name attachment ids of its own run).
- Fictional names only in fixtures and examples; no real app screens in tests.

## Left to the builder

- Exact field names in the domain model and decision schema, and whether findings
  live inside `Assessment` or next to it.
- Migration shape (new table vs. columns), staging cleanup interval.
- Rendering resolution for PDF pages (enough for the largest provider limit).
- Console component structure and copy, within the approved mockups.
- How the view-attachments turn records which attachment pages reached the model
  (it must be recorded, like observation provenance today).

## Verification

- Local checks: upload validation (types, sizes, page limit, magic bytes), PDF page
  rendering, artifact integrity, migration with backup, schema decoding of the new
  decision and findings, rejection of unpresented attachment ids, provider request
  shapes with attachment pages, console pure modules, CLI `--attach`, report output.
- Live check on a real phone with fictional layouts: one run where the UI matches,
  one with a missing element (expect `passed` with a finding), the same with a binding
  prompt (expect `failed`), and one with an attachment that matches no screen (expect
  it to be reported as not compared). These are smoke checks, not a labelled
  evaluation.
