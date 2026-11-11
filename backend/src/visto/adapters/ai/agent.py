"""Shared runtime conversation turn: one stateless multimodal request per decision.

Each provider adapter supplies only its wire request and how to read its reply; the
context, screenshots, bounds and decision decoding are the same for every provider.
No SDK retries, hidden fallback or remote state.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from importlib.resources import files
from pathlib import Path

from visto.adapters.devices import Platform
from visto.application.context import AgentContext, AgentReply
from visto.application.ports import PortError
from visto.domain.models import ModelUsage

from .imaging import (
    OPENAI_LIMITS,
    ImageLimits,
    Scale,
    present,
    presented_history,
    presented_size,
)
from .providers import provider as catalog_entry
from .schema import decode_decision
from .transport import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, request_response

INSTRUCTION_VERSIONS = {
    Platform.ANDROID: "android-agent-v4",
    Platform.IOS: "ios-agent-v2",
}
# The shared core each version renders with; earlier versions keep their own text.
INSTRUCTION_CORES = {
    "android-agent-v3": "agent-core.md",
    "ios-agent-v1": "agent-core.md",
    "android-agent-v4": "agent-core-v2.md",
    "ios-agent-v2": "agent-core-v2.md",
}
PLATFORM_NOTES_MARKER = "\n<!-- platform notes -->\n"
PLATFORM_NAMES = {Platform.ANDROID: "Android", Platform.IOS: "iOS"}
INSTRUCTION_VERSION = INSTRUCTION_VERSIONS[Platform.ANDROID]


def history_data(value):
    """Retain record/command variants which dataclasses.asdict alone would erase."""
    if is_dataclass(value):
        return {
            "record_type": type(value).__name__,
            **{f.name: history_data(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, tuple):
        return [history_data(v) for v in value]
    return value


def _prompt(name: str) -> str:
    packaged = files("visto").joinpath(f"prompts/{name}")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Source-tree execution; wheels contain the resource at the path above.
    return (Path(__file__).resolve().parents[4] / "prompts" / name).read_text(encoding="utf-8")


def load_instructions(platform: Platform = Platform.ANDROID) -> str:
    """The current instructions for one platform; unknown platforms fail loudly."""
    return render_instructions(INSTRUCTION_VERSIONS[platform], platform)


def render_instructions(version: str, platform: Platform) -> str:
    """Render one version's core with its platform notes."""
    # A platform file holds the type_text semantics, the marker line, then the notes.
    type_text, marker, notes = _prompt(f"{version}.md").partition(PLATFORM_NOTES_MARKER)
    if not marker:
        raise ValueError(f"{version}.md is missing the platform notes marker")
    return (
        _prompt(INSTRUCTION_CORES[version])
        .replace("{{PLATFORM}}", PLATFORM_NAMES[platform])
        .replace("{{TYPE_TEXT_SEMANTICS}}", type_text)
        .replace("{{PLATFORM_NOTES}}", notes)
    )


@dataclass(frozen=True, slots=True)
class Screenshot:
    """One presented image and the JSON line that describes it to the model."""

    description: str
    media_type: str
    data: bytes


