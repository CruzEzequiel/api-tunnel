from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Callable
from urllib.parse import urlencode


@dataclass(slots=True)
class TrafficEntry:
    id: str
    method: str
    path: str
    query: list[tuple[str, str]] = field(default_factory=list)
    request_headers: list[tuple[str, str]] = field(default_factory=list)
    request_body: bytes = b""
    status: int | None = None
    response_headers: list[tuple[str, str]] = field(default_factory=list)
    response_body: bytes = b""
    elapsed_ms: int | None = None
    error: str | None = None

    @property
    def display_path(self) -> str:
        if not self.query:
            return self.path
        return f"{self.path}?{urlencode(self.query)}"


Subscriber = Callable[[TrafficEntry], None]


class TrafficLog:
    def __init__(self, limit: int = 500) -> None:
        self.limit = limit
        self._entries: list[TrafficEntry] = []
        self._subscribers: list[Subscriber] = []
        self._lock = RLock()

    def add(self, entry: TrafficEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.limit:
                self._entries = self._entries[-self.limit :]
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            subscriber(entry)

    def entries(self) -> list[TrafficEntry]:
        with self._lock:
            return list(self._entries)

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe


log = TrafficLog()
