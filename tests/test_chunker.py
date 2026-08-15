"""Der Satz-Chunker entscheidet über die gefühlte Reaktionszeit.

Zwei Fehlerbilder sind hier teuer: zu spät trennen (JARVIS wirkt träge)
und falsch trennen (die Stimme stolpert mitten im Wort).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.voice.chunker import SentenceChunker, chunk_stream


def feed_all(chunker: SentenceChunker, text: str) -> list[str]:
    """Zeichenweise füttern — härter als der echte Fall, wo Token kommen."""
    chunks: list[str] = []
    for char in text:
        chunks.extend(chunker.feed(char))
    rest = chunker.flush()
    if rest:
        chunks.append(rest)
    return chunks


def test_splits_at_sentence_boundaries() -> None:
    chunker = SentenceChunker()
    chunks = feed_all(chunker, "Das ist der erste Satz. Und hier folgt der zweite Satz.")

    assert chunks == ["Das ist der erste Satz.", "Und hier folgt der zweite Satz."]


def test_first_chunk_may_be_short_for_fast_first_audio() -> None:
    # Der erste Chunk bestimmt die wahrgenommene Latenz und darf deshalb
    # deutlich kürzer sein als die folgenden.
    chunker = SentenceChunker(first_chunk_min_chars=5, min_chars=40)
    chunks = feed_all(chunker, "Ja, klar. Ich habe die Anfrage verstanden und melde mich gleich.")

    assert chunks[0] == "Ja, klar."


def test_decimal_numbers_are_not_sentence_ends() -> None:
    chunker = SentenceChunker(first_chunk_min_chars=1)
    chunks = feed_all(chunker, "Der Wert liegt bei 3.14 Grad und bleibt stabil.")

    assert chunks == ["Der Wert liegt bei 3.14 Grad und bleibt stabil."]


def test_german_abbreviations_do_not_split() -> None:
    chunker = SentenceChunker(first_chunk_min_chars=1)
    chunks = feed_all(chunker, "Das gilt z. B. für alle Geräte bzw. deren Zubehör. Alles klar.")

    assert chunks == ["Das gilt z. B. für alle Geräte bzw. deren Zubehör.", "Alles klar."]


def test_long_text_without_punctuation_is_still_split() -> None:
    # Sonst würde die Wiedergabe erst nach der kompletten Antwort beginnen.
    chunker = SentenceChunker(first_chunk_min_chars=5, min_chars=10, max_chars=40)
    text = "und dann noch " * 8
    chunks = feed_all(chunker, text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_flush_returns_trailing_text_without_terminator() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Kein Punkt am Ende") == []
    assert chunker.flush() == "Kein Punkt am Ende"
    assert chunker.flush() == ""


async def test_chunk_stream_over_async_deltas() -> None:
    async def deltas() -> AsyncIterator[str]:
        for token in ["Hallo", " zusammen.", " Zweiter", " Satz", " folgt", " hier."]:
            yield token

    chunks = [chunk async for chunk in chunk_stream(deltas())]

    assert chunks == ["Hallo zusammen.", "Zweiter Satz folgt hier."]
