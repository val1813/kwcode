"""Aggregate base class for event-sourced domain objects."""

from event_store import Event, EventStore, ConcurrencyError
import uuid


class Aggregate:
    """Base class for event-sourced aggregates."""

    def __init__(self, aggregate_id: str):
        self.id = aggregate_id
        self._version = 0
        self._pending_events: list[Event] = []

    def _apply(self, event: Event) -> None:
        """Apply an event to update state. Subclasses override."""
        pass

    def _raise_event(self, event_type: str, payload: dict) -> Event:
        """Create and stage a new event."""
        event = Event(
            event_id=str(uuid.uuid4()),
            aggregate_id=self.id,
            event_type=event_type,
            payload=payload,
            version=self._version,
        )
        self._pending_events.append(event)
        self._apply(event)
        # BUG: _version is NOT incremented here, so all pending events get
        # the same version number and save() passes the wrong expected_version.
        return event

    def save(self, store: EventStore) -> None:
        """Persist pending events to the store."""
        for event in self._pending_events:
            store.append(event, expected_version=self._version)
            self._version += 1
        self._pending_events.clear()

    @classmethod
    def load_from_store(cls, aggregate_id: str, store: EventStore) -> "Aggregate":
        """Reconstruct aggregate from stored events."""
        instance = cls(aggregate_id)
        events = store.load(aggregate_id)
        for event in events:
            instance._apply(event)
            instance._version += 1
        return instance


class BankAccount(Aggregate):
    """A simple bank account aggregate."""

    def __init__(self, account_id: str):
        super().__init__(account_id)
        self.balance = 0
        self.owner = ""
        self.is_open = False

    def open(self, owner: str, initial_deposit: float) -> None:
        if self.is_open:
            raise ValueError("Account already open")
        if initial_deposit < 0:
            raise ValueError("Initial deposit cannot be negative")
        self._raise_event("AccountOpened", {"owner": owner, "amount": initial_deposit})

    def deposit(self, amount: float) -> None:
        if not self.is_open:
            raise ValueError("Account is not open")
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        self._raise_event("MoneyDeposited", {"amount": amount})

    def withdraw(self, amount: float) -> None:
        if not self.is_open:
            raise ValueError("Account is not open")
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")
        if amount > self.balance:
            raise ValueError("Insufficient funds")
        self._raise_event("MoneyWithdrawn", {"amount": amount})

    def close(self) -> None:
        if not self.is_open:
            raise ValueError("Account is not open")
        self._raise_event("AccountClosed", {})

    def _apply(self, event: Event) -> None:
        if event.event_type == "AccountOpened":
            self.owner = event.payload["owner"]
            self.balance = event.payload["amount"]
            self.is_open = True
        elif event.event_type == "MoneyDeposited":
            self.balance += event.payload["amount"]
        elif event.event_type == "MoneyWithdrawn":
            self.balance -= event.payload["amount"]
        elif event.event_type == "AccountClosed":
            self.is_open = False
