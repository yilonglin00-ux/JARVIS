"""Seiten holen und lesbar machen.

Der Fetcher hat drei Aufgaben, und zwei davon sind Begrenzungen:

* **Größe.** Abgebrochen wird beim Streamen, nicht nach dem Download. Ein
  `Content-Length`-Feld ist eine Behauptung der Gegenseite; eine Datei, die
  erst im Arbeitsspeicher landet und dann verworfen wird, hat den Schaden
  schon angerichtet.
* **Ziel.** Dieselbe Prüfung wie beim Browser-Werkzeug — auch nach jeder
  Weiterleitung. Ohne das wäre eine harmlose URL, die auf `192.168.1.1`
  weiterleitet, ein Weg ins eigene Netz.
* **Inhalt.** Nur Text; alles andere wird gar nicht erst gelesen.

`robots.txt` wird respektiert. Nicht aus Rechtsgründen, sondern weil ein
Assistent, der im Namen seines Nutzers das Netz abgrast, sich an dessen
Regeln halten sollte.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from jarvis.core.errors import PolicyViolation
from jarvis.security.policy import PolicyEngine

log = structlog.get_logger(__name__)

USER_AGENT = "JARVIS/0.3 (persönlicher Assistent; +https://github.com/yilonglin00-ux/JARVIS)"
MAX_BYTES = 2 * 1024 * 1024
TEXTUAL = ("text/html", "text/plain", "application/xhtml+xml", "application/xml")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    text: str = ""
    title: str = ""
    error: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)


class PageFetcher:
    def __init__(
        self,
        policy: PolicyEngine,
        *,
        timeout_s: float = 10.0,
        max_bytes: int = MAX_BYTES,
        respect_robots: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._policy = policy
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._respect_robots = respect_robots
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._owns_client = client is None
        self._robots: dict[str, RobotFileParser | None] = {}

    async def fetch(self, url: str) -> FetchedPage:
        """Eine Seite holen. Wirft nicht — Fehler stehen im Ergebnis."""
        try:
            target = self._check(url)
        except PolicyViolation as exc:
            return FetchedPage(url=url, error=str(exc))

        if self._respect_robots and not await self._allowed_by_robots(target):
            return FetchedPage(url=target, error="Die Seite verbietet das Abrufen (robots.txt).")

        try:
            async with self._client.stream("GET", target, timeout=self._timeout_s) as response:
                response.raise_for_status()
                # Nach Weiterleitungen kann eine ganz andere Adresse stehen.
                final = str(response.url)
                if final != target:
                    self._check(final)

                content_type = response.headers.get("content-type", "")
                if not any(kind in content_type for kind in TEXTUAL):
                    return FetchedPage(
                        url=final, error=f"Kein Text, sondern '{content_type.split(';')[0]}'."
                    )

                chunks: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    # Den Chunk selbst zuschneiden, nicht erst danach
                    # abbrechen: sonst hängt die tatsächliche Obergrenze an
                    # der Chunk-Größe der Gegenseite, und ein einziger
                    # großer Block läge schon im Speicher.
                    rest = self._max_bytes - size
                    if len(chunk) >= rest:
                        chunks.append(chunk[:rest])
                        truncated = True
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                raw = b"".join(chunks)
        except PolicyViolation as exc:
            return FetchedPage(url=target, error=str(exc))
        except httpx.HTTPStatusError as exc:
            return FetchedPage(url=target, error=f"HTTP {exc.response.status_code}.")
        except httpx.HTTPError as exc:
            return FetchedPage(url=target, error=f"Nicht erreichbar ({type(exc).__name__}).")

        html = raw.decode("utf-8", errors="replace")
        text, title = extract_text(html)
        if not text:
            return FetchedPage(url=target, error="Kein lesbarer Text auf der Seite.")
        return FetchedPage(url=target, text=text, title=title, truncated=truncated)

    def _check(self, url: str) -> str:
        """Dieselbe Schleuse wie beim Browser — inklusive Allowlist."""
        return self._policy.check_url(url)

    async def _allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            self._robots[root] = await self._load_robots(root)
        parser = self._robots[root]
        if parser is None:
            # Keine robots.txt oder nicht erreichbar: erlaubt.
            return True
        return bool(parser.can_fetch(USER_AGENT, url))

    async def _load_robots(self, root: str) -> RobotFileParser | None:
        try:
            response = await self._client.get(urljoin(root, "/robots.txt"), timeout=5.0)
            if response.status_code >= 400:
                return None
            parser = RobotFileParser()
            # `parse` erwartet Zeilen, nicht einen String.
            await asyncio.to_thread(parser.parse, response.text.splitlines())
            return parser
        except (httpx.HTTPError, ValueError):
            return None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def extract_text(html: str) -> tuple[str, str]:
    """(Fließtext, Titel).

    Mit `trafilatura`, wenn es da ist — es trennt Artikeltext deutlich
    besser von Navigation und Fußzeilen als alles Selbstgebaute. Fehlt das
    Paket, greift ein einfacher Ausbau: schlechter, aber kein Ausfall.
    """
    try:
        import trafilatura
    except ImportError:
        return _strip_tags(html), _title_from(html)

    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    if not text.strip():
        return _strip_tags(html), _title_from(html)
    metadata = trafilatura.extract_metadata(html)
    title = getattr(metadata, "title", "") or _title_from(html)
    return text.strip(), title


def _title_from(html: str) -> str:
    lowered = html.lower()
    start = lowered.find("<title")
    if start == -1:
        return ""
    start = lowered.find(">", start)
    end = lowered.find("</title>", start)
    if start == -1 or end == -1:
        return ""
    return _collapse(html[start + 1 : end])


def _strip_tags(html: str) -> str:
    """Notbehelf ohne trafilatura: Skripte raus, Tags raus, Leerraum glätten."""
    import re

    without_code = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return _collapse(re.sub(r"<[^>]+>", " ", without_code))


def _collapse(text: str) -> str:
    import html as html_module
    import re

    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()
