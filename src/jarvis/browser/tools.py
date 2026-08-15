"""Die Browser-Werkzeuge.

Hier sitzen drei Sicherungen, die in dieser Reihenfolge greifen:

1. **Allowlist.** Jede URL geht durch `PolicyEngine.check_url`, auch die,
   auf die ein Klick führt. Sonst wäre die Prüfung beim Öffnen nur eine
   Formalie — ein Link auf der erlaubten Seite trüge einen sonst überall
   hin.
2. **Risiko nach Element.** Einem Link zu folgen ist Navigation. Einen
   Knopf zu drücken kann ein Formular abschicken oder etwas kaufen. Beides
   heißt „klicken“ und ist nicht dasselbe.
3. **Kennzeichnung.** Alles, was von einer Seite kommt, ist
   `untrusted=True`. Der Kernel klammert es in eine Warnung ein, und im
   System-Prompt steht, was das bedeutet. Prompt-Injection ist kein
   Randfall, sondern der Normalfall für einen Agenten, der fremde Seiten
   liest (Architektur §7).
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

from jarvis.browser.base import BrowserBackend, PageElement, PageSnapshot
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.result import ToolResult

log = structlog.get_logger(__name__)

# Rollen, deren Betätigung nach außen wirken kann.
ACTING_ROLES = frozenset({"button", "checkbox", "radio", "switch", "menuitem", "tab"})
MAX_TEXT_IN_RESULT = 8_000


class _BrowserTool(Tool):
    """Teilt Backend und den zuletzt gesehenen Seitenstand.

    Der letzte Stand wird gebraucht, weil `risk_for` synchron ist: die
    Frage „ist `e7` ein Link oder ein Kaufknopf?“ muss beantwortet sein,
    bevor entschieden wird, ob eine Rückfrage nötig ist.
    """

    def __init__(self, backend: BrowserBackend, state: dict[str, PageElement]) -> None:
        self._backend = backend
        self._elements = state

    def _remember(self, snapshot: PageSnapshot) -> None:
        self._elements.clear()
        for element in snapshot.elements:
            self._elements[element.ref] = element

    def _render(self, snapshot: PageSnapshot, *, with_elements: bool = True) -> str:
        parts = [f"{snapshot.title} — {snapshot.url}", "", snapshot.text[:MAX_TEXT_IN_RESULT]]
        if snapshot.truncated or len(snapshot.text) > MAX_TEXT_IN_RESULT:
            parts.append("… (gekürzt)")
        if with_elements and snapshot.elements:
            parts += ["", "Bedienbare Elemente:"]
            parts += [element.describe() for element in snapshot.elements]
        return "\n".join(parts)


class OpenPageTool(_BrowserTool):
    name = "browser.oeffnen"
    description = (
        "Öffnet eine Webseite und liefert ihren Text sowie die bedienbaren Elemente. "
        "Benutzen, wenn nach dem Inhalt einer konkreten Adresse gefragt wird."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Vollständige Adresse."}},
        "required": ["url"],
    }
    risk = RiskLevel.READ
    timeout_s = 45.0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = ctx.policy.check_url(str(args["url"]))
        session = await self._backend.session()
        snapshot = await session.goto(url)
        self._remember(snapshot)
        return ToolResult.success(
            self._render(snapshot),
            data={"url": snapshot.url, "titel": snapshot.title, "text": snapshot.text},
            untrusted=True,
        )


class ReadPageTool(_BrowserTool):
    name = "browser.lesen"
    description = "Liest die aktuell geöffnete Seite erneut, etwa nach einem Klick."
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    risk = RiskLevel.READ
    timeout_s = 45.0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        session = await self._backend.session()
        snapshot = await session.snapshot()
        self._remember(snapshot)
        return ToolResult.success(
            self._render(snapshot),
            data={"url": snapshot.url, "titel": snapshot.title, "text": snapshot.text},
            untrusted=True,
        )


class ClickTool(_BrowserTool):
    name = "browser.klicken"
    description = (
        "Klickt ein Element der aktuellen Seite an, adressiert über sein Kürzel wie 'e3'. "
        "Die Kürzel stehen in der Elementliste der zuletzt gelesenen Seite."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"element": {"type": "string", "description": "Kürzel, etwa 'e3'."}},
        "required": ["element"],
    }
    # Boden: einem Link zu folgen ist Navigation. `risk_for` hebt an,
    # sobald es kein Link ist.
    risk = RiskLevel.LOW
    timeout_s = 45.0

    def _element(self, args: dict[str, Any]) -> PageElement | None:
        return self._elements.get(str(args.get("element", "")))

    def risk_for(self, args: dict[str, Any]) -> RiskLevel:
        element = self._element(args)
        if element is None:
            # Unbekanntes Element: der Aufruf scheitert gleich ohnehin,
            # aber bis dahin gilt die vorsichtigere Annahme.
            return RiskLevel.SENSITIVE
        return RiskLevel.SENSITIVE if element.role in ACTING_ROLES else RiskLevel.LOW

    def summarize(self, args: dict[str, Any]) -> str:
        element = self._element(args)
        if element is None:
            return f"Soll ich das Element „{args.get('element', '?')}“ anklicken?"
        return f"Soll ich {element.role} „{element.name}“ anklicken?"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        session = await self._backend.session()
        snapshot = await session.click(str(args["element"]))
        # Auch das Klickziel muss durch die Allowlist: ein Link auf einer
        # erlaubten Seite darf nicht zum Schlupfloch werden.
        ctx.policy.check_url(snapshot.url)
        self._remember(snapshot)
        return ToolResult.success(
            self._render(snapshot),
            data={"url": snapshot.url, "titel": snapshot.title, "text": snapshot.text},
            untrusted=True,
        )


class FillTool(_BrowserTool):
    name = "browser.ausfuellen"
    description = (
        "Trägt Text in ein Eingabefeld der aktuellen Seite ein. Schickt nichts ab — "
        "dafür danach den passenden Knopf anklicken."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "element": {"type": "string", "description": "Kürzel des Feldes, etwa 'e2'."},
            "text": {"type": "string", "description": "Der einzutragende Text."},
        },
        "required": ["element", "text"],
    }
    # Eintragen allein bewirkt nichts; erst das Absenden wirkt nach außen.
    risk = RiskLevel.LOW
    timeout_s = 45.0

    def summarize(self, args: dict[str, Any]) -> str:
        element = self._elements.get(str(args.get("element", "")))
        wo = f"„{element.name}“" if element else f"„{args.get('element', '?')}“"
        return f"Soll ich {wo} mit „{args.get('text', '')}“ ausfüllen?"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        session = await self._backend.session()
        snapshot = await session.fill(str(args["element"]), str(args["text"]))
        self._remember(snapshot)
        return ToolResult.success(
            f"Eingetragen. {self._render(snapshot, with_elements=False)}",
            data={"url": snapshot.url},
            untrusted=True,
        )


def browser_tools(backend: BrowserBackend) -> list[Tool]:
    """Alle Browser-Werkzeuge mit geteiltem Seitenstand."""
    state: dict[str, PageElement] = {}
    return [
        OpenPageTool(backend, state),
        ReadPageTool(backend, state),
        ClickTool(backend, state),
        FillTool(backend, state),
    ]
