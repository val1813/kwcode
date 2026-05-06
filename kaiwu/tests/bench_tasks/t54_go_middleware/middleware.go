// Package t57_go_middleware implements an HTTP middleware chain.
// Bugs:
// 1. middleware.go: error-handling middleware is registered first (outermost) but
//    should be last (innermost before the handler) — the Chain helper reverses order,
//    so callers must pass error middleware LAST, but the example wires it FIRST.
// 2. handler.go: context timeout is created with context.Background() instead of
//    inheriting the parent context's deadline.
package t57_go_middleware

import (
	"context"
	"fmt"
	"net/http"
	"strings"
)

// MiddlewareFunc wraps an http.Handler.
type MiddlewareFunc func(http.Handler) http.Handler

// Chain builds a handler by applying middlewares left-to-right so that the
// first middleware in the slice is the outermost wrapper.
func Chain(h http.Handler, mws ...MiddlewareFunc) http.Handler {
	// iterate in reverse so first middleware ends up outermost
	for i := len(mws) - 1; i >= 0; i-- {
		h = mws[i](h)
	}
	return h
}

// RequestLog records what happened during a request.
type RequestLog struct {
	Path       string
	StatusCode int
	Recovered  bool
	ErrMsg     string
}

// statusRecorder captures the status code written to the response.
type statusRecorder struct {
	http.ResponseWriter
	code int
	body strings.Builder
}

func (r *statusRecorder) WriteHeader(code int) {
	r.code = code
	r.ResponseWriter.WriteHeader(code)
}

func (r *statusRecorder) Write(b []byte) (int, error) {
	r.body.Write(b)
	return r.ResponseWriter.Write(b)
}

// Logger logs every request path and response status into logs.
func Logger(logs *[]RequestLog) MiddlewareFunc {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
			next.ServeHTTP(rec, r)
			*logs = append(*logs, RequestLog{Path: r.URL.Path, StatusCode: rec.code})
		})
	}
}

// Recovery catches panics and writes a 500 response.
func Recovery(logs *[]RequestLog) MiddlewareFunc {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if v := recover(); v != nil {
					*logs = append(*logs, RequestLog{
						Path:      r.URL.Path,
						Recovered: true,
						ErrMsg:    fmt.Sprintf("%v", v),
					})
					http.Error(w, "internal server error", http.StatusInternalServerError)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// ErrorHandler converts non-2xx responses into structured JSON error bodies.
// Bug: it is wired as the FIRST middleware (outermost) in NewApp, but because
// Chain applies left-to-right it ends up executing before Logger and Recovery.
// Error middleware should be innermost (last in the slice passed to Chain) so
// that Logger and Recovery can observe the final status code.
func ErrorHandler(logs *[]RequestLog) MiddlewareFunc {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
			next.ServeHTTP(rec, r)
			if rec.code >= 400 {
				*logs = append(*logs, RequestLog{
					Path:       r.URL.Path,
					StatusCode: rec.code,
					ErrMsg:     fmt.Sprintf("error: status %d", rec.code),
				})
			}
		})
	}
}

// NewApp wires up the middleware chain for the given handler.
// Bug: ErrorHandler is passed FIRST — it becomes the outermost middleware and
// therefore runs before Logger/Recovery. It should be passed LAST so it is
// innermost and sees the final response after all other middleware have run.
func NewApp(handler http.Handler, logs *[]RequestLog) http.Handler {
	// BUG: ErrorHandler should be last, not first
	return Chain(handler,
		ErrorHandler(logs), // wrong position — should be last
		Logger(logs),
		Recovery(logs),
	)
}
