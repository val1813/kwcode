package t58_go_channel_pool

import "errors"

// TaskResult bundles a task ID with its outcome.
type TaskResult struct {
	ID  int
	Err error
}

// ErrSkipped is returned for tasks that were not executed because the pool
// was closed before they could be processed.
var ErrSkipped = errors.New("task skipped: pool closed")
