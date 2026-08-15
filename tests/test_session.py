from __future__ import annotations

import asyncio

import pytest

from jarvis.core.events import (
    ConversationState,
    EventBus,
    Interrupted,
    ReplyCompleted,
    StateChanged,
)
from jarvis.core.session import Session, Turn
from jarvis.llm.base import Role


async def test_completed_turn_lands_in_history(session: Session) -> None:
    async def runner(turn: Turn) -> None:
        turn.generated = "Alles klar."
        turn.spoken = turn.generated

    await session.run_turn("Hallo", runner)

    assert [(m.role, m.content) for m in session.history] == [
        (Role.USER, "Hallo"),
        (Role.ASSISTANT, "Alles klar."),
    ]


async def test_interrupted_turn_records_only_what_was_spoken(session: Session) -> None:
    """Der Kern der Barge-in-Korrektheit.

    Was nie erklungen ist, darf nicht im Verlauf stehen — sonst bezieht
    sich JARVIS im nächsten Turn auf Sätze, die niemand gehört hat.
    """
    started = asyncio.Event()

    async def runner(turn: Turn) -> None:
        turn.generated = "Der erste Teil. Der zweite Teil, den niemand hört."
        turn.spoken = "Der erste Teil."
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(session.run_turn("Erzähl mir was", runner))
    await started.wait()
    await session.interrupt()
    await task

    assert [m.content for m in session.history] == ["Erzähl mir was", "Der erste Teil."]


async def test_interrupt_publishes_event_with_spoken_prefix(
    session: Session, bus: EventBus
) -> None:
    subscription = bus.subscribe(Interrupted)
    started = asyncio.Event()

    async def runner(turn: Turn) -> None:
        turn.spoken = "Bis hierher"
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(session.run_turn("Frage", runner))
    await started.wait()
    await session.interrupt(reason="barge_in")
    await task

    event = await asyncio.wait_for(anext(aiter(subscription)), timeout=1)
    assert event.reason == "barge_in"
    assert event.spoken_prefix == "Bis hierher"
    subscription.close()


async def test_new_utterance_cancels_the_running_turn(session: Session) -> None:
    first_started = asyncio.Event()

    async def slow(turn: Turn) -> None:
        turn.spoken = "Erste Antwort"
        first_started.set()
        await asyncio.sleep(10)

    async def quick(turn: Turn) -> None:
        turn.generated = turn.spoken = "Zweite Antwort"

    first = asyncio.create_task(session.run_turn("Erste Frage", slow))
    await first_started.wait()
    await session.run_turn("Zweite Frage", quick)
    await first

    assert [m.content for m in session.history] == [
        "Erste Frage",
        "Erste Antwort",
        "Zweite Frage",
        "Zweite Antwort",
    ]


async def test_reply_completed_is_published_once_per_turn(session: Session, bus: EventBus) -> None:
    subscription = bus.subscribe(ReplyCompleted)

    async def runner(turn: Turn) -> None:
        turn.generated = turn.spoken = "Fertig."

    await session.run_turn("Los", runner)

    event = await asyncio.wait_for(anext(aiter(subscription)), timeout=1)
    assert event.spoken == "Fertig."
    assert event.interrupted is False
    subscription.close()


async def test_history_is_trimmed_to_configured_turn_count(bus: EventBus) -> None:
    session = Session(bus, history_turns=2)

    async def runner(turn: Turn) -> None:
        turn.generated = turn.spoken = f"Antwort {turn.user_text}"

    for index in range(5):
        await session.run_turn(str(index), runner)

    assert len(session.history) == 4
    assert session.history[0].content == "3"


async def test_state_changes_are_published_once(session: Session, bus: EventBus) -> None:
    subscription = bus.subscribe(StateChanged)
    session.set_state(ConversationState.LISTENING)
    session.set_state(ConversationState.LISTENING)  # keine Wiederholung senden
    session.set_state(ConversationState.THINKING)

    events = []
    async for event in subscription:
        events.append(event)
        if len(events) == 2:
            break

    assert [e.state for e in events] == [
        ConversationState.LISTENING,
        ConversationState.THINKING,
    ]
    subscription.close()


async def test_empty_reply_adds_no_assistant_message(session: Session) -> None:
    async def runner(turn: Turn) -> None:
        return None

    await session.run_turn("Nur Rauschen", runner)

    assert [m.role for m in session.history] == [Role.USER]


async def test_runner_exception_propagates_but_keeps_session_usable(
    session: Session,
) -> None:
    async def failing(turn: Turn) -> None:
        raise RuntimeError("Provider weg")

    with pytest.raises(RuntimeError):
        await session.run_turn("Frage", failing)

    assert session.is_busy is False

    async def ok(turn: Turn) -> None:
        turn.generated = turn.spoken = "Geht wieder."

    await session.run_turn("Nochmal", ok)
    assert session.history[-1].content == "Geht wieder."
