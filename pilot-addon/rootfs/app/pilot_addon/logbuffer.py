"""In-process log ring buffer: recent tail + live fan-out for WS subscribers.

Memory-only short-term log window for the workshop UI (live logs view).
The durable record stays the append-only audit trail on disk (trust.py).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import logging
from typing import Any

MAX_LOG_LINES = 5000

LogEntry = dict[str, Any]
LogSubscriber = Callable[[LogEntry], None]


class LogBuffer:
    """Ring buffer of recent log records; fans new records out to subscribers."""

    def __init__(self, maxlen: int = MAX_LOG_LINES) -> None:
        self._records: deque[LogEntry] = deque(maxlen=maxlen)
        self._subscribers: set[LogSubscriber] = set()

    def append(self, entry: LogEntry) -> None:
        """Store a record and notify subscribers (called from a logging handler)."""
        self._records.append(entry)
        for callback in list(self._subscribers):
            try:
                callback(entry)
            except Exception:  # a broken subscriber must not break logging
                pass

    def recent(self, limit: int | None = None) -> list[LogEntry]:
        """Return the tail of the buffer, oldest first."""
        records = list(self._records)
        if limit is not None:
            records = records[-limit:]
        return records

    def subscribe(self, callback: LogSubscriber) -> None:
        self._subscribers.add(callback)

    def unsubscribe(self, callback: LogSubscriber) -> None:
        self._subscribers.discard(callback)


class RingBufferHandler(logging.Handler):
    """Push every emitted record into a LogBuffer as a JSON-safe entry."""

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:
            self.handleError(record)
