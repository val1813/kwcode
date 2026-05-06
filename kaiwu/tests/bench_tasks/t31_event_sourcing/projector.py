"""Read-side projections built from the event stream."""

from event_store import Event


class AccountSummaryProjection:
    """Projects account events into a summary read model."""

    def __init__(self):
        # account_id -> summary dict
        self._summaries: dict[str, dict] = {}

    def handle(self, event: Event) -> None:
        """Process a single event and update the projection."""
        aid = event.aggregate_id

        if event.event_type == "AccountOpened":
            self._summaries[aid] = {
                "account_id": aid,
                "owner": event.payload["owner"],
                "balance": event.payload["amount"],
                "is_open": True,
                "transaction_count": 1,
            }
        elif event.event_type == "MoneyDeposited":
            if aid in self._summaries:
                self._summaries[aid]["balance"] += event.payload["amount"]
                self._summaries[aid]["transaction_count"] += 1
        elif event.event_type == "MoneyWithdrawn":
            if aid in self._summaries:
                self._summaries[aid]["balance"] -= event.payload["amount"]
                self._summaries[aid]["transaction_count"] += 1
        elif event.event_type == "AccountClosed":
            if aid in self._summaries:
                self._summaries[aid]["is_open"] = False

    def rebuild(self, events: list[Event]) -> None:
        """Rebuild projection from scratch using all events in order."""
        self._summaries.clear()
        for event in events:
            self.handle(event)

    def get(self, account_id: str) -> dict | None:
        return self._summaries.get(account_id)

    def all_open_accounts(self) -> list[dict]:
        return [s for s in self._summaries.values() if s["is_open"]]
