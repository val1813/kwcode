package t57_go_middleware

import (
	"context"
	"net/http"
	"time"
)

// HandlerConfig controls per-handler timeouts.
type HandlerConfig struct {
	Timeout time.Duration
}

// WithTimeout wraps h so that each request runs with a deadline.
// Bug: uses context.Background() instead of r.Context(), so any deadline
// already set by the parent (e.g. the server's ReadTimeout) is ignored and
// the child context never inherits cancellation from the request context.
func WithTimeout(cfg HandlerConfig, h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// BUG: should be context.WithTimeout(r.Context(), cfg.Timeout)
		ctx, cancel := context.WithTimeout(context.Background(), cfg.Timeout)
		defer cancel()
		h.ServeHTTP(w, r.WithContext(ctx))
	})
}

// EchoHandler writes the request path back as the response body.
func EchoHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(r.URL.Path)) //nolint:errcheck
	})
}

// PanicHandler always panics — used to test Recovery middleware.
func PanicHandler(msg string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic(msg)
	})
}

// ErrorHandler404 always returns 404 — used to test ErrorHandler middleware.
func ErrorHandler404() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "not found", http.StatusNotFound)
	})
}
