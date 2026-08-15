"""Fake-TTS: erzeugt Stille proportional zur Textlänge.

Damit lässt sich die vollständige Sprachschleife inklusive Barge-in ohne
Netz und ohne Lautsprecher testen — auch die Frage, wie viel Audio zum
Zeitpunkt der Unterbrechung bereits erzeugt war.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from jarvis.voice.tts.base import AudioChunk, TextToSpeech, VoiceSpec

SAMPLE_RATE = 24000
CHUNK_MS = 40
BYTES_PER_CHUNK = int(SAMPLE_RATE * CHUNK_MS / 1000) * 2
# Grobe Sprechgeschwindigkeit: ~14 Zeichen pro Sekunde.
CHARS_PER_SECOND = 14.0


class FakeTTS(TextToSpeech):
    name = "fake"

    def __init__(self, *, realtime: bool = False) -> None:
        self._realtime = realtime
        self._stopped = False
        self.spoken: list[str] = []

    @property
    def output_sample_rate(self) -> int:
        return SAMPLE_RATE

    async def stop(self) -> None:
        self._stopped = True

    def is_stopped(self) -> bool:
        # Als Methode und nicht als Attributzugriff, damit die Prüfung in
        # jeder Schleifenrunde wirklich neu ausgewertet wird.
        return self._stopped

    async def synthesize_stream(
        self,
        text: AsyncIterator[str] | str,
        *,
        voice: VoiceSpec,
    ) -> AsyncIterator[AudioChunk]:
        self._stopped = False
        seq = 0
        async for piece in _as_stream(text):
            if self.is_stopped():
                return
            self.spoken.append(piece)
            duration_s = max(len(piece), 1) / CHARS_PER_SECOND
            for _ in range(max(1, round(duration_s * 1000 / CHUNK_MS))):
                if self.is_stopped():
                    return
                if self._realtime:
                    await asyncio.sleep(CHUNK_MS / 1000)
                yield AudioChunk(
                    data=b"\x00" * BYTES_PER_CHUNK,
                    sample_rate=SAMPLE_RATE,
                    seq=seq,
                )
                seq += 1


async def _as_stream(text: AsyncIterator[str] | str) -> AsyncIterator[str]:
    if isinstance(text, str):
        yield text
        return
    async for piece in text:
        yield piece
