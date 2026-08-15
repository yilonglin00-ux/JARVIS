"""Die Browser-Werkzeuge — gegen den Fake-Browser, ohne Chromium.

Die beiden Prüfungen, auf die es ankommt:

* Das Klickziel geht **erneut** durch die Allowlist. Sonst wäre ein Link
  auf einer erlaubten Seite ein Weg nach überall.
* Alles, was von einer Seite kommt, ist als fremde Quelle gekennzeichnet.
"""

from __future__ import annotations

import pytest

from jarvis.browser.fake import FakeBrowserBackend, FakePage
from jarvis.browser.tools import ClickTool, FillTool, OpenPageTool, browser_tools
from jarvis.core.errors import PolicyViolation
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import ToolContext

START = "https://example.com/"
INTERN = "https://intranet.test/geheim"


def backend() -> FakeBrowserBackend:
    return FakeBrowserBackend(
        {
            START: FakePage(
                title="Beispiel",
                text="Preise: Kaffee 3 Euro, Tee 2 Euro.",
                elements={
                    "e1": ("link", "Mehr erfahren", "https://example.com/mehr"),
                    "e2": ("button", "Jetzt kaufen", ""),
                    "e3": ("textbox", "Suche", ""),
                    "e4": ("link", "Partnerseite", INTERN),
                },
            ),
            "https://example.com/mehr": FakePage(title="Mehr", text="Noch mehr Text."),
            INTERN: FakePage(title="Intern", text="Nicht für Agenten."),
        }
    )


async def test_seite_oeffnen_liefert_text_und_elemente(tool_ctx: ToolContext) -> None:
    tools = {tool.name: tool for tool in browser_tools(backend())}
    result = await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)

    assert result.ok
    assert "Kaffee 3 Euro" in result.display_text
    assert "[e1] link: Mehr erfahren" in result.display_text


async def test_seiteninhalt_ist_fremde_quelle(tool_ctx: ToolContext) -> None:
    """Ohne diese Kennzeichnung wäre Prompt-Injection eine offene Tür."""
    result = await OpenPageTool(backend(), {}).execute({"url": START}, tool_ctx)
    assert result.untrusted


async def test_gesperrte_domain(tool_ctx: ToolContext) -> None:
    with pytest.raises(PolicyViolation, match="allowed_domains"):
        await OpenPageTool(backend(), {}).execute({"url": INTERN}, tool_ctx)


async def test_klickziel_geht_erneut_durch_die_allowlist(tool_ctx: ToolContext) -> None:
    """Der Link steht auf einer erlaubten Seite — sein Ziel ist es nicht."""
    tools = {tool.name: tool for tool in browser_tools(backend())}
    await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)

    with pytest.raises(PolicyViolation, match="allowed_domains"):
        await tools["browser.klicken"].execute({"element": "e4"}, tool_ctx)


async def test_link_ist_navigation_knopf_ist_sensibel(tool_ctx: ToolContext) -> None:
    """Beides heißt „klicken“ und ist nicht dasselbe."""
    tools = {tool.name: tool for tool in browser_tools(backend())}
    await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)
    click = tools["browser.klicken"]
    assert isinstance(click, ClickTool)

    assert click.risk_for({"element": "e1"}) is RiskLevel.LOW
    assert click.risk_for({"element": "e2"}) is RiskLevel.SENSITIVE
    # Unbekanntes Element: im Zweifel die strengere Annahme.
    assert click.risk_for({"element": "e99"}) is RiskLevel.SENSITIVE


async def test_rueckfrage_nennt_das_element_beim_namen(tool_ctx: ToolContext) -> None:
    tools = {tool.name: tool for tool in browser_tools(backend())}
    await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)

    frage = tools["browser.klicken"].summarize({"element": "e2"})
    assert "Jetzt kaufen" in frage
    assert "button" in frage


async def test_klick_auf_link_navigiert(tool_ctx: ToolContext) -> None:
    tools = {tool.name: tool for tool in browser_tools(backend())}
    await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)
    result = await tools["browser.klicken"].execute({"element": "e1"}, tool_ctx)

    assert result.ok
    assert "Noch mehr Text" in result.display_text


async def test_ausfuellen_schickt_nichts_ab(tool_ctx: ToolContext) -> None:
    driver = backend()
    tools = {tool.name: tool for tool in browser_tools(driver)}
    await tools["browser.oeffnen"].execute({"url": START}, tool_ctx)

    fill = tools["browser.ausfuellen"]
    assert isinstance(fill, FillTool)
    assert fill.risk is RiskLevel.LOW  # erst der Knopf danach wirkt nach außen

    assert (await fill.execute({"element": "e3", "text": "Kaffee"}, tool_ctx)).ok
    session = await driver.session()
    assert session.filled == {"e3": "Kaffee"}  # type: ignore[attr-defined]
    assert session.clicks == []  # type: ignore[attr-defined]
