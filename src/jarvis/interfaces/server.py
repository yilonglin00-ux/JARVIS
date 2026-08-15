"""FastAPI-Host mit genau einem WebSocket-Endpunkt.

Das ist der einzige Zugang zum Core. Es gibt keine REST-Endpunkte für
Aktionen — alles läuft über den einen bidirektionalen Kanal, weil die
Reihenfolge zwischen Audio und Ereignissen sonst nicht garantiert wäre.

Jede Verbindung bekommt eine eigene Session, einen eigenen Event-Bus und
eigene STT/TTS-Instanzen. Der LLM-Client wird geteilt — er ist zustandslos
und hält nur einen HTTP-Pool.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from jarvis import __version__, factory
from jarvis.config.settings import Settings, load_settings
from jarvis.core.errors import AuthenticationError
from jarvis.core.events import (
    AudioChunkReady,
    ConfirmationRequested,
    ConfirmationResolved,
    ErrorOccurred,
    Event,
    EventBus,
    LatencyMeasured,
    ReplyCompleted,
    ReplyDelta,
    StateChanged,
    ToolFinished,
    ToolStarted,
    TranscriptReceived,
)
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session
from jarvis.interfaces.auth import RateLimiter, TokenAuth
from jarvis.interfaces.protocol import (
    ConfirmRequestMsg,
    ConfirmResolvedMsg,
    ConfirmResponseMsg,
    ErrorMsg,
    LatencyMsg,
    MicToggleMsg,
    PingMsg,
    ReplyCompletedMsg,
    ServerMessage,
    SessionReady,
    StateChangedMsg,
    StopMsg,
    TextDeltaMsg,
    ToolFinishedMsg,
    ToolStartedMsg,
    TranscriptMsg,
    UserInterruptMsg,
    UserTextMsg,
    dump_server_message,
    encode_audio_frame,
    parse_client_message,
)
from jarvis.llm.base import LLMProvider
from jarvis.llm.router import ModelRouter
from jarvis.logging import configure_logging
from jarvis.security.confirm import ConfirmationBroker
from jarvis.security.policy import PolicyEngine, load_policies
from jarvis.tools.base import ToolContext
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.pipeline import VoicePipeline

log = structlog.get_logger(__name__)

CLOSE_POLICY_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013


def to_wire(event: Event) -> ServerMessage | None:
    """Internes Domänenereignis in eine Drahtnachricht übersetzen.

    Die einzige Stelle, an der beide Welten sich berühren. Ereignisse ohne
    Entsprechung (etwa `Interrupted`) bleiben absichtlich intern: der
    Client hat die Unterbrechung selbst ausgelöst und weiß bereits davon.
    """
    match event:
        case StateChanged():
            return StateChangedMsg(state=event.state.value, previous=event.previous.value)
        case TranscriptReceived():
            return TranscriptMsg(
                type="transcript.final" if event.is_final else "transcript.partial",
                text=event.text,
                confidence=event.confidence,
            )
        case ReplyDelta():
            return TextDeltaMsg(text=event.text)
        case ReplyCompleted():
            return ReplyCompletedMsg(spoken=event.spoken, interrupted=event.interrupted)
        case LatencyMeasured():
            return LatencyMsg(name=event.name, ms=event.ms)
        case ToolStarted():
            return ToolStartedMsg(
                call_id=event.call_id,
                tool=event.tool,
                risk=event.risk,
                summary=event.summary,
                arguments=dict(event.arguments),
            )
        case ToolFinished():
            return ToolFinishedMsg(
                call_id=event.call_id,
                tool=event.tool,
                ok=event.ok,
                display_text=event.display_text,
                duration_ms=event.duration_ms,
            )
        case ConfirmationRequested():
            return ConfirmRequestMsg(
                request_id=event.request_id,
                tool=event.tool,
                risk=event.risk,
                summary=event.summary,
                arguments=dict(event.arguments),
                requires_tap=event.requires_tap,
                timeout_s=event.timeout_s,
            )
        case ConfirmationResolved():
            return ConfirmResolvedMsg(
                request_id=event.request_id,
                approved=event.approved,
                decided_by=event.decided_by,
            )
        case ErrorOccurred():
            return ErrorMsg(message=event.message, recoverable=event.recoverable)
        case _:
            return None


class Connection:
    """Eine Client-Verbindung samt eigener Session und Sprachschleife."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        settings: Settings,
        llm: LLMProvider,
        router: ModelRouter,
        policy: PolicyEngine,
        tools: ToolRegistry,
    ) -> None:
        self._ws = websocket
        self._settings = settings
        self.bus = EventBus()
        self.session = Session(self.bus, history_turns=settings.voice.history_turns)
        # Broker und Kontext gehören zur Verbindung, nicht zum Prozess:
        # zwei Sitzungen dürfen einander keine Rückfragen beantworten.
        self.confirm = ConfirmationBroker(self.bus, timeout_s=policy.confirm_timeout_s)
        self._tool_ctx = ToolContext(
            session_id=self.session.id,
            bus=self.bus,
            policy=policy,
            confirm=self.confirm,
        )
        # Der Core hängt am Bus *dieser* Verbindung — sonst landeten die
        # Text-Deltas in einem Bus, den niemand liest.
        core = JarvisCore(
            llm=llm,
            settings=settings,
            bus=self.bus,
            router=router,
            tools=tools,
            tool_context=self._tool_ctx,
            max_tool_steps=policy.max_tool_steps,
        )
        self._stt = factory.build_stt(settings)
        self._tts = factory.build_tts(settings)
        self.pipeline = VoicePipeline(
            core=core,
            session=self.session,
            stt=self._stt,
            tts=self._tts,
            bus=self.bus,
            settings=settings,
            voice=factory.build_voice_spec(settings),
        )

    async def serve(self) -> None:
        await self._send(
            SessionReady(
                session_id=self.session.id,
                language=self._settings.language,
                input_sample_rate=self._settings.voice.input_sample_rate,
                input_frame_ms=self._settings.voice.input_frame_ms,
                barge_in_min_speech_ms=self._settings.voice.barge_in_min_speech_ms,
            )
        )
        async with asyncio.TaskGroup() as group:
            group.create_task(self._forward_events(), name="ws-events")
            group.create_task(self.pipeline.run(), name="voice-pipeline")
            group.create_task(self._read_client(), name="ws-reader")

    # --- Ausgehend -----------------------------------------------------------

    async def _send(self, message: ServerMessage) -> None:
        await self._ws.send_text(dump_server_message(message))

    async def _forward_events(self) -> None:
        subscription = self.bus.subscribe()
        try:
            async for event in subscription:
                if isinstance(event, AudioChunkReady):
                    await self._ws.send_bytes(
                        encode_audio_frame(event.data, seq=event.seq, sample_rate=event.sample_rate)
                    )
                    continue
                message = to_wire(event)
                if message is not None:
                    await self._send(message)
        finally:
            subscription.close()

    # --- Eingehend -----------------------------------------------------------

    async def _read_client(self) -> None:
        """Liest bis zum Verbindungsende.

        Diese Schleife darf nie blockieren: käme `user.interrupt` verzögert
        an, wäre Barge-in praktisch wirkungslos. Deshalb wird hier nur
        entgegengenommen und weitergereicht, nie gearbeitet.
        """
        while True:
            message = await self._ws.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if (payload := message.get("bytes")) is not None:
                self.pipeline.push_audio(payload)
            elif (text := message.get("text")) is not None:
                await self._handle_text_message(text)

    async def _handle_text_message(self, raw: str) -> None:
        try:
            parsed = parse_client_message(raw)
        except ValidationError:
            log.warning("ws.bad_message")
            await self._send(ErrorMsg(message="Unbekannte Nachricht", recoverable=True))
            return

        match parsed:
            case UserTextMsg():
                if parsed.text.strip():
                    asyncio.create_task(  # noqa: RUF006 - bewusst nebenläufig
                        self._run_text_turn(parsed.text.strip()),
                        name="text-turn",
                    )
            case UserInterruptMsg():
                # Beim Barge-in auch offene Rückfragen abräumen: wer
                # dazwischenredet, statt zu antworten, hat nicht zugestimmt.
                self.confirm.deny_all(reason="barge_in")
                await self.pipeline.interrupt(played_ms=parsed.played_ms, reason=parsed.reason)
            case ConfirmResponseMsg():
                if not self.confirm.resolve(parsed.request_id, approved=parsed.approved):
                    log.info("confirm.stale_response", request=parsed.request_id)
            case StopMsg():
                self.confirm.deny_all(reason="kill_switch")
                await self.pipeline.interrupt(reason="kill_switch")
            case MicToggleMsg():
                self.pipeline.set_mic_open(parsed.open)
            case PingMsg():
                pass

    async def _run_text_turn(self, text: str) -> None:
        try:
            await self.pipeline.handle_text(text)
        except Exception as exc:
            log.exception("text_turn.failed")
            self.bus.publish(ErrorOccurred(message=str(exc)))

    # --- Abbau ---------------------------------------------------------------

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self.pipeline.aclose()
        self.bus.close()
        for provider in (self._stt, self._tts):
            with contextlib.suppress(Exception):
                await provider.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(
        level=settings.secrets.jarvis_log_level,
        log_transcripts=settings.secrets.jarvis_log_transcripts,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Der LLM-Client ist zustandslos und wird geteilt; STT und TTS sind
        # es nicht und entstehen pro Verbindung. Werkzeuge sind ebenfalls
        # zustandslos genug, um geteilt zu werden — der veränderliche Teil
        # steckt im `ToolContext`, und der gehört zur Verbindung.
        policy = PolicyEngine(load_policies())
        llm = factory.build_llm(settings)
        browser = factory.build_browser_backend(settings, policy)
        app.state.llm = llm
        app.state.policy = policy
        app.state.tools = factory.build_tools(settings, policy, browser=browser)
        app.state.router = factory.build_router(settings)
        log.info(
            "jarvis.started",
            version=__version__,
            stt=settings.profile.providers.stt,
            tts=settings.profile.providers.tts,
            llm=settings.profile.providers.llm,
            tools=len(app.state.tools),
        )
        try:
            yield
        finally:
            await llm.aclose()
            if browser is not None:
                await browser.aclose()

    app = FastAPI(title="JARVIS Host", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = TokenAuth(settings.secrets.jarvis_auth_token.get_secret_value())
    app.state.limiter = RateLimiter()

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Bewusst ohne Auth und ohne Details: nur "der Prozess lebt".
        return {"status": "ok", "version": __version__}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = Query(default="")) -> None:
        client = websocket.client.host if websocket.client else "unknown"
        if not app.state.limiter.allow(client):
            await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
            return
        try:
            app.state.auth.verify(token)
        except AuthenticationError:
            log.warning("ws.auth_failed", client=client)
            await websocket.close(code=CLOSE_POLICY_VIOLATION)
            return

        await websocket.accept()
        connection = Connection(
            websocket,
            settings=settings,
            llm=app.state.llm,
            router=app.state.router,
            policy=app.state.policy,
            tools=app.state.tools,
        )
        log.info("ws.connected", client=client, session=connection.session.id)
        try:
            await connection.serve()
        except* WebSocketDisconnect:
            pass
        except* Exception as group:  # noqa: BLE001 - eine Verbindung darf den Host nie killen
            for exc in group.exceptions:
                log.error("ws.failed", error=str(exc))
        finally:
            await connection.aclose()
            log.info("ws.disconnected", session=connection.session.id)

    return app
