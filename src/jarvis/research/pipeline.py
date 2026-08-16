"""Die Recherchestrecke.

    Frage → Provider (parallel) → Entdoppeln → Bewerten → Belege

Was hier **nicht** passiert: die Antwort formulieren. Die Pipeline liefert
geordnete Belege mit Absender; formuliert wird im Modell, mit der Auflage
aus `CITATION_RULES`. Diese Trennung ist der Grund, warum ein Widerspruch
bis zur Antwort durchkommen kann — wer hier zusammenfasst, hat ihn bereits
verloren.

Provider laufen parallel und dürfen einzeln ausfallen. Ein Anbieter, der
gerade nicht erreichbar ist, macht die Recherche schlechter, aber nicht
unmöglich — und das Fehlen wird benannt, statt still zu verschwinden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

from jarvis.research.base import Finding, ResearchProvider, ResearchQuery
from jarvis.research.evaluator import Evaluator, Evidence
from jarvis.research.fetcher import PageFetcher

log = structlog.get_logger(__name__)

# Steht im Werkzeugergebnis, nicht im System-Prompt: es gilt nur für diese
# eine Antwort und soll nicht in jedem Turn Kontext kosten.
#
# Wichtig: Diese Regeln gehen als `ToolResult.instructions` heraus, also
# **außerhalb** der Untrusted-Klammer um die Belege. Innerhalb stünden sie
# in einem Block, der dem Modell sagt, dort stehe nichts, was es befolgen
# soll — die Auflage hätte sich selbst aufgehoben.
CITATION_RULES = """
Regeln für diese Antwort:
Nenne zu jeder Tatsachenaussage die Quelle im Fließtext, so wie man es
spricht — „laut Tagesschau“ —, keine Fußnoten und keine URLs.
Widersprechen sich die Quellen, sage das ausdrücklich und nenne beide
Angaben. Löse den Widerspruch nicht auf und wähle nicht stillschweigend
eine Seite.
Was in den Belegen nicht steht, weißt du nicht. Sage das, statt zu ergänzen.
""".strip()


@dataclass(slots=True)
class ResearchPipeline:
    providers: list[ResearchProvider] = field(default_factory=list)
    evaluator: Evaluator = field(default_factory=Evaluator)
    # Optional: Volltext für Treffer nachladen, die nur eine URL brachten.
    fetcher: PageFetcher | None = None
    fetch_limit: int = 3
    max_snippet_chars: int = 1200

    async def run(self, query: ResearchQuery) -> Evidence:
        if not self.providers:
            return Evidence(failed_providers=("keine Provider konfiguriert",))

        responses = await asyncio.gather(
            *(provider.search(query) for provider in self.providers),
            return_exceptions=True,
        )

        findings: list[Finding] = []
        failed: list[str] = []
        for provider, response in zip(self.providers, responses, strict=True):
            if isinstance(response, BaseException):
                # Ein Provider, der wirft statt zu melden, hält sich nicht
                # an das Interface. Hier abfangen, nicht den Turn beenden.
                log.warning("research.provider_raised", provider=provider.name)
                failed.append(f"{provider.name} ({type(response).__name__})")
                continue
            if not response.ok:
                failed.append(f"{provider.name}: {response.error}")
                continue
            findings.extend(response.findings)

        evidence = self.evaluator.evaluate(findings, failed_providers=tuple(failed))
        if self.fetcher is not None:
            evidence = await self._fill_gaps(evidence)
        log.info(
            "research.done",
            findings=len(evidence.findings),
            domains=len(evidence.domains),
            conflicts=len(evidence.conflicts),
            failed=len(evidence.failed_providers),
        )
        return evidence

    async def _fill_gaps(self, evidence: Evidence) -> Evidence:
        """Volltext für Treffer nachladen, die ohne Auszug kamen.

        Perplexity liefert in einer seiner Antwortformen nur URLs. Ohne
        Text wäre so ein Treffer für die Synthese wertlos — und für die
        Widerspruchsprüfung unsichtbar, weil sie an Zahlen im Text hängt.
        """
        assert self.fetcher is not None
        leer = [f for f in evidence.findings if not f.text.strip()][: self.fetch_limit]
        if not leer:
            return evidence

        pages = await asyncio.gather(
            *(self.fetcher.fetch(f.source.url) for f in leer),
            return_exceptions=True,
        )
        nachgeladen: dict[str, str] = {}
        for finding, page in zip(leer, pages, strict=True):
            if isinstance(page, BaseException) or not page.ok:
                continue
            nachgeladen[finding.source.url] = page.text[: self.max_snippet_chars]

        if not nachgeladen:
            return evidence

        aktualisiert = tuple(
            Finding(
                source=f.source,
                text=nachgeladen.get(f.source.url, f.text),
                relevance=f.relevance,
            )
            for f in evidence.findings
        )
        # Nach dem Nachladen erneut auf Zahlenkonflikte sehen: die Belege
        # sind jetzt andere.
        from jarvis.research.evaluator import find_numeric_conflicts

        return Evidence(
            findings=aktualisiert,
            conflicts=tuple(find_numeric_conflicts(list(aktualisiert))),
            duplicates_removed=evidence.duplicates_removed,
            failed_providers=evidence.failed_providers,
        )

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()
        if self.fetcher is not None:
            await self.fetcher.aclose()


def render_evidence(evidence: Evidence, *, question: str) -> str:
    """Belege so aufschreiben, dass das Modell die Absender sieht.

    Nummerierte Blöcke je Quelle, Widersprüche vorangestellt. Kein
    Fließtext — sobald hier zusammengefasst würde, könnte das Modell
    „A und B widersprechen sich“ nicht mehr sagen.
    """
    zeilen: list[str] = [f"Frage: {question}", ""]

    if evidence.conflicts:
        zeilen.append("ACHTUNG — die Quellen sind sich nicht einig:")
        zeilen += [f"- {conflict.describe()}" for conflict in evidence.conflicts]
        zeilen.append("")

    if not evidence.findings:
        zeilen.append("Keine Belege gefunden.")
    else:
        zeilen.append("Belege:")
        for index, finding in enumerate(evidence.findings, start=1):
            datum = f", {finding.source.published.isoformat()}" if finding.source.published else ""
            zeilen.append(f"[{index}] {finding.source.cite()}{datum}")
            zeilen.append(f"    {finding.text.strip() or '(kein Auszug)'}")
            zeilen.append(f"    {finding.source.url}")
        zeilen.append("")

    if evidence.is_thin and evidence.findings:
        zeilen.append(
            "Hinweis: alle Belege stammen aus derselben Quelle. Nichts davon ist "
            "gegengeprüft — sage das in der Antwort."
        )
        zeilen.append("")

    if evidence.failed_providers:
        zeilen.append(f"Nicht erreichbar: {', '.join(evidence.failed_providers)}")

    # `CITATION_RULES` steht bewusst nicht hier — dieser Text wird gleich
    # als fremde Quelle eingeklammert. Die Auflage reist getrennt.
    return "\n".join(zeilen).rstrip()
