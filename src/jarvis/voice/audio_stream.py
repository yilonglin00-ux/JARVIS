"""Audiotransport zwischen Client und Core.

Auf dem Host gibt es **kein** Audiogerät. Das Mikrofon sitzt im iPad, der
Lautsprecher auch. Diese Datei ist die Nahtstelle: eingehende PCM-Frames
werden gepuffert und als Strom an den STT-Provider gereicht.

Der Puffer ist bewusst begrenzt und verwirft im Überlauf die *ältesten*
Frames. Ein hängender STT-Provider darf niemals dazu führen, dass der
WebSocket-Leser blockiert — dann käme auch `user.interrupt` nicht mehr
durch, und Barge-in wäre tot.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog

log = structlog.get_logger(__name__)

BYTES_PER_SAMPLE = 2  # 16 bit mono


def pcm_duration_ms(num_bytes: int, sample_rate: int) -> float:
    return num_bytes / (sample_rate * BYTES_PER_SAMPLE) * 1000


class AudioInbox:
    """Eingehende Mikrofonframes als abschließbarer async Strom."""

    def __init__(self, *, sample_rate: int = 16000, max_frames: int = 200) -> None:
        self.sample_rate = sample_rate
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max_frames)
        self._closed = False
        self.dropped_frames = 0
        self.received_bytes = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, frame: bytes) -> None:
        """Frame einreihen. Nie blockierend, nie awaitbar — läuft im WS-Leser."""
        if self._closed or not frame:
            return
        self.received_bytes += len(frame)
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self.dropped_frames += 1
            if self.dropped_frames % 50 == 1:
                log.warning("audio.inbox_overflow", dropped=self.dropped_frames)
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(frame)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def stream(self) -> AsyncIterator[bytes]:
        """Frames ausliefern, bis `close()` gerufen wurde."""
        while True:
            frame = await self._queue.get()
            if frame is None:
                return
            yield frame

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self.stream()
