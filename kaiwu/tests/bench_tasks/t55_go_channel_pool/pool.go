// Package t58_go_channel_pool implements a goroutine worker pool.
// Bugs:
// 1. pool.go: Close() does not drain the pending tasks channel before
//    signalling workers to stop — tasks submitted before Close() may be lost
//    and goroutines waiting to send on the tasks channel leak.
// 2. worker.go: after recovering from a panic the worker exits instead of
//    restarting, so the pool's effective capacity shrinks over time.
package t58_go_channel_pool

import (
	"sync"
	"sync/atomic"
)

// Task is a unit of work submitted to the pool.
type Task struct {
	ID  int
	Fn  func() error
	Err error // filled in after execution
}

// Pool manages a fixed set of worker goroutines.
type Pool struct {
	tasks   chan *Task
	results chan *Task
	quit    chan struct{}
	wg      sync.WaitGroup
	size    int
	active  atomic.Int32 // number of currently running workers
}

// NewPool creates a pool with `size` workers and a task buffer of `bufSize`.
func NewPool(size, bufSize int) *Pool {
	p := &Pool{
		tasks:   make(chan *Task, bufSize),
		results: make(chan *Task, bufSize),
		quit:    make(chan struct{}),
		size:    size,
	}
	for i := 0; i < size; i++ {
		p.wg.Add(1)
		go runWorker(p)
	}
	return p
}

// Submit enqueues a task. Returns false if the pool is closed.
func (p *Pool) Submit(t *Task) bool {
	select {
	case p.tasks <- t:
		return true
	case <-p.quit:
		return false
	}
}

// Results returns the channel on which completed tasks are delivered.
func (p *Pool) Results() <-chan *Task {
	return p.results
}

// ActiveWorkers returns the number of goroutines currently processing tasks.
func (p *Pool) ActiveWorkers() int {
	return int(p.active.Load())
}

// Close signals workers to stop and waits for them to finish.
// Bug: closes quit immediately without draining p.tasks first.
// Any tasks already in the buffer are abandoned, and goroutines blocked in
// Submit() leak because the tasks channel is never drained.
func (p *Pool) Close() {
	// BUG: should drain p.tasks into results (marking them as skipped) or
	// wait until the channel is empty before closing quit.
	close(p.quit)
	p.wg.Wait()
	close(p.results)
}
