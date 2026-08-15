"""Session- und Turn-Lebenszyklus.

Eine Session ist ein Gespräch: Verlauf, aktueller Zustand, höchstens ein
laufender Turn. Ein Turn ist genau eine Nutzeräußerung samt Antwort und
ist als Ganzes abbrechbar — das ist die Voraussetzung für Barge-in.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from time import monotonic

import structlog

from jarvis.core.events import (
    ConversationState,
    EventBus,
    Interrupted,
    ReplyCompleted,
    StateChanged,
)
from jarvis.llm.base import Message, Role

log = structlog.get_logger(__name__)


TurnRunner = Callable[["Turn"], Coroutine[object, object, None]]


def _self_cancelled() -> bool:
    """True, wenn für die *aufrufende* Task selbst ein Abbruch angefordert wurde."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


@dataclass
class Turn:
    """Eine Nutzeräußerung und die darauf folgende Antwort."""

    user_text: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: float = field(default_factory=monotonic)

    # Was das LLM erzeugt hat.
    generated: str = ""
    # Was tatsächlich als Audio erklang. Bei Barge-in ein Präfix von
    # `generated` — nur dieser Teil darf in den Verlauf, sonst glaubt
    # JARVIS, du hättest etwas gehört, das nie erklungen ist.
    spoken: str = ""
    interrupted: bool = False

    def elapsed_ms(self) -> float:
        return (monotonic() - self.started_at) * 1000

    @property
    def committed_text(self) -> str:
        """Der Text, der in den Verlauf geschrieben wird."""
        return (self.spoken if self.interrupted else self.generated).strip()


class Session:
    """Ein Gespräch mit genau einem aktiven Turn.

    Der Zustand hier ist die Quelle der Wahrheit; Clients spiegeln ihn nur.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        history_turns: int = 20,
        session_id: str | None = None,
    ) -> None:
        self.id = session_id or uuid.uuid4().hex[:12]
        self._bus = bus
        self._history_turns = history_turns
        self._history: list[Message] = []
        self._state = ConversationState.IDLE
        self._turn: Turn | None = None
        self._task: asyncio.Task[None] | None = None

    # --- Zustand -------------------------------------------------------------

    @property
    def state(self) -> ConversationState:
        return self._state

    def set_state(self, state: ConversationState) -> None:
        if state is self._state:
            return
        previous, self._state = self._state, state
        self._bus.publish(StateChanged(state=state, previous=previous))

    @property
    def current_turn(self) -> Turn | None:
        return self._turn

    @property
    def is_busy(self) -> bool:
        return self._task is not None and not self._task.done()

    # --- Verlauf -------------------------------------------------------------

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def messages_for(self, user_text: str) -> list[Message]:
        """Verlauf plus die neue Äußerung — was das LLM zu sehen bekommt."""
        return [*self._history, Message(role=Role.USER, content=user_text)]

    def _append_turn_to_history(self, turn: Turn) -> None:
        reply = turn.committed_text
        self._history.append(Message(role=Role.USER, content=turn.user_text))
        if reply:
            self._history.append(Message(role=Role.ASSISTANT, content=reply))
        # Ältestes zuerst kürzen; Paare bleiben paarweise erhalten.
        limit = self._history_turns * 2
        if len(self._history) > limit:
            self._history = self._history[-limit:]

    def clear_history(self) -> None:
        self._history.clear()

    # --- Turns ---------------------------------------------------------------

    async def run_turn(
        self,
        user_text: str,
        runner: TurnRunner,
    ) -> Turn:
        """Einen Turn ausführen. Ein bereits laufender wird vorher abgebrochen.

        `runner` bekommt den Turn und trägt `generated` und `spoken` ein.
        Wird der Turn abgebrochen, gilt trotzdem alles, was bis dahin in
        `spoken` steht — deshalb wird der Verlauf auch im Abbruchfall
        geschrieben.
        """
        if self.is_busy:
            await self.interrupt(reason="new_utterance")

        turn = Turn(user_text=user_text)
        self._turn = turn
        task = asyncio.create_task(runner(turn), name=f"turn-{turn.id}")
        self._task = task
        try:
            await task
        except asyncio.CancelledError:
            turn.interrupted = True
            # Zwei Fälle sehen hier gleich aus: der Turn wurde abgebrochen
            # (Barge-in) — dann ist die Session gesund und wir kehren normal
            # zurück — oder *wir selbst* wurden abgebrochen. Nur im zweiten
            # Fall darf die Cancellation weiterlaufen.
            if _self_cancelled():
                raise
        finally:
            self._finish(turn)
        return turn

    def _finish(self, turn: Turn) -> None:
        self._append_turn_to_history(turn)
        self._bus.publish(ReplyCompleted(spoken=turn.committed_text, interrupted=turn.interrupted))
        log.info(
            "turn.finished",
            turn=turn.id,
            interrupted=turn.interrupted,
            duration_ms=round(turn.elapsed_ms(), 1),
            reply_chars=len(turn.committed_text),
        )
        self._turn = None
        self._task = None

    async def interrupt(self, *, reason: str = "barge_in") -> None:
        """Laufenden Turn abbrechen und darauf warten, dass er wirklich endet."""
        task, turn = self._task, self._turn
        if task is None or task.done():
            return
        if turn is not None:
            turn.interrupted = True
        self._bus.publish(Interrupted(reason=reason, spoken_prefix=turn.spoken if turn else ""))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def aclose(self) -> None:
        await self.interrupt(reason="session_closed")
        self.set_state(ConversationState.IDLE)
