package t57_go_middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestMiddlewareOrder verifies that Logger runs before Recovery, and Recovery
// runs before ErrorHandler (i.e. ErrorHandler is innermost).
// With the bug, ErrorHandler is outermost so it executes first and its log
// entry appears before Logger's entry.
func TestMiddlewareOrder(t *testing.T) {
	var logs []RequestLog

	// A handler that returns 404
	app := NewApp(ErrorHandler404(), &logs)

	req := httptest.NewRequest(http.MethodGet, "/test", nil)
	rw := httptest.NewRecorder()
	app.ServeHTTP(rw, req)

	// After fix: Logger entry comes first (outermost), then ErrorHandler entry.
	// With bug: ErrorHandler entry comes first because it is outermost.
	if len(logs) < 2 {
		t.Fatalf("expected at least 2 log entries, got %d", len(logs))
	}
	// First log entry must be from Logger (has StatusCode, no ErrMsg)
	if logs[0].ErrMsg != "" {
		t.Errorf("first log entry should be from Logger (no ErrMsg), got ErrMsg=%q — ErrorHandler is in wrong position", logs[0].ErrMsg)
	}
	// Second log entry must be from ErrorHandler (has ErrMsg)
	if logs[1].ErrMsg == "" {
		t.Errorf("second log entry should be from ErrorHandler (has ErrMsg), got empty ErrMsg")
	}
}

// TestRecoveryMiddleware verifies that panics are caught and a 500 is returned.
func TestRecoveryMiddleware(t *testing.T) {
	var logs []RequestLog
	app := NewApp(PanicHandler("boom"), &logs)

	req := httptest.NewRequest(http.MethodGet, "/panic", nil)
	rw := httptest.NewRecorder()
	app.ServeHTTP(rw, req)

	if rw.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 after panic, got %d", rw.Code)
	}
	recovered := false
	for _, l := range logs {
		if l.Recovered {
			recovered = true
			break
		}
	}
	if !recovered {
		t.Error("expected a recovery log entry")
	}
}

// TestWithTimeoutInheritsParentContext verifies that WithTimeout inherits the
// parent request context so that if the parent is already cancelled the child
// is immediately cancelled too.
// With the bug (context.Background()), the child is never cancelled by the parent.
func TestWithTimeoutInheritsParentContext(t *testing.T) {
	cancelled := make(chan struct{})

	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
			close(cancelled)
		case <-time.After(2 * time.Second):
			// context was not cancelled — bug present
		}
	})

	h := WithTimeout(HandlerConfig{Timeout: 5 * time.Second}, inner)

	// Create a parent context that is already cancelled.
	parentCtx, parentCancel := context.WithCancel(context.Background())
	parentCancel() // cancel immediately

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req = req.WithContext(parentCtx)
	rw := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		h.ServeHTTP(rw, req)
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("handler did not respect parent context cancellation — WithTimeout uses context.Background() instead of r.Context()")
	}

	select {
	case <-cancelled:
		// good
	default:
		t.Error("inner handler did not observe context cancellation")
	}
}

// TestLoggerRecordsStatus verifies Logger captures the correct status code.
func TestLoggerRecordsStatus(t *testing.T) {
	var logs []RequestLog
	logger := Logger(&logs)
	h := logger(EchoHandler())

	req := httptest.NewRequest(http.MethodGet, "/hello", nil)
	rw := httptest.NewRecorder()
	h.ServeHTTP(rw, req)

	if len(logs) != 1 {
		t.Fatalf("expected 1 log entry, got %d", len(logs))
	}
	if logs[0].StatusCode != http.StatusOK {
		t.Errorf("expected status 200, got %d", logs[0].StatusCode)
	}
	if logs[0].Path != "/hello" {
		t.Errorf("expected path /hello, got %q", logs[0].Path)
	}
}
