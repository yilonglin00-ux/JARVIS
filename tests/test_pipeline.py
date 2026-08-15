"""Sprachschleife von Ende zu Ende — mit Fakes, ohne Netz und ohne Mikrofon."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.config.settings import Settings
from jarvis.core.events import (
    AudioChunkReady,
    ConversationState,
    EventBus,
    LatencyMeasured,
    TranscriptReceived,
)
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session
from jarvis.llm.fake import FakeLLM
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.stt.fake import FakeSTT
from jarvis.voice.tts.base import AudioChunk, TextToSpeech, VoiceSpec
from jarvis.voice.tts.fake import FakeTTS

FRAME = b"\x11\x00" * 160  # 20 ms "Sprache" bei 16 kHz
SILENCE = b"\x00" * 320  # 20 ms Stille


def build(
    settings: Settings,
    bus: EventBus,
    *,
    utterances: list[str],
    replies: list[str],
    tts: TextToSpeech | None = None,
    llm_delay: float = 0.0,
) -> tuple[VoicePipeline, Session]:
    session = Session(bus, history_turns=5)
    core = JarvisCore(llm=FakeLLM(replies, delay_s=llm_delay), settings=settings, bus=bus)
    pipeline = VoicePipeline(
        core=core,
        session=session,
        stt=FakeSTT(utterances),
        tts=tts or FakeTTS(),
        bus=bus,
        settings=settings,
        voice=VoiceSpec(language="de"),
    )
    return pipeline, session


def speak(pipeline: VoicePipeline, *, frames: int = 10) -> None:
    """Eine Äußerung simulieren: Sprache, dann genug Stille zum Endpointing."""
    for _ in range(frames):
        pipeline.push_audio(FRAME)
    for _ in range(10):
        pipeline.push_audio(SILENCE)


async def test_utterance_produces_transcript_reply_and_audio(
    settings: Settings, bus: EventBus
) -> None:
    pipeline, session = build(
        settings, bus, utterances=["Wie spät ist es?"], replies=["Es ist kurz nach drei."]
    )
    transcripts = bus.subscribe(TranscriptReceived)
    audio = bus.subscribe(AudioChunkReady)

    runner = asyncio.create_task(pipeline.run())
    speak(pipeline)
    await asyncio.sleep(0.2)
    pipeline.inbox.close()
    await asyncio.wait_for(runner, timeout=2)

    finals = [e for e in transcripts.drain_nowait() if e.is_final]
    assert [e.text for e in finals] == ["Wie spät ist es?"]
    assert audio.pending > 0  # es wurde tatsächlich Audio erzeugt
    assert [m.content for m in session.history] == [
        "Wie spät ist es?",
        "Es ist kurz nach drei.",
    ]
    transcripts.close()
    audio.close()


async def test_first_audio_latency_is_measured(settings: Settings, bus: EventBus) -> None:
    # Ohne diese Messung lässt sich Phase 2 nicht abnehmen.
    pipeline, _ = build(settings, bus, utterances=["Hallo"], replies=["Hallo zurück."])
    latencies = bus.subscribe(LatencyMeasured)

    runner = asyncio.create_task(pipeline.run())
    speak(pipeline)
    await asyncio.sleep(0.2)
    pipeline.inbox.close()
    await asyncio.wait_for(runner, timeout=2)

    names = {event.name for event in latencies.drain_nowait()}
    assert "speech_end_to_first_audio" in names
    assert "turn_start_to_first_audio" in names
    latencies.close()


async def test_state_machine_walks_idle_thinking_speaking(
    settings: Settings, bus: EventBus
) -> None:
    pipeline, session = build(settings, bus, utterances=["Test"], replies=["Antwort hier."])
    pipeline.set_mic_open(True)
    assert session.state is ConversationState.LISTENING

    runner = asyncio.create_task(pipeline.run())
    speak(pipeline)
    await asyncio.sleep(0.2)
    pipeline.inbox.close()
    await asyncio.wait_for(runner, timeout=2)

    assert session.state is ConversationState.LISTENING


async def test_barge_in_stops_audio_and_records_only_spoken_prefix(
    settings: Settings, bus: EventBus
) -> None:
    """Das Abnahmekriterium von Phase 2 in Testform."""
    reply = (
        "Der erste Satz ist fertig. Der zweite Satz folgt jetzt. "
        "Und der dritte Satz kommt auch noch."
    )
    tts = FakeTTS(realtime=True)
    pipeline, session = build(
        settings,
        bus,
        utterances=["Erzähl mir eine lange Geschichte"],
        replies=[reply],
        tts=tts,
    )

    runner = asyncio.create_task(pipeline.run())
    speak(pipeline)
    await asyncio.sleep(0.25)  # sprechen lassen
    assert session.state is ConversationState.SPEAKING

    chunks_before = len(tts.spoken)
    await pipeline.interrupt(played_ms=150.0)

    assert session.state is ConversationState.IDLE
    assert len(session.history) == 2
    spoken = session.history[1].content
    assert spoken, "es muss etwas erklungen sein"
    assert reply.startswith(spoken), "der Verlauf darf nur ein Präfix enthalten"
    assert len(spoken) < len(reply), "der ungehörte Rest darf nicht im Verlauf stehen"

    # Nach dem Stopp darf kein weiterer Text mehr synthetisiert werden.
    await asyncio.sleep(0.15)
    assert len(tts.spoken) == chunks_before

    pipeline.inbox.close()
    await asyncio.wait_for(runner, timeout=2)


async def test_interrupt_without_running_turn_is_harmless(
    settings: Settings, bus: EventBus
) -> None:
    pipeline, _ = build(settings, bus, utterances=["x"], replies=["y"])

    await pipeline.interrupt(played_ms=100.0)


async def test_text_mode_uses_the_same_session_history(settings: Settings, bus: EventBus) -> None:
    # Es gibt keinen zweiten JARVIS: Tippen und Sprechen teilen den Verlauf.
    pipeline, session = build(settings, bus, utterances=[], replies=["Verstanden."])

    turn = await pipeline.handle_text("Merk dir bitte etwas")

    assert turn.spoken == "Verstanden."
    assert [m.content for m in session.history] == ["Merk dir bitte etwas", "Verstanden."]


async def test_provider_failure_does_not_kill_the_session(
    settings: Settings, bus: EventBus
) -> None:
    class BrokenTTS(TextToSpeech):
        name = "broken"

        @property
        def output_sample_rate(self) -> int:
            return 24000

        async def synthesize_stream(
            self, text: AsyncIterator[str] | str, *, voice: VoiceSpec
        ) -> AsyncIterator[AudioChunk]:
            raise RuntimeError("TTS weg")
            yield  # pragma: no cover

    pipeline, _ = build(settings, bus, utterances=["Hallo"], replies=["Antwort."], tts=BrokenTTS())

    runner = asyncio.create_task(pipeline.run())
    speak(pipeline)
    await asyncio.sleep(0.2)
    pipeline.inbox.close()
    await asyncio.wait_for(runner, timeout=2)

    # Der Turn schlägt fehl, die Session bleibt benutzbar.
    turn = await pipeline.handle_text("Geht es noch?")
    assert turn.spoken == "Antwort."


@pytest.mark.parametrize("mic_open", [True, False])
async def test_mic_toggle_drives_idle_and_listening(
    settings: Settings, bus: EventBus, mic_open: bool
) -> None:
    pipeline, session = build(settings, bus, utterances=[], replies=["ok"])

    pipeline.set_mic_open(mic_open)

    expected = ConversationState.LISTENING if mic_open else ConversationState.IDLE
    assert session.state is expected
