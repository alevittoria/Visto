# Multimodal provider setup

Visto drives one AI provider at a time, chosen on the console's **Settings** page
or with `visto settings use-provider --provider <id>`. Each provider keeps its own
stored key, so switching never removes another provider's key. The active
provider is used by every new run; a run records its provider and model and keeps
them for its whole lifetime.

| Provider | Id | Models (default first) | Inference endpoint |
|---|---|---|---|
| OpenAI | `openai` | `gpt-6-luna`, `gpt-5.6-terra`, `gpt-6-sol` | `https://api.openai.com/v1/responses` |
| Anthropic | `anthropic` | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5` | `https://api.anthropic.com/v1/messages` |
| Google Gemini | `gemini` | `gemini-4-argon`, `gemini-4-carbon`, `gemini-3.8-flash` | `https://generativelanguage.googleapis.com/v1beta/interactions` |

OpenAI is the default provider, so an installation that stored only an OpenAI key
keeps working unchanged. Every listed model accepts image input and structured
JSON output according to its provider's documentation (see
[references](references.md)). `--model` and the console's model choice are limited
to the active provider's list. Account access and judgement quality are unverified
until a live run; the Anthropic and Gemini adapters have local checks only. There
is no provider fallback: a failing provider is an infrastructure failure, never a
silent switch to another one.

## Provider keys

Each provider needs its own API account and key with model access and billing; a
chat-app session is not an API credential. Enter a key on the **Settings** page
(opened automatically while the active provider has no key) or with
`visto settings set-key [--provider <id>]`, which reads one line from standard
input (a hidden prompt on a terminal) and never takes the key as an argument.
Without `--provider`, key actions apply to the active provider. Visto reads keys
from its settings store only: no environment variable or other file is consulted,
and the frontend never receives them.

