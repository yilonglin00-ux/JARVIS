"""Recherche-Abstraktion.

Der Core kennt keinen Anbieternamen — nur `ResearchProvider`. Perplexity ist
eine von mehreren Implementierungen und per Konfiguration abschaltbar
(Architektur §8).

Der wichtigste Entwurfspunkt steckt in `Finding`: ein Befund gehört immer
**einer** Quelle. Nirgends in dieser Phase werden Quellen zu einem Text
verschmolzen. Genau das ist die Voraussetzung dafür, dass ein Widerspruch
später überhaupt sichtbar werden kann — aus einem eingeebneten Absatz
lässt er sich nicht zurückgewinnen.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class Source:
    """Eine Fundstelle. `url` ist der Schlüssel für Dedup und Zitat."""

    url: str
    title: str = ""
    published: date | None = None
    # Woher der Befund kam — "perplexity", "brave", … Für die Frage, ob
    # zwei Quellen wirklich unabhängig sind.
    via: str = ""

    @property
    def domain(self) -> str:
        host = (urlparse(self.url).hostname or "").lower()
        return host.removeprefix("www.")

    def cite(self) -> str:
        name = self.title.strip() or self.domain or self.url
        return f"{name} ({self.domain})"


@dataclass(frozen=True, slots=True)
class Finding:
    """Was **eine** Quelle sagt.

    Bewusst nicht „die Antwort“, sondern eine Aussage mit Absender. Der
    Unterschied entscheidet darüber, ob JARVIS später „A und B widersprechen
    sich“ sagen kann oder ob er sich für eine Seite entscheidet, ohne es zu
    merken.
    """

    source: Source
    text: str
    # Von 0 bis 1, wie gut der Befund zur Frage passt. Provider liefern das
    # selten mit; der Evaluator setzt es dann selbst.
    relevance: float = 0.0

    @property
    def numbers(self) -> tuple[str, ...]:
        """Zahlen im Text — die häufigste Stelle, an der Quellen sich uneins sind."""
        return tuple(_NUMBER.findall(self.text))


_NUMBER = re.compile(r"\d[\d.,]*\s?(?:%|Prozent|Euro|EUR|€|\$|Mio\.?|Mrd\.?)?")


@dataclass(frozen=True, slots=True)
class ResearchQuery:
    question: str
    # Mehrsprachig suchen: zu vielen Themen steht auf Englisch mehr und
    # Aktuelleres als auf Deutsch.
    languages: tuple[str, ...] = ("de", "en")
    max_results: int = 6
    recency_days: int | None = None


@dataclass(frozen=True, slots=True)
class ResearchResponse:
    """Was ein einzelner Provider zurückgibt."""

    findings: tuple[Finding, ...] = ()
    # Manche Anbieter (Perplexity) liefern zusätzlich eine eigene Synthese.
    # Sie ist ein Hinweis, nicht das Ergebnis — die Synthese macht JARVIS
    # selbst, sonst hinge die Antwort an einem Anbieter.
    provider_summary: str = ""
    error: str = ""
    cost_usd: float = 0.0
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error


class ResearchProvider(ABC):
    """Eine Quelle für Rechercheergebnisse."""

    name: str = "research"

    @abstractmethod
    async def search(self, query: ResearchQuery) -> ResearchResponse:
        """Suchen. Wirft nicht — Fehler kommen als `error` zurück.

        Begründung: Recherche läuft über mehrere Provider parallel. Fällt
        einer aus, soll die Recherche mit den übrigen weiterlaufen und das
        Fehlen benannt werden, statt den ganzen Turn zu beenden.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
