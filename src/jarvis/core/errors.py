"""Fehlertypen des Core.

Absichtlich flach: der Core unterscheidet nur, ob ein Fehler an der
Konfiguration, an einem Provider oder am Ablauf liegt. Anbieterspezifische
Ausnahmen werden im jeweiligen Adapter in `ProviderError` übersetzt, damit
kein Anbieter-Schema in den Core leckt.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Basisklasse aller JARVIS-Fehler."""


class ConfigurationError(JarvisError):
    """Profil oder Secrets sind unvollständig oder widersprüchlich."""


class ProviderError(JarvisError):
    """Ein externer Provider hat versagt.

    `recoverable` steuert, ob der Turn abgebrochen wird oder ob es sich
    lohnt, dem Nutzer eine Rückfrage zu stellen statt still zu scheitern.
    """

    def __init__(self, provider: str, message: str, *, recoverable: bool = True) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.recoverable = recoverable


class TurnCancelled(JarvisError):
    """Der laufende Turn wurde abgebrochen — durch Barge-in oder Kill-Switch."""


class AuthenticationError(JarvisError):
    """Ein Client hat sich nicht oder falsch ausgewiesen."""


class ProtocolError(JarvisError):
    """Eine Nachricht auf dem WebSocket war nicht interpretierbar."""
