package pubsub

import (
	"fmt"
	"sync"
	"sync/atomic"
)

var subCounter int64

// Broker manages topic subscriptions and message delivery.
type Broker struct {
	mu   sync.RWMutex
	subs map[string][]*Subscriber // topic -> subscribers
}

// NewBroker creates a new Broker.
func NewBroker() *Broker {
	return &Broker{
		subs: make(map[string][]*Subscriber),
	}
}

// Subscribe registers a new subscriber for the given topic.
func (b *Broker) Subscribe(topic string, bufSize int) *Subscriber {
	id := fmt.Sprintf("sub-%d", atomic.AddInt64(&subCounter, 1))
	sub := newSubscriber(id, topic, bufSize)
	b.mu.Lock()
	b.subs[topic] = append(b.subs[topic], sub)
	b.mu.Unlock()
	return sub
}

// Unsubscribe removes a subscriber by ID from its topic.
func (b *Broker) Unsubscribe(sub *Subscriber) {
	b.mu.Lock()
	defer b.mu.Unlock()
	subs := b.subs[sub.Topic]
	for i, s := range subs {
		if s.ID == sub.ID {
			// Bug: removes index 0 instead of index i
			b.subs[sub.Topic] = append(subs[:0], subs[1:]...)
			sub.close()
			_ = i
			return
		}
	}
}

// Publish sends a message to all subscribers of the given topic.
func (b *Broker) Publish(topic string, payload interface{}) int {
	msg := Message{Topic: topic, Payload: payload}
	b.mu.RLock()
	subs := make([]*Subscriber, len(b.subs[topic]))
	copy(subs, b.subs[topic])
	b.mu.RUnlock()

	count := 0
	for _, sub := range subs {
		// Bug: calls sub.ch <- msg directly without checking if closed (should use sub.send())
		sub.ch <- msg
		count++
	}
	return count
}

// PublishToAll sends a message to all subscribers regardless of topic.
func (b *Broker) PublishToAll(payload interface{}) int {
	b.mu.RLock()
	var allSubs []*Subscriber
	for _, subs := range b.subs {
		allSubs = append(allSubs, subs...)
	}
	b.mu.RUnlock()

	count := 0
	for _, sub := range allSubs {
		if sub.send(Message{Topic: sub.Topic, Payload: payload}) {
			count++
		}
	}
	return count
}

// Close shuts down the broker and all subscribers.
func (b *Broker) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, subs := range b.subs {
		for _, sub := range subs {
			sub.close()
		}
	}
	b.subs = make(map[string][]*Subscriber)
}

// SubscriberCount returns the number of subscribers for a topic.
func (b *Broker) SubscriberCount(topic string) int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return len(b.subs[topic])
}
