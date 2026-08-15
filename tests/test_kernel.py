from __future__ import annotations

import asyncio

from jarvis.config.settings import Settings
from jarvis.core.events import EventBus, ReplyDelta
from jarvis.core.kernel import VOICE_OUTPUT_RULES, JarvisCore
from jarvis.core.session import Session, Turn
from jarvis.llm.base import Role, TaskClass
from jarvis.llm.fake import FakeLLM
from jarvis.llm.router import ModelRouter


async def test_stream_reply_yields_tokens_and_publishes_deltas(
    core: JarvisCore, session: Session, bus: EventBus
) -> None:
    subscription = bus.subscribe(ReplyDelta)

    tokens = [token async for token in core.stream_reply(session, "Hallo")]

    assert "".join(tokens) == "Guten Tag. Wie kann ich helfen?"
    published = []
    async for event in subscription:
        published.append(event.text)
        if len(published) == len(tokens):
            break
    assert "".join(published) == "".join(tokens)
    subscription.close()


async def test_system_prompt_contains_persona_and_voice_rules(core: JarvisCore) -> None:
    # Die Ausgaberegeln dürfen nicht aus der Persona herausfallen: ohne sie
    # liest die Sprachsynthese Markdown-Sternchen vor.
    assert "Du bist JARVIS." in core.system_prompt
    assert VOICE_OUTPUT_RULES in core.system_prompt


async def test_request_carries_history_and_new_utterance(
    fake_llm: FakeLLM, settings: Settings, bus: EventBus
) -> None:
    core = JarvisCore(llm=fake_llm, settings=settings, bus=bus)
    session = Session(bus, history_turns=5)

    async def runner(turn: Turn) -> None:
        async for delta in core.stream_reply(session, turn.user_text):
            turn.generated += delta
        turn.spoken = turn.generated

    await session.run_turn("Erste Frage", runner)
    await session.run_turn("Zweite Frage", runner)

    last = fake_llm.requests[-1]
    assert [(m.role, m.content) for m in last.messages] == [
        (Role.USER, "Erste Frage"),
        (Role.ASSISTANT, "Guten Tag. Wie kann ich helfen?"),
        (Role.USER, "Zweite Frage"),
    ]


async def test_prompt_caching_is_requested_when_provider_supports_it(
    core: JarvisCore, session: Session, fake_llm: FakeLLM
) -> None:
    # Der größte Kostenhebel des Projekts — deshalb ein eigener Test.
    async for _ in core.stream_reply(session, "Hallo"):
        pass

    assert fake_llm.requests[-1].cache_system is True


async def test_router_selects_model_per_task_class(
    fake_llm: FakeLLM, settings: Settings, bus: EventBus, session: Session
) -> None:
    router = ModelRouter(
        models={
            TaskClass.CLASSIFICATION: "haiku",
            TaskClass.CONVERSATION: "sonnet",
            TaskClass.PLANNING: "opus",
        }
    )
    core = JarvisCore(llm=fake_llm, settings=settings, bus=bus, router=router)

    async for _ in core.stream_reply(session, "Hi"):
        pass
    async for _ in core.stream_reply(session, "Plane das", task=TaskClass.PLANNING):
        pass

    assert [r.model for r in fake_llm.requests] == ["sonnet", "opus"]


async def test_stream_reply_stops_promptly_when_cancelled(
    settings: Settings, bus: EventBus, session: Session
) -> None:
    slow_llm = FakeLLM(["Ein sehr langer Satz der niemals endet"], delay_s=0.05)
    core = JarvisCore(llm=slow_llm, settings=settings, bus=bus)
    seen: list[str] = []

    async def consume() -> None:
        async for delta in core.stream_reply(session, "Erzähl"):
            seen.append(delta)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.12)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert 0 < len(seen) < 8
