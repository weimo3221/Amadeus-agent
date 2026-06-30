from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


RuntimeEvent = dict[str, Any]


class RuntimeEventBus:
    def __init__(self, *, subscriber_queue_size: int = 200) -> None:
        self._subscriber_queue_size = subscriber_queue_size
        self._lock = threading.Lock()
        self._subscribers: dict[str, queue.Queue[RuntimeEvent]] = {}

    def publish(self, event_type: str, session_id: str, payload: dict[str, Any]) -> RuntimeEvent:
        event = {
            "id": str(uuid4()),
            "type": event_type,
            "sessionId": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with self._lock:
            subscribers = list(self._subscribers.values())
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                # Drop the oldest event for slow subscribers so worker threads never block.
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(event)
                except queue.Empty:
                    pass
        return event

    def subscribe(self) -> tuple[str, queue.Queue[RuntimeEvent]]:
        subscriber_id = str(uuid4())
        subscriber_queue: queue.Queue[RuntimeEvent] = queue.Queue(maxsize=self._subscriber_queue_size)
        with self._lock:
            self._subscribers[subscriber_id] = subscriber_queue
        return subscriber_id, subscriber_queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)
