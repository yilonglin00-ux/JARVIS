"""Protokolltests.

Das Drahtformat ist der Vertrag zwischen zwei Sprachen. Bricht er
unbemerkt, äußert sich das im Betrieb als stumme Verbindung — deshalb
prüft der letzte Test hier gegen die TypeScript-Datei.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.interfaces.protocol import (
    AUDIO_HEADER_SIZE,
    CLIENT_MESSAGE_TYPES,
    PROTOCOL_VERSION,
    SERVER_MESSAGE_TYPES,
    MicToggleMsg,
    StateChangedMsg,
    TranscriptMsg,
    UserInterruptMsg,
    decode_audio_frame,
    dump_server_message,
    encode_audio_frame,
    parse_client_message,
)

PROTOCOL_TS = Path(__file__).resolve().parents[1] / "ui" / "src" / "lib" / "protocol.ts"


def test_audio_frame_roundtrip() -> None:
    pcm = b"\x01\x02" * 480
    frame = encode_audio_frame(pcm, seq=7, sample_rate=24000)

    assert len(frame) == AUDIO_HEADER_SIZE + len(pcm)
    assert decode_audio_frame(frame) == (7, 24000, pcm)


def test_audio_frame_rejects_foreign_data() -> None:
    with pytest.raises(ValueError, match="Audiokennung"):
        decode_audio_frame(b"XX" + b"\x00" * 20)
    with pytest.raises(ValueError, match="zu kurz"):
        decode_audio_frame(b"JA")


def test_client_messages_parse_from_json() -> None:
    assert parse_client_message('{"type":"mic.toggle","open":true}') == MicToggleMsg(open=True)
    interrupt = parse_client_message('{"type":"user.interrupt","played_ms":420.5}')
    assert isinstance(interrupt, UserInterruptMsg)
    assert interrupt.played_ms == 420.5
    assert interrupt.reason == "barge_in"


def test_interrupt_without_played_ms_is_valid() -> None:
    # Der Client darf die abgespielte Dauer nicht kennen; der Host geht dann
    # konservativ davon aus, dass nichts gehört wurde.
    message = parse_client_message('{"type":"user.interrupt"}')
    assert isinstance(message, UserInterruptMsg)
    assert message.played_ms is None


def test_unknown_message_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"tool.execute","name":"rm"}')


def test_server_message_serialisation_keeps_type_tag() -> None:
    payload = dump_server_message(StateChangedMsg(state="speaking", previous="thinking"))

    assert '"type":"state.changed"' in payload
    assert '"state":"speaking"' in payload


def test_transcript_message_distinguishes_partial_and_final() -> None:
    partial = TranscriptMsg(type="transcript.partial", text="Wie spät")
    final = TranscriptMsg(type="transcript.final", text="Wie spät ist es?", confidence=0.9)

    assert '"type":"transcript.partial"' in dump_server_message(partial)
    assert '"confidence":0.9' in dump_server_message(final)


def test_typescript_client_knows_every_message_type() -> None:
    """Beide Seiten müssen dieselben Nachrichten kennen.

    Das ersetzt keine Codegenerierung, fängt aber genau den Fehler ab, der
    sonst erst auf dem iPad auffällt: ein neuer Ereignistyp im Host, den
    der Client stillschweigend verwirft.
    """
    source = PROTOCOL_TS.read_text(encoding="utf-8")
    declared = set(re.findall(r"'([a-z]+\.[a-z]+|error|ping)'", source))

    missing = (SERVER_MESSAGE_TYPES | CLIENT_MESSAGE_TYPES) - declared
    assert not missing, f"In protocol.ts fehlen: {sorted(missing)}"
    assert f"PROTOCOL_VERSION = {PROTOCOL_VERSION}" in source
