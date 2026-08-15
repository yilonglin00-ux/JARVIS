"""Die Sprachschleife: Audio rein → STT → Core → TTS → Audio raus.

Alle vier Stufen laufen nebenläufig. Das ist keine Optimierung, sondern
Voraussetzung: Während JARVIS spricht, muss das Mikrofon weiterlaufen und
transkribiert werden, sonst kann man ihn nicht unterbrechen.

Der STT-Strom lebt über die gesamte Sitzung, nicht pro Turn. Ein Turn ist
eine eigene Task — deshalb blockiert eine laufende Antwort das Zuhören
nicht.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from time import monotonic

import structlog

from jarvis.config.settings import Settings
from jarvis.core.errors import ProviderError
from jarvis.core.events import (
    AudioChunkReady,
    ConversationState,
    ErrorOccurred,
    EventBus,
    LatencyMeasured,
    TranscriptReceived,
)
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session, Turn
from jarvis.voice.audio_stream import AudioInbox
from jarvis.voice.barge_in import SpeechLedger
from jarvis.voice.chunker import SentenceChunker
from jarvis.voice.stt.base import SpeechToText
from jarvis.voice.tts.base import TextToSpeech, VoiceSpec

log = structlog.get_logger(__name__)


class VoicePipeline:
    def __init__(
        self,
        *,
        core: JarvisCore,
        session: Session,
        stt: SpeechToText,
        tts: TextToSpeech,
        bus: EventBus,
        settings: Settings,
        voice: VoiceSpec | None = None,
    ) -> None:
        self._core = core
        self._session = session
        self._stt = stt
        self._tts = tts
        self._bus = bus
        self._settings = settings
        self._voice = voice or VoiceSpec(language=settings.language)

        self.inbox = AudioInbox(sample_rate=settings.voice.input_sample_rate)
        self._ledger = SpeechLedger()
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._speech_end_at: float | None = None
        self._mic_open = False

    # --- Mikrofon ------------------------------------------------------------

    @property
    def mic_open(self) -> bool:
        return self._mic_open

    def set_mic_open(self, open_: bool) -> None:
        """Der Client meldet, ob das Mikrofon offen ist ("Tap to Speak")."""
        self._mic_open = open_
        if self._session.state in (ConversationState.IDLE, ConversationState.LISTENING):
            self._session.set_state(
                ConversationState.LISTENING if open_ else ConversationState.IDLE
            )

    def push_audio(self, frame: bytes) -> None:
        self.inbox.push(frame)

    # --- Hauptschleife -------------------------------------------------------

    async def run(self) -> None:
        """Transkripte entgegennehmen, bis der Audiostrom endet."""
        try:
            async for transcript in self._stt.transcribe_stream(
                self.inbox.stream(), language=self._settings.language
            ):
                self._bus.publish(
                    TranscriptReceived(
                        text=transcript.text,
                        is_final=transcript.is_final,
                        confidence=transcript.confidence,
                    )
                )
                if transcript.is_final and transcript.text.strip():
                    self._speech_end_at = monotonic()
                    self._spawn_turn(transcript.text.strip())
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            log.error("stt.failed", error=str(exc))
            self._bus.publish(ErrorOccurred(message=str(exc), recoverable=exc.recoverable))
        finally:
            await self._drain_turns()

    async def aclose(self) -> None:
        self.inbox.close()
        await self._session.interrupt(reason="closing")
        await self._drain_turns()
        with contextlib.suppress(Exception):
            await self._tts.stop()

    # --- Turns ---------------------------------------------------------------

    def _spawn_turn(self, text: str) -> None:
        """Turn als eigene Task starten, damit die STT-Schleife weiterläuft."""
        task = asyncio.create_task(self._run_turn(text), name="voice-turn")
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def _run_turn(self, text: str) -> None:
        try:
            await self._session.run_turn(text, self._speak_reply)
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            log.error("turn.provider_failed", error=str(exc))
            self._bus.publish(ErrorOccurred(message=str(exc), recoverable=exc.recoverable))
        except Exception as exc:
            log.exception("turn.failed")
            self._bus.publish(ErrorOccurred(message=str(exc), recoverable=True))
        finally:
            if not self._session.is_busy:
                self._session.set_state(
                    ConversationState.LISTENING if self._mic_open else ConversationState.IDLE
                )

    async def _speak_reply(self, turn: Turn) -> None:
        """Antwort erzeugen und sprechen — der eigentliche Turn."""
        self._session.set_state(ConversationState.THINKING)
        self._ledger.reset()
        chunker = SentenceChunker()
        started = monotonic()
        first_audio = True
        seq = 0

        async def sentences() -> AsyncIterator[str]:
            """LLM-Deltas sammeln und an Satzgrenzen an das TTS weitergeben."""
            async for delta in self._core.stream_reply(self._session, turn.user_text):
                turn.generated += delta
                for chunk in chunker.feed(delta):
                    self._ledger.note_text(chunk)
                    yield chunk
            rest = chunker.flush()
            if rest:
                self._ledger.note_text(rest)
                yield rest

        async for audio in self._tts.synthesize_stream(sentences(), voice=self._voice):
            if first_audio:
                first_audio = False
                self._session.set_state(ConversationState.SPEAKING)
                self._report_first_audio(started)
            self._ledger.note_audio(len(audio.data), audio.sample_rate)
            self._bus.publish(
                AudioChunkReady(
                    data=audio.data,
                    seq=seq,
                    sample_rate=audio.sample_rate,
                )
            )
            seq += 1

        # Ungestört zu Ende gesprochen: alles Generierte gilt als gehört.
        turn.spoken = self._ledger.text

    def _report_first_audio(self, turn_started: float) -> None:
        now = monotonic()
        self._bus.publish(
            LatencyMeasured(name="turn_start_to_first_audio", ms=(now - turn_started) * 1000)
        )
        if self._speech_end_at is not None:
            ms = (now - self._speech_end_at) * 1000
            self._bus.publish(LatencyMeasured(name="speech_end_to_first_audio", ms=ms))
            log.info("latency.speech_end_to_first_audio", ms=round(ms, 1))

    # --- Unterbrechung -------------------------------------------------------

    async def interrupt(self, *, played_ms: float | None = None, reason: str = "barge_in") -> None:
        """Vom Client gemeldete Unterbrechung verarbeiten.

        Reihenfolge ist wichtig: erst das TTS stoppen (billigster Weg, die
        Erzeugung zu beenden), dann den tatsächlich gehörten Präfix
        festschreiben, dann den Turn abbrechen. Andersherum wäre der
        Präfix beim Schreiben des Verlaufs noch nicht bekannt.
        """
        turn = self._session.current_turn
        if turn is None:
            return
        with contextlib.suppress(Exception):
            await self._tts.stop()
        turn.spoken = self._ledger.spoken_prefix(played_ms)
        turn.interrupted = True
        log.info(
            "barge_in",
            reason=reason,
            played_ms=round(played_ms, 1) if played_ms is not None else None,
            generated_audio_ms=round(self._ledger.audio_ms, 1),
            spoken_chars=len(turn.spoken),
        )
        await self._session.interrupt(reason=reason)
        self._session.set_state(
            ConversationState.LISTENING if self._mic_open else ConversationState.IDLE
        )

    # --- Textmodus -----------------------------------------------------------

    async def handle_text(self, text: str) -> Turn:
        """Texteingabe ohne Sprachausgabe — CLI und Tippen im Client.

        Derselbe Core, derselbe Verlauf, dieselbe Session. Es gibt keinen
        zweiten JARVIS (Architektur §13).
        """

        async def runner(turn: Turn) -> None:
            self._session.set_state(ConversationState.THINKING)
            async for delta in self._core.stream_reply(self._session, turn.user_text):
                turn.generated += delta
            # Geschriebener Text gilt vollständig als zugestellt.
            turn.spoken = turn.generated

        try:
            return await self._session.run_turn(text, runner)
        finally:
            self._session.set_state(
                ConversationState.LISTENING if self._mic_open else ConversationState.IDLE
            )

    # --- intern --------------------------------------------------------------

    async def _drain_turns(self) -> None:
        for task in list(self._turn_tasks):
            task.cancel()
        for task in list(self._turn_tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
