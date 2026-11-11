# Current status

Current state of the project only. Git history is the changelog; do not append
dated progress notes here.

## Implemented

- **Domain and application**: immutable typed contracts and a single-conversation
  run loop with fresh pre/post observations, bounded context, ask/answer,
  pause/resume, distinct semantic and technical terminal records, no blind
  retries of uncertain mutations, and a cancellable post-action settle delay
  (default 1500 ms, skipped for rejected commands).
- **Shared Appium core**: the platform-neutral half of a device adapter — HTTP
  wire, PNG framing checks, fail-closed lease marker, single-operation guard,
  source-frame validation, focused-element lookup and receipt/uncertainty
  semantics. Each platform supplies only its capabilities, its frame capture and
  its command translation.
- **Android adapter**: serial-bound Appium/UiAutomator2 sessions, native PNG and
  rotation validation, typed commands (`tap`, `long_press`, `swipe`, `type_text`
  as one whole-value set, `back`, `home`, `enter`) and fail-closed
  device leases in the Visto home. Device inventory via a fixed, bounded
  `adb devices -l`; the backend starts the adb server at startup.
- **iOS tunnel**: on macOS `visto start` runs the XCUITest driver's tunnel script as
  root through sudo (one password prompt, credential refreshed while running,
  ownership and permission checks before escalating, `--no-ios-tunnel` to opt out),
  prints a notice when the script ends on its own, starts it again before a run on a
  physical iPhone when it has ended, and stops it after Appium. The tunnel only covers
  phones connected by USB cable. Refreshing the device list also
  tries to reconnect paired phones that CoreDevice reports as not connected.
- **iOS adapter**: Appium/XCUITest sessions bound to a simulator or an attached
  device, the same seven typed commands, and `back` as a dispatch-only swipe from
  the left screen edge that may not navigate — the AI judges the effect from the
  next observation and adapts. Screenshot pixels are converted to display points
  through one authority, with the ratio derived and validated per session and an
  inconsistent or non-uniform ratio refusing the device rather than guessing.
  WebDriverAgent signing values come from the settings store and are never
  committed. Device inventory from two independent fixed, bounded, read-only
  Xcode commands, one for simulators and one for attached devices, so a failure
  of either still lists the other.
- **AI adapters**: three providers, one active at a time: OpenAI Responses
  (default), Anthropic Messages and Gemini Interactions. They share one turn
  (context, screenshot sizing, decision decoding) and differ only in wire request
  and reply reading; each sends the same decision schema with the keywords its
  provider rejects removed, and every reply is checked locally against the full
  schema. Requests go through a fixed HTTPS child process (secret via stdin,
  fixed endpoint per provider, bounded lifecycle, sanitized error categories,
  redirects rejected). Screenshots are downsized to each provider's limits
  (Claude per resolution tier). Instructions render from a shared core plus
  one versioned notes file per platform, `android-agent-v4` and `ios-agent-v2`;
  earlier versions still render and a test pins the v3 Android text by digest.
  Selectable models from the active provider's allowlist. Audio transcription for
  dictated goals, always with the OpenAI key.
- **Attachments**: a run can carry PNG, JPG and PDF reference files (20 MB each,
  40 pages per run), checked by their bytes; PDF pages are rendered locally with
  pypdfium2. Uploads wait in an expiring staging folder until a run claims them,
  then become owned artifacts of the run. Each turn lists them; the AI opens up to
  four with `view_attachments` and reports differences as conclusion findings,
  which by default do not change the verdict unless the goal makes the layout
  binding. Console: Attach button, drop on the goal, chips, a notice above 10
  pages, findings with screenshot and page side by side, Rerun and Edit and rerun
  staging the files again; CLI `--attach`; reports list attachments and findings.
  Older runs load unchanged because the codec accepts records without the new fields.
- **Storage**: SQLite run history and owned immutable screenshot artifacts with
  scoped provenance and first-terminal-wins finalization.
- **Platform selection**: a run carries its platform from the console or the API
  to the composition root, which is the one place a platform picks its adapter;
  the stored run records the choice through its existing adapter and instruction
  version strings, and neither domain nor application knows about platforms.
