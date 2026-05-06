package t57_go_middleware

import (
	"net/http"
	"strings"
)

// Router is a simple path-prefix router. No bugs here.
type Router struct {
	routes []route
	mux    *http.ServeMux
}

type route struct {
	prefix  string
	handler http.Handler
}

// NewRouter creates an empty Router.
func NewRouter() *Router {
	return &Router{mux: http.NewServeMux()}
}

// Handle registers handler for paths with the given prefix.
func (ro *Router) Handle(prefix string, handler http.Handler) {
	ro.routes = append(ro.routes, route{prefix: prefix, handler: handler})
}

// ServeHTTP dispatches to the longest matching prefix.
func (ro *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	best := -1
	var bestHandler http.Handler
	for _, rt := range ro.routes {
		if strings.HasPrefix(r.URL.Path, rt.prefix) && len(rt.prefix) > best {
			best = len(rt.prefix)
			bestHandler = rt.handler
		}
	}
	if bestHandler != nil {
		bestHandler.ServeHTTP(w, r)
		return
	}
	http.NotFound(w, r)
}
