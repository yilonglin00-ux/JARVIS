"""Provider-Factory — die einzige Stelle, die konkrete Anbieter kennt.

Der Core importiert ausschließlich die ABCs. Welche Implementierung
dahintersteckt, entscheidet sich hier anhand von `config/jarvis.yaml`.
Deshalb kostet ein Providerwechsel eine Zeile Konfiguration und keinen
Codeumbau (Architektur §2).
"""

from __future__ import annotations

from typing import Any

import structlog

from jarvis.browser.base import BrowserBackend
from jarvis.browser.tools import browser_tools
from jarvis.config.settings import Settings
from jarvis.core.errors import ConfigurationError
from jarvis.llm.base import LLMProvider
from jarvis.llm.fake import FakeLLM
from jarvis.llm.router import ModelRouter
from jarvis.research.base import ResearchProvider
from jarvis.research.evaluator import Evaluator
from jarvis.research.fetcher import PageFetcher
from jarvis.research.pipeline import ResearchPipeline
from jarvis.research.tools import ResearchTool
from jarvis.security.policy import PolicyEngine
from jarvis.tools.base import Tool
from jarvis.tools.builtin import (
    AppendNoteTool,
    ClockTool,
    DeleteFileTool,
    ListFilesTool,
    ReadFileTool,
    ReadNotesTool,
    WriteFileTool,
)
from jarvis.tools.registry import ToolRegistry
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


def build_browser_backend(settings: Settings, policy: PolicyEngine) -> BrowserBackend | None:
    """Backend für die Browser-Werkzeuge, oder None wenn abgeschaltet.

    Ohne Einträge in `browser.allowed_domains` dürfte der Browser ohnehin
    keine einzige Seite öffnen. Dann die Werkzeuge gar nicht erst
    anzubieten, ist ehrlicher als ein Modell, das es versucht und jedes
    Mal eine Absage bekommt.
    """
    if not settings.profile.tools.browser:
        return None
    if not policy.allowed_domains:
        log.warning("browser.disabled", reason="browser.allowed_domains ist leer")
        return None

    from jarvis.browser.playwright_backend import PlaywrightBackend

    download_dir = policy.download_dir
    download_dir.mkdir(parents=True, exist_ok=True)
    return PlaywrightBackend(
        headless=settings.profile.tools.browser_headless,
        download_dir=str(download_dir),
    )


def build_research(settings: Settings, policy: PolicyEngine) -> ResearchPipeline | None:
    """Recherchestrecke, oder None wenn abgeschaltet oder ohne Schlüssel.

    Ein Anbieter ohne Schlüssel wird stillschweigend weggelassen, statt bei
    jeder Suche einen Fehler zu liefern. Bleibt keiner übrig, gibt es die
    Pipeline nicht — und damit das Werkzeug nicht, statt eines, das immer
    scheitert.
    """
    config = settings.profile.research
    if not settings.profile.tools.research:
        return None

    providers: list[ResearchProvider] = []
    for name in config.providers:
        if name == "perplexity":
            key = settings.secrets.perplexity_api_key.get_secret_value()
            if key:
                from jarvis.research.perplexity import PerplexityResearch

                providers.append(PerplexityResearch(key, model=config.perplexity_model))
            else:
                log.warning("research.provider_skipped", provider=name, reason="kein Schlüssel")
        elif name == "brave":
            key = settings.secrets.brave_api_key.get_secret_value()
            if key:
                from jarvis.research.websearch import BraveResearch

                providers.append(BraveResearch(key, country=settings.language))
            else:
                log.warning("research.provider_skipped", provider=name, reason="kein Schlüssel")
        elif name == "fake":
            from jarvis.research.fake import FakeResearch

            providers.append(FakeResearch())
        else:
            raise ConfigurationError(
                f"Unbekannter Research-Provider '{name}' in config/jarvis.yaml"
            )

    if not providers:
        log.warning("research.disabled", reason="kein Anbieter mit Schlüssel")
        return None
    if len(providers) == 1:
        # Kein Fehler, aber erwähnenswert: mit einer Quelle gibt es keinen
        # Abgleich, und Widersprüche können gar nicht auffallen.
        log.warning("research.single_provider", provider=providers[0].name)

    fetcher = (
        PageFetcher(policy, respect_robots=config.respect_robots) if config.fetch_pages else None
    )
    return ResearchPipeline(
        providers=providers,
        evaluator=Evaluator(max_findings=config.max_results + 2),
        fetcher=fetcher,
        fetch_limit=config.fetch_limit,
    )


def build_tools(
    settings: Settings,
    policy: PolicyEngine,
    *,
    browser: BrowserBackend | None = None,
    research: ResearchPipeline | None = None,
) -> ToolRegistry:
    """Alle aktiven Werkzeuge. Die Policy entscheidet, was mitkommt."""
    registry = ToolRegistry()
    if not settings.profile.tools.enabled:
        return registry

    root = policy.files_root
    candidates: list[Tool] = [
        ClockTool(default_timezone=settings.profile.tools.timezone),
        ListFilesTool(root, max_bytes=policy.max_file_bytes),
        ReadFileTool(root, max_bytes=policy.max_file_bytes),
        WriteFileTool(root, max_bytes=policy.max_file_bytes),
        DeleteFileTool(root, max_bytes=policy.max_file_bytes),
        AppendNoteTool(root),
        ReadNotesTool(root),
    ]
    if browser is not None:
        candidates += browser_tools(browser)
    if research is not None:
        candidates.append(ResearchTool(research, max_results=settings.profile.research.max_results))

    for tool in candidates:
        # Abgeschaltete Werkzeuge tauchen gar nicht erst im Schema auf. Ein
        # Modell, das ein Werkzeug sieht und dann eine Absage bekommt,
        # versucht es beim nächsten Turn wieder.
        if policy.is_enabled(tool.name):
            registry.register(tool)
    log.info("tools.ready", count=len(registry), names=registry.names)
    return registry


def build_router(settings: Settings) -> ModelRouter:
    return ModelRouter.from_options(settings.profile.provider_options("llm"))


def build_voice_spec(settings: Settings) -> VoiceSpec:
    options = settings.profile.provider_options("tts")
    return VoiceSpec(
        voice_id=_opt(options, "voice_id", ""),
        language=settings.language,
        speed=float(_opt(options, "speed", 1.0)),
    )