- **Interfaces**: `visto` CLI (`setup`, `start`, `doctor`, `settings`, `run`,
  `inspect`, observe-only mode, `--platform` with `ios` offered only on macOS);
  local FastAPI execution and settings API; React console with a prompt-first
  start screen (one goal field with Device and Options chips, start notices, a
  short privacy line; the device list refreshes on its own while no device is
  ready and never while a run is open), a live-progress run view (latest AI
  reasoning, question block that answers and resumes, outcome block with one-click
  Rerun and Edit and rerun, step list grouped by AI decision with action overlay,
  details), a collapsible run-history sidebar (drawer on narrow screens), a
  settings page with the active AI provider and a write-only key per provider,
  statistics with daily-priced cost
  estimates and dictation; JSON/HTML report export. The device list shows both
  platforms with the platform visible, selectability follows each platform's own
  state vocabulary, and a run cannot be submitted until a device list has
  confirmed the device's platform.
- **Installation**: a wheel attached to GitHub Releases (built by a tag-triggered
  workflow, with the compiled console and the Appium lockfiles inside) is
  installed with `uv tool install`. `visto setup` downloads pinned,
  checksum-verified portable Node, JDK and Android platform-tools/build-tools
  into the Visto home (`~/.visto`) after the Android SDK license is accepted,
  then installs Appium from the shipped lockfile (UiAutomator2 everywhere, plus
  XCUITest on macOS). No system environment variable or `PATH` is changed.
  `visto start` runs Appium as a child process and the backend, which also
  serves the console, from one terminal. Settings (active AI provider, one key
  per provider in an owner-only secrets file whose earlier OpenAI-only form stays
  valid unchanged, iOS signing values, price refresh) are read at use;
  no environment variable or `.env` file configures them. The run
  database has numbered migrations with a backup before each, and a process
  registry keeps setup and migrations away from running Visto processes. See
  [installation](../local-development.md) and [iOS setup](../ios-setup.md).

## Verification state

- Local checks (`python scripts/verify.py`) cover backend tests, Ruff, build,
  frontend type-check/tests/build and companion integrity. They exercise
  software boundaries, not model judgement quality.
- The installation path (toolchain download and extraction, Appium install,
  `visto start` supervision, settings store and API, migrations, release
  workflow) has local checks, with fake downloads, processes and servers. The
  release workflow has run on GitHub for a version tag and attached a wheel that
  passes the wheel check; no clean host has been installed from a released wheel.
  The case-insensitive-filesystem archive guard is simulated, not observed on
  macOS.
- iOS has local checks against a fake HTTP wire for every adapter behaviour, and
  works on a physical iPhone once the setup is complete (WebDriverAgent built,
  signed and trusted, signing values saved, phone connected by USB cable): runs
  have been carried out end to end there. The XCUITest routes and the Xcode
  inventory commands were checked against driver documentation, driver sources
  and the installed toolchain's own output; `docs/references.md` records which
  claims rest on which evidence.
- Attachments have local checks only (ingest and PDF rendering with generated
  files, staging, domain validation, loop, provider request shapes, API, CLI,
  reports, console pure modules); no live run has used them, so how well models
  compare layouts and how they phrase findings is unknown. A password-protected PDF
  is covered by a trailer that declares encryption, not a real encrypted file. The
  new console components have no DOM tests and were not checked in a browser.
- The Anthropic and Gemini adapters have local checks only (fake wire, worker
  endpoints and headers, schema reduction, settings and key routes); no live
  request has been made to either provider, so account access, structured-output
  acceptance of the reduced schema, and Gemini's coordinate scale are unverified.
- Live smoke runs on a real phone have passed for positive and negative cases,
  pause/resume/cancel through the API, and the `long_press`/`enter` commands.
  This is preliminary smoke, not a labelled evaluation: the product is
  **unevaluated** for judgement quality.
- The console layout has been checked in a headless browser at desktop and 360 px
  widths, and a real Android run has passed from the start screen; the README
  demo is a recording of that run. Screen-reader behaviour has not been checked.
