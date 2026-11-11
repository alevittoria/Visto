"""OpenAI Responses REST adapter.

Wire contract: https://developers.openai.com/api/docs/guides/structured-outputs
and https://developers.openai.com/api/docs/guides/images-vision .
"""

import base64

from visto.application.ports import PortError
from visto.domain.models import ModelUsage

from .agent import Screenshot, VisionAgent, single_text, token_usage
from .schema import SCHEMA


class OpenAIAgent(VisionAgent):
    provider = "openai"
    default_model = "gpt-6-sol"

    def build_request(self, metadata_text: str, screenshots: list[Screenshot]) -> dict:
        content = [{"type": "input_text", "text": metadata_text}]
        for shot in screenshots:
            content.extend(
                [
                    {"type": "input_text", "text": shot.description},
                    {
                        "type": "input_image",
                        "image_url": (
                            f"data:{shot.media_type};base64,"
                            + base64.b64encode(shot.data).decode("ascii")
                        ),
                        "detail": "high",
                    },
                ]
            )
        return {
            "model": self.model,
            "store": False,
            "instructions": self._instructions,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": self.schema_name,
                    "strict": True,
                    "schema": SCHEMA,
                }
            },
        }

    def read_reply(self, response: dict) -> tuple[str, ModelUsage]:
        if response.get("status") != "completed":
            raise PortError("provider: incomplete_or_failed_response")
        texts = []
        for item in response["output"]:
            if item.get("type") != "message":
                continue
            for part in item["content"]:
                if part.get("type") == "refusal":
                    raise PortError("provider: refusal")
                if part.get("type") == "output_text":
                    texts.append(part["text"])
        return single_text(texts), token_usage(
            response.get("usage"), "input_tokens", "output_tokens"
        )
