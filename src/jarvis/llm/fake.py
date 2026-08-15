"""Fake-LLM für Tests und Trockenlauf ohne API-Key.

Antwortet deterministisch und streamt wortweise, damit der Satz-Chunker
und die Barge-in-Logik realistisch getestet werden können.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from jarvis.llm.base import (
    Capabilities,
    LLMProvider,
    LLMRequest,
    StreamChunk,
    Usage,
)

DEFAULT_REPLY = "Verstanden. Ich habe deine Nachricht erhalten. Was möchtest du als Nächstes tun?"


class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(
        self,
        replies: Sequence[str] | None = None,
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._replies = list(replies) if replies else []
        self._delay_s = delay_s
        self.requests: list[LLMRequest] = []

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            streaming=True,
            prompt_caching=True,
            context_window=200_000,
        )

    def _next_reply(self) -> str:
        if not self._replies:
            return DEFAULT_REPLY
        if len(self._replies) == 1:
            return self._replies[0]
        return self._replies.pop(0)

    async def stream(self, req: LLMRequest) -> AsyncIterator[StreamChunk]:
        self.requests.append(req)
        reply = self._next_reply()
        # Wortweise inklusive nachfolgendem Leerzeichen — so wie echte
        # Provider ihre Token liefern.
        for token in re.findall(r"\S+\s*", reply):
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield StreamChunk(text=token)
        yield StreamChunk(
            usage=Usage(
                input_tokens=sum(len(m.content) for m in req.messages) // 4,
                output_tokens=len(reply) // 4,
            )
        )
