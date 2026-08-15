"""Werkzeuge und Bestätigung über den echten WebSocket.

Die Einzeltests weiter oben prüfen die Bausteine. Hier geht es um den Weg,
den es im Betrieb wirklich gibt: Modell will ein Werkzeug → Host fragt über
die Leitung nach → iPad antwortet → Aktion läuft (oder eben nicht). Genau
dieser Weg war in Phase 2 die Stelle, an der ein falsch gewählter Bus
niemandem auffiel, bis ein Ende-zu-Ende-Test ihn beleuchtet hat.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.config.settings import Profile, Secrets, Settings
from jarvis.interfaces.server import create_app
from jarvis.llm.fake import FakeLLM, ToolTurn
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.result import ToolResult

TOKEN = "test-token-mit-genug-laenge"


class SendMailTool(Tool):
    """Steht stellvertretend für alles, was nach außen wirkt."""

    name = "mail.senden"
    description = "Sendet eine Mail."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"an": {"type": "string"}, "betreff": {"type": "string"}},
        "required": ["an", "betreff"],
    }
    risk = RiskLevel.SENSITIVE

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def summarize(self, args: dict[str, Any]) -> str:
        return (
            f"Soll ich die Mail an {args.get('an')} mit dem Betreff „{args.get('betreff')}“ senden?"
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.sent.append(args)
        return ToolResult.success("Mail gesendet.")


@pytest.fixture
def app() -> FastAPI:
    profile = Profile.model_validate(
        {
            "profile": {"language": "de", "persona": "Du bist JARVIS."},
            "providers": {"stt": "fake", "tts": "fake", "llm": "fake"},
        }
    )
    settings = Settings(
        profile=profile,
        secrets=Secrets(_env_file=None, jarvis_auth_token=TOKEN),
    )
    return create_app(settings)


@pytest.fixture
def mail_tool() -> SendMailTool:
    return SendMailTool()


@pytest.fixture
def client(app: FastAPI, mail_tool: SendMailTool) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        # Nach dem Lifespan überschreiben: das Skript für das Modell und
        # ein Werkzeug, dessen Ausführung nachweisbar ist.
        app.state.llm = FakeLLM(
            [
                ToolTurn(
                    text="Einen Moment. ",
                    calls=[("mail.senden", {"an": "anna@example.com", "betreff": "Angebot"})],
                ),
                "Erledigt.",
            ]
        )
        app.state.tools = ToolRegistry([mail_tool])
        yield test_client


def drain_until(socket: Any, wanted: set[str], limit: int = 60) -> list[dict[str, Any]]:
    """Nachrichten lesen, bis einer der gesuchten Typen dabei ist."""
    seen: list[dict[str, Any]] = []
    for _ in range(limit):
        message = socket.receive()
        if message.get("text") is None:
            continue
        payload = json.loads(message["text"])
        seen.append(payload)
        if payload["type"] in wanted:
            return seen
    raise AssertionError(f"{wanted} kam nicht an. Gesehen: {[m['type'] for m in seen]}")


def test_sensible_aktion_loest_rueckfrage_aus(client: TestClient, mail_tool: SendMailTool) -> None:
    """Die Abnahmebedingung aus Phase 3, über die echte Leitung geprüft."""
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()  # session.ready
        socket.send_text(json.dumps({"type": "user.text", "text": "Schreib Anna"}))

        seen = drain_until(socket, {"confirm.request"})
        request = seen[-1]

        assert request["tool"] == "mail.senden"
        assert request["risk"] == "sensitive"
        # Konkret, nicht generisch: der Nutzer muss beurteilen können, was
        # gleich passiert.
        assert "anna@example.com" in request["summary"]
        assert "Angebot" in request["summary"]
        assert request["arguments"]["an"] == "anna@example.com"
        assert not request["requires_tap"]  # erst DESTRUCTIVE verlangt den Tap

        # Bis hierhin ist nichts passiert.
        assert mail_tool.sent == []


def test_zustimmung_fuehrt_die_aktion_aus(client: TestClient, mail_tool: SendMailTool) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()
        socket.send_text(json.dumps({"type": "user.text", "text": "Schreib Anna"}))
        request = drain_until(socket, {"confirm.request"})[-1]

        socket.send_text(
            json.dumps(
                {
                    "type": "confirm.response",
                    "request_id": request["request_id"],
                    "approved": True,
                }
            )
        )
        seen = drain_until(socket, {"reply.completed"})

    assert mail_tool.sent == [{"an": "anna@example.com", "betreff": "Angebot"}]
    types = [message["type"] for message in seen]
    assert "confirm.resolved" in types
    assert "tool.started" in types
    assert "tool.finished" in types


def test_ablehnung_verhindert_die_aktion(client: TestClient, mail_tool: SendMailTool) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()
        socket.send_text(json.dumps({"type": "user.text", "text": "Schreib Anna"}))
        request = drain_until(socket, {"confirm.request"})[-1]

        socket.send_text(
            json.dumps(
                {
                    "type": "confirm.response",
                    "request_id": request["request_id"],
                    "approved": False,
                }
            )
        )
        seen = drain_until(socket, {"reply.completed"})

    assert mail_tool.sent == []
    resolved = [m for m in seen if m["type"] == "confirm.resolved"]
    assert resolved and resolved[0]["approved"] is False
    # Der Turn läuft trotzdem sauber zu Ende und JARVIS antwortet.
    assert seen[-1]["spoken"]


def test_kill_switch_raeumt_offene_rueckfragen_ab(
    client: TestClient, mail_tool: SendMailTool
) -> None:
    with client.websocket_connect(f"/ws?token={TOKEN}") as socket:
        socket.receive_text()
        socket.send_text(json.dumps({"type": "user.text", "text": "Schreib Anna"}))
        drain_until(socket, {"confirm.request"})

        socket.send_text(json.dumps({"type": "user.stop"}))
        seen = drain_until(socket, {"confirm.resolved"})

    assert mail_tool.sent == []
    assert seen[-1]["approved"] is False
