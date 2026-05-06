package t59_go_config

import (
	"time"
)

// ChangeEvent represents a detected file change.
type ChangeEvent struct {
	Path      string
	Timestamp time.Time
}

// Watcher deduplicates rapid file-change events within a time window.
type Watcher struct {
	window   time.Duration
	lastSeen map[string]int64 // key → last event time
	Events   []ChangeEvent    // deduplicated events (for testing)
}

// NewWatcher creates a Watcher with the given dedup window.
func NewWatcher(window time.Duration) *Watcher {
	return &Watcher{
		window:   window,
		lastSeen: make(map[string]int64),
	}
}

// Notify processes a raw file-change event.
// It suppresses duplicate events that arrive within the dedup window.
// Bug: the comparison uses absolute Unix seconds (time.Now().Unix()) instead
// of elapsed time. When two events arrive in the same second the difference
// is 0, which is always less than window.Seconds(), so dedup never fires.
// Across a day boundary the absolute value wraps and the comparison is wrong.
// The fix: store the last event time as a time.Time and compare with time.Since.
func (w *Watcher) Notify(path string, at time.Time) bool {
	now := at.Unix() // absolute Unix timestamp in seconds
	last, seen := w.lastSeen[path]
	if seen {
		// BUG: compares absolute timestamps; should compare elapsed duration.
		// Correct: if at.Sub(time.Unix(last, 0)) < w.window { return false }
		elapsed := now - last // difference in seconds (can be 0 for same-second events)
		if elapsed < int64(w.window.Seconds()) {
			return false // suppress duplicate
		}
	}
	w.lastSeen[path] = now
	w.Events = append(w.Events, ChangeEvent{Path: path, Timestamp: at})
	return true
}

// Reset clears all state (for testing).
func (w *Watcher) Reset() {
	w.lastSeen = make(map[string]int64)
	w.Events = nil
}
