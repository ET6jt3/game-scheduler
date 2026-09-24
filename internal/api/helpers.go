package api

import (
	"encoding/json"
	"errors"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/store"
	"io"
	"net/http"
	"strconv"
)

func (s *Server) helperRoutes(m *http.ServeMux) {
	m.HandleFunc("POST /api/server/stop", func(w http.ResponseWriter, r *http.Request) {
		if s.RequestShutdown == nil {
			writeErr(w, 503, errors.New("shutdown unavailable"))
			return
		}
		writeJSON(w, 202, map[string]string{"status": "stopping"})
		s.RequestShutdown()
	})
	m.HandleFunc("GET /api/helpers", func(w http.ResponseWriter, r *http.Request) { v, e := s.store.ListHelpers(); respond(w, v, e) })
	m.HandleFunc("POST /api/helpers", s.saveHelper)
	m.HandleFunc("GET /api/helpers/{id}", func(w http.ResponseWriter, r *http.Request) {
		v, e := s.store.GetHelper(r.PathValue("id"))
		respond(w, v, e)
	})
	m.HandleFunc("PUT /api/helpers/{id}", s.saveHelper)
	m.HandleFunc("DELETE /api/helpers/{id}", func(w http.ResponseWriter, r *http.Request) {
		e := s.store.DeleteHelper(r.PathValue("id"))
		respond(w, map[string]bool{"deleted": e == nil}, s.changed(e))
	})
	m.HandleFunc("POST /api/helpers/{id}/preflight", s.preflightHelper)
	m.HandleFunc("POST /api/helpers/discover", s.discoverScan)
	m.HandleFunc("GET /api/helper-definitions", func(w http.ResponseWriter, r *http.Request) {
		if s.helpersReady(w) {
			writeJSON(w, 200, s.svc.Helpers.Definitions())
		}
	})
	m.HandleFunc("POST /api/helper-definitions/reload", func(w http.ResponseWriter, r *http.Request) {
		if !s.helpersReady(w) {
			return
		}
		if e := s.svc.Helpers.Reload(); e != nil {
			writeErr(w, 400, e)
			return
		}
		s.changed(nil)
		writeJSON(w, 200, s.svc.Helpers.Definitions())
	})
	m.HandleFunc("GET /api/helper-settings/discovery", func(w http.ResponseWriter, r *http.Request) {
		if s.helpersReady(w) {
			v, e := s.svc.Helpers.Discovery()
			respond(w, v, e)
		}
	})
	m.HandleFunc("PUT /api/helper-settings/discovery", func(w http.ResponseWriter, r *http.Request) {
		if !s.helpersReady(w) {
			return
		}
		var v config.DiscoveryConfig
		if !decode(w, r, &v) {
			return
		}
		if e := s.svc.Helpers.SaveDiscovery(v); e != nil {
			writeErr(w, 400, e)
			return
		}
		writeJSON(w, 200, v)
	})
	m.HandleFunc("GET /api/executions/{id}/diagnostics", func(w http.ResponseWriter, r *http.Request) {
		id, e := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if e != nil {
			writeErr(w, 400, e)
			return
		}
		v, e := s.store.ExecutionDiagnostics(id)
		respond(w, v, e)
	})
}
func (s *Server) helpersReady(w http.ResponseWriter) bool {
	if s.svc == nil || s.svc.Helpers == nil {
		writeErr(w, 503, errors.New("helper hub is not initialized"))
		return false
	}
	return true
}
func (s *Server) saveHelper(w http.ResponseWriter, r *http.Request) {
	if !s.helpersReady(w) {
		return
	}
	v := store.HelperInstance{Enabled: true}
	if !decode(w, r, &v) {
		return
	}
	create := r.Method == "POST"
	if !create {
		v.ID = r.PathValue("id")
	}
	if e := s.svc.Helpers.ValidateInstance(v); e != nil {
		writeErr(w, 400, e)
		return
	}
	e := s.store.SaveHelper(v, create)
	if e != nil {
		if errors.Is(e, store.ErrNotFound) {
			writeErr(w, 404, e)
		} else {
			writeErr(w, 409, e)
		}
		return
	}
	s.changed(nil)
	status := 200
	if create {
		status = 201
	}
	writeJSON(w, status, v)
}
func (s *Server) preflightHelper(w http.ResponseWriter, r *http.Request) {
	if !s.helpersReady(w) {
		return
	}
	var req struct {
		Type   string         `json:"type"`
		Params map[string]any `json:"params"`
	}
	if r.Body != nil {
		if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBodyBytes)).Decode(&req); e != nil && !errors.Is(e, io.EOF) {
			writeErr(w, 400, e)
			return
		}
	}
	v, e := s.svc.PreflightHelper(r.PathValue("id"), req.Type, req.Params)
	respond(w, v, e)
}
