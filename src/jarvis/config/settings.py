"""Konfiguration: YAML für das Profil, Umgebung für Secrets.

Bewusste Trennung — das Profil (`config/jarvis.yaml`) ist versioniert und
teilbar, die Secrets kommen ausschließlich aus der Umgebung bzw. dem
OS-Keychain und landen nie im Repo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = str

DEFAULT_CONFIG_PATH = Path("config/jarvis.yaml")


class ProfileConfig(BaseModel):
    name: str = "JARVIS"
    language: str = "de"
    persona: str = ""


class ProviderSelection(BaseModel):
    """Welche Implementierung hinter welchem Interface läuft."""

    stt: ProviderName = "fake"
    tts: ProviderName = "fake"
    llm: ProviderName = "fake"


class VoiceConfig(BaseModel):
    input_sample_rate: int = 16000
    input_frame_ms: int = 20
    barge_in_min_speech_ms: int = 200
    history_turns: int = 20
    auto_listen_after_reply: bool = False

    @property
    def input_frame_bytes(self) -> int:
        """Bytes eines Eingangsframes bei 16-bit mono."""
        return int(self.input_sample_rate * self.input_frame_ms / 1000) * 2


class ToolsConfig(BaseModel):
    """Was an Werkzeugen überhaupt angeboten wird.

    Der Feinschnitt — welches Werkzeug welche Risikostufe hat, welche
    Domains der Browser sehen darf — steht bewusst nicht hier, sondern in
    `config/policies.yaml`. Diese Datei sagt *ob*, jene sagt *unter
    welchen Bedingungen*.
    """

    enabled: bool = True
    browser: bool = False
    browser_headless: bool = True
    # Leer = Zeitzone des Hosts.
    timezone: str = ""


class LimitsConfig(BaseModel):
    max_turn_seconds: int = 120
    max_monthly_usd: float = 150.0


class Profile(BaseModel):
    """Der geparste Inhalt von `config/jarvis.yaml`."""

    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    providers: ProviderSelection = Field(default_factory=ProviderSelection)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # Anbieterspezifische Blöcke bleiben absichtlich untypisiert: der Core
    # liest sie nie, nur der jeweilige Adapter kennt seine eigenen Felder.
    stt: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    llm: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def provider_options(self, slot: Literal["stt", "tts", "llm"]) -> dict[str, Any]:
        """Optionen des für `slot` aktiven Providers, oder {} falls keine da sind."""
        selected: str = getattr(self.providers, slot)
        blocks: dict[str, dict[str, Any]] = getattr(self, slot)
        return blocks.get(selected, {})


class Secrets(BaseSettings):
    """Alles, was nicht ins Repo darf. Ausschließlich aus der Umgebung."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jarvis_auth_token: SecretStr = SecretStr("")
    jarvis_host: str = "127.0.0.1"
    jarvis_port: int = 8765
    jarvis_log_level: str = "info"
    jarvis_log_transcripts: bool = False

    anthropic_api_key: SecretStr = SecretStr("")
    deepgram_api_key: SecretStr = SecretStr("")
    elevenlabs_api_key: SecretStr = SecretStr("")
    perplexity_api_key: SecretStr = SecretStr("")
    brave_api_key: SecretStr = SecretStr("")


class Settings(BaseModel):
    """Profil + Secrets, wie der Rest des Systems sie sieht."""

    profile: Profile
    secrets: Secrets

    model_config = {"arbitrary_types_allowed": True}

    @property
    def voice(self) -> VoiceConfig:
        return self.profile.voice

    @property
    def persona(self) -> str:
        return self.profile.profile.persona.strip()

    @property
    def language(self) -> str:
        return self.profile.profile.language


def load_profile(path: Path | str | None = None) -> Profile:
    """Profil aus YAML laden. Fehlt die Datei, gelten die Defaults."""
    config_path = Path(path or os.getenv("JARVIS_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        return Profile()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: erwartet wird ein YAML-Mapping auf oberster Ebene")
    return Profile.model_validate(raw)


def load_settings(path: Path | str | None = None) -> Settings:
    return Settings(profile=load_profile(path), secrets=Secrets())
