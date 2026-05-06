// Package pubsub implements an in-process publish-subscribe system.
// Bugs:
// 1. broker.go: Unsubscribe removes the wrong subscriber (removes first, not the one matching id)
// 2. broker.go: Publish sends to closed subscriber channels causing panic
// 3. filter.go: TopicFilter matches topics using prefix instead of exact match
package pubsub

import (
	"sync"
)

// Message is a published event.
type Message struct {
	Topic   string
	Payload interface{}
}

// Subscriber receives messages on a channel.
type Subscriber struct {
	ID      string
	Topic   string
	ch      chan Message
	closed  bool
	mu      sync.Mutex
}

func newSubscriber(id, topic string, bufSize int) *Subscriber {
	return &Subscriber{
		ID:    id,
		Topic: topic,
		ch:    make(chan Message, bufSize),
	}
}

// C returns the receive-only message channel.
func (s *Subscriber) C() <-chan Message {
	return s.ch
}

func (s *Subscriber) send(msg Message) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return false
	}
	select {
	case s.ch <- msg:
		return true
	default:
		return false // drop if full
	}
}

func (s *Subscriber) close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.closed {
		s.closed = true
		close(s.ch)
	}
}