class VisionAgent:
    """Base class; subclasses set `provider` and implement the two wire methods."""

    provider = ""
    # Used when no model is passed; None means the catalog default.
    default_model: str | None = None
    default_max_output_tokens = 4096

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        platform: Platform = Platform.ANDROID,
        transport=None,
        max_output_tokens: int | None = None,
    ):
        if model is None:
            model = self.default_model or catalog_entry(self.provider).default_model
        if max_output_tokens is None:
            max_output_tokens = self.default_max_output_tokens
        if not api_key.strip() or not model.strip():
            raise ValueError("Provider key and model are required")
        if type(max_output_tokens) is not int or not 512 <= max_output_tokens <= 16384:
            raise ValueError("Invalid output token bound")
        self._key = api_key
        self.model = model
        self._transport = transport
        self.max_output_tokens = max_output_tokens
        self.platform = platform
        self._instructions = load_instructions(platform)

    @property
    def schema_name(self) -> str:
        return INSTRUCTION_VERSIONS[self.platform].replace("-", "_")

    def image_limits(self) -> ImageLimits:
        """Sizes the model sees unchanged, so its pixels equal the stated ones."""
        return OPENAI_LIMITS

    def build_request(self, metadata_text: str, screenshots: list[Screenshot]) -> dict:
        raise NotImplementedError

    def read_reply(self, response: dict) -> tuple[str, ModelUsage]:
        """The single decision text and token usage; raise PortError for refusals."""
        raise NotImplementedError

    async def decide(self, context: AgentContext, timeout_ms: int) -> AgentReply:
        identity = context.run.configuration.identity
        if (identity.provider, identity.model, identity.instruction_version) != (
            self.provider,
            self.model,
            INSTRUCTION_VERSIONS[self.platform],
        ):
            raise PortError("provider: identity_mismatch")
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("Invalid inference timeout")
        if len(context.images) > 8 or len(context.recent_decisions) > 20:
            raise PortError("provider: context_limit")
        scales = {}
        limits = self.image_limits()
        for image in context.images:
            o = image.observation
            source = (o.width, o.height)
            scales[o.observation_id] = Scale(source, presented_size(*source, limits))
        current = scales[context.current_observation_id]
        metadata = {
            # Attachments are listed separately, without private storage references.
            "original_goal": {
                k: v for k, v in asdict(context.run.goal).items() if k != "attachments"
            },
            "configuration": asdict(context.run.configuration),
            "current_observation_id": context.current_observation_id,
            "working_plan": context.working_plan,
            "previous_action": history_data(
                presented_history(context.previous_action, scales, current)
            ),
            "previous_receipt": (
                asdict(context.previous_receipt) if context.previous_receipt else None
            ),
            "recent_decisions": [
                history_data(presented_history(d, scales, current))
                for d in context.recent_decisions
            ],
            "attributed_user_answers": [asdict(a) for a in context.answers],
            "attachments": [
                {
                    "attachment_id": page.attachment_id,
                    "name": page.name,
                    "page": page.page,
                    "page_count": page.page_count,
                    "width": page.width,
                    "height": page.height,
                }
                for page in context.run.goal.attachments
            ],
        }
        metadata_text = json.dumps(metadata, default=str, ensure_ascii=False)
        if len(metadata_text) > 100000:
            raise PortError("provider: context_limit")
        screenshots = []
        for image in context.images:
            o = image.observation
            scale = scales[o.observation_id]
            media_type, data = o.screenshot.media_type, image.content
            if scale.presented != scale.source:
                try:
                    media_type, data = "image/png", present(data, scale.presented)
                except Exception:
                    raise PortError("provider: unreadable_screenshot") from None
            width, height = scale.presented
            description = json.dumps(
                {
                    "observation_id": o.observation_id,
                    "width": width,
                    "height": height,
                    "rotation": o.rotation,
                    "captured_at": o.captured_at.isoformat(),
                }
            )
            screenshots.append(Screenshot(description, media_type, data))
        for attachment in context.attachments:
            page = attachment.page
            source = (page.width, page.height)
            presented = presented_size(*source, limits)
            media_type, data = page.image.media_type, attachment.content
            if presented != source:
                try:
                    media_type, data = "image/png", present(data, presented)
                except Exception:
                    raise PortError("provider: unreadable_attachment") from None
            description = json.dumps(
                {
                    "attachment_id": page.attachment_id,
                    "name": page.name,
                    "page": page.page,
                    "width": presented[0],
                    "height": presented[1],
                }
            )
            screenshots.append(Screenshot(description, media_type, data))
        request = self.build_request(metadata_text, screenshots)
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise PortError("provider: context_limit")
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                if self._transport is None:
                    response = await request_response(
                        self._key, payload, timeout_ms, provider=self.provider
                    )
                else:
                    # Explicit synchronous fakes only. Production owns a killable
                    # subprocess, never an uninterruptible default-executor job.
                    response = self._transport(self._key, payload, timeout_ms)
            if type(response) is not dict:
                raise PortError("provider: incomplete_or_failed_response")
            text, usage = self.read_reply(response)
            if len(text) > MAX_RESPONSE_BYTES:
                raise PortError("provider: missing_or_multiple_decisions")
            decision = decode_decision(json.loads(text), context, usage, current.to_source)
            return AgentReply(decision)
        except PortError:
            raise
        except TimeoutError:
            raise PortError("provider: timeout") from None
        except Exception:
            raise PortError("provider: malformed_response") from None


def single_text(texts: list[str]) -> str:
    if len(texts) != 1:
        raise PortError("provider: missing_or_multiple_decisions")
    return texts[0]


def token_usage(usage: object, input_name: str, output_name: str) -> ModelUsage:
    usage = usage if isinstance(usage, dict) else {}
    return ModelUsage(usage.get(input_name), usage.get(output_name))
