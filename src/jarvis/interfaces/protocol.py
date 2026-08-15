"""Das Drahtformat zwischen Host und iPad.

Ein einziger WebSocket trägt beides: JSON-Nachrichten für Ereignisse und
Binärframes für Audio. Ein Kanal hält Reihenfolge und Latenz kontrollier-
bar (Architektur §1).

Diese Datei ist die *Quelle der Wahrheit*. `ui/src/lib/protocol.ts` ist
ihr handgepflegtes Gegenstück; `tests/test_protocol.py` prüft, dass beide
Seiten dieselben Nachrichtentypen kennen.

Phase 2 implementiert bewusst nur die Teilmenge, die für die Sprach-
schleife nötig ist. Tool-, Agenten-, Memory- und Bestätigungsereignisse
kommen in den Phasen 3 bis 6 dazu, ohne dass sich das Rahmenformat ändert.
"""

from __future__ import annotations

import struct
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1

# --- Binärrahmen für Audio ----------------------------------------------------
#
# Aufwärts (iPad -> Host) sind Binärframes rohes PCM ohne Kopf: 16 kHz,
# 16 bit, mono, Little Endian. Jedes zusätzliche Byte kostet hier
# Bandbreite bei 50 Frames pro Sekunde.
#
# Abwärts (Host -> iPad) steht ein 12-Byte-Kopf davor, weil der Client
# Sequenznummer und Abtastrate braucht, um nach einem Barge-in veraltete
# Chunks sicher zu verwerfen.

AUDIO_MAGIC = b"JA"
AUDIO_HEADER = struct.Struct("<2sBBII")  # magic, version, flags, seq, sample_rate
AUDIO_HEADER_SIZE = AUDIO_HEADER.size


def encode_audio_frame(data: bytes, *, seq: int, sample_rate: int) -> bytes:
    return AUDIO_HEADER.pack(AUDIO_MAGIC, PROTOCOL_VERSION, 0, seq, sample_rate) + data


def decode_audio_frame(frame: bytes) -> tuple[int, int, bytes]:
    """(seq, sample_rate, pcm) — spiegelt `decodeAudioFrame` in protocol.ts."""
    if len(frame) < AUDIO_HEADER_SIZE:
        raise ValueError("Audioframe zu kurz")
    magic, version, _flags, seq, sample_rate = AUDIO_HEADER.unpack_from(frame)
    if magic != AUDIO_MAGIC:
        raise ValueError("Unbekannte Audiokennung")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Protokollversion {version} wird nicht unterstützt")
    return seq, sample_rate, frame[AUDIO_HEADER_SIZE:]


# --- Host -> Client -----------------------------------------------------------


class SessionReady(BaseModel):
    """Erste Nachricht nach erfolgreicher Anmeldung. Konfiguriert den Client."""

    type: Literal["session.ready"] = "session.ready"
    session_id: str
    protocol_version: int = PROTOCOL_VERSION
    language: str = "de"
    input_sample_rate: int = 16000
    input_frame_ms: int = 20
    barge_in_min_speech_ms: int = 200


class StateChangedMsg(BaseModel):
    type: Literal["state.changed"] = "state.changed"
    state: str
    previous: str


class TranscriptMsg(BaseModel):
    type: Literal["transcript.partial", "transcript.final"]
    text: str
    confidence: float | None = None


class TextDeltaMsg(BaseModel):
    type: Literal["text.delta"] = "text.delta"
    text: str


class ReplyCompletedMsg(BaseModel):
    type: Literal["reply.completed"] = "reply.completed"
    spoken: str
    interrupted: bool = False


class LatencyMsg(BaseModel):
    type: Literal["metrics.latency"] = "metrics.latency"
    name: str
    ms: float


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True


ServerMessage = Annotated[
    SessionReady
    | StateChangedMsg
    | TranscriptMsg
    | TextDeltaMsg
    | ReplyCompletedMsg
    | LatencyMsg
    | ErrorMsg,
    Field(discriminator="type"),
]


# --- Client -> Host -----------------------------------------------------------


class UserTextMsg(BaseModel):
    type: Literal["user.text"] = "user.text"
    text: str


class UserInterruptMsg(BaseModel):
    """Barge-in. Der Client hat lokal bereits geflusht.

    `played_ms` ist die tatsächlich abgespielte Audiodauer dieses Turns.
    Nur der Client kennt sie — der Host weiß lediglich, was er *gesendet*
    hat. Ohne diesen Wert kann der gesprochene Präfix nicht bestimmt
    werden, und der Verlauf würde verfälscht.
    """

    type: Literal["user.interrupt"] = "user.interrupt"
    played_ms: float | None = None
    reason: str = "barge_in"


class MicToggleMsg(BaseModel):
    type: Literal["mic.toggle"] = "mic.toggle"
    open: bool


class PingMsg(BaseModel):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    UserTextMsg | UserInterruptMsg | MicToggleMsg | PingMsg,
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    return _client_adapter.validate_json(raw)


def dump_server_message(message: ServerMessage) -> str:
    return _server_adapter.dump_json(message).decode()


CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset(
    {"user.text", "user.interrupt", "mic.toggle", "ping"}
)
SERVER_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "session.ready",
        "state.changed",
        "transcript.partial",
        "transcript.final",
        "text.delta",
        "reply.completed",
        "metrics.latency",
        "error",
    }
)
