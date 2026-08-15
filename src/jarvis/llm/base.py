"""LLM-Abstraktion.

Der Core kennt ausschließlich `LLMProvider` und die neutralen Datentypen
hier. Kein Anbieter-Schema (Anthropic-Blocks, OpenAI-Choices, …) darf über
diese Grenze. Jeder Adapter übersetzt in beide Richtungen.

Anbieterspezifische Fähigkeiten werden über `Capabilities` *abgefragt*,
nie vorausgesetzt — das ist der Mechanismus, der Lock-in verhindert.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class TaskClass(StrEnum):
    """Aufgabenklasse — der Router wählt daraus das Modell (Architektur §6)."""

    CLASSIFICATION = "classification"
    CONVERSATION = "conversation"
    PLANNING = "planning"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: list[Message]
    system: str = ""
    model: str | None = None
    max_output_tokens: int = 1024
    temperature: float | None = None
    # Stabiler Präfix (System-Prompt, später Tool-Schemas) darf gecacht
    # werden. Adapter, die kein Caching können, ignorieren das Flag.
    cache_system: bool = True


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """Ein Stück eines laufenden Streams.

    Genau eines von `text` (Token) oder `usage` (Abschluss) ist gesetzt.
    """

    text: str = ""
    usage: Usage | None = None


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@dataclass(frozen=True, slots=True)
class Capabilities:
    streaming: bool = True
    tools: bool = False
    vision: bool = False
    thinking: bool = False
    prompt_caching: bool = False
    context_window: int = 0


class LLMProvider(ABC):
    """Ein Sprachmodell-Anbieter."""

    name: str = "llm"

    @abstractmethod
    def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]:
        """Antwort tokenweise streamen.

        Muss auf Cancellation sofort reagieren — Barge-in bricht diesen
        Stream mitten im Satz ab.
        """
        raise NotImplementedError

    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Nicht-streamende Variante. Default sammelt den Stream ein."""
        parts: list[str] = []
        usage = Usage()
        async for chunk in self.stream(req):
            if chunk.text:
                parts.append(chunk.text)
            if chunk.usage is not None:
                usage = chunk.usage
        return LLMResponse(text="".join(parts), usage=usage, model=req.model or "")

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Verbindungen freigeben. Default: nichts zu tun."""
        return None
