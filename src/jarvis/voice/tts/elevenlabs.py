"""ElevenLabs-Adapter über die WebSocket-Streaming-API.

Diese API nimmt Text *während* er entsteht entgegen und liefert Audio
zurück, sobald genug Kontext für eine natürliche Prosodie da ist. Genau
deshalb wird sie hier der HTTP-Variante vorgezogen: sie passt zum
`AsyncIterator[str]`-Eingang des Interfaces.

Wie beim STT-Adapter bewusst ohne SDK — der Vertrag ist ein WebSocket mit
JSON-Nachrichten und base64-kodiertem Audio.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import structlog
import websockets

from jarvis.core.errors import ConfigurationError, ProviderError
from jarvis.voice.tts.base import AudioChunk, TextToSpeech, VoiceSpec

log = structlog.get_logger(__name__)

BASE_URL = "wss://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel", mehrsprachig — nur als Notnagel
SAMPLE_RATES = {
    "pcm_16000": 16000,
    "pcm_22050": 22050,
    "pcm_24000": 24000,
    "pcm_44100": 44100,
}


class ElevenLabsTTS(TextToSpeech):
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "",
        model: str = "eleven_flash_v2_5",
        output_format: str = "pcm_24000",
        speed: float = 1.0,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "ELEVENLABS_API_KEY fehlt. Key in .env eintragen oder "
                "providers.tts in config/jarvis.yaml auf 'fake' setzen."
            )
        if output_format not in SAMPLE_RATES:
            raise ConfigurationError(
                f"output_format '{output_format}' wird nicht unterstützt. "
                f"Erlaubt: {', '.join(sorted(SAMPLE_RATES))}. "
                "Andere Formate als rohes PCM kann der AudioWorklet-Player "
                "auf dem iPad nicht ohne Dekoder abspielen."
            )
        self._api_key = api_key
        self._voice_id = voice_id or DEFAULT_VOICE_ID
        self._model = model
        self._output_format = output_format
        self._speed = speed
        self._stopped = False

    @property
    def output_sample_rate(self) -> int:
        return SAMPLE_RATES[self._output_format]

    async def stop(self) -> None:
        self._stopped = True

    def _url(self, voice: VoiceSpec) -> str:
        voice_id = voice.voice_id or self._voice_id
        params = urlencode(
            {
                "model_id": self._model,
                "output_format": self._output_format,
                # Sorgt dafür, dass schon nach wenigen Zeichen Audio kommt,
                # statt auf einen vollständigen Satz zu warten.
                "auto_mode": "true",
            }
        )
        return f"{BASE_URL}/{voice_id}/stream-input?{params}"

    async def synthesize_stream(
        self,
        text: AsyncIterator[str] | str,
        *,
        voice: VoiceSpec,
    ) -> AsyncIterator[AudioChunk]:
        self._stopped = False
        try:
            connection = await websockets.connect(
                self._url(voice),
                additional_headers={"xi-api-key": self._api_key},
                open_timeout=10,
            )
        except Exception as exc:
            raise ProviderError("elevenlabs", f"Verbindung fehlgeschlagen: {exc}") from exc

        await connection.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.8,
                        "speed": voice.speed or self._speed,
                    },
                }
            )
        )

        async def pump_text() -> None:
            try:
                async for piece in _as_stream(text):
                    if self._stopped:
                        break
                    if piece:
                        await connection.send(json.dumps({"text": piece}))
                # Leerer Text = "fertig, synthetisiere den Rest".
                await connection.send(json.dumps({"text": ""}))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("elevenlabs.text_pump_failed", error=str(exc))

        pump = asyncio.create_task(pump_text(), name="elevenlabs-text-pump")
        seq = 0
        try:
            async for raw in connection:
                if self._stopped:
                    break
                if isinstance(raw, bytes):
                    continue
                payload = json.loads(raw)
                audio_b64 = payload.get("audio")
                if audio_b64:
                    yield AudioChunk(
                        data=base64.b64decode(audio_b64),
                        sample_rate=self.output_sample_rate,
                        seq=seq,
                    )
                    seq += 1
                if payload.get("isFinal"):
                    break
        except websockets.ConnectionClosedError as exc:
            raise ProviderError("elevenlabs", f"Verbindung abgebrochen: {exc}") from exc
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await connection.close()


async def _as_stream(text: AsyncIterator[str] | str) -> AsyncIterator[str]:
    if isinstance(text, str):
        yield text
        return
    async for piece in text:
        yield piece
