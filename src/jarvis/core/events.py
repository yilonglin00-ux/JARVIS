"""Typisierter Event-Bus.

Der Bus ist die einzige Kopplung zwischen Core, Sprachschleife und
Interfaces. Alles, was der Nutzer später im HUD sieht, ist ein Event —
deshalb steht er schon in Phase 2, obwohl es noch kein HUD gibt.

Wichtig: Diese Events sind *interne Domänenereignisse*. Sie sind bewusst
nicht identisch mit den WebSocket-Nachrichten in `interfaces.protocol` —
die Übersetzung passiert an genau einer Stelle (im Server). So kann sich
das Drahtformat ändern, ohne den Core anzufassen.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from types import TracebackType
from typing import TypeVar

import structlog

log = structlog.get_logger(__name__)


class ConversationState(StrEnum):
    """Zustandsautomat aus der Architektur, §4. Quelle der Wahrheit ist der Host."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


# --- Events ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    """Basisklasse. `at` erlaubt Latenzmessungen ohne separates Tracing."""

    at: float = field(default_factory=monotonic, kw_only=True)


@dataclass(frozen=True, slots=True)
class StateChanged(Event):
    state: ConversationState = ConversationState.IDLE
    previous: ConversationState = ConversationState.IDLE


@dataclass(frozen=True, slots=True)
class TranscriptReceived(Event):
    """Ein Stück Transkript vom STT-Provider. Partials kommen laufend."""

    text: str = ""
    is_final: bool = False
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ReplyDelta(Event):
    """Ein Text-Token der Antwort. Füttert die Sprechblase im Client."""

    text: str = ""


@dataclass(frozen=True, slots=True)
class ReplyCompleted(Event):
    """Turn beendet. `spoken` ist der Teil, der tatsächlich erklang."""

    spoken: str = ""
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class AudioChunkReady(Event):
    """Ein synthetisierter Audioblock, bereit zum Versand an den Client."""

    data: bytes = b""
    seq: int = 0
    sample_rate: int = 24000


@dataclass(frozen=True, slots=True)
class Interrupted(Event):
    """Barge-in oder Kill-Switch. `spoken_prefix` ist das bereits Gehörte."""

    reason: str = "barge_in"
    spoken_prefix: str = ""


@dataclass(frozen=True, slots=True)
class ToolStarted(Event):
    """Ein Werkzeug läuft an. `arguments` ist bereits redigiert."""

    call_id: str = ""
    tool: str = ""
    risk: str = "read"
    summary: str = ""
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolFinished(Event):
    call_id: str = ""
    tool: str = ""
    ok: bool = True
    display_text: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ConfirmationRequested(Event):
    """Rückfrage vor einer Aktion ab `SENSITIVE`.

    Geht an alle Clients der Sitzung. Wer zuerst antwortet, entscheidet;
    keine Antwort innerhalb `timeout_s` gilt als Ablehnung.
    """

    request_id: str = ""
    tool: str = ""
    risk: str = "sensitive"
    summary: str = ""
    arguments: dict[str, object] = field(default_factory=dict)
    requires_tap: bool = False
    timeout_s: float = 60.0


@dataclass(frozen=True, slots=True)
class ConfirmationResolved(Event):
    request_id: str = ""
    approved: bool = False
    # "user", "timeout" oder "cancelled" — für das Log und die Anzeige.
    decided_by: str = "user"


@dataclass(frozen=True, slots=True)
class ErrorOccurred(Event):
    message: str = ""
    recoverable: bool = True


@dataclass(frozen=True, slots=True)
class LatencyMeasured(Event):
    """Ein Messpunkt der Sprachkette, z. B. `speech_end_to_first_audio`."""

    name: str = ""
    ms: float = 0.0


E = TypeVar("E", bound=Event)


# --- Bus ---------------------------------------------------------------------


class Subscription:
    """Ein Abonnement als async Iterator.

    Jeder Abonnent bekommt eine eigene Queue. Läuft sie über, werden die
    *ältesten* Events verworfen und das gezählt — ein langsamer Client
    (etwa ein iPad mit schlechtem Netz) darf niemals die Sprachschleife
    blockieren. Genau deshalb ist die Queue begrenzt.
    """

    def __init__(self, bus: EventBus, types: tuple[type[Event], ...], maxsize: int) -> None:
        self._bus = bus
        self._types = types
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    @property
    def pending(self) -> int:
        """Noch nicht abgeholte Events. Nützlich für Tests und Diagnose."""
        return self._queue.qsize()

    def drain_nowait(self) -> list[Event]:
        """Alles sofort Verfügbare abholen, ohne zu warten."""
        events: list[Event] = []
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return events
            if event is _CLOSED:
                return events
            events.append(event)

    def _accepts(self, event: Event) -> bool:
        return not self._types or isinstance(event, self._types)

    def _offer(self, event: Event) -> None:
        if not self._accepts(event):
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(event)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Event]:
        while True:
            event = await self._queue.get()
            if event is _CLOSED:
                return
            yield event

    def close(self) -> None:
        self._bus._unsubscribe(self)
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_CLOSED)


_CLOSED = Event()


class EventBus:
    """Synchroner Fan-out in Abonnenten-Queues.

    `publish` ist absichtlich nicht awaitbar-blockierend: es stellt nur zu
    und kehrt sofort zurück. Ein Publisher in der Audiopfad-Schleife darf
    nie auf einen Konsumenten warten.
    """

    def __init__(self, *, queue_size: int = 256) -> None:
        self._subs: list[Subscription] = []
        self._queue_size = queue_size

    def subscribe(self, *types: type[Event], queue_size: int | None = None) -> Subscription:
        """Ohne Typangabe werden alle Events zugestellt."""
        sub = Subscription(self, types, queue_size or self._queue_size)
        self._subs.append(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def publish(self, event: Event) -> None:
        for sub in list(self._subs):
            sub._offer(event)

    def publish_all(self, events: Iterable[Event]) -> None:
        for event in events:
            self.publish(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def close(self) -> None:
        for sub in list(self._subs):
            sub.close()
