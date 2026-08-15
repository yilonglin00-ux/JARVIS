"""Playwright-Backend.

Läuft ausschließlich auf dem Host; das iPad sieht nur Ergebnisse. Der
Browser bekommt ein eigenes Profil, getrennt vom persönlichen — ein Agent,
der in der angemeldeten Alltagssitzung klickt, ist eine andere Klasse von
Risiko als einer mit eigenen Cookies.

Der Seiteninhalt wird über den Accessibility-Tree gelesen und nicht über
den DOM: zehn- bis fünfzigmal kleiner und semantisch näher an dem, was der
Nutzer meint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from jarvis.browser.base import BrowserBackend, BrowserSession, PageElement, PageSnapshot
from jarvis.core.errors import ConfigurationError, ProviderError

if TYPE_CHECKING:  # pragma: no cover - nur für die Typprüfung
    from playwright.async_api import Browser, BrowserContext, Page

log = structlog.get_logger(__name__)

# Rollen, die man anklicken oder ausfüllen kann. Alles andere ist Deko und
# würde die Liste für das Modell nur verrauschen.
INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "menuitem",
        "tab",
        "switch",
        "slider",
    }
)

MAX_TEXT_CHARS = 12_000
MAX_ELEMENTS = 60
DEFAULT_TIMEOUT_MS = 15_000


class PlaywrightSession(BrowserSession):
    def __init__(self, page: Page) -> None:
        self._page = page
        self._elements: dict[str, PageElement] = {}
        self._locators: dict[str, Any] = {}

    async def goto(self, url: str) -> PageSnapshot:
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except Exception as exc:
            raise ProviderError("browser", f"'{url}' ließ sich nicht laden: {exc}") from exc
        return await self.snapshot()

    async def snapshot(self) -> PageSnapshot:
        text = await self._readable_text()
        elements = await self._interactive_elements()
        self._elements = {element.ref: element for element in elements}
        return PageSnapshot(
            url=self._page.url,
            title=await self._page.title(),
            text=text[:MAX_TEXT_CHARS],
            elements=tuple(elements),
            truncated=len(text) > MAX_TEXT_CHARS,
        )

    async def _readable_text(self) -> str:
        # `innerText` respektiert Sichtbarkeit — versteckte Navigation und
        # Skript-Inhalte fallen damit von selbst weg.
        try:
            raw: str = await self._page.inner_text("body", timeout=DEFAULT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - kein body: leerer Text statt Abbruch
            return ""
        lines = [line.strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)

    async def _interactive_elements(self) -> list[PageElement]:
        elements: list[PageElement] = []
        self._locators.clear()
        for role in sorted(INTERACTIVE_ROLES):
            if len(elements) >= MAX_ELEMENTS:
                break
            locator = self._page.get_by_role(role)
            try:
                count = min(await locator.count(), MAX_ELEMENTS - len(elements))
            except Exception:  # noqa: BLE001 - Rolle unbekannt: überspringen
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    name = (await item.inner_text(timeout=1000)).strip()
                except Exception:  # noqa: BLE001 - kein Text: über aria-label versuchen
                    name = ""
                if not name:
                    name = (await item.get_attribute("aria-label")) or ""
                ref = f"e{len(elements) + 1}"
                elements.append(PageElement(ref=ref, role=role, name=name.strip()[:120]))
                self._locators[ref] = item
        return elements

    def _locator(self, ref: str) -> Any:
        locator = self._locators.get(ref)
        if locator is None:
            known = ", ".join(sorted(self._locators)) or "keine"
            raise ProviderError("browser", f"Unbekanntes Element '{ref}'. Bekannt: {known}")
        return locator

    async def click(self, ref: str) -> PageSnapshot:
        try:
            await self._locator(ref).click(timeout=DEFAULT_TIMEOUT_MS)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("browser", f"Klick auf '{ref}' scheiterte: {exc}") from exc
        return await self.snapshot()

    async def fill(self, ref: str, value: str) -> PageSnapshot:
        try:
            await self._locator(ref).fill(value, timeout=DEFAULT_TIMEOUT_MS)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("browser", f"Eingabe in '{ref}' scheiterte: {exc}") from exc
        return await self.snapshot()

    async def aclose(self) -> None:
        await self._page.close()


class PlaywrightBackend(BrowserBackend):
    """Startet Chromium beim ersten Zugriff, nicht beim Import.

    Ein Browserprozess, der bei jedem Hochfahren des Hosts mitläuft, obwohl
    monatelang niemand eine Webseite anfordert, ist verschwendeter
    Arbeitsspeicher.
    """

    def __init__(self, *, headless: bool = True, download_dir: str | None = None) -> None:
        self._headless = headless
        self._download_dir = download_dir
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._session: PlaywrightSession | None = None

    async def _ensure_context(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - Abhängigkeit optional
            raise ConfigurationError(
                "Paket 'playwright' nicht installiert: uv pip install -e '.[browser]' "
                "und danach 'playwright install chromium'."
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            accept_downloads=bool(self._download_dir),
            **({"downloads_path": self._download_dir} if self._download_dir else {}),
        )
        self._context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        log.info("browser.started", headless=self._headless)
        return self._context

    async def session(self) -> BrowserSession:
        if self._session is not None:
            return self._session
        context = await self._ensure_context()
        self._session = PlaywrightSession(await context.new_page())
        return self._session

    async def aclose(self) -> None:
        self._session = None
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:  # pragma: no cover - Abbau darf nie werfen
                    log.debug("browser.close_failed", exc_info=True)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # pragma: no cover
                log.debug("browser.stop_failed", exc_info=True)
        self._context = None
        self._browser = None
        self._playwright = None
