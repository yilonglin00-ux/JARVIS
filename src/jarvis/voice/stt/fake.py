"""Fake-STT: erzeugt Transkripte nach Audiomenge, ohne Netz.

Modelliert das Verhalten, auf das sich die Pipeline verlässt: erst
Partials, dann genau ein Final pro Segment. Ein Segment endet, sobald
genug Stille-Frames (nur Nullen) gesehen wurden — dasselbe Endpointing-
Prinzip wie bei echten Providern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from jarvis.voice.stt.base import SpeechToText, Transcript

SILENCE_FRAMES_TO_ENDPOINT = 8  # 8 × 20 ms = 160 ms Stille


class FakeSTT(SpeechToText):
    name = "fake"

    def __init__(
        self,
        utterances: Sequence[str] | None = None,
        *,
        partials_per_utterance: int = 2,
    ) -> None:
        self._utterances = list(utterances or ["Hallo JARVIS, wie geht es dir?"])
        self._partials = max(0, partials_per_utterance)
        self.received_bytes = 0

    @property
    def languages(self) -> set[str]:
        return {"de", "en"}

    async def transcribe_stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str | None = None,
    ) -> AsyncIterator[Transcript]:
        index = 0
        silence_run = 0
        had_speech = False

        async for frame in audio:
            self.received_bytes += len(frame)
            is_silence = not any(frame)
            if is_silence:
                silence_run += 1
            else:
                silence_run = 0
                had_speech = True

            if had_speech and silence_run == SILENCE_FRAMES_TO_ENDPOINT:
                if index >= len(self._utterances):
                    return
                text = self._utterances[index]
                index += 1
                for step in range(1, self._partials + 1):
                    cut = max(1, len(text) * step // (self._partials + 1))
                    yield Transcript(text=text[:cut], is_final=False)
                yield Transcript(text=text, is_final=True, confidence=0.95)
                had_speech = False
