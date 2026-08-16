"""Werkzeugverzeichnis und die Ausführungsstrecke.

Hier liegt die Sicherheitsschleuse, und zwar genau einmal für alle
Werkzeuge. Jeder Aufruf durchläuft dieselbe Reihenfolge:

    bekannt? → freigeschaltet? → Risiko nach Policy → Argumente gültig?
             → ggf. Rückfrage → ausführen → Ereignisse

Dass die Rückfrage *hier* sitzt und nicht im Werkzeug, ist der Kern: ein
Werkzeug kann seine Frage schöner formulieren, aber es kann sie nicht
weglassen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Iterator
from dataclasses import replace

import structlog

from jarvis.core.errors import PolicyViolation
from jarvis.core.events import ToolFinished, ToolStarted
from jarvis.llm.base import ToolSpec
from jarvis.security.confirm import ConfirmationRequest
from jarvis.security.redaction import redact_arguments
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import ToolResult

log = structlog.get_logger(__name__)


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    # --- Verzeichnis ---------------------------------------------------------

    def register(self, tool: Tool) -> Tool:
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} hat keinen Namen")
        if tool.name in self._tools:
            raise ValueError(f"Werkzeug '{tool.name}' ist bereits registriert")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self, *, allowed: Iterable[str] | None = None) -> list[ToolSpec]:
        """Schemas für das Modell — optional auf eine Auswahl beschränkt.

        Nicht jeder Agent sieht jedes Werkzeug (Architektur §9). Das ist
        Sicherheitsmaßnahme und Qualitätsmaßnahme zugleich: weniger
        Auswahl bedeutet messbar weniger Fehlgriffe.
        """
        whitelist = None if allowed is None else set(allowed)
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
            for tool in self._tools.values()
            if whitelist is None or tool.name in whitelist
        ]

    # --- Ausführung ----------------------------------------------------------

    async def invoke(
        self,
        name: str,
        args: dict[str, object],
        ctx: ToolContext,
        *,
        call_id: str = "",
    ) -> ToolResult:
        """Ein Werkzeug aufrufen. Wirft nie — außer bei Cancellation."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"Unbekanntes Werkzeug '{name}'.")
        if not ctx.policy.is_enabled(name):
            return ToolResult.failure(
                f"Das Werkzeug '{name}' ist in config/policies.yaml abgeschaltet."
            )

        if problem := tool.validate(dict(args)):
            return ToolResult.failure(problem)

        # Drei Quellen, immer die strengste: der Boden im Code, die
        # argumentabhängige Verschärfung des Werkzeugs und die Policy.
        declared = max(tool.risk, tool.risk_for(dict(args)))
        risk = ctx.policy.effective_risk(name, declared)

        if ctx.policy.needs_confirmation(risk):
            decision = await ctx.confirm.ask(
                ConfirmationRequest(
                    tool=name,
                    summary=tool.summarize(dict(args)),
                    risk=risk,
                    # Der Nutzer soll beurteilen, was gleich passiert —
                    # dafür braucht er die echten Werte, nicht Platzhalter.
                    arguments=redact_arguments(dict(args), mask_secrets=False),
                    requires_tap=ctx.policy.needs_tap(risk),
                ),
                timeout_s=ctx.policy.confirm_timeout_s,
            )
            if not decision.approved:
                return ToolResult.failure(decision.reason, decision=decision.value)

        ctx.bus.publish(
            ToolStarted(
                call_id=call_id,
                tool=name,
                risk=str(risk),
                summary=tool.summarize(dict(args)),
                arguments=redact_arguments(dict(args)),
            )
        )
        started = time.monotonic()
        result = await self._run(tool, dict(args), ctx)
        duration_ms = (time.monotonic() - started) * 1000

        log.info(
            "tool.finished",
            tool=name,
            ok=result.ok,
            risk=str(risk),
            duration_ms=round(duration_ms, 1),
        )
        ctx.bus.publish(
            ToolFinished(
                call_id=call_id,
                tool=name,
                ok=result.ok,
                display_text=result.display_text,
                duration_ms=duration_ms,
            )
        )
        # `replace` statt Feld für Feld: beim Umkopieren von Hand fehlt
        # sonst genau das Feld, das zuletzt dazugekommen ist.
        return replace(result, duration_ms=duration_ms)

    @staticmethod
    async def _run(tool: Tool, args: dict[str, object], ctx: ToolContext) -> ToolResult:
        """Ausführen und jeden Fehler in ein Ergebnis übersetzen.

        Ein abstürzendes Werkzeug darf den Turn nicht beenden — das Modell
        soll die Fehlermeldung sehen und dem Nutzer etwas Sinnvolles sagen
        können. Nur `CancelledError` läuft durch, sonst wäre Barge-in
        wirkungslos.
        """
        try:
            async with asyncio.timeout(tool.timeout_s):
                return await tool.execute(args, ctx)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ToolResult.failure(
                f"'{tool.name}' hat nach {tool.timeout_s:.0f} Sekunden nicht geantwortet."
            )
        except PolicyViolation as exc:
            # Kein Defekt, sondern das Sicherheitsmodell bei der Arbeit.
            log.info("tool.policy_violation", tool=tool.name, reason=str(exc))
            return ToolResult.failure(str(exc))
        except Exception as exc:
            log.exception("tool.crashed", tool=tool.name)
            return ToolResult.failure(f"'{tool.name}' ist gescheitert: {exc}")
