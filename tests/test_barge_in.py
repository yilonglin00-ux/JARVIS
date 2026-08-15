"""Die Präfixbestimmung entscheidet, ob der Verlauf der Wirklichkeit entspricht."""

from __future__ import annotations

from jarvis.voice.audio_stream import pcm_duration_ms
from jarvis.voice.barge_in import SpeechLedger


def test_pcm_duration_matches_sample_math() -> None:
    # 16 kHz, 16 bit, mono: 320 Byte sind genau 10 ms.
    assert pcm_duration_ms(320, 16000) == 10.0
    assert pcm_duration_ms(0, 16000) == 0.0


def make_ledger(text: str, audio_ms: float) -> SpeechLedger:
    ledger = SpeechLedger()
    ledger.note_text(text)
    # 24 kHz, 16 bit: 48 Byte pro Millisekunde.
    ledger.note_audio(int(audio_ms * 48), 24000)
    return ledger


def test_full_playback_returns_complete_text() -> None:
    ledger = make_ledger("Der erste Satz ist fertig.", 1000)

    assert ledger.spoken_prefix(1000) == "Der erste Satz ist fertig."
    assert ledger.spoken_prefix(5000) == "Der erste Satz ist fertig."


def test_partial_playback_snaps_back_to_word_boundary() -> None:
    ledger = make_ledger("Der erste Satz ist fertig.", 1000)

    # Nach der Hälfte der Audiodauer ist etwa die Hälfte des Textes erklungen,
    # aber niemals ein angebrochenes Wort.
    prefix = ledger.spoken_prefix(500)

    assert prefix == "Der erste"
    assert "Der erste Satz ist fertig.".startswith(prefix)


def test_no_playback_reports_nothing_spoken() -> None:
    ledger = make_ledger("Egal was hier steht.", 1000)

    assert ledger.spoken_prefix(0) == ""


def test_missing_client_report_is_treated_as_nothing_spoken() -> None:
    # Konservativ: lieber ein Wort zu wenig im Verlauf als eines zu viel.
    ledger = make_ledger("Etwas Text.", 1000)

    assert ledger.spoken_prefix(None) == ""


def test_text_without_audio_counts_as_unspoken() -> None:
    ledger = SpeechLedger()
    ledger.note_text("Generiert, aber nie synthetisiert.")

    assert ledger.spoken_prefix(100) == ""


def test_note_text_joins_chunks_with_single_space() -> None:
    ledger = SpeechLedger()
    ledger.note_text("Erster Satz.")
    ledger.note_text("Zweiter Satz.")

    assert ledger.text == "Erster Satz. Zweiter Satz."


def test_reset_clears_everything() -> None:
    ledger = make_ledger("Alter Turn.", 500)
    ledger.reset()

    assert ledger.text == ""
    assert ledger.audio_ms == 0.0
    assert ledger.spoken_prefix(100) == ""
