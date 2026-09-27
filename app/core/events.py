"""
A tiny in-process pub/sub for Server-Sent Events. One process holds one Broadcaster; each
connected kitchen display subscribes with a bounded queue and the SSE endpoint blocks reading
from it. This is intentionally simple: it fits the single-server desktop deployment this app
targets (see docs/DECISIONS.md). A clustered/multi-process deployment would need a shared
broker (e.g. Redis pub/sub) instead, since queues here are per-process memory, not shared.
"""
from __future__ import annotations

import queue
import threading
import time


class Broadcaster:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue] = {}
        self._next_id = 0

    def subscribe(self) -> tuple[int, queue.Queue]:
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            q: queue.Queue = queue.Queue(maxsize=100)
            self._subscribers[sub_id] = q
            return sub_id, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def publish(self, branch_id: int, event: str = "update") -> None:
        with self._lock:
            subs = list(self._subscribers.values())
        for q in subs:
            try:
                q.put_nowait((branch_id, event))
            except queue.Full:
                pass  # a slow/stuck client just misses a beat; it will still poll on reconnect


def stream(broadcaster: Broadcaster, branch_id: int | None, *, heartbeat_seconds: int = 15):
    """Generator for an SSE response: 'update' events for this branch, else a keep-alive comment."""
    sub_id, q = broadcaster.subscribe()
    try:
        # Flush the response immediately on connect, rather than leaving the client (and the
        # HTTP response's own headers, which some servers don't send until the first byte of
        # body) waiting up to `heartbeat_seconds` for anything at all.
        yield ": connected\n\n"
        while True:
            try:
                event_branch, event = q.get(timeout=heartbeat_seconds)
                if branch_id is None or event_branch == branch_id:
                    yield f"event: {event}\ndata: {int(time.time())}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"
    finally:
        broadcaster.unsubscribe(sub_id)
