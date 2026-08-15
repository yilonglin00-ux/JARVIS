"""Der Bestätigungsfluss.

Kern dieser Datei: **jeder** Ausgang außer einer ausdrücklichen Zustimmung
verhindert die Aktion. Ablehnung, Zeitablauf, Abbruch, verspätete Antwort —
alle vier landen bei „passiert nicht“.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.events import ConfirmationRequested, ConfirmationResolved, EventBus
from jarvis.security.confirm import ConfirmationBroker, ConfirmationRequest, Decision
from jarvis.security.risk import RiskLevel


def request(**kwargs: object) -> ConfirmationRequest:
    base: dict[str, object] = {"tool": "dateien.loeschen", "summary": "Wirklich löschen?"}
    return ConfirmationRequest(**{**base, **kwargs})  # type: ignore[arg-type]


async def test_zustimmung(bus: EventBus) -> None:
    broker = ConfirmationBroker(bus, timeout_s=5)
    task = asyncio.create_task(broker.ask(request()))
    await asyncio.sleep(0)

    [pending] = broker.pending_ids
    assert broker.resolve(pending, approved=True)
    assert (await task) is Decision.APPROVED


async def test_ablehnung(bus: EventBus) -> None:
    broker = ConfirmationBroker(bus, timeout_s=5)
    task = asyncio.create_task(broker.ask(request()))
    await asyncio.sleep(0)

    broker.resolve(broker.pending_ids[0], approved=False)
    decision = await task
    assert decision is Decision.DENIED
    assert not decision.approved


async def test_zeitablauf_gilt_als_ablehnung(bus: EventBus) -> None:
    """Der wichtigste Fall: Schweigen ist kein Ja."""
    broker = ConfirmationBroker(bus, timeout_s=0.01)
    decision = await broker.ask(request())
    assert decision is Decision.TIMEOUT
    assert not decision.approved
    assert broker.pending_ids == []


async def test_abbruch_waehrend_der_rueckfrage(bus: EventBus) -> None:
    """Barge-in mitten in der Rückfrage: Aktion unterbleibt, Abbruch läuft weiter."""
    broker = ConfirmationBroker(bus, timeout_s=5)
    subscription = bus.subscribe(ConfirmationResolved)
    task = asyncio.create_task(broker.ask(request()))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert broker.pending_ids == []
    events = [e for e in subscription.drain_nowait() if isinstance(e, ConfirmationResolved)]
    assert [(e.approved, e.decided_by) for e in events] == [(False, "cancelled")]
    subscription.close()


async def test_verspaetete_antwort_wird_abgewiesen(bus: EventBus) -> None:
    broker = ConfirmationBroker(bus, timeout_s=0.01)
    subscription = bus.subscribe(ConfirmationRequested)
    decision = await broker.ask(request())
    assert decision is Decision.TIMEOUT

    [event] = [e for e in subscription.drain_nowait() if isinstance(e, ConfirmationRequested)]
    # Wer nach Ablauf noch antwortet, findet nichts Offenes mehr vor.
    assert not broker.resolve(event.request_id, approved=True)
    subscription.close()


async def test_kill_switch_lehnt_alles_offene_ab(bus: EventBus) -> None:
    broker = ConfirmationBroker(bus, timeout_s=5)
    tasks = [asyncio.create_task(broker.ask(request(tool=f"t{i}"))) for i in range(3)]
    await asyncio.sleep(0)

    assert broker.deny_all(reason="kill_switch") == 3
    assert await asyncio.gather(*tasks) == [Decision.DENIED] * 3


async def test_ereignis_traegt_alles_fuer_den_dialog(bus: EventBus) -> None:
    """Der Client muss aus dem Ereignis allein einen Dialog bauen können."""
    broker = ConfirmationBroker(bus, timeout_s=0.01)
    subscription = bus.subscribe(ConfirmationRequested)
    await broker.ask(
        request(
            risk=RiskLevel.DESTRUCTIVE,
            requires_tap=True,
            arguments={"pfad": "notizen.md"},
        )
    )

    [event] = [e for e in subscription.drain_nowait() if isinstance(e, ConfirmationRequested)]
    assert event.risk == "destructive"
    assert event.requires_tap
    assert event.summary == "Wirklich löschen?"
    assert event.arguments == {"pfad": "notizen.md"}
    subscription.close()
