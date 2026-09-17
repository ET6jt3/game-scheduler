package api

import (
	"fmt"
	"github.com/xiabee/game-scheduler/internal/chains"
	"github.com/xiabee/game-scheduler/internal/startup"
	"github.com/xiabee/game-scheduler/internal/store"
	"net/http"
	"strconv"
	"time"
)

func (s *Server) chainRoutes(m *http.ServeMux) {
	m.HandleFunc("GET /automation", func(w http.ResponseWriter, r *http.Request) {
		b, err := webFS.ReadFile("web/automation.html")
		if err != nil {
			writeErr(w, 500, err)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(b)
	})
	m.HandleFunc("GET /api/chains", func(w http.ResponseWriter, r *http.Request) {
		cs, err := s.store.ListChains()
		if err != nil {
			writeStoreErr(w, err)
			return
		}
		runs, err := s.store.ListChainRuns()
		if err != nil {
			writeStoreErr(w, err)
			return
		}
		next := map[int64]time.Time{}
		for _, c := range cs {
			if c.Enabled {
				next[c.ID] = chains.Next(c, time.Now())
			}
		}
		if len(runs) > 100 {
			runs = runs[:100]
		}
		writeJSON(w, 200, map[string]any{"chains": cs, "runs": runs, "next": next, "desktop_ready": chains.DesktopReady(), "local_zone": time.Now().Format("MST -07:00")})
	})
	m.HandleFunc("POST /api/chains", s.saveChain)
	m.HandleFunc("PUT /api/chains/{id}", s.saveChain)
	m.HandleFunc("POST /api/chains/{id}/enabled", func(w http.ResponseWriter, r *http.Request) {
		if !s.chainsReady(w) {
			return
		}
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		var body struct {
			Enabled bool `json:"enabled"`
		}
		if !decode(w, r, &body) {
			return
		}
		respond(w, map[string]bool{"enabled": body.Enabled}, s.Chains.Enable(id, body.Enabled))
	})
	m.HandleFunc("POST /api/chains/{id}/run", func(w http.ResponseWriter, r *http.Request) {
		if !s.chainsReady(w) {
			return
		}
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		v, err := s.Chains.RunNow(id, time.Now())
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		writeJSON(w, 202, v)
	})
	m.HandleFunc("POST /api/chain-runs/{id}/{action}", func(w http.ResponseWriter, r *http.Request) {
		if !s.chainsReady(w) {
			return
		}
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		err = s.Chains.Control(id, r.PathValue("action"))
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		writeJSON(w, 200, map[string]bool{"ok": true})
	})
	m.HandleFunc("GET /api/startup", func(w http.ResponseWriter, r *http.Request) {
		v, err := startup.Apply(s.root, "Status", false)
		respond(w, v, err)
	})
	m.HandleFunc("POST /api/startup", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Enabled  bool `json:"enabled"`
			Elevated bool `json:"elevated"`
		}
		if !decode(w, r, &body) {
			return
		}
		action := "Disable"
		if body.Enabled {
			action = "Enable"
		}
		v, err := startup.Apply(s.root, action, body.Elevated)
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		writeJSON(w, 200, v)
	})
}
func (s *Server) chainsReady(w http.ResponseWriter) bool {
	if s.Chains == nil {
		writeErr(w, 503, fmt.Errorf("daily chain engine unavailable"))
		return false
	}
	return true
}
func (s *Server) saveChain(w http.ResponseWriter, r *http.Request) {
	if !s.chainsReady(w) {
		return
	}
	var body struct {
		store.Chain
		DisablePlans bool `json:"disable_separate_plans"`
	}
	if !decode(w, r, &body) {
		return
	}
	if r.PathValue("id") != "" {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			writeErr(w, 400, err)
			return
		}
		body.ID = id
	} else {
		body.ID = 0
	}
	c, err := s.Chains.Save(body.Chain, body.DisablePlans)
	if err != nil {
		writeErr(w, 400, err)
		return
	}
	if s.sched != nil {
		if err = s.sched.Reload(); err != nil {
			writeErr(w, 500, err)
			return
		}
	}
	writeJSON(w, 200, c)
}
