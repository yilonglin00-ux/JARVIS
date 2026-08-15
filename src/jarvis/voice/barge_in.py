"""Host-Seite der Unterbrechung.

Die *Erkennung* passiert im Client (Silero-VAD im Browser), weil ein
Roundtrip zum Host 40–120 ms kostet und das Flush-Ziel bei unter 60 ms
liegt. Der Host bekommt die Unterbrechung also gemeldet, statt sie zu
entdecken (Architektur §5).

Seine Aufgabe ist die schwierigere Hälfte: herausfinden, *was der Nutzer
tatsächlich gehört hat*. Nur dieser Präfix darf in den Gesprächsverlauf.
Schreibt man die vollständig generierte Antwort hinein, glaubt JARVIS in
allen folgenden Turns, es hätte etwas gesagt, das nie erklang.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis.voice.audio_stream import pcm_duration_ms

_WORD_BOUNDARY = re.compile(r"\s")


@dataclass(slots=True)
class SpeechLedger:
    """Buchführung über einen Sprechvorgang: Text rein, Audiodauer raus.

    Eine exakte Zuordnung von Audiomillisekunden zu Textstellen liefert
    kein TTS-Provider. Deshalb wird über die Zeichenlänge interpoliert und
    anschließend auf eine Wortgrenze gerundet — lieber ein Wort zu wenig
    im Verlauf als ein Wort zu viel.
    """

    text: str = ""
    audio_ms: float = 0.0
    chunks: int = 0
    _sample_rates: set[int] = field(default_factory=set)

    def note_text(self, chunk: str) -> None:
        if not chunk:
            return
        self.text = f"{self.text} {chunk}".strip() if self.text else chunk

    def note_audio(self, num_bytes: int, sample_rate: int) -> None:
        self.audio_ms += pcm_duration_ms(num_bytes, sample_rate)
        self.chunks += 1
        self._sample_rates.add(sample_rate)

    def spoken_prefix(self, played_ms: float | None) -> str:
        """Der Teil des Textes, der bis `played_ms` erklungen sein dürfte.

        `played_ms` kommt vom Client, der als Einziger weiß, wie viel
        seines Puffers tatsächlich am Lautsprecher war. Fehlt die Angabe,
        gilt konservativ: nichts wurde gehört.
        """
        if not self.text:
            return ""
        if played_ms is None:
            return ""
        if self.audio_ms <= 0:
            return ""
        if played_ms >= self.audio_ms:
            return self.text

        ratio = max(0.0, played_ms / self.audio_ms)
        cut = int(len(self.text) * ratio)
        return _snap_to_word(self.text, cut)

    def reset(self) -> None:
        self.text = ""
        self.audio_ms = 0.0
        self.chunks = 0
        self._sample_rates.clear()


def _snap_to_word(text: str, cut: int) -> str:
    """Auf die letzte abgeschlossene Wortgrenze vor `cut` zurückgehen."""
    if cut <= 0:
        return ""
    if cut >= len(text):
        return text.strip()
    head = text[:cut]
    match = None
    for match in _WORD_BOUNDARY.finditer(head):  # noqa: B007 - letztes Vorkommen
        pass
    if match is None:
        return ""
    return head[: match.start()].strip()