The keys are saved in plain text in `secrets.json` in the Visto home, one field
per provider (`openai_api_key`, `anthropic_api_key`, `gemini_api_key`), written
atomically; on macOS and Linux the file is readable by your user only (mode 0600),
on Windows it inherits the access rules of your user profile folder. See
[artifact storage](artifact-storage.md#visto-home). No API response, log line,
error message, report or command output contains a key; the console and
`visto settings show` show only whether each is configured and its last four
characters. Replace or remove a key from the Settings page, or with `visto settings
set-key` and `visto settings delete-key`; removing one provider's key leaves the
others in place. A change applies to the next run without a restart; a running
run keeps the provider and key it started with.

**Verify** on the Settings page, or `visto settings verify-key`, retrieves the
provider's default model metadata with the stored key: `GET
https://api.openai.com/v1/models/<model>`, `GET
https://api.anthropic.com/v1/models/<model>` or `GET
https://generativelanguage.googleapis.com/v1beta/models/<model>`. It runs no
inference and is not billed. The result is one word: `ok`, `missing` (no key
stored), `unauthorized` (the key was rejected), `model_unavailable` (the key cannot
see the default model) or `unreachable` (network, proxy or TLS problem). Do not put
a real key into goals, answers, screenshots, example files or source.

## Requests

The adapters use each provider's documented REST wire format directly, with
standard-library HTTPS and no provider SDK dependency. A fixed hidden Python child
performs one HTTPS request to a fixed endpoint chosen by provider id; the parent
sends the key/request through stdin and removes any `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY` variable from the
inherited child environment, preserving proxy/TLS settings. Authentication is a
bearer token for OpenAI, `x-api-key` with `anthropic-version: 2026-10-21` for
Anthropic, and `x-goog-api-key` with `Api-Revision: 2026-05-20` for Gemini.
HTTP redirects are rejected so credentials/images stay bound to the fixed endpoint.
Deadline/cancellation kills and reaps that child and joins its local pipe task.
OS process creation itself is not interruptible; startup time counts against the
deadline and an expired startup receives no request packet. Killing a client
cannot guarantee remote processing or billing stopped after dispatch.
It submits one bounded structured decision
per inference with no automatic retries. Errors, refusal, incomplete output,
timeouts, invalid coordinates and references are technical failures. They do not
become a failed application test. Missing reported token usage remains unknown.

The decision schema is the same for every provider. Anthropic structured outputs
reject numeric bounds, string lengths and array maximums, and Gemini lists no
string length bound, so those keywords are removed from the schema sent to that
provider; every reply is still checked locally against the full schema before a
decision is accepted. Anthropic and Gemini requests allow 16,000 output tokens,
because their current models may think before answering; OpenAI requests allow
4,096.

The instructions are a shared core (`agent-core.md`) plus one versioned notes
file per platform, `android-agent-v4` and `ios-agent-v2` (on the shared core
`agent-core-v2.md`), rendered together for the run's platform and sent unchanged
to every provider; runs recorded with earlier versions keep theirs, and those
files stay renderable. All of them live under
`backend/prompts/` and are included in the wheel. Every turn rebuilds the same
logical conversation from immutable goal/constraints, bounded recent decisions,
working plan, actual pre/post images, technical receipt and attributed user
answers. There are no opaque remote continuation handles. Context overflow fails
explicitly rather than silently discarding original constraints. This is one
conversational AI, not an actor/judge pair. Software validates evidence
references; model quality requires the separately labelled evaluation protocol.
v2 changes only type_text semantics: one whole-value set on the
focused field; runs recorded with v1 keep their original identity.
v3 adds the `long_press` and `enter` commands; v2 runs keep theirs.
v4 (and iOS v2) add attachments: every turn lists the run's attachment pages, the
AI opens up to four with `view_attachments`, and its conclusion carries
`findings`. The instructions tell it to infer the attachments' role from the
goal, compare layouts by structure, order, fixed texts and style but never data
values, record differences as findings that by default do not change the verdict
unless the goal makes the layout binding, and report attachments it could not
match. Attachment pages are sized like screenshots before they are sent.

A screenshot larger than the size the provider shows its model unchanged is
downscaled locally, keeping its aspect ratio, before it is sent; the provider would
otherwise resize it itself and the model would answer in coordinates of an image
it was never told about. The limits are 2048 pixels per side and 2,500 patches of
32×32 pixels for OpenAI (`high` detail) and for Gemini; 2576 pixels on the long
edge and 4,784 patches of 28×28 pixels for Claude 4.7 and later models; 1568 pixels
and 1,568 patches for `claude-haiku-4-5`. The Gemini limit reuses the OpenAI one:
Gemini's documentation states no resize threshold, so its coordinate accuracy is
unverified until a live run. The dimensions sent with each image are those of the
image actually sent, the model answers in its pixels, and the adapter maps
commands back to source-screenshot pixels; commands in the history are shown in
the same scale. Stored screenshots, evidence and reports keep the original size.
Smaller screenshots are sent unchanged. Responses requesting unsupported
commands or fields are rejected. App text and screenshots cannot redefine the
goal or instructions. The AI may adapt its plan, ask a question or conclude
passed, failed or inconclusive using only supplied evidence.

## Connection diagnostics

Provider failures retain only fixed technical codes in CLI output and history:
`connection_refused`, `dns_error`, `tls_error`, `connection_error`, `timeout`,
`invalid_json`, `invalid_request`, `worker_start_error`, `worker_protocol_error`
or `http_NNN` for supported HTTP errors. Raw exceptions, proxy addresses and
provider response bodies are excluded. An infrastructure error is not an AI
application verdict; an attempted request may still incur unknown remote usage.

The worker retains the launching process's proxy configuration and TLS checks.
A restricted agent session may have a disabled network path even when Appium
and the Android device are accessible locally. Check the same CLI invocation
from an ordinary host terminal to distinguish environments. Do not remove proxy
variables, disable TLS, change the endpoint or add blind retries to work around
an execution policy. Behind a TLS-inspecting proxy, give Visto the proxy's
certificate bundle as described in
[installation](local-development.md#behind-a-tls-inspecting-proxy). `visto doctor`
does not contact the provider; `visto settings verify-key` checks the key and the
connection without inference.

## Data sent and stored

A live invocation sends the original goal, constraints, expected result,
the names and sizes of attached files and the attachment pages the AI asks to see,
screenshots, recent AI decisions, entered text/technical action details, receipts
and user clarifications to the active provider's inference endpoint. These may
contain private application data. Use a dedicated test account/device with
suitable data. No remote conversation storage is used: OpenAI and Gemini requests
set `store: false`, and Anthropic's Messages API keeps no conversation state. This
does not mean zero provider retention: each provider documents its own abuse
monitoring retention and organization-specific controls. See the
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data),
[Anthropic's API data retention](https://platform.claude.com/docs/en/build-with-claude/api-and-data-retention)
and the [Gemini data logging and sharing policy](https://ai.google.dev/gemini-api/docs/logs-policy).

Dictation in the console sends the operator's recording (not screenshots) to
`https://api.openai.com/v1/audio/transcriptions` with model `gpt-transcribe`. It
uses the stored OpenAI key whichever provider is active, and is unavailable while
no OpenAI key is stored. Only the returned text is kept, in the form field; the
audio is not stored. Transcription minutes are billed separately and are not
counted in run statistics or cost estimates.

Local evidence is retained for inspection until the operator removes the chosen
data directory. It is private filesystem data, not encrypted by this application
and not automatically uploaded or served through HTTP. Consult
[artifact storage](artifact-storage.md) and [first device test](first-device-test.md)
before a live invocation. No live call was needed for the adapters' fake tests.
