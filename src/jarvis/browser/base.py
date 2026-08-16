"""Die Browser-Abstraktion.

Zwischen Werkzeug und Playwright liegt bewusst ein Interface. Zwei Gründe,
beide praktisch: die Browser-Werkzeuge lassen sich damit ohne Chromium
testen, und der Austausch der Automatisierung (Playwright, Patchright,
eine Fernsteuerung auf einem anderen Rechner) kostet keinen Umbau an den
Werkzeugen.

Was hier *nicht* passiert: die Policy prüfen. Das macht die Ebene darüber,
damit keine Implementierung sie vergessen kann.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PageElement:
    """Ein bedienbares Element, adressiert über Rolle und Name.

    Kein CSS-Selektor: die überleben das nächste Layout-Update nicht.
    Rolle und zugänglicher Name kommen aus dem Accessibility-Tree und
    beschreiben, was der Nutzer *meint* (Architektur §7).
    """

    ref: str
    role: str
    name: str

    def describe(self) -> str:
        return f"[{self.ref}] {self.role}: {self.name}"


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    url: str
    title: str
    # Lesbarer Fließtext, nicht roher DOM — der sprengt jedes Kontextfenster.
    text: str = ""
    elements: tuple[PageElement, ...] = field(default_factory=tuple)
    truncated: bool = False


class BrowserSession(ABC):
    """Ein isolierter Browser-Kontext mit eigenen Cookies."""

    @abstractmethod
    async def goto(self, url: str) -> PageSnapshot: ...

    @abstractmethod
    async def snapshot(self) -> PageSnapshot: ...

    @abstractmethod
    async def click(self, ref: str) -> PageSnapshot: ...

    @abstractmethod
    async def fill(self, ref: str, value: str) -> PageSnapshot: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class BrowserBackend(ABC):
    """Erzeugt Sitzungen. Startet den Browser erst beim ersten Zugriff."""

    @abstractmethod
    async def session(self) -> BrowserSession: ...

    @abstractmethod
    async def aclose(self) -> None: ...
