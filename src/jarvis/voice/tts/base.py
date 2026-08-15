"""Text-to-Speech-Abstraktion.

Harte Anforderung an jede Implementierung: **Chunk-Streaming**. Die
Ausgabe muss innerhalb eines Frames abbrechbar sein, sonst ist Barge-in
unmöglich (Architektur §5). Ein Provider, der nur fertige Dateien
liefert, ist für JARVIS untauglich.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceSpec:
    """Stimmwunsch des Core.

    Enthält von Anfang an mehr Felder, als jeder einzelne Provider kann.
    Nicht Unterstütztes wird geloggt und ignoriert, nie als Fehler
    behandelt — sonst wird der Wechsel des Providers zum Umbau.
    """

    voice_id: str = ""
    language: str = "de"
    speed: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0


@dataclass(frozen=True, slots=True)
class AudioChunk:
    data: bytes
    sample_rate: int = 24000
    # Fortlaufend je Turn. Der Client erkennt daran Lücken und kann nach
    # einem Barge-in alte Chunks sicher verwerfen.
    seq: int = 0


class TextToSpeech(ABC):
    name: str = "tts"

    @abstractmethod
    def synthesize_stream(
        self,
        text: AsyncIterator[str] | str,
        *,
        voice: VoiceSpec,
    ) -> AsyncIterator[AudioChunk]:
        """Text in Audio wandeln, während er noch entsteht.

        Ein `AsyncIterator[str]` als Eingabe ist der Grund, warum JARVIS
        zu sprechen beginnt, bevor das LLM fertig ist.
        """
        raise NotImplementedError

    async def stop(self) -> None:
        """Laufende Synthese sofort beenden. Muss ohne Verzögerung greifen."""
        return None

    @property
    @abstractmethod
    def output_sample_rate(self) -> int:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
