package t59_go_config

import (
	"testing"
	"time"
)

// TestWatcherDedupSameSecond verifies that two events for the same path
// arriving within the dedup window (but in the same second) are deduplicated.
// With the bug, elapsed = now - last = 0 which is always < window, so the
// second event is incorrectly suppressed even when window is 0.
// Wait — the bug is the opposite: same-second events have elapsed=0 which IS
// less than window, so they ARE suppressed (correct by accident).
// The real failure: events 1ms apart should be suppressed when window=500ms,
// but because we use integer seconds, 1ms apart → elapsed=0 < 0 (window=0s)
// which means window=0 never suppresses anything.
func TestWatcherDedupWithSubSecondWindow(t *testing.T) {
	// Window of 500ms — two events 100ms apart should be deduplicated.
	w := NewWatcher(500 * time.Millisecond)

	t0 := time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)
	t1 := t0.Add(100 * time.Millisecond) // 100ms later, within window

	first := w.Notify("config.yaml", t0)
	second := w.Notify("config.yaml", t1)

	if !first {
		t.Error("first event should be accepted")
	}
	if second {
		t.Error("second event 100ms later should be suppressed (within 500ms window) — watcher uses integer seconds so sub-second dedup fails")
	}
}

// TestWatcherDedupAfterWindow verifies that an event after the window passes is accepted.
func TestWatcherDedupAfterWindow(t *testing.T) {
	w := NewWatcher(500 * time.Millisecond)

	t0 := time.Date(2024, 1, 15, 12, 0, 0, 0, time.UTC)
	t1 := t0.Add(600 * time.Millisecond) // 600ms later, outside window

	w.Notify("config.yaml", t0)
	second := w.Notify("config.yaml", t1)

	if !second {
		t.Error("event after window should be accepted — watcher uses integer seconds so 600ms may round to 0s elapsed")
	}
}

// TestDeepMergeAppendsSlices verifies that merging a patch with a slice value
// appends to the existing slice rather than replacing it.
func TestDeepMergeAppendsSlices(t *testing.T) {
	base := map[string]interface{}{
		"servers": []interface{}{"a", "b"},
		"name":    "base",
	}
	patch := map[string]interface{}{
		"servers": []interface{}{"c"},
		"name":    "patched",
	}

	result := deepMerge(base, patch)

	servers, ok := result["servers"].([]interface{})
	if !ok {
		t.Fatal("servers key missing or wrong type after merge")
	}
	if len(servers) != 3 {
		t.Errorf("expected 3 servers after append merge, got %d (%v) — merger replaces slices instead of appending", len(servers), servers)
	}
}

// TestDeepMergeNestedMap verifies nested map merging works correctly.
func TestDeepMergeNestedMap(t *testing.T) {
	base := map[string]interface{}{
		"db": map[string]interface{}{
			"host": "localhost",
			"port": 5432,
		},
	}
	patch := map[string]interface{}{
		"db": map[string]interface{}{
			"port": 5433,
		},
	}

	result := deepMerge(base, patch)
	db := result["db"].(map[string]interface{})
	if db["host"] != "localhost" {
		t.Errorf("nested key 'host' should be preserved, got %v", db["host"])
	}
	if db["port"] != 5433 {
		t.Errorf("nested key 'port' should be updated to 5433, got %v", db["port"])
	}
}

// TestConfigApplySliceAppend exercises Config.Apply with slice values.
func TestConfigApplySliceAppend(t *testing.T) {
	c := NewConfig()
	c.Apply(map[string]interface{}{
		"tags": []interface{}{"go", "config"},
	})
	c.Apply(map[string]interface{}{
		"tags": []interface{}{"hot-reload"},
	})

	tags := c.GetSlice("tags")
	if len(tags) != 3 {
		t.Errorf("expected 3 tags after two Apply calls, got %d (%v) — slice replaced instead of appended", len(tags), tags)
	}
}
