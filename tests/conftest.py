from __future__ import annotations

import pytest

from jarvis.config.settings import Profile, Secrets, Settings
from jarvis.core.events import EventBus
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session
from jarvis.llm.fake import FakeLLM


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Profil mit Fake-Providern. Liest bewusst keine .env des Entwicklers."""
    for key in (
        "JARVIS_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "DEEPGRAM_API_KEY",
        "ELEVENLABS_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    profile = Profile.model_validate(
        {
            "profile": {"name": "JARVIS", "language": "de", "persona": "Du bist JARVIS."},
            "providers": {"stt": "fake", "tts": "fake", "llm": "fake"},
            "voice": {"history_turns": 5},
        }
    )
    return Settings(profile=profile, secrets=Secrets(_env_file=None))


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def session(bus: EventBus) -> Session:
    return Session(bus, history_turns=5)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(["Guten Tag. Wie kann ich helfen?"])


@pytest.fixture
def core(fake_llm: FakeLLM, settings: Settings, bus: EventBus) -> JarvisCore:
    return JarvisCore(llm=fake_llm, settings=settings, bus=bus)
