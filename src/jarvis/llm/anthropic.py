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
    ToolCall,
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
            # Ein Cache-Punkt speichert den gesamten Präfix bis hierhin.
            # Weil Anthropic in der Reihenfolge Werkzeuge → System →
            # Nachrichten aufbaut, sind die Werkzeugschemas damit
            # mitgecacht — und die sind der größere Block von beiden.
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    @staticmethod
    def _to_wire(messages: list[Message]) -> list[dict[str, Any]]:
        # System-Nachrichten gehören bei Anthropic in den `system`-Parameter,
        # nicht in die Nachrichtenliste.
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role is Role.SYSTEM:
                continue
            if m.role is Role.TOOL:
                # Werkzeugergebnisse reist Anthropic als User-Nachricht an.
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": outcome.call_id,
                                "content": outcome.content,
                                "is_error": outcome.is_error,
                            }
                            for outcome in m.tool_results
                        ],
                    }
                )
                continue
            if m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content.strip():
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                    for call in m.tool_calls
                )
                wire.append({"role": "assistant", "content": blocks})
                continue
            if m.content.strip():
                wire.append({"role": m.role.value, "content": m.content})
        return wire

    @staticmethod
    def _tool_params(req: LLMRequest) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in req.tools
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
        if req.tools:
            kwargs["tools"] = self._tool_params(req)
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

        # Werkzeugaufrufe erst aus der fertigen Nachricht. Anthropic streamt
        # die Argumente als JSON-Fragmente; sie hier zusammenzusetzen wäre
        # doppelte Arbeit und würde halbe Objekte über die Core-Grenze
        # lassen. Der Zeitverlust ist null — Werkzeugblöcke stehen ohnehin
        # am Ende der Nachricht.
        for block in getattr(final, "content", []):
            if getattr(block, "type", "") != "tool_use":
                continue
            raw = getattr(block, "input", {})
            yield StreamChunk(
                tool_call=ToolCall(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "")),
                    arguments=dict(raw) if isinstance(raw, dict) else {},
                )
            )

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
