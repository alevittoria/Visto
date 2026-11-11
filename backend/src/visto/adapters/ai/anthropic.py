"""Anthropic Messages REST adapter.

Wire contract: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
and https://platform.claude.com/docs/en/build-with-claude/vision .
"""

import base64

from visto.application.ports import PortError
from visto.domain.models import ModelUsage

from .agent import Screenshot, VisionAgent, single_text, token_usage
from .imaging import CLAUDE_HIGH_LIMITS, CLAUDE_STANDARD_LIMITS, ImageLimits
from .schema import SCHEMA, without_keywords

# Structured outputs reject numeric and string bounds and array sizes above one;
# the decoder still enforces them locally.
UNSUPPORTED = frozenset(("minimum", "maximum", "maxLength", "maxItems"))
OUTPUT_SCHEMA = without_keywords(SCHEMA, UNSUPPORTED)
# Offered models on the standard resolution tier; the others are Claude 4.7 or later.
STANDARD_RESOLUTION = frozenset(("claude-haiku-4-5",))


class AnthropicAgent(VisionAgent):
    provider = "anthropic"
    # Models that think before answering spend part of this bound on thinking.
    default_max_output_tokens = 16000

    def image_limits(self) -> ImageLimits:
        if self.model in STANDARD_RESOLUTION:
            return CLAUDE_STANDARD_LIMITS
        return CLAUDE_HIGH_LIMITS

    def build_request(self, metadata_text: str, screenshots: list[Screenshot]) -> dict:
        content = [{"type": "text", "text": metadata_text}]
        for shot in screenshots:
            content.extend(
                [
                    {"type": "text", "text": shot.description},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": shot.media_type,
                            "data": base64.b64encode(shot.data).decode("ascii"),
                        },
                    },
                ]
            )
        return {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": self._instructions,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        }

    def read_reply(self, response: dict) -> tuple[str, ModelUsage]:
        stop = response.get("stop_reason")
        if stop == "refusal":
            raise PortError("provider: refusal")
        if stop != "end_turn":
            raise PortError("provider: incomplete_or_failed_response")
        # Thinking blocks precede the answer and are not part of the decision.
        texts = [part["text"] for part in response["content"] if part.get("type") == "text"]
        return single_text(texts), token_usage(
            response.get("usage"), "input_tokens", "output_tokens"
        )
