package pubsub

import (
	"testing"
	"time"
)

func TestSubscribeAndPublish(t *testing.T) {
	b := NewBroker()
	defer b.Close()
	sub := b.Subscribe("events", 10)
	b.Publish("events", "hello")
	select {
	case msg := <-sub.C():
		if msg.Payload != "hello" {
			t.Errorf("expected 'hello', got %v", msg.Payload)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("timeout waiting for message")
	}
}

func TestPublishToMultipleSubscribers(t *testing.T) {
	b := NewBroker()
	defer b.Close()
	sub1 := b.Subscribe("events", 10)
	sub2 := b.Subscribe("events", 10)
	count := b.Publish("events", "broadcast")
	if count != 2 {
		t.Errorf("expected 2 deliveries, got %d", count)
	}
	for _, sub := range []*Subscriber{sub1, sub2} {
		select {
		case msg := <-sub.C():
			if msg.Payload != "broadcast" {
				t.Errorf("expected 'broadcast', got %v", msg.Payload)
			}
		case <-time.After(100 * time.Millisecond):
			t.Fatal("timeout waiting for message")
		}
	}
}

func TestPublishDoesNotCrossTopics(t *testing.T) {
	b := NewBroker()
	defer b.Close()
	sub1 := b.Subscribe("topic-a", 10)
	sub2 := b.Subscribe("topic-b", 10)
	b.Publish("topic-a", "for-a")
	select {
	case msg := <-sub1.C():
		if msg.Payload != "for-a" {
			t.Errorf("expected 'for-a', got %v", msg.Payload)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("timeout")
	}
	select {
	case msg := <-sub2.C():
		t.Errorf("sub2 should not receive message for topic-a, got %v", msg)
	case <-time.After(20 * time.Millisecond):
		// expected: no message
	}
}

func TestUnsubscribeRemovesCorrectSubscriber(t *testing.T) {
	b := NewBroker()
	defer b.Close()
	sub1 := b.Subscribe("events", 10)
	sub2 := b.Subscribe("events", 10)
	b.Unsubscribe(sub1)
	if b.SubscriberCount("events") != 1 {
		t.Errorf("expected 1 subscriber after unsubscribe, got %d", b.SubscriberCount("events"))
	}
	// sub2 should still receive messages
	b.Publish("events", "after-unsub")
	select {
	case msg := <-sub2.C():
		if msg.Payload != "after-unsub" {
			t.Errorf("expected 'after-unsub', got %v", msg.Payload)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("sub2 should still receive messages after sub1 unsubscribed")
	}
}

func TestPublishAfterUnsubscribeNoPanic(t *testing.T) {
	b := NewBroker()
	sub := b.Subscribe("events", 10)
	b.Unsubscribe(sub)
	// Should not panic
	b.Publish("events", "after-close")
}

func TestTopicFilterExactMatch(t *testing.T) {
	f := NewTopicFilter("user.created")
	if !f.Matches("user.created") {
		t.Error("exact pattern should match exact topic")
	}
	if f.Matches("user.created.extra") {
		t.Error("exact pattern should not match longer topic")
	}
	if f.Matches("user") {
		t.Error("exact pattern should not match prefix")
	}
}

func TestTopicFilterWildcard(t *testing.T) {
	f := NewTopicFilter("user.*")
	if !f.Matches("user.created") {
		t.Error("wildcard should match user.created")
	}
	if !f.Matches("user.deleted") {
		t.Error("wildcard should match user.deleted")
	}
	if f.Matches("order.created") {
		t.Error("wildcard should not match different prefix")
	}
}

func TestTopicFilterGlobalWildcard(t *testing.T) {
	f := NewTopicFilter("*")
	if !f.Matches("anything") {
		t.Error("global wildcard should match anything")
	}
}

func TestFilteredBrokerPublish(t *testing.T) {
	b := NewBroker()
	defer b.Close()
	fb := NewFilteredBroker(b)
	sub := fb.SubscribeWithFilter("events", "user.*", 10)
	fb.PublishFiltered("user.created", "new user")
	fb.PublishFiltered("order.placed", "new order")
	select {
	case msg := <-sub.C():
		if msg.Payload != "new user" {
			t.Errorf("expected 'new user', got %v", msg.Payload)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("timeout")
	}
	select {
	case msg := <-sub.C():
		t.Errorf("should not receive order message, got %v", msg)
	case <-time.After(20 * time.Millisecond):
		// expected
	}
}
