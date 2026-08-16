"""Recherche als Werkzeug.

Hängt sich in dieselbe Registry wie alles aus Phase 3 und durchläuft
dieselbe Schleuse. `READ`, weil Suchen nichts verändert — die
Bestätigungspflicht bliebe aber wirksam, wenn jemand sie in
`policies.yaml` anhebt.

Ergebnisse tragen `untrusted=True`. Ein Suchtreffer ist fremder Text, und
zwar einer, den ein Dritter gezielt platzieren kann: wer weiß, wonach
JARVIS sucht, kann eine Seite bauen, die Anweisungen enthält. Das ist der
Angriff mit der niedrigsten Hürde im ganzen System.
"""

from __future__ import annotations

from typing import Any, ClassVar

from jarvis.research.base import ResearchQuery
from jarvis.research.evaluator import Evidence
from jarvis.research.pipeline import CITATION_RULES, ResearchPipeline, render_evidence
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import Citation, ToolResult

MAX_RESULTS = 10


class ResearchTool(Tool):
    name = "recherche"
    description = (
        "Sucht in mehreren Quellen im Netz und liefert Belege mit Herkunft. Benutzen "
        "bei Fragen zu aktuellen Ereignissen, Zahlen, Preisen oder allem, was sich seit "
        "deinem Trainingsstand geändert haben kann. Nicht benutzen für Wissen, das "
        "sich nicht ändert."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "frage": {
                "type": "string",
                "description": "Die Recherchefrage, ausformuliert und ohne Suchoperatoren.",
            },
            "aktualitaet_tage": {
                "type": "integer",
                "description": "Nur Quellen aus den letzten n Tagen. Weglassen, wenn egal.",
            },
        },
        "required": ["frage"],
    }
    risk = RiskLevel.READ
    timeout_s = 60.0

    def __init__(self, pipeline: ResearchPipeline, *, max_results: int = 6) -> None:
        self._pipeline = pipeline
        self._max_results = max_results

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        frage = str(args["frage"]).strip()
        if not frage:
            return ToolResult.failure("Die Recherchefrage ist leer.")

        tage = args.get("aktualitaet_tage")
        evidence = await self._pipeline.run(
            ResearchQuery(
                question=frage,
                max_results=min(self._max_results, MAX_RESULTS),
                recency_days=int(tage) if isinstance(tage, int) and tage > 0 else None,
            )
        )

        if not evidence.findings:
            grund = (
                f" ({'; '.join(evidence.failed_providers)})" if evidence.failed_providers else ""
            )
            return ToolResult.failure(f"Keine Belege zu dieser Frage gefunden{grund}.")

        return ToolResult(
            ok=True,
            data=render_evidence(evidence, question=frage),
            display_text=_summary_line(evidence),
            citations=tuple(
                Citation(title=f.source.title or f.source.domain, url=f.source.url)
                for f in evidence.findings
            ),
            untrusted=True,
            # Auflage des Werkzeugs, nicht Inhalt der Quellen — landet
            # außerhalb der Untrusted-Klammer.
            instructions=CITATION_RULES,
            meta={
                "domains": list(evidence.domains),
                "konflikte": [c.describe() for c in evidence.conflicts],
                "nicht_erreichbar": list(evidence.failed_providers),
            },
        )


def _summary_line(evidence: Evidence) -> str:
    """Eine Zeile für die Anzeige im Client — nicht für das Modell."""
    teile = [f"{len(evidence.findings)} Belege aus {len(evidence.domains)} Quellen"]
    if evidence.conflicts:
        teile.append(f"{len(evidence.conflicts)} widersprüchliche Angabe(n)")
    if evidence.failed_providers:
        teile.append(f"{len(evidence.failed_providers)} Anbieter ausgefallen")
    return ", ".join(teile) + "."
