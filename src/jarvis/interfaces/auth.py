"""Zugangskontrolle für den WebSocket.

Der Core kann in späteren Phasen Mails senden und einen Browser
fernsteuern. Er darf deshalb nie ohne Nachweis erreichbar sein — auch
nicht im "eigenen" WLAN (Architektur §12, Risiko 7).

Der vorgeteilte Token ist die Untergrenze, nicht die Absicherung. Die
eigentliche Netzgrenze ist Tailscale bzw. das Binden an 127.0.0.1.
"""

from __future__ import annotations

import secrets
import time
from collections import deque

import structlog

from jarvis.core.errors import AuthenticationError

log = structlog.get_logger(__name__)

MIN_TOKEN_LENGTH = 16


class TokenAuth:
    def __init__(self, token: str) -> None:
        if not token:
            raise AuthenticationError(
                "JARVIS_AUTH_TOKEN ist nicht gesetzt. Der Server startet nicht ohne "
                'Token — erzeugen mit: python -c "import secrets; '
                'print(secrets.token_urlsafe(32))"'
            )
        if len(token) < MIN_TOKEN_LENGTH:
            raise AuthenticationError(
                f"JARVIS_AUTH_TOKEN ist zu kurz (mindestens {MIN_TOKEN_LENGTH} Zeichen)."
            )
        self._token = token

    def verify(self, presented: str | None) -> None:
        """Konstantzeit-Vergleich, damit der Token nicht erratbar wird."""
        if not presented or not secrets.compare_digest(presented, self._token):
            raise AuthenticationError("Ungültiger oder fehlender Token")


class RateLimiter:
    """Einfaches Schiebefenster gegen Verbindungs-Hämmern."""

    def __init__(self, *, max_events: int = 10, window_s: float = 60.0) -> None:
        self._max = max_events
        self._window = window_s
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] > self._window:
            events.popleft()
        if len(events) >= self._max:
            log.warning("auth.rate_limited", client=key)
            return False
        events.append(now)
        return True
