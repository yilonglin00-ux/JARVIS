"""Das Drahtformat zwischen Host und iPad.

Ein einziger WebSocket trägt beides: JSON-Nachrichten für Ereignisse und
Binärframes für Audio. Ein Kanal hält Reihenfolge und Latenz kontrollier-
bar (Architektur §1).

Diese Datei ist die *Quelle der Wahrheit*. `ui/src/lib/protocol.ts` ist
ihr handgepflegtes Gegenstück; `tests/test_protocol.py` prüft, dass beide
Seiten dieselben Nachrichtentypen kennen.

Phase 2 hat die Sprachschleife abgedeckt, Phase 3 Werkzeuge und
Bestätigungen ergänzt — ohne dass sich das Rahmenformat ändern musste.
Agenten-, Plan- und Memory-Ereignisse kommen in den Phasen 5 und 6 auf
demselben Weg dazu.
"""

from __future__ import annotations

import struct
from typing import Annotated, Any, Literal

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


class ToolStartedMsg(BaseModel):
    type: Literal["tool.started"] = "tool.started"
    call_id: str
    tool: str
    risk: str
    summary: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolFinishedMsg(BaseModel):
    type: Literal["tool.finished"] = "tool.finished"
    call_id: str
    tool: str
    ok: bool = True
    display_text: str = ""
    duration_ms: float = 0.0


class ConfirmRequestMsg(BaseModel):
    """Rückfrage vor einer Aktion ab `SENSITIVE`.

    `requires_tap` heißt: eine gesprochene Zustimmung genügt hier nicht,
    es braucht den Fingertipp. Der Client muss das durchsetzen — der Host
    kann nicht sehen, wie geantwortet wurde.
    """

    type: Literal["confirm.request"] = "confirm.request"
    request_id: str
    tool: str
    risk: str
    summary: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requires_tap: bool = False
    timeout_s: float = 60.0


class ConfirmResolvedMsg(BaseModel):
    """Die Rückfrage ist erledigt — auch durch Zeitablauf oder Abbruch.

    Der Client braucht das, um den Dialog wieder zu schließen, wenn *er*
    nicht derjenige war, der geantwortet hat.
    """

    type: Literal["confirm.resolved"] = "confirm.resolved"
    request_id: str
    approved: bool
    decided_by: str = "user"


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
    | ToolStartedMsg
    | ToolFinishedMsg
    | ConfirmRequestMsg
    | ConfirmResolvedMsg
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


class ConfirmResponseMsg(BaseModel):
    """Antwort auf eine `confirm.request`.

    Ausbleiben ist kein gültiger Wert: nur `approved=true` lässt die
    Aktion zu, alles andere — auch gar keine Nachricht — verhindert sie.
    """

    type: Literal["confirm.response"] = "confirm.response"
    request_id: str
    approved: bool


class StopMsg(BaseModel):
    """Kill-Switch. Bricht den laufenden Turn ab und lehnt jede offene
    Rückfrage ab — im Zweifel lieber zu viel gestoppt als zu wenig."""

    type: Literal["user.stop"] = "user.stop"


class PingMsg(BaseModel):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    UserTextMsg | UserInterruptMsg | MicToggleMsg | ConfirmResponseMsg | StopMsg | PingMsg,
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    return _client_adapter.validate_json(raw)


def dump_server_message(message: ServerMessage) -> str:
    return _server_adapter.dump_json(message).decode()


CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "user.text",
        "user.interrupt",
        "user.stop",
        "mic.toggle",
        "confirm.response",
        "ping",
    }
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
        "tool.started",
        "tool.finished",
        "confirm.request",
        "confirm.resolved",
        "error",
    }
)
