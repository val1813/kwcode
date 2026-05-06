// Package t59_go_config implements configuration hot-reload with file watching.
// Bugs:
// 1. watcher.go: the dedup time-window check compares against an absolute
//    clock value (Unix epoch seconds) instead of elapsed time, so it breaks
//    when the wall clock crosses a second boundary (and always fails cross-day).
// 2. merger.go: deep-merge of map values replaces slice-typed values instead
//    of appending them, so slice config keys lose their base values on reload.
package t59_go_config

import (
	"sync"
)

// Config holds arbitrary key-value configuration.
type Config struct {
	mu   sync.RWMutex
	data map[string]interface{}
}

// NewConfig returns an empty Config.
func NewConfig() *Config {
	return &Config{data: make(map[string]interface{})}
}

// Set stores a value under key (thread-safe).
func (c *Config) Set(key string, value interface{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.data[key] = value
}

// Get retrieves a value by key (thread-safe).
func (c *Config) Get(key string) (interface{}, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	v, ok := c.data[key]
	return v, ok
}

// GetString returns a string value or the empty string.
func (c *Config) GetString(key string) string {
	v, ok := c.Get(key)
	if !ok {
		return ""
	}
	s, _ := v.(string)
	return s
}

// GetSlice returns a []interface{} value or nil.
func (c *Config) GetSlice(key string) []interface{} {
	v, ok := c.Get(key)
	if !ok {
		return nil
	}
	s, _ := v.([]interface{})
	return s
}

// Apply merges patch into the config using deep-merge semantics.
func (c *Config) Apply(patch map[string]interface{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.data = deepMerge(c.data, patch)
}

// Snapshot returns a shallow copy of the current config data.
func (c *Config) Snapshot() map[string]interface{} {
	c.mu.RLock()
	defer c.mu.RUnlock()
	out := make(map[string]interface{}, len(c.data))
	for k, v := range c.data {
		out[k] = v
	}
	return out
}
