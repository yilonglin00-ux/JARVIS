"""Anthropic-Adapter.

Übersetzt zwischen den neutralen Core-Typen und dem Messages-API-Schema.
Prompt-Caching ist der wichtigste Kostenhebel des Projekts: der
System-Prompt ist über hunderte Turns am Tag identisch, wird aber ohne
`cache_control` jedes Mal voll berechnet (Architektur §16).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog

from jarvis.core.errors import ConfigurationError, ProviderError
from jarvis.llm.base import (
    Capabilities,
    LLMProvider,
    LLMRequest,
    Message,
    Role,
    StreamChunk,
    Usage,
)

log = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, *, default_model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY fehlt. Key in .env eintragen oder "
                "providers.llm in config/jarvis.yaml auf 'fake' setzen."
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - Abhängigkeit optional
            raise ConfigurationError(
                "Paket 'anthropic' nicht installiert: uv sync --extra providers"
            ) from exc
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            streaming=True,
            tools=True,
            vision=True,
            thinking=True,
            prompt_caching=True,
            context_window=200_000,
        )

    def _system_blocks(self, req: LLMRequest) -> list[dict[str, Any]] | None:
        if not req.system:
            return None
        block: dict[str, Any] = {"type": "text", "text": req.system}
        if req.cache_system:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    @staticmethod
    def _to_wire(messages: list[Message]) -> list[dict[str, str]]:
        # System-Nachrichten gehören bei Anthropic in den `system`-Parameter,
        # nicht in die Nachrichtenliste.
        return [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role is not Role.SYSTEM
        ]

    async def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {
            "model": req.model or self._default_model,
            "max_tokens": req.max_output_tokens,
            "messages": self._to_wire(req.messages),
        }
        system = self._system_blocks(req)
        if system is not None:
            kwargs["system"] = system
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield StreamChunk(text=text)
                final = await stream.get_final_message()
        except Exception as exc:
            raise ProviderError("anthropic", str(exc)) from exc

        usage = getattr(final, "usage", None)
        yield StreamChunk(
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            )
        )

    async def aclose(self) -> None:
        await self._client.close()
