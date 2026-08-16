"""Kommandozeile — Entwicklung und Headless-Betrieb.

`jarvis chat` spricht denselben Core über dieselbe Session wie das iPad,
nur ohne Audio. Das ist kein zweiter JARVIS, sondern dasselbe System mit
einem anderen Interface (Architektur §13) — und der schnellste Weg, den
Core ohne Mikrofon und ohne Zertifikat zu testen.
"""

from __future__ import annotations

import asyncio
import secrets
import sys

import structlog
import typer

from jarvis import __version__, factory
from jarvis.config.settings import load_settings
from jarvis.core.errors import JarvisError
from jarvis.core.events import ConfirmationRequested, EventBus, ToolStarted
from jarvis.core.kernel import JarvisCore
from jarvis.core.session import Session, Turn
from jarvis.logging import configure_logging
from jarvis.security.confirm import ConfirmationBroker
from jarvis.security.policy import PolicyEngine, load_policies
from jarvis.tools.base import ToolContext

app = typer.Typer(add_completion=False, help="JARVIS — persönlicher KI-Assistent (Host)")
log = structlog.get_logger(__name__)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind-Adresse (Default aus .env)"),
    port: int | None = typer.Option(None, help="Port (Default aus .env)"),
    reload: bool = typer.Option(False, help="Autoreload für die Entwicklung"),
) -> None:
    """Den WebSocket-Host starten."""
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "jarvis.interfaces.server:create_app",
        factory=True,
        host=host or settings.secrets.jarvis_host,
        port=port or settings.secrets.jarvis_port,
        reload=reload,
        log_level=settings.secrets.jarvis_log_level,
    )


@app.command()
def chat() -> None:
    """Textgespräch im Terminal — ohne Mikrofon, ohne Client."""
    asyncio.run(_chat())


@app.command()
def token() -> None:
    """Einen Zugangstoken erzeugen (nach JARVIS_AUTH_TOKEN in .env kopieren)."""
    typer.echo(secrets.token_urlsafe(32))


@app.command()
def doctor() -> None:
    """Prüfen, ob Konfiguration und Schlüssel für den gewählten Modus reichen."""
    settings = load_settings()
    providers = settings.profile.providers
    policy = PolicyEngine(load_policies())
    typer.echo(f"JARVIS {__version__}")
    typer.echo(f"Sprache:  {settings.language}")
    typer.echo(f"Provider: stt={providers.stt}  tts={providers.tts}  llm={providers.llm}")

    tools = factory.build_tools(settings, policy)
    typer.echo(f"Werkzeuge: {', '.join(tools.names) if len(tools) else '— keine'}")
    typer.echo(f"Sandkasten: {policy.files_root}")
    typer.echo(f"Bestätigung ab: {policy.confirm_from} (Timeout {policy.confirm_timeout_s:.0f} s)")
    domains = policy.allowed_domains
    typer.echo(f"Browser-Domains: {', '.join(domains) if domains else '— keine, Browser aus'}")

    problems: list[str] = []
    if not settings.secrets.jarvis_auth_token.get_secret_value():
        problems.append("JARVIS_AUTH_TOKEN fehlt — 'jarvis token' erzeugt einen.")
    required = {
        "anthropic": ("ANTHROPIC_API_KEY", settings.secrets.anthropic_api_key),
        "deepgram": ("DEEPGRAM_API_KEY", settings.secrets.deepgram_api_key),
        "elevenlabs": ("ELEVENLABS_API_KEY", settings.secrets.elevenlabs_api_key),
    }
    for provider in (providers.stt, providers.tts, providers.llm):
        entry = required.get(provider)
        if entry and not entry[1].get_secret_value():
            problems.append(f"{entry[0]} fehlt (benötigt von Provider '{provider}').")

    if problems:
        typer.echo("")
        for problem in problems:
            typer.echo(f"  ✗ {problem}")
        raise typer.Exit(code=1)
    typer.echo("\n  ✓ Konfiguration vollständig.")


async def _watch_events(bus: EventBus, broker: ConfirmationBroker) -> None:
    """Werkzeuge anzeigen und Rückfragen im Terminal beantworten.

    Im Client macht das der Bestätigungsdialog. Hier übernimmt es das
    Terminal — dieselben Ereignisse, dieselbe Regel: nur ein ausdrückliches
    „j“ lässt die Aktion zu.
    """
    subscription = bus.subscribe(ConfirmationRequested, ToolStarted)
    try:
        async for event in subscription:
            if isinstance(event, ToolStarted):
                sys.stdout.write(f"\n  ⚙ {event.tool} ({event.risk})\n")
                sys.stdout.flush()
                continue
            if not isinstance(event, ConfirmationRequested):
                continue
            sys.stdout.write(f"\n  ⚠ {event.summary}\n")
            for key, value in event.arguments.items():
                sys.stdout.write(f"      {key}: {value}\n")
            answer = (await asyncio.to_thread(input, "  [j/N] › ")).strip().lower()
            broker.resolve(event.request_id, approved=answer in {"j", "ja"})
    finally:
        subscription.close()


async def _chat() -> None:
    settings = load_settings()
    configure_logging(
        level=settings.secrets.jarvis_log_level,
        log_transcripts=settings.secrets.jarvis_log_transcripts,
    )
    bus = EventBus()
    policy = PolicyEngine(load_policies())
    llm = factory.build_llm(settings)
    browser = factory.build_browser_backend(settings, policy)
    research = factory.build_research(settings, policy)
    tools = factory.build_tools(settings, policy, browser=browser, research=research)
    broker = ConfirmationBroker(bus, timeout_s=policy.confirm_timeout_s)
    core = JarvisCore(
        llm=llm,
        settings=settings,
        bus=bus,
        router=factory.build_router(settings),
        tools=tools,
        tool_context=ToolContext(session_id="cli", bus=bus, policy=policy, confirm=broker),
        max_tool_steps=policy.max_tool_steps,
    )
    session = Session(bus, history_turns=settings.voice.history_turns)
    watcher = asyncio.create_task(_watch_events(bus, broker), name="cli-events")

    typer.echo(f"JARVIS {__version__} — Textmodus. Beenden mit Strg-D oder /exit.")
    if len(tools):
        typer.echo(f"Werkzeuge: {', '.join(tools.names)}")
    typer.echo("")
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, "du  › ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            if line == "/reset":
                session.clear_history()
                typer.echo("(Verlauf gelöscht)\n")
                continue

            sys.stdout.write("jarvis › ")
            sys.stdout.flush()

            async def runner(turn: Turn) -> None:
                async for delta in core.stream_reply(session, turn.user_text):
                    turn.generated += delta
                    sys.stdout.write(delta)
                    sys.stdout.flush()
                # Geschriebener Text gilt vollständig als zugestellt.
                turn.spoken = turn.generated

            try:
                # Über `run_turn` statt direkt, damit der Verlauf exakt so
                # entsteht wie im Sprachmodus.
                await session.run_turn(line, runner)
            except JarvisError as exc:
                sys.stdout.write(f"\n(Fehler: {exc})")
            sys.stdout.write("\n\n")
    finally:
        watcher.cancel()
        bus.close()
        await core.aclose()
        if browser is not None:
            await browser.aclose()
        if research is not None:
            await research.aclose()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
