"""Fetcher und Textausbau.

Der Fetcher ist der zweite Weg, auf dem fremde Adressen ins System kommen —
nach dem Browser. Er muss dieselben Grenzen ziehen, und zwar auch **nach
einer Weiterleitung**: eine erlaubte URL, die auf das eigene Netz zeigt,
wäre sonst genau die Lücke, die der Browser nicht hat.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.research.fetcher import PageFetcher, extract_text
from jarvis.security.policy import Policies, PolicyEngine

HTML = """
<html><head><title>Beispielseite</title></head>
<body>
  <nav>Navigation, die nicht in den Text gehört</nav>
  <script>var tracker = 1;</script>
  <article><p>Der Beitrag liegt bei zwölf Prozent.</p></article>
</body></html>
"""


def engine(**raw: object) -> PolicyEngine:
    return PolicyEngine(Policies.model_validate(raw))


def transport(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        follow_redirects=True,
    )


# --- Textausbau ---------------------------------------------------------------


def test_skripte_fliegen_raus() -> None:
    text, title = extract_text(HTML)
    assert "zwölf Prozent" in text
    assert "tracker" not in text
    assert title == "Beispielseite"


def test_kaputtes_html_stuerzt_nicht_ab() -> None:
    text, _ = extract_text("<p>Angefangen und nicht zu Ende")
    assert "Angefangen" in text


# --- Grenzen ------------------------------------------------------------------


async def test_gesperrte_domain_wird_nicht_geholt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Es darf keine Anfrage rausgehen")

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["erlaubt.test"]}),
        respect_robots=False,
        client=transport(handler),
    )
    page = await fetcher.fetch("https://verboten.test/x")

    assert not page.ok
    assert "allowed_domains" in page.error


async def test_weiterleitung_ins_eigene_netz_wird_gestoppt() -> None:
    """Die Lücke, die entstünde, wenn nur die Start-URL geprüft würde."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "erlaubt.test":
            return httpx.Response(302, headers={"Location": "http://192.168.1.1/admin"})
        return httpx.Response(200, text="<p>Router-Oberfläche</p>")

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        respect_robots=False,
        client=transport(handler),
    )
    page = await fetcher.fetch("https://erlaubt.test/start")

    assert not page.ok
    assert "eigenen Netz" in page.error
    assert "Router" not in page.text


async def test_grosse_seite_wird_beim_lesen_abgeschnitten() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        riesig = "<p>" + ("x" * 50_000) + "</p>"
        return httpx.Response(200, text=riesig, headers={"content-type": "text/html"})

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        max_bytes=1024,
        respect_robots=False,
        client=transport(handler),
    )
    page = await fetcher.fetch("https://gross.test/x")

    assert page.truncated
    assert len(page.text) < 5_000


async def test_nicht_text_wird_abgelehnt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.7 binary", headers={"content-type": "application/pdf"}
        )

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        respect_robots=False,
        client=transport(handler),
    )
    page = await fetcher.fetch("https://datei.test/x.pdf")

    assert not page.ok
    assert "application/pdf" in page.error


async def test_fehlerstatus_wird_gemeldet_nicht_geworfen() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="weg")

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        respect_robots=False,
        client=transport(handler),
    )
    page = await fetcher.fetch("https://fehlt.test/x")

    assert not page.ok
    assert "404" in page.error


# --- robots.txt ---------------------------------------------------------------


async def test_robots_verbot_wird_beachtet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privat")
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        client=transport(handler),
    )
    gesperrt = await fetcher.fetch("https://hoeflich.test/privat/seite")
    erlaubt = await fetcher.fetch("https://hoeflich.test/offen")

    assert not gesperrt.ok
    assert "robots.txt" in gesperrt.error
    assert erlaubt.ok


async def test_ohne_robots_darf_geholt_werden() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    fetcher = PageFetcher(engine(browser={"allowed_domains": ["*"]}), client=transport(handler))
    assert (await fetcher.fetch("https://ohne.test/x")).ok


@pytest.mark.parametrize("schema", ["file:///etc/passwd", "javascript:alert(1)"])
async def test_gefaehrliche_schemata(schema: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Es darf keine Anfrage rausgehen")

    fetcher = PageFetcher(
        engine(browser={"allowed_domains": ["*"]}),
        respect_robots=False,
        client=transport(handler),
    )
    assert not (await fetcher.fetch(schema)).ok
