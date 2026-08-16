"""Die Adapter für Perplexity und Brave.

Gegen nachgebaute Antworten, nicht gegen das Netz. Der Zweck ist nicht,
die Anbieter zu prüfen, sondern die Übersetzung in unsere Typen — und vor
allem, dass ein Anbieter, der sein Schema ändert oder ausfällt, die
Recherche nicht abschaltet.
"""

from __future__ import annotations

from datetime import date

import httpx

from jarvis.research.base import ResearchQuery
from jarvis.research.perplexity import PerplexityResearch
from jarvis.research.websearch import BraveResearch

QUERY = ResearchQuery(question="Wie hoch ist der Beitrag?")


def client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# --- Perplexity ---------------------------------------------------------------


async def test_perplexity_liest_strukturierte_treffer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Es sind zwölf Prozent."}}],
                "search_results": [
                    {
                        "url": "https://a.test/x",
                        "title": "Quelle A",
                        "date": "2026-08-01",
                        "snippet": "Zwölf Prozent laut A.",
                    }
                ],
            },
        )

    response = await PerplexityResearch("key", client=client(handler)).search(QUERY)

    assert response.ok
    assert response.provider_summary == "Es sind zwölf Prozent."
    [befund] = response.findings
    assert befund.source.title == "Quelle A"
    assert befund.source.published == date(2026, 8, 1)
    assert befund.source.via == "perplexity"


async def test_perplexity_liest_auch_die_alte_form() -> None:
    """Ältere Antworten liefern nur URLs — der Text kommt dann vom Fetcher."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Zusammenfassung."}}],
                "citations": ["https://a.test/x", "https://b.test/y"],
            },
        )

    response = await PerplexityResearch("key", client=client(handler)).search(QUERY)

    assert [f.source.url for f in response.findings] == ["https://a.test/x", "https://b.test/y"]
    assert all(f.text == "" for f in response.findings)


async def test_perplexity_doppelte_urls_nur_einmal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "x"}}],
                "search_results": [{"url": "https://a.test/x", "title": "A"}],
                "citations": ["https://a.test/x"],
            },
        )

    response = await PerplexityResearch("key", client=client(handler)).search(QUERY)
    assert len(response.findings) == 1


async def test_perplexity_unbekanntes_schema_ist_kein_absturz() -> None:
    """Ein erweitertes Antwortformat darf die Recherche nicht abschalten."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"etwas": "ganz", "anderes": True})

    response = await PerplexityResearch("key", client=client(handler)).search(QUERY)
    assert not response.ok
    assert "verwertbare" in response.error


async def test_perplexity_fehler_nennt_keinen_schluessel() -> None:
    """Der Antworttext eines 401 kann den Key enthalten — er darf nicht durch."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key sk-geheim-123")

    response = await PerplexityResearch("sk-geheim-123", client=client(handler)).search(QUERY)

    assert not response.ok
    assert "401" in response.error
    assert "geheim" not in response.error


async def test_perplexity_ohne_schluessel_fragt_nicht_an() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Es darf keine Anfrage rausgehen")

    response = await PerplexityResearch("", client=client(handler)).search(QUERY)
    assert not response.ok


async def test_perplexity_aktualitaet_wird_uebersetzt() -> None:
    gesehen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        gesehen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    await PerplexityResearch("key", client=client(handler)).search(
        ResearchQuery(question="?", recency_days=5)
    )
    assert gesehen["search_recency_filter"] == "week"


# --- Brave --------------------------------------------------------------------


async def test_brave_liest_treffer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Subscription-Token"] == "key"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://b.test/y",
                            "title": "Quelle B",
                            "description": "Es sind <strong>fünfzehn</strong> Prozent.",
                        }
                    ]
                }
            },
        )

    response = await BraveResearch("key", client=client(handler)).search(QUERY)

    [befund] = response.findings
    # Die Hervorhebung der Suchbegriffe ist für uns Rauschen.
    assert befund.text == "Es sind fünfzehn Prozent."
    assert befund.source.via == "brave"


async def test_brave_ohne_treffer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"web": {"results": []}})

    response = await BraveResearch("key", client=client(handler)).search(QUERY)
    assert not response.ok


async def test_brave_netzfehler_wirft_nicht() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Netz weg")

    response = await BraveResearch("key", client=client(handler)).search(QUERY)
    assert not response.ok
    assert "nicht erreichbar" in response.error
