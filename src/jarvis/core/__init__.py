"""Kern-Bausteine: Fehler, Ereignisse, Session.

`JarvisCore` steht bewusst **nicht** hier, obwohl er in `core/` liegt. Seit
Phase 3 hängt er am Werkzeugsystem, und das hängt über die Policy wieder an
`core.events`. Würde dieses Paket den Kernel importieren, entstünde daraus
ein Zyklus: wer `jarvis.core.events` importiert, zöge den Kernel mit und
damit `jarvis.tools`, das gerade auf `jarvis.security` wartet.

Der Kernel wird deshalb direkt importiert:

    from jarvis.core.kernel import JarvisCore

Das ist keine Unschönheit, sondern die Schichtung sichtbar gemacht — der
Kernel liegt *über* Werkzeugen und Sicherheit, nicht neben Ereignissen und
Session.
"""

from jarvis.core.errors import (
    ConfigurationError,
    ConfirmationDenied,
    JarvisError,
    PolicyViolation,
    ProviderError,
    TurnCancelled,
)
from jarvis.core.events import ConversationState, Event, EventBus
from jarvis.core.session import Session, Turn

__all__ = [
    "ConfigurationError",
    "ConfirmationDenied",
    "ConversationState",
    "Event",
    "EventBus",
    "JarvisError",
    "PolicyViolation",
    "ProviderError",
    "Session",
    "Turn",
    "TurnCancelled",
]
