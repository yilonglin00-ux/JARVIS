"""Werkzeugverzeichnis und Ausführungsstrecke.

Geprüft wird vor allem die Reihenfolge der Schleuse: dass eine
bestätigungspflichtige Aktion ohne Zustimmung **gar nicht erst läuft**, und
dass ein Werkzeug diese Prüfung nicht umgehen kann, indem es sie einfach
nicht aufruft — sie liegt nicht im Werkzeug.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest

from jarvis.core.events import EventBus, ToolFinished, ToolStarted
from jarvis.security.confirm import ConfirmationBroker
from jarvis.security.policy import Policies, PolicyEngine
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool, ToolContext
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.result import ToolResult


class SpyTool(Tool):
    """Zählt, wie oft es wirklich ausgeführt wurde."""

    name = "spion"
    description = "Tut nichts und merkt es sich."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"wert": {"type": "string"}},
    }

    def __init__(self, risk: RiskLevel = RiskLevel.READ) -> None:
        self.risk = risk
        self.calls: list[dict[str, Any]] = []

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult.success("fertig")


class ExplodingTool(Tool):
    name = "bombe"
    description = "Wirft immer."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise RuntimeError("kaputt")


class SlowTool(Tool):
    name = "schnecke"
    description = "Antwortet nie."
    timeout_s = 0.02

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult.success("nie")


# --- Verzeichnis --------------------------------------------------------------


def test_doppelte_registrierung_wirft() -> None:
    registry = ToolRegistry([SpyTool()])
    with pytest.raises(ValueError, match="bereits registriert"):
        registry.register(SpyTool())


def test_schemas_lassen_sich_filtern() -> None:
    registry = ToolRegistry([SpyTool(), ExplodingTool()])
    assert {spec.name for spec in registry.specs()} == {"spion", "bombe"}
    assert [spec.name for spec in registry.specs(allowed={"spion"})] == ["spion"]


# --- Schleuse -----------------------------------------------------------------


async def test_lesendes_werkzeug_laeuft_ohne_rueckfrage(tool_ctx: ToolContext) -> None:
    tool = SpyTool(RiskLevel.READ)
    result = await ToolRegistry([tool]).invoke("spion", {"wert": "x"}, tool_ctx)
    assert result.ok
    assert tool.calls == [{"wert": "x"}]


async def test_sensible_aktion_ohne_antwort_laeuft_nicht(tool_ctx: ToolContext) -> None:
    """Timeout in der Rückfrage heißt: das Werkzeug wird nie aufgerufen."""
    tool = SpyTool(RiskLevel.SENSITIVE)
    result = await ToolRegistry([tool]).invoke("spion", {}, tool_ctx)
    assert not result.ok
    assert tool.calls == []
    assert "nicht ausgeführt" in result.error


async def test_sensible_aktion_nach_zustimmung(bus: EventBus, tool_ctx: ToolContext) -> None:
    broker = ConfirmationBroker(bus, timeout_s=5)
    tool_ctx.confirm = broker
    tool = SpyTool(RiskLevel.SENSITIVE)
    registry = ToolRegistry([tool])

    task = asyncio.create_task(registry.invoke("spion", {"wert": "ja"}, tool_ctx))
    await asyncio.sleep(0)
    broker.resolve(broker.pending_ids[0], approved=True)

    assert (await task).ok
    assert tool.calls == [{"wert": "ja"}]


async def test_ablehnung_verhindert_ausfuehrung(bus: EventBus, tool_ctx: ToolContext) -> None:
    broker = ConfirmationBroker(bus, timeout_s=5)
    tool_ctx.confirm = broker
    tool = SpyTool(RiskLevel.SENSITIVE)

    task = asyncio.create_task(ToolRegistry([tool]).invoke("spion", {}, tool_ctx))
    await asyncio.sleep(0)
    broker.resolve(broker.pending_ids[0], approved=False)

    assert not (await task).ok
    assert tool.calls == []


async def test_policy_macht_aus_read_eine_rueckfrage(bus: EventBus, sandbox: object) -> None:
    """Die Policy kann jedes Werkzeug bestätigungspflichtig machen."""
    policy = PolicyEngine(
        Policies.model_validate(
            {"defaults": {"confirm_timeout_s": 0.01}, "tools": {"spion": {"risk": "sensitive"}}}
        )
    )
    ctx = ToolContext(
        session_id="t",
        bus=bus,
        policy=policy,
        confirm=ConfirmationBroker(bus, timeout_s=0.01),
    )
    tool = SpyTool(RiskLevel.READ)
    assert not (await ToolRegistry([tool]).invoke("spion", {}, ctx)).ok
    assert tool.calls == []


async def test_abgeschaltetes_werkzeug(bus: EventBus) -> None:
    policy = PolicyEngine(Policies.model_validate({"tools": {"spion": {"disabled": True}}}))
    ctx = ToolContext(
        session_id="t", bus=bus, policy=policy, confirm=ConfirmationBroker(bus, timeout_s=1)
    )
    tool = SpyTool()
    result = await ToolRegistry([tool]).invoke("spion", {}, ctx)
    assert not result.ok
    assert "abgeschaltet" in result.error
    assert tool.calls == []


# --- Robustheit ---------------------------------------------------------------


async def test_unbekanntes_werkzeug(tool_ctx: ToolContext) -> None:
    result = await ToolRegistry().invoke("gibtsnicht", {}, tool_ctx)
    assert not result.ok
    assert "Unbekanntes Werkzeug" in result.error


async def test_absturz_wird_zum_ergebnis(tool_ctx: ToolContext) -> None:
    """Ein kaputtes Werkzeug darf den Turn nicht beenden."""
    result = await ToolRegistry([ExplodingTool()]).invoke("bombe", {}, tool_ctx)
    assert not result.ok
    assert "kaputt" in result.error


async def test_zeitueberschreitung(tool_ctx: ToolContext) -> None:
    result = await ToolRegistry([SlowTool()]).invoke("schnecke", {}, tool_ctx)
    assert not result.ok
    assert "nicht geantwortet" in result.error


async def test_fehlendes_pflichtfeld(tool_ctx: ToolContext) -> None:
    class Strict(SpyTool):
        input_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"wert": {"type": "string"}},
            "required": ["wert"],
        }

    tool = Strict()
    result = await ToolRegistry([tool]).invoke("spion", {}, tool_ctx)
    assert not result.ok
    assert "Pflichtfeld" in result.error
    assert tool.calls == []


async def test_falscher_typ(tool_ctx: ToolContext) -> None:
    result = await ToolRegistry([SpyTool()]).invoke("spion", {"wert": 42}, tool_ctx)
    assert not result.ok
    assert "erwartet string" in result.error


# --- Ereignisse ---------------------------------------------------------------


async def test_ereignisse_umrahmen_die_ausfuehrung(bus: EventBus, tool_ctx: ToolContext) -> None:
    subscription = bus.subscribe(ToolStarted, ToolFinished)
    await ToolRegistry([SpyTool()]).invoke("spion", {"wert": "x"}, tool_ctx, call_id="c1")

    started, finished = subscription.drain_nowait()
    assert isinstance(started, ToolStarted)
    assert isinstance(finished, ToolFinished)
    assert started.call_id == finished.call_id == "c1"
    assert finished.ok
    subscription.close()


async def test_secrets_werden_im_ereignis_maskiert(bus: EventBus, tool_ctx: ToolContext) -> None:
    class WithSecret(SpyTool):
        input_schema: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
        }

    subscription = bus.subscribe(ToolStarted)
    await ToolRegistry([WithSecret()]).invoke("spion", {"api_key": "geheim123"}, tool_ctx)

    [started] = [e for e in subscription.drain_nowait() if isinstance(e, ToolStarted)]
    assert started.arguments == {"api_key": "<verborgen>"}
    subscription.close()
