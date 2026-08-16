"""Fake-LLM für Tests und Trockenlauf ohne API-Key.

Antwortet deterministisch und streamt wortweise, damit der Satz-Chunker
und die Barge-in-Logik realistisch getestet werden können.

Für die Werkzeugschleife lassen sich Züge skripten: ein `ToolTurn` fordert
Werkzeuge an, der nächste Eintrag ist dann die Antwort, die das Modell
nach dem Ergebnis gibt. Damit ist der komplette Weg — Modell will
Werkzeug, Registry fragt nach, Nutzer bestätigt, Ergebnis geht zurück —
ohne Netz prüfbar.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.llm.base import (
    Capabilities,
    LLMProvider,
    LLMRequest,
    StreamChunk,
    ToolCall,
    Usage,
)

DEFAULT_REPLY = "Verstanden. Ich habe deine Nachricht erhalten. Was möchtest du als Nächstes tun?"


@dataclass(frozen=True, slots=True)
class ToolTurn:
    """Ein Zug, in dem das Modell Werkzeuge anfordert."""

    text: str = ""
    calls: Sequence[tuple[str, dict[str, Any]]] = field(default_factory=tuple)


Scripted = str | ToolTurn


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(
        self,
        replies: Sequence[Scripted] | None = None,
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._replies: list[Scripted] = list(replies) if replies else []
        self._delay_s = delay_s
        self.requests: list[LLMRequest] = []
        self._call_seq = 0

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            streaming=True,
            tools=True,
            prompt_caching=True,
            context_window=200_000,
        )

    @property
    def offered_tools(self) -> list[str]:
        """Welche Werkzeuge das Modell zuletzt zu sehen bekam."""
        if not self.requests:
            return []
        return [spec.name for spec in self.requests[-1].tools]

    def _next_reply(self) -> Scripted:
        if not self._replies:
            return DEFAULT_REPLY
        if len(self._replies) == 1:
            return self._replies[0]
        return self._replies.pop(0)

    async def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(req)
        reply = self._next_reply()
        text = reply if isinstance(reply, str) else reply.text

        # Wortweise inklusive nachfolgendem Leerzeichen — so wie echte
        # Provider ihre Token liefern.
        for token in re.findall(r"\S+\s*", text):
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield StreamChunk(text=token)

        if isinstance(reply, ToolTurn):
            for name, arguments in reply.calls:
                self._call_seq += 1
                yield StreamChunk(
                    tool_call=ToolCall(
                        id=f"call_{self._call_seq}",
                        name=name,
                        arguments=dict(arguments),
                    )
                )

        yield StreamChunk(
            usage=Usage(
                input_tokens=sum(len(m.content) for m in req.messages) // 4,
                output_tokens=len(text) // 4,
            )
        )
