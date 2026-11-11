"""Google Gemini Interactions REST adapter.

Wire contract: https://ai.google.dev/api/interactions-api ,
https://ai.google.dev/gemini-api/docs/structured-output and
https://ai.google.dev/gemini-api/docs/interactions/image-understanding .
"""

import base64

from visto.application.ports import PortError
from visto.domain.models import ModelUsage

from .agent import Screenshot, VisionAgent, token_usage
from .schema import SCHEMA, without_keywords

# Structured outputs list no string length bound; the decoder still enforces it.
UNSUPPORTED = frozenset(("maxLength",))
OUTPUT_SCHEMA = without_keywords(SCHEMA, UNSUPPORTED)


class GeminiAgent(VisionAgent):
    provider = "gemini"
    # Thinking models spend part of this bound on thinking.
    default_max_output_tokens = 16000

    def build_request(self, metadata_text: str, screenshots: list[Screenshot]) -> dict:
        content = [{"type": "text", "text": metadata_text}]
        for shot in screenshots:
            content.extend(
                [
                    {"type": "text", "text": shot.description},
                    {
                        "type": "image",
                        "data": base64.b64encode(shot.data).decode("ascii"),
                        "mime_type": shot.media_type,
                        "resolution": "high",
                    },
                ]
            )
        return {
            "model": self.model,
            # Stateless: nothing is kept server-side for later retrieval.
            "store": False,
            "system_instruction": self._instructions,
            "input": content,
            "generation_config": {"max_output_tokens": self.max_output_tokens},
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": OUTPUT_SCHEMA,
            },
        }

    def read_reply(self, response: dict) -> tuple[str, ModelUsage]:
        if response.get("status") != "completed":
            raise PortError("provider: incomplete_or_failed_response")
        texts = [
            part["text"]
            for step in response["steps"]
            if step.get("type") == "model_output"
            for part in step.get("content") or ()
            if part.get("type") == "text"
        ]
        if not texts:
            raise PortError("provider: missing_or_multiple_decisions")
        # A reply may arrive split across text parts of one output.
        return "".join(texts), token_usage(
            response.get("usage"), "total_input_tokens", "total_output_tokens"
        )
