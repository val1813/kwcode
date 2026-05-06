"""Event store: append-only log of domain events."""

from dataclasses import dataclass, field
from typing import Any, Optional
import time


@dataclass
class Event:
    """A domain event."""
    event_id: str
    aggregate_id: str
    event_type: str
    payload: dict
    version: int
    timestamp: float = field(default_factory=time.time)


class EventStore:
    """Append-only event store keyed by aggregate ID."""

    def __init__(self):
        self._streams: dict[str, list[Event]] = {}
        self._global_log: list[Event] = []

    def append(self, event: Event, expected_version: int = -1) -> None:
        """Append an event to the stream.

        expected_version: the version the caller expects the stream to be at.
        -1 means no version check. Raises ConcurrencyError if mismatch.
        """
        stream = self._streams.setdefault(event.aggregate_id, [])

        if expected_version != -1:
            # Bug: should compare expected_version to len(stream), not len(stream) - 1
            current_version = len(stream) - 1
            if current_version != expected_version:
                raise ConcurrencyError(
                    f"Expected version {expected_version}, got {current_version}"
                )

        stream.append(event)
        self._global_log.append(event)

    def load(self, aggregate_id: str, from_version: int = 0) -> list[Event]:
        """Load events for an aggregate starting from a given version."""
        stream = self._streams.get(aggregate_id, [])
        return stream[from_version:]

    def get_version(self, aggregate_id: str) -> int:
        """Return the current version (number of events) for an aggregate."""
        return len(self._streams.get(aggregate_id, []))

    def all_events(self) -> list[Event]:
        """Return all events in insertion order."""
        return list(self._global_log)


class ConcurrencyError(Exception):
    pass
