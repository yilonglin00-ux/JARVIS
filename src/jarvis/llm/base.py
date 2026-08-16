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
from typing import Any


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    # Ergebnisse von Werkzeugaufrufen. Anthropic verpackt sie in eine
    # User-Nachricht, OpenAI in eine eigene Rolle — welche von beidem, ist
    # Sache des Adapters, nicht des Core.
    TOOL = "tool"


class TaskClass(StrEnum):
    """Aufgabenklasse — der Router wählt daraus das Modell (Architektur §6)."""

    CLASSIFICATION = "classification"
    CONVERSATION = "conversation"
    PLANNING = "planning"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Ein Werkzeug, so wie das Modell es zu sehen bekommt."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Das Modell möchte ein Werkzeug aufrufen."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Was von einem Werkzeugaufruf zurück ins Gespräch geht."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    # Nur auf Assistant-Nachrichten: die Werkzeuge, die das Modell wollte.
    tool_calls: tuple[ToolCall, ...] = ()
    # Nur auf Tool-Nachrichten: die Ergebnisse dazu.
    tool_results: tuple[ToolOutcome, ...] = ()


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
    # Stabiler Präfix — Werkzeugschemas und System-Prompt — darf gecacht
    # werden. Adapter, die kein Caching können, ignorieren das Flag.
    cache_system: bool = True
    tools: tuple[ToolSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """Ein Stück eines laufenden Streams.

    Genau eines von `text`, `tool_call` oder `usage` ist gesetzt. Ein
    `tool_call` kommt erst, wenn seine Argumente vollständig sind — halbe
    JSON-Fragmente über die Core-Grenze zu reichen, hätte jeden Konsumenten
    gezwungen, den Parser des jeweiligen Anbieters nachzubauen.
    """

    text: str = ""
    tool_call: ToolCall | None = None
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
