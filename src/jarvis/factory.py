"""Provider-Factory — die einzige Stelle, die konkrete Anbieter kennt.

Der Core importiert ausschließlich die ABCs. Welche Implementierung
dahintersteckt, entscheidet sich hier anhand von `config/jarvis.yaml`.
Deshalb kostet ein Providerwechsel eine Zeile Konfiguration und keinen
Codeumbau (Architektur §2).
"""

from __future__ import annotations

from typing import Any

import structlog

from jarvis.config.settings import Settings
from jarvis.core.errors import ConfigurationError
from jarvis.llm.base import LLMProvider
from jarvis.llm.fake import FakeLLM
from jarvis.llm.router import ModelRouter
from jarvis.voice.stt.base import SpeechToText
from jarvis.voice.stt.fake import FakeSTT
from jarvis.voice.tts.base import TextToSpeech, VoiceSpec
from jarvis.voice.tts.fake import FakeTTS

log = structlog.get_logger(__name__)


def _opt(options: dict[str, Any], key: str, default: Any) -> Any:
    value = options.get(key, default)
    return default if value is None else value


def build_llm(settings: Settings) -> LLMProvider:
    name = settings.profile.providers.llm
    options = settings.profile.provider_options("llm")
    if name == "fake":
        return FakeLLM()
    if name == "anthropic":
        from jarvis.llm.anthropic import AnthropicLLM

        router = ModelRouter.from_options(options)
        from jarvis.llm.base import TaskClass

        return AnthropicLLM(
            settings.secrets.anthropic_api_key.get_secret_value(),
            default_model=router.model_for(TaskClass.CONVERSATION),
        )
    raise ConfigurationError(f"Unbekannter LLM-Provider '{name}' in config/jarvis.yaml")


def build_stt(settings: Settings) -> SpeechToText:
    name = settings.profile.providers.stt
    options = settings.profile.provider_options("stt")
    if name == "fake":
        return FakeSTT()
    if name == "deepgram":
        from jarvis.voice.stt.deepgram import DeepgramSTT

        return DeepgramSTT(
            settings.secrets.deepgram_api_key.get_secret_value(),
            model=_opt(options, "model", "nova-3"),
            language=_opt(options, "language", settings.language),
            sample_rate=_opt(options, "sample_rate", settings.voice.input_sample_rate),
            encoding=_opt(options, "encoding", "linear16"),
            endpointing_ms=_opt(options, "endpointing_ms", 300),
            interim_results=_opt(options, "interim_results", True),
        )
    raise ConfigurationError(f"Unbekannter STT-Provider '{name}' in config/jarvis.yaml")


def build_tts(settings: Settings) -> TextToSpeech:
    name = settings.profile.providers.tts
    options = settings.profile.provider_options("tts")
    if name == "fake":
        return FakeTTS()
    if name == "elevenlabs":
        from jarvis.voice.tts.elevenlabs import ElevenLabsTTS

        return ElevenLabsTTS(
            settings.secrets.elevenlabs_api_key.get_secret_value(),
            voice_id=_opt(options, "voice_id", ""),
            model=_opt(options, "model", "eleven_flash_v2_5"),
            output_format=_opt(options, "output_format", "pcm_24000"),
            speed=_opt(options, "speed", 1.0),
        )
    raise ConfigurationError(f"Unbekannter TTS-Provider '{name}' in config/jarvis.yaml")


def build_router(settings: Settings) -> ModelRouter:
    return ModelRouter.from_options(settings.profile.provider_options("llm"))


def build_voice_spec(settings: Settings) -> VoiceSpec:
    options = settings.profile.provider_options("tts")
    return VoiceSpec(
        voice_id=_opt(options, "voice_id", ""),
        language=settings.language,
        speed=float(_opt(options, "speed", 1.0)),
    )