- Not yet exercised live: console Cancel button, pending question/answer from the
  console (question block, answer then resume), one-click Rerun, delayed UI, observe-only mode, CLI path, Appium/provider failure
  handling (expected: infrastructure failure, not a verdict).
- iOS not yet exercised live: the simulator path, the `connected` device mapping,
  rotation on hardware and the labelled acceptance scenario.

## Known issues

- Gemini's documentation states no image resize threshold; screenshots use the
  OpenAI limits, and tap accuracy on Gemini is unknown until a live run.
- `claude-haiku-4-5` has no entry in the public price list under that id, so its
  runs have no cost estimate.
- A turn with many large screenshots plus four attachment pages may exceed a
  provider's request size: Visto's own bound is 24 MB, Gemini documents 20 MB for
  inline data. Such a turn fails as an infrastructure error, not a verdict.

- AI false pass on procedure-shaped goals whose final state is already visible:
  the requested procedure is skipped and the run passes.
- Storage errors are logged without their original cause.
- An answer racing the run deadline returns 503.
- `cleanup_failed` is not durable.
- A failed session start whose outcome is uncertain leaves a device lease that needs
  manual cleanup. An explicit Appium refusal of the session request releases it, and
  on iOS a session created but found unusable is deleted once and the lease released
  when that delete succeeds.
- A failed session start is stored with a generic detail unless the adapter
  recognises the cause (iOS: phone not visible to Appium, WebDriverAgent launch
  failure); other causes, such as the exact `xcodebuild` error, are only in the
  Appium server log or the build result.
- The iOS tunnel, its sudo handling and the refresh reconnect have local checks
  only. Stopping the tunnel signals the sudo process Visto started and relies on sudo
  relaying the signal to the script (sudo was seen running a second sudo before node);
  that relay and `devicectl device info details` on a disconnected phone are
  unverified. The ownership checks cannot stop another process of the same user
  from replacing the files root runs.
- The tunnel script exits when no phone is on USB at start; Visto starts it again
  before the next run on a physical iPhone and waits for that phone's tunnel through
  the driver's registry. This relaunch has local checks only, and depends on the sudo
  credential cached at `visto start` still being valid. Simulators are told apart by
  their UUID-shaped device ID. `visto run` does not start the tunnel.
- After `visto start` restarts, the WebDriverAgent left on the phone is reused and
  refuses UI testing; the adapter deletes that session and retries once with
  `appium:useNewWDA`. The retry has local checks only; the stale-runner answers it
  recognises come from a real session.
- Nothing compares a requested platform with the attached device: a mismatch
  surfaces only as a session that Appium cannot start.
- No DOM tests for React components; the console's decisions are tested through
  pure modules (`steps.ts`, `start.ts`, `console.ts`, `form.ts`) only.
- The composer focus ring relies on CSS `:has()`; browsers without it show no
  ring on the goal field's frame.
- Transcription minutes are not included in cost statistics.

## Next work

1. Hands-on test round of the prompt-first console (start screen, automatic device
   discovery on iOS, question block, Rerun, history sidebar), then independent QA
   review of the console features, installation guide and Android commands.
2. Live checks for the scenarios listed under "Not yet exercised live", and a
   live smoke run with Anthropic and with Gemini (key verify, then one positive
   and one negative case) before calling those providers supported, and an
   attachments smoke with fictional layouts: matching UI, a missing element
   (expect passed with a finding), the same with a binding prompt (expect failed),
   and a layout that matches no screen (expect it reported).
3. Fix the known issues above, starting with the false-pass pattern.
4. Repeated labelled evaluation following the
   [evaluation protocol](../evaluation/protocol.md); agree numeric thresholds
   before running it.
5. Run the remaining iOS live checks: the simulator path, then the acceptance
   scenario on the simulator and the attached phone, following
   [iOS setup](../ios-setup.md) and [first device test](../first-device-test.md).
6. Live installation checks from a released wheel on a clean Windows host and a
   clean Mac (`visto setup`, `visto start`, Settings page, `visto doctor`, a run),
   then an upgrade from one release to the next with data and settings kept.
