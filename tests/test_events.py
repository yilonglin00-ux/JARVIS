from __future__ import annotations

import asyncio

from jarvis.core.events import (
    ConversationState,
    EventBus,
    ReplyDelta,
    StateChanged,
    TranscriptReceived,
)


async def _collect(subscription, count: int) -> list:
    events = []
    async for event in subscription:
        events.append(event)
        if len(events) == count:
            break
    return events


async def test_subscriber_receives_published_events(bus: EventBus) -> None:
    subscription = bus.subscribe()
    bus.publish(ReplyDelta(text="Hallo"))
    bus.publish(ReplyDelta(text=" Welt"))

    events = await asyncio.wait_for(_collect(subscription, 2), timeout=1)

    assert [e.text for e in events] == ["Hallo", " Welt"]
    subscription.close()


async def test_type_filter_only_delivers_matching_events(bus: EventBus) -> None:
    subscription = bus.subscribe(StateChanged)
    bus.publish(ReplyDelta(text="ignoriert"))
    bus.publish(StateChanged(state=ConversationState.LISTENING))

    events = await asyncio.wait_for(_collect(subscription, 1), timeout=1)

    assert isinstance(events[0], StateChanged)
    assert events[0].state is ConversationState.LISTENING
    subscription.close()


async def test_slow_subscriber_drops_oldest_instead_of_blocking(bus: EventBus) -> None:
    # Ein iPad mit schlechtem Netz darf die Sprachschleife nicht anhalten.
    subscription = bus.subscribe(queue_size=4)
    for index in range(10):
        bus.publish(TranscriptReceived(text=str(index)))

    assert subscription.dropped == 6
    events = await asyncio.wait_for(_collect(subscription, 4), timeout=1)
    assert [e.text for e in events] == ["6", "7", "8", "9"]
    subscription.close()


async def test_closing_subscription_ends_iteration(bus: EventBus) -> None:
    subscription = bus.subscribe()
    subscription.close()

    received = [event async for event in subscription]

    assert received == []
    assert bus.subscriber_count == 0


async def test_publish_does_not_fail_without_subscribers(bus: EventBus) -> None:
    bus.publish(ReplyDelta(text="niemand hört zu"))
