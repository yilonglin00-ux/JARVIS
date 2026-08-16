"""Bestätigungsfluss.

Eine Rückfrage ist hier eine erstklassige Interaktion, kein Dialogfenster
am Rand: sie geht über den Event-Bus an alle Clients der Sitzung, blockiert
das Werkzeug so lange, und **jeder** Ausgang außer einer ausdrücklichen
Zustimmung bedeutet, dass die Aktion nicht stattfindet — Ablehnung,
Zeitablauf und Abbruch gleichermaßen (Architektur §12).

Die Formulierung ist Teil der Sicherheit. „Bist du sicher?“ trainiert
reflexhaftes Ja; deshalb verlangt `ConfirmationRequest` eine konkrete
Zusammenfassung im Klartext, und die Werkzeuge liefern sie.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from jarvis.core.events import ConfirmationRequested, ConfirmationResolved, EventBus
from jarvis.security.risk import RiskLevel

log = structlog.get_logger(__name__)


class Decision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def approved(self) -> bool:
        return self is Decision.APPROVED

    @property
    def reason(self) -> str:
        """Was JARVIS dem Nutzer sagen kann, wenn die Aktion ausbleibt."""
        return _REASONS[self]


_REASONS: dict[Decision, str] = {
    Decision.APPROVED: "Bestätigt.",
    Decision.DENIED: "Vom Nutzer abgelehnt — die Aktion wurde nicht ausgeführt.",
    Decision.TIMEOUT: (
        "Keine Antwort auf die Rückfrage — die Aktion wurde sicherheitshalber nicht ausgeführt."
    ),
    Decision.CANCELLED: "Abgebrochen, bevor die Rückfrage beantwortet wurde.",
}


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    tool: str
    summary: str
    risk: RiskLevel = RiskLevel.SENSITIVE
    arguments: dict[str, Any] = field(default_factory=dict)
    requires_tap: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class ConfirmationBroker:
    """Verwaltet offene Rückfragen einer Sitzung.

    Gehört zur Verbindung, nicht zum Prozess: zwei Sitzungen dürfen sich
    ihre Rückfragen nicht gegenseitig beantworten.
    """

    def __init__(self, bus: EventBus, *, timeout_s: float = 60.0) -> None:
        self._bus = bus
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[Decision]] = {}

    @property
    def pending_ids(self) -> list[str]:
        return list(self._pending)

    async def ask(
        self, request: ConfirmationRequest, *, timeout_s: float | None = None
    ) -> Decision:
        """Rückfrage stellen und auf die Antwort warten."""
        timeout = self._timeout_s if timeout_s is None else timeout_s
        future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future

        self._bus.publish(
            ConfirmationRequested(
                request_id=request.id,
                tool=request.tool,
                risk=str(request.risk),
                summary=request.summary,
                arguments=dict(request.arguments),
                requires_tap=request.requires_tap,
                timeout_s=timeout,
            )
        )
        log.info(
            "confirm.requested",
            request=request.id,
            tool=request.tool,
            risk=str(request.risk),
            timeout_s=timeout,
        )

        try:
            decision = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            decision = Decision.TIMEOUT
        except asyncio.CancelledError:
            # Barge-in oder Kill-Switch während der Rückfrage. Die Aktion
            # findet nicht statt; die Cancellation läuft weiter.
            self._settle(request.id, Decision.CANCELLED)
            raise
        return self._settle(request.id, decision)

    def _settle(self, request_id: str, decision: Decision) -> Decision:
        self._pending.pop(request_id, None)
        self._bus.publish(
            ConfirmationResolved(
                request_id=request_id,
                approved=decision.approved,
                decided_by=_decided_by(decision),
            )
        )
        log.info("confirm.resolved", request=request_id, decision=decision.value)
        return decision

    def resolve(self, request_id: str, *, approved: bool) -> bool:
        """Antwort eines Clients einspielen. False, wenn nichts offen war."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(Decision.APPROVED if approved else Decision.DENIED)
        return True

    def deny_all(self, *, reason: str = "kill_switch") -> int:
        """Kill-Switch: jede offene Rückfrage abschlägig beantworten."""
        count = 0
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(Decision.DENIED)
                count += 1
        if count:
            log.info("confirm.denied_all", count=count, reason=reason)
        return count


def _decided_by(decision: Decision) -> str:
    match decision:
        case Decision.TIMEOUT:
            return "timeout"
        case Decision.CANCELLED:
            return "cancelled"
        case _:
            return "user"
