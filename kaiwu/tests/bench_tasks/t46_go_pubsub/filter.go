package pubsub

import "strings"

// TopicFilter matches topics against a pattern.
type TopicFilter struct {
	pattern string
}

// NewTopicFilter creates a filter. Pattern supports '*' as wildcard suffix.
// e.g. "user.*" matches "user.created", "user.deleted"
// e.g. "user.created" matches only "user.created"
func NewTopicFilter(pattern string) *TopicFilter {
	return &TopicFilter{pattern: pattern}
}

// Matches returns true if topic matches the pattern.
func (f *TopicFilter) Matches(topic string) bool {
	if f.pattern == "*" {
		return true
	}
	if strings.HasSuffix(f.pattern, ".*") {
		prefix := strings.TrimSuffix(f.pattern, ".*")
		return strings.HasPrefix(topic, prefix)
	}
	// Bug: uses HasPrefix instead of exact match for non-wildcard patterns
	return strings.HasPrefix(topic, f.pattern)
}

// FilteredBroker wraps a Broker and routes messages using topic filters.
type FilteredBroker struct {
	broker  *Broker
	filters map[string]*TopicFilter // subscriber ID -> filter
}

// NewFilteredBroker creates a FilteredBroker.
func NewFilteredBroker(broker *Broker) *FilteredBroker {
	return &FilteredBroker{
		broker:  broker,
		filters: make(map[string]*TopicFilter),
	}
}

// SubscribeWithFilter subscribes to a topic with a filter pattern.
func (fb *FilteredBroker) SubscribeWithFilter(topic, pattern string, bufSize int) *Subscriber {
	sub := fb.broker.Subscribe(topic, bufSize)
	fb.filters[sub.ID] = NewTopicFilter(pattern)
	return sub
}

// PublishFiltered publishes to subscribers whose filter matches the topic.
func (fb *FilteredBroker) PublishFiltered(topic string, payload interface{}) int {
	msg := Message{Topic: topic, Payload: payload}
	fb.broker.mu.RLock()
	var matching []*Subscriber
	for _, subs := range fb.broker.subs {
		for _, sub := range subs {
			if f, ok := fb.filters[sub.ID]; ok {
				if f.Matches(topic) {
					matching = append(matching, sub)
				}
			}
		}
	}
	fb.broker.mu.RUnlock()

	count := 0
	for _, sub := range matching {
		if sub.send(msg) {
			count++
		}
	}
	return count
}
