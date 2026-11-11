"""Multimodal provider boundary."""

from .agent import INSTRUCTION_VERSION, INSTRUCTION_VERSIONS, load_instructions
from .anthropic import AnthropicAgent
from .gemini import GeminiAgent
from .openai import OpenAIAgent
from .providers import DEFAULT_PROVIDER, PROVIDERS, TRANSCRIPTION_PROVIDER, Provider, provider
from .transcription import transcribe
from .transport import AUDIO_EXTENSIONS, MAX_AUDIO_BYTES, TRANSCRIPTION_MODEL

# The one place a provider selects its agent adapter.
AGENTS = {
    OpenAIAgent.provider: OpenAIAgent,
    AnthropicAgent.provider: AnthropicAgent,
    GeminiAgent.provider: GeminiAgent,
}

__all__ = [
    "AGENTS",
    "AUDIO_EXTENSIONS",
    "AnthropicAgent",
    "DEFAULT_PROVIDER",
    "GeminiAgent",
    "INSTRUCTION_VERSION",
    "INSTRUCTION_VERSIONS",
    "MAX_AUDIO_BYTES",
    "OpenAIAgent",
    "PROVIDERS",
    "Provider",
    "TRANSCRIPTION_MODEL",
    "TRANSCRIPTION_PROVIDER",
    "load_instructions",
    "provider",
    "transcribe",
]
