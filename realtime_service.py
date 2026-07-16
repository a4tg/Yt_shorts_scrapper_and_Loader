from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class EventSubscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, object]]


class ProjectEventBroker:
    """Small in-process realtime broker for the single-worker deployment."""

    def __init__(self, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[str, set[EventSubscriber]] = defaultdict(set)
        self._lock = threading.Lock()

    @asynccontextmanager
    async def subscribe(self, project_id: str) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        subscriber = EventSubscriber(
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=self.queue_size),
        )
        with self._lock:
            self._subscribers[project_id].add(subscriber)
        try:
            yield subscriber.queue
        finally:
            with self._lock:
                listeners = self._subscribers.get(project_id)
                if listeners is not None:
                    listeners.discard(subscriber)
                    if not listeners:
                        self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, event: dict[str, object]) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.get(project_id, ()))
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._enqueue, subscriber.queue, event)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[dict[str, object]], event: dict[str, object]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)


project_events = ProjectEventBroker()
