from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config.settings import Profile, Secrets, Settings
from jarvis.core.events import EventBus
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session
from jarvis.llm.fake import FakeLLM
from jarvis.security.confirm import ConfirmationBroker
from jarvis.security.policy import Policies, PolicyEngine
from jarvis.tools.base import ToolContext


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


# --- Phase 3: Werkzeuge und Sicherheit ---------------------------------------


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Arbeitsverzeichnis für die Dateiwerkzeuge, pro Test frisch."""
    root = tmp_path / "arbeit"
    root.mkdir()
    return root


@pytest.fixture
def policy(sandbox: Path) -> PolicyEngine:
    """Policy mit kurzem Timeout, damit Zeitablauf-Tests nicht bummeln."""
    return PolicyEngine(
        Policies.model_validate(
            {
                "defaults": {"confirm_timeout_s": 0.05},
                "files": {"root": str(sandbox)},
                "browser": {"allowed_domains": ["example.com"]},
            }
        )
    )


@pytest.fixture
def broker(bus: EventBus, policy: PolicyEngine) -> ConfirmationBroker:
    return ConfirmationBroker(bus, timeout_s=policy.confirm_timeout_s)


@pytest.fixture
def tool_ctx(bus: EventBus, policy: PolicyEngine, broker: ConfirmationBroker) -> ToolContext:
    return ToolContext(session_id="test", bus=bus, policy=policy, confirm=broker)
