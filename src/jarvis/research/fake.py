"""Fake-Recherche: ein festes Quellennetz im Speicher.

Damit lässt sich der ganze Weg prüfen — Dedup, Zitatpflicht, und vor allem
der Fall, auf den es in dieser Phase ankommt: zwei Quellen, die sich
widersprechen, müssen als widersprüchlich durchkommen und nicht zu einer
glatten Antwort verrechnet werden.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from jarvis.research.base import (
    Finding,
    ResearchProvider,
    ResearchQuery,
    ResearchResponse,
    Source,
)


class FakeResearch(ResearchProvider):
    def __init__(
        self,
        findings: Sequence[Finding] = (),
        *,
        name: str = "fake",
        summary: str = "",
        error: str = "",
    ) -> None:
        self.name = name
        self._findings = tuple(findings)
        self._summary = summary
        self._error = error
        self.queries: list[ResearchQuery] = []

    async def search(self, query: ResearchQuery) -> ResearchResponse:
        self.queries.append(query)
        if self._error:
            return ResearchResponse(error=self._error)
        return ResearchResponse(
            findings=self._findings[: query.max_results],
            provider_summary=self._summary,
        )


def finding(
    url: str,
    text: str,
    *,
    title: str = "",
    via: str = "fake",
    published: date | None = None,
    relevance: float = 0.5,
) -> Finding:
    """Kurzschreibweise für Tests."""
    return Finding(
        source=Source(url=url, title=title, published=published, via=via),
        text=text,
        relevance=relevance,
    )
