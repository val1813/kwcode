"""Tests for the event sourcing system (event_store, aggregate, projection)."""

import pytest
from event_store import Event, EventStore, ConcurrencyError
from aggregate import BankAccount
from projection import AccountSummaryProjection


class TestEventStore:
    def test_append_and_load(self):
        store = EventStore()
        e = Event("e1", "acc-1", "AccountOpened", {"owner": "Alice", "amount": 100}, 0)
        store.append(e)
        events = store.load("acc-1")
        assert len(events) == 1
        assert events[0].event_id == "e1"

    def test_version_check_passes_on_empty_stream(self):
        store = EventStore()
        e = Event("e1", "acc-1", "AccountOpened", {"owner": "Alice", "amount": 100}, 0)
        # expected_version=0 means we expect the stream to have 0 events
        store.append(e, expected_version=0)
        assert store.get_version("acc-1") == 1

    def test_version_check_passes_on_subsequent_event(self):
        store = EventStore()
        e1 = Event("e1", "acc-1", "AccountOpened", {"owner": "Alice", "amount": 100}, 0)
        e2 = Event("e2", "acc-1", "MoneyDeposited", {"amount": 50}, 1)
        store.append(e1, expected_version=0)
        store.append(e2, expected_version=1)
        assert store.get_version("acc-1") == 2

    def test_version_check_raises_on_mismatch(self):
        store = EventStore()
        e1 = Event("e1", "acc-1", "AccountOpened", {"owner": "Alice", "amount": 100}, 0)
        e2 = Event("e2", "acc-1", "MoneyDeposited", {"amount": 50}, 1)
        store.append(e1)
        with pytest.raises(ConcurrencyError):
            store.append(e2, expected_version=5)

    def test_load_from_version(self):
        store = EventStore()
        for i in range(5):
            e = Event(f"e{i}", "acc-1", "MoneyDeposited", {"amount": i * 10}, i)
            store.append(e)
        events = store.load("acc-1", from_version=3)
        assert len(events) == 2

    def test_all_events_global_order(self):
        store = EventStore()
        e1 = Event("e1", "acc-1", "AccountOpened", {"owner": "Alice", "amount": 100}, 0)
        e2 = Event("e2", "acc-2", "AccountOpened", {"owner": "Bob", "amount": 200}, 0)
        store.append(e1)
        store.append(e2)
        all_ev = store.all_events()
        assert len(all_ev) == 2
        assert all_ev[0].event_id == "e1"
        assert all_ev[1].event_id == "e2"


class TestBankAccount:
    def test_open_account(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 500)
        acc.save(store)
        assert acc.balance == 500
        assert acc.is_open is True
        assert store.get_version("acc-1") == 1

    def test_deposit(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 100)
        acc.save(store)
        acc.deposit(50)
        acc.save(store)
        assert acc.balance == 150

    def test_withdraw(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 200)
        acc.save(store)
        acc.withdraw(80)
        acc.save(store)
        assert acc.balance == 120

    def test_insufficient_funds(self):
        acc = BankAccount("acc-1")
        acc.open("Alice", 50)
        with pytest.raises(ValueError, match="Insufficient funds"):
            acc.withdraw(100)

    def test_load_from_store(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 300)
        acc.save(store)
        acc.deposit(100)
        acc.save(store)
        acc.withdraw(50)
        acc.save(store)

        # Reconstruct from events
        acc2 = BankAccount.load_from_store("acc-1", store)
        assert acc2.balance == 350
        assert acc2.owner == "Alice"
        assert acc2.is_open is True

    def test_save_multiple_events_sequential_versions(self):
        """Each save call should use the correct expected_version."""
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 100)
        acc.save(store)
        acc.deposit(50)
        acc.deposit(25)
        # Saving two events at once — versions must be sequential
        acc.save(store)
        assert store.get_version("acc-1") == 3


class TestProjection:
    def test_rebuild_correct_order(self):
        """Projection rebuild must process events in chronological order."""
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 1000)
        acc.save(store)
        acc.deposit(500)
        acc.save(store)
        acc.withdraw(200)
        acc.save(store)

        proj = AccountSummaryProjection()
        proj.rebuild(store.all_events())
        summary = proj.get("acc-1")
        assert summary is not None
        assert summary["balance"] == 1300
        assert summary["owner"] == "Alice"
        assert summary["is_open"] is True

    def test_rebuild_closed_account(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Bob", 200)
        acc.save(store)
        acc.close()
        acc.save(store)

        proj = AccountSummaryProjection()
        proj.rebuild(store.all_events())
        summary = proj.get("acc-1")
        assert summary["is_open"] is False

    def test_all_open_accounts(self):
        store = EventStore()
        acc1 = BankAccount("acc-1")
        acc1.open("Alice", 100)
        acc1.save(store)

        acc2 = BankAccount("acc-2")
        acc2.open("Bob", 200)
        acc2.save(store)
        acc2.close()
        acc2.save(store)

        proj = AccountSummaryProjection()
        proj.rebuild(store.all_events())
        open_accounts = proj.all_open_accounts()
        assert len(open_accounts) == 1
        assert open_accounts[0]["account_id"] == "acc-1"

    def test_transaction_count(self):
        store = EventStore()
        acc = BankAccount("acc-1")
        acc.open("Alice", 100)
        acc.save(store)
        acc.deposit(50)
        acc.save(store)
        acc.deposit(25)
        acc.save(store)

        proj = AccountSummaryProjection()
        proj.rebuild(store.all_events())
        summary = proj.get("acc-1")
        assert summary["transaction_count"] == 3
