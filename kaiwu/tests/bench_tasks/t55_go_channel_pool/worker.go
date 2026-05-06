package t58_go_channel_pool

import "fmt"

// runWorker is the goroutine body for each pool worker.
// Bug: when the task function panics, the deferred recovery catches it but
// then returns without restarting the worker. Over time every worker that
// encounters a panic exits permanently, shrinking the pool to zero.
func runWorker(p *Pool) {
	defer p.wg.Done()
	p.active.Add(1)
	defer p.active.Add(-1)

	for {
		select {
		case task, ok := <-p.tasks:
			if !ok {
				return
			}
			executeTask(p, task)
		case <-p.quit:
			return
		}
	}
}

// executeTask runs a single task with panic recovery.
// Bug: on panic it records the error and returns — the caller (runWorker)
// then falls through to the next loop iteration, which is correct, BUT the
// deferred p.wg.Done() / p.active.Add(-1) in runWorker fire when runWorker
// returns. The real bug is that executeTask itself calls return after panic,
// which causes runWorker's for-loop to exit via the deferred return path
// because we use a named-return trick below that triggers the outer defer.
//
// Simpler reproduction: the worker goroutine is started with p.wg.Add(1) and
// the defer p.wg.Done() is in runWorker. If executeTask panics and we recover
// inside executeTask, runWorker continues normally — that part is fine.
// The actual bug is that after a panic recovery the worker goroutine calls
// p.wg.Done() prematurely by returning from runWorker entirely.
// We reproduce this by having executeTask signal the pool to restart the
// worker but the restart is missing.
func executeTask(p *Pool, task *Task) {
	defer func() {
		if r := recover(); r != nil {
			task.Err = fmt.Errorf("panic: %v", r)
			p.results <- task
			// BUG: after recovering from panic we should restart the worker,
			// but instead we just return. Because runWorker's loop calls
			// executeTask and executeTask returns normally after recovery,
			// the loop continues — so the worker itself is fine.
			//
			// The real capacity-shrink bug: we decrement active count on
			// panic and never increment it back, making ActiveWorkers() lie.
			p.active.Add(-1) // BUG: double-decrement; runWorker also decrements
		}
	}()
	task.Err = task.Fn()
	p.results <- task
}
