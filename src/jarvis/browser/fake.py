"""Fake-Browser: ein Netz aus Seiten im Speicher.

Damit lassen sich die Browser-Werkzeuge vollständig prüfen — Allowlist,
Kennzeichnung fremder Inhalte, Risiko je nach Element, Klickfolgen — ohne
Chromium, ohne Netz und ohne Flakiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.browser.base import BrowserBackend, BrowserSession, PageElement, PageSnapshot
from jarvis.core.errors import ProviderError


@dataclass(slots=True)
class FakePage:
    title: str
    text: str = ""
    # ref -> (Rolle, Name, Ziel-URL bei Klick)
    elements: dict[str, tuple[str, str, str]] = field(default_factory=dict)


class FakeBrowserSession(BrowserSession):
    def __init__(self, pages: dict[str, FakePage]) -> None:
        self._pages = pages
        self._url = ""
        self.filled: dict[str, str] = {}
        self.clicks: list[str] = []

    @property
    def current_url(self) -> str:
        return self._url

    def _page(self) -> FakePage:
        page = self._pages.get(self._url)
        if page is None:
            raise ProviderError("browser", f"'{self._url}' gibt es nicht.")
        return page

    async def goto(self, url: str) -> PageSnapshot:
        if url not in self._pages:
            raise ProviderError("browser", f"'{url}' ließ sich nicht laden.")
        self._url = url
        return await self.snapshot()

    async def snapshot(self) -> PageSnapshot:
        page = self._page()
        return PageSnapshot(
            url=self._url,
            title=page.title,
            text=page.text,
            elements=tuple(
                PageElement(ref=ref, role=role, name=name)
                for ref, (role, name, _) in page.elements.items()
            ),
        )

    async def click(self, ref: str) -> PageSnapshot:
        page = self._page()
        if ref not in page.elements:
            raise ProviderError("browser", f"Unbekanntes Element '{ref}'.")
        self.clicks.append(ref)
        target = page.elements[ref][2]
        if target:
            return await self.goto(target)
        return await self.snapshot()

    async def fill(self, ref: str, value: str) -> PageSnapshot:
        page = self._page()
        if ref not in page.elements:
            raise ProviderError("browser", f"Unbekanntes Element '{ref}'.")
        self.filled[ref] = value
        return await self.snapshot()

    async def aclose(self) -> None:
        return None


class FakeBrowserBackend(BrowserBackend):
    def __init__(self, pages: dict[str, FakePage] | None = None) -> None:
        self.pages: dict[str, FakePage] = pages or {}
        self._session: FakeBrowserSession | None = None

    async def session(self) -> BrowserSession:
        if self._session is None:
            self._session = FakeBrowserSession(self.pages)
        return self._session

    async def aclose(self) -> None:
        self._session = None
