"""Satz-Chunker — der größte Latenzhebel im System.

Ohne ihn wartet man LLM-Zeit *plus* TTS-Zeit, bevor der erste Ton kommt.
Mit ihm beginnt die Sprachausgabe an der ersten Satzgrenze, während das
Modell noch schreibt (Architektur §4).

Der erste Chunk darf deutlich kürzer sein als die folgenden: er
bestimmt die wahrgenommene Reaktionszeit, während für alle weiteren nur
zählt, dass die Wiedergabe nicht abreißt.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

TERMINATORS = ".!?…"
# Weiche Grenzen: hier wird nur getrennt, wenn der Chunk sonst zu lang wird.
SOFT_TERMINATORS = ",;:"

# Abkürzungen, nach deren Punkt kein Satz endet. Ohne diese Liste zerhackt
# "z. B." die Ausgabe mitten im Wort.
ABBREVIATIONS = frozenset(
    {
        "z",
        "b",
        "d",
        "h",
        "u",
        "a",
        "s",
        "o",
        "ca",
        "bzw",
        "etc",
        "evtl",
        "ggf",
        "inkl",
        "max",
        "min",
        "mio",
        "mrd",
        "nr",
        "usw",
        "vgl",
        "dr",
        "prof",
        "str",
        "bspw",
        "sog",
    }
)

_LAST_WORD = re.compile(r"([\w]+)$", re.UNICODE)


class SentenceChunker:
    """Zerlegt einen Textstrom in sprechbare Stücke.

    Getrennt wird nur an Grenzen, die im Puffer bereits *abgeschlossen*
    sind — also Satzzeichen, auf das schon ein Leerzeichen folgt. Dadurch
    entstehen aus "3.14" oder "Dr." keine falschen Grenzen, ohne dass der
    Chunker in die Zukunft schauen müsste.
    """

    def __init__(
        self,
        *,
        first_chunk_min_chars: int = 12,
        min_chars: int = 40,
        max_chars: int = 240,
    ) -> None:
        self._first_min = first_chunk_min_chars
        self._min = min_chars
        self._max = max_chars
        self._buffer = ""
        self._emitted = 0

    @property
    def pending(self) -> str:
        return self._buffer

    def _min_chars(self) -> int:
        return self._first_min if self._emitted == 0 else self._min

    def feed(self, text: str) -> list[str]:
        """Neuen Text aufnehmen und alle fertigen Stücke zurückgeben."""
        self._buffer += text
        chunks: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            chunk, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:]
            if chunk:
                chunks.append(chunk)
                self._emitted += 1
        return chunks

    def flush(self) -> str:
        """Rest ausgeben — am Ende der Antwort, wenn kein Satzzeichen mehr kommt."""
        rest, self._buffer = self._buffer.strip(), ""
        if rest:
            self._emitted += 1
        return rest

    def reset(self) -> None:
        self._buffer = ""
        self._emitted = 0

    # --- intern --------------------------------------------------------------

    def _find_cut(self) -> int | None:
        minimum = self._min_chars()
        hard = self._scan(TERMINATORS, minimum)
        if hard is not None:
            return hard
        # Erst wenn der Chunk sonst zu lang würde, auch an Kommas trennen —
        # und notfalls am letzten Leerzeichen.
        if len(self._buffer) >= self._max:
            soft = self._scan(SOFT_TERMINATORS, minimum)
            if soft is not None:
                return soft
            space = self._buffer.rfind(" ", minimum, self._max)
            if space > 0:
                return space + 1
        return None

    def _scan(self, terminators: str, minimum: int) -> int | None:
        for index, char in enumerate(self._buffer):
            if char not in terminators or index + 1 < minimum:
                continue
            following = self._buffer[index + 1 : index + 2]
            if not following or not following.isspace():
                # Grenze noch nicht abgeschlossen (oder Dezimalzahl).
                continue
            if char == "." and self._is_abbreviation(index):
                continue
            return index + 2  # Satzzeichen und das folgende Leerzeichen mitnehmen
        return None

    def _is_abbreviation(self, dot_index: int) -> bool:
        match = _LAST_WORD.search(self._buffer[:dot_index])
        if match is None:
            return False
        word = match.group(1)
        # Reine Zahl vor dem Punkt: Ordnungszahl oder Datum, kein Satzende.
        return word.lower() in ABBREVIATIONS or word.isdigit()


async def chunk_stream(
    deltas: AsyncIterator[str],
    *,
    chunker: SentenceChunker | None = None,
) -> AsyncIterator[str]:
    """Textstrom in sprechbare Stücke wandeln."""
    chunker = chunker or SentenceChunker()
    async for delta in deltas:
        for chunk in chunker.feed(delta):
            yield chunk
    rest = chunker.flush()
    if rest:
        yield rest
