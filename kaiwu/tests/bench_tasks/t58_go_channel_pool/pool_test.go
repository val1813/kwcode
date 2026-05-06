package t58_go_channel_pool

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

// TestPoolDrainsTasksOnClose verifies that tasks submitted before Close() are
// all eventually delivered to the results channel (not silently dropped).
// With the bug, Close() shuts down workers immediately so buffered tasks are lost.
func TestPoolDrainsTasksOnClose(t *testing.T) {
	const numTasks = 20
	p := NewPool(2, numTasks)

	// Submit all tasks before closing.
	for i := 0; i < numTasks; i++ {
		id := i
		p.Submit(&Task{ID: id, Fn: func() error { return nil }})
	}

	// Close the pool — all submitted tasks must appear in results.
	go p.Close()

	received := make(map[int]bool)
	timeout := time.After(3 * time.Second)
	for len(received) < numTasks {
		select {
		case task, ok := <-p.Results():
			if !ok {
				goto done
			}
			received[task.ID] = true
		case <-timeout:
			t.Fatalf("timeout: only received %d/%d task results — pool dropped tasks on Close()", len(received), numTasks)
		}
	}
done:
	if len(received) != numTasks {
		t.Errorf("expected %d results, got %d — pool dropped tasks on Close()", numTasks, len(received))
	}
}

// TestWorkerCountAfterPanic verifies that the pool's active worker count does
// not decrease after a task panics.
// With the bug, executeTask double-decrements p.active so ActiveWorkers() drops.
func TestWorkerCountAfterPanic(t *testing.T) {
	p := NewPool(4, 16)
	// Let workers start.
	time.Sleep(20 * time.Millisecond)
	initialActive := p.ActiveWorkers()
	if initialActive != 4 {
		t.Fatalf("expected 4 active workers at start, got %d", initialActive)
	}

	// Submit tasks that panic.
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		id := i
		p.Submit(&Task{ID: id, Fn: func() error {
			defer wg.Done()
			panic(fmt.Sprintf("intentional panic %d", id))
		}})
	}

	// Collect results.
	go func() {
		for range p.Results() {
		}
	}()

	wg.Wait()
	time.Sleep(50 * time.Millisecond)

	afterActive := p.ActiveWorkers()
	if afterActive != initialActive {
		t.Errorf("active worker count dropped from %d to %d after panics — worker not restarted or active count double-decremented",
			initialActive, afterActive)
	}

	p.Close()
}

// TestNormalExecution verifies basic task execution and result delivery.
func TestNormalExecution(t *testing.T) {
	p := NewPool(3, 10)

	const n = 9
	for i := 0; i < n; i++ {
		id := i
		p.Submit(&Task{ID: id, Fn: func() error {
			if id%3 == 0 {
				return fmt.Errorf("task %d failed", id)
			}
			return nil
		}})
	}

	go p.Close()

	results := make([]*Task, 0, n)
	for task := range p.Results() {
		results = append(results, task)
	}

	if len(results) != n {
		t.Errorf("expected %d results, got %d", n, len(results))
	}
}
