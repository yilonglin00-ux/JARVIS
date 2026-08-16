"""Perplexity Sonar über die offizielle API.

Direktes HTTP statt SDK oder MCP-Server: weniger bewegliche Teile, volle
Kontrolle über Timeouts und Kosten, und man sieht, was über die Leitung
geht (Architektur §8). Browser-Automation gegen perplexity.ai kommt nicht
in Frage — es gibt eine API, und sie ist OpenAI-kompatibel.

Die Antwortform hat sich bei Perplexity über die Zeit geändert: mal
`citations` als reine URL-Liste, mal `search_results` mit Titel und Datum.
Dieser Adapter liest beides und kommt auch mit keinem von beiden zurecht —
ein Anbieter, der sein Schema erweitert, darf die Recherche nicht
abschalten.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import structlog

from jarvis.research.base import (
    Finding,
    ResearchProvider,
    ResearchQuery,
    ResearchResponse,
    Source,
)

log = structlog.get_logger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"

SYSTEM_PROMPT = (
    "Beantworte die Frage knapp und nenne für jede Aussage die Quelle. "
    "Wenn die Quellen sich widersprechen, benenne den Widerspruch, statt "
    "dich für eine Seite zu entscheiden."
)


class PerplexityResearch(ResearchProvider):
    name = "perplexity"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 25.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None

    async def search(self, query: ResearchQuery) -> ResearchResponse:
        if not self._api_key:
            return ResearchResponse(error="PERPLEXITY_API_KEY fehlt.")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query.question},
            ],
        }
        if query.recency_days is not None:
            payload["search_recency_filter"] = _recency_bucket(query.recency_days)

        try:
            response = await self._client.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            # Der Statuscode sagt mehr als der Text — und der Text kann den
            # Key enthalten, deshalb geht er nicht ins Ergebnis.
            log.warning("perplexity.http_error", status=exc.response.status_code)
            return ResearchResponse(error=f"Perplexity antwortete mit {exc.response.status_code}.")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("perplexity.failed", error=type(exc).__name__)
            return ResearchResponse(error=f"Perplexity nicht erreichbar ({type(exc).__name__}).")

        summary = _first_message(body)
        findings = tuple(_to_findings(body, limit=query.max_results))
        if not findings and not summary:
            return ResearchResponse(error="Perplexity lieferte keine verwertbare Antwort.")
        return ResearchResponse(
            findings=findings,
            provider_summary=summary,
            meta={"model": self._model},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _first_message(body: Any) -> str:
    try:
        return str(body["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _to_findings(body: Any, *, limit: int) -> list[Finding]:
    """Quellen aus beiden bekannten Antwortformen lesen."""
    if not isinstance(body, dict):
        return []

    findings: list[Finding] = []
    seen: set[str] = set()

    # Neuere Form: strukturierte Treffer mit Titel und Datum.
    for entry in _as_list(body.get("search_results")):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        findings.append(
            Finding(
                source=Source(
                    url=url,
                    title=str(entry.get("title") or ""),
                    published=_parse_date(entry.get("date") or entry.get("last_updated")),
                    via="perplexity",
                ),
                text=str(entry.get("snippet") or entry.get("title") or "").strip(),
                relevance=0.7,
            )
        )

    # Ältere Form: nur URLs. Der Text kommt dann aus dem Fetcher.
    for raw in _as_list(body.get("citations")):
        url = str(raw).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        findings.append(Finding(source=Source(url=url, via="perplexity"), text="", relevance=0.6))

    return findings[:limit]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _recency_bucket(days: int) -> str:
    """Perplexity nimmt Stufen, keine Tageszahl."""
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"
