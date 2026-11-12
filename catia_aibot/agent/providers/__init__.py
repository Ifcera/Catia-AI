"""Seçilebilir LLM sağlayıcıları."""
from __future__ import annotations

from .base import Provider, ProviderError, StepResult, StepUsage, StopRequested, ToolCall, ToolOutput
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .openai_provider import OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {
    ClaudeProvider.name: ClaudeProvider,
    OpenAIProvider.name: OpenAIProvider,
    GeminiProvider.name: GeminiProvider,
}


def make_provider(name: str, model: str | None = None, api_key: str | None = None, **kw) -> Provider:
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Bilinmeyen sağlayıcı '{name}'. Seçenekler: {list(PROVIDERS)}") from None
    return cls(model, api_key, **kw)


__all__ = ["PROVIDERS", "make_provider", "Provider", "ProviderError", "StepResult", "StepUsage", "StopRequested",
           "ToolCall", "ToolOutput", "ClaudeProvider", "OpenAIProvider", "GeminiProvider"]
