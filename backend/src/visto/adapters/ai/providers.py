"""The AI providers Visto can drive and their operator-selectable models (docs/references.md).

One provider is active at a time; each provider keeps its own stored key, so
switching never discards another provider's key.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    label: str
    # Default first.
    models: tuple[str, ...]
    # Provider name in the public price list (adapters/pricing).
    price_provider: str

    @property
    def default_model(self) -> str:
        return self.models[0]


OPENAI = Provider("openai", "OpenAI", ("gpt-6-luna", "gpt-5.6-terra", "gpt-6-sol"), "openai")
ANTHROPIC = Provider(
    "anthropic",
    "Anthropic",
    ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"),
    "anthropic",
)
GEMINI = Provider(
    "gemini", "Google Gemini", ("gemini-4-argon", "gemini-4-carbon", "gemini-3.8-flash"), "google"
)
PROVIDERS: dict[str, Provider] = {p.id: p for p in (OPENAI, ANTHROPIC, GEMINI)}
DEFAULT_PROVIDER = OPENAI.id
# Speech-to-text for dictated goals is offered by this provider only.
TRANSCRIPTION_PROVIDER = OPENAI.id


def provider(identifier: str) -> Provider:
    """The catalog entry; unknown identifiers fail loudly."""
    try:
        return PROVIDERS[identifier]
    except (KeyError, TypeError):
        raise ValueError("Unsupported AI provider") from None
