"""Deepgram-Streaming-Adapter über die rohe WebSocket-API.

Bewusst ohne SDK: der Vertrag ist ein WebSocket mit Query-Parametern und
JSON-Antworten. Das ist stabiler als eine SDK-Version, gibt volle
Kontrolle über Timeouts und macht sichtbar, was tatsächlich über die
Leitung geht — was für ein System, das Rohaudio versendet, wichtig ist.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import structlog
import websockets

from jarvis.core.errors import ConfigurationError, ProviderError
from jarvis.voice.stt.base import SpeechToText, Transcript

log = structlog.get_logger(__name__)

ENDPOINT = "wss://api.deepgram.com/v1/listen"
# Signalisiert dem Provider das Ende des Streams, damit er das letzte
# Segment finalisiert statt in einen Timeout zu laufen.
CLOSE_MESSAGE = json.dumps({"type": "CloseStream"})


class DeepgramSTT(SpeechToText):
    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "de",
        sample_rate: int = 16000,
        encoding: str = "linear16",
        endpointing_ms: int = 300,
        interim_results: bool = True,
        endpoint: str = ENDPOINT,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "DEEPGRAM_API_KEY fehlt. Key in .env eintragen oder "
                "providers.stt in config/jarvis.yaml auf 'fake' setzen."
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._params: dict[str, Any] = {
            "model": model,
            "language": language,
            "encoding": encoding,
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true" if interim_results else "false",
            "endpointing": endpointing_ms,
            "smart_format": "true",
        }

    @property
    def languages(self) -> set[str]:
        return {"de", "en"}

    def _url(self, language: str | None) -> str:
        params = dict(self._params)
        if language:
            params["language"] = language
        return f"{self._endpoint}?{urlencode(params)}"

    async def transcribe_stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str | None = None,
    ) -> AsyncIterator[Transcript]:
        url = self._url(language)
        try:
            connection = await websockets.connect(
                url,
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=10,
                ping_interval=5,
            )
        except Exception as exc:
            raise ProviderError("deepgram", f"Verbindung fehlgeschlagen: {exc}") from exc

        async def pump_audio() -> None:
            """Frames hochschieben, bis der Aufrufer den Strom schließt."""
            try:
                async for frame in audio:
                    await connection.send(frame)
                await connection.send(CLOSE_MESSAGE)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("deepgram.audio_pump_failed", error=str(exc))

        pump = asyncio.create_task(pump_audio(), name="deepgram-audio-pump")
        try:
            async for raw in connection:
                if isinstance(raw, bytes):
                    continue
                transcript = _parse(raw)
                if transcript is not None:
                    yield transcript
        except websockets.ConnectionClosedError as exc:
            raise ProviderError("deepgram", f"Verbindung abgebrochen: {exc}") from exc
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await connection.close()


def _parse(raw: str | bytes) -> Transcript | None:
    """Eine Deepgram-Ergebnisnachricht in ein `Transcript` übersetzen."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("deepgram.bad_json")
        return None
    if payload.get("type") != "Results":
        return None
    alternatives = payload.get("channel", {}).get("alternatives") or []
    if not alternatives:
        return None
    text = (alternatives[0].get("transcript") or "").strip()
    if not text:
        return None
    # `speech_final` heißt: der Sprecher hat aufgehört. `is_final` heißt
    # nur, dass dieses Segment nicht mehr revidiert wird. Für einen Turn
    # ist `speech_final` das richtige Signal.
    return Transcript(
        text=text,
        is_final=bool(payload.get("speech_final")),
        confidence=alternatives[0].get("confidence"),
    )
