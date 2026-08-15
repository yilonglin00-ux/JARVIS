"""Speech-to-Text-Abstraktion.

Bewusst minimal gehalten: ein Audiostrom rein, ein Strom von Transkripten
raus. Alles, was ein Provider darüber hinaus kann (Diarisierung,
Keyword-Boosting, Wortzeiten), gehört in seine eigene Konfiguration und
nicht in dieses Interface — sonst wird es unaustauschbar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    is_final: bool = False
    confidence: float | None = None


class SpeechToText(ABC):
    name: str = "stt"

    @abstractmethod
    def transcribe_stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str | None = None,
    ) -> AsyncIterator[Transcript]:
        """Rohes PCM (16 kHz, 16 bit, mono) transkribieren.

        Liefert laufend Partials und pro erkanntem Segment ein Final.
        Der Aufrufer entscheidet, was ein Turn ist — nicht der Provider.
        """
        raise NotImplementedError

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    @abstractmethod
    def languages(self) -> set[str]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
