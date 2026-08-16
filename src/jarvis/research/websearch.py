"""Websuche über Brave.

Zweite, unabhängige Quelle neben Perplexity. Der Zweck ist nicht mehr
Treffer, sondern **Abgleich**: erst wenn zwei Wege unabhängig voneinander
suchen, lässt sich feststellen, ob sie dasselbe sagen. Ein einzelner
Anbieter, der sich irrt, sieht von innen genauso aus wie einer, der recht
hat.

Brave ist austauschbar — Tavily oder ein selbst betriebenes SearXNG hängen
hinter demselben Interface.
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

API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveResearch(ResearchProvider):
    name = "brave"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 15.0,
        country: str = "de",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._country = country
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None

    async def search(self, query: ResearchQuery) -> ResearchResponse:
        if not self._api_key:
            return ResearchResponse(error="BRAVE_API_KEY fehlt.")

        params: dict[str, Any] = {
            "q": query.question,
            "count": max(1, min(query.max_results, 20)),
            "country": self._country,
        }
        if query.recency_days is not None:
            params["freshness"] = _freshness(query.recency_days)

        try:
            response = await self._client.get(
                API_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._api_key,
                },
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            log.warning("brave.http_error", status=exc.response.status_code)
            return ResearchResponse(error=f"Brave antwortete mit {exc.response.status_code}.")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("brave.failed", error=type(exc).__name__)
            return ResearchResponse(error=f"Brave nicht erreichbar ({type(exc).__name__}).")

        findings = tuple(_to_findings(body, limit=query.max_results))
        if not findings:
            return ResearchResponse(error="Brave lieferte keine Treffer.")
        return ResearchResponse(findings=findings)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _to_findings(body: Any, *, limit: int) -> list[Finding]:
    if not isinstance(body, dict):
        return []
    results = body.get("web", {}).get("results") if isinstance(body.get("web"), dict) else None
    if not isinstance(results, list):
        return []

    findings: list[Finding] = []
    for entry in results[:limit]:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        findings.append(
            Finding(
                source=Source(
                    url=url,
                    title=str(entry.get("title") or ""),
                    published=_parse_age(entry.get("page_age") or entry.get("age")),
                    via="brave",
                ),
                text=_strip_marks(str(entry.get("description") or "")),
                relevance=0.5,
            )
        )
    return findings


def _strip_marks(text: str) -> str:
    """Brave hebt Suchbegriffe mit <strong> hervor. Für uns ist das Rauschen."""
    return text.replace("<strong>", "").replace("</strong>", "").strip()


def _parse_age(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _freshness(days: int) -> str:
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    return "py"
